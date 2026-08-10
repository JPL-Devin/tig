#!/bin/bash
set -e

echo "=== Terrain Intelligence Generator - Co-registration Demo ==="
echo ""

# Every VICAR tool below runs through tig, which starts and reuses the
# container, mounts this workspace, and translates host paths.
WORKSPACE="$(pwd)/workspace-coreg"

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
    echo "ERROR: find-calibration.sh not found next to this script."
    echo "Set MARS_CONFIG_PATH to a mission calibration directory and retry."
    exit 1
fi

IMAGE_LIST=""
IMAGES=()
OVERLAP=40
DENSITY=50
GRID_SPACING=15
MAX_RESIDUAL=10
SOLUTION_ID="COREG"
RUN_NAV2=false

print_usage() {
  echo "Usage: $0 [OPTIONS] [IMAGE ...]"
  echo ""
  echo "Co-register a set of overlapping images: tiepoints (marsautotie) ->"
  echo "pointing solution (marsnav) -> nav table consumed by the mosaickers."
  echo ""
  echo "Options:"
  echo "  --list FILE          Text file listing input images, one per line"
  echo "  --overlap PCT        marschkovl minimum overlap percentage (default: $OVERLAP)"
  echo "  --density N          marsautotie tiepoint density; lower = more (default: $DENSITY)"
  echo "  --grid-spacing N     marsautotie candidate grid spacing (default: $GRID_SPACING)"
  echo "  --max-residual PX    marsnav outlier removal threshold (default: $MAX_RESIDUAL)"
  echo "  --solution-id ID     Solution ID written into the nav table (default: $SOLUTION_ID)"
  echo "  --nav2               Also run marsnav2 (Ceres bundle adjustment) for comparison"
  echo ""
  echo "Examples:"
  echo "  $0 --list frames.lis"
  echo "  $0 --density 100 --max-residual 5 NLB_*.IMG"
  echo ""
  echo "Requirements:"
  echo "  - tig-cli installed (pip install tig-cli) and a running Docker daemon"
  echo "  - Mission calibration on MARS_CONFIG_PATH (see docs/reference/calibration-data.md)"
  echo "  - Images from the same site with real overlap and usable initial pointing"
  echo "  - Up to 200 images (marsautotie/marsnav limit)"
  exit 1
}

while [[ $# -gt 0 ]]; do
  case $1 in
    --list)
      [ -z "$2" ] && { echo "ERROR: --list requires a FILE argument"; print_usage; }
      IMAGE_LIST="$2"
      shift 2
      ;;
    --overlap)
      [ -z "$2" ] && { echo "ERROR: --overlap requires a value"; print_usage; }
      OVERLAP="$2"
      shift 2
      ;;
    --density)
      [ -z "$2" ] && { echo "ERROR: --density requires a value"; print_usage; }
      DENSITY="$2"
      shift 2
      ;;
    --grid-spacing)
      [ -z "$2" ] && { echo "ERROR: --grid-spacing requires a value"; print_usage; }
      GRID_SPACING="$2"
      shift 2
      ;;
    --max-residual)
      [ -z "$2" ] && { echo "ERROR: --max-residual requires a value"; print_usage; }
      MAX_RESIDUAL="$2"
      shift 2
      ;;
    --solution-id)
      [ -z "$2" ] && { echo "ERROR: --solution-id requires a value"; print_usage; }
      SOLUTION_ID="$2"
      shift 2
      ;;
    --nav2)
      RUN_NAV2=true
      shift
      ;;
    --help|-h)
      print_usage
      ;;
    -*)
      echo "ERROR: Unknown option: $1"
      print_usage
      ;;
    *)
      IMAGES+=("$1")
      shift
      ;;
  esac
done

abspath() {
  [ -z "$1" ] && return 0
  local dir
  # Fall back to the path as given, so the file checks below report it.
  dir=$(cd "$(dirname "$1")" 2>/dev/null && pwd) || { echo "$1"; return 0; }
  echo "${dir%/}/$(basename "$1")"
}

# Build one absolute-path list: the MARS programs read the list from inside the
# container, where a relative path would resolve against a different directory.
INPUTS=()
if [ -n "$IMAGE_LIST" ]; then
  IMAGE_LIST=$(abspath "$IMAGE_LIST")
  if [ ! -f "$IMAGE_LIST" ]; then
    echo "ERROR: Image list not found: $IMAGE_LIST"
    exit 1
  fi
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    INPUTS+=("$(abspath "$line")")
  done < "$IMAGE_LIST"
fi
for image in "${IMAGES[@]}"; do
  INPUTS+=("$(abspath "$image")")
done

if [ "${#INPUTS[@]}" -lt 2 ]; then
  echo "ERROR: At least two input images are required"
  print_usage
fi

for input in "${INPUTS[@]}"; do
  if [ ! -f "$input" ]; then
    echo "ERROR: Input image not found: $input"
    exit 1
  fi
done

echo "Using calibration from: $CALIB_DIR"
# tig mounts this read-only at /usr/local/vicar/mars_calib and points the MARS
# programs at it. Resolve it now: tig runs from the workspace directory below,
# where a relative calibration path would no longer resolve.
CALIB_DIR="$(cd "$CALIB_DIR" && pwd)"
export MARS_CONFIG_PATH="$CALIB_DIR"

mkdir -p "$WORKSPACE"
echo "✓ Created workspace: $WORKSPACE"
echo ""
cd "$WORKSPACE"

# marstie and marsautotie refuse to overwrite an existing tiepoint file, and a
# leftover nav table would make a failed solve look like a success.
rm -f frames.lis overlap_left.lis overlap_right.lis marschkovl.log \
      tiepoints.tpt tiepoints_kept.tpt pointing.nav marsnav.log \
      tiepoints_nav2.tpt pointing_nav2.nav marsnav2.log

printf '%s\n' "${INPUTS[@]}" > frames.lis
echo "Input frames: ${#INPUTS[@]}"
echo ""

# Step 1: which frames actually overlap
echo "Step 1: Checking overlap (marschkovl)..."
# marschkovl prints an "Overlap for" line for every pair it evaluates, with the
# measured percentage; only the pairs above the threshold reach the two lists.
tig marschkovl inp=frames.lis out=\( overlap_left.lis overlap_right.lis \) \
  overlap="$OVERLAP" > marschkovl.log 2>&1
echo "  ✓ $(wc -l < overlap_left.lis) pair(s) with >= ${OVERLAP}% overlap"
echo "  (pairs listed in overlap_left.lis / overlap_right.lis;"
echo "   measured percentages for every pair in marschkovl.log)"
echo ""

# Step 2: tiepoints
echo "Step 2: Generating tiepoints (marsautotie)..."
tig marsautotie inp=frames.lis out=tiepoints.tpt \
  density="$DENSITY" grid_spacing="$GRID_SPACING" 2>&1 | \
  grep -E "Found [0-9]+ tiepoints|not represented" || true

if [ ! -f tiepoints.tpt ]; then
  echo "  ❌ ERROR: marsautotie produced no tiepoint file"
  echo "  Frames may not overlap, or initial pointing may be too far off."
  exit 1
fi
echo "  ✓ $(grep -c '<tie ' tiepoints.tpt) tiepoints in tiepoints.tpt"
echo ""

# Step 3: pointing solution. MARSNAV requires tiepoints to exist first.
echo "Step 3: Solving for corrected pointing (marsnav)..."
tig marsnav inp=frames.lis out=pointing.nav in_tpt=tiepoints.tpt \
  out_tpt=tiepoints_kept.tpt out_solution_id="$SOLUTION_ID" \
  -remove max_residual="$MAX_RESIDUAL" max_remove=50 2>&1 | tee marsnav.log | \
  grep -E "^Input image .* not represented|not connected" || true

if [ ! -f pointing.nav ]; then
  echo "  ❌ ERROR: marsnav produced no nav table"
  exit 1
fi

grep -m1 "Commanded mean pixel error" marsnav.log | \
  sed 's/^/  initial: /' || true
grep "Final solution mean pixel error" marsnav.log | tail -1 | \
  sed 's/^/  final:   /' || true
echo "  ✓ $(grep -c '<tie ' tiepoints_kept.tpt) of $(grep -c '<tie ' tiepoints.tpt) tiepoints kept"
echo "  ✓ Corrected pointing written to pointing.nav (solution id $SOLUTION_ID)"
echo ""
echo "  Per-image pointing corrections:"
sed -n '/Img ----corrected----/,$p' marsnav.log | sed 's/^/    /'
echo ""

# Step 4: optional bundle adjustment for comparison
if $RUN_NAV2; then
  echo "Step 4: Bundle adjustment (marsnav2)..."
  tig marsnav2 inp=frames.lis out=pointing_nav2.nav in_tpt=tiepoints.tpt \
    out_tpt=tiepoints_nav2.tpt out_solution_id="$SOLUTION_ID" \
    > marsnav2.log 2>&1 || true
  grep -E "initial tracks found|observations tracks|Solution mean error|Solution median error" \
    marsnav2.log | sed 's/^/  /' || true
  sed -n '/There are disconnected groups/,/^ *$/p' marsnav2.log | sed 's/^/  /' || true
  echo ""
fi

echo "=== Demo Complete ==="
echo ""
echo "Generated files in: $WORKSPACE"
echo "  - frames.lis         : Input list, absolute paths"
echo "  - overlap_*.lis      : Overlapping pairs found by marschkovl"
echo "  - marschkovl.log     : Measured overlap percentage for every pair"
echo "  - tiepoints.tpt      : Tiepoints from marsautotie (XML format)"
echo "  - tiepoints_kept.tpt : Tiepoints surviving marsnav outlier removal"
echo "  - pointing.nav       : Corrected pointing, solution id $SOLUTION_ID"
echo "  - marsnav.log        : Full solver printout, including residuals"
$RUN_NAV2 && echo "  - pointing_nav2.nav  : Bundle-adjusted pointing (marsnav2)"
echo ""
echo "The nav table is an input to the mosaic programs, which is where the"
echo "corrected pointing takes effect:"
echo "  tig marsmos inp=frames.lis out=mosaic.img navtable=pointing.nav"
echo "  tig marsmap inp=frames.lis out=mosaic.img navtable=pointing.nav"
echo ""
echo "The container stays up for the next run. To remove it:"
echo "  tig --shutdown"
