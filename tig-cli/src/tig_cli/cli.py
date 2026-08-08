"""CLI entry point for the tig command."""
import signal
import sys
from pathlib import Path

import click

from .config import (
    Config,
    ConfigError,
    PROJECT_CONFIG_NAME,
    SYSTEM_CONFIG_PATH,
    env_disable_path_translation,
    env_selinux_label_disable,
    env_writable_paths,
    load_config,
    user_config_path,
)
from .container import (
    ContainerManager,
    TigError,
    get_calibration_path,
    get_container_image,
)
from .shim import default_shim_dir, tig_executable, write_shims


class DynamicHelpCommand(click.Command):
    def format_help(self, ctx, formatter):
        try:
            config = load_config()
        except ConfigError:
            config = Config()
        image = get_container_image(config)
        calibration = get_calibration_path(config)
        sources = ", ".join(str(p) for p in config.sources) or "none found"
        self.help = (
            f"Execute a VICAR tool via Docker.\n\n"
            f"Active image: {image}\n\n"
            f"Calibration path: {calibration or '(none)'}\n\n"
            f"Set CONTAINER_IMAGE or MARS_CONFIG_PATH to override.\n\n"
            f"Config files (later overrides earlier): {SYSTEM_CONFIG_PATH}, "
            f"{user_config_path()}, nearest {PROJECT_CONFIG_NAME}.\n\n"
            f"Loaded config: {sources}\n\n"
            f"The container is reused across invocations; "
            f"'tig --shutdown' removes it."
        )
        super().format_help(ctx, formatter)


@click.command(
    cls=DynamicHelpCommand,
    context_settings=dict(
        ignore_unknown_options=True,
        allow_extra_args=True,
        allow_interspersed_args=False,
    ),
)
@click.argument("vicar_tool", required=False)
@click.argument("args", nargs=-1, type=click.UNPROCESSED)
@click.option(
    "--config",
    "config_path",
    type=click.Path(path_type=Path),
    default=None,
    metavar="PATH",
    help="Load only this config file instead of the standard layered files.",
)
@click.option(
    "--writable-path",
    multiple=True,
    metavar="PATH",
    help="Additional host path to mount as read-write in the container.",
)
@click.option(
    "--calibration-path",
    metavar="PATH",
    default=None,
    help="Host path with MARS/VISOR calibration files "
         "(defaults to $MARS_CONFIG_PATH).",
)
@click.option(
    "--disable-path-translation",
    is_flag=True,
    default=False,
    help="Disable automatic host→container path translation (for debugging).",
)
@click.option(
    "--selinux-label-disable/--no-selinux-label-disable",
    "selinux_label_disable",
    default=None,
    help="Run the container with '--security-opt label=disable' (Linux). "
         "Defaults to enabled when SELinux is Enforcing.",
)
@click.option(
    "--shim",
    is_flag=True,
    default=False,
    help="Write one command per VICAR tool into a directory on PATH (default "
         "~/.local/share/tig/shims), so tools run unqualified, then exit.",
)
@click.option(
    "--shim-dir",
    "shim_dir",
    default=None,
    metavar="PATH",
    help="Where --shim writes its commands; implies --shim.",
)
@click.option(
    "--shim-force",
    is_flag=True,
    default=False,
    help="With --shim, also create commands whose names already exist on "
         "PATH (such as 'sort').",
)
@click.option(
    "--status",
    "show_status",
    is_flag=True,
    default=False,
    help="Show the reusable containers tig has created, then exit.",
)
@click.option(
    "--shutdown",
    is_flag=True,
    default=False,
    help="Remove the containers tig has created, then exit.",
)
@click.version_option(package_name="tig-cli")
def main(
    vicar_tool,
    args,
    config_path,
    writable_path,
    calibration_path,
    disable_path_translation,
    selinux_label_disable,
    shim,
    shim_dir,
    shim_force,
    show_status,
    shutdown,
):
    try:
        config = load_config(config_path)
        image = get_container_image(config)

        from_env = env_writable_paths()
        writable_paths = list(writable_path) if writable_path else (
            from_env if from_env is not None else config.writable_paths
        )

        if not disable_path_translation:
            override = env_disable_path_translation()
            if override is None:
                override = config.disable_path_translation
            disable_path_translation = bool(override)

        if selinux_label_disable is None:
            selinux_label_disable = env_selinux_label_disable()
        if selinux_label_disable is None:
            selinux_label_disable = config.selinux_label_disable
    except ConfigError as e:
        raise click.ClickException(str(e)) from e

    # --shim writes commands and exits, so anything else asked for in the same
    # invocation would be silently dropped. Checked before any container work.
    if shim or shim_dir:
        if vicar_tool:
            raise click.UsageError(
                f"--shim writes commands and exits; it cannot also run "
                f"'{vicar_tool}'."
            )
        for option, other in ((show_status, "--status"), (shutdown, "--shutdown")):
            if option:
                raise click.UsageError(
                    f"--shim writes commands and exits; run {other} separately."
                )

    lifecycle_only = shutdown or show_status
    try:
        manager = ContainerManager(
            image,
            disable_path_translation=disable_path_translation,
            # Not needed to list or remove containers, and validating it would
            # make --shutdown fail on a stale MARS_CONFIG_PATH.
            calibration_path=(
                None if lifecycle_only
                else calibration_path or get_calibration_path(config)
            ),
            selinux_label_disable=selinux_label_disable,
        )
    except TigError as e:
        raise click.ClickException(str(e)) from e

    if shutdown:
        removed = manager.shutdown()
        click.echo(f"Removed {removed} container(s).")
        return

    if shim or shim_dir:
        directory = Path(shim_dir) if shim_dir else default_shim_dir()
        try:
            manager.ensure_container(writable_paths=writable_paths)
            tools = manager.list_tools()
        except TigError as e:
            raise click.ClickException(str(e)) from e
        finally:
            manager.release_claim()

        written, skipped = write_shims(
            directory, tools, tig_executable(), force=shim_force
        )
        click.echo(f"Wrote {len(written)} command(s) to {directory}.")
        if skipped:
            click.echo(
                f"Skipped {len(skipped)} name(s) already taken "
                f"({', '.join(skipped)}); run these as 'tig <tool>'. "
                f"--shim-force covers names that merely exist elsewhere "
                f"on PATH, not files already in {directory}."
            )
        click.echo(f'Add to your shell profile: export PATH="{directory}:$PATH"')
        return

    if show_status:
        containers = manager.status()
        if not containers:
            click.echo("No tig containers running.")
        for container in containers:
            line = (
                f"{container['name']}  {container['status']}  {container['image']}"
            )
            if container["writable"]:
                line += f"  writable: {container['writable']}"
            click.echo(line)
        return

    if vicar_tool is None:
        raise click.UsageError("Missing argument 'VICAR_TOOL'.")

    def terminate(signum, frame):
        # The container is shared with other invocations, so it is left
        # running; 'tig --shutdown' removes it.
        manager.signal_command(signum)
        sys.exit(128 + signum)

    previous_handlers = {
        sig: signal.signal(sig, terminate)
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
    }

    try:
        manager.ensure_container(writable_paths=writable_paths)
        exit_code = manager.execute_vicar_command(vicar_tool, list(args))
        sys.exit(exit_code)
    except TigError as e:
        raise click.ClickException(str(e)) from e
    finally:
        manager.release_claim()
        for sig, handler in previous_handlers.items():
            signal.signal(sig, handler)
