"""Integration tests for the Python API against the real image.

Run with: pytest -m integration
"""
import pytest

from tig_cli import Tig, VicarError, VicarTimeout


@pytest.fixture
def tig(tmp_path):
    # tmp_path is outside the home directory, so this exercises the
    # read-write mount of the working directory and /host translation.
    with Tig(workdir=tmp_path) as tig:
        yield tig


@pytest.mark.integration
def test_generates_an_image_and_reports_it(tig, tmp_path):
    result = tig.run("gen", "test.vic", 32, 32)

    assert result.ok, result.stdout
    assert "GEN task completed" in result.stdout
    assert result.outputs == [tmp_path / "test.vic"]
    assert result.output.stat().st_size > 32 * 32


@pytest.mark.integration
def test_pipelines_files_between_tools(tig, tmp_path):
    tig.run("gen", "test.vic", 32, 32, check=True)
    listing = tig.run("label", "-list", inp="test.vic", check=True)
    png = tig.vicario("test.vic", "test.png").check()

    assert "File test.vic" in listing.stdout
    assert listing.outputs == []
    assert png.output == tmp_path / "test.png"
    assert png.output.read_bytes().startswith(b"\x89PNG")


@pytest.mark.integration
def test_absolute_host_paths_are_translated(tig, tmp_path):
    result = tig.run("gen", tmp_path / "abs.vic", 16, 16, check=True)

    assert result.command[1] == f"/host{tmp_path / 'abs.vic'}"
    assert result.outputs == [tmp_path / "abs.vic"]


@pytest.mark.integration
def test_a_failing_tool_reports_its_status(tig):
    result = tig.run("copy", "missing.vic", "out.vic")

    assert not result.ok
    assert result.outputs == []
    with pytest.raises(VicarError):
        result.check()


@pytest.mark.integration
def test_stdin_and_environment_reach_the_tool(tig):
    result = tig.run("sh", "-c", 'cat; echo "$GREETING"',
                     stdin="in\n", env={"GREETING": "hi"})

    assert result.stdout == "in\nhi\n"


@pytest.mark.integration
def test_a_timed_out_tool_is_killed_in_the_container(tig):
    with pytest.raises(VicarTimeout):
        tig.run("sh", "-c", "echo started; sleep 60", timeout=2)

    survivors = tig.run("sh", "-c", "ps -eo stat,args | grep '[s]leep 60' || true")
    assert all(line.startswith("Z") for line in survivors.stdout.splitlines())


@pytest.mark.integration
def test_the_container_is_shared_between_handles_and_runs(tmp_path):
    with Tig(workdir=tmp_path) as first:
        first.run("gen", "a.vic", 8, 8, check=True)
        name = first.container_name
    with Tig(workdir=tmp_path) as second:
        second.run("gen", "b.vic", 8, 8, check=True)
        assert second.container_name == name


@pytest.mark.integration
def test_tools_include_the_vicar_programs(tig):
    tools = tig.tools()
    assert {"gen", "label", "marsxyz"} <= set(tools)
