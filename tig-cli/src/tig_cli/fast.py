"""Warm path: run a VICAR command in an already-running container.

This is the case that dominates a terrain pipeline - the container exists, so
all that is needed is one exec in it. Getting there without importing click or
the Docker SDK, and without spawning the ``docker`` CLI, is what keeps a tig
command in the tens of milliseconds.

Whenever anything is not exactly as expected - no container yet, a different
image, an unusual Docker setup, options that need real parsing - :func:`run`
returns ``None`` and the full :mod:`tig_cli.cli` path takes over. It never
returns ``None`` after a command has started.
"""
from __future__ import annotations

import os
import sys
import termios
import tty

from .config import ConfigError, load_config
from .engine import Engine, EngineError
from .path_translator import PathTranslator
from .spec import (
    Claim,
    TigError,
    build_run_kwargs,
    build_volume_mounts,
    container_display,
    container_name_for,
    get_calibration_path,
    get_container_image,
    home_directory,
    resolve_calibration_path,
    resolve_disable_path_translation,
    resolve_selinux_label_disable,
    resolve_writable_paths,
    selinux_enforcing,
)

DISABLE_ENV = "TIG_NO_FAST_PATH"


def run(argv: list[str]) -> int | None:
    """Run ``argv`` in an existing container.

    Args:
        argv: Arguments after the program name.

    Returns:
        The command's exit code, or ``None`` if this path does not apply and
        the caller should fall through to the click CLI.
    """
    if os.environ.get(DISABLE_ENV):
        return None
    # Options are only accepted before the tool name (the click command sets
    # allow_interspersed_args=False), so a leading non-option means the whole
    # command line is 'tool arg...' and needs no option parsing.
    if not argv or argv[0].startswith("-"):
        return None

    try:
        container, command, workdir = _plan(argv[0], argv[1:])
    except (ConfigError, TigError, EngineError, OSError):
        return None
    if container is None:
        return None

    claim = Claim()
    claim.acquire(container.name)
    try:
        return _exec(container, command, workdir)
    finally:
        claim.release()


class _Target:
    """A container that is ready to run commands, and how to reach it."""

    def __init__(self, engine: Engine, name: str):
        self.engine = engine
        self.name = name


def _plan(tool: str, args: list[str]):
    """Work out which container and command line to use.

    Returns a ``(target, command, workdir)`` triple, with a ``None`` target
    when no suitable running container exists.
    """
    config = load_config()
    image = get_container_image(config)
    calibration_path = resolve_calibration_path(get_calibration_path(config))
    label_disable = resolve_selinux_label_disable(config)
    if label_disable is None:
        label_disable = selinux_enforcing()

    home = home_directory()
    volumes = build_volume_mounts(
        home, resolve_writable_paths(config), calibration_path
    )
    run_kwargs = build_run_kwargs(
        image, volumes, home, calibration_path, label_disable
    )
    name = container_name_for(run_kwargs)

    engine = Engine.detect()
    if not _is_current(engine, name, image):
        return None, None, None

    if resolve_disable_path_translation(config):
        return _Target(engine, name), [tool, *args], os.getcwd()

    translator = PathTranslator(home)
    return (
        _Target(engine, name),
        [tool, *translator.translate_args(args)],
        translator.get_container_cwd(os.getcwd()),
    )


def _is_current(engine: Engine, name: str, image: str) -> bool:
    """Whether ``name`` is running and built from today's ``image``.

    The image is re-checked so that a re-pulled moving tag such as
    :opensource takes effect instead of being served by the old container.
    """
    container = engine.inspect_container(name)
    if not ((container.get("State") or {}).get("Running")):
        return False
    return container.get("Image") == engine.inspect_image(image).get("Id")


def _exec(target: _Target, command: list[str], workdir: str) -> int:
    """Run ``command`` in the container, wired to this process's stdio."""
    interactive = sys.stdin.isatty() and sys.stdout.isatty()
    # Allocate a TTY only for interactive use; with a TTY, Docker merges
    # stderr into stdout and mangles redirected output.
    with _raw_terminal(interactive):
        return target.engine.exec_command(
            target.name,
            command,
            workdir,
            {"DISPLAY": container_display()},
            tty=interactive,
        )


class _raw_terminal:
    """Put the terminal in raw mode, as an interactive exec expects.

    Keystrokes then reach the container instead of being line-buffered and
    interpreted by the host terminal - the same thing ``docker exec -t`` does.
    """

    def __init__(self, enabled: bool):
        self.enabled = enabled
        self.fd = None
        self.saved = None

    def __enter__(self):
        if not self.enabled:
            return self
        try:
            self.fd = sys.stdin.fileno()
            self.saved = termios.tcgetattr(self.fd)
            tty.setraw(self.fd)
        except (termios.error, ValueError, OSError):
            self.saved = None
        return self

    def __exit__(self, *exc_info):
        if self.saved is not None:
            try:
                termios.tcsetattr(self.fd, termios.TCSADRAIN, self.saved)
            except (termios.error, OSError):
                pass
        return False
