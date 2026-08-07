"""Run commands through a shell already running inside the container.

Even a direct Engine API ``exec`` costs ~20ms: Docker has to create an exec
instance and runc has to spawn a process in the container's namespaces. A
sidecar makes that avoidable - a shell started once in the container can fork
the command itself, which costs a fraction of a millisecond.

The shell and this process talk over FIFOs in a directory that is bind-mounted
into the container, so no image change is needed: the dispatcher is written
from the host and needs nothing but ``/bin/sh``.

FIFOs only connect processes sharing a kernel, which a bind mount gives on
Linux but Docker Desktop's virtual machine does not, so this path is used only
where it can work and every failure - no dispatcher yet, a mount that cannot
carry FIFOs, an argument the line protocol cannot express - simply returns
``None`` for the caller to run the command the ordinary way.
"""
from __future__ import annotations

import errno
import os
import selectors
import signal
import sys

from .spec import RUNNER_MARKER, TigError

DISABLE_ENV = "TIG_NO_DISPATCHER"

# Part of the control FIFO's name together with the script's digest, so a
# client never submits to a dispatcher that speaks anything else.
PROTOCOL = "1"

READ_SIZE = 65536

# How long to wait between checks that the dispatcher is still there while a
# command runs.
LIVENESS_INTERVAL = 5.0

# What the dispatcher writes instead of an exit status when it could not
# even enter the working directory, so nothing ran and the caller can still
# run the command itself.
NOT_STARTED = b"unstarted"

SHELL = "/bin/sh"

_FIFOS = ("in", "out", "err", "status", "ready")

SCRIPT = r"""#!/bin/sh
# tig command dispatcher: runs commands submitted over a FIFO, so callers do
# not pay for a container exec. Written by the tig CLI on the host; see
# tig_cli/dispatch.py for the protocol.
rundir=$1
control=$2
[ -n "$rundir" ] && [ -n "$control" ] || exit 64

# Job directories are removed by their client; these are ones whose client
# was killed.
find "$rundir" -maxdepth 1 -name 'job-*' -mmin +1440 -exec rm -rf {} + 2>/dev/null

# Read-write so the loop below sees no end-of-file between clients.
exec 3<>"$control" || exit 65

while IFS= read -r job <&3; do
    case $job in
        job-*) ;;
        *) continue ;;
    esac
    dir=$rundir/$job
    [ -d "$dir" ] || continue

    (
        # Only the loop above holds the control FIFO, so a client can tell
        # from it whether the dispatcher itself is still there.
        exec 3<&-
        workdir=''
        display=''
        set --
        field=0
        while IFS= read -r line; do
            case $field in
                0) workdir=$line; field=1 ;;
                1) display=$line; field=2 ;;
                *) set -- "$@" "$line" ;;
            esac
        done < "$dir/cmd"
        [ $# -gt 0 ] || exit 0
        if ! [ -d "$workdir" ]; then
            echo unstarted > "$dir/status"
            exit 0
        fi

        # A group of its own, so the command and its children can be
        # signalled together.
        set -m 2>/dev/null
        (
            cd "$workdir" || exit 126
            [ -n "$display" ] && export DISPLAY=$display
            exec <"$dir/in" >"$dir/out" 2>"$dir/err"
            # Tells the client its end of the input FIFO may be closed; it
            # has to be held open until this open completes, or that open
            # would wait for a writer that has already gone.
            echo r > "$dir/ready"
            # Said plainly here; the shell would otherwise name this script
            # and its line number, which mean nothing to the caller.
            command -v -- "$1" >/dev/null 2>&1 || {
                echo "tig: $1: not found" >&2
                exit 127
            }
            exec "$@"
        ) &
        child=$!
        echo "$child" > "$dir/pid"
        wait "$child"
        echo "$?" > "$dir/status"
    ) &
done
"""


def run(
    container: str,
    home: str,
    command: list[str],
    workdir: str,
    env: dict[str, str],
) -> int | None:
    """Run ``command`` through the container's dispatcher.

    Returns:
        The command's exit code, or ``None`` when the dispatcher cannot be
        used and the caller should run the command itself.
    """
    if os.environ.get(DISABLE_ENV) or not usable():
        return None
    if not _expressible(command, workdir, env):
        return None

    job = _Job(_Paths(home, container), command, workdir, env)
    try:
        submitted = job.start()
    except OSError:
        job.close()
        return None
    # Past this point the command may have run, so a failure must be an
    # error: telling the caller the dispatcher went unused would have it run
    # the command a second time.
    try:
        return job.wait() if submitted else None
    finally:
        job.close()


def usable() -> bool:
    """Whether host and container could share FIFOs at all.

    They have to be the same kernel, which a bind mount gives on Linux.
    Elsewhere nothing is started, and a stray dispatcher could not be
    reached anyway: its FIFOs would simply look unopened.
    """
    return sys.platform.startswith("linux")


def ensure_running(engine, container: str, home: str) -> None:
    """Start the container's dispatcher unless it is already listening.

    Called after a command has been run the slow way, so the cost of getting
    the dispatcher up is never on the critical path of the command itself.
    """
    from .engine import EngineError

    if os.environ.get(DISABLE_ENV) or not usable() or engine.kind != "unix":
        return
    paths = _Paths(home, container)
    try:
        if _listening(paths.control):
            return
        paths.install()
    except OSError:
        return
    try:
        engine.exec_detached(
            container,
            [SHELL, paths.script, paths.rundir, paths.control, RUNNER_MARKER],
        )
    except EngineError:
        pass


def recently_verified(home: str, container: str, interval: float) -> bool:
    """Whether the container was checked against the daemon lately."""
    import time

    try:
        age = time.time() - os.path.getmtime(_Paths(home, container).marker)
    except OSError:
        return False
    return 0 <= age < interval


def mark_verified(home: str, container: str) -> None:
    """Record that the container was just checked against the daemon."""
    path = _Paths(home, container).marker
    try:
        os.close(os.open(path, os.O_WRONLY | os.O_CREAT, 0o600))
        os.utime(path, None)
    except OSError:
        pass


def retire(home: str, container: str) -> None:
    """Take a dispatcher out of service, whatever it is still running.

    Removing the control FIFO is enough: clients then find nothing to submit
    to and go the full path, which sorts the container out.
    """
    try:
        os.unlink(_Paths(home, container).control)
    except OSError:
        pass


def _expressible(command: list[str], workdir: str, env: dict[str, str]) -> bool:
    """Whether the request survives the newline-separated request format.

    Arguments holding a newline are vanishingly rare for VICAR tools and not
    worth a heavier protocol; they just go the ordinary way.
    """
    display = env.get("DISPLAY", "")
    return not any("\n" in text for text in [workdir, display, *command])


def _listening(control: str) -> bool:
    """Whether a dispatcher has the control FIFO open for reading."""
    try:
        os.close(os.open(control, os.O_WRONLY | os.O_NONBLOCK))
    except OSError as e:
        if e.errno in (errno.ENXIO, errno.ENOENT):
            return False
        raise
    return True


class _Paths:
    """Where a container's dispatcher and its jobs live on the host.

    Under the home directory because that is bind-mounted read-write at the
    same path inside the container, so both sides name these files alike.
    """

    def __init__(self, home: str, container: str):
        self.container = container
        self.rundir = os.path.join(home, ".cache", "tig", container)
        # The script's own digest names its control FIFO, so an upgraded tig
        # starts its own dispatcher instead of talking to the one an older
        # version left running.
        version = f"{PROTOCOL}-{_digest(SCRIPT)}"
        self.control = os.path.join(self.rundir, f"control-{version}")
        self.script = os.path.join(self.rundir, f"dispatch-{version}.sh")
        self.marker = os.path.join(self.rundir, "verified")

    def install(self) -> None:
        """Create the run directory, the script and the control FIFO."""
        os.makedirs(self.rundir, mode=0o700, exist_ok=True)
        self._discard_older()
        _write_once(self.script, SCRIPT.encode())
        _make_fifo(self.control)
        with open(self.marker, "wb"):
            pass

    def _discard_older(self) -> None:
        """Retire the dispatchers of earlier versions of the CLI.

        Their control FIFO goes, so nothing submits to them again; the shell
        itself is left to go when the container does.
        """
        keep = {os.path.basename(self.control), os.path.basename(self.script)}
        for entry in os.listdir(self.rundir):
            if entry in keep or not entry.startswith(("control-", "dispatch-")):
                continue
            try:
                os.unlink(os.path.join(self.rundir, entry))
            except OSError:
                pass


def _digest(script: str) -> str:
    import hashlib

    return hashlib.sha256(script.encode()).hexdigest()[:8]


def _write_once(path: str, content: bytes) -> None:
    """Create ``path`` with ``content``, leaving an existing file alone."""
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return
    with os.fdopen(fd, "wb") as handle:
        handle.write(content)


def _make_fifo(path: str) -> None:
    try:
        os.mkfifo(path, 0o600)
    except FileExistsError:
        pass


class _Job:
    """One command handed to the dispatcher, and the FIFOs carrying it.

    Every FIFO is opened read-write: a plain reader would see end-of-file
    whenever the other side has not opened its end yet, which is
    indistinguishable from the command having finished. Holding both ends
    means the streams end exactly once, when the exit status says so.
    """

    def __init__(
        self,
        paths: _Paths,
        command: list[str],
        workdir: str,
        env: dict[str, str],
    ):
        self.paths = paths
        self.command = command
        self.workdir = workdir
        self.display = env.get("DISPLAY", "")
        self.directory = os.path.join(
            paths.rundir, f"job-{os.getpid()}-{os.urandom(4).hex()}"
        )
        self.fds: dict[str, int] = {}
        self.opened = False
        self.stdin_done = False
        self.outgoing = b""
        self.selector = selectors.SelectSelector()
        self.stdin: int | None = None
        self.watching_stdin = False
        self.watching_in = False
        self.closed: set[str] = set()

    def start(self) -> bool:
        """Describe the job and hand it over; False if nobody is listening."""
        os.makedirs(self.directory, mode=0o700)
        for name in _FIFOS:
            _make_fifo(self._path(name))
        request = "\n".join(
            [self.workdir, self.display, *self.command]
        ).encode() + b"\n"
        with open(self._path("cmd"), "wb") as handle:
            handle.write(request)
        for name in _FIFOS:
            self.fds[name] = os.open(
                self._path(name), os.O_RDWR | os.O_NONBLOCK
            )
        return self._submit()

    def close(self) -> None:
        for fd in self.fds.values():
            try:
                os.close(fd)
            except OSError:
                pass
        self.fds.clear()
        _remove_tree(self.directory)

    def _path(self, name: str) -> str:
        return os.path.join(self.directory, name)

    def _submit(self) -> bool:
        try:
            control = os.open(self.paths.control, os.O_WRONLY | os.O_NONBLOCK)
        except OSError as e:
            if e.errno in (errno.ENXIO, errno.ENOENT):
                return False
            raise
        try:
            # One short write, so the dispatcher reads it as a whole line
            # even with other clients submitting at the same time.
            os.write(control, (os.path.basename(self.directory) + "\n").encode())
        finally:
            os.close(control)
        return True

    def wait(self) -> int | None:
        """Stream the command's output until it exits; return its status.

        ``None`` means the dispatcher never started the command, so the
        caller can still run it the ordinary way.
        """
        with _forwarded_signals(self._interrupt):
            code = self._pump()
        self._drain()
        return code

    def _pump(self) -> int | None:
        selector = self.selector
        for name in ("out", "err", "status", "ready"):
            selector.register(self.fds[name], selectors.EVENT_READ, name)
        self.stdin = _stdin_fileno()
        self._watch_stdin(True)
        # With no input of our own there is nothing to forward, so the
        # command's input ends as soon as it has opened it.
        self.stdin_done = not self.watching_stdin

        status = b""
        try:
            while True:
                events = selector.select(LIVENESS_INTERVAL)
                if not events and not _listening(self.paths.control):
                    raise TigError(
                        "The container's command dispatcher went away"
                    )
                for key, _ in events:
                    name = key.data
                    if name == "stdin":
                        self._read_stdin(key.fd)
                        continue
                    if name == "in":
                        self._flush_stdin()
                        continue
                    data = _read(key.fd)
                    if name == "ready":
                        self.opened = True
                        self._end_stdin()
                    elif name == "status":
                        status += data
                        if b"\n" in status:
                            return _exit_code(status)
                    else:
                        self._write_out(name, data)
        finally:
            selector.close()

    def _read_stdin(self, fd: int) -> None:
        """Take a chunk of this process's input for the command."""
        try:
            data = os.read(fd, READ_SIZE)
        except OSError:
            data = b""
        if not data:
            self._finish_stdin()
            return
        self.outgoing += data
        self._flush_stdin()

    def _flush_stdin(self) -> None:
        """Pass on as much input as the command will take right now.

        A command reading its input slowly fills the FIFO; reading more from
        our own input then has to wait, or a large input would be dropped.
        """
        fd = self.fds.get("in")
        while self.outgoing and fd is not None:
            try:
                self.outgoing = self.outgoing[os.write(fd, self.outgoing):]
            except BlockingIOError:
                break
            except OSError:
                self.outgoing = b""
                self._finish_stdin()
                return
        self._watch_in(bool(self.outgoing))
        self._watch_stdin(not self.outgoing)
        self._end_stdin()

    def _finish_stdin(self) -> None:
        """Note that this process's input is exhausted."""
        self.stdin_done = True
        self._watch_stdin(False)
        self._end_stdin()

    def _end_stdin(self) -> None:
        """Close our writer so the command reads end-of-file.

        Only once the command holds the other end: closing earlier would
        leave its own open waiting for a writer that never comes.
        """
        if self.outgoing or not (self.opened and self.stdin_done):
            return
        self._watch_in(False)
        fd = self.fds.pop("in", None)
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass

    def _watch_stdin(self, wanted: bool) -> None:
        wanted = wanted and self.stdin is not None and not self.stdin_done
        if wanted == self.watching_stdin:
            return
        try:
            if wanted:
                self.selector.register(self.stdin, selectors.EVENT_READ, "stdin")
            else:
                self.selector.unregister(self.stdin)
        except (KeyError, OSError, ValueError):
            self.stdin_done = True
            self.watching_stdin = False
            return
        self.watching_stdin = wanted

    def _watch_in(self, wanted: bool) -> None:
        fd = self.fds.get("in")
        wanted = wanted and fd is not None
        if wanted == self.watching_in:
            return
        if wanted:
            self.selector.register(fd, selectors.EVENT_WRITE, "in")
        else:
            self.selector.unregister(fd)
        self.watching_in = wanted

    def _write_out(self, name: str, data: bytes) -> None:
        """Pass output on, giving up on a stream nobody is reading.

        ``tig list big.img | head`` closes the pipe early; the command still
        has to be seen through to its exit status.
        """
        if not data or name in self.closed:
            return
        stream = sys.stdout.buffer if name == "out" else sys.stderr.buffer
        try:
            stream.write(data)
            stream.flush()
        except OSError:
            self.closed.add(name)

    def _drain(self) -> None:
        """Emit whatever the command wrote just before exiting."""
        for name in ("out", "err"):
            while True:
                data = _read(self.fds[name])
                if not data:
                    break
                self._write_out(name, data)

    def _interrupt(self, signum: int) -> None:
        """Pass a signal on to the command running in the container."""
        try:
            with open(self._path("pid")) as handle:
                pid = handle.read().strip()
        except OSError:
            return
        if not pid.isdigit():
            return
        from .engine import Engine, EngineError

        name = "TERM" if signum == signal.SIGTERM else "INT"
        try:
            Engine.detect().exec_detached(
                self.paths.container,
                [
                    SHELL,
                    "-c",
                    # The command's whole group, so nothing of it is left
                    # running in the shared container.
                    f"kill -{name} -{pid} 2>/dev/null || kill -{name} {pid}",
                ],
            )
        except (EngineError, OSError):
            pass


def _exit_code(status: bytes) -> int | None:
    text = status.split(b"\n", 1)[0].strip()
    if text == NOT_STARTED:
        return None
    try:
        return int(text)
    except ValueError:
        raise TigError(
            f"The dispatcher reported an unusable exit status: {text!r}"
        ) from None


def _read(fd: int) -> bytes:
    try:
        return os.read(fd, READ_SIZE)
    except BlockingIOError:
        return b""
    except OSError:
        return b""


def _stdin_fileno() -> int | None:
    try:
        return sys.stdin.fileno()
    except (AttributeError, ValueError, OSError):
        return None


def _remove_tree(directory: str) -> None:
    try:
        entries = os.listdir(directory)
    except OSError:
        return
    for entry in entries:
        try:
            os.unlink(os.path.join(directory, entry))
        except OSError:
            pass
    try:
        os.rmdir(directory)
    except OSError:
        pass


class _forwarded_signals:
    """Send interrupts to the container's command instead of only exiting."""

    HANDLED = (signal.SIGINT, signal.SIGTERM)

    def __init__(self, forward):
        self.forward = forward
        self.previous: dict[int, object] = {}

    def __enter__(self) -> "_forwarded_signals":
        for signum in self.HANDLED:
            try:
                self.previous[signum] = signal.signal(
                    signum, lambda number, frame: self.forward(number)
                )
            except (ValueError, OSError):
                pass
        return self

    def __exit__(self, *exc_info) -> bool:
        for signum, handler in self.previous.items():
            try:
                signal.signal(signum, handler)
            except (ValueError, OSError):
                pass
        return False
