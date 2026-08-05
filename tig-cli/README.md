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
| `--writable-path PATH` | Mount an additional host directory read-write inside the container. May be repeated. |
| `--calibration-path PATH` | Host directory with MARS/VISOR calibration files. Defaults to `$MARS_CONFIG_PATH`. |
| `--disable-path-translation` | Disable automatic host→container path translation (debugging). |
| `--status` | List the containers tig has created, then exit. |
| `--shutdown` | Remove the containers tig has created, then exit. |
| `--help` | Show help, including the currently active container image. |
| `--version` | Show the installed tig-cli version. |

Options must precede the tool name, so that everything after it reaches the VICAR
tool untouched:

```bash
tig --writable-path /data/results marsmap INP=/data/in.vic OUT=/data/results/out.vic
```

### Configuration

Set the `CONTAINER_IMAGE` environment variable to use a different VICAR image:

```bash
export CONTAINER_IMAGE=ghcr.io/my-org/custom-vicar:latest
tig marsmap input.vic output.vic
```

## Container reuse

The container is created on first use and then reused, so a pipeline of many
VICAR commands starts one container instead of one per command:

```bash
tig --status     # tig-vicar-1783dae8b4c9  running  ghcr.io/.../opensource
tig --shutdown   # Removed 1 container(s).
```

The container name is a digest of the image and mount configuration, so
changing `--writable-path`, `--calibration-path`, `CONTAINER_IMAGE` or the
directory you work from gets its own container rather than silently reusing one
that lacks the mount you asked for. Re-pulling a moving tag such as
`:opensource` also replaces the container instead of reusing the old image.

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
