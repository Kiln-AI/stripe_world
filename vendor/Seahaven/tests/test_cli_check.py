"""`seahaven check` end to end: the line it prints, the order, and the exit code.

The rules themselves are tested one at a time in `test_lint_*.py`. What is left
here is the command: the shape of a finding on a terminal, that warnings alone do
not fail a build, and that a world which could not be imported is reported as a
line with a code on it rather than as a traceback -- which is the difference
between an authoring agent fixing the problem and an authoring agent guessing.
"""

import re
from pathlib import Path

import pytest

from seahaven.cli import CliError
from seahaven.cli.check import collect
from seahaven.cli.new import render
from tests.conftest import WORLDS, run_cli

pytestmark = pytest.mark.usefixtures("isolated_imports")

# `<code> <severity> <path>[:<line>]  <message>  fix: <sentence>`
FINDING_LINE = re.compile(r"^SH\d{3} (error|warning) \S+  .+  fix: .+$")


def check(
    world: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], *argv: str
) -> tuple[int, list[str]]:
    monkeypatch.chdir(world)
    result = run_cli(capsys, "check", *argv)
    return result.code, result.lines


def test_a_clean_world_prints_nothing_and_exits_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert check(WORLDS / "tidy", monkeypatch, capsys) == (0, [])


def test_every_finding_has_the_documented_shape(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _code, lines = check(WORLDS / "messy", monkeypatch, capsys)
    assert lines
    for line in lines:
        assert FINDING_LINE.match(line), line


def test_findings_are_sorted_by_code_then_path(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _code, lines = check(WORLDS / "messy", monkeypatch, capsys)
    assert [line.split(" ", 1)[0] for line in lines] == sorted(
        line.split(" ", 1)[0] for line in lines
    )


def test_paths_are_printed_relative_to_the_project(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _code, lines = check(WORLDS / "messy", monkeypatch, capsys)
    places = [line.split(" ")[2] for line in lines]
    assert any(place.startswith("src/messy/tools/notes.py:") for place in places)
    assert not any(place.startswith("/") for place in places)


def test_paths_are_relative_to_the_project_from_a_subdirectory_too(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Discovery walks up to the project, so the paths it reports come from there.

    Otherwise a check run from `src/<pkg>/middleware/` prints every path absolute,
    and the same file is described two ways depending on where the shell was.
    """
    _code, lines = check(WORLDS / "messy" / "src" / "messy" / "middleware", monkeypatch, capsys)
    places = [line.split(" ")[2] for line in lines]
    assert any(place.startswith("src/messy/tools/notes.py:") for place in places)
    assert not any(place.startswith("/") for place in places)


def test_an_error_exits_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`messy` has SH301, which is an error."""
    code, lines = check(WORLDS / "messy", monkeypatch, capsys)
    assert code == 1
    assert any(line.startswith("SH301 error") for line in lines)


def test_warnings_alone_exit_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A warning is a thing to look at, not a thing that stops a commit."""
    scaffold = render("warned", tmp_path / "warned")
    tool = scaffold / "src" / "warned" / "tools" / "items.py"
    tool.write_text(
        tool.read_text(encoding="utf-8").replace(
            "from typing import Any", "from datetime import datetime\nfrom typing import Any"
        )
        + "\n\n@world.tool\ndef when(ctx: seahaven.Ctx) -> str:\n"
        '    """The time, read from the wrong clock."""\n'
        "    return datetime.now().isoformat()\n",
        encoding="utf-8",
    )
    code, (line,) = check(scaffold, monkeypatch, capsys)
    assert line.startswith("SH201 warning")
    assert code == 0


def test_ddl_that_does_not_execute_is_sh104_with_sqlites_message(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, (line,) = check(WORLDS / "broken_ddl", monkeypatch, capsys)
    assert code == 1
    assert line.startswith("SH104 error")
    assert "TEXTUAL" in line
    assert "Traceback" not in line


def test_a_world_that_does_not_seal_is_a_line_and_not_a_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The whole point of sealing first: a registration error arrives as a finding.

    `unsealed` also carries an undescribed tool, so this asserts the other half
    too -- a world whose tree does not resolve still gets every rule that needs
    no tree.
    """
    code, lines = check(WORLDS / "unsealed", monkeypatch, capsys)
    assert code == 1
    for line in lines:
        assert FINDING_LINE.match(line), line
    assert [line.split(" ", 1)[0] for line in lines] == ["SH205", "SH504"]
    assert "tool_allow_list names 'post_entrie'" in lines[1]


def test_a_package_with_no_world_is_sh501(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, (line,) = check(WORLDS / "no_world", monkeypatch, capsys)
    assert code == 1
    assert line.startswith("SH501 error")
    assert "world = seahaven.World(...)" in line


def test_an_import_error_is_sh501_with_the_last_traceback_line() -> None:
    (finding,) = collect("no_such_module_at_all:world", WORLDS / "tidy").findings
    assert finding.code == "SH501"
    assert "ModuleNotFoundError" in finding.message
    assert "\n" not in finding.message


def test_a_user_error_is_not_a_finding() -> None:
    """No project to lint is the user's problem, and there is nothing to report."""
    with pytest.raises(CliError):
        collect("not-a-spec", WORLDS / "tidy")


def test_the_world_option_reaches_check(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.syspath_prepend(str(WORLDS / "tidy" / "src"))
    monkeypatch.chdir(WORLDS / "messy")
    result = run_cli(capsys, "check", "--world", "tidy:world")
    assert result.code == 0
    assert result.out == ""


def test_the_reference_world_passes_check(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The lints against a real world, which is the only thing that proves them.

    `tests/worlds/` is four packages written to make rules fire; ProjectTracker is
    a world built to be a world, and a rule that is wrong about a real one is
    wrong. This is also what would catch a rule that fires on every fixture of
    every world -- a fixture is committed, and git hands back what git stores.
    """
    reference = Path(__file__).resolve().parent.parent / "worlds" / "projecttracker"
    assert check(reference, monkeypatch, capsys) == (0, [])


def test_sh501s_fix_does_not_restate_its_message(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The message already names the export and the override; once is enough."""
    _code, (line,) = check(WORLDS / "no_world", monkeypatch, capsys)
    message, _, fix = line.partition("  fix: ")
    assert "world = seahaven.World(...)" in message
    assert "world = seahaven.World(...)" not in fix


def test_a_world_that_is_one_module_is_refused_in_a_sentence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Functional spec §2.1: a world is a package. Refusing is right; crashing is not."""
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "flat"\n', encoding="utf-8")
    (tmp_path / "flat.py").write_text(
        "import seahaven\n\n"
        "world = seahaven.World(\n"
        '    "flat", "1.0.0", "CREATE TABLE t (id TEXT PRIMARY KEY) STRICT;",\n'
        '    state_format="seahaven.state/1",\n'
        ")\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    result = run_cli(capsys, "check")
    assert result.code == 1
    assert "a world is a package" in result.err
    assert "Traceback" not in result.err


def test_the_world_option_may_name_the_module_the_world_is_built_in(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--world tidy.world:world` is the natural spelling, and the lints need `tidy`."""
    monkeypatch.syspath_prepend(str(WORLDS / "tidy" / "src"))
    monkeypatch.chdir(WORLDS / "messy")
    result = run_cli(capsys, "check", "--world", "tidy.world:world")
    assert (result.code, result.out) == (0, "")


def test_a_subpackage_that_raises_on_import_does_not_crash_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`pkgutil.walk_packages` imports what it recurses into; a lint must survive it."""
    scaffold = render("exploding", tmp_path / "exploding")
    legacy = scaffold / "src" / "exploding" / "tools" / "legacy"
    legacy.mkdir()
    (legacy / "__init__.py").write_text('raise ValueError("boom")\n', encoding="utf-8")
    code, lines = check(scaffold, monkeypatch, capsys)
    assert code == 1
    assert any("exploding.tools.legacy" in line for line in lines)
    assert all(line.startswith("SH301 error") for line in lines)
