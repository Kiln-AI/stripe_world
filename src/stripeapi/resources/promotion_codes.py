"""The promotion_codes slice.

The pinned version nests the coupon reference under
`promotion: {type: "coupon", coupon: "<id>"}` — there is no top-level
`coupon` field on the wire and no top-level `coupon` parameter, and sending
one answers `parameter_unknown` (all probed, Phase 7, 2026-09-20). The row
stores the bare coupon id on the `coupon` column and the serializer wraps it
into the `promotion` object at the edge; `expand[]=promotion.coupon` walks
the embedded `promotion` into the reference and inflates it, which the
generic resolver already knew how to do. Recorded refusals carried verbatim:
the missing-`promotion.coupon` message (dot-spelled param) versus the
unknown-coupon `resource_missing` (bracket-spelled), the code regex, the
duplicate-active-code message, and `expires_at`'s past and five-year bounds.
"""

import re
import string
from typing import TYPE_CHECKING, Any

import seahaven

from stripeapi import _json, _time
from stripeapi.dispatch.params import Param, ParamSpec
from stripeapi.dispatch.resource import (
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

CUS = ("cus_",)

#: The recorded code contract: "Valid characters are lower case letters (a-z),
#: upper case letters (A-Z), digits (0-9), and dashes (-)" — plus underscore,
#: per the regex the refusal printed.
_CODE = re.compile(r"\A[a-zA-Z0-9\-_]+\Z")

#: The minted `code` shape: eight uppercase alphanumerics (`BFDACGQS`,
#: `B0IW5ESE`, both recorded).
_CODE_ALPHABET = string.ascii_uppercase + string.digits

_RESTRICTIONS = Param(
    name="restrictions",
    kind="object",
    shape=(
        Param(name="first_time_transaction", kind="boolean"),
        Param(name="minimum_amount", kind="integer", minimum=1),
        Param(name="minimum_amount_currency", kind="string", max_length=5_000),
        Param(
            name="currency_options",
            kind="map",
            item=Param(
                name="", kind="object", shape=(Param(name="minimum_amount", kind="integer"),)
            ),
        ),
    ),
)

PROMO_CREATE = ParamSpec(
    op_id="PostPromotionCodes",
    body=(
        Param(name="active", kind="boolean"),
        Param(name="code", kind="string", max_length=5_000),
        Param(name="customer", kind="id", id_prefixes=CUS),
        Param(name="expires_at", kind="timestamp"),
        Param(name="max_redemptions", kind="integer", minimum=1),
        Param(
            name="promotion",
            kind="object",
            required=True,
            shape=(
                Param(name="coupon", kind="string", max_length=5_000),
                Param(name="type", kind="literal", choices=("coupon",), required=True),
            ),
        ),
        _RESTRICTIONS,
    ),
    metadata=True,
)

PROMO_UPDATE = ParamSpec(
    op_id="PostPromotionCodesPromotionCode",
    path=("promotion_code",),
    body=(
        Param(name="active", kind="boolean"),
        Param(
            name="restrictions",
            kind="object",
            shape=(
                Param(
                    name="currency_options",
                    kind="map",
                    item=Param(
                        name="",
                        kind="object",
                        shape=(Param(name="minimum_amount", kind="integer"),),
                    ),
                ),
            ),
        ),
    ),
    metadata=True,
)

PROMO_LIST = ParamSpec(op_id="GetPromotionCodes", paginated=True)

PROMO_RETRIEVE = ParamSpec(op_id="GetPromotionCodesPromotionCode", path=("promotion_code",))

always_present, omit_when_none = presence_sets("promotion_code")


def _promotion_wrapper(ctx: seahaven.Ctx, row) -> dict[str, Any]:
    """The pinned version's wrapper: the stored coupon id, dressed as
    `{"type": "coupon", "coupon": id}`."""
    return {"coupon": row["coupon"], "type": "coupon"}


FIELDS = FieldMap(
    object="promotion_code",
    table="promotion_codes",
    columns={
        "id": "id",
        "created": "created",
        "active": "active",
        "code": "code",
        "customer": "customer",
        "expires_at": "expires_at",
        "max_redemptions": "max_redemptions",
        "metadata": "metadata",
        "restrictions": "restrictions",
        "times_redeemed": "times_redeemed",
    },
    timestamps=frozenset({"created", "expires_at"}),
    json_columns=frozenset({"metadata", "restrictions"}),
    booleans=frozenset({"active"}),
    constants={"livemode": False, "customer_account": None},
    # The `coupon` column is not a wire field at the pinned version: it is
    # dressed into the `promotion` wrapper, and `expand[]=promotion.coupon`
    # inflates the id inside it (the generic resolver walks the embedded
    # object, so no expansion code lives in this module).
    derived={"promotion": _promotion_wrapper},
    always_present=always_present,
    omit_when_none=omit_when_none,
)

_serialize = serializer_for(FIELDS)


def _canonical_restrictions(restrictions: dict[str, Any] | None) -> dict[str, Any]:
    canonical = {
        "first_time_transaction": bool((restrictions or {}).get("first_time_transaction", False)),
        "minimum_amount": (restrictions or {}).get("minimum_amount"),
        "minimum_amount_currency": (restrictions or {}).get("minimum_amount_currency"),
    }
    currency_options = (restrictions or {}).get("currency_options")
    if currency_options:
        canonical["currency_options"] = currency_options
    return canonical


def _mint_code(ctx: seahaven.Ctx) -> str:
    return "".join(ctx.ids.random.choice(_CODE_ALPHABET) for _ in range(8))


def _before_create(ctx: seahaven.Ctx, req: Request, cols: dict[str, Any]) -> dict[str, Any]:
    params = req.params
    promotion = params["promotion"]
    coupon_id = promotion.get("coupon")
    if coupon_id is None:
        # Probed verbatim: dot-spelled param, unlike the bracket-spelled one
        # the unknown-coupon refusal below carries.
        raise invalid_request(
            "You must pass promotion.coupon when passing promotion",
            param="promotion.coupon",
            pre_execution=True,
        )
    # Probed: the unknown coupon refuses at 400 `resource_missing` with the
    # bracket-spelled param — a request-parameter id, not a path one.
    _lookup.require_live_row(
        ctx, "coupons", "coupon", coupon_id, param="promotion[coupon]", status=400
    )
    cols["coupon"] = coupon_id
    del cols["promotion"]
    if params.get("customer") is not None:
        _lookup.require_live_row(ctx, "customers", "customer", params["customer"], param="customer")
    code = params.get("code")
    if code is None:
        code = _mint_code(ctx)
    elif not _CODE.fullmatch(code):
        # Probed verbatim, regex and all.
        raise invalid_request(
            "This value must match the regex pattern. "
            "(/\\A[a-zA-Z0-9\\-_]+\\z/ does not match for the value "
            f"{code}).",
            param="code",
            pre_execution=True,
        )
    holder = ctx.db.one("SELECT id FROM promotion_codes WHERE code = ? AND active = 1", code)
    if holder is not None:
        # Probed verbatim: no code, no param, the value embedded.
        raise invalid_request(
            f"An active promotion code with `code: {code}` already exists.",
            pre_execution=True,
        )
    cols["code"] = code
    cols["active"] = int(params.get("active", True))
    expires_at = params.get("expires_at")
    if expires_at is not None:
        _check_expires_at(ctx, expires_at)
    cols["restrictions"] = _json.dumps(_canonical_restrictions(params.get("restrictions")))
    return cols


def _check_expires_at(ctx: seahaven.Ctx, iso: str) -> None:
    """Both probed verbatim: a past value and one beyond five years out."""
    now = ctx.clock.iso()
    if iso <= now:
        unix = _time.to_unix(iso)
        raise invalid_request(
            f"The parameter `expires_at` expects a unix timestamp representing a "
            f"date and time in the future. You specified the value `{unix}` which "
            f"is in the past.",
            param="expires_at",
            pre_execution=True,
        )
    if _time.to_unix(iso) - _time.to_unix(now) > 5 * 365 * 24 * 3600:
        raise invalid_request(
            "Invalid timestamp: can be no more than five years in the future.",
            param="expires_at",
            pre_execution=True,
        )


def _before_update(ctx: seahaven.Ctx, req: Request, sets: dict[str, Any]) -> dict[str, Any]:
    """At the pinned version the update's `restrictions` carries only
    `currency_options` (probed: the other keys answer `parameter_unknown`),
    which replaces the stored sub-key inside the otherwise-frozen
    restrictions object."""
    if "restrictions" in sets:
        sets["restrictions"] = _lookup.merge_json_map(
            ctx,
            "promotion_codes",
            req.path_params["promotion_code"],
            "restrictions",
            req.params.get("restrictions") or {},
            drop=("currency_options",),
        )
    return sets


SPEC = register(
    ResourceSpec(
        object="promotion_code",
        table="promotion_codes",
        id_prefix="promo_",
        collection_url="/v1/promotion_codes",
        serializer=_serialize,
        columns=tuple(FIELDS.columns),
        # Probed: "No such promotion code" — two words, unlike the
        # discriminator's underscore; the placeholder spelling is kept.
        error_name="promotion code",
        missing_path_param=None,
        list_filters=(
            ListFilter(name="active", column="active", kind="boolean"),
            ListFilter(name="code", column="code", kind="exact"),
            ListFilter(
                name="coupon",
                column="coupon",
                kind="exact",
                references="coupon",
            ),
            ListFilter(name="created", column="created", kind="range"),
            ListFilter(
                name="customer",
                column="customer",
                kind="exact",
                id_prefixes=CUS,
                references="customer",
            ),
        ),
        creatable=PROMO_CREATE,
        updatable=PROMO_UPDATE,
        delete=None,
        metadata=True,
        before_create=_before_create,
        before_update=_before_update,
        created_event="promotion_code.created",
        updated_event="promotion_code.updated",
    )
)
