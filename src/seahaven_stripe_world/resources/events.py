"""Event emission and the `/v1/events` read surface.

`emit_event` runs inside the call's transaction, after every row for the
change is written (`components/cross_cutting.md` §3.4.2). A rolled-back call
emits no event because the insert rolls back with everything else; an event
written before the rows would survive a later refusal and describe a change
that did not happen.

`type` is checked against the 266-entry closed set at call time. A miss is a
`WorldBug`, not a Stripe error: `type` is never agent input -- a value outside
the set means world code invented an event Stripe does not have, and grading
an agent for it would grade our bug as behavior.

`obj` is the already-serialized API object, snapshotted verbatim: `/v1/events`
reports the object as of the change, not as it stands now. Timestamps inside
it are Unix seconds -- it is a wire snapshot (`components/data_model.md`
§3.10).

The two read routes -- list and retrieve -- are hand-written rather than
engine-served: the `type` filter supports wildcard matching (`charge.*`),
`types` is an array alternative, `delivery_success` is a quirk filter with no
column behind it, and events have no create/update/delete API surface.
"""

from collections.abc import Mapping
from typing import Any

import seahaven

from seahaven_stripe_world import _ids, _json, _seq, _time
from seahaven_stripe_world.dispatch.params import Param, ParamSpec
from seahaven_stripe_world.dispatch.resource import page
from seahaven_stripe_world.dispatch.response import Request
from seahaven_stripe_world.resources import _lookup
from seahaven_stripe_world.serialize.fields import instance_livemode
from seahaven_stripe_world.spec import EVENT_TYPES

__all__ = ["EVENT_LIST", "EVENT_RETRIEVE", "emit_event", "list_", "retrieve", "serialize"]


# --- emission -------------------------------------------------------------------


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


# --- serializer -----------------------------------------------------------------


def serialize(ctx: seahaven.Ctx, row: Mapping[str, Any]) -> dict[str, Any]:
    """One events row -> one Stripe event object."""
    data = _json.loads(row["data"])
    return {
        "id": row["id"],
        "object": "event",
        "api_version": row["api_version"],
        "created": _time.to_unix(row["created"]),
        "data": data,
        "livemode": instance_livemode(ctx),
        "pending_webhooks": 0,
        "request": {
            "id": row["request_id"],
            "idempotency_key": row["request_idempotency_key"],
        },
        "type": row["type"],
    }


# --- ParamSpecs -----------------------------------------------------------------


EVENT_LIST = ParamSpec(
    op_id="GetEvents",
    paginated=True,
    body=(
        Param(name="type", kind="string"),
        Param(
            name="types",
            kind="array",
            item=Param(name="", kind="string"),
        ),
        Param(name="created", kind="range"),
        Param(name="delivery_success", kind="boolean"),
    ),
    mutually_exclusive=(("type", "types"),),
)

EVENT_RETRIEVE = ParamSpec(
    op_id="GetEventsId",
    path=("id",),
)


# --- handlers -------------------------------------------------------------------

# The range operator map, reused from the engine's list filter path.
_RANGE_SQL = {"eq": "=", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}


def list_(ctx: seahaven.Ctx, req: Request) -> dict[str, Any]:
    """`GET /v1/events`: type/types/created/delivery_success filters, paginated."""
    if req.page is None:
        raise seahaven.WorldBug("paginated route GetEvents bound without a page")

    where: list[str] = []
    binds: list[Any] = []

    # delivery_success: true returns all, false returns nothing (no actual
    # delivery exists in this world -- cross_cutting.md §3.4).
    ds = req.params.get("delivery_success")
    if ds is False:
        # Short-circuit: an empty page, preserving pagination semantics.
        return {
            "object": "list",
            "data": [],
            "has_more": False,
            "url": "/v1/events",
        }

    # type: exact match or wildcard (e.g. "charge.*" matches "charge.succeeded")
    type_value = req.params.get("type")
    if type_value is not None:
        if type_value.endswith("*"):
            prefix = type_value[:-1]
            where.append("type LIKE ? ESCAPE '\\'")
            # Escape any SQL LIKE metacharacters in the prefix itself.
            safe = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            binds.append(f"{safe}%")
        else:
            where.append("type = ?")
            binds.append(type_value)

    # types: array of exact type strings
    types_value = req.params.get("types")
    if types_value is not None and types_value:
        placeholders = ", ".join("?" for _ in types_value)
        where.append(f"type IN ({placeholders})")
        binds.extend(types_value)

    # created: range filter
    created = req.params.get("created")
    if created is not None:
        for op, bound in created.items():
            where.append(f"created {_RANGE_SQL[op]} ?")
            binds.append(bound)

    one_page = page(
        ctx,
        table="events",
        object_name="event",
        where=where,
        params=binds,
        limit=req.page.limit,
        starting_after=req.page.starting_after,
        ending_before=req.page.ending_before,
    )
    return one_page.envelope("/v1/events", lambda row: serialize(ctx, row))


def retrieve(ctx: seahaven.Ctx, req: Request) -> dict[str, Any]:
    """`GET /v1/events/{id}`: one event by id."""
    id_ = req.path_params["id"]
    row = _lookup.require_row(ctx, "events", "event", id_, param="id")
    return serialize(ctx, row)
