"""Container lifecycle management."""
import os
import subprocess
import sys
from pathlib import Path
from typing import List, Dict, Any, Optional
import docker

from .path_translator import PathTranslator

DEFAULT_IMAGE = "ghcr.io/nasa-ammos/tig/terrain-intelligence-generator:opensource"


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
        try:
            self.client = docker.from_env()
        except docker.errors.DockerException as e:
            raise RuntimeError(
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

        for path in writable_paths:
            if os.path.isdir(path):
                volumes[path] = {"bind": f"/host{path}", "mode": "rw"}

        return volumes

    def start_container(self, writable_paths: List[str]) -> None:
        """Start the VICAR container with appropriate mounts.

        Args:
            writable_paths: Additional paths to mount as read-write
        """
        volumes = self._build_volume_mounts(writable_paths)

        environment = {}
        extra_kwargs = {}

        if sys.platform == "darwin":
            environment["DISPLAY"] = "host.docker.internal:0"
        else:
            environment["DISPLAY"] = os.environ.get("DISPLAY", ":0")
            volumes["/tmp/.X11-unix"] = {"bind": "/tmp/.X11-unix", "mode": "rw"}
            extra_kwargs["network_mode"] = "host"

        self.container = self.client.containers.run(
            image=self.image,
            name=self.container_name,
            volumes=volumes,
            environment=environment,
            detach=True,
            command="tail -f /dev/null",
            **extra_kwargs
        )

    def stop_container(self) -> None:
        """Stop and remove the container."""
        if self.container:
            try:
                self.container.stop()
            except docker.errors.APIError:
                pass
            try:
                self.container.remove()
            except docker.errors.APIError:
                pass

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
            "-w", container_cwd,
            "-e", "XFILESEARCHPATH=/usr/local/vicar/gui/%N",
            "-e", "XBMLANGPATH=/usr/local/vicar/gui/%L",
            self.container_name,
            vicar_tool,
            *translated_args
        ]

        result = subprocess.run(exec_args)
        return result.returncode
