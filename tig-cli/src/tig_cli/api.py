"""Run VICAR programs in the TIG container from Python.

The ``tig`` command hands a tool this process's own terminal; this module
captures what the tool writes and hands it back, together with the files it
produced::

    from tig_cli import Tig

    with Tig() as tig:
        result = tig.run("gen", "test.vic", 64, 64)
        result.stdout          # 'Beginning VICAR task GEN\\n...'
        result.outputs         # [PosixPath('/home/you/work/test.vic')]

        listing = tig.run("label", "-list", inp="test.vic").check()
        tig.run("marsxyz", inp=["left.vic", "right.vic"], out="xyz.vic")

Keyword arguments become VICAR ``keyword=value`` parameters; a list becomes
a parenthesized value list. Host paths are translated exactly as they are for
the command line, and the container writes straight onto the host filesystem,
so an output file is on the host as soon as the tool exits - nothing has to
be copied back.

The container is the same long-lived one the ``tig`` command uses, so a script
and a shell share it, and ``tig --shutdown`` removes it.
"""
from __future__ import annotations

import os
import signal
import subprocess
import uuid
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence, Union

from .config import Config, ConfigError, load_config
from .container import ContainerManager
from .runtime import Runtime
from .spec import (
    EXEC_ID_ENV,
    TigError,
    container_display,
    ensure_x11_ready,
    get_calibration_path,
    get_container_image,
    kill_tree_command,
    resolve_disable_path_translation,
    resolve_selinux_label_disable,
    resolve_writable_paths,
)

# What run() accepts as a positional argument or a keyword's value.
Value = Union[str, "os.PathLike[str]", int, float]
Param = Union[Value, Sequence[Value]]

# Seconds allowed for the in-container kill after a timeout.
KILL_TIMEOUT = 5.0


class VicarError(TigError):
    """A VICAR program exited non-zero when the caller asked for success."""

    def __init__(self, result: "Result"):
        detail = (result.stderr or result.stdout).strip()
        message = f"{result.tool} exited with status {result.exit_code}"
        if detail:
            message += f": {detail.splitlines()[-1].strip()}"
        super().__init__(message)
        self.result = result


class VicarTimeout(TigError):
    """A VICAR program was stopped for running longer than allowed."""

    def __init__(self, tool: str, timeout: float, stdout: str, stderr: str):
        super().__init__(f"{tool} did not finish within {timeout:g} seconds")
        self.tool = tool
        self.timeout = timeout
        self.stdout = stdout
        self.stderr = stderr


class Result:
    """What one VICAR program run produced.

    Attributes:
        tool: The program that was run.
        command: The full command line as run in the container, paths
            translated.
        exit_code: The program's exit status.
        stdout: Everything it wrote to standard output.
        stderr: Everything it wrote to standard error.
        outputs: Host paths named in the arguments that the run created or
            modified, in argument order. Files a tool writes under names of
            its own (``marsmesh``'s ``.mtl`` beside the ``.obj``, say) are
            not listed but are in the working directory all the same.
    """

    def __init__(
        self,
        tool: str,
        command: list[str],
        exit_code: int,
        stdout: str,
        stderr: str,
        outputs: list[Path],
    ):
        self.tool = tool
        self.command = command
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.outputs = outputs

    @property
    def ok(self) -> bool:
        """Whether the program exited with status 0."""
        return self.exit_code == 0

    @property
    def output(self) -> Path | None:
        """The single output file, when there is exactly one."""
        return self.outputs[0] if len(self.outputs) == 1 else None

    def check(self) -> "Result":
        """Return this result, or raise :class:`VicarError` if it failed."""
        if not self.ok:
            raise VicarError(self)
        return self

    def __bool__(self) -> bool:
        return self.ok

    def __repr__(self) -> str:
        return (
            f"Result(tool={self.tool!r}, exit_code={self.exit_code}, "
            f"outputs={[str(path) for path in self.outputs]!r})"
        )


class Tig:
    """A handle on the TIG container, for running VICAR programs.

    Settings default to what the ``tig`` command would use - the layered config
    files and the ``CONTAINER_IMAGE`` / ``MARS_CONFIG_PATH`` / ``TIG_*``
    environment variables - and each argument here overrides that one setting.

    Args:
        image: Container image to run.
        calibration_path: Host directory with MARS/VISOR calibration files.
        writable_paths: Host directories to mount read-write, besides the
            home directory and ``workdir``; replaces the configured ones.
        workdir: Where programs run, and what relative paths are relative
            to. Defaults to the current directory.
        config: A :class:`~tig_cli.config.Config`, or the path of one config
            file to load instead of the layered ones.
        disable_path_translation: Pass host paths through untranslated.
        selinux_label_disable: Run with ``--security-opt label=disable``.
        env: Environment variables set for every program run.
        runtime: Container runtime to drive, instead of finding one.

    A ``Tig`` holds a claim on its container while open, which keeps
    concurrent ``tig`` invocations from removing it; use it as a context
    manager or call :meth:`close`.
    """

    def __init__(
        self,
        image: str | None = None,
        *,
        calibration_path: str | os.PathLike[str] | None = None,
        writable_paths: Iterable[str | os.PathLike[str]] | None = None,
        workdir: str | os.PathLike[str] | None = None,
        config: Config | str | os.PathLike[str] | None = None,
        disable_path_translation: bool | None = None,
        selinux_label_disable: bool | None = None,
        env: Mapping[str, str] | None = None,
        runtime: Runtime | None = None,
    ):
        try:
            if config is None:
                config = load_config()
            elif not isinstance(config, Config):
                config = load_config(Path(config))
            if writable_paths is None:
                self.writable_paths = resolve_writable_paths(config)
            else:
                self.writable_paths = [os.fspath(path) for path in writable_paths]
            if disable_path_translation is None:
                translate_off = resolve_disable_path_translation(config)
            else:
                translate_off = disable_path_translation
            label_disable = resolve_selinux_label_disable(config, selinux_label_disable)
        except ConfigError as e:
            raise TigError(str(e)) from e

        self.config = config
        self.image = image or get_container_image(config)
        self.workdir = str(Path(os.fspath(workdir) if workdir else os.getcwd()).resolve())
        self.env = dict(env or {})
        # Programs write where they run, so the working directory has to be
        # writable like the one the CLI is invoked from.
        if self.workdir not in self.writable_paths:
            self.writable_paths.append(self.workdir)

        self.manager = ContainerManager(
            self.image,
            config=config,
            disable_path_translation=translate_off,
            calibration_path=(
                os.fspath(calibration_path)
                if calibration_path is not None
                else get_calibration_path(config)
            ),
            selinux_label_disable=label_disable,
            runtime=runtime,
        )

    def __enter__(self) -> "Tig":
        return self

    def __exit__(self, *exc_info) -> bool:
        self.close()
        return False

    def __repr__(self) -> str:
        return f"Tig(image={self.image!r}, workdir={self.workdir!r})"

    def __getattr__(self, name: str):
        """``tig.marsxyz(...)`` as a spelling of ``tig.run("marsxyz", ...)``."""
        if name.startswith("_"):
            raise AttributeError(name)

        def run_tool(*args: Param, **kwargs: Any) -> Result:
            return self.run(name, *args, **kwargs)

        run_tool.__name__ = name
        return run_tool

    @property
    def container_name(self) -> str:
        """The container programs run in, once one has been used."""
        return self.manager.container_name

    def start(self) -> "Tig":
        """Make sure the container is running, pulling the image if needed.

        :meth:`run` does this itself; calling it first only moves the
        (possibly slow) first-use cost to a moment of the caller's choosing.
        """
        self.manager.ensure_container(writable_paths=self.writable_paths)
        return self

    def close(self) -> None:
        """Release this handle's claim on the container, leaving it running."""
        self.manager.release_claim()

    def shutdown(self) -> int:
        """Remove the containers tig has created; returns how many."""
        return self.manager.shutdown()

    def tools(self) -> list[str]:
        """The names of the VICAR programs the image provides."""
        self.start()
        return self.manager.list_tools()

    def run(
        self,
        tool: str,
        *args: Param,
        stdin: str | bytes | None = None,
        timeout: float | None = None,
        check: bool = False,
        workdir: str | os.PathLike[str] | None = None,
        env: Mapping[str, str] | None = None,
        **params: Param,
    ) -> Result:
        """Run one VICAR program and wait for it.

        Args:
            tool: Program name, as on the ``tig`` command line.
            *args: Positional arguments. Paths may be given as ``Path``.
            stdin: Text or bytes fed to the program's standard input.
            timeout: Seconds to allow; the program is killed after that
                and :class:`VicarTimeout` raised.
            check: Raise :class:`VicarError` on a non-zero exit status.
                Off by default: VICAR programs often exit non-zero on
                success, so look at ``stdout`` before relying on it.
            workdir: Run in this directory instead of the handle's. It must
                be under the home directory or one of ``writable_paths``
                for the program to be able to write there.
            env: Environment variables for this run, over the handle's.
            **params: VICAR parameters, written ``keyword=value``. A list
                or tuple becomes ``keyword=(a,b)``.

        Returns:
            The program's :class:`Result`.
        """
        arguments = [_text(arg) for arg in args] + [
            f"{keyword}={_value(value)}" for keyword, value in params.items()
        ]
        host_workdir = str(Path(os.fspath(workdir)).resolve()) if workdir else self.workdir
        candidates = _named_paths(arguments, host_workdir)
        before = {path: _fingerprint(path) for path in candidates}

        self.start()
        ensure_x11_ready()

        manager = self.manager
        if manager.disable_path_translation:
            command = [tool, *arguments]
            container_workdir = host_workdir
        else:
            command = [tool, *manager.translator.translate_args(arguments)]
            container_workdir = manager.translator.get_container_cwd(host_workdir)

        exec_id = uuid.uuid4().hex
        environment = {
            "DISPLAY": container_display(manager.runtime.name),
            **self.env,
            **(env or {}),
            EXEC_ID_ENV: exec_id,
        }
        exec_args = ["exec", "-w", container_workdir]
        if stdin is not None:
            exec_args.append("-i")
        for key, value in environment.items():
            exec_args += ["-e", f"{key}={value}"]
        exec_args += [manager.container_name, *command]

        payload = stdin.encode() if isinstance(stdin, str) else stdin
        try:
            completed = subprocess.run(
                manager.runtime.args(*exec_args),
                input=payload,
                stdin=None if stdin is not None else subprocess.DEVNULL,
                capture_output=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as e:
            self._kill(exec_id, signal.SIGKILL)
            raise VicarTimeout(
                tool, timeout or 0.0, _decode(e.stdout), _decode(e.stderr)
            ) from None
        except OSError as e:
            raise TigError(f"Failed to run {manager.runtime.name}: {e}") from e
        except BaseException:
            # Ctrl-C or a cancelled thread: the client is gone, the tool is not.
            self._kill(exec_id, signal.SIGTERM)
            raise

        outputs = [
            path for path in candidates if _fingerprint(path) not in (None, before[path])
        ]
        result = Result(
            tool,
            command,
            completed.returncode,
            _decode(completed.stdout),
            _decode(completed.stderr),
            outputs,
        )
        return result.check() if check else result

    def _kill(self, exec_id: str, signum: int) -> None:
        """Signal a run's processes in the container after its client is gone."""
        try:
            subprocess.run(
                self.manager.runtime.args(
                    "exec", self.manager.container_name,
                    "sh", "-c", kill_tree_command(exec_id, signum),
                ),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=KILL_TIMEOUT,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            pass


def run(tool: str, *args: Param, **kwargs: Any) -> Result:
    """Run one VICAR program with default settings; see :meth:`Tig.run`."""
    with Tig() as tig:
        return tig.run(tool, *args, **kwargs)


def _text(value: Value) -> str:
    if isinstance(value, (str, int, float)):
        return str(value)
    return os.fspath(value)


def _value(value: Param) -> str:
    """A keyword's value as VICAR spells it; a sequence is a value list."""
    if isinstance(value, (list, tuple)):
        return "(" + ",".join(_text(item) for item in value) + ")"
    return _text(value)


def _named_paths(arguments: list[str], workdir: str) -> list[Path]:
    """Host paths the arguments may name, in order; flags and keywords aren't."""
    paths: list[Path] = []
    for argument in arguments:
        if argument.startswith("-"):
            continue
        value = argument
        if "=" in argument and not os.path.isabs(argument):
            value = argument.partition("=")[2]
        if value.startswith("(") and value.endswith(")"):
            items = value[1:-1].split(",")
        else:
            items = [value]
        for item in items:
            item = item.strip()
            if not item:
                continue
            path = Path(item) if os.path.isabs(item) else Path(workdir) / item
            if path not in paths:
                paths.append(path)
    return paths


def _fingerprint(path: Path) -> tuple[int, int] | None:
    """What tells one version of a file from the next; None if no file."""
    try:
        info = path.stat()
    except OSError:
        return None
    if not (info.st_mode & 0o170000) == 0o100000:
        return None
    return (info.st_mtime_ns, info.st_size)


def _decode(data: bytes | None) -> str:
    return (data or b"").decode("utf-8", errors="replace")
