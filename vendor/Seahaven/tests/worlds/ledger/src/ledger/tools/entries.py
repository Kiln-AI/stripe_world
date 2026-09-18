"""Two tools that describe each other, the way a real vendor's docs do."""

from typing import Any

import seahaven
from ledger.world import world

__all__ = ["list_entries", "post_entry"]


@world.tool
def post_entry(ctx: seahaven.Ctx, memo: str) -> dict[str, Any]:
    """Post one entry to the ledger. Read them back with list_entries."""
    entry = {"id": ctx.ids.uuid(), "memo": memo, "posted_at": ctx.clock.iso()}
    ctx.db.execute(
        "INSERT INTO entries (id, memo, posted_at) VALUES (?, ?, ?)",
        entry["id"],
        entry["memo"],
        entry["posted_at"],
    )
    return entry


@world.tool
def list_entries(ctx: seahaven.Ctx) -> list[dict[str, Any]]:
    """Every entry post_entry has written, oldest first."""
    return ctx.db.rows("SELECT id, memo, posted_at FROM entries ORDER BY id")
