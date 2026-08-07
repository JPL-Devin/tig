#!/bin/bash
set -e

echo "=== Terrain Intelligence Generator - Mesh Generation Demo ==="
echo ""

# Every VICAR tool below runs through tig, which starts and reuses the
# container, mounts this workspace, and translates host paths.
WORKSPACE="$(pwd)/workspace"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! command -v tig &> /dev/null; then
  echo "ERROR: tig not found on PATH."
  echo ""
  echo "Install it with:  pip install tig-cli"
  exit 1
fi

# Find calibration files using helper script
if [ -f "$SCRIPT_DIR/find-calibration.sh" ]; then
    source "$SCRIPT_DIR/find-calibration.sh"
    # Guard the assignment: under 'set -e' a failed lookup would otherwise end
    # the script before the help below is printed.
    CALIB_DIR=$(find_calibration) || true
    if [ -z "$CALIB_DIR" ] || ! verify_calibration "$CALIB_DIR"; then
        echo "ERROR: MARS calibration not found."
        echo ""
        print_calibration_help
        exit 1
    fi
else
    # Fallback to default location if helper not found
    CALIB_DIR="$(pwd)/terrain-intelligence-generator/docker/mars_calibration_m20"
fi

# Parse arguments
STEREO_LEFT=""
STEREO_RIGHT=""
XYZ_FILE=""
TEXTURE_FILE=""

print_usage() {
  echo "Usage: $0 [OPTIONS]"
  echo ""
  echo "Options:"
  echo "  --xyz FILE           Use pre-computed XYZ point cloud (fast)"
  echo "  --stereo-left FILE   Left stereo image (for XYZ calculation)"
  echo "  --stereo-right FILE  Right stereo image (for XYZ calculation)"
  echo "  --texture FILE       Texture image (optional, defaults to left stereo)"
  echo ""
  echo "Examples:"
  echo "  # Use pre-computed XYZ (fast, ~90 seconds)"
  echo "  $0 --xyz pointcloud.IMG --texture image.IMG"
  echo ""
  echo "  # Calculate XYZ from stereo pair (slow, ~10+ minutes)"
  echo "  $0 --stereo-left left.VIC --stereo-right right.VIC"
  echo ""
  echo "Requirements:"
  echo "  - tig-cli installed (pip install tig-cli) and a running Docker daemon"
  echo "  - Stereo images must be from same acquisition (matching SCLK)"
  echo "  - Full-resolution or subframe images supported"
  echo "  - Downsampled/thumbnails not recommended (causes pixel distortion)"
  exit 1
}

while [[ $# -gt 0 ]]; do
  case $1 in
    --xyz)
      [ -z "$2" ] && { echo "ERROR: --xyz requires a FILE argument"; print_usage; }
      XYZ_FILE="$2"
      shift 2
      ;;
    --stereo-left)
      [ -z "$2" ] && { echo "ERROR: --stereo-left requires a FILE argument"; print_usage; }
      STEREO_LEFT="$2"
      shift 2
      ;;
    --stereo-right)
      [ -z "$2" ] && { echo "ERROR: --stereo-right requires a FILE argument"; print_usage; }
      STEREO_RIGHT="$2"
      shift 2
      ;;
    --texture)
      [ -z "$2" ] && { echo "ERROR: --texture requires a FILE argument"; print_usage; }
      TEXTURE_FILE="$2"
      shift 2
      ;;
    --help|-h)
      print_usage
      ;;
    *)
      echo "ERROR: Unknown option: $1"
      print_usage
      ;;
  esac
done

# Validate inputs
if [ -z "$XYZ_FILE" ] && [ -z "$STEREO_LEFT" ]; then
  echo "ERROR: Must specify either --xyz or --stereo-left/--stereo-right"
  print_usage
fi

if [ -n "$STEREO_LEFT" ] && [ -z "$STEREO_RIGHT" ]; then
  echo "ERROR: --stereo-right required when using --stereo-left"
  exit 1
fi

if [ -n "$STEREO_RIGHT" ] && [ -z "$STEREO_LEFT" ]; then
  echo "ERROR: --stereo-left required when using --stereo-right"
  exit 1
fi

# Verify calibration exists
echo "Using calibration from: $CALIB_DIR"
if [ ! -d "$CALIB_DIR" ]; then
  echo "ERROR: Calibration directory not accessible"
  exit 1
fi

# tig mounts this read-only at /usr/local/vicar/mars_calib and points the MARS
# programs at it. Resolve it now: tig runs from the workspace directory below,
# where a relative calibration path would no longer resolve.
CALIB_DIR="$(cd "$CALIB_DIR" && pwd)"
export MARS_CONFIG_PATH="$CALIB_DIR"

# Resolve inputs before changing directory, so relative paths keep working
abspath() {
  [ -z "$1" ] && return 0
  local dir
  # Fall back to the path as given, so the file checks below report it.
  dir=$(cd "$(dirname "$1")" 2>/dev/null && pwd) || { echo "$1"; return 0; }
  echo "${dir%/}/$(basename "$1")"
}
XYZ_FILE=$(abspath "$XYZ_FILE")
STEREO_LEFT=$(abspath "$STEREO_LEFT")
STEREO_RIGHT=$(abspath "$STEREO_RIGHT")
TEXTURE_FILE=$(abspath "$TEXTURE_FILE")

# Create workspace
mkdir -p "$WORKSPACE"
echo "✓ Created workspace: $WORKSPACE"
echo ""

cd "$WORKSPACE"

is_input() {
  local candidate="$PWD/$1"
  local given
  for given in "$XYZ_FILE" "$STEREO_LEFT" "$STEREO_RIGHT" "$TEXTURE_FILE"; do
    [ "$given" = "$candidate" ] && return 0
  done
  return 1
}

# Copy an input into place, unless it is already the file we want.
stage() {
  [ "$1" = "$PWD/$2" ] || cp "$1" "$2"
}

# Each step below is judged by whether its output appeared, so clear the
# outputs of an earlier run: a leftover file would make a failure look like
# success and mesh the previous scene's data. Inputs are spared, since
# re-meshing an earlier run's pointcloud.xyz is a documented workflow.
for stale in left.vic right.vic disparity_init.img disparity.img \
  pointcloud.xyz pointcloud_filtered.xyz \
  texture.img texture.png terrain.obj terrain.mtl; do
  is_input "$stale" || rm -f "$stale"
done

# Step 1: Get or generate XYZ
if [ -n "$XYZ_FILE" ]; then
  # Use pre-computed XYZ
  echo "Step 1: Using pre-computed XYZ point cloud..."
  if [ ! -f "$XYZ_FILE" ]; then
    echo "ERROR: XYZ file not found: $XYZ_FILE"
    exit 1
  fi

  stage "$XYZ_FILE" pointcloud_filtered.xyz
  echo "✓ XYZ copied: $(du -h "$XYZ_FILE" | cut -f1)"

  # Set texture
  if [ -n "$TEXTURE_FILE" ]; then
    stage "$TEXTURE_FILE" texture.img
  elif [ -n "$STEREO_LEFT" ]; then
    stage "$STEREO_LEFT" texture.img
  else
    echo "ERROR: No texture specified"
    exit 1
  fi
else
  # Calculate XYZ from stereo pair
  echo "Step 1: Calculating XYZ from stereo pair..."
  echo "  WARNING: This takes 10+ minutes for full-resolution images"
  echo ""

  # Validate files exist
  if [ ! -f "$STEREO_LEFT" ]; then
    echo "ERROR: Left stereo file not found: $STEREO_LEFT"
    exit 1
  fi
  if [ ! -f "$STEREO_RIGHT" ]; then
    echo "ERROR: Right stereo file not found: $STEREO_RIGHT"
    exit 1
  fi

  stage "$STEREO_LEFT" left.vic
  stage "$STEREO_RIGHT" right.vic
  echo "  ✓ Stereo pair copied"

  # Validate image resolution
  echo "  Checking image resolution..."
  left_nl=$(head -c 2000 left.vic | grep -a "NL=" | head -1 | sed "s/.*NL=\([0-9]*\).*/\1/")
  left_ns=$(head -c 2000 left.vic | grep -a "NS=" | head -1 | sed "s/.*NS=\([0-9]*\).*/\1/")
  echo "  Left image: ${left_ns}x${left_nl}"

  # Check if this is a subframe by looking for FIRST_LINE or if dimensions dont match sensor
  has_subframe=$(head -c 20000 left.vic | grep -ac "FIRST_LINE=" || true)

  if [ "$has_subframe" -gt 0 ]; then
    echo "  ✓ Subframe/windowed image detected (partial sensor readout)"
    echo "  Note: Subframes are valid for stereo correlation"
  elif [ -z "$left_ns" ] || [ -z "$left_nl" ]; then
    echo "  Note: could not read NL/NS from the label; skipping the size check"
  elif [ "$left_ns" -ge 3840 ] && [ "$left_nl" -ge 2880 ]; then
    echo "  ✓ Full or near-full resolution image"
  elif [ "$left_ns" -lt 500 ] || [ "$left_nl" -lt 500 ]; then
    echo "  ERROR: Images too small (${left_ns}x${left_nl})"
    echo "  Minimum ~500x500 pixels required for stereo correlation"
    exit 1
  else
    echo "  ✓ Image dimensions: ${left_ns}x${left_nl}"
    echo "  Note: Smaller subframes may have reduced mesh quality"
  fi

  # Step 1a: Stereo correlation (disparity map)
  echo ""
  echo "  Step 1a: Running stereo correlation..."
  echo "  This may take 5-15 minutes..."

  echo "    Running initial correlation (marscorr)..."
  tig marscorr \( left.vic right.vic \) disparity_init.img \
    template=15 search=51 quality=0.2 2>&1 |
    grep -E "tiepoints gathered|Seed point" | tail -3 || true

  if [ ! -f disparity_init.img ]; then
    echo "  ❌ ERROR: marscorr failed to generate disparity_init.img"
    exit 1
  fi
  echo "    ✓ Initial disparity generated"

  echo "    Running refinement (marscor3)..."
  tig marscor3 \( left.vic right.vic \) disparity.img \
    in_disp=disparity_init.img template=11 search=31 quality=0.4 -omp_on 2>&1 |
    grep -E "tiepoints|Pyramid|Zooming" | tail -3 || true

  if [ ! -f disparity.img ]; then
    echo "  ❌ ERROR: marscor3 failed to generate disparity.img"
    exit 1
  fi

  echo "  ✓ Disparity map generated"

  # Step 1b: Generate XYZ from disparity
  echo ""
  echo "  Step 1b: Generating XYZ point cloud (marsxyz)..."
  echo "  This may take 2-5 minutes..."
  tig marsxyz \( left.vic right.vic \) pointcloud.xyz disp=disparity.img \
    error=10.0 abserr=0.15 lined=100 avgline=50 zlimit=\(-300,300\) \
    spike_range=0.04 outlier=0.5 2>&1 |
    grep -E "Successfully|valid|rejected|XYZ" | tail -10 || true

  if [ ! -f pointcloud.xyz ]; then
    echo "  ❌ ERROR: marsxyz failed to generate pointcloud.xyz"
    exit 1
  fi

  echo "  ✓ XYZ point cloud generated"

  # Step 1c: Filter rover hardware from XYZ
  echo ""
  echo "  Step 1c: Filtering rover hardware (marsrfilt)..."
  echo "  This removes rover body, wheels, mast from point cloud..."
  tig marsrfilt inp=pointcloud.xyz out=pointcloud_filtered.xyz 2>&1 |
    grep -E "MARSRFILT|Version|Filtering|points|removed" || true

  if [ ! -f pointcloud_filtered.xyz ]; then
    echo "  ⚠ WARNING: marsrfilt failed, using unfiltered XYZ"
    cp pointcloud.xyz pointcloud_filtered.xyz
  else
    echo "  ✓ Rover hardware filtered"
  fi

  # Use right image as texture (matches reference mesh workflow)
  if [ -n "$TEXTURE_FILE" ]; then
    stage "$TEXTURE_FILE" texture.img
  else
    stage "$STEREO_RIGHT" texture.img
  fi
fi

echo ""
echo "Step 2: Generating 3D mesh..."
echo "  This takes ~30-90 seconds..."
echo "  Note: Using adaptive decimation with filtered XYZ to match M20 IDS pipeline"
tig marsmesh inp=pointcloud_filtered.xyz out=terrain.obj in_skin=texture.img \
  x_subsample=1 y_subsample=1 \
  range_min=0.2 range_mid=100 range_max=100 \
  lod_levels=10 max_angle=87.5 \
  res_min=3000 res_max=500000 density=1 -adaptive \
  maxgap=5 2>&1 |
  grep -E "MARSMESH|Version|mesh|triangles|vertices|Writing|LOD|decimat" || true

if [ ! -f terrain.obj ]; then
  echo "❌ ERROR: marsmesh failed to generate terrain.obj"
  exit 1
fi

echo "✓ Mesh generated: terrain.obj"
echo ""

# Convert texture using Java vicario, which rescales 16-bit VICAR images
# properly (oform=byte rescale=true).
echo "Step 3: Converting texture to PNG..."
tig vicario texture.img texture.png 2>&1 |
  grep -E "Image write Done|inp =|out =|format =|oform =|rescale =" || true
if [ -f texture.png ]; then
  echo "✓ Texture converted: texture.png"
else
  echo "⚠ WARNING: texture conversion failed; texture.img left as-is"
fi
echo ""

# List results
echo "Step 4: Results summary"
ls -lh pointcloud.xyz pointcloud_filtered.xyz terrain.obj terrain.mtl texture.png 2>/dev/null || true
echo ""

echo "=== Demo Complete ==="
echo ""
echo "Generated files in: $WORKSPACE"
echo "  - pointcloud.xyz         : Raw 3D point cloud"
echo "  - pointcloud_filtered.xyz: Filtered point cloud (rover hardware removed)"
echo "  - terrain.obj            : 3D mesh (Wavefront OBJ)"
echo "  - terrain.mtl            : Material file"
echo "  - texture.png            : Texture image"
echo ""
echo "To view the mesh:"
echo "  - Blender: File → Import → Wavefront (.obj)"
echo "  - MeshLab: File → Import Mesh → terrain.obj"
echo "  - CloudCompare: File → Open → terrain.obj"
echo "  - Online: Upload to https://3dviewer.net/"
echo ""
echo "The container stays up for the next run. To remove it:"
echo "  tig --shutdown"
