# TIG VICAR Image

The container image every TIG workflow runs in: VICAR built from the binaries
published on [NASA-AMMOS/VICAR](https://github.com/NASA-AMMOS/VICAR) releases,
plus the Java `vicario` converter.

```
docker/Dockerfile          Multi-stage build (builder + runtime)
docker/vicario.jar         Java VICAR→PNG/JPEG/TIFF converter (see docker/VICARIO.md)
builder/Dockerfile         Build environment for compiling VICAR units from source
builder/vicar-build        vimake + make for one unit, run inside the builder image
build-opensource-image.sh  Local build, mirrors CI
build-builder-image.sh     Local build of the builder image (not published)
test-docker-image.sh       Smoke tests, mirrors CI
test-release-regression.sh Release-only visual regression on real VISOR data
visor/Dockerfile           VISOR calibration variants, built FROM the base image
build-visor-image.sh       Local variant build, mirrors CI
test-visor-image.sh        Variant smoke tests, mirrors CI
```

## Using the published image

```bash
docker pull ghcr.io/nasa-ammos/tig/terrain-intelligence-generator:opensource
```

This is the default image for [`tig`](../tig-cli/README.md); no pull is needed,
tig fetches it on first use.

## Building locally

```bash
./build-opensource-image.sh                       # VICAR 5.0, tag :opensource
VICAR_VERSION=5.0 ./build-opensource-image.sh     # specific VICAR release
IMAGE_NAME=my-vicar IMAGE_TAG=dev ./build-opensource-image.sh
```

Then point tig at the result:

```bash
CONTAINER_IMAGE=my-vicar:dev tig gen test.vic 64 64
```

Build time is 5-10 minutes, mostly download.

## Testing

```bash
./test-docker-image.sh terrain-intelligence-generator:opensource
```

These are the same checks CI runs in
[`build-publish-terrain-intelligence-generator.yml`](../.github/workflows/build-publish-terrain-intelligence-generator.yml),
which builds, tests and publishes to GHCR on pushes to `main`/`develop` and on
version tags.

### Release visual regression

`test-release-regression.sh` is the release gate: it runs the shipped demo
pipelines on real MSL Navcam frames, asserts on the content of every product,
and writes a `report.md` with the generated PNGs embedded, so a release can be
signed off by looking at the imagery.

```bash
./test-release-regression.sh                        # published image, ~/visor_data
./test-release-regression.sh <image-tag> --download --out-dir ./rr
```

Data is the pinned public VICAR 5.0 VISOR sample data plus the MSL calibration
(740 MB + 380 MB compressed, 1.8 GB on disk); `--download` fetches both into the
data root, so it cannot be combined with a custom `--calibration`. Missing
data or calibration is a hard failure carrying the exact `curl` command to fix
it, never a silent skip. Runtime is ~7 minutes, ~6 of them the full-frame
`marscorr`.

| Stage | Pipeline | Assertion |
| --- | --- | --- |
| `radiometric-correction` | `marsrad` frames from the mosaic run | one corrected frame per input, DN scale changed, contrast retained |
| `cylindrical-mosaic` | `demo-panorama-mosaic.sh --auto-extent` | mosaic dimensions and a non-degenerate histogram |
| `polar-mosaic` | `demo-panorama-mosaic.sh --projection polar` | square projection above a size floor, non-degenerate histogram |
| `stereo-xyz` | `marscorr` -> `marscor3` -> `marsxyz` | 2-band disparity with real spread, valid XYZ point count, 3-band cloud |
| `mesh` | `demo-mesh-generation-with-xyz.sh --xyz` | vertex and face counts, every coordinate finite and within 1e6 m |
| `surface-characteristics` | `demo-surface-characteristics.sh` | products share the XYZ grid, slope in degrees, roughness a small height that actually varies (a raster of the 0.1 m "could not compute" fill fails), both normals fields written |
| `co-registration` | `demo-co-registration.sh` plus a difference raster | tiepoints kept, mean pixel error improved and under tolerance, non-zero difference raster |

Bounds are floors and ranges taken from an observed run and commented with the
value seen, not golden images: VICAR residuals do not reproduce bit for bit
between runs, so an exact-match comparison would be flaky.

Not demonstrated by the suite, and reported as UNVERIFIED in its report rather
than omitted:

- **change-monitoring** — needs `demo-change-monitoring.sh`; the stage runs
  automatically once that demo is in the tree.
- **the demos' built-in stereo path** — `demo-mesh-generation-with-xyz.sh
  --stereo-left/--stereo-right` relies on `marscorr`'s default seed, which
  reports "No valid seed points found" on these Navcam frames. The suite seeds
  the frame centre itself and feeds the resulting cloud to the demo's `--xyz`
  path.
- **M20 frames** — the suite is MSL-only, because M20 calibration is 2.7 GB
  across two release assets.

CI runs it from
[`release-visual-regression.yml`](../.github/workflows/release-visual-regression.yml)
on published releases and on `workflow_dispatch` only — never on pull requests
or ordinary pushes — and uploads `report.md` and every PNG as an artifact. On a
release it tests the image tagged with that release's version, warning and
falling back to `:opensource` when the release published no image.

## What is in the image

- VICAR core (p2 programs), TAE, MARS terrain tools and supporting libraries
- ~540 command wrappers in `/usr/local/bin`
- Oracle Linux 8, Python 3.9, Java 8 runtime, X11 libraries, libtiff/libpng/libjpeg

Environment set by the image:

| Variable | Value |
| --- | --- |
| `V2TOP` | `/usr/local/vicar/dev` |
| `WORKSPACE` | `/usr/local/vicar` |
| `VICSYS` | `DEVELOPMENT` |
| `LD_LIBRARY_PATH`, `PATH` | VICAR and externals directories |

## The builder image

The runtime image is optimized for running VICAR, not building it: it has no
compilers, and the pruning that halves its size removes the headers, the
imakefiles, the program sources and the external link archives. Compiling a
VICAR program from source therefore happens in a second image, which
[`tig --build`](../docs/demos/building-from-source.md) uses:

```bash
./build-builder-image.sh          # ~7GB, 10-20 minutes, tag :opensource-builder
```

Same VICAR release, nothing pruned, plus GCC/G++/GFortran, make, imake, tcsh and
the X11, Motif, ncurses and TBB development packages VICAR's MARS programs link
against. It is deliberately not published, so the release tarballs are only ever
fetched by whoever builds it. Both images carry an
`org.nasa.tig.vicar-version` label, and `tig --build` refuses to install a
program built against a different release than the one it runs on.

Standalone, without tig:

```bash
docker run --rm -v "$PWD:/build" terrain-intelligence-generator:opensource-builder \
    vicar-build marsmesh          # vimake + make for one unit
```

## Build arguments

The Dockerfile takes `VICAR_VERSION` (the release to download) plus the two
tarball names it fetches from it, `BINARIES_FILE` and `EXTERNAL_FILE`.
Both tarballs are assets of that one release, so `build-opensource-image.sh`
derives their names from it and setting `VICAR_VERSION` alone is enough:

| Variable | Default | Meaning |
| --- | --- | --- |
| `VICAR_VERSION` | `5.0` | Release to download |
| `BINARIES_FILE` | `vicar_open_bin_x86-64-linx_$VICAR_VERSION.tar.gz` | Binaries tarball name |
| `EXTERNAL_FILE` | `vicar_open_ext_x86-64-linx_$VICAR_VERSION.tar.gz` | Externals tarball name |

## Image tags

On `ghcr.io/nasa-ammos/tig/terrain-intelligence-generator`:

- `:opensource` — latest open-source build from `main`
- `:latest` — latest stable release
- `:main`, `:develop` — branch builds
- `:v*` — version tags
- `:visor-<mission>` — base image plus one mission's VISOR calibration (below)

## Troubleshooting

The image is `linux/amd64`; tig always requests that platform, so it runs under
emulation on Apple Silicon.

**A build download fails.** Check the version exists in
[VICAR releases](https://github.com/NASA-AMMOS/VICAR/releases) and pass a
matching `VICAR_VERSION`; override `BINARIES_FILE`/`EXTERNAL_FILE` if a release
names its assets differently.

**Out of disk space during build.** `docker system prune -a`, then rebuild.

**A command is missing.** `tig bash -c 'ls /usr/local/bin | grep <name>'`.

**Library errors (`libXXX.so not found`).** `tig bash -c 'echo $LD_LIBRARY_PATH'`
and `tig ldconfig -p | grep <library>`.

**X11 tools do not display.** tig handles `xhost`/socket setup; see the
[X11 notes](../tig-cli/README.md#gui-tools-and-x11).

## VISOR calibration variants

MARS tools (`marsrad`, `marsmap`, `marsxyz`, `marsmesh`, ...) need camera
models, flat fields and parameter files. The base `:opensource` image
deliberately does not bundle them: they are gigabytes per mission and most of
that is the wrong mission for any given user.

The `:visor-<mission>` variants bundle one mission's VISOR calibration, so
MARS tools work out of the box with nothing downloaded and nothing mounted:

```bash
CONTAINER_IMAGE=ghcr.io/nasa-ammos/tig/terrain-intelligence-generator:visor-msl \
  tig marsrad NLB_712299404EDR_F0961766NCAM00353M1.IMG out.RAD.IMG
```

| Tag | Mission | Image size |
| --- | --- | --- |
| `:opensource` | none - mount your own | 3.12 GB |
| `:visor-nsyt` | InSight | 3.29 GB |
| `:visor-msam` | MSAM | 3.66 GB |
| `:visor-msl` | Mars Science Laboratory / Curiosity | 3.68 GB |
| `:visor-phx` | Phoenix | 3.78 GB |
| `:visor-mer` | MER / Spirit and Opportunity | 4.16 GB |
| `:visor-m20` | Mars 2020 / Perseverance | 8.75 GB |

Use the base image when the work is not mission-specific, when the
calibration you need is not what VISOR publishes, or when you already keep
calibration on the host. Otherwise use the variant for your mission; nobody
needs six missions at once, and M20 alone is over half the total bulk.

Sample data (`visor_sample_data_20230623.tar.gz`) is not bundled in any
variant - it is inputs, not calibration. See
[Downloading VISOR data](../docs/demos/downloading-visor-data.md).

The bundled calibration comes from the VICAR 5.0 release of
[NASA-AMMOS/VICAR](https://github.com/NASA-AMMOS/VICAR), which is Apache-2.0,
and is treated as open source on the same terms as VICAR itself.

### Building a variant locally

```bash
./build-visor-image.sh nsyt                       # smallest, good for a smoke test
./build-visor-image.sh m20                        # largest
./build-visor-image.sh "m20 mer msl msam phx nsyt" tig:visor-all
```

The variants are built `FROM` the published base image
([`visor/Dockerfile`](visor/Dockerfile)) rather than rebuilding VICAR, so a
variant build is a download and an extraction - minutes, not the hour a VICAR
build costs. It sits outside `docker/` because the base image workflow
rebuilds on any change under that directory.

`VISOR_MISSIONS` is a space-separated build argument, which is why an
all-missions image needs no separate definition.

M20 calibration is published as two GitHub release assets
(`...tar.gzaa`, `...tar.gzab`) because a release asset cannot exceed 2GB. The
Dockerfile discovers the split parts by probing suffixes and concatenates them
into a single `tar`, so nothing about it is specific to M20.

Every asset is verified against a pinned SHA-256 in
[`visor/calibration.sha256`](visor/calibration.sha256) before extraction, and
an asset missing from that file fails the build rather than being bundled
unverified. After changing `VICAR_VERSION` or the calibration date stamp,
regenerate it with `visor/update-calibration-checksums.sh`.

### Testing a variant

```bash
./test-visor-image.sh terrain-intelligence-generator:visor-msl
```

Asserts calibration is bundled for every mission in `VISOR_MISSIONS`, that
`MARS_CONFIG_PATH` covers them, that no compressed archives survived into the
image, and that a calibration mount cleanly overrides the bundled data. For
the `msl` variant it also runs `marsrad` on a real MSL Navcam EDR and checks
the camera model came from the bundled calibration - the VISOR sample data
covers no other mission, so that step reports itself as skipped elsewhere.

These are the same checks CI runs in
[`build-publish-visor-variants.yml`](../.github/workflows/build-publish-visor-variants.yml).

The base image's own suite asserts the opposite - that VISOR data is *not*
bundled - and is unaffected by any of this.

### Overriding the bundled calibration

A user-supplied calibration path is mounted read-only at
`/usr/local/vicar/mars_calib`, the same directory the variants populate, so
the mount hides the bundled data completely rather than merging with it. The
bundled data lives in a per-mission subdirectory
(`/usr/local/vicar/mars_calib/msl/...`) while a mounted directory holds one
mission's files directly; `MARS_CONFIG_PATH` lists the mount point first and
each mission directory after it, and MARS `CONFIG_PATH` entries that do not
exist are skipped. Both layouts therefore resolve, with the mount winning.

## License

VICAR is licensed under Apache 2.0 by
[NASA-AMMOS/VICAR](https://github.com/NASA-AMMOS/VICAR).
