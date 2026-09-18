"""One described tool, taking its time and its ids from the context."""

from typing import Any

import seahaven
from tidy.world import world

__all__ = ["write_note"]


@world.tool
def write_note(ctx: seahaven.Ctx, body: str) -> dict[str, Any]:
    """Write a note and return it."""
    note = {"id": ctx.ids.uuid(), "body": body, "created_at": ctx.clock.iso()}
    ctx.db.execute(
        "INSERT INTO notes (id, body, created_at) VALUES (?, ?, ?)",
        note["id"],
        note["body"],
        note["created_at"],
    )
    return note
