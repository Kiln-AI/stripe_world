"""`seahaven fixture`, driven on a scaffolded world the way its author would.

A scaffold rather than one of `tests/worlds/`: `seahaven new` writes
`fixtures_src/generate.py` precisely so `--run fixtures_src.generate:<id>` has
something to name, and the round trip through both is the thing worth proving.
"""

from pathlib import Path

import pytest

from seahaven.cli import CliError
from seahaven.cli.fixture import _generator
from seahaven.cli.new import render
from seahaven.fixtures import SIDECAR_NAME, load
from tests.conftest import CliResult, run_cli

pytestmark = pytest.mark.usefixtures("isolated_imports")

NOW = "2026-01-01T00:00:00.000Z"
GENERATOR = "fixtures_src.generate:empty"


@pytest.fixture
def world_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A scaffolded world, with the shell sitting in it."""
    scaffold = render("scaffolded", tmp_path / "scaffolded")
    monkeypatch.chdir(scaffold)
    return scaffold


def freeze(capsys: pytest.CaptureFixture[str], *argv: str) -> CliResult:
    return run_cli(capsys, "fixture", "freeze", *argv)


def test_freeze_writes_a_fixture_and_prints_its_sidecar(
    world_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    result = freeze(capsys, "empty", "--run", GENERATOR, "--description", "No rows.", "--now", NOW)
    assert result.code == 0
    fixture = load(world_dir / "fixtures" / "empty")
    assert fixture.now == NOW
    assert fixture.parent_id is None
    assert fixture.description == "No rows."
    # The sidecar as it was written: the record of what was minted.
    assert (world_dir / "fixtures" / "empty" / SIDECAR_NAME).read_text(
        encoding="utf-8"
    ) in result.out


def test_freeze_defaults_to_the_wall_clock(
    world_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A blank instance with no `--now` is one of a world's two wall-clock reads."""
    assert freeze(capsys, "empty", "--run", GENERATOR, "--description", "Now-ish.").code == 0
    fixture = load(world_dir / "fixtures" / "empty")
    assert fixture.now != NOW
    assert fixture.now.endswith("Z")


def test_fork_records_its_parent(world_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert (
        freeze(capsys, "empty", "--run", GENERATOR, "--description", "No rows.", "--now", NOW).code
        == 0
    )
    result = run_cli(
        capsys, "fixture", "fork", "empty", "forked", "--run", GENERATOR, "--description", "A fork."
    )
    assert result.code == 0
    forked = load(world_dir / "fixtures" / "forked")
    assert forked.parent_id == "empty"
    # A fork inherits the instant it was forked from; nothing reads a clock here.
    assert forked.now == NOW


def test_a_frozen_fixture_passes_check(world_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """What `seahaven fixture` mints is what SH401 to SH405 expect to find."""
    assert freeze(capsys, "empty", "--run", GENERATOR, "--description", "No rows.").code == 0
    result = run_cli(capsys, "check")
    assert (result.code, result.out) == (0, "")


def test_a_generator_that_raises_leaves_nothing_behind(
    world_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (world_dir / "fixtures_src" / "broken.py").write_text(
        "import seahaven\n\n\n"
        "def half(inst: seahaven.Instance) -> None:\n"
        '    inst.call("create_item", name="one")\n'
        '    raise RuntimeError("the generator gave up")\n',
        encoding="utf-8",
    )
    # The author's own code failing, so the traceback is the useful output and is
    # let through; what the CLI owes is that nothing is left behind.
    with pytest.raises(RuntimeError, match="the generator gave up"):
        run_cli(
            capsys,
            "fixture",
            "freeze",
            "empty",
            "--run",
            "fixtures_src.broken:half",
            "--description",
            "x",
        )
    assert list((world_dir / "fixtures").iterdir()) == [world_dir / "fixtures" / ".gitkeep"]


def test_freezing_over_an_existing_id_is_refused(
    world_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Fixtures are immutable: rebuilding one means deleting it first, deliberately."""
    assert freeze(capsys, "empty", "--run", GENERATOR, "--description", "No rows.").code == 0
    again = freeze(capsys, "empty", "--run", GENERATOR, "--description", "Again.")
    assert again.code == 1
    assert "already exists" in again.err


@pytest.mark.parametrize("spec", ["fixtures_src.generate", "", ":empty"])
def test_a_malformed_run_target_says_what_it_wanted(world_dir: Path, spec: str) -> None:
    with pytest.raises(CliError) as raised:
        _generator(spec)
    assert "module:function" in str(raised.value)


def test_a_run_target_that_does_not_import_is_a_user_error(
    world_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    result = freeze(capsys, "empty", "--run", "nothing.here:empty", "--description", "x")
    assert result.code == 1
    assert "cannot import" in result.err


def test_a_run_target_that_is_not_callable_is_a_user_error(
    world_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    result = freeze(capsys, "empty", "--run", "fixtures_src.generate:missing", "--description", "x")
    assert result.code == 1
    assert "a generator is a function" in result.err


def test_list_prints_id_parent_now_and_description(
    world_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        freeze(capsys, "empty", "--run", GENERATOR, "--description", "No rows.", "--now", NOW).code
        == 0
    )
    assert (
        run_cli(
            capsys,
            "fixture",
            "fork",
            "empty",
            "forked",
            "--run",
            GENERATOR,
            "--description",
            "A fork.",
        ).code
        == 0
    )
    result = run_cli(capsys, "fixture", "list")
    assert result.code == 0
    assert result.lines == [
        f"empty\t-\t{NOW}\tNo rows.",
        f"forked\tempty\t{NOW}\tA fork.",
    ]


def test_list_on_a_world_with_no_fixtures_prints_nothing(
    world_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    result = run_cli(capsys, "fixture", "list")
    assert (result.code, result.out) == (0, "")


def test_a_now_that_is_not_a_timestamp_is_a_user_error(
    world_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    result = freeze(capsys, "empty", "--run", GENERATOR, "--description", "x", "--now", "tuesday")
    assert result.code == 1
    assert "not a timestamp" in result.err
