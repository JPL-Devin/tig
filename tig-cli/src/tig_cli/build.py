"""Building VICAR program units from local source and installing them.

VICAR builds a program from an imakefile: ``vimake <unit>`` turns
``<unit>.imake`` into ``<unit>.make``, and ``make -f <unit>.make std`` produces
the executable. None of that can happen in the runtime image, which has no
compilers and none of the headers, imakefiles or external archives a build needs
(that pruning is what halves its size), so compilation runs in a separate
builder image holding the same VICAR release unpruned.

The result is then installed either into the running container - overwriting the
program's real path, so every caller of it sees the new build - or as one extra
layer on top of the runtime image, which is the shareable, reproducible form.

Injected binaries are recorded per image, because tig containers are disposable:
they are reaped, replaced when the image moves, and removed by ``--shutdown``,
and the record is what lets a fresh container be patched again on the next
invocation.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .spec import (
    BUILDS_NAMES_DIR,
    BUILDS_RECORDED_FILE,
    IMAGE_PLATFORM,
    TigError,
    build_state_root,
)

# Not published as an image: the VICAR release tarballs it unpacks are the
# user's to fetch, so this is built locally by build-builder-image.sh.
DEFAULT_BUILDER_IMAGE = "terrain-intelligence-generator:opensource-builder"

BUILDER_SCRIPT = "vicar-build"

# Set on both images by their Dockerfiles. A unit links against the runtime
# image's own libraries, so the two must come from one VICAR release.
VICAR_VERSION_LABEL = "org.nasa.tig.vicar-version"

V2TOP = "/usr/local/vicar/dev"
ARCH = "x86-64-linx"
WRAPPER_DIR = "/usr/local/bin"

# The imakefile macro naming the subsystem whose lib directory holds the
# program: gen.imake says R2LIB, marsmesh.imake says MARSLIB.
LIB_MACROS = {
    "R2LIB": "p2",
    "P2LIB": "p2",
    "P3LIB": "p3",
    "MARSLIB": "mars",
    "P1LIB": "p1",
}

# Unit kinds vimake understands. Only PROGRAM units produce an executable that
# can be installed on its own; the others build into link libraries.
UNIT_KINDS = ("PROGRAM", "SUBROUTINE", "PROCEDURE", "MODULE")

# Depth of the search for <unit>.imake below the current directory, enough to
# reach mars/src/prog/<unit> or p2/prog/<unit> from a source root.
SEARCH_DEPTH = 6

# Unit names end up in shell and Dockerfile commands, so nothing but what a
# VICAR program name can be is accepted.
UNIT_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$")

# Records which builder image compiled a unit's objects, inside its directory.
BUILDER_STAMP = ".tig-builder"

DOCKER_TIMEOUT = 60
VERIFY_TIMEOUT = 120


class Unit:
    """A VICAR unit to build: its imakefile, and where its program belongs."""

    def __init__(self, name: str, directory: Path, kind: str, subsystem: str):
        self.name = name
        self.directory = directory
        self.kind = kind
        self.subsystem = subsystem

    @property
    def imakefile(self) -> Path:
        return self.directory / f"{self.name}.imake"

    @property
    def pdf(self) -> Optional[Path]:
        """The TAE parameter definition, which is installed with the program.

        It lives beside the binary in the image, so a change to a program's
        parameters is only complete once it is installed too.
        """
        candidate = self.directory / f"{self.name}.pdf"
        return candidate if candidate.is_file() else None

    @property
    def container_path(self) -> str:
        return f"{V2TOP}/{self.subsystem}/lib/{ARCH}/{self.name}"

    @property
    def container_lib_dir(self) -> str:
        return f"{V2TOP}/{self.subsystem}/lib/{ARCH}"

    def __repr__(self) -> str:
        return f"Unit({self.name!r}, {str(self.directory)!r}, {self.kind})"


def parse_imakefile(path: Path) -> Tuple[str, str, Optional[str]]:
    """Return the (name, kind, subsystem) an imakefile describes.

    Args:
        path: Path of the ``<unit>.imake`` file

    Returns:
        The unit name, its kind (``PROGRAM``, ``SUBROUTINE``, ...) and the
        subsystem directory its program installs into, or ``None`` when the
        imakefile names no library macro.
    """
    try:
        text = path.read_text(errors="replace")
    except OSError as e:
        raise TigError(f"Cannot read {path}: {e}") from e

    # Strip C comments: imakefiles routinely comment out a macro rather than
    # delete it, and a commented PROGRAM line must not win.
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.DOTALL)

    name = ""
    kind = ""
    subsystem = None
    for match in re.finditer(r"^[ \t]*#[ \t]*define[ \t]+(\w+)[ \t]*(\S*)", text, re.M):
        macro, value = match.group(1), match.group(2)
        if macro in UNIT_KINDS and not kind:
            kind, name = macro, value
        elif macro in LIB_MACROS and subsystem is None:
            subsystem = LIB_MACROS[macro]

    if not kind:
        raise TigError(
            f"{path} defines no PROGRAM, SUBROUTINE or PROCEDURE; "
            "it does not look like a VICAR imakefile."
        )
    if not name:
        raise TigError(f"{path}: '#define {kind}' names no unit.")
    if not UNIT_NAME.match(name):
        raise TigError(f"{path}: {name!r} is not a valid VICAR unit name.")
    return name, kind, subsystem


def _imakefiles_below(root: Path, name: str) -> List[Path]:
    """Imakefiles for ``name`` at or below ``root``, hidden trees aside."""
    found = []
    root = root.resolve()
    for directory, subdirectories, files in os.walk(root):
        depth = len(Path(directory).relative_to(root).parts)
        if depth >= SEARCH_DEPTH:
            subdirectories[:] = []
        subdirectories[:] = [d for d in subdirectories if not d.startswith(".")]
        if f"{name}.imake" in files:
            found.append(Path(directory) / f"{name}.imake")
    return sorted(found)


def find_unit(source: Path, name: Optional[str] = None) -> Unit:
    """Locate the unit to build.

    With a name, the unit's own directory and any source root above it both
    work: ``tig --build marsmesh`` finds ``marsmesh.imake`` in the current
    directory, or the one directory below it that holds it (say
    ``mars/src/prog/marsmesh``). Without a name, the current directory must
    hold exactly one imakefile.

    Args:
        source: Directory to resolve the unit from
        name: Unit name, if the user gave one

    Returns:
        The unit, with its kind and install location read from the imakefile
    """
    source = Path(source).resolve()
    if not source.is_dir():
        raise TigError(f"Not a directory: {source}")
    if name and not UNIT_NAME.match(name):
        raise TigError(f"{name!r} is not a valid VICAR unit name.")

    if name:
        candidates = [source / f"{name}.imake"]
        if not candidates[0].is_file():
            candidates = _imakefiles_below(source, name)
        if not candidates:
            raise TigError(
                f"No {name}.imake in {source} or below it. Copy the unit's "
                f"source here, or run from its source root."
            )
        if len(candidates) > 1:
            listed = "\n  ".join(str(c.parent) for c in candidates)
            raise TigError(
                f"{name}.imake found in more than one place; run from the one "
                f"you mean:\n  {listed}"
            )
        imakefile = candidates[0]
    else:
        candidates = sorted(source.glob("*.imake"))
        if not candidates:
            raise TigError(
                f"No *.imake file in {source}. Name the unit to search for it "
                f"below this directory, as in 'tig --build marsmesh'."
            )
        if len(candidates) > 1:
            listed = ", ".join(c.stem for c in candidates)
            raise TigError(
                f"{source} holds several imakefiles ({listed}); name the one "
                f"to build, as in 'tig --build {candidates[0].stem}'."
            )
        imakefile = candidates[0]

    unit_name, kind, subsystem = parse_imakefile(imakefile)
    if unit_name != imakefile.stem:
        raise TigError(
            f"{imakefile} defines {kind} {unit_name}, so it should be named "
            f"{unit_name}.imake."
        )
    if kind != "PROGRAM":
        raise TigError(
            f"{unit_name} is a {kind} unit, which builds into a VICAR link "
            f"library rather than an executable. tig --build installs programs; "
            f"a library change means rebuilding everything that links it."
        )
    if subsystem is None:
        raise TigError(
            f"{imakefile} names no install library (R2LIB, P3LIB or MARSLIB), "
            f"so where the program belongs in the image is unknown."
        )
    return Unit(unit_name, imakefile.parent, kind, subsystem)


def docker_output(args: List[str], timeout: float = DOCKER_TIMEOUT) -> Optional[str]:
    """Run a read-only docker command, returning its output, or None if it fails."""
    try:
        completed = subprocess.run(
            ["docker", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def image_exists(image: str) -> bool:
    return docker_output(["image", "inspect", "-f", "{{.Id}}", image]) is not None


def image_label(image: str, label: str) -> Optional[str]:
    """The value of one label on a local image, or None if it has none."""
    value = docker_output(
        ["image", "inspect", "-f", "{{index .Config.Labels " + f'"{label}"' + "}}", image]
    )
    if not value or value == "<no value>":
        return None
    return value


def state_root() -> Path:
    """Where injected-build state is kept, following XDG."""
    return build_state_root()


def names_dir() -> Path:
    """Markers naming the containers that carry the current recording."""
    return state_root() / BUILDS_NAMES_DIR


def invalidate_names() -> None:
    """Forget which containers carry the recording, after it changed.

    The warm path reads these, so dropping them is what makes an already
    running container take the full path and be patched again.
    """
    root = state_root()
    try:
        root.mkdir(parents=True, exist_ok=True)
        (root / BUILDS_RECORDED_FILE).touch()
    except OSError:
        pass
    shutil.rmtree(names_dir(), ignore_errors=True)


def mark_name_applied(container_name: str) -> None:
    """Record that a container carries the current recording."""
    try:
        names_dir().mkdir(parents=True, exist_ok=True)
        (names_dir() / container_name).touch()
    except OSError:
        pass


def object_dir(unit: Unit, builder_image: str) -> Path:
    """Private build directory for a unit: objects, makefile and executable.

    Keyed by source directory and builder image, and kept out of the user's
    source tree: make stays incremental across invocations without leaving
    object files behind, and a different builder never reuses its objects.
    """
    fingerprint = f"{unit.directory}\0{builder_image}".encode()
    digest = hashlib.sha256(fingerprint).hexdigest()[:12]
    return state_root() / "objects" / f"{unit.name}-{digest}"


def prepare_object_dir(unit: Unit, builder_image: str, builder_id: str) -> Path:
    """The unit's object directory, emptied when the builder image changed.

    The builder is a fixed tag rebuilt per VICAR release, so its objects are
    only incremental as long as the image behind that tag is the one that
    compiled them; make goes by timestamps and would link the old ones.
    """
    work = object_dir(unit, builder_image)
    stamp = work / BUILDER_STAMP
    try:
        previous = stamp.read_text().strip()
    except OSError:
        previous = ""
    if previous != builder_id:
        shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    try:
        stamp.write_text(builder_id)
    except OSError:
        pass
    return work


class Overrides:
    """The injected programs recorded for one runtime image.

    Keyed by image ID, so builds made against an image that has since been
    re-pulled are reported as stale rather than copied into an image they were
    never linked against.
    """

    def __init__(self, image_id: str, root: Optional[Path] = None):
        self.image_id = image_id
        short = image_id.replace("sha256:", "")[:12] or "unknown"
        self.directory = (root or state_root()) / short

    @property
    def manifest_path(self) -> Path:
        return self.directory / "manifest.json"

    @property
    def artifact_dir(self) -> Path:
        return self.directory / "bin"

    def load(self) -> Dict[str, dict]:
        try:
            data = json.loads(self.manifest_path.read_text())
        except (OSError, ValueError):
            return {}
        units = data.get("units")
        return units if isinstance(units, dict) else {}

    def save(self, units: Dict[str, dict]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        payload = {"image_id": self.image_id, "units": units}
        # Written whole and moved into place, so a crash cannot leave a
        # half-written manifest that would strand the artifacts.
        temporary = self.manifest_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True))
        temporary.replace(self.manifest_path)

    def record(self, unit: Unit, artifact: Path, pdf: Optional[Path]) -> None:
        """Store a copy of a built program, so a new container can be patched."""
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(artifact, self.artifact_dir / unit.name)
        if pdf is not None:
            shutil.copy2(pdf, self.artifact_dir / f"{unit.name}.pdf")

        units = self.load()
        units[unit.name] = {
            "path": unit.container_path,
            "lib_dir": unit.container_lib_dir,
            "pdf": pdf is not None,
            "source": str(unit.directory),
            "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        self.save(units)
        # A newer build must reach every container, including those already
        # patched with the previous one.
        shutil.rmtree(self.applied_dir, ignore_errors=True)
        invalidate_names()

    def forget(self, name: Optional[str] = None) -> List[str]:
        """Drop one override, or all of them; returns the names dropped."""
        units = self.load()
        dropped = [name] if name and name in units else ([] if name else list(units))
        for unit_name in dropped:
            units.pop(unit_name, None)
            for artifact in (unit_name, f"{unit_name}.pdf"):
                try:
                    (self.artifact_dir / artifact).unlink()
                except OSError:
                    pass
        self.save(units)
        if not units:
            shutil.rmtree(self.applied_dir, ignore_errors=True)
        if dropped:
            invalidate_names()
        return dropped

    @property
    def applied_dir(self) -> Path:
        return self.directory / "applied"

    def applied(self, container_id: str) -> bool:
        """Whether this container already carries the recorded overrides.

        Recorded by container ID: a container recreated under the same name is
        a different container, and needs patching again.
        """
        return (self.applied_dir / container_id).is_file()

    def mark_applied(self, container_id: str) -> None:
        try:
            self.applied_dir.mkdir(parents=True, exist_ok=True)
            (self.applied_dir / container_id).touch()
        except OSError:
            # Unwritable state: the overrides are simply re-applied next time.
            pass


def forget_stale(current_image_id: str) -> List[str]:
    """Delete the recorded state of every image other than the one in use."""
    current = Overrides(current_image_id).directory
    dropped = []
    try:
        directories = [d for d in state_root().iterdir() if d.is_dir()]
    except OSError:
        return dropped
    for directory in directories:
        if directory == current or directory.name == "objects":
            continue
        try:
            data = json.loads((directory / "manifest.json").read_text())
        except (OSError, ValueError):
            continue
        dropped.extend(sorted((data.get("units") or {})))
        shutil.rmtree(directory, ignore_errors=True)
    if dropped:
        invalidate_names()
    return dropped


def stale_units(current_image_id: str) -> List[str]:
    """Units recorded against an image other than the one in use.

    Re-pulling a moving tag such as :opensource leaves these behind: they were
    linked against libraries that image no longer has, so they are reported
    rather than installed.
    """
    current = Overrides(current_image_id).directory
    names = []
    try:
        directories = [d for d in state_root().iterdir() if d.is_dir()]
    except OSError:
        return names
    for directory in directories:
        if directory == current or directory.name == "objects":
            continue
        try:
            data = json.loads((directory / "manifest.json").read_text())
        except (OSError, ValueError):
            continue
        names.extend(sorted((data.get("units") or {})))
    return names


def copy_into_container(container: str, source: Path, destination: str) -> None:
    """Copy a host file into a container, replacing what is there."""
    try:
        completed = subprocess.run(
            ["docker", "cp", str(source), f"{container}:{destination}"],
            capture_output=True,
            text=True,
            timeout=DOCKER_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as e:
        raise TigError(f"Failed to copy {source.name} into {container}: {e}") from e
    if completed.returncode != 0:
        raise TigError(
            f"Failed to copy {source.name} into {container}: "
            f"{completed.stderr.strip() or completed.stdout.strip()}"
        )


# Gives a newly added program the same wrapper the image generates for its own
# programs, preferring a template from the same library directory and falling
# back to any VICAR wrapper (the image only wraps p2, mars, tae53 and gui).
_WRAPPER_SCRIPT = r"""set -e
unit="$1"; lib_dir="$2"; program="$3"
[ -e "$WRAPPER_DIR/$unit" ] && exit 0
template=$(grep -l "vicar-run $lib_dir/" "$WRAPPER_DIR"/* 2>/dev/null | head -1)
if [ -z "$template" ]; then
    template=$(grep -l "vicar-run /" "$WRAPPER_DIR"/* 2>/dev/null | head -1)
fi
[ -n "$template" ] || { echo "no VICAR wrapper to copy in $WRAPPER_DIR" >&2; exit 1; }
sed "s|vicar-run [^ ]*|vicar-run $program|" "$template" > "$WRAPPER_DIR/$unit"
chmod 755 "$WRAPPER_DIR/$unit"
""".replace("$WRAPPER_DIR", WRAPPER_DIR)


def ensure_wrapper(container: str, unit_name: str, lib_dir: str, program: str) -> None:
    """Make ``unit_name`` callable by name in the container.

    A program not in the image has no ``/usr/local/bin`` wrapper, so nothing -
    not ``tig <unit>``, not the shims, not another VICAR program - would find
    it without one.
    """
    try:
        completed = subprocess.run(
            [
                "docker", "exec", "--user", "0", container,
                "sh", "-c", _WRAPPER_SCRIPT, "wrapper",
                unit_name, lib_dir, program,
            ],
            capture_output=True,
            text=True,
            timeout=DOCKER_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as e:
        raise TigError(f"Failed to add a wrapper for {unit_name}: {e}") from e
    if completed.returncode != 0:
        raise TigError(
            f"Failed to add a wrapper for {unit_name}: "
            f"{completed.stderr.strip() or completed.stdout.strip()}"
        )


def apply_overrides(container_name: str, container_id: str, image_id: str) -> List[str]:
    """Re-install the recorded programs into a container.

    Called when a container is created or adopted: containers are reaped and
    replaced routinely, and an injected program lives in the container's
    filesystem, not the image.

    Returns:
        Names of the programs installed, empty when there are none or when
        this container already carries them.
    """
    overrides = Overrides(image_id)
    units = overrides.load()
    if not units or overrides.applied(container_id):
        mark_name_applied(container_name)
        return []

    installed = []
    for name, entry in sorted(units.items()):
        artifact = overrides.artifact_dir / name
        if not artifact.is_file():
            continue
        path = entry.get("path")
        lib_dir = entry.get("lib_dir")
        if not path or not lib_dir:
            continue
        copy_into_container(container_name, artifact, path)
        if entry.get("pdf"):
            pdf = overrides.artifact_dir / f"{name}.pdf"
            if pdf.is_file():
                copy_into_container(container_name, pdf, f"{path}.pdf")
        ensure_wrapper(container_name, name, lib_dir, path)
        installed.append(name)

    overrides.mark_applied(container_id)
    mark_name_applied(container_name)
    return installed


def verify_in_container(container: str, unit_name: str) -> Optional[str]:
    """Check a program installed in the runtime container actually loads.

    Only the runtime image can answer this: the builder has different shared
    libraries, so a program that runs there proves nothing. ``-help`` is
    rejected by TAE before any work is done, which is enough to get the
    dynamic loader and VICAR's startup to run.

    Returns:
        A description of the failure, or None if the program loaded.
    """
    try:
        completed = subprocess.run(
            ["docker", "exec", container, unit_name, "-help"],
            capture_output=True,
            text=True,
            timeout=VERIFY_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as e:
        return f"could not run it in the container: {e}"

    output = f"{completed.stdout}\n{completed.stderr}".strip()
    broken = (
        "error while loading shared libraries" in output
        or "command not found" in output
        or "No such file or directory" in output
        or completed.returncode in (126, 127)
    )
    if not broken:
        return None
    return output.splitlines()[-1] if output else f"exit {completed.returncode}"


def resolve_builder_image(configured: Optional[str], option: Optional[str] = None) -> str:
    """The builder image to compile in: option, environment, config, default."""
    if option:
        return option
    from_env = os.environ.get("TIG_BUILDER_IMAGE")
    if from_env:
        return from_env
    return configured or DEFAULT_BUILDER_IMAGE


def image_id(image: str) -> str:
    """The local ID of an image; raises if it has not been pulled."""
    identifier = docker_output(["image", "inspect", "-f", "{{.Id}}", image])
    if not identifier:
        raise TigError(
            f"Image {image} is not available locally, so there is nothing to "
            f"install into. Run any tig command once to pull it."
        )
    return identifier


def install(
    container: str, unit: Unit, artifact: Path, pdf: Optional[Path]
) -> None:
    """Install a built program into a running container, replacing the image's."""
    copy_into_container(container, artifact, unit.container_path)
    if pdf is not None:
        copy_into_container(container, pdf, f"{unit.container_path}.pdf")
    ensure_wrapper(container, unit.name, unit.container_lib_dir, unit.container_path)


class Builder:
    """Compiles one VICAR unit in the builder image."""

    def __init__(
        self,
        builder_image: str,
        runtime_image: str,
        force: bool = False,
        selinux_label_disable: bool = False,
    ):
        self.builder_image = builder_image
        self.runtime_image = runtime_image
        self.force = force
        self.selinux_label_disable = selinux_label_disable
        # Which image the tag resolves to, so objects are not reused across a
        # rebuild of it. Resolved by check_images.
        self.builder_id = ""

    def check_images(self) -> None:
        """Fail early on a missing builder image or a VICAR version mismatch."""
        if not image_exists(self.builder_image):
            raise TigError(
                f"Builder image {self.builder_image} is not available locally. "
                "It is not published - the VICAR release tarballs are yours to "
                "fetch - so build it once with:\n"
                "  terrain-intelligence-generator/build-builder-image.sh"
            )
        self.builder_id = (
            docker_output(["image", "inspect", "-f", "{{.Id}}", self.builder_image]) or ""
        )
        builder_version = image_label(self.builder_image, VICAR_VERSION_LABEL)
        runtime_version = image_label(self.runtime_image, VICAR_VERSION_LABEL)
        if not builder_version or not runtime_version:
            # An image predating the label; nothing to compare against.
            return
        if builder_version != runtime_version and not self.force:
            raise TigError(
                f"VICAR version mismatch: builder image is {builder_version}, "
                f"runtime image is {runtime_version}. A program built against "
                f"one release and run against another may not load. Rebuild the "
                f"builder image, or pass --build-force."
            )

    def build(self, unit: Unit, jobs: Optional[int] = None) -> Path:
        """Compile a unit, returning the executable on the host.

        The source directory is mounted read-only and the build happens in a
        tig-owned directory, so the user's source tree gains no object files.
        """
        work = prepare_object_dir(
            unit, self.builder_image, self.builder_id or self.builder_image
        )

        command = [
            "docker", "run", "--rm",
            "--platform", IMAGE_PLATFORM,
            "-v", f"{unit.directory}:/src:ro",
            "-v", f"{work}:/build",
            "-w", "/build",
            "-e", "HOME=/tmp",
        ]
        if jobs:
            command += ["-e", f"MAKEFLAGS=-j{jobs}"]
        if self.selinux_label_disable:
            # Without it, SELinux denies the source and object bind mounts.
            command += ["--security-opt", "label=disable"]
        if os.name == "posix" and sys.platform != "darwin":
            # Docker Desktop maps ownership; elsewhere the build would leave
            # root-owned objects and an unreadable executable behind.
            command += ["--user", f"{os.getuid()}:{os.getgid()}"]
        command += [self.builder_image, BUILDER_SCRIPT, unit.name, "/src", "/build"]

        try:
            completed = subprocess.run(command, check=False)
        except (OSError, subprocess.SubprocessError) as e:
            raise TigError(f"Failed to run the builder image: {e}") from e
        if completed.returncode != 0:
            raise TigError(
                f"Build of {unit.name} failed (exit {completed.returncode}); "
                f"build directory: {work}"
            )

        artifact = work / unit.name
        if not artifact.is_file():
            raise TigError(f"Build reported success but {artifact} is missing.")
        return artifact


def build_image(
    tag: str, base_image: str, unit: Unit, artifact: Path, pdf: Optional[Path]
) -> None:
    """Build a new image: the runtime image plus one layer holding the program."""
    with tempfile.TemporaryDirectory(prefix="tig-build-") as context:
        directory = Path(context)
        shutil.copy2(artifact, directory / unit.name)
        lines = [
            f"FROM {base_image}",
            f"COPY {unit.name} {unit.container_path}",
        ]
        if pdf is not None:
            shutil.copy2(pdf, directory / f"{unit.name}.pdf")
            lines.append(f"COPY {unit.name}.pdf {unit.container_path}.pdf")
        # A program the image does not already have needs its wrapper too, or
        # nothing in the image would find it by name.
        wrapper_script = f"tig-{unit.name}-wrapper.sh"
        (directory / wrapper_script).write_text(_WRAPPER_SCRIPT)
        lines += [
            f"COPY {wrapper_script} /tmp/{wrapper_script}",
            f"RUN sh /tmp/{wrapper_script} '{unit.name}' '{unit.container_lib_dir}' "
            f"'{unit.container_path}' && rm -f /tmp/{wrapper_script}",
        ]
        (directory / "Dockerfile").write_text("\n".join(lines) + "\n")

        try:
            completed = subprocess.run(
                [
                    "docker", "build",
                    "--platform", IMAGE_PLATFORM,
                    "-t", tag, str(directory),
                ],
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as e:
            raise TigError(f"Failed to build image {tag}: {e}") from e
        if completed.returncode != 0:
            raise TigError(f"Failed to build image {tag} (exit {completed.returncode}).")
