"""Tests for container management."""
import os
import subprocess
from datetime import datetime, timezone
import pytest
from unittest.mock import Mock, patch, MagicMock
import docker
from tig_cli.config import Config
from tig_cli.container import (
    CALIBRATION_MOUNT,
    CONTAINER_PREFIX,
    ContainerManager,
    IMAGE_PLATFORM,
    MAX_KEPT_CONTAINERS,
    TigError,
    ensure_x11_ready,
    get_calibration_path,
    get_container_image,
    selinux_enforcing,
    DEFAULT_IMAGE,
)

OLD_TIMESTAMP = "2020-01-01T00:00:00.123456789Z"


@pytest.fixture
def home_dir(tmp_path):
    return str(tmp_path / "home" / "user")


def make_manager(home, image="test-image:latest", client=None, **kwargs):
    """Build a ContainerManager with Docker mocked out."""
    # which() is stubbed too: Docker is mocked, and the CLI is absent from the
    # macOS runners.
    with patch('tig_cli.container.docker.from_env',
               return_value=client or MagicMock()), \
         patch('tig_cli.container.shutil.which',
               side_effect=lambda name: "/usr/bin/docker" if name == "docker" else None), \
         patch.dict(os.environ, {"HOME": home}):
        return ContainerManager(image, **kwargs)


def make_client(image_id="sha256:image", containers=()):
    """Docker client mock with no pre-existing container of our own name."""
    client = MagicMock()
    client.containers.get.side_effect = docker.errors.NotFound("absent")
    client.images.get.return_value = MagicMock(id=image_id)
    client.containers.list.return_value = list(containers)
    return client


def make_container(
    name,
    started_at=OLD_TIMESTAMP,
    exec_ids=None,
    image_id="sha256:image",
    status="running",
    binds=None,
):
    """Mock of an existing container, as Docker reports one after inspect."""
    container = MagicMock(status=status)
    container.name = name
    container.image.id = image_id
    container.attrs = {
        "State": {"StartedAt": started_at},
        "ExecIDs": exec_ids,
        "HostConfig": {"Binds": binds or []},
    }
    return container


def utc_now_stamp():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


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
         patch('tig_cli.container.shutil.which', return_value="/usr/bin/docker"), \
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


def test_ensure_container_reuse_failure_is_user_facing(home_dir):
    """A container that cannot be started is an error, not a traceback."""
    client = make_client()
    existing = MagicMock(status="exited")
    existing.image.id = "sha256:image"
    existing.start.side_effect = docker.errors.APIError("denied")
    client.containers.get.side_effect = None
    client.containers.get.return_value = existing

    manager = make_manager(home_dir, client=client)
    with pytest.raises(TigError, match="Failed to reuse container"):
        manager.ensure_container([])


def test_ensure_container_replacement_failure_is_user_facing(home_dir):
    """So is a container that cannot be replaced."""
    client = make_client(image_id="sha256:new")
    existing = MagicMock(status="running")
    existing.image.id = "sha256:old"
    existing.remove.side_effect = docker.errors.APIError("in use")
    client.containers.get.side_effect = None
    client.containers.get.return_value = existing

    manager = make_manager(home_dir, client=client)
    with pytest.raises(TigError, match="Failed to replace container"):
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
    container = make_container(f"{CONTAINER_PREFIX}-abc123")
    container.image.tags = ["test-image:latest"]
    client.containers.list.return_value = [container]

    assert make_manager(home_dir, client=client).status() == [{
        "name": f"{CONTAINER_PREFIX}-abc123",
        "status": "running",
        "image": "test-image:latest",
        "writable": "",
    }]


def test_status_reports_writable_mounts(home_dir):
    """The writable mounts are what distinguishes one container from another."""
    client = MagicMock()
    container = make_container(
        f"{CONTAINER_PREFIX}-abc123",
        binds=[
            "/:/host:ro",
            "/home/user:/home/user:rw",
            "/scenes:/host/scenes:rw",
            "/tmp/.X11-unix:/tmp/.X11-unix:rw",
        ],
    )
    container.image.tags = ["test-image:latest"]
    client.containers.list.return_value = [container]

    status = make_manager(home_dir, client=client).status()

    assert status[0]["writable"] == "/home/user, /scenes"


# --- reaping surplus containers ---

def test_creating_a_container_reaps_surplus_idle_ones(home_dir):
    """Working in several directories must not leave a pile of containers."""
    older = make_container(f"{CONTAINER_PREFIX}-old", started_at=OLD_TIMESTAMP)
    newer = make_container(
        f"{CONTAINER_PREFIX}-new", started_at="2021-01-01T00:00:00Z"
    )
    client = make_client(containers=[older, newer])

    manager = make_manager(home_dir, client=client)
    manager.ensure_container([])

    # One kept alongside the container just created.
    assert MAX_KEPT_CONTAINERS == 2
    older.remove.assert_called_once_with(force=True)
    newer.remove.assert_not_called()


def test_reaping_never_touches_the_container_in_use(home_dir):
    client = make_client()
    manager = make_manager(home_dir, client=client)
    manager.ensure_container([])

    mine = make_container(manager.container_name)
    others = [make_container(f"{CONTAINER_PREFIX}-{i}") for i in range(3)]
    client.containers.list.return_value = [mine, *others]

    manager._reap_containers(keep=manager.container_name)

    mine.remove.assert_not_called()


def test_reaping_skips_a_container_running_a_command_started_outside_tig(home_dir):
    """A hand-run 'docker exec' also keeps a container alive."""
    busy = make_container(f"{CONTAINER_PREFIX}-busy", exec_ids=["exec1"])
    idle = make_container(f"{CONTAINER_PREFIX}-idle")
    client = make_client(containers=[busy, idle])
    client.api.exec_inspect.return_value = {"Running": True}

    manager = make_manager(home_dir, client=client)
    manager.ensure_container([])

    busy.remove.assert_not_called()


def test_reaping_skips_a_container_when_docker_cannot_say(home_dir):
    """An unanswerable liveness check is treated as in use."""
    unknown = make_container(f"{CONTAINER_PREFIX}-unknown", exec_ids=["exec1"])
    idle = make_container(f"{CONTAINER_PREFIX}-idle")
    client = make_client(containers=[unknown, idle])
    client.api.exec_inspect.side_effect = docker.errors.APIError("boom")

    manager = make_manager(home_dir, client=client)
    manager.ensure_container([])

    unknown.remove.assert_not_called()


def test_reaping_skips_a_container_claimed_by_a_live_process(home_dir):
    """A container another live invocation claimed is not reaped, even idle.

    The claim is what covers the window between another process creating its
    container and its first 'docker exec' appearing.
    """
    newest = make_container(f"{CONTAINER_PREFIX}-newest", "2022-01-01T00:00:00Z")
    claimed = make_container(f"{CONTAINER_PREFIX}-claimed", "2021-01-01T00:00:00Z")
    oldest = make_container(f"{CONTAINER_PREFIX}-oldest", "2020-01-01T00:00:00Z")
    client = make_client(containers=[newest, claimed, oldest])

    manager = make_manager(home_dir, client=client)
    claim_dir = manager._claim_dir()
    claim_dir.mkdir(parents=True, exist_ok=True)
    (claim_dir / f"{claimed.name}.{os.getpid()}").touch()

    manager.ensure_container([])

    claimed.remove.assert_not_called()
    oldest.remove.assert_called_once_with(force=True)


def test_reaping_ignores_and_prunes_a_claim_from_a_dead_process(home_dir):
    newest = make_container(f"{CONTAINER_PREFIX}-newest", "2022-01-01T00:00:00Z")
    stale = make_container(f"{CONTAINER_PREFIX}-stale", "2021-01-01T00:00:00Z")
    client = make_client(containers=[newest, stale])

    manager = make_manager(home_dir, client=client)
    claim_dir = manager._claim_dir()
    claim_dir.mkdir(parents=True, exist_ok=True)
    claim = claim_dir / f"{stale.name}.999999"
    claim.touch()

    with patch('tig_cli.container.os.kill', side_effect=ProcessLookupError):
        manager.ensure_container([])

    assert not claim.exists()
    stale.remove.assert_called_once_with(force=True)


def test_a_container_is_claimed_before_it_is_created(home_dir):
    """Claiming first is what closes the create-then-exec race."""
    client = make_client()
    manager = make_manager(home_dir, client=client)

    def creating(*args, **kwargs):
        assert manager.claim.exists()
        return MagicMock()

    client.containers.run.side_effect = creating
    manager.ensure_container([])

    assert manager.claim.name.endswith(f".{os.getpid()}")
    assert manager.claim.name.startswith(manager.container_name)


def test_releasing_the_claim_makes_the_container_reapable(home_dir):
    client = make_client()
    manager = make_manager(home_dir, client=client)
    manager.ensure_container([])
    claim = manager.claim

    manager.release_claim()

    assert not claim.exists()
    assert manager.claim is None
    manager.release_claim()  # idempotent


def test_reaping_tolerates_a_container_removed_concurrently(home_dir):
    first = make_container(f"{CONTAINER_PREFIX}-a")
    first.remove.side_effect = docker.errors.NotFound("gone")
    second = make_container(f"{CONTAINER_PREFIX}-b")
    client = make_client(containers=[first, second])

    manager = make_manager(home_dir, client=client)
    manager.ensure_container([])  # should not raise


def test_reusing_a_container_does_not_reap(home_dir):
    """The warm path stays a single lookup, so latency is unchanged."""
    client = make_client()
    existing = make_container(f"{CONTAINER_PREFIX}-x")
    client.containers.get.side_effect = None
    client.containers.get.return_value = existing

    manager = make_manager(home_dir, client=client)
    manager.ensure_container([])

    client.containers.list.assert_not_called()


# --- SELinux ---

def test_selinux_enforcing_reads_getenforce():
    with patch('sys.platform', 'linux'), \
         patch('tig_cli.container.shutil.which', return_value="/usr/sbin/getenforce"), \
         patch('tig_cli.container.subprocess.run',
               return_value=subprocess.CompletedProcess([], 0, "Enforcing\n", "")):
        assert selinux_enforcing() is True


def test_selinux_permissive_is_not_enforcing():
    with patch('sys.platform', 'linux'), \
         patch('tig_cli.container.shutil.which', return_value="/usr/sbin/getenforce"), \
         patch('tig_cli.container.subprocess.run',
               return_value=subprocess.CompletedProcess([], 0, "Permissive\n", "")):
        assert selinux_enforcing() is False


def test_selinux_falls_back_to_the_kernel_state(tmp_path):
    enforce = tmp_path / "enforce"
    enforce.write_text("1\n")

    with patch('sys.platform', 'linux'), \
         patch('tig_cli.container.shutil.which', return_value=None), \
         patch('tig_cli.container.Path', return_value=enforce):
        assert selinux_enforcing() is True


def test_selinux_absent_when_there_is_no_selinux():
    with patch('sys.platform', 'linux'), \
         patch('tig_cli.container.shutil.which', return_value=None):
        assert selinux_enforcing() is False


def test_selinux_not_checked_off_linux():
    with patch('sys.platform', 'darwin'), \
         patch('tig_cli.container.shutil.which') as which:
        assert selinux_enforcing() is False
        which.assert_not_called()


def test_enforcing_host_gets_label_disable(home_dir):
    client = make_client()

    with patch('tig_cli.container.selinux_enforcing', return_value=True):
        manager = make_manager(home_dir, client=client)
    with patch('sys.platform', 'linux'):
        manager.ensure_container([])

    assert client.containers.run.call_args[1]["security_opt"] == ["label=disable"]


def test_non_enforcing_host_keeps_standard_labeling(home_dir):
    client = make_client()

    with patch('tig_cli.container.selinux_enforcing', return_value=False):
        manager = make_manager(home_dir, client=client)
    with patch('sys.platform', 'linux'):
        manager.ensure_container([])

    assert "security_opt" not in client.containers.run.call_args[1]


def test_label_disable_can_be_forced_on(home_dir):
    client = make_client()

    with patch('tig_cli.container.selinux_enforcing', return_value=False):
        manager = make_manager(home_dir, client=client, selinux_label_disable=True)
    with patch('sys.platform', 'linux'):
        manager.ensure_container([])

    assert client.containers.run.call_args[1]["security_opt"] == ["label=disable"]


def test_label_disable_can_be_forced_off_on_an_enforcing_host(home_dir, capsys):
    client = make_client()

    with patch('tig_cli.container.selinux_enforcing', return_value=True):
        manager = make_manager(home_dir, client=client, selinux_label_disable=False)
    with patch('sys.platform', 'linux'):
        manager.ensure_container([])

    assert "security_opt" not in client.containers.run.call_args[1]
    assert "SELinux is Enforcing" in capsys.readouterr().err


def test_label_disable_is_not_used_on_macos(home_dir):
    client = make_client()

    with patch('tig_cli.container.selinux_enforcing', return_value=True):
        manager = make_manager(home_dir, client=client, selinux_label_disable=True)
    with patch('sys.platform', 'darwin'):
        manager.ensure_container([])

    assert "security_opt" not in client.containers.run.call_args[1]


def test_host_root_mount_is_never_relabeled(home_dir, tmp_path):
    """Relabeling (:z/:Z) the host root filesystem would be unrecoverable."""
    calib = tmp_path / "mars_calibration_m20"
    calib.mkdir()

    with patch('tig_cli.container.selinux_enforcing', return_value=True):
        manager = make_manager(home_dir, calibration_path=str(calib))
    volumes = manager._build_volume_mounts([])

    assert all(
        "z" not in mount["mode"].lower() for mount in volumes.values()
    )


# --- X11 host setup ---

def test_x11_setup_authorizes_local_connections_on_linux(monkeypatch):
    monkeypatch.setenv("DISPLAY", ":0")

    with patch('sys.platform', 'linux'), \
         patch('tig_cli.container.shutil.which', return_value="/usr/bin/xhost"), \
         patch('tig_cli.container.subprocess.run') as run:
        ensure_x11_ready()

    # The broad form: label=disable makes the container connect as LOCAL:.
    assert run.call_args[0][0] == ["xhost", "+local:"]


def test_x11_setup_skipped_without_a_display(monkeypatch):
    monkeypatch.delenv("DISPLAY", raising=False)

    with patch('tig_cli.container.subprocess.run') as run:
        ensure_x11_ready()

    run.assert_not_called()


def test_x11_setup_skipped_without_xhost(monkeypatch):
    monkeypatch.setenv("DISPLAY", ":0")

    with patch('tig_cli.container.shutil.which', return_value=None), \
         patch('tig_cli.container.subprocess.run') as run:
        ensure_x11_ready()

    run.assert_not_called()


def test_x11_setup_failure_is_not_fatal(monkeypatch):
    monkeypatch.setenv("DISPLAY", ":0")

    with patch('sys.platform', 'linux'), \
         patch('tig_cli.container.shutil.which', return_value="/usr/bin/xhost"), \
         patch('tig_cli.container.subprocess.run', side_effect=OSError):
        ensure_x11_ready()  # should not raise


def test_x11_setup_prepares_xquartz_on_macos(monkeypatch):
    monkeypatch.setenv("DISPLAY", "/private/tmp/com.apple.launchd.x/org.xquartz:0")

    with patch('sys.platform', 'darwin'), \
         patch('tig_cli.container.shutil.which', return_value="/opt/X11/bin/xhost"), \
         patch('tig_cli.container.subprocess.run') as run:
        run.return_value = subprocess.CompletedProcess([], 0)
        ensure_x11_ready()

    commands = [call[0][0] for call in run.call_args_list]
    assert ["defaults", "write", "org.xquartz.X11",
            "nolisten_tcp", "-bool", "false"] in commands
    assert ["xhost", "+localhost"] in commands


def test_x11_setup_runs_when_a_container_is_created(home_dir):
    client = make_client()

    manager = make_manager(home_dir, client=client)
    with patch('tig_cli.container.ensure_x11_ready') as ready:
        manager.ensure_container([])

    ready.assert_called_once()


def test_x11_setup_skipped_when_a_container_is_reused(home_dir):
    """Authorizing the display is container setup, not per-command work."""
    client = make_client()
    existing = make_container(f"{CONTAINER_PREFIX}-x")
    client.containers.get.side_effect = None
    client.containers.get.return_value = existing

    manager = make_manager(home_dir, client=client)
    with patch('tig_cli.container.ensure_x11_ready') as ready:
        manager.ensure_container([])

    ready.assert_not_called()


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
def test_execute_vicar_command_leaves_x_resource_paths_to_the_image(
    mock_popen, home_dir
):
    """The image's own xvd wrapper exports these; ours pointed at nothing."""
    mock_popen.return_value = Mock(wait=Mock(return_value=0))

    make_manager(home_dir).execute_vicar_command("xvd", [])

    call_args = mock_popen.call_args[0][0]
    assert not any("XFILESEARCHPATH" in arg for arg in call_args)
    assert not any("XBMLANGPATH" in arg for arg in call_args)


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

    with patch("tig_cli.container.subprocess.run"):
        manager.signal_command(15)

    command.send_signal.assert_called_once_with(15)
    # Waiting here would deadlock against the wait() the main flow is in.
    command.wait.assert_not_called()


def test_signal_command_kills_the_processes_in_the_container(home_dir):
    """Docker does not proxy signals to an exec, so tig has to do it."""
    manager = make_manager(home_dir)
    manager.command = MagicMock()
    manager.container_name = "tig-vicar-abc"
    manager.exec_id = "deadbeef"

    with patch("tig_cli.container.subprocess.run") as run:
        manager.signal_command(15)

    command = run.call_args[0][0]
    assert command[:3] == ["docker", "exec", "tig-vicar-abc"]
    assert "TIG_EXEC_ID=deadbeef" in command[-1]
    assert "kill -15" in command[-1]


def test_signal_command_tolerates_a_failing_kill(home_dir):
    manager = make_manager(home_dir)
    manager.command = MagicMock()
    manager.exec_id = "deadbeef"

    with patch("tig_cli.container.subprocess.run", side_effect=OSError):
        manager.signal_command(15)  # should not raise

    manager.command.send_signal.assert_called_once_with(15)


def test_signal_command_tolerates_an_exited_client(home_dir):
    manager = make_manager(home_dir)
    manager.command = MagicMock(
        send_signal=MagicMock(side_effect=ProcessLookupError)
    )

    with patch("tig_cli.container.subprocess.run"):
        manager.signal_command(15)  # should not raise


def test_signal_command_without_running_command(home_dir):
    make_manager(home_dir).signal_command(15)  # should not raise

# --- list_tools ---

def test_list_tools_returns_sorted_unique_names(home_dir):
    manager = make_manager(home_dir)
    manager.container = MagicMock(
        exec_run=MagicMock(
            return_value=Mock(exit_code=0, output=b"marsmesh\nmarsmap\nmarsmap\n")
        )
    )

    assert manager.list_tools() == ["marsmap", "marsmesh"]


def test_list_tools_asks_only_for_executables(home_dir):
    """The tool directories also hold payloads such as vicario.jar."""
    manager = make_manager(home_dir)
    manager.container = MagicMock(
        exec_run=MagicMock(return_value=Mock(exit_code=0, output=b"marsmap\n"))
    )

    assert manager.list_tools() == ["marsmap"]
    command = manager.container.exec_run.call_args[0][0]
    assert command[0] == "find"
    assert "-executable" in command


def test_list_tools_without_a_container(home_dir):
    with pytest.raises(TigError):
        make_manager(home_dir).list_tools()


def test_list_tools_reports_a_failed_listing(home_dir):
    """A failing 'ls' must not turn its stderr into a tool name."""
    manager = make_manager(home_dir)
    manager.container = MagicMock(
        exec_run=MagicMock(
            return_value=Mock(
                exit_code=2,
                output=b"ls: cannot access '/usr/local/bin': No such file\n",
            )
        )
    )

    with pytest.raises(TigError, match="cannot access"):
        manager.list_tools()


def test_list_tools_reports_a_docker_failure(home_dir):
    manager = make_manager(home_dir)
    manager.container = MagicMock(
        exec_run=MagicMock(side_effect=docker.errors.APIError("boom"))
    )

    with pytest.raises(TigError):
        manager.list_tools()
