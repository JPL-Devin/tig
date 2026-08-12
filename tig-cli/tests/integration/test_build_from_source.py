"""Integration tests for --build: compile a VICAR unit and run it.

These need Docker, the runtime image, and the builder image, which is local
only: terrain-intelligence-generator/build-builder-image.sh builds it.
Run with: pytest -m integration
"""
import os
import subprocess
import sys

import pytest

from tig_cli.build import (
    Builder,
    Overrides,
    find_unit,
    image_id,
    install,
    resolve_builder_image,
    verify_in_container,
)
from tig_cli.container import ContainerManager, get_container_image
from tig_cli.runtime import Runtime

PATCH = "TIG-BUILD-INTEGRATION"
TIG = [sys.executable, "-m", "tig_cli"]


def docker(*arguments, check=True):
    return subprocess.run(
        ["docker", *arguments], capture_output=True, text=True, check=check
    )


@pytest.fixture(scope="module")
def builder_image():
    image = resolve_builder_image(None)
    available = docker("image", "inspect", image, check=False).returncode == 0
    if not available:
        pytest.skip(f"builder image {image} not built; see build-builder-image.sh")
    return image


@pytest.fixture
def patched_source(builder_image, tmp_path):
    """A copy of the release's own gen program with its version string changed."""
    source = tmp_path / "src" / "gen"
    source.mkdir(parents=True)
    docker(
        "run", "--rm", "--platform", "linux/amd64",
        "-v", f"{source}:/out",
        builder_image,
        "bash", "-c",
        "cp $V2TOP/p2/prog/gen/gen.c $V2TOP/p2/prog/gen/gen.imake "
        "$V2TOP/p2/prog/gen/gen.pdf /out/ && chmod -R a+rw /out",
    )
    program = source / "gen.c"
    text = program.read_text()
    marker = "GEN Version"
    line = next(li for li in text.splitlines() if marker in li and '"' in li)
    program.write_text(text.replace(line.split('"')[1], PATCH))
    return source


@pytest.fixture
def state_root(tmp_path, monkeypatch):
    """Keep recorded builds out of the invoking user's data directory."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    yield


@pytest.mark.integration
def test_a_patched_program_builds_and_runs_in_the_container(
    builder_image, patched_source, state_root, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    runtime_image = get_container_image()
    unit = find_unit(patched_source)
    runtime = Runtime.detect()
    builder = Builder(runtime, builder_image, runtime_image)
    builder.check_images()
    artifact = builder.build(unit)
    assert artifact.is_file()
    # The user's source directory keeps no build output.
    assert sorted(p.name for p in patched_source.iterdir()) == [
        "gen.c", "gen.imake", "gen.pdf"
    ]

    manager = ContainerManager(runtime_image)
    try:
        manager.ensure_container([])
        install(runtime, manager.container_name, unit, artifact, unit.pdf)
        assert verify_in_container(runtime, manager.container_name, unit.name) is None

        Overrides(str(manager.image_id())).record(unit, artifact, unit.pdf)
        exit_code = manager.execute_vicar_command("gen", ["out=t.vic", "nl=4", "ns=4"])
        assert exit_code == 0

        # A container created later gets the recorded build re-applied to it.
        docker("rm", "-f", manager.container_name)
        replacement = ContainerManager(runtime_image)
        replacement.ensure_container([])
        printed = docker(
            "exec", replacement.container_name,
            "cat", "-v", unit.container_path, check=False,
        )
        assert PATCH in printed.stdout
    finally:
        manager.shutdown()


@pytest.mark.integration
def test_the_build_is_installed_by_the_cli_and_runs_by_name(
    builder_image, patched_source, state_root, tmp_path
):
    environment = dict(os.environ, TIG_BUILDER_IMAGE=builder_image)
    build = subprocess.run(
        [*TIG, "--build", "gen"],
        cwd=patched_source.parent,  # the source root, not the unit's directory
        capture_output=True,
        text=True,
        env=environment,
    )
    assert build.returncode == 0, build.stderr
    assert "Installed /usr/local/vicar/dev/p2/lib/x86-64-linx/gen" in build.stdout

    try:
        run = subprocess.run(
            [*TIG, "gen", "out=t.vic", "nl=4", "ns=4"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            env=environment,
        )
        assert run.returncode == 0, run.stderr
        assert PATCH in run.stdout

        listed = subprocess.run(
            [*TIG, "--build-list"], capture_output=True, text=True, env=environment
        )
        assert "gen" in listed.stdout
    finally:
        subprocess.run([*TIG, "--build-clean"], capture_output=True, env=environment)
        subprocess.run([*TIG, "--shutdown"], capture_output=True, env=environment)


@pytest.mark.integration
def test_a_container_running_before_the_build_still_gets_it(
    builder_image, patched_source, state_root, tmp_path
):
    environment = dict(os.environ, TIG_BUILDER_IMAGE=builder_image)
    # Leaves a container running for this directory, holding the image's gen.
    warmed = subprocess.run(
        [*TIG, "gen", "out=warm.vic", "nl=4", "ns=4"],
        cwd=tmp_path, capture_output=True, text=True, env=environment,
    )
    assert warmed.returncode == 0, warmed.stderr
    assert PATCH not in warmed.stdout

    try:
        build = subprocess.run(
            [*TIG, "--build", "gen"],
            cwd=patched_source, capture_output=True, text=True, env=environment,
        )
        assert build.returncode == 0, build.stderr

        run = subprocess.run(
            [*TIG, "gen", "out=t.vic", "nl=4", "ns=4"],
            cwd=tmp_path, capture_output=True, text=True, env=environment,
        )
        assert run.returncode == 0, run.stderr
        assert PATCH in run.stdout
    finally:
        subprocess.run([*TIG, "--build-clean"], capture_output=True, env=environment)
        subprocess.run([*TIG, "--shutdown"], capture_output=True, env=environment)


@pytest.mark.integration
def test_image_mode_produces_an_image_that_runs_the_program(
    builder_image, patched_source, state_root
):
    runtime_image = get_container_image()
    unit = find_unit(patched_source)
    tag = "tig-build-integration:gen"
    build = subprocess.run(
        [*TIG, "--build-image", tag],
        cwd=patched_source,
        capture_output=True,
        text=True,
        env=dict(os.environ, TIG_BUILDER_IMAGE=builder_image),
    )
    assert build.returncode == 0, build.stderr

    try:
        # The layer replaced the image's own program, leaving the rest of it.
        runtime = Runtime.detect()
        assert image_id(runtime, tag) != image_id(runtime, runtime_image)
        ran = docker(
            "run", "--rm", "--platform", "linux/amd64", "-w", "/tmp", tag,
            "gen", "out=t.vic", "nl=4", "ns=4",
        )
        assert PATCH in ran.stdout
    finally:
        docker("image", "rm", "-f", tag, check=False)
