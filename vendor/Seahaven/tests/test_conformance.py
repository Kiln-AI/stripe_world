"""The live schema against the world's DDL: what counts as a difference, and what does not."""

from pathlib import Path

import pytest

from seahaven import conformance
from seahaven.db import SCHEMA_CHECK_CLOCK, SCHEMA_CHECK_SEED, build_blank
from seahaven.errors import WorldBug
from seahaven.world import World
from tests.conftest import NOTES_SCHEMA, build_world

FTS_SCHEMA = """
CREATE TABLE notes (id TEXT PRIMARY KEY, body TEXT NOT NULL) STRICT;
CREATE VIRTUAL TABLE notes_fts USING fts5(body, content='notes', content_rowid='rowid');
"""


def test_an_instance_of_the_world_conforms(world: World) -> None:
    """The whole point: a fresh instance of a world holds exactly the world's schema."""
    with world.instance(None) as instance:
        assert conformance.differences(instance.db.conn, world) == []
        conformance.check(instance.db.conn, world)


def test_an_added_table_is_flagged(world: World) -> None:
    with world.instance(None) as instance:
        instance.call("execute", sql="CREATE TABLE extra (id TEXT PRIMARY KEY) STRICT")

        found = conformance.differences(instance.db.conn, world)

        assert found == ["extra: in the instance but not in the world's schema"]


def test_a_dropped_table_is_flagged(world: World) -> None:
    with world.instance(None) as instance:
        instance.call("execute", sql="DROP TABLE notes")

        assert conformance.differences(instance.db.conn, world) == [
            "notes: in the world's schema but not in the instance"
        ]


def test_an_added_column_is_flagged_with_both_definitions(world: World) -> None:
    with world.instance(None) as instance:
        instance.call("execute", sql="ALTER TABLE notes ADD COLUMN extra TEXT")

        (line,) = conformance.differences(instance.db.conn, world)

        assert line.startswith("notes: the instance has ")
        assert "extra TEXT" in line


def test_a_column_order_change_is_flagged(tmp_path: Path) -> None:
    """Textual, and deliberately stricter than SQLite's own idea of the same schema."""
    reordered = build_world(
        tmp_path / "other",
        "CREATE TABLE notes (body TEXT NOT NULL, id TEXT PRIMARY KEY, n INTEGER NOT NULL "
        "DEFAULT 0) STRICT;",
    )
    world = build_world(tmp_path)
    with world.instance(None) as instance:
        assert conformance.differences(instance.db.conn, reordered) != []


def test_layout_is_not_a_difference(tmp_path: Path) -> None:
    """Reformatting a world's DDL does not invalidate the instances built from it."""
    spaced = build_world(
        tmp_path / "spaced",
        NOTES_SCHEMA.replace("\n", "\n\t ").replace("(", " (  "),
    )
    world = build_world(tmp_path)
    with world.instance(None) as instance:
        assert conformance.differences(instance.db.conn, spaced) == []


def test_an_extra_index_is_flagged(world: World) -> None:
    with world.instance(None) as instance:
        instance.call("execute", sql="CREATE INDEX notes_by_body ON notes (body)")

        assert conformance.differences(instance.db.conn, world) == [
            "notes_by_body: in the instance but not in the world's schema"
        ]


def test_an_index_the_schema_declares_and_the_instance_lost_is_flagged(tmp_path: Path) -> None:
    world = build_world(tmp_path, NOTES_SCHEMA + "CREATE INDEX notes_by_body ON notes (body);")
    with world.instance(None) as instance:
        instance.call("execute", sql="DROP INDEX notes_by_body")

        assert conformance.differences(instance.db.conn, world) == [
            "notes_by_body: in the world's schema but not in the instance"
        ]


def test_a_view_is_part_of_the_schema(tmp_path: Path) -> None:
    world = build_world(tmp_path)
    with world.instance(None) as instance:
        instance.call("execute", sql="CREATE VIEW busy AS SELECT * FROM notes WHERE n > 0")

        assert conformance.differences(instance.db.conn, world) == [
            "busy: in the instance but not in the world's schema"
        ]


def test_check_lists_every_difference_at_once(world: World) -> None:
    with world.instance(None) as instance:
        instance.call("execute", sql="CREATE TABLE extra (id TEXT PRIMARY KEY) STRICT")
        instance.call("execute", sql="CREATE INDEX notes_by_body ON notes (body)")

        with pytest.raises(WorldBug) as raised:
            conformance.check(instance.db.conn, world)

        message = str(raised.value)
        assert "extra" in message
        assert "notes_by_body" in message
        assert world.name in message


def test_a_world_with_an_fts5_table_conforms_to_itself(tmp_path: Path) -> None:
    """The module writes the shadow tables, so they must not read as drift."""
    world = build_world(tmp_path, FTS_SCHEMA)
    with world.instance(None) as instance:
        assert conformance.differences(instance.db.conn, world) == []


def test_shadow_tables_are_in_no_schema_map() -> None:
    conn = build_blank(":memory:", FTS_SCHEMA, clock=SCHEMA_CHECK_CLOCK, seed=SCHEMA_CHECK_SEED)
    try:
        mapped = conformance.schema_map(conn)
    finally:
        conn.close()

    assert set(mapped) == {"notes", "notes_fts"}


def test_sqlite_s_own_objects_are_in_no_schema_map(tmp_path: Path) -> None:
    """An autoindex is SQLite's, not the world's, and is on both sides of every comparison."""
    conn = build_blank(
        ":memory:",
        "CREATE TABLE t (a TEXT, b TEXT, UNIQUE (a, b)) STRICT;",
        clock=SCHEMA_CHECK_CLOCK,
        seed=SCHEMA_CHECK_SEED,
    )
    try:
        assert set(conformance.schema_map(conn)) == {"t"}
    finally:
        conn.close()
