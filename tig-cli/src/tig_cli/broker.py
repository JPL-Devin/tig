"""Run commands through an agent in the container, without a shared kernel.

The FIFO dispatcher in :mod:`tig_cli.dispatch` needs host and container to be
the same kernel, which Docker Desktop's virtual machine is not. What crosses
that boundary is a socket, and the container can dial the host even though the
host cannot dial the container: no ports are published on the sidecar.

So a small bash agent, started once in the container, keeps a connection to a
broker on the host, and each ``tig`` hands its command - and its own standard
input, output and error, passed as file descriptors over a unix socket - to
that broker. Nothing but the descriptors is copied: the broker splices them
onto the sockets the agent opens for the command, so output stays byte for
byte what the command wrote, and no exec is created for it.

Only ``bash`` is needed in the container, with the network redirections
``/dev/tcp/host/port`` that the VICAR image's build has. Anything else - no
broker, no agent, a bash without them - simply returns ``None`` and the caller
runs the command the ordinary way.
"""
from __future__ import annotations

import os
import socket
import sys

from .spec import TigError

DISABLE_ENV = "TIG_NO_BROKER"

# Set to use the broker where the FIFO dispatcher would otherwise be
# preferred, which is how it is exercised on Linux.
FORCE_ENV = "TIG_BROKER"

PROTOCOL = "1"

_VERSION: str | None = None

READ_SIZE = 65536

SHELL = "/bin/bash"

# How long a client waits for the broker it just started, and how long the
# broker waits for its agent to call back. Both are one-off costs paid off
# the critical path, but neither may hang a command.
STARTUP_TIMEOUT = 10.0

# The broker goes when nothing has used it for this long, so a laptop is not
# left with a stray process per container it once used.
IDLE_TIMEOUT = 3600.0

AGENT = r"""
# tig command agent: runs commands sent by the broker on the host, so callers
# pay for no container exec. Started by the tig CLI; see tig_cli/broker.py.
host=$1
port=$2
token=$3
[ -n "$host" ] && [ -n "$port" ] && [ -n "$token" ] || exit 64

exec 3<>"/dev/tcp/$host/$port" || exit 65
printf 'hello %s\n' "$token" >&3 || exit 65

while IFS=' ' read -r verb first second <&3; do
    case $verb in
        kill)
            # The command's whole group: a tool that spawns children must
            # not leave them running in the shared container.
            kill "-$second" "-$first" 2>/dev/null ||
                kill "-$second" "$first" 2>/dev/null
            continue
            ;;
        job) ;;
        *) continue ;;
    esac

    IFS= read -r workdir <&3 || break
    IFS= read -r display <&3 || break
    set --
    count=0
    while [ "$count" -lt "$second" ]; do
        IFS= read -r argument <&3 || break 2
        set -- "$@" "$argument"
        count=$((count + 1))
    done

    (
        # Only the loop above speaks for the agent; a command must not be
        # able to write to the connection carrying other jobs.
        exec 3<&-
        exec 4<>"/dev/tcp/$host/$port" || exit 70
        exec 5<>"/dev/tcp/$host/$port" || exit 70
        exec 6<>"/dev/tcp/$host/$port" || exit 70
        printf 'stream %s %s in\n' "$token" "$first" >&4
        printf 'stream %s %s out\n' "$token" "$first" >&5
        printf 'stream %s %s err\n' "$token" "$first" >&6
        [ -d "$workdir" ] || { printf 'unstarted\n' >&4; exit 0; }

        # A group of its own, so the command and its children can be
        # signalled together.
        set -m 2>/dev/null
        (
            cd "$workdir" || exit 126
            [ -n "$display" ] && export DISPLAY=$display
            exec <&4 >&5 2>&6
            # Said plainly here; bash would otherwise name this script and
            # its line number, which mean nothing to the caller.
            command -v -- "$1" >/dev/null 2>&1 || {
                echo "tig: $1: not found" >&2
                exit 127
            }
            exec "$@"
        ) &
        child=$!
        # The input connection carries what the command reads one way and
        # how it is getting on the other; the broker only ever writes.
        printf 'pid %s\n' "$child" >&4
        wait "$child"
        printf 'status %s\n' "$?" >&4
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
    """Run ``command`` through the container's agent.

    Returns:
        The command's exit code, or ``None`` when the broker cannot be used
        and the caller should run the command itself.
    """
    if not usable() or not _expressible(command, workdir, env):
        return None

    address = socket_path(home, container)
    try:
        connection = _connect(address)
    except OSError:
        return None
    if connection is None:
        return None
    with connection:
        return _submit(connection, command, workdir, env)


def usable() -> bool:
    """Whether this path is wanted at all.

    Deliberately not restricted by platform: what it needs is a container
    that can reach the host, which is as true of Docker Desktop as of a
    remote daemon.
    """
    return not os.environ.get(DISABLE_ENV)


def preferred() -> bool:
    """Whether to try the broker before the FIFO dispatcher.

    The dispatcher is a shade faster where it works, so the broker is for
    everywhere else - or for a Linux host being asked to exercise it.
    """
    from . import dispatch

    return usable() and (
        bool(os.environ.get(FORCE_ENV)) or not dispatch.usable()
    )


def ensure_running(engine, container: str, home: str) -> None:
    """Start the container's broker unless one is already answering.

    Called after a command has been run the slow way, so getting the broker
    and its agent up is never on the critical path of a command.
    """
    if not usable():
        return
    try:
        answering = _connect(socket_path(home, container))
    except OSError:
        return
    if answering is not None:
        answering.close()
        return
    _spawn(container, home)


def retire(home: str, container: str) -> None:
    """Take a broker out of service, whatever it is still running."""
    try:
        os.unlink(socket_path(home, container))
    except OSError:
        pass


def socket_path(home: str, container: str) -> str:
    """Where a container's broker listens for the commands to run.

    The agent's own text names the socket, so an installed tig never speaks
    to a broker running an older agent; that one is simply not found.
    """
    return os.path.join(
        home, ".cache", "tig", container, f"broker-{PROTOCOL}-{_version()}"
    )


def _version() -> str:
    """A short name for this agent's text, worked out once."""
    global _VERSION
    if _VERSION is None:
        import zlib

        _VERSION = format(zlib.crc32(AGENT.encode()), "08x")
    return _VERSION


def _connect(address: str) -> socket.socket | None:
    """Connect to a broker, or ``None`` if none is listening.

    A socket file left behind by a broker that died is removed, so the next
    command starts a new one rather than finding this one again.
    """
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        connection.connect(address)
    except (ConnectionRefusedError, FileNotFoundError):
        connection.close()
        try:
            os.unlink(address)
        except OSError:
            pass
        return None
    except OSError:
        connection.close()
        raise
    return connection


def _spawn(container: str, home: str) -> None:
    """Start a broker for this container in the background.

    Nothing waits for it: it is ready within a moment, and until then
    commands simply go the way they went before it existed.
    """
    import subprocess

    try:
        subprocess.Popen(
            [sys.executable, "-m", "tig_cli.broker", container, home],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        pass


def _expressible(command: list[str], workdir: str, env: dict[str, str]) -> bool:
    """Whether the request survives the line-based protocol.

    Arguments holding a newline are vanishingly rare for VICAR tools and not
    worth a heavier protocol; they just go the ordinary way.
    """
    display = env.get("DISPLAY", "")
    return not any("\n" in text for text in [workdir, display, *command])


def _submit(
    connection: socket.socket,
    command: list[str],
    workdir: str,
    env: dict[str, str],
) -> int | None:
    """Hand this process's command and stdio to the broker and wait."""
    import array

    fds = _stdio_fds()
    if fds is None:
        return None
    request = "\n".join([workdir, env.get("DISPLAY", ""), *command]) + "\n"
    payload = request.encode()
    header = len(payload).to_bytes(4, "big")
    try:
        connection.sendmsg(
            [header],
            [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", fds))],
        )
        connection.sendall(payload)
    except OSError:
        return None

    with _forwarded_signals(connection):
        answer = _read_line(connection)
    return _status(answer)


def _stdio_fds() -> list[int] | None:
    fds = []
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            fds.append(stream.fileno())
        except (AttributeError, ValueError, OSError):
            return None
    return fds


def _read_line(connection: socket.socket) -> bytes:
    answer = b""
    while b"\n" not in answer:
        try:
            chunk = connection.recv(READ_SIZE)
        except InterruptedError:
            continue
        except OSError:
            break
        if not chunk:
            break
        answer += chunk
    return answer


def _status(answer: bytes) -> int | None:
    """Read the broker's answer; ``None`` means nothing was run."""
    text = answer.split(b"\n", 1)[0].strip()
    if text in (b"", b"unstarted"):
        return None
    try:
        return int(text)
    except ValueError:
        raise TigError(
            f"The broker reported an unusable exit status: {text!r}"
        ) from None


class _forwarded_signals:
    """Send interrupts to the container's command instead of only exiting."""

    def __init__(self, connection: socket.socket):
        import signal

        self.connection = connection
        self.handled = (signal.SIGINT, signal.SIGTERM)
        self.previous: dict[int, object] = {}

    def __enter__(self) -> "_forwarded_signals":
        import signal

        for signum in self.handled:
            try:
                self.previous[signum] = signal.signal(
                    signum, lambda number, frame: self.forward(number)
                )
            except (ValueError, OSError):
                pass
        return self

    def __exit__(self, *exc_info) -> bool:
        import signal

        for signum, handler in self.previous.items():
            try:
                signal.signal(signum, handler)
            except (ValueError, OSError):
                pass
        return False

    def forward(self, signum: int) -> None:
        try:
            self.connection.sendall(f"signal {signum}\n".encode())
        except OSError:
            pass


def main(argv: list[str]) -> int:
    """Run the broker itself: ``python -m tig_cli.broker <container> <home>``."""
    if len(argv) != 2:
        print("usage: python -m tig_cli.broker <container> <home>", file=sys.stderr)
        return 64

    from .server import Broker

    container, home = argv
    try:
        broker = Broker(container, home)
    except OSError:
        return 69
    return broker.serve()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
