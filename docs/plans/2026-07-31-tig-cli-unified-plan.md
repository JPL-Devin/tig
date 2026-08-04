# TIG CLI Unified Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace three pip packages (`tig-cli-core`, `tig-opensource`, `tig-m20-g87`) with a single `tig-cli` package exposing one `tig` command, with backend Docker image configurable via `CONTAINER_IMAGE` env var.

**Architecture:** Single `tig-cli/` directory containing all source, tests, and packaging config. `container.py` reads `CONTAINER_IMAGE` at invocation time with a hardcoded opensource default. `variants.py` deleted. `cli.py` becomes a single `main()` function.

**Tech Stack:** Python 3.9+, Click 8.0+, docker-py 6.0+, pytest, setuptools

---

## Outline

- Task 1: Create `tig-cli/` package scaffold
- Task 2: Write `path_translator.py` (copy unchanged, verify tests pass)
- Task 3: Write `container.py` (remove VariantConfig, add `get_container_image()`)
- Task 4: Write `cli.py` (single `main()`, show active image in help)
- Task 5: Update tests (remove variant tests, update container/cli tests)
- Task 6: Update CI workflows
- Task 7: Delete old packages
- Task 8: Verify full test suite passes

---

## Task 1: Create `tig-cli/` package scaffold

**Files:**
- Create: `tig-cli/pyproject.toml`
- Create: `tig-cli/MANIFEST.in`
- Create: `tig-cli/src/tig_cli/__init__.py`
- Create: `tig-cli/tests/__init__.py`
- Create: `tig-cli/tests/integration/__init__.py`

- [ ] **Step 1: Create directory structure**

```bash
mkdir -p tig-cli/src/tig_cli
mkdir -p tig-cli/tests/integration
touch tig-cli/tests/__init__.py
touch tig-cli/tests/integration/__init__.py
```

- [ ] **Step 2: Write `tig-cli/pyproject.toml`**

```toml
[project]
name = "tig-cli"
version = "0.1.0"
description = "TIG CLI for running VICAR commands via Docker"
requires-python = ">=3.9"
dependencies = [
    "click>=8.0.0",
    "docker>=6.0.0",
]
authors = [
    {name = "NASA AMMOS", email = "ammos@jpl.nasa.gov"}
]
readme = "README.md"
license = {text = "Apache-2.0"}

[project.scripts]
tig = "tig_cli.cli:main"

[project.optional-dependencies]
dev = [
    "pytest>=7.0.0",
    "pytest-cov>=4.0.0",
    "pytest-mock>=3.10.0",
]

[build-system]
requires = ["setuptools>=61.0", "wheel"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = ["test_*.py"]
python_classes = ["Test*"]
python_functions = ["test_*"]
addopts = "-v --cov=tig_cli --cov-report=term-missing"
markers = [
    "integration: marks tests as integration tests (deselect with '-m \"not integration\"')",
]
```

- [ ] **Step 3: Write `tig-cli/MANIFEST.in`**

```
include README.md
include LICENSE
recursive-include src/tig_cli *.py
```

- [ ] **Step 4: Write `tig-cli/src/tig_cli/__init__.py`**

```python
"""TIG CLI — run VICAR commands via Docker.

Configure the backend image with the CONTAINER_IMAGE environment variable.
"""

__version__ = "0.1.0"
```

- [ ] **Step 5: Install package in editable mode**

```bash
cd tig-cli
pip install -e ".[dev]"
```

Expected: successful install with no errors.

- [ ] **Step 6: Commit**

```bash
git add tig-cli/
git commit -m "feat: scaffold tig-cli package"
```

---

## Task 2: Copy `path_translator.py` and its tests

**Files:**
- Create: `tig-cli/src/tig_cli/path_translator.py`
- Create: `tig-cli/tests/test_path_translator.py`

- [ ] **Step 1: Write `tig-cli/src/tig_cli/path_translator.py`**

This file is identical to the old `tig-cli-core` version — no logic changes.

```python
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
```

- [ ] **Step 2: Write `tig-cli/tests/test_path_translator.py`**

```python
"""Tests for path translation."""
import os
from pathlib import Path
import pytest
from tig_cli.path_translator import PathTranslator


@pytest.fixture
def home_dir(tmp_path):
    """Create a temporary home directory."""
    return str(tmp_path / "home" / "user")


@pytest.fixture
def translator(home_dir):
    """Create a PathTranslator instance."""
    return PathTranslator(home_dir)


def test_relative_path_unchanged(translator):
    assert translator.translate("file.vic") == "file.vic"
    assert translator.translate("./data/file.vic") == "./data/file.vic"
    assert translator.translate("../other/file.vic") == "../other/file.vic"


def test_home_path_unchanged(translator, home_dir):
    path = f"{home_dir}/data/file.vic"
    assert translator.translate(path) == path


def test_system_path_gets_host_prefix(translator):
    assert translator.translate("/data/file.vic") == "/host/data/file.vic"
    assert translator.translate("/tmp/output.vic") == "/host/tmp/output.vic"


def test_empty_path_unchanged(translator):
    assert translator.translate("") == ""


def test_translate_args_list(translator, home_dir):
    args = [
        "file.vic",
        f"{home_dir}/input.vic",
        "/data/system.vic",
    ]
    expected = [
        "file.vic",
        f"{home_dir}/input.vic",
        "/host/data/system.vic",
    ]
    assert translator.translate_args(args) == expected


def test_get_container_cwd_in_home(translator, home_dir):
    cwd = f"{home_dir}/projects/vicar"
    assert translator.get_container_cwd(cwd) == cwd


def test_get_container_cwd_outside_home(translator):
    cwd = "/opt/vicar/workspace"
    assert translator.get_container_cwd(cwd) == "/host/opt/vicar/workspace"


def test_home_directory_itself(translator, home_dir):
    assert translator.translate(home_dir) == home_dir


def test_root_path_gets_host_prefix(translator):
    assert translator.translate("/") == "/host/"


def test_path_with_spaces(translator, home_dir):
    path = f"{home_dir}/my documents/file.vic"
    assert translator.translate(path) == path
    system_path = "/data/my files/image.vic"
    assert translator.translate(system_path) == "/host/data/my files/image.vic"


def test_path_with_special_characters(translator):
    assert translator.translate("/data/file-name.vic") == "/host/data/file-name.vic"
    assert translator.translate("/data/file_name.vic") == "/host/data/file_name.vic"
    assert translator.translate("/data/file.name.vic") == "/host/data/file.name.vic"


def test_non_path_arguments(translator):
    assert translator.translate("123") == "123"
    assert translator.translate("3.14") == "3.14"
    assert translator.translate("-v") == "-v"
    assert translator.translate("--verbose") == "--verbose"
    assert translator.translate("INP=file.vic") == "INP=file.vic"
    assert translator.translate("OUT=/tmp/out.vic") == "OUT=/tmp/out.vic"


def test_translate_args_mixed_types(translator, home_dir):
    args = [
        "marsmap",
        "-v",
        f"{home_dir}/input.vic",
        "/data/system.vic",
        "output.vic",
        "SIZE=(1,1,1024,1024)",
    ]
    result = translator.translate_args(args)
    assert result[0] == "marsmap"
    assert result[1] == "-v"
    assert result[2] == f"{home_dir}/input.vic"
    assert result[3] == "/host/data/system.vic"
    assert result[4] == "output.vic"
    assert result[5] == "SIZE=(1,1,1024,1024)"
```

- [ ] **Step 3: Run tests to verify they pass**

```bash
cd tig-cli
pytest tests/test_path_translator.py -v
```

Expected: all 14 tests PASS.

- [ ] **Step 4: Commit**

```bash
git add tig-cli/src/tig_cli/path_translator.py tig-cli/tests/test_path_translator.py
git commit -m "feat: add path translator to tig-cli"
```

---

## Task 3: Write `container.py`

**Files:**
- Create: `tig-cli/src/tig_cli/container.py`
- Create: `tig-cli/tests/test_container.py`

Key changes from old version:
- Remove `VariantConfig` import
- Add `get_container_image()` function reading `CONTAINER_IMAGE` env var
- `ContainerManager.__init__` takes `image: str` instead of `variant: VariantConfig`
- Container name: `tig-vicar-{pid}` (fixed prefix, no variant-derived name)

- [ ] **Step 1: Write failing tests first**

```python
# tig-cli/tests/test_container.py
"""Tests for container management."""
import os
import pytest
from unittest.mock import Mock, patch, MagicMock
from tig_cli.container import ContainerManager, get_container_image

DEFAULT_IMAGE = "ghcr.io/nasa-ammos/tig/terrain-intelligence-generator:opensource"


# --- get_container_image ---

def test_get_container_image_default():
    """Returns default image when CONTAINER_IMAGE not set."""
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("CONTAINER_IMAGE", None)
        assert get_container_image() == DEFAULT_IMAGE


def test_get_container_image_from_env():
    """Returns value of CONTAINER_IMAGE env var."""
    custom = "ghcr.io/my-org/custom-vicar:v2"
    with patch.dict(os.environ, {"CONTAINER_IMAGE": custom}):
        assert get_container_image() == custom


# --- ContainerManager init ---

@pytest.fixture
def home_dir(tmp_path):
    return str(tmp_path / "home" / "user")


def test_container_manager_init(home_dir):
    """ContainerManager initializes with image string."""
    with patch('tig_cli.container.docker.from_env'), \
         patch.dict(os.environ, {"HOME": home_dir}):
        manager = ContainerManager("test-image:latest")
    assert manager.image == "test-image:latest"
    assert manager.container_name.startswith("tig-vicar-")


def test_container_manager_default_no_translation(home_dir):
    """Path translation enabled by default."""
    with patch('tig_cli.container.docker.from_env'), \
         patch.dict(os.environ, {"HOME": home_dir}):
        manager = ContainerManager("test-image:latest")
    assert manager.disable_path_translation is False


# --- _build_volume_mounts ---

def test_build_volume_mounts_basic(home_dir):
    with patch('tig_cli.container.docker.from_env'), \
         patch.dict(os.environ, {"HOME": home_dir}):
        manager = ContainerManager("test-image:latest")
        volumes = manager._build_volume_mounts([])

    assert "/" in volumes
    assert volumes["/"]["bind"] == "/host"
    assert volumes["/"]["mode"] == "ro"
    assert home_dir in volumes
    assert volumes[home_dir]["bind"] == home_dir
    assert volumes[home_dir]["mode"] == "rw"


def test_build_volume_mounts_with_writable_paths(home_dir, tmp_path):
    writable_path = str(tmp_path / "data")
    os.makedirs(writable_path, exist_ok=True)

    with patch('tig_cli.container.docker.from_env'), \
         patch.dict(os.environ, {"HOME": home_dir}):
        manager = ContainerManager("test-image:latest")
        volumes = manager._build_volume_mounts([writable_path])

    assert writable_path in volumes
    assert volumes[writable_path]["bind"] == f"/host{writable_path}"
    assert volumes[writable_path]["mode"] == "rw"


def test_build_volume_mounts_skips_nonexistent_paths(home_dir):
    with patch('tig_cli.container.docker.from_env'), \
         patch.dict(os.environ, {"HOME": home_dir}):
        manager = ContainerManager("test-image:latest")
        volumes = manager._build_volume_mounts(["/nonexistent/path"])

    assert len(volumes) == 2


# --- start_container ---

@patch('tig_cli.container.docker.from_env')
def test_start_container_linux(mock_docker, home_dir):
    mock_client = MagicMock()
    mock_docker.return_value = mock_client

    with patch.dict(os.environ, {"HOME": home_dir, "DISPLAY": ":0"}):
        manager = ContainerManager("test-image:latest")
        with patch('sys.platform', 'linux'):
            manager.start_container([])

    call_kwargs = mock_client.containers.run.call_args[1]
    assert call_kwargs['image'] == "test-image:latest"
    assert call_kwargs['detach'] is True
    assert call_kwargs['network_mode'] == 'host'
    assert 'DISPLAY' in call_kwargs['environment']


@patch('tig_cli.container.docker.from_env')
def test_start_container_macos(mock_docker, home_dir):
    mock_client = MagicMock()
    mock_docker.return_value = mock_client

    with patch.dict(os.environ, {"HOME": home_dir}):
        manager = ContainerManager("test-image:latest")
        with patch('sys.platform', 'darwin'):
            manager.start_container([])

    call_kwargs = mock_client.containers.run.call_args[1]
    assert call_kwargs['environment']['DISPLAY'] == 'host.docker.internal:0'
    assert 'network_mode' not in call_kwargs


# --- stop_container ---

@patch('tig_cli.container.docker.from_env')
def test_stop_container(mock_docker, home_dir):
    mock_client = MagicMock()
    mock_container = MagicMock()
    mock_docker.return_value = mock_client

    with patch.dict(os.environ, {"HOME": home_dir}):
        manager = ContainerManager("test-image:latest")
    manager.container = mock_container
    manager.stop_container()

    mock_container.stop.assert_called_once()
    mock_container.remove.assert_called_once()


# --- execute_vicar_command ---

@patch('tig_cli.container.subprocess.run')
def test_execute_vicar_command(mock_run, home_dir):
    mock_run.return_value = Mock(returncode=0)

    with patch('tig_cli.container.docker.from_env'), \
         patch.dict(os.environ, {"HOME": home_dir}):
        manager = ContainerManager("test-image:latest")
        with patch('os.getcwd', return_value=f"{home_dir}/projects"):
            exit_code = manager.execute_vicar_command("marsmap", ["input.vic", "output.vic"])

    assert exit_code == 0
    call_args = mock_run.call_args[0][0]
    assert call_args[0] == "docker"
    assert call_args[1] == "exec"
    assert "marsmap" in call_args
    assert "input.vic" in call_args
    assert "output.vic" in call_args


@patch('tig_cli.container.subprocess.run')
def test_execute_vicar_command_with_path_translation(mock_run, home_dir):
    mock_run.return_value = Mock(returncode=0)

    with patch('tig_cli.container.docker.from_env'), \
         patch.dict(os.environ, {"HOME": home_dir}):
        manager = ContainerManager("test-image:latest")
        with patch('os.getcwd', return_value=f"{home_dir}/projects"):
            manager.execute_vicar_command(
                "marsmap",
                ["/data/input.vic", f"{home_dir}/output.vic"]
            )

    call_args = mock_run.call_args[0][0]
    assert "/host/data/input.vic" in call_args
    assert f"{home_dir}/output.vic" in call_args


@patch('tig_cli.container.subprocess.run')
def test_execute_vicar_command_without_translation(mock_run, home_dir):
    mock_run.return_value = Mock(returncode=0)

    with patch('tig_cli.container.docker.from_env'), \
         patch.dict(os.environ, {"HOME": home_dir}):
        manager = ContainerManager("test-image:latest", disable_path_translation=True)
        with patch('os.getcwd', return_value=f"{home_dir}/projects"):
            manager.execute_vicar_command("marsmap", ["/data/input.vic"])

    call_args = mock_run.call_args[0][0]
    assert "/data/input.vic" in call_args
    assert "/host/data/input.vic" not in call_args
```

- [ ] **Step 2: Run tests — expect ImportError (module not written yet)**

```bash
cd tig-cli
pytest tests/test_container.py -v 2>&1 | head -20
```

Expected: `ImportError` or `ModuleNotFoundError` for `tig_cli.container`.

- [ ] **Step 3: Write `tig-cli/src/tig_cli/container.py`**

```python
"""Container lifecycle management."""
import os
import subprocess
import sys
from pathlib import Path
from typing import List, Dict, Any, Optional
import docker

from .path_translator import PathTranslator

DEFAULT_IMAGE = "ghcr.io/nasa-ammos/tig/terrain-intelligence-generator:opensource"


def get_container_image() -> str:
    """Return the Docker image to use for VICAR execution.

    Reads CONTAINER_IMAGE environment variable. Falls back to the
    opensource image if not set.
    """
    return os.environ.get("CONTAINER_IMAGE", DEFAULT_IMAGE)


class ContainerManager:
    """Manages VICAR container lifecycle and execution.

    Handles starting containers with appropriate mounts,
    executing VICAR commands, and cleanup.
    """

    def __init__(
        self,
        image: str,
        disable_path_translation: bool = False
    ):
        """Initialize the container manager.

        Args:
            image: Docker image name and tag
            disable_path_translation: Skip path translation (for debugging)
        """
        self.image = image
        self.disable_path_translation = disable_path_translation
        self.client = docker.from_env()
        self.container_name = f"tig-vicar-{os.getpid()}"
        self.translator = PathTranslator(os.environ["HOME"])
        self.container: Optional[Any] = None

    def _build_volume_mounts(
        self,
        writable_paths: List[str]
    ) -> Dict[str, Dict[str, str]]:
        """Build volume mount configuration.

        Args:
            writable_paths: Additional paths to mount as read-write

        Returns:
            Dictionary of volume mounts for docker-py
        """
        home = os.environ["HOME"]

        volumes = {
            "/": {"bind": "/host", "mode": "ro"},
            home: {"bind": home, "mode": "rw"},
        }

        for path in writable_paths:
            if os.path.isdir(path):
                volumes[path] = {"bind": f"/host{path}", "mode": "rw"}

        return volumes

    def start_container(self, writable_paths: List[str]) -> None:
        """Start the VICAR container with appropriate mounts.

        Args:
            writable_paths: Additional paths to mount as read-write
        """
        volumes = self._build_volume_mounts(writable_paths)

        environment = {}
        extra_kwargs = {}

        if sys.platform == "darwin":
            environment["DISPLAY"] = "host.docker.internal:0"
        else:
            environment["DISPLAY"] = os.environ.get("DISPLAY", ":0")
            volumes["/tmp/.X11-unix"] = {"bind": "/tmp/.X11-unix", "mode": "rw"}
            extra_kwargs["network_mode"] = "host"

        self.container = self.client.containers.run(
            image=self.image,
            name=self.container_name,
            volumes=volumes,
            environment=environment,
            detach=True,
            command="tail -f /dev/null",
            **extra_kwargs
        )

    def stop_container(self) -> None:
        """Stop and remove the container."""
        if self.container:
            self.container.stop()
            self.container.remove()

    def execute_vicar_command(
        self,
        vicar_tool: str,
        args: List[str]
    ) -> int:
        """Execute a VICAR command in the container.

        Args:
            vicar_tool: VICAR tool name (e.g., "marsmap", "label")
            args: Command arguments

        Returns:
            Exit code from command execution
        """
        if self.disable_path_translation:
            translated_args = args
        else:
            translated_args = self.translator.translate_args(args)

        container_cwd = self.translator.get_container_cwd(os.getcwd())

        exec_args = [
            "docker", "exec",
            "-w", container_cwd,
            "-e", "XFILESEARCHPATH=/usr/local/vicar/gui/%N",
            "-e", "XBMLANGPATH=/usr/local/vicar/gui/%L",
            self.container_name,
            vicar_tool,
            *translated_args
        ]

        result = subprocess.run(exec_args)
        return result.returncode
```

- [ ] **Step 4: Run tests — expect all pass**

```bash
cd tig-cli
pytest tests/test_container.py -v
```

Expected: all 14 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add tig-cli/src/tig_cli/container.py tig-cli/tests/test_container.py
git commit -m "feat: add container manager to tig-cli (image via env var)"
```

---

## Task 4: Write `cli.py`

**Files:**
- Create: `tig-cli/src/tig_cli/cli.py`
- Create: `tig-cli/tests/test_cli.py`

Key changes from old version:
- Remove `create_cli(variant_name)` factory; single `main()` function
- Call `get_container_image()` at invocation time
- Help text shows active image

- [ ] **Step 1: Write failing tests first**

```python
# tig-cli/tests/test_cli.py
"""Tests for the tig CLI."""
import os
import pytest
from click.testing import CliRunner
from unittest.mock import patch, MagicMock
from tig_cli.cli import main
from tig_cli.container import DEFAULT_IMAGE


@pytest.fixture
def runner():
    return CliRunner()


def test_help_shows_active_image_default(runner):
    """Help text includes active image (default)."""
    result = runner.invoke(main, ['--help'])
    assert result.exit_code == 0
    assert DEFAULT_IMAGE in result.output


def test_help_shows_active_image_from_env(runner):
    """Help text includes active image from CONTAINER_IMAGE env var."""
    custom = "my-org/custom-vicar:v2"
    with patch.dict(os.environ, {"CONTAINER_IMAGE": custom}):
        result = runner.invoke(main, ['--help'])
    assert result.exit_code == 0
    assert custom in result.output


@patch('tig_cli.cli.ContainerManager')
def test_cli_executes_vicar_command(mock_manager_class, runner):
    """CLI starts container, executes command, stops container."""
    mock_manager = MagicMock()
    mock_manager.execute_vicar_command.return_value = 0
    mock_manager_class.return_value = mock_manager

    result = runner.invoke(main, ['marsmap', 'input.vic', 'output.vic'])

    assert result.exit_code == 0
    mock_manager.start_container.assert_called_once()
    mock_manager.execute_vicar_command.assert_called_once_with(
        'marsmap', ['input.vic', 'output.vic']
    )
    mock_manager.stop_container.assert_called_once()


@patch('tig_cli.cli.ContainerManager')
def test_cli_uses_container_image_env_var(mock_manager_class, runner):
    """CLI passes CONTAINER_IMAGE value to ContainerManager."""
    custom = "ghcr.io/my-org/custom:v1"
    mock_manager = MagicMock()
    mock_manager.execute_vicar_command.return_value = 0
    mock_manager_class.return_value = mock_manager

    with patch.dict(os.environ, {"CONTAINER_IMAGE": custom}):
        runner.invoke(main, ['marsmap', 'input.vic'])

    call_args = mock_manager_class.call_args
    assert call_args[0][0] == custom


@patch('tig_cli.cli.ContainerManager')
def test_cli_uses_default_image_when_env_unset(mock_manager_class, runner):
    """CLI uses default image when CONTAINER_IMAGE not set."""
    mock_manager = MagicMock()
    mock_manager.execute_vicar_command.return_value = 0
    mock_manager_class.return_value = mock_manager

    env = {k: v for k, v in os.environ.items() if k != "CONTAINER_IMAGE"}
    with patch.dict(os.environ, env, clear=True):
        runner.invoke(main, ['marsmap', 'input.vic'])

    call_args = mock_manager_class.call_args
    assert call_args[0][0] == DEFAULT_IMAGE


@patch('tig_cli.cli.ContainerManager')
def test_cli_with_writable_path_option(mock_manager_class, runner):
    """--writable-path flag passed to start_container."""
    mock_manager = MagicMock()
    mock_manager.execute_vicar_command.return_value = 0
    mock_manager_class.return_value = mock_manager

    result = runner.invoke(main, ['--writable-path', '/data', 'marsmap', 'input.vic'])

    assert result.exit_code == 0
    mock_manager.start_container.assert_called_once_with(writable_paths=['/data'])


@patch('tig_cli.cli.ContainerManager')
def test_cli_with_multiple_writable_paths(mock_manager_class, runner):
    """Multiple --writable-path flags all passed to start_container."""
    mock_manager = MagicMock()
    mock_manager.execute_vicar_command.return_value = 0
    mock_manager_class.return_value = mock_manager

    result = runner.invoke(main, [
        '--writable-path', '/data',
        '--writable-path', '/output',
        'marsmap', 'input.vic'
    ])

    assert result.exit_code == 0
    mock_manager.start_container.assert_called_once_with(
        writable_paths=['/data', '/output']
    )


@patch('tig_cli.cli.ContainerManager')
def test_cli_with_disable_path_translation(mock_manager_class, runner):
    """--disable-path-translation passed to ContainerManager."""
    mock_manager = MagicMock()
    mock_manager.execute_vicar_command.return_value = 0
    mock_manager_class.return_value = mock_manager

    result = runner.invoke(main, ['--disable-path-translation', 'marsmap', 'input.vic'])

    assert result.exit_code == 0
    call_kwargs = mock_manager_class.call_args[1]
    assert call_kwargs['disable_path_translation'] is True


@patch('tig_cli.cli.ContainerManager')
def test_cli_stops_container_on_error(mock_manager_class, runner):
    """Container is stopped even when command raises an exception."""
    mock_manager = MagicMock()
    mock_manager.execute_vicar_command.side_effect = Exception("Test error")
    mock_manager_class.return_value = mock_manager

    runner.invoke(main, ['marsmap', 'input.vic'])

    mock_manager.stop_container.assert_called_once()
```

- [ ] **Step 2: Run tests — expect ImportError**

```bash
cd tig-cli
pytest tests/test_cli.py -v 2>&1 | head -20
```

Expected: `ImportError` — `tig_cli.cli` doesn't exist yet.

- [ ] **Step 3: Write `tig-cli/src/tig_cli/cli.py`**

```python
"""TIG CLI — execute VICAR commands via Docker."""
import sys
import click

from .container import ContainerManager, get_container_image


@click.command(
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
    help="Additional writable paths to mount (can be specified multiple times)",
)
@click.option(
    "--disable-path-translation",
    is_flag=True,
    help="Disable automatic path translation (for debugging)",
)
@click.pass_context
def main(
    ctx: click.Context,
    vicar_tool: str,
    args: tuple,
    writable_path: tuple,
    disable_path_translation: bool,
) -> None:
    """Execute a VICAR command via Docker.

    \b
    Active image: {image}

    Set CONTAINER_IMAGE env var to override.
    """.format(image=get_container_image())

    manager = ContainerManager(
        get_container_image(),
        disable_path_translation=disable_path_translation,
    )

    try:
        manager.start_container(writable_paths=list(writable_path))
        exit_code = manager.execute_vicar_command(vicar_tool, list(args))
        sys.exit(exit_code)
    finally:
        manager.stop_container()
```

- [ ] **Step 4: Run tests — expect all pass**

```bash
cd tig-cli
pytest tests/test_cli.py -v
```

Expected: all 9 tests PASS.

- [ ] **Step 5: Run full suite**

```bash
cd tig-cli
pytest -m "not integration" -v
```

Expected: all tests PASS (path_translator + container + cli).

- [ ] **Step 6: Commit**

```bash
git add tig-cli/src/tig_cli/cli.py tig-cli/tests/test_cli.py
git commit -m "feat: add unified tig CLI with CONTAINER_IMAGE env var"
```

---

## Task 5: Port integration tests

**Files:**
- Create: `tig-cli/tests/integration/test_vicar_execution.py`

- [ ] **Step 1: Write `tig-cli/tests/integration/test_vicar_execution.py`**

```python
"""Integration tests for VICAR command execution.

These tests require Docker and the VICAR images to be available.
Run separately from unit tests: pytest -m integration
"""
import os
import pytest
from click.testing import CliRunner
from tig_cli.cli import main
from tig_cli.container import DEFAULT_IMAGE

pytestmark = pytest.mark.integration


@pytest.fixture
def runner():
    return CliRunner()


@pytest.mark.skipif(
    os.environ.get("SKIP_INTEGRATION") == "1",
    reason="Integration tests skipped",
)
def test_tig_cli_help(runner):
    """tig --help exits 0 and mentions VICAR."""
    result = runner.invoke(main, ['--help'])
    assert result.exit_code == 0
    assert "VICAR" in result.output


@pytest.mark.skipif(
    os.environ.get("SKIP_INTEGRATION") == "1",
    reason="Integration tests skipped",
)
def test_tig_cli_help_shows_default_image(runner):
    """tig --help shows the default image URI."""
    env = {k: v for k, v in os.environ.items() if k != "CONTAINER_IMAGE"}
    with __import__('unittest.mock', fromlist=['patch']).patch.dict(
        os.environ, env, clear=True
    ):
        result = runner.invoke(main, ['--help'])
    assert DEFAULT_IMAGE in result.output


# Additional integration tests would go here.
# These require actual VICAR images and test data. Example:
#
# def test_execute_label_command(runner, tmp_path):
#     test_file = tmp_path / "test.vic"
#     test_file.write_bytes(b"test data")
#     result = runner.invoke(main, ['label', f'INP={test_file}'])
#     assert result.exit_code == 0
```

- [ ] **Step 2: Run integration tests (will skip without SKIP_INTEGRATION)**

```bash
cd tig-cli
pytest -m integration -v
```

Expected: 2 tests collected, either PASS (if Docker+image available) or SKIP.

- [ ] **Step 3: Commit**

```bash
git add tig-cli/tests/integration/test_vicar_execution.py
git commit -m "test: add integration test stubs for tig-cli"
```

---

## Task 6: Update CI workflows

**Files:**
- Modify: `.github/workflows/test.yml`
- Modify: `.github/workflows/publish.yml`

- [ ] **Step 1: Update `.github/workflows/test.yml`**

Replace the entire file with:

```yaml
name: Test

on: [push, pull_request]

jobs:
  test:
    runs-on: ${{ matrix.os }}
    strategy:
      matrix:
        os: [ubuntu-latest, macos-latest]
        python-version: ["3.9", "3.10", "3.11", "3.12"]

    steps:
      - uses: actions/checkout@v3

      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: ${{ matrix.python-version }}

      - name: Install dependencies
        run: |
          cd tig-cli
          pip install -e ".[dev]"

      - name: Run tests
        run: |
          cd tig-cli
          pytest -m "not integration" -v --cov=tig_cli --cov-report=xml

      - name: Upload coverage
        uses: codecov/codecov-action@v3
        with:
          files: ./tig-cli/coverage.xml
```

- [ ] **Step 2: Update `.github/workflows/publish.yml`**

Replace the entire file with:

```yaml
name: Publish

on:
  release:
    types: [published]

jobs:
  publish:
    runs-on: ubuntu-latest

    steps:
      - uses: actions/checkout@v3

      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: "3.10"

      - name: Install build tools
        run: pip install build twine

      - name: Build package
        run: |
          cd tig-cli && python -m build

      - name: Publish to PyPI
        env:
          TWINE_USERNAME: __token__
          TWINE_PASSWORD: ${{ secrets.PYPI_API_TOKEN }}
        run: |
          twine upload tig-cli/dist/*
```

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/test.yml .github/workflows/publish.yml
git commit -m "ci: update workflows for unified tig-cli package"
```

---

## Task 7: Delete old packages

**Files:**
- Delete: `tig-cli-core/` (entire directory)
- Delete: `tig-opensource/` (entire directory)
- Delete: `tig-m20-g87/` (entire directory)

- [ ] **Step 1: Remove old package directories**

```bash
rm -rf tig-cli-core tig-opensource tig-m20-g87
```

- [ ] **Step 2: Verify only expected directories remain**

```bash
ls -la
```

Expected: `tig-cli-core/`, `tig-opensource/`, `tig-m20-g87/` are gone. `tig-cli/` remains.

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "feat: remove old variant packages (replaced by tig-cli)"
```

---

## Task 8: Final verification

- [ ] **Step 1: Run full unit test suite from tig-cli directory**

```bash
cd tig-cli
pytest -m "not integration" -v --cov=tig_cli --cov-report=term-missing
```

Expected: all unit tests PASS, coverage report shows 100% on `container.py`, `path_translator.py`, `cli.py`.

- [ ] **Step 2: Verify `tig --help` works**

```bash
tig --help
```

Expected output contains:
- "Execute a VICAR command via Docker"
- The default image URI: `ghcr.io/nasa-ammos/tig/terrain-intelligence-generator:opensource`
- `--writable-path` option
- `--disable-path-translation` option

- [ ] **Step 3: Verify CONTAINER_IMAGE override works**

```bash
CONTAINER_IMAGE=my-custom/image:v1 tig --help
```

Expected: `my-custom/image:v1` appears in the help output.

- [ ] **Step 4: Commit if any cleanup needed, then final commit**

```bash
git log --oneline -10
```

Confirm all tasks have their own commits in clean order.
