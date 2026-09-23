"""The subscriptions slice (Phase 12): the eight-status machine's routed
surface — create, update, immediate cancel, `/resume` — plus the
engine-served reads.

Every wire shape and refusal is a live probe at `2026-08-26.dahlia`
(2026-09-20/21): the created body's constants (`invoice_settings` with its
`issuer`, `payment_settings` with `save_default_payment_method: "off"`,
`cancellation_details`, `trial_settings` defaulting
`missing_payment_method: create_invoice`), the missing-id 404s
(`No such subscription`, `param: id`), the update-on-canceled refusal
(`invalid_canceled_subscription_fields`), and the list's `status` filter
with its own ten-value enum message (the eight statuses plus `all` and
`ended`).

The live body's top-level `plan`/`quantity` echoes, the item's
`current_trial`, and `trial_settings.end_behavior.billing_cycle_anchor`
are fields the pinned spec does not declare; the spec is the authority for
shape and this world omits them (allow-listed). The recording account's
`billing_mode: flexible` is dashboard configuration; this world's constant
is `classic` until the flexible-mode phase.

Scope cuts, all declared in `allowed_differences.py`'s structural section:
`billing_cycle_anchor` (the future-anchor stub invoice is flexible-mode
proration arithmetic, conformance scenario 1's), `billing_mode`,
`billing_schedules`, `billing_cycle_anchor_config`, `add_invoice_items`,
`backdate_start_date`, `off_session`, `automatic_tax`, Connect fields,
`pending_invoice_item_interval`, the item-level `discounts`/`price_data`,
the legacy `/v1/customers/{customer}/subscriptions*` aliases, `/migrate`
and the discount sub-routes.
"""

from collections.abc import Mapping
from typing import Any

import seahaven

from seahaven_stripe_world import _json, _time
from seahaven_stripe_world.billing import subscription_lifecycle
from seahaven_stripe_world.dispatch.params import Param, ParamSpec
from seahaven_stripe_world.dispatch.resource import ResourceSpec, register
from seahaven_stripe_world.dispatch.response import Request
from seahaven_stripe_world.resources import _lookup
from seahaven_stripe_world.serialize.fields import FieldMap, presence_sets, serializer_for

__all__ = [
    "FIELDS",
    "SPEC",
    "SUB_CREATE",
    "SUB_DELETE",
    "SUB_LIST",
    "SUB_RESUME",
    "SUB_RETRIEVE",
    "SUB_UPDATE",
    "cancel",
    "create",
    "list_",
    "resume",
    "serialize",
    "update",
]

CUS = ("cus_",)
PM = ("pm_",)
PRICE = ("price_",)
TXR = ("txr_",)
SUB = ("sub_",)
DISCOUNT = ("di_",)

PAYMENT_BEHAVIORS = (
    "allow_incomplete",
    "error_if_incomplete",
    "pending_if_incomplete",
    "default_incomplete",
)

#: The recorded `status` filter enum — the eight statuses plus `all` and
#: `ended`, message verbatim (Phase 12 probe).
STATUS_FILTERS = (
    "active",
    "past_due",
    "unpaid",
    "canceled",
    "incomplete",
    "incomplete_expired",
    "trialing",
    "paused",
    "all",
    "ended",
)

_ENDED_STATUSES = ("canceled", "incomplete_expired")

_ITEMS_SHAPE = Param(
    name="",
    kind="object",
    shape=(
        Param(
            name="billing_thresholds",
            kind="object",
            shape=(Param(name="usage_gte", kind="integer"),),
        ),
        # The update flow names the item it changes by `id`; a create that
        # sends one ignores it (a simplification no recorded step pins).
        Param(name="id", kind="string", max_length=5_000),
        Param(name="price", kind="id", id_prefixes=PRICE),
        Param(name="quantity", kind="integer"),
        Param(name="tax_rates", kind="array", item=Param(name="", kind="id", id_prefixes=TXR)),
    ),
)

_DISCOUNTS = Param(
    name="discounts",
    kind="array",
    item=Param(
        name="",
        kind="object",
        # Coupon ids are caller-suppliable and unprefixed (Phase 7), so this
        # is a string the lifecycle resolves, not a prefix-checked id.
        shape=(Param(name="coupon", kind="string", max_length=5_000, required=True),),
    ),
)

_TRIAL_SETTINGS = Param(
    name="trial_settings",
    kind="object",
    shape=(
        Param(
            name="end_behavior",
            kind="object",
            shape=(
                Param(
                    name="missing_payment_method",
                    kind="literal",
                    choices=("cancel", "create_invoice", "pause"),
                ),
            ),
        ),
    ),
)

_TRIAL_END = Param(name="trial_end", kind="int_literal", choices=("now",))

_PAYMENT_SETTINGS = Param(
    name="payment_settings",
    kind="object",
    shape=(
        Param(
            name="payment_method_options",
            kind="object",
            shape=(Param(name="card", kind="object", shape=()),),
        ),
        Param(
            name="payment_method_types",
            kind="array",
            item=Param(name="", kind="string", max_length=5_000),
        ),
        Param(
            name="save_default_payment_method",
            kind="literal",
            # The spec's own two-value enum (`on_subscription`, not the
            # historical `on_subscription_update` spelling).
            choices=("off", "on_subscription"),
        ),
    ),
)

_DEFAULT_TAX_RATES = Param(
    name="default_tax_rates", kind="array", item=Param(name="", kind="id", id_prefixes=TXR)
)

_PRORATION_BEHAVIOR = Param(
    name="proration_behavior",
    kind="literal",
    choices=("create_prorations", "always_invoice", "none"),
)

SUB_CREATE = ParamSpec(
    op_id="PostSubscriptions",
    body=(
        Param(name="cancel_at", kind="timestamp"),
        Param(name="cancel_at_period_end", kind="boolean"),
        Param(
            name="collection_method",
            kind="literal",
            choices=("charge_automatically", "send_invoice"),
        ),
        Param(name="customer", kind="id", id_prefixes=CUS, required=True),
        Param(name="days_until_due", kind="integer"),
        Param(name="default_payment_method", kind="id", id_prefixes=PM),
        _DEFAULT_TAX_RATES,
        Param(name="description", kind="string", max_length=5_000, unset_with_empty_string=True),
        _DISCOUNTS,
        Param(name="items", kind="array", item=_ITEMS_SHAPE, required=True),
        Param(name="payment_behavior", kind="literal", choices=PAYMENT_BEHAVIORS),
        _PAYMENT_SETTINGS,
        Param(
            name="billing_thresholds",
            kind="object",
            shape=(
                Param(name="amount_gte", kind="integer"),
                Param(name="reset_billing_cycle_anchor", kind="boolean"),
            ),
        ),
        _TRIAL_END,
        Param(name="trial_from_plan", kind="boolean"),
        Param(name="trial_period_days", kind="integer"),
        _TRIAL_SETTINGS,
    ),
    metadata=True,
)

SUB_UPDATE = ParamSpec(
    op_id="PostSubscriptionsSubscriptionExposedId",
    path=("subscription_exposed_id",),
    body=(
        Param(name="cancel_at", kind="timestamp", unset_with_empty_string=True),
        Param(name="cancel_at_period_end", kind="boolean"),
        Param(
            name="cancellation_details",
            kind="object",
            shape=(
                Param(name="comment", kind="string", max_length=5_000),
                Param(name="feedback", kind="string", max_length=5_000),
                Param(name="feedback_option", kind="string", max_length=5_000),
            ),
        ),
        Param(
            name="collection_method",
            kind="literal",
            choices=("charge_automatically", "send_invoice"),
        ),
        Param(name="days_until_due", kind="integer"),
        Param(
            name="default_payment_method", kind="id", id_prefixes=PM, unset_with_empty_string=True
        ),
        _DEFAULT_TAX_RATES,
        Param(name="description", kind="string", max_length=5_000, unset_with_empty_string=True),
        _DISCOUNTS,
        Param(name="items", kind="array", item=_ITEMS_SHAPE),
        Param(
            name="pause_collection",
            kind="object",
            unset_with_empty_string=True,
            shape=(
                Param(
                    name="behavior",
                    kind="literal",
                    choices=("keep_as_draft", "mark_uncollectible", "void"),
                ),
                Param(name="resumes_at", kind="timestamp"),
            ),
        ),
        Param(name="payment_behavior", kind="literal", choices=PAYMENT_BEHAVIORS),
        _PAYMENT_SETTINGS,
        Param(
            name="billing_cycle_anchor",
            kind="literal",
            # Update accepts only the literals (spec3.json); the free
            # timestamp form is create-only and stays cut (declared).
            choices=("now", "unchanged"),
        ),
        _PRORATION_BEHAVIOR,
        Param(name="proration_date", kind="timestamp"),
        Param(
            name="billing_thresholds",
            kind="object",
            shape=(
                Param(name="amount_gte", kind="integer"),
                Param(name="reset_billing_cycle_anchor", kind="boolean"),
            ),
        ),
        _TRIAL_END,
        Param(name="trial_from_plan", kind="boolean"),
        _TRIAL_SETTINGS,
    ),
    metadata=True,
)

SUB_DELETE = ParamSpec(
    op_id="DeleteSubscriptionsSubscriptionExposedId",
    path=("subscription_exposed_id",),
    expand=False,
    body=(
        Param(
            name="cancellation_details",
            kind="object",
            shape=(
                Param(name="comment", kind="string", max_length=5_000),
                Param(name="feedback", kind="string", max_length=5_000),
                Param(name="feedback_option", kind="string", max_length=5_000),
            ),
        ),
        Param(name="invoice_now", kind="boolean"),
        Param(name="prorate", kind="boolean"),
    ),
)

SUB_RESUME = ParamSpec(
    op_id="PostSubscriptionsSubscriptionResume",
    path=("subscription",),
    body=(
        Param(name="billing_cycle_anchor", kind="literal", choices=("now", "unchanged")),
        _PRORATION_BEHAVIOR,
        Param(name="proration_date", kind="timestamp"),
    ),
)

SUB_LIST = ParamSpec(
    op_id="GetSubscriptions",
    paginated=True,
    body=(
        Param(
            name="collection_method",
            kind="literal",
            choices=("charge_automatically", "send_invoice"),
        ),
        Param(name="created", kind="range"),
        Param(name="customer", kind="id", id_prefixes=CUS),
        Param(name="price", kind="id", id_prefixes=PRICE),
        Param(name="status", kind="literal", choices=STATUS_FILTERS),
    ),
)

SUB_RETRIEVE = ParamSpec(
    op_id="GetSubscriptionsSubscriptionExposedId",
    path=("subscription_exposed_id",),
)

# --- the serializer -----------------------------------------------------------------

always_present, omit_when_none = presence_sets("subscription")


def _billing_mode(ctx: seahaven.Ctx, row: Mapping[str, Any]) -> dict[str, Any]:
    # The wire object from the column; `updated_at` is the creation instant
    # (the mode has never changed under this world's clock). The recording
    # account answers `flexible` — dashboard config, allow-listed.
    return {
        "type": row["billing_mode"],
        "flexible": None,
        "updated_at": _time.to_unix(row["created"]),
    }


def _default_tax_rate_bodies(ctx: seahaven.Ctx, row: Mapping[str, Any]) -> list[dict[str, Any]]:
    from seahaven_stripe_world.resources import tax_rates

    bodies = []
    loaded: object = _json.loads(row["default_tax_rates"])
    for id_ in loaded if isinstance(loaded, list) else []:
        tax_row = _lookup.require_row(ctx, "tax_rates", "tax rate", id_, param="default_tax_rates")
        bodies.append(tax_rates._serialize(ctx, tax_row))
    return bodies


def _discount_ids(ctx: seahaven.Ctx, row: Mapping[str, Any]) -> list[str]:
    # Stored as full inline discount objects (data_model §6 — no discounts
    # table); emitted as the bare `di_` ids unless expanded.
    loaded: object = _json.loads(row["discounts"])
    discounts = loaded if isinstance(loaded, list) else []
    return [dict(discount)["id"] for discount in discounts]


def _items_envelope(ctx: seahaven.Ctx, row: Mapping[str, Any]) -> dict[str, Any]:
    # Always present (recorded), always-inflated items, oldest-first — the
    # one nested collection the live API orders by creation. The recorded
    # envelope also carries `total_count`, which the pinned spec's nested
    # list schema does not declare; the spec is the authority for shape, so
    # it is omitted (the `charge.refunds` ruling, Phase 9) and allow-listed.
    from seahaven_stripe_world.resources import subscription_items

    items = ctx.db.rows(
        "SELECT * FROM subscription_items WHERE subscription = ? ORDER BY x_seq ASC",
        row["id"],
    )
    data = [subscription_items.serialize(ctx, item) for item in items]
    return {
        "object": "list",
        "data": data,
        "has_more": False,
        "url": f"/v1/subscription_items?subscription={row['id']}",
    }


FIELDS = FieldMap(
    object="subscription",
    table="subscriptions",
    columns={
        "id": "id",
        "created": "created",
        "billing_cycle_anchor": "billing_cycle_anchor",
        "billing_cycle_anchor_config": "billing_cycle_anchor_config",
        "billing_schedules": "billing_schedules",
        "billing_thresholds": "billing_thresholds",
        "cancel_at": "cancel_at",
        "cancel_at_period_end": "cancel_at_period_end",
        "canceled_at": "canceled_at",
        "cancellation_details": "cancellation_details",
        "collection_method": "collection_method",
        "currency": "currency",
        "customer": "customer",
        "days_until_due": "days_until_due",
        "default_payment_method": "default_payment_method",
        "description": "description",
        "ended_at": "ended_at",
        "invoice_settings": "invoice_settings",
        "latest_invoice": "latest_invoice",
        "metadata": "metadata",
        "next_pending_invoice_item_invoice": "next_pending_invoice_item_invoice",
        "pause_collection": "pause_collection",
        "payment_settings": "payment_settings",
        "pending_invoice_item_interval": "pending_invoice_item_interval",
        "pending_setup_intent": "pending_setup_intent",
        "pending_update": "pending_update",
        "schedule": "schedule",
        "start_date": "start_date",
        "status": "status",
        "trial_end": "trial_end",
        "trial_settings": "trial_settings",
        "trial_start": "trial_start",
        # derived below: billing_mode, default_tax_rates, discounts, items
    },
    timestamps=frozenset(
        {
            "created",
            "billing_cycle_anchor",
            "cancel_at",
            "canceled_at",
            "ended_at",
            "start_date",
            "trial_end",
            "trial_start",
        }
    ),
    json_columns=frozenset(
        {
            "billing_cycle_anchor_config",
            "billing_schedules",
            "billing_thresholds",
            "cancellation_details",
            "invoice_settings",
            "metadata",
            "pause_collection",
            "payment_settings",
            "pending_invoice_item_interval",
            "pending_update",
            "trial_settings",
        }
    ),
    booleans=frozenset({"cancel_at_period_end"}),
    derived={
        "billing_mode": _billing_mode,
        "default_tax_rates": _default_tax_rate_bodies,
        "discounts": _discount_ids,
        "items": _items_envelope,
    },
    constants={
        "application": None,
        "application_fee_percent": None,
        "automatic_tax": {"disabled_reason": None, "enabled": False, "liability": None},
        "customer_account": None,
        "default_source": None,
        "managed_payments": None,
        "on_behalf_of": None,
        "test_clock": None,
        "transfer_data": None,
    },
    always_present=always_present,
    omit_when_none=omit_when_none,
)


_serialize = serializer_for(FIELDS)


def serialize(ctx: seahaven.Ctx, row: Mapping[str, Any]) -> dict[str, Any]:
    return _serialize(ctx, row)


# --- the handlers --------------------------------------------------------------------


def create(ctx: seahaven.Ctx, req: Request) -> dict[str, Any]:
    """`POST /v1/subscriptions`: the lifecycle's creation flow (the
    hand-written rule — it writes items and invoices and emits)."""
    params = dict(req.params)
    if req.metadata is not None:
        params["metadata"] = dict(req.metadata.apply({}))
    params.setdefault("metadata", {})
    return subscription_lifecycle.create_subscription(ctx, params)


def update(ctx: seahaven.Ctx, req: Request) -> dict[str, Any]:
    """`POST /v1/subscriptions/{id}`: the lifecycle's update flow. The
    proration family and `billing_cycle_anchor` drive the Phase 14 side
    effects inside the lifecycle; `payment_behavior` is update-legal
    including `pending_if_incomplete` and consumed here."""
    params = dict(req.params)
    params.pop("payment_behavior", None)
    if req.metadata is not None:
        row = _lookup.require_row(
            ctx,
            "subscriptions",
            "subscription",
            req.path_params["subscription_exposed_id"],
            param="id",
        )
        current = _json.loads(row.get("metadata"))
        params["metadata"] = dict(req.metadata.apply(current or {}))
    return subscription_lifecycle.apply_update(
        ctx, req.path_params["subscription_exposed_id"], params
    )


def cancel(ctx: seahaven.Ctx, req: Request) -> dict[str, Any]:
    """`DELETE /v1/subscriptions/{id}`: immediate cancel — the full canceled
    body, not the deleted stub (recorded)."""
    return subscription_lifecycle.cancel_subscription(
        ctx,
        req.path_params["subscription_exposed_id"],
        prorate=req.params.get("prorate") is True,
        invoice_now=req.params.get("invoice_now") is True,
        cancellation_details=req.params.get("cancellation_details"),
    )


def resume(ctx: seahaven.Ctx, req: Request) -> dict[str, Any]:
    """`POST /v1/subscriptions/{id}/resume`: the recorded park (the flow's
    docstring in the lifecycle module)."""
    params = dict(req.params)
    params.pop("proration_behavior", None)
    params.pop("proration_date", None)
    return subscription_lifecycle.resume_subscription(
        ctx,
        req.path_params["subscription"],
        billing_cycle_anchor=params.get("billing_cycle_anchor", "now"),
    )


def list_(ctx: seahaven.Ctx, req: Request) -> dict[str, Any]:
    """`GET /v1/subscriptions`: hand-written for the two filter spellings the
    engine has no knob for — `status=all` matches everything and
    `status=ended` names the terminal pair (both in the recorded enum
    message) — and for `price`, a membership filter through the items."""
    from seahaven_stripe_world.dispatch import resource

    where: list[str] = []
    binds: list[Any] = []
    for name, value in req.params.items():
        if name == "status":
            if value == "all":
                continue
            if value == "ended":
                placeholders = ", ".join("?" for _ in _ENDED_STATUSES)
                where.append(f"status IN ({placeholders})")
                binds.extend(_ENDED_STATUSES)
            else:
                where.append("status = ?")
                binds.append(value)
        elif name == "customer":
            where.append("customer = ?")
            binds.append(value)
        elif name == "collection_method":
            where.append("collection_method = ?")
            binds.append(value)
        elif name == "price":
            where.append(
                "EXISTS (SELECT 1 FROM subscription_items si "
                "WHERE si.subscription = subscriptions.id AND si.price = ?)"
            )
            binds.append(value)
        elif name == "created":
            for op, bound in value.items():
                where.append(f"created {resource._RANGE_SQL[op]} ?")
                binds.append(bound)
        else:
            raise seahaven.WorldBug(f"unexpected list parameter {name!r} on GetSubscriptions")
    if req.page is None:
        raise seahaven.WorldBug("GetSubscriptions bound without a page")
    one_page = resource.page(
        ctx,
        table="subscriptions",
        object_name="subscription",
        where=where,
        params=binds,
        limit=req.page.limit,
        starting_after=req.page.starting_after,
        ending_before=req.page.ending_before,
    )
    return one_page.envelope(req.path, lambda row: serialize(ctx, row))


SPEC = register(
    ResourceSpec(
        object="subscription",
        table="subscriptions",
        id_prefix="sub_",
        collection_url="/v1/subscriptions",
        serializer=serialize,
        columns=tuple(FIELDS.columns),
        # Recorded: the paths keep their placeholders but every missing-id
        # message says `No such subscription` under `param: "id"`.
        missing_path_param="id",
        error_name="subscription",
        creatable=None,
        updatable=None,
        delete=None,
        metadata=True,
        created_event=None,
        updated_event=None,
    )
)
