"""Tests for CLI entry point."""
import os
import sys
import pytest
from unittest.mock import patch, MagicMock
from click.testing import CliRunner
from tig_cli.cli import main
from tig_cli.container import DEFAULT_IMAGE


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


def test_config_file_provides_image_and_paths(tmp_path, monkeypatch):
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
        "ghcr.io/my-org/vicar:cfg", disable_path_translation=True
    )
    mock_manager.start_container.assert_called_once_with(writable_paths=["/data"])


def test_env_var_overrides_config_file(tmp_path, monkeypatch):
    """CONTAINER_IMAGE and TIG_WRITABLE_PATHS win over the config file."""
    config = tmp_path / "tig.toml"
    config.write_text('image = "cfg:1"\nwritable_paths = ["/data"]\n')
    monkeypatch.setenv("TIG_CONFIG", str(config))
    monkeypatch.setenv("CONTAINER_IMAGE", "env:2")
    monkeypatch.setenv("TIG_WRITABLE_PATHS", "/scratch")

    runner = CliRunner()
    mock_manager = MagicMock()
    mock_manager.execute_vicar_command.return_value = 0

    with patch('tig_cli.cli.ContainerManager') as mock_cls:
        mock_cls.return_value = mock_manager
        runner.invoke(main, ["marsmap"])

    mock_cls.assert_called_once_with("env:2", disable_path_translation=False)
    mock_manager.start_container.assert_called_once_with(writable_paths=["/scratch"])


def test_cli_flag_overrides_config_writable_paths(tmp_path, monkeypatch):
    """--writable-path replaces configured writable paths."""
    config = tmp_path / "tig.toml"
    config.write_text('writable_paths = ["/data"]\n')
    monkeypatch.setenv("TIG_CONFIG", str(config))

    runner = CliRunner()
    mock_manager = MagicMock()
    mock_manager.execute_vicar_command.return_value = 0

    with patch('tig_cli.cli.ContainerManager', return_value=mock_manager):
        runner.invoke(main, ["--writable-path", "/flag", "marsmap"])

    mock_manager.start_container.assert_called_once_with(writable_paths=["/flag"])


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

    mock_cls.assert_called_once_with("explicit:9", disable_path_translation=False)


def test_invalid_config_reports_error(tmp_path, monkeypatch):
    """A malformed config file produces a clean error, not a traceback."""
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
