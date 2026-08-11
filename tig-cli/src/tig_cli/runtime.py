"""The host's OCI container runtime, and how tig drives it.

tig needs no Docker in particular: creating the container and running commands
in it is done through a runtime's command line, and every runtime here speaks
the same one (``run``, ``container inspect``, ``exec``, ``rm``). Docker,
Podman, nerdctl and Finch therefore all work, and which one is used is a matter
of what is installed, ``TIG_CONTAINER_RUNTIME``, or the ``runtime`` config key.

:meth:`Runtime.api_host` reports where the runtime's Docker-compatible HTTP API
can be reached, when it has one. That is only an accelerator - the warm path in
:mod:`tig_cli.fast` uses it - so containerd-backed runtimes, which have no such
API, simply go through the command line for everything.
"""
from __future__ import annotations

import hashlib
import json
import os

from .config import Config, env_runtime
from .spec import TigError

# Tried in this order when nothing says which runtime to use.
KNOWN_RUNTIMES = ("docker", "podman", "nerdctl", "finch")

DOCKER_SOCKET = "/var/run/docker.sock"

PODMAN_SOCKET_SUBPATH = os.path.join("podman", "podman.sock")

# How long a runtime command that should answer immediately is waited for.
COMMAND_TIMEOUT = 120.0


class RuntimeCommandError(TigError):
    """A runtime command failed."""


class CommandFailed(RuntimeCommandError):
    """The runtime ran but refused or failed the request."""

    def __init__(self, message: str, exit_code: int):
        super().__init__(message)
        self.exit_code = exit_code


def resolve_runtime_name(config: Config | None = None) -> str | None:
    """The runtime asked for by the environment or the config, if any."""
    from_env = env_runtime()
    if from_env:
        return from_env
    if config is not None and config.runtime:
        return config.runtime
    return None


class Runtime:
    """One container runtime, addressed through its command line."""

    def __init__(self, name: str, executable: str):
        self.name = name
        self.executable = executable

    def __repr__(self) -> str:
        return f"Runtime(name={self.name!r}, executable={self.executable!r})"

    @classmethod
    def detect(cls, config: Config | None = None) -> "Runtime":
        """Find the runtime to use.

        A runtime named explicitly is used even if it is not one tig knows,
        so an unlisted but Docker-compatible command line still works.

        Raises:
            TigError: if the named runtime, or any known one, is not installed.
        """
        # Imported here so the warm path pays for these only when it must.
        import shutil

        requested = resolve_runtime_name(config)
        if requested:
            executable = shutil.which(requested) or (
                requested if os.path.isabs(requested) else None
            )
            if executable is None:
                raise TigError(
                    f"The container runtime '{requested}' was not found on "
                    f"PATH. Unset TIG_CONTAINER_RUNTIME (or the 'runtime' "
                    f"config key) to use whichever runtime is installed."
                )
            return cls(os.path.basename(requested), executable)

        for name in KNOWN_RUNTIMES:
            executable = shutil.which(name)
            if executable:
                return cls(name, executable)

        raise TigError(
            f"No container runtime was found on PATH (tried "
            f"{', '.join(KNOWN_RUNTIMES)}). Install one - "
            f"https://docs.docker.com/get-docker/ or https://podman.io - or "
            f"set TIG_CONTAINER_RUNTIME to the command to use."
        )

    def args(self, *arguments: str) -> list[str]:
        """A full command line for this runtime."""
        return [self.executable, *arguments]

    def run(
        self,
        *arguments: str,
        timeout: float | None = COMMAND_TIMEOUT,
    ) -> str:
        """Run a runtime command and return its standard output.

        Raises:
            CommandFailed: if the command exits non-zero.
            RuntimeCommandError: if it cannot be run at all.
        """
        import subprocess

        try:
            completed = subprocess.run(
                self.args(*arguments),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except (OSError, subprocess.SubprocessError) as e:
            raise RuntimeCommandError(f"Failed to run {self.name}: {e}") from e
        if completed.returncode != 0:
            message = (completed.stderr or completed.stdout).strip()
            raise CommandFailed(
                message or f"{self.name} {arguments[0]} failed", completed.returncode
            )
        return completed.stdout

    def inspect(self, kind: str, reference: str) -> dict:
        """Inspect a container or an image.

        Args:
            kind: Either ``container`` or ``image``.
            reference: The name, id or image reference to inspect.

        Raises:
            CommandFailed: if the object does not exist.
        """
        output = self.run(kind, "inspect", reference)
        try:
            details = json.loads(output)
        except ValueError as e:
            raise RuntimeCommandError(
                f"Unexpected reply from '{self.name} {kind} inspect': {e}"
            ) from e
        if not isinstance(details, list) or not details:
            raise RuntimeCommandError(
                f"'{self.name} {kind} inspect {reference}' described nothing"
            )
        first = details[0]
        if not isinstance(first, dict):
            raise RuntimeCommandError(
                f"'{self.name} {kind} inspect {reference}' described nothing"
            )
        return first

    def api_host(self) -> str | None:
        """Where this runtime's Docker-compatible API is, if it has one.

        ``None`` for containerd-backed runtimes (nerdctl, Finch), which serve
        no such API, and for a runtime whose service is not running; their
        callers fall back to the command line.

        ``DOCKER_HOST`` is honoured for Docker only: another runtime's
        containers are not in that daemon, and answers about them from it
        would be about containers that are not the ones tig created.

        Raises:
            TigError: if the host names an endpoint that cannot be resolved.
        """
        if self.name == "podman":
            return _podman_host()
        if self.name == "docker":
            return (
                os.environ.get("DOCKER_HOST")
                or _docker_context_host()
                or _existing_socket(DOCKER_SOCKET)
            )
        return None


def _existing_socket(path: str) -> str | None:
    return f"unix://{path}" if os.path.exists(path) else None


def _podman_host() -> str | None:
    """Podman's Docker-compatible socket, if its service is running.

    Rootless Podman puts it under the runtime directory, the system service
    under /run; ``CONTAINER_HOST`` overrides both, as for Podman's own client,
    and so does ``DOCKER_HOST`` when it names a Podman socket.
    """
    host = os.environ.get("CONTAINER_HOST")
    if host:
        return host
    # Set by the Podman socket's own users, and what tools expecting Docker
    # are pointed at on a Podman host.
    host = os.environ.get("DOCKER_HOST")
    if host and PODMAN_SOCKET_SUBPATH in host:
        return host
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    candidates = []
    if runtime_dir:
        candidates.append(os.path.join(runtime_dir, PODMAN_SOCKET_SUBPATH))
    candidates.append(os.path.join("/run", PODMAN_SOCKET_SUBPATH))
    for path in candidates:
        found = _existing_socket(path)
        if found:
            return found
    return None


def _docker_context_host() -> str | None:
    """The daemon address of the current Docker CLI context, if any.

    Mirrors what the CLI does, so tig follows ``docker context use`` (Docker
    Desktop, Colima, rootless) rather than assuming the default socket.
    """
    config_dir = os.environ.get("DOCKER_CONFIG") or os.path.join(
        os.path.expanduser("~"), ".docker"
    )
    name = os.environ.get("DOCKER_CONTEXT")
    if not name:
        config = _read_json(os.path.join(config_dir, "config.json"))
        if not isinstance(config, dict):
            return None
        name = config.get("currentContext")
    if not name or name == "default":
        return None

    digest = hashlib.sha256(name.encode()).hexdigest()
    meta = _read_json(
        os.path.join(config_dir, "contexts", "meta", digest, "meta.json")
    )
    if not isinstance(meta, dict):
        # The CLI would fail too, and guessing another daemon could address a
        # container that is not the one the CLI created.
        raise TigError(f"Unknown Docker context: {name}")
    endpoints = meta.get("Endpoints")
    docker = endpoints.get("docker") if isinstance(endpoints, dict) else None
    host = docker.get("Host") if isinstance(docker, dict) else None
    if not isinstance(host, str) or not host:
        raise TigError(f"Docker context {name} has no endpoint")
    return host


def _read_json(path: str):
    try:
        with open(path, "rb") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None
