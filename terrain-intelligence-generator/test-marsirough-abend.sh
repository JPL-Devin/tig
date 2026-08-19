#!/bin/bash
#
# test-marsirough-abend.sh - Reproduction for the marsirough ZIX read abend
#
# marsirough abends reading the single-band ZIX file that marsitilt itself
# wrote, which is why demo-surface-characteristics.sh has to continue without
# its roughness product.  This script reproduces that in isolation, so the
# report can be filed against NASA-AMMOS/VICAR rather than described.
#
# What it shows:
#   1. marsitilt writes a 3-band UIX and a 1-band ZIX, as marsitilt.pdf says.
#   2. marsirough, invoked exactly as marsirough.pdf documents, reads BAND 2
#      of that 1-band ZIX and dies: [VIC2-EOF] End of file, ** ABEND called **.
#
# Cause, from vos/mars/src/prog/marsirough/marsirough.cc: the ZIX arrays are
# declared with one element (`int zix_unit[1]; int zix_band[1];`) but
# open_inputs() unconditionally fills three, so `band[1] = 2; band[2] = 3;`
# write past the end of zix_band and the band the read loop then uses is 2.
#
# Usage:
#   ./test-marsirough-abend.sh <image-tag> [--xyz FILE]
#
# --xyz runs the reproduction against a real XYZ product instead of the
# synthetic fixture; without it, no calibration or downloaded data is needed.
#
# Exit status:
#   0 - the abend reproduced (the defect is present in this image)
#   1 - marsirough completed: the defect is fixed, and the demo script and
#       docs/demos/surface-characteristics.md should stop saying otherwise
#   2 - the reproduction itself could not be set up

set -u

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

if [ $# -eq 0 ]; then
    echo "Usage: $0 <image-tag> [--xyz FILE]"
    exit 2
fi

IMAGE_TAG="$1"
shift
XYZ_INPUT=""
while [ $# -gt 0 ]; do
    case "$1" in
        --xyz)
            [ $# -ge 2 ] || { echo "ERROR: --xyz requires a FILE"; exit 2; }
            XYZ_INPUT="$2"
            shift 2
            ;;
        *)
            echo "ERROR: unknown option: $1"
            exit 2
            ;;
    esac
done

# Geometry of the synthetic fixture, as in test-product-pipeline.sh
IMG_SIZE=256
DISPARITY_PX=8
FOCAL_PX=800
BASELINE_M=0.2

CONTAINER_NAME="tig-marsirough-repro-$$"
PLATFORM_FLAG=""
if [[ "$(uname -m)" == "arm64" ]] || [[ "$(uname -m)" == "aarch64" ]]; then
    PLATFORM_FLAG="--platform linux/amd64"
fi

WORKSPACE=$(mktemp -d)
cleanup() {
    docker rm -f ${CONTAINER_NAME} > /dev/null 2>&1
    rm -rf "${WORKSPACE}"
}
trap cleanup EXIT

print_header() {
    echo ""
    echo "============================================"
    echo "$1"
    echo "============================================"
}

echo -e "${BLUE}Reproducing the marsirough ZIX abend in: ${IMAGE_TAG}${NC}"

if ! docker run -d --name ${CONTAINER_NAME} ${PLATFORM_FLAG} \
        -v "${WORKSPACE}:/workspace" "${IMAGE_TAG}" tail -f /dev/null > /dev/null 2>&1; then
    echo -e "${RED}✗ Could not start a container from ${IMAGE_TAG}${NC}"
    exit 2
fi

print_header "Step 1: XYZ input"
if [ -n "${XYZ_INPUT}" ]; then
    [ -f "${XYZ_INPUT}" ] || { echo -e "${RED}✗ No such file: ${XYZ_INPUT}${NC}"; exit 2; }
    docker cp "${XYZ_INPUT}" "${CONTAINER_NAME}:/workspace/pointcloud.xyz" > /dev/null || exit 2
    echo "using ${XYZ_INPUT}"
else
    # Synthetic fronto-parallel noise wall at a known range, meshed the same way
    # test-product-pipeline.sh builds its fixture: no calibration needed.
    docker exec ${CONTAINER_NAME} bash -c "
        set -u
        cd /workspace || exit 1
        gausnois n.vic nl=${IMG_SIZE} ns=$((IMG_SIZE + 24)) mean=128 sigma=40 seed=12345 > gausnois.log 2>&1
        boxflt2 n.vic nsmooth.vic nsw=3 nlw=3 > boxflt2.log 2>&1
        copy nsmooth.vic left.vic  sl=1 ss=9 nl=${IMG_SIZE} ns=${IMG_SIZE} > copy_left.log 2>&1
        copy nsmooth.vic right.vic sl=1 ss=$((9 + DISPARITY_PX)) nl=${IMG_SIZE} ns=${IMG_SIZE} > copy_right.log 2>&1

        write_cahv() {
            cat > \"\$1\" <<EOF
# synthetic pinhole camera, 1 m above ground, looking north
Model = CAHV = perspective, linear

Dimensions = ${IMG_SIZE} ${IMG_SIZE}

C = 0.0 \$2 -1.0
A = 1.0 0.0 0.0
H = $((IMG_SIZE / 2)).0 ${FOCAL_PX}.0 0.0
V = $((IMG_SIZE / 2)).0 0.0 ${FOCAL_PX}.0
EOF
        }
        write_cahv left.cahv 0.0
        write_cahv right.cahv ${BASELINE_M}

        for f in left right; do
            label -add inp=\$f.vic property=IDENTIFICATION \
                items=\"\\\"PRODUCT_ID='SYNTH_\${f}' INSTRUMENT_NAME='SYNTHETIC' TARGET_NAME='SYNTHETIC'\\\"\" \
                > label_\$f.log 2>&1
        done

        marscorr \( left.vic right.vic \) disparity.img template=15 search=51 quality=0.2 > marscorr.log 2>&1
        marsxyz \( left.vic right.vic \) pointcloud.xyz disp=disparity.img \
            error=10.0 abserr=0.15 -write_cm > marsxyz.log 2>&1
        grep -E 'Computed [0-9]+ valid XYZ points' marsxyz.log
        # The SITE group marsxyz writes has one index; the generic (no-mission)
        # camera frame wants two, which marsitilt cannot resolve.
        label -delete inp=pointcloud.xyz property=SITE_COORDINATE_SYSTEM > label_del.log 2>&1
        [ -s pointcloud.xyz ]
    " || { echo -e "${RED}✗ Could not build the synthetic XYZ fixture${NC}"; exit 2; }
fi

print_header "Step 2: marsitilt writes the UIX and ZIX (marsitilt.pdf, -heli)"
docker exec ${CONTAINER_NAME} bash -c '
    cd /workspace || exit 1
    marsitilt inp=pointcloud.xyz out=tilt.img \
        uix_out=uix.img zix_out=zix.img -heli > marsitilt.log 2>&1
    [ -s uix.img ] && [ -s zix.img ]
' || { echo -e "${RED}✗ marsitilt did not produce uix.img and zix.img${NC}"
       docker exec ${CONTAINER_NAME} tail -20 /workspace/marsitilt.log
       exit 2; }

# The band counts are the whole point: marsirough requires a 1-band ZIX
# ("A single ZIX file must have 1 bands") and then reads band 2 of it.
BANDS=$(docker exec ${CONTAINER_NAME} bash -c '
    cd /workspace || exit 1
    for f in uix.img zix.img; do
        label -list $f > $f.label.txt 2>&1
        echo "$f: $(grep -oE "^[[:space:]]*[0-9]+ bands" $f.label.txt | head -1 | awk "{print \$1}") bands"
    done
')
echo "${BANDS}"
echo "${BANDS}" | grep -q "^zix.img: 1 bands$" || {
    echo -e "${RED}✗ zix.img is not the 1-band file marsitilt.pdf documents${NC}"; exit 2; }

print_header "Step 3: marsirough reads that ZIX (marsirough.pdf invocation)"
docker exec ${CONTAINER_NAME} bash -c '
    cd /workspace || exit 1
    marsirough inp=pointcloud.xyz out=iroughness.img \
        uix=uix.img zix=zix.img -heli > marsirough.log 2>&1
'
IROUGH_RC=$?
docker exec ${CONTAINER_NAME} tail -6 /workspace/marsirough.log
LOG=$(docker exec ${CONTAINER_NAME} cat /workspace/marsirough.log)

echo ""
if echo "${LOG}" | grep -q "ABEND called" &&
   echo "${LOG}" | grep -q "Exception in XVREAD, processing file: zix.img"; then
    echo -e "${GREEN}✓ Reproduced: marsirough abends on the ZIX marsitilt wrote (exit ${IROUGH_RC})${NC}"
    echo ""
    echo "Filable summary:"
    echo "  marsirough reads BAND 2 of a ZIX file it has just required to have"
    echo "  1 band.  In marsirough.cc, open_inputs() fills unit[0..2]/band[0..2]"
    echo "  for every input, but the ZIX arrays are declared as zix_unit[1] and"
    echo "  zix_band[1], so 'band[1] = 2; band[2] = 3;' run off the end and the"
    echo "  read loop's zix_band[0] is no longer 1: under gdb the ZIX zvread is"
    echo "  issued with BAND 2.  Sizing the ZIX arrays like the others, or"
    echo "  bounding open_inputs() by its nbands argument, fixes it.  Reproduced"
    echo "  with this script's synthetic fixture and with the VISOR sample MSL"
    echo "  Navcam XYZ (Roughness/NLB_712299404XYZL*.IMG)."
    exit 0
fi

if [ ${IROUGH_RC} -eq 0 ] &&
   docker exec ${CONTAINER_NAME} test -s /workspace/iroughness.img; then
    echo -e "${YELLOW}marsirough completed and produced iroughness.img.${NC}"
    echo -e "${YELLOW}The defect is fixed in this image: update demo-surface-characteristics.sh${NC}"
    echo -e "${YELLOW}and docs/demos/surface-characteristics.md, which still document it.${NC}"
    exit 1
fi

echo -e "${RED}✗ marsirough failed, but not with the ZIX read abend (exit ${IROUGH_RC})${NC}"
exit 2
