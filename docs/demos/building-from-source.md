# Building a VICAR Program From Source

A worked example of `tig --build`: patch a VICAR program on your machine,
compile it, and run the patched program through tig — no native VICAR install.

## Prerequisites

- tig working (`tig gen test.vic 64 64` produces an image)
- The builder image, built once per machine:

  ```bash
  terrain-intelligence-generator/build-builder-image.sh
  ```

  It is the same VICAR release as the runtime image with nothing pruned, plus
  compilers, and takes 10-20 minutes and ~7GB. It is deliberately not published:
  the VICAR release tarballs it unpacks are yours to fetch. Its VICAR version is
  checked against the runtime image's on every build.

## 1. Get a unit's source

A VICAR program is one directory: `<unit>.imake`, the sources it lists, and
usually `<unit>.pdf`. The builder image carries the release's own sources, which
is the easiest place to start from:

```bash
mkdir -p ~/vicar-work && cd ~/vicar-work
docker run --rm -v "$PWD:/out" terrain-intelligence-generator:opensource-builder \
    bash -c 'cp -r $V2TOP/p2/prog/gen /out/ && chmod -R u+w /out/gen'
cd gen && ls
# gen.c  gen.imake  gen.pdf  test/
```

The imakefile says what is built and where it belongs:

```
#define PROGRAM gen
#define MODULE_LIST gen.c
#define R2LIB          /* installs in p2/lib/x86-64-linx */
```

## 2. Patch it

```bash
sed -i 's/GEN Version 2019-05-28/GEN Version MY-PATCH/' gen.c
```

## 3. Build

```bash
tig --build
```

tig compiles the unit in the builder image (`vimake gen`, then
`make -f gen.make std`), copies the program over the image's own inside the
running container, and checks it loads there:

```
Building gen from /home/you/vicar-work/gen in terrain-intelligence-generator:opensource-builder
gcc -O -I/usr/local/vicar/dev/rtl/inc ... -c gen.c
gcc -O ... -o gen gen.o -L/usr/local/vicar/dev/olb/x86-64-linx -lrtl -lm
Installed /usr/local/vicar/dev/p2/lib/x86-64-linx/gen (+ .pdf) in tig-vicar-0559e7ba3d72
Run it with: tig gen ...
```

Your source directory is mounted read-only and the objects live under
`~/.local/share/tig/builds`, so nothing is written next to your source and the
next build is incremental.

From a source root rather than the unit's own directory, name the unit and tig
finds it below:

```bash
cd ~/vicar-work && tig --build gen
```

## 4. Run it

```bash
tig gen out=patched.vic nl=4 ns=4
# Beginning VICAR task GEN
# GEN Version MY-PATCH
# GEN task completed
```

A program the image did not have works the same way: tig also writes the
`/usr/local/bin` wrapper, so `tig <unit>` and the `--shim` commands find it.

## 5. Keep track, and undo

Installed programs live in the container rather than the image, and tig replaces
containers when they are reaped, when `--shutdown` runs, or when the image
moves. Each build is recorded, so a fresh container is patched again on the next
invocation:

```
tig: re-applied locally built program(s): gen
```

```bash
tig --build-list          # what is installed over the image, and from where
tig --build-clean         # forget them; removes those containers, restoring the image's programs
tig --build-clean --build-unit gen    # just one
```

## 6. Share it: an image instead of a container

Injection is local and mutable. For CI, an Airflow DAG or a colleague, build an
image — the runtime image plus one layer holding the program:

```bash
tig --build-image my-vicar:gen-patch
CONTAINER_IMAGE=my-vicar:gen-patch tig gen out=patched.vic nl=4 ns=4
```

## Notes and limits

- **`PROGRAM` units only.** A `SUBROUTINE` unit builds into a VICAR link
  library (`libmarssub.a`), and changing one means rebuilding every program that
  links it; tig refuses those rather than producing something unusable.
- **One unit per build.** No dependency graph across units.
- **x86-64.** The images are `linux/amd64`; on Apple Silicon both the build and
  the run go through emulation.
- **`<unit>.pdf` matters.** It is the TAE parameter definition and lives beside
  the binary, so a change to a program's parameters is only installed if the
  `.pdf` is in the source directory too.
- **Missing dependency?** A link failure naming a library the builder lacks is a
  builder-image gap; add the `-devel` package to
  `terrain-intelligence-generator/docker/Dockerfile.builder` and rebuild.
