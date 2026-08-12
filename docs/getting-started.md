# Getting Started with TIG

Quick setup guide for TIG - a VICAR image processing environment with ~550 commands. This guide demonstrates the flagship stereo terrain reconstruction workflow.

## Prerequisites

- **A container runtime**: Docker, Podman, nerdctl or Finch
- **Python 3.9+**
- **8GB RAM** minimum (16GB recommended)
- **M2020 stereo images** (NavCam or Mastcam-Z)

## Installation

```bash
pip install tig-cli
tig gen test.vic 64 64      # pulls the image on first use
```

`tig` runs any VICAR tool inside the container, translating paths so files in
your current directory and home directory just work:

```bash
tig label image.vic
tig vicario image.vic image.png
```

To build the image yourself instead of using the published one, see the
[TIG VICAR image](../terrain-intelligence-generator/README.md).

## Running Your First Demo

### 1. Prepare Data

Get M2020 stereo images from PDS or use sample data:

```bash
# Sample NavCam stereo pair locations:
# Left:  NLM_<SCLK>_*FDR_*.VIC
# Right: NRM_<SCLK>_*FDR_*.VIC
```

### 2. Run Mesh Generation

```bash
./demo-mesh-generation-with-xyz.sh \
  --stereo-left /path/to/left.VIC \
  --stereo-right /path/to/right.VIC
```

**Processing time**: ~10 minutes for 1280x960 images

### 3. View Results

```bash
# Check output
ls workspace/
# terrain.obj      - 3D mesh (~273M)
# terrain.mtl      - Material file
# texture.png      - Texture (1280x960)
# pointcloud.xyz   - XYZ data (~15M)

# View mesh
meshlab workspace/terrain.obj
# or
blender workspace/terrain.obj
```

## What the Demo Does

1. **Stereo Correlation** (marscorr + marscor3)
   - Matches features between left/right images
   - Generates disparity map
   - ~8 minutes

2. **XYZ Generation** (marsxyz)
   - Converts disparity to 3D coordinates
   - Filters outliers
   - ~1 minute

3. **Mesh Creation** (marsmesh)
   - Triangulates point cloud
   - Applies texture
   - ~30 seconds

4. **Format Conversion** (vicario)
   - Converts VICAR to PNG
   - <1 second

## Troubleshooting

### Stale Container

tig reuses one container across runs. To start clean:

```bash
tig --status      # what is running
tig --shutdown    # remove it
```

### Out of Memory

```bash
no additional memory available
```

**Solution**: Increase Docker memory limit to 16GB or use lower resolution images

### Missing Calibration

```bash
ERROR: MARS calibration not found
```

**Solution**: Point `MARS_CONFIG_PATH` at your calibration directory; see
[Calibration Data](reference/calibration-data.md).

## Next Steps

### Terrain Reconstruction
- **[Mesh Generation Demo](demos/mesh-generation.md)** - Detailed walkthrough
- **[Command Reference](demos/commands.md)** - Available VICAR tools

### General Image Processing
- **[Vicario Reference](reference/vicario.md)** - Image format conversion
- **[tig-cli](../tig-cli/README.md)** - Running any of the ~550 VICAR commands, config, X11, shims

## Configuration

### Using Custom Calibration

```bash
export MARS_CONFIG_PATH=/path/to/calib
```

tig mounts it read-only at `/usr/local/vicar/mars_calib`; see
[Calibration Data](reference/calibration-data.md).

### Using Pre-computed XYZ

Skip correlation if you have XYZ files:

```bash
./demo-mesh-generation-with-xyz.sh \
  --xyz pointcloud.IMG \
  --texture image.IMG
```

See [demos/mesh-generation.md](demos/mesh-generation.md) for details.
