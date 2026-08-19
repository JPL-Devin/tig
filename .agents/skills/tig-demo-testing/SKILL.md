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
  the texture with gamma 2.2 (`gdal_translate -scale 0 255 0 255 -exponent 0.4545 texture.png`), which
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

## Devin Secrets Needed
None. Everything above runs from local data plus a public container image. The MMGIS example's local
dev login lives in `examples/mmgis-integration/.env` (AUTH=local); no org secret is required.
