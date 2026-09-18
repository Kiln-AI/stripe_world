"""The companion every SQL door ships with: what the tables an agent may query look like.

Read from the live schema on every call rather than from the world's DDL text, so
what the agent is told is what its own statements will meet.
"""

from collections.abc import Sequence
from typing import Any

from seahaven.ctx import Ctx
from seahaven.db import Db
from seahaven.errors import WorldBug
from seahaven.tool import Tool

__all__ = ["describe_schema"]

# Every column the world declared, and only a virtual table's hidden ones
# dropped: `hidden` is 2 and 3 for a `GENERATED ALWAYS AS ... VIRTUAL` and a
# `... STORED` column, both of which are declared, selectable, and to be
# described like any other. `hidden = 1` is an FTS5 table's own name column and
# its `rank`, which no world declared.
_COLUMNS = (
    'SELECT name, type, "notnull", pk FROM pragma_table_xinfo(?) WHERE hidden <> 1 ORDER BY cid'
)

_FOREIGN_KEYS = (
    'SELECT "from", "table", "to", id, seq FROM pragma_foreign_key_list(?) ORDER BY id, seq'
)

# A parent's key columns, in key order: what a foreign key that names no parent
# column refers to. `pragma_foreign_key_list` leaves `"to"` NULL there.
_PARENT_KEY = "SELECT name FROM pragma_table_info(?) WHERE pk > 0 ORDER BY pk"


def describe_schema(
    *, name: str = "describe_schema", tables: Sequence[str], description: str | None = None
) -> Tool:
    """A tool that describes `tables`: their columns, keys and foreign keys.

    Takes no arguments and writes nothing. `tables` must name at least one. The
    tables are described in the order they are listed here, and a table that is
    not in the instance's schema is a world bug rather than something the agent
    is told.
    """
    listed = tuple(tables)
    if not listed:
        # As `run_sql`: caught at registration, because an empty list is a world
        # mistake that otherwise reaches the agent as a tool describing nothing
        # and a description that trails off after "the tables: ".
        raise WorldBug(f"tool {name!r}: tables must name at least one table")

    def _describe_schema(ctx: Ctx) -> dict[str, Any]:
        return {"tables": [_describe_table(ctx.db, table) for table in listed]}

    return Tool.from_function(
        _describe_schema,
        name=name,
        description=(description if description is not None else _default_description(listed)),
        # It reads the schema and nothing else, so there is nothing for a
        # transaction to make atomic or to roll back.
        transaction=False,
    )


def _describe_table(db: Db, table: str) -> dict[str, Any]:
    columns = db.rows(_COLUMNS, table)
    if not columns:
        # Not an agent-facing error: the world listed a table it does not have,
        # and the scaffolded handler re-raises a `WorldBug` rather than hiding it.
        raise WorldBug(f"describe_schema lists table {table!r}, which is not in the world's schema")
    return {
        "name": table,
        "columns": [
            {
                "name": column["name"],
                "type": column["type"],
                "nullable": not column["notnull"],
                "primary_key": column["pk"] > 0,
            }
            for column in columns
        ],
        "foreign_keys": _foreign_keys(db, table),
    }


def _foreign_keys(db: Db, table: str) -> list[dict[str, Any]]:
    """The table's foreign keys, one entry each, composite ones grouped by `id`."""
    grouped: dict[Any, list[dict[str, Any]]] = {}
    for row in db.rows(_FOREIGN_KEYS, table):
        grouped.setdefault(row["id"], []).append(row)
    return [
        {
            "columns": [row["from"] for row in rows],
            "references_table": rows[0]["table"],
            "references_columns": _references_columns(db, rows),
        }
        for rows in grouped.values()
    ]


def _references_columns(db: Db, rows: list[dict[str, Any]]) -> list[Any]:
    """The parent columns a foreign key points at, named even when the DDL did not.

    `REFERENCES parent(id)` gives them; a bare `REFERENCES parent` means the
    parent's primary key and arrives as NULL, which is no use to an agent that
    has to write the join. Resolved from the parent rather than passed on.
    """
    named = [row["to"] for row in rows]
    # Asked for unconditionally rather than behind an `if any(... is None)`: that
    # guard saves one pragma read per key on the path nobody is waiting on, and a
    # branch whose two sides cannot be told apart is a branch no test can pin.
    key = [row["name"] for row in db.rows(_PARENT_KEY, rows[0]["table"])]
    # Position by position, so a composite key answers in key order. A parent
    # with no primary key at all cannot be referenced this way -- SQLite refuses
    # the foreign key at use -- and is left as it arrived rather than invented.
    return [
        key[row["seq"]] if to is None and row["seq"] < len(key) else to
        for row, to in zip(rows, named, strict=True)
    ]


def _default_description(tables: Sequence[str]) -> str:
    return (
        f"Describe the schema of the tables: {', '.join(tables)}. Returns each table's "
        f"columns with their types, nullability and primary keys, and its foreign keys."
    )
