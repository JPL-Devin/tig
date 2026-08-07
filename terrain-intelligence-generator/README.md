# TIG VICAR Image

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
Dockerfile discovers the split parts by probing suffixes and streams them
through a single `tar`, so nothing about it is specific to M20.

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
