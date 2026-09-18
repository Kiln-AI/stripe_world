"""The two tools this world contributes."""

from typing import Any

import seahaven
from payments.world import world

__all__ = ["create_charge", "list_charges"]


@world.tool
def create_charge(ctx: seahaven.Ctx, amount: int, currency: str = "usd") -> dict[str, Any]:
    """Charge an amount and return the charge."""
    charge = {
        "id": ctx.ids.uuid(),
        "amount": amount,
        "currency": currency,
        "created_at": ctx.clock.iso(),
    }
    ctx.db.execute(
        "INSERT INTO charges (id, amount, currency, created_at) VALUES (?, ?, ?, ?)",
        charge["id"],
        charge["amount"],
        charge["currency"],
        charge["created_at"],
    )
    return charge


@world.tool
def list_charges(ctx: seahaven.Ctx) -> list[dict[str, Any]]:
    """Every charge on this account, oldest first."""
    return ctx.db.rows("SELECT id, amount, currency, created_at FROM charges ORDER BY id")
