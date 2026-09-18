"""FTS5 end to end: a world that searches, through every part of the framework it touches.

There is no FTS5 module in Seahaven -- only `db.shadow_tables` and the four
consumers that ask it what to leave alone. These tests are the assembled view: a
world whose schema has a full-text index freezes, forks, conforms and logs its
changes as if the index were not there, and search works both from a world's own
tool and, when the world says so, from the SQL door.
"""

from pathlib import Path
from typing import Any

import pytest

from seahaven import conformance
from seahaven.ctx import Ctx
from seahaven.db import shadow_tables
from seahaven.errors import DbError
from seahaven.helpers import describe_schema, run_sql
from seahaven.instances import Instance
from seahaven.world import World
from tests.conftest import build_world
from tests.fold_support import fold

pytestmark = pytest.mark.filterwarnings("ignore:controller_run_sql is deprecated")

SCHEMA = """
CREATE TABLE issues (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    body TEXT NOT NULL
) STRICT;

CREATE VIRTUAL TABLE issues_fts USING fts5(title, body, content='issues', content_rowid='rowid');

CREATE TRIGGER issues_ai AFTER INSERT ON issues BEGIN
    INSERT INTO issues_fts (rowid, title, body) VALUES (new.rowid, new.title, new.body);
END;
"""

# What a world that wants `MATCH` through the SQL door has to list: the virtual
# table, the shadow tables the module reads to answer, and the functions SQLite
# asks about while running the query.
SHADOW = ("issues_fts_data", "issues_fts_idx", "issues_fts_docsize", "issues_fts_config")
SEARCH_FUNCTIONS = ("bm25", "snippet", "highlight", "match")

SEED = [
    ("i1", "Login fails on Safari", "The login button does nothing at all"),
    ("i2", "Export is slow", "The CSV export takes minutes for a large project"),
    ("i3", "Dark mode", "Add a dark theme to the settings page"),
]


def searching_world(tmp_path: Path, **options: Any) -> World:
    """A world with a full-text index and a tool that searches it the reference way."""
    world = build_world(tmp_path, SCHEMA, **options)

    @world.tool
    def search(ctx: Ctx, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """Find issues by full-text search, best match first."""
        return ctx.db.rows(
            "SELECT issues.id, snippet(issues_fts, 1, '[', ']', '...', 8) AS excerpt "
            "FROM issues_fts JOIN issues ON issues.rowid = issues_fts.rowid "
            "WHERE issues_fts MATCH ? ORDER BY rank, issues.id LIMIT ?",
            query,
            limit,
        )

    return world


def seed(instance: Instance) -> None:
    for id, title, body in SEED:
        instance.call(
            "execute",
            sql=f"INSERT INTO issues (id, title, body) VALUES ('{id}', '{title}', '{body}')",
        )


@pytest.fixture
def searching(tmp_path: Path) -> Any:
    world = searching_world(tmp_path)
    with world.instance(None) as instance:
        seed(instance)
        yield instance


def test_a_world_tool_searches_the_index(searching: Instance) -> None:
    found = searching.call("search", query="login")

    assert [row["id"] for row in found] == ["i1"]
    assert "[login]" in found[0]["excerpt"]


def test_the_index_is_not_in_the_change_log(searching: Instance) -> None:
    """Three rows written, three shadow tables filled, three records."""
    assert [record.table for record in searching.change_log()] == ["issues"] * 3


def test_the_world_conforms_to_itself_with_an_index_in_it(searching: Instance) -> None:
    assert conformance.differences(searching.db.conn, searching.world) == []
    assert shadow_tables(searching.db.conn)


def test_it_freezes_and_forks_with_the_index_intact(tmp_path: Path) -> None:
    """The fixture is the file, so the index travels with it and the fork can search."""
    world = searching_world(tmp_path)
    with world.instance(None) as instance:
        seed(instance)
        instance.freeze("seeded", "Three issues.")

    with world.instance("seeded") as forked:
        assert [row["id"] for row in forked.call("search", query="dark")] == ["i3"]
        assert conformance.differences(forked.db.conn, forked.world) == []
        # A fork starts with no changes of its own, index or not.
        assert forked.change_log() == []

        forked.call(
            "execute", sql="INSERT INTO issues (id, title, body) VALUES ('i4', 'New', 'thing')"
        )
        assert [record.table for record in forked.change_log()] == ["issues"]


MATCH_QUERY = "SELECT rowid FROM issues_fts WHERE issues_fts MATCH 'login'"

# The reference search shape: `MATCH ... ORDER BY rank` with an auxiliary
# function, which is what a world's SQL door is actually asked for.
RANKED_QUERY = (
    "SELECT issues.id, snippet(issues_fts, 1, '[', ']', '...', 5) FROM issues_fts "
    "JOIN issues ON issues.rowid = issues_fts.rowid "
    "WHERE issues_fts MATCH 'login' ORDER BY rank"
)


def test_match_through_the_sql_door_is_refused_by_default(tmp_path: Path) -> None:
    """`MATCH` is a function to SQLite, and it is not on the default allowlist."""
    world = searching_world(tmp_path)
    world.tool(run_sql(tables=["issues", "issues_fts"]))

    with world.instance(None) as instance:
        seed(instance)

        with pytest.raises(DbError) as raised:
            instance.call("run_sql", query=MATCH_QUERY)

    assert raised.value.refusals == ("function 'match'",)


def test_allowing_the_functions_is_not_enough_without_the_shadow_tables(tmp_path: Path) -> None:
    """The adjacent half of the same door: the module reads the index to answer.

    Both halves are pinned because a world that adds only the functions gets a
    different refusal and would otherwise think it had finished.
    """
    world = searching_world(tmp_path)
    world.tool(run_sql(tables=["issues", "issues_fts"], functions=SEARCH_FUNCTIONS))

    with world.instance(None) as instance:
        seed(instance)

        with pytest.raises(DbError) as raised:
            instance.call("run_sql", query=MATCH_QUERY)

    assert raised.value.refusals == ("read of table 'issues_fts_idx'",)


def test_match_through_the_sql_door_works_when_the_world_lists_what_it_needs(
    tmp_path: Path,
) -> None:
    world = searching_world(tmp_path)
    world.tool(run_sql(tables=["issues", "issues_fts", *SHADOW], functions=SEARCH_FUNCTIONS))

    with world.instance(None) as instance:
        seed(instance)

        result = instance.call(
            "run_sql",
            query=(
                "SELECT issues.id, bm25(issues_fts) < 0 AS scored FROM issues_fts "
                "JOIN issues ON issues.rowid = issues_fts.rowid "
                "WHERE issues_fts MATCH 'export' ORDER BY rank"
            ),
        )

    assert result == {
        "columns": ["id", "scored"],
        "rows": [["i2", 1]],
        "row_count": 1,
        "truncated": False,
    }


# The shadow tables this query makes SQLite *ask* about. `issues_fts_data` and
# `issues_fts_config` are read too, through statements the module prepares
# itself, which the authorizer is not asked about. Which is which is FTS5's
# business and may change with the amalgamation, so the advice to a world is to
# list all four -- and these two are what a world that lists fewer runs into.
ASKED_ABOUT = ("issues_fts_idx", "issues_fts_docsize")


@pytest.mark.parametrize("missing", ASKED_ABOUT)
def test_a_shadow_table_the_world_left_out_is_refused(tmp_path: Path, missing: str) -> None:
    """The allowlist is exactly what was listed; nothing is inferred from a name prefix."""
    world = searching_world(tmp_path)
    listed = [table for table in SHADOW if table != missing]
    world.tool(run_sql(tables=["issues", "issues_fts", *listed], functions=SEARCH_FUNCTIONS))

    with world.instance(None) as instance:
        seed(instance)

        with pytest.raises(DbError) as raised:
            instance.call("run_sql", query=RANKED_QUERY)

    assert raised.value.refusals == (f"read of table '{missing}'",)


def test_a_write_to_the_index_is_still_refused_at_a_writable_door(tmp_path: Path) -> None:
    """`read_only=False` allows writes to the listed tables; the index is not a table to write."""
    world = searching_world(tmp_path)
    world.tool(
        run_sql(
            tables=["issues", "issues_fts", *SHADOW], read_only=False, functions=SEARCH_FUNCTIONS
        )
    )

    with world.instance(None) as instance:
        seed(instance)

        # Allowed, because the world listed `issues` at a writable door.
        instance.call("run_sql", query="UPDATE issues SET title = 'Renamed' WHERE id = 'i1'")
        with pytest.raises(DbError, match="issues_fts_data"):
            instance.call(
                "run_sql", query="INSERT INTO issues_fts_data (id, block) VALUES (99, x'00')"
            )

        # The net of the episode: the seeded insert and this update collapse into
        # one insert carrying the new title, and the index is in neither.
        written = [net for net in fold(instance.change_log()) if net.key == {"id": "i1"}]
        assert [net.table for net in written] == ["issues"]
        after = written[0].after
        assert after is not None and after["title"] == "Renamed"


def test_describe_schema_describes_the_index_as_the_world_declared_it(tmp_path: Path) -> None:
    """`hidden = 1` drops FTS5's own `issues_fts` and `rank` columns; the declared ones stay."""
    world = searching_world(tmp_path)
    world.tool(describe_schema(tables=["issues", "issues_fts"]))

    with world.instance(None) as instance:
        described = instance.call("describe_schema")["tables"][1]

    assert described == {
        "name": "issues_fts",
        "columns": [
            {"name": "title", "type": "", "nullable": True, "primary_key": False},
            {"name": "body", "type": "", "nullable": True, "primary_key": False},
        ],
        "foreign_keys": [],
    }


def test_control_sql_reads_the_shadow_tables_nothing_else_may(searching: Instance) -> None:
    """No allowlist at all: the index an eval wants to look at is readable through control."""
    result = searching.call("controller_run_sql", sql="SELECT count(*) FROM issues_fts_docsize")

    assert result["rows"] == [[3]]


def test_control_sql_can_search_the_index_with_nothing_listed(searching: Instance) -> None:
    """The adjacent case to the door above: an eval grades a search world by searching it.

    Nothing has to be allowed for this -- not the shadow tables, not `match`, not
    the `data_version` read -- because the control authorizer mirrors the
    inspection connection's denial and that connection allows every read.
    """
    result = searching.call(
        "controller_run_sql",
        sql=(
            "SELECT issues.id FROM issues_fts JOIN issues ON issues.rowid = issues_fts.rowid "
            "WHERE issues_fts MATCH ? ORDER BY rank"
        ),
        params=["export"],
    )

    assert result["rows"] == [["i2"]]
