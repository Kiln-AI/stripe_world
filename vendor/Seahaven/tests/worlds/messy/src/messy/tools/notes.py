"""Tools that do every one of the things `seahaven check` warns about.

Wrong on purpose. A world's tools take time from `ctx.clock` and identifiers from
`ctx.ids`; these take them from the machine, which is what SH201 and SH203 are
for, and one of them has nothing for an agent to read, which is SH205.
"""

import random
import uuid
from datetime import datetime
from typing import Any

import seahaven
from messy.world import world

__all__ = ["roll", "undescribed", "write_note"]


@world.tool
def write_note(ctx: seahaven.Ctx, body: str) -> dict[str, Any]:
    """Write a note, dated from the machine and identified from the entropy pool."""
    return {"id": str(uuid.uuid4()), "body": body, "created_at": datetime.now().isoformat()}


@world.tool
def roll(ctx: seahaven.Ctx) -> float:
    """Draw a number from a source that does not replay."""
    return random.random()


@world.tool(description="")
def undescribed(ctx: seahaven.Ctx) -> None:
    """Registered with an empty description, which is what an agent would read."""
