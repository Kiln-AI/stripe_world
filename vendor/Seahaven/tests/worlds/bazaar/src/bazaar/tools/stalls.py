"""One tool that reaches its added worlds in all the wrong ways.

`ctx.worlds.ledger` is the one line here that is right, and it is here so that
the rule has something correct to leave alone.
"""

from typing import Any

import ledger

import seahaven
from bazaar.world import world
from bazaar.worlds import BazaarWorlds

__all__ = ["open_stall"]


@world.tool
def open_stall(ctx: seahaven.Ctx[BazaarWorlds], trader: str) -> dict[str, Any]:
    """Open a stall, post its opening entry, and look up its neighbour."""
    ctx.db.execute("INSERT INTO stalls (id, trader) VALUES (?, ?)", ctx.ids.uuid(), trader)
    entry = ctx.worlds.ledger.call("post_entry", memo=f"stall for {trader}")
    neighbour = ctx.worlds["marlet"].call("list_entries")
    audit = ledger.world.instance(None)
    return {"entry": entry["id"], "neighbour": len(neighbour), "audit": audit.id}
