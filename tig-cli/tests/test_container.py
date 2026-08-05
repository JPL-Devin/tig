"""Tests for container management."""
import os
import pytest
from unittest.mock import Mock, patch, MagicMock
import docker
from tig_cli.container import (
    ContainerManager,
    IMAGE_PLATFORM,
    TigError,
    get_container_image,
    DEFAULT_IMAGE,
)


# --- get_container_image ---

def test_get_container_image_default():
    """Returns default image when CONTAINER_IMAGE not set."""
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("CONTAINER_IMAGE", None)
        assert get_container_image() == DEFAULT_IMAGE


def test_get_container_image_from_env():
    """Returns value of CONTAINER_IMAGE env var."""
    custom = "ghcr.io/my-org/custom-vicar:v2"
    with patch.dict(os.environ, {"CONTAINER_IMAGE": custom}):
        assert get_container_image() == custom


# --- ContainerManager init ---

@pytest.fixture
def home_dir(tmp_path):
    return str(tmp_path / "home" / "user")


def test_container_manager_init(home_dir):
    """ContainerManager initializes with image string."""
    with patch('tig_cli.container.docker.from_env'), \
         patch.dict(os.environ, {"HOME": home_dir}):
        manager = ContainerManager("test-image:latest")
    assert manager.image == "test-image:latest"
    assert manager.container_name.startswith("tig-vicar-")


def test_container_manager_default_no_translation(home_dir):
    """Path translation enabled by default."""
    with patch('tig_cli.container.docker.from_env'), \
         patch.dict(os.environ, {"HOME": home_dir}):
        manager = ContainerManager("test-image:latest")
    assert manager.disable_path_translation is False


# --- _build_volume_mounts ---

def test_build_volume_mounts_basic(home_dir):
    with patch('tig_cli.container.docker.from_env'), \
         patch.dict(os.environ, {"HOME": home_dir}):
        manager = ContainerManager("test-image:latest")
        volumes = manager._build_volume_mounts([])

    assert "/" in volumes
    assert volumes["/"]["bind"] == "/host"
    assert volumes["/"]["mode"] == "ro"
    assert home_dir in volumes
    assert volumes[home_dir]["bind"] == home_dir
    assert volumes[home_dir]["mode"] == "rw"


def test_build_volume_mounts_with_writable_paths(home_dir, tmp_path):
    writable_path = str(tmp_path / "data")
    os.makedirs(writable_path, exist_ok=True)

    with patch('tig_cli.container.docker.from_env'), \
         patch.dict(os.environ, {"HOME": home_dir}):
        manager = ContainerManager("test-image:latest")
        volumes = manager._build_volume_mounts([writable_path])

    assert writable_path in volumes
    assert volumes[writable_path]["bind"] == f"/host{writable_path}"
    assert volumes[writable_path]["mode"] == "rw"


def test_build_volume_mounts_skips_nonexistent_paths(home_dir):
    with patch('tig_cli.container.docker.from_env'), \
         patch.dict(os.environ, {"HOME": home_dir}):
        manager = ContainerManager("test-image:latest")
        volumes = manager._build_volume_mounts(["/nonexistent/path"])

    assert "/nonexistent/path" not in volumes


def test_build_volume_mounts_makes_cwd_writable(home_dir, tmp_path):
    """A cwd outside home is mounted read-write, not just read-only at /host."""
    cwd = tmp_path / "scenes"
    cwd.mkdir()

    with patch('tig_cli.container.docker.from_env'), \
         patch.dict(os.environ, {"HOME": home_dir}):
        manager = ContainerManager("test-image:latest")
        with patch('os.getcwd', return_value=str(cwd)):
            volumes = manager._build_volume_mounts([])

    assert volumes[str(cwd)] == {"bind": f"/host{cwd}", "mode": "rw"}


def test_build_volume_mounts_skips_cwd_inside_home(home_dir):
    """A cwd under home needs no extra mount; home is already read-write."""
    os.makedirs(f"{home_dir}/projects", exist_ok=True)

    with patch('tig_cli.container.docker.from_env'), \
         patch.dict(os.environ, {"HOME": home_dir}):
        manager = ContainerManager("test-image:latest")
        with patch('os.getcwd', return_value=f"{home_dir}/projects"):
            volumes = manager._build_volume_mounts([])

    assert f"{home_dir}/projects" not in volumes


# --- start_container ---

@patch('tig_cli.container.docker.from_env')
def test_start_container_linux(mock_docker, home_dir):
    mock_client = MagicMock()
    mock_docker.return_value = mock_client

    with patch.dict(os.environ, {"HOME": home_dir, "DISPLAY": ":0"}):
        manager = ContainerManager("test-image:latest")
        with patch('sys.platform', 'linux'):
            manager.start_container([])

    call_kwargs = mock_client.containers.run.call_args[1]
    assert call_kwargs['image'] == "test-image:latest"
    assert call_kwargs['detach'] is True
    assert call_kwargs['network_mode'] == 'host'
    assert 'DISPLAY' in call_kwargs['environment']
    assert call_kwargs['platform'] == IMAGE_PLATFORM
    assert call_kwargs['user'] == f"{os.getuid()}:{os.getgid()}"


@patch('tig_cli.container.docker.from_env')
def test_start_container_macos(mock_docker, home_dir):
    mock_client = MagicMock()
    mock_docker.return_value = mock_client

    with patch.dict(os.environ, {"HOME": home_dir}):
        manager = ContainerManager("test-image:latest")
        with patch('sys.platform', 'darwin'):
            manager.start_container([])

    call_kwargs = mock_client.containers.run.call_args[1]
    assert call_kwargs['environment']['DISPLAY'] == 'host.docker.internal:0'
    assert 'network_mode' not in call_kwargs
    assert call_kwargs['platform'] == IMAGE_PLATFORM
    # Docker Desktop maps ownership already, so no user override.
    assert 'user' not in call_kwargs


@patch('tig_cli.container.docker.from_env')
def test_start_container_removes_stale_container(mock_docker, home_dir):
    """A leftover container of the same name is removed before reuse."""
    mock_client = MagicMock()
    stale = MagicMock()
    mock_client.containers.get.return_value = stale
    mock_docker.return_value = mock_client

    with patch.dict(os.environ, {"HOME": home_dir}):
        manager = ContainerManager("test-image:latest")
        manager.start_container([])

    mock_client.containers.get.assert_called_once_with(manager.container_name)
    stale.remove.assert_called_once_with(force=True)


@patch('tig_cli.container.docker.from_env')
def test_start_container_image_not_found_is_user_facing(mock_docker, home_dir):
    mock_client = MagicMock()
    mock_client.containers.get.side_effect = docker.errors.NotFound("absent")
    mock_client.containers.run.side_effect = docker.errors.ImageNotFound("nope")
    mock_docker.return_value = mock_client

    with patch.dict(os.environ, {"HOME": home_dir}):
        manager = ContainerManager("test-image:latest")
        with pytest.raises(TigError, match="Image not found: test-image:latest"):
            manager.start_container([])


def test_missing_docker_cli_is_user_facing(home_dir):
    with patch('tig_cli.container.shutil.which', return_value=None), \
         patch.dict(os.environ, {"HOME": home_dir}):
        with pytest.raises(TigError, match="not found on PATH"):
            ContainerManager("test-image:latest")


def test_docker_daemon_unavailable_is_user_facing(home_dir):
    with patch('tig_cli.container.docker.from_env',
               side_effect=docker.errors.DockerException("boom")), \
         patch.dict(os.environ, {"HOME": home_dir}):
        with pytest.raises(TigError, match="Is the Docker daemon running"):
            ContainerManager("test-image:latest")


# --- stop_container ---

@patch('tig_cli.container.docker.from_env')
def test_stop_container(mock_docker, home_dir):
    mock_client = MagicMock()
    mock_container = MagicMock()
    mock_docker.return_value = mock_client

    with patch.dict(os.environ, {"HOME": home_dir}):
        manager = ContainerManager("test-image:latest")
    manager.container = mock_container
    manager.stop_container()

    mock_container.remove.assert_called_once_with(force=True)
    assert manager.container is None


@patch('tig_cli.container.docker.from_env')
def test_stop_container_no_container(mock_docker, home_dir):
    """stop_container is safe to call when container never started."""
    mock_docker.return_value = MagicMock()
    with patch.dict(os.environ, {"HOME": home_dir}):
        manager = ContainerManager("test-image:latest")
    manager.stop_container()  # should not raise


# --- execute_vicar_command ---

@patch('tig_cli.container.subprocess.run')
def test_execute_vicar_command(mock_run, home_dir):
    mock_run.return_value = Mock(returncode=0)

    with patch('tig_cli.container.docker.from_env'), \
         patch.dict(os.environ, {"HOME": home_dir}):
        manager = ContainerManager("test-image:latest")
        with patch('os.getcwd', return_value=f"{home_dir}/projects"):
            exit_code = manager.execute_vicar_command("marsmap", ["input.vic", "output.vic"])

    assert exit_code == 0
    call_args = mock_run.call_args[0][0]
    assert call_args[0] == "docker"
    assert call_args[1] == "exec"
    assert "-i" in call_args
    assert "marsmap" in call_args
    assert "input.vic" in call_args
    assert "output.vic" in call_args


@patch('tig_cli.container.subprocess.run')
def test_execute_vicar_command_allocates_tty_when_interactive(mock_run, home_dir):
    mock_run.return_value = Mock(returncode=0)

    with patch('tig_cli.container.docker.from_env'), \
         patch.dict(os.environ, {"HOME": home_dir}):
        manager = ContainerManager("test-image:latest")
        with patch('sys.stdin.isatty', return_value=True), \
             patch('sys.stdout.isatty', return_value=True):
            manager.execute_vicar_command("tae", [])

    assert "-t" in mock_run.call_args[0][0]


@patch('tig_cli.container.subprocess.run')
def test_execute_vicar_command_no_tty_when_piped(mock_run, home_dir):
    """A TTY would merge stderr into stdout and mangle redirected output."""
    mock_run.return_value = Mock(returncode=0)

    with patch('tig_cli.container.docker.from_env'), \
         patch.dict(os.environ, {"HOME": home_dir}):
        manager = ContainerManager("test-image:latest")
        with patch('sys.stdin.isatty', return_value=False), \
             patch('sys.stdout.isatty', return_value=True):
            manager.execute_vicar_command("list", [])

    assert "-t" not in mock_run.call_args[0][0]


@patch('tig_cli.container.subprocess.run')
def test_execute_vicar_command_with_path_translation(mock_run, home_dir):
    mock_run.return_value = Mock(returncode=0)

    with patch('tig_cli.container.docker.from_env'), \
         patch.dict(os.environ, {"HOME": home_dir}):
        manager = ContainerManager("test-image:latest")
        with patch('os.getcwd', return_value=f"{home_dir}/projects"):
            manager.execute_vicar_command(
                "marsmap",
                ["/data/input.vic", f"{home_dir}/output.vic"]
            )

    call_args = mock_run.call_args[0][0]
    assert "/host/data/input.vic" in call_args
    assert f"{home_dir}/output.vic" in call_args


@patch('tig_cli.container.subprocess.run')
def test_execute_vicar_command_translates_keyword_args(mock_run, home_dir):
    mock_run.return_value = Mock(returncode=0)

    with patch('tig_cli.container.docker.from_env'), \
         patch.dict(os.environ, {"HOME": home_dir}):
        manager = ContainerManager("test-image:latest")
        with patch('os.getcwd', return_value=f"{home_dir}/projects"):
            manager.execute_vicar_command(
                "marsmap", ["INP=/data/input.vic", "SIZE=(1,1,500,500)"]
            )

    call_args = mock_run.call_args[0][0]
    assert "INP=/host/data/input.vic" in call_args
    assert "SIZE=(1,1,500,500)" in call_args


@patch('tig_cli.container.subprocess.run')
def test_execute_vicar_command_without_translation(mock_run, home_dir):
    mock_run.return_value = Mock(returncode=0)

    with patch('tig_cli.container.docker.from_env'), \
         patch.dict(os.environ, {"HOME": home_dir}):
        manager = ContainerManager("test-image:latest", disable_path_translation=True)
        with patch('os.getcwd', return_value=f"{home_dir}/projects"):
            manager.execute_vicar_command("marsmap", ["/data/input.vic"])

    call_args = mock_run.call_args[0][0]
    assert "/data/input.vic" in call_args
    assert "/host/data/input.vic" not in call_args
