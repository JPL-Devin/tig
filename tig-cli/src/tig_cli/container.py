"""Container lifecycle management."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Dict, Any, Optional
import docker

from .path_translator import PathTranslator

DEFAULT_IMAGE = "ghcr.io/nasa-ammos/tig/terrain-intelligence-generator:opensource"

# The VICAR image is published for linux/amd64 only; force that platform so
# pulls and runs succeed on arm64 hosts (via emulation). No-op on amd64.
IMAGE_PLATFORM = "linux/amd64"

CONTAINER_PREFIX = "tig-vicar"

# Where calibration files (VISOR / mars_calibration_*) are mounted, matching
# the layout VICAR's MARS programs expect.
CALIBRATION_MOUNT = "/usr/local/vicar/mars_calib"


class TigError(Exception):
    """User-facing error; reported without a traceback."""


def get_container_image() -> str:
    """Return the Docker image to use for VICAR execution.

    Reads CONTAINER_IMAGE environment variable. Falls back to the
    opensource image if not set.
    """
    return os.environ.get("CONTAINER_IMAGE", DEFAULT_IMAGE)


def get_calibration_path() -> Optional[str]:
    """Return the host path holding MARS/VISOR calibration files, if any.

    Reads MARS_CONFIG_PATH, the same variable the toolkit used.
    """
    return os.environ.get("MARS_CONFIG_PATH") or None


class ContainerManager:
    """Manages VICAR container lifecycle and execution.

    The container is long-lived: it is created on first use and then reused by
    later invocations, so repeated VICAR commands (a terrain pipeline is
    typically dozens) pay the container start cost once rather than every time.
    Its name encodes the image and mount configuration, so a container is only
    reused when it would be created identically today.
    """

    def __init__(
        self,
        image: str,
        disable_path_translation: bool = False,
        calibration_path: Optional[str] = None,
    ):
        """Initialize the container manager.

        Args:
            image: Docker image name and tag
            disable_path_translation: Skip path translation (for debugging)
            calibration_path: Host path with MARS/VISOR calibration files
        """
        self.image = image
        self.disable_path_translation = disable_path_translation
        if shutil.which("docker") is None:
            raise TigError(
                "The 'docker' command was not found on PATH. "
                "See https://docs.docker.com/get-docker/"
            )
        try:
            self.client = docker.from_env()
        except docker.errors.DockerException as e:
            raise TigError(
                "Failed to connect to Docker. Is the Docker daemon running?"
            ) from e
        self.home = os.environ.get("HOME") or str(Path.home())
        self.calibration_path = self._resolve_calibration_path(calibration_path)
        self.translator = PathTranslator(self.home)
        self.container: Optional[Any] = None
        self.container_name = CONTAINER_PREFIX
        self.command: Optional[subprocess.Popen] = None

    def _resolve_calibration_path(self, path: Optional[str]) -> Optional[str]:
        if path is None:
            return None
        if not os.path.isdir(path):
            raise TigError(f"Calibration path is not a directory: {path}")
        return str(Path(path).resolve())

    def _build_volume_mounts(
        self,
        writable_paths: List[str]
    ) -> Dict[str, Dict[str, str]]:
        """Build volume mount configuration.

        The host filesystem is mounted read-only at /host so tools can read
        inputs from anywhere. Locations the user is expected to write to - the
        home directory, the current directory and any --writable-path - are
        additionally mounted read-write.

        Args:
            writable_paths: Additional paths to mount as read-write

        Returns:
            Dictionary of volume mounts for docker-py
        """
        volumes = {
            "/": {"bind": "/host", "mode": "ro"},
            self.home: {"bind": self.home, "mode": "rw"},
        }

        for path in [os.getcwd()] + list(writable_paths):
            if not os.path.isdir(path):
                continue
            resolved = str(Path(path).resolve())
            if Path(resolved).is_relative_to(self.home):
                continue
            volumes[resolved] = {"bind": f"/host{resolved}", "mode": "rw"}

        if self.calibration_path:
            volumes[self.calibration_path] = {
                "bind": CALIBRATION_MOUNT,
                "mode": "ro",
            }

        return volumes

    def _run_kwargs(self, volumes: Dict[str, Dict[str, str]]) -> Dict[str, Any]:
        """Build the containers.run keyword arguments, minus the name."""
        environment = {"HOME": self.home}
        if self.calibration_path:
            environment["MARS_CONFIG_PATH"] = CALIBRATION_MOUNT

        kwargs: Dict[str, Any] = {
            "image": self.image,
            "volumes": volumes,
            "environment": environment,
            "detach": True,
            "command": "tail -f /dev/null",
            "platform": IMAGE_PLATFORM,
        }

        if sys.platform != "darwin":
            volumes["/tmp/.X11-unix"] = {"bind": "/tmp/.X11-unix", "mode": "rw"}
            kwargs["network_mode"] = "host"
            # Run as the invoking user so output files are owned by them
            # rather than by root. Docker Desktop already maps ownership.
            kwargs["user"] = f"{os.getuid()}:{os.getgid()}"
            kwargs["group_add"] = [str(os.getgid())]

        return kwargs

    def _container_name_for(self, run_kwargs: Dict[str, Any]) -> str:
        """Derive a container name that identifies this configuration.

        Anything that would change how the container is created - image,
        mounts, user, platform - changes the name, so a container is only ever
        reused when recreating it would produce the same thing.
        """
        fingerprint = json.dumps(run_kwargs, sort_keys=True, default=str)
        digest = hashlib.sha256(fingerprint.encode()).hexdigest()[:12]
        return f"{CONTAINER_PREFIX}-{digest}"

    def _reusable(self, container: Any) -> bool:
        """Whether an existing container can serve this invocation.

        The name covers the run configuration; the image ID is checked
        separately so that re-pulling a moving tag such as :opensource takes
        effect instead of silently reusing the old image.
        """
        try:
            expected = self.client.images.get(self.image).id
        except docker.errors.ImageNotFound:
            # Not pulled locally yet, so it cannot be what is running.
            return False
        except docker.errors.APIError:
            return False
        return container.image.id == expected

    def ensure_container(self, writable_paths: List[str]) -> None:
        """Ensure a container matching this configuration is running.

        Reuses a suitable existing container, starts it if it is stopped, and
        otherwise creates it.

        Args:
            writable_paths: Additional paths to mount as read-write
        """
        volumes = self._build_volume_mounts(writable_paths)
        run_kwargs = self._run_kwargs(volumes)
        self.container_name = self._container_name_for(run_kwargs)

        existing = self._get_container(self.container_name)
        if existing is not None:
            if self._reusable(existing):
                if existing.status != "running":
                    existing.start()
                self.container = existing
                return
            existing.remove(force=True)

        try:
            self.container = self.client.containers.run(
                name=self.container_name, **run_kwargs
            )
        except docker.errors.ImageNotFound as e:
            raise TigError(f"Image not found: {self.image}") from e
        except docker.errors.APIError as e:
            raise TigError(
                f"Failed to start container from image {self.image}: "
                f"{e.explanation or e}"
            ) from e

    def _get_container(self, name: str) -> Optional[Any]:
        try:
            return self.client.containers.get(name)
        except docker.errors.NotFound:
            return None
        except docker.errors.APIError:
            return None

    def shutdown(self) -> int:
        """Remove every container this tool has created.

        Returns:
            Number of containers removed
        """
        removed = 0
        for container in self.client.containers.list(
            all=True, filters={"name": CONTAINER_PREFIX}
        ):
            try:
                container.remove(force=True)
                removed += 1
            except docker.errors.APIError:
                pass
        self.container = None
        return removed

    def status(self) -> List[Dict[str, str]]:
        """Describe the containers this tool has created."""
        return [
            {
                "name": container.name,
                "status": container.status,
                "image": (
                    container.image.tags[0]
                    if container.image.tags
                    else container.image.short_id
                ),
            }
            for container in self.client.containers.list(
                all=True, filters={"name": CONTAINER_PREFIX}
            )
        ]

    def execute_vicar_command(
        self,
        vicar_tool: str,
        args: List[str]
    ) -> int:
        """Execute a VICAR command in the container.

        Args:
            vicar_tool: VICAR tool name (e.g., "marsmap", "label")
            args: Command arguments

        Returns:
            Exit code from command execution
        """
        if self.disable_path_translation:
            translated_args = args
            container_cwd = os.getcwd()
        else:
            translated_args = self.translator.translate_args(args)
            container_cwd = self.translator.get_container_cwd(os.getcwd())

        if sys.platform == "darwin":
            display = "host.docker.internal:0"
        else:
            display = os.environ.get("DISPLAY", ":0")

        exec_args = [
            "docker", "exec",
            "-i",
            "-w", container_cwd,
            # Passed per exec rather than baked into the container, so that
            # changing displays does not force a new container.
            "-e", f"DISPLAY={display}",
            "-e", "XFILESEARCHPATH=/usr/local/vicar/gui/%N",
            "-e", "XBMLANGPATH=/usr/local/vicar/gui/%L",
        ]

        # Allocate a TTY only for interactive use; with a TTY, docker merges
        # stderr into stdout and mangles redirected output.
        if sys.stdin.isatty() and sys.stdout.isatty():
            exec_args.append("-t")

        exec_args += [self.container_name, vicar_tool, *translated_args]

        self.command = subprocess.Popen(exec_args)
        try:
            return self.command.wait()
        finally:
            self.command = None

    def signal_command(self, signum: int) -> None:
        """Forward a signal to the running docker exec client."""
        if self.command is None:
            return
        try:
            self.command.send_signal(signum)
            self.command.wait(timeout=10)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            pass
