# Mounting MARS Calibration Data

MARS tools (`marsmap`, `marsmos`, `marsxyz`, `marsmesh`, ...) need camera models,
flat fields and parameter files. `tig` mounts a host directory of these into the
container read-only and points the tools at it.

## Quick start

```bash
export MARS_CONFIG_PATH="${HOME}/.mars_calib"
tig marsmesh inp=pointcloud.xyz out=terrain.obj in_skin=texture.img
```

Equivalently, per invocation:

```bash
tig --calibration-path ~/.mars_calib marsmesh ...
```

or persistently, in any of the [config files](../../tig-cli/README.md#configuration):

```toml
calibration_path = "~/.mars_calib"
```

Precedence is `--calibration-path`, then `MARS_CONFIG_PATH`, then `calibration_path`
from the config files.

## Verifying the mount

```bash
tig bash -c 'ls $MARS_CONFIG_PATH'
tig bash -c 'find $MARS_CONFIG_PATH/camera_models -name "*.cahv*" | wc -l'
```

`tig --help` prints the calibration path currently in effect.

## Directory structure

```
mars_calibration/
├── camera_models/
│   ├── *.cahvor     # Camera geometry files
│   └── *.cahvore    # Extended camera models
├── flat_fields/
│   └── *.IMG        # Radiometric correction data
└── param_files/
    └── *.xml        # Camera mapping configuration
```

`find-calibration.sh` in the repository root locates such a directory in the
common places and validates its contents.

## Mount behaviour

| Host path | Container path | Mode |
| --- | --- | --- |
| the resolved calibration path | `/usr/local/vicar/mars_calib` | read-only |

Inside the container `MARS_CONFIG_PATH` is set to `/usr/local/vicar/mars_calib`,
which is what the MARS tools read.

A calibration path that is not a directory is an error rather than a silent
skip, so a typo fails immediately instead of surfacing as a missing camera model
much later. With no calibration configured at all, tig mounts nothing; that is
the right configuration for non-MARS work.

Changing the calibration path changes the container's mount set, so tig starts a
separate container for it rather than reusing the old one. `tig --status` lists
what is running.

## Images with calibration already in them

The `:visor-<mission>` variants of the Terrain Intelligence Generator image
bundle one mission's VISOR calibration, so MARS tools run with no calibration
path configured and nothing downloaded:

```bash
CONTAINER_IMAGE=ghcr.io/nasa-ammos/tig/terrain-intelligence-generator:visor-msl \
  tig marsrad NLB_712299404EDR_F0961766NCAM00353M1.IMG out.RAD.IMG
```

Available as `:visor-m20`, `:visor-mer`, `:visor-msl`, `:visor-msam`,
`:visor-phx` and `:visor-nsyt`; sizes and build instructions are in the
[image README](../../terrain-intelligence-generator/README.md#visor-calibration-variants).

Reach for a variant when you work on one mission and want the calibration to
be someone else's problem. Keep mounting a host directory when you have
calibration VISOR does not publish, when you need several missions at once
without carrying an all-missions image, or when you want the base image's
3.12GB rather than a variant's 3.3-8.8GB.

## Mounted calibration always wins

A calibration path supplied to `tig` is mounted read-only at
`/usr/local/vicar/mars_calib` - exactly where a variant puts its bundled
calibration - so on a variant image the mount hides the bundled data
completely instead of merging with it. Nothing needs to be done to "turn off"
the bundled calibration: mounting over it is enough, and `tig` also sets
`MARS_CONFIG_PATH` to the mount point so the tools look there first.

The two layouts differ, and both work:

| | Layout under `/usr/local/vicar/mars_calib` |
| --- | --- |
| mounted | `camera_models/`, `param_files/`, ... directly |
| bundled | `<mission>/camera_models/`, `<mission>/param_files/`, ... |

MARS `CONFIG_PATH` is a colon-separated search list whose entries need not
exist, so the variant images set `MARS_CONFIG_PATH` to the mount point
followed by every mission directory. A mount is found via the first entry; a
bundled mission via its own.

## Getting the data

VISOR publishes calibration for M20, MSL, MER and other missions; see
[Downloading VISOR data](../demos/downloading-visor-data.md).
