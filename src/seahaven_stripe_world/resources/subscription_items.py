"""The subscription_items slice (Phase 12): the item rows a subscription's
lines bill, as their own resource.

Every refusal is a live probe at `2026-08-26.dahlia` (2026-09-20/21): the
required `subscription` filter on the list (`Missing required param:
subscription.`), the unknown-id retrieve's own message family
(`Invalid subscription_item id: si_nope` — no `param`, no `code`, unlike
the `No such …` shape every other resource uses), item create against a
canceled subscription answering `No such subscription` at 404 under
`param: subscription`, and the list's **oldest-first** order (the one
resource the live API lists in creation order — probed with two items:
both the list and the subscription's `items` envelope carry them
oldest-first), which is `ResourceSpec.list_ascending`.

`price` is always the full inflated price object (`ref:price` — joined from
`prices` on every read); `livemode` is absent (one of the four objects
without it). The live body's `plan` and `current_trial` echoes are fields
the pinned spec does not declare and this world omits — allow-listed.

Proration on item create/update/delete is Phase 14's span: the
`proration_behavior` family is accepted and validated, and no proration
lines are generated yet (declared).
"""

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

import seahaven

from seahaven_stripe_world import _ids, _json, _seq
from seahaven_stripe_world.billing import subscription_lifecycle
from seahaven_stripe_world.billing.invoicing import add_interval
from seahaven_stripe_world.dispatch.params import Param, ParamSpec
from seahaven_stripe_world.dispatch.resource import (
    ListFilter,
    ResourceSpec,
    register,
)
from seahaven_stripe_world.resources import _lookup, events
from seahaven_stripe_world.serialize.fields import FieldMap, presence_sets, serializer_for
from seahaven_stripe_world.stripe_errors import invalid_request

if TYPE_CHECKING:
    from seahaven_stripe_world.dispatch.response import Request

__all__ = [
    "FIELDS",
    "ITEM_CREATE",
    "ITEM_DELETE",
    "ITEM_LIST",
    "ITEM_RETRIEVE",
    "ITEM_UPDATE",
    "SPEC",
    "create",
    "delete",
    "retrieve",
    "serialize",
    "update",
]

SUB = ("sub_",)
PRICE = ("price_",)
TXR = ("txr_",)

_TAX_RATES = Param(name="tax_rates", kind="array", item=Param(name="", kind="id", id_prefixes=TXR))

_BILLING_THRESHOLDS = Param(
    name="billing_thresholds", kind="object", shape=(Param(name="usage_gte", kind="integer"),)
)

_PRORATION_BEHAVIOR = Param(
    name="proration_behavior",
    kind="literal",
    choices=("create_prorations", "always_invoice", "none"),
)

_PRORATION_DATE = Param(name="proration_date", kind="timestamp")

_PAYMENT_BEHAVIOR = Param(
    name="payment_behavior",
    kind="literal",
    choices=(
        "allow_incomplete",
        "default_incomplete",
        "error_if_incomplete",
        "pending_if_incomplete",
    ),
)

ITEM_CREATE = ParamSpec(
    op_id="PostSubscriptionItems",
    body=(
        _BILLING_THRESHOLDS,
        _PAYMENT_BEHAVIOR,
        Param(name="price", kind="id", id_prefixes=PRICE, required=True),
        _PRORATION_BEHAVIOR,
        _PRORATION_DATE,
        Param(name="quantity", kind="integer"),
        Param(name="subscription", kind="id", id_prefixes=SUB, required=True),
        _TAX_RATES,
    ),
    metadata=True,
)

ITEM_UPDATE = ParamSpec(
    op_id="PostSubscriptionItemsItem",
    path=("item",),
    body=(
        _BILLING_THRESHOLDS,
        _PAYMENT_BEHAVIOR,
        Param(name="price", kind="id", id_prefixes=PRICE),
        _PRORATION_BEHAVIOR,
        _PRORATION_DATE,
        Param(name="quantity", kind="integer"),
        _TAX_RATES,
    ),
    metadata=True,
)

ITEM_DELETE = ParamSpec(
    op_id="DeleteSubscriptionItemsItem",
    path=("item",),
    expand=False,
    body=(
        Param(name="clear_usage", kind="boolean"),
        _PAYMENT_BEHAVIOR,
        _PRORATION_BEHAVIOR,
        _PRORATION_DATE,
    ),
)

ITEM_LIST = ParamSpec(
    op_id="GetSubscriptionItems",
    paginated=True,
    # Required at bind so its absence is the recorded `parameter_missing`
    # (the hand-written list assumes it is present).
    body=(Param(name="subscription", kind="id", id_prefixes=SUB, required=True),),
)

ITEM_RETRIEVE = ParamSpec(
    op_id="GetSubscriptionItemsItem",
    path=("item",),
)

# --- the serializer -----------------------------------------------------------------

always_present, omit_when_none = presence_sets("subscription_item")


def _price_row(ctx: seahaven.Ctx, row: Mapping[str, Any]) -> dict[str, Any]:
    return _lookup.require_row(ctx, "prices", "price", row["price"], param="price")


def _price_body(ctx: seahaven.Ctx, row: Mapping[str, Any]) -> dict[str, Any]:
    # Function-level: prices does not import this module, but keeping the
    # pattern uniform with the lifecycle's serializer dodge costs nothing.
    from seahaven_stripe_world.resources import prices

    return prices._serialize(ctx, _price_row(ctx, row))


def _tax_rate_bodies(ctx: seahaven.Ctx, row: Mapping[str, Any]) -> list[dict[str, Any]]:
    from seahaven_stripe_world.resources import tax_rates

    bodies = []
    loaded: object = _json.loads(row["tax_rates"])
    for id_ in loaded if isinstance(loaded, list) else []:
        tax_row = _lookup.require_row(ctx, "tax_rates", "tax rate", id_, param="tax_rates")
        bodies.append(tax_rates._serialize(ctx, tax_row))
    return bodies


FIELDS = FieldMap(
    object="subscription_item",
    table="subscription_items",
    columns={
        "id": "id",
        "created": "created",
        "billed_until": "billed_until",
        "billing_thresholds": "billing_thresholds",
        "current_period_end": "current_period_end",
        "current_period_start": "current_period_start",
        "metadata": "metadata",
        "quantity": "quantity",
        "subscription": "subscription",
        # `price` and `tax_rates` are derived below (always-inflated /
        # joined); `discounts` emits the stored ids.
        "discounts": "discounts",
    },
    timestamps=frozenset({"created", "billed_until", "current_period_end", "current_period_start"}),
    json_columns=frozenset({"billing_thresholds", "discounts", "metadata"}),
    derived={
        "price": _price_body,
        "tax_rates": _tax_rate_bodies,
    },
    always_present=always_present,
    omit_when_none=omit_when_none,
)

_serialize = serializer_for(FIELDS)


def serialize(ctx: seahaven.Ctx, row: Mapping[str, Any]) -> dict[str, Any]:
    return _serialize(ctx, row)


# --- the handlers --------------------------------------------------------------------


def _require_item(ctx: seahaven.Ctx, id_: str) -> dict[str, Any]:
    row = ctx.db.one("SELECT * FROM subscription_items WHERE id = ?", id_)
    if row is None:
        # Recorded verbatim (Phase 12): this resource's unknown-id family is
        # its own — no `param`, no `code`, 404, unlike the `No such …`
        # shape.
        raise invalid_request(f"Invalid subscription_item id: {id_}", status=404)
    return row


def _require_live_subscription(ctx: seahaven.Ctx, sub_id: str) -> dict[str, Any]:
    row = _lookup.require_row(ctx, "subscriptions", "subscription", sub_id, param="subscription")
    if row["status"] in subscription_lifecycle.TERMINAL_STATUSES:
        # Recorded (Phase 12): item create against a canceled subscription
        # answers `No such subscription` at 404 under `param: subscription`.
        raise _lookup.resource_missing("subscription", sub_id, param="subscription")
    return row


def serialize_subscription(ctx: seahaven.Ctx, sub_row: Mapping[str, Any]) -> dict[str, Any]:
    return subscription_lifecycle._serialize(ctx, sub_row)


def _emit_sub_updated(
    ctx: seahaven.Ctx, sub_id: str, old_body: Mapping[str, Any] | None = None
) -> None:
    """An item change is a subscription change: the sub-level event fires
    with the post-write snapshot and its `previous_attributes` — the old
    body must be the caller's PRE-write serialization, because `items` is
    derived from the live table."""
    row = _lookup.require_row(ctx, "subscriptions", "subscription", sub_id, param="subscription")
    body = subscription_lifecycle._serialize(ctx, row)
    events.emit_event(
        ctx,
        type="customer.subscription.updated",
        obj=body,
        previous=(
            subscription_lifecycle._previous(old_body, body) if old_body is not None else None
        ),
    )


def list_(ctx: seahaven.Ctx, req: Request) -> dict[str, Any]:
    """`GET /v1/subscription_items`: the engine's page in this resource's own
    order, with the envelope URL the live API answers — the required
    `subscription` filter spelled out in the query string (recorded,
    Phase 12), which the generic envelope's bare path cannot express."""
    from seahaven_stripe_world.dispatch import resource

    sub_id = req.params["subscription"]
    if req.page is None:
        raise seahaven.WorldBug("GetSubscriptionItems bound without a page")
    one_page = resource.page(
        ctx,
        table="subscription_items",
        object_name="subscription_item",
        where=("subscription = ?",),
        params=(sub_id,),
        limit=req.page.limit,
        starting_after=req.page.starting_after,
        ending_before=req.page.ending_before,
        ascending=True,
    )
    return one_page.envelope(
        f"/v1/subscription_items?subscription={sub_id}",
        lambda row: serialize(ctx, row),
    )


def retrieve(ctx: seahaven.Ctx, req: Request) -> dict[str, Any]:
    """`GET /v1/subscription_items/{item}`: the recorded unknown-id family is
    this resource's own (`Invalid subscription_item id: …`), so the handler
    replaces the engine's `No such …` lookup."""
    return serialize(ctx, _require_item(ctx, req.path_params["item"]))


def create(ctx: seahaven.Ctx, req: Request) -> dict[str, Any]:
    """`POST /v1/subscription_items`: the new item spans `[now, the
    subscription's period end)` — the recorded period (probed: a new item's
    `current_period_start` is the creation instant, not the subscription's
    anchor). The added configuration prorates per `proration_behavior`
    (Phase 14): a debit-only pair for the remainder of the cycle."""
    now = ctx.clock.iso()
    params = dict(req.params)
    sub = _require_live_subscription(ctx, params["subscription"])
    old_body = serialize_subscription(ctx, sub)
    price_row = _lookup.require_live_row(ctx, "prices", "price", params["price"], param="price")
    # The two probed item-addition guards (Phase 12 CR probes): no
    # duplicate price, no currency mismatch with the subscription.
    subscription_lifecycle._guard_item_addition(ctx, sub["id"], price_row, price_param="price")
    boundary = ctx.db.one(
        "SELECT MIN(current_period_end) AS end FROM subscription_items WHERE subscription = ?",
        sub["id"],
    )
    period_end = boundary["end"] if boundary is not None else None
    if period_end is None:
        interval, interval_count = subscription_lifecycle._interval_of(price_row)
        period_end = add_interval(now, interval=interval, interval_count=interval_count)
    old_items = ctx.db.rows(
        "SELECT * FROM subscription_items WHERE subscription = ? ORDER BY x_seq ASC", sub["id"]
    )
    behavior = params.pop("proration_behavior", "create_prorations")
    proration_date = params.pop("proration_date", None)
    params.pop("payment_behavior", None)
    cols: dict[str, Any] = {
        "id": _ids.stripe_id(ctx, "si_"),
        "x_seq": _seq.next_seq(ctx, "subscription_items"),
        "created": now,
        "current_period_start": now,
        "current_period_end": period_end,
        "subscription": sub["id"],
        # Recorded (Phase 12): omitted quantity answers 1 on a licensed price.
        "quantity": subscription_lifecycle._default_quantity(params.get("quantity"), price_row),
    }
    params.pop("quantity", None)
    cols.update({key: _store(value) for key, value in params.items()})
    if req.metadata is not None:
        cols["metadata"] = _json.dumps(dict(req.metadata.apply({})))
    columns = ", ".join(cols)
    placeholders = ", ".join("?" for _ in cols)
    ctx.db.execute(
        f"INSERT INTO subscription_items ({columns}) VALUES ({placeholders})", *cols.values()
    )
    subscription_lifecycle.emit_proration_side_effects(
        ctx,
        sub["id"],
        old_items,
        ctx.db.rows(
            "SELECT * FROM subscription_items WHERE subscription = ? ORDER BY x_seq ASC",
            sub["id"],
        ),
        proration_behavior=behavior,
        proration_date=proration_date,
    )
    row = _require_item(ctx, cols["id"])
    _emit_sub_updated(ctx, sub["id"], old_body)
    return serialize(ctx, row)


def _store(value: Any) -> Any:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, dict | list):
        return _json.dumps(value)
    return value


def update(ctx: seahaven.Ctx, req: Request) -> dict[str, Any]:
    """`POST /v1/subscription_items/{item}`: price/quantity/tax changes; the
    item's periods never move on an update (recorded periods stay anchored
    to the cycle). The configuration diff prorates per `proration_behavior`
    (Phase 14)."""
    row = _require_item(ctx, req.path_params["item"])
    sub = _require_live_subscription(ctx, row["subscription"])
    old_body = serialize_subscription(ctx, sub)
    params = dict(req.params)
    if "price" in params:
        _lookup.require_live_row(ctx, "prices", "price", params["price"], param="price")
    behavior = params.pop("proration_behavior", "create_prorations")
    proration_date = params.pop("proration_date", None)
    params.pop("payment_behavior", None)
    old_items = ctx.db.rows(
        "SELECT * FROM subscription_items WHERE subscription = ? ORDER BY x_seq ASC",
        row["subscription"],
    )
    sets = {key: _store(value) for key, value in params.items()}
    if req.metadata is not None:
        current = _json.loads(row.get("metadata"))
        sets["metadata"] = _json.dumps(dict(req.metadata.apply(current or {})))
    if sets:
        assignments = ", ".join(f"{column} = ?" for column in sets)
        ctx.db.execute(
            f"UPDATE subscription_items SET {assignments} WHERE id = ?",
            *sets.values(),
            row["id"],
        )
    subscription_lifecycle.emit_proration_side_effects(
        ctx,
        row["subscription"],
        old_items,
        ctx.db.rows(
            "SELECT * FROM subscription_items WHERE subscription = ? ORDER BY x_seq ASC",
            row["subscription"],
        ),
        proration_behavior=behavior,
        proration_date=proration_date,
    )
    fresh = _require_item(ctx, row["id"])
    _emit_sub_updated(ctx, row["subscription"], old_body)
    return serialize(ctx, fresh)


def delete(ctx: seahaven.Ctx, req: Request) -> dict[str, Any]:
    """`DELETE /v1/subscription_items/{item}`: the row is removed (the
    recorded three-key stub answer) and the removed configuration's
    credit-only proration lands per `proration_behavior` (Phase 14)."""
    row = _require_item(ctx, req.path_params["item"])
    sub = _require_live_subscription(ctx, row["subscription"])
    old_body = serialize_subscription(ctx, sub)
    old_items = ctx.db.rows(
        "SELECT * FROM subscription_items WHERE subscription = ? ORDER BY x_seq ASC",
        row["subscription"],
    )
    behavior = req.params.get("proration_behavior", "create_prorations")
    proration_date = req.params.get("proration_date")
    ctx.db.execute("DELETE FROM subscription_items WHERE id = ?", row["id"])
    subscription_lifecycle.emit_proration_side_effects(
        ctx,
        row["subscription"],
        old_items,
        ctx.db.rows(
            "SELECT * FROM subscription_items WHERE subscription = ? ORDER BY x_seq ASC",
            row["subscription"],
        ),
        proration_behavior=behavior,
        proration_date=proration_date,
    )
    _emit_sub_updated(ctx, row["subscription"], old_body)
    return {"id": row["id"], "object": "subscription_item", "deleted": True}


SPEC = register(
    ResourceSpec(
        object="subscription_item",
        table="subscription_items",
        id_prefix="si_",
        collection_url="/v1/subscription_items",
        serializer=serialize,
        columns=tuple(FIELDS.columns),
        # The retrieve's message family is its own (see `_require_item`), so
        # the engine never serves that action.
        missing_path_param=None,
        list_filters=(
            ListFilter(
                name="subscription",
                column="subscription",
                kind="exact",
                id_prefixes=SUB,
                required=True,
            ),
        ),
        # Probed (Phase 12): the one oldest-first list.
        list_ascending=True,
        creatable=None,
        updatable=None,
        delete=None,
        metadata=True,
        created_event=None,
        updated_event=None,
    )
)
