"""Does this database still hold the schema the world declares?

Asked once, at `freeze`: a fixture is minted from a live instance, and an
instance whose schema has drifted -- a table created through `run_sql`, a column
added by hand while authoring -- would mint a fixture that no later instance of
the world can use. The check is textual, over `sqlite_master.sql` with whitespace
collapsed, and so is deliberately stricter than SQLite's own idea of two schemas
being the same: a reordered column list is a difference here, because a world
whose DDL says one thing and whose fixture says another is a thing to fix rather
than to reason about.

FTS5 keeps its own tables and writes their DDL itself, so those are excluded from
both sides; the module, not the world, is responsible for them.
"""

import re
from typing import TYPE_CHECKING, cast

import apsw

from seahaven.db import SCHEMA_CHECK_CLOCK, SCHEMA_CHECK_SEED, build_blank, shadow_tables
from seahaven.errors import WorldBug

if TYPE_CHECKING:  # `world.py` imports this module's callers; the annotation is all that is needed
    from seahaven.world import World

__all__ = ["check", "differences", "schema_map"]

_WHITESPACE = re.compile(r"\s+")


def schema_map(conn: apsw.Connection) -> dict[str, str]:
    """Every schema object the world owns, name to normalised SQL.

    Tables, indexes, triggers and views: an index a world declares is part of its
    schema, and one an instance grew is drift. SQLite's own objects and
    everything FTS5 maintains are left out -- `sqlite_autoindex_*` and a shadow
    table are written by the engine, not by the DDL, so they are on both sides of
    every comparison and say nothing.
    """
    shadow = shadow_tables(conn)
    # The cast says what the schema table holds; APSW types every column as any
    # SQLite value.
    rows = cast(
        list[tuple[str, str, str]],
        conn.execute(
            "SELECT name, tbl_name, sql FROM sqlite_master WHERE sql IS NOT NULL"
        ).fetchall(),
    )
    return {
        name: _WHITESPACE.sub(" ", sql).strip()
        for name, table, sql in rows
        # A table's `tbl_name` is its own name, so one clause covers both a shadow
        # table and an index or a trigger defined on one: all of it belongs to the
        # FTS5 module rather than to the world.
        if not name.startswith("sqlite_") and table not in shadow
    }


def differences(conn: apsw.Connection, world: World) -> list[str]:
    """Every way `conn`'s schema differs from a fresh build of the world's DDL."""
    expected = _expected(world)
    found = schema_map(conn)
    lines = [
        f"{name}: in the instance but not in the world's schema"
        for name in found
        if name not in expected
    ]
    lines += [
        f"{name}: in the world's schema but not in the instance"
        for name in expected
        if name not in found
    ]
    lines += [
        f"{name}: the instance has {found[name]!r}, the world's schema has {expected[name]!r}"
        for name in expected
        if name in found and found[name] != expected[name]
    ]
    return sorted(lines)


def check(conn: apsw.Connection, world: World) -> None:
    """Raise unless the database holds exactly the world's schema."""
    found = differences(conn, world)
    if found:
        raise WorldBug(
            f"this instance no longer holds world {world.name!r}'s schema: " + "; ".join(found)
        )


def _expected(world: World) -> dict[str, str]:
    """The world's DDL as SQLite records it, built fresh in memory for the purpose."""
    conn = build_blank(":memory:", world.schema, clock=SCHEMA_CHECK_CLOCK, seed=SCHEMA_CHECK_SEED)
    try:
        return schema_map(conn)
    finally:
        conn.close()
