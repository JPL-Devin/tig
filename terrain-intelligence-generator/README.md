# TIG VICAR Image

The container image every TIG workflow runs in: VICAR built from the binaries
published on [NASA-AMMOS/VICAR](https://github.com/NASA-AMMOS/VICAR) releases,
plus the Java `vicario` converter.

```
docker/Dockerfile          Multi-stage build (builder + runtime)
docker/vicario.jar         Java VICAR→PNG/JPEG/TIFF converter (see docker/VICARIO.md)
build-opensource-image.sh  Local build, mirrors CI
test-docker-image.sh       Smoke tests, mirrors CI
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

## Build arguments

The Dockerfile takes `VICAR_VERSION` (the release to download) plus the two
tarball names it fetches from it, `BINARIES_FILE` and `EXTERNAL_FILE`.
`build-opensource-image.sh` derives the tarball names from the versions, so
setting `VICAR_VERSION` alone is enough:

| Variable | Default | Meaning |
| --- | --- | --- |
| `VICAR_VERSION` | `5.0` | Release to download |
| `EXTERNAL_VERSION` | `$VICAR_VERSION` | Version in the externals tarball name |
| `BINARIES_FILE` | `vicar_open_bin_x86-64-linx_$VICAR_VERSION.tar.gz` | Binaries tarball name |
| `EXTERNAL_FILE` | `vicar_open_ext_x86-64-linx_$EXTERNAL_VERSION.tar.gz` | Externals tarball name |

## Image tags

On `ghcr.io/nasa-ammos/tig/terrain-intelligence-generator`:

- `:opensource` — latest open-source build from `main`
- `:latest` — latest stable release
- `:main`, `:develop` — branch builds
- `:v*` — version tags

## Troubleshooting

The image is `linux/amd64`; tig always requests that platform, so it runs under
emulation on Apple Silicon.

**A build download fails.** Check the version exists in
[VICAR releases](https://github.com/NASA-AMMOS/VICAR/releases) and pass matching
`VICAR_VERSION`/`EXTERNAL_VERSION`.

**Out of disk space during build.** `docker system prune -a`, then rebuild.

**A command is missing.** `tig bash -c 'ls /usr/local/bin | grep <name>'`.

**Library errors (`libXXX.so not found`).** `tig bash -c 'echo $LD_LIBRARY_PATH'`
and `tig ldconfig -p | grep <library>`.

**X11 tools do not display.** tig handles `xhost`/socket setup; see the
[X11 notes](../tig-cli/README.md#gui-tools-and-x11).

## License

VICAR is licensed under Apache 2.0 by
[NASA-AMMOS/VICAR](https://github.com/NASA-AMMOS/VICAR).
