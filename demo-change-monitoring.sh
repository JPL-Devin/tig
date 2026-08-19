#!/bin/bash
set -e

echo "=== Terrain Intelligence Generator - Change Monitoring Demo ==="
echo ""

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE="$(pwd)/workspace-change"
CALIBRATION=""
OVERLAP=40
DENSITY=50
GRID_SPACING=40
MAX_RESIDUAL=5
SOLUTION_ID="CHANGE"
SCALE=""
BOUNDS=""
BAND=1
USE_BRTCORR=true
BOX=""
EPOCH1_SPECS=()
EPOCH2_SPECS=()
TIE_EXTRA_SPECS=()

print_usage() {
  cat << EOF
Usage: $0 --epoch1 FILE_OR_LIST --epoch2 FILE_OR_LIST [OPTIONS]

Co-register two Mars 2020 epochs, project them into one cylindrical frame,
normalise and difference them, and print registration control experiments.

Options:
  --epoch1 FILE_OR_LIST  Epoch 1 frame or .lis file; repeatable
  --epoch2 FILE_OR_LIST  Epoch 2 frame or .lis file; repeatable
  --tie-extra FILE_OR_LIST
                         Extra frames for the joint tiepoint/nav solve
  --calibration DIR      MARS calibration directory
  --workspace DIR        Output directory (default: $(pwd)/workspace-change)
  --overlap PCT          marschkovl threshold (default: $OVERLAP)
  --density N            marsautotie density (default: $DENSITY)
  --grid-spacing N       marsautotie grid spacing (default: $GRID_SPACING)
  --max-residual PX      marsnav removal threshold (default: $MAX_RESIDUAL)
  --solution-id ID       Navigation/brightness solution id (default: $SOLUTION_ID)
  --scale PX_PER_DEG     Cylindrical output scale (default: probe natural scale)
  --bounds "L R T B"     Azimuth/elevation bounds (default: probe bounds)
  --band N               Input band (default: $BAND)
  --box "SL SS NL NS"    Statistics rectangle (default: centred inner region)
  --no-brtcorr           Skip marsbrt and BRTCORR renderings
  --help, -h             Show this message

All MARS/VICAR programs are invoked through tig. Input lists may contain one
absolute or relative frame path per line.
EOF
  exit 1
}

require_value() {
  [ -n "${2:-}" ] || { echo "ERROR: $1 requires a value"; print_usage; }
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --epoch1)
      require_value "$1" "$2"
      EPOCH1_SPECS+=("$2")
      shift 2
      ;;
    --epoch2)
      require_value "$1" "$2"
      EPOCH2_SPECS+=("$2")
      shift 2
      ;;
    --tie-extra)
      require_value "$1" "$2"
      TIE_EXTRA_SPECS+=("$2")
      shift 2
      ;;
    --calibration)
      require_value "$1" "$2"
      CALIBRATION="$2"
      shift 2
      ;;
    --workspace)
      require_value "$1" "$2"
      WORKSPACE="$2"
      shift 2
      ;;
    --overlap)
      require_value "$1" "$2"
      OVERLAP="$2"
      shift 2
      ;;
    --density)
      require_value "$1" "$2"
      DENSITY="$2"
      shift 2
      ;;
    --grid-spacing)
      require_value "$1" "$2"
      GRID_SPACING="$2"
      shift 2
      ;;
    --max-residual)
      require_value "$1" "$2"
      MAX_RESIDUAL="$2"
      shift 2
      ;;
    --solution-id)
      require_value "$1" "$2"
      SOLUTION_ID="$2"
      shift 2
      ;;
    --scale)
      require_value "$1" "$2"
      SCALE="$2"
      shift 2
      ;;
    --bounds)
      require_value "$1" "$2"
      BOUNDS="$2"
      shift 2
      ;;
    --band)
      require_value "$1" "$2"
      BAND="$2"
      shift 2
      ;;
    --box)
      require_value "$1" "$2"
      BOX="$2"
      shift 2
      ;;
    --no-brtcorr)
      USE_BRTCORR=false
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
      echo "ERROR: Unexpected argument: $1"
      print_usage
      ;;
  esac
done

if ! command -v tig >/dev/null 2>&1; then
  echo "ERROR: tig not found on PATH."
  echo "Install it with: pip install tig-cli"
  exit 1
fi

if [ "${#EPOCH1_SPECS[@]}" -eq 0 ] || [ "${#EPOCH2_SPECS[@]}" -eq 0 ]; then
  echo "ERROR: --epoch1 and --epoch2 are both required"
  print_usage
fi

abspath() {
  readlink -f "$1" 2>/dev/null && return 0
  local dir
  dir=$(cd "$(dirname "$1")" 2>/dev/null && pwd -P) || {
    echo "$1"
    return 0
  }
  echo "${dir%/}/$(basename "$1")"
}

container_path() {
  case "$1" in
    "$HOME"/*) echo "$1" ;;
    *) echo "/host$1" ;;
  esac
}

append_spec() {
  local spec="$1"
  local target_name="$2"
  local resolved
  local line
  resolved=$(abspath "$spec")
  if [[ "$resolved" == *.lis ]]; then
    [ -f "$resolved" ] || {
      echo "ERROR: Input list not found: $resolved"
      exit 1
    }
    while IFS= read -r line || [ -n "$line" ]; do
      [ -n "$line" ] || continue
      line=$(abspath "$line")
      [ -f "$line" ] || {
        echo "ERROR: Input image not found: $line"
        exit 1
      }
      eval "$target_name+=(\"\$line\")"
    done < "$resolved"
  else
    [ -f "$resolved" ] || {
      echo "ERROR: Input image not found: $resolved"
      exit 1
    }
    eval "$target_name+=(\"\$resolved\")"
  fi
}

INPUTS1=()
INPUTS2=()
TIE_EXTRA=()
for spec in "${EPOCH1_SPECS[@]}"; do append_spec "$spec" INPUTS1; done
for spec in "${EPOCH2_SPECS[@]}"; do append_spec "$spec" INPUTS2; done
for spec in "${TIE_EXTRA_SPECS[@]}"; do append_spec "$spec" TIE_EXTRA; done

[ "${#INPUTS1[@]}" -gt 0 ] || { echo "ERROR: epoch 1 contains no frames"; exit 1; }
[ "${#INPUTS2[@]}" -gt 0 ] || { echo "ERROR: epoch 2 contains no frames"; exit 1; }

if [ -n "$CALIBRATION" ]; then
  CALIBRATION=$(abspath "$CALIBRATION")
  export MARS_CONFIG_PATH="$CALIBRATION"
fi

if [ -f "$SCRIPT_DIR/find-calibration.sh" ]; then
  # shellcheck disable=SC1091
  source "$SCRIPT_DIR/find-calibration.sh"
  CALIB_DIR=$(find_calibration) || true
  if [ -z "$CALIB_DIR" ] || ! verify_calibration "$CALIB_DIR"; then
    echo "ERROR: MARS calibration not found."
    echo ""
    print_calibration_help
    exit 1
  fi
else
  echo "ERROR: find-calibration.sh not found next to this script."
  exit 1
fi

CALIB_DIR=$(cd "$CALIB_DIR" && pwd)
export MARS_CONFIG_PATH="$CALIB_DIR"
WORKSPACE=$(abspath "$WORKSPACE")
mkdir -p "$WORKSPACE"
cd "$WORKSPACE"

rm -rf labels
rm -f -- ./*.lis ./*.log ./*.img ./*.nav ./*.tpt ./*.brt ./*.ovr \
      ./*.png ./stats_*.txt ./maxmin_*.txt
mkdir -p labels

echo "Using calibration from: $CALIB_DIR"
echo "Created workspace: $WORKSPACE"
echo "Epoch 1 frames: ${#INPUTS1[@]}"
echo "Epoch 2 frames: ${#INPUTS2[@]}"
echo "Tie-extra frames: ${#TIE_EXTRA[@]}"
echo ""

write_list() {
  local output="$1"
  shift
  : > "$output"
  local input
  for input in "$@"; do
    container_path "$input" >> "$output"
  done
}

write_list epoch1.lis "${INPUTS1[@]}"
write_list epoch2.lis "${INPUTS2[@]}"
write_list joint.lis "${INPUTS1[@]}" "${INPUTS2[@]}" "${TIE_EXTRA[@]}"
write_list epochs.lis "${INPUTS1[@]}" "${INPUTS2[@]}"

run_logged() {
  local log="$1"
  shift
  if ! "$@" > "$log" 2>&1; then
    echo "ERROR: command failed; see $WORKSPACE/$(basename "$log")" >&2
    tail -20 "$log" >&2 || true
    exit 1
  fi
}

label_field() {
  local file="$1"
  local field="$2"
  awk -v key="$field" '
    $0 ~ "^" key "=" {
      value=$0
      sub("^[^=]*=", "", value)
      gsub(/^'\''|'\''$/, "", value)
      print value
      exit
    }
  ' "$file"
}

echo "Step 1: Input exposure and solar geometry"
printf '%-3s %-62s %10s %-22s %12s %14s\n' \
  "#" "FRAME" "EXPOSURE_MS" "LOCAL_TRUE_SOLAR_TIME" "SOLAR_AZ" "SOLAR_EL"
EXPOSURES=()
ALL_INPUTS=("${INPUTS1[@]}" "${INPUTS2[@]}" "${TIE_EXTRA[@]}")
for index in "${!ALL_INPUTS[@]}"; do
  input="${ALL_INPUTS[$index]}"
  label_log=$(printf 'labels/%03d.log' "$((index + 1))")
  run_logged "$label_log" tig label -list inp="$input"
  exposure=$(label_field "$label_log" EXPOSURE_DURATION)
  local_time=$(label_field "$label_log" LOCAL_TRUE_SOLAR_TIME)
  solar_az=$(label_field "$label_log" SOLAR_AZIMUTH)
  solar_el=$(label_field "$label_log" SOLAR_ELEVATION)
  EXPOSURES+=("$exposure")
  printf '%-3s %-62s %10s %-22s %12s %14s\n' \
    "$((index + 1))" "$(basename "$input")" "$exposure" "$local_time" "$solar_az" "$solar_el"
done
if awk -v a="${EXPOSURES[0]}" -v b="${EXPOSURES[${#INPUTS1[@]}]}" \
  'BEGIN { d=(a>b?a-b:b-a); m=(a>b?a:b); exit !(m > 0 && d/m > 0.20) }'; then
  echo "WARNING: epoch exposure durations differ by more than 20%."
  echo "         Exposure brackets should be matched before comparing epochs."
fi
echo ""

echo "Step 2: Finding overlapping pairs (marschkovl)"
run_logged marschkovl.log tig marschkovl inp=joint.lis \
  out=\( ovl.lis ovr.lis \) overlap="$OVERLAP"
ACCEPTED_PAIRS=$(wc -l < ovl.lis)
echo "Accepted pairs: $ACCEPTED_PAIRS (threshold ${OVERLAP}%)"
awk '
  /^Overlap for / {
    ref=$0
    sub(/^.*ref=/, "", ref)
    sub(/ test=.*$/, "", ref)
    test=$0
    sub(/^.* test=/, "", test)
    sub(/ [0-9.]+$/, "", test)
    pct=$NF
    printf "  %s <-> %s: %s%%\n", ref, test, pct
  }
' marschkovl.log
echo ""

echo "Step 3: Generating joint tiepoints (marsautotie)"
run_logged marsautotie.log tig marsautotie inp=joint.lis \
  out=tiepoints.tpt density="$DENSITY" grid_spacing="$GRID_SPACING"
TOTAL_TIEPOINTS=$(grep -c '<tie ' tiepoints.tpt || true)
echo "Tiepoints: $TOTAL_TIEPOINTS"
[ "$TOTAL_TIEPOINTS" -gt 0 ] || {
  echo "ERROR: marsautotie produced no tiepoints; see $WORKSPACE/marsautotie.log." >&2
  exit 1
}
echo ""

echo "Step 4: Solving corrected pointing (marsnav)"
run_logged marsnav.log tig marsnav inp=joint.lis out=pointing.nav \
  in_tpt=tiepoints.tpt out_tpt=kept.tpt out_solution_id="$SOLUTION_ID" \
  -remove max_residual="$MAX_RESIDUAL" max_remove=100
RESIDUAL_BEFORE=$(grep -m1 "Commanded mean pixel error" marsnav.log | awk '{print $NF}')
RESIDUAL_AFTER=$(grep "Final solution mean pixel error" marsnav.log | tail -1 | awk '{print $NF}')
KEPT_TIEPOINTS=$(grep -c '<tie ' kept.tpt || true)
[ "$KEPT_TIEPOINTS" -gt 0 ] || {
  echo "ERROR: marsnav kept no tiepoints; see $WORKSPACE/marsnav.log." >&2
  exit 1
}
echo "Commanded mean pixel error: $RESIDUAL_BEFORE"
echo "Final solution mean pixel error: $RESIDUAL_AFTER"
echo "Kept tiepoints: $KEPT_TIEPOINTS / $TOTAL_TIEPOINTS"
echo "Per-image corrected/original az-el deltas:"
sed -n '/Img ----corrected----/,$p' marsnav.log
echo ""

echo "Step 5: Probing common projection geometry"
run_logged probe.log tig marsmap inp=epochs.lis out=probe.img \
  navtable=pointing.nav match_method=TIGHT projection=CYLINDRICAL \
  grid=NOGRID zoom=0.25 ovr_out=joint.ovr
NATURAL_SCALE=$(grep -m1 "Pixel scale: .*pixels/degree" probe.log | \
  sed -E 's/.*or ([0-9.]+) pixels\/degree.*/\1/')
PROBE_TOPEL=$(grep -m1 "Elevation minimum" probe.log | \
  sed -E 's/.*Elevation minimum ([^,]+), Elevation maximum ([^ ]+).*/\2/')
PROBE_BOTTOMEL=$(grep -m1 "Elevation minimum" probe.log | \
  sed -E 's/.*Elevation minimum ([^,]+), Elevation maximum ([^ ]+).*/\1/')
PROBE_LEFTAZ=$(grep -m1 "Azimuth minimum" probe.log | \
  sed -E 's/.*Azimuth minimum ([^,]+), Azimuth maximum ([^ ]+).*/\1/')
PROBE_RIGHTAZ=$(grep -m1 "Azimuth minimum" probe.log | \
  sed -E 's/.*Azimuth minimum ([^,]+), Azimuth maximum ([^ ]+).*/\2/')
if [ -z "$NATURAL_SCALE" ] || [ -z "$PROBE_TOPEL" ] ||
  [ -z "$PROBE_BOTTOMEL" ] || [ -z "$PROBE_LEFTAZ" ] || [ -z "$PROBE_RIGHTAZ" ]; then
  echo "ERROR: Could not parse common geometry from probe.log"
  exit 1
fi
if [ -n "$SCALE" ]; then
  OUTPUT_SCALE="$SCALE"
else
  OUTPUT_SCALE="$NATURAL_SCALE"
fi
if [ -n "$BOUNDS" ]; then
  read -r LEFTAZ RIGHTAZ TOPEL BOTTOMEL <<< "$BOUNDS"
else
  LEFTAZ="$PROBE_LEFTAZ"
  RIGHTAZ="$PROBE_RIGHTAZ"
  TOPEL="$PROBE_TOPEL"
  BOTTOMEL="$PROBE_BOTTOMEL"
fi
echo "Natural scale: $NATURAL_SCALE pixels/degree"
echo "Output scale: $OUTPUT_SCALE pixels/degree"
echo "Output bounds: $LEFTAZ $RIGHTAZ $TOPEL $BOTTOMEL"
echo ""

if $USE_BRTCORR; then
  echo "Step 6: Solving radiometric normalization (marsbrt)"
  run_logged marsbrt.log tig marsbrt inp=joint.lis out=joint.brt \
    in_ovr=joint.ovr navtable=pointing.nav match_method=TIGHT \
    out_solution_id="$SOLUTION_ID"
  sed -n '/^Image   MultCorr/,/^$/p' marsbrt.log
  BRTCORR_BAD_MULTS=$(awk '
    /^Image[[:space:]]+MultCorr/ { table=1; next }
    table && $1 ~ /^[0-9]+$/ && ($2 + 0) < 0.01 {
      printf "image %s MultCorr=%s\n", $1, $2
    }
  ' marsbrt.log)
  echo ""
fi

MAP_COMMON=(navtable=pointing.nav match_method=TIGHT projection=CYLINDRICAL
  leftaz="$LEFTAZ" rightaz="$RIGHTAZ" topel="$TOPEL" bottomel="$BOTTOMEL"
  scale="$OUTPUT_SCALE" band="$BAND")

render_epoch() {
  local epoch="$1"
  local list="$2"
  local raw="$3"
  local log="$4"
  local brt="$5"
  local brt_log="$6"
  local count
  run_logged "$log" tig marsmap inp="$list" out="$raw" "${MAP_COMMON[@]}"
  count=$(grep -c "Pointing Correction has been applied" "$log" || true)
  [ "$count" -ge "$(wc -l < "$list")" ] || {
    echo "ERROR: $log applied pointing correction to $count of $(wc -l < "$list") inputs."
    echo "       match_method=TIGHT is required for corrected nav entries."
    exit 1
  }
  if $USE_BRTCORR; then
    run_logged "$brt_log" tig marsmap inp="$list" out="$brt" \
      "${MAP_COMMON[@]}" brtcorr=joint.brt
    count=$(grep -c "Pointing Correction has been applied" "$brt_log" || true)
    [ "$count" -ge "$(wc -l < "$list")" ] || {
      echo "ERROR: $brt_log applied pointing correction to $count of $(wc -l < "$list") inputs."
      echo "       match_method=TIGHT is required for corrected nav entries."
      exit 1
    }
  fi
  if $USE_BRTCORR; then
    echo "Rendered epoch $epoch: $raw and $brt"
  else
    echo "Rendered epoch $epoch: $raw"
  fi
}

echo "Step 7: Rendering identical epoch geometries"
render_epoch 1 epoch1.lis epoch1_raw.img epoch1_raw.log epoch1_brt.img epoch1_brt.log
render_epoch 2 epoch2.lis epoch2_raw.img epoch2_raw.log epoch2_brt.img epoch2_brt.log
echo ""

run_logged epoch1_label.log tig label -list inp=epoch1_raw.img
NL=$(sed -n 's/.* \([0-9][0-9]*\) lines per band.*/\1/p' epoch1_label.log | head -1)
NS=$(sed -n 's/.* \([0-9][0-9]*\) samples per line.*/\1/p' epoch1_label.log | head -1)
if [ -z "$NL" ] || [ -z "$NS" ]; then
  echo "ERROR: could not determine rendered raster dimensions from epoch1_label.log"
  exit 1
fi
if [ -z "$BOX" ]; then
  BOX_SL=$((NL * 3 / 10))
  BOX_SS=$((NS * 3 / 10))
  BOX_NL=$((NL * 2 / 5))
  BOX_NS=$((NS * 2 / 5))
else
  read -r BOX_SL BOX_SS BOX_NL BOX_NS <<< "$BOX"
fi
if [ -z "$BOX_SL" ] || [ -z "$BOX_SS" ] || [ -z "$BOX_NL" ] || [ -z "$BOX_NS" ]; then
  echo "ERROR: invalid statistics box: $BOX"
  exit 1
fi
echo "Step 8: Validating statistics box ($BOX_SL $BOX_SS $BOX_NL $BOX_NS)"

parse_maxmin_value() {
  local log="$1"
  local label="$2"
  awk -v label="$label" '
    index($0, label) {
      value = substr($0, index($0, label) + length(label))
      sub(/^[[:space:]]*/, "", value)
      split(value, fields, /[[:space:]]+/)
      if (fields[1] ~ /^[+-]?([0-9]+([.][0-9]*)?|[.][0-9]+)([Ee][+-]?[0-9]+)?$/) {
        printf "%.12g\n", (fields[1] + 0)
        exit
      }
    }
  ' "$log"
}

parse_hist_value() {
  local log="$1"
  local label="$2"
  awk -v label="$label" '
    index($0, label) {
      value = substr($0, index($0, label) + length(label))
      sub(/^[[:space:]]*/, "", value)
      split(value, fields, /[[:space:]]+/)
      print fields[1]
      exit
    }
  ' "$log"
}

validate_maxmin() {
  local name="$1"
  local file="$2"
  local log="maxmin_${name}.txt"
  run_logged "$log" tig maxmin "$file" sl="$BOX_SL" ss="$BOX_SS" \
    nl="$BOX_NL" ns="$BOX_NS"
  local minimum
  minimum=$(parse_maxmin_value "$log" "Min. value:")
  [ -n "$minimum" ] || {
    echo "ERROR: could not parse minimum from $log" >&2
    exit 1
  }
  if awk -v min="$minimum" 'BEGIN {exit !(min == 0)}'; then
    echo "ERROR: statistics box contains 0 DN in $file." >&2
    echo "       Choose a smaller interior box with --box SL SS NL NS." >&2
    exit 1
  fi
  echo "$minimum"
}

MIN_EPOCH1=$(validate_maxmin epoch1 epoch1_raw.img)
MIN_EPOCH2=$(validate_maxmin epoch2 epoch2_raw.img)
echo "Epoch 1 box minimum: $MIN_EPOCH1"
echo "Epoch 2 box minimum: $MIN_EPOCH2"
echo ""

declare -A STAT_MEAN STAT_STD STAT_MIN STAT_MAX
measure_stats() {
  local name="$1"
  local file="$2"
  local hist_log="stats_${name}.txt"
  local maxmin_log="maxmin_${name}.txt"
  run_logged "$hist_log" tig hist "$file" -nohist sl="$BOX_SL" ss="$BOX_SS" \
    nl="$BOX_NL" ns="$BOX_NS"
  run_logged "$maxmin_log" tig maxmin "$file" sl="$BOX_SL" ss="$BOX_SS" \
    nl="$BOX_NL" ns="$BOX_NS"
  STAT_MEAN["$name"]=$(parse_hist_value "$hist_log" "AVERAGE GRAY LEVEL=")
  STAT_STD["$name"]=$(parse_hist_value "$hist_log" "STANDARD DEVIATION=")
  STAT_MIN["$name"]=$(parse_maxmin_value "$maxmin_log" "Min. value:")
  STAT_MAX["$name"]=$(parse_maxmin_value "$maxmin_log" "Max. value:")
  echo "$name: mean=${STAT_MEAN[$name]} stdev=${STAT_STD[$name]} min=${STAT_MIN[$name]} max=${STAT_MAX[$name]}"
}

echo "Step 9: Epoch statistics"
measure_stats epoch1 epoch1_raw.img
measure_stats epoch2 epoch2_raw.img
if $USE_BRTCORR; then
  measure_stats epoch1_brt epoch1_brt.img
  measure_stats epoch2_brt epoch2_brt.img
  BRTCORR_LOW_STDS=""
  for epoch in 1 2; do
    if awk -v raw="${STAT_STD[epoch${epoch}]}" \
      -v brt="${STAT_STD[epoch${epoch}_brt]}" \
      'BEGIN { exit !(raw > 0 && brt < raw * 0.10) }'; then
      BRTCORR_LOW_STDS="${BRTCORR_LOW_STDS}epoch${epoch}_brt stdev=${STAT_STD[epoch${epoch}_brt]} "
    fi
  done
  if [ -n "$BRTCORR_BAD_MULTS" ] || [ -n "$BRTCORR_LOW_STDS" ]; then
    echo "WARNING: BRTCORR radiometric solution is ill-conditioned on this input set."
    [ -n "$BRTCORR_BAD_MULTS" ] && echo "         Near-zero or nonpositive multipliers: $BRTCORR_BAD_MULTS"
    [ -n "$BRTCORR_LOW_STDS" ] && echo "         Flattened BRTCORR renders: $BRTCORR_LOW_STDS"
    echo "         Do not read BRTCORR difference statistics as normalized change."
    echo "         Use the linear mean/stdev match as the usable normalization."
  fi
fi
MEAN1="${STAT_MEAN[epoch1]}"
MEAN2="${STAT_MEAN[epoch2]}"
STD1="${STAT_STD[epoch1]}"
STD2="${STAT_STD[epoch2]}"
GAIN=$(awk -v a="$MEAN1" -v b="$MEAN2" 'BEGIN {printf "%.12g", a/b}')
G2=$(awk -v a="$STD1" -v b="$STD2" 'BEGIN {printf "%.12g", a/b}')
O2=$(awk -v a="$MEAN1" -v g="$G2" -v b="$MEAN2" 'BEGIN {printf "%.12g", a-g*b}')
O2_ABS=$(awk -v offset="$O2" 'BEGIN {if (offset < 0) offset=-offset; printf "%.12g", offset}')
if awk -v offset="$O2" 'BEGIN {exit !(offset < 0)}'; then
  LINEAR_FUNC="in1-${G2}*in2+${O2_ABS}"
else
  LINEAR_FUNC="in1-${G2}*in2-${O2_ABS}"
fi
echo "Gain match: G=$GAIN"
echo "Linear match: G2=$G2 O2=$O2"
echo ""

run_f2() {
  local out="$1"
  local first="$2"
  local second="$3"
  local function="$4"
  run_logged "${out%.img}.log" tig f2 inp=\( "$first" "$second" \) \
    out="$out" func="$function" format=REAL
}

echo "Step 10: Difference products"
run_f2 diff_raw.img epoch1_raw.img epoch2_raw.img "in1-in2"
run_f2 diff_gain.img epoch1_raw.img epoch2_raw.img "in1-${GAIN}*in2"
run_f2 diff_lin.img epoch1_raw.img epoch2_raw.img "$LINEAR_FUNC"
measure_stats diff_raw diff_raw.img
measure_stats diff_gain diff_gain.img
measure_stats diff_lin diff_lin.img
if $USE_BRTCORR; then
  run_f2 diff_brt.img epoch1_brt.img epoch2_brt.img "in1-in2"
  measure_stats diff_brt diff_brt.img
fi
echo ""

echo "Step 11: Control experiments"
run_logged epoch1_repeat.log tig marsmap inp=epoch1.lis out=epoch1_repeat_raw.img \
  "${MAP_COMMON[@]}"
run_logged determinism.log tig difpic inp=\( epoch1_raw.img epoch1_repeat_raw.img \)
DETERMINISM_COUNT=$(sed -n \
  's/.*NUMBER OF DIFFERENCES = *\([0-9][0-9]*\).*/\1/p' determinism.log | tail -1)
echo "Determinism: ${DETERMINISM_COUNT:-unknown} differences"

run_logged copy_a0.log tig copy epoch1_raw.img a0.img sl=1 ss=1 \
  nl="$((NL - 1))" ns="$((NS - 4))"
SHIFT_STDS=()
for shift in 1 2 3; do
  sample_start=$((shift + 1))
  run_logged "copy_a${shift}.log" tig copy epoch1_raw.img "a${shift}.img" \
    sl=1 ss="$sample_start" nl="$((NL - 1))" ns="$((NS - 4))"
  run_f2 "diff_shift${shift}.img" a0.img "a${shift}.img" "in1-in2"
  measure_stats "shift${shift}" "diff_shift${shift}.img"
  SHIFT_STDS+=("${STAT_STD[shift${shift}]}")
done
run_logged nonav.log tig marsmap inp=epoch1.lis out=epoch1_nonav.img \
  projection=CYLINDRICAL grid=NOGRID leftaz="$LEFTAZ" rightaz="$RIGHTAZ" \
  topel="$TOPEL" bottomel="$BOTTOMEL" scale="$OUTPUT_SCALE" band="$BAND"
run_f2 diff_nav.img epoch1_raw.img epoch1_nonav.img "in1-in2"
run_logged nav_effect_difpic.log tig difpic inp=\( epoch1_raw.img epoch1_nonav.img \)
NAV_EFFECT_COUNT=$(sed -n \
  's/.*NUMBER OF DIFFERENCES = *\([0-9][0-9]*\).*/\1/p' nav_effect_difpic.log | tail -1)
measure_stats nav_effect diff_nav.img
echo "1-pixel shift stdev: ${SHIFT_STDS[0]}"
echo "2-pixel shift stdev: ${SHIFT_STDS[1]}"
echo "3-pixel shift stdev: ${SHIFT_STDS[2]}"
echo "Nav effect stdev: ${STAT_STD[nav_effect]}"
echo "Nav effect difpic: ${NAV_EFFECT_COUNT:-not parsed} differences"
echo ""

echo "Step 12: Difference display and PNG products"
K=$(awk -v sigma="${STAT_STD[diff_lin]}" 'BEGIN {printf "%.12g", 255/(6*sigma)}')
DISPLAY_FUNC="\"min(255,max(0,in1*${K}+128))\""
run_logged diffview.log tig f2 inp=diff_lin.img out=diffview.img \
  func="$DISPLAY_FUNC" format=BYTE
run_logged diffview_vicario.log tig vicario diffview.img diffview.png
for epoch in 1 2; do
  tig stretch inp="epoch${epoch}_raw.img" out="epoch${epoch}_display.img" \
    -astretch percent=2 dnmin=0 dnmax=255 > "epoch${epoch}_stretch.log" 2>&1
  run_logged "epoch${epoch}_vicario.log" tig vicario \
    "epoch${epoch}_display.img" "epoch${epoch}_display.png"
done
echo "Difference display scale K=$K (0 -> 128, +/-3 sigma -> 0/255)"
echo ""

echo "=== Numbers Summary ==="
echo "Workspace: $WORKSPACE"
echo "Residual before/after: $RESIDUAL_BEFORE -> $RESIDUAL_AFTER px"
echo "Tiepoints kept: $KEPT_TIEPOINTS / $TOTAL_TIEPOINTS"
echo "Epoch 1 mean/stdev: ${STAT_MEAN[epoch1]} +/- ${STAT_STD[epoch1]}"
echo "Epoch 2 mean/stdev: ${STAT_MEAN[epoch2]} +/- ${STAT_STD[epoch2]}"
echo "Raw difference mean/stdev/min/max: ${STAT_MEAN[diff_raw]} / ${STAT_STD[diff_raw]} / ${STAT_MIN[diff_raw]} / ${STAT_MAX[diff_raw]}"
echo "Gain difference mean/stdev/min/max: ${STAT_MEAN[diff_gain]} / ${STAT_STD[diff_gain]} / ${STAT_MIN[diff_gain]} / ${STAT_MAX[diff_gain]}"
echo "Linear difference mean/stdev/min/max: ${STAT_MEAN[diff_lin]} / ${STAT_STD[diff_lin]} / ${STAT_MIN[diff_lin]} / ${STAT_MAX[diff_lin]}"
if $USE_BRTCORR; then
  echo "BRTCORR difference mean/stdev/min/max: ${STAT_MEAN[diff_brt]} / ${STAT_STD[diff_brt]} / ${STAT_MIN[diff_brt]} / ${STAT_MAX[diff_brt]}"
fi
echo "Shift-control stdevs (1/2/3 px): ${SHIFT_STDS[0]} / ${SHIFT_STDS[1]} / ${SHIFT_STDS[2]}"
echo "Determinism: ${DETERMINISM_COUNT:-unknown} differences"
echo "Nav-effect stdev: ${STAT_STD[nav_effect]}"
echo "Nav-effect difpic: ${NAV_EFFECT_COUNT:-not parsed} differences"
echo ""

artifact_description() {
  case "$1" in
    joint.lis) echo "All epoch and tie-extra inputs" ;;
    epoch1.lis) echo "Epoch 1 projection input list" ;;
    epoch2.lis) echo "Epoch 2 projection input list" ;;
    epochs.lis) echo "Probe input list containing both epochs" ;;
    ovl.lis|ovr.lis) echo "Accepted overlap pair list" ;;
    tiepoints.tpt) echo "Joint autotie tiepoints" ;;
    kept.tpt) echo "Tiepoints retained by marsnav" ;;
    pointing.nav) echo "Corrected pointing navigation table" ;;
    probe.img) echo "Common-frame probe raster" ;;
    joint.ovr) echo "Overlap statistics for marsbrt" ;;
    joint.brt) echo "Per-frame radiometric correction table" ;;
    epoch1_raw.img|epoch2_raw.img) echo "Projected unnormalised epoch raster" ;;
    epoch1_brt.img|epoch2_brt.img) echo "Projected BRTCORR epoch raster" ;;
    diff_raw.img) echo "Raw epoch difference raster" ;;
    diff_gain.img) echo "Gain-matched difference raster" ;;
    diff_lin.img) echo "Linear mean/stdev-matched difference raster" ;;
    diff_brt.img) echo "BRTCORR epoch difference raster" ;;
    a0.img|a1.img|a2.img|a3.img) echo "Registration-shift control raster" ;;
    diff_shift*.img) echo "Pixel-shift control difference raster" ;;
    epoch1_nonav.img) echo "Epoch 1 projection without nav table" ;;
    diff_nav.img) echo "Navigation-effect difference raster" ;;
    diffview.img) echo "BYTE display-scaled linear difference" ;;
    epoch1_display.img|epoch2_display.img) echo "2-percent-stretched epoch raster" ;;
    diffview.png) echo "PNG linear difference display" ;;
    epoch1_display.png|epoch2_display.png) echo "PNG epoch display" ;;
    *.log) echo "Full program or control log" ;;
    *.txt) echo "Parsed statistics output" ;;
    *.png) echo "PNG display product" ;;
    *.img) echo "VICAR raster product" ;;
    *) echo "Pipeline artifact" ;;
  esac
}

echo "Artifacts:"
ARTIFACTS=(
  joint.lis epoch1.lis epoch2.lis epochs.lis ovl.lis ovr.lis
  tiepoints.tpt kept.tpt pointing.nav probe.img joint.ovr joint.brt
  epoch1_raw.img epoch2_raw.img epoch1_brt.img epoch2_brt.img
  diff_raw.img diff_gain.img diff_lin.img diff_brt.img diffview.img
  diffview.png epoch1_display.img epoch2_display.img epoch1_display.png
  epoch2_display.png a0.img a1.img a2.img a3.img diff_shift1.img
  diff_shift2.img diff_shift3.img epoch1_nonav.img diff_nav.img
  marschkovl.log marsautotie.log marsnav.log probe.log marsbrt.log
  epoch1_label.log epoch1_raw.log epoch2_raw.log epoch1_brt.log
  epoch2_brt.log epoch1_repeat.log determinism.log nav_effect_difpic.log
  nonav.log diffview.log diffview_vicario.log
)
for artifact in "${ARTIFACTS[@]}"; do
  [ -e "$artifact" ] || continue
  printf '  %-26s %-7s %s\n' "$artifact" "$(du -h "$artifact" | cut -f1)" \
    "$(artifact_description "$artifact")"
done
echo ""
echo "A nonzero difference is not, by itself, detected surface change: illumination,"
echo "radiometry, pointing residual, and resampling all produce signal. Compare it"
echo "with the shift-control numbers above as the registration floor."
echo ""
echo "=== Demo Complete ==="
