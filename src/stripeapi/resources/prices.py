"""The prices slice: generated CRUD plus the two hand-written seams the
lookup-key machinery forces.

Every refusal message, default and wire shape here is pinned by live probe at
`2026-08-26.dahlia` (Phase 7, 2026-09-20): the product/product_data XOR, the
amount requirement, the tiered-needs-recurring and metered refusals, the
lookup-key conflict message (which names the holding price's id), and the
transfer semantics — the holder's key is **cleared**, not archived, and the
holder keeps `active` as it was, with the holder's `price.updated` emitted
before the subject's own event. `tiers`, `currency_options` and a tiered
price's amounts are accepted and stored but never serialized: none of them
appears in a recorded response body at the pinned version.
"""

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

import seahaven

from stripeapi import _ids, _json, _seq
from stripeapi.dispatch.params import Param, ParamSpec
from stripeapi.dispatch.resource import (
    ListFilter,
    ResourceSpec,
    register,
)
from stripeapi.resources import _lookup, events
from stripeapi.serialize.fields import FieldMap, presence_sets, serializer_for
from stripeapi.stripe_errors import invalid_request

if TYPE_CHECKING:
    from stripeapi.dispatch.response import Request

__all__ = [
    "FIELDS",
    "PRICE_AMOUNT_BODY",
    "SPEC",
    "create",
    "create_inline",
    "update",
]

PROD = ("prod_",)

_TAX_BEHAVIORS = ("exclusive", "inclusive", "unspecified")
_INTERVALS = ("day", "month", "week", "year")

_TIERS_ITEM = Param(
    name="",
    kind="object",
    shape=(
        # `up_to` is a count or the literal `inf` for the last tier.
        Param(name="up_to", kind="int_literal", choices=("inf",), required=True),
        Param(name="flat_amount", kind="integer"),
        Param(name="flat_amount_decimal", kind="string", max_length=5_000),
        Param(name="unit_amount", kind="integer"),
        Param(name="unit_amount_decimal", kind="string", max_length=5_000),
    ),
)

_RECURRING = Param(
    name="recurring",
    kind="object",
    shape=(
        Param(name="interval", kind="literal", choices=_INTERVALS, required=True),
        Param(name="interval_count", kind="integer", minimum=1),
        Param(name="meter", kind="string", max_length=5_000),
        Param(
            # Accepted and stored in the recurring JSON (probed, Phase 12:
            # a price created with it trials a subscription through
            # `trial_from_plan`); the pinned spec does not declare it on
            # the wire shape, so it is never serialized.
            name="trial_period_days",
            kind="integer",
            minimum=1,
        ),
        Param(name="usage_type", kind="literal", choices=("licensed", "metered")),
    ),
)

_CUSTOM_UNIT_AMOUNT = Param(
    name="custom_unit_amount",
    kind="object",
    shape=(
        Param(name="enabled", kind="boolean", required=True),
        Param(name="maximum", kind="integer"),
        Param(name="minimum", kind="integer"),
        Param(name="preset", kind="integer"),
    ),
)

PRICE_AMOUNT_BODY: tuple[Param, ...] = (
    _CUSTOM_UNIT_AMOUNT,
    Param(
        name="currency_options",
        kind="map",
        item=Param(
            name="",
            kind="object",
            shape=(
                Param(name="tax_behavior", kind="literal", choices=_TAX_BEHAVIORS),
                Param(name="unit_amount", kind="integer"),
                Param(name="unit_amount_decimal", kind="string", max_length=5_000),
            ),
        ),
    ),
    _RECURRING,
    Param(name="tax_behavior", kind="literal", choices=_TAX_BEHAVIORS),
    Param(name="tiers", kind="array", item=_TIERS_ITEM),
    Param(
        name="transform_quantity",
        kind="object",
        shape=(
            Param(name="divide_by", kind="integer", minimum=1, required=True),
            Param(name="round", kind="literal", choices=("down", "up"), required=True),
        ),
    ),
    Param(name="unit_amount", kind="integer"),
    Param(name="unit_amount_decimal", kind="string", max_length=5_000),
)

PRICE_CREATE = ParamSpec(
    op_id="PostPrices",
    body=(
        Param(name="active", kind="boolean"),
        Param(name="billing_scheme", kind="literal", choices=("per_unit", "tiered")),
        Param(name="currency", kind="string", max_length=5_000, required=True),
        Param(name="lookup_key", kind="string", max_length=5_000),
        Param(name="nickname", kind="string", max_length=5_000),
        Param(name="product", kind="id", id_prefixes=PROD),
        Param(
            name="product_data",
            kind="object",
            shape=(
                Param(name="active", kind="boolean"),
                Param(name="name", kind="string", max_length=5_000, required=True),
                Param(name="statement_descriptor", kind="string", max_length=22),
                Param(name="tax_code", kind="string", max_length=5_000),
                Param(name="unit_label", kind="string", max_length=12),
            ),
        ),
        Param(name="tiers_mode", kind="literal", choices=("graduated", "volume")),
        *PRICE_AMOUNT_BODY,
        Param(name="transfer_lookup_key", kind="boolean"),
    ),
    metadata=True,
)

PRICE_UPDATE = ParamSpec(
    op_id="PostPricesPrice",
    path=("price",),
    body=(
        Param(name="active", kind="boolean"),
        Param(
            name="currency_options",
            kind="map",
            item=Param(
                name="",
                kind="object",
                shape=(Param(name="unit_amount", kind="integer"),),
            ),
        ),
        Param(name="lookup_key", kind="string", max_length=5_000, unset_with_empty_string=True),
        Param(name="nickname", kind="string", max_length=5_000, unset_with_empty_string=True),
        Param(name="tax_behavior", kind="literal", choices=_TAX_BEHAVIORS),
        Param(name="transfer_lookup_key", kind="boolean"),
    ),
    metadata=True,
)

PRICE_LIST = ParamSpec(op_id="GetPrices", paginated=True)

PRICE_RETRIEVE = ParamSpec(op_id="GetPricesPrice", path=("price",))

always_present, omit_when_none = presence_sets("price")


def _recurring_body(ctx: seahaven.Ctx, row: Mapping[str, Any]) -> dict[str, Any] | None:
    """The wire's recurring: the stored JSON without its trial key."""
    loaded: object = _json.loads(row["recurring"])
    if not isinstance(loaded, dict):
        return None
    recurring = dict(loaded)
    recurring.pop("trial_period_days", None)
    return recurring


FIELDS = FieldMap(
    object="price",
    table="prices",
    columns={
        "id": "id",
        "created": "created",
        "active": "active",
        "billing_scheme": "billing_scheme",
        "currency": "currency",
        "custom_unit_amount": "custom_unit_amount",
        "lookup_key": "lookup_key",
        "metadata": "metadata",
        "nickname": "nickname",
        "product": "product",
        # `recurring` is derived below: the stored JSON may carry
        # `trial_period_days`, which never reaches the wire.
        "tax_behavior": "tax_behavior",
        "tiers_mode": "tiers_mode",
        "transform_quantity": "transform_quantity",
        "type": "type",
        "unit_amount": "unit_amount",
        "unit_amount_decimal": "unit_amount_decimal",
    },
    timestamps=frozenset({"created"}),
    # `tiers` and `currency_options` are stored columns that never serialise:
    # no recorded response body at the pinned version carries either, so they
    # stay out of the map entirely rather than riding `omit_when_none`.
    json_columns=frozenset({"custom_unit_amount", "metadata", "transform_quantity"}),
    derived={"recurring": _recurring_body},
    booleans=frozenset({"active"}),
    constants={"livemode": False},
    always_present=always_present,
    omit_when_none=omit_when_none,
)

_serialize = serializer_for(FIELDS)


# --- the derivation one price row is built from ---------------------------------


def _require_product(ctx: seahaven.Ctx, product_id: str) -> dict[str, Any]:
    return _lookup.require_live_row(ctx, "products", "product", product_id, param="product")


def _canonical_recurring(recurring: dict[str, Any]) -> dict[str, Any]:
    """The stored recurring JSON: the wire's four fields plus
    `trial_period_days` when the caller set it (probed, Phase 12 — the
    field `trial_from_plan` reads). The undeclared trial key never reaches
    the wire (`_recurring_body` strips it; the same ruling as every
    undeclared live field)."""
    canonical = {
        "interval": recurring["interval"],
        "interval_count": recurring.get("interval_count") or 1,
        "meter": recurring.get("meter"),
        "usage_type": recurring.get("usage_type") or "licensed",
    }
    if recurring.get("trial_period_days") is not None:
        canonical["trial_period_days"] = int(recurring["trial_period_days"])
    return canonical


def _canonical_custom_unit_amount(custom: dict[str, Any]) -> dict[str, Any]:
    """The recorded shape: `{maximum, minimum, preset}` — the request's
    `enabled` switch does not survive onto the wire."""
    return {
        "maximum": custom.get("maximum"),
        "minimum": custom.get("minimum"),
        "preset": custom.get("preset"),
    }


def _price_columns(
    ctx: seahaven.Ctx,
    params: dict[str, Any],
    *,
    product_id: str,
) -> dict[str, Any]:
    """Validated params (amount half + billing_scheme + active) -> the INSERT
    column dict. Every recorded refusal fires here, before any write."""
    recurring = params.get("recurring")
    if recurring is not None and recurring.get("usage_type") == "metered":
        # Probed verbatim: no code, no param. Meters do not exist in this
        # world, so the refusal is permanent — declared, not accidental.
        raise invalid_request(
            "Starting with Stripe version `2025-03-31.basil`, metered prices "
            "must be backed by meters.",
            pre_execution=True,
        )
    billing_scheme = params.get("billing_scheme") or "per_unit"
    if billing_scheme == "tiered":
        if recurring is None:
            # Probed verbatim, including the surprising `param: interval`.
            raise invalid_request(
                "Prices with `type=one_time` are not supported with tiered billing.",
                param="interval",
                pre_execution=True,
            )
        unit_amount = None
        unit_amount_decimal = None
    else:
        unit_amount = params.get("unit_amount")
        unit_amount_decimal = params.get("unit_amount_decimal")
        custom = params.get("custom_unit_amount")
        if unit_amount is None and unit_amount_decimal is None and custom is None:
            # Probed verbatim for one_time and recurring alike: no code, no
            # param.
            raise invalid_request(
                "Prices require an `unit_amount` or `unit_amount_decimal` parameter to be set.",
                pre_execution=True,
            )
        if unit_amount is not None:
            unit_amount_decimal = str(unit_amount)
    cols: dict[str, Any] = {
        "id": _ids.stripe_id(ctx, "price_"),
        "x_seq": _seq.next_seq(ctx, "prices"),
        "created": ctx.clock.iso(),
        "active": int(params.get("active", True)),
        "billing_scheme": billing_scheme,
        "currency": params["currency"],
        "product": product_id,
        "type": "recurring" if recurring is not None else "one_time",
        "tax_behavior": params.get("tax_behavior") or "unspecified",
        "unit_amount": unit_amount,
        "unit_amount_decimal": unit_amount_decimal,
        "nickname": params.get("nickname"),
        "lookup_key": params.get("lookup_key"),
        "tiers_mode": params.get("tiers_mode"),
    }
    if recurring is not None:
        cols["recurring"] = _json.dumps(_canonical_recurring(recurring))
    if params.get("custom_unit_amount") is not None:
        cols["custom_unit_amount"] = _json.dumps(
            _canonical_custom_unit_amount(params["custom_unit_amount"])
        )
    if params.get("tiers") is not None:
        cols["tiers"] = _json.dumps(params["tiers"])
    if params.get("currency_options") is not None:
        cols["currency_options"] = _json.dumps(params["currency_options"])
    if params.get("transform_quantity") is not None:
        cols["transform_quantity"] = _json.dumps(params["transform_quantity"])
    return cols


def _insert(ctx: seahaven.Ctx, cols: dict[str, Any]) -> dict[str, Any]:
    columns = ", ".join(cols)
    placeholders = ", ".join("?" for _ in cols)
    ctx.db.execute(f"INSERT INTO prices ({columns}) VALUES ({placeholders})", *cols.values())
    return _lookup.require_row(ctx, "prices", "price", cols["id"], param="price", status=404)


def _emit_created(ctx: seahaven.Ctx, row: dict[str, Any]) -> None:
    events.emit_event(ctx, type="price.created", obj=_serialize(ctx, row))


# --- the lookup-key seam ---------------------------------------------------------


def _lookup_holder(
    ctx: seahaven.Ctx, key: str, *, own_id: str | None = None
) -> dict[str, Any] | None:
    """The row currently holding `key`, any row: the conflict fires even when
    the holder is inactive (probed — data_model §3's live-only uniqueness was
    wrong, and the schema's partial index matches this reading)."""
    holder = ctx.db.one("SELECT * FROM prices WHERE lookup_key = ?", key)
    if holder is not None and holder["id"] != own_id:
        return holder
    return None


def _refuse_taken_key(holder: dict[str, Any]) -> None:
    raise invalid_request(
        f"A price (`{holder['id']}`) already uses that lookup key.",
        param="lookup_key",
        pre_execution=True,
    )


def _transfer_key(ctx: seahaven.Ctx, holder: dict[str, Any], key: str) -> None:
    """Clear the holder's key and emit its `price.updated` — the recorded
    transfer semantics: the holder keeps `active` as it was, and its event
    lands before the subject's own (`previous_attributes: {lookup_key: …}`,
    both probed)."""
    ctx.db.execute("UPDATE prices SET lookup_key = NULL WHERE id = ?", holder["id"])
    fresh = _lookup.require_row(ctx, "prices", "price", holder["id"], param="price")
    events.emit_event(
        ctx,
        type="price.updated",
        obj=_serialize(ctx, fresh),
        previous={"lookup_key": key},
    )


def _resolve_key(ctx: seahaven.Ctx, params: dict[str, Any], *, own_id: str | None = None) -> None:
    """The lookup-key rules shared by create and update: conflict without
    transfer, clear-and-emit with it. Runs before the subject's own write."""
    key = params.get("lookup_key")
    transfer = params.get("transfer_lookup_key")
    if key is None:
        return
    holder = _lookup_holder(ctx, key, own_id=own_id)
    if holder is None:
        return
    if not transfer:
        _refuse_taken_key(holder)
    _transfer_key(ctx, holder, key)


# --- the handlers ----------------------------------------------------------------


def create(ctx: seahaven.Ctx, req: Request) -> dict[str, Any]:
    """`POST /v1/prices` — hand-written for `product_data` (a second row) and
    the lookup-key transfer (a second write), the two things the engine's
    normalizer contract deliberately cannot do."""
    product = req.params.get("product")
    product_data = req.params.get("product_data")
    if product is None and product_data is None:
        # Probed verbatim: no code, no param.
        raise invalid_request(
            "You must specify either `product` or `product_data` when creating a price.",
            pre_execution=True,
        )
    if product is not None and product_data is not None:
        # Probed verbatim: param names the first of the pair, no code.
        raise invalid_request(
            "You may only specify one of these parameters: product, product_data.",
            param="product",
            pre_execution=True,
        )
    if product is not None:
        _require_product(ctx, product)
        product_id = product
    else:
        # Local import: products reaches back into this module for
        # `PRICE_AMOUNT_BODY`, so the cycle closes only inside the handlers.
        from stripeapi.resources import products as products_module

        product_id = products_module.insert_inline(ctx, product_data or {})
    _resolve_key(ctx, dict(req.params))
    cols = _price_columns(ctx, dict(req.params), product_id=product_id)
    if req.metadata is not None:
        # The engine's metadata handling, repeated here because this create
        # is hand-written.
        cols["metadata"] = _json.dumps(dict(req.metadata.apply({})))
    row = _insert(ctx, cols)
    _emit_created(ctx, row)
    return _serialize(ctx, row)


def create_inline(
    ctx: seahaven.Ctx, amount_params: dict[str, Any], *, product_id: str
) -> dict[str, Any]:
    """The price a product's `default_price_data` describes: same derivation,
    no lookup-key surface (the pinned request body for `default_price_data`
    carries no `lookup_key`) and **no event** — the caller owns the order,
    because the recorded sequence is `product.created` first, then this
    price's `price.created`."""
    cols = _price_columns(ctx, amount_params, product_id=product_id)
    return _insert(ctx, cols)


def update(ctx: seahaven.Ctx, req: Request) -> dict[str, Any]:
    """`POST /v1/prices/{price}` — the engine's update with the lookup-key
    seam in front: the holder's clear-and-emit must precede the subject's own
    event, which the engine emits, so both happen here in that order."""
    from stripeapi.dispatch.resource import update as engine_update

    price_id = req.path_params["price"]
    _resolve_key(ctx, dict(req.params), own_id=price_id)
    return engine_update(ctx, req)


def _before_update(ctx: seahaven.Ctx, req: Request, sets: dict[str, Any]) -> dict[str, Any]:
    """Engine-update column fix-ups: `transfer_lookup_key` is a directive,
    not a column (the wrapper has already applied it), and `currency_options`
    merges per currency into the stored map like metadata merges per key."""
    sets.pop("transfer_lookup_key", None)
    if "currency_options" in sets:
        sets["currency_options"] = _lookup.merge_json_map(
            ctx,
            "prices",
            req.path_params["price"],
            "currency_options",
            req.params.get("currency_options") or {},
        )
    return sets


#: The engine reads this through `Route.resource`; the handlers above are the
#: only hand-written actions.
SPEC = register(
    ResourceSpec(
        object="price",
        table="prices",
        id_prefix="price_",
        collection_url="/v1/prices",
        serializer=_serialize,
        columns=tuple(FIELDS.columns),
        error_name="price",
        list_filters=(
            ListFilter(name="active", column="active", kind="boolean"),
            ListFilter(name="created", column="created", kind="range"),
            ListFilter(name="currency", column="currency", kind="exact"),
            ListFilter(
                name="product",
                column="product",
                kind="exact",
                id_prefixes=PROD,
                references="product",
            ),
            ListFilter(
                name="type", column="type", kind="literal", choices=("one_time", "recurring")
            ),
            ListFilter(name="lookup_keys", column="lookup_key", kind="in"),
            ListFilter(
                name="recurring",
                column="recurring",
                kind="json",
                sub_shape=(
                    Param(name="interval", kind="literal", choices=_INTERVALS),
                    Param(name="meter", kind="string", max_length=5_000),
                    Param(name="usage_type", kind="literal", choices=("licensed", "metered")),
                ),
            ),
        ),
        creatable=PRICE_CREATE,
        updatable=PRICE_UPDATE,
        # No DeleteSpec: DELETE /v1/prices/{id} is unrouted (the recorded 404
        # "Unrecognized request URL" is the router's, not a handler's).
        delete=None,
        metadata=True,
        before_update=_before_update,
        created_event="price.created",
        updated_event="price.updated",
    )
)
