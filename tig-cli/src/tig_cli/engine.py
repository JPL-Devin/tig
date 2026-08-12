"""Minimal Docker Engine API client used by the warm path.

A runtime CLI is a Go binary whose startup alone costs more than the work it
does for an ``exec``. That is avoided here: this speaks the Engine API over its
socket with nothing but the standard library, which is what makes a warm ``tig``
command cheaper than the shell wrapper it replaces. Docker and Podman both
serve this API; runtimes that serve none (nerdctl, Finch) go through their
command line instead.

Only what the warm path needs is implemented (inspect a container, an image or
an exec, and run one exec). Anything unusual - a remote or TLS endpoint, a
missing socket, an unexpected reply - raises :class:`EngineUnavailable`, and
the caller falls back to :mod:`tig_cli.container`.
"""
from __future__ import annotations

import json
import os
import selectors
import signal
import socket
import struct
import sys

from .runtime import Runtime
from .spec import TigError

API_VERSION = "v1.41"


# Docker's stdout/stderr framing for non-TTY streams: a one-byte stream number,
# three padding bytes, then a big-endian payload length.
FRAME_HEADER = struct.Struct(">BxxxI")
FRAME_HEADER_SIZE = FRAME_HEADER.size
STDERR_STREAM = 2

READ_SIZE = 65536

# How much unsent stdin to hold before pausing reads from the caller.
STDIN_HIGH_WATER = 1 << 20

CONNECT_TIMEOUT = 5.0


class EngineError(TigError):
    """The daemon was reached but refused or failed the request."""


class EngineUnavailable(EngineError):
    """This runtime cannot be driven over its API; use its command line."""


class EngineNotFound(EngineError):
    """The runtime has no such container, image or exec."""


def _safe(reference: str) -> str:
    """Check a name is usable in a request line, rather than escaping it.

    Docker references only ever contain URL-safe characters, so anything
    else is a sign the caller is not describing a real container or image.
    Path segments matter too: a reference may contain slashes, but one that
    walks the path would reach an endpoint other than the one intended.
    """
    unusable = (
        not reference
        or any(c.isspace() or c in "?#%" or ord(c) < 32 for c in reference)
        or any(segment in ("", ".", "..") for segment in reference.split("/"))
    )
    if unusable:
        raise EngineUnavailable(f"Unusable Docker reference: {reference!r}")
    return reference


def _parse_host(host: str) -> tuple[str, object]:
    """Turn a ``DOCKER_HOST``-style address into connection details."""
    if host.startswith("unix://"):
        path = host[len("unix://"):]
        if not path:
            raise EngineUnavailable(f"Endpoint names no socket: {host}")
        return "unix", path
    if host.startswith(("tcp://", "http://")):
        if os.environ.get("DOCKER_TLS_VERIFY"):
            raise EngineUnavailable("TLS-protected daemons are not supported")
        rest = host.split("://", 1)[1]
        address, _, port = rest.partition(":")
        return "tcp", (address, int(port or 2375))
    raise EngineUnavailable(f"Unsupported runtime endpoint: {host}")


class Engine:
    """A connection factory for one runtime's Docker-compatible API."""

    def __init__(self, kind: str, address):
        self.kind = kind
        self.address = address

    @classmethod
    def detect(cls, runtime: Runtime | None = None) -> "Engine":
        """Locate the API endpoint of the host's container runtime.

        Args:
            runtime: The runtime to use; detected from the host if absent.

        Raises:
            EngineUnavailable: if the runtime serves no reachable API.
        """
        try:
            runtime = runtime or Runtime.detect()
            host = runtime.api_host()
        except TigError as e:
            raise EngineUnavailable(str(e)) from e
        if not host:
            raise EngineUnavailable(
                f"{runtime.name} serves no Docker-compatible API socket"
            )
        return cls(*_parse_host(host))

    def connect(self) -> "Connection":
        try:
            if self.kind == "unix":
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.settimeout(CONNECT_TIMEOUT)
                sock.connect(self.address)
            else:
                sock = socket.create_connection(self.address, CONNECT_TIMEOUT)
        except OSError as e:
            raise EngineUnavailable(f"Cannot reach the container runtime: {e}") from e
        sock.settimeout(None)
        return Connection(sock)

    def get(self, path: str):
        return self._json_request("GET", path, None)

    def post(self, path: str, body: dict | None = None):
        return self._json_request("POST", path, body)

    def _json_request(self, method: str, path: str, body):
        connection = self.connect()
        try:
            connection.send(method, f"/{API_VERSION}{path}", body)
            status, headers = connection.read_headers()
            payload = connection.read_body(headers)
        finally:
            connection.close()

        if status == 404:
            raise EngineNotFound(f"Not found: {path}")
        if status >= 400:
            raise EngineError(_message(payload, status))
        if not payload:
            return None
        try:
            return json.loads(payload)
        except ValueError as e:
            raise EngineUnavailable(f"Unexpected reply from Docker: {e}") from e

    def inspect_container(self, name: str) -> dict:
        return self.get(f"/containers/{_safe(name)}/json")

    def inspect_image(self, reference: str) -> dict:
        return self.get(f"/images/{_safe(reference)}/json")

    def inspect_exec(self, exec_id: str) -> dict:
        return self.get(f"/exec/{_safe(exec_id)}/json") or {}

    def exec_command(
        self,
        container: str,
        command: list[str],
        workdir: str,
        env: dict[str, str],
        tty: bool,
    ) -> int:
        """Run ``command`` in a running container and stream it to this process.

        Returns:
            The command's exit code.
        """
        created = self.post(
            f"/containers/{_safe(container)}/exec",
            {
                "AttachStdin": True,
                "AttachStdout": True,
                "AttachStderr": True,
                "Tty": tty,
                "Cmd": command,
                "WorkingDir": workdir,
                "Env": [f"{k}={v}" for k, v in env.items()],
            },
        )
        exec_id = (created or {}).get("Id")
        if not exec_id:
            raise EngineUnavailable("Docker did not return an exec id")

        connection = self.connect()
        try:
            connection.send(
                "POST",
                f"/{API_VERSION}/exec/{exec_id}/start",
                {"Detach": False, "Tty": tty},
                upgrade=True,
            )
            status, headers = connection.read_headers()
            if status >= 400:
                raise EngineError(_message(connection.read_body(headers), status))
            self._resize(exec_id, tty)
            with _terminal_resizes(lambda: self._resize(exec_id, tty)):
                connection.pump(tty)
        finally:
            connection.close()

        return self._exit_code(exec_id)

    def exec_detached(
        self,
        container: str,
        command: list[str],
        env: dict[str, str] | None = None,
    ) -> None:
        """Start ``command`` in the container and leave it running."""
        created = self.post(
            f"/containers/{_safe(container)}/exec",
            {
                "AttachStdin": False,
                "AttachStdout": False,
                "AttachStderr": False,
                "Tty": False,
                "Cmd": command,
                "Env": [f"{name}={value}" for name, value in (env or {}).items()],
            },
        )
        exec_id = (created or {}).get("Id")
        if not exec_id:
            raise EngineUnavailable("Docker did not return an exec id")
        self.post(f"/exec/{exec_id}/start", {"Detach": True, "Tty": False})

    def _resize(self, exec_id: str, tty: bool) -> None:
        """Match the exec's pseudo-terminal to the caller's terminal."""
        if not tty:
            return
        try:
            size = os.get_terminal_size(sys.stdout.fileno())
        except (OSError, ValueError):
            return
        try:
            self.post(f"/exec/{exec_id}/resize?h={size.lines}&w={size.columns}")
        except EngineError:
            # A terminal that cannot be resized is not worth failing over.
            pass

    def _exit_code(self, exec_id: str) -> int:
        result = self.get(f"/exec/{exec_id}/json") or {}
        code = result.get("ExitCode")
        if code is None:
            raise EngineUnavailable("Docker did not report an exit code")
        return int(code)


class _terminal_resizes:
    """Keep the exec's pseudo-terminal in step with the caller's window."""

    def __init__(self, resize):
        self.resize = resize
        self.previous = None

    def __enter__(self):
        if not hasattr(signal, "SIGWINCH"):
            return self
        try:
            self.previous = signal.signal(
                signal.SIGWINCH, lambda signum, frame: self.resize()
            )
        except ValueError:
            # Not the main thread; the initial size still applies.
            self.previous = None
        return self

    def __exit__(self, *exc_info):
        if self.previous is not None:
            signal.signal(signal.SIGWINCH, self.previous)
        return False


def _message(payload: bytes, status: int) -> str:
    try:
        return json.loads(payload).get("message") or f"Docker returned {status}"
    except (ValueError, AttributeError):
        return f"Docker returned {status}"


class Connection:
    """One HTTP/1.1 connection to the daemon, with hijacked-stream support."""

    def __init__(self, sock: socket.socket):
        self.sock = sock
        self.buffer = b""
        self.stdin_ended = False

    def send(
        self,
        method: str,
        path: str,
        body: dict | None = None,
        upgrade: bool = False,
    ) -> None:
        encoded = json.dumps(body).encode() if body is not None else b""
        lines = [
            f"{method} {path} HTTP/1.1",
            "Host: docker",
            f"Content-Length: {len(encoded)}",
        ]
        if body is not None:
            lines.append("Content-Type: application/json")
        if upgrade:
            lines += ["Connection: Upgrade", "Upgrade: tcp"]
        request = ("\r\n".join(lines) + "\r\n\r\n").encode() + encoded
        try:
            self.sock.sendall(request)
        except OSError as e:
            raise EngineUnavailable(f"Docker connection failed: {e}") from e

    def _fill(self) -> bytes:
        try:
            chunk = self.sock.recv(READ_SIZE)
        except OSError as e:
            raise EngineUnavailable(f"Docker connection failed: {e}") from e
        if not chunk:
            raise EngineUnavailable("Docker closed the connection")
        self.buffer += chunk
        return chunk

    def read_headers(self) -> tuple[int, dict[str, str]]:
        while b"\r\n\r\n" not in self.buffer:
            self._fill()
        head, _, self.buffer = self.buffer.partition(b"\r\n\r\n")
        lines = head.decode("latin-1").split("\r\n")
        try:
            status = int(lines[0].split(" ")[1])
        except (IndexError, ValueError) as e:
            raise EngineUnavailable("Malformed reply from Docker") from e
        headers = {}
        for line in lines[1:]:
            name, _, value = line.partition(":")
            headers[name.strip().lower()] = value.strip()
        return status, headers

    def read_body(self, headers: dict[str, str]) -> bytes:
        if headers.get("transfer-encoding") == "chunked":
            return self._read_chunked()
        length = headers.get("content-length")
        if length is None:
            return self._read_to_eof()
        remaining = int(length)
        while len(self.buffer) < remaining:
            self._fill()
        body, self.buffer = self.buffer[:remaining], self.buffer[remaining:]
        return body

    def _read_chunked(self) -> bytes:
        body = b""
        while True:
            while b"\r\n" not in self.buffer:
                self._fill()
            line, _, self.buffer = self.buffer.partition(b"\r\n")
            size = int(line.split(b";")[0], 16)
            if size == 0:
                return body
            while len(self.buffer) < size + 2:
                self._fill()
            body += self.buffer[:size]
            self.buffer = self.buffer[size + 2:]

    def _read_to_eof(self) -> bytes:
        try:
            while True:
                chunk = self.sock.recv(READ_SIZE)
                if not chunk:
                    break
                self.buffer += chunk
        except OSError as e:
            raise EngineUnavailable(f"Docker connection failed: {e}") from e
        body, self.buffer = self.buffer, b""
        return body

    def pump(self, tty: bool) -> None:
        """Shuttle bytes between this process and the hijacked stream.

        Ends when the daemon closes the stream, which it does when the exec'd
        command exits.

        Reads and writes are interleaved rather than sequential: a command
        that is fed a large stdin while producing output would otherwise
        deadlock, with each side waiting for the other to drain.
        """
        stdout = sys.stdout.buffer
        stderr = sys.stderr.buffer
        incoming = self.buffer
        self.buffer = b""
        outgoing = b""

        self.sock.setblocking(False)
        # select() rather than epoll: stdin is often a redirected regular
        # file, which epoll refuses to watch.
        selector = selectors.SelectSelector()
        selector.register(self.sock, selectors.EVENT_READ)
        stdin = _Stdin(selector)

        try:
            while True:
                incoming = _emit(incoming, tty, stdout, stderr)
                self._watch_socket(selector, writing=bool(outgoing))
                # Read ahead only while the daemon keeps up with what it has
                # already been given.
                stdin.watch(len(outgoing) < STDIN_HIGH_WATER)
                if stdin.finished and not outgoing:
                    self._end_stdin()

                for key, event in selector.select():
                    if key.fileobj is not self.sock:
                        outgoing += stdin.read()
                        continue

                    if event & selectors.EVENT_WRITE:
                        outgoing = self._send_available(outgoing)
                    if event & selectors.EVENT_READ:
                        chunk = _receive(self.sock)
                        if chunk is None:
                            _emit(incoming, tty, stdout, stderr, final=True)
                            return
                        incoming += chunk
        finally:
            selector.close()
            _flush(stdout)
            _flush(stderr)

    def _watch_socket(self, selector: selectors.BaseSelector, writing: bool) -> None:
        events = selectors.EVENT_READ
        if writing:
            events |= selectors.EVENT_WRITE
        selector.modify(self.sock, events)

    def _send_available(self, data: bytes) -> bytes:
        """Send what the socket will take right now; return the remainder."""
        try:
            sent = self.sock.send(data)
        except BlockingIOError:
            return data
        except OSError:
            return b""
        return data[sent:]

    def _end_stdin(self) -> None:
        """Tell the container's command that its input is finished."""
        if self.stdin_ended:
            return
        self.stdin_ended = True
        try:
            self.sock.shutdown(socket.SHUT_WR)
        except OSError:
            pass

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


class _Stdin:
    """The caller's standard input, watched only while it is wanted."""

    def __init__(self, selector: selectors.BaseSelector):
        self.selector = selector
        self.fd = _stdin_fileno()
        self.watching = False
        self.finished = self.fd is None

    def watch(self, wanted: bool) -> None:
        """Start or stop selecting on stdin, applying backpressure."""
        if self.finished or wanted == self.watching:
            return
        try:
            if wanted:
                self.selector.register(self.fd, selectors.EVENT_READ)
            else:
                self.selector.unregister(self.fd)
        except (KeyError, OSError, ValueError):
            self.finished = True
            self.watching = False
            return
        self.watching = wanted

    def read(self) -> bytes:
        """Read what is available; empty once stdin is exhausted."""
        try:
            data = os.read(self.fd, READ_SIZE)
        except OSError:
            data = b""
        if not data:
            self.watch(False)
            self.finished = True
        return data


def _receive(sock: socket.socket) -> bytes | None:
    """Read from the hijacked stream; ``None`` once the daemon closes it."""
    try:
        chunk = sock.recv(READ_SIZE)
    except BlockingIOError:
        return b""
    except OSError:
        return None
    return chunk or None


def _stdin_fileno() -> int | None:
    try:
        return sys.stdin.fileno()
    except (AttributeError, ValueError, OSError):
        return None


def _put(stream, data: bytes) -> None:
    """Write output, letting go of a stream nobody is reading.

    ``tig list big.img | head`` closes the pipe early; the command still has
    to be seen through to its exit status.
    """
    if not data:
        return
    try:
        stream.write(data)
    except OSError:
        return
    _flush(stream)


def _flush(stream) -> None:
    try:
        stream.flush()
    except OSError:
        pass


def _emit(data: bytes, tty: bool, stdout, stderr, final: bool = False) -> bytes:
    """Write what is complete in ``data``; return what is not yet."""
    if tty:
        _put(stdout, data)
        return b""

    while len(data) >= FRAME_HEADER_SIZE:
        stream, size = FRAME_HEADER.unpack_from(data)
        if len(data) < FRAME_HEADER_SIZE + size:
            break
        payload = data[FRAME_HEADER_SIZE:FRAME_HEADER_SIZE + size]
        _put(stderr if stream == STDERR_STREAM else stdout, payload)
        data = data[FRAME_HEADER_SIZE + size:]

    if final:
        return b""
    return data
