"""CLI entry point for the tig command."""
import sys
from pathlib import Path

import click

from .config import (
    ConfigError,
    Config,
    SYSTEM_CONFIG_PATH,
    PROJECT_CONFIG_NAME,
    env_disable_path_translation,
    env_writable_paths,
    load_config,
    user_config_path,
)
from .container import ContainerManager, get_container_image


class DynamicHelpCommand(click.Command):
    def format_help(self, ctx, formatter):
        try:
            config = load_config()
        except ConfigError:
            config = Config()
        image = get_container_image(config)
        sources = ", ".join(str(p) for p in config.sources) or "none found"
        self.help = (
            f"Execute a VICAR tool via Docker.\n\n"
            f"Active image: {image}\n\n"
            f"Set CONTAINER_IMAGE env var to override.\n\n"
            f"Config files (later overrides earlier): {SYSTEM_CONFIG_PATH}, "
            f"{user_config_path()}, nearest {PROJECT_CONFIG_NAME}.\n\n"
            f"Loaded config: {sources}"
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
    "--disable-path-translation",
    is_flag=True,
    default=False,
    help="Disable automatic host→container path translation (for debugging).",
)
def main(vicar_tool, args, config_path, writable_path, disable_path_translation):
    try:
        config = load_config(config_path)
    except ConfigError as e:
        raise click.ClickException(str(e))

    image = get_container_image(config)

    from_env = env_writable_paths()
    writable_paths = list(writable_path) if writable_path else (
        from_env if from_env is not None else config.writable_paths
    )

    if not disable_path_translation:
        try:
            override = env_disable_path_translation()
        except ConfigError as e:
            raise click.ClickException(str(e))
        if override is None:
            override = config.disable_path_translation
        disable_path_translation = bool(override)

    manager = ContainerManager(image, disable_path_translation=disable_path_translation)
    try:
        manager.start_container(writable_paths=writable_paths)
        exit_code = manager.execute_vicar_command(vicar_tool, list(args))
        sys.exit(exit_code)
    finally:
        manager.stop_container()
