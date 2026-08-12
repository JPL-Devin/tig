"""Tests for layered configuration loading."""
import os

import pytest

from tig_cli import config as config_mod
from tig_cli.config import (
    Config,
    ConfigError,
    config_search_paths,
    env_disable_path_translation,
    env_runtime,
    env_selinux_label_disable,
    env_writable_paths,
    find_project_config,
    load_config,
    user_config_path,
)


# --- user_config_path ---

def test_user_config_path_uses_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert user_config_path() == tmp_path / "tig" / "config.toml"


def test_user_config_path_defaults_to_home(monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert user_config_path() == tmp_path / ".config" / "tig" / "config.toml"


# --- find_project_config ---

def test_find_project_config_walks_up(tmp_path):
    (tmp_path / "tig.toml").write_text("")
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    assert find_project_config(nested) == tmp_path / "tig.toml"


def test_find_project_config_prefers_nearest(tmp_path):
    (tmp_path / "tig.toml").write_text("")
    nested = tmp_path / "a"
    nested.mkdir()
    (nested / "tig.toml").write_text("")
    assert find_project_config(nested) == nested / "tig.toml"


def test_find_project_config_absent(tmp_path):
    assert find_project_config(tmp_path) is None


# --- search order ---

def test_config_search_paths_order(monkeypatch, tmp_path):
    monkeypatch.delenv("TIG_CONFIG", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    project = tmp_path / "proj"
    project.mkdir()
    (project / "tig.toml").write_text("")

    paths = config_search_paths(project)
    assert paths == [
        config_mod.SYSTEM_CONFIG_PATH,
        tmp_path / "xdg" / "tig" / "config.toml",
        project / "tig.toml",
    ]


def test_tig_config_env_replaces_search(monkeypatch, tmp_path):
    explicit = tmp_path / "only.toml"
    monkeypatch.setenv("TIG_CONFIG", str(explicit))
    assert config_search_paths(tmp_path) == [explicit]


# --- loading and merging ---

def test_load_config_empty_when_no_files(monkeypatch, tmp_path):
    monkeypatch.delenv("TIG_CONFIG", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(config_mod, "SYSTEM_CONFIG_PATH", tmp_path / "etc.toml")

    config = load_config(start=tmp_path)
    assert config == Config()


def test_load_config_reads_all_keys(tmp_path):
    path = tmp_path / "tig.toml"
    path.write_text(
        'image = "ghcr.io/my-org/vicar:v3"\n'
        'writable_paths = ["/data", "/scratch"]\n'
        'disable_path_translation = true\n'
        'calibration_path = "/opt/mars_calibration"\n'
    )
    config = load_config(path)
    assert config.image == "ghcr.io/my-org/vicar:v3"
    assert config.writable_paths == ["/data", "/scratch"]
    assert config.disable_path_translation is True
    assert config.calibration_path == "/opt/mars_calibration"
    assert config.sources == [path]


def test_calibration_path_expands_user(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", "/home/tester")
    path = tmp_path / "tig.toml"
    path.write_text('calibration_path = "~/.mars_calib"\n')
    assert load_config(path).calibration_path == "/home/tester/.mars_calib"


def test_user_config_overrides_system(monkeypatch, tmp_path):
    monkeypatch.delenv("TIG_CONFIG", raising=False)
    system = tmp_path / "etc.toml"
    system.write_text('image = "system:1"\nwritable_paths = ["/system"]\n')
    monkeypatch.setattr(config_mod, "SYSTEM_CONFIG_PATH", system)

    xdg = tmp_path / "xdg"
    (xdg / "tig").mkdir(parents=True)
    (xdg / "tig" / "config.toml").write_text('image = "user:2"\n')
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))

    config = load_config(start=tmp_path)
    assert config.image == "user:2"
    assert config.writable_paths == ["/system"]


def test_project_config_overrides_user(monkeypatch, tmp_path):
    monkeypatch.delenv("TIG_CONFIG", raising=False)
    monkeypatch.setattr(config_mod, "SYSTEM_CONFIG_PATH", tmp_path / "missing.toml")

    xdg = tmp_path / "xdg"
    (xdg / "tig").mkdir(parents=True)
    (xdg / "tig" / "config.toml").write_text(
        'image = "user:2"\ndisable_path_translation = true\n'
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))

    project = tmp_path / "proj"
    project.mkdir()
    (project / "tig.toml").write_text('image = "project:3"\n')

    config = load_config(start=project)
    assert config.image == "project:3"
    assert config.disable_path_translation is True
    assert config.sources == [
        xdg / "tig" / "config.toml",
        project / "tig.toml",
    ]


def test_load_config_missing_explicit_file(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.toml")


def test_load_config_missing_tig_config_env(monkeypatch, tmp_path):
    monkeypatch.setenv("TIG_CONFIG", str(tmp_path / "nope.toml"))
    with pytest.raises(ConfigError, match="not found"):
        load_config()


def test_load_config_invalid_toml(tmp_path):
    path = tmp_path / "tig.toml"
    path.write_text("image = ")
    with pytest.raises(ConfigError, match="Invalid TOML"):
        load_config(path)


def test_load_config_unknown_key(tmp_path):
    path = tmp_path / "tig.toml"
    path.write_text('imagee = "typo:1"\n')
    with pytest.raises(ConfigError, match="Unknown key"):
        load_config(path)


@pytest.mark.parametrize("body,message", [
    ("image = 5\n", "'image' must be a string"),
    ('writable_paths = "/data"\n', "'writable_paths' must be a list"),
    ("writable_paths = [1]\n", "'writable_paths' must be a list"),
    ('disable_path_translation = "yes"\n', "must be a boolean"),
    ("calibration_path = 3\n", "'calibration_path' must be a string"),
])
def test_load_config_type_errors(tmp_path, body, message):
    path = tmp_path / "tig.toml"
    path.write_text(body)
    with pytest.raises(ConfigError, match=message):
        load_config(path)


# --- environment variable helpers ---

def test_env_writable_paths_unset(monkeypatch):
    monkeypatch.delenv("TIG_WRITABLE_PATHS", raising=False)
    assert env_writable_paths() is None


def test_env_writable_paths_splits(monkeypatch):
    monkeypatch.setenv("TIG_WRITABLE_PATHS", os.pathsep.join(["/data", "/scratch"]))
    assert env_writable_paths() == ["/data", "/scratch"]


def test_env_writable_paths_empty(monkeypatch):
    monkeypatch.setenv("TIG_WRITABLE_PATHS", "")
    assert env_writable_paths() == []


@pytest.mark.parametrize("raw,expected", [
    ("1", True), ("true", True), ("TRUE", True), ("yes", True), ("on", True),
    ("0", False), ("false", False), ("no", False), ("off", False), ("", False),
])
def test_env_disable_path_translation(monkeypatch, raw, expected):
    monkeypatch.setenv("TIG_DISABLE_PATH_TRANSLATION", raw)
    assert env_disable_path_translation() is expected


def test_env_disable_path_translation_unset(monkeypatch):
    monkeypatch.delenv("TIG_DISABLE_PATH_TRANSLATION", raising=False)
    assert env_disable_path_translation() is None


def test_env_disable_path_translation_invalid(monkeypatch):
    monkeypatch.setenv("TIG_DISABLE_PATH_TRANSLATION", "maybe")
    with pytest.raises(ConfigError, match="boolean"):
        env_disable_path_translation()


# --- selinux_label_disable ---

def test_selinux_label_disable_key(tmp_path):
    path = tmp_path / "tig.toml"
    path.write_text("selinux_label_disable = true\n")
    assert load_config(path).selinux_label_disable is True


def test_selinux_label_disable_defaults_to_unset(tmp_path):
    path = tmp_path / "tig.toml"
    path.write_text("")
    assert load_config(path).selinux_label_disable is None


def test_selinux_label_disable_must_be_boolean(tmp_path):
    path = tmp_path / "tig.toml"
    path.write_text('selinux_label_disable = "yes"\n')
    with pytest.raises(ConfigError, match="must be a boolean"):
        load_config(path)


def test_env_selinux_label_disable(monkeypatch):
    monkeypatch.setenv("TIG_SELINUX_LABEL_DISABLE", "true")
    assert env_selinux_label_disable() is True


def test_env_selinux_label_disable_unset(monkeypatch):
    monkeypatch.delenv("TIG_SELINUX_LABEL_DISABLE", raising=False)
    assert env_selinux_label_disable() is None


def test_env_selinux_label_disable_invalid(monkeypatch):
    monkeypatch.setenv("TIG_SELINUX_LABEL_DISABLE", "maybe")
    with pytest.raises(ConfigError, match="boolean"):
        env_selinux_label_disable()


# --- runtime ---

def test_runtime_key(tmp_path):
    path = tmp_path / "tig.toml"
    path.write_text('runtime = "podman"\n')
    assert load_config(path).runtime == "podman"


def test_runtime_defaults_to_unset(tmp_path):
    path = tmp_path / "tig.toml"
    path.write_text("")
    assert load_config(path).runtime is None


def test_runtime_must_be_a_string(tmp_path):
    path = tmp_path / "tig.toml"
    path.write_text("runtime = 3\n")
    with pytest.raises(ConfigError, match="must be a string"):
        load_config(path)


def test_env_runtime(monkeypatch):
    monkeypatch.setenv("TIG_CONTAINER_RUNTIME", "nerdctl")
    assert env_runtime() == "nerdctl"


def test_env_runtime_unset(monkeypatch):
    monkeypatch.delenv("TIG_CONTAINER_RUNTIME", raising=False)
    assert env_runtime() is None
