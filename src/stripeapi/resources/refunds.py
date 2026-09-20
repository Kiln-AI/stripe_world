"""The refunds half of Phase 9: partial refunds, the running
`amount_refunded` / `refunded` bookkeeping on the charge, over-refund
rejection, the refund gate a dispute holds closed, and the legacy
charge-scoped create aliases.

Every wire shape and refusal here is pinned by live probe at
`2026-08-26.dahlia` (Phase 9, 2026-09-20): the refund body's derived
reference set (`charge` and `payment_intent` cross-fill; `customer`,
`payment_method`, `currency` come off the charge), `destination_details`'
pending acquirer-reference shape on card charges, the two distinct
over-refund refusals (the dollar-forming `param: "amount"` refusal while
partially refunded, `charge_already_refunded` once whole), the one-of
message naming `payment_intent or charge`, the unknown-charge 404 under
`param: "id"`, and `POST /v1/charges/{charge}/refund` — the singular legacy
alias — answering with the **charge**, where the plural scoped create
answers with the refund.

`balance_transaction` is the refund's ledger row, written at creation on the
synchronous path (Phase 11): `type: refund`, negative amount, fee 0 — all
recorded (probed; the row also resolves the research's legacy/`payment_refund`
pair question at this version). A pending refund — only the async-success
card writes those — reserves nothing on a frozen clock, so it carries no row
until it settles, which it never does here (declared, Phase 9's settlement
entry).
"""

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

import seahaven

from stripeapi import _ids, _json, _seq
from stripeapi.billing import ledger
from stripeapi.dispatch.params import Param, ParamSpec
from stripeapi.dispatch.resource import (
    ListFilter,
    ResourceSpec,
    Scope,
    register,
)
from stripeapi.resources import _lookup, charges, events
from stripeapi.serialize.expand import InlineList, register_inline_list
from stripeapi.serialize.fields import FieldMap, presence_sets, serializer_for
from stripeapi.stripe_errors import invalid_request

if TYPE_CHECKING:
    from stripeapi.dispatch.response import Request

__all__ = [
    "FIELDS",
    "SCOPE",
    "SPEC",
    "cancel",
    "create",
    "create_from_charge_singular",
    "create_scoped",
    "retrieve_scoped",
    "update_scoped",
]

CH = ("ch_",)
PI = ("pi_",)

#: The create parameter's own enum (spec): the system-generated
#: `expired_uncaptured_charge` is not caller-settable, and sending it is
#: refused with the recorded message in `create`.
CREATE_REASONS = ("duplicate", "fraudulent", "requested_by_customer")

#: The recorded acquirer-reference shape a card refund's
#: `destination_details` carries at creation (probed, Phase 9): the network
#: reference is pending, and its later availability flip is async settlement
#: a frozen clock cannot model — declared in the allow-list.
_DESTINATION_DETAILS_CARD = {
    "card": {
        "reference_status": "pending",
        "reference_type": "acquirer_reference_number",
        "type": "refund",
    },
    "type": "card",
}

# --- the ParamSpecs -----------------------------------------------------------------

_AMOUNT = Param(name="amount", kind="integer")
_INSTRUCTIONS_EMAIL = Param(name="instructions_email", kind="string", max_length=5_000)
_REASON = Param(name="reason", kind="string", max_length=5_000)

REFUND_CREATE = ParamSpec(
    op_id="PostRefunds",
    body=(
        _AMOUNT,
        Param(name="charge", kind="id", id_prefixes=CH),
        _INSTRUCTIONS_EMAIL,
        Param(name="payment_intent", kind="id", id_prefixes=PI),
        _REASON,
    ),
    metadata=True,
    # `required_one_of=(("charge", "payment_intent"),)` is deliberately NOT
    # declared: the live message is endpoint-specific ("…payment_intent or
    # charge.", probed) and params.py's generic one-of text would diverge;
    # `create` raises the recorded form pre-execution.
)

REFUND_RETRIEVE = ParamSpec(
    op_id="GetRefundsRefund",
    path=("refund",),
)

REFUND_UPDATE = ParamSpec(
    op_id="PostRefundsRefund",
    path=("refund",),
    metadata=True,
)

REFUND_CANCEL = ParamSpec(
    op_id="PostRefundsRefundCancel",
    path=("refund",),
)

REFUND_LIST = ParamSpec(
    op_id="GetRefunds",
    paginated=True,
)

CHARGE_REFUNDS_LIST = ParamSpec(
    op_id="GetChargesChargeRefunds",
    path=("charge",),
    paginated=True,
)

CHARGE_REFUND_RETRIEVE = ParamSpec(
    op_id="GetChargesChargeRefundsRefund",
    path=("charge", "refund"),
)

CHARGE_REFUND_UPDATE = ParamSpec(
    op_id="PostChargesChargeRefundsRefund",
    path=("charge", "refund"),
    metadata=True,
)

#: The two legacy creates under the charge path. The singular
#: `POST /v1/charges/{charge}/refund` takes the same body the plural does
#: (probed shapes); the response differs — see the module docstring.
CHARGE_REFUND_CREATE = ParamSpec(
    op_id="PostChargesChargeRefunds",
    path=("charge",),
    body=(_AMOUNT, _INSTRUCTIONS_EMAIL, _REASON),
    metadata=True,
)

CHARGE_REFUND_CREATE_SINGULAR = ParamSpec(
    op_id="PostChargesChargeRefund",
    path=("charge",),
    body=(_AMOUNT, _INSTRUCTIONS_EMAIL, _REASON),
    metadata=True,
)

# --- the serializer -----------------------------------------------------------------

always_present, omit_when_none = presence_sets("refund")

FIELDS = FieldMap(
    object="refund",
    table="refunds",
    columns={
        "id": "id",
        "created": "created",
        "amount": "amount",
        "balance_transaction": "balance_transaction",
        "charge": "charge",
        "currency": "currency",
        "customer": "customer",
        "description": "description",
        "destination_details": "destination_details",
        "failure_balance_transaction": "failure_balance_transaction",
        "failure_reason": "failure_reason",
        "instructions_email": "instructions_email",
        "metadata": "metadata",
        "payment_intent": "payment_intent",
        "payment_method": "payment_method",
        "pending_reason": "pending_reason",
        "reason": "reason",
        "receipt_number": "receipt_number",
        "status": "status",
    },
    timestamps=frozenset({"created"}),
    json_columns=frozenset({"destination_details", "metadata"}),
    constants={
        # `refund` is one of the four objects with no `livemode` field at the
        # pinned version (data_model §8) — emitting it would fail schema
        # conformance.
        "customer_account": None,
        "next_action": None,
        "presentment_details": None,
        "source_transfer_reversal": None,
        "transfer_reversal": None,
    },
    # Recorded (Phase 9, cassette 05): `description`, `failure_reason`,
    # `instructions_email`, `next_action` and `presentment_details` are
    # absent while valueless — the spec's own nullable flags, no overrides.
    always_present=always_present,
    omit_when_none=omit_when_none,
)

_serialize = serializer_for(FIELDS)


def serialize(ctx: seahaven.Ctx, row: Mapping[str, Any]) -> dict[str, Any]:
    return _serialize(ctx, row)


register_inline_list(
    "charge",
    "refunds",
    InlineList(
        child_object="refund", scope_column="charge", url_template="/v1/charges/{id}/refunds"
    ),
)

# --- the create core ----------------------------------------------------------------

_CURRENCY_SYMBOLS = {
    "usd": "$",
    "eur": "€",
    "gbp": "£",
    "cad": "$",
    "aud": "$",
    "nzd": "$",
    "hkd": "$",
    "sgd": "$",
}


def _money(currency: str, amount: int) -> str:
    """`$40.00` — the recorded shape of the over-refund message's amounts
    (probed on usd and eur). Integer formatting: no float exists in the
    money path, message-building included. The symbol table covers the
    majors this world's charges carry; anything else falls back to the ISO
    code in the symbol's place, a declared simplification no cassette
    exercises."""
    symbol = _CURRENCY_SYMBOLS.get(currency.lower(), currency.upper())
    return f"{symbol}{amount // 100}.{amount % 100:02d}"


def _target_charge(ctx: seahaven.Ctx, params: Mapping[str, Any]) -> dict[str, Any]:
    """The charge a refund names, directly or through its intent."""
    if "charge" in params:
        # Recorded (Phase 9): the create's charge parameter is refused with
        # `param: "id"` — the path-parameter spelling, oddly, on a body
        # parameter. Verbatim, like the rest of the family.
        return _lookup.require_row(
            ctx, "charges", "charge", params["charge"], param="id", status=404
        )
    pi_row = _lookup.require_row(
        ctx, "payment_intents", "payment_intent", params["payment_intent"], param="payment_intent"
    )
    charge = (
        None
        if pi_row["latest_charge"] is None
        else ctx.db.one("SELECT * FROM charges WHERE id = ?", pi_row["latest_charge"])
    )
    if charge is None or charge["status"] == "failed":
        raise invalid_request(
            f"This PaymentIntent ({pi_row['id']}) does not have a successful charge to refund."
        )
    return charge


def _guard_refundable(ctx: seahaven.Ctx, charge: Mapping[str, Any]) -> None:
    """The three ways a charge refuses a refund before any amount math runs
    (each recorded verbatim, Phase 9): the failed charge, the uncaptured
    hold — in that order, because a failed charge is also uncaptured and
    the recorded answers differ — and the open or lost dispute."""
    if charge["status"] == "failed":
        raise invalid_request(
            f"This PaymentIntent ({charge['payment_intent']}) does not have a successful charge "
            "to refund."
        )
    if charge["captured"] == 0:
        raise invalid_request(
            f"This uncaptured Charge was created by a PaymentIntent ({charge['payment_intent']}). "
            "You must cancel the PaymentIntent to reverse the authorization instead of refunding "
            "the Charge directly. For more information, see "
            "https://stripe.com/docs/payments/place-a-hold-on-a-payment-method"
        )
    dispute = ctx.db.one("SELECT is_charge_refundable FROM disputes WHERE charge = ?", charge["id"])
    if dispute is not None and dispute["is_charge_refundable"] == 0:
        raise invalid_request(
            f"Charge {charge['id']} has been charged back; cannot issue a refund.",
            code="charge_disputed",
        )


def create_refund(
    ctx: seahaven.Ctx,
    *,
    params: Mapping[str, Any],
    metadata_text: str,
    charge_id: str,
) -> dict[str, Any]:
    """Write one refund row against `charge_id`, update the charge's
    bookkeeping, emit the recorded event pair, and answer with the refund.

    The one place a `re_` row is born; both create surfaces — the canonical
    `POST /v1/refunds` and the charge-scoped aliases — come through here.
    """
    charge = _lookup.require_row(ctx, "charges", "charge", charge_id, param="charge")
    _guard_refundable(ctx, charge)
    reason = params.get("reason")
    if reason is not None and reason not in CREATE_REASONS:
        raise invalid_request(
            "Invalid reason: must be one of duplicate, fraudulent, or requested_by_customer",
            param="reason",
        )
    remaining = charge["amount_captured"] - charge["amount_refunded"]
    if remaining <= 0:
        raise invalid_request(
            f"Charge {charge['id']} has already been refunded.",
            code="charge_already_refunded",
        )
    amount = params.get("amount")
    if amount is not None and amount <= 0:
        raise invalid_request(
            "This value must be greater than or equal to 1.",
            code="parameter_invalid_integer",
            param="amount",
        )
    if amount is None:
        amount = remaining
    if amount > remaining:
        raise invalid_request(
            f"Refund amount ({_money(charge['currency'], amount)}) is greater than unrefunded "
            f"amount on charge ({_money(charge['currency'], remaining)})",
            param="amount",
        )
    id_ = _ids.stripe_id(ctx, "re_")
    # The recorded acquirer-reference shape rides card charges; the rails
    # this world stubs have no recorded destination detail, and the field is
    # neither required nor nullable — so it is simply absent for them.
    loaded: object = _json.loads(charge["payment_method_details"] or "{}")
    details: dict = loaded if isinstance(loaded, dict) else {}
    destination = _json.dumps(_DESTINATION_DETAILS_CARD) if details.get("type") == "card" else None
    # The async-success test card (…7726) begins `pending` and, on a frozen
    # clock, never settles; every other card refunds synchronously. The
    # failure card (…5126) begins `succeeded` and its async `failed` flip
    # never fires — both declared differences.
    pm_tag_row = ctx.db.one(
        "SELECT payment_methods.x_behavior AS tag FROM payment_methods WHERE id = ?",
        charge["payment_method"],
    )
    is_async_success = pm_tag_row is not None and "refund_async=succeeded" in (
        pm_tag_row["tag"] or ""
    )
    ctx.db.execute(
        "INSERT INTO refunds (id, x_seq, created, amount, charge, currency, customer,"
        " destination_details, failure_balance_transaction, failure_reason, instructions_email,"
        " metadata, payment_intent, payment_method, reason, receipt_number, status)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?, ?, NULL, ?)",
        id_,
        _seq.next_seq(ctx, "refunds"),
        ctx.clock.iso(),
        amount,
        charge["id"],
        charge["currency"],
        charge["customer"],
        destination,
        params.get("instructions_email"),
        metadata_text,
        charge["payment_intent"],
        charge["payment_method"],
        reason,
        "pending" if is_async_success else "succeeded",
    )
    if not is_async_success:
        # Bookkeeping counts settled refunds only (invariant I7): a pending
        # refund reserves nothing on a frozen clock because it can never
        # settle, and `refunded` flips exactly when the sums reach the
        # captured amount.
        refunded = charge["amount_refunded"] + amount
        ctx.db.execute(
            "UPDATE charges SET amount_refunded = ?, refunded = ? WHERE id = ?",
            refunded,
            int(refunded == charge["amount_captured"]),
            charge["id"],
        )
        # The settled refund moves money back out: its ledger row (recorded,
        # Phase 11 — `REFUND FOR CHARGE (<charge description>)`, the parens
        # only when the charge is described; the undescribed corner is this
        # world's ruling, declared).
        bt = ledger.record(
            ctx,
            type_="refund",
            amount=-amount,
            fee=0,
            currency=charge["currency"],
            source_id=id_,
            description=(
                f"REFUND FOR CHARGE ({charge['description']})"
                if charge["description"]
                else "REFUND FOR CHARGE"
            ),
            reporting_category="refund",
        )
        ctx.db.execute("UPDATE refunds SET balance_transaction = ? WHERE id = ?", bt["id"], id_)
    row = _lookup.require_row(ctx, "refunds", "refund", id_, param="refund")
    events.emit_event(ctx, type="refund.created", obj=serialize(ctx, row))
    if not is_async_success:
        events.emit_event(
            ctx,
            type="charge.refunded",
            obj=charges.serialize(
                ctx, _lookup.require_row(ctx, "charges", "charge", charge["id"], param="charge")
            ),
        )
    return serialize(ctx, row)


# --- the handlers --------------------------------------------------------------------


def create(ctx: seahaven.Ctx, req: Request) -> dict[str, Any]:
    """`POST /v1/refunds`: resolve the target (charge or intent), then the
    shared create core."""
    if "charge" not in req.params and "payment_intent" not in req.params:
        raise invalid_request(
            "One of the following params should be provided for this request: "
            "payment_intent or charge.",
            pre_execution=True,
        )
    charge = _target_charge(ctx, req.params)
    return create_refund(
        ctx,
        params=req.params,
        metadata_text=_json.dumps(dict(req.metadata.apply({})))
        if req.metadata is not None
        else "{}",
        charge_id=charge["id"],
    )


def create_scoped(ctx: seahaven.Ctx, req: Request) -> dict[str, Any]:
    """`POST /v1/charges/{charge}/refunds`: the plural scoped create — the
    parent is looked up first so a bad `{charge}` is the scoped 404."""
    _lookup.require_row(ctx, "charges", "charge", req.path_params["charge"], param="charge")
    return create_refund(
        ctx,
        params=req.params,
        metadata_text=_json.dumps(dict(req.metadata.apply({})))
        if req.metadata is not None
        else "{}",
        charge_id=req.path_params["charge"],
    )


def create_from_charge_singular(ctx: seahaven.Ctx, req: Request) -> dict[str, Any]:
    """`POST /v1/charges/{charge}/refund`: the legacy singular alias. The
    refund is written by the same core, but the recorded response is the
    **charge** with its updated bookkeeping — which is why this alias owns a
    handler rather than literally sharing `PostRefunds`'s (dispatcher
    §3.1.3's reading, corrected by the Phase 9 recording)."""
    _lookup.require_row(ctx, "charges", "charge", req.path_params["charge"], param="charge")
    create_refund(
        ctx,
        params=req.params,
        metadata_text=_json.dumps(dict(req.metadata.apply({})))
        if req.metadata is not None
        else "{}",
        charge_id=req.path_params["charge"],
    )
    return charges.serialize(
        ctx,
        _lookup.require_row(ctx, "charges", "charge", req.path_params["charge"], param="charge"),
    )


def cancel(ctx: seahaven.Ctx, req: Request) -> dict[str, Any]:
    """`POST /v1/refunds/{refund}/cancel`. A succeeded refund answers the
    recorded refusal; a pending one — only the async-success card writes
    those — cancels inside the 30-minute test-mode window, which a frozen
    clock never closes. The refund.updated + charge.refund.updated pair is
    this world's reading of a state change every refund mutation emits; no
    recording exercises a successful cancel, and the STRUCTURAL_DIFFERENCES
    settlement entry's "neither event exists here" refers to network
    settlement, not this path."""
    row = _lookup.require_row(ctx, "refunds", "refund", req.path_params["refund"], param="refund")
    if row["status"] != "pending":
        raise invalid_request("Canceling this refund is unsupported.")
    ctx.db.execute("UPDATE refunds SET status = 'canceled' WHERE id = ?", row["id"])
    fresh = _lookup.require_row(ctx, "refunds", "refund", row["id"], param="refund")
    body = serialize(ctx, fresh)
    events.emit_event(ctx, type="refund.updated", obj=body)
    events.emit_event(ctx, type="charge.refund.updated", obj=body)
    return body


def _scoped_refund(ctx: seahaven.Ctx, req: Request) -> dict[str, Any]:
    """The refund a charge-scoped path names, or the scoped 404 — recorded
    (Phase 9): a refund of another charge answers `No such refund: 're_…'`
    exactly like a missing one, not the generic scoped-mismatch shape the
    payment-methods paths carry."""
    charge_id = req.path_params["charge"]
    _lookup.require_row(ctx, "charges", "charge", charge_id, param="charge")
    row = ctx.db.one(
        "SELECT * FROM refunds WHERE id = ? AND charge = ?", req.path_params["refund"], charge_id
    )
    if row is None:
        raise invalid_request(
            f"No such refund: '{req.path_params['refund']}'",
            code="resource_missing",
            status=404,
            param="refund",
        )
    return row


def retrieve_scoped(ctx: seahaven.Ctx, req: Request) -> dict[str, Any]:
    """`GET /v1/charges/{charge}/refunds/{refund}`."""
    return serialize(ctx, _scoped_refund(ctx, req))


def update_scoped(ctx: seahaven.Ctx, req: Request) -> dict[str, Any]:
    """`POST /v1/charges/{charge}/refunds/{refund}`: the scoped metadata
    update — the engine's own two-line merge, inlined because the route is
    hand-written (the scope's mismatch shape differs from the engine's
    generic one) and therefore carries no ResourceSpec."""
    row = _scoped_refund(ctx, req)
    metadata_text = row["metadata"]
    if req.metadata is not None:
        metadata_text = _json.dumps(dict(req.metadata.apply(_json.loads(metadata_text) or {})))
        ctx.db.execute("UPDATE refunds SET metadata = ? WHERE id = ?", metadata_text, row["id"])
    return serialize(ctx, _scoped_refund(ctx, req))


# --- the engine-served rest ----------------------------------------------------------

SPEC = register(
    ResourceSpec(
        object="refund",
        table="refunds",
        id_prefix="re_",
        collection_url="/v1/refunds",
        serializer=serialize,
        columns=tuple(FIELDS.columns),
        list_filters=(
            ListFilter(
                name="charge",
                column="charge",
                kind="exact",
                id_prefixes=CH,
                references="charge",
            ),
            ListFilter(
                name="payment_intent",
                column="payment_intent",
                kind="exact",
                id_prefixes=PI,
                references="payment_intent",
            ),
            ListFilter(name="created", column="created", kind="range"),
        ),
        updatable=REFUND_UPDATE,
        delete=None,
        metadata=True,
        updated_event="refund.updated",
    )
)

#: The charge-scoped refund routes' `Scope` — declared here so `routes.py`
#: reads one line per route (dispatcher §3.1.3's pattern).
SCOPE = Scope(
    path_param="charge",
    column="charge",
    parent=charges.SPEC,
)
