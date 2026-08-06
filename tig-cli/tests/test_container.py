"""Tests for container management."""
import os
import pytest
from unittest.mock import Mock, patch, MagicMock
from tig_cli.container import (
    CONTAINER_MARS_CONFIG_PATH,
    ContainerManager,
    get_container_image,
    DEFAULT_IMAGE,
)
from tig_cli.config import Config


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


def test_get_container_image_from_config():
    """Falls back to the config file image when the env var is unset."""
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("CONTAINER_IMAGE", None)
        assert get_container_image(Config(image="cfg:1")) == "cfg:1"


def test_get_container_image_env_beats_config():
    """CONTAINER_IMAGE takes precedence over the config file."""
    with patch.dict(os.environ, {"CONTAINER_IMAGE": "env:2"}):
        assert get_container_image(Config(image="cfg:1")) == "env:2"


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


def test_build_volume_mounts_with_mars_config(home_dir, tmp_path):
    calib = str(tmp_path / "mars_calib")
    os.makedirs(calib, exist_ok=True)

    with patch('tig_cli.container.docker.from_env'), \
         patch.dict(os.environ, {"HOME": home_dir}):
        manager = ContainerManager("test-image:latest", mars_config_path=calib)
        volumes = manager._build_volume_mounts([])

    assert volumes[calib] == {"bind": CONTAINER_MARS_CONFIG_PATH, "mode": "ro"}


def test_build_volume_mounts_warns_on_missing_mars_config(home_dir, capsys):
    with patch('tig_cli.container.docker.from_env'), \
         patch.dict(os.environ, {"HOME": home_dir}):
        manager = ContainerManager(
            "test-image:latest", mars_config_path="/nonexistent/calib"
        )
        volumes = manager._build_volume_mounts([])

    assert "/nonexistent/calib" not in volumes
    assert "directory not found" in capsys.readouterr().err


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


@patch('tig_cli.container.docker.from_env')
def test_start_container_sets_mars_config_env(mock_docker, home_dir, tmp_path):
    """Container sees MARS_CONFIG_PATH pointing at the in-container mount."""
    mock_client = MagicMock()
    mock_docker.return_value = mock_client
    calib = str(tmp_path / "mars_calib")
    os.makedirs(calib, exist_ok=True)

    with patch.dict(os.environ, {"HOME": home_dir}):
        manager = ContainerManager("test-image:latest", mars_config_path=calib)
        manager.start_container([])

    call_kwargs = mock_client.containers.run.call_args[1]
    assert call_kwargs['environment']['MARS_CONFIG_PATH'] == CONTAINER_MARS_CONFIG_PATH


@patch('tig_cli.container.docker.from_env')
def test_start_container_without_mars_config(mock_docker, home_dir):
    """MARS_CONFIG_PATH is not set in the container when unconfigured."""
    mock_client = MagicMock()
    mock_docker.return_value = mock_client

    with patch.dict(os.environ, {"HOME": home_dir}):
        manager = ContainerManager("test-image:latest")
        manager.start_container([])

    call_kwargs = mock_client.containers.run.call_args[1]
    assert 'MARS_CONFIG_PATH' not in call_kwargs['environment']


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

    mock_container.stop.assert_called_once()
    mock_container.remove.assert_called_once()


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
    assert "marsmap" in call_args
    assert "input.vic" in call_args
    assert "output.vic" in call_args


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
