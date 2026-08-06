"""Shared test fixtures."""
import pytest


@pytest.fixture(autouse=True)
def isolated_config(tmp_path_factory, monkeypatch):
    """Point tig at an empty config file so host config never leaks into tests."""
    empty = tmp_path_factory.mktemp("tig-config") / "config.toml"
    empty.write_text("")
    monkeypatch.setenv("TIG_CONFIG", str(empty))
    for name in (
        "TIG_WRITABLE_PATHS",
        "TIG_DISABLE_PATH_TRANSLATION",
        "MARS_CONFIG_PATH",
    ):
        monkeypatch.delenv(name, raising=False)
    yield
