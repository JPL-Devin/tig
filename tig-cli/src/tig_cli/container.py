"""Container lifecycle management."""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional
import docker

from .config import Config
from .path_translator import PathTranslator

DEFAULT_IMAGE = "ghcr.io/nasa-ammos/tig/terrain-intelligence-generator:opensource"

# The VICAR image is published for linux/amd64 only; force that platform so
# pulls and runs succeed on arm64 hosts (via emulation). No-op on amd64.
IMAGE_PLATFORM = "linux/amd64"

CONTAINER_PREFIX = "tig-vicar"

# Retries for the create/adopt race between concurrent invocations.
CREATE_ATTEMPTS = 3
CREATE_RETRY_DELAY = 0.2

# How many containers tig keeps around; older idle ones are reaped whenever a
# new one is created, so working in many directories does not pile them up.
MAX_KEPT_CONTAINERS = 2

# Directory of claim files (one per invocation) naming the container each live
# tig process is using, so reaping never removes a container out from under a
# concurrent invocation. Per-boot state: stale claims are ignored by PID.
CLAIM_DIR_NAME = "tig-claims"

# How long to wait for XQuartz to come up on macOS before giving up.
XQUARTZ_START_TIMEOUT = 5.0

# Where calibration files (VISOR / mars_calibration_*) are mounted, matching
# the layout VICAR's MARS programs expect.
CALIBRATION_MOUNT = "/usr/local/vicar/mars_calib"

# Marks the processes of one invocation, so a signal can reach them inside the
# shared container. Every descendant inherits it.
EXEC_ID_ENV = "TIG_EXEC_ID"

# Seconds allowed for the in-container kill, which runs from a signal handler.
SIGNAL_TIMEOUT = 5


class TigError(Exception):
    """User-facing error; reported without a traceback."""


def get_container_image(config: Optional[Config] = None) -> str:
    """Return the Docker image to use for VICAR execution.

    Precedence: CONTAINER_IMAGE environment variable, then the ``image`` key
    from the configuration files, then the opensource image.
    """
    from_env = os.environ.get("CONTAINER_IMAGE")
    if from_env:
        return from_env
    if config is not None and config.image:
        return config.image
    return DEFAULT_IMAGE


def _run_quietly(command: List[str], timeout: float = 10.0) -> bool:
    """Run a host helper command, discarding output; True if it succeeded."""
    try:
        completed = subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


def selinux_enforcing() -> bool:
    """Whether this host is Linux with SELinux in Enforcing mode."""
    if not sys.platform.startswith("linux"):
        return False

    getenforce = shutil.which("getenforce")
    if getenforce is not None:
        try:
            completed = subprocess.run(
                [getenforce], capture_output=True, text=True, timeout=10.0
            )
        except (OSError, subprocess.SubprocessError):
            completed = None
        if completed is not None and completed.returncode == 0:
            return completed.stdout.strip() == "Enforcing"

    # No getenforce (common in minimal images): read the kernel state directly.
    try:
        return Path("/sys/fs/selinux/enforce").read_text().strip() == "1"
    except OSError:
        return False


def _ensure_xquartz() -> None:
    """Make XQuartz running and listening on TCP, as the container needs."""
    _run_quietly(
        ["defaults", "write", "org.xquartz.X11", "nolisten_tcp", "-bool", "false"]
    )
    if _run_quietly(["pgrep", "-x", "Xquartz"]):
        return
    if not _run_quietly(["open", "-a", "XQuartz"]):
        return
    deadline = time.monotonic() + XQUARTZ_START_TIMEOUT
    while time.monotonic() < deadline:
        if _run_quietly(["pgrep", "-x", "Xquartz"]):
            return
        time.sleep(0.5)


def ensure_x11_ready() -> None:
    """Authorize the host X server so GUI tools (xvd, marsmap) can connect.

    Silently does nothing when there is no display or no ``xhost``; those
    hosts simply have no GUI to authorize.
    """
    if not os.environ.get("DISPLAY") or shutil.which("xhost") is None:
        return

    if sys.platform == "darwin":
        # The container reaches XQuartz over TCP via host.docker.internal:0.
        _ensure_xquartz()
        _run_quietly(["xhost", "+localhost"])
    else:
        # The broad form is deliberate: with 'label=disable' the container
        # connects as the LOCAL: family, which '+local:docker' misses.
        _run_quietly(["xhost", "+local:"])


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


def get_calibration_path(config: Optional[Config] = None) -> Optional[str]:
    """Return the host path holding MARS/VISOR calibration files, if any.

    Precedence: MARS_CONFIG_PATH (the same variable the toolkit used), then the
    ``calibration_path`` key from the configuration files.
    """
    from_env = os.environ.get("MARS_CONFIG_PATH")
    if from_env:
        return os.path.expanduser(from_env)
    if config is not None and config.calibration_path:
        return config.calibration_path
    return None


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
        self.home = os.environ.get("HOME") or str(Path.home())
        self.calibration_path = self._resolve_calibration_path(calibration_path)
        self.translator = PathTranslator(self.home)
        self.container: Optional[Any] = None
        self.container_name = CONTAINER_PREFIX
        self.command: Optional[subprocess.Popen] = None
        self.exec_id: Optional[str] = None
        self.claim: Optional[Path] = None

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
            if self.selinux_label_disable:
                # SELinux Enforcing otherwise denies the container the bind
                # mounts and the X11 socket, and blocks the 32-bit legacy
                # libraries some VICAR tools load. Deliberately not ':z'/':Z'
                # relabeling, which must never touch the host-root mount.
                kwargs["security_opt"] = ["label=disable"]

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
        runtime = os.environ.get("XDG_RUNTIME_DIR")
        base = Path(runtime) if runtime else Path(tempfile.gettempdir())
        return base / f"{CLAIM_DIR_NAME}-{os.getuid()}"

    def _claim_container(self) -> None:
        """Record that this process is about to use ``self.container_name``.

        Claimed before the container is created or adopted, so a container
        another invocation is still setting up is never seen as unused.
        """
        self.release_claim()
        directory = self._claim_dir()
        claim = directory / f"{self.container_name}.{os.getpid()}"
        try:
            directory.mkdir(parents=True, exist_ok=True)
            claim.touch()
        except OSError:
            # Unclaimable state directory: reaping then just keeps containers.
            return
        self.claim = claim

    def release_claim(self) -> None:
        """Drop this process's claim, making its container reapable again."""
        if self.claim is None:
            return
        try:
            self.claim.unlink()
        except OSError:
            pass
        self.claim = None

    def _claimed_containers(self) -> set:
        """Container names claimed by a still-running tig process.

        Claims left behind by a process that died are pruned as they are found.
        """
        claimed = set()
        try:
            claims = list(self._claim_dir().iterdir())
        except OSError:
            return claimed

        for claim in claims:
            name, _, pid = claim.name.rpartition(".")
            if not pid.isdigit():
                continue
            try:
                os.kill(int(pid), 0)
            except ProcessLookupError:
                try:
                    claim.unlink()
                except OSError:
                    pass
                continue
            except OSError:
                pass
            claimed.add(name)
        return claimed

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
        started outside tig; claims cover tig's own invocations.

        Assumes busy when Docker cannot say, so a container in use by another
        invocation is never reaped on the strength of a failed check.
        """
        for exec_id in exec_ids:
            try:
                if self.client.api.exec_inspect(exec_id).get("Running"):
                    return True
            except docker.errors.NotFound:
                continue
            except docker.errors.APIError:
                return True
        return False

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
        # /proc is the only way in: the exec id is in the environment of the
        # tool and of everything it started.
        kill_tree = (
            "for p in /proc/[0-9]*; do "
            f'grep -qz {EXEC_ID_ENV}={self.exec_id} "$p/environ" 2>/dev/null '
            f'&& kill -{signum} "${{p#/proc/}}" 2>/dev/null; done'
        )
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
