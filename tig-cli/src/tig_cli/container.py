"""Container lifecycle management."""
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


class TigError(Exception):
    """User-facing error; reported without a traceback."""


def get_container_image() -> str:
    """Return the Docker image to use for VICAR execution.

    Reads CONTAINER_IMAGE environment variable. Falls back to the
    opensource image if not set.
    """
    return os.environ.get("CONTAINER_IMAGE", DEFAULT_IMAGE)


class ContainerManager:
    """Manages VICAR container lifecycle and execution.

    Handles starting containers with appropriate mounts,
    executing VICAR commands, and cleanup.
    """

    def __init__(
        self,
        image: str,
        disable_path_translation: bool = False
    ):
        """Initialize the container manager.

        Args:
            image: Docker image name and tag
            disable_path_translation: Skip path translation (for debugging)
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
        self.container_name = f"tig-vicar-{os.getpid()}"
        home = os.environ.get("HOME") or str(Path.home())
        self.translator = PathTranslator(home)
        self.container: Optional[Any] = None

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
        home = os.environ.get("HOME") or str(Path.home())

        volumes = {
            "/": {"bind": "/host", "mode": "ro"},
            home: {"bind": home, "mode": "rw"},
        }

        for path in [os.getcwd()] + list(writable_paths):
            if not os.path.isdir(path):
                continue
            resolved = str(Path(path).resolve())
            if Path(resolved).is_relative_to(home):
                continue
            volumes[resolved] = {"bind": f"/host{resolved}", "mode": "rw"}

        return volumes

    def _remove_stale_container(self) -> None:
        """Remove a leftover container of the same name.

        PIDs are recycled, and a container abandoned by a killed process (see
        stop_container) keeps its name, so reuse would otherwise fail with a
        name conflict.
        """
        try:
            self.client.containers.get(self.container_name).remove(force=True)
        except docker.errors.NotFound:
            pass
        except docker.errors.APIError:
            pass

    def start_container(self, writable_paths: List[str]) -> None:
        """Start the VICAR container with appropriate mounts.

        Args:
            writable_paths: Additional paths to mount as read-write
        """
        volumes = self._build_volume_mounts(writable_paths)

        home = os.environ.get("HOME") or str(Path.home())
        environment = {"HOME": home}
        extra_kwargs: Dict[str, Any] = {"platform": IMAGE_PLATFORM}

        if sys.platform == "darwin":
            environment["DISPLAY"] = "host.docker.internal:0"
        else:
            environment["DISPLAY"] = os.environ.get("DISPLAY", ":0")
            volumes["/tmp/.X11-unix"] = {"bind": "/tmp/.X11-unix", "mode": "rw"}
            extra_kwargs["network_mode"] = "host"
            # Run as the invoking user so output files are owned by them
            # rather than by root. Docker Desktop already maps ownership.
            extra_kwargs["user"] = f"{os.getuid()}:{os.getgid()}"
            extra_kwargs["group_add"] = [str(os.getgid())]

        self._remove_stale_container()

        try:
            self.container = self.client.containers.run(
                image=self.image,
                name=self.container_name,
                volumes=volumes,
                environment=environment,
                detach=True,
                command="tail -f /dev/null",
                **extra_kwargs
            )
        except docker.errors.ImageNotFound as e:
            raise TigError(f"Image not found: {self.image}") from e
        except docker.errors.APIError as e:
            raise TigError(
                f"Failed to start container from image {self.image}: "
                f"{e.explanation or e}"
            ) from e

    def stop_container(self) -> None:
        """Stop and remove the container."""
        if self.container:
            try:
                self.container.remove(force=True)
            except docker.errors.APIError:
                pass
            self.container = None

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

        exec_args = [
            "docker", "exec",
            "-i",
            "-w", container_cwd,
            "-e", "XFILESEARCHPATH=/usr/local/vicar/gui/%N",
            "-e", "XBMLANGPATH=/usr/local/vicar/gui/%L",
        ]

        # Allocate a TTY only for interactive use; with a TTY, docker merges
        # stderr into stdout and mangles redirected output.
        if sys.stdin.isatty() and sys.stdout.isatty():
            exec_args.append("-t")

        exec_args += [self.container_name, vicar_tool, *translated_args]

        result = subprocess.run(exec_args)
        return result.returncode
