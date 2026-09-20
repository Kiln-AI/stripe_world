"""`seahaven serve`: argument parsing, and the two translations it owes.

`serve` itself is `seahaven/openenv/serve.py` and is tested there; what the CLI
adds is a set of options and two places where the value on the command line is
not the value the function wants -- `--session-timeout 0` is "no reaper", which
OpenEnv spells `None`, and an option that was not given is the framework's
default and not the parser's. So `serve` is replaced with a recorder here: this
module is about what it would have been called with.
"""

import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from seahaven.cli.serve import MISSING_EXTRA
from tests.conftest import WORLDS, run_cli

# The subpackage and not `openenv`: what this module imports is
# `seahaven.openenv`, so that is what has to import for the tests below to mean
# anything. Only an `ImportError` skips -- an extra that is absent, or installed
# and unimportable. Anything else raises, and CI asserts this import separately,
# because an installed extra that skips quietly is a green run that tested none
# of this.
pytest.importorskip(
    "seahaven.openenv", exc_type=ImportError, reason="the serve extra does not import here"
)

from seahaven.openenv import DEFAULT_MAX_CONCURRENT_ENVS, DEFAULT_SESSION_TIMEOUT
from seahaven.openenv import serve as serving

pytestmark = pytest.mark.usefixtures("isolated_imports")


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """What `serve` was called with, instead of a server on a port."""
    recorded: list[dict[str, Any]] = []

    def record(world: Any, **options: Any) -> None:
        recorded.append({"world": world, **options})

    monkeypatch.setattr(serving, "serve", record)
    return recorded


@pytest.fixture
def in_a_world(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.chdir(WORLDS / "tidy")
    yield


def test_serve_passes_every_option_through(
    calls: list[dict[str, Any]], in_a_world: None, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        run_cli(
            capsys,
            "serve",
            "--host",
            "127.0.0.1",
            "--port",
            "9999",
            "--max_concurrent_envs",
            "7",
            "--concurrency",
            "3",
            "--session-timeout",
            "60",
            "--include-control-tools",
            "--no-console",
        ).code
        == 0
    )
    (call,) = calls
    assert call["world"].name == "tidy"
    assert call["host"] == "127.0.0.1"
    assert call["port"] == 9999
    assert call["max_concurrent_envs"] == 7
    assert call["concurrency"] == 3
    assert call["session_timeout"] == 60.0
    assert call["include_control_tools"] is True
    assert call["console"] is False


def test_the_defaults_are_the_frameworks(
    calls: list[dict[str, Any]], in_a_world: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """Not the parser's: one set of defaults, and `serve` is where they live."""
    assert run_cli(capsys, "serve").code == 0
    (call,) = calls
    assert call["host"] == serving.DEFAULT_HOST
    assert call["port"] == serving.DEFAULT_PORT
    assert call["max_concurrent_envs"] == DEFAULT_MAX_CONCURRENT_ENVS
    assert call["session_timeout"] == DEFAULT_SESSION_TIMEOUT
    assert call["include_control_tools"] is False
    # The console is on unless it is turned off: a served world is meant to be
    # openable in a browser without a flag.
    assert call["console"] is True
    # `None` is "leave the gate at min(cpus, 16)", which only `serve` knows.
    assert call["concurrency"] is None


def test_session_timeout_zero_disables_the_reaper(
    calls: list[dict[str, Any]], in_a_world: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """OpenEnv wants `None` for no reaper and refuses a `0`."""
    assert run_cli(capsys, "serve", "--session-timeout", "0").code == 0
    assert calls[0]["session_timeout"] is None


def test_concurrency_zero_removes_the_gate(
    calls: list[dict[str, Any]], in_a_world: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """`0` is a value `serve` has to see, and is not the same as not saying."""
    assert run_cli(capsys, "serve", "--concurrency", "0").code == 0
    assert calls[0]["concurrency"] == 0


def test_the_world_option_reaches_serve(
    calls: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.syspath_prepend(str(WORLDS / "tidy" / "src"))
    monkeypatch.chdir(WORLDS / "messy")
    assert run_cli(capsys, "serve", "--world", "tidy:world").code == 0
    assert calls[0]["world"].name == "tidy"


def test_a_missing_serve_extra_names_the_extra(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """A large wheel left out of a default install, and one thing to do about it."""
    monkeypatch.chdir(tmp_path)
    # `None` in `sys.modules` is what Python treats as "this import is blocked".
    monkeypatch.setitem(sys.modules, "seahaven.openenv", None)
    result = run_cli(capsys, "serve")
    assert result.code == 1
    assert result.err.strip() == MISSING_EXTRA
    # The extra is checked before the world, because it is the blocker either way.
    assert "pyproject.toml" not in result.err
