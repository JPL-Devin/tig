#!/bin/bash
#
# build-visor-image.sh - Build a per-mission VISOR calibration image variant
#
# The variant is built on top of the published base image, so this takes
# minutes (a download and an extraction) rather than the hour a VICAR build
# costs.
#
# Usage:
#   ./build-visor-image.sh <mission> [image-tag]
#   ./build-visor-image.sh "m20 mer msl msam phx nsyt" tig:visor-all
#
# Example:
#   ./build-visor-image.sh nsyt
#   BASE_TAG=develop ./build-visor-image.sh msl

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

: "${BASE_IMAGE:=ghcr.io/nasa-ammos/tig/terrain-intelligence-generator}"
: "${BASE_TAG:=opensource}"
: "${VICAR_VERSION:=5.0}"

if [ $# -lt 1 ]; then
    echo "Usage: $0 <mission...> [image-tag]"
    echo "Missions: m20 mer msl msam phx nsyt"
    exit 1
fi

MISSIONS="$1"
IMAGE_TAG="${2:-terrain-intelligence-generator:visor-$(echo "${MISSIONS}" | tr ' ' '-')}"

echo "Building ${IMAGE_TAG}"
echo "  base:     ${BASE_IMAGE}:${BASE_TAG}"
echo "  missions: ${MISSIONS}"
echo ""

docker pull --platform linux/amd64 "${BASE_IMAGE}:${BASE_TAG}"

docker build \
    --platform linux/amd64 \
    --file "${SCRIPT_DIR}/docker/Dockerfile.visor" \
    --build-arg "BASE_IMAGE=${BASE_IMAGE}" \
    --build-arg "BASE_TAG=${BASE_TAG}" \
    --build-arg "VICAR_VERSION=${VICAR_VERSION}" \
    --build-arg "VISOR_MISSIONS=${MISSIONS}" \
    --tag "${IMAGE_TAG}" \
    "${SCRIPT_DIR}/docker"

echo ""
docker images "${IMAGE_TAG%%:*}"
echo ""
echo "Test it with:"
echo "  ${SCRIPT_DIR}/test-visor-image.sh ${IMAGE_TAG}"
