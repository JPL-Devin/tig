# Downloading VISOR Data

VISOR (VIsualization System for Orbital Reconnaissance) calibration and sample data are published as assets of the [VICAR 5.0 release](https://github.com/NASA-AMMOS/VICAR/releases/tag/5.0). The base TIG Docker image does **not** bundle them, to keep it small.

If you work on a single mission, you probably do not need this page: the
`:visor-<mission>` image variants ship that mission's calibration already
extracted and configured. See [Mounting MARS Calibration Data](../reference/calibration-data.md).
Sample data is not in those variants, so download it here.

## Quick Download

Calibration has a script for this, which lists what the release publishes and
installs a mission where the demos look for it, verifying the pinned SHA-256 and
concatenating M20's two parts:

```bash
./fetch-calibration.sh --list      # missions, download size, installed size
./fetch-calibration.sh msl         # asks first, installs ~/.mars_calib/msl
```

The demos offer it themselves when they find no calibration; see
[Downloading it automatically](../reference/calibration-data.md#downloading-it-automatically).
Sample data is not covered by the script - download it, or a mission by hand,
like this:

```bash
mkdir -p visor_data

# Sample data (740MB compressed, 1.3GB extracted)
curl -L "https://github.com/NASA-AMMOS/VICAR/releases/download/5.0/visor_sample_data_20230623.tar.gz" | \
  tar -zxf - -C visor_data

# Calibration for one mission - here MSL (380MB compressed)
curl -L "https://github.com/NASA-AMMOS/VICAR/releases/download/5.0/visor_calibration_20230608_msl.tar.gz" | \
  tar -zxf - -C visor_data
```

M20 is the exception: GitHub caps a release asset at 2GB, so its calibration is published as two parts that must be concatenated before extraction.

```bash
curl -L "https://github.com/NASA-AMMOS/VICAR/releases/download/5.0/visor_calibration_20230608_m20.tar.gzaa" \
        "https://github.com/NASA-AMMOS/VICAR/releases/download/5.0/visor_calibration_20230608_m20.tar.gzab" | \
  tar -zxf - -C visor_data
```

**Result:**
```
visor_data/
├── sample_data/                     # from visor_sample_data_20230623.tar.gz
│   ├── OrthorectifiedMosaic/        # Pre-computed XYZ point clouds
│   ├── StereoCorrelation/           # Stereo image pairs
│   ├── RadiometricCorrection/       # Raw EDRs for marsrad
│   └── ...                          # ~20 more per-workflow directories
└── calibration/                     # from each visor_calibration_* archive
    └── msl/
        ├── camera_models/           # *.cahvor, *.cahvore
        ├── flat_fields/             # *.IMG
        ├── ilut/
        ├── param_files/             # *_camera_mapping.xml, *.parms
        └── rmc/
```

`tig --calibration-path` (or `MARS_CONFIG_PATH`, or `calibration_path` in a config file) takes the *mission* directory, `visor_data/calibration/msl` above - the directory that directly contains `camera_models/`.

## What's Included

### Calibration

Camera models (CAHVOR/CAHVORE), flat fields, inverse lookup tables, camera mapping XML and rover motion counter files, per mission.

| Asset | Mission | Compressed | Extracted |
| --- | --- | --- | --- |
| `visor_calibration_20230608_m20.tar.gzaa` + `...ab` | Mars 2020 / Perseverance | 2.69 GB | 5.3 GB |
| `visor_calibration_20230608_mer.tar.gz` | MER / Spirit and Opportunity | 845 MB | 989 MB |
| `visor_calibration_20230608_phx.tar.gz` | Phoenix | 258 MB | 627 MB |
| `visor_calibration_20230608_msl.tar.gz` | Mars Science Laboratory / Curiosity | 380 MB | 532 MB |
| `visor_calibration_20230608_msam.tar.gz` | MSAM | 366 MB | 516 MB |
| `visor_calibration_20230608_nsyt.tar.gz` | InSight | 117 MB | 159 MB |
| **Total (all six)** | | **4.6 GB** | **8.1 GB** |

### Sample Data (740MB compressed, 1.3GB extracted)

`visor_sample_data_20230623.tar.gz`. Inputs and pre-computed products for the VICAR sample workflows - stereo pairs, disparity maps, XYZ point clouds, mosaics, raw EDRs - plus the `Scripts/` csh drivers that run them. Predominantly MSL Navcam, with some MER Pancam.

**Example files:**
```
sample_data/StereoCorrelation/
  NLB_712299404EDR_F0961766NCAM00353M1.IMG   # Left image
  NRB_712299404EDR_F0961766NCAM00353M1.IMG   # Right image

sample_data/OrthorectifiedMosaic/
  NLB_712299404XYZ_F0961766NCAM00353M1.IMG   # XYZ point cloud
```

## Usage with TIG

```bash
# Per invocation
tig --calibration-path visor_data/calibration/msl marsrad edr.IMG out.RAD.IMG

# Or for the session
export MARS_CONFIG_PATH="$(pwd)/visor_data/calibration/msl"
tig marsrad edr.IMG out.RAD.IMG
```

The path is mounted read-only at `/usr/local/vicar/mars_calib` inside the container, and `MARS_CONFIG_PATH` is set to that location. On a `:visor-<mission>` image the same mount replaces the bundled calibration; see [Mounting MARS Calibration Data](../reference/calibration-data.md).

Sample data is just input files - mount it like any other data, or keep it under your home directory, which `tig` mounts already:

```bash
tig marsrad visor_data/sample_data/RadiometricCorrection/NLB_712299404EDR_F0961766NCAM00353M1.IMG out.RAD.IMG
```

## Alternative: Download Individual Files

If you only need specific samples, browse the release directly:

https://github.com/NASA-AMMOS/VICAR/releases/tag/5.0

## Troubleshooting

### "404 Not Found" errors

Asset names contain a date stamp that is not the release version. Check the release page, or list the assets:

```bash
gh release view 5.0 --repo NASA-AMMOS/VICAR --json assets \
  --jq '.assets[] | "\(.name)\t\(.size)"'
```

### M20 extraction fails with "unexpected end of file"

The two M20 parts were produced by `split`; neither is a valid archive on its own. Concatenate them, either through `curl` as above or on disk:

```bash
cat visor_calibration_20230608_m20.tar.gzaa visor_calibration_20230608_m20.tar.gzab | tar -xz -C visor_data
```

### Slow download

Use `wget` with resume capability:
```bash
wget -c "https://github.com/NASA-AMMOS/VICAR/releases/download/5.0/visor_sample_data_20230623.tar.gz"
```

## Related Documentation

- [VICAR Releases](https://github.com/NASA-AMMOS/VICAR/releases)
- [Mesh Generation Demo](mesh-generation.md)
- [Getting Started](../getting-started.md)
