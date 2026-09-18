"""What an instance changed, as data an eval can grade on.

The **change log** is the ordered record of every row the instance changed after
startup, one record per row per call, across every node: a session is opened on
each node for each call, and what it recorded is rendered and appended when the
call's transactions are done.

The session extension is what speaks, and its rule is the net of what it
recorded: a write that leaves a value unchanged records nothing, an insert
followed by an update of the same row is one insert, and a call that rolled back
leaves no trace. Over one call that makes the records the net of that call. A
consumer that wants the net of a whole episode folds the log itself
(`functional_spec.md` §3.6); nothing here computes one.

No session sees the startup hooks: the per-call sessions do not exist yet when
the hooks run, so seed rows are starting state rather than agent changes. A
composite instance records per node, each over its own world's tables and its own
`untracked_tables`, and every record says which node it came from.
"""

import base64
import copy
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, cast

import apsw

from seahaven.db import world_tables
from seahaven.errors import INTERNAL_ERROR_MESSAGE, WorldBug

if TYPE_CHECKING:  # `world.py` imports this module's callers; the annotation is all that is needed
    from seahaven.world import World

__all__ = [
    "CallRecord",
    "LogRecord",
    "open_session",
    "render_log",
    "tracked_tables",
]

# SQLite's own names for what a row change is, as this framework spells them.
_OPS: dict[str, Literal["insert", "update", "delete"]] = {
    "INSERT": "insert",
    "UPDATE": "update",
    "DELETE": "delete",
}


@dataclass(frozen=True)
class LogRecord:
    """One row one call changed: the unit of the change log.

    The whole row on an insert and on a delete, because that is the row; on an
    update, exactly the non-key columns the call changed, old values on one side
    and new on the other. The key is never repeated inside an update's sides: it
    is in `key`, which is where a reader joins on it.

    `i` is the ordinal of the call that made the change, or `None` for a write
    made with no call in flight (`inst.bulk()`). `world` is the path of the node
    the row belongs to -- `main` for the root, the added node's canonical path
    otherwise -- which is what tells two tables of one name in two stores apart.

    `key`, `before` and `after` are the instance's own dicts, handed out rather
    than copied; `to_dict()` copies.
    """

    i: int | None
    world: str
    table: str
    op: Literal["insert", "update", "delete"]
    key: dict[str, Any]
    before: dict[str, Any] | None
    after: dict[str, Any] | None

    def to_dict(self) -> dict[str, Any]:
        """The published shape, in the field order `functional_spec.md` §3.2 gives.

        The dicts are copied, so the document is the caller's: editing it edits
        nothing the instance holds. Every value in them is a JSON scalar, so a
        shallow copy is a whole one.
        """
        return {
            "i": self.i,
            "world": self.world,
            "table": self.table,
            "op": self.op,
            "key": dict(self.key),
            "before": None if self.before is None else dict(self.before),
            "after": None if self.after is None else dict(self.after),
        }


@dataclass(frozen=True)
class CallRecord:
    """One call dispatched to the instance: the unit of the call log.

    `tool` is the name as the caller gave it, which under composition is the
    root surface's name, prefix included. `arguments` is what the call carried,
    copied at capture and never re-serialised: over OpenEnv the JSON that
    arrived, in process the values the caller passed. `error` is the message of
    whatever the call raised, or `None`, and `tool_error` says whether that was a
    `ToolError` -- an error the world wrote for the agent -- or anything else.

    The record keeps the real message whatever it was, because an author
    debugging their own world reads it in process. `to_dict` is the boundary that
    decides what a consumer of the state document sees instead.
    """

    tool: str
    arguments: dict[str, Any]
    error: str | None
    tool_error: bool = False

    def to_dict(self) -> dict[str, Any]:
        """The published shape, in the field order `functional_spec.md` §4.2 gives.

        The state document is a wire boundary: OpenEnv's `StepEnvSessionAdapter`
        embeds the whole document in every trace entry, so a harness that renders
        a trace back into a model's context would put whatever a call raised in
        front of the agent. A `ToolError` is published as it was written, because
        the world wrote it for the agent and the agent has already read it on the
        observation; anything else -- a `WorldBug`, or a Python exception the
        world did not plan for -- is published as the generic error, in the same
        words the observation carries.

        `arguments` is copied the same way the capture copied it: deeply where a
        deep copy of the whole mapping is possible, and shallowly where it is
        not. Anything else
        would make a document raise for a call the framework deliberately let
        run -- and a document whose calls carried values JSON cannot carry is
        not JSON-able either way, which is what `functional_spec.md` §4.2 says
        of it.
        """
        written_for_the_agent = self.error is None or self.tool_error
        return {
            "tool": self.tool,
            "arguments": _copied_arguments(self.arguments),
            "error": self.error if written_for_the_agent else INTERNAL_ERROR_MESSAGE,
        }


def _copied_arguments(arguments: Mapping[str, Any]) -> dict[str, Any]:
    """One call's arguments as the call log keeps them: a deep copy where one is possible.

    The copy is what makes a record the call as made rather than whatever a tool
    left in the dict it was handed. Not every value a caller can pass is
    copyable, though -- in process a parameter annotated `object` accepts a lock
    or a socket, and `deepcopy` raises on those -- and the bookkeeping must not
    be what fails a call that would otherwise have run, nor leave the call
    unrecorded after its ordinal is spent (functional_spec.md §13:
    `len(call_log())` equals `call_count`). Such a call keeps a shallow copy
    instead: the mapping is still the record's own, and the values in it are the
    caller's. Over OpenEnv every argument is JSON and the deep copy always
    succeeds.

    Used at capture and again by `CallRecord.to_dict`, so that what one tolerates
    the other does too.
    """
    try:
        return copy.deepcopy(dict(arguments))
    except Exception:
        return dict(arguments)


def tracked_tables(conn: apsw.Connection, world: World) -> tuple[str, ...]:
    """Every table a session on this node records, in name order.

    Virtual tables are left out because the session extension cannot track one,
    and the tables a world names in `World(untracked_tables=...)` because it said
    to. A table with no explicit primary key is refused here rather than attached
    quietly, which is why this is asked once at instance creation: the refusal is
    a `WorldBug` about the world, and every session the instance opens afterwards
    attaches the list this answered.
    """
    untracked = frozenset(world.untracked_tables)
    virtual = _virtual_tables(conn)
    tables = []
    for table in world_tables(conn):
        if table in untracked or table in virtual:
            continue
        _refuse_a_table_with_no_primary_key(conn, table)
        tables.append(table)
    return tuple(tables)


def open_session(conn: apsw.Connection, tracked: Sequence[str]) -> apsw.Session:
    """Record every change to those tables, from now on.

    One of these is opened per node per call, so it is deliberately no more than
    a `Session` and an `attach` each: both are C, and the list they are given was
    computed once when the instance was made.
    """
    session = apsw.Session(conn, "main")
    for table in tracked:
        session.attach(table)
    return session


def render_log(
    changeset: bytes,
    conn: apsw.Connection,
    columns: dict[str, tuple[list[str], list[int], list[int]]],
    *,
    i: int | None,
    world: str,
) -> list[LogRecord]:
    """One node's changeset for one call as log records, in the changeset's own order.

    `columns` is the caller's cache of `_columns` for this node, kept for the
    life of the instance: a node's schema does not change while one is alive, so
    a table is looked up once however many calls touch it. The records of one
    call across every node are sorted afterwards by `_sort_key`, which is why this
    does not sort what it renders.
    """
    rendered = []
    for change in apsw.Changeset.iter(changeset):
        table = change.name
        if table not in columns:
            columns[table] = _columns(conn, table)
        names, key_positions, non_key = columns[table]
        op = _OPS[change.op]
        # The key is in whichever side of the change has it: an insert has only
        # `new`, everything else carries the old row's key in `old`.
        source = change.new if op == "insert" else change.old
        before, after = _sides(names, non_key, op, change.old, change.new)
        rendered.append(
            LogRecord(
                i=i,
                world=world,
                table=table,
                op=op,
                key=_row(names, source, key_positions) or {},
                before=before,
                after=after,
            )
        )
    return rendered


def _sort_key(record: LogRecord) -> tuple[Any, ...]:
    """Where a record sorts inside its call: node path, then table, then key values.

    Over the *rendered* key values, not the raw ones. The within-call order is
    part of the format (`functional_spec.md` §3.3) and the fold a consumer
    computes re-sorts by it (§3.6), and a consumer reading a saved document has
    a blob's base64 text and never its bytes -- so a blob key sorts by that
    text, which is the only order a reader can reproduce.
    """
    return (
        record.world,
        record.table,
        tuple(_sqlite_rank(value) for value in record.key.values()),
    )


def _sqlite_rank(value: Any) -> tuple[int, Any]:
    """A published key value in SQLite's cross-type order: NULL, then numbers, then text.

    The leading rank is what keeps the sort total: a key column declared ANY can
    hold two storage classes, which Python would refuse to compare. Every tracked
    table is STRICT in a linted world, so in practice a column's rank never
    varies.

    The `None` arm is unreachable through the log and is kept because the order
    it names is SQLite's: a row with `NULL` in a primary-key column is never
    recorded by the session extension at all (functional_spec.md §3.2, pinned by
    `test_changes.py`), so no key a record carries holds one.
    """
    match value:
        case None:
            return (0, None)
        case int() | float():
            return (1, value)
        case _:
            return (2, value)


def _sides(
    names: list[str],
    non_key: list[int],
    op: Literal["insert", "update", "delete"],
    old: tuple[Any, ...] | None,
    new: tuple[Any, ...] | None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """A record's `before` and `after`: whole rows at the ends of a row's life, deltas between.

    An update carries exactly the columns it changed -- a changeset marks the
    rest `apsw.no_change`, which is not the same as `NULL`, and `_row` drops
    them, so asking it for the non-key columns leaves exactly the changed ones --
    and carries them without the key, which `key` already holds. An insert and a
    delete carry the whole row, key columns included, because that is the row.

    `non_key` is the table's, worked out once by `_columns` and cached with the
    names: it is a function of the schema and not of the row, and this runs once
    per changed row.
    """
    if op != "update":
        return _row(names, old), _row(names, new)
    return _row(names, old, non_key), _row(names, new, non_key)


def _row(
    names: list[str], values: tuple[Any, ...] | None, positions: list[int] | None = None
) -> dict[str, Any] | None:
    """The columns a change carries, named. `None` where the change has no such side."""
    if values is None:
        return None
    wanted = range(len(names)) if positions is None else positions
    return {
        names[position]: _jsonable(values[position])
        for position in wanted
        if values[position] is not apsw.no_change
    }


def _jsonable(value: Any) -> Any:
    """A SQLite value as something JSON can carry: a blob becomes base64 text."""
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    if isinstance(value, float) and math.isinf(value):
        # JSON has no infinities; SQLite already stores NaN as NULL, so this is
        # its rule one step further (functional_spec.md §3.4).
        return None
    return value


def _columns(conn: apsw.Connection, table: str) -> tuple[list[str], list[int], list[int]]:
    """A table's column names in storage order, the positions of its key in key order, and the rest.

    All three are functions of the table alone, so they are worked out together
    and cached together: `render_log` asks per table and never per row.
    """
    # The cast says what the pragma returns; APSW types every column as any
    # SQLite value.
    rows = cast(
        list[tuple[str, int]],
        conn.execute("SELECT name, pk FROM pragma_table_info(?) ORDER BY cid", (table,)).fetchall(),
    )
    key = sorted((pk, position) for position, (_name, pk) in enumerate(rows) if pk > 0)
    key_positions = [position for _pk, position in key]
    in_key = frozenset(key_positions)
    return (
        [name for name, _pk in rows],
        key_positions,
        [position for position in range(len(rows)) if position not in in_key],
    )


def _refuse_a_table_with_no_primary_key(conn: apsw.Connection, table: str) -> None:
    """A table with no explicit primary key cannot be tracked, so it is not attached quietly.

    The session extension attaches one happily and then records nothing for it:
    every write to it would be missing from the changeset with nothing to say so.
    """
    _names, key, _non_key = _columns(conn, table)
    if not key:
        raise WorldBug(
            f"table {table!r} has no explicit primary key, so the changeset could not record its "
            f"writes; give it one, or name it in World(untracked_tables=...)"
        )


def _virtual_tables(conn: apsw.Connection) -> frozenset[str]:
    rows = cast(
        list[tuple[str]],
        conn.execute(
            "SELECT name FROM pragma_table_list WHERE schema = 'main' AND type = 'virtual'"
        ).fetchall(),
    )
    return frozenset(name for (name,) in rows)
