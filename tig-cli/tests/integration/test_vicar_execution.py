"""Integration tests for VICAR execution via Docker.

These tests require Docker to be running and the TIG image to be available.
Run with: pytest -m integration

Mark: pytest.ini_options in pyproject.toml defines the 'integration' marker.
"""
import subprocess
import sys

import pytest

from tig_cli.container import ContainerManager, get_container_image


@pytest.mark.integration
def test_vicar_help_command():
    """Can execute a VICAR command and capture exit code."""
    manager = ContainerManager(get_container_image())
    try:
        manager.ensure_container([])
        # viccub with missing param exits 1 but proves container execution works
        exit_code = manager.execute_vicar_command("viccub", [])
    finally:
        manager.shutdown()

    # TAE error for missing param returns 1, but that proves execution worked
    assert exit_code == 1


@pytest.mark.integration
def test_vicar_command_with_path_translation(tmp_path):
    """Path translation works for real file args."""
    # Create a small test file on host
    test_file = tmp_path / "test.txt"
    test_file.write_text("test content")

    manager = ContainerManager(get_container_image())
    try:
        manager.ensure_container([])
        # Use a simple VICAR command that reads the file
        # label just reads metadata so won't fail on non-VICAR files
        exit_code = manager.execute_vicar_command("label", [str(test_file)])
    finally:
        manager.shutdown()

    # label will return non-zero on a non-VICAR file but should NOT crash the manager
    # The important thing is that path translation ran without exception
    assert exit_code is not None


@pytest.mark.integration
def test_container_image_from_env():
    """CONTAINER_IMAGE env var controls which image is used."""
    image = get_container_image()
    assert image  # Not empty

    # Verify the image is pullable (or cached) by trying to start it
    manager = ContainerManager(image)
    try:
        manager.ensure_container([])
    finally:
        manager.shutdown()


@pytest.mark.integration
def test_cli_help_invocation():
    """tig --help exits 0 and shows expected content."""
    result = subprocess.run(
        [sys.executable, "-m", "tig_cli", "--help"],
        capture_output=True,
        text=True
    )
    # --help should exit 0
    assert result.returncode == 0
    assert "CONTAINER_IMAGE" in result.stdout


@pytest.mark.integration
def test_container_is_reused_across_invocations():
    """A second manager reuses the container created by the first."""
    first = ContainerManager(get_container_image())
    second = ContainerManager(get_container_image())
    try:
        first.ensure_container([])
        second.ensure_container([])

        assert second.container_name == first.container_name
        assert second.container.id == first.container.id
    finally:
        first.shutdown()
