"""The schema rules: SH101 to SH104.

Asked of a real database rather than of the text, because the text is not what a
world runs on. The schema is built in memory and then interrogated through
`PRAGMA table_list`, `PRAGMA table_info` and `sqlite_master`, so a `STRICT` that
SQLite did not accept as one, or a primary key spelled in a way nobody on the
review thought of, is answered by the engine and not by a regular expression.

The *line numbers* are the other way round: SQLite records a normalised copy of
the DDL and knows nothing about which file it came from, so the `*.sql` files
under the package are searched for the `CREATE ...` that names the object. A
world whose schema is not on disk -- built by hand, or installed without its
`.sql` files -- still gets every finding, with the package directory and no line.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import apsw

from seahaven.db import SCHEMA_CHECK_CLOCK, SCHEMA_CHECK_SEED, build_blank, shadow_tables
from seahaven.lint import Finding, Target
from seahaven.world import SQL_SUFFIX

__all__ = ["ddl_execution_finding", "run"]

# The expressions that read the machine's clock instead of the instance's. The
# hazard is not the instant -- an instance's overrides already make a DDL default
# read the frozen one -- but the text: what the engine writes is not the
# canonical millisecond form every other door of a world uses, and one format
# across every door is the rule.
#
# `'now'` is matched as text, so a schema whose CHECK constraint allows the
# literal string `'now'` is a false positive. It is written this way because
# `'now'` is a time value to every date function SQLite has, there is no way to
# tell the two apart in `sqlite_master.sql`, and a world with a status called
# `now` is rarer than a world with `datetime('now')` in a trigger.
_WALL_CLOCK = re.compile(r"\bCURRENT_(?:TIMESTAMP|DATE|TIME)\b|'now'", re.IGNORECASE)

# Everything SQLite keeps in `sqlite_master.sql` that a wall-clock search must not
# read: comments, which the engine preserves verbatim inside a statement, and the
# three kinds of quoted text a comment marker can appear inside. The alternation
# is ordered so that a `--` inside a string literal is consumed as part of the
# literal and never as the start of a comment.
_COMMENT_OR_QUOTED = re.compile(
    r"""
      '(?:[^']|'')*'            # a string literal ('' is an escaped quote)
    | "(?:[^"]|"")*"            # a quoted identifier
    | `(?:[^`]|``)*`            # a backquoted identifier, which SQLite also takes
    | \[[^\]]*\]                # a bracketed identifier
    | (?P<line>--[^\n]*)        # a line comment
    | (?P<block>/\*.*?\*/)       # a block comment; an unterminated one is not
                                #   valid DDL, so it never reaches sqlite_master
    """,
    re.VERBOSE | re.DOTALL,
)

# The head of a `CREATE` statement, up to and including the name of the thing it
# creates. `re.M` so `^` is the start of a line, which is what gives the finding
# its line number.
_CREATE = re.compile(
    r"""^[ \t]*CREATE\s+
        (?:TEMP\s+|TEMPORARY\s+)?
        (?:UNIQUE\s+)?
        (?:VIRTUAL\s+)?
        (?:TABLE|INDEX|TRIGGER|VIEW)\s+
        (?:IF\s+NOT\s+EXISTS\s+)?
        (?:"(?P<quoted>[^"]+)"|'(?P<single>[^']+)'|`(?P<back>[^`]+)`
           |\[(?P<bracket>[^\]]+)\]|(?P<bare>\w+))
    """,
    re.IGNORECASE | re.MULTILINE | re.VERBOSE,
)


@dataclass(frozen=True)
class _Place:
    """Where a finding points: a file, and a line in it when there is one."""

    path: Path
    line: int | None


def run(target: Target) -> list[Finding]:
    """SH101 to SH104 over a fresh in-memory build of the world's schema."""
    sources = _sources(target.package_dir)
    try:
        conn = build_blank(
            ":memory:", target.world.schema, clock=SCHEMA_CHECK_CLOCK, seed=SCHEMA_CHECK_SEED
        )
    except apsw.Error as error:
        # Unreachable through `check`, which cannot get a `World` whose DDL does
        # not execute: `World.__init__` builds it too, and `cli/check.py` renders
        # that failure as SH104 from the import. Here for a caller that built a
        # `Target` another way, and so that the rule has one implementation.
        return [ddl_execution_finding(f"the schema does not execute: {error}", target.package_dir)]
    try:
        return _findings(conn, sources, target.package_dir)
    finally:
        conn.close()


def ddl_execution_finding(message: str, path: Path, line: int | None = None) -> Finding:
    """SH104: the DDL does not execute.

    `message` is passed through whole, because the only message worth printing
    here is SQLite's own and it already reaches this function inside the
    framework's sentence about it. Restating it would put the same words on the
    line twice.
    """
    return Finding(
        code="SH104",
        severity="error",
        path=path,
        line=line,
        message=message,
        fix=(
            "fix the statement SQLite names; every *.sql file under schema/ is run in filename"
            " order, against a blank database"
        ),
    )


def _findings(
    conn: apsw.Connection, sources: Sequence[tuple[Path, str]], root: Path
) -> list[Finding]:
    findings: list[Finding] = []
    shadow = shadow_tables(conn)
    for name, kind, strict in _tables(conn):
        # Ordinary tables only. `PRAGMA table_list` also lists views, which have
        # neither property, and virtual tables, which are FTS5's (functional spec
        # §8): one cannot be STRICT and has no primary key of its own, and the
        # module maintains its shadow tables, which are not the world's to
        # declare either way.
        if kind != "table" or name in shadow or name.startswith("sqlite_"):
            continue
        place = _locate(name, sources, root)
        if not strict:
            findings.append(
                Finding(
                    code="SH101",
                    severity="error",
                    path=place.path,
                    line=place.line,
                    message=f"table {name!r} is not STRICT",
                    fix=f"append STRICT to the CREATE TABLE {name}",
                )
            )
        if not _has_primary_key(conn, name):
            findings.append(
                Finding(
                    code="SH102",
                    severity="error",
                    path=place.path,
                    line=place.line,
                    message=f"table {name!r} has no explicit primary key",
                    fix=f"give {name} a PRIMARY KEY; SQLite's implicit rowid is not one",
                )
            )
    findings += _wall_clock_findings(conn, shadow, sources, root)
    return findings


def _wall_clock_findings(
    conn: apsw.Connection,
    shadow: frozenset[str],
    sources: Sequence[tuple[Path, str]],
    root: Path,
) -> list[Finding]:
    """SH103, over every schema object the world owns.

    `sqlite_master` rather than the files, so a wall clock reaches the finding
    wherever it is spelled: a column default, a trigger body, a view, a generated
    column or a partial index.
    """
    findings: list[Finding] = []
    for name, table, sql in _schema_objects(conn):
        if name.startswith("sqlite_") or table in shadow:
            continue
        found = _WALL_CLOCK.search(_without_comments(sql))
        if found is None:
            continue
        place = _locate(name, sources, root)
        findings.append(
            Finding(
                code="SH103",
                severity="error",
                path=place.path,
                line=place.line,
                message=f"{name!r} reads the wall clock: {found.group(0)}",
                fix=(
                    "write the timestamp from world code with ctx.clock.iso(), so every door of the"
                    " world uses one format"
                ),
            )
        )
    return findings


def _without_comments(sql: str) -> str:
    """`sql` with its comments blanked out, and everything else left alone.

    SQLite stores a statement's text as it was written, comments included, so a
    schema that *documents* this rule -- "no CURRENT_TIMESTAMP default here; use
    ctx.clock.iso()", which is the style the scaffold's own DDL uses -- would
    otherwise be reported for the sentence telling its reader not to do the thing.
    SH103 is an error and there is no way to suppress one, so a false positive
    here blocks a commit on a world that is entirely correct.

    Blanked, not deleted, and blanked character for character with newlines kept:
    every offset and every line number in the result is the one it had in the
    file, so `_locate` can read line numbers off the same text the rules read.
    It also means removing a comment cannot join the tokens either side of it
    into a word that was never there.
    """
    return _COMMENT_OR_QUOTED.sub(
        lambda match: (
            _blanked(match.group(0))
            if match.group("line") or match.group("block")
            else match.group(0)
        ),
        sql,
    )


def _blanked(text: str) -> str:
    """`text` as whitespace of the same shape: spaces for everything but newlines."""
    return "".join("\n" if character == "\n" else " " for character in text)


def _tables(conn: apsw.Connection) -> list[tuple[str, str, bool]]:
    """`(name, type, strict)` for every table in the main schema.

    `PRAGMA table_list` is the engine's own answer to "is this STRICT", which is
    the only one worth having: a `STRICT` SQLite did not accept is not one.
    """
    rows = cast(
        list[tuple[str, str, str, int, int, int]],
        conn.execute("PRAGMA main.table_list").fetchall(),
    )
    return [(name, kind, bool(strict)) for _schema, name, kind, _ncol, _wr, strict in rows]


def _has_primary_key(conn: apsw.Connection, table: str) -> bool:
    """Whether any column of `table` is part of a declared primary key."""
    quoted = table.replace('"', '""')
    rows = cast(
        list[tuple[int, str, str, int, object, int]],
        conn.execute(f'PRAGMA main.table_info("{quoted}")').fetchall(),
    )
    return any(pk for *_rest, pk in rows)


def _schema_objects(conn: apsw.Connection) -> list[tuple[str, str, str]]:
    """`(name, tbl_name, sql)` for everything in `sqlite_master` with DDL."""
    return cast(
        list[tuple[str, str, str]],
        conn.execute(
            "SELECT name, tbl_name, sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY name"
        ).fetchall(),
    )


def _sources(package_dir: Path) -> list[tuple[Path, str]]:
    """Every `*.sql` file under the package, in path order, with its text.

    Not just `schema/`: `sql_files(__package__, ...)` names the directory and a
    world may spell it otherwise, and a file that is not part of the schema
    simply never matches an object's name.

    The text is comment-blanked, so a commented-out draft of a statement cannot
    win the line lookup against the live one that follows it. Blanking keeps the
    file's shape, so the line numbers are still the file's.
    """
    sources: list[tuple[Path, str]] = []
    for path in sorted(package_dir.rglob(f"*{SQL_SUFFIX}")):
        try:
            sources.append((path, _without_comments(path.read_text(encoding="utf-8"))))
        except OSError, UnicodeDecodeError:
            # A file the schema could not have been built from either. The world
            # still gets its findings, without a line.
            continue
    return sources


def _locate(name: str, sources: Sequence[tuple[Path, str]], root: Path) -> _Place:
    """The file and line `name` is created on, or the package directory."""
    for path, text in sources:
        for match in _CREATE.finditer(text):
            found = next(group for group in match.groups() if group is not None)
            if found.casefold() == name.casefold():
                return _Place(path, text.count("\n", 0, match.start()) + 1)
    return _Place(root, None)
