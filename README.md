# Terrain Intelligence Generator (TIG)

Open source planetary image processing and terrain reconstruction environment,
built on NASA JPL's VICAR.

## Overview

TIG turns raw instrument imagery into the visual and geometric products that
surface and orbital mission operations run on: terrain meshes, point clouds,
slope and roughness maps, map-projected mosaics, and format conversions that
carry instrument metadata through the pipeline.

It packages VICAR — NASA JPL's general-purpose image processing system, used on
planetary missions since the 1960s — as a container image with ~546 commands,
plus [`tig`](tig-cli/README.md), a CLI that runs any of them from your own shell
as if they were installed locally. Products come out in interchange formats
(Wavefront OBJ, PNG/JPEG/TIFF, map-projected VICAR) that downstream AMMOS
visualization tools such as MMGIS, ASTTRO and Astria consume; TIG itself does
not talk to those services.

## Capabilities

### Surface reconstruction

Stereo in-situ imagery to terrain products:

| Stage | Tools |
| --- | --- |
| Stereo correlation | `marscorr`, `marscor3`, `marsecorr`, `marsjplstereo` |
| Point clouds | `marsxyz`, `marsxyzmerge`, `marsxyzsurf`, `marsrfilt` |
| Meshes | `marsmesh`, `marsrefmesh` (Wavefront OBJ + OpenInventor) |
| Surface characteristics | `marsslope`, `marsrough`, `marsirough`, `marsuvw` (surface normals), `marsgreach` (goodness/reachability) |
| Range and depth | `marsrange`, `marsdepth`, `marsinvrange` |

The [mesh generation demo](docs/demos/mesh-generation.md) runs the stereo →
disparity → XYZ → mesh path end to end on Mars 2020 NavCam pairs. The slope,
roughness and reachability tools are part of the same MARS toolset and take the
XYZ products as input; this repository does not yet ship a worked example for
them.

### Orbital and large-area mapping

`marsmap` orthoprojects and map-projects images; `marsmos`, `marsremos` and
`marsunmosaic` build and unbuild large-area mosaics; `marsnav`, `marsnav2`,
`marsautotie`, `marstie` and `marsautoloco` tie images together and refine
pointing, which is how images are co-registered before mosaicking or change
comparison. Change detection over time is a workflow built on these, not a
single command.

### Multi-instrument and multi-mission products

Camera models in CAHV/CAHVOR/CAHVORE (`marscahv`, `marsmake_cm`, `marsget_cm`)
let products from different instruments be brought into a common frame
(`marscoordtrans`, `marsproj`), so surface and orbital views can be combined.
Radiometric correction (`marsrad`), colour handling (`marscolor`, `marsdebayer`)
and brightness matching (`marsbrt`, `marsrcorr`) depend on mission calibration
data, which is not bundled — see [Calibration Data](docs/reference/calibration-data.md).

### Format conversion with metadata retention

VICAR's label system carries acquisition metadata through processing, so
intermediate and final products keep their provenance. `vicario` converts VICAR
images to PNG/JPEG/TIFF (see the [reference](docs/reference/vicario.md));
`vtiff`, `visis2` and related tools handle other interchange formats.

### General image processing

~546 commands for enhancement (`stretch`, `filter`), geometry (`geom`, `rotate`,
`size`), analysis (`hist`, `list`, `label`) and arithmetic (`f2`).

### Automation

Every tool is an ordinary command, so pipelines are ordinary scripts — the
[demo script](demo-mesh-generation-with-xyz.sh) is one. `tig` keeps a container
warm between invocations, so per-command overhead is tens of milliseconds rather
than a container start.

## Quick Start

```bash
pip install tig-cli

# Any VICAR tool, running in the container against your local files
tig gen test.vic 512 512
tig vicario test.vic test.png
```

Prefer the tools unqualified? Generate a shim directory once:

```bash
tig --shim
export PATH="$HOME/.local/share/tig/shims:$PATH"
marsmesh inp=pointcloud.xyz out=terrain.obj in_skin=texture.img -adaptive
```

Generate a mesh from a Mars 2020 NavCam stereo pair:

```bash
export MARS_CONFIG_PATH=/path/to/mars_calibration_m20
./demo-mesh-generation-with-xyz.sh \
  --stereo-left /path/to/NLM_*_FDR_*.VIC \
  --stereo-right /path/to/NRM_*_FDR_*.VIC

ls workspace/          # terrain.obj, terrain.mtl, texture.png, pointcloud.xyz
meshlab workspace/terrain.obj
```

See [QUICKSTART.md](QUICKSTART.md) for the fuller tour.

## Components

### tig-cli

The command-line client. Runs any VICAR tool in the container, translating host
paths, reusing one warm container, mounting calibration data, and forwarding X11
so GUI tools such as `xvd` display on your desktop.

📁 `tig-cli/` · 📦 [`pip install tig-cli`](https://pypi.org/project/tig-cli/) · 📖 [README](tig-cli/README.md)

### TIG VICAR image

The container image: VICAR built from the
[NASA-AMMOS/VICAR](https://github.com/NASA-AMMOS/VICAR) open-source releases,
plus the Java `vicario` converter. Published as
`ghcr.io/nasa-ammos/tig/terrain-intelligence-generator:opensource`.

📁 `terrain-intelligence-generator/` · 📖 [README](terrain-intelligence-generator/README.md)

### VISOR

VICAR Institutional Stereo Observation Repository — camera calibration files for
M20, MSL, MER and other missions. Not bundled in the image; mount it at runtime.

📖 [Downloading VISOR Data](docs/demos/downloading-visor-data.md) · [Calibration Data](docs/reference/calibration-data.md)

### Demos

📁 `demo-mesh-generation-with-xyz.sh` · 📖 [Mesh Generation](docs/demos/mesh-generation.md) · [Command Reference](docs/demos/commands.md)

## Documentation

- **[Getting Started](docs/getting-started.md)** - Installation and setup
- **[Quick Start](QUICKSTART.md)** - Common workflows end to end
- **[Mesh Generation Demo](docs/demos/mesh-generation.md)** - Step-by-step mesh creation
- **[Command Reference](docs/demos/commands.md)** - Tour of the VICAR toolset
- **[Vicario Reference](docs/reference/vicario.md)** - Image format conversion
- **[Calibration Data](docs/reference/calibration-data.md)** - Mounting MARS/VISOR files
- **[Architecture](docs/architecture/components.md)** - How the pieces fit together
- **[Airflow + Kubernetes Pipeline](examples/airflow-k8s-pipeline/README.md)** - Event-driven terrain mesh generation example

## Key Tools

### Terrain reconstruction pipeline

| Tool | Purpose | Input | Output |
|------|---------|-------|--------|
| `marscorr` | Initial stereo correlation | Stereo pair | Disparity map |
| `marscor3` | Disparity refinement | Disparity + images | Refined disparity |
| `marsxyz` | 3D point generation | Disparity + images | XYZ point cloud |
| `marsrfilt` | Rover hardware filtering | XYZ | Filtered XYZ |
| `marsmesh` | Surface triangulation | XYZ + texture | 3D mesh (OBJ) |
| `marsslope`, `marsrough` | Surface characteristics | XYZ | Slope / roughness maps |
| `marsmap` | Orthoprojection | Images + geometry | Map-projected images |
| `marsmos` | Mosaicking | Map-projected images | Mosaic |

### General image processing

| Tool | Purpose | Category |
|------|---------|----------|
| `vicario` | VICAR ↔ PNG/JPEG/TIFF | Format conversion |
| `gen` | Generate test images | Development |
| `stretch` | Contrast adjustment | Enhancement |
| `filter` | Spatial filtering | Enhancement |
| `geom` | Geometric transformation | Geometric |
| `hist` | Histogram analysis | Analysis |
| `label` | VICAR metadata viewer | Metadata |
| `f2` | Image arithmetic | Mathematical |

*Representative examples out of ~546 commands; `tig bash -c 'ls /usr/local/bin'`
lists them all.*

### Exit Codes

VICAR programs return 1 on a successful run, and 1 again when TAE rejects the
invocation, so the raw code cannot tell the two apart. TIG's command wrappers
translate them, which means `set -e` and `&&` chains work as written:

| Code | Meaning |
|------|---------|
| 0 | The program ran |
| 1 | TAE rejected the invocation (missing parameter, unknown keyword) |
| 255 | The program called `abend()` — e.g. a missing or unreadable input |
| >128 | Killed by a signal |

The translation happens in the wrappers under `/usr/local/bin`, so it applies
to commands run through `tig-cli` or `docker exec`. Calling a program by its
full path (`$V2TOP/p2/lib/x86-64-linx/gen`) bypasses it and returns VICAR's
raw code.

## Requirements

- Docker or Podman
- Python 3.9+
- 8GB RAM minimum (16GB recommended for high-res meshes)
- Linux, macOS (including Apple Silicon, via emulation), or Windows with WSL2

## Project Structure

```
tig/
├── demo-mesh-generation-with-xyz.sh    # Mesh demo (stereo pair or pre-computed XYZ)
├── find-calibration.sh                 # Calibration helper
├── tig-cli/                            # The tig command-line client (PyPI: tig-cli)
├── terrain-intelligence-generator/     # Container image: Dockerfile, vicario, build/test
├── QUICKSTART.md                       # Common workflows end to end
├── examples/
│   └── airflow-k8s-pipeline/           # Airflow + Kubernetes terrain pipeline example
└── docs/
    ├── demos/                          # Demo guides
    ├── architecture/                   # System design
    └── reference/                      # Tool references
```

## Contributing

Contributions welcome! This project builds on:
- **VICAR**: JPL MIPL's general-purpose image processing system
- **Docker**: Containerized VICAR execution environment
- **VISOR**: Open source calibration repository for multiple missions

## License

Apache License 2.0 (see LICENSE file)

## About VICAR

VICAR (Video Image Communication and Retrieval) is a general-purpose image
processing system developed by NASA JPL's Multimission Image Processing
Laboratory (MIPL). Used for processing images from Mars rovers, lunar missions,
and deep space probes since the 1960s, it covers enhancement, filtering,
geometric transformation, radiometric calibration, stereo reconstruction, and
format conversion. TIG makes it accessible through modern containerization.

## Acknowledgments

- NASA JPL Multimission Image Processing Laboratory (MIPL)
- VICAR development team
- Open source planetary science community
