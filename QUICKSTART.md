# Quick Start - TIG Image Processing & Terrain Reconstruction

TIG provides ~550 VICAR image processing commands. This guide focuses on the flagship terrain reconstruction workflow, but you can use TIG for general image processing, format conversion, enhancement, and analysis.

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

Run any of the ~550 tools as `tig <tool> ...`. To drop the prefix:

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

### Mars 2020 NavCam Stereo Pairs

**VISOR (Recommended):**
- https://mars.nasa.gov/mmgis-maps/M20/Layers/json/
- Look for `*_FDR_*.VIC` files (Full Data Record format)
- Download matching left (NLM) and right (NRM) pairs

**PDS Geosciences Node:**
- https://pds-geosciences.wustl.edu/missions/mars2020/
- Navigate to NCAM data products
- Download stereo pairs with matching SCLK timestamps

**Example filenames:**
```
Left:  NLM_1835_0829848458_777FDR_N0874924NCAM00230_0A02LLJ01.VIC
Right: NRM_1835_0829848458_777FDR_N0874924NCAM00230_0A02LLJ01.VIC
        ^^^^^^^^^^^^^^^^^^^^  <-- Must match (SCLK timestamp)
```

### Pre-computed XYZ Files

VISOR provides pre-computed XYZ point clouds:
- Search for `*_xyz_*.img` files
- Much faster than stereo processing

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

### Process Real Mars 2020 Data

```bash
# 1. Download a stereo pair from VISOR
cd /tmp
wget https://mars.nasa.gov/.../NLM_1835_0829848458_777FDR_*.VIC
wget https://mars.nasa.gov/.../NRM_1835_0829848458_777FDR_*.VIC

# 2. Generate the mesh
cd /path/to/tig
./demo-mesh-generation-with-xyz.sh \
  --stereo-left /tmp/NLM_*.VIC \
  --stereo-right /tmp/NRM_*.VIC

# 3. View it
# Import workspace/terrain.obj into Blender/MeshLab
```

### Interactive Exploration

```bash
mkdir -p ~/vicar-play && cd ~/vicar-play

# Generate test images
tig gen out=test.img nl=100 ns=100

# View image metadata
tig label test.img

# Image processing examples
tig stretch inp=test.img out=stretched.img
tig filter inp=test.img out=filtered.img
tig geom inp=test.img out=rotated.img rotate=45

# Terrain processing
tig marsmesh inp=pointcloud.xyz out=custom.obj

# Convert VICAR to standard formats
tig vicario inp=test.img out=test.png

# GUI display
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
- Explore VICAR commands: `tig bash -c 'ls /usr/local/bin'` (~550 commands)
- Image enhancement: Try `stretch`, `filter`, `hist`
- Format conversion: Use `vicario` for VICAR ↔ PNG/JPEG/TIFF
- Geometric operations: Experiment with `geom`, `rotate`, `size`
- CLI details: [tig-cli README](tig-cli/README.md)
