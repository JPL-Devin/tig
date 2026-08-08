# Terrain Intelligence Generator - VISOR calibration variant
#
# Adds VISOR mission calibration data to the published base image so MARS
# tools run without the user downloading and mounting gigabytes of
# calibration by hand.
#
# The base image is used as-is rather than rebuilt: VICAR is identical for
# every mission, so rebuilding it once per mission would cost an hour of CI
# each time and produce byte-different copies of the same thing.
#
#   docker build --platform linux/amd64 \
#       -f visor/Dockerfile \
#       --build-arg VISOR_MISSIONS=nsyt \
#       -t terrain-intelligence-generator:visor-nsyt .
#
# VISOR_MISSIONS is a space-separated list, so an all-missions image is just
#   --build-arg VISOR_MISSIONS="m20 mer msl msam phx nsyt"

ARG BASE_IMAGE=ghcr.io/nasa-ammos/tig/terrain-intelligence-generator
ARG BASE_TAG=opensource

FROM ${BASE_IMAGE}:${BASE_TAG}

# Missions to bundle. Valid values: m20 mer msl msam phx nsyt
ARG VISOR_MISSIONS=nsyt
# The VICAR release the calibration assets belong to, and their date stamp.
# Calibration ships as assets of the same release as the VICAR binaries.
ARG VICAR_VERSION=5.0
ARG VISOR_CALIBRATION_DATE=20230608
# Suffixes tried when a mission is not published as a single asset. GitHub
# caps a release asset at 2GB, so the larger missions are split with
# split(1) and have to be concatenated before they can be extracted.
ARG VISOR_SPLIT_SUFFIXES="aa ab ac ad ae af ag ah"

LABEL org.opencontainers.image.title="Terrain Intelligence Generator (VISOR calibration)"
LABEL org.opencontainers.image.description="Terrain Intelligence Generator with VISOR mission calibration data bundled"
# Where the calibration itself comes from; image.source is left to CI, which
# sets it to the repository holding this recipe.
LABEL org.nasa.ammos.visor.calibration.source="https://github.com/NASA-AMMOS/VICAR/releases/tag/5.0"

# Where the tig CLI mounts user-supplied calibration, and what MARS tools
# call CONFIG_PATH. Keeping the bundled data here means a user mount at the
# same path shadows all of it instead of merging with it.
ENV VISOR_CALIB=/usr/local/vicar/mars_calib

# MARS CONFIG_PATH is a colon-separated search list whose entries need not
# exist, so it can name every mission regardless of which ones this image
# actually bundles - which keeps it a constant that ENV can express. The
# mount point itself comes first: calibration mounted there holds one
# mission's files directly, with no mission directory, and must win.
ENV MARS_CONFIG_PATH=/usr/local/vicar/mars_calib:/usr/local/vicar/mars_calib/m20:/usr/local/vicar/mars_calib/mer:/usr/local/vicar/mars_calib/msl:/usr/local/vicar/mars_calib/msam:/usr/local/vicar/mars_calib/phx:/usr/local/vicar/mars_calib/nsyt

ENV VISOR_MISSIONS=${VISOR_MISSIONS}

# Download, extract and delete the archives in a single layer: compressed
# calibration is the same order of magnitude as extracted calibration, so
# keeping the downloads in an earlier layer would nearly double the image.
# Nothing is written to disk in compressed form at all - curl concatenates
# the split parts on stdout and tar reads them as one stream.
RUN set -eu; \
    base_url="https://github.com/NASA-AMMOS/VICAR/releases/download/${VICAR_VERSION}"; \
    staging=/tmp/visor-staging; \
    mkdir -p "${VISOR_CALIB}"; \
    for mission in ${VISOR_MISSIONS}; do \
        asset="visor_calibration_${VISOR_CALIBRATION_DATE}_${mission}.tar.gz"; \
        url="${base_url}/${asset}"; \
        if curl -fsIL -o /dev/null "${url}"; then \
            sources="${url}"; \
        else \
            sources=""; \
            for suffix in ${VISOR_SPLIT_SUFFIXES}; do \
                curl -fsIL -o /dev/null "${url}${suffix}" || break; \
                sources="${sources} ${url}${suffix}"; \
            done; \
        fi; \
        if [ -z "${sources}" ]; then \
            echo "No release asset ${asset}, whole or split, in VICAR ${VICAR_VERSION}" >&2; \
            exit 1; \
        fi; \
        echo "Fetching ${mission} calibration:"; \
        for source in ${sources}; do echo "  ${source}"; done; \
        rm -rf "${staging}"; mkdir -p "${staging}"; \
        curl -fsSL ${sources} | tar -xz -C "${staging}"; \
        if [ -d "${staging}/calibration/${mission}" ]; then \
            mv "${staging}/calibration/${mission}" "${VISOR_CALIB}/${mission}"; \
        else \
            mkdir -p "${VISOR_CALIB}/${mission}"; \
            mv "${staging}"/* "${VISOR_CALIB}/${mission}/"; \
        fi; \
        rm -rf "${staging}"; \
        echo "${mission}: $(du -sh "${VISOR_CALIB}/${mission}" | cut -f1) extracted"; \
    done; \
    chmod -R a+rX "${VISOR_CALIB}"

# Fail the build rather than publish an image whose calibration the tools
# would not find
RUN set -eu; \
    for mission in ${VISOR_MISSIONS}; do \
        test -d "${VISOR_CALIB}/${mission}/camera_models" \
            || { echo "No camera models for ${mission}" >&2; exit 1; }; \
        case ":${MARS_CONFIG_PATH}:" in \
            *":${VISOR_CALIB}/${mission}:"*) ;; \
            *) echo "${mission} is not on MARS_CONFIG_PATH" >&2; exit 1;; \
        esac; \
    done; \
    echo "VISOR calibration bundled: ${VISOR_MISSIONS} ($(du -sh "${VISOR_CALIB}" | cut -f1))"
