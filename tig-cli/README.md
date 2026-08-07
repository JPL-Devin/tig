# tig-cli

Run [VICAR](https://github.com/nasa/VICAR) terrain-processing tools from your host shell,
executing them transparently inside the TIG Docker image. `tig-cli` handles container
lifecycle, X11 display forwarding, and host↔container path translation so VICAR commands
behave as if they ran locally.

## Requirements

- Python 3.9+
- A running Docker daemon
- Access to a TIG VICAR image (defaults to the public open-source image)

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
tig label /data/scenes/image.vic
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
writable_paths = ["/data/scenes", "/scratch"]
calibration_path = "~/mars_calibration_m20"
disable_path_translation = false
selinux_label_disable = true
```

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `image` | string | `ghcr.io/nasa-ammos/tig/terrain-intelligence-generator:opensource` | VICAR Docker image to run. |
| `writable_paths` | list of strings | `[]` | Host directories mounted read-write in the container. |
| `calibration_path` | string | unset | Host directory with MARS/VISOR calibration files. Mounted read-only at `/usr/local/vicar/mars_calib`, and exported as `MARS_CONFIG_PATH` inside the container. `~` is expanded. |
| `disable_path_translation` | boolean | `false` | Disable host→container path translation. |
| `selinux_label_disable` | boolean | auto | Run the container with `--security-opt label=disable`. Unset means: enabled when SELinux is Enforcing, off otherwise. |

### Environment variables

| Variable | Overrides | Description |
| --- | --- | --- |
| `CONTAINER_IMAGE` | `image` | VICAR Docker image to run. |
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
by you rather than by root.

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
  `DISPLAY=host.docker.internal:0`.

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

# Run integration tests (requires Docker + a pullable TIG image)
pytest -m integration
```

## License

Apache-2.0. See [LICENSE](LICENSE).
