"""Shared test fixtures."""
import pytest


@pytest.fixture(autouse=True)
def isolated_config(tmp_path_factory, monkeypatch):
    """Point tig at an empty config file so host config never leaks into tests."""
    empty = tmp_path_factory.mktemp("tig-config") / "config.toml"
    empty.write_text("")
    monkeypatch.setenv("TIG_CONFIG", str(empty))
    for name in (
        "TIG_BUILDER_IMAGE",
        "TIG_WRITABLE_PATHS",
        "TIG_DISABLE_PATH_TRANSLATION",
        "TIG_SELINUX_LABEL_DISABLE",
        "MARS_CONFIG_PATH",
    ):
        monkeypatch.delenv(name, raising=False)
    yield


@pytest.fixture(autouse=True)
def isolated_claim_dir(tmp_path_factory, monkeypatch):
    """Keep container claim files out of the real runtime directory."""
    monkeypatch.setenv(
        "XDG_RUNTIME_DIR", str(tmp_path_factory.mktemp("tig-runtime"))
    )
    yield


@pytest.fixture(autouse=True)
def no_host_display(request, monkeypatch):
    """Keep container creation from touching the host's xhost ACL."""
    if request.node.get_closest_marker("integration") is None:
        monkeypatch.delenv("DISPLAY", raising=False)
    yield
