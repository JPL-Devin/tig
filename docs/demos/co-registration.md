# Co-registration and Change Monitoring

Bringing overlapping frames into a common geometry with the MARS tools, driven by
[`tig`](../../tig-cli/README.md), and what it takes to build change monitoring on
top of that.

`demo-co-registration.sh` runs the sequence: overlap check → tiepoints → pointing
solution.

> **There is no change-detection program in this image.** Nothing in the ~74
> `mars*` programs takes two epochs and returns a difference product. Change
> monitoring is a workflow you compose: acquire repeat coverage, co-register it
> with the programs below, then difference or measure the registered products
> with general VICAR arithmetic (`f2`, `difpic`, `hist`). The
> [Change monitoring](#change-monitoring) section says what that costs you.

## What co-registration means here

MARS does not resample one image onto another. It corrects **pointing**: where
each camera was looking. The chain is

1. **Tiepoints** — matched features between overlapping frames
   (`marsautotie`, `marsautotie2`, `marstie`, `marsfidfinder`).
2. **Pointing solution** — adjust each frame's azimuth/elevation so the tiepoints
   agree (`marsnav`, or the bundle adjustment `marsnav2`). Output is a *navigation
   table*, an XML file of corrected pointing parameters. The input images are not
   modified.
3. **Consumption** — the mosaic and projection programs (`marsmos`, `marsmap`,
   `marsortho`) take that table as `navtable=` and render with the corrected
   pointing. `marsrelabel` can write pointing back into a product label.

Ordering is fixed, and the programs say so: `marsnav.pdf` states "MARSTIE must be
run first to gather tiepoints." `marsnav` will not invent tiepoints, and
`marsautotie` will not solve pointing.

Every parameter used below comes from the TAE parameter-definition files inside
the image (`$V2TOP/mars/lib/x86-64-linx/<prog>.pdf`), which are the authoritative
reference — the HTML docs are not in the image.

## Prerequisites

- Docker Engine 20.10+ and `pip install tig-cli`
- Mission calibration on `MARS_CONFIG_PATH` (see
  [Calibration Data](../reference/calibration-data.md) and
  [Downloading VISOR Data](downloading-visor-data.md)); the script locates it with
  `find-calibration.sh`
- Two or more frames **from the same site**, with real overlap and initial
  pointing good enough for the matcher to find that overlap. Up to 200 frames
  (`marsautotie`/`marsnav` `COUNT=(1:200)`).

## Running it

```bash
export MARS_CONFIG_PATH=/path/to/calibration/msl
./demo-co-registration.sh --list frames.lis --nav2
```

Outputs land in `workspace-coreg/`: `overlap_*.lis`, `tiepoints.tpt`,
`tiepoints_kept.tpt`, `pointing.nav`, and a full log per program
(`marschkovl.log`, `marsautotie.log`, `marsnav.log`) — the script prints the
headline numbers and reports the log tail if a program abends.

## The sequence, stage by stage

### 1. Which frames overlap — `marschkovl`

```bash
tig marschkovl inp=frames.lis out=\( overlap_left.lis overlap_right.lis \) \
  overlap=40
```

`marschkovl` projects each pair onto a surface model (`SURFACE=INFINITY|PLANE|
SPHERE1|SPHERE2|MESH`, default `PLANE`) and writes the pairs whose overlap exceeds
`OVERLAP` percent into two parallel lists. It prints the measured percentage for
every pair it evaluates, including the ones it rejects, which is the useful part
when a set produces no tiepoints. The default threshold is 70.

### 2. Tiepoints — `marsautotie`

```bash
tig marsautotie inp=frames.lis out=tiepoints.tpt density=50 grid_spacing=15
```

`marsautotie` correlates a grid of candidate points across each overlapping pair.
The parameters that matter:

| Parameter | Default | Effect |
|-----------|---------|--------|
| `density` | 250 | Minimum spacing, in pixels, between accepted tiepoints. **Lower gives more tiepoints.** |
| `grid_spacing` | 15 | Spacing of candidate points. The program aborts if the grid exceeds 300 points per axis — raise this for large frames. |
| `quality` | 0.93 | Correlation quality floor. |
| `search` | 199 | Search radius, in pixels. Must cover the initial pointing error. |
| `busy` | 270.0 | Rejects featureless (low-variance) areas. |
| `surface` | — | Surface model used to predict where a point lands in the other frame. |
| `navtable` | — | Start from an existing pointing solution rather than the labels. |

`marsautotie2` is the same job by a different method: ASIFT keypoint matching
rather than correlation, and its `.pdf` notes it is patent-encumbered (SIFT and
ASIFT) and camera-specific in its tuning. `marstie` gathers tiepoints manually or
automatically and accepts `in_tpt=` for refining an existing set.
`marsfidfinder` finds projected fiducial points instead of image features. All
four write the same tiepoint file for `marsnav`.

### 3. Pointing solution — `marsnav`

```bash
tig marsnav inp=frames.lis out=pointing.nav in_tpt=tiepoints.tpt \
  out_tpt=tiepoints_kept.tpt out_solution_id=COREG \
  -remove max_residual=10 max_remove=50
```

`marsnav` adjusts the pointing of each frame to minimise tiepoint disagreement,
prints the mean pixel error before and after, and writes the corrected pointing to
`out=` as an XML navigation table tagged with `OUT_SOLUTION_ID`. `-remove` with
`max_residual` (pixels) and `max_remove` (count) drops the worst tiepoints and
re-solves; `recycle` (default 10) bounds the iterations.

`marsnav2` solves the same problem as a proper bundle adjustment (Ceres): it
adjusts pointing and triangulated ground points together, prints a residual
histogram before and after, lists the largest individual residuals, and reports
**disconnected groups of images** — the single most useful diagnostic when a
solution goes wrong. Its residual statistic is computed against adjusted ground
points, so it is not comparable to `marsnav`'s number.

### 4. Verifying the result

- **Residuals.** `marsnav` prints `Commanded mean pixel error` (before) and
  `Final solution mean pixel error` (after). After should be much smaller than
  before, and of order a pixel for a well-connected set.
- **Coverage.** `marsnav` prints `Input image N not represented by tiepoints` for
  frames the matcher never tied in. Those frames are unconstrained and their
  "correction" is meaningless — see the worked example below, where an untied
  frame was handed a 3165° elevation change.
- **Connectivity.** `marsnav2` prints the disconnected groups explicitly. Frames
  in different groups are not co-registered to each other, however good the mean
  residual looks.
- **Kept tiepoints.** Compare `out_tpt` to the input tiepoint file. Losing most
  of them to outlier removal means the matcher, not the solver, is the problem.
- **Camera model sanity.** `marscheckcm inp=frame.img tol=0.001` compares the
  camera model in a product's label against the one derived by applying the
  kinematics to the calibration model, and prints `OK`/`FAIL` per vector
  component.

### 5. Using the corrected pointing

The navigation table is an input, not a rewritten image:

```bash
tig marsmos inp=frames.lis out=mosaic.img navtable=pointing.nav
tig marsmap inp=frames.lis out=mosaic.img \
  navtable=pointing.nav projection=CYLINDRICAL
```

`marsmos` mosaics under a synthetic wider-field camera model derived from the
inputs; `marsmap` mosaics into a cylindrical, polar or vertical projection;
`marsortho` produces orthographic mosaics and DEMs from XYZ data. `marsrelabel`
copies a product with an updated label (`-cm` camera model, `-pm` pointing model,
`navtable=`) — its `.pdf` states the image data is unchanged and only the label is
rewritten.

## Change monitoring

There is no change-detection command. What the toolset gives you is the
registration half; the differencing half is general VICAR:

1. **Repeat coverage.** Two or more epochs of the same scene from a similar
   viewpoint. This is a data-acquisition problem, not a software one.
2. **Overlap check.** `marschkovl` across the epochs, so you difference frames
   that actually see the same ground.
3. **Cross-epoch tiepoints.** `marsautotie`/`marsautotie2` over the combined
   frame list, so tiepoints tie *across* epochs and not only within one.
4. **One joint solution.** A single `marsnav`/`marsnav2` run over all epochs
   produces one navigation table in which every frame shares a geometry. Solving
   each epoch separately does not co-register the epochs to each other.
5. **Render each epoch with the same table.** Run `marsmos` or `marsmap` once per
   epoch with the same `navtable=` and identical projection parameters, so the two
   products are pixel-comparable.
6. **Difference.** `f2 inp=(a,b) out=diff.img func="in1-in2"`, `difpic` for a
   changed-pixel count, `hist` for statistics.

Two constraints that this workflow does not solve for you, and neither does any
program in the image:

- **Radiometry.** Registration does not make two epochs photometrically
  comparable. In the M20 pair below, taken ten minutes apart, mean DN differed by
  a factor of three; a raw difference is dominated by illumination and exposure,
  not by surface change. Normalise (`marsmap`/`marsmos` `BRTCORR`, or your own
  radiometric step) before attributing a difference to the surface.
- **Parallax.** Pointing correction is angular. Frames taken from different rover
  positions will not register with a pointing solution alone; that needs the
  stereo/XYZ path (see [Mesh Generation](mesh-generation.md)) and an ortho product
  from `marsortho`. `marsautoloco` handles the related but distinct problem of
  tying an orthomosaic's map coordinates to a base map.

The stereo comparison helpers named alongside these programs are not change
detectors either: `marsdispcompare` cross-checks L→R against R→L disparity to
build a rejection mask, `marserrdisp` computes disparity error images, and
`marserror` propagates those into range/XYZ error volumes. They quantify stereo
uncertainty within one acquisition.

## Worked example — MSL NavCam, verified

Run against `ghcr.io/nasa-ammos/tig/terrain-intelligence-generator:opensource`
(digest `sha256:58bae68a2c5c2f340186e40c43a98bd3bf448bb46d87e1a2872c4e4a9aea2a7c`),
tig-cli 0.1.0, with the 19 MSL NavCam frames in the VISOR sample data
(`sample_data/CylindricalMosaic/NLB_7122993*ILT_*NCAM00353/00293*.IMG` and
`NLB_7123012*/7123016*ILT_*NCAM00294/00354*.IMG`) and MSL VISOR calibration. These
are sequences from one site, so pointing correction is the right tool for them.

```bash
export MARS_CONFIG_PATH=/path/to/calibration/msl
./demo-co-registration.sh --list all.lis --nav2
```

Observed:

| Stage | Result |
|-------|--------|
| `marschkovl overlap=40` | 51 pairs evaluated, 8 pairs at or above 40% |
| `marsautotie density=50 grid_spacing=15` | 312 tiepoints; `Input image 10 not represented by tiepoints` |
| `marsnav -remove max_residual=10 max_remove=50` | mean pixel error 37.348320 → 4.729342; 182 of 312 tiepoints kept |
| `marsnav2` | 303 tracks (96% two-observation); mean 0.361996 px, median 0.262287 px; **six disconnected groups**; pointing changes all 0.000000 |
| `marsmos` ± `navtable=` | two 30000×30000 mosaics differing in 57,126,946 pixels |

Reading those numbers honestly:

- 37.35 → 4.73 pixels is a real improvement, but 4.73 px is not a good solution.
  `marsnav2` explains why: the 19 frames form six disconnected groups (13 frames
  in the largest, four frames alone), and 96% of tracks are seen by only two
  images. The set is under-tied, not mis-solved.
- Image 10 had no tiepoints, and `marsnav` still emitted a "correction" for it
  anyway — tens to hundreds of degrees, and by far the largest delta of the set,
  in every run (−824.5°/+3165.0°, +15.1°/−19.1°, −193.6°/−79.5° and +100.1°/−122.3°
  in four runs on identical inputs). An unconstrained frame produces garbage rather
  than being skipped. Always read the `not represented` lines.
- `marsnav2` converged without moving any pointing (cost change 0.000000e+00,
  seven unsuccessful steps): with `Nominal Pointing Errors 0.0630°` for this
  instrument, the sparse two-observation tracks bought no improvement over the
  nominal pointing. Its 0.36 px mean is a residual against simultaneously
  adjusted ground points, not a measure of registration quality.
- **These numbers are not exactly reproducible.** Every figure in the table above
  is one run. Across repeat runs on identical inputs the table's row values moved
  by a few percent — about 312 tiepoints of which 180–185 are kept, about
  37.4 px initial and 4.6–4.9 px final error, about 300 `marsnav2` tracks with a
  mean near 0.37 px and a median near 0.27 px. Treat them as magnitudes, not
  digits. What was identical in every run: the eight overlap pairs, the
  `not represented` line, the six disconnected groups and their membership, and
  the all-zero `marsnav2` pointing changes.

The corrected pointing does reach the mosaic. Rendering the same 19 frames twice,
with and without the table, gives two 30000×30000 mosaics differing in 57,126,946
pixels:

```bash
tig marsmos inp=all.lis out=mosaic_raw.img
tig marsmos inp=all.lis out=mosaic_nav.img navtable=pointing.nav
tig difpic inp=\( mosaic_raw.img mosaic_nav.img \)
```

`marsmos` prints `Unable to find ANY entries for solution id 'COREG'. Using best
available.` and then `19 values read from navigation file` — it reads and applies
the table regardless of that message.

`marsmap` on the same set:

```bash
tig marsmap inp=all.lis out=mosaic.img navtable=pointing.nav
```

which completes and writes a cylindrical mosaic (31 MB for this MSL set). On an
image built before the MPI/hwloc SIGFPE was fixed in the image itself, `marsmap`
dies with a floating-point exception (exit 136) instead; prefix such a run with
`tig env HWLOC_COMPONENTS=-x86`.

## Worked example — M20 NavCam repeat coverage, verified

Three Mars 2020 left NavCam frames from sol 650, downloaded from the PDS
`mars2020_navcam_ops_calibrated` bundle, all at the same commanded pointing and
taken across about ten minutes — that is, repeat coverage of one scene:

```
NLF_0650_0724654126_005RAD_N0320000NCAM08111_0A0095J01.IMG
NLF_0650_0724654402_851RAD_N0320000NCAM08111_0A0095J01.IMG
NLF_0650_0724654699_975RAD_N0320000NCAM08111_0A0095J01.IMG
```

```bash
export MARS_CONFIG_PATH=/path/to/calibration/m20
./demo-co-registration.sh --grid-spacing 40 NLF_0650_*NCAM08111_0A0095J01.IMG
```

Observed:

| Stage | Result |
|-------|--------|
| `marschkovl overlap=40` | 3 of 3 pairs above threshold |
| `marsautotie grid_spacing=40 density=50` | 224 tiepoints (127 / 67 / 30 per pair) |
| `marsnav` | mean pixel error 0.826029 → 0.799630; 224 of 224 tiepoints kept |
| pointing deltas | ≤ 0.005° in azimuth and elevation |

As with the MSL example the residuals move between runs (a repeat gave 0.916035 →
0.901525 from the same 224 tiepoints); the sub-pixel scale and the tiny pointing
deltas are the reproducible part.

The frames were already co-registered to sub-pixel accuracy: the commanded
pointing is identical across the three, and the solution barely moves it. That is
the expected and desirable result for a repeat-coverage set, and it is what makes
these frames differenceable — but it also means this example verifies the
*mechanics*, not a large correction.

`grid_spacing=40` is required: at the default 15 these 5120×3840 frames abort with
`Too many X grid points: 339, max is 300`.

Differencing two of them directly shows why registration alone is not change
detection:

```bash
tig difpic inp=\( first.IMG third.IMG \)   #  58,458,623 of 58,982,400 samples differ
tig hist first.IMG 'nohist'                #  mean DN 3598.898
tig hist third.IMG 'nohist'                #  mean DN 1082.897
```

Essentially every pixel "changed", because the two frames differ radiometrically
by a factor of three. Any real change-monitoring product needs radiometric
normalisation before the difference step.

## What is documented but not verified here

Executed end to end, with the results above: `marschkovl`, `marsautotie`,
`marsnav`, `marsnav2`, `marsmos` (with and without a navigation table), `marsmap`
(with a navigation table), `difpic`,
`f2`, `hist`, `marscheckcm` (all components `OK` at `tol=0.001` on an MSL frame).

Not verified:

- **`marsautotie2`, `marstie`, `marsfidfinder`, `marsautoloco`** — described from
  their `.pdf` files only. Not run.
- **`marsrelabel navtable=`** — the program runs, but the output with a navigation
  table was byte-identical to the output without one apart from the history label,
  and the pointing it printed was the uncorrected label pointing. Whether, and
  how, the corrected pointing reaches a relabelled product is not established
  here; prefer passing `navtable=` to the mosaic program.
- **`marsortho`, `marsdispcompare`, `marserrdisp`, `marserror`** — described from
  their `.pdf` files only. Not run.
- **The end-to-end change-monitoring workflow** — steps 1–4 are verified
  individually above, but no two-epoch difference product was produced from
  co-registered, radiometrically normalised mosaics. Treat the
  [Change monitoring](#change-monitoring) sequence as a composition of verified
  parts, not as a tested pipeline.
- **`--nav2` on the M20 example** and any run above 200 frames.

## Troubleshooting

| Symptom | Cause and fix |
|---------|---------------|
| `Found 0 tiepoints` for every pair | The frames do not overlap, or the initial pointing is too far off for `search=` to cover. Check with `marschkovl` at a low `overlap=` first: it prints the measured percentage per pair. |
| `Too many X grid points: N, max is 300` | Raise `grid_spacing` (the frame is larger than the default grid supports). |
| `Input image N not represented by tiepoints` | That frame is unconstrained; its pointing correction is meaningless. Lower `density`, lower `quality`, or drop the frame. |
| `marsnav2` reports disconnected groups | Groups are not registered to each other. More tiepoints between groups, or a joint set that actually overlaps. |
| Mean pixel error barely improves | Often the tracks are all two-observation; the geometry cannot be constrained. Check the track statistics `marsnav2` prints. |
| `Unable to find ANY entries for solution id 'X'. Using best available.` | Printed by the mosaic programs; they still read the table. Confirm with the `N values read from navigation file` line that follows. |
| Output tiepoint file not written | The tiepoint programs will not overwrite an existing file; remove it first. |
| `Floating point exception (core dumped)`, exit 136 | MPI/hwloc CPU detection, seen from `marsmap` on images built before the image itself set `HWLOC_COMPONENTS=-x86`. Re-run as `tig env HWLOC_COMPONENTS=-x86 <prog> ...`. |

## Data sources

- VISOR sample data and per-mission calibration:
  [Downloading VISOR Data](downloading-visor-data.md)
- Mars 2020 NavCam products, PDS Geosciences Node:
  <https://pds-geosciences.wustl.edu/missions/mars2020/>. The calibrated NavCam
  frames used above came from the `mars2020_navcam_ops_calibrated` bundle,
  `data/sol/00650/ids/rdr/ncam/`.

## References

- Program parameter definitions inside the image:
  `$V2TOP/mars/lib/x86-64-linx/{marsautotie,marsautotie2,marstie,marsnav,marsnav2,marschkovl,marsmos,marsmap,marsortho,marsrelabel,marscheckcm,marsfidfinder,marsautoloco}.pdf`
- [Mesh Generation](mesh-generation.md) — the stereo/XYZ path
- [Command Reference](commands.md) — the wider VICAR toolset
- [Components](../architecture/components.md) — where these programs sit
