# TIG CLI Unified Design

**Date:** 2026-07-31  
**Branch:** feature/tig-cli-unified  
**Status:** Approved

## Summary

Replace three pip packages (`tig-cli-core`, `tig-opensource`, `tig-m20-g87`) with a single `tig-cli` package that installs one command: `tig`. The Docker image used as the backend is selected via the `CONTAINER_IMAGE` environment variable; default is the opensource image.

## Motivation

The original design had separate packages per variant (one for open-source, one for M20 G87). These were never published. A single package with env-var image selection is simpler to install, simpler to document, and sufficient for all use cases.

## Package Structure

| Old | New |
|-----|-----|
| `tig-cli-core/` | deleted |
| `tig-opensource/` | deleted |
| `tig-m20-g87/` | deleted |
| *(new)* | `tig-cli/` |

### `tig-cli/`

```
tig-cli/
├── pyproject.toml          # package name: tig-cli, entrypoint: tig = tig_cli.cli:main
├── MANIFEST.in
├── README.md
└── src/
    └── tig_cli/
        ├── __init__.py
        ├── container.py    # ContainerManager + get_container_image()
        ├── path_translator.py  # unchanged
        └── cli.py          # single main() function
tests/
├── test_path_translator.py
├── test_container.py
├── test_cli.py
└── integration/
    └── test_vicar_execution.py
```

## CLI Interface

```
tig <vicar-tool> [args...]
```

**Options:**
- `--writable-path PATH` — mount additional host dir read-write inside container (repeatable)
- `--disable-path-translation` — skip automatic host→container path rewriting

**Help text** shows the active image (resolved from `CONTAINER_IMAGE` or default).

**No `--variant` or `--image` flag.** Image selection is env-var only.

## Image Configuration

```python
DEFAULT_IMAGE = "ghcr.io/nasa-ammos/tig/terrain-intelligence-generator:opensource"

def get_container_image() -> str:
    return os.environ.get("CONTAINER_IMAGE", DEFAULT_IMAGE)
```

`CONTAINER_IMAGE` accepts any valid Docker image reference (full URI including registry, repo, and tag). No short-name resolution — users set the full image string.

**Examples:**
```bash
# default (opensource)
tig marsmap ...

# proprietary variant
CONTAINER_IMAGE=ghcr.io/nasa-ammos/tig/terrain-intelligence-generator:m20-g87 tig marsmap ...

# custom/local image
CONTAINER_IMAGE=my-org/custom-vicar:v2 tig marsmap ...
```

## Code Changes

### `container.py`

- `ContainerManager.__init__` signature changes from `(variant: VariantConfig, ...)` to `(image: str, ...)`
- Add `get_container_image() -> str` module-level function (reads `CONTAINER_IMAGE` env var)
- Container name prefix: fixed string `tig-vicar` (no variant-derived name)
- All other logic (mounts, lifecycle, exec, path translation) unchanged

### `cli.py`

- Remove `create_cli(variant_name)` factory pattern
- Single `main()` function decorated with `@click.command`
- Calls `get_container_image()` at invocation time
- Help text includes: `f"Active image: {get_container_image()}"`

### `variants.py`

- Deleted entirely. `VariantConfig` dataclass and `VARIANTS` registry removed.

### `__init__.py`

- Remove any variant-related imports

## Testing

- **Delete** `test_variants.py`
- **Update** `test_container.py`: replace `VariantConfig` fixtures with direct `image` string; mock `CONTAINER_IMAGE` env var via `monkeypatch.setenv`
- **Update** `test_cli.py`: test `main()` directly; test env var override; test default image
- `test_path_translator.py` — unchanged
- Integration tests — unchanged

## CI / Publishing

- `.github/workflows/test.yml` — update paths to `tig-cli/`
- `.github/workflows/publish.yml` — update to publish single `tig-cli` package
- PyPI package name: `tig-cli`
- Entrypoint command: `tig`

## What Is Not Changing

- Path translation logic (`path_translator.py`) — unchanged
- Container mount strategy (root ro + home rw + writable paths) — unchanged
- `--writable-path` and `--disable-path-translation` flags — unchanged
- Container lifecycle (ephemeral: start → exec → stop per invocation) — unchanged
