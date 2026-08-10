"""Tests for the entry point that chooses between the warm path and the CLI."""
from unittest.mock import patch

import pytest

from tig_cli import entry
from tig_cli.engine import EngineUnavailable
from tig_cli.spec import TigError


def run_entry(argv, **patches):
    with patch("sys.argv", ["tig", *argv]), patch.multiple(
        "tig_cli.fast", **patches
    ):
        entry.main()


def test_exits_with_the_command_exit_code():
    with pytest.raises(SystemExit) as exit_info:
        run_entry(["vicar"], run=lambda argv: 7)

    assert exit_info.value.code == 7


def test_falls_through_to_the_cli():
    with patch("tig_cli.cli.main") as cli_main:
        run_entry(["--status"], run=lambda argv: None)

    cli_main.assert_called_once_with()


def test_interrupting_reports_the_signal():
    def interrupted(argv):
        raise KeyboardInterrupt

    with pytest.raises(SystemExit) as exit_info:
        run_entry(["vicar"], run=interrupted)

    assert exit_info.value.code == 130


@pytest.mark.parametrize(
    "error", [TigError("no home"), EngineUnavailable("no daemon"), OSError("gone")]
)
def test_reports_failures_without_a_traceback(error, capsys):
    def failing(argv):
        raise error

    with pytest.raises(SystemExit) as exit_info:
        run_entry(["vicar"], run=failing)

    assert exit_info.value.code == 1
    assert str(error) in capsys.readouterr().err
