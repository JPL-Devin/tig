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
from .spec import (
    resolve_disable_path_translation,
    resolve_selinux_label_disable,
    resolve_writable_paths,
)


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


def run_build(
    manager,
    config,
    image,
    writable_paths,
    unit_name=None,
    source=None,
    image_tag=None,
    builder_image=None,
    jobs=None,
    force=False,
):
    """Compile a VICAR unit and install it, into the container or a new image."""
    # Imported here so ordinary invocations never load the build machinery.
    from .build import (
        Builder,
        Overrides,
        build_image,
        find_unit,
        install,
        mark_name_applied,
        resolve_builder_image,
        verify_in_container,
    )

    unit = find_unit(Path(source) if source else Path.cwd(), unit_name)
    builder = Builder(
        resolve_builder_image(config.builder_image, builder_image),
        image,
        force=force,
        selinux_label_disable=bool(manager.selinux_label_disable),
    )
    builder.check_images()

    click.echo(
        f"Building {unit.name} from {unit.directory} in {builder.builder_image}"
    )
    artifact = builder.build(unit, jobs=jobs)
    pdf = unit.pdf

    if image_tag:
        build_image(image_tag, image, unit, artifact, pdf)
        click.echo(f"Built {image_tag}: {image} with {unit.container_path} replaced.")
        click.echo(f"Run it with: CONTAINER_IMAGE={image_tag} tig {unit.name} ...")
        return

    manager.ensure_container(writable_paths=writable_paths)
    install(manager.container_name, unit, artifact, pdf)
    overrides = Overrides(manager.container.image.id)
    overrides.record(unit, artifact, pdf)
    # This container now carries what was just recorded, so the next
    # invocation has nothing to re-apply.
    overrides.mark_applied(manager.container.id)
    mark_name_applied(manager.container_name)

    installed = unit.container_path + (" (+ .pdf)" if pdf else "")
    click.echo(f"Installed {installed} in {manager.container_name}")
    failure = verify_in_container(manager.container_name, unit.name)
    if failure:
        click.echo(
            f"Warning: {unit.name} did not run in the container: {failure}",
            err=True,
        )
    click.echo(f"Run it with: tig {unit.name} ...")


def run_build_state(manager, image, unit_name=None, clean=False):
    """List or discard the locally built programs installed over the image."""
    from .build import Overrides, forget_stale, image_id, stale_units

    identifier = image_id(image)
    overrides = Overrides(identifier)

    if clean:
        dropped = overrides.forget(unit_name)
        if not unit_name:
            dropped += forget_stale(identifier)
        if not dropped:
            click.echo(
                f"No locally built {unit_name} to clean."
                if unit_name
                else "No locally built programs to clean."
            )
            return
        # The image's own program is only back in a container created afresh:
        # the injected copy overwrote it in the container's filesystem.
        removed = manager.shutdown()
        click.echo(
            f"Forgot {', '.join(dropped)} and removed {removed} container(s); "
            f"the image's own programs are back."
        )
        return

    units = overrides.load()
    if not units:
        click.echo(f"No locally built programs installed over {image}.")
    for name, entry in sorted(units.items()):
        click.echo(
            f"{name}  {entry.get('path', '?')}  built {entry.get('built_at', '?')}"
            f"  from {entry.get('source', '?')}"
        )
    stale = stale_units(identifier)
    if stale:
        click.echo(
            f"Stale, built against an image no longer in use "
            f"({', '.join(stale)}); rebuild them or run --build-clean."
        )


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
    "--build",
    "build",
    is_flag=True,
    default=False,
    help="Compile the VICAR program unit in the current directory in the "
         "builder image, install it in the container, then exit.",
)
@click.option(
    "--build-unit",
    "build_unit",
    default=None,
    metavar="NAME",
    help="Unit to build: its directory, or a source root above it, may be the "
         "current directory. Implies --build. Also the first positional "
         "argument, as in 'tig --build marsmesh'.",
)
@click.option(
    "--build-source",
    "build_source",
    default=None,
    metavar="PATH",
    help="Directory to build from instead of the current one; implies --build.",
)
@click.option(
    "--build-image",
    "build_image_tag",
    default=None,
    metavar="TAG",
    help="Instead of installing into the running container, build this image: "
         "the runtime image plus one layer holding the program. Implies --build.",
)
@click.option(
    "--builder-image",
    "builder_image",
    default=None,
    metavar="IMAGE",
    help="Image to compile in (default terrain-intelligence-generator:"
         "opensource-builder, built by build-builder-image.sh).",
)
@click.option(
    "--build-jobs",
    "build_jobs",
    type=int,
    default=None,
    metavar="N",
    help="Parallel compile jobs for --build.",
)
@click.option(
    "--build-list",
    "build_list",
    is_flag=True,
    default=False,
    help="List the locally built programs installed over the image, then exit.",
)
@click.option(
    "--build-clean",
    "build_clean",
    is_flag=True,
    default=False,
    help="Forget locally built programs and remove the containers carrying "
         "them, restoring the image's own. Scoped by --build-unit.",
)
@click.option(
    "--build-force",
    "build_force",
    is_flag=True,
    default=False,
    help="With --build, proceed even when the builder and runtime images are "
         "different VICAR releases.",
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
    build,
    build_unit,
    build_source,
    build_image_tag,
    builder_image,
    build_jobs,
    build_list,
    build_clean,
    build_force,
    show_status,
    shutdown,
):
    try:
        config = load_config(config_path)
        image = get_container_image(config)

        writable_paths = resolve_writable_paths(config, list(writable_path))
        disable_path_translation = resolve_disable_path_translation(
            config, disable_path_translation
        )
        selinux_label_disable = resolve_selinux_label_disable(
            config, selinux_label_disable
        )
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

    building = bool(
        build or build_unit or build_source or build_image_tag or build_jobs
    )
    if building or build_list or build_clean:
        # The first positional argument is normally a VICAR tool, so with
        # --build it is read as the unit to build: 'tig --build marsmesh'.
        if vicar_tool and not build_unit:
            build_unit, vicar_tool = vicar_tool, None
        if vicar_tool or args:
            raise click.UsageError(
                f"--build compiles and exits; it cannot also run "
                f"'{vicar_tool or args[0]}'."
            )
        for option, other in (
            (shim or shim_dir, "--shim"),
            (show_status, "--status"),
            (shutdown, "--shutdown"),
        ):
            if option:
                raise click.UsageError(
                    f"--build compiles and exits; run {other} separately."
                )

    lifecycle_only = shutdown or show_status or build_list or build_clean
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

        try:
            written, skipped = write_shims(
                directory, tools, tig_executable(), force=shim_force
            )
        except OSError as e:
            raise click.ClickException(
                f"Failed to write commands to {directory}: {e}"
            ) from e
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

    if build_list or build_clean:
        try:
            run_build_state(manager, image, build_unit, clean=build_clean)
        except TigError as e:
            raise click.ClickException(str(e)) from e
        return

    if building:
        try:
            run_build(
                manager,
                config,
                image,
                writable_paths,
                unit_name=build_unit,
                source=build_source,
                image_tag=build_image_tag,
                builder_image=builder_image,
                jobs=build_jobs,
                force=build_force,
            )
        except TigError as e:
            raise click.ClickException(str(e)) from e
        finally:
            manager.release_claim()
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
