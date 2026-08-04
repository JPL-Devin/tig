"""CLI entry point for the tig command."""
import sys

import click

from .container import ContainerManager, get_container_image


class DynamicHelpCommand(click.Command):
    def format_help(self, ctx, formatter):
        image = get_container_image()
        self.help = (
            f"Execute a VICAR tool via Docker.\n\n"
            f"Active image: {image}\n\n"
            f"Set CONTAINER_IMAGE env var to override."
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
@click.argument("vicar_tool")
@click.argument("args", nargs=-1, type=click.UNPROCESSED)
@click.option(
    "--writable-path",
    multiple=True,
    metavar="PATH",
    help="Additional host path to mount as read-write in the container.",
)
@click.option(
    "--disable-path-translation",
    is_flag=True,
    default=False,
    help="Disable automatic host→container path translation (for debugging).",
)
def main(vicar_tool, args, writable_path, disable_path_translation):
    image = get_container_image()
    manager = ContainerManager(image, disable_path_translation=disable_path_translation)
    try:
        manager.start_container(writable_paths=list(writable_path))
        exit_code = manager.execute_vicar_command(vicar_tool, list(args))
        sys.exit(exit_code)
    finally:
        manager.stop_container()
