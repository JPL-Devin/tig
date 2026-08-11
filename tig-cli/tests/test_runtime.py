"""Tests for choosing and driving the host's container runtime."""
import hashlib
import json

import pytest

from tig_cli.config import Config
from tig_cli.runtime import (
    CommandFailed,
    KNOWN_RUNTIMES,
    Runtime,
    RuntimeCommandError,
    TigError,
)


@pytest.fixture
def installed(monkeypatch):
    """Control which runtime commands the host appears to have."""
    def install(*names):
        paths = {name: f"/usr/bin/{name}" for name in names}
        monkeypatch.setattr(
            "shutil.which", lambda command: paths.get(command)
        )
    return install


@pytest.fixture(autouse=True)
def no_runtime_environment(monkeypatch):
    for name in (
        "TIG_CONTAINER_RUNTIME", "DOCKER_HOST", "DOCKER_CONTEXT",
        "DOCKER_CONFIG", "CONTAINER_HOST",
    ):
        monkeypatch.delenv(name, raising=False)
    yield


# --- detection ---

def test_uses_docker_when_it_is_installed(installed):
    installed("docker", "podman")

    assert Runtime.detect().name == "docker"


def test_falls_back_to_another_runtime(installed):
    installed("podman")

    runtime = Runtime.detect()

    assert (runtime.name, runtime.executable) == ("podman", "/usr/bin/podman")


@pytest.mark.parametrize("name", KNOWN_RUNTIMES)
def test_every_known_runtime_can_be_the_one_installed(installed, name):
    installed(name)

    assert Runtime.detect().name == name


def test_the_environment_chooses_the_runtime(installed, monkeypatch):
    installed("docker", "podman")
    monkeypatch.setenv("TIG_CONTAINER_RUNTIME", "podman")

    assert Runtime.detect().name == "podman"


def test_the_config_chooses_the_runtime(installed):
    installed("docker", "nerdctl")

    assert Runtime.detect(Config(runtime="nerdctl")).name == "nerdctl"


def test_the_environment_overrides_the_config(installed, monkeypatch):
    installed("docker", "podman")
    monkeypatch.setenv("TIG_CONTAINER_RUNTIME", "podman")

    assert Runtime.detect(Config(runtime="docker")).name == "podman"


def test_an_unlisted_runtime_can_be_named(installed):
    """A Docker-compatible command line tig has not heard of still works."""
    installed("crun-compatible-thing")

    runtime = Runtime.detect(Config(runtime="crun-compatible-thing"))

    assert runtime.name == "crun-compatible-thing"


def test_reports_a_named_runtime_that_is_not_installed(installed):
    installed("docker")

    with pytest.raises(TigError, match="podman"):
        Runtime.detect(Config(runtime="podman"))


def test_reports_when_no_runtime_is_installed(installed):
    installed()

    with pytest.raises(TigError, match="No container runtime"):
        Runtime.detect()


# --- API endpoint ---

def test_docker_uses_its_default_socket(monkeypatch):
    monkeypatch.setattr("os.path.exists", lambda path: path == "/var/run/docker.sock")

    assert Runtime("docker", "/usr/bin/docker").api_host() == (
        "unix:///var/run/docker.sock"
    )


def test_docker_without_a_socket(monkeypatch):
    monkeypatch.setattr("os.path.exists", lambda path: False)

    assert Runtime("docker", "/usr/bin/docker").api_host() is None


def test_the_environment_names_the_endpoint(monkeypatch):
    monkeypatch.setenv("DOCKER_HOST", "tcp://10.0.0.1:2375")

    assert Runtime("podman", "/usr/bin/podman").api_host() == "tcp://10.0.0.1:2375"


def test_docker_follows_its_context(monkeypatch, tmp_path):
    monkeypatch.setenv("DOCKER_CONFIG", str(tmp_path))
    monkeypatch.setenv("DOCKER_CONTEXT", "colima")
    digest = hashlib.sha256(b"colima").hexdigest()
    meta = tmp_path / "contexts" / "meta" / digest
    meta.mkdir(parents=True)
    (meta / "meta.json").write_text(
        json.dumps({"Endpoints": {"docker": {"Host": "unix:///colima.sock"}}})
    )

    assert Runtime("docker", "/usr/bin/docker").api_host() == "unix:///colima.sock"


def test_docker_reports_an_unknown_context(monkeypatch, tmp_path):
    monkeypatch.setenv("DOCKER_CONFIG", str(tmp_path))
    monkeypatch.setenv("DOCKER_CONTEXT", "missing")

    with pytest.raises(TigError, match="Unknown Docker context"):
        Runtime("docker", "/usr/bin/docker").api_host()


def test_podman_uses_its_rootless_socket(monkeypatch, tmp_path):
    socket = tmp_path / "podman" / "podman.sock"
    socket.parent.mkdir()
    socket.touch()
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))

    assert Runtime("podman", "/usr/bin/podman").api_host() == f"unix://{socket}"


def test_podman_follows_container_host(monkeypatch):
    monkeypatch.setenv("CONTAINER_HOST", "unix:///run/podman/elsewhere.sock")

    assert Runtime("podman", "/usr/bin/podman").api_host() == (
        "unix:///run/podman/elsewhere.sock"
    )


def test_podman_without_a_running_service(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr("os.path.exists", lambda path: False)

    assert Runtime("podman", "/usr/bin/podman").api_host() is None


@pytest.mark.parametrize("name", ["nerdctl", "finch"])
def test_containerd_runtimes_serve_no_api(name):
    """They speak no Docker API at all, so the warm path must not try."""
    assert Runtime(name, f"/usr/bin/{name}").api_host() is None


# --- running commands ---

def test_runs_the_runtime_with_its_arguments():
    runtime = Runtime("docker", "/usr/bin/docker")

    assert runtime.args("ps", "--all") == ["/usr/bin/docker", "ps", "--all"]


def test_returns_what_the_runtime_printed(monkeypatch):
    runtime = Runtime("podman", "/usr/bin/podman")
    monkeypatch.setattr(
        "subprocess.run", lambda *a, **k: _completed(0, "tig-vicar-1\n")
    )

    assert runtime.run("ps") == "tig-vicar-1\n"


def test_reports_what_the_runtime_complained(monkeypatch):
    runtime = Runtime("podman", "/usr/bin/podman")
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **k: _completed(125, "", "no such container: tig-vicar-1"),
    )

    with pytest.raises(CommandFailed, match="no such container") as raised:
        runtime.run("start", "tig-vicar-1")

    assert raised.value.exit_code == 125


def test_reports_a_runtime_that_cannot_be_run(monkeypatch):
    runtime = Runtime("docker", "/usr/bin/docker")

    def missing(*args, **kwargs):
        raise OSError("gone")

    monkeypatch.setattr("subprocess.run", missing)

    with pytest.raises(RuntimeCommandError, match="Failed to run docker"):
        runtime.run("ps")


def test_inspect_reads_the_first_object(monkeypatch):
    runtime = Runtime("docker", "/usr/bin/docker")
    described = json.dumps([{"Id": "sha256:image"}])
    monkeypatch.setattr("subprocess.run", lambda *a, **k: _completed(0, described))

    assert runtime.inspect("image", "tig:latest") == {"Id": "sha256:image"}


@pytest.mark.parametrize("described", ["", "[]", "not json", "{}"])
def test_inspect_rejects_a_reply_it_cannot_read(monkeypatch, described):
    runtime = Runtime("docker", "/usr/bin/docker")
    monkeypatch.setattr("subprocess.run", lambda *a, **k: _completed(0, described))

    with pytest.raises(RuntimeCommandError):
        runtime.inspect("container", "tig-vicar-1")


class _completed:
    """What subprocess.run returns, as much of it as Runtime.run reads."""

    def __init__(self, returncode: int, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
