# Quick Start - TIG Image Processing & Terrain Reconstruction

TIG provides 546 VICAR image processing commands (74 of them the mission-specific `mars*` terrain programs). This guide focuses on the flagship terrain reconstruction workflow, but you can use TIG for general image processing, format conversion, enhancement, and analysis.

## Install

```bash
pip install tig-cli
```

You need Python 3.9+ and a container runtime — Docker, Podman, nerdctl or
Finch. The VICAR image is pulled on first use.

On macOS, start from [Installing on macOS](docs/install-macos.md): the runtime
runs in a virtual machine and the GUI tools need XQuartz.

```bash
tig gen test.vic 64 64      # writes test.vic in the current directory
tig vicario test.vic test.png
```

Run any of the 546 tools as `tig <tool> ...`. To drop the prefix:

```bash
tig --shim
export PATH="$HOME/.local/share/tig/shims:$PATH"
gen test2.vic 64 64
```

---

## Generate a terrain mesh

### With Pre-computed XYZ

```bash
./demo-mesh-generation-with-xyz.sh \
  --xyz /path/to/pointcloud.xyz \
  --texture /path/to/texture.img
```

**Time:** ~90 seconds

Quick mode needs an XYZ cloud whose VICAR label carries the stereo baseline —
M2020 VISOR XYZ products do, MSL NavCam XYZ from the VISOR samples do not (see
[mesh generation](docs/demos/mesh-generation.md#quick-mode-mesh-from-pre-computed-xyz)).

### With Stereo Pair

```bash
./demo-mesh-generation-with-xyz.sh \
  --stereo-left /path/to/NLM_*_FDR_*.VIC \
  --stereo-right /path/to/NRM_*_FDR_*.VIC
```

**Time:** ~10+ minutes (full resolution images)

**Requirements:**
- Left and right images from **same acquisition** (matching SCLK timestamp)
- Full-resolution or subframe images (not downsampled/thumbnails)
- MARS calibration files (see [calibration setup](docs/reference/calibration-data.md))

**Output:** `workspace/terrain.obj`, `workspace/texture.png`

**Cleanup:** the container is reused across runs; remove it with `tig --shutdown`.

---

## Where to Get Data

### Sample frames and XYZ clouds (VICAR 5.0 release assets)

The VISOR sample data published with VICAR 5.0 is the one download that is known
to work and contains MSL NavCam frames, stereo pairs and pre-computed XYZ clouds:

```bash
mkdir -p visor_data
curl -L "https://github.com/NASA-AMMOS/VICAR/releases/download/5.0/visor_sample_data_20230623.tar.gz" | \
  tar -zxf - -C visor_data
```

[Downloading VISOR data](docs/demos/downloading-visor-data.md) lists what is in
the archive, which demo consumes which product, and the sizes involved.

### Calibration

```bash
./fetch-calibration.sh --list        # what VISOR publishes open source
./fetch-calibration.sh msl          # installs into ~/.mars_calib/msl
```

The MSL calibration is also a release asset
(`visor_calibration_20230608_msl.tar.gz`) if you would rather fetch it by hand.

### Mission archives

The mission archives ([PDS Geosciences
Node](https://pds-geosciences.wustl.edu/missions/mars2020/) and the M20 raw-image
API) carry the full FDR/XYZ record, but they are browsed by hand rather than by
any script in this repo, and their layout changes; nothing here fetches frames
from them. Stereo pairs must share the SCLK timestamp, e.g.

```
Left:  NLM_1835_0829848458_777FDR_N0874924NCAM00230_0A02LLJ01.VIC
Right: NRM_1835_0829848458_777FDR_N0874924NCAM00230_0A02LLJ01.VIC
        ^^^^^^^^^^^^^^^^^^^^  <-- Must match (SCLK timestamp)
```

---

## Troubleshooting

### "MARS calibration not found"

Calibration is not in the image; point tig at it:

```bash
export MARS_CONFIG_PATH=/path/to/mars_calibration
tig bash -c 'ls $MARS_CONFIG_PATH/camera_models | head'
```

`find-calibration.sh` searches the usual locations. Have none at all?
`./fetch-calibration.sh --list` shows what VISOR publishes open source and
`./fetch-calibration.sh m20` installs it in `~/.mars_calib/m20`; the demos offer
this themselves when they find nothing. See
[Calibration Data](docs/reference/calibration-data.md).

### "Stereo images don't match"

**Error:** Disparity calculation fails or produces garbage

**Solution:** Verify images are from the same acquisition — the SCLK timestamp in
the left (NLM) and right (NRM) filenames must match exactly.

### "Out of memory"

**Increase the runtime's memory:**
- Docker Desktop: Settings → Resources → Memory (recommend 16GB)
- Podman: `podman machine set --memory 16384`

**Or use smaller images:**
- Use subframe/windowed images instead of full resolution
- Reduce marsmesh `res_max` parameter

### "No container runtime was found on PATH"

Nothing tig knows about is installed, or the one you want is not on `PATH`;
set `TIG_CONTAINER_RUNTIME` (or the `runtime` config key) to the command to
use. If it is installed, the daemon may not be running, or your user may not
be in the `docker` group.
`tig --status` shows what tig currently has running; `tig --shutdown` clears it.

---

## Example Workflows

### Process the public VISOR sample data (MSL NavCam)

This is the one dataset anyone can download, and it is what the release
regression suite runs on:

```bash
cd /path/to/tig
mkdir -p visor_data
curl -L "https://github.com/NASA-AMMOS/VICAR/releases/download/5.0/visor_sample_data_20230623.tar.gz" | \
  tar -zxf - -C visor_data
./fetch-calibration.sh msl
export MARS_CONFIG_PATH=~/.mars_calib/msl

# A panorama from frames taken at one rover position
./demo-panorama-mosaic.sh visor_data/<one-site>/*.IMG

# Slope, roughness, normals and placement goodness from a NavCam XYZ cloud
./demo-surface-characteristics.sh --xyz visor_data/<...>XYZ<...>.IMG --solar-angle 60
```

Meshing is the one thing these MSL samples are awkward for: the demo's built-in
stereo path reports `No valid seed points found` on these frames (see
[the image README](terrain-intelligence-generator/README.md#release-visual-regression)),
and `--xyz` quick mode needs the stereo baseline in the cloud's label, which
MSL NavCam XYZ products do not carry.

### Generate a mesh from Mars 2020 data

Meshing wants M2020 frames and M2020 calibration; the frames come from the
mission archives (see [Where to Get Data](#where-to-get-data)):

```bash
cd /path/to/tig
./fetch-calibration.sh m20
export MARS_CONFIG_PATH=~/.mars_calib/m20

# From a stereo pair (left/right must share the SCLK), ~10 minutes
./demo-mesh-generation-with-xyz.sh \
  --stereo-left /path/to/NLM_*_FDR_*.VIC \
  --stereo-right /path/to/NRM_*_FDR_*.VIC

# Or straight from an M2020 XYZ product, ~90 seconds
./demo-mesh-generation-with-xyz.sh --xyz /path/to/*xyz*.img --texture /path/to/*fdr*.vic

# Then import workspace/terrain.obj into Blender/MeshLab
```

### Interactive Exploration

```bash
mkdir -p ~/vicar-play && cd ~/vicar-play

# Generate test images
tig gen out=test.img nl=100 ns=100

# View image metadata (label is a TAE proc: the subcommand is a flag)
tig label -list inp=test.img

# Image processing examples
tig stretch inp=test.img out=stretched.img
tig filter inp=test.img out=filtered.img
tig size inp=test.img out=zoomed.img zoom=2

# Rotation is two steps: rotate2 writes the transform, lgeom applies it
tig rotate2 test.img rot.par angle=45
tig lgeom inp=test.img out=rotated.img parms=rot.par

# Convert VICAR to standard formats (vicario is positional-only)
tig vicario test.img test.png

# GUI display (needs an X display; see docs/install-macos.md on macOS)
tig xvd test.img
```

The container stays up between commands, so each one starts instantly.

---

## Viewing Meshes

**Desktop Applications:**
- **Blender:** File → Import → Wavefront (.obj)
- **MeshLab:** File → Import Mesh
- **CloudCompare:** File → Open

**Online Viewer:**
- https://3dviewer.net/ (drag and drop .obj + .png)

**Command-line Inspection:**
```bash
# Count vertices
grep -c "^v " terrain.obj

# Count triangles
grep -c "^f " terrain.obj

# Check file size
ls -lh terrain.obj
```

---

## Next Steps

### Terrain Reconstruction
- Read full documentation: `docs/demos/mesh-generation.md`
- Customize processing: Edit marsmesh/marsxyz parameters in the demo script
- Integrate into pipelines: Use as reference for your own scripts

### General Image Processing
- Explore VICAR commands: `tig bash -c 'ls /usr/local/bin'` (546 commands)
- Image enhancement: Try `stretch`, `filter`, `hist`
- Format conversion: Use `vicario` for VICAR ↔ PNG/JPEG/TIFF — it is positional
  (`tig vicario in.img out.png`) and exits 0 even when it fails, so check the
  output file exists ([reference](docs/reference/vicario.md))
- Geometric operations: Experiment with `size`, `rotate2` + `lgeom`, `mgeom`
- CLI details: [tig-cli README](tig-cli/README.md)
