# Surface Characteristics Demo

Slope, roughness, surface normals and placement/reachability goodness from a
Mars 2020 NavCam XYZ point cloud, driven by [`tig`](../../tig-cli/README.md).

`demo-surface-characteristics.sh` takes the XYZ product the
[mesh demo](mesh-generation.md) already produces and derives the rasters that
describe how drivable and how workable the terrain is.

## What it does

| Stage | Program | Product |
| --- | --- | --- |
| 1 | `marsuvw -slope` | Rover-scale surface normals (`normals_slope.uvw`) |
| 2 | `marsslope` | `slope.img`, `heading.img`, `ntilt.img`, optionally `solar.img` |
| 3 | `marsuvw` | Instrument-scale surface normals (`normals_arm.uvw`) |
| 4 | `marsrough` | `roughness.img` |
| 5 | `marsitilt -heli` | `tilt_heli.img` + the `uix`/`zix` files marsirough needs |
| 6 | `marsirough -heli` | `iroughness_heli.img` — **abends in the published image**, see [Troubleshooting](#troubleshooting) |
| 7 | `marsigood` | `goodness_heli.img` |
| 8 | `marsgreach` | `reach_goodness.img`, only with `--reach` (see [Reachability](#reachability-marsgreach)) |
| 9 | `vicario` | A PNG per product, stretched over a fixed physical range |

Everything after the XYZ is fast: the whole script took **16 seconds** on a
1280x960 NavCam cloud with a warm container. Producing the XYZ from the stereo
pair is the slow part (~10 minutes; see the [mesh demo](mesh-generation.md)).

## Prerequisites

- Docker Engine 20.10+
- `pip install tig-cli`
- An XYZ point cloud with an intact VICAR label — the camera model, the
  coordinate frame and the site are read from it
- M2020 calibration files (see [Calibration Data](../reference/calibration-data.md));
  the script locates them with `find-calibration.sh`

## Usage

```bash
# 1. Produce the XYZ (mesh demo, ~10 minutes for a full-res NavCam pair)
./demo-mesh-generation-with-xyz.sh \
  --stereo-left  NLF_0650_0724654097_444EDR_N0320000NCAM08111_01_295J01.IMG \
  --stereo-right NRF_0650_0724654097_444EDR_N0320000NCAM08111_01_295J01.IMG

# 2. Derive the surface characteristics from it
./demo-surface-characteristics.sh \
  --xyz workspace/pointcloud_filtered.xyz \
  --texture workspace/texture.img \
  --solar-angle 60
```

Options:

| Option | Meaning |
| --- | --- |
| `--xyz FILE` | XYZ point cloud, 3-band REAL (required) |
| `--texture FILE` | Scene image, converted to `scene.png` for context |
| `--instrument NAME` | `heli` (default), `seis`, `hp3` or `wts` for stages 5-7 |
| `--coord FRAME` | Frame for the slope products: `site` (default), `local_level`, `fixed`, `rover`, `instrument` |
| `--solar-angle DEG` | Sun elevation at local noon; enables the solar energy product |
| `--reach FILE` | 6-band arm reachability product; enables `marsgreach` |

Data for the run above came from the PDS Imaging Node NavCam raw bundle:
`https://pds-imaging.jpl.nasa.gov/data/mars2020/mars2020_navcam_ops_raw/data/sol/00650/ids/edr/ncam/`.
Calibration came from the VISOR M20 archive; see
[Downloading VISOR Data](downloading-visor-data.md).

## Stage by stage

### 1. Surface normals, rover scale (`marsuvw -slope`)

```bash
tig marsuvw inp=pointcloud_filtered.xyz out=normals_slope.uvw -slope \
  radius=10 separation=0.5 error=0.02 box_radius=1000 coord=site
```

`marsuvw` fits a plane to the 3D points in a window around each pixel and
writes the unit normal as a 3-band REAL image (U along X, V along Y, W along
Z). Slope mode (`-slope`) scales the window with range, skips pixels for
speed, and — importantly — drops the reject-and-refit loop: a pixel whose plane
fit error exceeds `ERROR` is simply discarded.

The parameters that decide coverage:

| Parameter | Default | Used here | Effect |
| --- | --- | --- | --- |
| `separation` | 0.02 m | 0.5 m | Cartesian radius of the neighbourhood the plane is fitted to. Rover-scale slope wants a rover-scale patch. |
| `radius` | 4 px | 10 px | Image-space half-window the neighbours are gathered from. |
| `error` | 0.0005 m | 0.02 m | Maximum mean plane-fit error. At the 0.5 mm default almost nothing survives over a half-metre patch. |
| `min_points` | 6 | 6 | Minimum points in the fit. |
| `box_radius` | 5 m | 1000 m | X/Y bounding box around `x_center`/`y_center`; effectively disabled here, as the help recommends for slope mode. |

Coverage on the sol-650 cloud: 76.2% of pixels have XYZ, and **75.6%** end up
with a normal. Leaving `error` at its default, changing nothing else, gives
**4.3%** — this one parameter is the difference between a usable slope map and a
speckle.

In the site and local-level frames Z points **down**, so a normal pointing out
of level ground is `w ≈ -1`, which is what this run produces.

### 2. Slope products (`marsslope`)

```bash
tig marsslope inp=pointcloud_filtered.xyz uvw=normals_slope.uvw \
  out=slope.img type=slope coord=site
```

`type` selects the function; all outputs are 1-band REAL with 0 as the missing
value:

| `type` | Formula | Units | Meaning |
| --- | --- | --- | --- |
| `slope` (default) | `90 + atan(w / sqrt(u²+v²))` | degrees | Tilt of the surface. 0 = horizontal. |
| `heading` | `atan2(v, u)` | degrees, -180..180 | Azimuth the slope faces. |
| `magnitude` | `sqrt(u²+v²)` | 0..1 | `sin(slope)`. |
| `direction` | radial component about `origin` | degrees | Slope toward/away from the rover; positive is a climb. Needs `origin=(x,y,z)`. |
| `ntilt` | `asin(u)` | degrees | North-facing component of the slope. |
| `solar` | `u·cos(sa) - w·cos(90-sa)` | 0..1 | Relative insolation from tilt alone; needs `sa`, the sun elevation at local noon. `sa` is a user input, not derived from SPICE. |

What the sol-650 scene actually measured: median slope **2.3°**, 83.5% of valid
pixels at or below 5°, 90th percentile 6.8°. 0.7% of pixels exceed 90°, which is
the sign convention reporting a flipped normal — those are horizon and
range-noise pixels, not overhangs.

### 3. Surface normals, instrument scale (`marsuvw`)

```bash
tig marsuvw inp=pointcloud_filtered.xyz out=normals_arm.uvw x_center=2 box_radius=5
```

`marsrough` wants normals over a patch the size of an instrument contact, not
the size of the rover, so the defaults (`separation=0.02`, `error=0.0005`,
`radius=4`) are the right ones here and the run keeps them. Only the bounding
box moves, to cover the workspace in front of the rover rather than a box
centred on the origin. Coverage: **53.3%** of pixels.

Note the frame: this stage runs in the default `INSTRUMENT` frame and the log
reports `ROVER_NAV_FRAME`, while the XYZ stays in `SITE_FRAME`. That is exactly
the combination `marsrough` documents as its assumption.

### 4. Roughness (`marsrough`)

```bash
tig marsrough inp=pointcloud_filtered.xyz uvw=normals_arm.uvw out=roughness.img \
  x_center=2 y_center=0 box_radius=5 max_rough=0.05 bad_rough=0.1
```

Roughness is **metres**, not an index: for each pixel the program takes the
points in an inner disc and an outer annulus around the 3D point, and reports
`max(highest in disc, highest in annulus) - lowest in annulus`, measured
perpendicular to the local normal. The algorithm was written to decide whether
the MER RAT could be placed safely: the contact sensors rest on the low points
of the annulus, and the hardware clears only if the high points are within a
centimetre of them.

| Parameter | Default | Used here | Effect |
| --- | --- | --- | --- |
| `inner_radius` | 0.0355 m | default | Radius of the inner disc / inner edge of the annulus (MER RAT geometry). |
| `outer_radius` | 0.0405 m | default | Outer edge of the annulus. |
| `max_rough` | 0.015 m | 0.05 m | Values above this are clipped. The help warns the default "clips out a LOT of points"; 5 cm keeps the rocks distinguishable. |
| `bad_rough` | 0.1 | 0.1 | Value written where roughness could not be computed, and recorded in `INVALID_CONSTANT`. Must be ≥ `max_rough`. |
| `min_close` | 6 | default | Minimum points in the plane fit; should be ≥ `marsuvw`'s `min_points`. |
| `x_center`/`y_center`/`box_radius` | 1.0 / 0.0 / 1.2 m | 2 / 0 / 5 m | Square bounding box outside which nothing is computed — the default is the MER IDD workspace. |

Result: **52.3%** of pixels got a roughness value, median **5.6 mm**, 99th
percentile **35.6 mm**. The remainder is `0.1` (not computable), which is why
the PNG is white outside the near field. 0.3% of the valid pixels are slightly
negative, an artefact of the plane fit on near-degenerate point sets.

### 5-7. Instrument placement: tilt, roughness, goodness

```bash
tig marsitilt inp=pointcloud_filtered.xyz out=tilt_heli.img \
  uix_out=uix_heli.img zix_out=zix_heli.img -heli
tig marsirough inp=pointcloud_filtered.xyz out=iroughness_heli.img \
  uix=uix_heli.img zix=zix_heli.img -heli
tig marsigood inp=tilt_heli.img out=goodness_heli.img band=1 thresh=5
```

`marsitilt` places the chosen instrument (`-heli`, `-seis`, `-hp3`, `-wts`) at
every pixel, rotates it through the clock range and reports what tilt it would
end up with. Output is 3-band REAL: band 1 status (5 = within limits), band 2
minimum tilt, band 3 maximum tilt, in degrees. `tilt_threshold` defaults to 15°,
`clock_range` to (-15, 15)° in `clock_step` 3° steps, `sinkage` to 5 mm. It also
writes the two files `marsirough` consumes, and those are pure intermediates —
the instrument normal (`uix`) and the instrument Z level (`zix`) at each pixel.

On this scene 11.1% of pixels are placeable at all (the near field, inside the
helicopter foot window): 10.8% status 5, 0.3% status 3. Over those pixels the
minimum tilt is a median 0.23° and the maximum tilt a median 2.45°.

`marsigood` then collapses per-product status bands into one 0-5 goodness
raster: 5 where every input is good, `single_bad`/`multi_bad` values below that,
0 where there is no data or the pixel is outside the optional `mask`. With
`marsirough` unavailable (below) the script feeds it the tilt status alone, so
`goodness_heli.img` is 10.8% good / 0.3% marginal / 88.9% no-data — honest, but
tilt-only rather than tilt-and-roughness.

### Reachability (`marsgreach`)

`marsgreach` is the traversability/goodness product the README points at, but it
does **not** consume an XYZ cloud. Its input is a 6-band HALF reachability
image, one band per arm instrument (DRILL, GDRT, SHERLOC_WATSON, SHERLOC, PIXL,
FCS for M2020), each pixel packing eight 2-bit reachability values, one per arm
configuration. It selects bands (`bands=`, default all six), takes the worst
value across the selected instruments per arm configuration, then the best
across configurations, and maps 0/1/2/3 to goodness 0/1/3/5. With `-best_conf`
it adds a second band flagging which arm configurations achieved that best
value.

That 6-band product is written by the mission arm-reachability program
(M20REACH for Mars 2020), **which is not present in this image** — the image
ships `marsgreach` but no program that produces its input. So the script skips
this stage unless you supply a reachability product with `--reach FILE`:

```bash
./demo-surface-characteristics.sh --xyz pointcloud.xyz --reach data.reach
```

`marsgreach` does not validate its input: given a 3-band REAL product it still
reports `Processing band 1/2/3! Selected for processing.` and writes a goodness
image. Whatever you pass it, it will produce something, so do not point it at an
XYZ, tilt or slope product to "see what happens".

### 9. PNG conversion

`vicario` rescales on the actual minimum and maximum of the image. On a slope
map, where the extremes are the near-vertical values at the horizon, that makes
the terrain black. The script therefore stretches each product through a fixed
physical range first:

```bash
tig cform slope.img _stretch.img oform=byte irange=\(0,30\) orange=\(0,255\)
tig vicario _stretch.img slope.png
```

| PNG | Black | White |
| --- | --- | --- |
| `slope.png` | 0° | 30° |
| `heading.png` | -180° | 180° |
| `ntilt.png` | -30° | 30° |
| `solar.png` | 0 | 1 |
| `roughness.png` | 0 m | 0.05 m (white also = invalid) |
| `normals.png` | -1 | 1, as R/G/B = U/V/W |
| `tilt_heli.png`, `goodness_heli.png` | 0 | 5 |

Because the range is fixed, two runs are comparable by eye.

## How this relates to pathfinding and obstacle avoidance

- **Slope** bounds where the rover may drive at all: thresholding `slope.img` at
  whatever tilt limit the vehicle is flown to turns it into a keep-out mask, and
  `heading` says which way a slope falls, which matters for slip.
- **Northerly tilt** and **solar energy** are power and thermal constraints on a
  parking spot rather than geometric ones.
- **Roughness** catches what slope cannot: a field of small rocks can be
  perfectly level in the plane fit and still be untraversable or unworkable.
  Metres of peak-to-peak deviation is the number a wheel- or foot-clearance rule
  is written against.
- **Surface normals** are the shared input; both slope and roughness are
  functions of them, and the two scales matter — a rover-sized patch for
  driving decisions, an instrument-sized patch for placement.
- **Placement goodness** (`marsitilt` → `marsirough` → `marsigood`) and
  **reachability goodness** (`marsgreach`) answer the last question: given that
  the rover can get there, can the hardware actually be put down and reached.

The products are per-pixel rasters in the camera frame, registered to the XYZ
cloud, so they can be draped on the mesh from the [mesh demo](mesh-generation.md)
or projected into a map with `marsmap`.

## Troubleshooting

The output below is from the sol-650 run described above, on
`ghcr.io/nasa-ammos/tig/terrain-intelligence-generator:opensource`
(digest `sha256:58bae68a2c5c2f340186e40c43a98bd3bf448bb46d87e1a2872c4e4a9aea2a7c`).

**`marsirough` abends on the `zix` file `marsitilt` just wrote:**

```
Clock range: -15.000000,15.000000 deg,  Clock step: 3.000000 deg
[VIC2-GENERR] Exception in XVREAD, processing file: zix_heli.img
[VIC2-EOF] End of file
 Current line in image = 0
 ** ABEND called **
```

Reproduced with `-heli` and with `-seis`, with `-omp_on` and `-omp_off`, on a
1-band REAL `zix` file of the size `marsitilt` produced and `marsirough`
documents (passing a 3-band file instead is rejected earlier, with
`A single ZIX file must have 1 bands`). The read is attempted at line 0, before
any data line. The script treats this stage as optional: it reports the abend
and continues, and `marsigood` then runs on the tilt status band alone.

The invocation is not the problem. It is what `marsirough.pdf` documents
(`INP`, `OUT`, `UIX`, `ZIX`, `INST`) and the `uix`/`zix` files are the ones
`marsitilt.pdf` says to write — 3-band `UIX`, 1-band `ZIX`, confirmed with
`label -list`. The program then reads **band 2** of the file it has just
required to have one band, which is why the read ends at end-of-file on line 0:
under `gdb` the ZIX `zvread` is issued with `BAND 2`. In VICAR's
[`marsirough.cc`](https://github.com/NASA-AMMOS/VICAR/blob/master/vos/mars/src/prog/marsirough/marsirough.cc)
the ZIX arrays are `zix_unit[1]`/`zix_band[1]`, but `open_inputs()` fills
`unit[0..2]`/`band[0..2]` for every input it is given, so `band[1] = 2` and
`band[2] = 3` are written past the end of the one-element array. Sizing the ZIX
arrays like the others, or bounding `open_inputs()` by the band count it is
passed, is the fix — it is an upstream change, not one this repository can make.

`terrain-intelligence-generator/test-marsirough-abend.sh` is the filable
reproduction: it builds its own fixture in the image (no calibration, no
download), or takes a real product with `--xyz FILE`, and exits 0 while the
abend reproduces and 1 once it stops — so when a fixed image appears, CI says
so and this section becomes wrong on purpose.

**Every PNG is black except a fringe at the horizon:** the product was converted
with `vicario` alone. Stretch it through a physical range with `cform` first, as
in [PNG conversion](#9-png-conversion).

**The slope map is almost empty (a few percent of pixels):** `marsuvw -slope`
kept its default `error=0.0005`. Slope mode has no reject-and-refit loop, so a
half-metre patch of real terrain almost never fits a plane to 0.5 mm. Raise
`error` (0.02 here) and `separation`, and disable the bounding box with a large
`box_radius`.

**The roughness image is nearly all invalid:** either `box_radius`/`x_center`
still describe the MER IDD workspace (1.2 m around x=1.0) rather than your
scene, or `max_rough` is at its 0.015 default and clipping most of the valid
range.

**`marsgreach` runs but the result looks like noise:** it was given something
that is not a 6-band reachability product. See
[Reachability](#reachability-marsgreach).

**Calibration errors:** the camera model is read from the XYZ label, so these
programs need calibration even though they never touch an image. Verify with
`tig bash -c 'ls $MARS_CONFIG_PATH'`.

## Verified and unverified

Verified end to end on the Mars 2020 sol-650 NavCam pair
`N[LR]F_0650_0724654097_444EDR_N0320000NCAM08111_01_295J01.IMG` (PDS Imaging
Node), XYZ from `demo-mesh-generation-with-xyz.sh`, VISOR M20 calibration:
`marsuvw` (both scales), `marsslope` (`slope`, `heading`, `ntilt`, `solar`),
`marsrough`, `marsitilt -heli`, `marsigood`, and every `cform` + `vicario`
conversion. All statistics quoted above are measured from those outputs.

Not verified: `marsirough` (abends, above — diagnosed as an upstream defect,
reproduced with the synthetic fixture and with the VISOR sample MSL Navcam XYZ);
`marsgreach` on a real reachability product (no such product available — the
producing program is not in the image);
`marsslope type=direction` and `type=magnitude`; `--instrument seis|hp3|wts`
beyond confirming that `marsitilt -seis` runs and `marsirough -seis` abends the
same way; `--coord` values other than `site`.

## References

- [Mesh Generation Demo](mesh-generation.md) — produces the XYZ this demo consumes
- [Calibration Data](../reference/calibration-data.md)
- [Downloading VISOR Data](downloading-visor-data.md)
- Program documentation lives in the image: `$V2TOP/mars/lib/x86-64-linx/<program>.pdf`
  is the TAE parameter definition and help text, e.g.
  `tig bash -c 'cat $V2TOP/mars/lib/x86-64-linx/marsslope.pdf'`
