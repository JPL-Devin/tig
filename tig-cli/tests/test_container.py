"""Tests for container management."""
import os
import pytest
from unittest.mock import Mock, patch, MagicMock
import docker
from tig_cli.config import Config
from tig_cli.container import (
    CALIBRATION_MOUNT,
    CONTAINER_PREFIX,
    ContainerManager,
    IMAGE_PLATFORM,
    TigError,
    get_calibration_path,
    get_container_image,
    DEFAULT_IMAGE,
)


@pytest.fixture
def home_dir(tmp_path):
    return str(tmp_path / "home" / "user")


def make_manager(home, image="test-image:latest", client=None, **kwargs):
    """Build a ContainerManager with Docker mocked out."""
    with patch('tig_cli.container.docker.from_env',
               return_value=client or MagicMock()), \
         patch.dict(os.environ, {"HOME": home}):
        return ContainerManager(image, **kwargs)


def make_client(image_id="sha256:image"):
    """Docker client mock with no pre-existing container."""
    client = MagicMock()
    client.containers.get.side_effect = docker.errors.NotFound("absent")
    client.images.get.return_value = MagicMock(id=image_id)
    return client


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


# --- get_calibration_path ---

def test_get_calibration_path_unset(monkeypatch):
    monkeypatch.delenv("MARS_CONFIG_PATH", raising=False)
    assert get_calibration_path() is None


def test_get_calibration_path_from_config(monkeypatch):
    monkeypatch.delenv("MARS_CONFIG_PATH", raising=False)
    assert get_calibration_path(Config(calibration_path="/cfg")) == "/cfg"


def test_get_calibration_path_env_beats_config(monkeypatch):
    monkeypatch.setenv("MARS_CONFIG_PATH", "/env")
    assert get_calibration_path(Config(calibration_path="/cfg")) == "/env"


def test_get_calibration_path_env_expands_user(monkeypatch):
    monkeypatch.setenv("HOME", "/home/tester")
    monkeypatch.setenv("MARS_CONFIG_PATH", "~/calib")
    assert get_calibration_path() == "/home/tester/calib"


# --- ContainerManager init ---

def test_container_manager_init(home_dir):
    """ContainerManager initializes with image string."""
    manager = make_manager(home_dir)
    assert manager.image == "test-image:latest"


def test_container_manager_default_no_translation(home_dir):
    """Path translation enabled by default."""
    assert make_manager(home_dir).disable_path_translation is False


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


# --- _build_volume_mounts ---

def test_build_volume_mounts_basic(home_dir):
    volumes = make_manager(home_dir)._build_volume_mounts([])

    assert "/" in volumes
    assert volumes["/"]["bind"] == "/host"
    assert volumes["/"]["mode"] == "ro"
    assert home_dir in volumes
    assert volumes[home_dir]["bind"] == home_dir
    assert volumes[home_dir]["mode"] == "rw"


def test_build_volume_mounts_with_writable_paths(home_dir, tmp_path):
    writable_path = str(tmp_path / "data")
    os.makedirs(writable_path, exist_ok=True)

    volumes = make_manager(home_dir)._build_volume_mounts([writable_path])

    assert writable_path in volumes
    assert volumes[writable_path]["bind"] == f"/host{writable_path}"
    assert volumes[writable_path]["mode"] == "rw"


def test_build_volume_mounts_skips_nonexistent_paths(home_dir):
    volumes = make_manager(home_dir)._build_volume_mounts(["/nonexistent/path"])

    assert "/nonexistent/path" not in volumes


def test_build_volume_mounts_makes_cwd_writable(home_dir, tmp_path):
    """A cwd outside home is mounted read-write, not just read-only at /host."""
    cwd = tmp_path / "scenes"
    cwd.mkdir()

    manager = make_manager(home_dir)
    with patch('os.getcwd', return_value=str(cwd)):
        volumes = manager._build_volume_mounts([])

    assert volumes[str(cwd)] == {"bind": f"/host{cwd}", "mode": "rw"}


def test_build_volume_mounts_skips_cwd_inside_home(home_dir):
    """A cwd under home needs no extra mount; home is already read-write."""
    os.makedirs(f"{home_dir}/projects", exist_ok=True)

    manager = make_manager(home_dir)
    with patch('os.getcwd', return_value=f"{home_dir}/projects"):
        volumes = manager._build_volume_mounts([])

    assert f"{home_dir}/projects" not in volumes


# --- calibration files ---

def test_get_calibration_path_from_env(tmp_path):
    with patch.dict(os.environ, {"MARS_CONFIG_PATH": str(tmp_path)}):
        assert get_calibration_path() == str(tmp_path)


def test_get_calibration_path_unset():
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("MARS_CONFIG_PATH", None)
        assert get_calibration_path() is None


def test_calibration_path_is_mounted_read_only(home_dir, tmp_path):
    calib = tmp_path / "mars_calibration_m20"
    calib.mkdir()

    manager = make_manager(home_dir, calibration_path=str(calib))
    volumes = manager._build_volume_mounts([])

    assert volumes[str(calib)] == {"bind": CALIBRATION_MOUNT, "mode": "ro"}


def test_calibration_path_is_exported_to_the_container(home_dir, tmp_path):
    calib = tmp_path / "mars_calibration_m20"
    calib.mkdir()

    manager = make_manager(home_dir, calibration_path=str(calib))
    kwargs = manager._run_kwargs(manager._build_volume_mounts([]))

    assert kwargs["environment"]["MARS_CONFIG_PATH"] == CALIBRATION_MOUNT


def test_missing_calibration_path_is_user_facing(home_dir):
    with pytest.raises(TigError, match="not a directory"):
        make_manager(home_dir, calibration_path="/nonexistent/calib")


# --- ensure_container ---

def test_ensure_container_linux(home_dir):
    client = make_client()

    manager = make_manager(home_dir, client=client)
    with patch('sys.platform', 'linux'):
        manager.ensure_container([])

    call_kwargs = client.containers.run.call_args[1]
    assert call_kwargs['image'] == "test-image:latest"
    assert call_kwargs['detach'] is True
    assert call_kwargs['network_mode'] == 'host'
    assert call_kwargs['platform'] == IMAGE_PLATFORM
    assert call_kwargs['user'] == f"{os.getuid()}:{os.getgid()}"
    assert call_kwargs['name'] == manager.container_name


def test_ensure_container_macos(home_dir):
    client = make_client()

    manager = make_manager(home_dir, client=client)
    with patch('sys.platform', 'darwin'):
        manager.ensure_container([])

    call_kwargs = client.containers.run.call_args[1]
    assert 'network_mode' not in call_kwargs
    assert call_kwargs['platform'] == IMAGE_PLATFORM
    # Docker Desktop maps ownership already, so no user override.
    assert 'user' not in call_kwargs


def test_ensure_container_reuses_running_container(home_dir):
    """A matching container is reused rather than recreated."""
    client = make_client()
    existing = MagicMock(status="running")
    existing.image.id = "sha256:image"
    client.containers.get.side_effect = None
    client.containers.get.return_value = existing

    manager = make_manager(home_dir, client=client)
    manager.ensure_container([])

    client.containers.run.assert_not_called()
    existing.start.assert_not_called()
    assert manager.container is existing


def test_ensure_container_starts_stopped_container(home_dir):
    client = make_client()
    existing = MagicMock(status="exited")
    existing.image.id = "sha256:image"
    client.containers.get.side_effect = None
    client.containers.get.return_value = existing

    manager = make_manager(home_dir, client=client)
    manager.ensure_container([])

    existing.start.assert_called_once()
    client.containers.run.assert_not_called()


def test_ensure_container_replaces_container_running_a_stale_image(home_dir):
    """Re-pulling a moving tag takes effect instead of reusing the old image."""
    client = make_client(image_id="sha256:new")
    existing = MagicMock(status="running")
    existing.image.id = "sha256:old"
    client.containers.get.side_effect = None
    client.containers.get.return_value = existing

    manager = make_manager(home_dir, client=client)
    manager.ensure_container([])

    existing.remove.assert_called_once_with(force=True)
    client.containers.run.assert_called_once()


def test_container_name_identifies_the_configuration(home_dir, tmp_path):
    """Same configuration shares a container; different mounts do not."""
    extra = tmp_path / "data"
    extra.mkdir()

    def name_for(writable_paths):
        manager = make_manager(home_dir, client=make_client())
        manager.ensure_container(writable_paths)
        return manager.container_name

    plain = name_for([])

    assert plain.startswith(f"{CONTAINER_PREFIX}-")
    assert plain == name_for([])
    assert plain != name_for([str(extra)])


def test_ensure_container_image_not_found_is_user_facing(home_dir):
    client = make_client()
    client.containers.run.side_effect = docker.errors.ImageNotFound("nope")

    manager = make_manager(home_dir, client=client)
    with pytest.raises(TigError, match="Image not found: test-image:latest"):
        manager.ensure_container([])


def test_ensure_container_adopts_container_created_concurrently(home_dir):
    """Two tig commands starting at once must not collide on the name."""
    client = make_client()
    winner = MagicMock(status="running")
    winner.image.id = "sha256:image"
    conflict = docker.errors.APIError("Conflict", response=MagicMock(status_code=409))
    client.containers.run.side_effect = conflict

    def get(name):
        # Absent on the first look, present once the other process created it.
        client.containers.get.side_effect = None
        client.containers.get.return_value = winner
        raise docker.errors.NotFound("absent")

    client.containers.get.side_effect = get

    manager = make_manager(home_dir, client=client)
    manager.ensure_container([])

    assert manager.container is winner


def test_ensure_container_tolerates_concurrent_removal(home_dir):
    """A container removed by another process mid-replacement is not fatal."""
    client = make_client(image_id="sha256:new")
    existing = MagicMock(status="running")
    existing.image.id = "sha256:old"
    existing.remove.side_effect = docker.errors.NotFound("gone")
    client.containers.get.side_effect = None
    client.containers.get.return_value = existing

    manager = make_manager(home_dir, client=client)
    manager.ensure_container([])

    client.containers.run.assert_called_once()


def test_ensure_container_api_error_is_user_facing(home_dir):
    client = make_client()
    client.containers.run.side_effect = docker.errors.APIError("denied")

    manager = make_manager(home_dir, client=client)
    with pytest.raises(TigError, match="Failed to start container"):
        manager.ensure_container([])


# --- shutdown / status ---

def test_shutdown_removes_every_tig_container(home_dir):
    client = MagicMock()
    first, second = MagicMock(), MagicMock()
    client.containers.list.return_value = [first, second]

    manager = make_manager(home_dir, client=client)
    removed = manager.shutdown()

    assert removed == 2
    assert client.containers.list.call_args[1]["filters"] == {
        "name": CONTAINER_PREFIX
    }
    first.remove.assert_called_once_with(force=True)
    second.remove.assert_called_once_with(force=True)


def test_shutdown_with_no_containers(home_dir):
    client = MagicMock()
    client.containers.list.return_value = []

    assert make_manager(home_dir, client=client).shutdown() == 0


def test_status_reports_containers(home_dir):
    client = MagicMock()
    container = MagicMock(status="running")
    container.name = f"{CONTAINER_PREFIX}-abc123"
    container.image.tags = ["test-image:latest"]
    client.containers.list.return_value = [container]

    assert make_manager(home_dir, client=client).status() == [{
        "name": f"{CONTAINER_PREFIX}-abc123",
        "status": "running",
        "image": "test-image:latest",
    }]


# --- execute_vicar_command ---

@patch('tig_cli.container.subprocess.Popen')
def test_execute_vicar_command(mock_popen, home_dir):
    mock_popen.return_value = Mock(wait=Mock(return_value=0))

    manager = make_manager(home_dir)
    with patch('os.getcwd', return_value=f"{home_dir}/projects"):
        exit_code = manager.execute_vicar_command(
            "marsmap", ["input.vic", "output.vic"]
        )

    assert exit_code == 0
    call_args = mock_popen.call_args[0][0]
    assert call_args[0] == "docker"
    assert call_args[1] == "exec"
    assert "-i" in call_args
    assert "marsmap" in call_args
    assert "input.vic" in call_args
    assert "output.vic" in call_args


@patch('tig_cli.container.subprocess.Popen')
def test_execute_vicar_command_passes_display(mock_popen, home_dir):
    """DISPLAY is passed per exec, so it is not part of container identity."""
    mock_popen.return_value = Mock(wait=Mock(return_value=0))

    with patch.dict(os.environ, {"HOME": home_dir, "DISPLAY": ":7"}):
        manager = make_manager(home_dir)
        with patch('sys.platform', 'linux'):
            manager.execute_vicar_command("xvd", [])

    assert "DISPLAY=:7" in mock_popen.call_args[0][0]


@patch('tig_cli.container.subprocess.Popen')
def test_execute_vicar_command_allocates_tty_when_interactive(mock_popen, home_dir):
    mock_popen.return_value = Mock(wait=Mock(return_value=0))

    manager = make_manager(home_dir)
    with patch('sys.stdin.isatty', return_value=True), \
         patch('sys.stdout.isatty', return_value=True):
        manager.execute_vicar_command("tae", [])

    assert "-t" in mock_popen.call_args[0][0]


@patch('tig_cli.container.subprocess.Popen')
def test_execute_vicar_command_no_tty_when_piped(mock_popen, home_dir):
    """A TTY would merge stderr into stdout and mangle redirected output."""
    mock_popen.return_value = Mock(wait=Mock(return_value=0))

    manager = make_manager(home_dir)
    with patch('sys.stdin.isatty', return_value=False), \
         patch('sys.stdout.isatty', return_value=True):
        manager.execute_vicar_command("list", [])

    assert "-t" not in mock_popen.call_args[0][0]


@patch('tig_cli.container.subprocess.Popen')
def test_execute_vicar_command_with_path_translation(mock_popen, home_dir):
    mock_popen.return_value = Mock(wait=Mock(return_value=0))

    manager = make_manager(home_dir)
    with patch('os.getcwd', return_value=f"{home_dir}/projects"):
        manager.execute_vicar_command(
            "marsmap",
            ["/data/input.vic", f"{home_dir}/output.vic"]
        )

    call_args = mock_popen.call_args[0][0]
    assert "/host/data/input.vic" in call_args
    assert f"{home_dir}/output.vic" in call_args


@patch('tig_cli.container.subprocess.Popen')
def test_execute_vicar_command_translates_keyword_args(mock_popen, home_dir):
    mock_popen.return_value = Mock(wait=Mock(return_value=0))

    manager = make_manager(home_dir)
    with patch('os.getcwd', return_value=f"{home_dir}/projects"):
        manager.execute_vicar_command(
            "marsmap", ["INP=/data/input.vic", "SIZE=(1,1,500,500)"]
        )

    call_args = mock_popen.call_args[0][0]
    assert "INP=/host/data/input.vic" in call_args
    assert "SIZE=(1,1,500,500)" in call_args


@patch('tig_cli.container.subprocess.Popen')
def test_execute_vicar_command_without_translation(mock_popen, home_dir):
    mock_popen.return_value = Mock(wait=Mock(return_value=0))

    manager = make_manager(home_dir, disable_path_translation=True)
    with patch('os.getcwd', return_value=f"{home_dir}/projects"):
        manager.execute_vicar_command("marsmap", ["/data/input.vic"])

    call_args = mock_popen.call_args[0][0]
    assert "/data/input.vic" in call_args
    assert "/host/data/input.vic" not in call_args


# --- signal_command ---

def test_signal_command_forwards_to_the_running_client(home_dir):
    manager = make_manager(home_dir)
    command = MagicMock()
    manager.command = command

    manager.signal_command(15)

    command.send_signal.assert_called_once_with(15)
    # Waiting here would deadlock against the wait() the main flow is in.
    command.wait.assert_not_called()


def test_signal_command_tolerates_an_exited_client(home_dir):
    manager = make_manager(home_dir)
    manager.command = MagicMock(
        send_signal=MagicMock(side_effect=ProcessLookupError)
    )

    manager.signal_command(15)  # should not raise


def test_signal_command_without_running_command(home_dir):
    make_manager(home_dir).signal_command(15)  # should not raise
