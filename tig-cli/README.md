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

# VICAR keyword=value arguments are passed through unchanged
tig marsmap INP=input.vic OUT=output.vic SIZE=(1,1,500,500)

# Absolute paths outside your home directory are translated automatically
tig label /data/scenes/image.vic
```

### Options

| Option | Description |
| --- | --- |
| `--config PATH` | Load only this config file instead of the standard layered files. |
| `--writable-path PATH` | Mount an additional host directory read-write inside the container. May be repeated. |
| `--mars-config-path PATH` | Host directory of MARS calibration data to mount read-only. |
| `--disable-path-translation` | Disable automatic host→container path translation (debugging). |
| `--help` | Show help, including the active container image and the config files in use. |

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
mars_config_path = "~/.mars_calib"
disable_path_translation = false
```

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `image` | string | `ghcr.io/nasa-ammos/tig/terrain-intelligence-generator:opensource` | VICAR Docker image to run. |
| `writable_paths` | list of strings | `[]` | Host directories mounted read-write in the container. |
| `mars_config_path` | string | unset | Host directory of MARS calibration data (`camera_models/`, `flat_fields/`, `param_files/`). Mounted read-only at `/usr/local/vicar/mars_calib`, with `MARS_CONFIG_PATH` set to that mount point inside the container. `~` is expanded. |
| `disable_path_translation` | boolean | `false` | Disable host→container path translation. |

### Environment variables

| Variable | Overrides | Description |
| --- | --- | --- |
| `CONTAINER_IMAGE` | `image` | VICAR Docker image to run. |
| `TIG_WRITABLE_PATHS` | `writable_paths` | `:`-separated list of host directories to mount read-write. |
| `MARS_CONFIG_PATH` | `mars_config_path` | Host directory of MARS calibration data. Same variable the `vicar-native-toolkit` uses, so an activated toolkit environment is picked up automatically. |
| `TIG_DISABLE_PATH_TRANSLATION` | `disable_path_translation` | `1`/`true`/`yes`/`on` to disable path translation. |
| `TIG_CONFIG` | (all files) | Load only this config file instead of the layered files. |

```bash
export CONTAINER_IMAGE=ghcr.io/my-org/custom-vicar:latest
tig marsmap input.vic output.vic
```

## How path translation works

- **Relative paths** are left unchanged.
- **Paths under your home directory** are mounted directly and left unchanged.
- **Other absolute paths** are prefixed with `/host` (the host root filesystem is
  mounted read-only at `/host` inside the container).

## MARS calibration data

MARS tools (`marscorr`, `marsxyz`, `marsmap`, …) need mission calibration data —
camera models, flat fields and param files. Point `mars_config_path` (or
`MARS_CONFIG_PATH`) at the host directory containing `camera_models/`,
`flat_fields/` and `param_files/`:

```bash
tig --mars-config-path ~/.mars_calib marsxyz INP=left.vic,right.vic OUT=xyz.vic
```

It is mounted read-only at `/usr/local/vicar/mars_calib` and `MARS_CONFIG_PATH`
is exported inside the container to that path, matching the layout used by
`vicar-native-toolkit`. If the directory does not exist, `tig` warns and starts
without it (MARS tools that need camera models will then fail).

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
