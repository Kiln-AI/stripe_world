"""What this world contributes of its own: one note, and one order settled end to end."""

from typing import Any

import seahaven
from emporium.world import world

__all__ = ["record_charge_owner", "settle_order"]


@world.tool
def record_charge_owner(ctx: seahaven.Ctx, charge_id: str, owner_id: str) -> dict[str, Any]:
    """Note which of this company's people owns a charge."""
    ctx.db.execute(
        "INSERT INTO charge_owners (charge_id, owner_id) VALUES (?, ?)", charge_id, owner_id
    )
    return {"charge_id": charge_id, "owner_id": owner_id}


@world.tool
def settle_order(ctx: seahaven.Ctx, total: int) -> dict[str, Any]:
    """Place an order in the shop, charge the company account, and note who owns it."""
    order = ctx.worlds.shop.call("place_order", total=total)
    charge = ctx.worlds.payments.call("create_charge", amount=total)
    ctx.db.execute(
        "INSERT INTO charge_owners (charge_id, owner_id) VALUES (?, ?)", charge["id"], order["id"]
    )
    return {"order": order["id"], "charge": charge["id"], "total": total}
