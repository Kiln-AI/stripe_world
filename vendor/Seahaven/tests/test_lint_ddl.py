"""The schema rules, one positive and one negative case each.

Each case is a whole world: a `World` built from the schema under test, and the
same schema written to a `*.sql` file beside it, which is what the line numbers
come from. A committed package per malformed table would be a directory of them,
and there is nothing a package adds here -- these rules read a database and some
text.
"""

from pathlib import Path

import pytest

from seahaven.lint import Finding
from seahaven.lint import ddl as ddl_lint
from seahaven.world import World
from tests.conftest import stub_target

CLEAN = """
CREATE TABLE notes (
    id TEXT PRIMARY KEY,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL
) STRICT;
"""


def findings(tmp_path: Path, schema: str, *, on_disk: bool = True) -> list[Finding]:
    """Every DDL finding for `schema`, with the schema written where the lint looks."""
    package_dir = tmp_path / "pkg"
    (package_dir / "schema").mkdir(parents=True)
    if on_disk:
        (package_dir / "schema" / "001_core.sql").write_text(schema, encoding="utf-8")
    world = World(
        "linted",
        "1.0.0",
        schema,
        fixtures_dir=tmp_path / "fixtures",
        work_dir=tmp_path / "work",
        state_format="seahaven.state/1",
    )
    return ddl_lint.run(stub_target(world, package_dir))


def codes(found: list[Finding]) -> list[str]:
    return [finding.code for finding in found]


def test_a_clean_schema_has_no_findings(tmp_path: Path) -> None:
    assert findings(tmp_path, CLEAN) == []


def test_a_table_that_is_not_strict_is_sh101(tmp_path: Path) -> None:
    schema = "CREATE TABLE notes (\n    id TEXT PRIMARY KEY\n);\n"
    (finding,) = findings(tmp_path, schema)
    assert finding.code == "SH101"
    assert finding.severity == "error"
    assert "notes" in finding.message
    assert "STRICT" in finding.fix


def test_sh101s_line_is_the_create_table_line(tmp_path: Path) -> None:
    """The finding has to send its reader to the statement, not to the file."""
    schema = "-- a comment\n-- another\nCREATE TABLE notes (\n    id TEXT PRIMARY KEY\n);\n"
    (finding,) = findings(tmp_path, schema)
    assert finding.line == 3
    assert finding.path.name == "001_core.sql"


def test_a_finding_with_no_source_on_disk_carries_no_line(tmp_path: Path) -> None:
    """An installed world may ship no `*.sql`; it still gets every finding."""
    schema = "CREATE TABLE notes (id TEXT PRIMARY KEY);\n"
    (finding,) = findings(tmp_path, schema, on_disk=False)
    assert finding.code == "SH101"
    assert finding.line is None
    assert "SH101 error" in finding.render()


def test_a_table_with_no_primary_key_is_sh102(tmp_path: Path) -> None:
    schema = "CREATE TABLE notes (id TEXT NOT NULL) STRICT;\n"
    (finding,) = findings(tmp_path, schema)
    assert finding.code == "SH102"
    assert "primary key" in finding.message


@pytest.mark.parametrize(
    "declaration",
    [
        pytest.param("id INTEGER PRIMARY KEY", id="an integer primary key"),
        pytest.param("id TEXT, PRIMARY KEY (id)", id="a table constraint"),
        pytest.param("a TEXT, b TEXT, PRIMARY KEY (a, b)", id="a composite key"),
    ],
)
def test_every_way_of_declaring_a_primary_key_counts(tmp_path: Path, declaration: str) -> None:
    assert findings(tmp_path, f"CREATE TABLE notes ({declaration}) STRICT;\n") == []


def test_a_table_can_fail_both_rules_at_once(tmp_path: Path) -> None:
    assert codes(findings(tmp_path, "CREATE TABLE notes (id TEXT);\n")) == ["SH101", "SH102"]


@pytest.mark.parametrize(
    "schema",
    [
        pytest.param(
            "CREATE TABLE notes (id TEXT PRIMARY KEY, at TEXT DEFAULT CURRENT_TIMESTAMP) STRICT;",
            id="a CURRENT_TIMESTAMP default",
        ),
        pytest.param(
            "CREATE TABLE notes (id TEXT PRIMARY KEY, on_ TEXT DEFAULT CURRENT_DATE) STRICT;",
            id="a CURRENT_DATE default",
        ),
        pytest.param(
            "CREATE TABLE notes (id TEXT PRIMARY KEY, at TEXT DEFAULT CURRENT_TIME) STRICT;",
            id="a CURRENT_TIME default",
        ),
        pytest.param(
            "CREATE TABLE notes (id TEXT PRIMARY KEY, at TEXT"
            " GENERATED ALWAYS AS (datetime('now'))) STRICT;",
            id="a generated column",
        ),
    ],
)
def test_a_wall_clock_in_a_table_is_sh103(tmp_path: Path, schema: str) -> None:
    (finding,) = findings(tmp_path, schema)
    assert finding.code == "SH103"
    assert "ctx.clock.iso()" in finding.fix


def test_a_wall_clock_in_a_trigger_is_sh103(tmp_path: Path) -> None:
    """Not just defaults: a trigger writes timestamps too, and SQLite records it."""
    schema = (
        CLEAN + "\nCREATE TRIGGER touch AFTER UPDATE ON notes BEGIN\n"
        "  UPDATE notes SET created_at = datetime('now') WHERE id = NEW.id;\nEND;\n"
    )
    (finding,) = findings(tmp_path, schema)
    assert finding.code == "SH103"
    assert "touch" in finding.message


def test_a_wall_clock_in_a_view_is_sh103(tmp_path: Path) -> None:
    schema = CLEAN + "\nCREATE VIEW recent AS SELECT *, CURRENT_TIMESTAMP AS seen_at FROM notes;\n"
    (finding,) = findings(tmp_path, schema)
    assert finding.code == "SH103"
    assert "recent" in finding.message


def test_a_wall_clock_in_a_partial_index_is_sh103(tmp_path: Path) -> None:
    schema = CLEAN + "\nCREATE INDEX fresh ON notes (id) WHERE created_at > datetime('now');\n"
    (finding,) = findings(tmp_path, schema)
    assert finding.code == "SH103"
    assert "fresh" in finding.message


def test_an_fts5_table_is_exempt_from_strict_and_the_primary_key_rule(tmp_path: Path) -> None:
    """A virtual table cannot be either, and its shadow tables are the module's."""
    schema = CLEAN + "\nCREATE VIRTUAL TABLE notes_fts USING fts5(body, content='notes');\n"
    assert findings(tmp_path, schema) == []


def test_ddl_that_does_not_execute_is_sh104_in_sqlites_words() -> None:
    """The one rule with no `World` behind it: there is no world to build."""
    finding = ddl_lint.ddl_execution_finding('near "NOT": syntax error', Path("schema/001.sql"))
    assert finding.code == "SH104"
    assert finding.severity == "error"
    assert 'near "NOT"' in finding.message
    assert "filename order" in finding.fix


@pytest.mark.parametrize(
    "comment",
    [
        pytest.param("-- never add a CURRENT_TIMESTAMP default; use ctx.clock.iso()", id="a line"),
        pytest.param("/* no CURRENT_DATE here */", id="a block"),
    ],
)
def test_a_comment_documenting_the_rule_is_not_a_finding(tmp_path: Path, comment: str) -> None:
    """SQLite keeps a statement's comments, and SH103 is an error with no way off.

    A schema that says "do not put a wall clock here" -- which is the style the
    scaffold's own DDL uses -- would otherwise fail `check` on the sentence
    telling its reader not to do the thing.
    """
    schema = f"CREATE TABLE notes (\n    {comment}\n    id TEXT PRIMARY KEY\n) STRICT;\n"
    assert findings(tmp_path, schema) == []


def test_a_wall_clock_beside_a_comment_is_still_a_finding(tmp_path: Path) -> None:
    """Blanking comments must not blank the statement they are in."""
    schema = (
        "CREATE TABLE notes (\n"
        "    -- documented, and then done anyway\n"
        "    id TEXT PRIMARY KEY,\n"
        "    at TEXT DEFAULT CURRENT_TIMESTAMP\n"
        ") STRICT;\n"
    )
    assert codes(findings(tmp_path, schema)) == ["SH103"]


def test_a_comment_marker_inside_a_string_literal_is_not_a_comment(tmp_path: Path) -> None:
    """`'--'` is a value, and the text after it is still the statement."""
    schema = (
        "CREATE TABLE notes (\n"
        "    id TEXT PRIMARY KEY,\n"
        "    sep TEXT NOT NULL DEFAULT '-- ',\n"
        "    at TEXT DEFAULT CURRENT_TIMESTAMP\n"
        ") STRICT;\n"
    )
    assert codes(findings(tmp_path, schema)) == ["SH103"]


def test_a_commented_out_draft_does_not_win_the_line_lookup(tmp_path: Path) -> None:
    """The live statement is the one a finding has to point at.

    `-- CREATE TABLE …` is safe on its own, because the `CREATE` regex anchors to
    the start of a line; a block comment around a draft is not.
    """
    schema = (
        "/* an earlier draft:\n"
        "CREATE TABLE notes (id TEXT PRIMARY KEY) STRICT;\n"
        "*/\n"
        "\n"
        "CREATE TABLE notes (id TEXT PRIMARY KEY);\n"
    )
    (finding,) = findings(tmp_path, schema)
    assert (finding.code, finding.line) == ("SH101", 5)


def test_blanking_comments_keeps_the_files_line_numbers(tmp_path: Path) -> None:
    """A comment is blanked in place, not removed, or every line below it moves."""
    schema = "-- one\n/* two\n   three */\nCREATE TABLE notes (id TEXT PRIMARY KEY);\n"
    (finding,) = findings(tmp_path, schema)
    assert finding.line == 4
