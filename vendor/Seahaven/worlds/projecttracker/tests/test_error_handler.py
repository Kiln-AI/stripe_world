"""The error wrapper: what an agent is told when something goes wrong.

The rule the handler exists to keep is that no engine or framework text reaches
an agent unless this world chose it, and the two doors where this world does
choose it are named in the middleware. Every case here drives a real tool on a
real world through `Instance.call`, because a middleware tested with a stub for
`next_` proves only that a function maps an exception.
"""

import logging
from collections.abc import Callable
from typing import Any

import pytest

import seahaven
from conftest import BLANK_NOW
from projecttracker.errors import Conflict, Internal, NotFound
from projecttracker.middleware.error_handler import SEARCH_TOOLS, SQL_DOOR_TOOLS
from seahaven.helpers import run_sql

# An FTS5 table for the search door to fail on, added to the probe world's
# schema. Not this world's own `issues_fts`, because what these tests need is a
# tool that only does the failing -- `search_issues` itself is driven against the
# real index in `test_search.py`. Any FTS5 table rejects the same query strings.
SEARCH_SCHEMA = "\nCREATE VIRTUAL TABLE memos_fts USING fts5(body);\n"

# A table no world here has, for the `DbError` an ordinary tool provokes by
# writing SQL that cannot run. Spelled as a constant because two tests use it and
# because a name that *is* in this world's schema would make both of them pass by
# succeeding rather than by failing the way they mean to.
MISSING_TABLE = "sprints"

# A table in the world but not behind the SQL door, for the refusal the door
# answers with in this framework's words rather than SQLite's.
PRIVATE_SCHEMA = "\nCREATE TABLE salaries (person TEXT PRIMARY KEY, amount INTEGER) STRICT;\n"

type Probe = Callable[..., seahaven.World]


def a_tool(fn: Callable[..., Any], name: str) -> Any:
    """Register `fn` under `name`, so a test can name a tool the handler knows."""
    return seahaven.Tool.from_function(fn, name=name, description=f"the {name} probe")


def test_a_tool_error_this_world_raised_reaches_the_agent_unchanged(probe: Probe) -> None:
    """This world's own shapes are already written for the agent. Nothing restates them."""

    def missing(ctx: seahaven.Ctx) -> None:
        raise NotFound("issue", "ENG-1")

    world = probe(a_tool(missing, "missing"))
    with world.instance(None, now=BLANK_NOW) as instance:
        with pytest.raises(NotFound) as raised:
            instance.call("missing")
        assert raised.value.to_dict() == {
            "code": "NOT_FOUND",
            "message": "issue ENG-1 not found",
            "details": {"kind": "issue", "key": "ENG-1"},
        }


def test_a_conflict_reaches_the_agent_unchanged(probe: Probe) -> None:
    """The second half of the `ToolError` branch: a shape with no details."""

    def clashing(ctx: seahaven.Ctx) -> None:
        raise Conflict("the issue is already closed")

    world = probe(a_tool(clashing, "clashing"))
    with world.instance(None, now=BLANK_NOW) as instance:
        with pytest.raises(Conflict) as raised:
            instance.call("clashing")
        assert raised.value.to_dict() == {
            "code": "CONFLICT",
            "message": "the issue is already closed",
            "details": None,
        }


def test_an_argument_error_becomes_invalid_input_and_keeps_the_framework_error_behind_it(
    probe: Probe,
) -> None:
    """Every violation in one `INVALID_INPUT`, with the framework's own error as the cause.

    The agent gets this world's shape. The author gets the full violation list,
    which the restated error flattens into one message string -- and the only
    thing carrying it is `__cause__`, so the chain is what this pins.
    """

    def edit(ctx: seahaven.Ctx, title: str, rank: int) -> None:  # pragma: no cover - never called
        raise AssertionError("validation refuses the call before the tool runs")

    world = probe(a_tool(edit, "edit"))
    with world.instance(None, now=BLANK_NOW) as instance:
        with pytest.raises(seahaven.ToolError) as raised:
            instance.call("edit", title=5, rank="soon")
        assert raised.value.code == "INVALID_INPUT"
        # Both failures in one error, so the agent fixes them in one turn.
        assert raised.value.details == {"field": "title"}
        assert "rank" in raised.value.message
        cause = raised.value.__cause__
        assert isinstance(cause, seahaven.ArgumentError)
        assert [v["path"] for v in cause.violations] == ["title", "rank"]


def test_a_database_error_under_an_ordinary_tool_becomes_internal(probe: Probe) -> None:
    """SQLite's complaint about this world's own SQL is this world's bug, not the agent's."""

    def broken(ctx: seahaven.Ctx) -> None:
        ctx.db.rows(f"SELECT * FROM {MISSING_TABLE}")

    world = probe(a_tool(broken, "broken"))
    with world.instance(None, now=BLANK_NOW) as instance:
        with pytest.raises(Internal) as raised:
            instance.call("broken")
        assert raised.value.to_dict() == {
            "code": "INTERNAL",
            "message": "Something went wrong",
            "details": None,
        }
        # The whole point of the branch: none of SQLite's text got out.
        assert MISSING_TABLE not in repr(raised.value)
        assert "no such table" not in repr(raised.value)
        # And the author can still find it: the `DbError` is the cause, with the text.
        cause = raised.value.__cause__
        assert isinstance(cause, seahaven.DbError)
        assert f"no such table: {MISSING_TABLE}" in cause.sqlite_message


def test_a_database_error_the_agent_never_sees_is_written_to_the_log(
    probe: Probe, caplog: pytest.LogCaptureFixture
) -> None:
    """`INTERNAL` tells the author nothing, so the handler tells the log everything.

    The one place the engine's text and the traceback survive the mapping. A
    world whose tool writes bad SQL is debugged from this line and from nothing
    else, because the agent-facing error is deliberately empty of detail.
    """

    def broken(ctx: seahaven.Ctx) -> None:
        ctx.db.rows(f"SELECT * FROM {MISSING_TABLE}")

    world = probe(a_tool(broken, "broken"))
    with (
        caplog.at_level(logging.ERROR, logger="projecttracker.errors"),
        world.instance(None, now=BLANK_NOW) as instance,
    ):
        with pytest.raises(Internal):
            instance.call("broken")
        (record,) = [r for r in caplog.records if r.name == "projecttracker.errors"]
        assert instance.id in record.getMessage()
        assert "broken" in record.getMessage()
        assert record.exc_info is not None
        logged = record.exc_info[1]
        assert isinstance(logged, seahaven.DbError)
        # The text the agent did not get, and the APSW error behind it.
        assert logged.sqlite_message == f"no such table: {MISSING_TABLE}"
        assert f"no such table: {MISSING_TABLE}" in str(logged.__cause__)


def test_a_constraint_violation_becomes_internal_too(probe: Probe) -> None:
    """The adjacent case to a typo: SQL that is correct and data that is not.

    A world that writes a duplicate email has a bug in the tool that did not
    check first, and the agent is owed this product's wording for it, not
    SQLite's `UNIQUE constraint failed`.
    """

    def double_insert(ctx: seahaven.Ctx) -> None:
        for n in (1, 2):
            ctx.db.execute(
                "INSERT INTO users (id, email, name, role, created_at) VALUES (?, ?, ?, ?, ?)",
                f"u_{n}",
                "same@example.invalid",
                "Same",
                "member",
                ctx.clock.iso(),
            )

    world = probe(a_tool(double_insert, "double_insert"))
    with world.instance(None, now=BLANK_NOW) as instance:
        with pytest.raises(Internal) as raised:
            instance.call("double_insert")
        assert "UNIQUE" not in repr(raised.value)
        # The call's transaction rolled back, so neither row is there: an error
        # the handler restated is still an error, and the call still fails.
        assert instance.inspect().one("SELECT count(*) AS n FROM users") == {"n": 0}


def test_a_database_error_from_the_sql_door_keeps_sqlites_own_text(probe: Probe) -> None:
    """A SQL console's errors are SQL errors: the one door where engine text is right."""
    world = probe(run_sql(tables=["users"]))
    assert "run_sql" in SQL_DOOR_TOOLS
    with world.instance(None, now=BLANK_NOW) as instance:
        with pytest.raises(seahaven.DbError) as raised:
            instance.call("run_sql", query="SELCT * FROM users")
        assert raised.value.code == "db_error"
        # The engine's own words, and the door's decision that they are what an
        # agent reads: the handler neither replaced them nor added to them.
        assert raised.value.message == raised.value.sqlite_message
        assert "syntax error" in raised.value.message


def test_a_refusal_from_the_sql_door_keeps_the_frameworks_wording(probe: Probe) -> None:
    """The adjacent case: a refusal is not SQLite's text and must not be replaced either.

    `run_sql` already decides which of the two an agent reads; the handler's job
    is to leave that decision alone, and a refusal is the half that would be
    silently downgraded to `INTERNAL` if the branch were keyed on anything but
    the tool.
    """
    world = probe(run_sql(tables=["users"]), schema=PRIVATE_SCHEMA)
    with world.instance(None, now=BLANK_NOW) as instance:
        with pytest.raises(seahaven.DbError) as raised:
            instance.call("run_sql", query="SELECT * FROM salaries")
        assert raised.value.code == "db_error"
        assert raised.value.message == "not allowed: read of table 'salaries'"
        assert raised.value.details == {"refusals": ["read of table 'salaries'"]}


def test_a_database_error_from_the_search_door_becomes_invalid_input(probe: Probe) -> None:
    """An FTS5 syntax error is the agent's query, not this world's SQL."""

    def search_issues(ctx: seahaven.Ctx, query: str) -> list[dict[str, Any]]:
        return ctx.db.rows("SELECT body FROM memos_fts WHERE memos_fts MATCH ?", query)

    assert "search_issues" in SEARCH_TOOLS
    world = probe(a_tool(search_issues, "search_issues"), schema=SEARCH_SCHEMA)
    with world.instance(None, now=BLANK_NOW) as instance:
        assert instance.call("search_issues", query="hello") == []
        with pytest.raises(seahaven.ToolError) as raised:
            instance.call("search_issues", query='"')
        assert raised.value.code == "INVALID_INPUT"
        assert raised.value.details == {"field": "query"}
        # FTS5's complaint about the query string, on the field it came in on.
        cause = raised.value.__cause__
        assert isinstance(cause, seahaven.DbError)
        assert raised.value.message == f"query: {cause.sqlite_message}"
        assert cause.sqlite_message == "unterminated string"


def test_an_unexpected_exception_becomes_internal(probe: Probe) -> None:
    """A bug in world code is not something an agent can be told about."""

    def crash(ctx: seahaven.Ctx) -> None:
        raise ValueError("a bug in world code, with a stack trace behind it")

    world = probe(a_tool(crash, "crash"))
    with world.instance(None, now=BLANK_NOW) as instance:
        with pytest.raises(Internal) as raised:
            instance.call("crash")
        assert raised.value.message == "Something went wrong"
        assert "a bug in world code" not in repr(raised.value)
        assert isinstance(raised.value.__cause__, ValueError)


def test_a_world_bug_is_re_raised_and_never_becomes_a_product_error(probe: Probe) -> None:
    """Turning a `WorldBug` into `INTERNAL` would hide the author's own mistake.

    The case that makes this concrete is a tool returning something the
    framework cannot serialise: the call fails, and the author needs to see why
    rather than reading "Something went wrong" in an agent transcript.
    """

    def unserialisable(ctx: seahaven.Ctx) -> Any:
        return b"bytes are not a result type"

    world = probe(a_tool(unserialisable, "unserialisable"))
    with world.instance(None, now=BLANK_NOW) as instance, pytest.raises(seahaven.WorldBug):
        instance.call("unserialisable")


def test_the_handler_does_not_touch_a_successful_call(probe: Probe) -> None:
    """The path every call takes: through the handler and out with its result."""

    def fine(ctx: seahaven.Ctx, n: int) -> dict[str, int]:
        return {"n": n + 1}

    world = probe(a_tool(fine, "fine"))
    with world.instance(None, now=BLANK_NOW) as instance:
        assert instance.call("fine", n=41) == {"n": 42}
