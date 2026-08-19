# MMGIS Integration

Gets a TIG product onto a map in [MMGIS](https://github.com/NASA-AMMOS/MMGIS),
NASA-AMMOS' Multi-Mission Geographic Information System, without vendoring any
MMGIS code: a pinned `docker-compose.yml` for MMGIS + PostGIS, an export script
that turns TIG outputs into layers MMGIS can serve, and a registration script
that adds those layers to a mission through MMGIS' Configure REST API.

Two product types are covered:

| TIG product | MMGIS layer type | How it gets there |
|---|---|---|
| `demo-panorama-mosaic.sh --projection vertical` mosaic (`workspace/panorama.img`) | `tile` | `vicario` to PNG, `gdal_translate` to a georeferenced GeoTIFF, `gdal2tiles.py` to a mercator TMS `{z}/{x}/{y}.png` pyramid |
| `demo-mesh-generation-with-xyz.sh` mesh (`workspace/terrain.obj`) | `model` | SITE frame rotated into the model frame, `obj2gltf` to a GLB, texture gamma pre-encoded |

## What MMGIS ingests (and where that is defined)

Everything below was read out of the MMGIS repository, not assumed:

- A mission is a row of JSON config in Postgres plus a directory on disk.
  `scripts/server.js` serves `Missions/` statically at `/Missions`, so a layer
  `url` of `Layers/foo/{z}/{x}/{y}.png` resolves against
  `/Missions/<mission>/`.
- `tile` layers take `url` with `{z}/{x}/{y}`, `tileformat` (`tms` here),
  `minZoom`/`maxNativeZoom`/`maxZoom` and a `boundingBox` of
  `[west, south, east, north]` — see `docs/pages/Configure/Layers/Tile/Tile.md`
  and the tile layers in
  `blueprints/Missions/Reference-Mission-Mars/config.reference-mission-mars.json`.
- `model` layers take `url` plus `position.longitude`, `position.latitude`,
  `position.elevation`, and optional `rotation.x/y/z` and `scale`
  (`docs/pages/Configure/Layers/Model/Model.md`,
  `plugins/core/layertypes/Model/globe/layerConfig.js`). They render on the
  **globe only**, per `plugins/core/layertypes/Model/plugin.json`.
- **glTF/GLB vs 3D Tiles**: `model` is the plain-mesh layer type. Its
  `plugin.json` advertises `"fileTypes": ["dae", "obj"]`, but the loaders it
  goes through — `src/essence/Basics/Viewer_/ModelViewer.js` and the bundled
  LithoSphere globe — also accept `gltf` and `glb`. 3D Tiles is a *separate*
  layer type (`plugins/core/layertypes/ThreeDTiles/`) that needs an OGC 3D
  Tiles `tileset.json`, which TIG cannot produce: `obj2gltf` emits glTF/GLB,
  not a tileset. So this example ships a GLB as a `model` layer and keeps the
  OBJ/MTL/texture beside it as the documented fallback.
- Layers are registered with the Configure REST API rooted at `/api/configure`
  (`docs/pages/APIs/Configure/Configure_REST_API.md`): `POST /add` creates a
  mission from `API/templates/config_template.js`, `POST /upsert` replaces a
  mission's config, `POST /addLayer` appends a layer. Mutating routes need an
  admin session or `Authorization:Bearer <token>`.

## Prerequisites

- Docker with the Compose plugin, and ~1 GB of free disk for the images
- `tig-cli` (`pip install tig-cli`) with the TIG image pulled
- GDAL ≥ 3.4 (`apt install gdal-bin`) for `gdal_translate` and `gdal2tiles.py`
- `python3` and `curl`

## 1. Produce TIG products

Follow [the mesh demo](../../docs/demos/mesh-generation.md) and
[the panorama demo](../../docs/demos/panorama-mosaic.md), including their
calibration setup. The mosaic must be a **vertical** projection — a plan view is
what a raster tile layer means; cylindrical and polar mosaics are
camera-centred and belong in a Viewer panel, not on the map. The export script
refuses anything whose label is not `MAP_PROJECTION_TYPE='VERTICAL'`.

```bash
./demo-panorama-mosaic.sh --projection vertical --min-x -30 --max-x 30 \
    --min-y -30 --max-y 30 /path/to/frame_list.txt   # -> workspace/panorama.img
./demo-mesh-generation-with-xyz.sh ...               # -> workspace/terrain.obj
```

## 2. Georeferencing: what you have to supply

**TIG's in-situ products carry no planetary coordinates.** `marsmap` writes
`REFERENCE_COORD_SYSTEM_NAME='SITE_FRAME'` and extents in metres, and
`marsmesh` writes vertices in the same frame — metres from the rover's site
origin, +X north, +Y east, +Z down. Nothing in the product says where that
origin is on Mars.

So you must supply it: `--site-lon` and `--site-lat` are required arguments.
Get them from the mission's localization products (for M20/MSL, the
per-sol `RVER`/localization tables published with the PDS releases, or the
rover-position layers in the MMGIS layer index). Consequences worth being
explicit about:

- The mosaic is placed by converting its metre extents to degrees with a
  spherical approximation about that origin (`--radius`, default the Mars
  equatorial radius). At a 60 m product this is sub-pixel, but it is not a
  substitute for a real projected reprojection over large extents.
- The mesh's SITE frame is *not* the globe's model frame: an MMGIS `model` has
  +Y away from the planet centre, so the export rotates (north, east, down) to
  (east, up, south), recentres the vertices on the mesh centroid, and anchors
  the layer at that centroid's longitude/latitude. `rotation.x/y/z` is then zero
  legitimately — the axes are already in the renderer's frame — but this assumes
  the SITE frame's +X is true north. If your localization says otherwise, put
  the difference in `rotation.y`.
- `--mesh-elev` defaults to 0, which sits the mesh on the globe's zero surface
  (this mission has no elevation layer, so that surface is the ellipsoid). A
  real site elevation only helps once the mission has a matching terrain layer.

Nothing here invents a datum, a north azimuth, or a rover quaternion.

## 3. Export

```bash
./tig-to-mmgis.sh \
    --mosaic /path/to/workspace/panorama.img \
    --mesh   /path/to/workspace/terrain.obj \
    --site-lon 137.4417 --site-lat -4.5895
```

This writes `Missions/TIG-Demo/Layers/<layer>/…` plus one
`Missions/TIG-Demo/<layer>.layer.json` per product — the MMGIS layer objects,
ready to register. `--mission`, `--missions-dir` and the layer names are
options; `./tig-to-mmgis.sh --help` lists them.

Two things about the raster path are worth knowing before changing it:

- **The pyramid has to be mercator, not geodetic.** A mission without
  `projection.custom` leaves Leaflet on a Mars-radius web mercator CRS
  (`src/essence/Basics/Map_/Map_.js`), so a `-p geodetic` pyramid 404s at every
  zoom — the row indices do not line up. That is exactly how this first failed
  here: the tiles were fine when fetched by hand, and the browser asked for
  different ones.
- **The GeoTIFF is tagged `EPSG:4326`, not a Mars ellipsoid**, because GDAL/PROJ
  reject a custom Mars ellipsoid here ("Source and target ellipsoid do not
  belong to the same celestial body"). The degree extents are still computed
  with the body radius, and MMGIS takes the body radius from the mission config,
  so only the intermediate GeoTIFF's own label is Earth-flavoured. If you need
  correct planetary metadata on it, MMGIS ships `auxiliary/gdal2customtiles`.

The mesh has one non-obvious step. `obj2gltf`'s TAE `.pdf` describes it as
narrowly implemented for "a mesh with 3D vertex positions and a single texture
coordinate … paired with a single unlit textured material": it writes no vertex
normals, and does not declare the material unlit either, so MMGIS' globe shades
a normal-less mesh and draws it almost black. The export fixes both ends:

- it patches the GLB to declare `KHR_materials_unlit` and `doubleSided`, so the
  loader skips lighting entirely, and
- it pre-encodes the texture with gamma 2.2 (`--texture-gamma`, `1` disables),
  because that globe is three.js r118 with `outputEncoding = LinearEncoding`
  while `GLTFLoader` tags the base colour texture sRGB: the texture is
  linearised on sampling and never re-encoded, costing about a gamma.

Even so the model renders dimmer than the same imagery served as a tile layer —
that is the globe's model shading path, not a broken export.

## 4. Bring up MMGIS

```bash
cp env.example .env      # then change SECRET and DB_PASS
docker compose up -d
```

`docker-compose.yml` is MMGIS' own `docker-compose.sample.yml` reduced to the
two services this path needs — MMGIS pinned to `ghcr.io/nasa-ammos/mmgis:5.2.24`
and PostGIS — and it mounts `./Missions` into the container. The sample's
optional adjacent servers (STAC, TiPG, TiTiler, TiTiler-pgSTAC, veloserver) are
omitted because static TMS tiles and a mesh served off disk do not use them;
copy them back from the sample if you want COG/`image` layers instead.

Create the admin account the Configure API requires (once, on a fresh database):

```bash
curl -X POST http://localhost:8888/api/users/first_signup \
    -H "Content-Type: application/json" \
    -d '{"username":"tigadmin","password":"<strong password>"}'
```

## 5. Register the layers

```bash
./register-layers.sh --center-lon 137.4417 --center-lat -4.5895 \
    --username tigadmin --password '<strong password>'
```

That creates the mission, points its `msv.view` at the site and sets the body
radii, then `POST /addLayer`s every `*.layer.json`. Pass `--token` instead of
`--username`/`--password` to use an API token from the Configure page. Then open
<http://localhost:8888/?mission=TIG-Demo>: the mosaic draws in **Map**, and the
mesh in **Globe** (model layers are globe-only).

Note: deleting a mission from the Configure page renames its directory to
`<mission>_deleted_` on disk, so re-registering needs the directory renamed
back or the export re-run.

## What was actually run

Verified on this machine, end to end, against the real MSL sample data from the
VICAR 5.0 release (`visor_sample_data_20230623.tar.gz`, the
`CylindricalMosaic` NavCam frames and the `OrthorectifiedMosaic`
`NLB_712299508{XYZ,ILT}_F0961766NCAM00293M1.IMG` stereo product) with the MSL
VISOR calibration:

- `marsmap` vertical mosaic, 60 m × 60 m at 0.05 m/pixel, and `marsmesh`
  terrain with its texture, both produced through `tig`.
- `tig-to-mmgis.sh` on both products: PNG via `vicario`, GeoTIFF, a 23-tile
  mercator TMS pyramid over zoom 18-20, an 8.9 MB GLB via `obj2gltf` with the
  unlit patch and the gamma-2.2 texture, and both layer objects.
- `docker compose up -d` with this compose file: MMGIS 5.2.24 and PostGIS both
  healthy, `/api/utils/healthcheck` passing.
- `register-layers.sh` against that instance: mission created, view set, both
  layers added, and re-run to confirm it updates rather than duplicates them.
- MMGIS loaded in Chrome on the mission: the mosaic draws in Map (tile requests
  HTTP 200, NavCam imagery), and the mesh draws on the Globe at its anchor with
  legible NavCam texture. Both were measured off the canvas rather than
  eyeballed — the mesh went from mean luma 10/255 before the unlit and gamma
  fixes to 36/255 after, against 127/255 for the same imagery as a tile layer.
  Screenshots are in the pull request.

Two caveats about the environment this ran in: `gdal2tiles.py` printed a NumPy
1.x/2.x ABI warning and still produced correct tiles, and Chrome needed software
WebGL (`--use-angle=swiftshader-webgl`) because the box has no GPU.

Not verified: the M20 path. The M20 VISOR calibration bundle (~2.7 GB split
across two release assets) was not downloaded, so only MSL products were run
through this. Nothing in the export is mission-specific, but it has only been
executed on MSL data. The mesh's `rotation` is left at zero, which is a
placement default and not an alignment claim.
