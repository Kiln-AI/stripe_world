"""The SQL door, driven the way an agent drives it: `instance.call("run_sql", query=...)`.

Nothing here calls the tool function directly. The containment is the sandbox's
and has its own attack suite in `test_sandbox.py`; what is tested here is the
door built on it -- the allowlists it computes, the result it answers with, the
message it shows, and the fact that one call's refusal is only ever its own.
"""

import threading
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import apsw
import pytest

from seahaven import sandbox
from seahaven.db import Db, SqlValue
from seahaven.errors import ArgumentError, DbError, WorldBug
from seahaven.helpers import run_sql
from seahaven.instances import Instance
from seahaven.sandbox import Authorizer, SqlResult
from seahaven.tool import Tool
from seahaven.world import World
from tests.conftest import Caller, build_world
from tests.fold_support import fold

SCHEMA = """
CREATE TABLE issues (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    points INTEGER NOT NULL DEFAULT 0
) STRICT;

CREATE TABLE attachments (
    id TEXT PRIMARY KEY,
    blob BLOB
) STRICT;

CREATE TABLE salaries (
    person TEXT PRIMARY KEY,
    amount INTEGER NOT NULL
) STRICT;

CREATE TABLE audit (
    id TEXT PRIMARY KEY,
    note TEXT NOT NULL
) STRICT;
"""

# The tables the door offers. `salaries` and `audit` are in the schema and are
# deliberately not among them: they are what the refusals here are refused from.
OFFERED = ("issues", "attachments")


def sql_world(tmp_path: Path, **options: Any) -> World:
    """A world whose SQL door is built from `options`."""
    world = build_world(tmp_path, SCHEMA)
    world.tool(run_sql(tables=OFFERED, **options))
    return world


@pytest.fixture
def reader(tmp_path: Path) -> Any:
    """A live instance of a read-only door, with two issues in it."""
    world = sql_world(tmp_path)
    with world.instance(None) as instance:
        seed(instance)
        yield instance


def seed(instance: Instance) -> None:
    with instance.bulk() as ctx:
        ctx.db.execute("INSERT INTO issues VALUES ('i1', 'first', 3), ('i2', 'second', 5)")
        ctx.db.execute("INSERT INTO salaries VALUES ('alice', 100)")


def ask(instance: Instance, query: str, tool: str = "run_sql") -> dict[str, Any]:
    result = instance.call(tool, query=query)
    assert isinstance(result, dict)
    return result


def test_a_read_of_a_listed_table_answers_with_columns_and_rows(reader: Instance) -> None:
    assert ask(reader, "SELECT id, points FROM issues ORDER BY id") == {
        "columns": ["id", "points"],
        "rows": [["i1", 3], ["i2", 5]],
        "row_count": 2,
        "truncated": False,
    }


def test_a_query_that_matches_nothing_still_names_its_columns(reader: Instance) -> None:
    """The empty result is the one an agent has to be able to read the shape of."""
    assert ask(reader, "SELECT id, title FROM issues WHERE points > 99") == {
        "columns": ["id", "title"],
        "rows": [],
        "row_count": 0,
        "truncated": False,
    }


def test_a_read_of_a_table_the_world_did_not_list_is_refused(reader: Instance) -> None:
    with pytest.raises(DbError) as raised:
        ask(reader, "SELECT amount FROM salaries")

    error = raised.value
    assert error.refusals == ("read of table 'salaries'",)
    assert error.message == "not allowed: read of table 'salaries'"
    assert error.details == {"refusals": ["read of table 'salaries'"]}


def test_explain_is_a_read_like_any_other(reader: Instance) -> None:
    """Answered, not refused -- and prepared under the same allowlist.

    The decision this pins is that a SQL door does not special-case `EXPLAIN`.
    SQLite prepares it exactly as it prepares the statement itself, so the
    authorizer is asked the same questions and an agent learns nothing about a
    table it could not have selected from; what comes back is the VDBE program,
    which is columns and rows like anything else. A product that exposes SQL
    exposes its `EXPLAIN`, and refusing it would be this framework inventing a
    rule the thing it mimics does not have.
    """
    result = ask(reader, "EXPLAIN SELECT id FROM issues")

    assert result["columns"][:2] == ["addr", "opcode"]
    assert result["row_count"] > 0
    with pytest.raises(DbError) as raised:
        ask(reader, "EXPLAIN SELECT amount FROM salaries")

    assert raised.value.refusals == ("read of table 'salaries'",)


def test_counting_a_denied_table_is_refused_too(reader: Instance) -> None:
    """`count(*)` reads no column, so it arrives as a bare row read: the shape that folds."""
    with pytest.raises(DbError, match="not allowed") as raised:
        ask(reader, "SELECT count(*) FROM SALARIES")

    assert raised.value.refusals == ("read of table 'SALARIES'",)


def test_a_write_is_refused_when_the_door_is_read_only(reader: Instance) -> None:
    with pytest.raises(DbError) as raised:
        ask(reader, "UPDATE issues SET points = 0")

    assert raised.value.refusals == ("write of table 'issues' in a read-only query",)
    assert reader.call("rows", sql="SELECT points FROM issues ORDER BY id") == [
        {"points": 3},
        {"points": 5},
    ]


def test_a_write_to_a_listed_table_commits_with_the_call(tmp_path: Path) -> None:
    """`read_only=False` is the sandbox mode; the tool is in the call's transaction either way."""
    world = sql_world(tmp_path, read_only=False)
    with world.instance(None) as instance:
        seed(instance)

        assert ask(instance, "UPDATE issues SET points = 9 WHERE id = 'i1'")["row_count"] == 0

        assert instance.inspect().rows("SELECT points FROM issues ORDER BY id") == [
            {"points": 9},
            {"points": 5},
        ]
        # Net over the episode: the seeded insert and this update fold into one
        # insert of the row as it now stands.
        assert {"id": "i1", "title": "first", "points": 9} in [
            net.after for net in fold(instance.change_log())
        ]


def test_a_write_to_a_table_the_world_never_wrote_leaves_the_log_readable(
    tmp_path: Path,
) -> None:
    """The change log's session first sees a table inside the agent's statement.

    SQLite asks `PRAGMA table_xinfo` then, and a denial is not an error the
    statement reports -- the session stores it and the `changeset()` that call
    reads raises `SQLITE_AUTH` instead. So the damage shows up nowhere near the
    write: the call succeeds, the row is there, and the records it should have
    left are gone. `attachments` is the table the seeding never touches, which is
    what makes this the agent's write and not the world's.
    """
    world = sql_world(tmp_path, read_only=False)
    with world.instance(None) as instance:
        seed(instance)

        ask(instance, "INSERT INTO attachments (id, blob) VALUES ('a1', NULL)")

        assert sorted(
            (record.table, record.op, tuple(record.key.items())) for record in instance.change_log()
        ) == [
            ("attachments", "insert", (("id", "a1"),)),
            ("issues", "insert", (("id", "i1"),)),
            ("issues", "insert", (("id", "i2"),)),
            ("salaries", "insert", (("person", "alice"),)),
        ]
        # Every shape of write, each the first the call's session sees of its
        # table, folded back into the net an eval grades.
        ask(instance, "UPDATE attachments SET blob = x'00' WHERE id = 'a1'")
        ask(instance, "DELETE FROM issues WHERE id = 'i2'")
        assert sorted(
            (net.table, net.op, str(net.key["id"]))
            for net in fold(instance.change_log())
            if net.table != "salaries"
        ) == [
            ("attachments", "insert", "a1"),
            ("issues", "insert", "i1"),
        ]


def test_the_shape_question_that_write_needs_is_not_one_the_agent_can_ask(
    tmp_path: Path,
) -> None:
    """The allowance above is the session's, not a pragma the write door hands out."""
    world = sql_world(tmp_path, read_only=False)
    with world.instance(None) as instance:
        seed(instance)
        ask(instance, "INSERT INTO attachments (id, blob) VALUES ('a1', NULL)")

        for query in ("PRAGMA table_xinfo(attachments)", "PRAGMA table_xinfo(salaries)"):
            with pytest.raises(DbError) as raised:
                ask(instance, query)
            assert raised.value.refusals == ("action PRAGMA 'table_xinfo'",)

        # The adjacent route, which is the one a statement could take *with* a
        # row write in it: the pragma table-valued function. It is a table, so
        # the table allowlist turns it down before the pragma rule is consulted,
        # whether or not the same statement writes a row first.
        for query in (
            "SELECT name FROM pragma_table_xinfo('attachments')",
            "INSERT INTO attachments (id, blob) SELECT name, NULL "
            "FROM pragma_table_xinfo('salaries')",
        ):
            with pytest.raises(DbError) as raised:
                ask(instance, query)
            assert raised.value.refusals == ("read of table 'pragma_table_xinfo'",)


def test_a_write_door_still_refuses_a_table_it_does_not_list(tmp_path: Path) -> None:
    world = sql_world(tmp_path, read_only=False)
    with world.instance(None) as instance:
        seed(instance)

        with pytest.raises(DbError) as raised:
            ask(instance, "UPDATE salaries SET amount = 1")

        assert raised.value.refusals == ("write of table 'salaries'",)


def test_a_write_door_still_refuses_a_schema_change(tmp_path: Path) -> None:
    world = sql_world(tmp_path, read_only=False)
    with world.instance(None) as instance:
        with pytest.raises(DbError) as raised:
            ask(instance, "CREATE TABLE sneaky (id TEXT PRIMARY KEY) STRICT")

        # SQLite asks about the row it would write into its own schema table
        # first, and a write to one of those is classified as the action it is.
        assert raised.value.refusals == ("action INSERT 'sqlite_master'",)


def test_a_failed_write_leaves_nothing_behind(tmp_path: Path) -> None:
    """The refusal happens inside the call's transaction, which rolls back with it."""
    world = sql_world(tmp_path, read_only=False)
    with world.instance(None) as instance:
        seed(instance)

        with pytest.raises(DbError):
            ask(instance, "INSERT INTO issues VALUES ('i1', 'duplicate', 0)")

        assert instance.inspect().rows("SELECT id FROM issues ORDER BY id") == [
            {"id": "i1"},
            {"id": "i2"},
        ]


def test_max_rows_truncates_and_says_so(tmp_path: Path) -> None:
    world = sql_world(tmp_path, max_rows=1)
    with world.instance(None) as instance:
        seed(instance)

        result = ask(instance, "SELECT id FROM issues ORDER BY id")

        assert result == {
            "columns": ["id"],
            "rows": [["i1"]],
            "row_count": 1,
            "truncated": True,
        }


def test_max_bytes_truncates_and_says_so(tmp_path: Path) -> None:
    world = sql_world(tmp_path, max_bytes=20)
    with world.instance(None) as instance:
        seed(instance)

        result = ask(instance, "SELECT id, title FROM issues ORDER BY id")

        assert result["rows"] == [["i1", "first"]]
        assert result["truncated"] is True


def test_an_untruncated_result_says_that_too(tmp_path: Path) -> None:
    world = sql_world(tmp_path, max_rows=10, max_bytes=10_000)
    with world.instance(None) as instance:
        seed(instance)

        assert ask(instance, "SELECT id FROM issues")["truncated"] is False


@pytest.mark.parametrize(
    ("cap", "build"),
    [
        ("max_rows", lambda: run_sql(tables=OFFERED, max_rows=-1)),
        ("max_bytes", lambda: run_sql(tables=OFFERED, max_bytes=-1)),
    ],
)
def test_a_negative_cap_is_refused_at_registration(cap: str, build: Callable[[], Tool]) -> None:
    """A world's mistake, found where every other tool mistake is: at registration."""
    with pytest.raises(WorldBug, match=cap):
        build()


def test_a_door_onto_no_tables_is_refused_at_registration() -> None:
    """The same class of mistake as a negative cap, and it would reach the agent.

    An empty list is not a locked door -- `sqlite_master` and the table-valued
    functions are still there -- it is a door whose description trails off after
    "against the tables: ".
    """
    with pytest.raises(WorldBug, match="at least one table"):
        run_sql(tables=[])


def test_sqlite_master_is_readable_without_being_listed(reader: Instance) -> None:
    result = ask(reader, "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")

    assert ["attachments"] in result["rows"]


def test_json_each_is_usable(reader: Instance) -> None:
    result = ask(reader, "SELECT value FROM json_each('[1, 2, 3]')")

    assert result["rows"] == [[1], [2], [3]]


def test_json_tree_is_usable(reader: Instance) -> None:
    """The other table-valued function every door offers, and the one a mutant drops."""
    result = ask(reader, "SELECT fullkey FROM json_tree('{\"a\": [1]}') ORDER BY fullkey")

    assert result["rows"] == [["$"], ["$.a"], ["$.a[0]"]]


def test_a_cap_of_zero_is_a_world_that_truncates_everything(tmp_path: Path) -> None:
    """Zero is a policy, not a mistake: only a negative cap is refused at registration."""
    world = sql_world(tmp_path, max_rows=0)
    with world.instance(None) as instance:
        seed(instance)

        assert ask(instance, "SELECT id FROM issues") == {
            "columns": ["id"],
            "rows": [],
            "row_count": 0,
            "truncated": True,
        }


def test_random_is_answered_from_the_instances_seed(tmp_path: Path) -> None:
    """An instance is reproducible, so the door offers randomness rather than refusing it.

    The stream is the instance's, not the process's: the same seed replays it
    through the door, and a different seed gives a different one.
    """
    world = sql_world(tmp_path)
    dice = "SELECT random(), hex(randomblob(4))"

    def rolls(seed: int) -> list[list[Any]]:
        with world.instance(None, seed=seed) as instance:
            return [ask(instance, dice)["rows"] for _ in range(3)]

    assert rolls(11) == rolls(11)
    assert rolls(11) != rolls(12)
    # Three calls to one door, not one call answered three times: a stream, not
    # a constant.
    first, second, third = rolls(11)
    assert first != second != third


def test_drawing_random_in_sql_does_not_shift_the_worlds_ids(tmp_path: Path) -> None:
    """An agent rolling dice through the door must not move `ctx.ids` along with it.

    Both streams come from the one instance seed, so the run is still determined
    by the seed and the fixture; what is separate is which draw each takes. A
    door wired to `ctx.ids.random` instead would pass every test in `test_ids.py`
    and fail this one.
    """
    world = sql_world(tmp_path)

    def minted(*, rolling: bool) -> list[str]:
        with world.instance(None, seed=5) as instance:
            seed(instance)
            if rolling:
                ask(instance, "SELECT random(), randomblob(16)")
            with instance.bulk() as ctx:
                return [ctx.ids.uuid() for _ in range(3)]

    assert minted(rolling=True) == minted(rolling=False)


def test_a_blob_comes_back_as_base64_and_a_null_as_null(reader: Instance) -> None:
    with reader.bulk() as ctx:
        ctx.db.execute("INSERT INTO attachments VALUES (?, ?)", "a1", b"\x00\xff\x10")
        ctx.db.execute("INSERT INTO attachments VALUES (?, ?)", "a2", None)

    assert ask(reader, "SELECT id, blob FROM attachments ORDER BY id")["rows"] == [
        ["a1", "AP8Q"],
        ["a2", None],
    ]


def test_a_syntax_error_carries_sqlites_own_text(reader: Instance) -> None:
    """The one place engine text is the message: the product being mimicked is a SQL door."""
    with pytest.raises(DbError) as raised:
        ask(reader, "SELECT FROM WHERE")

    error = raised.value
    assert error.code == "db_error"
    assert error.message == error.sqlite_message
    assert "syntax error" in error.message
    assert error.details is None


def test_an_unknown_column_carries_sqlites_own_text(reader: Instance) -> None:
    """The adjacent case: a statement that parses and then cannot be prepared."""
    with pytest.raises(DbError) as raised:
        ask(reader, "SELECT nope FROM issues")

    assert raised.value.message == "no such column: nope"


def test_a_second_statement_in_one_call_is_refused(reader: Instance) -> None:
    with pytest.raises(DbError) as raised:
        ask(reader, "SELECT id FROM issues; SELECT 1")

    assert raised.value.refusals == ("statement after the first in one call",)


def test_the_value_cap_applies_to_the_statement_and_is_put_back_afterwards(
    reader: Instance,
) -> None:
    """The fixed cap is the sandbox's and belongs to the statement, not to the connection."""
    before = reader.db.conn.limit(apsw.SQLITE_LIMIT_LENGTH)

    with pytest.raises(DbError) as raised:
        ask(reader, "SELECT printf('%1100000d', 1)")

    assert raised.value.refusals == ("value larger than 1000000 bytes",)
    assert reader.db.conn.limit(apsw.SQLITE_LIMIT_LENGTH) == before
    # The world's own code is not bound by it: a tool may build what it likes.
    assert reader.call("rows", sql="SELECT length(printf('%1100000d', 1)) AS n") == [{"n": 1100000}]


def test_an_empty_query_is_an_argument_error(reader: Instance) -> None:
    with pytest.raises(ArgumentError) as raised:
        ask(reader, "")

    assert raised.value.violations[0]["path"] == "query"


def test_the_tool_takes_one_required_string_argument(tmp_path: Path) -> None:
    listing = run_sql(tables=OFFERED).listing()

    assert listing["input_schema"]["required"] == ["query"]
    assert listing["input_schema"]["properties"]["query"]["type"] == "string"
    assert listing["input_schema"]["additionalProperties"] is False


def test_the_tool_is_registered_inside_the_calls_transaction() -> None:
    """`transaction=True`, as the spec says, whatever `read_only` is.

    Pinned as a property rather than through a behaviour: this door runs exactly
    one statement and serialises a result that cannot fail to serialise, so there
    is no call in which the flag's two settings behave differently. It is what
    makes the write commit *with* the call, and the next thing this door grows
    would be the first thing to need it.
    """
    assert run_sql(tables=OFFERED).transaction is True
    assert run_sql(tables=OFFERED, read_only=False).transaction is True


def test_the_default_description_names_the_tables_and_the_mode() -> None:
    assert run_sql(tables=OFFERED).description == (
        "Run one read-only SQL statement (SQLite dialect) against the tables: "
        "issues, attachments. Returns columns and rows."
    )
    assert run_sql(tables=OFFERED, read_only=False).description == (
        "Run one SQL statement (SQLite dialect) against the tables: "
        "issues, attachments. Returns columns and rows."
    )
    assert run_sql(tables=OFFERED, description="Query the issue tracker.").description == (
        "Query the issue tracker."
    )


def test_the_world_names_the_tool(tmp_path: Path) -> None:
    world = build_world(tmp_path, SCHEMA)
    world.tool(run_sql(name="query", tables=OFFERED))

    with world.instance(None) as instance:
        seed(instance)

        assert ask(instance, "SELECT id FROM issues ORDER BY id", tool="query")["row_count"] == 2
        assert "query" in {tool["name"] for tool in instance.tools()}


def test_a_refusal_is_not_carried_into_the_next_call(reader: Instance) -> None:
    """The `Authorizer` is per call: a shared one would answer for the call before."""
    with pytest.raises(DbError):
        ask(reader, "SELECT amount FROM salaries")

    assert ask(reader, "SELECT id FROM issues ORDER BY id")["rows"] == [["i1"], ["i2"]]

    with pytest.raises(DbError) as raised:
        ask(reader, "SELECT note FROM audit")

    assert raised.value.refusals == ("read of table 'audit'",)


def test_every_call_builds_its_own_authorizer(
    reader: Instance, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one property the test below cannot pin without a race, pinned without one.

    An `Authorizer` carries the refusals of the statement it ran, and one `Tool`
    serves every instance of a world on every thread. `run_statement` resets the
    object it is handed, so a shared one is wrong in a way that shows up only when
    two calls overlap: one call's reset clears what another is about to report.
    Hoisting the construction to factory time survives the whole suite otherwise,
    which is why this asks the question directly.
    """
    # The objects, not their ids: the first is collectable the moment the call
    # ends, and a later one lands at the same address often enough to pass.
    seen: list[Authorizer] = []
    real = sandbox.run_statement

    def spy(db: Db, sql: str, params: Sequence[SqlValue] = (), **options: Any) -> SqlResult:
        seen.append(options["authorizer"])
        return real(db, sql, params, **options)

    monkeypatch.setattr(sandbox, "run_statement", spy)

    ask(reader, "SELECT id FROM issues")
    ask(reader, "SELECT id FROM issues")

    assert len(seen) == 2
    assert seen[0] is not seen[1]


def test_two_instances_refusing_different_tables_classify_independently(tmp_path: Path) -> None:
    """Two calls in flight at once, each told what *it* was refused and nothing else."""
    world = build_world(tmp_path, SCHEMA)
    world.tool(run_sql(name="issues_only", tables=["issues"]))
    world.tool(run_sql(name="attachments_only", tables=["attachments"]))
    both = threading.Barrier(2, timeout=5.0)
    refusals: dict[str, tuple[str, ...]] = {}

    def refuse(instance: Instance, tool: str, query: str) -> None:
        both.wait()
        for _ in range(20):
            with pytest.raises(DbError) as raised:
                instance.call(tool, query=query)
            assert raised.value.refusals == (f"read of table '{denied(tool)}'",)
        refusals[tool] = raised.value.refusals

    def denied(tool: str) -> str:
        return "attachments" if tool == "issues_only" else "issues"

    with world.instance(None) as first, world.instance(None) as second:
        callers = [
            Caller(lambda: refuse(first, "issues_only", "SELECT id FROM attachments")),
            Caller(lambda: refuse(second, "attachments_only", "SELECT id FROM issues")),
        ]
        for caller in callers:
            caller.start()
        for caller in callers:
            caller.finish()

    assert refusals == {
        "issues_only": ("read of table 'attachments'",),
        "attachments_only": ("read of table 'issues'",),
    }
