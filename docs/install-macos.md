# Installing TIG on macOS (Podman)

A complete first-run setup for macOS, from an empty machine to a rendered
VICAR image. It uses [Podman](https://podman.io) as the container runtime and
[XQuartz](https://www.xquartz.org) as the X server for the GUI tools.

Two macOS facts shape everything below:

- **Containers run in a virtual machine.** `podman machine` is a Linux VM, so a
  container sees the VM's filesystem and network, not your Mac's. Directories
  have to be shared with the VM to be visible, and the container reaches your
  Mac by the gateway name `host.containers.internal`.
- **The VICAR image is `linux/amd64` only.** On Apple Silicon it runs
  translated, which needs working x86-64 emulation inside the VM.

## 1. Prerequisites

| | |
|---|---|
| macOS | 13 Ventura or later, Intel or Apple Silicon |
| Python | 3.9 or later (`python3 --version`) |
| Homebrew | [brew.sh](https://brew.sh) — used for Podman and XQuartz |
| Disk | ~10 GB free (the image is 3.12 GB, plus the VM's own disk) |
| RAM | 8 GB minimum, 16 GB recommended for high-resolution meshes |

## 2. Install Podman and start the machine

```bash
brew install podman
```

Create the VM with enough resources — the default is 2 GiB of memory, which is
too small for mesh generation:

```bash
podman machine init --cpus 4 --memory 8192 --disk-size 100
podman machine start
podman info | head -20        # succeeds only when the machine is running
```

`podman machine start` is needed again after every reboot.

### Share directories outside your home directory

`podman machine` shares your home directory with the VM automatically, so
paths under `/Users/<you>` are your real files. Anything else — an external
volume under `/Volumes`, a data directory under `/opt` — is **not** shared, and
a container that reads it silently sees the VM's empty path instead of your
data. Keep TIG inputs and outputs under your home directory, or share the
extra location when creating the machine:

```bash
podman machine init --cpus 4 --memory 8192 --disk-size 100 -v /Volumes:/Volumes
```

Mounts can only be set at `init` time; changing them means recreating the
machine (`podman machine rm`, then `init` again).

## 3. Install tig-cli and point it at Podman

```bash
pip install tig-cli
```

TIG picks the first runtime it finds on `PATH`, in the order `docker`,
`podman`, `nerdctl`, `finch`. If you have `docker` installed as well, name
Podman explicitly:

```bash
export TIG_CONTAINER_RUNTIME=podman     # add to ~/.zshrc to make it stick
```

Or set it once for every shell in `~/.config/tig/config.toml`:

```toml
runtime = "podman"
```

Then pull the image and run a tool that needs no GUI:

```bash
tig gen test.vic 64 64        # pulls the image on first use — 3.12 GB, several minutes
tig label test.vic
```

If `tig gen` writes `test.vic` in the current directory, the container, the
mounts and the path translation all work. Nothing so far involves X11.

## 4. Install XQuartz for the GUI tools

Only the interactive tools — `xvd`, `marsmap`, `xvd_ipl` and the other X11
programs — need an X server. If you never open a window, you can skip this
section entirely.

```bash
brew install --cask xquartz
```

**Log out of macOS and log back in.** The XQuartz installer sets your `DISPLAY`
environment variable through a launch agent, and existing login sessions do not
pick it up. This single step is the most common cause of first-run failures:
until you do it, `DISPLAY` is unset, and TIG then skips its X11 setup silently
and the GUI tool fails later with a bare `Can't open display` error.

Confirm the variable is set, in a new terminal:

```bash
echo $DISPLAY
# /private/tmp/com.apple.launchd.XXXX/org.xquartz:0   <- good
# (empty)                                             <- log out and back in
which xhost                       # /usr/X11/bin/xhost or /opt/X11/bin/xhost
```

With XQuartz installed, TIG does the rest of the X11 setup itself on each run:
it sets `nolisten_tcp` to `false` so XQuartz accepts TCP connections, starts
XQuartz if it is not already running, and runs `xhost +localhost` to authorize
the connection. Under Podman the container is given
`DISPLAY=host.containers.internal:0`, which routes back out of the VM to
XQuartz on your Mac.

Check a window actually opens:

```bash
tig xvd test.vic
```

XQuartz appears in the Dock and displays the 64×64 test image. Quit the window
to return to the shell.

> If you changed XQuartz's `Allow connections from network clients` setting by
> hand, restart XQuartz afterwards — the preference is read at startup.

## 5. Apple Silicon: x86-64 emulation

TIG always requests `linux/amd64`, because that is the only platform the VICAR
image is published for. On Apple Silicon the VM must therefore translate
x86-64 binaries. Recent Podman versions enable Rosetta by default on the
`applehv` provider and fall back to QEMU otherwise; either works, Rosetta is
substantially faster.

Verify emulation is registered inside the VM:

```bash
podman machine ssh ls /proc/sys/fs/binfmt_misc/
# rosetta        <- Rosetta translation, fast
# qemu-x86_64    <- QEMU, slower but functional
```

If neither name is listed, no emulation is active and every VICAR command
fails immediately with `exec format error`; recreate the machine
(`podman machine rm && podman machine init ...`) on an up-to-date Podman.

Expect the first commands to be slow. Under QEMU in particular, memory-heavy
work such as mesh generation runs several times slower than native and has
been reported to crash with `SIGSEGV`
([containers/podman#28181](https://github.com/containers/podman/issues/28181));
that is an emulation bug, not a TIG one. `podman machine inspect` reporting
`Rosetta: true` is not sufficient — trust the `binfmt_misc` listing above.

## 6. Verify the install

```bash
tig gen check.vic 128 128            # container + mounts
tig label check.vic                  # VICAR runs and reads the file
tig vicario check.vic check.png      # Java tooling and file output
tig xvd check.vic                    # X11, if you installed XQuartz
tig --status                         # what containers TIG has running
tig --shutdown                       # stop them when you are done
```

All five succeeding means the environment is complete. To drop the `tig`
prefix, see `tig --shim` in the [tig-cli README](../tig-cli/README.md).

## 7. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `Error: no container runtime found` | The machine is not running, or Podman is not on `PATH`. Run `podman machine start`, then `podman info`. |
| GUI tool prints `Can't open display` or `Error: Can't open display: ` | XQuartz is missing, or you have not logged out and back in since installing it. Check `echo $DISPLAY` and `which xhost` (step 4). |
| `xhost: unable to open display` | XQuartz is installed but `DISPLAY` points at a stale socket. Quit XQuartz, open a new terminal, and retry. |
| A window opens but is blank or freezes | XQuartz was running before its `nolisten_tcp` setting changed. Quit XQuartz entirely and rerun the command; TIG restarts it. |
| `exec format error`, `no matching manifest` | No x86-64 emulation in the VM on Apple Silicon (step 5). |
| VICAR says a file does not exist, though it exists on your Mac | The path is outside your home directory and therefore not shared with the VM. Move the data under `$HOME` or recreate the machine with `-v` (step 2). |
| Killed processes, `SIGSEGV`, or out-of-memory during a mesh | The VM has too little memory. Recreate it with `--memory 8192` or more (step 2). |
| Commands hang after printing nothing | A previous run left a container behind. `tig --status`, then `tig --shutdown`. |
| Docker is being used instead of Podman | `TIG_CONTAINER_RUNTIME` is unset and `docker` is earlier in the detection order (step 3). |

Still stuck? Open an issue with the output of `tig --status`, `podman info`,
`echo $DISPLAY` and `podman machine ssh ls /proc/sys/fs/binfmt_misc/`.

## Related

- **[Getting Started](getting-started.md)** — First demo once the install works.
- **[tig-cli](../tig-cli/README.md)** — Runtime selection, the macOS broker and configuration reference.
- **[TIG VICAR image](../terrain-intelligence-generator/README.md)** — Building the image yourself.
