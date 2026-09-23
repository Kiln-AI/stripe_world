"""The payouts: manual payouts drawn against the available balance, with
cancel and reverse, over the `payouts` table.

**What is recorded versus ruled** (Phase 11, probed 2026-09-20): the sandbox
cannot produce a successful payout — no external account in any currency, a
recording key that cannot add one (403 `more_permissions_required`), top-ups
unsupported for its country — so the cassette pins the create refusals
(amount floor, missing amount, the currency list), the missing-id spellings
and the empty list, while the success bodies, the draw-down, cancel, reverse
and the wrong-state refusals are spec-derived and unit-tested. The
unrecordability is declared in `allowed_differences.py`'s structural section;
the ledger effects themselves live in `billing/ledger.py`.

`balance` is a computed read with no table; `destination` is the
scope-boundary stub (data_model §3.2) — a `ba_` id minted at creation, never
resolved, so the payout-failure magic bank values have no destination to ride
and `fail_payout` is the fixture/test surface instead.
"""

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

import seahaven

from seahaven_stripe_world import _json
from seahaven_stripe_world.billing import ledger
from seahaven_stripe_world.dispatch.params import Param, ParamSpec
from seahaven_stripe_world.dispatch.resource import ListFilter, ResourceSpec, register
from seahaven_stripe_world.serialize.fields import FieldMap, presence_sets, serializer_for
from seahaven_stripe_world.stripe_errors import invalid_request

if TYPE_CHECKING:
    from seahaven_stripe_world.dispatch.response import Request

__all__ = [
    "FIELDS",
    "SPEC",
    "cancel",
    "create",
    "reverse",
    "serialize",
]

# --- the ParamSpecs -----------------------------------------------------------------

PAYOUT_CREATE = ParamSpec(
    op_id="PostPayouts",
    body=(
        # No `minimum` on the Param: the live refusal is the endpoint's own
        # `This value must be greater than or equal to 1.` spelling (probed),
        # not params.py's generic bounds message, so `create` raises it.
        Param(name="amount", kind="integer", required=True),
        Param(name="currency", kind="currency", required=True),
        Param(name="description", kind="string", max_length=5_000),
        Param(name="destination", kind="string", max_length=5_000),
        Param(name="method", kind="literal", choices=("standard", "instant")),
        # The deprecated twin of `method` (the spec keeps both): accepted and
        # ignored — `method` wins, and neither moves money differently here
        # because instant payouts draw `instant_available`, a bucket this
        # world never fills (declared in the allow-list's structural section).
        Param(name="payout_method", kind="literal", choices=("standard", "instant")),
        Param(name="source_type", kind="literal", choices=("card", "fpx", "bank_account")),
        Param(name="statement_descriptor", kind="string", max_length=22),
    ),
    metadata=True,
)

PAYOUT_LIST = ParamSpec(
    op_id="GetPayouts",
    paginated=True,
)

PAYOUT_RETRIEVE = ParamSpec(
    op_id="GetPayoutsPayout",
    path=("payout",),
)

PAYOUT_UPDATE = ParamSpec(
    op_id="PostPayoutsPayout",
    path=("payout",),
    metadata=True,
)

PAYOUT_CANCEL = ParamSpec(
    op_id="PostPayoutsPayoutCancel",
    path=("payout",),
)

PAYOUT_REVERSE = ParamSpec(
    op_id="PostPayoutsPayoutReverse",
    path=("payout",),
    metadata=True,
)

# --- the serializer -----------------------------------------------------------------

always_present, omit_when_none = presence_sets("payout")

FIELDS = FieldMap(
    object="payout",
    table="payouts",
    columns={
        "id": "id",
        "created": "created",
        "amount": "amount",
        "arrival_date": "arrival_date",
        "automatic": "automatic",
        "balance_transaction": "balance_transaction",
        "currency": "currency",
        "description": "description",
        "destination": "destination",
        "failure_balance_transaction": "failure_balance_transaction",
        "failure_code": "failure_code",
        "failure_message": "failure_message",
        "metadata": "metadata",
        "method": "method",
        "original_payout": "original_payout",
        "reconciliation_status": "reconciliation_status",
        "reversed_by": "reversed_by",
        "source_type": "source_type",
        "statement_descriptor": "statement_descriptor",
        "status": "status",
        "type": "type",
    },
    timestamps=frozenset({"created", "arrival_date"}),
    json_columns=frozenset({"metadata"}),
    booleans=frozenset({"automatic"}),
    constants={
        # Connect-shaped and out of scope (scope-boundary-edges.md): present
        # as the nulls the spec's own nullable flags allow.
        "application_fee": None,
        "application_fee_amount": None,
        "payout_method": None,
        "trace_id": None,
    },
    always_present=always_present,
    omit_when_none=omit_when_none,
)

_serialize = serializer_for(FIELDS)


def serialize(ctx: seahaven.Ctx, row: Mapping[str, Any]) -> dict[str, Any]:
    return _serialize(ctx, row)


# --- the hand-written actions -------------------------------------------------------


def create(ctx: seahaven.Ctx, req: Request) -> dict[str, Any]:
    """`POST /v1/payouts`: the amount floor refusal (recorded verbatim), then
    the ledger's create — the draw-down check, the bt, the sweep and the
    `payout.created` event all live there."""
    amount = req.params["amount"]
    if amount <= 0:
        raise invalid_request(
            "This value must be greater than or equal to 1.",
            code="parameter_invalid_integer",
            param="amount",
            pre_execution=True,
        )
    row = ledger.create_payout(
        ctx,
        amount=amount,
        currency=req.params["currency"],
        method=req.params.get("method", "standard"),
        destination=req.params.get("destination"),
        description=req.params.get("description"),
        statement_descriptor=req.params.get("statement_descriptor"),
        metadata_text=_json.dumps(dict(req.metadata.apply({})))
        if req.metadata is not None
        else "{}",
    )
    return serialize(ctx, row)


def cancel(ctx: seahaven.Ctx, req: Request) -> dict[str, Any]:
    """`POST /v1/payouts/{payout}/cancel`: pending only; the ledger owns the
    reversal row, the sweep clear and the event."""
    return serialize(ctx, ledger.cancel_payout(ctx, req.path_params["payout"]))


def reverse(ctx: seahaven.Ctx, req: Request) -> dict[str, Any]:
    """`POST /v1/payouts/{payout}/reverse`: paid only; the ledger writes the
    reversing payout, cross-links both rows and fires the documented event
    order."""
    return serialize(ctx, ledger.reverse_payout(ctx, req.path_params["payout"]))


# --- the engine-served rest ----------------------------------------------------------

SPEC = register(
    ResourceSpec(
        object="payout",
        table="payouts",
        id_prefix="po_",
        collection_url="/v1/payouts",
        serializer=serialize,
        columns=tuple(FIELDS.columns),
        # Probed (Phase 11): every payout path names `payout` and says
        # "No such payout", the placeholder's own spelling.
        list_filters=(
            ListFilter(name="arrival_date", column="arrival_date", kind="range"),
            ListFilter(name="created", column="created", kind="range"),
            ListFilter(name="destination", column="destination", kind="exact"),
            # A plain string, not a literal filter: a bogus status answers an
            # empty page at 200 (probed).
            ListFilter(name="status", column="status", kind="exact"),
        ),
        updatable=PAYOUT_UPDATE,
        delete=None,
        metadata=True,
        updated_event="payout.updated",
    )
)
