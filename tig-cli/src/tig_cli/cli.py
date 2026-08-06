"""CLI entry point for the tig command."""
import signal
import sys

import click

from .container import (
    ContainerManager,
    TigError,
    get_calibration_path,
    get_container_image,
)


class DynamicHelpCommand(click.Command):
    def format_help(self, ctx, formatter):
        image = get_container_image()
        calibration = get_calibration_path()
        self.help = (
            f"Execute a VICAR tool via Docker.\n\n"
            f"Active image: {image}\n\n"
            f"Calibration path: {calibration or '(none)'}\n\n"
            f"Set CONTAINER_IMAGE or MARS_CONFIG_PATH to override.\n\n"
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
    writable_path,
    calibration_path,
    disable_path_translation,
    show_status,
    shutdown,
):
    image = get_container_image()
    lifecycle_only = shutdown or show_status
    try:
        manager = ContainerManager(
            image,
            disable_path_translation=disable_path_translation,
            # Not needed to list or remove containers, and validating it would
            # make --shutdown fail on a stale MARS_CONFIG_PATH.
            calibration_path=(
                None if lifecycle_only
                else calibration_path or get_calibration_path()
            ),
        )
    except TigError as e:
        raise click.ClickException(str(e)) from e

    if shutdown:
        removed = manager.shutdown()
        click.echo(f"Removed {removed} container(s).")
        return

    if show_status:
        containers = manager.status()
        if not containers:
            click.echo("No tig containers running.")
        for container in containers:
            click.echo(
                f"{container['name']}  {container['status']}  {container['image']}"
            )
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
        manager.ensure_container(writable_paths=list(writable_path))
        exit_code = manager.execute_vicar_command(vicar_tool, list(args))
        sys.exit(exit_code)
    except TigError as e:
        raise click.ClickException(str(e)) from e
    finally:
        for sig, handler in previous_handlers.items():
            signal.signal(sig, handler)
