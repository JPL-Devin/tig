# `tig build`: compile VICAR programs from local source into the running container

**Date:** 2026-08-11
**Status:** Proposed (design only; nothing implemented)

## Summary

Add a `tig build` command that compiles one VICAR program unit from the source
in the current directory and then makes the result the program TIG runs — by
default injected into the warm container, optionally baked into a new image with
one layer replacing the binary. Compilation happens in a separate, pinned
*builder* image, because the runtime image cannot compile anything.

That turns TIG from a consume-only environment into a self-contained VICAR dev
loop: edit `marsmesh.cc` on the host, `tig build`, `tig marsmesh ...`, with no
native VICAR install.

## Motivation

Today the only VICAR binaries available are the ones the published image was
built with. Changing a MARS program means installing VICAR natively (the reason
this repository exists is that this is painful), or rebuilding the whole image.
There is no way to test a local patch against the demos, the Airflow example or
CI.

## Why the runtime image cannot compile

Measured against `ghcr.io/nasa-ammos/tig/terrain-intelligence-generator:opensource`:

| Needed to build | State in the runtime image |
| --- | --- |
| `gcc`, `g++`, `gfortran`, `make`, `imake` | none installed |
| Headers (`$V2INC` = `$V2TOP/rtl/inc`, `p2/inc`, `mars/inc`, externals) | `find $V2TOP -name '*.h'` returns 0 files |
| External link archives | `*.a` deleted from `$WORKSPACE/external` |
| Program sources | `p2/prog`, `p3/prog`, `mars/src` deleted |
| `*.imake` build descriptions | deleted |
| VICAR link archives (`$V2TOP/olb/x86-64-linx/*.a`) | **present** (24 files) |
| imake templates (`$V2UTIL/imake_unix.tmpl`, `config/ms_config.x86-64-linx`) | **present** |

This is deliberate — that pruning is what takes the image from ~8GB to ~3GB —
so `tig build` needs a second image rather than an addition to this one.

## How VICAR builds a program

VICAR uses `vimake`, a thin wrapper over `imake` (see
[Building and Delivering VICAR Applications](https://nasa-ammos.github.io/VICAR-DOCS/buildapps/)):

```
vimake <unit>              # imake -I$V2INC -I$V2UTIL -T$V2UTIL/imake_unix.tmpl \
                           #       -f <unit>.imake -s <unit>.make
make -f <unit>.make std    # 'std' = private build in the current directory
```

Four facts shape the design:

- `vimake` is a **csh alias** defined by `$V2TOP/vicset2.csh`, and it only
  appears if `$V2INC` is set by `$V2TOP/vicset1.csh` first. It does not exist in
  bash, and because csh expands aliases when it parses a line, it cannot be
  defined and used in the same `csh -c` string. The in-container build step must
  therefore be a small tcsh **script** that sources both files and then calls
  `vimake`/`make`.
- A unit is described entirely by `<unit>.imake`. `#define PROGRAM <name>` gives
  the name, and one of `R2LIB` / `MARSLIB` / `P3LIB` gives the destination
  directory — `gen.imake` says `R2LIB` (installs in `p2/lib/$VICCPU`),
  `marsmesh.imake` says `MARSLIB` (`mars/lib/$VICCPU`). `tig build` reads the
  imakefile for both rather than guessing.
- `<unit>.pdf`, the TAE parameter definition, lives **next to the binary** in
  the image (508 of them in `p2/lib/x86-64-linx`). A patch that changes the
  program's parameters is only complete if the `.pdf` is installed too.
- `make ... std` leaves the binary, `<unit>.make` and the `.o` files in the
  build directory, exactly as a native VICAR build would.

## Builder image

New `terrain-intelligence-generator/docker/Dockerfile.builder`, published as
`…/terrain-intelligence-generator:opensource-builder`, built from the *same*
`VICAR_VERSION` release tarballs as the runtime image but without the pruning,
plus a toolchain. The dev packages below are not a guess; they are what a
`marsmesh` link actually needed, found by iterating link failures:

```dockerfile
FROM oraclelinux:8
RUN dnf install -y oracle-epel-release-el8; \
    dnf config-manager --set-enabled ol8_codeready_builder; \
    dnf install -y gcc gcc-c++ gcc-gfortran make imake perl tcsh tar gzip \
      findutils which diffutils \
      libX11-devel libXt-devel libXext-devel libXmu-devel libXp-devel \
      motif-devel ncurses-devel tbb-devel \
      libjpeg-turbo-devel libpng-devel libtiff-devel zlib-devel \
      tcl-devel tk-devel && dnf clean all
# then: unpack vicar_open_bin_* into $V2TOP and vicar_open_ext_* into
# $WORKSPACE/external, deleting nothing
```

- `X11/X.h` is reached through `$V2TOP/crumbs/dev/include/image/*.h`, so the
  X11 and Motif headers are needed even for a headless program like `marsmesh`.
- `tbb-devel` is needed because `libembree3.so` references TBB symbols.
- The bin tarball ships the sources, so no extra download is required: the
  builder image has `$V2TOP/mars/src/prog/marsmesh/` and friends, which also
  gives users a reference tree to copy a unit from.
- Size is ~7GB. It is a separate tag pulled only on first `tig build`, so users
  who never build never pay for it.
- **Pinning matters**: the artifact links against the runtime image's own
  `librtl.a`/`libmarssub.a` and external shared objects, so builder and runtime
  must come from one VICAR release. Proposal: add an
  `org.nasa.tig.vicar-version` label to both images and have `tig build` refuse
  a mismatch (`--force` to override).

## The command

```
tig build [UNIT] [--source DIR] [--image TAG] [--builder IMAGE]
          [--jobs N] [--list] [--clean [UNIT]] [--force]
```

- **Unit resolution**: with no `UNIT`, the single `*.imake` in the source
  directory; error listing candidates if there are several.
- **Source directory**: the current directory by default, `--source DIR`
  otherwise; mounted read-write into the builder container at `/work`.
- **Ownership**: the builder container runs with `--user $(id -u):$(id -g)`.
  Without it the `.o`, `.make` and binary land root-owned in the user's source
  tree, which the spike hit immediately.
- **Output**: the build streams through, and the compile exit status is
  propagated verbatim (this is `make`, not a VICAR program, so the exit-code
  translation in `vicar-run` does not apply).

### Injection: default mode

`docker cp` the binary — and `<unit>.pdf` when present — over the real path in
the running container:

```
$V2TOP/{p2,p3,mars}/lib/x86-64-linx/<unit>
```

Overriding the *path* rather than `PATH` is deliberate. The wrappers in
`/usr/local/bin` `exec` an absolute path, each one resets `PATH` for its
children, and VICAR programs invoke other VICAR programs — so a
`PATH`-prepending override would apply to the command the user typed and not to
anything it calls.

Injection into a running container is immediate (no restart, warm container
preserved), but containers are disposable: the name encodes the mount
configuration, `_reap_containers` removes surplus ones, `--shutdown` removes all
of them, and a re-pulled `:opensource` forces a replacement. So the override has
to be recorded host-side and re-applied:

```
~/.local/share/tig/builds/<image-id>/
├── manifest.json        # unit -> {container path, sha256, source dir, built at}
└── bin/<unit>[.pdf]     # the artifact, so a new container can be patched
```

`ContainerManager.ensure_container` re-applies the manifest after creating or
adopting a container. The manifest is keyed by image ID: after a new
`:opensource` is pulled, overrides built against the old one are held back and
reported as stale rather than silently copied into an image they were not linked
against.

- `tig build --list` shows the active overrides, their units and staleness.
- `tig build --clean [UNIT]` drops them and restores the image's own binary,
  which means recreating the container (the file in the layer is unrecoverable
  once overwritten).
- **A unit not already in the image** additionally needs a
  `/usr/local/bin/<unit>` wrapper, generated the same way the Dockerfile
  generates them (env exports + `exec /usr/local/libexec/vicar-run <path>`), so
  that `tig <unit>`, `list_tools` and `tig --shim` see it.

### Injection: image mode

`--image TAG` writes a two-line Dockerfile and builds it:

```dockerfile
FROM ghcr.io/nasa-ammos/tig/terrain-intelligence-generator:opensource
COPY <unit> /usr/local/vicar/dev/mars/lib/x86-64-linx/<unit>
```

One layer, no rebuild of VICAR, seconds to build. This is the shareable,
reproducible form: `CONTAINER_IMAGE=TAG tig marsmesh …`, or the image a CI job
or the Airflow example pulls.

## Feasibility: what was actually verified

A spike on this box exercised the whole path (evidence, not projection):

1. Builder image built as above from the VICAR 5.0 tarballs.
2. `gen.c` patched (`GEN Version 2019-05-28` → `GEN Version LOCAL-DEV-SPIKE`),
   then `vimake gen && make -f gen.make std` produced a binary in seconds.
3. `docker cp` of that binary over
   `$V2TOP/p2/lib/x86-64-linx/gen` in a running container, then `gen out=t.vic
   nl=4 ns=4` through the normal wrapper printed:
   `Beginning VICAR task GEN` / `GEN Version LOCAL-DEV-SPIKE` / `GEN task completed`.
4. `marsmesh` — C++, `crumbs`, embree, Motif, PDS, xerces, OpenMP — built from
   `$V2TOP/mars/src/prog/marsmesh` and, once injected, started correctly under
   the runtime image (`[TAE-MISPAR] Missing parameter: 'INP'`, i.e. the program
   ran and parsed parameters).
5. Image mode (`FROM :opensource` + `COPY gen …`) reproduced the same patched
   behaviour in a fresh container.

## Code changes

| File | Change |
| --- | --- |
| `tig-cli/src/tig_cli/cli.py` | Dispatch `build` before tool execution |
| `tig-cli/src/tig_cli/build.py` | New: unit discovery, imakefile parsing, builder container run, injection, manifest |
| `tig-cli/src/tig_cli/container.py` | Re-apply overrides in `ensure_container`; wrapper generation for new units |
| `tig-cli/src/tig_cli/spec.py` | Builder image default, override/manifest paths |
| `terrain-intelligence-generator/docker/Dockerfile.builder` | New |
| `terrain-intelligence-generator/docker/vicar-build` | New: tcsh script (`source vicset1/vicset2; vimake; make std`) |
| `.github/workflows/build-publish-terrain-intelligence-generator.yml` | Build and publish `:opensource-builder`; label both images with the VICAR version |
| `docs/` | `docs/demos/building-from-source.md`, README component list |

Tests: unit tests for imakefile parsing, unit resolution and manifest handling;
a docker-gated integration test that patches `gen`, builds, injects and asserts
the patched message — the spike above, as a test.

`build` does not collide with anything: none of the 546 commands in
`/usr/local/bin` is named `build`. Since the first positional argument is
otherwise a tool name, the design still needs an escape hatch decision (see open
questions).

## Limits and non-goals

- **PROGRAM units only.** `SUBROUTINE` units build into link libraries
  (`libmarssub.a`), and changing one means relinking every program that uses it.
  Out of scope for v1; `tig build` should detect a non-PROGRAM imakefile and say
  so rather than produce something useless.
- **One unit per invocation.** No dependency graph across units.
- **x86-64 only**, matching the image platform; on Apple Silicon the build runs
  under emulation and will be slow.
- **Overrides are local and mutable.** A container carrying an injected binary
  is no longer the published image; `tig --status` and `tig build --list` are the
  only record. Anything shared or reproducible should use `--image`.
- The builder image does **not** rebuild VICAR itself, only units against the
  released libraries. Changing a released library is a full VICAR build, which
  belongs upstream.

## Phasing

1. Builder image + `vicar-build` script + CI publish + version labels + docs.
   Useful on its own: `docker run … tig-builder` already gives a working build
   environment.
2. `tig build` with default injection, manifest and re-apply; `--list`,
   `--clean`.
3. `--image` mode, wrapper generation for new units, integration test, a demo
   doc that patches a MARS program and re-runs the mesh demo against it.

## Open questions

1. **Subcommand or flag?** `tig build` reads best and is what was asked for, but
   every other lifecycle operation is a flag (`--shim`, `--status`,
   `--shutdown`) and the first positional is otherwise a VICAR tool name. If
   `build` is a subcommand, do we add `tig run <tool>` (or `tig -- <tool>`) as
   the escape hatch for a future VICAR program called `build`?
2. **Publish the ~7GB builder image**, or ship only the Dockerfile plus a
   `build-builder-image.sh` and let users build it once locally?
3. **Copy-per-container overrides or a mounted overlay directory?** A mount
   would survive container recreation for free, but it changes the container's
   mount configuration — and therefore its name — so every first build in a
   directory would replace the warm container. The copy-based design keeps the
   warm container and pays with a re-apply step.
4. **Cache the `.o` files** in a tig-owned directory instead of the user's
   source tree, so a build does not litter the source directory?
5. Should `tig build` verify the artifact (run `<unit>` with no arguments and
   check it reaches TAE parameter parsing) before recording the override?
