#!/bin/bash
# fetch-calibration.sh - list and download open-source VISOR mission calibration
#
# VISOR calibration is published as assets of the NASA-AMMOS/VICAR release, so
# the data the demos need can be fetched without any institutional access. This
# is the host-side counterpart of terrain-intelligence-generator/visor/
# install-visor-calibration.sh, which bundles the same assets into an image.
#
#   ./fetch-calibration.sh --list            # what is available, and how big
#   ./fetch-calibration.sh msl              # ask, then download and install
#   ./fetch-calibration.sh --yes m20 msl    # no questions asked
#
# Installs into <dest>/<mission> (default ~/.mars_calib), the layout the
# calibration-bundled images use, which find-calibration.sh then discovers.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

VICAR_VERSION="${VICAR_VERSION:-5.0}"
VISOR_CALIBRATION_DATE="${VISOR_CALIBRATION_DATE:-20230608}"
VISOR_SPLIT_SUFFIXES="${VISOR_SPLIT_SUFFIXES:-aa ab ac ad ae af ag ah}"
BASE_URL="https://github.com/NASA-AMMOS/VICAR/releases/download/${VICAR_VERSION}"
CHECKSUMS="${VISOR_CHECKSUMS:-$SCRIPT_DIR/terrain-intelligence-generator/visor/calibration.sha256}"

# Missions VISOR publishes, with the rover or lander each one belongs to.
MISSIONS="m20 mer msl msam phx nsyt"
mission_description() {
    case "$1" in
        m20)  echo "Mars 2020 / Perseverance";;
        mer)  echo "MER / Spirit and Opportunity";;
        msl)  echo "Mars Science Lab / Curiosity";;
        msam) echo "MSAM";;
        phx)  echo "Phoenix";;
        nsyt) echo "InSight";;
        *)    echo "unknown mission";;
    esac
}

# Extracted size, as published in docs/demos/downloading-visor-data.md. Only
# used to warn before filling a disk; the download itself is measured.
extracted_kb() {
    case "$1" in
        m20)  echo 5557452;;
        mer)  echo 1012736;;
        msl)  echo 544768;;
        msam) echo 528384;;
        phx)  echo 641024;;
        nsyt) echo 162816;;
        *)    echo 0;;
    esac
}

DEST="${TIG_CALIBRATION_DEST:-$HOME/.mars_calib}"
CACHE="${TIG_CALIBRATION_CACHE:-${XDG_CACHE_HOME:-$HOME/.cache}/tig/visor-calibration}"
ASSUME_YES=false
KEEP_ARCHIVES=false
DO_LIST=false
ALLOW_UNVERIFIED=false
REQUESTED=()

print_usage() {
    cat << EOF
Usage: $0 [OPTIONS] [MISSION...]

Download open-source VISOR calibration from the NASA-AMMOS/VICAR ${VICAR_VERSION}
release and install it where the tig demos and 'tig' itself look for it.

Missions: ${MISSIONS}

Options:
  --list                 Show the missions published for this release, with
                         download and installed sizes, then exit.
  -y, --yes              Do not ask before downloading (also \$TIG_FETCH_CALIBRATION=1).
  --dest DIR             Install root; each mission lands in DIR/<mission>.
                         Default \$TIG_CALIBRATION_DEST or ~/.mars_calib.
  --cache DIR            Where archives are downloaded and resumed from.
                         Default \$TIG_CALIBRATION_CACHE or
                         \${XDG_CACHE_HOME:-~/.cache}/tig/visor-calibration.
  --keep-archives        Keep the downloaded archives after extraction.
  --allow-unverified     Install an asset that has no pinned SHA-256.
  -h, --help             This message.

Environment: VICAR_VERSION, VISOR_CALIBRATION_DATE and VISOR_CHECKSUMS select a
different release; they default to the pinned ${VICAR_VERSION} assets.
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --list) DO_LIST=true; shift;;
        -y|--yes) ASSUME_YES=true; shift;;
        --dest) DEST="${2:?--dest needs a directory}"; shift 2;;
        --cache) CACHE="${2:?--cache needs a directory}"; shift 2;;
        --keep-archives) KEEP_ARCHIVES=true; shift;;
        --allow-unverified) ALLOW_UNVERIFIED=true; shift;;
        -h|--help) print_usage; exit 0;;
        -*) echo "ERROR: unknown option $1" >&2; print_usage >&2; exit 2;;
        *) REQUESTED+=("$1"); shift;;
    esac
done

if [ "${TIG_FETCH_CALIBRATION:-}" = 1 ]; then
    ASSUME_YES=true
fi

command -v curl > /dev/null || { echo "ERROR: curl is required." >&2; exit 1; }

asset_name() {
    echo "visor_calibration_${VISOR_CALIBRATION_DATE}_$1.tar.gz"
}

human() {
    local kb="$1"
    if [ "$kb" -ge 1048576 ]; then
        awk -v k="$kb" 'BEGIN { printf "%.1f GB", k / 1048576 }'
    else
        awk -v k="$kb" 'BEGIN { printf "%.0f MB", k / 1024 }'
    fi
}

# Echo the space-separated asset parts published for a mission, empty if none.
# GitHub caps a release asset at 2GB, so larger missions are split with
# split(1) and have to be concatenated before they can be extracted.
asset_parts() {
    local mission="$1" asset suffix parts=""
    asset="$(asset_name "$mission")"
    if curl -fsIL -o /dev/null "${BASE_URL}/${asset}"; then
        echo "$asset"
        return 0
    fi
    for suffix in $VISOR_SPLIT_SUFFIXES; do
        curl -fsIL -o /dev/null "${BASE_URL}/${asset}${suffix}" || break
        parts="${parts:+$parts }${asset}${suffix}"
    done
    echo "$parts"
}

# Size of one asset in KB, from the last Content-Length of the redirect chain.
asset_kb() {
    local length
    length=$(curl -fsIL "${BASE_URL}/$1" \
        | tr -d '\r' \
        | awk 'tolower($1) == "content-length:" { n = $2 } END { print n }') || return 1
    [ -n "$length" ] || return 1
    echo $(( length / 1024 ))
}

pinned_sha() {
    [ -f "$CHECKSUMS" ] || return 1
    awk -v n="$1" '$2 == n { print $1 }' "$CHECKSUMS"
}

installed_mission() {
    local dir="$DEST/$1"
    [ -n "$(ls -A "$dir/camera_models" 2>/dev/null)" ] || return 1
    [ -n "$(ls -A "$dir/param_files" 2>/dev/null)" ] || return 1
}

list_missions() {
    echo "VISOR calibration published with VICAR ${VICAR_VERSION} (${BASE_URL}):"
    echo ""
    printf '  %-6s %-30s %10s %10s  %s\n' MISSION DESCRIPTION DOWNLOAD INSTALLED STATUS
    local mission parts part total status
    for mission in $MISSIONS; do
        parts="$(asset_parts "$mission")"
        if [ -z "$parts" ]; then
            printf '  %-6s %-30s %10s %10s  %s\n' \
                "$mission" "$(mission_description "$mission")" - - "not published"
            continue
        fi
        total=0
        for part in $parts; do
            total=$(( total + $(asset_kb "$part" || echo 0) ))
        done
        status="available"
        [ -n "$(pinned_sha "${parts%% *}")" ] || status="available, no pinned digest"
        if installed_mission "$mission"; then
            status="installed in $DEST/$mission"
        fi
        printf '  %-6s %-30s %10s %10s  %s\n' \
            "$mission" "$(mission_description "$mission")" \
            "$(human "$total")" "$(human "$(extracted_kb "$mission")")" "$status"
    done
    echo ""
    echo "Download one with: $0 <mission>"
}

# Warn rather than refuse: df on a network or overlay filesystem can be wrong,
# and the caller knows their disk better than this check does.
check_space() {
    local needed_kb="$1" dir="$2" free
    # Nothing is created before the user says yes, so measure the nearest
    # existing ancestor of the directory that would hold the data.
    while [ ! -d "$dir" ] && [ "$dir" != "/" ] && [ -n "$dir" ]; do
        dir=$(dirname "$dir")
    done
    free=$(df -Pk "$dir" 2>/dev/null | awk 'NR == 2 { print $4 }') || return 0
    [ -n "$free" ] || return 0
    if [ "$free" -lt "$needed_kb" ]; then
        echo "WARNING: $dir has $(human "$free") free, and this needs about" \
             "$(human "$needed_kb")." >&2
    fi
}

confirm() {
    local prompt="$1"
    if $ASSUME_YES; then
        return 0
    fi
    # No terminal to ask at: say what would have been asked instead of hanging.
    if [ ! -t 0 ]; then
        echo "$prompt" >&2
        echo "Not running interactively; re-run with --yes to download." >&2
        return 1
    fi
    local reply
    read -r -p "$prompt [y/N] " reply < /dev/tty
    case "$reply" in
        [yY]|[yY][eE][sS]) return 0;;
        *) echo "Skipped." >&2; return 1;;
    esac
}

download_part() {
    local name="$1" expected
    expected="$(pinned_sha "$name" || true)"
    if [ -z "$expected" ]; then
        if ! $ALLOW_UNVERIFIED; then
            echo "ERROR: no pinned SHA-256 for $name in $CHECKSUMS." >&2
            echo "       Pass --allow-unverified to install it unchecked, or" \
                 "regenerate the file with" \
                 "terrain-intelligence-generator/visor/update-calibration-checksums.sh." >&2
            return 1
        fi
        echo "WARNING: $name has no pinned SHA-256; installing it unverified." >&2
    fi

    # A cached part that already matches is not downloaded again, so an
    # interrupted run of several missions resumes at the part it died on.
    if [ -n "$expected" ] && [ -f "$CACHE/$name" ] \
       && echo "$expected  $CACHE/$name" | sha256sum -c - > /dev/null 2>&1; then
        echo "  $name already downloaded"
        return 0
    fi

    echo "  fetching $name"
    curl -fL --progress-bar -C - -o "$CACHE/$name" "${BASE_URL}/${name}"
    if [ -n "$expected" ]; then
        # A resumed transfer that had appended to a stale or truncated file
        # fails here, so drop it and let the next run start clean.
        echo "$expected  $CACHE/$name" | sha256sum -c - > /dev/null || {
            echo "ERROR: SHA-256 mismatch for $name; removing it." >&2
            rm -f "$CACHE/$name"
            return 1
        }
        echo "  $name verified"
    fi
}

install_mission() {
    local mission="$1" parts="$2" staging part

    mkdir -p "$CACHE" "$DEST"
    for part in $parts; do
        download_part "$part"
    done

    # Staged next to the destination so the move is a rename, not a copy of
    # gigabytes, and a failed extraction never leaves a half-populated mission.
    staging="$(mktemp -d "$DEST/.staging-$mission.XXXXXX")"
    # shellcheck disable=SC2064 # expand the path now, while it is known
    trap "rm -rf '$staging'" EXIT
    echo "  extracting"
    # shellcheck disable=SC2086 # parts is a deliberately word-split name list
    ( cd "$CACHE" && cat $parts ) | tar -xz -C "$staging"

    # The archives hold calibration/<mission>/..., but a future layout that
    # extracts the mission directly is installed as it is.
    local extracted="$staging"
    if [ -d "$staging/calibration/$mission" ]; then
        extracted="$staging/calibration/$mission"
    fi
    if [ -n "$(ls -A "$DEST/$mission" 2>/dev/null)" ]; then
        rm -rf "$DEST/$mission.previous"
        mv "$DEST/$mission" "$DEST/$mission.previous"
    fi
    mv "$extracted" "$DEST/$mission"
    rm -rf "$staging" "$DEST/$mission.previous"
    trap - EXIT

    if ! $KEEP_ARCHIVES; then
        for part in $parts; do rm -f "$CACHE/$part"; done
    fi

    if ! installed_mission "$mission"; then
        echo "ERROR: $DEST/$mission has no camera_models/ and param_files/;" \
             "the archive layout is not what was expected." >&2
        return 1
    fi
    echo "  installed $DEST/$mission ($(du -sh "$DEST/$mission" | cut -f1))"
}

fetch_mission() {
    local mission="$1" parts total=0 part kb

    if installed_mission "$mission"; then
        echo "$mission: already installed in $DEST/$mission"
        return 0
    fi

    parts="$(asset_parts "$mission")"
    if [ -z "$parts" ]; then
        echo "ERROR: VICAR ${VICAR_VERSION} publishes no calibration for" \
             "$mission (asset $(asset_name "$mission"))." >&2
        return 1
    fi
    for part in $parts; do
        kb=$(asset_kb "$part" || echo 0)
        total=$(( total + kb ))
    done

    echo "$mission ($(mission_description "$mission")): $(human "$total") to" \
         "download, $(human "$(extracted_kb "$mission")") installed in $DEST/$mission"
    check_space "$total" "$CACHE"
    check_space "$(extracted_kb "$mission")" "$DEST"
    confirm "Download $mission calibration from ${BASE_URL}?" || return 1
    install_mission "$mission" "$parts"
}

if $DO_LIST; then
    list_missions
    exit 0
fi

if [ ${#REQUESTED[@]} -eq 0 ]; then
    echo "ERROR: no mission given." >&2
    echo "" >&2
    print_usage >&2
    exit 2
fi

for requested in "${REQUESTED[@]}"; do
    case " $MISSIONS " in
        *" $requested "*) ;;
        *) echo "ERROR: unknown mission '$requested'; VISOR publishes:" \
                "$MISSIONS" >&2; exit 2;;
    esac
done

failed=()
for mission in "${REQUESTED[@]}"; do
    fetch_mission "$mission" || failed+=("$mission")
done

if [ ${#failed[@]} -gt 0 ]; then
    echo "Not installed: ${failed[*]}" >&2
    exit 1
fi

echo ""
echo "Point the tools at it with one of:"
echo "  export MARS_CONFIG_PATH=$DEST/${REQUESTED[0]}"
echo "  tig --calibration-path $DEST/${REQUESTED[0]} <tool> ..."
if [ "$DEST" = "$HOME/.mars_calib" ]; then
    echo "The demos and find-calibration.sh find $DEST themselves."
fi
