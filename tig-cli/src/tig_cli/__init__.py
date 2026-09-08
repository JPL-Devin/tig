"""TIG CLI — run VICAR commands via Docker.

Configure the tool with layered TOML config files (system, user, project),
the CONTAINER_IMAGE / TIG_* environment variables, or command-line flags.

From Python, ``from tig_cli import Tig`` runs VICAR programs in the same
container; see :mod:`tig_cli.api`.
"""

__version__ = "0.2.0"

__all__ = ["Tig", "Result", "VicarError", "VicarTimeout", "run", "__version__"]


def __getattr__(name: str):
    # Imported on demand so the command-line fast path stays light.
    if name in __all__:
        from . import api

        return getattr(api, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
