"""Tests for CLI entry point."""
import os
import signal
import sys
import pytest
from unittest.mock import patch, MagicMock
from click.testing import CliRunner
from tig_cli.cli import main
from tig_cli.container import DEFAULT_IMAGE, TigError


def test_help_text_shows_image():
    """Help text shows the active container image."""
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert DEFAULT_IMAGE in result.output


def test_help_text_shows_env_var_hint():
    """Help text mentions CONTAINER_IMAGE."""
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert "CONTAINER_IMAGE" in result.output


def test_custom_image_in_help():
    """When CONTAINER_IMAGE is set, help text shows that image."""
    custom = "ghcr.io/my-org/custom-vicar:v2"
    runner = CliRunner()
    with patch.dict(os.environ, {"CONTAINER_IMAGE": custom}):
        result = runner.invoke(main, ["--help"])
    assert custom in result.output


def test_basic_command_execution():
    """CLI invokes ContainerManager with correct arguments."""
    runner = CliRunner()
    mock_manager = MagicMock()
    mock_manager.execute_vicar_command.return_value = 0

    with patch('tig_cli.cli.ContainerManager', return_value=mock_manager), \
         patch('tig_cli.cli.get_container_image', return_value=DEFAULT_IMAGE):
        result = runner.invoke(main, ["marsmap", "input.vic", "output.vic"])

    mock_manager.ensure_container.assert_called_once_with(writable_paths=[])
    mock_manager.execute_vicar_command.assert_called_once_with(
        "marsmap", ["input.vic", "output.vic"]
    )


def test_writable_path_option():
    """--writable-path passed through to ContainerManager."""
    runner = CliRunner()
    mock_manager = MagicMock()
    mock_manager.execute_vicar_command.return_value = 0

    with patch('tig_cli.cli.ContainerManager', return_value=mock_manager), \
         patch('tig_cli.cli.get_container_image', return_value=DEFAULT_IMAGE):
        result = runner.invoke(main, [
            "--writable-path", "/data",
            "--writable-path", "/scratch",
            "marsmap"
        ])

    mock_manager.ensure_container.assert_called_once_with(
        writable_paths=["/data", "/scratch"]
    )


def test_disable_path_translation_option():
    """--disable-path-translation passed to ContainerManager."""
    runner = CliRunner()
    mock_manager = MagicMock()
    mock_manager.execute_vicar_command.return_value = 0

    with patch('tig_cli.cli.ContainerManager') as mock_cls, \
         patch('tig_cli.cli.get_container_image', return_value=DEFAULT_IMAGE):
        mock_cls.return_value = mock_manager
        result = runner.invoke(main, ["--disable-path-translation", "marsmap"])

    mock_cls.assert_called_once_with(
        DEFAULT_IMAGE, disable_path_translation=True, calibration_path=None
    )


def test_container_is_left_running_for_reuse():
    """The container outlives the command so the next one starts instantly."""
    runner = CliRunner()
    mock_manager = MagicMock()
    mock_manager.execute_vicar_command.return_value = 0

    with patch('tig_cli.cli.ContainerManager', return_value=mock_manager), \
         patch('tig_cli.cli.get_container_image', return_value=DEFAULT_IMAGE):
        runner.invoke(main, ["marsmap"])

    mock_manager.shutdown.assert_not_called()


def test_shutdown_option_removes_containers():
    runner = CliRunner()
    mock_manager = MagicMock()
    mock_manager.shutdown.return_value = 2

    with patch('tig_cli.cli.ContainerManager', return_value=mock_manager), \
         patch('tig_cli.cli.get_container_image', return_value=DEFAULT_IMAGE):
        result = runner.invoke(main, ["--shutdown"])

    assert result.exit_code == 0
    assert "Removed 2 container(s)." in result.output
    mock_manager.execute_vicar_command.assert_not_called()


def test_status_option_lists_containers():
    runner = CliRunner()
    mock_manager = MagicMock()
    mock_manager.status.return_value = [
        {"name": "tig-vicar-abc123", "status": "running", "image": "img:v1"}
    ]

    with patch('tig_cli.cli.ContainerManager', return_value=mock_manager), \
         patch('tig_cli.cli.get_container_image', return_value=DEFAULT_IMAGE):
        result = runner.invoke(main, ["--status"])

    assert result.exit_code == 0
    assert "tig-vicar-abc123" in result.output
    assert "running" in result.output


def test_status_option_with_no_containers():
    runner = CliRunner()
    mock_manager = MagicMock()
    mock_manager.status.return_value = []

    with patch('tig_cli.cli.ContainerManager', return_value=mock_manager), \
         patch('tig_cli.cli.get_container_image', return_value=DEFAULT_IMAGE):
        result = runner.invoke(main, ["--status"])

    assert "No tig containers running." in result.output


def test_missing_tool_name_is_a_usage_error():
    runner = CliRunner()
    mock_manager = MagicMock()

    with patch('tig_cli.cli.ContainerManager', return_value=mock_manager), \
         patch('tig_cli.cli.get_container_image', return_value=DEFAULT_IMAGE):
        result = runner.invoke(main, [])

    assert result.exit_code == 2
    assert "VICAR_TOOL" in result.output


def test_calibration_path_option_passed_through():
    runner = CliRunner()
    mock_manager = MagicMock()
    mock_manager.execute_vicar_command.return_value = 0

    with patch('tig_cli.cli.ContainerManager') as mock_cls, \
         patch('tig_cli.cli.get_container_image', return_value=DEFAULT_IMAGE):
        mock_cls.return_value = mock_manager
        runner.invoke(main, ["--calibration-path", "/calib", "marsmap"])

    assert mock_cls.call_args[1]["calibration_path"] == "/calib"


def test_calibration_path_defaults_to_env_var():
    runner = CliRunner()
    mock_manager = MagicMock()
    mock_manager.execute_vicar_command.return_value = 0

    with patch('tig_cli.cli.ContainerManager') as mock_cls, \
         patch('tig_cli.cli.get_container_image', return_value=DEFAULT_IMAGE), \
         patch.dict(os.environ, {"MARS_CONFIG_PATH": "/env/calib"}):
        mock_cls.return_value = mock_manager
        runner.invoke(main, ["marsmap"])

    assert mock_cls.call_args[1]["calibration_path"] == "/env/calib"


def test_exit_code_propagated():
    """CLI exits with the vicar tool's return code."""
    runner = CliRunner()
    mock_manager = MagicMock()
    mock_manager.execute_vicar_command.return_value = 42

    with patch('tig_cli.cli.ContainerManager', return_value=mock_manager), \
         patch('tig_cli.cli.get_container_image', return_value=DEFAULT_IMAGE):
        result = runner.invoke(main, ["marsmap"])

    assert result.exit_code == 42


def test_uses_container_image_env_var():
    """Uses CONTAINER_IMAGE env var when set."""
    custom = "ghcr.io/my-org/custom:latest"
    runner = CliRunner()
    mock_manager = MagicMock()
    mock_manager.execute_vicar_command.return_value = 0

    with patch('tig_cli.cli.ContainerManager') as mock_cls, \
         patch.dict(os.environ, {"CONTAINER_IMAGE": custom}):
        mock_cls.return_value = mock_manager
        result = runner.invoke(main, ["marsmap"])

    mock_cls.assert_called_once_with(
        custom, disable_path_translation=False, calibration_path=None
    )


def test_passes_unknown_args_to_vicar_tool():
    """Unknown args (VICAR keyword=value format) passed through."""
    runner = CliRunner()
    mock_manager = MagicMock()
    mock_manager.execute_vicar_command.return_value = 0

    with patch('tig_cli.cli.ContainerManager', return_value=mock_manager), \
         patch('tig_cli.cli.get_container_image', return_value=DEFAULT_IMAGE):
        result = runner.invoke(main, ["marsmap", "INP=input.vic", "SIZE=(1,1,500,500)"])

    mock_manager.execute_vicar_command.assert_called_once_with(
        "marsmap", ["INP=input.vic", "SIZE=(1,1,500,500)"]
    )


def test_setup_error_reported_without_traceback():
    """A Docker setup failure is a one-line message, not a stack trace."""
    runner = CliRunner()

    with patch('tig_cli.cli.ContainerManager',
               side_effect=TigError("Is the Docker daemon running?")), \
         patch('tig_cli.cli.get_container_image', return_value=DEFAULT_IMAGE):
        result = runner.invoke(main, ["marsmap"])

    assert result.exit_code == 1
    assert "Is the Docker daemon running?" in result.output
    assert "Traceback" not in result.output


def test_run_error_reported_without_traceback():
    """A failure starting the container is reported the same way."""
    runner = CliRunner()
    mock_manager = MagicMock()
    mock_manager.ensure_container.side_effect = TigError("Image not found: x:y")

    with patch('tig_cli.cli.ContainerManager', return_value=mock_manager), \
         patch('tig_cli.cli.get_container_image', return_value=DEFAULT_IMAGE):
        result = runner.invoke(main, ["marsmap"])

    assert result.exit_code == 1
    assert "Image not found: x:y" in result.output


def test_signal_handlers_forward_signal_and_are_restored():
    """A signal stops the running command; the shared container stays up."""
    runner = CliRunner()
    mock_manager = MagicMock()
    original = signal.getsignal(signal.SIGTERM)
    captured = {}

    def fake_execute(tool, args):
        captured["handler"] = signal.getsignal(signal.SIGTERM)
        return 0

    mock_manager.execute_vicar_command.side_effect = fake_execute

    with patch('tig_cli.cli.ContainerManager', return_value=mock_manager), \
         patch('tig_cli.cli.get_container_image', return_value=DEFAULT_IMAGE):
        runner.invoke(main, ["marsmap"])

    handler = captured["handler"]
    assert handler is not original
    assert signal.getsignal(signal.SIGTERM) is original

    with pytest.raises(SystemExit) as exc:
        handler(signal.SIGTERM, None)
    assert exc.value.code == 128 + signal.SIGTERM
    mock_manager.signal_command.assert_called_once_with(signal.SIGTERM)
    mock_manager.shutdown.assert_not_called()


# --- config files ---

def test_config_file_provides_settings(tmp_path, monkeypatch):
    """Config file supplies image, writable paths and path-translation flag."""
    config = tmp_path / "tig.toml"
    config.write_text(
        'image = "ghcr.io/my-org/vicar:cfg"\n'
        'writable_paths = ["/data"]\n'
        'disable_path_translation = true\n'
    )
    monkeypatch.setenv("TIG_CONFIG", str(config))
    monkeypatch.delenv("CONTAINER_IMAGE", raising=False)

    runner = CliRunner()
    mock_manager = MagicMock()
    mock_manager.execute_vicar_command.return_value = 0

    with patch('tig_cli.cli.ContainerManager') as mock_cls:
        mock_cls.return_value = mock_manager
        runner.invoke(main, ["marsmap"])

    mock_cls.assert_called_once_with(
        "ghcr.io/my-org/vicar:cfg",
        disable_path_translation=True,
        calibration_path=None,
    )
    mock_manager.ensure_container.assert_called_once_with(writable_paths=["/data"])


def test_config_file_provides_calibration_path(tmp_path, monkeypatch):
    """calibration_path from the config file reaches ContainerManager."""
    config = tmp_path / "tig.toml"
    config.write_text('calibration_path = "/opt/calib"\n')
    monkeypatch.setenv("TIG_CONFIG", str(config))

    runner = CliRunner()
    mock_manager = MagicMock()
    mock_manager.execute_vicar_command.return_value = 0

    with patch('tig_cli.cli.ContainerManager') as mock_cls:
        mock_cls.return_value = mock_manager
        runner.invoke(main, ["marsmap"])

    assert mock_cls.call_args[1]["calibration_path"] == "/opt/calib"


def test_env_vars_override_config_file(tmp_path, monkeypatch):
    """Environment variables win over the config file."""
    config = tmp_path / "tig.toml"
    config.write_text(
        'image = "cfg:1"\n'
        'writable_paths = ["/data"]\n'
        'calibration_path = "/cfg/calib"\n'
    )
    monkeypatch.setenv("TIG_CONFIG", str(config))
    monkeypatch.setenv("CONTAINER_IMAGE", "env:2")
    monkeypatch.setenv("TIG_WRITABLE_PATHS", "/scratch")
    monkeypatch.setenv("MARS_CONFIG_PATH", "/env/calib")

    runner = CliRunner()
    mock_manager = MagicMock()
    mock_manager.execute_vicar_command.return_value = 0

    with patch('tig_cli.cli.ContainerManager') as mock_cls:
        mock_cls.return_value = mock_manager
        runner.invoke(main, ["marsmap"])

    mock_cls.assert_called_once_with(
        "env:2", disable_path_translation=False, calibration_path="/env/calib"
    )
    mock_manager.ensure_container.assert_called_once_with(
        writable_paths=["/scratch"]
    )


def test_cli_flags_override_config_file(tmp_path, monkeypatch):
    """Flags win over both env vars and the config file."""
    config = tmp_path / "tig.toml"
    config.write_text('writable_paths = ["/data"]\ncalibration_path = "/cfg"\n')
    monkeypatch.setenv("TIG_CONFIG", str(config))
    monkeypatch.setenv("MARS_CONFIG_PATH", "/env/calib")

    runner = CliRunner()
    mock_manager = MagicMock()
    mock_manager.execute_vicar_command.return_value = 0

    with patch('tig_cli.cli.ContainerManager') as mock_cls:
        mock_cls.return_value = mock_manager
        runner.invoke(main, [
            "--writable-path", "/flag",
            "--calibration-path", "/flag/calib",
            "marsmap",
        ])

    assert mock_cls.call_args[1]["calibration_path"] == "/flag/calib"
    mock_manager.ensure_container.assert_called_once_with(writable_paths=["/flag"])


def test_config_option_selects_file(tmp_path, monkeypatch):
    """--config loads the given file instead of the layered files."""
    config = tmp_path / "custom.toml"
    config.write_text('image = "explicit:9"\n')
    monkeypatch.delenv("CONTAINER_IMAGE", raising=False)

    runner = CliRunner()
    mock_manager = MagicMock()
    mock_manager.execute_vicar_command.return_value = 0

    with patch('tig_cli.cli.ContainerManager') as mock_cls:
        mock_cls.return_value = mock_manager
        runner.invoke(main, ["--config", str(config), "marsmap"])

    assert mock_cls.call_args[0][0] == "explicit:9"


def test_invalid_config_reported_without_traceback(tmp_path, monkeypatch):
    """A malformed config file produces a clean error message."""
    config = tmp_path / "tig.toml"
    config.write_text('image = 5\n')
    monkeypatch.setenv("TIG_CONFIG", str(config))

    runner = CliRunner()
    with patch('tig_cli.cli.ContainerManager') as mock_cls:
        result = runner.invoke(main, ["marsmap"])

    assert result.exit_code != 0
    assert "must be a string" in result.output
    mock_cls.assert_not_called()


def test_help_lists_config_locations():
    """Help text documents where config files are read from."""
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert "tig.toml" in result.output
