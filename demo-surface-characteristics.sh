#!/bin/bash
set -e

echo "=== Terrain Intelligence Generator - Surface Characteristics Demo ==="
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

# Find calibration files using helper script. The MARS programs here read the
# camera model out of the XYZ label, so calibration is required even though no
# image is correlated. Looked up after argument parsing so --help stays fast.
if [ -f "$SCRIPT_DIR/find-calibration.sh" ]; then
    source "$SCRIPT_DIR/find-calibration.sh"
else
    CALIB_DIR="$(pwd)/terrain-intelligence-generator/docker/mars_calibration_m20"
fi

# Parse arguments
XYZ_FILE=""
TEXTURE_FILE=""
REACH_FILE=""
INSTRUMENT="heli"
COORD="site"
SOLAR_ANGLE=""

print_usage() {
  echo "Usage: $0 --xyz FILE [OPTIONS]"
  echo ""
  echo "Options:"
  echo "  --xyz FILE           XYZ point cloud (3-band REAL, from marsxyz)   [required]"
  echo "  --texture FILE       Scene image converted to PNG for context     [optional]"
  echo "  --instrument NAME    Instrument for the placement products:"
  echo "                       heli, seis, hp3 or wts (default: heli)"
  echo "  --coord FRAME        Frame for slope products: site, local_level,"
  echo "                       fixed, rover, instrument (default: site)"
  echo "  --solar-angle DEG    Sun elevation at local noon; enables the solar"
  echo "                       energy product (marsslope type=solar)"
  echo "  --reach FILE         6-band arm reachability product; enables the"
  echo "                       marsgreach goodness product"
  echo ""
  echo "Example:"
  echo "  # The mesh demo writes workspace/pointcloud.xyz from a stereo pair"
  echo "  ./demo-mesh-generation-with-xyz.sh --stereo-left L.IMG --stereo-right R.IMG"
  echo "  $0 --xyz workspace/pointcloud_filtered.xyz --texture workspace/texture.img"
  echo ""
  echo "Requirements:"
  echo "  - tig-cli installed (pip install tig-cli) and a running Docker daemon"
  echo "  - MARS calibration for the mission the XYZ came from"
  echo "  - An XYZ product with an intact VICAR label: the camera model and the"
  echo "    coordinate frame are read from it"
  exit 1
}

while [[ $# -gt 0 ]]; do
  case $1 in
    --xyz)
      [ -z "$2" ] && { echo "ERROR: --xyz requires a FILE argument"; print_usage; }
      XYZ_FILE="$2"
      shift 2
      ;;
    --texture)
      [ -z "$2" ] && { echo "ERROR: --texture requires a FILE argument"; print_usage; }
      TEXTURE_FILE="$2"
      shift 2
      ;;
    --reach)
      [ -z "$2" ] && { echo "ERROR: --reach requires a FILE argument"; print_usage; }
      REACH_FILE="$2"
      shift 2
      ;;
    --instrument)
      [ -z "$2" ] && { echo "ERROR: --instrument requires a NAME argument"; print_usage; }
      INSTRUMENT="$2"
      shift 2
      ;;
    --coord)
      [ -z "$2" ] && { echo "ERROR: --coord requires a FRAME argument"; print_usage; }
      COORD="$2"
      shift 2
      ;;
    --solar-angle)
      [ -z "$2" ] && { echo "ERROR: --solar-angle requires a DEG argument"; print_usage; }
      SOLAR_ANGLE="$2"
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

if [ -z "$XYZ_FILE" ]; then
  echo "ERROR: --xyz is required"
  print_usage
fi

case "$INSTRUMENT" in
  heli|seis|hp3|wts) ;;
  *)
    echo "ERROR: --instrument must be one of: heli, seis, hp3, wts"
    exit 1
    ;;
esac

if declare -f calibration_setup > /dev/null; then
  # Guard the call: under 'set -e' a failed lookup would otherwise end the
  # script before the help below is printed.
  calibration_setup || true
  if [ -z "$CALIB_DIR" ] && [ -z "$CALIB_IN_IMAGE" ]; then
    echo "ERROR: MARS calibration not found."
    echo ""
    print_calibration_help
    exit 1
  fi
fi

if [ -n "${CALIB_IN_IMAGE:-}" ]; then
  # Bundled in the image: nothing to mount, and MARS_CONFIG_PATH must stay
  # unset, since tig reads it as a host path to mount.
  echo "Using calibration ${CALIB_IN_IMAGE_DESC:-already in the container}: $CALIB_IN_IMAGE"
  unset MARS_CONFIG_PATH
else
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
fi

# Resolve inputs before changing directory, so relative paths keep working
abspath() {
  [ -z "$1" ] && return 0
  local dir
  dir=$(cd "$(dirname "$1")" 2>/dev/null && pwd) || { echo "$1"; return 0; }
  echo "${dir%/}/$(basename "$1")"
}
XYZ_FILE=$(abspath "$XYZ_FILE")
TEXTURE_FILE=$(abspath "$TEXTURE_FILE")
REACH_FILE=$(abspath "$REACH_FILE")

for f in "$XYZ_FILE" "$TEXTURE_FILE" "$REACH_FILE"; do
  if [ -n "$f" ] && [ ! -f "$f" ]; then
    echo "ERROR: Input file not found: $f"
    exit 1
  fi
done

mkdir -p "$WORKSPACE"
echo "✓ Created workspace: $WORKSPACE"
echo ""

cd "$WORKSPACE"

# -ef compares the files themselves, so a symlinked workspace or any other
# spelling of the same path is still recognised as an input and never deleted.
is_input() {
  local given
  for given in "$XYZ_FILE" "$TEXTURE_FILE" "$REACH_FILE"; do
    [ -n "$given" ] && [ "$given" -ef "$1" ] && return 0
  done
  return 1
}

# Each step below is judged by whether its output appeared, so clear the
# outputs of an earlier run: a leftover file would make a failure look like
# success and describe the previous scene's terrain.
stale_outputs="normals_slope.uvw normals_arm.uvw
  slope.img heading.img ntilt.img solar.img roughness.img reach_goodness.img
  slope.png heading.png ntilt.png solar.png roughness.png normals.png
  reach_goodness.png scene.png"
# Every instrument, not just this run's: switching --instrument would otherwise
# leave the previous instrument's products sitting in the workspace.
for inst in heli seis hp3 wts; do
  stale_outputs="$stale_outputs
    tilt_$inst.img uix_$inst.img zix_$inst.img
    iroughness_$inst.img goodness_$inst.img
    tilt_$inst.png iroughness_$inst.png goodness_$inst.png"
done
for stale in $stale_outputs; do
  is_input "$stale" || rm -f "$stale"
done

# Convert a REAL/BYTE product to PNG through a fixed physical range, so the
# grey levels mean the same thing from run to run. vicario alone rescales on
# the actual min/max, which on a slope image is dominated by the near-vertical
# values at the horizon and leaves the terrain black.
#   $1 input  $2 output.png  $3 range low  $4 range high  $5 description
to_png() {
  local inp="$1" png="$2" lo="$3" hi="$4" what="$5"
  local tmp
  tmp="_stretch_$(basename "$png" .png).img"
  rm -f "$tmp"
  tig cform "$inp" "$tmp" oform=byte irange=\("$lo","$hi"\) orange=\(0,255\) \
    > /dev/null 2>&1 || true
  if [ ! -f "$tmp" ]; then
    echo "  ⚠ WARNING: cform failed for $inp; no $png written"
    return 0
  fi
  tig vicario "$tmp" "$png" > /dev/null 2>&1 || true
  rm -f "$tmp"
  if [ -f "$png" ]; then
    echo "  ✓ $png  ($what; black=$lo, white=$hi)"
  else
    echo "  ⚠ WARNING: vicario failed for $inp; no $png written"
  fi
}

echo "Step 1: Surface normals at rover scale (marsuvw -slope)..."
echo "  Plane fit over a rover-sized patch; this is what the slope products use."
tig marsuvw inp="$XYZ_FILE" out=normals_slope.uvw -slope \
  radius=10 separation=0.5 error=0.02 box_radius=1000 coord="$COORD" 2>&1 |
  grep -E "MARSUVW|Interpreting|Generating|camera model" || true

if [ ! -f normals_slope.uvw ]; then
  echo "❌ ERROR: marsuvw failed to generate normals_slope.uvw"
  exit 1
fi
echo "  ✓ Rover-scale normals: normals_slope.uvw"
echo ""

echo "Step 2: Slope products (marsslope)..."
for type in slope heading ntilt; do
  tig marsslope inp="$XYZ_FILE" uvw=normals_slope.uvw out="$type.img" \
    type="$type" coord="$COORD" 2>&1 |
    grep -E "Calculating|Output #" || true
  if [ ! -f "$type.img" ]; then
    echo "❌ ERROR: marsslope type=$type produced no output"
    exit 1
  fi
done
if [ -n "$SOLAR_ANGLE" ]; then
  tig marsslope inp="$XYZ_FILE" uvw=normals_slope.uvw out=solar.img \
    type=solar sa="$SOLAR_ANGLE" coord="$COORD" 2>&1 |
    grep -E "Calculating|Output #" || true
fi
echo "  ✓ slope.img (degrees), heading.img (degrees), ntilt.img (degrees)"
[ -f solar.img ] && echo "  ✓ solar.img (relative insolation, sa=$SOLAR_ANGLE deg)"
echo ""

echo "Step 3: Surface normals at instrument scale (marsuvw)..."
echo "  Smaller patch, tighter plane fit: what marsrough expects."
tig marsuvw inp="$XYZ_FILE" out=normals_arm.uvw x_center=2 box_radius=5 2>&1 |
  grep -E "MARSUVW|Generating" || true

if [ ! -f normals_arm.uvw ]; then
  echo "❌ ERROR: marsuvw failed to generate normals_arm.uvw"
  exit 1
fi
echo "  ✓ Instrument-scale normals: normals_arm.uvw"
echo ""

echo "Step 4: Surface roughness (marsrough)..."
echo "  Peak-to-peak deviation from the local plane, in metres."
tig marsrough inp="$XYZ_FILE" uvw=normals_arm.uvw out=roughness.img \
  x_center=2 y_center=0 box_radius=5 max_rough=0.05 bad_rough=0.1 2>&1 |
  grep -E "MARSROUGH|Generating" || true

if [ ! -f roughness.img ]; then
  echo "❌ ERROR: marsrough failed to generate roughness.img"
  exit 1
fi
echo "  ✓ roughness.img (metres; 0.1 = could not be computed)"
echo ""

echo "Step 5: Instrument placement tilt (marsitilt -$INSTRUMENT)..."
echo "  Tilt the instrument would have if placed at each pixel."
tig marsitilt inp="$XYZ_FILE" out="tilt_$INSTRUMENT.img" \
  uix_out="uix_$INSTRUMENT.img" zix_out="zix_$INSTRUMENT.img" \
  "-$INSTRUMENT" 2>&1 |
  grep -E "MARSITILT|Generating|Clock range" || true

if [ ! -f "tilt_$INSTRUMENT.img" ]; then
  echo "❌ ERROR: marsitilt failed to generate tilt_$INSTRUMENT.img"
  exit 1
fi
echo "  ✓ tilt_$INSTRUMENT.img (band 1 status, band 2 min tilt, band 3 max tilt)"
echo ""

echo "Step 6: Instrument placement roughness (marsirough -$INSTRUMENT)..."
# marsirough abends reading the 1-band ZIX file marsitilt just wrote, in the
# published image; the tilt status band alone still drives step 7.
irough_log=$(tig marsirough inp="$XYZ_FILE" out="iroughness_$INSTRUMENT.img" \
  uix="uix_$INSTRUMENT.img" zix="zix_$INSTRUMENT.img" "-$INSTRUMENT" 2>&1) || true
IROUGH_OK=false
if echo "$irough_log" | grep -q "ABEND"; then
  echo "  ⚠ marsirough abended; continuing without it:"
  echo "$irough_log" | grep -E "VIC2-|Current line|ABEND" | sed 's/^/      /'
  rm -f "iroughness_$INSTRUMENT.img"
elif [ -f "iroughness_$INSTRUMENT.img" ]; then
  IROUGH_OK=true
  echo "  ✓ iroughness_$INSTRUMENT.img (band 1 status, band 2 body, band 3 feet)"
else
  echo "  ⚠ marsirough produced no output; continuing without it"
fi
echo ""

echo "Step 7: Combined placement goodness (marsigood)..."
if $IROUGH_OK; then
  tig marsigood inp=\( "iroughness_$INSTRUMENT.img" "tilt_$INSTRUMENT.img" \) \
    out="goodness_$INSTRUMENT.img" band=\(1,1\) thresh=\(5,5\) 2>&1 |
    grep -E "MARSIGOOD|Complete" || true
else
  echo "  Only the tilt status band is available, so goodness reduces to it."
  tig marsigood inp="tilt_$INSTRUMENT.img" out="goodness_$INSTRUMENT.img" \
    band=1 thresh=5 2>&1 |
    grep -E "MARSIGOOD|Complete" || true
fi

if [ ! -f "goodness_$INSTRUMENT.img" ]; then
  echo "❌ ERROR: marsigood failed to generate goodness_$INSTRUMENT.img"
  exit 1
fi
echo "  ✓ goodness_$INSTRUMENT.img (5 = all inputs good, 0 = no data)"
echo ""

echo "Step 8: Arm reachability goodness (marsgreach)..."
if [ -n "$REACH_FILE" ]; then
  tig marsgreach inp="$REACH_FILE" out=reach_goodness.img 2>&1 |
    grep -E "MARSGREACH|Processing band" || true
  if [ -f reach_goodness.img ]; then
    echo "  ✓ reach_goodness.img (0 unreachable, 1/3/5 increasing goodness)"
  else
    echo "  ⚠ WARNING: marsgreach produced no output"
  fi
else
  echo "  Skipped: marsgreach collapses a 6-band arm reachability product, which"
  echo "  is produced by the mission arm-reachability program (M20REACH for"
  echo "  Mars 2020) and cannot be derived from an XYZ cloud. That program is"
  echo "  not in this image. Pass one with --reach FILE to run this stage."
fi
echo ""

echo "Step 9: Converting products to PNG..."
to_png slope.img slope.png 0 30 "slope, degrees"
to_png heading.img heading.png -180 180 "slope heading, degrees"
to_png ntilt.img ntilt.png -30 30 "northerly tilt, degrees"
[ -f solar.img ] && to_png solar.img solar.png 0 1 "relative solar energy"
to_png roughness.img roughness.png 0 0.05 "roughness, metres"
to_png normals_slope.uvw normals.png -1 1 "surface normal U/V/W as R/G/B"
to_png "tilt_$INSTRUMENT.img" "tilt_$INSTRUMENT.png" 0 5 \
  "placement tilt: status, min tilt, max tilt as R/G/B"
$IROUGH_OK && to_png "iroughness_$INSTRUMENT.img" "iroughness_$INSTRUMENT.png" \
  0 0.05 "placement roughness as R/G/B"
to_png "goodness_$INSTRUMENT.img" "goodness_$INSTRUMENT.png" 0 5 \
  "placement goodness"
[ -f reach_goodness.img ] && to_png reach_goodness.img reach_goodness.png 0 5 \
  "reachability goodness"
if [ -n "$TEXTURE_FILE" ]; then
  tig vicario "$TEXTURE_FILE" scene.png > /dev/null 2>&1 || true
  [ -f scene.png ] && echo "  ✓ scene.png (the scene these products describe)"
fi
echo ""

echo "Step 10: Results summary"
ls -lh ./*.img ./*.uvw ./*.png 2>/dev/null || true
echo ""

echo "=== Demo Complete ==="
echo ""
echo "Generated files in: $WORKSPACE"
echo "  - slope.img/.png             : Surface slope in degrees (0 = horizontal)"
echo "  - heading.img/.png           : Azimuth the slope faces, degrees"
echo "  - ntilt.img/.png             : North-facing component of slope, degrees"
[ -f solar.img ] && echo "  - solar.img/.png             : Relative solar energy from tilt alone"
echo "  - roughness.img/.png         : Peak-to-peak deviation from local plane, metres"
echo "  - normals_slope.uvw          : Rover-scale surface normals (3-band unit vectors)"
echo "  - normals_arm.uvw            : Instrument-scale surface normals"
echo "  - tilt_$INSTRUMENT.img            : Placement tilt (status, min, max)"
$IROUGH_OK && echo "  - iroughness_$INSTRUMENT.img      : Placement roughness (status, body, feet)"
echo "  - goodness_$INSTRUMENT.img        : Combined placement goodness, 0-5"
[ -f reach_goodness.img ] && echo "  - reach_goodness.img         : Arm reachability goodness, 0-5"
echo ""
echo "What these are for: slope and roughness bound where the rover may drive,"
echo "the normals feed both, and the goodness rasters say where hardware can"
echo "actually be put down. See docs/demos/surface-characteristics.md."
echo ""
echo "The container stays up for the next run. To remove it:"
echo "  tig --shutdown"
