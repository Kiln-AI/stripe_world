"""The attack suite: SQL an agent wrote, against the containment it runs under.

Every test here talks to the sandbox in raw SQLite text, because the sandbox
makes no claim about the text -- only about what SQLite is allowed to do with it.
"""

import time
from collections.abc import Iterator, Sequence
from contextlib import suppress
from types import SimpleNamespace
from typing import Any, cast

import apsw
import pytest

from seahaven import sandbox
from seahaven.db import Db, SqlValue
from seahaven.errors import DbError, WorldBug
from seahaven.sandbox import (
    MAX_VALUE_BYTES,
    Authorizer,
    SqlResult,
    refusal_kind,
    run_statement,
)

SCHEMA = """
CREATE TABLE notes (id TEXT NOT NULL PRIMARY KEY, body TEXT NOT NULL) STRICT;
CREATE TABLE secrets (id TEXT NOT NULL PRIMARY KEY, token TEXT NOT NULL) STRICT;
CREATE VIRTUAL TABLE notes_fts USING fts5(body);
"""

# Each attack, and the refusal the sandbox records for it. Several read as a
# write to `sqlite_master`, because that is genuinely the first thing SQLite asks
# to do when it runs DDL, and the authorizer answers the question it was asked
# rather than guessing at the statement behind it.
DENIED = [
    (
        "INSERT INTO notes (id, body) VALUES ('z', 'zed')",
        "write of table 'notes' in a read-only query",
    ),
    ("UPDATE notes SET body = 'x'", "write of table 'notes' in a read-only query"),
    ("DELETE FROM notes", "write of table 'notes' in a read-only query"),
    ("CREATE TABLE sneaky (x)", "action INSERT 'sqlite_master'"),
    ("DROP TABLE notes", "action DELETE 'sqlite_master'"),
    ("ALTER TABLE notes RENAME TO diary", "action ALTER TABLE 'notes'"),
    ("CREATE INDEX i ON notes (body)", "action INSERT 'sqlite_master'"),
    ("CREATE VIEW v AS SELECT 1", "action INSERT 'sqlite_master'"),
    ("CREATE VIRTUAL TABLE v2 USING fts5(body)", "action INSERT 'sqlite_master'"),
    ("CREATE TEMP TABLE t (x)", "action INSERT 'sqlite_temp_master'"),
    (
        "CREATE TRIGGER stamp AFTER INSERT ON notes BEGIN SELECT 1; END",
        "action CREATE TRIGGER 'stamp'",
    ),
    ("ATTACH DATABASE ':memory:' AS other", "action ATTACH ':memory:'"),
    ("DETACH DATABASE other", "action DETACH 'other'"),
    ("PRAGMA journal_mode = DELETE", "action PRAGMA 'journal_mode'"),
    # Including the introspection ones: what the schema looks like is
    # `describe_schema`'s to answer, not the SQL door's.
    ("PRAGMA table_info(notes)", "action PRAGMA 'table_info'"),
    # `table_xinfo` is the one the changeset session asks for, and the allowance
    # for it is spelled so that only the session can reach it. Asked as the
    # agent's own statement it is refused by the name, like its sibling above.
    ("PRAGMA table_xinfo(notes)", "action PRAGMA 'table_xinfo'"),
    ("PRAGMA data_version = 3", "action PRAGMA 'data_version'"),
    # Asked with no argument at all, which is the shape the one allowed pragma
    # has: these two are refused by the name and by nothing else. Widen the
    # allowlist by either name and this file says so -- `database_list` hands out
    # the instance's absolute path on the host, `compile_options` the build.
    ("PRAGMA database_list", "action PRAGMA 'database_list'"),
    ("PRAGMA compile_options", "action PRAGMA 'compile_options'"),
    ("BEGIN", "action transaction control 'BEGIN'"),
    ("SAVEPOINT s", "action SAVEPOINT 's'"),
    ("REINDEX notes", "action REINDEX 'sqlite_autoindex_notes_1'"),
    ("ANALYZE", "action INSERT 'sqlite_master'"),
    # VACUUM asks for nothing until it runs, so the authorizer never sees it:
    # SQLite's own verdict on the prepared statement is what stops it.
    ("VACUUM", "write in a read-only query"),
    ("SELECT * FROM secrets", "read of table 'secrets'"),
    # The SQLITE_IGNORE regression: ignoring would leave the true count in place,
    # which is the data being refused.
    ("SELECT count(*) FROM secrets", "read of table 'secrets'"),
    ("SELECT load_extension('libsneaky.so')", "function 'load_extension'"),
    ("SELECT sqlite_version()", "function 'sqlite_version'"),
    ("SELECT last_insert_rowid()", "function 'last_insert_rowid'"),
    # FTS5's shadow tables are the index, not the world.
    ("SELECT * FROM notes_fts_data", "read of table 'notes_fts_data'"),
]


@pytest.fixture
def world(db: Db) -> Db:
    db.execute(SCHEMA)
    db.executemany(
        "INSERT INTO notes (id, body) VALUES (?, ?)",
        [(f"n{index}", f"note {index}") for index in range(5)],
    )
    db.execute("INSERT INTO secrets (id, token) VALUES ('s1', 'hunter2')")
    return db


def run(
    db: Db,
    sql: str,
    params: Sequence[SqlValue] = (),
    *,
    tables: Sequence[str] = ("notes",),
    read_only: bool = True,
    functions: frozenset[str] | None = None,
    max_rows: int | None = None,
    max_bytes: int | None = None,
) -> SqlResult:
    authorizer = (
        Authorizer(tables, read_only=read_only)
        if functions is None
        else Authorizer(tables, read_only=read_only, functions=functions)
    )
    return run_statement(
        db, sql, params, authorizer=authorizer, max_rows=max_rows, max_bytes=max_bytes
    )


def refusals(
    db: Db,
    sql: str,
    *,
    tables: Sequence[str] = ("notes",),
    read_only: bool = True,
    functions: frozenset[str] | None = None,
) -> tuple[str, ...]:
    """What the sandbox turned `sql` down for. Fails if it ran."""
    with pytest.raises(DbError) as raised:
        run(db, sql, tables=tables, read_only=read_only, functions=functions)
    assert raised.value.refusals, f"expected a refusal, got {raised.value.sqlite_message}"
    return raised.value.refusals


def test_an_allowed_query_returns_rows_and_columns(world: Db) -> None:
    result = run(world, "SELECT id, body FROM notes ORDER BY id LIMIT 2")

    assert result.columns == ["id", "body"]
    assert result.rows == [["n0", "note 0"], ["n1", "note 1"]]
    assert result.row_count == 2
    assert not result.truncated


def test_columns_are_named_even_with_no_rows(world: Db) -> None:
    result = run(world, "SELECT id, body FROM notes WHERE id = 'nope'")

    assert result.columns == ["id", "body"]
    assert result.rows == []


def test_params_bind_positionally(world: Db) -> None:
    result = run(world, "SELECT id FROM notes WHERE body = ?", ("note 3",))

    assert result.rows == [["n3"]]


@pytest.mark.parametrize(("sql", "refusal"), DENIED)
def test_the_sandbox_refuses(world: Db, sql: str, refusal: str) -> None:
    # The first refusal is the one the agent reads, and some statements ask
    # several questions before SQLite gives up on them.
    assert refusals(world, sql, tables=("notes", "notes_fts"))[0] == refusal


def test_the_one_pragma_that_is_allowed_is_the_data_version_question(world: Db) -> None:
    """FTS5 asks it while *preparing* a `MATCH`, and the authorizer cannot tell who asked.

    So an agent may ask it too, and this pins what that is worth: a counter that
    only another connection's commit moves, which in an instance with one writer
    is the same number on every run. Every other pragma, and this one in its
    assignment form, are in `DENIED` above.
    """
    before = run(world, "PRAGMA data_version").rows
    world.execute("INSERT INTO notes (id, body) VALUES ('n9', 'written since')")

    # Two, not one: the schema was written through the connection `build_blank`
    # used, and another connection's commit is exactly what this counter counts.
    assert before == [[2]]
    # Unmoved by this connection's own write: an instance has one writer, so the
    # agent reads the same number all run.
    assert run(world, "PRAGMA data_version").rows == before
    # SQLite hands the authorizer the pragma name as the agent spelled it, so the
    # allowlist folds it the way every other name in this module is folded.
    assert run(world, "PRAGMA DATA_VERSION").rows == before
    # The upper bound, which no query can state: the refusals above say that
    # these two names are not in the set, and this says that nothing else is
    # either. A pragma allowed here is allowed to every agent in every world.
    assert set(sandbox._PRAGMAS) == {"data_version"}


def test_the_session_shape_question_is_allowed_only_after_a_write_it_follows() -> None:
    """The changeset session's `PRAGMA table_xinfo`, and nothing else, gets through.

    SQLite asks it from inside the agent's statement, the first time the
    instance's session records a change to a table, and a denial poisons the
    session: every later `changeset()` raises `SQLITE_AUTH`. The allowance is
    written as the shape that question always has -- after a row write this same
    statement was already allowed -- so an agent's own `PRAGMA table_xinfo` never
    matches it. `DENIED` above holds the read-only half; this is the writable one.
    """
    authorizer = Authorizer(("notes",), read_only=False)

    # Before any write, on a door that permits writes: still refused.
    assert authorizer(apsw.SQLITE_PRAGMA, "table_xinfo", "notes", "main", None) == apsw.SQLITE_DENY
    assert authorizer.refusals == ("action PRAGMA 'table_xinfo'",)
    authorizer.reset()

    assert authorizer(apsw.SQLITE_INSERT, "notes", None, "main", None) == apsw.SQLITE_OK
    assert authorizer(apsw.SQLITE_PRAGMA, "table_xinfo", "notes", "main", None) == apsw.SQLITE_OK
    # Only the session's question, and only about a table the door lists: the
    # write that was allowed is what says the table is one of them.
    assert (
        authorizer(apsw.SQLITE_PRAGMA, "table_xinfo", "secrets", "main", None) == apsw.SQLITE_DENY
    )
    assert authorizer(apsw.SQLITE_PRAGMA, "table_info", "notes", "main", None) == apsw.SQLITE_DENY
    assert authorizer(apsw.SQLITE_PRAGMA, "table_xinfo", None, "main", None) == apsw.SQLITE_DENY
    assert authorizer.refusals == (
        "action PRAGMA 'table_xinfo'",
        "action PRAGMA 'table_info'",
        "action PRAGMA 'table_xinfo'",
    )

    # Spelled as SQLite hands it over, which is as the asker wrote it.
    assert authorizer(apsw.SQLITE_PRAGMA, "TABLE_XINFO", "NOTES", "main", None) == apsw.SQLITE_OK

    # `reset` is per call, and the permission goes back with it.
    authorizer.reset()
    assert authorizer(apsw.SQLITE_PRAGMA, "table_xinfo", "notes", "main", None) == apsw.SQLITE_DENY


def test_the_shape_question_is_the_tracers_to_refuse_in_the_second_statement(world: Db) -> None:
    """The flag's scope is the call, and the single-statement rule is what bounds it.

    `run_statement` resets the authorizer once per call, not once per statement,
    and SQLite prepares the second statement of a payload before the tracer gets
    to veto it -- so the second statement's `PRAGMA table_xinfo(notes)` really
    does reach `SQLITE_OK` from `_session_asking`. It never steps: the tracer
    refuses it unconditionally, which is the refusal the agent reads. This pins
    the invariant the code has rather than the stronger one the comment used to
    claim, and it pins that the tracer is load-bearing for it.
    """
    listed = ("notes", "sqlite_master")

    def payload(key: str, tail: str) -> str:
        return f"INSERT INTO notes (id, body) VALUES ('{key}', 'zed'); {tail}"

    # A table the door does not list is refused by the authorizer, as ever: the
    # flag is not the only condition, and the other three still hold.
    assert refusals(
        world, payload("k1", "PRAGMA table_xinfo(secrets)"), tables=listed, read_only=False
    ) == ("action PRAGMA 'table_xinfo'",)

    # A table it does list gets through the authorizer, and no further. Both
    # spellings, because folding and schema-qualification take the same door.
    for key, tail in (
        ("k2", "PRAGMA table_xinfo(notes)"),
        ("k3", 'PRAGMA main.table_xinfo("Notes")'),
    ):
        assert refusals(world, payload(key, tail), tables=listed, read_only=False) == (
            "statement after the first in one call",
        )

    # The first statement of each payload did step -- the veto is on the second,
    # not a rollback of the first, and `run_statement` leaves the undo to the
    # caller's transaction. What never ran is the pragma: three payloads, three
    # rows, and no fourth statement's worth of anything.
    assert run(world, "SELECT id FROM notes WHERE id LIKE 'k%' ORDER BY id").rows == [
        ["k1"],
        ["k2"],
        ["k3"],
    ]


def test_a_write_a_read_only_door_refused_does_not_open_the_shape_question() -> None:
    """The flag follows the write that was *allowed*, not the write that was asked for."""
    refused = Authorizer(("notes",), read_only=True)
    assert refused(apsw.SQLITE_INSERT, "notes", None, "main", None) == apsw.SQLITE_DENY
    assert refused(apsw.SQLITE_PRAGMA, "table_xinfo", "notes", "main", None) == apsw.SQLITE_DENY

    unlisted = Authorizer(("notes",), read_only=False)
    assert unlisted(apsw.SQLITE_INSERT, "secrets", None, "main", None) == apsw.SQLITE_DENY
    assert unlisted(apsw.SQLITE_PRAGMA, "table_xinfo", "notes", "main", None) == apsw.SQLITE_DENY


def test_a_refusal_is_what_the_agent_reads(world: Db) -> None:
    with pytest.raises(DbError) as raised:
        run(world, "SELECT * FROM secrets")

    assert raised.value.message == "not allowed: read of table 'secrets'"
    assert raised.value.code == "db_error"
    assert refusal_kind(raised.value.refusals[0]) == "read"


def test_every_refusal_is_classifiable(world: Db) -> None:
    for sql, _ in DENIED:
        for refusal in refusals(world, sql, tables=("notes", "notes_fts")):
            # Raises unless the refusal begins with one of the stable names.
            refusal_kind(refusal)


def test_a_refused_write_says_it_was_a_write(world: Db) -> None:
    # The classification an extension reads: this tool cannot write, which is a
    # different answer from "that statement is not a tool".
    write = refusals(world, "DELETE FROM notes")[0]
    ddl = refusals(world, "DROP TABLE notes")[0]

    assert refusal_kind(write) == "write"
    assert refusal_kind(ddl) == "action"


def test_refusal_kind_refuses_text_that_is_not_a_refusal() -> None:
    with pytest.raises(WorldBug, match="not a refusal"):
        refusal_kind("something else entirely")


def test_table_names_fold_the_way_sqlite_folds_them(world: Db) -> None:
    # A column read arrives canonicalised and a bare row read arrives as the
    # agent spelled it; both have to be allowed.
    assert run(world, "SELECT id FROM NOTES ORDER BY id LIMIT 1").rows == [["n0"]]
    assert run(world, "SELECT count(*) FROM NOTES").rows == [[5]]


def test_the_fold_is_ascii_only() -> None:
    authorizer = Authorizer(["İnbox"])

    assert authorizer(apsw.SQLITE_READ, "İNBOX", "id", "main", None) == apsw.SQLITE_OK
    # `"İnbox".lower()` is `"i̇nbox"`; SQLite treats them as different tables and
    # so must the allowlist.
    assert authorizer(apsw.SQLITE_READ, "i̇nbox", "id", "main", None) == apsw.SQLITE_DENY


def test_a_second_statement_never_runs(world: Db) -> None:
    assert refusals(world, "SELECT 1; SELECT 2") == ("statement after the first in one call",)

    assert refusals(world, "SELECT 1; DROP TABLE notes") == ("action DELETE 'sqlite_master'",)
    assert world.one("SELECT count(*) AS n FROM notes") == {"n": 5}


def test_a_cap_reached_first_truncates_rather_than_refusing(world: Db) -> None:
    payload = "SELECT id FROM notes; INSERT INTO notes (id, body) VALUES ('evil', 'x')"

    # The tracer only sees a statement SQLite is about to run, so a first
    # statement that stops at a cap ends the payload there: the caller is told
    # `truncated` instead of being refused, and what followed never ran.
    result = run(world, payload, tables=("notes",), read_only=False, max_rows=1)

    assert result.truncated
    assert world.one("SELECT count(*) AS n FROM notes") == {"n": 5}
    # The same text with no cap is refused.
    assert refusals(world, payload, read_only=False) == ("statement after the first in one call",)


def test_a_write_the_authorizer_let_through_is_still_refused(world: Db) -> None:
    class Permissive(Authorizer):
        """What an extension must not be able to open up by accident."""

        def __call__(
            self,
            action: int,
            third: str | None,
            fourth: str | None,
            database: str | None,
            trigger: str | None,
            /,
        ) -> int:
            return apsw.SQLITE_OK

    with pytest.raises(DbError) as raised:
        run_statement(
            world,
            "INSERT INTO notes (id, body) VALUES ('z', 'zed')",
            authorizer=Permissive(["notes"]),
        )

    assert raised.value.refusals == ("write in a read-only query",)
    assert world.one("SELECT count(*) AS n FROM notes") == {"n": 5}


def test_writes_go_only_to_the_tables_that_were_listed(world: Db) -> None:
    result = run(
        world,
        "INSERT INTO notes (id, body) VALUES ('z', 'zed')",
        tables=("notes",),
        read_only=False,
    )

    assert result.rows == []
    assert world.one("SELECT body FROM notes WHERE id = 'z'") == {"body": "zed"}
    assert refusals(
        world, "UPDATE secrets SET token = 'x'", tables=("notes",), read_only=False
    ) == ("write of table 'secrets'",)
    assert refusals(world, "DROP TABLE notes", tables=("notes",), read_only=False) == (
        "action DELETE 'sqlite_master'",
    )


def test_the_function_allowlist_is_the_whole_vocabulary(world: Db) -> None:
    assert run(world, "SELECT upper(body) FROM notes ORDER BY id LIMIT 1").rows == [["NOTE 0"]]
    assert run(world, "SELECT json_extract('{\"a\": 1}', '$.a')").rows == [[1]]
    assert run(world, "SELECT current_timestamp").rows[0][0].endswith("Z")
    # Allowed rather than refused because it is seeded: `ids.py`'s override, not
    # SQLite's own function. `test_ids.py` is where the seeding is pinned.
    assert isinstance(run(world, "SELECT random()").rows[0][0], int)


def test_a_random_blob_cannot_grow_past_the_value_cap(world: Db) -> None:
    """The override reads the cap `run_statement` borrowed, before it draws a byte.

    `randomblob` is the one allowed function that allocates a value of the size
    it is given, and the allocation happens in Python rather than inside SQLite.
    Left to SQLite's own 1 GB limit it would be a gigabyte drawn before the
    statement could be stopped, which is exactly what `MAX_VALUE_BYTES` exists to
    prevent; refusing at the cap is what `test_one_value_cannot_grow_without_bound`
    asserts for `printf`.
    """
    assert refusals(world, "SELECT randomblob(1000000000)") == (
        f"value larger than {MAX_VALUE_BYTES} bytes",
    )
    # The refusal is read off the length before a byte is drawn: a draw this
    # size is a `MemoryError` out of Python, which is not a refusal and not a
    # `DbError` either, whatever SQLite would have said about the value after.
    assert refusals(world, "SELECT randomblob(9223372036854775807)") == (
        f"value larger than {MAX_VALUE_BYTES} bytes",
    )
    # The cap and not the function: a blob at the cap is drawn as asked.
    assert run(world, f"SELECT length(randomblob({MAX_VALUE_BYTES}))").rows == [[MAX_VALUE_BYTES]]


def test_a_caller_can_replace_the_function_allowlist(world: Db) -> None:
    counting_only = frozenset({"count"})

    assert run(world, "SELECT count(*) FROM notes", functions=counting_only).rows == [[5]]
    assert refusals(world, "SELECT upper(body) FROM notes", functions=counting_only) == (
        "function 'upper'",
    )


def test_the_function_allowlist_folds_like_sqlite(world: Db) -> None:
    shouting = frozenset({"UPPER"})

    assert run(world, "SELECT upper(body) FROM notes LIMIT 1", functions=shouting).row_count == 1
    assert refusals(world, "SELECT lower(body) FROM notes", functions=shouting) == (
        "function 'lower'",
    )


def test_shadow_tables_can_be_opened_deliberately(world: Db) -> None:
    result = run(world, "SELECT count(*) FROM notes_fts_data", tables=("notes_fts_data",))

    assert result.row_count == 1


def test_one_value_cannot_grow_without_bound(world: Db) -> None:
    started = time.monotonic()

    assert refusals(world, "SELECT printf('%1000000000d', 1)") == (
        f"value larger than {MAX_VALUE_BYTES} bytes",
    )

    # Refused where SQLite's own 1 GB limit would have spent seconds and a
    # gigabyte of memory reaching the same answer.
    assert time.monotonic() - started < 1.0


def test_a_quadratic_builtin_is_a_documented_residual(world: Db) -> None:
    # Nothing acts inside one opcode, so the value cap bounds the memory a single
    # function call takes but only partly bounds its time: at the cap, this shape
    # runs for about eight seconds. A tenth of the size is used here to pin the
    # residual rather than to pay for it.
    haystack = _letters(MAX_VALUE_BYTES // 5)
    needle = _letters(MAX_VALUE_BYTES // 10)

    result = run(world, f"SELECT instr({haystack}, {needle} || 'b')")

    # It ran to the end and was not refused: nothing here bounds the time.
    assert result.rows == [[0]]


def _letters(count: int) -> str:
    """SQL for a string of `count` letters, built inside SQLite."""
    return f"replace(printf('%*c', {count}, 'a'), ' ', 'a')"


def test_max_rows_truncates(world: Db) -> None:
    result = run(world, "SELECT id FROM notes ORDER BY id", max_rows=2)

    assert result.rows == [["n0"], ["n1"]]
    assert result.truncated


def test_max_bytes_truncates(world: Db) -> None:
    result = run(world, "SELECT id, body FROM notes ORDER BY id", max_bytes=40)

    assert 0 < result.row_count < 5
    assert result.truncated


def test_max_bytes_counts_bytes_not_characters(world: Db) -> None:
    world.execute("INSERT INTO notes (id, body) VALUES ('jp', ?)", "日本語のテキストです")

    # 14 characters of JSON, 34 bytes of UTF-8: a cap between the two has to
    # count the bytes the caller asked about.
    result = run(world, "SELECT body FROM notes WHERE id = 'jp'", max_bytes=20)

    assert result.rows == []
    assert result.truncated
    assert run(world, "SELECT body FROM notes WHERE id = 'jp'", max_bytes=40).row_count == 1


def test_a_negative_cap_is_a_mistake(world: Db) -> None:
    with pytest.raises(WorldBug, match="must not be negative"):
        run(world, "SELECT id FROM notes", max_rows=-1)

    with pytest.raises(WorldBug, match="must not be negative"):
        run(world, "SELECT id FROM notes", max_bytes=-1)


def test_unset_caps_mean_the_whole_result(world: Db) -> None:
    result = run(world, "SELECT id FROM notes ORDER BY id")

    assert result.row_count == 5
    assert not result.truncated


def test_bytes_come_back_as_base64(world: Db) -> None:
    result = run(world, "SELECT x'6869' AS blob")

    assert result.rows == [["aGk="]]


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT id FROM notes ORDER BY id",
        "SELECT * FROM secrets",
        "SELECT printf('%1000000000d', 1)",
    ],
)
def test_the_connection_is_left_as_it_was_found(world: Db, sql: str) -> None:
    before = world.conn.limit(apsw.SQLITE_LIMIT_LENGTH)

    # Whether the statement succeeded, truncated or was refused, the shared
    # connection has to come back exactly as it was.
    with suppress(DbError):
        run(world, sql, max_rows=1)

    assert world.conn.authorizer is None
    assert world.conn.limit(apsw.SQLITE_LIMIT_LENGTH) == before
    # And world code, which is not sandboxed, still works.
    assert world.one("SELECT count(*) AS n FROM notes") == {"n": 5}


def test_one_authorizer_forgets_between_statements(world: Db) -> None:
    authorizer = Authorizer(["notes"])

    with pytest.raises(DbError):
        run_statement(world, "SELECT * FROM secrets", authorizer=authorizer)
    assert authorizer.refusals == ("read of table 'secrets'",)

    result = run_statement(world, "SELECT count(*) FROM notes", authorizer=authorizer)

    assert result.rows == [[5]]
    assert authorizer.refusals == ()


def test_a_plain_sql_mistake_is_not_a_refusal(world: Db) -> None:
    with pytest.raises(DbError) as raised:
        run(world, "SELECT missing_column FROM notes")

    assert raised.value.refusals == ()
    assert raised.value.message == "database error"
    assert raised.value.sqlite_message == "no such column: missing_column"


def test_a_connection_that_cannot_even_be_asked_is_still_a_db_error(world: Db) -> None:
    """Everything out of this function is a `DbError`, including what goes wrong before the SQL.

    Opening a cursor and reading the connection's current authorizer and limit
    happen before anything has been borrowed, which is why they sit outside the
    restoring `try` -- but a caller is owed the same error shape there as
    anywhere else, rather than whichever APSW exception the connection happened
    to be in a state to raise.
    """
    world.close()

    with pytest.raises(DbError) as raised:
        run(world, "SELECT id FROM notes")

    assert raised.value.refusals == ()
    assert "closed" in raised.value.sqlite_message


class _Cursor:
    """Enough of an APSW cursor for `run_statement` to drive."""

    def __init__(self) -> None:
        self.exec_trace: Any = None
        self.closed = False

    def execute(self, sql: str, params: Sequence[SqlValue]) -> Iterator[tuple[Any, ...]]:
        return iter([(1,)])

    def close(self, force: bool = False) -> None:
        self.closed = True


class _RefusesToRestore:
    """A connection whose authorizer will not go back to what it was.

    APSW will not do this -- which is the point: the ordering being pinned is
    only reachable if one of the three things `run_statement` has to put back
    fails, and the whole risk of a failing restore is that it is unforeseen.
    """

    def __init__(self) -> None:
        self.authorizer: Any = None
        self.lengths: list[int] = [4242]
        self.cursor_ = _Cursor()

    def cursor(self) -> _Cursor:
        return self.cursor_

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "authorizer" and value is None and "authorizer" in self.__dict__:
            raise apsw.MisuseError("this connection will not take its authorizer back")
        super().__setattr__(name, value)

    def limit(self, which: int, value: int | None = None) -> int:
        if value is None:
            return self.lengths[-1]
        self.lengths.append(value)
        return self.lengths[-2]


def test_every_restore_runs_even_when_one_of_them_fails() -> None:
    """The limit goes back and the cursor is retired even if the authorizer restore raises.

    Three restores in a row would stop at the first failure, leaving the
    sandbox's tighter limit pinned on a long-lived connection for the rest of the
    process and a half-stepped statement outstanding inside the caller's
    transaction. The failure itself is not swallowed.
    """
    conn = _RefusesToRestore()
    db = cast(Db, SimpleNamespace(conn=conn))

    with pytest.raises(apsw.MisuseError):
        run_statement(db, "SELECT 1", authorizer=Authorizer(["notes"]))

    assert conn.lengths == [4242, MAX_VALUE_BYTES, 4242]
    assert conn.cursor_.closed
