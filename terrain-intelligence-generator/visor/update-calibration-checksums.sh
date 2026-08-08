#!/bin/bash
#
# update-calibration-checksums.sh - Regenerate calibration.sha256
#
# Run this when the calibration assets change release or date stamp. It
# streams every asset once and never keeps it on disk, so it needs bandwidth
# but no space.
#
# Usage:
#   ./update-calibration-checksums.sh [vicar-version] [calibration-date]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VICAR_VERSION="${1:-5.0}"
CALIBRATION_DATE="${2:-20230608}"
MISSIONS="m20 mer msl msam phx nsyt"
SPLIT_SUFFIXES="aa ab ac ad ae af ag ah"
BASE_URL="https://github.com/NASA-AMMOS/VICAR/releases/download/${VICAR_VERSION}"
OUTPUT="${SCRIPT_DIR}/calibration.sha256"

{
    echo "# SHA-256 of the VISOR calibration assets of the VICAR ${VICAR_VERSION} release. The build"
    echo "# refuses to bundle an asset that is absent from this file or fails its digest."
    echo "# Regenerate with ./update-calibration-checksums.sh after changing releases."
} > "${OUTPUT}"

for mission in ${MISSIONS}; do
    asset="visor_calibration_${CALIBRATION_DATE}_${mission}.tar.gz"

    names=()
    if curl -fsIL -o /dev/null "${BASE_URL}/${asset}"; then
        names=("${asset}")
    else
        for suffix in ${SPLIT_SUFFIXES}; do
            curl -fsIL -o /dev/null "${BASE_URL}/${asset}${suffix}" || break
            names+=("${asset}${suffix}")
        done
    fi

    if [ ${#names[@]} -eq 0 ]; then
        echo "No release asset ${asset}, whole or split, in VICAR ${VICAR_VERSION}" >&2
        exit 1
    fi

    for name in "${names[@]}"; do
        echo "Hashing ${name}" >&2
        digest=$(curl -fsSL "${BASE_URL}/${name}" | sha256sum | cut -d' ' -f1)
        echo "${digest}  ${name}" >> "${OUTPUT}"
    done
done

echo "" >&2
cat "${OUTPUT}"
