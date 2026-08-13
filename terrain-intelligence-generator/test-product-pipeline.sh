#!/bin/bash
#
# test-product-pipeline.sh - Terrain product tests for the TIG Docker image
#
# test-docker-image.sh proves the image is wired up (wrappers exist, gen/copy/
# stretch run, files persist).  This script proves the image actually produces
# terrain products: it runs a stereo -> disparity -> XYZ -> mesh chain and two
# mosaic programs, then asserts on the CONTENT of the outputs (vertex/face
# counts, finite and physically plausible coordinates, mosaic dimensions and
# pixel statistics), not on exit status.
#
# Fixture: a synthetic stereo pair plus synthetic CAHV camera models, both
# built inside the image with gausnois/boxflt2/copy/label.  No mission
# calibration (VISOR) and no downloaded data are required.  The pair is a
# fronto-parallel noise "wall" viewed by two 256x256 pinhole cameras 0.2 m
# apart, so the true scene range is known exactly (800 * 0.2 / 8 px = 20 m)
# and the tests can assert reconstructed geometry against it.
#
# Usage:
#   ./test-product-pipeline.sh <image-tag>
#
# Example:
#   ./test-product-pipeline.sh tig-vicar-test:latest
#   ./test-product-pipeline.sh ghcr.io/nasa-ammos/tig/terrain-intelligence-generator:opensource
#

# No 'set -e': each test records its own result and the summary below exits 1.

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Check arguments
if [ $# -eq 0 ]; then
    echo "Usage: $0 <image-tag>"
    echo "Example: $0 tig-vicar-test:latest"
    exit 1
fi

IMAGE_TAG="$1"
TESTS_PASSED=0
TESTS_FAILED=0
CONTAINER_NAME="tig-product-test-$$"

# Geometry of the synthetic fixture (see header)
IMG_SIZE=256          # stereo pair is IMG_SIZE x IMG_SIZE
DISPARITY_PX=8        # sample offset between left and right crops
FOCAL_PX=800          # H/V scale of the synthetic CAHV models
BASELINE_M=0.2        # camera separation
TRUE_RANGE_M=20       # FOCAL_PX * BASELINE_M / DISPARITY_PX

# Floors for the product assertions.  The pair correlates over most of the
# frame, so these are deliberately far below what a working chain produces
# and far above what a degenerate/empty product would.
MIN_XYZ_POINTS=5000
MIN_VERTICES=5000
MIN_FACES=5000
MIN_MOSAIC_DIM=200

# Detect platform and set Docker platform flag if needed
PLATFORM_FLAG=""
if [[ "$(uname -m)" == "arm64" ]] || [[ "$(uname -m)" == "aarch64" ]]; then
    PLATFORM_FLAG="--platform linux/amd64"
    echo -e "${YELLOW}Detected ARM architecture, using platform flag: linux/amd64${NC}"
fi

# Create temporary workspace for testing
TEST_WORKSPACE=$(mktemp -d)
echo -e "${BLUE}Created test workspace: ${TEST_WORKSPACE}${NC}"
echo ""

cleanup() {
    docker rm -f ${CONTAINER_NAME} > /dev/null 2>&1
    rm -rf ${TEST_WORKSPACE}
}
trap cleanup EXIT

# Function to print test header
print_test_header() {
    echo ""
    echo "============================================"
    echo "$1"
    echo "============================================"
}

# Function to handle test result
test_result() {
    if [ $1 -eq 0 ]; then
        echo -e "${GREEN}✓ $2${NC}"
        TESTS_PASSED=$((TESTS_PASSED + 1))
        return 0
    else
        echo -e "${RED}✗ ERROR: $2${NC}"
        TESTS_FAILED=$((TESTS_FAILED + 1))
        return 1
    fi
}

echo -e "${BLUE}Testing image: ${IMAGE_TAG}${NC}"

if ! docker run -d --name ${CONTAINER_NAME} ${PLATFORM_FLAG} \
        -v ${TEST_WORKSPACE}:/workspace ${IMAGE_TAG} tail -f /dev/null > /dev/null 2>&1; then
    echo -e "${RED}✗ ERROR: Could not start container from ${IMAGE_TAG}${NC}"
    exit 1
fi

# ---------------------------------------------------------------------------
# Test 1: Build the synthetic stereo fixture inside the image
# ---------------------------------------------------------------------------
print_test_header "Test 1: Synthetic stereo pair and camera models (no calibration)"
docker exec ${CONTAINER_NAME} bash -c "
    set -u
    cd /workspace || exit 1

    # Textured noise, then two crops offset by DISPARITY_PX samples.  The
    # offset crop is a fronto-parallel scene at a known constant range.
    gausnois n.vic nl=${IMG_SIZE} ns=$((IMG_SIZE + 24)) mean=128 sigma=40 seed=12345 > gausnois.log 2>&1
    boxflt2 n.vic nsmooth.vic nsw=3 nlw=3 > boxflt2.log 2>&1
    copy nsmooth.vic left.vic  sl=1 ss=9 nl=${IMG_SIZE} ns=${IMG_SIZE} > copy_left.log 2>&1
    copy nsmooth.vic right.vic sl=1 ss=$((9 + DISPARITY_PX)) nl=${IMG_SIZE} ns=${IMG_SIZE} > copy_right.log 2>&1

    # Synthetic CAHV camera models as sidecar files.  MARS' generic mission
    # picks these up by stripping the image extension (PigGenericImage), so no
    # mission calibration is needed.  Frame is X=north, Y=east, Z=down; the
    # cameras sit 1 m above the ground looking north, BASELINE_M apart in Y.
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

    # MARS label writers copy PRODUCT_ID from the inputs into derived products;
    # without it marscorr dereferences a null product id and dies.
    for f in left right; do
        label -add inp=\$f.vic property=IDENTIFICATION \
            items=\"\\\"PRODUCT_ID='SYNTH_\${f}' INSTRUMENT_NAME='SYNTHETIC' TARGET_NAME='SYNTHETIC'\\\"\" \
            > label_\$f.log 2>&1
    done

    for f in left.vic right.vic left.cahv right.cahv; do
        [ -s \$f ] || { echo \"missing fixture file: \$f\"; exit 1; }
    done
    # Redirect, never pipe: closing a wrapper's output early blocks it on write.
    label -list left.vic > left.label.txt 2>/dev/null
    grep -q \"PRODUCT_ID='SYNTH_left'\" left.label.txt || {
        echo 'PRODUCT_ID not written to left.vic label'; exit 1; }
    echo \"fixture: two ${IMG_SIZE}x${IMG_SIZE} images, baseline ${BASELINE_M} m, true range ${TRUE_RANGE_M} m\"
"
test_result $? "Synthetic stereo pair + CAHV camera models built in-image"

# ---------------------------------------------------------------------------
# Test 2: Stereo correlation product (marscorr)
# ---------------------------------------------------------------------------
print_test_header "Test 2: Stereo correlation (marscorr) produces a disparity map"
docker exec ${CONTAINER_NAME} bash -c '
    cd /workspace || exit 1
    marscorr \( left.vic right.vic \) disparity.img template=15 search=51 quality=0.2 > marscorr.log 2>&1
    grep -E "[0-9]+ tiepoints acquired" marscorr.log | tail -1
    label -list disparity.img > disparity.label.txt 2>&1
    [ -s disparity.img ]
'
CORR_RC=$?
TIEPOINTS=$(grep -oE '[0-9]+ tiepoints acquired' ${TEST_WORKSPACE}/marscorr.log 2>/dev/null | tail -1 | awk '{print $1}')
TIEPOINTS=${TIEPOINTS:-0}
DISP_BANDS=$(grep -oE '^[[:space:]]*[0-9]+ bands' ${TEST_WORKSPACE}/disparity.label.txt 2>/dev/null | head -1 | awk '{print $1}')
DISP_BANDS=${DISP_BANDS:-0}
echo "tiepoints acquired: ${TIEPOINTS}, disparity bands: ${DISP_BANDS}"
if [ ${CORR_RC} -eq 0 ] && [ "${TIEPOINTS}" -ge ${MIN_XYZ_POINTS} ] && [ "${DISP_BANDS}" -eq 2 ]; then
    test_result 0 "Disparity map has ${TIEPOINTS} tiepoints in a 2-band image"
else
    test_result 1 "marscorr did not produce a populated 2-band disparity map"
fi

# ---------------------------------------------------------------------------
# Test 3: XYZ point cloud (marsxyz), checked against the known scene range
# ---------------------------------------------------------------------------
print_test_header "Test 3: Triangulation (marsxyz) produces an XYZ product"
docker exec ${CONTAINER_NAME} bash -c '
    cd /workspace || exit 1
    marsxyz \( left.vic right.vic \) pointcloud.xyz disp=disparity.img \
        error=10.0 abserr=0.15 -write_cm > marsxyz.log 2>&1
    grep -E "Computed [0-9]+ valid XYZ points" marsxyz.log
    # marsxyz writes a SITE coordinate-system group with one index; the generic
    # (no-mission) camera frame wants two, and marsmesh cannot resolve the
    # mismatch.  Drop the group so the generic frame is used.
    label -delete inp=pointcloud.xyz property=SITE_COORDINATE_SYSTEM > label_del.log 2>&1
    [ -s pointcloud.xyz ]
'
XYZ_RC=$?
XYZ_POINTS=$(grep -oE 'Computed [0-9]+ valid XYZ points' ${TEST_WORKSPACE}/marsxyz.log 2>/dev/null | tail -1 | awk '{print $2}')
XYZ_POINTS=${XYZ_POINTS:-0}
echo "valid XYZ points: ${XYZ_POINTS}"
if [ ${XYZ_RC} -eq 0 ] && [ "${XYZ_POINTS}" -ge ${MIN_XYZ_POINTS} ]; then
    test_result 0 "XYZ product has ${XYZ_POINTS} valid points"
else
    test_result 1 "marsxyz did not produce at least ${MIN_XYZ_POINTS} valid XYZ points"
fi

# ---------------------------------------------------------------------------
# Test 4: Mesh product (marsmesh) - non-degenerate OBJ
# ---------------------------------------------------------------------------
print_test_header "Test 4: Mesh generation (marsmesh) produces a non-degenerate OBJ"
docker exec ${CONTAINER_NAME} bash -c "
    cd /workspace || exit 1
    marsmesh inp=pointcloud.xyz out=terrain.obj in_skin=left.vic \
        x_subsample=1 y_subsample=1 \
        range_min=0.2 range_mid=100 range_max=100 \
        lod_levels=1 max_angle=87.5 \
        res_min=3000 res_max=500000 density=1 -adaptive \
        baseline=${BASELINE_M} maxgap=5 > marsmesh.log 2>&1
    grep -iE 'mesh is [0-9]+ triangles' marsmesh.log | tail -1
    [ -s terrain.obj ]
"
MESH_RC=$?
OBJ="${TEST_WORKSPACE}/terrain.obj"
if [ ${MESH_RC} -ne 0 ] || [ ! -s "${OBJ}" ]; then
    test_result 1 "marsmesh did not produce terrain.obj"
else
    VERTICES=$(grep -c '^v ' "${OBJ}")
    FACES=$(grep -c '^f ' "${OBJ}")
    echo "OBJ vertices: ${VERTICES}, faces: ${FACES}"
    if [ "${VERTICES}" -ge ${MIN_VERTICES} ] && [ "${FACES}" -ge ${MIN_FACES} ]; then
        test_result 0 "OBJ is non-degenerate (${VERTICES} vertices, ${FACES} faces)"
    else
        test_result 1 "OBJ is degenerate (${VERTICES} vertices, ${FACES} faces)"
    fi

    # Content check: every vertex coordinate must be a finite number, and the
    # reconstructed wall must sit at the range the fixture geometry implies.
    print_test_header "Test 5: Mesh coordinates are finite and physically plausible"
    awk -v truth=${TRUE_RANGE_M} -v minv=${MIN_VERTICES} '
        { gsub(/\r/, "") }   # marsmesh writes CRLF line endings
        $1 == "v" {
            n++
            for (i = 2; i <= 4; i++) {
                if ($i ~ /[Nn][Aa][Nn]|[Ii][Nn][Ff]/ || $i + 0 != $i || ($i < -1e6 || $i > 1e6)) {
                    bad++
                }
            }
            xsum += $2 + 0
            if (n == 1 || $2 + 0 < xmin) xmin = $2 + 0
            if (n == 1 || $2 + 0 > xmax) xmax = $2 + 0
        }
        END {
            if (n < minv) { printf "only %d vertices\n", n + 0; exit 1 }
            xmean = xsum / n
            printf "vertices=%d non-finite/out-of-range coords=%d range(X) mean=%.3f min=%.3f max=%.3f (truth %.1f m)\n",
                   n, bad + 0, xmean, xmin, xmax, truth
            if (bad > 0) exit 1
            if (xmean < truth * 0.9 || xmean > truth * 1.1) exit 1
            exit 0
        }
    ' "${OBJ}"
    test_result $? "All vertex coordinates finite; mean range within 10% of the ${TRUE_RANGE_M} m truth"
fi

# ---------------------------------------------------------------------------
# Test 6: Camera-space mosaic (marsmos)
# ---------------------------------------------------------------------------
print_test_header "Test 6: Mosaic (marsmos) produces an image with plausible dimensions"
docker exec ${CONTAINER_NAME} bash -c '
    cd /workspace || exit 1
    marsmos \( left.vic right.vic \) mosaic.vic surface=infinity > marsmos.log 2>&1
    label -list mosaic.vic > mosaic.label.txt 2>&1
    hist mosaic.vic > mosaic.hist.txt 2>&1
    [ -s mosaic.vic ]
'
MOS_RC=$?
check_mosaic() {
    # $1 = label file, $2 = hist file
    local nl ns sd
    nl=$(grep -oE '[0-9]+ lines per band' "$1" 2>/dev/null | head -1 | awk '{print $1}')
    ns=$(grep -oE '[0-9]+ samples per line' "$1" 2>/dev/null | head -1 | awk '{print $1}')
    sd=$(grep -oE 'STANDARD DEVIATION=[0-9.]+' "$2" 2>/dev/null | head -1 | cut -d= -f2)
    nl=${nl:-0}; ns=${ns:-0}; sd=${sd:-0}
    echo "dimensions: ${nl} lines x ${ns} samples, pixel standard deviation: ${sd}"
    [ "${nl}" -ge ${MIN_MOSAIC_DIM} ] && [ "${ns}" -ge ${MIN_MOSAIC_DIM} ] &&
        awk -v s="${sd}" 'BEGIN { exit !(s + 0 > 1.0) }'
}
if [ ${MOS_RC} -eq 0 ] && check_mosaic "${TEST_WORKSPACE}/mosaic.label.txt" "${TEST_WORKSPACE}/mosaic.hist.txt"; then
    test_result 0 "marsmos mosaic has plausible dimensions and non-uniform image content"
else
    test_result 1 "marsmos did not produce a plausible mosaic"
fi

# ---------------------------------------------------------------------------
# Test 7: Map-projected mosaic (marsmap)
#
# marsmap is built against MPICH, whose bundled hwloc used to raise SIGFPE in
# MPI_Init on some host CPUs; the image sets HWLOC_COMPONENTS=-x86 so it starts
# everywhere, so nothing is needed here.
# ---------------------------------------------------------------------------
print_test_header "Test 7: Map projection (marsmap) produces a projected mosaic"
docker exec ${CONTAINER_NAME} bash -c '
    cd /workspace || exit 1
    marsmap \( left.vic right.vic \) map.vic surface=infinity > marsmap.log 2>&1
    label -list map.vic > map.label.txt 2>&1
    hist map.vic > map.hist.txt 2>&1
    [ -s map.vic ]
'
MAP_RC=$?
if [ ${MAP_RC} -eq 0 ] && check_mosaic "${TEST_WORKSPACE}/map.label.txt" "${TEST_WORKSPACE}/map.hist.txt"; then
    test_result 0 "marsmap projected mosaic has plausible dimensions and non-uniform image content"
else
    test_result 1 "marsmap did not produce a plausible projected mosaic"
fi

echo ""

# Final summary
echo "============================================"
echo "PRODUCT TEST SUMMARY"
echo "============================================"
echo -e "${GREEN}Passed: $TESTS_PASSED${NC}"
if [ $TESTS_FAILED -gt 0 ]; then
    echo -e "${RED}Failed: $TESTS_FAILED${NC}"
    echo ""
    echo -e "${RED}✗ PRODUCT TESTS FAILED${NC}"
    exit 1
else
    echo -e "${GREEN}Failed: 0${NC}"
    echo ""
    echo -e "${GREEN}✅ ALL PRODUCT TESTS PASSED${NC}"
    exit 0
fi
