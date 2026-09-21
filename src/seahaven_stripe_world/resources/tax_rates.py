"""The tax_rates slice: plain generated CRUD over a TEXT-decimal rate.

The recorded wire shape (Phase 7 probe, 2026-09-20): `percentage` and
`effective_percentage` carry the same value, `rate_type` is `"percentage"`,
`flat_amount` and `jurisdiction_level` are null — the pinned request surface
can only create percentage rates, so the flat-amount columns exist but stay
empty until some later phase can write them. `percentage` arrives as a JSON
number and is stored as a TEXT decimal literal, emitted with exactly the
digits stored (`8.875`, never a float). It is immutable on update (the
pinned request body has no such parameter), which is why nothing here ever
re-mirrors `effective_percentage` after create.
"""

from typing import TYPE_CHECKING, Any

import seahaven

from seahaven_stripe_world import _json
from seahaven_stripe_world.dispatch.params import Param, ParamSpec
from seahaven_stripe_world.dispatch.resource import (
    ListFilter,
    ResourceSpec,
    register,
)
from seahaven_stripe_world.serialize.fields import FieldMap, presence_sets, serializer_for

if TYPE_CHECKING:
    from seahaven_stripe_world.dispatch.response import Request

__all__ = ["FIELDS", "SPEC"]

_TAX_TYPES = (
    "amusement_tax",
    "communications_tax",
    "gst",
    "hst",
    "igst",
    "jct",
    "lease_tax",
    "mass_transit_parking_tax",
    "parking_tax",
    "pst",
    "qst",
    "retail_delivery_fee",
    "rst",
    "sales_tax",
    "service_tax",
    "vat",
)

_TAX_RATE_BODY = (
    Param(name="active", kind="boolean"),
    Param(name="country", kind="string", max_length=5_000, unset_with_empty_string=True),
    Param(name="description", kind="string", max_length=5_000, unset_with_empty_string=True),
    Param(name="display_name", kind="string", max_length=5_000),
    Param(name="jurisdiction", kind="string", max_length=5_000, unset_with_empty_string=True),
    Param(name="state", kind="string", max_length=5_000, unset_with_empty_string=True),
    Param(name="tax_type", kind="literal", choices=_TAX_TYPES),
)

TAX_RATE_CREATE = ParamSpec(
    op_id="PostTaxRates",
    body=(
        Param(name="display_name", kind="string", max_length=5_000, required=True),
        Param(name="inclusive", kind="boolean", required=True),
        Param(name="percentage", kind="number", required=True),
        *_TAX_RATE_BODY,
    ),
    metadata=True,
)

TAX_RATE_UPDATE = ParamSpec(
    op_id="PostTaxRatesTaxRate",
    path=("tax_rate",),
    body=_TAX_RATE_BODY,
    metadata=True,
)

TAX_RATE_LIST = ParamSpec(op_id="GetTaxRates", paginated=True)

TAX_RATE_RETRIEVE = ParamSpec(op_id="GetTaxRatesTaxRate", path=("tax_rate",))

always_present, omit_when_none = presence_sets("tax_rate")

FIELDS = FieldMap(
    object="tax_rate",
    table="tax_rates",
    columns={
        "id": "id",
        "created": "created",
        "active": "active",
        "country": "country",
        "description": "description",
        "display_name": "display_name",
        "effective_percentage": "effective_percentage",
        "flat_amount": "flat_amount",
        "inclusive": "inclusive",
        "jurisdiction": "jurisdiction",
        "jurisdiction_level": "jurisdiction_level",
        "metadata": "metadata",
        "percentage": "percentage",
        "rate_type": "rate_type",
        "state": "state",
        "tax_type": "tax_type",
    },
    timestamps=frozenset({"created"}),
    json_columns=frozenset({"flat_amount", "metadata"}),
    booleans=frozenset({"active", "inclusive"}),
    decimals=frozenset({"percentage", "effective_percentage"}),
    constants={"livemode": False},
    always_present=always_present,
    omit_when_none=omit_when_none,
)

_serialize = serializer_for(FIELDS)


def _before_create(ctx: seahaven.Ctx, req: Request, cols: dict[str, Any]) -> dict[str, Any]:
    """The recorded read-only fields a percentage rate answers with:
    `effective_percentage` mirroring `percentage`, `rate_type` fixed, the
    flat-amount half and `jurisdiction_level` null."""
    percentage = _json.decimal_text(req.params["percentage"])
    cols["percentage"] = percentage
    cols["effective_percentage"] = percentage
    cols["rate_type"] = "percentage"
    cols["active"] = int(req.params.get("active", True))
    cols["inclusive"] = int(req.params["inclusive"])
    return cols


SPEC = register(
    ResourceSpec(
        object="tax_rate",
        table="tax_rates",
        id_prefix="txr_",
        collection_url="/v1/tax_rates",
        serializer=_serialize,
        columns=tuple(FIELDS.columns),
        # Probed: "No such tax rate: 'txr_nope'", placeholder spelling kept.
        error_name="tax rate",
        missing_path_param=None,
        list_filters=(
            ListFilter(name="active", column="active", kind="boolean"),
            ListFilter(name="created", column="created", kind="range"),
            ListFilter(name="inclusive", column="inclusive", kind="boolean"),
        ),
        creatable=TAX_RATE_CREATE,
        updatable=TAX_RATE_UPDATE,
        delete=None,
        metadata=True,
        before_create=_before_create,
        created_event="tax_rate.created",
        updated_event="tax_rate.updated",
    )
)
