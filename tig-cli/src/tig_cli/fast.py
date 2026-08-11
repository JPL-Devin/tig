"""Warm path: run a VICAR command in an already-running container.

This is the case that dominates a terrain pipeline - the container exists, so
all that is needed is to start one command in it. Getting there without
importing click or the Docker SDK, without spawning the ``docker`` CLI and,
where the dispatcher can be used, without talking to the Docker daemon at all,
is what keeps a tig command in the tens of milliseconds.

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
import uuid

from . import broker, dispatch
from .config import ConfigError, load_config
from .path_translator import PathTranslator
from .spec import (
    EXEC_ID_ENV,
    Claim,
    TigError,
    build_run_kwargs,
    build_volume_mounts,
    container_carries_builds,
    container_display,
    container_name_for,
    forwarded_signals,
    get_calibration_path,
    get_container_image,
    home_directory,
    kill_tree_command,
    resolve_calibration_path,
    resolve_disable_path_translation,
    resolve_selinux_label_disable,
    resolve_writable_paths,
    selinux_enforcing,
)

DISABLE_ENV = "TIG_NO_FAST_PATH"

# How long the dispatcher is trusted to be running the current image before
# that is checked with the daemon again. The check happens after a command,
# so a re-pulled tag is picked up on the next one rather than costing every
# command a daemon round trip.
REVALIDATE_INTERVAL = 30.0


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
        plan = _plan(argv[0], argv[1:])
    except (ConfigError, TigError, OSError):
        return None

    # A container predating a --build, or a rebuild, still runs the image's own
    # program; the full path installs the recorded build into it first.
    if not container_carries_builds(plan.name):
        return None

    claim = Claim()
    claim.acquire(plan.name)
    try:
        return _exec(plan)
    finally:
        claim.release()


class _Plan:
    """Everything needed to run one command in the container."""

    def __init__(
        self,
        name: str,
        image: str,
        home: str,
        command: list[str],
        workdir: str,
    ):
        self.name = name
        self.image = image
        self.home = home
        self.command = command
        self.workdir = workdir
        self.env = {"DISPLAY": container_display()}


def _plan(tool: str, args: list[str]) -> _Plan:
    """Work out which container to use and what to run in it.

    Purely local: the same inputs :mod:`tig_cli.container` creates a
    container from also name it, so which container to address is known
    without asking the daemon anything.
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

    if resolve_disable_path_translation(config):
        return _Plan(name, image, home, [tool, *args], os.getcwd())

    translator = PathTranslator(home)
    return _Plan(
        name,
        image,
        home,
        [tool, *translator.translate_args(args)],
        translator.get_container_cwd(os.getcwd()),
    )


def _exec(plan: _Plan) -> int | None:
    """Run the planned command, wired to this process's stdio."""
    interactive = sys.stdin.isatty() and sys.stdout.isatty()

    if not interactive:
        # Both warm transports have the command forked by a shell already
        # running in the container, which costs far less than an exec - and
        # a command getting there proves the container is running, so
        # nothing has to be asked of the daemon. Neither can give the
        # command a terminal, so interactive use skips them.
        code = _warm(plan)
        if code is not None:
            _revalidate(plan)
            return code

    from .engine import Engine, EngineError

    try:
        engine = Engine.detect()
        if not _is_current(engine, plan.name, plan.image):
            return None
    except (EngineError, OSError):
        return None

    # Docker does not pass signals on to an exec, and the container is
    # shared, so an interrupted tig would leave the tool running; the id
    # every descendant inherits is how they are found again.
    exec_id = uuid.uuid4().hex
    env = {**plan.env, EXEC_ID_ENV: exec_id}

    # Allocate a TTY only for interactive use; with a TTY, Docker merges
    # stderr into stdout and mangles redirected output.
    with _raw_terminal(interactive), forwarded_signals(
        lambda signum: _signal_in_container(engine, plan.name, exec_id, signum)
    ):
        code = engine.exec_command(
            plan.name, plan.command, plan.workdir, env, tty=interactive
        )
    _prepare_warm_path(engine, plan)
    return code


def _signal_in_container(engine, container: str, exec_id: str, signum: int) -> None:
    """Signal this invocation's processes inside the shared container."""
    from .engine import EngineError

    try:
        engine.exec_detached(
            container, ["/bin/sh", "-c", kill_tree_command(exec_id, signum)]
        )
    except (EngineError, OSError):
        pass


def _warm(plan: _Plan) -> int | None:
    """Run the command through whichever in-container runner can serve it."""
    if broker.preferred():
        return broker.run(
            plan.name, plan.home, plan.command, plan.workdir, plan.env
        )
    return dispatch.run(
        plan.name, plan.home, plan.command, plan.workdir, plan.env
    )


def _prepare_warm_path(engine, plan: _Plan) -> None:
    """Get the in-container runner up, now that the command is done."""
    if broker.preferred():
        broker.ensure_running(engine, plan.name, plan.home)
        return
    dispatch.ensure_running(engine, plan.name, plan.home)


def _is_current(engine, name: str, image: str) -> bool:
    """Whether ``name`` is running and built from today's ``image``.

    The image is re-checked so that a re-pulled moving tag such as
    :opensource takes effect instead of being served by the old container.
    """
    container = engine.inspect_container(name)
    if not ((container.get("State") or {}).get("Running")):
        return False
    return container.get("Image") == engine.inspect_image(image).get("Id")


def _revalidate(plan: _Plan) -> None:
    """Check now and then that the dispatcher's container is still right.

    Run after the command, so this costs nothing in the common case and a
    daemon round trip once every :data:`REVALIDATE_INTERVAL`. A container
    left behind by a re-pulled tag is retired by taking its dispatcher out
    of service, which sends the next command down the full path.
    """
    if dispatch.recently_verified(plan.home, plan.name, REVALIDATE_INTERVAL):
        return

    from .engine import Engine, EngineError

    try:
        if _is_current(Engine.detect(), plan.name, plan.image):
            dispatch.mark_verified(plan.home, plan.name)
            return
    except (EngineError, OSError):
        return
    dispatch.retire(plan.home, plan.name)
    broker.retire(plan.home, plan.name)


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
