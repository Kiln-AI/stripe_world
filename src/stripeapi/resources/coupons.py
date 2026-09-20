"""The coupons slice: the one unprefixed, caller-suppliable id in the world.

Refusals, defaults and the minted id shape are pinned by live probe at
`2026-08-26.dahlia` (Phase 7, 2026-09-20): the percent/amount XOR with its
two bespoke messages, `amount_off`'s currency requirement, `repeating`'s
duration_in_months requirement (which names `param: duration`), the
redeem_by-in-the-past refusal, the 0.01 floor on `percent_off`, and the
`resource_already_exists` refusal for a taken custom id. `applies_to` and
`currency_options` are accepted and stored but never serialized — no recorded
response body carries either. The soft delete zeroes `valid` (the
`coupon.deleted` snapshot shows `valid: false`) and a later retrieve is a
404, both probed.
"""

from typing import TYPE_CHECKING, Any

import seahaven

from stripeapi import _ids, _json, _time
from stripeapi.dispatch.params import Param, ParamSpec
from stripeapi.dispatch.resource import (
    DeleteSpec,
    ListFilter,
    ResourceSpec,
    register,
)
from stripeapi.resources import _lookup
from stripeapi.serialize.fields import FieldMap, presence_sets, serializer_for
from stripeapi.stripe_errors import invalid_request

if TYPE_CHECKING:
    from stripeapi.dispatch.response import Request

__all__ = ["FIELDS", "SPEC"]

_DURATIONS = ("forever", "once", "repeating")

COUPON_CREATE = ParamSpec(
    op_id="PostCoupons",
    body=(
        Param(name="amount_off", kind="integer", minimum=1),
        Param(
            name="applies_to",
            kind="object",
            shape=(
                Param(
                    name="products",
                    kind="array",
                    item=Param(name="", kind="id", id_prefixes=("prod_",)),
                    required=True,
                ),
            ),
        ),
        Param(name="currency", kind="string", max_length=5_000),
        Param(
            name="currency_options",
            kind="map",
            item=Param(name="", kind="object", shape=(Param(name="amount_off", kind="integer"),)),
        ),
        Param(name="duration", kind="literal", choices=_DURATIONS),
        Param(name="duration_in_months", kind="integer", minimum=1),
        Param(name="id", kind="string", max_length=5_000),
        Param(name="max_redemptions", kind="integer", minimum=1),
        Param(name="name", kind="string", max_length=5_000, unset_with_empty_string=True),
        Param(name="percent_off", kind="number"),
        Param(name="redeem_by", kind="timestamp"),
    ),
    metadata=True,
)

COUPON_UPDATE = ParamSpec(
    op_id="PostCouponsCoupon",
    path=("coupon",),
    body=(
        Param(
            name="currency_options",
            kind="map",
            item=Param(name="", kind="object", shape=(Param(name="amount_off", kind="integer"),)),
        ),
        Param(name="name", kind="string", max_length=5_000, unset_with_empty_string=True),
    ),
    metadata=True,
)

COUPON_LIST = ParamSpec(op_id="GetCoupons", paginated=True)

COUPON_RETRIEVE = ParamSpec(op_id="GetCouponsCoupon", path=("coupon",))

COUPON_DELETE = ParamSpec(op_id="DeleteCouponsCoupon", path=("coupon",), expand=False)

always_present, omit_when_none = presence_sets("coupon")

FIELDS = FieldMap(
    object="coupon",
    table="coupons",
    columns={
        "id": "id",
        "created": "created",
        "amount_off": "amount_off",
        "currency": "currency",
        "duration": "duration",
        "duration_in_months": "duration_in_months",
        "max_redemptions": "max_redemptions",
        "metadata": "metadata",
        "name": "name",
        "percent_off": "percent_off",
        "redeem_by": "redeem_by",
        "times_redeemed": "times_redeemed",
        "valid": "valid",
    },
    timestamps=frozenset({"created", "redeem_by"}),
    json_columns=frozenset({"metadata"}),
    booleans=frozenset({"valid"}),
    decimals=frozenset({"percent_off"}),
    constants={"livemode": False},
    always_present=always_present,
    omit_when_none=omit_when_none,
)

_serialize = serializer_for(FIELDS)


def _mint(ctx: seahaven.Ctx, supplied: str | None) -> str:
    return _ids.coupon_id(ctx, supplied)


def _before_create(ctx: seahaven.Ctx, req: Request, cols: dict[str, Any]) -> dict[str, Any]:
    params = req.params
    has_amount = params.get("amount_off") is not None
    has_percent = params.get("percent_off") is not None
    if not has_amount and not has_percent:
        # Probed verbatim: `code: parameter_missing`, no `param`.
        raise invalid_request(
            "Must provide percent_off or amount_off.",
            code="parameter_missing",
            pre_execution=True,
        )
    if has_amount and has_percent:
        # Probed verbatim: no code, no param.
        raise invalid_request(
            "Received both percent_off and amount_off parameters. Please pass in only one.",
            pre_execution=True,
        )
    if has_amount and params.get("currency") is None:
        # Probed verbatim: param names the currency, message has no period.
        raise invalid_request(
            "You must pass currency when passing amount_off",
            param="currency",
            pre_execution=True,
        )
    duration = params.get("duration") or "once"
    cols["duration"] = duration
    if duration == "repeating" and params.get("duration_in_months") is None:
        # Probed verbatim: the param named is `duration`, not
        # `duration_in_months` — Stripe's own inconsistency, kept as recorded.
        raise invalid_request(
            "The duration_in_months param must be set when creating a coupon "
            "with a repeating duration",
            param="duration",
            pre_execution=True,
        )
    if has_percent:
        percent = params["percent_off"]
        if percent < 0.01:
            # Probed verbatim for 0: the value prints as a Ruby float and the
            # bound is quoted — `'0.0'`.
            raise invalid_request(
                f"This value must be greater than or equal to 0.01 "
                f"(it currently is '{float(percent)}').",
                param="percent_off",
                pre_execution=True,
            )
        cols["percent_off"] = _json.decimal_text(percent)
        if params.get("currency_options") is not None:
            # Probed verbatim: a percent coupon carries no per-currency
            # amounts, and the refusal outranks the map's own validation.
            raise invalid_request(
                "You may only specify one of these parameters: currency_options, percent_off.",
                param="currency_options",
                pre_execution=True,
            )
    supplied = params.get("id")
    if supplied is not None and ctx.db.one("SELECT id FROM coupons WHERE id = ?", supplied):
        # Probed verbatim: `resource_already_exists`, no param.
        raise invalid_request(
            "Coupon already exists.",
            code="resource_already_exists",
            pre_execution=True,
        )
    if params.get("redeem_by") is not None and params["redeem_by"] <= ctx.clock.iso():
        # Probed verbatim, value embedded: the comparison is on the canonical
        # TEXT timestamp, which sorts correctly.
        unix = _time.to_unix(params["redeem_by"])
        raise invalid_request(
            f"The parameter `redeem_by` expects a unix timestamp representing a "
            f"date and time in the future. You specified the value `{unix}` which is "
            f"in the past.",
            param="redeem_by",
            pre_execution=True,
        )
    cols.setdefault("valid", 1)
    for key in ("applies_to", "currency_options"):
        if params.get(key) is not None:
            cols[key] = _json.dumps(params[key])
    return cols


def _before_update(ctx: seahaven.Ctx, req: Request, sets: dict[str, Any]) -> dict[str, Any]:
    if "currency_options" in sets:
        sets["currency_options"] = _lookup.merge_json_map(
            ctx,
            "coupons",
            req.path_params["coupon"],
            "currency_options",
            req.params.get("currency_options") or {},
        )
    return sets


SPEC = register(
    ResourceSpec(
        object="coupon",
        table="coupons",
        id_prefix="",
        collection_url="/v1/coupons",
        serializer=_serialize,
        columns=tuple(FIELDS.columns),
        # The unprefixed, caller-suppliable id: `coupon_id` mints or adopts.
        mint_id=_mint,
        # Probed: a missing coupon path id keeps the placeholder
        # (`No such coupon: 'NOPE123'`, param coupon).
        missing_path_param=None,
        list_filters=(ListFilter(name="created", column="created", kind="range"),),
        creatable=COUPON_CREATE,
        updatable=COUPON_UPDATE,
        # Probed (Phase 7): the soft delete also zeroes `valid` (the
        # `coupon.deleted` snapshot carries `valid: false`), and a later
        # retrieve of the tombstone is a 404, not the customers' stub.
        delete=DeleteSpec(mode="soft", zero_columns=("valid",), deleted_retrieve="missing"),
        metadata=True,
        before_create=_before_create,
        before_update=_before_update,
        created_event="coupon.created",
        updated_event="coupon.updated",
        deleted_event="coupon.deleted",
    )
)
