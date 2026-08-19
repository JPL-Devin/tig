# Terrain Intelligence Generator - fullfeatured variant
#
# One image that runs the shipped demos with no external downloads: the
# published base image plus one mission's VISOR calibration, already on
# MARS_CONFIG_PATH.
#
# It bundles calibration and nothing else, because nothing else is missing:
# every program the demos invoke (marsrad, marsmap, marsxyz, marsmesh,
# marsnav, stretch, vicario, obj2gltf, marstile, ...) is already in the base
# image, and the demo scripts themselves run on the host through the tig CLI.
# Adding packages would only undo the slimming that took the base image from
# ~8GB to ~2GB.
#
# The base image is used as-is rather than rebuilt: VICAR is identical for
# every mission, so rebuilding it would cost an hour of CI and produce a
# byte-different copy of the same thing.
#
# The build context is terrain-intelligence-generator/ rather than this
# directory, because the calibration installer and the pinned digests are
# shared with visor/Dockerfile and a COPY cannot reach outside the context:
#
#   docker build --platform linux/amd64 \
#       -f fullfeatured/Dockerfile \
#       --build-arg VISOR_MISSIONS=m20 \
#       -t terrain-intelligence-generator:fullfeatured .
#
# VISOR_MISSIONS is a space-separated list, exactly as in visor/Dockerfile, so
# a multi-mission image is just --build-arg VISOR_MISSIONS="m20 msl".

ARG BASE_IMAGE=ghcr.io/nasa-ammos/tig/terrain-intelligence-generator
ARG BASE_TAG=opensource

FROM ${BASE_IMAGE}:${BASE_TAG}

# Mission to bundle. Valid values: m20 mer msl msam phx nsyt
# M20 is the default because the shipped demos are written around M20 NavCam
# products; bundling every mission would be a 20GB image nobody needs.
ARG VISOR_MISSIONS=m20
# The VICAR release the calibration assets belong to, and their date stamp.
ARG VICAR_VERSION=5.0
ARG VISOR_CALIBRATION_DATE=20230608
ARG VISOR_SPLIT_SUFFIXES="aa ab ac ad ae af ag ah"

LABEL org.opencontainers.image.title="Terrain Intelligence Generator (fullfeatured)"
LABEL org.opencontainers.image.description="Terrain Intelligence Generator with VISOR mission calibration bundled, ready to run the TIG demos with no external downloads"
LABEL org.nasa.ammos.visor.calibration.source="https://github.com/NASA-AMMOS/VICAR/releases/tag/5.0"

# Where the tig CLI mounts user-supplied calibration, and what MARS tools call
# CONFIG_PATH. Keeping the bundled data here means a user mount at the same
# path shadows all of it instead of merging with it.
ENV VISOR_CALIB=/usr/local/vicar/mars_calib

# MARS CONFIG_PATH is a colon-separated search list whose entries need not
# exist, so it can name every mission regardless of which ones this image
# actually bundles. The mount point itself comes first: calibration mounted
# there holds one mission's files directly, with no mission directory, and
# must win.
ENV MARS_CONFIG_PATH=/usr/local/vicar/mars_calib:/usr/local/vicar/mars_calib/m20:/usr/local/vicar/mars_calib/mer:/usr/local/vicar/mars_calib/msl:/usr/local/vicar/mars_calib/msam:/usr/local/vicar/mars_calib/phx:/usr/local/vicar/mars_calib/nsyt

ENV VISOR_MISSIONS=${VISOR_MISSIONS}
# What this image is, for tests and for anything that has only the running
# container to go on.
ENV TIG_VARIANT=fullfeatured

# Pinned digests of the release assets, kept in the image as provenance, and
# the installer, shared with visor/Dockerfile.
COPY visor/calibration.sha256 /usr/local/share/visor-calibration.sha256
COPY visor/install-visor-calibration.sh /usr/local/libexec/install-visor-calibration.sh

# One layer: the installer downloads, verifies, extracts and deletes each
# archive, then asserts the result is on MARS_CONFIG_PATH.
RUN /usr/local/libexec/install-visor-calibration.sh ${VISOR_MISSIONS}
