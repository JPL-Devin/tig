#!/bin/bash
# Build the TIG VICAR builder image: the same VICAR release as the runtime
# image, unpruned and with a toolchain, which is what 'tig --build' compiles in.
#
# Not published as an image: run this once per machine, and per VICAR release.

set -e

IMAGE_NAME="${IMAGE_NAME:-terrain-intelligence-generator}"
IMAGE_TAG="${IMAGE_TAG:-opensource-builder}"
VICAR_VERSION="${VICAR_VERSION:-5.0}"
BINARIES_FILE="${BINARIES_FILE:-vicar_open_bin_x86-64-linx_${VICAR_VERSION}.tar.gz}"
EXTERNAL_FILE="${EXTERNAL_FILE:-vicar_open_ext_x86-64-linx_${VICAR_VERSION}.tar.gz}"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${GREEN}===== Building TIG VICAR builder image =====${NC}"
echo ""
echo "Configuration:"
echo "  Image name: ${IMAGE_NAME}:${IMAGE_TAG}"
echo "  VICAR version: ${VICAR_VERSION}"
echo "  Binaries tarball: ${BINARIES_FILE}"
echo "  Externals tarball: ${EXTERNAL_FILE}"
echo ""

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Outside docker/, so the runtime image's workflow does not rebuild on a
# change to the builder.
BUILDER_DIR="${SCRIPT_DIR}/builder"

if [ ! -f "${BUILDER_DIR}/Dockerfile" ]; then
    echo -e "${RED}ERROR: Dockerfile not found in ${BUILDER_DIR}${NC}"
    exit 1
fi

echo -e "${YELLOW}Building...${NC}"
echo "This downloads the VICAR release tarballs (~2GB) and the compilers."
echo "Estimated build time: 10-20 minutes; the image is around 7GB."
echo ""

docker build \
    --platform linux/amd64 \
    -f "${BUILDER_DIR}/Dockerfile" \
    -t "${IMAGE_NAME}:${IMAGE_TAG}" \
    --build-arg VICAR_VERSION="${VICAR_VERSION}" \
    --build-arg BINARIES_FILE="${BINARIES_FILE}" \
    --build-arg EXTERNAL_FILE="${EXTERNAL_FILE}" \
    "${BUILDER_DIR}"

echo ""
echo -e "${GREEN}✓ Build completed successfully!${NC}"
echo ""
echo "Image: ${IMAGE_NAME}:${IMAGE_TAG}"
echo ""
echo "Compile a VICAR program from source and run it:"
echo "  cd \$(mktemp -d) && docker run --rm -v \$PWD:/build ${IMAGE_NAME}:${IMAGE_TAG} \\"
echo "      bash -c 'cp \$V2TOP/p2/prog/gen/gen.* /build/ && vicar-build gen'"
echo "  tig --build gen        # from a directory holding gen.imake"
echo ""
