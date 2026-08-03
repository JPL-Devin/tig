#!/bin/bash
# Build script for TIG+GeoCal Docker image

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOCKER_FILE="$SCRIPT_DIR/Dockerfile.geocal"

# Default values
IMAGE_NAME="tig"
IMAGE_TAG="geocal"
GEOCAL_VERSION="e4c3cb071f3063352bd35b3048ddad1c077e10db"
PUSH_IMAGE=false

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --name)
            IMAGE_NAME="$2"
            shift 2
            ;;
        --tag)
            IMAGE_TAG="$2"
            shift 2
            ;;
        --geocal-version)
            GEOCAL_VERSION="$2"
            shift 2
            ;;
        --push)
            PUSH_IMAGE=true
            shift
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: $0 [--name IMAGE_NAME] [--tag IMAGE_TAG] [--geocal-version VERSION] [--push]"
            exit 1
            ;;
    esac
done

FULL_IMAGE_NAME="${IMAGE_NAME}:${IMAGE_TAG}"

echo ""
echo "╔════════════════════════════════════════════════════╗"
echo "║  Building TIG+GeoCal Image                         ║"
echo "╚════════════════════════════════════════════════════╝"
echo ""
echo "Image:          ${FULL_IMAGE_NAME}"
echo "GeoCal version: ${GEOCAL_VERSION}"
echo "Push:           ${PUSH_IMAGE}"
echo ""

# Check if base image exists
echo -e "\e[32m✓\e[0m Checking for base TIG image..."
if ! docker image inspect ghcr.io/nasa-ammos/tig/terrain-intelligence-generator:opensource &> /dev/null; then
    echo -e "\e[31m✗\e[0m Base image not found. Pulling..."
    docker pull ghcr.io/nasa-ammos/tig/terrain-intelligence-generator:opensource
fi

# Build the image
echo -e "\e[32m✓\e[0m Starting build (this will take 45-60 minutes)..."
echo ""

if docker build \
    -f "${DOCKER_FILE}" \
    -t "${FULL_IMAGE_NAME}" \
    --build-arg GEOCAL_VERSION="${GEOCAL_VERSION}" \
    "$SCRIPT_DIR"; then
    echo ""
    echo -e "\e[32m✓\e[0m Build successful: ${FULL_IMAGE_NAME}"
    
    # Show image size
    IMAGE_SIZE=$(docker images "${FULL_IMAGE_NAME}" --format "{{.Size}}")
    echo -e "\e[32m✓\e[0m Image size: ${IMAGE_SIZE}"
    
    # Push if requested
    if [ "$PUSH_IMAGE" = true ]; then
        echo -e "\e[32m✓\e[0m Pushing image..."
        docker push "${FULL_IMAGE_NAME}"
        echo -e "\e[32m✓\e[0m Push complete"
    fi
    
    echo ""
    echo "To run the image:"
    echo "  docker run -it --rm ${FULL_IMAGE_NAME}"
    echo ""
else
    echo ""
    echo -e "\e[31m✗\e[0m Build failed"
    exit 1
fi
