"""Keyset pagination: the one way a list in this world is cut into pages.

Every list tool answers `{"<items>": [...], "next_cursor": str | None, "has_next":
bool}` and takes the cursor back to continue. The cut is a keyset and never an
`OFFSET`: an offset page re-reads every row before it and, worse, shifts under a
concurrent insert, so an agent walking a list can see a row twice or miss one
entirely. A keyset names the last row it saw and asks for what comes after it,
which is stable whatever happens behind the cursor and costs the same on page one
and page fifty.

The sort column alone is not a key -- two issues can share a `created_at` -- so
the key is the row value `(sort column, id)` and the ordering is on both. That is
also why the cursor carries both halves.

A cursor is base64 of JSON `[sort_value, id, sort_key]`, and `sort_key` is what
makes a cursor refusable: a page cut by one ordering says nothing about where
another ordering is, so continuing `created_at_desc` with a cursor cut by
`updated_at_asc` would silently return a wrong page. The key names the resource as
well as the ordering (`issues:created_at_desc`), so a cursor from one list is
refused by every other one too -- `components/projecttracker.md` §3 asks for the
ordering half, and the resource half closes the case where two lists share an
ordering and the wrong page would look plausible.

It is opaque, not secret: base64 of JSON is readable by anyone who cares to look,
and there is nothing in it that is not already in the rows the agent just read.
What the encoding buys is that it is one string an agent can carry back without
having to know what a keyset is.
"""

import base64
import binascii
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import seahaven
from projecttracker.errors import InvalidInput
from seahaven.db import SqlValue

__all__ = ["Order", "Page", "decode_cursor", "encode_cursor", "page"]

# What `decode_cursor` says about anything it cannot read. One sentence for every
# way a cursor can be wrong, because an agent's fix is the same in all of them:
# drop it and start from the first page, or carry back the one the list gave it.
_UNREADABLE = "not a cursor from this list; pass the next_cursor a page returned, or omit it"


@dataclass(frozen=True)
class Order:
    """One ordering a list offers: what to sort by, and what to call it.

    `column` and `id_column` are SQL expressions, written by this world and never
    by a caller -- they are interpolated into the statement, which a value from
    outside never is. `key` is what a cursor carries, and changing it invalidates
    every cursor an agent is holding, which is the point of it.
    """

    key: str
    column: str
    id_column: str
    descending: bool = False

    @property
    def sql(self) -> str:
        """The `ORDER BY` clause, both columns in the same direction.

        Both, and not just the first: a keyset is a comparison on the row value
        `(column, id)`, and a row value comparison only lines up with an ordering
        that sorts both halves the same way.
        """
        direction = "DESC" if self.descending else "ASC"
        return f"{self.column} {direction}, {self.id_column} {direction}"

    @property
    def after(self) -> str:
        """The keyset predicate: the rows strictly past the cursor's row."""
        comparison = "<" if self.descending else ">"
        return f"({self.column}, {self.id_column}) {comparison} (?, ?)"


@dataclass(frozen=True)
class Page:
    """One page of rows, and how to ask for the next one."""

    rows: list[dict[str, Any]]
    next_cursor: str | None
    has_next: bool

    def as_result(self, items: str) -> dict[str, Any]:
        """The page as a list tool returns it, with the rows under `items`."""
        return {items: self.rows, "next_cursor": self.next_cursor, "has_next": self.has_next}


def encode_cursor(order: Order, row: dict[str, Any], *, sort_column: str, id_column: str) -> str:
    """A cursor pointing at `row`, for continuing `order`.

    `sort_column` and `id_column` are the *result* names of the two key columns,
    which are not always the names in `order`: the statement selects
    `issues.created_at`, and the row it returns has a key called `created_at`.
    """
    payload = json.dumps(
        [row[sort_column], row[id_column], order.key], separators=(",", ":"), ensure_ascii=False
    )
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii")


def decode_cursor(cursor: str, order: Order) -> tuple[Any, Any]:
    """The `(sort_value, id)` a cursor points at, or `INVALID_INPUT`.

    Every failure is the same error, because every fix is the same: a cursor that
    is not base64, is not JSON, is not a triple, does not hold two values SQLite
    can bind, or was cut for another list is a cursor this page cannot be
    continued from.

    The two halves are checked and not only the sort key. They become SQL
    parameters, and a JSON object or array there is a binding APSW refuses with a
    `TypeError` -- which is not a `ToolError`, so the handler would answer
    `INTERNAL` and log a traceback, making this world accuse itself of a bug an
    agent caused by mangling an opaque string it was handed.
    """
    try:
        decoded = json.loads(base64.urlsafe_b64decode(cursor.encode("ascii")))
    except (ValueError, binascii.Error, UnicodeEncodeError) as error:
        raise InvalidInput("cursor", _UNREADABLE) from error
    match decoded:
        case [_, _, str(key)] if key != order.key:
            raise InvalidInput(
                "cursor",
                f"was cut for {key!r} and this page is {order.key!r}; a cursor cannot be "
                f"carried from one ordering to another",
            )
        case [sort_value, row_id, str()] if _bindable(sort_value) and _bindable(row_id):
            return sort_value, row_id
        case _:
            raise InvalidInput("cursor", _UNREADABLE)


def _bindable(value: Any) -> bool:
    """Whether a cursor's half is something SQLite will take as a parameter.

    The type is not enough. SQLite's integers are 64 bits and APSW refuses a
    Python one that does not fit with `OverflowError`, which -- like the
    `TypeError` a container raises -- is not a `ToolError`, so it would reach the
    agent as `INTERNAL` and the author as a traceback. JSON has no integer bound,
    so `2 ** 80` in a cursor is a number `json.loads` hands over happily.

    Everything else `json.loads` can produce binds: `str`, `float` (`NaN` and the
    infinities included), `None`, and `bool` as the `int` SQLite makes of it -- a
    legal parameter that matches no key, which is an empty page rather than a
    failure.
    """
    if isinstance(value, bool):
        return True
    if isinstance(value, int):
        return -(2**63) <= value < 2**63
    return value is None or isinstance(value, str | float)


def page(
    ctx: seahaven.Ctx,
    *,
    select: str,
    where: Sequence[str] = (),
    params: Sequence[SqlValue] = (),
    order: Order,
    limit: int,
    cursor: str | None,
    sort_column: str,
    id_column: str = "id",
) -> Page:
    """Run one page of `select`, filtered by `where` and cut by `cursor`.

    `select` is everything up to the `WHERE`: the columns and the tables. The
    conditions in `where` are `AND`ed with the keyset predicate, and `params`
    binds their placeholders in order. Nothing a caller supplied is ever part of
    the SQL text -- `select`, `where` and `order` are this world's own strings,
    and every value arrives as a parameter.

    One row more than `limit` is read and thrown away: that extra row is the whole
    of what `has_next` knows, and asking for it costs one row where a second
    `count(*)` would cost a scan and could still disagree with the page it
    described.
    """
    conditions = list(where)
    values = list(params)
    if cursor is not None:
        sort_value, row_id = decode_cursor(cursor, order)
        conditions.append(order.after)
        values += [sort_value, row_id]
    clause = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    found = ctx.db.rows(f"{select}{clause} ORDER BY {order.sql} LIMIT ?", *values, limit + 1)
    has_next = len(found) > limit
    rows = found[:limit]
    # `has_next` is true only when more than `limit` rows came back, so `rows` is
    # never empty here and the last of them is the row the next page starts after.
    next_cursor = (
        encode_cursor(order, rows[-1], sort_column=sort_column, id_column=id_column)
        if has_next
        else None
    )
    return Page(rows=rows, next_cursor=next_cursor, has_next=has_next)
