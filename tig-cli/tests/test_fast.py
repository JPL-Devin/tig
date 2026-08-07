"""Tests for the warm path that runs commands without click or the Docker SDK."""
import os
from unittest.mock import MagicMock, patch

import pytest

from tig_cli import fast
from tig_cli.engine import EngineUnavailable
from tig_cli.spec import (
    build_run_kwargs,
    build_volume_mounts,
    container_name_for,
)

IMAGE = "test-image:latest"
IMAGE_ID = "sha256:image"


@pytest.fixture
def host(monkeypatch, tmp_path):
    """A host with a home directory and no configuration files."""
    home = tmp_path / "home" / "user"
    home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CONTAINER_IMAGE", IMAGE)
    empty = tmp_path / "empty.toml"
    empty.write_text("")
    monkeypatch.setenv("TIG_CONFIG", str(empty))
    monkeypatch.delenv(fast.DISABLE_ENV, raising=False)
    monkeypatch.delenv("MARS_CONFIG_PATH", raising=False)
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.chdir(home)
    return home


def expected_name(home):
    """The container name both paths derive for this configuration."""
    volumes = build_volume_mounts(str(home), [])
    return container_name_for(build_run_kwargs(IMAGE, volumes, str(home)))


@pytest.fixture
def engine(monkeypatch):
    """A daemon reporting one running container built from the wanted image."""
    fake = MagicMock()
    fake.inspect_container.return_value = {
        "State": {"Running": True},
        "Image": IMAGE_ID,
    }
    fake.inspect_image.return_value = {"Id": IMAGE_ID}
    fake.exec_command.return_value = 0
    monkeypatch.setattr(fast.Engine, "detect", classmethod(lambda cls: fake))
    return fake


def test_runs_in_the_container_the_cli_would_use(host, engine):
    engine.exec_command.return_value = 3

    assert fast.run(["vicar", "in.img"]) == 3

    engine.exec_command.assert_called_once()
    name, command, workdir, env = engine.exec_command.call_args.args[:4]
    assert name == expected_name(host)
    assert command == ["vicar", "in.img"]
    assert workdir == str(host)
    assert env == {"DISPLAY": ":0"}


def test_translates_host_paths(host, engine):
    fast.run(["vicar", "INP=/data/in.img", "OUT=out.img"])

    command = engine.exec_command.call_args.args[1]
    assert command == ["vicar", "INP=/host/data/in.img", "OUT=out.img"]


def test_path_translation_can_be_disabled(host, engine, monkeypatch):
    monkeypatch.setenv("TIG_DISABLE_PATH_TRANSLATION", "1")

    fast.run(["vicar", "INP=/data/in.img"])

    command = engine.exec_command.call_args.args[1]
    assert command == ["vicar", "INP=/data/in.img"]


def test_claims_the_container_while_it_runs(host, engine):
    claimed = []
    engine.exec_command.side_effect = lambda *a, **k: claimed.append(
        set(fast.Claim.claimed_containers())
    )

    fast.run(["vicar"])

    assert claimed == [{expected_name(host)}]
    assert fast.Claim.claimed_containers() == set()


def test_declines_when_disabled(host, engine, monkeypatch):
    monkeypatch.setenv(fast.DISABLE_ENV, "1")

    assert fast.run(["vicar"]) is None
    engine.exec_command.assert_not_called()


@pytest.mark.parametrize("argv", [[], ["--status"], ["-v", "vicar"]])
def test_declines_anything_needing_option_parsing(host, engine, argv):
    assert fast.run(argv) is None
    engine.exec_command.assert_not_called()


def test_declines_when_the_container_is_not_running(host, engine):
    engine.inspect_container.return_value = {"State": {"Running": False}}

    assert fast.run(["vicar"]) is None
    engine.exec_command.assert_not_called()


def test_declines_when_there_is_no_container_yet(host, engine):
    engine.inspect_container.side_effect = EngineUnavailable("not found")

    assert fast.run(["vicar"]) is None


def test_declines_when_the_image_was_repulled(host, engine):
    engine.inspect_image.return_value = {"Id": "sha256:rebuilt"}

    assert fast.run(["vicar"]) is None
    engine.exec_command.assert_not_called()


def test_declines_when_the_daemon_cannot_be_driven(host, monkeypatch):
    def unavailable(cls):
        raise EngineUnavailable("remote daemon")

    monkeypatch.setattr(fast.Engine, "detect", classmethod(unavailable))

    assert fast.run(["vicar"]) is None


def test_declines_when_the_configuration_is_unusable(host, engine, tmp_path):
    broken = tmp_path / "broken.toml"
    broken.write_text("image = [")
    with patch.dict(os.environ, {"TIG_CONFIG": str(broken)}):
        assert fast.run(["vicar"]) is None


def test_uses_a_tty_only_when_attached_to_one(host, engine, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True, raising=False)

    fast.run(["vicar"])

    assert engine.exec_command.call_args.kwargs["tty"] is True
