# Co-registration and Change Monitoring

Bringing overlapping frames into a common geometry with the MARS tools, driven by
[`tig`](../../tig-cli/README.md), and what it takes to build change monitoring on
top of that.

`demo-co-registration.sh` runs the sequence: overlap check → tiepoints → pointing
solution. `demo-change-monitoring.sh` composes that into a two-epoch change
product: joint solve → common projection → difference raster → registration
controls.

> **There is still no change-detection program in this image.** Nothing in the
> ~74 `mars*` programs takes two epochs and returns a difference product. Change
> monitoring is a workflow you compose, and `demo-change-monitoring.sh` is that
> composition, run end to end on two sols of M20 NavCam repeat coverage:
> `marschkovl` → `marsautotie` → `marsnav` → `marsmap` per epoch with one shared
> navigation table → `f2`/`difpic`/`hist`. What it produces is a *difference
> raster with a measured registration floor*, not a change map — see
> [Two-epoch change product](#two-epoch-change-product--verified) for the numbers
> and for what dominates the difference (illumination, not surface change).

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
four write the same tiepoint file for `marsnav` — but see
[The other tiepoint sources](#the-other-tiepoint-sources--marstie-marsautotie2-marsfidfinder)
for what happened when they were actually run: only `marsautotie` and `marstie`
produced tiepoints `marsnav` could use.

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

`demo-change-monitoring.sh` is exactly that sequence, plus the normalisation and
the control experiments without which the difference cannot be interpreted — see
[Two-epoch change product](#two-epoch-change-product--verified).

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
uncertainty within one acquisition. All three are verified below.

## Two-epoch change product — verified

`demo-change-monitoring.sh` implements the sequence above and adds what makes the
output readable: a common projection, a normalisation step, the difference
rasters, and **control experiments that measure the registration floor** so the
difference can be compared against it.

It is a separate script rather than a `--change` mode of
`demo-co-registration.sh` because it does a different job downstream of the same
solve — projection, radiometric matching, four differences, five controls and
display conversion — and folding that into the co-registration demo would have
buried the three-stage sequence that demo exists to show. It calls the same
programs with the same conventions.

```bash
./demo-change-monitoring.sh \
  --epoch1 NLF_0649_0724552533_722RAD_N0320000NCAM08111_0A0095J01.IMG \
  --epoch2 NLF_0650_0724654126_005RAD_N0320000NCAM08111_0A0095J01.IMG \
  --tie-extra sol649_extra.lis --tie-extra sol650_extra.lis \
  --calibration /path/to/calibration/m20 \
  --scale 40 --bounds "0 93 0 -70" --box "100 300 2200 3100"
```

Stages: label/solar-geometry report → `marschkovl` → `marsautotie` → `marsnav` →
`marsmap` geometry probe → `marsbrt` → one `marsmap` render per epoch with the
*same* navigation table and *identical* projection parameters → statistics box
validation → `f2` differences → controls → `f2`/`vicario` display products.
`--tie-extra` frames take part in the overlap, tiepoint and pointing solve but
are not rendered or differenced: more frames tie the two epochs together, only
the two primaries need to be pixel-comparable.

### The data

Two epochs of Mars 2020 left NavCam coverage of the same scene, same product
family (`N0320000NCAM08111_0A0095J01`, SITE 32 DRIVE 0), from
`mars2020_navcam_ops_calibrated`, three frames per sol:

| | Sol 649 (epoch 1) | Sol 650 (epoch 2) |
|---|---|---|
| Primary frame | `NLF_0649_0724552533_722RAD_…` | `NLF_0650_0724654126_005RAD_…` |
| Local true solar time | 11:04:54 | 14:33:07 |
| Solar azimuth / elevation | 100.685° / 66.917° | 201.858° / 46.5214° |
| Instrument az / el | 45.769° / −34.6043° | 45.5813° / −34.3798° |
| Exposure | 17.161 ms | 18.111 ms |

The rover did not move between the two sols and the commanded pointing is
effectively identical, which is what makes a pointing-only workflow legitimate
here. What did change is the sun: ~101° of solar azimuth and ~20° of solar
elevation. That is the dominant signal in the difference, by design of the
available data — repeat coverage at matched local time is what you would actually
want and is not what sol 649/650 offers.

### Measured results

One run of that command, on `…/terrain-intelligence-generator:opensource` (digest
`sha256:1ea12248a0014a34263aec530b1e42780f57f8d135857d7cec53a82a791e9af6`),
tig-cli 0.2.0, M20 VISOR calibration:

| Stage | Result |
|-------|--------|
| `marschkovl overlap=40` | 15 of 15 pairs accepted; the cross-epoch primary pair at **99.961068%** overlap |
| `marsautotie density=50 grid_spacing=40` | 1003 tiepoints across all six frames |
| `marsnav -remove max_residual=5` | mean pixel error **7.762723 → 1.580886 px**; 748 of 1003 tiepoints kept |
| pointing deltas | ≤ 0.02° for five frames, 0.19°/0.21° for one |
| `marsmap` probe (`zoom=0.25`) | natural scale 51.623641 px/degree; el −71.06…+9.53°, az 326.50…126.95° |
| epoch renders | 2799 × 3720 HALF, `scale=40`, az 0…93°, el 0…−70°, one per epoch |
| `marsbrt` | ran; solution **ill-conditioned on this set** (see below) |

Statistics, all over the interior box `sl=100 ss=300 nl=2200 ns=3100` (excludes
the projection's empty margins — `maxmin` in the script refuses a box containing
0 DN):

| Product | mean | σ | min | max |
|---------|-----:|---:|----:|----:|
| epoch 1 render | 2852.428 | 367.5240 | 616 | 5058 |
| epoch 2 render | 2590.362 | 713.6342 | 487 | 4597 |
| raw difference `in1-in2` | 262.0670 | **637.8832** | −2404 | 3299 |
| gain-matched `in1-1.10117*in2` | 0.001243 | **700.8071** | −2785.409 | 3190.951 |
| linear-matched `in1-0.515003*in2-1518.38` | 0.000804 | **384.5241** | −2233.198 | 2422.102 |

The linear match (scale by the σ ratio, offset by the mean) is the usable
normalisation: it is the only one of the three that reduces the spread rather
than moving it around. Gain-only matching makes it *worse* (700.8 vs 637.9),
because the two epochs differ in contrast, not just in level.

### The registration floor — why the controls matter

A difference image is only meaningful relative to how much signal
mis-registration alone produces. The script measures that on the same data by
differencing epoch 1 against deliberately perturbed versions of itself:

| Control | σ of the difference | difpic |
|---------|--------------------:|-------:|
| re-render epoch 1, difference against the first render | 0 | **0 pixels differ** |
| epoch 1 shifted **1 px** in sample | 133.6843 | — |
| epoch 1 shifted **2 px** | 220.0082 | — |
| epoch 1 shifted **3 px** | 270.2544 | — |
| epoch 1 rendered *without* the navigation table | 106.5743 | 10,179,460 pixels differ |

Read against those numbers:

- Rendering is bit-exact reproducible (difpic 0), so nothing in the difference is
  pipeline noise.
- **One pixel of mis-registration produces σ ≈ 134 DN** on this terrain. The
  normalised epoch difference is σ = 384.5, i.e. ~2.9× the one-pixel floor —
  above the floor, but the same order of magnitude. A σ = 150 "change" on this
  scene would be indistinguishable from a sub-pixel registration error.
- Applying the navigation table moves the render by less than the one-pixel
  control (106.6 < 133.7) while changing 10.2 M pixels: the correction is real
  and sub-pixel, which is also why co-registration cannot be validated by
  "did the mosaic change".

### `marsbrt` was ill-conditioned here — read the warning

`marsbrt` computes per-frame multiplicative/additive corrections from the overlap
statistics in `marsmap`'s `ovr_out=` file, and `marsmap brtcorr=` applies them.
It ran (exit 0) but its solution on this six-frame, two-sol set is degenerate —
near-zero multipliers for the two primaries:

```
Image   MultCorr        AddCorr
      0    0.0008025605  398.8228988771
      1    0.0010552859  398.8335266865
      2    1.0754533796  138.6786923533
      3    2.6174911459 -224.3749295958
      4    0.7979243246 -152.6082524834
      5    1.5072733034 -559.3519358378
```

The quoted run's corrected renders collapse to near-constant DN (σ 0.485881 and
0.866135, against 367.5 and 713.6 raw). Across the quoted and independent runs,
the epoch-1 box min/max were 616/5058 and 615/5069 (roughly ±1/±11 DN), and the
BRTCORR sigmas were 0.485881/0.866135 and 0.486056/0.598729 (about 0.49–0.87
depending on the solve). The BRTCORR difference statistics
(σ 0.789881) describe a flattened image, not normalised change. The script
detects this — near-zero or non-positive multipliers (`|MultCorr| < 0.01` or
`MultCorr <= 0`), or a BRTCORR render whose σ falls below 10% of the raw σ —
and prints a warning rather than silently reporting the numbers. The independent
run's primaries were near-zero positive multipliers, so the flattened-σ rule is
the reliable catch in that case. Across runs the `marsbrt` table varied (an
earlier run gave −0.264 and −2.55 multipliers), so treat the multi-epoch
`marsbrt` solve as unreliable on sets like this and use the linear match.
`--no-brtcorr` skips it.

### What the difference image actually shows

`diffview.png` is the linear difference scaled so 0 → 128 DN and ±3σ → 0/255,
next to 2%-stretched renders of both epochs. Inspected: the difference is
dominated by (a) the shadow of the rover and its mast falling across the
foreground in the 14:33 epoch and absent from the 11:04 epoch, (b) an overall
brightness/contrast change across the whole scene, and (c) thin bright/dark
outlines on the highest-contrast edges — the wheel tracks and the rover deck —
which is the sub-pixel residual and resampling signature, not change. The terrain
itself is unchanged between the two sols, and that is the honest verdict for this
pair: **the product works; the scene did not change; almost all of the difference
is illumination.**

Confounders, in the order they bit on this data:

1. **Illumination.** 101° of solar azimuth and 20° of solar elevation between
   epochs. Shadows move, and moving shadows are the largest feature in the
   difference. Nothing in the toolset corrects for this; only acquiring the
   epochs at matched local solar time does.
2. **Radiometry.** A factor-of-1.1 mean and 1.9 σ difference even at nearly equal
   exposure. Normalise before differencing, and say which normalisation you used.
3. **Pointing residual.** 1.58 px mean tiepoint residual here, against a 1 px
   floor worth σ ≈ 134 DN. Any change smaller than that is not detectable.
4. **Resampling.** Both epochs are resampled into the cylindrical frame at
   40 px/degree, below the 51.6 px/degree natural scale. Edges pick up
   interpolation differences the flat terrain does not.
5. **Parallax.** Zero here because the rover did not move. Between drives,
   pointing correction cannot register the epochs at all — that needs
   `marsortho` on XYZ products.
6. **Rover hardware in frame.** The deck, wheels and their shadows are in the
   foreground of both epochs and move relative to the terrain whenever the
   mast or the rover moves.

A non-zero difference is not, by itself, detected surface change. Report the
difference statistic and the shift-control statistic together, or the number
means nothing.

### Reproducibility

The stage counts move between runs on identical inputs: three runs of the same
command gave 1003/1004/1008 tiepoints, 748/779/796 kept, 7.69–7.86 px initial and
1.47–1.58 px final residual, and σ 384.3–384.5 for the linear difference. The
pixel-shift controls reproduce to four figures (133.68 / 220.01 / 270.25), but
the nav-effect control varies by a few percent: 106.5743 / 10,179,460 differing
pixels in the quoted run versus 102.1765 / 10,175,950 in the independent run.
Both are below the one-pixel floor of 133.7, so the conclusion is unchanged.
Treat the residuals, tiepoint counts, and nav-effect control as magnitudes. A
statistics box rejected for containing 0 DN now reports the reason on stderr.

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

## The other tiepoint sources — `marstie`, `marsautotie2`, `marsfidfinder`

Run on the two-epoch M20 primary pair above (sol 649 + sol 650, M20 VISOR
calibration), which `marsautotie` alone ties with 126 tiepoints:

**`marstie` works, in batch mode, and it is a matcher — not only an editor.**

```bash
tig marstie inp=pair.lis out_tpt=ties.tpt interact=BATCH pairs=ALL_PAIRS cull=0
```

Exit 0 in 0.3 s, 9 tiepoints across the cross-epoch pair, printed as a table with
per-point quality and Δsample/Δline. At the default `cull=1` it keeps only the
best point per pair (1 tiepoint). `INTERACT` must be given **by name**: passing
the keyword positionally (`tig marstie 'BATCH' inp=...`) is silently dropped, the
program falls back to `INTERACT` and tries to launch the X11 tiepoint editor —
which is not in the image (`sh: /tp: No such file or directory`) — and still exits
0. The manual editing path is therefore unusable in this image; `interact=BATCH`
is the only way to run it.

**`marsautotie2` runs, produces tiepoints, and `marsnav` cannot use them.**

```bash
tig marsautotie2 inp=pair.lis out=ties_asift.tpt
```

Exit 0 in 2 m 13 s, 135 tiepoints on these two 5120×3840 frames. With
`navtable=`, `match_method=TIGHT` and
`crosscheck=CROSSCHECK`: 3 m 00 s, 109 tiepoints. In every record the
`<projected>` element is `0.000000` in both line and sample, where `marsautotie`
fills it with the predicted position. `marsnav` measures its "commanded" error
against that field, so it reads the whole set as wildly inconsistent:

| Tiepoint source | `marsnav` commanded error | Final | Kept |
|-----------------|--------------------------:|------:|-----:|
| `marsautotie` (126 ties) | 2.328178 px | 2.101460 px | 124 |
| `marsautotie2` (135 ties) | 554.716959 px | 0.010000 px | 1 |
| `marsautotie2` + `CROSSCHECK` (109 ties) | 297.546606 px | `inf` | 0 |

Passing the ASIFT file back through `marstie` as `in_tpt=` does not fill the
projected field either. So `marsautotie2` is verified as *executing* and as
producing plausible left/right coordinates, and is **not** usable as a drop-in
tiepoint source for `marsnav` here — consistent with its `.pdf` warning that its
parameters are camera-specific and "some tuning may be required".

**`marsfidfinder` cannot run on the public calibration bundles.** It reaches the
per-image search and then needs fiducial-template configuration that the VISOR
calibration data does not contain:

```
Error opening camera mapping file 'param_files/M20_M20_camera_mapping.xml'
```

That file name is the mission id twice; the bundle ships `M20_camera_mapping.xml`.
With a copy under the doubled name placed on `MARS_CONFIG_PATH`, it gets one step
further and stops on the file that actually matters:

```
couldnt open param_files/M20_M20_fiducial_info.parms
** ABEND called **
```

`marsfidfinder.pdf` says it "requires a set of templates for the fiducials … for
each fiducial, there needs to be a template matching all possible filters and
eyes", described in an engineering or flight parameter file. Neither the
`.parms` file nor the template images are in the public M20 or MSL VISOR
calibration, so this program stays unverified.

## Orthorectification and localisation — `marsortho`, `marsautoloco`

**`marsortho` verified** on the MSL sample data, which ships matching linearised
images and XYZ maps (`sample_data/OrthorectifiedMosaic/NLB_*ILT_*.IMG` +
`NLB_*XYZ_*.IMG`), four frames, MSL VISOR calibration:

```bash
tig marsortho inp=ilt.lis in_xyz=xyz.lis out=ortho.img out_dem=ortho_dem.img \
  scale=0.05
```

Exit 0 in 0.9 s. It computes the output extent from the XYZ data
(`MINX=-144.239960 MAXX=-54.620903 MINY=60.657669 MAXY=166.535324`, metres) and
writes a 1792×2118 HALF ortho mosaic (7.6 MB, mean DN 290.7244, σ 774.8824 over
the whole frame, i.e. sparse coverage inside the bounding box) plus a REAL DEM
(15 MB). `scale=` or `nl=` is mandatory even though the extent is derived: with
neither, it abends on `Neither NL nor SCALE is defined. Cannot compute output's
geometry.`

**`marsautoloco` remains unverified: it needs an orbital base map.** It
co-registers an orthomosaic to a base map in map coordinates, and there is no
HiRISE/orbital base map for the sample scene in the VISOR data. Substituting a
4× decimated copy of the `marsortho` product above as a synthetic base map gets
through label parsing, resolution ratio (`4.00, 4.00`) and rescaling, then stops
at the correlation:

```
No Image Map Projection labels could be read from base map
Using weighted correlation.
Correlation unsuccessful! No output generated
```

Most easting/northing sign conventions abend earlier with `Error. Required base
map footprint steps out of bounds` — the base map has to be larger than the
orthomosaic footprint plus the search space, and the program takes its footprint
geometry from map-projection labels a home-made base map does not have. Verifying
this program needs a real map-projected base map, not the frames we have.

## The stereo uncertainty helpers — verified

Run on the MSL sample stereo pair
(`sample_data/StereoCorrelation/N{L,R}B_712299404EDR_F0961766NCAM00353M1.IMG`,
MSL calibration), following `sample_data/Scripts/run_corr`. Every stage exit 0,
with statistics from `hist`/`maxmin` on each output — none of them all-zero:

| Stage | Output | mean | σ | Notes |
|-------|--------|-----:|---:|-------|
| `marsecorr` L→R / R→L | 2-band 512×512 | 113.05 / 134.70 | 131.66 / 156.14 | 56.23% / 54.78% coverage |
| `marscor3` L→R / R→L | 2-band 1024×1024 | 228.74 / 271.78 | 265.61 / 313.18 | 889,531 / 871,173 tiepoints (84.8% / 83.1%) |
| `marsdispcompare` | 1-band mask | 117.68 | 127.12 | L→R vs R→L consistency mask, 0…255 |
| `marsmask` | masked disparity | 216.80 | 260.00 | applies that mask |
| `marsxyz` → `marsrfilt` | 3-band XYZ | −10.20 | 49.75 | 562,365 valid points, 483,919 rejected for missing correlation |
| `marserrdisp` | 2-band disparity error | 0.182156 | 0.021816 | line/sample uncertainty, px |
| `marserror` | range error, XYZ error, range, error magnitude | 1.5257 (range) | 1.4787 | range error magnitude mean 0.002790 m |

```bash
tig marsdispcompare inp=\( ldsr.img rdsr.img \) out=mask.img -scaling point=cm=label
tig marserrdisp inp=\( left.IMG right.IMG \) out=disp_err.img ls_order=1 point=cm=label
tig marserror inp=\( left.IMG right.IMG \) out=range_err.img dispar=ldsp.img \
  xyz=lxyz.img disp_err=disp_err.img range=range.img rng_err_magnt=rng_mag.img \
  xyz_err=xyz_err.img point=cm=label
```

`marserror` abends with `XYZ_ERR must have 0, 1 or 3 filenames` unless `xyz_err=`
is supplied as well — it is optional in the `.pdf` but not independent of the
other error outputs. `marscor3 band=2` on these single-band EDRs does not fail; it
prints `Input band (2) is greater than number of bands in input image. Band set to
1.` and continues, and an explicit `band=1` rerun gave identical statistics.

These quantify stereo uncertainty inside one acquisition. They are the right tool
for "how well do I know this XYZ point", and no part of them compares epochs.

## What is documented but not verified here

Executed end to end, with the results above: `marschkovl`, `marsautotie`,
`marsnav`, `marsnav2`, `marsmos` (with and without a navigation table), `marsmap`
(with a navigation table, with explicit projection bounds, and with `brtcorr=`),
`marsbrt`, `marstie` (`interact=BATCH`), `marsautotie2`, `marsortho`,
`marsecorr`, `marscor3`, `marsdispcompare`, `marsmask`, `marsxyz`, `marsrfilt`,
`marserrdisp`, `marserror`, `difpic`, `f2`, `hist`, `maxmin`, `stretch`,
`vicario`, `marscheckcm` (all components `OK` at `tol=0.001` on an MSL frame),
and the whole of `demo-change-monitoring.sh` on two sols of M20 NavCam coverage.

Ran, but not usable as documented:

- **`marsautotie2`** — produces tiepoints with an unfilled `<projected>` field, and
  `marsnav` keeps 1 of 135 / 0 of 109 of them. Verified as executing; not
  verified as a tiepoint source for a pointing solve. See
  [The other tiepoint sources](#the-other-tiepoint-sources--marstie-marsautotie2-marsfidfinder).
- **`marsbrt` across epochs** — runs, but its solution was degenerate on this
  two-sol set (near-zero and negative multipliers, flattened output) and varied
  run to run. Verified as executing; not verified as a radiometric normaliser for
  change monitoring.
- **`marstie` interactive mode** — only `interact=BATCH` is verified. The manual
  editor is not present in the image (`sh: /tp: No such file or directory`).
- **`marsrelabel navtable=`** — the program runs, but the output with a navigation
  table was byte-identical to the output without one apart from the history label,
  and the pointing it printed was the uncorrected label pointing. Whether, and
  how, the corrected pointing reaches a relabelled product is not established
  here; prefer passing `navtable=` to the mosaic program.

Not run at all:

- **`marsfidfinder`** — needs `param_files/M20_M20_fiducial_info.parms` and per
  filter/eye fiducial templates, which are in neither the public M20 nor the MSL
  VISOR calibration bundle. Abends before any image processing.
- **`marsautoloco`** — needs a map-projected orbital base map covering the scene
  (HiRISE-class), which the VISOR sample data does not include. With a synthetic
  base map it reaches `Correlation unsuccessful! No output generated`.

Also unverified: **`--nav2` on the M20 examples**, any run above 200 frames,
change monitoring **across a drive** (all epochs here are from one rover
position, so parallax is zero and `marsortho` is not in the change path), and
change monitoring **at matched local solar time** — no such repeat pair was
available, so the illumination confounder is present in every number above rather
than controlled for.

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
| `marsmap`/`marsmos` does not apply the corrections | Pass `match_method=TIGHT` with `navtable=`, then confirm one `Pointing Correction has been applied` line per input in the log. With loose matching the corrections were silently not applied on the M20 set. |
| `[TAE-POSERR] Positional values may not follow values specified by name.` | A keyword given positionally (`nohist`, `NOGRID`) after named parameters. Put it first (`tig hist file -nohist sl=…`) or use the named form (`grid=NOGRID`). |
| `Output Solution ID required!` from `marsbrt` | `out_solution_id=` is mandatory even though its `.pdf` shows no default. |
| `BAD FUNCTION STRING` from `f2` | The expression parser rejects some parenthesised forms. Write `in1-0.515003*in2-1518.38` flat, and for a byte display double-quote inside the shell quotes: `func='"min(255,max(0,in1*0.11+128))"'`. |
| `Neither NL nor SCALE is defined` from `marsortho` | Supply `scale=` (metres/pixel) or `nl=`; the extent is derived from the XYZ data but the sampling is not. |
| `XYZ_ERR must have 0, 1 or 3 filenames` from `marserror` | Add `xyz_err=` alongside the other error outputs. |
| Statistics dominated by 0 DN | A projected raster has empty margins. Measure inside an interior box (`sl/ss/nl/ns`); `demo-change-monitoring.sh` refuses a box whose `maxmin` minimum is 0. |
| `INTERACT` ignored, `sh: /tp: No such file or directory` | `marstie` keywords must be named: `interact=BATCH`. The X11 tiepoint editor is not in the image. |
| `Floating point exception (core dumped)`, exit 136 | MPI/hwloc CPU detection, seen from `marsmap` on images built before the image itself set `HWLOC_COMPONENTS=-x86`. Re-run as `tig env HWLOC_COMPONENTS=-x86 <prog> ...`. |

## Data sources

- VISOR sample data and per-mission calibration:
  [Downloading VISOR Data](downloading-visor-data.md)
- Mars 2020 NavCam products, PDS Geosciences Node:
  <https://pds-geosciences.wustl.edu/missions/mars2020/>. The calibrated NavCam
  frames used above came from the `mars2020_navcam_ops_calibrated` bundle,
  `data/sol/00649/ids/rdr/ncam/` and `data/sol/00650/ids/rdr/ncam/` (the two-epoch
  example uses three frames from each sol). Fetch them from
  <https://planetarydata.jpl.nasa.gov/img/data/mars2020/mars2020_navcam_ops_calibrated/>;
  the `pds-geosciences.wustl.edu` browse paths return HTML, not VICAR data.
- MSL stereo pair and orthorectification inputs: the VISOR sample data
  (`sample_data/StereoCorrelation/`, `sample_data/OrthorectifiedMosaic/`), with
  `sample_data/Scripts/run_corr` as the authoritative stereo recipe.

## References

- Program parameter definitions inside the image:
  `$V2TOP/mars/lib/x86-64-linx/{marsautotie,marsautotie2,marstie,marsnav,marsnav2,marschkovl,marsmos,marsmap,marsbrt,marsortho,marsrelabel,marscheckcm,marsfidfinder,marsautoloco,marsecorr,marscor3,marsdispcompare,marsmask,marsxyz,marsrfilt,marserrdisp,marserror}.pdf`
- [Mesh Generation](mesh-generation.md) — the stereo/XYZ path
- [Command Reference](commands.md) — the wider VICAR toolset
- [Components](../architecture/components.md) — where these programs sit
