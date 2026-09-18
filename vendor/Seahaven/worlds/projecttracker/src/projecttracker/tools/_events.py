"""`issue_events`: the audit trail, and the one function that appends to it.

Every write to an issue leaves a row here -- created, status changed, assignee
changed, comment added -- and every one of them goes through `record_event`.
One function rather than an `INSERT` in each tool, because the trail is what an
eval grades an agent's work on: a tool that wrote its own row would eventually
write a payload shaped differently from the rest, and the grader would have to
know which tool wrote which shape.

`payload` is a JSON object with the before and after of whatever changed, written
with sorted keys, no spaces and no escaping of non-ASCII, so that two runs that
made the same change write the same bytes and a title in any script reads back as
itself.
"""

import json
from typing import Any

import seahaven
from projecttracker.tools._types import EventKind

__all__ = ["record_event"]


def record_event(
    ctx: seahaven.Ctx,
    *,
    issue_id: str,
    actor_id: str,
    kind: EventKind,
    payload: dict[str, Any],
) -> None:
    """Append one row to the trail, stamped with the instance's clock."""
    ctx.db.execute(
        "INSERT INTO issue_events (id, issue_id, actor_id, kind, payload, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        ctx.ids.uuid(),
        issue_id,
        actor_id,
        kind,
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
        ctx.clock.iso(),
    )
