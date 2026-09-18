"""The one tool this world contributes."""

from typing import Any

import seahaven
from shop.world import world

__all__ = ["place_order"]


@world.tool
def place_order(ctx: seahaven.Ctx, total: int) -> dict[str, Any]:
    """Place an order and return it."""
    order = {"id": ctx.ids.uuid(), "total": total, "placed_at": ctx.clock.iso()}
    ctx.db.execute(
        "INSERT INTO orders (id, total, placed_at) VALUES (?, ?, ?)",
        order["id"],
        order["total"],
        order["placed_at"],
    )
    return order
