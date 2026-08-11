"""Container lifecycle management."""
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
import docker

from .spec import (  # noqa: F401  (re-exported for callers and tests)
    CALIBRATION_MOUNT,
    CONTAINER_PREFIX,
    DEFAULT_IMAGE,
    EXEC_ID_ENV,
    IMAGE_PLATFORM,
    RUNNER_MARKER,
    Claim,
    TigError,
    build_run_kwargs,
    build_volume_mounts,
    container_display,
    container_name_for,
    ensure_x11_ready,
    get_calibration_path,
    get_container_image,
    home_directory,
    kill_tree_command,
    resolve_calibration_path,
    selinux_enforcing,
)
from .path_translator import PathTranslator

# Retries for the create/adopt race between concurrent invocations.
CREATE_ATTEMPTS = 3
CREATE_RETRY_DELAY = 0.2

# How many containers tig keeps around; older idle ones are reaped whenever a
# new one is created, so working in many directories does not pile them up.
MAX_KEPT_CONTAINERS = 2

# Directories in the image holding the VICAR tools users invoke by name.
TOOL_PATHS = ("/usr/local/bin",)

# Seconds allowed for the in-container kill, which runs from a signal handler.
SIGNAL_TIMEOUT = 5


def _started_at(container: Any) -> float:
    """Unix time the container was last started, or 0.0 if unknown."""
    raw = (container.attrs.get("State") or {})
    if not isinstance(raw, dict):
        return 0.0
    stamp = raw.get("StartedAt") or ""
    match = re.match(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(\.\d+)?", stamp)
    if not match:
        return 0.0
    fraction = float(match.group(2) or 0.0)
    parsed = datetime.strptime(match.group(1), "%Y-%m-%dT%H:%M:%S")
    return parsed.replace(tzinfo=timezone.utc).timestamp() + fraction


class ContainerManager:
    """Manages VICAR container lifecycle and execution.

    The container is long-lived: it is created on first use and then reused by
    later invocations, so repeated VICAR commands (a terrain pipeline is
    typically dozens) pay the container start cost once rather than every time.
    Its name encodes the image and mount configuration, so a container is only
    reused when it would be created identically today.

    Warm invocations normally never get here: :mod:`tig_cli.fast` talks to the
    Docker daemon directly. This is the path that creates containers, and the
    fallback whenever that shortcut does not apply.
    """

    def __init__(
        self,
        image: str,
        disable_path_translation: bool = False,
        calibration_path: Optional[str] = None,
        selinux_label_disable: Optional[bool] = None,
    ):
        """Initialize the container manager.

        Args:
            image: Docker image name and tag
            disable_path_translation: Skip path translation (for debugging)
            calibration_path: Host path with MARS/VISOR calibration files
            selinux_label_disable: Force ``--security-opt label=disable`` on or
                off; ``None`` enables it when SELinux is Enforcing
        """
        self.image = image
        self.disable_path_translation = disable_path_translation
        self.selinux_enforcing = selinux_enforcing()
        self.selinux_label_disable = (
            self.selinux_enforcing
            if selinux_label_disable is None
            else selinux_label_disable
        )
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
        self.home = home_directory()
        self.calibration_path = resolve_calibration_path(calibration_path)
        self.translator = PathTranslator(self.home)
        self.container: Optional[Any] = None
        self.container_name = CONTAINER_PREFIX
        self.command: Optional[subprocess.Popen] = None
        self.exec_id: Optional[str] = None
        self._claim = Claim()

    def _build_volume_mounts(
        self,
        writable_paths: List[str]
    ) -> Dict[str, Dict[str, str]]:
        """Build volume mount configuration."""
        return build_volume_mounts(
            self.home, writable_paths, self.calibration_path
        )

    def _run_kwargs(self, volumes: Dict[str, Dict[str, str]]) -> Dict[str, Any]:
        """Build the containers.run keyword arguments, minus the name."""
        return build_run_kwargs(
            self.image,
            volumes,
            self.home,
            self.calibration_path,
            self.selinux_label_disable,
        )

    def _container_name_for(self, run_kwargs: Dict[str, Any]) -> str:
        """Derive a container name that identifies this configuration."""
        return container_name_for(run_kwargs)

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

        self._claim_container()

        if self.selinux_enforcing and not self.selinux_label_disable:
            print(
                "tig: SELinux is Enforcing and label=disable is turned off; "
                "mounts and GUI tools may be denied. Re-run with "
                "--selinux-label-disable if so.",
                file=sys.stderr,
            )

        # Concurrent invocations share the name, so creating and adopting race
        # against each other; both outcomes are fine, but either can lose once.
        for attempt in reversed(range(CREATE_ATTEMPTS)):
            existing = self._get_container(self.container_name)
            if existing is not None:
                if self._reusable(existing):
                    try:
                        self._adopt(existing)
                        self._apply_built_programs()
                        return
                    except docker.errors.APIError as e:
                        if not attempt:
                            raise TigError(
                                f"Failed to reuse container "
                                f"{self.container_name}: {e.explanation or e}"
                            ) from e
                        time.sleep(CREATE_RETRY_DELAY)
                        continue
                self._remove(existing)

            try:
                # Authorize the display before the container exists, so the
                # first GUI command in it can already connect.
                ensure_x11_ready()
                self.container = self.client.containers.run(
                    name=self.container_name, **run_kwargs
                )
                self._apply_built_programs()
                self._reap_containers(keep=self.container_name)
                return
            except docker.errors.ImageNotFound as e:
                raise TigError(f"Image not found: {self.image}") from e
            except docker.errors.APIError as e:
                if e.status_code == 409 and attempt:
                    time.sleep(CREATE_RETRY_DELAY)
                    continue
                raise TigError(
                    f"Failed to start container from image {self.image}: "
                    f"{e.explanation or e}"
                ) from e

    def _apply_built_programs(self) -> None:
        """Re-install programs built with --build into this container.

        Injected programs live in the container's filesystem, and containers
        are replaced routinely, so the recorded builds are re-applied whenever
        one is created or adopted. A failure is reported and does not stop the
        command: the container still holds the image's own programs.
        """
        if self.container is None:
            return
        # Imported here so invocations that never built anything do not pay
        # for it, and so the build module can use this one.
        from .build import apply_overrides

        try:
            installed = apply_overrides(
                self.container_name, self.container.id, self.container.image.id
            )
        except TigError as e:
            print(f"tig: {e}", file=sys.stderr)
            return
        if installed:
            print(
                f"tig: re-applied locally built program(s): "
                f"{', '.join(installed)}",
                file=sys.stderr,
            )

    def _adopt(self, container: Any) -> None:
        """Use an existing container, starting it if it is stopped."""
        if container.status != "running":
            container.start()
        self.container = container

    def _remove(self, container: Any) -> None:
        try:
            container.remove(force=True)
        except docker.errors.NotFound:
            pass
        except docker.errors.APIError as e:
            raise TigError(
                f"Failed to replace container {container.name}: "
                f"{e.explanation or e}"
            ) from e

    def _claim_dir(self) -> Path:
        return Claim.directory()

    def _claim_container(self) -> None:
        """Record that this process is about to use ``self.container_name``."""
        self._claim.acquire(self.container_name)

    @property
    def claim(self) -> Optional[Path]:
        """Path of this process's claim file, if it holds one."""
        return self._claim.path

    def release_claim(self) -> None:
        """Drop this process's claim, making its container reapable again."""
        self._claim.release()

    def _claimed_containers(self) -> set:
        """Container names claimed by a still-running tig process."""
        return Claim.claimed_containers()

    def _reap_containers(self, keep: str) -> int:
        """Remove surplus tig containers, keeping the most recently started.

        Called only when a container is created, so the warm path is untouched.
        Containers claimed by another live invocation, or with a command
        running in them, are left alone.

        Returns:
            Number of containers removed
        """
        claimed = self._claimed_containers()
        candidates = []
        for container in self.client.containers.list(
            all=True, filters={"name": CONTAINER_PREFIX}
        ):
            if container.name == keep:
                continue
            try:
                # The list response omits StartedAt and ExecIDs.
                container.reload()
            except docker.errors.APIError:
                continue
            candidates.append(container)

        candidates.sort(key=_started_at, reverse=True)

        removed = 0
        for container in candidates[MAX_KEPT_CONTAINERS - 1:]:
            if container.name in claimed:
                continue
            if self._busy(container.attrs.get("ExecIDs") or []):
                continue
            try:
                container.remove(force=True)
            except docker.errors.APIError:
                continue
            removed += 1
        return removed

    def _busy(self, exec_ids: List[str]) -> bool:
        """Whether any of a container's exec instances is still running.

        Docker prunes finished execs, so this only guards against a command
        started outside tig; claims cover tig's own invocations. tig's own
        dispatcher and broker agent run for the container's whole life, so
        they are not what busy means here.

        Assumes busy when Docker cannot say, so a container in use by another
        invocation is never reaped on the strength of a failed check.
        """
        for exec_id in exec_ids:
            try:
                details = self.client.api.exec_inspect(exec_id)
            except docker.errors.NotFound:
                continue
            except docker.errors.APIError:
                return True
            if details.get("Running") and not self._is_runner(details):
                return True
        return False

    @staticmethod
    def _is_runner(details: dict) -> bool:
        """Whether an exec is tig's own in-container runner."""
        process = details.get("ProcessConfig") or {}
        arguments = process.get("arguments") or []
        return RUNNER_MARKER in arguments

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
            except docker.errors.NotFound:
                pass
            except docker.errors.APIError:
                pass
        self.container = None
        self.release_claim()
        return removed

    def remove_containers_of(self, image_id: str) -> Tuple[int, int]:
        """Remove this image's containers, leaving the ones still in use.

        For forgetting a locally built program: the injected copy overwrote
        the image's own in the container's filesystem, so only a container
        created afresh has it back. Containers of another image never carried
        it, and a container another invocation is using is not taken away.

        Returns:
            Numbers of containers removed and left alone as in use
        """
        claimed = self._claimed_containers()
        removed = 0
        in_use = 0
        for container in self.client.containers.list(
            all=True, filters={"name": CONTAINER_PREFIX}
        ):
            try:
                # The list response omits ExecIDs.
                container.reload()
            except docker.errors.APIError:
                continue
            if container.image.id != image_id:
                continue
            if container.name in claimed or self._busy(
                container.attrs.get("ExecIDs") or []
            ):
                in_use += 1
                continue
            try:
                container.remove(force=True)
            except docker.errors.NotFound:
                pass
            except docker.errors.APIError:
                continue
            if self.container is not None and self.container.id == container.id:
                self.container = None
            removed += 1
        return removed, in_use

    def status(self) -> List[Dict[str, str]]:
        """Describe the containers this tool has created.

        Includes each container's writable mounts, since those (the home
        directory, the directory tig was invoked from, any --writable-path) are
        what distinguishes one container from another.
        """
        return [
            {
                "name": container.name,
                "status": container.status,
                "image": (
                    container.image.tags[0]
                    if container.image.tags
                    else container.image.short_id
                ),
                "writable": ", ".join(self._writable_mounts(container)),
            }
            for container in self.client.containers.list(
                all=True, filters={"name": CONTAINER_PREFIX}
            )
        ]

    def _writable_mounts(self, container: Any) -> List[str]:
        """Host paths mounted read-write in the container, X11 socket aside."""
        try:
            container.reload()
        except docker.errors.APIError:
            return []
        binds = (container.attrs.get("HostConfig") or {}).get("Binds") or []
        paths = []
        for bind in binds:
            source, _, mode = bind.partition(":")
            if "ro" in mode.split(":")[-1].split(","):
                continue
            if source == "/tmp/.X11-unix":
                continue
            paths.append(source)
        return sorted(paths)

    def list_tools(self) -> List[str]:
        """Return the VICAR tool names available in the container.

        Requires a container to be running; call ``ensure_container`` first.
        """
        if self.container is None:
            raise TigError("No container is running.")
        try:
            # Executable files only: the tool directories also hold payloads
            # such as vicario.jar, which is not a command.
            result = self.container.exec_run(
                [
                    "find", *TOOL_PATHS, "-maxdepth", "1",
                    "-type", "f", "-executable", "-printf", "%f\\n",
                ],
                demux=False,
            )
        except docker.errors.APIError as e:
            raise TigError(f"Failed to list VICAR tools: {e}") from e

        output = result.output.decode("utf-8", "replace")
        if result.exit_code != 0:
            raise TigError(
                f"Failed to list VICAR tools in the container: {output.strip()}"
            )
        return sorted({
            line.strip() for line in output.splitlines()
            if line.strip() and "/" not in line
        })

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

        self.exec_id = uuid.uuid4().hex

        exec_args = [
            "docker", "exec",
            "-i",
            "-w", container_cwd,
            # Passed per exec rather than baked into the container, so that
            # changing displays does not force a new container.
            "-e", f"DISPLAY={container_display()}",
            "-e", f"{EXEC_ID_ENV}={self.exec_id}",
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
            self.exec_id = None

    def signal_command(self, signum: int) -> None:
        """Forward a signal to the running docker exec client.

        Deliberately does not wait: this runs from a signal handler while the
        main flow already holds Popen's waitpid lock, so a nested wait() could
        not observe the exit and would just stall.
        """
        if self.command is None:
            return
        self._signal_in_container(signum)
        try:
            self.command.send_signal(signum)
        except ProcessLookupError:
            pass

    def _signal_in_container(self, signum: int) -> None:
        """Signal this invocation's processes inside the container.

        Docker does not proxy signals for ``docker exec``, and the container is
        shared, so killing the client alone would leave the tool running.
        """
        if self.exec_id is None:
            return
        kill_tree = kill_tree_command(self.exec_id, signum)
        try:
            subprocess.run(
                ["docker", "exec", self.container_name, "sh", "-c", kill_tree],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=SIGNAL_TIMEOUT,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            pass
