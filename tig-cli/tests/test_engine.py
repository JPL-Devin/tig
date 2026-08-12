"""Tests for the runtime API client used by the warm path."""
import io
import json
import os
import socket
import struct
import tempfile
import threading

import pytest

from tig_cli.engine import (
    Engine,
    EngineError,
    EngineUnavailable,
    _emit,
    _parse_host,
    _safe,
)
from tig_cli.runtime import Runtime, TigError


def frame(stream, payload):
    """Build one of Docker's multiplexed stdout/stderr frames."""
    return struct.pack(">BxxxI", stream, len(payload)) + payload


class FakeDaemon:
    """A Docker daemon that serves canned replies over a unix socket.

    Real sockets rather than mocks, so the framing, the HTTP parsing and the
    stream hijack are all exercised the way the daemon drives them.
    """

    def __init__(self, path, exit_code=0, output=(), echo_stdin=False):
        self.path = path
        self.exit_code = exit_code
        self.output = output
        self.echo_stdin = echo_stdin
        self.requests = []
        self.received_stdin = b""
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(path)
        self.server.listen(8)
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        while True:
            try:
                connection, _ = self.server.accept()
            except OSError:
                return
            threading.Thread(
                target=self._handle, args=(connection,), daemon=True
            ).start()

    def _handle(self, connection):
        with connection:
            buffer = b""
            while b"\r\n\r\n" not in buffer:
                chunk = connection.recv(4096)
                if not chunk:
                    return
                buffer += chunk
            head, _, rest = buffer.partition(b"\r\n\r\n")
            lines = head.decode().split("\r\n")
            method, path, _ = lines[0].split(" ")
            headers = dict(
                line.split(": ", 1) for line in lines[1:] if ": " in line
            )
            length = int(headers.get("Content-Length", 0))
            while len(rest) < length:
                rest += connection.recv(4096)
            self.requests.append((method, path, rest[:length]))

            if path.endswith("/exec") and method == "POST":
                self._reply(connection, {"Id": "exec-1"})
            elif path.endswith("/start"):
                self._hijack(connection)
            elif path.endswith("/exec-1/json"):
                self._reply(connection, {"ExitCode": self.exit_code})
            elif "/containers/" in path:
                self._reply(
                    connection, {"State": {"Running": True}, "Image": "sha256:i"}
                )
            elif "/images/" in path:
                self._reply(connection, {"Id": "sha256:i"})
            else:
                self._reply(connection, {}, status="404 Not Found")

    def _reply(self, connection, body, status="200 OK"):
        payload = json.dumps(body).encode()
        connection.sendall(
            f"HTTP/1.1 {status}\r\nContent-Type: application/json\r\n"
            f"Content-Length: {len(payload)}\r\n\r\n".encode()
            + payload
        )

    def _hijack(self, connection):
        connection.sendall(
            b"HTTP/1.1 101 UPGRADED\r\nContent-Type: application/vnd.docker"
            b".raw-stream\r\nConnection: Upgrade\r\nUpgrade: tcp\r\n\r\n"
        )
        if self.echo_stdin:
            while True:
                chunk = connection.recv(65536)
                if not chunk:
                    break
                self.received_stdin += chunk
                connection.sendall(frame(1, chunk))
        for stream, payload in self.output:
            connection.sendall(frame(stream, payload))
        connection.shutdown(socket.SHUT_WR)

    def close(self):
        self.server.close()


@pytest.fixture
def daemon():
    """A fake daemon, with any created one closed at the end of the test.

    Its socket lives in a short-named directory of its own: a unix socket
    path is limited to ~100 characters, which pytest's tmp_path exceeds on
    macOS.
    """
    created = []
    directory = tempfile.mkdtemp(prefix="tig")

    def make(**kwargs):
        instance = FakeDaemon(os.path.join(directory, "d.sock"), **kwargs)
        created.append(instance)
        return instance

    yield make
    for instance in created:
        instance.close()
        os.unlink(instance.path)
    os.rmdir(directory)


@pytest.fixture
def quiet_stdin(monkeypatch):
    """Stdin that is at end of file, as it is for `tig cmd` with no input."""
    read, write = os.pipe()
    os.close(write)
    monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.FileIO(read)))
    yield
    os.close(read)


def engine_for(daemon):
    return Engine("unix", daemon.path)


# --- host detection ---

def test_parse_host_reads_a_unix_socket():
    assert _parse_host("unix:///run/user/docker.sock") == (
        "unix", "/run/user/docker.sock"
    )


def test_parse_host_rejects_an_empty_unix_socket():
    with pytest.raises(EngineUnavailable):
        _parse_host("unix://")


def test_parse_host_reads_a_tcp_endpoint():
    assert _parse_host("tcp://127.0.0.1:2375") == ("tcp", ("127.0.0.1", 2375))


def test_parse_host_rejects_tls(monkeypatch):
    monkeypatch.setenv("DOCKER_TLS_VERIFY", "1")
    with pytest.raises(EngineUnavailable):
        _parse_host("tcp://10.0.0.1:2376")


def test_parse_host_rejects_an_unknown_scheme():
    with pytest.raises(EngineUnavailable):
        _parse_host("ssh://user@host")


def test_detect_uses_the_endpoint_the_runtime_reports(monkeypatch):
    runtime = Runtime("podman", "/usr/bin/podman")
    monkeypatch.setattr(runtime, "api_host", lambda: "unix:///podman.sock")

    engine = Engine.detect(runtime)

    assert (engine.kind, engine.address) == ("unix", "/podman.sock")


def test_detect_declines_a_runtime_without_an_api(monkeypatch):
    """nerdctl and Finch have no API; the caller uses their command line."""
    runtime = Runtime("nerdctl", "/usr/bin/nerdctl")

    with pytest.raises(EngineUnavailable, match="nerdctl"):
        Engine.detect(runtime)


def test_detect_reports_an_endpoint_that_cannot_be_resolved(monkeypatch):
    runtime = Runtime("docker", "/usr/bin/docker")

    def unresolvable():
        raise TigError("Unknown Docker context: missing")

    monkeypatch.setattr(runtime, "api_host", unresolvable)

    with pytest.raises(EngineUnavailable, match="Unknown Docker context"):
        Engine.detect(runtime)


def test_detect_finds_the_runtime_when_it_is_not_given(monkeypatch):
    runtime = Runtime("docker", "/usr/bin/docker")
    monkeypatch.setattr(runtime, "api_host", lambda: "unix:///docker.sock")
    monkeypatch.setattr(
        Runtime, "detect", classmethod(lambda cls, config=None: runtime)
    )

    assert Engine.detect().address == "/docker.sock"


def test_detect_declines_when_no_runtime_is_installed(monkeypatch):
    def missing(cls, config=None):
        raise TigError("No container runtime was found on PATH")

    monkeypatch.setattr(Runtime, "detect", classmethod(missing))

    with pytest.raises(EngineUnavailable, match="No container runtime"):
        Engine.detect()


def test_connecting_to_a_missing_socket(tmp_path):
    with pytest.raises(EngineUnavailable):
        Engine("unix", str(tmp_path / "none.sock")).connect()


# --- references ---

@pytest.mark.parametrize(
    "reference",
    [
        "",
        "a b",
        "who?",
        "line\nbreak",
        "50%",
        "/containers/other/json",
        "image/../../containers/other/json",
        "image//json",
    ],
)
def test_unusable_references_are_refused(reference):
    with pytest.raises(EngineUnavailable):
        _safe(reference)


def test_ordinary_references_are_kept():
    assert _safe("ghcr.io/nasa-ammos/tig:opensource") == (
        "ghcr.io/nasa-ammos/tig:opensource"
    )


# --- output framing ---

def test_emit_splits_stdout_from_stderr():
    out, err = io.BytesIO(), io.BytesIO()

    rest = _emit(frame(1, b"hello") + frame(2, b"oops"), False, out, err)

    assert (out.getvalue(), err.getvalue(), rest) == (b"hello", b"oops", b"")


def test_emit_holds_a_partial_frame_back():
    out, err = io.BytesIO(), io.BytesIO()
    data = frame(1, b"hello")

    rest = _emit(data[:-2], False, out, err)

    assert out.getvalue() == b""
    assert _emit(rest + data[-2:], False, out, err) == b""
    assert out.getvalue() == b"hello"


def test_emit_passes_tty_output_through_unframed():
    out, err = io.BytesIO(), io.BytesIO()

    _emit(b"raw text", True, out, err)

    assert (out.getvalue(), err.getvalue()) == (b"raw text", b"")


# --- exec ---

def test_exec_streams_output_and_returns_the_exit_code(
    daemon, quiet_stdin, capfdbinary
):
    fake = daemon(exit_code=7, output=[(1, b"out"), (2, b"err")])

    code = engine_for(fake).exec_command(
        "tig-vicar-1", ["vicar"], "/work", {"DISPLAY": ":0"}, False
    )

    assert code == 7
    assert capfdbinary.readouterr() == (b"out", b"err")


def test_exec_passes_the_command_and_environment(daemon, quiet_stdin):
    fake = daemon()

    engine_for(fake).exec_command(
        "tig-vicar-1", ["gen", "out.img"], "/work", {"DISPLAY": ":1"}, False
    )

    body = json.loads(
        next(b for method, path, b in fake.requests if path.endswith("/exec"))
    )
    assert body["Cmd"] == ["gen", "out.img"]
    assert body["WorkingDir"] == "/work"
    assert body["Env"] == ["DISPLAY=:1"]
    assert body["Tty"] is False


def test_exec_forwards_stdin(daemon, monkeypatch, tmp_path, capfdbinary):
    source = tmp_path / "input"
    source.write_bytes(b"piped input")
    handle = source.open("rb")
    monkeypatch.setattr("sys.stdin", io.TextIOWrapper(handle))
    fake = daemon(echo_stdin=True)

    engine_for(fake).exec_command(
        "tig-vicar-1", ["cat"], "/work", {}, False
    )

    assert fake.received_stdin == b"piped input"
    assert capfdbinary.readouterr().out == b"piped input"
    handle.close()


def test_exec_forwards_stdin_larger_than_one_read(
    daemon, monkeypatch, tmp_path, capfdbinary
):
    payload = os.urandom(3 * 1024 * 1024)
    source = tmp_path / "input"
    source.write_bytes(payload)
    handle = source.open("rb")
    monkeypatch.setattr("sys.stdin", io.TextIOWrapper(handle))
    fake = daemon(echo_stdin=True)

    engine_for(fake).exec_command("tig-vicar-1", ["cat"], "/work", {}, False)

    assert fake.received_stdin == payload
    assert capfdbinary.readouterr().out == payload
    handle.close()


def test_a_refused_request_is_an_error(daemon):
    fake = daemon()
    with pytest.raises(EngineError):
        engine_for(fake).get("/nothing/here")
