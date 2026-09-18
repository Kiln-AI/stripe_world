"""`seahaven docs`: one line of output, and it has to be true.

The whole point of bundling the docs is that an authoring agent can find the ones
that match the version it has installed. A command that prints a path which is
not there would be worse than no command, so the test is that the path exists and
that `index.md` is in it.
"""

from pathlib import Path

import pytest

from seahaven.cli.docs import docs_path
from tests.conftest import run_cli


def test_docs_prints_a_directory_that_exists_and_holds_index_md(
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = run_cli(capsys, "docs")
    assert result.code == 0
    (printed,) = result.lines
    directory = Path(printed)
    assert directory.is_dir()
    assert (directory / "index.md").is_file()


def test_docs_prints_the_path_and_nothing_else(capsys: pytest.CaptureFixture[str]) -> None:
    """The output composes: `cat "$(seahaven docs)/index.md"`."""
    result = run_cli(capsys, "docs")
    assert result.out == f"{docs_path()}\n"
    assert result.err == ""


def test_docs_needs_no_world(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """It is the command an author runs before there is a world to find."""
    monkeypatch.chdir(tmp_path)
    assert run_cli(capsys, "docs").code == 0
