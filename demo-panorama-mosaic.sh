#!/bin/bash
set -e

echo "=== Terrain Intelligence Generator - In-Situ Panorama Mosaic Demo ==="
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

# Source the helper here, but look up calibration after argument parsing so
# --help is not delayed by the helper's container-image probe.
if [ -f "$SCRIPT_DIR/find-calibration.sh" ]; then
    source "$SCRIPT_DIR/find-calibration.sh"
else
    # Fallback to default location if helper not found
    CALIB_DIR="$(pwd)/terrain-intelligence-generator/docker/mars_calibration_m20"
fi

# Parse arguments
PROJECTION="cylindrical"
LEFTAZ=""
RIGHTAZ=""
TOPEL=""
BOTTOMEL=""
UPAZ=""
MINX=""
MAXX=""
MINY=""
MAXY=""
VERT_SCALE=""
WRAP_AZ=""
ZOOM=""
GRID="nogrid"
BBOX=0
BRIGHTNESS_MATCH=1
STRETCH=1
FRAMES=()

print_usage() {
  cat << 'EOF'
Usage: demo-panorama-mosaic.sh [OPTIONS] FRAME [FRAME ...]

Builds an in-situ panorama from surface camera frames taken from one rover
position: marsrad (radiometric correction) -> marsmap (overlap statistics) ->
marsbrt (brightness match) -> marsmap (mosaic) -> stretch + vicario (PNG).

Frames must come from one site/drive; azimuth and elevation are measured about
the landing site, so frames taken after a drive do not belong in the same
mosaic. Orbital imagery is NOT handled here - see the doc.

Options:
  --projection NAME    cylindrical (default), polar or vertical
  --left-az DEG        Azimuth of the left edge      (cylindrical; default 0)
  --right-az DEG       Azimuth of the right edge     (cylindrical; default 360)
  --auto-extent        Let marsmap fit the extent to the frames instead
  --top-el DEG         Elevation of the top edge     (cylindrical, polar)
  --bottom-el DEG      Elevation of the bottom edge  (cylindrical)
  --up-az DEG          Azimuth placed at the top of the image (polar)
  --min-x / --max-x M  Extent in X, metres, +X north (vertical)
  --min-y / --max-y M  Extent in Y, metres, +Y east  (vertical)
  --vert-scale M       Metres per pixel              (vertical)
  --wrap-az DEG        Azimuth at which a full 360 mosaic is cut
  --zoom FACTOR        Scale relative to the camera's natural scale
  --grid               Draw the az/el grid under the imagery
  --bbox               Also write each frame's footprint polygon as CSV/WKT
  --no-brightness-match  Skip the marsmap-overlap/marsbrt seam matching
  --no-stretch         Write the PNG without the 2% display stretch
  --help, -h           This message

Examples:
  # 360-degree cylindrical panorama from one sol's NavCam frames
  ./demo-panorama-mosaic.sh /path/to/NLF_0300_*NCAM00501*.IMG

  # Same frames as a polar (nadir-centred) projection
  ./demo-panorama-mosaic.sh --projection polar --top-el 30 \
    /path/to/NLF_0300_*NCAM00501*.IMG

  # Vertical (map-like) projection of the ground within 15 m
  ./demo-panorama-mosaic.sh --projection vertical \
    --min-x -15 --max-x 15 --min-y -15 --max-y 15 --vert-scale 0.03 \
    /path/to/NLF_0300_*NCAM00501*.IMG

Requirements:
  - tig-cli installed (pip install tig-cli) and a running Docker daemon
  - Mission calibration for the instrument: MARS_CONFIG_PATH, or an image
    that bundles it (CONTAINER_IMAGE=...:fullfeatured)
  - Frames from a single site/drive, overlapping in azimuth
EOF
  exit 1
}

require_value() {
  [ -n "$2" ] || { echo "ERROR: $1 requires a value"; print_usage; }
}

while [[ $# -gt 0 ]]; do
  case $1 in
    --projection)  require_value "$1" "$2"; PROJECTION=$(echo "$2" | tr '[:upper:]' '[:lower:]'); shift 2 ;;
    --left-az)     require_value "$1" "$2"; LEFTAZ="$2"; shift 2 ;;
    --right-az)    require_value "$1" "$2"; RIGHTAZ="$2"; shift 2 ;;
    --auto-extent) LEFTAZ="auto"; RIGHTAZ="auto"; shift ;;
    --top-el)      require_value "$1" "$2"; TOPEL="$2"; shift 2 ;;
    --bottom-el)   require_value "$1" "$2"; BOTTOMEL="$2"; shift 2 ;;
    --up-az)       require_value "$1" "$2"; UPAZ="$2"; shift 2 ;;
    --min-x)       require_value "$1" "$2"; MINX="$2"; shift 2 ;;
    --max-x)       require_value "$1" "$2"; MAXX="$2"; shift 2 ;;
    --min-y)       require_value "$1" "$2"; MINY="$2"; shift 2 ;;
    --max-y)       require_value "$1" "$2"; MAXY="$2"; shift 2 ;;
    --vert-scale)  require_value "$1" "$2"; VERT_SCALE="$2"; shift 2 ;;
    --wrap-az)     require_value "$1" "$2"; WRAP_AZ="$2"; shift 2 ;;
    --zoom)        require_value "$1" "$2"; ZOOM="$2"; shift 2 ;;
    --grid)        GRID="grid"; shift ;;
    --bbox)        BBOX=1; shift ;;
    --no-brightness-match) BRIGHTNESS_MATCH=0; shift ;;
    --no-stretch)  STRETCH=0; shift ;;
    --help|-h)     print_usage ;;
    -*)            echo "ERROR: Unknown option: $1"; print_usage ;;
    *)             FRAMES+=("$1"); shift ;;
  esac
done

case "$PROJECTION" in
  cylindrical|polar|vertical) ;;
  *)
    echo "ERROR: --projection must be cylindrical, polar or vertical (got: $PROJECTION)"
    echo "  marsmap also implements an experimental SINUSOIDAL projection, which"
    echo "  its own documentation calls not fully implemented or tested."
    exit 1
    ;;
esac

if [ ${#FRAMES[@]} -lt 2 ]; then
  echo "ERROR: give at least two frames to mosaic"
  print_usage
fi

# marsmap's help asks for the azimuth limits to be given by hand near 360
# degrees rather than fitted to the frames.
if [ "$PROJECTION" = "cylindrical" ]; then
  [ -z "$LEFTAZ" ] && LEFTAZ=0
  [ -z "$RIGHTAZ" ] && RIGHTAZ=360
fi

if declare -f calibration_setup > /dev/null; then
  # Guard the call: under 'set -e' a failed lookup would otherwise end the
  # script before the help below is printed.
  calibration_setup || true
  if [ -z "$CALIB_DIR" ] && [ -z "$CALIB_IN_IMAGE" ]; then
    echo "ERROR: MARS calibration not found."
    echo ""
    echo "A panorama needs it twice over: marsrad reads flat fields and"
    echo "responsivity from it, and marsmap needs the camera model to know"
    echo "where each frame points."
    echo ""
    print_calibration_help
    exit 1
  fi
fi

if [ -n "${CALIB_IN_IMAGE:-}" ]; then
  # Bundled in the image: nothing to mount, and MARS_CONFIG_PATH must stay
  # unset, since it is read as a host path to mount.
  echo "Using calibration ${CALIB_IN_IMAGE_DESC:-already in the container}: $CALIB_IN_IMAGE"
  unset MARS_CONFIG_PATH
else
  echo "Using calibration from: $CALIB_DIR"
  if [ ! -d "$CALIB_DIR" ]; then
    echo "ERROR: Calibration directory not accessible"
    exit 1
  fi

  # Resolve before the cd below, where a relative calibration path would not.
  CALIB_DIR="$(cd "$CALIB_DIR" && pwd)"
  export MARS_CONFIG_PATH="$CALIB_DIR"
fi

# Resolve inputs before changing directory, so relative paths keep working
abspath() {
  local dir
  # Fall back to the path as given, so the file checks below report it.
  dir=$(cd "$(dirname "$1")" 2>/dev/null && pwd) || { echo "$1"; return 0; }
  echo "${dir%/}/$(basename "$1")"
}

for i in "${!FRAMES[@]}"; do
  FRAMES[$i]=$(abspath "${FRAMES[$i]}")
  if [ ! -f "${FRAMES[$i]}" ]; then
    echo "ERROR: frame not found: ${FRAMES[$i]}"
    exit 1
  fi
done

# The stale-output cleanup below would delete an input living in the workspace.
for frame in "${FRAMES[@]}"; do
  case "$frame" in
    "$WORKSPACE"/*)
      echo "ERROR: frame lives in the workspace this run would overwrite:"
      echo "         $frame"
      echo "       Run from a different directory, or copy the frames out."
      exit 1
      ;;
  esac
done

mkdir -p "$WORKSPACE"
echo "✓ Created workspace: $WORKSPACE"
echo "  Frames to mosaic: ${#FRAMES[@]}"
echo "  Projection: $PROJECTION"
echo ""

cd "$WORKSPACE"

# Each step is judged by whether its output appeared, so an earlier run's
# outputs would make a failure look like success.
rm -rf panorama_rad
rm -f panorama_frames.txt panorama_overlaps.xml panorama_overlap_mosaic.img \
      panorama_brtcorr.xml panorama.img panorama_stretched.img panorama.png \
      panorama_bbox.csv

# Step 1: Radiometric correction. marsmap would do this itself, but a separate
# step keeps the corrected frames for marsbrt and for further mosaics.
echo "Step 1: Radiometric correction (marsrad)..."
mkdir -p panorama_rad
# The list file fixes the frame order, which is the stacking order in the
# mosaic; the index prefix keeps same-named frames from colliding.
: > panorama_frames.txt
for i in "${!FRAMES[@]}"; do
  frame="${FRAMES[$i]}"
  out=$(printf 'panorama_rad/%03d_%s.rad.img' "$i" "$(basename "${frame%.*}")")
  tig marsrad inp="$frame" out="$out" > /dev/null 2>&1 || true
  if [ ! -f "$out" ]; then
    echo "  ❌ ERROR: marsrad produced nothing for $(basename "$frame")"
    echo "     Check that the calibration covers this instrument:"
    echo "       tig marsrad inp=$frame out=/tmp/x.img"
    exit 1
  fi
  echo "$out" >> panorama_frames.txt
  echo "  ✓ $(basename "$out")"
done
echo ""

# Assembled once: the overlap pass and the mosaic pass have to agree, or the
# statistics describe pixels other than the ones being corrected.
PROJ_ARGS=(projection="$PROJECTION")
add_arg() { [ -n "$2" ] && [ "$2" != "auto" ] && PROJ_ARGS+=("$1=$2"); return 0; }
case "$PROJECTION" in
  cylindrical)
    add_arg leftaz "$LEFTAZ"; add_arg rightaz "$RIGHTAZ"
    add_arg topel "$TOPEL";   add_arg bottomel "$BOTTOMEL"
    ;;
  polar)
    add_arg topel "$TOPEL"; add_arg up_az "$UPAZ"
    ;;
  vertical)
    add_arg minx "$MINX"; add_arg maxx "$MAXX"
    add_arg miny "$MINY"; add_arg maxy "$MAXY"
    add_arg vert_scale "$VERT_SCALE"
    ;;
esac
add_arg wrap_az "$WRAP_AZ"

# Step 2 + 3: brightness matching. Frames minutes apart pointing different ways
# leave visible seams, so marsmap measures the overlaps and marsbrt solves a
# per-frame correction. The overlap pass is a full mosaic run, hence zoomed out.
if [ "$BRIGHTNESS_MATCH" -eq 1 ]; then
  echo "Step 2: Measuring frame overlaps (marsmap ovr_out)..."
  tig marsmap inp=panorama_frames.txt out=panorama_overlap_mosaic.img \
    ovr_out=panorama_overlaps.xml -norad -nogrid zoom=0.25 \
    "${PROJ_ARGS[@]}" 2>&1 | grep -E "Projection|Pixel scale|Output lines" || true

  if [ ! -f panorama_overlaps.xml ]; then
    echo "  ❌ ERROR: marsmap wrote no overlap file"
    exit 1
  fi
  echo "  ✓ $(grep -c "<overlap " panorama_overlaps.xml) overlaps measured"
  echo ""

  echo "Step 3: Solving brightness corrections (marsbrt)..."
  # DO_MULT solves one gain per frame. The default DO_LINEAR also solves an
  # offset, which on a five-frame ring is not always well conditioned.
  tig marsbrt inp=panorama_frames.txt in_ovr=panorama_overlaps.xml \
    out=panorama_brtcorr.xml out_solution_id=TIGDEMO do_what=DO_MULT 2>&1 |
    grep -A100 "^Image  " || true

  if [ ! -f panorama_brtcorr.xml ]; then
    echo "  ⚠ WARNING: marsbrt wrote no correction file; mosaicking uncorrected"
  fi
  echo ""
else
  echo "Steps 2-3: brightness matching skipped (--no-brightness-match)"
  echo ""
fi

# Step 4: The mosaic itself
echo "Step 4: Building the mosaic (marsmap)..."
MAP_ARGS=(inp=panorama_frames.txt out=panorama.img -norad "-$GRID" "${PROJ_ARGS[@]}")
[ -f panorama_brtcorr.xml ] && MAP_ARGS+=(brtcorr=panorama_brtcorr.xml)
[ -n "$ZOOM" ] && MAP_ARGS+=(zoom="$ZOOM")
[ "$BBOX" -eq 1 ] && MAP_ARGS+=(bbox=panorama_bbox.csv)

tig marsmap "${MAP_ARGS[@]}" 2>&1 |
  grep -E "Projection|Pixel scale|azimuth of first sample|line of zero elevation|Output lines" || true

if [ ! -f panorama.img ]; then
  echo "  ❌ ERROR: marsmap produced no mosaic"
  exit 1
fi
echo "  ✓ panorama.img"
if [ "$BBOX" -eq 1 ]; then
  if [ -f panorama_bbox.csv ]; then
    # A frame gets two rows when its footprint straddles the wrap azimuth, and
    # the polygon gains three points closing it to the edge over a pole.
    echo "  ✓ panorama_bbox.csv ($(($(wc -l < panorama_bbox.csv) - 1)) polygons for ${#FRAMES[@]} frames)"
  else
    echo "  ⚠ WARNING: marsmap wrote no bounding-box file"
  fi
fi
echo ""

# Step 5: PNG. The mosaic holds scaled radiance and a Mars scene uses little of
# that range, so stretch the middle 98% for display; panorama.img is untouched.
echo "Step 5: Converting to PNG..."
PNG_INPUT=panorama.img
if [ "$STRETCH" -eq 1 ]; then
  tig stretch inp=panorama.img out=panorama_stretched.img \
    -astretch percent=2 dnmin=0 dnmax=255 2>&1 | grep -E "AUTO-STRETCH:" || true
  if [ -f panorama_stretched.img ]; then
    PNG_INPUT=panorama_stretched.img
  else
    STRETCH=0
    echo "  ⚠ WARNING: stretch wrote nothing; the PNG comes from the raw"
    echo "    radiance mosaic and will look nearly black."
  fi
fi

conversion_log=$(tig vicario "$PNG_INPUT" panorama.png 2>&1) || true
if [ -f panorama.png ]; then
  echo "  ✓ panorama.png"
else
  echo "$conversion_log"
  echo "  ⚠ WARNING: PNG conversion failed; panorama.img is still there."
fi
echo ""

echo "Step 6: Results summary"
ls -lh panorama.img panorama.png 2>/dev/null || true
echo ""

echo "=== Demo Complete ==="
echo ""
echo "Generated files in: $WORKSPACE"
echo "  - panorama.img          : Mosaic, VICAR, with the projection in its label"
echo "  - panorama.png          : Same mosaic as PNG$([ "$STRETCH" -eq 1 ] && echo " (display stretch applied)")"
echo "  - panorama_rad/         : Radiometrically corrected frames"
if [ "$BRIGHTNESS_MATCH" -eq 1 ]; then
  echo "  - panorama_overlaps.xml : Overlap statistics from marsmap"
  echo "  - panorama_brtcorr.xml  : Per-frame brightness corrections from marsbrt"
fi
echo ""
echo "The label of panorama.img carries everything needed to turn a pixel back"
echo "into an azimuth/elevation view ray:"
echo "  tig label -list inp=panorama.img | less"
echo ""
echo "The container stays up for the next run. To remove it:"
echo "  tig --shutdown"
