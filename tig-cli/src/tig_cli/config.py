"""Layered TOML configuration for the tig CLI.

Configuration is read from up to three files, each overriding the previous:

1. system  — ``/etc/tig/config.toml``
2. user    — ``$XDG_CONFIG_HOME/tig/config.toml`` (``~/.config/tig/config.toml``)
3. project — the nearest ``tig.toml`` found walking up from the current directory

Setting ``TIG_CONFIG`` (or passing an explicit path) replaces the search
entirely and loads only that file.
"""
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib

SYSTEM_CONFIG_PATH = Path("/etc/tig/config.toml")
USER_CONFIG_SUBPATH = Path("tig/config.toml")
PROJECT_CONFIG_NAME = "tig.toml"

KNOWN_KEYS = frozenset({
    "image",
    "writable_paths",
    "disable_path_translation",
    "calibration_path",
})


class ConfigError(Exception):
    """Raised when a configuration file is unreadable or invalid."""


@dataclass
class Config:
    """Resolved configuration values.

    ``None`` means the setting was not configured, so a caller may fall back
    to an environment variable or a built-in default.
    """

    image: Optional[str] = None
    writable_paths: List[str] = field(default_factory=list)
    disable_path_translation: Optional[bool] = None
    calibration_path: Optional[str] = None
    sources: List[Path] = field(default_factory=list)


def user_config_path() -> Path:
    """Return the path of the per-user configuration file."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path(os.environ.get("HOME") or Path.home()) / ".config"
    return base / USER_CONFIG_SUBPATH


def find_project_config(start: Optional[Path] = None) -> Optional[Path]:
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


def config_search_paths(start: Optional[Path] = None) -> List[Path]:
    """Return the configuration files to load, lowest precedence first."""
    explicit = os.environ.get("TIG_CONFIG")
    if explicit:
        return [Path(explicit)]

    paths = [SYSTEM_CONFIG_PATH, user_config_path()]
    project = find_project_config(start)
    if project:
        paths.append(project)
    return paths


def _read_file(path: Path) -> Dict[str, Any]:
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


def _apply(config: Config, data: Dict[str, Any], path: Path) -> None:
    if "image" in data:
        value = data["image"]
        if not isinstance(value, str):
            raise ConfigError(f"{path}: 'image' must be a string.")
        config.image = value

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


def load_config(
    path: Optional[Path] = None,
    start: Optional[Path] = None,
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


def _env_bool(name: str) -> Optional[bool]:
    raw = os.environ.get(name)
    if raw is None:
        return None
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off", ""}:
        return False
    raise ConfigError(f"{name} must be a boolean value, got {raw!r}.")


def env_writable_paths() -> Optional[List[str]]:
    """Return paths from ``TIG_WRITABLE_PATHS`` (``os.pathsep``-separated)."""
    raw = os.environ.get("TIG_WRITABLE_PATHS")
    if raw is None:
        return None
    return [p for p in raw.split(os.pathsep) if p]


def env_disable_path_translation() -> Optional[bool]:
    """Return the value of ``TIG_DISABLE_PATH_TRANSLATION``, if set."""
    return _env_bool("TIG_DISABLE_PATH_TRANSLATION")

