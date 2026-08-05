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

    mock_manager.start_container.assert_called_once_with(writable_paths=[])
    mock_manager.execute_vicar_command.assert_called_once_with(
        "marsmap", ["input.vic", "output.vic"]
    )
    mock_manager.stop_container.assert_called_once()


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

    mock_manager.start_container.assert_called_once_with(
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

    mock_cls.assert_called_once_with(DEFAULT_IMAGE, disable_path_translation=True)


def test_stop_container_called_on_success():
    """stop_container called even when command succeeds."""
    runner = CliRunner()
    mock_manager = MagicMock()
    mock_manager.execute_vicar_command.return_value = 0

    with patch('tig_cli.cli.ContainerManager', return_value=mock_manager), \
         patch('tig_cli.cli.get_container_image', return_value=DEFAULT_IMAGE):
        runner.invoke(main, ["marsmap"])

    mock_manager.stop_container.assert_called_once()


def test_stop_container_called_on_error():
    """stop_container called even when command raises an exception."""
    runner = CliRunner()
    mock_manager = MagicMock()
    mock_manager.execute_vicar_command.side_effect = RuntimeError("boom")

    with patch('tig_cli.cli.ContainerManager', return_value=mock_manager), \
         patch('tig_cli.cli.get_container_image', return_value=DEFAULT_IMAGE):
        result = runner.invoke(main, ["marsmap"])

    mock_manager.stop_container.assert_called_once()


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

    mock_cls.assert_called_once_with(custom, disable_path_translation=False)


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
    mock_manager.start_container.side_effect = TigError("Image not found: x:y")

    with patch('tig_cli.cli.ContainerManager', return_value=mock_manager), \
         patch('tig_cli.cli.get_container_image', return_value=DEFAULT_IMAGE):
        result = runner.invoke(main, ["marsmap"])

    assert result.exit_code == 1
    assert "Image not found: x:y" in result.output
    mock_manager.stop_container.assert_called_once()


def test_signal_handlers_stop_container_and_are_restored():
    """SIGTERM tears the container down instead of leaking it."""
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

    mock_manager.stop_container.reset_mock()
    with pytest.raises(SystemExit) as exc:
        handler(signal.SIGTERM, None)
    assert exc.value.code == 128 + signal.SIGTERM
    mock_manager.stop_container.assert_called_once()
