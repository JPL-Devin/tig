"""Layered TOML configuration for the tig CLI.

Configuration is read from up to three files, each overriding the previous:

1. system  — ``/etc/tig/config.toml``
2. user    — ``$XDG_CONFIG_HOME/tig/config.toml`` (``~/.config/tig/config.toml``)
3. project — the nearest ``tig.toml`` found walking up from the current directory

Setting ``TIG_CONFIG`` (or passing an explicit path) replaces the search
entirely and loads only that file.
"""
from __future__ import annotations

import os
from pathlib import Path

SYSTEM_CONFIG_PATH = Path("/etc/tig/config.toml")
USER_CONFIG_SUBPATH = Path("tig/config.toml")
PROJECT_CONFIG_NAME = "tig.toml"

# Names the container runtime to use, ahead of the config and of what is
# installed. Also how a spawned helper is told the runtime already chosen.
RUNTIME_ENV = "TIG_CONTAINER_RUNTIME"

KNOWN_KEYS = frozenset({
    "image",
    "builder_image",
    "runtime",
    "writable_paths",
    "disable_path_translation",
    "calibration_path",
    "selinux_label_disable",
})


class ConfigError(Exception):
    """Raised when a configuration file is unreadable or invalid."""


class Config:
    """Resolved configuration values.

    ``None`` means the setting was not configured, so a caller may fall back
    to an environment variable or a built-in default.

    Hand-written rather than a dataclass: importing ``dataclasses`` costs
    more than everything this module does, on every tig invocation.
    """

    def __init__(
        self,
        image: str | None = None,
        builder_image: str | None = None,
        runtime: str | None = None,
        writable_paths: list[str] | None = None,
        disable_path_translation: bool | None = None,
        calibration_path: str | None = None,
        selinux_label_disable: bool | None = None,
        sources: list[Path] | None = None,
    ):
        self.image = image
        self.builder_image = builder_image
        self.runtime = runtime
        self.writable_paths: list[str] = writable_paths or []
        self.disable_path_translation = disable_path_translation
        self.calibration_path = calibration_path
        self.selinux_label_disable = selinux_label_disable
        self.sources: list[Path] = sources or []

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Config):
            return NotImplemented
        return vars(self) == vars(other)

    def __repr__(self) -> str:
        values = ", ".join(f"{key}={value!r}" for key, value in vars(self).items())
        return f"Config({values})"


def user_config_path() -> Path:
    """Return the path of the per-user configuration file."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path(os.environ.get("HOME") or Path.home()) / ".config"
    return base / USER_CONFIG_SUBPATH


def find_project_config(start: Path | None = None) -> Path | None:
    """Return the nearest ``tig.toml`` at or above ``start``.

    Args:
        start: Directory to search from. Defaults to the current directory.
    """
    directory = (start or Path.cwd()).resolve()
    for candidate in [directory, *directory.parents]:
        path = candidate / PROJECT_CONFIG_NAME
        if path.is_file():
            return path
    return None


def config_search_paths(start: Path | None = None) -> list[Path]:
    """Return the configuration files to load, lowest precedence first."""
    explicit = os.environ.get("TIG_CONFIG")
    if explicit:
        return [Path(explicit)]

    paths = [SYSTEM_CONFIG_PATH, user_config_path()]
    project = find_project_config(start)
    if project:
        paths.append(project)
    return paths


def _read_file(path: Path) -> dict:
    # Imported here so invocations without a config file never load it.
    try:
        import tomllib
    except ModuleNotFoundError:  # Python < 3.11
        import tomli as tomllib

    try:
        with open(path, "rb") as handle:
            data = tomllib.load(handle)
    except OSError as e:
        raise ConfigError(f"Cannot read config file {path}: {e}") from e
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"Invalid TOML in config file {path}: {e}") from e

    unknown = sorted(set(data) - KNOWN_KEYS)
    if unknown:
        raise ConfigError(
            f"Unknown key(s) in config file {path}: {', '.join(unknown)}. "
            f"Supported keys: {', '.join(sorted(KNOWN_KEYS))}."
        )
    return data


def _apply(config: Config, data: dict, path: Path) -> None:
    if "image" in data:
        value = data["image"]
        if not isinstance(value, str):
            raise ConfigError(f"{path}: 'image' must be a string.")
        config.image = value

    if "builder_image" in data:
        value = data["builder_image"]
        if not isinstance(value, str):
            raise ConfigError(f"{path}: 'builder_image' must be a string.")
        config.builder_image = value

    if "runtime" in data:
        value = data["runtime"]
        if not isinstance(value, str):
            raise ConfigError(f"{path}: 'runtime' must be a string.")
        config.runtime = value

    if "writable_paths" in data:
        value = data["writable_paths"]
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ConfigError(f"{path}: 'writable_paths' must be a list of strings.")
        config.writable_paths = list(value)

    if "disable_path_translation" in data:
        value = data["disable_path_translation"]
        if not isinstance(value, bool):
            raise ConfigError(f"{path}: 'disable_path_translation' must be a boolean.")
        config.disable_path_translation = value

    if "calibration_path" in data:
        value = data["calibration_path"]
        if not isinstance(value, str):
            raise ConfigError(f"{path}: 'calibration_path' must be a string.")
        config.calibration_path = os.path.expanduser(value)

    if "selinux_label_disable" in data:
        value = data["selinux_label_disable"]
        if not isinstance(value, bool):
            raise ConfigError(f"{path}: 'selinux_label_disable' must be a boolean.")
        config.selinux_label_disable = value


def load_config(
    path: Path | None = None,
    start: Path | None = None,
) -> Config:
    """Load and merge the configuration layers.

    Args:
        path: Load only this file instead of searching the standard layers.
            A missing file raises ``ConfigError``.
        start: Directory to search for a project ``tig.toml``.

    Returns:
        The merged configuration, with later layers overriding earlier ones.
    """
    if path is not None:
        candidates = [Path(path)]
        require_existing = True
    else:
        candidates = config_search_paths(start)
        require_existing = bool(os.environ.get("TIG_CONFIG"))

    config = Config()
    for candidate in candidates:
        if not candidate.is_file():
            if require_existing:
                raise ConfigError(f"Config file not found: {candidate}")
            continue
        _apply(config, _read_file(candidate), candidate)
        config.sources.append(candidate)
    return config


def _env_bool(name: str) -> bool | None:
    raw = os.environ.get(name)
    if raw is None:
        return None
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off", ""}:
        return False
    raise ConfigError(f"{name} must be a boolean value, got {raw!r}.")


def env_runtime() -> str | None:
    """Return the container runtime named by ``TIG_CONTAINER_RUNTIME``, if set."""
    value = os.environ.get(RUNTIME_ENV, "").strip()
    return value or None


def env_writable_paths() -> list[str] | None:
    """Return paths from ``TIG_WRITABLE_PATHS`` (``os.pathsep``-separated)."""
    raw = os.environ.get("TIG_WRITABLE_PATHS")
    if raw is None:
        return None
    return [p for p in raw.split(os.pathsep) if p]


def env_disable_path_translation() -> bool | None:
    """Return the value of ``TIG_DISABLE_PATH_TRANSLATION``, if set."""
    return _env_bool("TIG_DISABLE_PATH_TRANSLATION")


def env_selinux_label_disable() -> bool | None:
    """Return the value of ``TIG_SELINUX_LABEL_DISABLE``, if set."""
    return _env_bool("TIG_SELINUX_LABEL_DISABLE")

