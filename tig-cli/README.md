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
| `--writable-path PATH` | Mount an additional host directory read-write inside the container. May be repeated. |
| `--disable-path-translation` | Disable automatic host→container path translation (debugging). |
| `--help` | Show help, including the currently active container image. |

### Configuration

Set the `CONTAINER_IMAGE` environment variable to use a different VICAR image:

```bash
export CONTAINER_IMAGE=ghcr.io/my-org/custom-vicar:latest
tig marsmap input.vic output.vic
```

## How path translation works

- **Relative paths** are left unchanged.
- **Paths under your home directory** are mounted directly and left unchanged.
- **Other absolute paths** are prefixed with `/host` (the host root filesystem is
  mounted read-only at `/host` inside the container).

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
