"""`describe_schema`, driven through a real call on a real instance.

The schema below carries one of each thing the shape has to say something about:
a nullable column, a declared type, a single and a composite primary key, a
single and a composite foreign key, a foreign key that names no parent column,
and both kinds of generated column.
"""

from pathlib import Path
from typing import Any

import pytest

from seahaven.errors import WorldBug
from seahaven.helpers import describe_schema
from seahaven.instances import Instance
from seahaven.world import World
from tests.conftest import build_world

SCHEMA = """
CREATE TABLE users (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL,
    nickname TEXT
) STRICT;

CREATE TABLE issues (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    author TEXT NOT NULL REFERENCES users(id),
    owner TEXT REFERENCES users,
    slug TEXT GENERATED ALWAYS AS (lower(title)) VIRTUAL,
    shout TEXT GENERATED ALWAYS AS (upper(title)) STORED
) STRICT;

CREATE TABLE comments (
    issue_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    body TEXT NOT NULL,
    PRIMARY KEY (issue_id, seq)
) STRICT;

CREATE TABLE votes (
    a TEXT NOT NULL,
    b TEXT NOT NULL,
    PRIMARY KEY (b, a)
) STRICT;

CREATE TABLE ballots (
    x TEXT NOT NULL,
    y TEXT NOT NULL,
    PRIMARY KEY (x, y),
    FOREIGN KEY (x, y) REFERENCES votes
) STRICT;

CREATE TABLE loose (
    v TEXT
) STRICT;

CREATE TABLE tied (
    v TEXT PRIMARY KEY REFERENCES loose
) STRICT;

CREATE TABLE reactions (
    emoji TEXT NOT NULL,
    issue_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    PRIMARY KEY (emoji, issue_id, seq),
    FOREIGN KEY (issue_id, seq) REFERENCES comments(issue_id, seq)
) STRICT;
"""

LISTED = ("issues", "users", "comments", "reactions", "ballots", "tied")


def schema_world(tmp_path: Path) -> World:
    """The world these tests describe.

    `loose` is the one table with no primary key, which the changeset extension
    cannot track, so the world says so -- it is here to be the parent of a
    foreign key that has nothing to resolve to.
    """
    return build_world(tmp_path, SCHEMA, untracked_tables=["loose"])


@pytest.fixture
def described(tmp_path: Path) -> Any:
    """A live instance of a world whose schema door lists six tables."""
    world = schema_world(tmp_path)
    world.tool(describe_schema(tables=LISTED))
    with world.instance(None) as instance:
        yield instance


def described_table(instance: Instance, name: str) -> dict[str, Any]:
    result = instance.call("describe_schema")
    return next(table for table in result["tables"] if table["name"] == name)


def test_the_tables_are_described_in_the_order_the_world_listed_them(described: Instance) -> None:
    result = described.call("describe_schema")

    assert [table["name"] for table in result["tables"]] == list(LISTED)
    assert sorted(result["tables"][0]) == ["columns", "foreign_keys", "name"]


def test_columns_carry_their_type_nullability_and_key(described: Instance) -> None:
    assert described_table(described, "users")["columns"] == [
        {"name": "id", "type": "TEXT", "nullable": False, "primary_key": True},
        {"name": "email", "type": "TEXT", "nullable": False, "primary_key": False},
        {"name": "nickname", "type": "TEXT", "nullable": True, "primary_key": False},
    ]


def test_nullability_is_what_sqlite_says_not_what_the_ddl_text_said(described: Instance) -> None:
    """`users.id` is `TEXT PRIMARY KEY` with no `NOT NULL` written, and is reported NOT NULL.

    A STRICT table's primary key is implicitly NOT NULL, which is the schema the
    agent's statements will actually meet -- the reason this helper reads the
    live schema rather than parsing the world's DDL.
    """
    assert described_table(described, "users")["columns"][0] == {
        "name": "id",
        "type": "TEXT",
        "nullable": False,
        "primary_key": True,
    }


def test_every_column_of_a_composite_key_is_marked(described: Instance) -> None:
    """`pk` counts up from 1 across the key, so a `pk > 0` test and not a `pk == 1` one."""
    comments = described_table(described, "comments")

    assert [column["primary_key"] for column in comments["columns"]] == [True, True, False]


def test_generated_columns_are_described_like_any_other(described: Instance) -> None:
    """`hidden` is 2 and 3 for them; only a virtual table's own hidden columns are dropped."""
    names = [column["name"] for column in described_table(described, "issues")["columns"]]

    assert names == ["id", "title", "author", "owner", "slug", "shout"]


def test_a_single_column_foreign_key_names_what_it_points_at(described: Instance) -> None:
    """The whole list, in order: `issues` declares `author`'s key first and `owner`'s second.

    They come back the other way round. `pragma_foreign_key_list` numbers a
    table's keys from the last declared, and the entries are ordered by that
    number, so what an agent reads is reverse declaration order. Pinned rather
    than sorted: it is SQLite's order, it is stable, and an agent reading a
    schema does not care which way round two keys are -- but a reader of this
    file should not have to run it to find out.
    """
    assert described_table(described, "issues")["foreign_keys"] == [
        {"columns": ["owner"], "references_table": "users", "references_columns": ["id"]},
        {"columns": ["author"], "references_table": "users", "references_columns": ["id"]},
    ]


def test_a_foreign_key_that_names_no_parent_column_is_resolved_to_the_parents_key(
    described: Instance,
) -> None:
    """`REFERENCES users` means the parent's primary key; the pragma leaves it NULL."""
    keys = described_table(described, "issues")["foreign_keys"]

    assert {
        "columns": ["owner"],
        "references_table": "users",
        "references_columns": ["id"],
    } in keys


def test_a_composite_foreign_key_is_one_entry_in_key_order(described: Instance) -> None:
    assert described_table(described, "reactions")["foreign_keys"] == [
        {
            "columns": ["issue_id", "seq"],
            "references_table": "comments",
            "references_columns": ["issue_id", "seq"],
        }
    ]


def test_an_unnamed_composite_parent_key_is_resolved_in_key_order(described: Instance) -> None:
    """`votes` declares `PRIMARY KEY (b, a)`, which is not the order its columns are in.

    The resolution is by key position, so the pair lines up with `(x, y)` the way
    SQLite matches it -- column order would silently give the agent a join that
    compares the wrong two columns.
    """
    assert described_table(described, "ballots")["foreign_keys"] == [
        {"columns": ["x", "y"], "references_table": "votes", "references_columns": ["b", "a"]}
    ]


def test_a_parent_with_no_primary_key_is_left_as_it_arrived(described: Instance) -> None:
    """Nothing to resolve to, and nothing invented: SQLite refuses this key at use, not here."""
    assert described_table(described, "tied")["foreign_keys"] == [
        {"columns": ["v"], "references_table": "loose", "references_columns": [None]}
    ]


def test_a_schema_door_onto_no_tables_is_refused_at_registration() -> None:
    """As `run_sql`: a world mistake, caught where the world made it."""
    with pytest.raises(WorldBug, match="at least one table"):
        describe_schema(tables=[])


def test_a_table_with_no_foreign_keys_says_so(described: Instance) -> None:
    assert described_table(described, "users")["foreign_keys"] == []


def test_a_listed_table_that_is_not_in_the_schema_is_a_world_bug(tmp_path: Path) -> None:
    """The world's mistake, not the agent's: it is never rendered as a tool error."""
    world = schema_world(tmp_path)
    world.tool(describe_schema(tables=["issues", "epics"]))

    with world.instance(None) as instance, pytest.raises(WorldBug, match="'epics'"):
        instance.call("describe_schema")


def test_the_tool_takes_no_arguments_and_is_registered_outside_the_transaction() -> None:
    tool = describe_schema(tables=LISTED)

    assert tool.listing()["input_schema"]["properties"] == {}
    assert "required" not in tool.listing()["input_schema"]
    assert tool.transaction is False


def test_an_argument_it_does_not_take_is_refused(described: Instance) -> None:
    from seahaven.errors import ArgumentError

    with pytest.raises(ArgumentError):
        described.call("describe_schema", table="issues")


def test_the_default_description_names_the_tables(tmp_path: Path) -> None:
    assert describe_schema(tables=("issues", "users")).description.startswith(
        "Describe the schema of the tables: issues, users."
    )
    assert describe_schema(tables=LISTED, description="What the tables look like.").description == (
        "What the tables look like."
    )


def test_the_world_names_the_tool(tmp_path: Path) -> None:
    world = schema_world(tmp_path)
    world.tool(describe_schema(name="schema", tables=["users"]))

    with world.instance(None) as instance:
        assert instance.call("schema")["tables"][0]["name"] == "users"


def test_it_describes_the_live_schema_and_writes_nothing(described: Instance) -> None:
    """Read from the instance, so a table a startup hook added is described too."""
    described.call("execute", sql="CREATE TABLE users_extra (id TEXT PRIMARY KEY) STRICT")

    assert described.call("describe_schema")["tables"][1]["name"] == "users"
    assert described.change_log() == []
