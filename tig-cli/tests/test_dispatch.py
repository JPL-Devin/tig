"""Tests for the in-container command dispatcher.

The dispatcher is a POSIX shell script, and the protocol between it and the
CLI is plain files and FIFOs, so it can be exercised for real by running the
very same script next to the tests. What is a bind mount in production is
just a directory here; nothing else about the exchange differs.
"""
import errno
import os
import signal
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from tig_cli import dispatch
from tig_cli.spec import TigError

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="the dispatcher only runs where host and container share a kernel",
)

CONTAINER = "tig-vicar-test"


class Dispatcher:
    """A running dispatcher and the directory it serves."""

    def __init__(self, home, process, paths):
        self.home = str(home)
        self.process = process
        self.paths = paths

    def run(self, command, workdir=None, env=None):
        return dispatch.run(
            CONTAINER,
            self.home,
            command,
            workdir if workdir is not None else self.home,
            env if env is not None else {},
        )


@pytest.fixture
def home(tmp_path, monkeypatch):
    directory = tmp_path / "home"
    directory.mkdir()
    monkeypatch.delenv(dispatch.DISABLE_ENV, raising=False)
    return directory


@pytest.fixture
def dispatcher(home, request):
    shell = getattr(request, "param", dispatch.SHELL)
    paths = dispatch._Paths(str(home), CONTAINER)
    paths.install()
    process = subprocess.Popen([shell, paths.script, paths.rundir, paths.control])
    deadline = time.monotonic() + 10
    while not dispatch._listening(paths.control):
        assert process.poll() is None, "the dispatcher exited at once"
        assert time.monotonic() < deadline, "the dispatcher never listened"
        time.sleep(0.01)
    yield Dispatcher(home, process, paths)
    process.terminate()
    process.wait(timeout=10)


@pytest.fixture
def stdin(monkeypatch):
    """A pipe standing in for this process's input."""
    read, write = os.pipe()
    monkeypatch.setattr(dispatch, "_stdin_fileno", lambda: read)
    yield write
    for fd in (read, write):
        try:
            os.close(fd)
        except OSError:
            pass


def test_runs_a_command_and_reports_its_output(dispatcher, capfd):
    assert dispatcher.run(["sh", "-c", "echo out; echo err >&2"]) == 0

    captured = capfd.readouterr()
    assert captured.out == "out\n"
    assert captured.err == "err\n"


def test_reports_the_exit_status(dispatcher):
    assert dispatcher.run(["sh", "-c", "exit 7"]) == 7


def test_runs_in_the_requested_directory(dispatcher, home, capfd):
    workdir = home / "work"
    workdir.mkdir()

    assert dispatcher.run(["pwd"], workdir=str(workdir)) == 0

    assert capfd.readouterr().out.strip() == str(workdir)


def test_passes_the_display_through(dispatcher, capfd):
    dispatcher.run(["sh", "-c", "echo $DISPLAY"], env={"DISPLAY": ":9"})

    assert capfd.readouterr().out.strip() == ":9"


def test_passes_arguments_exactly(dispatcher, capfd):
    awkward = ["a b", "*", "", "-n", "'quoted'", "$HOME", "tab\there"]
    echo_each = 'for a in "$@"; do echo "[$a]"; done'

    assert dispatcher.run(["sh", "-c", echo_each, "sh", *awkward]) == 0

    printed = capfd.readouterr().out.splitlines()
    assert printed == [f"[{argument}]" for argument in awkward]


def test_carries_large_output(dispatcher, capfd):
    size = 4 << 20

    assert dispatcher.run(["sh", "-c", f"yes 0123456789 | head -c {size}"]) == 0

    assert len(capfd.readouterr().out) == size


def test_forwards_this_process_s_input(dispatcher, stdin, capfd):
    os.write(stdin, b"hello\n")
    os.close(stdin)

    assert dispatcher.run(["cat"]) == 0

    assert capfd.readouterr().out == "hello\n"


def test_forwards_input_larger_than_a_pipe(dispatcher, stdin, capfd):
    payload = b"x" * (4 << 20)

    def feed():
        os.write(stdin, payload)
        os.close(stdin)

    writer = threading.Thread(target=feed)
    writer.start()
    try:
        assert dispatcher.run(["cat"]) == 0
    finally:
        writer.join(timeout=30)

    assert len(capfd.readouterr().out) == len(payload)


def test_input_ends_for_the_command(dispatcher, stdin, capfd):
    """A command reading to end-of-file must not wait for one that never comes."""
    os.close(stdin)

    assert dispatcher.run(["sh", "-c", "cat; echo done"]) == 0

    assert capfd.readouterr().out == "done\n"


def test_keeps_concurrent_commands_apart(dispatcher):
    results = {}

    def call(index):
        results[index] = subprocess.run(
            [
                sys.executable,
                "-c",
                _CLIENT % (repr(dispatcher.home), index),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )

    threads = [threading.Thread(target=call, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    for index, result in sorted(results.items()):
        assert result.stdout == f"{index}\n" * 200, result.stderr
        assert result.stderr.strip().endswith(f"code {index % 5}")


_CLIENT = """
import sys
from tig_cli import dispatch

home, index = %s, %s
script = 'i=0; while [ $i -lt 200 ]; do echo %%s; i=$((i+1)); done; ' \\
         'echo code %%s >&2; exit %%s' %% (index, index %% 5, index %% 5)
code = dispatch.run(
    'tig-vicar-test', home, ['sh', '-c', script], home, {}
)
sys.exit(0 if code == index %% 5 else 1)
"""


@pytest.mark.parametrize("dispatcher", ["/bin/bash"], indirect=True)
def test_a_command_and_its_children_can_be_signalled_together(
    dispatcher, home
):
    """Signalling must not leave a tool's children in the container.

    Each job is put in a process group of its own, which needs a shell with
    job control -- the image's ``/bin/sh`` is bash, so this runs under bash.
    """
    marker = home / "child"
    script = f"sleep 300 & echo $! > {marker}; wait"
    code = []
    running = threading.Thread(
        target=lambda: code.append(dispatcher.run(["sh", "-c", script]))
    )
    running.start()
    _wait_for(marker)
    child = int(marker.read_text())

    os.killpg(_job_pid(dispatcher), signal.SIGTERM)

    running.join(timeout=30)
    assert code, "the command never ended"
    deadline = time.monotonic() + 30
    while _alive(child):
        assert time.monotonic() < deadline, "a child of the command was left"
        time.sleep(0.05)


def _job_pid(dispatcher):
    """The process the dispatcher started for the one job in flight."""
    for job in os.listdir(dispatcher.paths.rundir):
        recorded = os.path.join(dispatcher.paths.rundir, job, "pid")
        if os.path.exists(recorded):
            return int(open(recorded).read())
    raise AssertionError("the dispatcher recorded no job")


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _wait_for(path, timeout=30):
    deadline = time.monotonic() + timeout
    while not path.exists():
        assert time.monotonic() < deadline, f"{path} never appeared"
        time.sleep(0.02)


def test_survives_output_nobody_is_reading(dispatcher, monkeypatch):
    """``tig list big.img | head`` must still report the command's status.

    Reporting the dispatcher unused instead would have the caller run the
    command all over again.
    """

    class Broken:
        def write(self, data):
            raise BrokenPipeError(errno.EPIPE, "broken pipe")

        def flush(self):
            pass

    monkeypatch.setattr(sys, "stdout", SimpleNamespace(buffer=Broken()))

    assert dispatcher.run(["sh", "-c", "echo plenty; exit 5"]) == 5


def test_reports_a_command_that_does_not_exist(dispatcher, capfd):
    assert dispatcher.run(["definitely-not-a-tool"]) == 127

    assert capfd.readouterr().err == "tig: definitely-not-a-tool: not found\n"


def test_declines_when_no_dispatcher_is_listening(home):
    assert dispatch.run(CONTAINER, str(home), ["true"], str(home), {}) is None


def test_declines_when_disabled(dispatcher, monkeypatch):
    monkeypatch.setenv(dispatch.DISABLE_ENV, "1")

    assert dispatcher.run(["true"]) is None


def test_declines_arguments_it_cannot_express(dispatcher):
    assert dispatcher.run(["echo", "two\nlines"]) is None


def test_declines_when_the_directory_is_missing(dispatcher, home):
    assert dispatcher.run(["true"], workdir=str(home / "gone")) is None


def test_reports_a_dispatcher_that_disappears(dispatcher, monkeypatch):
    """A command whose dispatcher dies must not wait for output forever."""
    monkeypatch.setattr(dispatch, "LIVENESS_INTERVAL", 0.1)
    raised = []

    def call():
        try:
            dispatcher.run(["sleep", "30"])
        except TigError as e:
            raised.append(e)

    caller = threading.Thread(target=call)
    caller.start()
    time.sleep(0.3)
    dispatcher.process.kill()
    caller.join(timeout=30)

    assert not caller.is_alive()
    assert raised


def test_leaves_nothing_behind(dispatcher):
    dispatcher.run(["true"])

    remaining = [
        entry
        for entry in os.listdir(dispatcher.paths.rundir)
        if entry.startswith("job-")
    ]
    assert remaining == []


def test_retiring_stops_new_commands(dispatcher):
    dispatch.retire(dispatcher.home, CONTAINER)

    assert dispatcher.run(["true"]) is None


def test_a_running_command_outlives_the_dispatcher_being_retired(
    dispatcher, monkeypatch
):
    """Retiring only stops new commands; a quiet one in flight is not dead."""
    monkeypatch.setattr(dispatch, "LIVENESS_INTERVAL", 0.05)
    threading.Timer(0.2, dispatch.retire, (dispatcher.home, CONTAINER)).start()

    assert dispatcher.run(["sh", "-c", "sleep 1; exit 3"]) == 3


def test_a_new_script_gets_its_own_dispatcher(dispatcher, home, monkeypatch):
    """An upgraded CLI must not talk to a dispatcher from the old one."""
    monkeypatch.setattr(dispatch, "SCRIPT", dispatch.SCRIPT + "\n# changed\n")

    assert dispatch._Paths(str(home), CONTAINER).control != dispatcher.paths.control
    assert dispatcher.run(["true"]) is None


def test_verification_is_remembered_for_a_while(home):
    assert dispatch.recently_verified(str(home), CONTAINER, 30) is False

    os.makedirs(dispatch._Paths(str(home), CONTAINER).rundir, exist_ok=True)
    dispatch.mark_verified(str(home), CONTAINER)

    assert dispatch.recently_verified(str(home), CONTAINER, 30) is True
    assert dispatch.recently_verified(str(home), CONTAINER, 0) is False

def test_signalling_addresses_the_runtime_holding_the_container(
    home, monkeypatch
):
    """The runtime is passed on, not picked again: on a host with several
    installed, another one has no such container and the tool would run on."""
    from tig_cli import engine
    from tig_cli.runtime import Runtime

    runtime = Runtime("podman", "/usr/bin/podman")
    job = dispatch._Job(
        dispatch._Paths(str(home), CONTAINER), ["true"], str(home), {}, runtime
    )
    os.makedirs(job.directory, exist_ok=True)
    with open(os.path.join(job.directory, "pid"), "w") as handle:
        handle.write("4242")

    asked = []
    detected = SimpleNamespace(exec_detached=lambda *args, **kwargs: None)
    monkeypatch.setattr(
        engine.Engine,
        "detect",
        classmethod(lambda cls, rt=None: asked.append(rt) or detected),
    )

    job._interrupt(signal.SIGINT)

    assert asked == [runtime]
