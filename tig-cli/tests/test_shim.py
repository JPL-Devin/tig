"""Tests for the shim directory of per-tool commands."""
import os
import stat
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from tig_cli.cli import main
from tig_cli.container import DEFAULT_IMAGE
from tig_cli.shim import (
    DISPATCHER_NAME,
    default_shim_dir,
    tig_executable,
    write_shims,
)


def test_default_dir_follows_xdg_data_home(monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", "/xdg/data")
    assert default_shim_dir() == Path("/xdg/data/tig/shims")


def test_default_dir_without_xdg(monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert default_shim_dir() == tmp_path / ".local/share/tig/shims"


def test_writes_a_link_per_tool(tmp_path):
    written, skipped = write_shims(tmp_path, ["marsmap", "marsmesh"], "/bin/tig")

    assert written == ["marsmap", "marsmesh"]
    assert skipped == []
    for tool in written:
        link = tmp_path / tool
        assert link.is_symlink()
        assert os.readlink(link) == DISPATCHER_NAME


def test_dispatcher_is_executable_and_calls_tig(tmp_path):
    write_shims(tmp_path, ["marsmap"], "/opt/some dir/tig")
    dispatcher = tmp_path / DISPATCHER_NAME

    assert os.access(dispatcher, os.X_OK)
    # Executable for everyone, so a shared shim directory works.
    mode = dispatcher.stat().st_mode
    assert mode & stat.S_IXUSR and mode & stat.S_IXGRP and mode & stat.S_IXOTH
    assert "'/opt/some dir/tig'" in dispatcher.read_text()


def test_skips_names_already_on_path(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "tig_cli.shim.shutil.which",
        lambda name: "/usr/bin/sort" if name == "sort" else None,
    )

    written, skipped = write_shims(tmp_path, ["marsmap", "sort"], "/bin/tig")

    assert written == ["marsmap"]
    assert skipped == ["sort"]
    assert not (tmp_path / "sort").exists()


def test_force_creates_shadowing_names(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "tig_cli.shim.shutil.which",
        lambda name: "/usr/bin/sort" if name == "sort" else None,
    )

    written, skipped = write_shims(
        tmp_path, ["sort"], "/bin/tig", force=True
    )

    assert written == ["sort"]
    assert skipped == []
    assert (tmp_path / "sort").is_symlink()


def test_own_shims_do_not_count_as_host_commands(tmp_path, monkeypatch):
    """A regenerated shim directory already on PATH does not skip everything."""
    monkeypatch.setattr(
        "tig_cli.shim.shutil.which",
        lambda name: str(tmp_path / name),
    )

    written, skipped = write_shims(tmp_path, ["marsmap"], "/bin/tig")

    assert written == ["marsmap"]
    assert skipped == []


def test_removes_shims_for_tools_that_disappeared(tmp_path):
    write_shims(tmp_path, ["marsmap", "oldtool"], "/bin/tig")
    write_shims(tmp_path, ["marsmap"], "/bin/tig")

    assert (tmp_path / "marsmap").is_symlink()
    assert not (tmp_path / "oldtool").exists()


def test_regenerating_works_with_a_relative_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_shims(Path("shims"), ["marsmap", "oldtool"], "/bin/tig")
    write_shims(Path("shims"), ["marsmap"], "/bin/tig")

    assert (tmp_path / "shims/marsmap").is_symlink()
    assert not (tmp_path / "shims/oldtool").exists()


def test_skips_names_taken_by_a_foreign_symlink(tmp_path, monkeypatch):
    monkeypatch.setattr("tig_cli.shim.shutil.which", lambda name: None)
    mine = tmp_path / "marsmap"
    mine.symlink_to("/opt/tools/marsmap")

    written, skipped = write_shims(tmp_path, ["marsmap"], "/bin/tig")

    assert written == []
    assert skipped == ["marsmap"]
    assert os.readlink(mine) == "/opt/tools/marsmap"


def test_leaves_unrelated_files_alone(tmp_path):
    keep = tmp_path / "notes.txt"
    keep.write_text("mine")

    written, skipped = write_shims(tmp_path, ["notes.txt"], "/bin/tig")

    assert keep.read_text() == "mine"
    assert skipped == ["notes.txt"]
    assert written == []


def test_dispatcher_runs_the_tool_it_is_called_as(tmp_path):
    """End-to-end: invoking the symlink execs tig with the tool name."""
    fake_tig = tmp_path / "fake-tig"
    fake_tig.write_text('#!/bin/sh\necho "called: $*"\n')
    fake_tig.chmod(0o755)
    shim_dir = tmp_path / "shims"

    write_shims(shim_dir, ["marsmap"], str(fake_tig))
    result = subprocess.run(
        [str(shim_dir / "marsmap"), "INP=a.vic"],
        capture_output=True,
        text=True,
    )

    assert result.stdout.strip() == "called: marsmap INP=a.vic"


def test_dispatcher_invoked_directly_takes_the_tool_as_first_argument(tmp_path):
    fake_tig = tmp_path / "fake-tig"
    fake_tig.write_text('#!/bin/sh\necho "called: $*"\n')
    fake_tig.chmod(0o755)
    shim_dir = tmp_path / "shims"
    write_shims(shim_dir, [], str(fake_tig))

    result = subprocess.run(
        [str(shim_dir / DISPATCHER_NAME), "label", "x.vic"],
        capture_output=True,
        text=True,
    )
    usage = subprocess.run(
        [str(shim_dir / DISPATCHER_NAME)], capture_output=True, text=True
    )

    assert result.stdout.strip() == "called: label x.vic"
    assert usage.returncode == 2


def test_tig_executable_resolves_a_bare_name(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["tig"])
    monkeypatch.setattr("tig_cli.shim.shutil.which", lambda name: "/bin/tig")
    assert tig_executable() == "/bin/tig"


def test_tig_executable_uses_an_invoked_path(monkeypatch, tmp_path):
    executable = tmp_path / "tig"
    executable.touch()
    executable.chmod(0o755)
    monkeypatch.setattr(sys, "argv", [str(executable)])
    assert tig_executable() == str(executable.resolve())


def test_tig_executable_skips_a_module_path(monkeypatch, tmp_path):
    """'python -m tig_cli --shim' must not point shims at __main__.py."""
    module = tmp_path / "tig_cli" / "__main__.py"
    module.parent.mkdir()
    module.touch()
    console_script = tmp_path / "bin" / "tig"
    console_script.parent.mkdir()
    console_script.write_text("#!/bin/sh\n")
    console_script.chmod(0o755)
    monkeypatch.setattr(sys, "argv", [str(module)])
    monkeypatch.setattr(sys, "executable", str(tmp_path / "bin" / "python"))

    assert tig_executable() == str(console_script)


def _shim_manager(tools):
    manager = MagicMock()
    manager.list_tools.return_value = tools
    return manager


def test_cli_shim_writes_to_the_given_directory(tmp_path):
    runner = CliRunner()
    manager = _shim_manager(["marsmap", "marsmesh"])

    with patch("tig_cli.cli.ContainerManager", return_value=manager), \
         patch("tig_cli.cli.get_container_image", return_value=DEFAULT_IMAGE):
        result = runner.invoke(main, ["--shim-dir", str(tmp_path)])

    assert result.exit_code == 0
    assert (tmp_path / "marsmap").is_symlink()
    assert f"Wrote 2 command(s) to {tmp_path}" in result.output
    assert str(tmp_path) in result.output
    manager.ensure_container.assert_called_once()
    manager.release_claim.assert_called_once()


def test_cli_shim_defaults_to_the_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    runner = CliRunner()
    manager = _shim_manager(["marsmap"])

    with patch("tig_cli.cli.ContainerManager", return_value=manager), \
         patch("tig_cli.cli.get_container_image", return_value=DEFAULT_IMAGE):
        result = runner.invoke(main, ["--shim"])

    assert result.exit_code == 0
    assert (tmp_path / "tig/shims/marsmap").is_symlink()


def test_cli_shim_reports_skipped_names(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "tig_cli.shim.shutil.which",
        lambda name: "/usr/bin/sort" if name == "sort" else None,
    )
    runner = CliRunner()
    manager = _shim_manager(["marsmap", "sort"])

    with patch("tig_cli.cli.ContainerManager", return_value=manager), \
         patch("tig_cli.cli.get_container_image", return_value=DEFAULT_IMAGE):
        result = runner.invoke(main, ["--shim-dir", str(tmp_path)])

    assert "Skipped 1 name(s) already taken (sort)" in result.output


def test_cli_shim_does_not_run_a_vicar_tool(tmp_path):
    runner = CliRunner()
    manager = _shim_manager(["marsmap"])

    with patch("tig_cli.cli.ContainerManager", return_value=manager), \
         patch("tig_cli.cli.get_container_image", return_value=DEFAULT_IMAGE):
        runner.invoke(main, ["--shim-dir", str(tmp_path)])

    manager.execute_vicar_command.assert_not_called()


def test_cli_shim_rejects_a_tool_argument(tmp_path):
    """'tig --shim marsmap in.vic' must not quietly shim instead of running."""
    runner = CliRunner()
    manager = _shim_manager(["marsmap"])

    with patch("tig_cli.cli.ContainerManager", return_value=manager), \
         patch("tig_cli.cli.get_container_image", return_value=DEFAULT_IMAGE):
        result = runner.invoke(main, ["--shim", "marsmap", "in.vic"])

    assert result.exit_code == 2
    assert "cannot also run 'marsmap'" in result.output
    assert not (tmp_path / "marsmap").exists()
    manager.execute_vicar_command.assert_not_called()


def test_cli_shim_rejects_status(tmp_path):
    """--status drops the calibration mount, so shimming would start a
    second container and the status would never be printed."""
    runner = CliRunner()
    manager = _shim_manager(["marsmap"])

    with patch("tig_cli.cli.ContainerManager", return_value=manager), \
         patch("tig_cli.cli.get_container_image", return_value=DEFAULT_IMAGE):
        result = runner.invoke(main, ["--status", "--shim-dir", str(tmp_path)])

    assert result.exit_code == 2
    assert "run --status separately" in result.output
    assert not (tmp_path / "marsmap").exists()
    manager.ensure_container.assert_not_called()


def test_cli_shim_rejects_shutdown(tmp_path):
    """--shutdown runs first, so the shims would silently not be written."""
    runner = CliRunner()
    manager = _shim_manager(["marsmap"])

    with patch("tig_cli.cli.ContainerManager", return_value=manager), \
         patch("tig_cli.cli.get_container_image", return_value=DEFAULT_IMAGE):
        result = runner.invoke(
            main, ["--shutdown", "--shim-dir", str(tmp_path)]
        )

    assert result.exit_code == 2
    assert "run --shutdown separately" in result.output
    assert not (tmp_path / "marsmap").exists()
    manager.shutdown.assert_not_called()
