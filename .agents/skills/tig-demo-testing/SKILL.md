---
name: tig-demo-testing
description: How to run and verify the tig VICAR demo shell scripts (mesh, panorama mosaic, co-registration) end to end on a Linux box, including calibration setup, path translation inside list files, the MPI/hwloc startup crash on older images, and how to visually check the generated imagery.
---

# Testing tig demo scripts (CLI, image-producing)

## Prerequisites
- Docker daemon running and the VICAR image pulled:
  `docker pull ghcr.io/nasa-ammos/tig/terrain-intelligence-generator:opensource`
- `tig` CLI on PATH (`pip install -e tig-cli` or `pip install tig-cli`).
- Mission calibration on disk with `camera_models/` and `param_files/` subdirs, exported as
  `MARS_CONFIG_PATH` (e.g. `/home/ubuntu/visor/calibration/m20`). `find-calibration.sh` also probes
  `$MARS_CALIB_PATH`, `<repo>/calibration`, `~/.mars_calib`, `/opt/mars_calib`, `./mars_calibration_m20`,
  `./mars_calib` — when testing the "calibration missing" error path, confirm none of those exist,
  otherwise the negative test silently passes through.
- Sample data: Mars 2020 NavCam FDR `.IMG` frames from a single site/drive (e.g. sequence
  `NCAM00501`, five left-NavCam frames per sol).

## Running the demos
- The demo scripts write into `./workspace` relative to the *current* directory, so run every case
  in its own fresh empty scratch dir; otherwise outputs from a previous case are mistaken for the
  new run's.
- Five 1280x960 NavCam frames build a 360-degree cylindrical mosaic in ~8 s on an 8-core VM.

## Path translation stops at the command line
`tig` rewrites host paths in *arguments* only. The contents of a list file (`inp=frames.lis`) are read
by the MARS program inside the container and are never rewritten, so the list must already hold
container-visible paths: `$HOME` is mounted at its host path, everything else is read-only under
`/host`, so `/data/x.IMG` must appear as `/host/data/x.IMG`. Symlinks must be resolved first
(`readlink -f`) — `tig` resolves paths before mapping them, and an unresolved link target is not
reachable in the container. Getting this wrong shows up as `Unable to create file model for input 0`
followed by `** ABEND called **`, so test any list-driven demo with inputs outside `$HOME`.

## MPI/hwloc SIGFPE (fixed in the image)
`marsmap`, `marsremos`, `marscor2` and `marsint` call `MPI_Init`, and the bundled hwloc divides by
zero in its x86 backend on some host CPUs, so on an older image they die with `Floating point
exception (core dumped)` (exit 136) before any output. The image now sets `HWLOC_COMPONENTS=-x86`
itself, so no per-invocation workaround is needed; on an image predating that, prefix the run with
`tig env HWLOC_COMPONENTS=-x86`. If a MARS step mysteriously produces no output and exits 136, check
that `tig printenv HWLOC_COMPONENTS` shows `-x86`.

## Verifying image output without a browser
Do not judge success by exit code / file size alone; VICAR tools often exit non-zero on success and
can emit an all-black image.
- Dimensions and brightness stats: `python3 -c "from PIL import Image, ImageStat; ..."` (Pillow is
  present; numpy may not be — use `ImageStat`/`histogram()` instead). Set
  `Image.MAX_IMAGE_PIXELS=None` for large mosaics.
- Actually look at the pixels: downscale with `convert IN -resize 1400x OUT.png` and display it with
  ImageMagick on the X display, then screenshot:
  `nohup env DISPLAY=:0 display -geometry +0+0 /path/small.png >/dev/null 2>&1 &`
  followed by a screenshot. Launch it with `nohup ... &` in its own exec call — backgrounding it in
  the same call as other commands sometimes returns before the window maps.
- Useful sanity numbers for the sol 300 NavCam panorama: stretched PNG mean grey ≈ 59, stddev ≈ 43;
  an unstretched (`--no-stretch`) PNG is markedly darker (mean ≈ 29).

## Inspecting products
- `tig label -list inp=panorama.img` shows `MAP_PROJECTION_TYPE`, `MAP_RESOLUTION` (pixels/degree),
  `START_AZIMUTH`/`STOP_AZIMUTH`, `ZERO_ELEVATION_LINE`, `SOURCE_PRODUCT_ID`.
- Frame pointing lives in the input label: `INSTRUMENT_AZIMUTH` is in the site frame, while the
  "mast azimuth" people usually quote is the RSM articulation angle
  (`Property: RSM_ARTICULATION_STATE` → first `ARTICULATION_DEVICE_ANGLE` element, in radians).
  These two differ by roughly 180 degrees on M2020 — don't compare them to each other.

## Devin Secrets Needed
None. Everything above runs from local data plus a public container image.
