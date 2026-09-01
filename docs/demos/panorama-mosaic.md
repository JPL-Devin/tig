# In-Situ Panorama Mosaic Demo

Builds a 360-degree panorama from Mars 2020 NavCam frames with the VICAR MARS
mosaic tools, driven by [`tig`](../../tig-cli/README.md).

`demo-panorama-mosaic.sh` takes surface camera frames from one rover position
and projects them into a single cylindrical, polar or vertical mosaic.

## Scope

These are **in-situ** mosaics. `marsmap` works in spherical coordinates about
the rover: every output pixel is an azimuth and an elevation measured from a
projection origin near the camera, and the frames are placed using the pointing
in their labels. That is what makes a 360-degree panorama possible, and it is
also the limit of this demo.

**Orbital cartographic reprojection is out of scope.** VICAR's map-projection
programs (`map3`, `maptran`, `mapcoord`) reproject orbital imagery onto a
planetary body's coordinate system; that is a different coordinate system, a
different set of programs, and not what runs here. Nothing in this document or
in `demo-panorama-mosaic.sh` demonstrates them.

## What it does

1. **Radiometric correction** (`marsrad`) — flat field and responsivity per frame
2. **Overlap statistics** (`marsmap ovr_out=`) — measures the brightness of each
   pair of frames where they overlap in the mosaic
3. **Brightness matching** (`marsbrt`) — solves a per-frame correction from
   those statistics
4. **Mosaic** (`marsmap`) — projects and blends the frames into one image
5. **PNG** (`stretch` + `vicario`) — display stretch and format conversion

On an 8-core VM, five 1280x960 NavCam frames take about 8 seconds end to end.

## Prerequisites

- Docker Engine 20.10+
- `pip install tig-cli`
- M2020 calibration files (see
  [Calibration Data](../reference/calibration-data.md)); the script locates them
  with `find-calibration.sh`. Calibration is needed twice over: `marsrad` reads
  flat fields and responsivity from it, and `marsmap` needs the camera model to
  know where each frame points. There is no useful fallback — without it the
  demo stops at step 1.
- Frames from one site/drive with overlapping azimuth coverage (see below)

## Usage

```bash
./demo-panorama-mosaic.sh /path/to/NLF_0300_*NCAM00501*.IMG
```

**Output** (in `workspace/`):

- `panorama.img` — the mosaic, VICAR format, with the projection recorded in its
  label
- `panorama.png` — the same mosaic, display-stretched, 8-bit
- `panorama_rad/` — the radiometrically corrected frames
- `panorama_overlaps.xml` — overlap statistics from `marsmap`
- `panorama_overlap_mosaic.img` — the zoomed-out mosaic the overlap pass had to
  build to measure them; of no further use
- `panorama_brtcorr.xml` — per-frame brightness corrections from `marsbrt`
- `panorama_stretched.img` — the display-stretched copy the PNG was written
  from, unless `--no-stretch`
- `panorama_bbox.csv` — with `--bbox`, one CSV row per frame footprint: the
  filename and a WKT polygon in mosaic (sample line) coordinates. This is
  `marsmap`'s `BBOX` output, and it is how the wrap and pole handling below was
  checked.

## Choosing frames

The frames of a mosaic must come from **one rover position**. Azimuth and
elevation are measured about the site, so a frame taken after a drive points at
the world from somewhere else and lands in the wrong place. The site/drive is in
the product name: in
`NLF_0300_0693569889_022FDR_N0090000NCAM00501_0A0295J01.IMG`, `N0090000` is the
site/drive counter and `NCAM00501` is the sequence. Frames of one panorama share
both.

To wrap in azimuth you need the ring to close. A NavCam frame is 1280 samples
wide, and the cylindrical mosaic below came out at 12.9 pixels/degree, so one
frame spans just under 100 degrees of azimuth. The `NCAM00501` sequence is four
frames stepped about 90 degrees apart — the mast azimuths in the sol 300 labels
below are 2.0, 91.5, 181.8 and 271.8 degrees — which closes the circle with
overlap at every seam, including between the last frame and the first. Its
fifth frame is within a few degrees of the fourth in azimuth but at a
different elevation;
frames at a second elevation tier extend the panorama vertically and do not
need to line up with the ring.

Where the ring does not close, the mosaic still builds; the gap is left at the
background DN. Elevations outside the frames' coverage are also background:
that is why a 360-degree cylindrical panorama from one elevation tier has a dark
band across the top, and why the polar projection has a hole in the middle unless
the frames actually look at the nadir — a sequence that does is used below.

## Projections

`marsmap`'s `PROJECTION` parameter selects the output geometry. The script
exposes three of the four; each has its own extent parameters, and passing an
extent that belongs to another projection has no effect.

### Cylindrical (default)

Lines of constant elevation, columns of constant azimuth, an equal scale in
pixels/degree everywhere. This is the projection for a 360-degree panorama: it
is the only one where the full azimuth circle maps to a finite image without a
singularity, and the one whose left and right edges join.

Extent: `--left-az`, `--right-az` (azimuth of the left and right edges),
`--top-el`, `--bottom-el` (elevation of the top and bottom edges), all in
degrees.

The script defaults to `--left-az 0 --right-az 360`, i.e. a full circle, and
leaves the elevation limits to be fitted to the frames. `marsmap`'s own help
asks for the azimuth limits to be given by hand near 360 degrees rather than
fitted — a fitted extent of a nearly closed ring is ambiguous about which way
round the gap goes. `--auto-extent` restores the fitted behaviour.

### Polar

Radial lines of constant azimuth from the centre of the image, nadir at the
centre, zero azimuth up by default. The scale is constant only radially. Use it
to look at the ground around the rover, or at the layout of a workspace, where a
cylindrical panorama's horizontal stretch near the bottom gets in the way.

Extent: `--top-el` (elevation of the outer edge) and `--up-az` (azimuth placed
at the top of the image). `--left-az`/`--right-az`/`--bottom-el` do not apply.

`--up-az` rotates the mosaic. Run on the sol 10 nadir set with `--top-el -50`,
once as `--projection polar` and once with `--up-az 180` added, the two outputs
are both 1033 x 1033 x 3 — the parameter changes no dimension and clips nothing —
and the second is the first turned through 180 degrees, so the rover hardware
that sat at the top of the default mosaic sits at the bottom. The label records
which way is up:

```
# default                    # --up-az 180
MAP_PROJECTION_TYPE='POLAR'  MAP_PROJECTION_TYPE='POLAR'
REFERENCE_AZIMUTH=0.0        REFERENCE_AZIMUTH=180.0
TOPEL=-50.0                  TOPEL=-50.0
                             UP_AZ=180.0
```

Without `--up-az` there is no `UP_AZ` keyword and `REFERENCE_AZIMUTH` is 0, i.e.
north up. `marsmap`'s help says `UP_AZ` is polar-only; that it is ignored by the
other projections was not tested here.

### Vertical

An overhead view, assuming the site is a plane: north up, east right, an equal
X/Y scale in metres. Useful as a rough map of the terrain immediately around the
rover. It is a projection of the imagery onto an assumed plane, not a
photogrammetric product — nothing here uses range data, so anything with height
leans outward from the rover.

Extent: `--min-x`, `--max-x`, `--min-y`, `--max-y` in metres and `--vert-scale`
in metres per pixel. X runs south to north and gives the picture height; Y runs
west to east and gives its width. Note that vertical
projections take their scale from `VERT_SCALE`, not `SCALE`; `--zoom` still
applies on top.

### Sinusoidal

`marsmap` also implements a `SINUSOIDAL` projection. Its own documentation
describes it as not fully implemented or tested, so the script does not offer
it.

## Wrap-around and the zenith/nadir

Two behaviours matter for a full-circle panorama, both handled inside `marsmap`
and both documented in its help:

- **Azimuth wrap-around.** In the cylindrical (and cylindrical-perspective and
  sinusoidal) projections a frame straddling the mosaic's left edge continues on
  the right, rather than being clipped. That is what lets a frame sit across the
  seam of a 0–360 mosaic. `--wrap-az` sets the azimuth at which a complete
  mosaic is cut. It only means anything for a full 360-degree mosaic —
  otherwise the cut goes where the data is missing — and only where the extent
  is fitted: with `--auto-extent --wrap-az 180`, `marsmap` reports `azimuth of
  first sample = 180`, whereas explicit `--left-az`/`--right-az` already pin
  the edges.
- **Zenith/nadir polygons.** In the same projections, a frame whose footprint
  contains the zenith or the nadir does not close as a normal polygon — near the
  pole a bounded patch of sky maps to the whole width of the mosaic. `marsmap`
  adds three points to the footprint, running vertically up or down to the edge
  of the mosaic, so the region closes correctly. Nothing needs to be passed for
  this.

### Pole-reaching sequences, and what was observed

Most sequences never reach a pole, so this path stays dormant. Two M2020 NavCam
sets that do reach one, both used below:

- **Nadir.** Sol 10, sequence `NCAM00130`, site/drive `N0030000`: six left-NavCam
  FDR products with mast elevation about -89.5 degrees, i.e. pointed straight
  down at the deck and the ground under the rover. With a 73.1-degree elevation
  field of view, the nadir is inside every footprint.
- **Zenith.** The fifth frame of the sol 300 `NCAM00501` ring used elsewhere in
  this document points at elevation +80.2 degrees, so its footprint crosses the
  zenith while the other four stay near +25.

Run with `--bbox` and the footprint polygons show both behaviours directly. On
the nadir set with the default extent (6 frames, 11 polygon rows, mosaic 4646 x
334) five of the six frames produced **two** rows — the wrapped pair — and each
row ends with a run of points pinned to the bottom line of the mosaic:

```
000_NLF_0010_0667835038_916FDR_...rad.img,"POLYGON((2207 -14,...,4585 59,4585 334,-61 334,-61 59,255 279,...))"
000_NLF_0010_0667835038_916FDR_...rad.img,"POLYGON((6853 -14,...,9231 59,9231 334,4585 334,4585 59,4901 279,...))"
```

`334` is the last line of that mosaic: those are the extra points closing the
polygon down to the mosaic edge, and the two rows are the same polygon translated
by the full 4646-sample width. The zenith frame in the sol 300 set behaves the
same way at the top edge (`line 1`), again in two rows:

```
004_NLF_0300_0693570083_989FDR_...rad.img,"POLYGON((2580 342,...,364 556,9483 683,9483 1,4837 1,4837 683,4613 481,...))"
```

One caveat from the same run: the sixth nadir frame (mast azimuth 130 degrees)
came out as a **single** row whose sample coordinates jump across the wrap point
(`...,4508 184,89 60,405 278,...`) with no pole-closing points, where the other
five were split and closed. Why that frame is treated differently was not
established; if you consume `panorama_bbox.csv`, do not assume every
pole-crossing frame yields the split-and-closed pair.

**The fitted extent does not include the pole.** `TOPEL`/`BOTTOMEL` default to
the extreme elevation of a corner or edge centre of the inputs, and for a frame
looking straight down the extreme point is in the middle of the frame, not on its
edge. The nadir set therefore fitted to `MINIMUM_ELEVATION=-54.8968` — the nadir
is in the footprints, in the bbox polygons, and off the picture. Ask for it:

```bash
# Nadir in the picture: bottom edge at -90
./demo-panorama-mosaic.sh --bbox --bottom-el -90 --top-el -30 --grid \
  /path/to/sol010/NLF_0010_*_0A0295J0[34].IMG

# Zenith in the picture: top edge at +90
./demo-panorama-mosaic.sh --bbox --top-el 90 --bottom-el 0 --grid \
  /path/to/sol300/NLF_0300_*NCAM00501*.IMG
```

Those two runs gave 4646 x 774 and 4646 x 1161 mosaics. In the first, the last
line of the PNG (elevation -90) has no background pixels at all across its 4646
samples: the nadir is a single view direction smeared across the full width, and
the bottom of the image is that smear. In the second, the top line (elevation
+90) is filled except for 12 background samples (a 24-sample gap once the
grid-shaded edge pixels are counted), entirely from the one frame that sees the
zenith. That is the picture-space counterpart of the closing points above.

The same nadir set in the polar projection has no hole in the middle:

```bash
./demo-panorama-mosaic.sh --projection polar --top-el -50 \
  /path/to/sol010/NLF_0010_*_0A0295J0[34].IMG   # 1033 x 1033 x 3
```

## Brightness matching

Frames of a panorama are minutes apart with the camera pointed in different
directions, so radiometric correction alone leaves visible seams. The demo
measures them and corrects for them:

```bash
# Overlap statistics: a full mosaic pass, zoomed out, that writes statistics
# instead of caring about the picture
tig marsmap inp=frames.txt out=overlap_mosaic.img \
  ovr_out=overlaps.xml -norad -nogrid zoom=0.25 projection=cylindrical \
  leftaz=0 rightaz=360

# Solve one gain per frame
tig marsbrt inp=frames.txt in_ovr=overlaps.xml \
  out=brtcorr.xml out_solution_id=TIGDEMO do_what=DO_MULT
```

The overlap pass must use the same projection as the mosaic, or its statistics
describe pixels other than the ones being corrected.

`DO_MULT` solves a multiplicative gain per frame. `marsbrt`'s default,
`DO_LINEAR`, also solves an additive offset. On a five-frame ring that extra
freedom is only sometimes well conditioned: on the sol 300 set below it
converged fine, but on a sol 170 sequence with the sun in frame it returned
gains from 0.004 to 4.98 with offsets of order 10^5, and a final error metric an
order of magnitude worse than the gain-only solution. The demo therefore uses
`DO_MULT`; `DO_LINEAR` is worth trying by hand on a set with dense overlap.

`--no-brightness-match` skips both steps and mosaics the radiometrically
corrected frames directly, which is the honest comparison for judging whether
the matching helped.

## Running the steps by hand

```bash
export MARS_CONFIG_PATH=~/mars_calibration_m20
cd workspace

tig marsrad inp=NLF_0300_0693569889_022FDR_N0090000NCAM00501_0A0295J01.IMG \
  out=frame1.rad.img
# ... one per frame, then list them
ls *.rad.img > frames.txt

tig marsmap inp=frames.txt out=overlap_mosaic.img \
  ovr_out=overlaps.xml -norad -nogrid zoom=0.25 leftaz=0 rightaz=360
tig marsbrt inp=frames.txt in_ovr=overlaps.xml \
  out=brtcorr.xml out_solution_id=TIGDEMO do_what=DO_MULT
tig marsmap inp=frames.txt out=panorama.img \
  brtcorr=brtcorr.xml -norad -nogrid projection=cylindrical leftaz=0 rightaz=360

tig stretch inp=panorama.img out=panorama_stretched.img -astretch percent=2 \
  dnmin=0 dnmax=255
tig vicario panorama_stretched.img panorama.png
```

`marsmap`, `marsbrt` and `marsmos` accept either a list of files or, as here, a
text file with one filename per line.

## What the mosaic label carries

```bash
tig label -list inp=panorama.img
```

`marsmap` records the projection in a `SURFACE_PROJECTION_PARMS` property, so a
consumer can turn a pixel back into a view ray. For the sol 300 panorama below:

```
MAP_PROJECTION_TYPE='CYLINDRICAL'
MAP_RESOLUTION=(12.9059, 12.9059)      # pixels/degree
MAXIMUM_ELEVATION=69.9294
MINIMUM_ELEVATION=-21.673
START_AZIMUTH=0.0
STOP_AZIMUTH=360.0
ZERO_ELEVATION_LINE=903.503
REFERENCE_COORD_SYSTEM_NAME='SITE_FRAME'
```

The label also lists every source product in `SOURCE_PRODUCT_ID`.

## `marsmos`: the perspective alternative

`marsmos` also assembles frames into a mosaic, but under an **output camera
model** derived from the inputs: the result looks like a photograph from a
camera with a much wider field of view, not a map projection. It takes the same
`brtcorr` file, and `AZOUT`/`ELOUT`/`TWIST` point its synthetic camera:

```bash
tig marsmos inp=frames.txt out=mosaic_persp.img brtcorr=brtcorr.xml -norad
```

Use it when the output should stay perspective — a wide-angle view of a
workspace, say. Use `marsmap` when the output needs a defined azimuth/elevation
grid, and for anything approaching 360 degrees: a perspective camera cannot see
a full circle.

## Worked example

The demo was run on Mars 2020 sol 300, sequence `NCAM00501`, site/drive
`N0090000` — the five left-NavCam FDR products at
`https://planetarydata.jpl.nasa.gov/img/data/mars2020/mars2020_navcam_ops_calibrated/data/sol/00300/ids/fdr/ncam/`:

```
NLF_0300_0693569889_022FDR_N0090000NCAM00501_0A0295J01.IMG
NLF_0300_0693569929_286FDR_N0090000NCAM00501_0A0295J01.IMG
NLF_0300_0693569973_908FDR_N0090000NCAM00501_0A0295J01.IMG
NLF_0300_0693570021_381FDR_N0090000NCAM00501_0A0295J01.IMG
NLF_0300_0693570083_989FDR_N0090000NCAM00501_0A0295J01.IMG
```

```bash
./demo-panorama-mosaic.sh /path/to/sol300/NLF_0300_*NCAM00501*.IMG
```

What came out, on this machine:

| Projection | Command | Output size |
|---|---|---|
| Cylindrical, 0–360 | (default) | 4646 x 1182 x 3, 12.906 px/degree |
| Polar | `--projection polar --top-el 30` | 3097 x 3097 x 3 |
| Vertical | `--projection vertical --min-x -15 --max-x 15 --min-y -15 --max-y 15 --vert-scale 0.03` | 1000 x 1000 x 3 |

The overlap pass found about 177 overlapping regions — repeated runs on the same
frames have also reported 176, with no effect on the solved gains or the mosaic
— and `marsbrt` solved gains of 1.232, 1.079, 0.903, 1.015 and 0.771 for the
five frames.

## A second instrument: Mastcam-Z

Everything above is NavCam. The demo was also run unchanged on **Mastcam-Z**
(`MCZ_LEFT`), sol 15, sequence `ZCAM07000`, site/drive `N0030376` — 24 `ZLF_*FDR`
products from
`https://planetarydata.jpl.nasa.gov/img/data/mars2020/mars2020_mastcamz_ops_calibrated/data/sol/00015/ids/fdr/zcam/`:

```bash
./demo-panorama-mosaic.sh --auto-extent --bbox --zoom 0.5 \
  /path/to/sol015_zcam/ZLF_0015_*ZCAM07000_110085J01.IMG
```

No script change and no calibration change were needed: the M2020 VISOR
calibration covers `MCZ_LEFT`/`MCZ_RIGHT` as well as the NavCams, and `marsrad`,
`marsmap` and `marsbrt` accepted the frames as they came. Twenty-four frames took
27 seconds at full scale on an 8-core VM. What is genuinely different:

- **Frame size and camera model.** 1648 x 1200 x 3 per frame against NavCam's
  1280 x 960 x 3, and `MODEL_TYPE=CAHVOR` where the NavCam and Hazcam labels say
  `CAHVORE`. Pig picks the model from the label; nothing has to be told which.
- **Narrow field of view.** These frames are 6.36 x 4.63 degrees (the 110 mm zoom
  position), so a mosaic needs many of them and comes out large: 24 frames
  spanning 61 degrees of azimuth produced a 15869 x 2163 mosaic at the camera's
  natural scale. `--zoom 0.5` halves that. NavCam's 96-degree frames make this
  easy to forget.
- **The label azimuth is not the footprint azimuth.** `INSTRUMENT_AZIMUTH` in
  these labels runs 301.7 to 355.7 degrees, but the mosaic `marsmap` fits to the
  footprints starts at `START_AZIMUTH=325.975` and stops at `STOP_AZIMUTH=26.072`
  — about 24 degrees away. Choosing `--left-az`/`--right-az` from the labels
  (`--left-az 298 --right-az 359`) left the left third of the mosaic empty. For a
  narrow-field instrument, use `--auto-extent` and read the azimuth range back out
  of the label rather than deriving it from the frame labels.
- **Wrap for free.** Because the fitted extent runs 326 -> 26 degrees, the mosaic
  straddles 0/360: the bbox file held 25 polygons for 24 frames, the extra row
  being the wrapped copy of the one frame sitting on the seam.
- **Two FDR variants per frame.** The PDS directory lists both
  `ZLF_..._110085J01.IMG` (full 1648 x 1200) and `ZLF_..._1100LUJ02.IMG`, which is
  a 206 x 150 thumbnail of the same acquisition. A careless glob mixes them.
- **Colour.** These are `FILTER_NAME='ZCAM_L0_RGB'`, `FILTER_NUMBER=0`, three
  bands — as are the M2020 NavCam FDRs, so `marsmap` had three bands in and three
  bands out in both cases and nothing special was needed. Its documented handling
  of mixed band counts, and of the narrowband Mastcam-Z filters (`ZL1`..`ZL6`,
  which are single-band and would need `BAND`/filter bookkeeping), was not
  exercised.

## What is verified, and what is not

Run end to end on this machine, on the real products named above, with the
published image:

- Cylindrical, polar and vertical projections on the sol 300 NavCam ring
- Nadir coverage: sol 10 `NCAM00130`, cylindrical (fitted and `--bottom-el -90`)
  and polar; pole-closing points and wrapped polygon pairs read out of
  `panorama_bbox.csv`, and the pole line of the PNG checked for background pixels
- Zenith coverage: the +80-degree frame of sol 300 `NCAM00501`, cylindrical with
  `--top-el 90`
- `--up-az 180` against the default polar orientation, image and label
- A second instrument, Mastcam-Z `MCZ_LEFT`, 24 frames, with the gotchas above

Not exercised, and therefore not described as working:

- **Hazcam.** Products exist (e.g. sol 300 `FHAZ00206`, `FRONT_HAZCAM_LEFT_A`,
  `CAHVORE`, 102-degree elevation field of view) and the M2020 calibration covers
  them, but a sol with front *and* rear Hazcam frames from one site/drive was not
  hunted down and none of it was run, so nothing here says how the fisheye models
  mosaic.
- **MSL and MER.** The MSL calibration bundle and MSL NavCam samples are
  available, but no MSL or MER frames were mosaicked; the demo's mission handling
  beyond M2020 is untested.
- **Sinusoidal projection.** `marsmap` calls it not fully implemented or tested;
  the script does not offer it and it was not run.
- **`marsnav` navigation tables** (`navtable=`), which `marsmap` accepts to
  improve pointing.
- **Mixed colour and monochrome inputs**, and the narrowband Mastcam-Z filters.
- **The one nadir frame that produced a single unclosed bbox row** (above): the
  behaviour is recorded, its cause is not.
- **Orbital cartographic reprojection**, which is out of scope by construction.

## Troubleshooting

### marsmap dies immediately with no output

Fixed in the image; only images built before that fix landed behave this way.

`marsmap`, `marsremos`, `marscor2` and `marsint` are linked against MPI, and
`MPI_Init` asks hwloc to discover the machine topology before the program prints
anything. The hwloc bundled in the image divides by zero in its x86 backend on
some host CPUs, so the program dies with SIGFPE at startup — no message, no
VICAR banner, exit code 136:

```
Floating point exception (core dumped)
```

A backtrace on an affected host puts the fault in
`look_proc -> hwloc_look_x86 -> hwloc_x86_discover -> hwloc_topology_load ->
MPIR_Init_thread -> PMPI_Init`. The image now sets `HWLOC_COMPONENTS=-x86`,
which makes hwloc read the topology from Linux sysfs instead — all a single-node
run needs. On an older image, set it per invocation:

```bash
tig env HWLOC_COMPONENTS=-x86 marsmap ...
```

Other MARS programs are unaffected — they do not call `MPI_Init`.

### The PNG is nearly black

Expected without a stretch: the mosaic holds scaled radiance and a Mars scene
occupies a small part of that range, so a straight conversion is very dark. The
script runs `stretch -astretch percent=2` before `vicario` and leaves
`panorama.img` photometrically intact. `--no-stretch` writes the PNG straight
from the mosaic.

### Frames land in the wrong place, or the mosaic is empty

- Check that every frame shares one site/drive counter (`N0090000` above). A
  drive between frames invalidates the whole mosaic, not just the frames after
  it.
- Check that the calibration has a camera model for the instrument:
  `tig bash -c 'ls $MARS_CONFIG_PATH/camera_models'`. `marsbrt` prints the model
  file it used for each frame.

### Visible seams

- Confirm `panorama_brtcorr.xml` exists and that the gains printed in step 3 are
  near 1. A gain far from 1 usually means that frame barely overlaps the others,
  so its correction is poorly constrained.
- Compare against `--no-brightness-match` to see what the matching actually did.
- Large brightness differences across a panorama can be real: sky brightness
  varies strongly with the angle from the sun, and a single gain per frame
  cannot follow that.

### A stage produced no output

MARS tools frequently exit non-zero even when they succeed, so the script checks
for the file rather than the exit status. `tig --status` confirms the container
and its mounts.

## Data sources

**M2020 NavCam frames:**

- PDS Imaging archive:
  `https://planetarydata.jpl.nasa.gov/img/data/mars2020/mars2020_navcam_ops_calibrated/data/sol/<sol>/ids/fdr/ncam/`
- PDS Geosciences Node: https://pds-geosciences.wustl.edu/missions/mars2020/
- VISOR layer index: https://mars.nasa.gov/mmgis-maps/M20/Layers/json/

Look for `NLF_*FDR_*` (left NavCam, Full Data Record). The `NCAM00501` sequence
is a five-frame 360-degree NavCam ring and is a good starting point on any sol
that has one.

**Calibration:** see [Downloading VISOR Data](downloading-visor-data.md).

## References

- [Mesh Generation Demo](mesh-generation.md) — the stereo/terrain pipeline
- [Calibration Data](../reference/calibration-data.md)
- [MARS Tools Overview](../architecture/components.md)
- [tig-cli README](../../tig-cli/README.md)
