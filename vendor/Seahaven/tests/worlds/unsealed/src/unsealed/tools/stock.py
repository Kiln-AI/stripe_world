"""One tool with nothing said about it, which is SH205 and needs no tree at all.

It is here so that a world that does not seal can be shown still getting the
rules that do not read a composition: without a second finding, "every other rule
still runs" is a sentence no test can hold anything to.
"""

import seahaven
from unsealed.world import world

__all__ = ["count_stock"]


@world.tool
def count_stock(ctx: seahaven.Ctx) -> int:
    return len(ctx.db.rows("SELECT id FROM stock"))
