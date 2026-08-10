"""Path translation for host-to-container path mapping."""
from __future__ import annotations

import os
from pathlib import Path

KEYWORD_EXTRA_CHARACTERS = "_.-"


def is_keyword(text: str) -> bool:
    """Whether ``text`` looks like a VICAR parameter name.

    That is ``INP``, optionally written as a CLI-style flag (``--inp``).
    Spelled out rather than matched with a regular expression, which would
    cost more to import than every path this module translates.
    """
    name = text.lstrip("-")
    if len(text) - len(name) > 2 or not name:
        return False
    if not (name[0].isascii() and (name[0].isalpha() or name[0] == "_")):
        return False
    return all(
        character.isascii()
        and (character.isalnum() or character in KEYWORD_EXTRA_CHARACTERS)
        for character in name[1:]
    )


class PathTranslator:
    """Translates host paths to container paths.

    Handles the mapping between host filesystem paths and their
    corresponding paths inside the container:
    - Relative paths: unchanged
    - Home directory paths: unchanged (mounted directly)
    - Other absolute paths: prefixed with /host
    """

    def __init__(self, home: str):
        """Initialize the path translator.

        Args:
            home: Home directory path (typically os.environ["HOME"])
        """
        self.home = Path(home).resolve()

    def translate(self, path: str) -> str:
        """Translate a single path from host to container.

        Args:
            path: Host filesystem path

        Returns:
            Container filesystem path
        """
        if not path:
            return path

        # Relative path - no translation
        if not os.path.isabs(path):
            return path

        resolved = Path(path).resolve()

        # Home directory - mounted directly at same path
        if resolved.is_relative_to(self.home):
            return str(resolved)

        # Absolute path outside home - add /host prefix
        return f"/host{resolved}"

    def translate_arg(self, arg: str) -> str:
        """Translate a single command argument.

        Besides bare paths, this understands VICAR's keyword syntax
        (``INP=/data/img.vic``) and parenthesized value lists
        (``INP=(/data/a.vic,/data/b.vic)``). Values that are not absolute
        paths - flags, numbers, sizes such as ``SIZE=(1,1,500,500)`` - are
        left alone, since ``translate`` only rewrites absolute paths.

        Args:
            arg: Command argument, possibly containing paths

        Returns:
            Argument with any absolute paths translated
        """
        if os.path.isabs(arg) or "=" not in arg:
            return self.translate(arg)

        keyword, sep, value = arg.partition("=")
        if not is_keyword(keyword):
            return self.translate(arg)

        return f"{keyword}{sep}{self._translate_value(value)}"

    def _translate_value(self, value: str) -> str:
        """Translate the value side of a ``keyword=value`` argument."""
        if value.startswith("(") and value.endswith(")"):
            items = value[1:-1].split(",")
            return "(" + ",".join(self.translate(item) for item in items) + ")"

        return self.translate(value)

    def translate_args(self, args: list[str]) -> list[str]:
        """Translate a list of arguments.

        Args:
            args: List of command arguments (may contain paths)

        Returns:
            List of arguments with paths translated
        """
        return [self.translate_arg(arg) for arg in args]

    def get_container_cwd(self, host_cwd: str) -> str:
        """Map host CWD to container CWD.

        Args:
            host_cwd: Host current working directory

        Returns:
            Container working directory path
        """
        cwd = Path(host_cwd).resolve()

        if cwd.is_relative_to(self.home):
            return str(cwd)

        return f"/host{cwd}"
