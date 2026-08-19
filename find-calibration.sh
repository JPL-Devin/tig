#!/bin/bash
# Helper script to locate MARS calibration files
# Checks multiple common locations and environment variables

# Priority order for calibration location:
# 1. MARS_CONFIG_PATH, then MARS_CALIB_PATH environment variables
# 2. Relative to script location (for demos in repo)
# 3. User's home directory
# 4. /opt/mars_calib (system-wide)
# 5. Current directory
# 6. Calibration bundled inside the container image tig will use (the
#    :fullfeatured and :visor-<mission> variants), which needs no host copy

# Given a candidate directory, echo the first path that is a VALID calib dir:
# either the candidate itself, or a nested mars_calibration_m20/ inside it.
# Returns 0 on success (and echoes the valid path), 1 otherwise.
resolve_calib_dir() {
    local candidate="$1"
    [ -d "$candidate" ] || return 1

    if verify_calibration "$candidate"; then
        echo "$candidate"
        return 0
    fi

    # Common repo layout: wrapper dir containing mars_calibration_m20/
    if verify_calibration "$candidate/mars_calibration_m20"; then
        echo "$candidate/mars_calibration_m20"
        return 0
    fi

    return 1
}

find_calibration() {
    local script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    local candidate

    # Priority-ordered list of candidate locations
    for candidate in \
        "$MARS_CONFIG_PATH" \
        "$MARS_CALIB_PATH" \
        "$script_dir/calibration" \
        "$HOME/.mars_calib" \
        "/opt/mars_calib" \
        "./mars_calibration_m20" \
        "./mars_calib"; do

        [ -n "$candidate" ] || continue

        local resolved
        if resolved=$(resolve_calib_dir "$candidate"); then
            echo "$resolved"
            return 0
        fi
    done

    # Not found
    return 1
}

verify_calibration() {
    local calib_dir="$1"
    
    if [ ! -d "$calib_dir" ]; then
        return 1
    fi
    
    # Check for required subdirectories
    local has_cameras=false
    local has_params=false
    
    if [ -d "$calib_dir/camera_models" ] && [ -n "$(ls -A $calib_dir/camera_models 2>/dev/null)" ]; then
        has_cameras=true
    fi
    
    if [ -d "$calib_dir/param_files" ] && [ -n "$(ls -A $calib_dir/param_files 2>/dev/null)" ]; then
        has_params=true
    fi
    
    if $has_cameras && $has_params; then
        return 0
    else
        return 1
    fi
}

# Echo the first entry of the image's own MARS_CONFIG_PATH that really holds
# camera_models/ and param_files/. Probing through tig picks the same image and
# runtime a real invocation would.
find_image_calibration() {
    command -v tig > /dev/null 2>&1 || return 1

    # Say so first: on a cold cache this starts the container, and tig pulls
    # gigabytes of image before the probe itself runs.
    echo "No calibration on this host; checking the container image" \
         "(pulls it if it is not present yet)..." >&2

    local errfile found
    errfile=$(mktemp)

    # Unset: a host MARS_CONFIG_PATH makes tig mount over the bundled data.
    found=$(env -u MARS_CONFIG_PATH tig sh -c '
        [ -n "$MARS_CONFIG_PATH" ] || exit 1
        IFS=:
        for dir in $MARS_CONFIG_PATH; do
            [ -n "$dir" ] || continue
            [ -n "$(ls -A "$dir/camera_models" 2>/dev/null)" ] || continue
            [ -n "$(ls -A "$dir/param_files" 2>/dev/null)" ] || continue
            echo "$dir"
            exit 0
        done
        exit 1
    ' 2> "$errfile") || true

    # A runtime failure (no daemon, pull refused) is not "the image carries no
    # calibration", so let the user see it rather than the calibration help.
    if [ -z "$found" ] && [ -s "$errfile" ]; then
        sed 's/^/  tig: /' "$errfile" >&2
    fi
    rm -f "$errfile"

    [ -n "$found" ] || return 1
    echo "$found"
}

# Resolve calibration for a demo, host first. On success exactly one of
# CALIB_DIR (a host directory) or CALIB_IN_IMAGE (a path inside the image) is
# set; returns 1 with both empty when neither is available.
calibration_setup() {
    CALIB_DIR=""
    CALIB_IN_IMAGE=""

    local host_dir
    if host_dir=$(find_calibration); then
        CALIB_DIR="$host_dir"
        return 0
    fi

    local image_dir
    if image_dir=$(find_image_calibration) && [ -n "$image_dir" ]; then
        CALIB_IN_IMAGE="$image_dir"
        return 0
    fi

    return 1
}

print_calibration_help() {
    cat << 'EOF'
ERROR: MARS calibration files not found.

The mesh generation tools require MARS calibration files containing:
  - camera_models/  (CAHV/CAHVOR/CAHVORE camera models)
  - param_files/    (camera mapping XML, flat field parameters)
  - flat_fields/    (optional, for radiometric correction)

To specify calibration location, use one of:

1. Environment variable (recommended; tig reads this one too):
   export MARS_CONFIG_PATH=/path/to/mars_calibration_m20

2. User home directory:
   mkdir -p ~/.mars_calib
   cp -r /path/to/calibration/* ~/.mars_calib/

3. System-wide installation:
   sudo mkdir -p /opt/mars_calib
   sudo cp -r /path/to/calibration/* /opt/mars_calib/

4. Local directory:
   cp -r /path/to/calibration ./mars_calib

5. An image that already carries the calibration, downloading nothing:
   export CONTAINER_IMAGE=ghcr.io/nasa-ammos/tig/terrain-intelligence-generator:fullfeatured

The script will check these locations in order:
  1. $MARS_CONFIG_PATH
  2. $MARS_CALIB_PATH
  3. ./calibration (repo structure)
  4. ~/.mars_calib
  5. /opt/mars_calib
  6. ./mars_calibration_m20
  7. ./mars_calib
  8. inside $CONTAINER_IMAGE (:fullfeatured, :visor-<mission>)

For TIG repository users:
  Calibration is already in: ./calibration/

EOF
}

# Main execution when sourced or run directly
if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
    # Script is being executed directly
    if calibration_setup; then
        if [ -n "$CALIB_DIR" ]; then
            echo "Found calibration at: $CALIB_DIR"
        else
            echo "Found calibration inside the container image at: $CALIB_IN_IMAGE"
        fi
        exit 0
    else
        print_calibration_help
        exit 1
    fi
fi
