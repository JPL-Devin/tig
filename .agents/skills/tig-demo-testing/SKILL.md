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

## Fastest way to get calibration: `./fetch-calibration.sh`
`./fetch-calibration.sh --list` probes the VICAR 5.0 release with `HEAD` per asset (~5 s, no auth)
and prints real download sizes; `VICAR_VERSION=4.9 --list` should show every row `not published`,
which is a cheap way to prove the sizes are probed rather than hardcoded. Install with
`TIG_CALIBRATION_DEST=<dir> TIG_CALIBRATION_CACHE=<dir> ./fetch-calibration.sh -y <mission>`.

When testing, prefer `nsyt` (111 MB download / 159 MB installed) — it installs in seconds and is
enough for `tig --calibration-path <dest>/nsyt ...` to see `camera_models/`. `msam` (349 MB / 516 MB)
is a good second mission; **never** pull `m20` (2.5 GB / 5.3 GB) just to exercise the code path.
Always point `TIG_CALIBRATION_DEST`/`TIG_CALIBRATION_CACHE` (or a fake `HOME`) at `/tmp` so the real
`~/.mars_calib` stays empty — otherwise later "calibration missing" negative tests silently pass.

### Exercising the auto-download end to end (~2 min, run it before the demos)
Every step below is cheap and self-contained; run them in order in a throwaway tree, and expect the
quoted output. `$F` is the repo's `fetch-calibration.sh`.
```bash
F=$PWD/fetch-calibration.sh; T=$(mktemp -d); D=$T/dest; C=$T/cache
"$F" --list                                              # nsyt row: 111 MB / 159 MB, "available"
VICAR_VERSION=4.9 "$F" --list                            # every row "not published"
"$F" --dest "$D" --cache "$C" nsyt < /dev/null           # refuses: "re-run with --yes"
"$F" -y --keep-archives --dest "$D" --cache "$C" nsyt     # "verified" then "installed .../nsyt (159M)"
"$F" --list --dest "$D"                                   # nsyt row now "installed in $D/nsyt"
"$F" -y --dest "$D" --cache "$C" nsyt                     # "nsyt: already installed in ..."
rm -rf "$D/nsyt"
"$F" -y --keep-archives --dest "$D" --cache "$C" nsyt   # "already downloaded" — no second transfer
```
Keep `--keep-archives` on every step: without it a successful install deletes the cached archive, and
the next case re-downloads 111 MB instead of exercising the cache.

A mission that is already installed is skipped before anything is downloaded, so **every failure
case below needs the install to look incomplete first** — emptying `param_files/` is the cheapest way
(`installed_mission` wants a non-empty `camera_models/` *and* `param_files/`), and it leaves the rest
of the tree in place as the thing that must survive. Assert on that tree, not just the exit code:
```bash
rm -rf "$D/nsyt/param_files"/*
VISOR_CHECKSUMS=/dev/null "$F" -y --keep-archives --dest "$D" --cache "$C" nsyt  # "no pinned SHA-256", exit 1
ls "$D/nsyt/camera_models" | wc -l                               # still 12 — untouched
printf 'X' | dd of="$C/visor_calibration_20230608_nsyt.tar.gz" bs=1 seek=999 conv=notrunc
"$F" -y --keep-archives --dest "$D" --cache "$C" nsyt   # "could not resume ...; starting over", verified
```
A full-length corrupted cache is the case that used to wedge forever (HTTP 416 on every resume), so
keep it in the rotation. To prove a *transient* failure keeps its partial file instead, shim curl to
fail only the resume attempt and check the partial survives:
```bash
mkdir -p "$T/bin"; truncate -s 40000000 "$C/visor_calibration_20230608_nsyt.tar.gz"
printf '#!/bin/bash\nfor a in "$@"; do [ "$a" = "-C" ] && exit 28; done\nexec /usr/bin/curl "$@"\n' > "$T/bin/curl"
chmod +x "$T/bin/curl"; rm -rf "$D/nsyt/param_files"/*
PATH=$T/bin:$PATH "$F" -y --keep-archives --dest "$D" --cache "$C" nsyt  # names the cache; file still 40 MB
```
Tar-escape guard (runs for *every* download, not only `--allow-unverified`): build a small tar.gz
holding `calibration/nsyt/evil -> /etc` plus dummy `camera_models`/`param_files` entries, pin its
real digest in a `VISOR_CHECKSUMS` file, and confirm the script refuses with "holds absolute or
parent-relative paths" and leaves `$D` untouched.

Finally the demo-facing path, which is what a new user actually hits. The entry point is
`calibration_setup` (not `find_calibration`, which only probes host paths and never fetches), and the
image probe has to be stubbed so the local `:opensource` image does not answer first:
```bash
env -i HOME=$T/fakehome CALIB_MISSION=nsyt TIG_FETCH_CALIBRATION=1 PATH=$PATH bash -c \
  'source find-calibration.sh; find_image_calibration() { return 1; }; calibration_setup && echo "$CALIB_DIR"'
```
That installs into `$HOME/.mars_calib/nsyt` and echoes it as `CALIB_DIR`. Repeat it without
`TIG_FETCH_CALIBRATION` and with stdin redirected from `/dev/null`: it must print
`No calibration found. <repo>/fetch-calibration.sh nsyt downloads it ...` and return 1 rather than
hang. Keep `HOME` pointed at a scratch dir for both — this path deliberately writes to the real
`~/.mars_calib` otherwise.

Hazards seen while testing this script:
- On a fast network a 350 MB asset finishes in ~5 s, so a mid-download SIGINT test needs a throttled
  curl: put `#!/bin/bash\nexec /usr/bin/curl --limit-rate 3M "$@"` in a dir at the front of `PATH`.
  `kill -INT` the *script's own* pgid (`ps -eo pid,pgid,cmd | grep fetch-calibration`); `pgrep -f`
  tends to match the outer `bash -c` wrapper instead.
- `fetch_mission` is called as `fetch_mission "$m" || failed+=(...)`, which disables `set -e` inside
  the whole call chain, so every download and extraction step has to check its own status. Force a
  failure (corrupt a cached archive, or `VISOR_CHECKSUMS=/dev/null` without `--allow-unverified`)
  and assert on the destination tree, not just the exit code: an existing `<dest>/<mission>` must
  survive untouched.
- Cover a pre-existing *empty* `<dest>/<mission>` directory too: a plain `mv "$extracted"
  "$DEST/$mission"` would nest the tree at `<dest>/<mission>/<mission>` instead of replacing it.
- Integration tests of `calibration_setup` need `find_image_calibration` stubbed out (source
  `find-calibration.sh`, then redefine it to `return 1`) plus `env -i HOME=<fakehome>` — otherwise
  the local `:opensource` image probe answers first and the fetch path is never reached.
  `CALIB_MISSION=<m>` picks the mission, `TIG_FETCH_CALIBRATION=1` accepts without asking, and
  with neither a tty nor that env var the helper prints the command instead of hanging.

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
- `marsbrt` conditioning depends on the mode: a `DO_LINEAR` (gain+offset) solve on a multi-sol set is
  ill-conditioned — multipliers vary in *sign* between runs and can be near-zero *positive*, which a
  guard testing only `MultCorr <= 0` misses. Gain-only (`do_what=DO_MULT`) is well conditioned on the
  same data (multipliers ~0.23-2.51, AddCorr exactly 0, BRTCORR renders keeping σ ~85 / ~210). If a
  BRTCORR stage looks flattened (σ < 1, or < 10% of the raw σ), suspect the mode before the data.
- When a demo scrapes a VICAR log table with awk, check the scan *terminates* at the blank line after
  the table and requires the captured field to be numeric — otherwise trailing log lines (e.g.
  `Solution 9 0.000005 written`) fake a near-zero entry and trip the guard. You can test that scan in
  isolation on a hand-written fake log instead of re-running the pipeline.
- Overlap coverage: `marsmap ovr_out=` only measures frames whose footprint lands inside the
  `--bounds` window, but `marsbrt` still assigns every frame in `joint.lis` a multiplier — so an
  out-of-bounds tie frame is silently normalised by an unconstrained value. To exercise that path you
  need frames with genuinely different pointing: the six M20 sol-649/650 frames are all co-pointed
  (`INSTRUMENT_AZIMUTH` 1.19-1.39°), so no `--bounds` can include the primaries and exclude a tie
  frame. Use MSL `sample_data/CylindricalMosaic` ILT frames instead, which span all azimuths — e.g.
  two `NCAM00293` frames as the epochs with an `NCAM00294` frame (~200° away) as `--tie-extra` and
  `--bounds "150 200 5 -40"`. Verify per-frame coverage directly with
  `grep '<img ' joint.ovr | grep -o 'key="[0-9]*"' | sort | uniq -c` against the `<images>` block.
- Pitfall to check in any of these demos: a validation helper that `echo`s its result for capture
  (`MIN=$(validate ...)`) **swallows its own error messages**, because the `echo "ERROR: ..."` goes to
  the same captured stdout. With `set -e` the script exits with the right status but prints nothing,
  so a guardrail looks like a silent crash. When a demo aborts with no message, re-run the failing
  helper outside command substitution, or check whether stderr (`>&2`) was used.
- Workspace reuse: the demo marks its workspace with a dotfile and refuses a non-empty directory that
  lacks it (without deleting anything), but on an *accepted* reuse it deletes its own product types
  (`*.img *.png *.log *.lis ...`) first. Copy any PNG/log evidence out of a workspace before re-running
  into it, and note that killing a run mid-way leaves that workspace stripped.
- Guardrail negative tests that abort before the first VICAR call are cheap and worth doing first:
  a bogus `--calibration` must fail fast even when a valid `MARS_CONFIG_PATH` is exported (no silent
  fallback), and a nested `<dir>/mars_calibration_m20` layout must resolve — you can fake the nested
  layout with symlinks to the real `camera_models`/`param_files`.

## Testing image-bundled calibration in the demo scripts
- The in-image branch is proven only if the run is genuinely clean: wrap every command in
  `env -u MARS_CONFIG_PATH -u MARS_CALIB_PATH` **and** re-check the other probe paths
  (`<repo>/calibration`, `~/.mars_calib`, `/opt/mars_calib`, `./mars_calibration_m20`,
  `./mars_calib`) right before each run. A stray probe silently sends you down the host branch.
- Expected in-image message on a `:fullfeatured` (m20) image:
  `Using calibration bundled in the container image: /usr/local/vicar/mars_calib/m20` — the plain
  `/usr/local/vicar/mars_calib` entry is on the image's `MARS_CONFIG_PATH` first but has no
  `camera_models/`, so the mission subdirectory is the expected answer.
- Two independent proofs of where calibration came from, worth capturing both:
  - the MARS program log line `... (path=/usr/local/vicar/mars_calib/m20/camera_models/...)`
    (`marsrad`, `marsuvw`, and friends all print it);
  - `docker inspect -f '{{range .Mounts}}...'` on the `tig-vicar-*` container: an in-image run has
    **no** mount whose destination is `/usr/local/vicar/mars_calib`, while a host run shows
    `<hostcalib> -> /usr/local/vicar/mars_calib (rw=false)`. Note `tig` keys containers by
    mount set, so the host and in-image runs get *different* containers on the same image tag —
    list all of them when inspecting.
- Comparing host-calibration and in-image outputs: don't use `md5sum` on VICAR `.img`/`.uvw`
  products. Their labels embed `DAT_TIM`, so two identical runs differ in ~3 bytes. Use
  `cmp -l a b` (expect only bytes inside the label, `LBLSIZE=...`) or, for the PNGs,
  `compare -metric AE a b diff.png` (expect `0`).

## Getting M20 data from PDS when you need it
- Directory listings and w10n are useless: any PDS listing URL, and any wrong filename, returns a
  **24702-byte HTML landing page** with HTTP 200. Always check `size_download` and that the file
  starts with `ODL_VERSION_ID`. w10n JSON returns empty `leaves`/`nodes`.
- What did work (only exact, known filenames):
  - raw EDR bundle:
    `https://planetarydata.jpl.nasa.gov/img/data/mars2020/mars2020_navcam_ops_raw/data/sol/00650/ids/edr/ncam/<PRODUCT>.IMG`
  - calibrated FDR bundle, same shape with `mars2020_navcam_ops_calibrated/.../ids/fdr/ncam/`.
  Filenames quoted in `docs/demos/*.md` are the reliable source; guessed SCLK/version codes fail.
- A verified M20 stereo pair (both eyes, same SCLK), used by
  `docs/demos/surface-characteristics.md`:
  `N{L,R}F_0650_0724654097_444EDR_N0320000NCAM08111_01_295J01.IMG` from the raw EDR bundle.
- Don't be scared off by `DETECTOR_LINE_SAMPLES=5120` in the label: M20 NavCam products are stored
  1280x960, 3 bands, 16-bit (file size 7,424,000). Full stereo correlation + mesh on such a pair
  takes ~5 minutes, not hours.

## Co-registration data suitability (bites you before any calibration bug does)
- `marschkovl` applies a fixed pointing pre-test — `angle= <deg> theshold= 53.000000` in
  `marschkovl.log` — and `--overlap` does **not** move that threshold. Frames from a 360 deg
  panorama sequence are typically 55-131 deg apart, so you get `0 pair(s)`, then
  `0 tiepoints`, then `marsnav` `** ABEND called **`. That is a data problem, not a demo bug.
- What works: frames at (nearly) the same pointing. A left/right NavCam stereo pair is the cheapest
  such input on hand and gave `1 pair`, 25 tiepoints, mean pixel error 2.390458 -> 1.364541.
  Repeat-coverage sequences (e.g. sol 650 `NCAM08111`) are the documented case.

## Known non-fatal stage failures
- `demo-surface-characteristics.sh` step 6 (`marsirough -heli`) abends with
  `[VIC2-GENERR] Exception in XVREAD, processing file: zix_heli.img / [VIC2-EOF] End of file` when
  the input is an XYZ-derived cloud; the demo prints `⚠ marsirough abended; continuing without it`
  and finishes. This reproduces identically with host calibration, so it is unrelated to where
  calibration comes from — don't attribute it to a calibration change.
- `demo-mesh-generation-with-xyz.sh` prints `Left image: x` /
  `Note: could not read NL/NS from the label; skipping the size check` for PDS `.IMG` inputs. Only
  the optional size pre-check is skipped; the pipeline still runs.

## Shell hazards while inspecting output
- `pkill -f "display ..."` matches the agent's own shell command line and kills it. Close viewers by
  window (`wmctrl -c`) or just launch the next `display` in a fresh exec call.
- `nohup ... > log 2>&1 &` plus a later `cat log` must use an absolute path: one-shot exec calls do
  not share a working directory.

## Verifying TIG products inside MMGIS (examples/mmgis-integration)
`cd examples/mmgis-integration && docker compose up -d` starts MMGIS 5.2.24 + postgis on
`http://localhost:8888`; `Missions/` is bind-mounted, so products are served off disk. Open
`http://localhost:8888/?mission=<MISSION>` and log in (AUTH=local dev creds).

Browser/env gotchas that cost the most time:
- Chrome must be started with `--use-angle=swiftshader-webgl --enable-unsafe-swiftshader`, otherwise
  the 3D globe (Cesium) has no WebGL. Even then the globe is heavy: allow 20-30 s after clicking
  `GLOBE`, and a large/scaled model can crash the tab ("Aw, Snap!", error code 5).
- **Log in once.** MMGIS rate-limits `/api/users/login`; repeated logins return HTTP 429 and the
  client reload-loops forever on the animated splash screen (looks like a broken mission config).
  A `curl` login from the shell **invalidates the browser session**, forcing another UI login — so
  do all API work (register-layers.sh, /api/configure/*) *before* logging in in the browser.
  If you do get rate-limited, `docker compose restart mmgis` resets the limiter (no rebuild needed).
- A mission with no basemap renders as pure black in Map and as a starfield in Globe, so "nothing
  visible" is ambiguous — always corroborate with the Network panel and by opening a product URL
  (e.g. a single `.png` tile) directly in a tab to prove the file itself is good.
- `/api/configure/destroy` deletes the mission's `Missions/<name>/` directory too (it renames it to
  `<name>_deleted_`), so scratch missions clean up on disk as well; `GET /api/configure/missions`
  confirms what is left.
- Per-layer `Locate` / `Settings` icons only appear when hovering a layer row in the Layers tool. A
  `type: model` layer's Settings panel exposes **only Opacity** — position/rotation/elevation cannot
  be adjusted from the UI, so placement bugs must be diagnosed by registering a scratch mission.
  `Locate` does **not** move the globe camera for `type: model` layers.
- Navigating the globe to exact coordinates: the globe's on-canvas controls are `SPIN / TILT / PAN /
  ZOOM` (top-right) plus a gear icon that opens the **Observe** panel with numeric Latitude,
  Longitude, Height, Azimuth, Elevation and FOV fields — that panel is the only reliable way to put
  the camera at a known lon/lat. Observe is a first-person mode (WASD to move, ESC to quit) and can
  end up staring at empty sky; ESC restores the previous orbit view. Scroll-zoom and the ZOOM +/-
  buttons move the camera only a little per click at close range, so budget many clicks.
- Toggling a layer's checkbox off/on in the Layers tool is the cheapest proof that a faint/dark blob
  on the globe really is that layer, and it does not modify the shipped mission config.
- The globe also drapes `type: tile` layers on the ellipsoid, so a working tile pyramid gives a
  bright scale/orientation reference next to a model layer in the same view.
- Test-harness artifact: agent tooling that dumps the annotated DOM can trigger requests to
  *truncated* URLs (e.g. `.../tig_vertical_mosaic/20...`) which 404 with initiator `<anonymous>` or
  a `VM…` script. Do not report those as app 404s — cross-check with
  `performance.getEntriesByType('resource')` and only trust URLs ending in `.png`.

Two failure modes seen with tig-exported products (both are product/config bugs, not MMGIS bugs):
- **Tile pyramid grid mismatch.** `gdal2tiles` run in geodetic mode writes
  `tilemapresource.xml` with `profile="geodetic"` (EPSG:4326). MMGIS/Leaflet requests
  web-mercator TMS rows, so at z20 near lat -4.59 it asks for `y≈510904-510907` while the pyramid
  on disk holds `y=248774-248777` -> every tile 404s and the map stays black. Check
  `ls Layers/<layer>/<z>/<x>/` against the y in the 404 URLs before blaming serving/auth.
- **Model placement.** Parse the GLB to check where its vertices actually are:
  `struct.unpack` the header, read the JSON chunk, look at `accessors[POSITION].min/max`. The tig
  mesh is not origin-centred (e.g. X -100..-55 m, Y +63..+136 m, Z -37..-14 m) and glTF is Y-up
  while the SITE frame is Z-down, so the patch lands tens of metres off the anchor and rotated.
  Combined with `position.elevation: -4501` (Gale crater MOLA elevation) the patch sits ~4.5 km
  below MMGIS's zero-elevation globe surface and is occluded. Registering the same GLB at
  `elevation: 0` in a scratch mission is the cheapest way to separate "buried" from "never drawn".
  Fix confirmed in a later run: rotating SITE (+X north/+Y east/+Z down) into MMGIS' model frame
  (+X east/+Y up/+Z south), recentring vertices on the centroid and anchoring at the centroid's real
  lon/lat with `elevation: 0` makes the mesh draw.
- **Unlit / almost-black model.** A GLB whose primitive has `POSITION` + `TEXCOORD_0` but **no
  `NORMAL` accessor** renders nearly black in the MMGIS globe (LithoSphere/three.js): the texture is
  applied but the surface is effectively unlit, so the mesh is visible only as a faint gray shape.
  Check `meshes[0].primitives[0].attributes` in the GLB JSON chunk; if `NORMAL` is missing, ask the
  exporter to write vertex normals (and consider `doubleSided: true` on the material) rather than
  blaming the browser's software WebGL.
- **Still almost-black after declaring `KHR_materials_unlit`.** Patching the GLB to add
  `KHR_materials_unlit` + `doubleSided: true` does make three.js build an unlit `MeshBasicMaterial`
  (verify at runtime, see below) — but the model can *still* render near-black, because MMGIS 5.2.24
  ships three.js r118 with `renderer.outputEncoding = LinearEncoding (3000)` while `GLTFLoader` tags
  the glTF baseColorTexture `sRGBEncoding (3001)`. The texture is therefore gamma-linearised on
  sampling and never re-encoded on output, costing roughly a factor of ~4 in brightness (measured:
  mesh-region luma ~10/255 vs ~127/255 for the same NavCam imagery served as a tile layer, which is
  unaffected because tile textures keep the default LinearEncoding). Confirm/refute this in ~1 min
  from the browser console, read-only, without touching any mission config:
  ```js
  const r = L_.Globe_.litho.renderer;         // LithoSphere renderer
  r._.renderer.outputEncoding                 // 3000 = Linear  -> mismatch likely
  let m; r.scene.traverse(o => { if (o.isMesh && o.name === 'mesh_0') m = o; });
  m.material.type            // 'MeshBasicMaterial' => the unlit patch DID take effect
  m.material.map.encoding    // 3001 = sRGB -> double gamma; set to 3000 + needsUpdate = true
  ```
  If flipping `map.encoding` to `3000` makes the surface legible, the GLB/material is fine and the
  remaining defect is colour-space handling in the viewer. **Confirmed exporter-side fix:** pre-encode
  the texture with gamma 2.2 (`gdal_translate -ot Byte -scale <src_min> <src_max> 0 255 -exponent 0.4545`;
  8-bit PNG input is already 0..255, but marsmesh's Int16 `texture.img` spans e.g. 147..4048), which
  cancels the loader's sRGB→linear step (texture mean luma ~71 → ~141; rendered mesh-region luma
  ~10 → ~36, i.e. it reproduces the runtime-override result of ~43 without any browser patching).
  Note obj2gltf keeps the texture as a *relative URI* (`images[0].uri = "texture.png"`), so the GLB is
  byte-identical after a texture-only change — when re-verifying, hard-reload and confirm
  `texture.png`'s own `decodedBodySize` changed, not just the GLB's. Even when fixed, the model stays
  noticeably dimmer than the same imagery served as a tile layer (luma ~36 vs ~127), so judge
  "legible" by visible rock/shadow detail, not by matching the raster's brightness. Always revert any
  runtime tweak after the diagnostic and quantify
  brightness from the screenshot (e.g. PIL mean luma over the mesh region) instead of eyeballing it —
  note screenshots are saved at the display's real resolution (1600x1200), so scale coordinates taken
  from the 1024x768 tool space by ~1.5625 before cropping.
- Expect the mesh and the mosaic to be tens/hundreds of metres apart on the globe if the mesh
  centroid is far from the SITE origin the mosaic is anchored to — verify the intended relative
  placement before declaring either one "misplaced".

## Verifying the CI test scripts in terrain-intelligence-generator/
These are the scripts CI runs; all take an image tag and are quick enough to run repeatedly against
`ghcr.io/nasa-ammos/tig/terrain-intelligence-generator:opensource`.
- `test-product-pipeline.sh <image>` — 7 assertions, synthetic in-image fixture, no calibration,
  no download. ~9 s wall on an 8-core VM. Expect `Passed: 7 / Failed: 0`, exit 0.
- `test-marsirough-abend.sh <image> [--xyz FILE]` — **inverted exit convention**: exit 0 means the
  upstream ZIX abend still reproduces, exit 1 means it stopped reproducing (image fixed → docs and
  `demo-surface-characteristics.sh` need updating), exit 2 is a setup failure. Do not read exit 0
  as "no bug". ~8 s synthetic, ~3 s with `--xyz` on a real product. Verify the printed
  `marsirough.log` tail actually contains `Exception in XVREAD, processing file: zix.img` and
  `** ABEND called **`, plus `uix.img: 3 bands` / `zix.img: 1 bands` — otherwise the script is
  claiming a reproduction it did not show. The synthetic path prints
  `Frame SITE expected 1 indices, got 0`; a real MSL XYZ instead prints
  `Generating Roughness using the SITE_FRAME coordinate frame.` Both still abend.
- `test-calibration-demo.sh [<image>] [--data-dir DIR] [--keep]` — downloads ~1.1 GB of VICAR 5.0
  release assets into a `mktemp -d` (removed on exit) unless `--data-dir` is given. On a fast link
  the whole run is ~35 s; only ~620 MB lands on disk because only two members are extracted from
  the sample tarball. Expected products for the MSL `NCAM00353` XYZ: slope/heading/ntilt/roughness
  each 4,214,784 bytes, `normals_*.uvw` and `tilt_heli.img` 12,603,392, `goodness_heli.img`
  1,067,008, `slope.img` stddev ≈ 12.55 deg.

### Proving the calibration really came from the host mount
`✓ A calibration camera model was read` is a strong assertion only because
`/usr/local/vicar/mars_calib` **does not exist inside the image** — confirm with
`docker run --rm <image> ls /usr/local/vicar/mars_calib` (expect "No such file or directory").
So a `Successfully read calibration camera model ... (path=/usr/local/vicar/mars_calib/...)` line can
only come from the bind-mounted `MARS_CONFIG_PATH`.

### Cleanup expectations after running the CI scripts
`tig-marsirough-repro-$$` and `tig-product-test-$$` containers and their `mktemp -d` workspaces are
removed by `trap ... EXIT`; `docker ps -a | grep -E 'tig-marsirough|tig-product-test'` and
`ls -d /tmp/tmp.*` should both come back empty. The persistent `tig-vicar-<hash>` containers the
`tig` CLI itself creates *do* survive (one per working directory) and are not a leak from these
scripts — snapshot `docker ps -a` before testing so you do not misattribute them.

### Lint expectations
`bash -n` is clean on all of these. `shellcheck` flags one SC2086 (info) in
`test-marsirough-abend.sh` on the deliberately unquoted `${PLATFORM_FLAG}` (quoting it would pass an
empty argument to `docker run`), so treat that one as expected. `actionlint` is clean on
`product-tests.yml` and `calibration-demo.yml`; the older `build-publish-*` workflows carry
pre-existing SC2086/SC2129 notes and a "softprops/action-gh-release@v1 runner too old" note, so lint
the new files by name rather than running bare `actionlint` and reading the whole repo's noise.

## Devin Secrets Needed
None. Everything above runs from local data plus a public container image. The MMGIS example's local
dev login lives in `examples/mmgis-integration/.env` (AUTH=local); no org secret is required.
