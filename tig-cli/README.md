# tig-cli

Run [VICAR](https://github.com/nasa/VICAR) terrain-processing tools from your host shell,
executing them transparently inside the TIG container image. `tig-cli` handles container
lifecycle, X11 display forwarding, and host↔container path translation so VICAR commands
behave as if they ran locally.

## Requirements

- Python 3.9+
- A container runtime on `PATH`: Docker, Podman, nerdctl or Finch
- Access to a TIG VICAR image (defaults to the public open-source image)

Nothing here is Docker-specific: the container is created and commands are run
through the runtime's own command line, which these runtimes share. The first
one of `docker`, `podman`, `nerdctl`, `finch` that is installed is used;
`TIG_CONTAINER_RUNTIME` or the `runtime` config key names one instead, and may
name any other command that takes the same arguments.

## Installation

```bash
pip install tig-cli
```

Or from a checkout of this repository:

```bash
cd tig-cli
pip install -e .
```

## Usage

Invoke any VICAR tool by name, followed by its arguments:

```bash
tig <vicar_tool> [args...]
```

Examples:

```bash
# Run marsmap on a local file (relative paths work as-is)
tig marsmap input.vic output.vic

# VICAR keyword=value arguments work too; paths in them are translated
tig marsmap INP=/data/input.vic OUT=output.vic SIZE=(1,1,500,500)

# Absolute paths outside your home directory are translated automatically
tig label -list inp=/data/scenes/image.vic
```

### Options

| Option | Description |
| --- | --- |
| `--config PATH` | Load only this config file instead of the standard layered files. |
| `--writable-path PATH` | Mount an additional host directory read-write inside the container. May be repeated. |
| `--calibration-path PATH` | Host directory with MARS/VISOR calibration files. Defaults to `$MARS_CONFIG_PATH`. |
| `--disable-path-translation` | Disable automatic host→container path translation (debugging). |
| `--selinux-label-disable` / `--no-selinux-label-disable` | Force `--security-opt label=disable` on or off (Linux). Defaults to on when SELinux is Enforcing. |
| `--shim` | Write one command per VICAR tool into `~/.local/share/tig/shims`, then exit. See [Running tools unqualified](#running-tools-unqualified). |
| `--shim-dir PATH` | Write those commands somewhere else; implies `--shim`. |
| `--shim-force` | With `--shim`, also create commands whose names already exist on your `PATH`. |
| `--build [UNIT]` | Compile a VICAR program unit from local source and install it in the container, then exit. See [Building from source](#building-from-source). |
| `--build-unit NAME` | The unit to build; the same as the positional argument. Implies `--build`. |
| `--build-source PATH` | Build from this directory instead of the current one; implies `--build`. |
| `--build-image TAG` | Build an image (the runtime image plus one layer holding the program) instead of installing into the running container; implies `--build`. |
| `--builder-image IMAGE` | Image to compile in. Defaults to `terrain-intelligence-generator:opensource-builder`. |
| `--build-jobs N` | Parallel compile jobs. |
| `--build-list` | List the locally built programs installed over the image, then exit. |
| `--build-clean` | Forget them and remove this image's containers, which carry them, restoring the image's own programs. Containers another invocation is using are left alone. Scoped by `--build-unit`. |
| `--build-force` | Build even when the builder and runtime images are different VICAR releases. |
| `--status` | List the containers tig has created, with their writable mounts, then exit. |
| `--shutdown` | Remove the containers tig has created, then exit. |
| `--help` | Show help, including the active container image and the config files in use. |
| `--version` | Show the installed tig-cli version. |

Options must precede the tool name, so that everything after it reaches the VICAR
tool untouched:

```bash
tig --writable-path /data/results marsmap INP=/data/in.vic OUT=/data/results/out.vic
```

## Configuration

Settings can come from TOML config files, environment variables, or command-line
flags. Later sources override earlier ones:

1. system config — `/etc/tig/config.toml`
2. user config — `$XDG_CONFIG_HOME/tig/config.toml` (default `~/.config/tig/config.toml`)
3. project config — the nearest `tig.toml`, searching upwards from the current directory
4. environment variables
5. command-line flags

Each file only needs the keys it wants to change; unspecified keys keep the value
from the layer below. Setting `TIG_CONFIG` (or passing `--config`) skips the search
and loads only that file.

### Config file keys

```toml
# ~/.config/tig/config.toml or ./tig.toml
image = "ghcr.io/my-org/custom-vicar:latest"
builder_image = "terrain-intelligence-generator:opensource-builder"
runtime = "podman"
writable_paths = ["/data/scenes", "/scratch"]
calibration_path = "~/mars_calibration_m20"
disable_path_translation = false
selinux_label_disable = true
```

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `image` | string | `ghcr.io/nasa-ammos/tig/terrain-intelligence-generator:opensource` | VICAR container image to run. |
| `builder_image` | string | `terrain-intelligence-generator:opensource-builder` | Image `--build` compiles in. |
| `runtime` | string | auto | Container runtime command to use, e.g. `podman`. Unset means the first of `docker`, `podman`, `nerdctl`, `finch` on `PATH`. |
| `writable_paths` | list of strings | `[]` | Host directories mounted read-write in the container. |
| `calibration_path` | string | unset | Host directory with MARS/VISOR calibration files. Mounted read-only at `/usr/local/vicar/mars_calib`, and exported as `MARS_CONFIG_PATH` inside the container. `~` is expanded. |
| `disable_path_translation` | boolean | `false` | Disable host→container path translation. |
| `selinux_label_disable` | boolean | auto | Run the container with `--security-opt label=disable`. Unset means: enabled when SELinux is Enforcing, off otherwise. |

### Environment variables

| Variable | Overrides | Description |
| --- | --- | --- |
| `CONTAINER_IMAGE` | `image` | VICAR container image to run. |
| `TIG_BUILDER_IMAGE` | `builder_image` | Image `--build` compiles in. |
| `TIG_CONTAINER_RUNTIME` | `runtime` | Container runtime command to use, e.g. `podman`. |
| `TIG_WRITABLE_PATHS` | `writable_paths` | `:`-separated list of host directories to mount read-write. |
| `MARS_CONFIG_PATH` | `calibration_path` | Host directory with MARS/VISOR calibration files. |
| `TIG_DISABLE_PATH_TRANSLATION` | `disable_path_translation` | `1`/`true`/`yes`/`on` to disable path translation. |
| `TIG_SELINUX_LABEL_DISABLE` | `selinux_label_disable` | `1`/`true`/`yes`/`on` to force `label=disable`; `0`/`false` to force it off. |
| `TIG_CONFIG` | (all files) | Load only this config file instead of the layered files. |

```bash
export CONTAINER_IMAGE=ghcr.io/my-org/custom-vicar:latest
tig marsmap input.vic output.vic
```

## Running tools unqualified

`tig --shim` writes one small command per VICAR tool, so scripts and habits that
call the tools directly keep working:

```bash
tig --shim
export PATH="$HOME/.local/share/tig/shims:$PATH"

marsmap INP=input.vic OUT=output.vic   # same as: tig marsmap ...
```

The tool list comes from the image, so re-run `tig --shim` after switching
images; commands for tools that disappeared are removed. Pass
`--shim-dir PATH` to write them somewhere else, such as `~/bin`.

Names that already exist on your `PATH` — VICAR ships a `sort`, a `patch` and a
`size`, among others — are skipped and reported, so putting the directory first
on `PATH` cannot shadow your system commands. Reach those as `tig sort ...`, or
pass `--shim-force` if you want the VICAR ones to win.

## Building from source

`tig --build` compiles a VICAR program from your own source and makes it the
program tig runs, so a patch can be tested without a native VICAR install:

```bash
cd mars/src/prog/marsmesh     # your copy, with marsmesh.imake in it
tig --build                   # compile, then install it in the container
tig marsmesh INP=in.vic OUT=out.obj
```

From a source root, name the unit and tig finds it below:

```bash
cd ~/vicar-work               # holds mars/src/prog/marsxyz/marsxyz.imake
tig --build marsxyz
```

Compilation happens in a separate builder image, because the runtime image has
no compilers and none of the headers, imakefiles or external archives a build
needs. Build it once per machine (it is not published: the VICAR release
tarballs are yours to fetch):

```bash
terrain-intelligence-generator/build-builder-image.sh
```

What `--build` does: runs `vimake` and `make` on `<unit>.imake` in the builder,
with your source mounted read-only and objects kept in
`~/.local/share/tig/builds` rather than your source tree; copies the program —
and `<unit>.pdf`, its TAE parameter definition, when there is one — over the
image's own at `/usr/local/vicar/dev/{p2,p3,mars}/lib/x86-64-linx/<unit>`; adds
a `/usr/local/bin` wrapper for a program the image does not already have; and
checks it loads in the runtime container.

Installed programs live in the container, not the image, and tig replaces
containers routinely, so each build is recorded and re-applied automatically —
to a fresh container, and to any container that predates the build or still
carries an earlier one, whose next command takes the slow path once:

```bash
tig --build-list    # gen  /usr/local/vicar/dev/p2/lib/x86-64-linx/gen  built 2026-08-11T19:17:27Z  from ~/src/gen
tig --build-clean   # forget them; the image's own programs are back
```

For something shareable or reproducible — CI, Airflow, a colleague — build an
image instead. It is the runtime image plus one layer:

```bash
tig --build-image my-vicar:marsmesh-fix
CONTAINER_IMAGE=my-vicar:marsmesh-fix tig marsmesh INP=in.vic OUT=out.obj
```

Only `PROGRAM` units are supported: `SUBROUTINE` units build into VICAR's link
libraries, and changing one means rebuilding everything that links it. See
[Building from source](../docs/demos/building-from-source.md) for a worked
example.

## Container reuse

The container is created on first use and then reused, so a pipeline of many
VICAR commands starts one container instead of one per command:

```bash
tig --status     # tig-vicar-1783dae8b4c9  running  ghcr.io/.../opensource  writable: /home/you, /data/scenes
tig --shutdown   # Removed 1 container(s).
```

The container name is a digest of the image and mount configuration, so
changing `--writable-path`, `--calibration-path`, `CONTAINER_IMAGE` or the
directory you work from gets its own container rather than silently reusing one
that lacks the mount you asked for. Re-pulling a moving tag such as
`:opensource` also replaces the container instead of reusing the old image.

Because each such configuration gets its own container, tig keeps at most two:
whenever a container is created, older ones are removed, most recently started
first. A container is never removed while a command is running in it, so
concurrent `tig` invocations are safe, and reaping only happens on the (already
slow) create path, leaving warm command latency untouched. Use `tig --status`
to see which containers exist and what each has mounted read-write.

Interrupting a command (Ctrl-C, `SIGTERM`) stops that command and leaves the
container up for the next one; `tig --shutdown` removes it.

## Warm command latency

Once the container is up, `tig <tool> <args...>` runs on a warm path that costs
neither the runtime CLI's startup nor click's imports.
Commands are handed to a small shell runner already running in the container,
which starts them without a container exec and without the daemon being
involved at all. On Linux the two meet over FIFOs under `~/.cache/tig/`; on
macOS, where a bind-mounted FIFO does not cross into the runtime's virtual machine,
the runner instead dials back to a broker on the host (see below). On this
machine, against the `:opensource` image:

| per command | |
| --- | --- |
| `tig <tool>` | 27 ms |
| the runtime's `exec` in a sidecar container | 32-34 ms |
| `tig <tool>` without the in-container runner | 55 ms |
| `tig <tool>` before this path existed | 158 ms |

The runner is started in the background after a command that had to go the
slow way, so nothing waits for it, and it needs nothing of the image beyond
`/bin/sh` (`/bin/bash` for the broker's agent). Each command is run in a
process group of its own, so interrupting `tig` stops what the tool started
too. Because a command reaching it proves the container is running, the
runtime is asked whether the container still matches its image only every 30
seconds, after the command rather than before it; a container left behind by a
re-pulled tag is retired then and replaced on the next command.

The warm path needs the runtime's Docker-compatible API socket, which Docker
and Podman serve but containerd-backed runtimes such as nerdctl and Finch do
not; without one, every command goes through the runtime's command line.
Anything else the warm path does not recognise - options, a container that is
not running, an interactive terminal, a setup it cannot drive (TLS, an unknown
context) - falls back too, and behaves exactly as before. Set
`TIG_NO_DISPATCHER=1` to use the API socket directly and `TIG_NO_FAST_PATH=1`
to always take the full path.

### The macOS broker

Where FIFOs cannot be shared, `tig` starts a broker process on the host and an
agent in the container; the agent reaches the broker through the gateway name
its runtime gives the host, and each `tig` hands its own standard input, output
and error to the broker over a unix socket in `~/.cache/tig/`, which only the
invoking user can reach. A token in the agent's greeting means nothing else
on the machine can push commands into the container. If the broker or the
agent cannot be started, the command goes to the API socket as before. Set
`TIG_NO_BROKER=1` to leave it out, `TIG_BROKER=1` to use it where the FIFO
dispatcher would otherwise be preferred.

## MARS / VISOR calibration files

VICAR's MARS programs need mission calibration data, which is not in the image.
Point tig at it and it is mounted read-only and exported as `MARS_CONFIG_PATH`
inside the container:

```bash
export MARS_CONFIG_PATH=/data/mars_calibration_m20
tig marsmap INP=/data/in.vic OUT=out.vic
```

The same directory can be set once per machine or per project with the
`calibration_path` config key instead.

## How path translation works

- **Relative paths** are left unchanged.
- **Paths under your home directory** are mounted directly and left unchanged.
- **Other absolute paths** are prefixed with `/host` (the host root filesystem is
  mounted read-only at `/host` inside the container).
- **`keyword=value` arguments** have their value translated, including
  parenthesized lists: `INP=(/data/a.vic,/data/b.vic)`. Values that are not
  absolute paths (`SIZE=(1,1,500,500)`) are left alone.

## Where you can write

The host filesystem is mounted read-only, except for your home directory, the
directory you invoke `tig` from, and anything passed with `--writable-path`.
Writing anywhere else fails with `Read-only file system`.

On Linux the container runs as your own user and group, so output files are owned
by you rather than by root. Under rootless Podman, which maps you to a
subordinate uid instead, the container is run with `--userns=keep-id` for the
same reason.

## GUI tools and X11

GUI tools such as `xvd` and `marsmap` render on your host display. When tig
creates a container it first authorizes the display, so you do not have to:

- **Linux** — runs `xhost +local:`. The broad form is deliberate: with
  `label=disable` the container connects as the `LOCAL:` family, which
  `xhost +local:docker` does not cover. The container shares the host network
  and `/tmp/.X11-unix`, and `DISPLAY` is passed through per command.
- **macOS** — makes XQuartz listen on TCP
  (`defaults write org.xquartz.X11 nolisten_tcp -bool false`), starts it if it
  is not running, and runs `xhost +localhost`; the container uses
  `DISPLAY=host.docker.internal:0` (`host.containers.internal` under Podman,
  `host.lima.internal` under Finch).

This happens only when a container is created, not on every command, and is
skipped silently when there is no `DISPLAY` or no `xhost` (a headless host has
no display to authorize).

## SELinux (RHEL, Fedora, CentOS)

With SELinux in Enforcing mode a container is denied access to bind mounts and
to the host X11 socket, and VICAR tools that load 32-bit legacy shared
libraries fail with `cannot change memory protections`. tig detects Enforcing
mode at runtime (`getenforce`, falling back to `/sys/fs/selinux/enforce`) and
then runs the container with `--security-opt label=disable`.

That opts the container out of SELinux confinement instead of relabeling the
mounts: tig mounts the host root filesystem, and relabeling (`:z`/`:Z`) it
would rewrite labels across the whole host, which is not recoverable. Nothing
tig mounts is ever relabeled.

Override the detection with `--selinux-label-disable` /
`--no-selinux-label-disable`, `TIG_SELINUX_LABEL_DISABLE`, or the
`selinux_label_disable` config key. With it turned off on an Enforcing host,
tig prints a warning, since mounts and GUI tools will likely be denied.


## Development

```bash
cd tig-cli
pip install -e ".[dev]"

# Run unit tests
pytest -m "not integration"

# Run integration tests (requires a container runtime + a pullable TIG image)
pytest -m integration
```

## License

Apache-2.0. See [LICENSE](LICENSE).
