"""Tests for the Python API, against a runtime answered from memory."""
import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

import tig_cli
from tig_cli import api
from tig_cli.api import Result, Tig, VicarError, VicarTimeout
from tig_cli.config import Config
from tig_cli.spec import EXEC_ID_ENV, TigError

from tests.test_container import IMAGE, FakeRuntime


class FakeExec:
    """Stands in for subprocess.run: records the exec, plays a scripted tool.

    ``script`` is called with the argument list after the tool name and may
    write files, so that output tracking sees what a real tool would do.
    """

    def __init__(self, exit_code=0, stdout=b"", stderr=b"", script=None):
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.script = script
        self.calls = []
        self.kwargs = []

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        self.kwargs.append(kwargs)
        if self.script is not None and argv[1] == "exec":
            self.script(argv)
        return subprocess.CompletedProcess(
            argv, self.exit_code, self.stdout, self.stderr
        )

    @property
    def exec(self):
        """The one exec of a tool that was issued."""
        execs = [c for c in self.calls if c[1] == "exec" and "-w" in c]
        assert len(execs) == 1, f"tools were run {len(execs)} times"
        return execs[0]

    def tool_args(self):
        """The exec's arguments past the container name."""
        argv = self.exec
        container = next(a for a in argv if a.startswith("tig-vicar-"))
        return argv[argv.index(container) + 1:]

    def env(self):
        argv = self.exec
        return dict(
            argv[i + 1].split("=", 1) for i, a in enumerate(argv) if a == "-e"
        )


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "home" / "user"
    home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    return home


@pytest.fixture
def workdir(home):
    workdir = home / "work"
    workdir.mkdir()
    return workdir


@pytest.fixture
def no_x11(monkeypatch):
    monkeypatch.setattr(api, "ensure_x11_ready", lambda: None)


def make_tig(workdir, runtime=None, **kwargs):
    return Tig(IMAGE, workdir=workdir, runtime=runtime or FakeRuntime(), **kwargs)


def run_with(fake, tig, *args, **kwargs):
    with patch.object(api.subprocess, "run", fake):
        return tig.run(*args, **kwargs)


# --- Result ---


def result(exit_code=0, stdout="", stderr="", outputs=()):
    return Result("gen", ["gen"], exit_code, stdout, stderr, list(outputs))


def test_result_ok_follows_the_exit_code():
    assert result(0).ok
    assert result(0)
    assert not result(1).ok
    assert not result(1)


def test_check_returns_a_passing_result():
    passing = result(0)
    assert passing.check() is passing


def test_check_raises_on_failure_with_the_last_line_of_output():
    failing = result(255, stdout="Beginning VICAR task GEN\n ** ABEND called **\n")
    with pytest.raises(VicarError, match=r"gen exited with status 255: \*\* ABEND") as e:
        failing.check()
    assert e.value.result is failing
    assert isinstance(e.value, TigError)


def test_check_prefers_stderr_for_the_detail():
    with pytest.raises(VicarError, match="status 1: boom"):
        result(1, stdout="chatter", stderr="boom\n").check()


def test_output_is_the_single_output_file():
    assert result(outputs=[Path("/a")]).output == Path("/a")
    assert result(outputs=[]).output is None
    assert result(outputs=[Path("/a"), Path("/b")]).output is None


def test_result_repr_is_compact():
    assert repr(result(outputs=[Path("/a.vic")])) == (
        "Result(tool='gen', exit_code=0, outputs=['/a.vic'])"
    )


# --- Tig construction ---


def test_defaults_come_from_the_config(home, workdir, monkeypatch):
    calibration = home / "calib"
    calibration.mkdir()
    config = Config(
        image="config-image:1",
        calibration_path=str(calibration),
        writable_paths=["/data"],
        disable_path_translation=True,
    )
    tig = Tig(workdir=workdir, config=config, runtime=FakeRuntime())
    assert tig.image == "config-image:1"
    assert tig.manager.calibration_path == str(calibration)
    assert tig.manager.disable_path_translation
    assert "/data" in tig.writable_paths


def test_arguments_override_the_config(home, workdir):
    config = Config(image="config-image:1")
    tig = Tig("arg-image:2", workdir=workdir, config=config, runtime=FakeRuntime())
    assert tig.image == "arg-image:2"


def test_config_may_be_a_file(home, workdir, tmp_path):
    path = tmp_path / "tig.toml"
    path.write_text('image = "file-image:3"\n')
    tig = Tig(workdir=workdir, config=path, runtime=FakeRuntime())
    assert tig.image == "file-image:3"


def test_a_bad_config_file_is_user_facing(home, workdir, tmp_path):
    path = tmp_path / "tig.toml"
    path.write_text("image = [\n")
    with pytest.raises(TigError):
        Tig(workdir=workdir, config=path, runtime=FakeRuntime())


def test_workdir_defaults_to_the_current_directory(home, workdir, monkeypatch):
    monkeypatch.chdir(workdir)
    assert Tig(IMAGE, runtime=FakeRuntime()).workdir == str(workdir.resolve())


def test_workdir_is_made_writable(home, tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    tig = make_tig(outside)
    assert str(outside.resolve()) in tig.writable_paths


def test_the_package_exports_the_api_lazily():
    assert tig_cli.Tig is Tig
    assert tig_cli.run is api.run
    with pytest.raises(AttributeError):
        tig_cli.nonexistent


# --- Running a tool ---


def test_run_execs_the_tool_in_the_container(home, workdir, no_x11):
    tig = make_tig(workdir)
    fake = FakeExec(stdout=b"GEN task completed\n")
    result = run_with(fake, tig, "gen", "test.vic", 64, 64)

    assert result.ok
    assert result.stdout == "GEN task completed\n"
    assert result.stderr == ""
    assert result.tool == "gen"
    assert fake.tool_args() == ["gen", "test.vic", "64", "64"]
    assert result.command == ["gen", "test.vic", "64", "64"]
    argv = fake.exec
    assert argv[:2] == ["/usr/bin/docker", "exec"]
    assert argv[argv.index("-w") + 1] == str(workdir.resolve())
    assert tig.container_name in argv
    assert tig.container_name in tig.manager.runtime.containers


def test_run_starts_the_container_once(home, workdir, no_x11):
    runtime = FakeRuntime()
    tig = make_tig(workdir, runtime)
    fake = FakeExec()
    run_with(fake, tig, "gen", "a.vic")
    run_with(fake, tig, "gen", "b.vic")
    assert runtime.created == [tig.container_name]


def test_keywords_become_vicar_parameters(home, workdir, no_x11):
    fake = FakeExec()
    run_with(fake, make_tig(workdir), "label", "-list", inp="test.vic", nl=10)
    assert fake.tool_args() == ["label", "-list", "inp=test.vic", "nl=10"]


def test_sequences_become_value_lists(home, workdir, no_x11):
    fake = FakeExec()
    run_with(
        fake, make_tig(workdir), "marsxyz",
        inp=[Path("left.vic"), "right.vic"], out=("xyz.vic",),
    )
    assert fake.tool_args() == [
        "marsxyz", "inp=(left.vic,right.vic)", "out=(xyz.vic)"
    ]


def test_host_paths_are_translated(home, workdir, no_x11):
    fake = FakeExec()
    run_with(
        fake, make_tig(workdir), "copy", "/data/in.vic", out=Path("/data/out.vic")
    )
    assert fake.tool_args() == [
        "copy", "/host/data/in.vic", "out=/host/data/out.vic"
    ]


def test_paths_under_home_are_left_alone(home, workdir, no_x11):
    fake = FakeExec()
    run_with(fake, make_tig(workdir), "copy", workdir / "in.vic")
    assert fake.tool_args() == ["copy", str(workdir / "in.vic")]


def test_translation_can_be_disabled(home, workdir, no_x11):
    fake = FakeExec()
    tig = make_tig(workdir, disable_path_translation=True)
    run_with(fake, tig, "copy", "/data/in.vic")
    assert fake.tool_args() == ["copy", "/data/in.vic"]


def test_the_workdir_is_translated_too(home, tmp_path, no_x11):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    fake = FakeExec()
    run_with(fake, make_tig(outside), "gen", "a.vic")
    argv = fake.exec
    assert argv[argv.index("-w") + 1] == f"/host{outside.resolve()}"


def test_run_may_use_another_workdir(home, workdir, no_x11):
    other = workdir.parent / "other"
    other.mkdir()
    fake = FakeExec()
    run_with(fake, make_tig(workdir), "gen", "a.vic", workdir=other)
    argv = fake.exec
    assert argv[argv.index("-w") + 1] == str(other.resolve())


def test_each_run_gets_a_display_and_an_exec_id(home, workdir, no_x11):
    fake = FakeExec()
    run_with(fake, make_tig(workdir), "gen", "a.vic")
    env = fake.env()
    assert "DISPLAY" in env
    assert len(env[EXEC_ID_ENV]) == 32


def test_environment_layers_handle_then_run(home, workdir, no_x11):
    fake = FakeExec()
    tig = make_tig(workdir, env={"A": "handle", "B": "handle"})
    run_with(fake, tig, "gen", env={"B": "run", "C": "run"})
    env = fake.env()
    assert (env["A"], env["B"], env["C"]) == ("handle", "run", "run")


def test_stdin_is_closed_unless_given(home, workdir, no_x11):
    fake = FakeExec()
    run_with(fake, make_tig(workdir), "gen")
    assert "-i" not in fake.exec
    assert fake.kwargs[-1]["stdin"] is subprocess.DEVNULL
    assert fake.kwargs[-1]["input"] is None


def test_stdin_text_is_fed_to_the_tool(home, workdir, no_x11):
    fake = FakeExec()
    run_with(fake, make_tig(workdir), "cat", stdin="hello\n")
    assert "-i" in fake.exec
    assert fake.kwargs[-1]["input"] == b"hello\n"
    assert fake.kwargs[-1]["stdin"] is None


def test_stdin_bytes_are_fed_as_is(home, workdir, no_x11):
    fake = FakeExec()
    run_with(fake, make_tig(workdir), "cat", stdin=b"\x00\x01")
    assert fake.kwargs[-1]["input"] == b"\x00\x01"


def test_output_is_decoded_leniently(home, workdir, no_x11):
    fake = FakeExec(stdout=b"ok \xff\n", stderr=b"\xfe")
    result = run_with(fake, make_tig(workdir), "gen")
    assert result.stdout == "ok \ufffd\n"
    assert result.stderr == "\ufffd"


def test_a_failing_tool_is_reported_not_raised(home, workdir, no_x11):
    fake = FakeExec(exit_code=255, stdout=b" ** ABEND called **\n")
    result = run_with(fake, make_tig(workdir), "gen")
    assert result.exit_code == 255
    assert not result


def test_check_raises_for_a_failing_tool(home, workdir, no_x11):
    fake = FakeExec(exit_code=255, stdout=b" ** ABEND called **\n")
    with pytest.raises(VicarError, match="gen exited with status 255"):
        run_with(fake, make_tig(workdir), "gen", check=True)


def test_timeout_is_passed_and_kills_the_tool(home, workdir, no_x11):
    tig = make_tig(workdir)
    calls = []

    def fake(argv, **kwargs):
        calls.append((list(argv), kwargs))
        if len(calls) == 1:
            raise subprocess.TimeoutExpired(argv, 2.5, output=b"start\n")
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    with pytest.raises(VicarTimeout, match="sleep did not finish within 2.5") as e:
        run_with(fake, tig, "sleep", "30", timeout=2.5)

    assert e.value.stdout == "start\n"
    assert calls[0][1]["timeout"] == 2.5
    exec_id = dict(
        a.split("=", 1) for a in calls[0][0] if a.startswith(EXEC_ID_ENV)
    )[EXEC_ID_ENV]
    kill_argv, kill_kwargs = calls[1]
    assert kill_argv[:3] == ["/usr/bin/docker", "exec", tig.container_name]
    assert kill_argv[3:5] == ["sh", "-c"]
    assert exec_id in kill_argv[5]
    assert "-9" in kill_argv[5] or "KILL" in kill_argv[5]
    assert kill_kwargs["timeout"] == api.KILL_TIMEOUT


def test_a_failed_kill_does_not_hide_the_timeout(home, workdir, no_x11):
    def fake(argv, **kwargs):
        if kwargs.get("capture_output"):
            raise subprocess.TimeoutExpired(argv, 1)
        raise OSError("runtime gone")

    with pytest.raises(VicarTimeout):
        run_with(fake, make_tig(workdir), "sleep", timeout=1)


def test_a_missing_runtime_binary_is_user_facing(home, workdir, no_x11):
    def fake(argv, **kwargs):
        raise FileNotFoundError(argv[0])

    with pytest.raises(TigError, match="Failed to run docker"):
        run_with(fake, make_tig(workdir), "gen")


def test_tools_are_methods(home, workdir, no_x11):
    tig = make_tig(workdir)
    fake = FakeExec()
    with patch.object(api.subprocess, "run", fake):
        result = tig.marsxyz("left.vic", out="xyz.vic")
    assert isinstance(result, Result)
    assert fake.tool_args() == ["marsxyz", "left.vic", "out=xyz.vic"]
    assert tig.marsxyz.__name__ == "marsxyz"


def test_private_names_are_not_tools(home, workdir):
    with pytest.raises(AttributeError):
        make_tig(workdir)._marsxyz


def test_x11_is_readied_before_each_run(home, workdir, monkeypatch):
    readied = []
    monkeypatch.setattr(api, "ensure_x11_ready", lambda: readied.append(True))
    monkeypatch.setattr(
        "tig_cli.container.ensure_x11_ready", lambda: None
    )
    run_with(FakeExec(), make_tig(workdir), "gen")
    assert readied == [True]


# --- Output files ---


def touch(path, content=b"x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def writes(*names):
    """A tool that writes the named files relative to the exec's workdir."""

    def script(argv):
        workdir = argv[argv.index("-w") + 1]
        for name in names:
            touch(Path(workdir) / name)

    return script


def test_created_files_named_in_the_arguments_are_outputs(home, workdir, no_x11):
    fake = FakeExec(script=writes("test.vic"))
    result = run_with(fake, make_tig(workdir), "gen", "test.vic", 64, 64)
    assert result.outputs == [workdir / "test.vic"]
    assert result.output == workdir / "test.vic"


def test_untouched_inputs_are_not_outputs(home, workdir, no_x11):
    touch(workdir / "in.vic")
    fake = FakeExec(script=writes("out.vic"))
    result = run_with(fake, make_tig(workdir), "copy", "in.vic", "out.vic")
    assert result.outputs == [workdir / "out.vic"]


def test_rewritten_files_are_outputs(home, workdir, no_x11):
    touch(workdir / "out.vic", b"old")
    os.utime(workdir / "out.vic", ns=(0, 0))
    fake = FakeExec(script=writes("out.vic"))
    result = run_with(fake, make_tig(workdir), "gen", "out.vic")
    assert result.outputs == [workdir / "out.vic"]


def test_keyword_values_and_lists_are_tracked(home, workdir, no_x11):
    fake = FakeExec(script=writes("xyz.vic", "b.vic"))
    result = run_with(
        fake, make_tig(workdir), "marsxyz", inp=["a.vic", "b.vic"], out="xyz.vic"
    )
    assert result.outputs == [workdir / "b.vic", workdir / "xyz.vic"]


def test_absolute_host_paths_are_tracked_untranslated(home, workdir, no_x11):
    target = workdir / "abs.vic"

    def script(argv):
        touch(target)

    result = run_with(
        FakeExec(script=script), make_tig(workdir), "gen", str(target)
    )
    assert result.outputs == [target]


def test_outputs_follow_the_runs_workdir(home, workdir, no_x11):
    other = workdir.parent / "other"
    other.mkdir()
    fake = FakeExec(script=writes("a.vic"))
    result = run_with(fake, make_tig(workdir), "gen", "a.vic", workdir=other)
    assert result.outputs == [other / "a.vic"]


def test_flags_and_directories_are_not_outputs(home, workdir, no_x11):
    (workdir / "-list").mkdir()
    (workdir / "sub").mkdir()
    fake = FakeExec()
    result = run_with(fake, make_tig(workdir), "label", "-list", "sub", "64")
    assert result.outputs == []


def test_a_failed_run_still_reports_what_it_wrote(home, workdir, no_x11):
    fake = FakeExec(exit_code=1, script=writes("partial.vic"))
    result = run_with(fake, make_tig(workdir), "gen", "partial.vic")
    assert result.outputs == [workdir / "partial.vic"]
    assert not result.ok


# --- Lifecycle ---


def test_context_manager_releases_the_claim(home, workdir, no_x11):
    with make_tig(workdir) as tig:
        run_with(FakeExec(), tig, "gen")
        claim = tig.manager.claim
        assert claim is not None and claim.exists()
    assert not claim.exists()


def test_close_leaves_the_container_running(home, workdir, no_x11):
    runtime = FakeRuntime()
    with make_tig(workdir, runtime) as tig:
        run_with(FakeExec(), tig, "gen")
    assert tig.container_name in runtime.containers


def test_shutdown_removes_the_container(home, workdir, no_x11):
    runtime = FakeRuntime()
    tig = make_tig(workdir, runtime)
    run_with(FakeExec(), tig, "gen")
    assert tig.shutdown() == 1
    assert tig.container_name not in runtime.containers


def test_tools_lists_what_the_image_provides(home, workdir):
    runtime = FakeRuntime()
    runtime.exec_output = "gen\nlabel\n"
    tig = make_tig(workdir, runtime)
    with patch("tig_cli.container.ensure_x11_ready", lambda: None):
        tools = tig.tools()
    assert tools == ["gen", "label"]


def test_module_run_uses_a_fresh_handle(home, workdir, monkeypatch, no_x11):
    monkeypatch.chdir(workdir)
    monkeypatch.setattr(api.Runtime, "detect", classmethod(lambda cls, config=None: FakeRuntime()))
    fake = FakeExec(stdout=b"done\n")
    with patch.object(api.subprocess, "run", fake):
        result = api.run("gen", "test.vic")
    assert result.stdout == "done\n"
    assert fake.tool_args() == ["gen", "test.vic"]
