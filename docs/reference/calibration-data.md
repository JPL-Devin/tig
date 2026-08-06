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

## Getting the data

VISOR publishes calibration for M20, MSL, MER and other missions; see
[Downloading VISOR data](../demos/downloading-visor-data.md).
