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
- `panorama_brtcorr.xml` — per-frame brightness corrections from `marsbrt`

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
fifth frame is at the same azimuth as the fourth but a different elevation;
frames at a second elevation tier extend the panorama vertically and do not
need to line up with the ring.

Where the ring does not close, the mosaic still builds; the gap is left at the
background DN. Elevations outside the frames' coverage are also background:
that is why a 360-degree cylindrical panorama from one elevation tier has a dark
band across the top, and why the polar projection has a hole where the nadir
would be.

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

## Brightness matching

Frames of a panorama are minutes apart with the camera pointed in different
directions, so radiometric correction alone leaves visible seams. The demo
measures them and corrects for them:

```bash
# Overlap statistics: a full mosaic pass, zoomed out, that writes statistics
# instead of caring about the picture
tig env HWLOC_COMPONENTS=-x86 marsmap inp=frames.txt out=overlap_mosaic.img \
  ovr_out=overlaps.xml -norad -nogrid zoom=0.25 projection=cylindrical \
  leftaz=0 rightaz=360

# Solve one gain per frame
tig env HWLOC_COMPONENTS=-x86 marsbrt inp=frames.txt in_ovr=overlaps.xml \
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

tig env HWLOC_COMPONENTS=-x86 marsmap inp=frames.txt out=overlap_mosaic.img \
  ovr_out=overlaps.xml -norad -nogrid zoom=0.25 leftaz=0 rightaz=360
tig env HWLOC_COMPONENTS=-x86 marsbrt inp=frames.txt in_ovr=overlaps.xml \
  out=brtcorr.xml out_solution_id=TIGDEMO do_what=DO_MULT
tig env HWLOC_COMPONENTS=-x86 marsmap inp=frames.txt out=panorama.img \
  brtcorr=brtcorr.xml -norad -nogrid projection=cylindrical leftaz=0 rightaz=360

tig stretch inp=panorama.img out=panorama_stretched.img -astretch percent=2 \
  dnmin=0 dnmax=255
tig vicario panorama_stretched.img panorama.png
```

`marsmap`, `marsbrt` and `marsmos` accept either a list of files or, as here, a
text file with one filename per line.

On the `tig env HWLOC_COMPONENTS=-x86` prefix, see
[marsmap dies immediately](#marsmap-dies-immediately-with-no-output) below.

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

The overlap pass found 177 overlapping regions and `marsbrt` solved gains of
1.232, 1.079, 0.903, 1.015 and 0.771 for the five frames.

## Troubleshooting

### marsmap dies immediately with no output

`marsmap` and `marsremos` are linked against MPI, and `MPI_Init` asks hwloc to
discover the machine topology before the program prints anything. The hwloc
bundled in the image divides by zero in its x86 backend on some host CPUs, so
the program dies with SIGFPE at startup — no message, no VICAR banner, exit code
136:

```
Floating point exception (core dumped)
```

A backtrace on an affected host puts the fault in
`look_proc -> hwloc_look_x86 -> hwloc_x86_discover -> hwloc_topology_load ->
MPIR_Init_thread -> PMPI_Init`. Disabling that one backend makes hwloc read the
topology from Linux sysfs instead, which is all a single-node run needs:

```bash
tig env HWLOC_COMPONENTS=-x86 marsmap ...
```

`demo-panorama-mosaic.sh` always sets it; it is harmless where `marsmap` already
works. Other MARS programs are unaffected — they do not call `MPI_Init`.

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
