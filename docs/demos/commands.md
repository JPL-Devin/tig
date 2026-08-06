# Terrain Intelligence Generator - Demo Commands

A tour of the TIG image's core capabilities, run from your own shell with
[`tig`](../../tig-cli/README.md). Every command below executes inside the
container; files land in the directory you ran from.

## Prerequisites

- Docker Engine 20.10+ or Docker Desktop
- Python 3.9+

```bash
pip install tig-cli
```

The image (`ghcr.io/nasa-ammos/tig/terrain-intelligence-generator:opensource`)
is pulled automatically on first use.

---

## Step 1: Generate test images

```bash
mkdir -p ~/vicar-demo && cd ~/vicar-demo

tig gen test.vic 64 64
tig gen large.vic 512 512
ls -lh *.vic
```

**Expected output:**
```
Beginning VICAR task GEN
GEN Version 2019-05-28
GEN task completed
```

The first command starts the container; later ones reuse it, so they return in
well under a second. Note that VICAR tools often exit non-zero even on success,
so avoid chaining them with `&&` in scripts.

## Step 2: Image operations

```bash
tig copy test.vic test_copy.vic
tig stretch test.vic stretched.vic
tig label test.vic
tig hist test.vic
```

`copy` prints a "COPY VERSION" banner; `stretch` prints histogram statistics and
the auto-stretch parameters it chose.

## Step 3: Format conversion with vicario

```bash
tig vicario test.vic test.png
tig vicario test.vic test.jpg
tig vicario test.vic test.tiff
ls -lh test.*
```

**Expected output:**
```
Image write Done
JConvertIIO
0) inp = test.vic
1) out = test.png
2) format = png
3) oform = byte
4) rescale = true
```

See the [vicario reference](../reference/vicario.md) for the full option set.

## Step 4: MARS terrain commands

```bash
tig bash -c 'ls /usr/local/bin | grep "^mars"'
tig bash -c 'ls /usr/local/bin | grep -c "^mars"'
```

74 MARS commands, including `marsmap`, `marscorr`, `marscor3`, `marsxyz`,
`marsrfilt`, `marsmesh` and `marsautotie`. Most need calibration data; see
[Calibration Data](../reference/calibration-data.md). For an end-to-end terrain
run, see [Mesh Generation](mesh-generation.md).

## Step 5: Browse everything available

```bash
tig bash -c 'ls /usr/local/bin | wc -l'      # ~545 commands
tig bash -c 'ls /usr/local/bin | head -20'
```

To call them without the `tig` prefix:

```bash
tig --shim
export PATH="$HOME/.local/share/tig/shims:$PATH"
gen another.vic 64 64
```

## Step 6: Inspect the VICAR environment

```bash
tig bash -c 'echo "V2TOP=$V2TOP VICSYS=$VICSYS"'
```

The image ships no mission data. Calibration and sample images come from VISOR;
see [Downloading VISOR Data](downloading-visor-data.md).

## Step 7: GUI tools

VICAR's X11 tools display on your host:

```bash
tig xvd test.vic
```

## Step 8: Interactive shell

```bash
tig bash
```

## Cleanup

```bash
tig --status      # containers tig is keeping around
tig --shutdown    # remove them
```

---

## Working with data outside your home directory

tig mounts your current directory and home directory read-write, and everything
else read-only. To write elsewhere:

```bash
tig --writable-path /data/results marsmap INP=/data/in.vic OUT=/data/results/out.vic
```

---

## Troubleshooting

**`Failed to connect to Docker`** — the daemon is not running, or your user is
not in the `docker` group.

**Permission denied on mounts (SELinux)** — tig passes
`--security-opt label=disable` automatically when SELinux is Enforcing;
`--no-selinux-label-disable` turns that off.

**Output written where you did not expect** — relative paths resolve against the
directory you ran `tig` from. `--disable-path-translation` shows the raw paths
tig would pass through.

**A command is not found** — `tig bash -c 'ls /usr/local/bin | grep <name>'`.

---

## Next steps

- **[Mesh Generation](mesh-generation.md)** — full stereo terrain pipeline
- **[tig-cli README](../../tig-cli/README.md)** — configuration, X11, shims
- **[TIG VICAR image](../../terrain-intelligence-generator/README.md)** — building from source
