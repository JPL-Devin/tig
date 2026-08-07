"""Container specification: everything that decides how a container is run.

Kept free of the Docker SDK so the warm path (:mod:`tig_cli.fast`) can work
out which container to use without paying for that import. :mod:`tig_cli
.container` builds on the same functions, so both paths always derive the
same container name from the same inputs.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from .config import (
    Config,
    env_disable_path_translation,
    env_selinux_label_disable,
    env_writable_paths,
)

DEFAULT_IMAGE = "ghcr.io/nasa-ammos/tig/terrain-intelligence-generator:opensource"

# The VICAR image is published for linux/amd64 only; force that platform so
# pulls and runs succeed on arm64 hosts (via emulation). No-op on amd64.
IMAGE_PLATFORM = "linux/amd64"

CONTAINER_PREFIX = "tig-vicar"

# Directory of claim files (one per invocation) naming the container each live
# tig process is using, so reaping never removes a container out from under a
# concurrent invocation. Per-boot state: stale claims are ignored by PID.
CLAIM_DIR_NAME = "tig-claims"

# How long to wait for XQuartz to come up on macOS before giving up.
XQUARTZ_START_TIMEOUT = 5.0

# Where calibration files (VISOR / mars_calibration_*) are mounted, matching
# the layout VICAR's MARS programs expect.
CALIBRATION_MOUNT = "/usr/local/vicar/mars_calib"

SELINUX_ENFORCE_PATH = "/sys/fs/selinux/enforce"


class TigError(Exception):
    """User-facing error; reported without a traceback."""


def get_container_image(config: Config | None = None) -> str:
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


def get_calibration_path(config: Config | None = None) -> str | None:
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


def resolve_writable_paths(
    config: Config, option: list[str] | None = None
) -> list[str]:
    """Extra read-write mounts, from the command line, environment or config."""
    if option:
        return list(option)
    from_env = env_writable_paths()
    if from_env is not None:
        return from_env
    return list(config.writable_paths)


def resolve_disable_path_translation(config: Config, flag: bool = False) -> bool:
    """Whether host paths are passed through untranslated."""
    if flag:
        return True
    override = env_disable_path_translation()
    if override is None:
        override = config.disable_path_translation
    return bool(override)


def resolve_selinux_label_disable(
    config: Config, flag: bool | None = None
) -> bool | None:
    """Whether to run with ``label=disable``; ``None`` means decide by host."""
    if flag is not None:
        return flag
    from_env = env_selinux_label_disable()
    if from_env is not None:
        return from_env
    return config.selinux_label_disable


def _run_quietly(command: list[str], timeout: float = 10.0) -> bool:
    """Run a host helper command, discarding output; True if it succeeded."""
    import subprocess

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
    """Whether this host is Linux with SELinux in Enforcing mode.

    The kernel state is read first: it answers for every host that has
    SELinux compiled in, and costs a file read rather than a process spawn.
    """
    if not sys.platform.startswith("linux"):
        return False

    try:
        return Path(SELINUX_ENFORCE_PATH).read_text().strip() == "1"
    except OSError:
        pass

    import shutil
    import subprocess

    getenforce = shutil.which("getenforce")
    if getenforce is None:
        return False
    try:
        completed = subprocess.run(
            [getenforce], capture_output=True, text=True, timeout=10.0
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0 and completed.stdout.strip() == "Enforcing"


def _ensure_xquartz() -> None:
    """Make XQuartz running and listening on TCP, as the container needs."""
    import time

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
    import shutil

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


def container_display() -> str:
    """The DISPLAY value a command should see inside the container."""
    if sys.platform == "darwin":
        return "host.docker.internal:0"
    return os.environ.get("DISPLAY", ":0")


def home_directory() -> str:
    """The invoking user's home directory, as mounted in the container."""
    return os.environ.get("HOME") or str(Path.home())


def resolve_calibration_path(path: str | None) -> str | None:
    """Validate and normalize a calibration directory path."""
    if path is None:
        return None
    if not os.path.isdir(path):
        raise TigError(f"Calibration path is not a directory: {path}")
    return str(Path(path).resolve())


def build_volume_mounts(
    home: str,
    writable_paths: list[str],
    calibration_path: str | None = None,
) -> dict[str, dict[str, str]]:
    """Build volume mount configuration.

    The host filesystem is mounted read-only at /host so tools can read
    inputs from anywhere. Locations the user is expected to write to - the
    home directory, the current directory and any --writable-path - are
    additionally mounted read-write.

    Args:
        home: The invoking user's home directory
        writable_paths: Additional paths to mount as read-write
        calibration_path: Host path with MARS/VISOR calibration files

    Returns:
        Dictionary of volume mounts for docker-py
    """
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

    if calibration_path:
        volumes[calibration_path] = {"bind": CALIBRATION_MOUNT, "mode": "ro"}

    return volumes


def build_run_kwargs(
    image: str,
    volumes: dict[str, dict[str, str]],
    home: str,
    calibration_path: str | None = None,
    selinux_label_disable: bool = False,
) -> dict[str, object]:
    """Build the containers.run keyword arguments, minus the name."""
    environment = {"HOME": home}
    if calibration_path:
        environment["MARS_CONFIG_PATH"] = CALIBRATION_MOUNT

    kwargs: dict[str, object] = {
        "image": image,
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
        if selinux_label_disable:
            # SELinux Enforcing otherwise denies the container the bind
            # mounts and the X11 socket, and blocks the 32-bit legacy
            # libraries some VICAR tools load. Deliberately not ':z'/':Z'
            # relabeling, which must never touch the host-root mount.
            kwargs["security_opt"] = ["label=disable"]

    return kwargs


def container_name_for(run_kwargs: dict[str, object]) -> str:
    """Derive a container name that identifies this configuration.

    Anything that would change how the container is created - image,
    mounts, user, platform - changes the name, so a container is only ever
    reused when recreating it would produce the same thing.
    """
    fingerprint = json.dumps(run_kwargs, sort_keys=True, default=str)
    digest = hashlib.sha256(fingerprint.encode()).hexdigest()[:12]
    return f"{CONTAINER_PREFIX}-{digest}"


class Claim:
    """This process's claim on a container, protecting it from reaping.

    Claimed before the container is created or adopted, so a container
    another invocation is still setting up is never seen as unused.
    """

    def __init__(self) -> None:
        self.path: Path | None = None

    @staticmethod
    def directory() -> Path:
        base = (
            os.environ.get("XDG_RUNTIME_DIR")
            or os.environ.get("TMPDIR")
            or "/tmp"
        )
        return Path(base) / f"{CLAIM_DIR_NAME}-{os.getuid()}"

    def acquire(self, container_name: str) -> None:
        """Record that this process is about to use ``container_name``."""
        self.release()
        directory = self.directory()
        claim = directory / f"{container_name}.{os.getpid()}"
        try:
            directory.mkdir(parents=True, exist_ok=True)
            claim.touch()
        except OSError:
            # Unclaimable state directory: reaping then just keeps containers.
            return
        self.path = claim

    def release(self) -> None:
        """Drop this process's claim, making its container reapable again."""
        if self.path is None:
            return
        try:
            self.path.unlink()
        except OSError:
            pass
        self.path = None

    @classmethod
    def claimed_containers(cls) -> set:
        """Container names claimed by a still-running tig process.

        Claims left behind by a process that died are pruned as they are
        found.
        """
        claimed = set()
        try:
            claims = list(cls.directory().iterdir())
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
