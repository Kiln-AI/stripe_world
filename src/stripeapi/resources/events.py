"""Event emission: one `events` row per state change, written after the rows.

`emit_event` runs inside the call's transaction, after every row for the
change is written (`components/cross_cutting.md` §3.4.2). A rolled-back call
emits no event because the insert rolls back with everything else; an event
written before the rows would survive a later refusal and describe a change
that did not happen.

`type` is checked against the 266-entry closed set at call time. A miss is a
`WorldBug`, not a Stripe error: `type` is never agent input — a value outside
the set means world code invented an event Stripe does not have, and grading
an agent for it would grade our bug as behavior.

`obj` is the already-serialized API object, snapshotted verbatim: `/v1/events`
reports the object as of the change, not as it stands now. Timestamps inside
it are Unix seconds — it is a wire snapshot (`components/data_model.md`
§3.10).
"""

from collections.abc import Mapping
from typing import Any

import seahaven

from stripeapi import _ids, _json, _seq
from stripeapi.spec import EVENT_TYPES

__all__ = ["emit_event"]


def emit_event(
    ctx: seahaven.Ctx,
    *,
    type: str,
    obj: dict[str, Any],
    previous: Mapping[str, Any] | None = None,
) -> str:
    """Append one `event` row and return its id.

    `previous` is the changed keys' prior values, rendered as
    `data.previous_attributes`; omit it and the key is absent rather than null.
    """
    if type not in EVENT_TYPES:
        raise seahaven.WorldBug(f"unknown event type {type!r}: not in spec/event_types.py")
    request = ctx.state.get("_request") or {}
    data: dict[str, Any] = {"object": obj}
    if previous:
        data["previous_attributes"] = dict(previous)
    event_id = _ids.stripe_id(ctx, "evt_")
    ctx.db.execute(
        "INSERT INTO events (id, x_seq, created, api_version, data, request_id,"
        " request_idempotency_key, type) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        event_id,
        _seq.next_seq(ctx, "events"),
        ctx.clock.iso(),
        "2026-08-26.dahlia",
        _json.dumps(data),
        request.get("id"),
        request.get("idempotency_key"),
        type,
    )
    return event_id
