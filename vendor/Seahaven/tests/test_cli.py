"""The command itself: its exit codes, its streams, and the entry point that names it.

Three exit codes and nothing else. 0 is success, 1 is a finding or a user error
with one line on stderr, and 2 is argparse's own for a usage mistake. A traceback
is none of them, which is the whole point of `CliError`.
"""

import tomllib
from pathlib import Path

import pytest

from seahaven.cli import CliError, build_parser, main
from seahaven.cli.new import render
from seahaven.world import DDL_DOES_NOT_EXECUTE, World
from tests.conftest import NOTES_SCHEMA, run_cli

pytestmark = pytest.mark.usefixtures("isolated_imports")

REPOSITORY = Path(__file__).resolve().parent.parent


def test_the_entry_point_is_declared() -> None:
    """`[project.scripts] seahaven` is what puts the command on a PATH."""
    data = tomllib.loads((REPOSITORY / "pyproject.toml").read_text(encoding="utf-8"))
    assert data["project"]["scripts"] == {"seahaven": "seahaven.cli:main"}


@pytest.mark.parametrize("name", ["new", "check", "docs", "fixture", "serve"])
def test_every_subcommand_the_spec_names_is_there(name: str) -> None:
    with pytest.raises(SystemExit) as raised:
        build_parser().parse_args([name, "--help"])
    assert raised.value.code == 0


@pytest.mark.parametrize("action", ["list", "freeze", "fork"])
def test_the_fixture_subcommand_has_its_three_actions(action: str) -> None:
    with pytest.raises(SystemExit) as raised:
        build_parser().parse_args(["fixture", action, "--help"])
    assert raised.value.code == 0


def test_an_action_the_fixture_subcommand_does_not_have_is_exit_two() -> None:
    with pytest.raises(SystemExit) as raised:
        main(["fixture", "sprinkle"])
    assert raised.value.code == 2


@pytest.mark.parametrize("argv", [[], ["nonesuch"], ["check", "--nonesuch"]])
def test_a_usage_mistake_is_exit_two(argv: list[str]) -> None:
    """argparse's own code, left alone: 1 is reserved for something that ran."""
    with pytest.raises(SystemExit) as raised:
        main(argv)
    assert raised.value.code == 2


def test_a_user_error_is_one_line_on_stderr_and_exit_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    result = run_cli(capsys, "check")
    assert result.code == 1
    assert result.out == ""
    assert len(result.err.splitlines()) == 1
    assert "Traceback" not in result.err


def test_a_seahaven_error_is_reported_the_same_way(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A `WorldBug` out of the framework is an author's error, not a crash report.

    The generator has to be one that imports: `--run` is resolved before the
    instance is made, so a bad `--run` would raise `CliError` and the fixture
    that does not exist would never be reached -- and the `except SeahavenError`
    arm of `main` would go untested behind a passing assertion.
    """
    monkeypatch.chdir(render("forkable", tmp_path / "forkable"))
    result = run_cli(
        capsys,
        "fixture",
        "fork",
        "nosuchfixture",
        "x",
        "--run",
        "fixtures_src.generate:empty",
        "--description",
        "d",
    )
    assert result.code == 1
    assert "nosuchfixture" in result.err
    assert len(result.err.splitlines()) == 1
    assert "Traceback" not in result.err


def test_a_cli_error_carries_the_code_check_renders_it_with() -> None:
    assert CliError("no world here").code is None
    assert CliError("no world here", code="SH501").code == "SH501"


def test_the_ddl_failure_message_is_the_constant_check_matches_on(tmp_path: Path) -> None:
    """`cli.check` tells SH104 from SH501 by this phrase, so the two must not drift."""
    with pytest.raises(Exception) as raised:
        World(
            "broken",
            "1.0.0",
            "CREATE TABLE t (a NOTATYPE) STRICT;",
            fixtures_dir=tmp_path,
            state_format="seahaven.state/1",
        )
    assert DDL_DOES_NOT_EXECUTE in str(raised.value)
    # And a world whose DDL is fine never says it.
    World(
        "fine",
        "1.0.0",
        NOTES_SCHEMA,
        fixtures_dir=tmp_path,
        work_dir=tmp_path,
        state_format="seahaven.state/1",
    )
