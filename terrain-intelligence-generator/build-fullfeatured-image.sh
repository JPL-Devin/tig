#!/bin/bash
#
# build-fullfeatured-image.sh - Build the fullfeatured image variant
#
# Base image plus one mission's VISOR calibration, so the shipped demos run
# with nothing downloaded and nothing mounted. Built on top of the published
# base image, so this takes minutes (a download and an extraction) rather than
# the hour a VICAR build costs.
#
# Usage:
#   ./build-fullfeatured-image.sh [mission...] [image-tag]
#
# Example:
#   ./build-fullfeatured-image.sh                     # m20, tag :fullfeatured
#   ./build-fullfeatured-image.sh msl
#   ./build-fullfeatured-image.sh "m20 msl" tig:fullfeatured-m20-msl
#   BASE_TAG=develop ./build-fullfeatured-image.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

: "${BASE_IMAGE:=ghcr.io/nasa-ammos/tig/terrain-intelligence-generator}"
: "${BASE_TAG:=opensource}"
: "${VICAR_VERSION:=5.0}"

MISSIONS="${1:-m20}"
if [ $# -ge 2 ]; then
    IMAGE_TAG="$2"
elif [ "${MISSIONS}" = "m20" ]; then
    IMAGE_TAG="terrain-intelligence-generator:fullfeatured"
else
    IMAGE_TAG="terrain-intelligence-generator:fullfeatured-$(echo "${MISSIONS}" | tr ' ' '-')"
fi

echo "Building ${IMAGE_TAG}"
echo "  base:     ${BASE_IMAGE}:${BASE_TAG}"
echo "  missions: ${MISSIONS}"
echo ""

docker pull --platform linux/amd64 "${BASE_IMAGE}:${BASE_TAG}"

# Context is the parent directory: the calibration installer and the pinned
# digests live in visor/ and are shared with the VISOR variant build.
docker build \
    --platform linux/amd64 \
    --file "${SCRIPT_DIR}/fullfeatured/Dockerfile" \
    --build-arg "BASE_IMAGE=${BASE_IMAGE}" \
    --build-arg "BASE_TAG=${BASE_TAG}" \
    --build-arg "VICAR_VERSION=${VICAR_VERSION}" \
    --build-arg "VISOR_MISSIONS=${MISSIONS}" \
    --tag "${IMAGE_TAG}" \
    "${SCRIPT_DIR}"

echo ""
docker images "${IMAGE_TAG%%:*}"
echo ""
echo "Test it with:"
echo "  ${SCRIPT_DIR}/test-fullfeatured-image.sh ${IMAGE_TAG}"
