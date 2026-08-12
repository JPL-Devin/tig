"""Container lifecycle management, through whichever OCI runtime is installed."""
import os
import re
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional

from .config import Config
from .runtime import CommandFailed, Runtime
from .spec import (  # noqa: F401  (re-exported for callers and tests)
    CALIBRATION_MOUNT,
    CONTAINER_PREFIX,
    DEFAULT_IMAGE,
    EXEC_ID_ENV,
    IMAGE_PLATFORM,
    RUNNER_MARKER,
    Claim,
    TigError,
    build_mounts,
    build_run_spec,
    container_display,
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

# What a runtime says when the name is taken, which is how the create/adopt
# race is lost.
NAME_TAKEN = ("already in use", "conflict")


def _started_at(details: Dict[str, Any]) -> float:
    """Unix time the container was last started, or 0.0 if unknown."""
    state = details.get("State")
    stamp = state.get("StartedAt") if isinstance(state, dict) else None
    if not isinstance(stamp, str):
        return 0.0
    match = re.match(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(\.\d+)?", stamp)
    if not match:
        return 0.0
    fraction = float(match.group(2) or 0.0)
    parsed = datetime.strptime(match.group(1), "%Y-%m-%dT%H:%M:%S")
    return parsed.replace(tzinfo=timezone.utc).timestamp() + fraction


def _name_taken(error: CommandFailed) -> bool:
    message = str(error).lower()
    return any(phrase in message for phrase in NAME_TAKEN)


class ContainerManager:
    """Manages VICAR container lifecycle and execution.

    The container is long-lived: it is created on first use and then reused by
    later invocations, so repeated VICAR commands (a terrain pipeline is
    typically dozens) pay the container start cost once rather than every time.
    Its name encodes the image and mount configuration, so a container is only
    reused when it would be created identically today.

    Everything here goes through the runtime's command line, which Docker,
    Podman, nerdctl and Finch share, so tig is tied to no particular one.
    Warm invocations normally never get here: :mod:`tig_cli.fast` talks to the
    runtime's API socket where there is one. This is the path that creates
    containers, and the fallback whenever that shortcut does not apply.
    """

    def __init__(
        self,
        image: str,
        disable_path_translation: bool = False,
        calibration_path: Optional[str] = None,
        selinux_label_disable: Optional[bool] = None,
        config: Optional[Config] = None,
        runtime: Optional[Runtime] = None,
    ):
        """Initialize the container manager.

        Args:
            image: Container image name and tag
            disable_path_translation: Skip path translation (for debugging)
            calibration_path: Host path with MARS/VISOR calibration files
            selinux_label_disable: Force ``--security-opt label=disable`` on or
                off; ``None`` enables it when SELinux is Enforcing
            config: Configuration, which may name the runtime to use
            runtime: Container runtime to use, instead of finding one
        """
        self.image = image
        self.disable_path_translation = disable_path_translation
        self.selinux_enforcing = selinux_enforcing()
        self.selinux_label_disable = (
            self.selinux_enforcing
            if selinux_label_disable is None
            else selinux_label_disable
        )
        self._config = config
        self._runtime = runtime
        self.home = home_directory()
        self.calibration_path = resolve_calibration_path(calibration_path)
        self.translator = PathTranslator(self.home)
        self.running = False
        self.container_name = CONTAINER_PREFIX
        self.command: Optional[subprocess.Popen] = None
        self.exec_id: Optional[str] = None
        self._claim = Claim()

    @property
    def runtime(self) -> Runtime:
        """The runtime to drive, found on first use.

        Not in the constructor: lifecycle-only commands such as ``--status``
        need it, but nothing that only reports configuration does.
        """
        if self._runtime is None:
            self._runtime = Runtime.detect(self._config)
        return self._runtime

    def _run_spec(self, writable_paths: List[str]):
        """The specification of the container this invocation needs."""
        return build_run_spec(
            self.image,
            build_mounts(self.home, writable_paths, self.calibration_path),
            self.home,
            self.calibration_path,
            self.selinux_label_disable,
            self.runtime.name,
        )

    def _inspect(self, name: str) -> Optional[Dict[str, Any]]:
        """Describe a container, or ``None`` if the runtime has no such one."""
        try:
            return self.runtime.inspect("container", name)
        except TigError:
            return None

    def _image_id(self) -> Optional[str]:
        """The id of the configured image, or ``None`` if it is not pulled."""
        try:
            details = self.runtime.inspect("image", self.image)
        except TigError:
            return None
        identifier = details.get("Id")
        return identifier if isinstance(identifier, str) else None

    def _reusable(self, details: Dict[str, Any]) -> bool:
        """Whether an existing container can serve this invocation.

        The name covers the run configuration; the image is checked separately
        so that re-pulling a moving tag such as :opensource takes effect
        instead of silently reusing the old image. Runtimes differ in whether
        they record the image's id or the reference it was run from, so either
        counts.
        """
        image_id = self._image_id()
        if image_id is None:
            # Not pulled locally, so it cannot be what is running.
            return False
        return details.get("Image") in (image_id, self.image)

    def ensure_container(self, writable_paths: List[str]) -> None:
        """Ensure a container matching this configuration is running.

        Reuses a suitable existing container, starts it if it is stopped, and
        otherwise creates it.

        Args:
            writable_paths: Additional paths to mount as read-write
        """
        spec = self._run_spec(writable_paths)
        self.container_name = spec.container_name()

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
            existing = self._inspect(self.container_name)
            if existing is not None:
                if self._reusable(existing):
                    try:
                        self._adopt(existing)
                        return
                    except CommandFailed as e:
                        if not attempt:
                            raise TigError(
                                f"Failed to reuse container "
                                f"{self.container_name}: {e}"
                            ) from e
                        time.sleep(CREATE_RETRY_DELAY)
                        continue
                self._remove(self.container_name)

            try:
                # Authorize the display before the container exists, so the
                # first GUI command in it can already connect.
                ensure_x11_ready()
                # No timeout: creating the container pulls the image the first
                # time, which is gigabytes of VICAR.
                self.runtime.run(
                    *spec.create_args(self.container_name), timeout=None
                )
            except CommandFailed as e:
                if _name_taken(e) and attempt:
                    time.sleep(CREATE_RETRY_DELAY)
                    continue
                raise TigError(
                    f"Failed to start container from image {self.image}: {e}"
                ) from e
            self.running = True
            self._reap_containers(keep=self.container_name)
            return

    def _adopt(self, details: Dict[str, Any]) -> None:
        """Use an existing container, starting it if it is stopped."""
        state = details.get("State")
        running = state.get("Running") is True if isinstance(state, dict) else False
        if not running:
            self.runtime.run("start", self.container_name)
        self.running = True

    def _remove(self, name: str) -> None:
        try:
            self.runtime.run("rm", "--force", name)
        except CommandFailed as e:
            if self._gone(name):
                # Removed by a concurrent invocation, which is all this wanted.
                return
            raise TigError(f"Failed to replace container {name}: {e}") from e

    def _gone(self, name: str) -> bool:
        """Whether the runtime says there is no such container.

        Only its own answer counts: a runtime that cannot be asked at all
        says nothing about whether the container is still there.
        """
        try:
            self.runtime.inspect("container", name)
        except CommandFailed:
            return True
        except TigError:
            return False
        return False

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

    def _container_names(self) -> List[str]:
        """The names of the containers tig has created.

        Raises:
            TigError: if there is no runtime, or it cannot be asked.
        """
        listed = self.runtime.run(
            "ps", "--all",
            "--filter", f"name={CONTAINER_PREFIX}",
            "--format", "{{.Names}}",
        )
        names = []
        for line in listed.splitlines():
            # Podman reports a container's names as a comma-separated list.
            for name in line.strip().split(","):
                if name.startswith(CONTAINER_PREFIX):
                    names.append(name)
        return names

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
        try:
            names = self._container_names()
        except TigError:
            # Best-effort housekeeping, run after the container is ready.
            return 0
        for name in names:
            if name == keep:
                continue
            details = self._inspect(name)
            if details is None:
                continue
            candidates.append((name, details))

        candidates.sort(key=lambda candidate: _started_at(candidate[1]), reverse=True)

        removed = 0
        for name, details in candidates[MAX_KEPT_CONTAINERS - 1:]:
            if name in claimed or self._busy(details):
                continue
            try:
                self.runtime.run("rm", "--force", name)
            except TigError:
                continue
            removed += 1
        return removed

    def _busy(self, details: Dict[str, Any]) -> bool:
        """Whether a command someone is waiting on is running in a container.

        Runtimes prune finished execs, so this only guards against a command
        started outside tig; claims cover tig's own invocations. tig's own
        dispatcher and broker agent run for the container's whole life, so
        they are not what busy means here - and they only ever exist where
        the runtime has an API socket, which is also the only way to tell one
        exec from another. Without one, any exec at all means busy.
        """
        exec_ids = details.get("ExecIDs")
        if not isinstance(exec_ids, list) or not exec_ids:
            return False

        from .engine import Engine, EngineError, EngineNotFound

        try:
            engine = Engine.detect(self.runtime)
        except (EngineError, OSError):
            return True

        for exec_id in exec_ids:
            try:
                inspected = engine.inspect_exec(str(exec_id))
            except EngineNotFound:
                # Already gone, so nothing of it is running.
                continue
            except EngineError:
                # Assume busy when the runtime cannot say, so a container in
                # use by another invocation is never reaped on a failed check.
                return True
            if inspected.get("Running") and not self._is_runner(inspected):
                return True
        return False

    @staticmethod
    def _is_runner(details: dict) -> bool:
        """Whether an exec is tig's own in-container runner."""
        process = details.get("ProcessConfig") or {}
        arguments = process.get("arguments") or []
        return RUNNER_MARKER in arguments

    def shutdown(self) -> int:
        """Remove every container this tool has created.

        Returns:
            Number of containers removed
        """
        removed = 0
        for name in self._container_names():
            try:
                self.runtime.run("rm", "--force", name)
                removed += 1
            except TigError:
                pass
        self.running = False
        self.release_claim()
        return removed

    def status(self) -> List[Dict[str, str]]:
        """Describe the containers this tool has created.

        Includes each container's writable mounts, since those (the home
        directory, the directory tig was invoked from, any --writable-path) are
        what distinguishes one container from another.
        """
        containers = []
        for name in self._container_names():
            details = self._inspect(name)
            if details is None:
                continue
            containers.append({
                "name": name,
                "status": self._state(details),
                "image": self._image_of(details),
                "writable": ", ".join(self._writable_mounts(details)),
            })
        return containers

    @staticmethod
    def _state(details: Dict[str, Any]) -> str:
        state = details.get("State")
        status = state.get("Status") if isinstance(state, dict) else None
        return status if isinstance(status, str) else "unknown"

    @staticmethod
    def _image_of(details: Dict[str, Any]) -> str:
        """The image a container runs, as the user would name it."""
        for key in ("ImageName", "Image"):
            value = details.get(key)
            if isinstance(value, str) and not value.startswith("sha256:"):
                return value
        configured = details.get("Config")
        reference = configured.get("Image") if isinstance(configured, dict) else None
        if isinstance(reference, str) and reference:
            return reference
        identifier = details.get("Image")
        return identifier[7:19] if isinstance(identifier, str) else "unknown"

    @staticmethod
    def _writable_mounts(details: Dict[str, Any]) -> List[str]:
        """Host paths mounted read-write in the container, X11 socket aside."""
        paths = []
        for mount in details.get("Mounts") or []:
            if not isinstance(mount, dict) or not mount.get("RW"):
                continue
            source = mount.get("Source")
            if not isinstance(source, str) or source == "/tmp/.X11-unix":
                continue
            paths.append(source)
        return sorted(paths)

    def list_tools(self) -> List[str]:
        """Return the VICAR tool names available in the container.

        Requires a container to be running; call ``ensure_container`` first.
        """
        if not self.running:
            raise TigError("No container is running.")
        try:
            # Executable files only: the tool directories also hold payloads
            # such as vicario.jar, which is not a command.
            output = self.runtime.run(
                "exec", self.container_name,
                "find", *TOOL_PATHS, "-maxdepth", "1",
                "-type", "f", "-executable", "-printf", "%f\\n",
            )
        except TigError as e:
            raise TigError(f"Failed to list VICAR tools: {e}") from e

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
            "exec",
            "-i",
            "-w", container_cwd,
            # Passed per exec rather than baked into the container, so that
            # changing displays does not force a new container.
            "-e", f"DISPLAY={container_display(self.runtime.name)}",
            "-e", f"{EXEC_ID_ENV}={self.exec_id}",
        ]

        # Allocate a TTY only for interactive use; with a TTY, runtimes merge
        # stderr into stdout and mangle redirected output.
        if sys.stdin.isatty() and sys.stdout.isatty():
            exec_args.append("-t")

        exec_args += [self.container_name, vicar_tool, *translated_args]

        self.command = subprocess.Popen(self.runtime.args(*exec_args))
        try:
            return self.command.wait()
        finally:
            self.command = None
            self.exec_id = None

    def signal_command(self, signum: int) -> None:
        """Forward a signal to the running exec client.

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

        Runtimes do not proxy signals to an exec, and the container is
        shared, so killing the client alone would leave the tool running.
        """
        if self.exec_id is None:
            return
        kill_tree = kill_tree_command(self.exec_id, signum)
        try:
            subprocess.run(
                self.runtime.args(
                    "exec", self.container_name, "sh", "-c", kill_tree
                ),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=SIGNAL_TIMEOUT,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            pass
