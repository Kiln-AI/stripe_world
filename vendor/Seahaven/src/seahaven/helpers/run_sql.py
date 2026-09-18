"""The SQL door: one statement of agent-written SQL, run inside the sandbox.

A world mimicking a product that exposes SQL registers this tool; the containment
is `sandbox.py`'s and nothing here adds to it. What this module owns is the tool
around it -- the allowlists, decided once at factory time, the result shape every
SQL door in this framework answers with, and the one place in Seahaven where
SQLite's own error text is what the agent reads.
"""

from collections.abc import Sequence
from typing import Annotated, Any

from pydantic import Field

from seahaven import sandbox
from seahaven.ctx import Ctx
from seahaven.errors import DbError, WorldBug
from seahaven.sandbox import ALLOWED_FUNCTIONS, Authorizer, SqlResult
from seahaven.tool import Tool

__all__ = ["ALWAYS_ALLOWED_TABLES", "run_sql", "showing_sqlite_text", "to_result"]

# Offered by every SQL door on top of the world's own tables. `sqlite_master` and
# `sqlite_schema` are the same table under two names, and an agent reading the
# schema it is querying is the product behaviour being mimicked; `json_each` and
# `json_tree` are table-valued functions, which SQLite asks about as table reads.
ALWAYS_ALLOWED_TABLES = frozenset({"json_each", "json_tree", "sqlite_master", "sqlite_schema"})


def run_sql(
    *,
    name: str = "run_sql",
    tables: Sequence[str],
    read_only: bool = True,
    max_rows: int | None = None,
    max_bytes: int | None = None,
    functions: Sequence[str] = (),
    description: str | None = None,
) -> Tool:
    """A tool that runs one agent-written SQL statement against `tables`.

    `tables` must name at least one; what it names is the whole of what the
    statement may read, beyond `ALWAYS_ALLOWED_TABLES`. `EXPLAIN` is a read like
    any other and is answered with the query plan SQLite prints -- it is still
    prepared under the same allowlist, so it says nothing about a table the agent
    could not have selected from.

    `read_only` is the sandbox mode for the SQL the agent writes, and has nothing
    to do with how the tool is registered: the tool runs in the call's
    transaction either way, so with `read_only=False` a write to a listed table
    commits with the call. Schema changes, `ATTACH` and `PRAGMA` are refused
    whatever it is set to.

    `max_rows` and `max_bytes` are the world's truncation policy, for a product
    that truncates; unset means the whole result. `functions` names SQLite
    functions to allow beyond `sandbox.ALLOWED_FUNCTIONS` -- a world that lists
    an FTS5 table and its shadow tables passes `("bm25", "snippet", "highlight",
    "match")`, `match` being what SQLite calls the `MATCH` operator.
    """
    for cap, value in (("max_rows", max_rows), ("max_bytes", max_bytes)):
        # Found at registration rather than on the first call, like everything
        # else that can be wrong with a tool.
        if value is not None and value < 0:
            raise WorldBug(f"tool {name!r}: {cap} must not be negative: {value}")
    listed = tuple(tables)
    if not listed:
        # The same class of world mistake as a negative cap, and it reaches the
        # agent rather than stopping at registration: the default description
        # ends "against the tables: ." and the door opens onto nothing the world
        # declared. A door onto no tables is not a door.
        raise WorldBug(f"tool {name!r}: tables must name at least one table")
    # Decided once, here: the per-call `Authorizer` is built from them and is
    # never shared, because it carries that call's refusals.
    allowed_tables = frozenset(listed) | ALWAYS_ALLOWED_TABLES
    allowed_functions = ALLOWED_FUNCTIONS | frozenset(functions)

    def _run_sql(
        ctx: Ctx,
        query: Annotated[
            str, Field(min_length=1, description="One SQL statement in the SQLite dialect.")
        ],
    ) -> dict[str, Any]:
        try:
            result = sandbox.run_statement(
                ctx.db,
                query,
                authorizer=Authorizer(
                    allowed_tables, read_only=read_only, functions=allowed_functions
                ),
                max_rows=max_rows,
                max_bytes=max_bytes,
            )
        except DbError as error:
            raise showing_sqlite_text(error) from error
        return to_result(result)

    return Tool.from_function(
        _run_sql,
        name=name,
        description=description
        if description is not None
        else _default_description(listed, read_only),
        transaction=True,
    )


def to_result(result: SqlResult) -> dict[str, Any]:
    """`{columns, rows, row_count, truncated}`: what every SQL door answers with.

    Shared with `controller_run_sql`, whose result shape is this one by
    specification, so the two cannot drift apart.
    """
    return {
        "columns": result.columns,
        "rows": result.rows,
        "row_count": result.row_count,
        "truncated": result.truncated,
    }


def showing_sqlite_text(error: DbError) -> DbError:
    """The same failure with SQLite's own text as its message, when there is one.

    A SQL door is the one place engine text is the right thing for an agent to
    read: the product being mimicked *is* a SQLite door, and "database error"
    would tell an agent nothing about the syntax it got wrong. A refusal keeps
    the refusal as its message -- "not allowed: read of table 'salaries'" says
    more than SQLite's "not authorized", and it is this framework's wording
    rather than the engine's.

    A new error rather than the one that was raised: an exception is not the
    sandbox's to rewrite after the fact, and `raise ... from error` keeps both.
    """
    return DbError(
        error.sqlite_message,
        error.sqlite_code,
        error.refusals,
        message=None if error.refusals else error.sqlite_message,
    )


def _default_description(tables: Sequence[str], read_only: bool) -> str:
    """What the agent is told the tool does, when the world does not say."""
    mode = "read-only " if read_only else ""
    return (
        f"Run one {mode}SQL statement (SQLite dialect) against the tables: "
        f"{', '.join(tables)}. Returns columns and rows."
    )
