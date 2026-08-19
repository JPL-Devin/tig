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

## Getting calibration and frames when the box has none
PDS bulk download may be unavailable: every `pds-imaging.jpl.nasa.gov` /
`pdsimage2.wr.usgs.gov` / `planetarydata.jpl.nasa.gov` data path can answer `200` with the HTML
bundle landing page instead of the file, and the w10n JSON listings can come back with empty
`nodes`/`leaves`. Do not conclude the frame names in `docs/demos/*.md` are wrong — check the first
bytes of what you downloaded (`head -c 200 f.IMG` showing `<!DOCTYPE HTML` means you got the
landing page).

The VICAR 5.0 GitHub release assets do work and are the reliable fallback (see
`docs/demos/downloading-visor-data.md`):
```bash
mkdir -p ~/visor_data
curl -sL "https://github.com/NASA-AMMOS/VICAR/releases/download/5.0/visor_sample_data_20230623.tar.gz" | tar -zxf - -C ~/visor_data
curl -sL "https://github.com/NASA-AMMOS/VICAR/releases/download/5.0/visor_calibration_20230608_msl.tar.gz" | tar -zxf - -C ~/visor_data
# M20 calibration is split in two parts and must be concatenated (5.3 GB extracted)
curl -sL ".../visor_calibration_20230608_m20.tar.gzaa" ".../visor_calibration_20230608_m20.tar.gzab" | tar -zxf - -C ~/visor_data
```
Budget ~15 GB of disk and several minutes; run the `curl | tar` in the background and poll `du -sh`.

The sample data has **no M20 frames**, but `sample_data/CylindricalMosaic/` holds 19 MSL left-NavCam
`ILT` frames from a single site/drive (`F0961766`, sequences `NCAM00293`/`00353`/`00354`) that drive
the panorama and co-registration demos fine with
`MARS_CONFIG_PATH=~/visor_data/calibration/msl`. Five `NLB_*NCAM00293*.IMG` frames yield a
7696x1217 cylindrical mosaic and a 26-tiepoint marsnav solution (mean pixel error 6.19 -> 2.65).
MSL NavCam is 1024x1024, not M20's 1280x960, and covers ~110 deg of azimuth rather than 360, so
expect a mostly-black 360 deg canvas — judge the imagery, not the frame coverage.

## Running the demos
- The demo scripts write into `./workspace` relative to the *current* directory, so run every case
  in its own fresh empty scratch dir; otherwise outputs from a previous case are mistaken for the
  new run's.
- Five 1280x960 NavCam frames build a 360-degree cylindrical mosaic in ~8 s on an 8-core VM.
- Point the demos at a specific image with `export CONTAINER_IMAGE=<tag>`; `tig --shutdown` between
  images, since `tig` reuses a running container and would otherwise keep serving the old one.

## The demos must call every MARS program through `tig`
There is no `marsmap`/`marsbrt`/`marsrad` binary on the host — they only exist inside the container.
A demo line that invokes one as a bare command (`marsmap inp=...` instead of `tig marsmap inp=...`)
fails with `line N: marsmap: command not found` (exit 127). The demos pipe these calls into
`grep ... || true`, which **swallows the `command not found` message**, so the only symptom is the
script's own downstream check, e.g. `❌ ERROR: marsmap wrote no overlap file`. When a demo step
reports "wrote no output", re-run the script under `bash -x` and then run the traced command
standalone without the `| grep` to see the real error. Beware of shell functions defined near the
top of a demo (`marsmap() { tig ... marsmap "$@"; }`) — they can be the only thing making a bare
call resolve, so deleting them breaks the script even though the diff looks like a pure cleanup.

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

A clean way to prove the image-level fix at runtime: run the same `tig marsmap inp=... out=...` twice
with only `CONTAINER_IMAGE` differing. The fixed image exits 0 and writes the mosaic; an image
predating the fix exits 136 with
`/usr/local/libexec/vicar-run: line 97: Floating point exception(core dumped)` and writes nothing.

## Verifying image output without a browser
Do not judge success by exit code / file size alone; VICAR tools often exit non-zero on success and
can emit an all-black image.
- Dimensions and brightness stats: `python3 -c "from PIL import Image, ImageStat; ..."`. Pillow may
  not be in the system Python — activate the venv that provides `tig`
  (`source ~/repos/fprime/fprime-venv/bin/activate`) and `pip install pillow` there. numpy may not
  be present — use `ImageStat`/`histogram()` instead. Set
  `Image.MAX_IMAGE_PIXELS=None` for large mosaics.
- Actually look at the pixels: downscale with `convert IN -resize 1400x OUT.png` and display it with
  ImageMagick on the X display, then screenshot:
  `nohup env DISPLAY=:0 display -geometry +0+0 /path/small.png >/dev/null 2>&1 &`
  followed by a screenshot. Launch it with `nohup ... &` in its own exec call — backgrounding it in
  the same call as other commands sometimes returns before the window maps.
- Useful sanity numbers for the sol 300 NavCam panorama: stretched PNG mean grey ≈ 59, stddev ≈ 43;
  an unstretched (`--no-stretch`) PNG is markedly darker (mean ≈ 29).
- For the 5-frame MSL `NCAM00293` cylindrical panorama: 7696x1217, mean ≈ 44, stddev ≈ 67 (the low
  mean is the unfilled part of the 360 deg canvas); the rover deck is visible at bottom centre.
- `Image.resize()` then `save()` is a fine substitute for `convert` if ImageMagick's `convert` is
  missing; `display` is usually still installed.

## Inspecting products
- `tig label -list inp=panorama.img` shows `MAP_PROJECTION_TYPE`, `MAP_RESOLUTION` (pixels/degree),
  `START_AZIMUTH`/`STOP_AZIMUTH`, `ZERO_ELEVATION_LINE`, `SOURCE_PRODUCT_ID`.
- Frame pointing lives in the input label: `INSTRUMENT_AZIMUTH` is in the site frame, while the
  "mast azimuth" people usually quote is the RSM articulation angle
  (`Property: RSM_ARTICULATION_STATE` → first `ARTICULATION_DEVICE_ANGLE` element, in radians).
  These two differ by roughly 180 degrees on M2020 — don't compare them to each other.

## Two-epoch change monitoring (`demo-change-monitoring.sh`)
- Runs ~6 min for six M20 NavCam frames on an 8-core VM; each run writes ~700 MB into its
  `--workspace`, so always give a fresh workspace dir and keep any reference run untouched.
- The whole solve happens *before* the statistics-box validation, so a negative test on `--box`
  (e.g. `--box "1 1 100 100"`) costs a full run — budget for it.
- Reference magnitudes with `--scale 40 --bounds "0 93 0 -70" --box "100 300 2200 3100"`:
  15/15 overlap pairs, ~1000 tiepoints, residual ~7.6 → ~1.5 px, renders 2799x3720,
  epoch σ 367.5 / 713.6, raw diff σ ~638, gain ~700, linear ~384.4, shift controls
  133.68 / 220.01 / 270.25, difpic re-render 0, no-navtable σ 102-107 with ~10.2 M pixels differing.
  Tiepoint counts, residuals and the *nav-effect* control move a few percent run to run (the
  nav-effect control depends on the nav solution); the pixel-shift controls are stable to 4 figures.
- `marsbrt` is genuinely ill-conditioned on multi-sol sets: multipliers vary in *sign* between runs,
  so a guard that only tests `MultCorr <= 0` can miss a run where the primaries get near-zero
  *positive* multipliers. Check that the flattened-σ guard (BRTCORR σ < 10% of raw σ) is what fires.
- Pitfall to check in any of these demos: a validation helper that `echo`s its result for capture
  (`MIN=$(validate ...)`) **swallows its own error messages**, because the `echo "ERROR: ..."` goes to
  the same captured stdout. With `set -e` the script exits with the right status but prints nothing,
  so a guardrail looks like a silent crash. When a demo aborts with no message, re-run the failing
  helper outside command substitution, or check whether stderr (`>&2`) was used.

## Devin Secrets Needed
None. Everything above runs from local data plus a public container image.
