# Mesh Generation Demo

Mars 2020 NavCam stereo terrain reconstruction using VICAR MARS tools, driven by
[`tig`](../../tig-cli/README.md).

`demo-mesh-generation-with-xyz.sh` runs the whole pipeline from a stereo pair, or
skips straight to meshing from a pre-computed XYZ point cloud (`--xyz`).

## Full pipeline from a stereo pair

### What it does

1. **Stereo Correlation** (marscorr + marscor3) - Generate disparity maps (~8 minutes)
2. **XYZ Generation** (marsxyz) - Convert disparity to 3D coordinates (~1 minute)
3. **Rover filtering** (marsrfilt) - Remove rover body, wheels and mast
4. **Mesh Creation** (marsmesh) - Triangulate surface (~30 seconds)
5. **Texture Conversion** (vicario) - VICAR to PNG (<1 second)

**Total Time:** ~10 minutes for 1280x960 NavCam images

### Prerequisites

- Docker Engine 20.10+
- `pip install tig-cli`
- M2020 NavCam stereo pair (FDR format)
- 16GB RAM recommended
- M2020 calibration files (see [Calibration Data](../reference/calibration-data.md));
  the script locates them with `find-calibration.sh`

### Usage

```bash
./demo-mesh-generation-with-xyz.sh \
  --stereo-left /path/to/NLM_*_FDR_*.VIC \
  --stereo-right /path/to/NRM_*_FDR_*.VIC
```

**Output** (in `workspace/`):
- `terrain.obj` - 3D mesh (~273M, 1.2M vertices)
- `terrain.mtl` - Material file
- `texture.png` - Texture image (1280x960)
- `pointcloud.xyz` - XYZ point cloud (~15M)
- `pointcloud_filtered.xyz` - Rover hardware removed
- `disparity.img` - Disparity map

### Example

```bash
./demo-mesh-generation-with-xyz.sh \
  --stereo-left NLM_1835_0829848458_777FDR_N0874924NCAM00230_0A02LLJ01.VIC \
  --stereo-right NRM_1835_0829848458_777FDR_N0874924NCAM00230_0A02LLJ01.VIC
```

### Algorithm parameters

**Stereo Correlation:**
- Initial (marscorr): `template=15 search=51 quality=0.2`
- Refinement (marscor3): `template=11 search=31 quality=0.4 -omp_on`

**XYZ Generation:**
- Filters: `error=10.0 abserr=0.15 spike_range=0.04 outlier=0.5`
- Coordinate frame: SITE_FRAME

**Mesh Generation:**
- Adaptive decimation, `lod_levels=10`, `res_min=3000 res_max=500000`
- Gap filling: `maxgap=5`

## Quick mode: mesh from pre-computed XYZ

Skips stereo correlation, ~90 seconds total.

```bash
./demo-mesh-generation-with-xyz.sh \
  --xyz pointcloud.IMG \
  --texture image.IMG
```

**Output** (in `workspace/`):
- `terrain.obj` - 3D mesh (~179M, 752K vertices)
- `terrain.mtl` - Material file
- `texture.png` - Texture image (1280x960)
- `pointcloud_input.xyz` - The supplied cloud, as meshed

### Data source

Uses pre-computed NavCam XYZ from VISOR samples:
- **XYZ:** `nlf_1835_0829848458_777xyz_n0874924ncam00230_0a02llj08.img`
- **Texture:** `nlm_1835_0829848458_777fdr_n0874924ncam00230_0a02llj01.vic`

VISOR (VICAR Institutional Stereo Observation Repository) provides processed M2020
data products; see [Downloading VISOR Data](downloading-visor-data.md).

## Running the steps by hand

The script is a thin wrapper over ordinary `tig` invocations, so each stage can be
run and tuned individually:

```bash
export MARS_CONFIG_PATH=~/mars_calibration_m20
cd workspace

tig marscorr \( left.vic right.vic \) disparity_init.img template=15 search=51 quality=0.2
tig marscor3 \( left.vic right.vic \) disparity.img in_disp=disparity_init.img \
  template=11 search=31 quality=0.4 -omp_on
tig marsxyz  \( left.vic right.vic \) pointcloud.xyz disp=disparity.img
tig marsrfilt inp=pointcloud.xyz out=pointcloud_filtered.xyz
tig marsmesh inp=pointcloud_filtered.xyz out=terrain.obj in_skin=texture.img -adaptive
tig vicario texture.img texture.png
```

To drop the `tig ` prefix entirely, generate shims once:

```bash
tig --shim
export PATH="$HOME/.local/share/tig/shims:$PATH"
marsmesh inp=pointcloud_filtered.xyz out=terrain.obj in_skin=texture.img -adaptive
```

The container persists between commands, so each one starts instantly. `tig --status`
shows it; `tig --shutdown` removes it.

## Viewing meshes

Output is standard Wavefront OBJ:

**Desktop viewers:**
- **Blender:** File → Import → Wavefront (.obj) - Best for editing/rendering
- **MeshLab:** File → Import Mesh - Best for analysis/measurements
- **CloudCompare:** File → Open - Best for point cloud comparison

**Online viewers:**
- https://3dviewer.net/ - drag and drop `terrain.obj` + `texture.png`

**Command-line inspection:**
```bash
# Vertex count
grep -c "^v " terrain.obj

# Triangle count
grep -c "^f " terrain.obj

# Bounding box
grep "^v " terrain.obj | awk '{print $2,$3,$4}' | \
  awk 'NR==1{min_x=max_x=$1; min_y=max_y=$2; min_z=max_z=$3}
       {if($1<min_x) min_x=$1; if($1>max_x) max_x=$1;
        if($2<min_y) min_y=$2; if($2>max_y) max_y=$2;
        if($3<min_z) min_z=$3; if($3>max_z) max_z=$3}
       END{print "X:", min_x, max_x; print "Y:", min_y, max_y; print "Z:", min_z, max_z}'
```

## Troubleshooting

**Out of memory:**
- Increase Docker memory limit (Settings → Resources)
- Use smaller images (subframes instead of full resolution)
- Reduce `res_max` in marsmesh

**Calibration errors:**
- Verify `MARS_CONFIG_PATH` points to a valid calibration directory
- Check the calibration includes camera models for your instrument
- Use `find-calibration.sh` to locate calibration files
- `tig bash -c 'ls $MARS_CONFIG_PATH'` shows what the container actually sees

**A stage produced no output:**
- MARS tools frequently exit non-zero even when they succeed; check for the file
  rather than the exit status
- `tig --status` confirms the container and its mounts

**Poor mesh quality:**
- Use full-resolution images (not thumbnails/downsampled)
- Adjust the stereo correlation quality threshold
- Tune the marsmesh decimation parameters

## Data sources

**M2020 NavCam Images:**
- VISOR: https://mars.nasa.gov/mmgis-maps/M20/Layers/json/
- PDS Geosciences Node: https://pds-geosciences.wustl.edu/missions/mars2020/
- Look for `*_FDR_*.VIC` (Full Data Record format)

## References

- [VICAR Documentation](https://github.com/NASA-AMMOS/VICAR)
- [MARS Tools Overview](../architecture/components.md)
- [tig-cli README](../../tig-cli/README.md)
