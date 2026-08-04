"""Path translation for host-to-container path mapping."""
import os
from pathlib import Path
from typing import List


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

    def translate_args(self, args: List[str]) -> List[str]:
        """Translate a list of arguments.

        Args:
            args: List of command arguments (may contain paths)

        Returns:
            List of arguments with paths translated
        """
        return [self.translate(arg) for arg in args]

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
