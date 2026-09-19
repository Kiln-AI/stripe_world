"""The only reader and writer of `counters`: the pagination key source.

Every listable table's rows carry `x_seq INTEGER NOT NULL`, assigned on insert
from that table's `counters` row (`components/data_model.md` §3.1.2). A counter
rather than `MAX(x_seq) + 1`, because this world hard-deletes rows and a `MAX`
form reuses a number after a delete — which makes a `starting_after` cursor
ambiguous: the id resolves to an `x_seq` that may now belong to a different
row, and the page silently skips or repeats.

`counters` is untracked (`world.py`), so a bump never appears in a graded
change log: every insert would otherwise put a bookkeeping row into every
episode.
"""

import seahaven

__all__ = ["next_seq"]


def next_seq(ctx: seahaven.Ctx, table: str) -> int:
    """The next pagination key for `table`.

    Raises `WorldBug` for a table with no counter row — one that is not
    listable, or whose DDL forgot the seed row.
    """
    row = ctx.db.one("UPDATE counters SET value = value + 1 WHERE name = ? RETURNING value", table)
    if row is None:
        raise seahaven.WorldBug(f"no counter for table {table!r}")
    return int(row["value"])
