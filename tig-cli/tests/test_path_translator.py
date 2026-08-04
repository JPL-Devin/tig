"""Tests for path translation."""
import pytest
from tig_cli.path_translator import PathTranslator


@pytest.fixture
def home_dir(tmp_path):
    """Create a temporary home directory."""
    return str(tmp_path / "home" / "user")


@pytest.fixture
def translator(home_dir):
    """Create a PathTranslator instance."""
    return PathTranslator(home_dir)


def test_relative_path_unchanged(translator):
    assert translator.translate("file.vic") == "file.vic"
    assert translator.translate("./data/file.vic") == "./data/file.vic"
    assert translator.translate("../other/file.vic") == "../other/file.vic"


def test_home_path_unchanged(translator, home_dir):
    path = f"{home_dir}/data/file.vic"
    assert translator.translate(path) == path


def test_system_path_gets_host_prefix(translator):
    import os
    from pathlib import Path
    
    # Use /data which doesn't have symlink issues
    assert translator.translate("/data/file.vic") == "/host/data/file.vic"
    
    # For /tmp, expect the resolved path (handles macOS /tmp -> /private/tmp)
    tmp_resolved = str(Path("/tmp").resolve())
    expected = f"/host{tmp_resolved}/output.vic"
    assert translator.translate("/tmp/output.vic") == expected


def test_empty_path_unchanged(translator):
    assert translator.translate("") == ""


def test_translate_args_list(translator, home_dir):
    args = [
        "file.vic",
        f"{home_dir}/input.vic",
        "/data/system.vic",
    ]
    expected = [
        "file.vic",
        f"{home_dir}/input.vic",
        "/host/data/system.vic",
    ]
    assert translator.translate_args(args) == expected


def test_get_container_cwd_in_home(translator, home_dir):
    cwd = f"{home_dir}/projects/vicar"
    assert translator.get_container_cwd(cwd) == cwd


def test_get_container_cwd_outside_home(translator):
    cwd = "/opt/vicar/workspace"
    assert translator.get_container_cwd(cwd) == "/host/opt/vicar/workspace"


def test_home_directory_itself(translator, home_dir):
    assert translator.translate(home_dir) == home_dir


def test_root_path_gets_host_prefix(translator):
    assert translator.translate("/") == "/host/"


def test_path_with_spaces(translator, home_dir):
    path = f"{home_dir}/my documents/file.vic"
    assert translator.translate(path) == path
    system_path = "/data/my files/image.vic"
    assert translator.translate(system_path) == "/host/data/my files/image.vic"


def test_path_with_special_characters(translator):
    assert translator.translate("/data/file-name.vic") == "/host/data/file-name.vic"
    assert translator.translate("/data/file_name.vic") == "/host/data/file_name.vic"
    assert translator.translate("/data/file.name.vic") == "/host/data/file.name.vic"


def test_non_path_arguments(translator):
    assert translator.translate("123") == "123"
    assert translator.translate("3.14") == "3.14"
    assert translator.translate("-v") == "-v"
    assert translator.translate("--verbose") == "--verbose"
    assert translator.translate("INP=file.vic") == "INP=file.vic"
    assert translator.translate("OUT=/tmp/out.vic") == "OUT=/tmp/out.vic"


def test_translate_args_mixed_types(translator, home_dir):
    args = [
        "marsmap",
        "-v",
        f"{home_dir}/input.vic",
        "/data/system.vic",
        "output.vic",
        "SIZE=(1,1,1024,1024)",
    ]
    result = translator.translate_args(args)
    assert result[0] == "marsmap"
    assert result[1] == "-v"
    assert result[2] == f"{home_dir}/input.vic"
    assert result[3] == "/host/data/system.vic"
    assert result[4] == "output.vic"
    assert result[5] == "SIZE=(1,1,1024,1024)"
