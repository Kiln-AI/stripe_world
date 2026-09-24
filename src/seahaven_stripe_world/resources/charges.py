"""The charges half of the money path: the charge rows the PaymentIntent
transitions write, the list/retrieve/update reads, and the legacy
direct-charge surface's recorded refusals.

Every wire shape here is pinned by live probe at `2026-08-26.dahlia` (Phase 8,
2026-09-20): the charge body's constants (`calculated_statement_descriptor:
"Stripe"`, `fraud_details: {}`, `radar_options` absent, `refunds` absent
unexpanded — expand-only at this version, Phase 9's to serve), the card rail
under `payment_method_details` including the deterministic status objects and
manual capture's `capture_before = created + 7 days`, the outcome object for
approval and decline, and the four ways `POST /v1/charges` and
`POST /v1/charges/{charge}/capture` refuse on the modern API — the legacy
charge path is end-of-life for cards, so this resource's create and capture
are refusals, recorded verbatim.

The charge *attempt* — deciding success/decline/3DS from a payment method's
magic tag and writing the row — lives in `resources/payment_intents.py`, which
drives the PaymentIntent state machine around it; this module owns the row
shape both of them write.
"""

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

import seahaven

from seahaven_stripe_world import _ids, _json, _seq, _time
from seahaven_stripe_world.billing import ledger
from seahaven_stripe_world.billing._money import stripe_fee
from seahaven_stripe_world.dispatch.params import Param, ParamSpec
from seahaven_stripe_world.dispatch.resource import (
    ListFilter,
    ResourceSpec,
    register,
)
from seahaven_stripe_world.resources import _lookup
from seahaven_stripe_world.resources.customers import _canonical_shipping, _merge_canonical
from seahaven_stripe_world.serialize.fields import OMIT, FieldMap, presence_sets, serializer_for
from seahaven_stripe_world.stripe_errors import card_error, invalid_request

if TYPE_CHECKING:
    from seahaven_stripe_world.dispatch.response import Request

__all__ = [
    "FIELDS",
    "SPEC",
    "capture",
    "create",
    "insert_charge",
    "record_capture_ledger",
    "serialize",
]

CUS = ("cus_",)
PM = ("pm_",)
PI = ("pi_",)

# --- the ParamSpecs ---------------------------------------------------------------

CHARGE_LIST = ParamSpec(
    op_id="GetCharges",
    paginated=True,
)

CHARGE_RETRIEVE = ParamSpec(
    op_id="GetChargesCharge",
    path=("charge",),
)

_ADDRESS_BODY = (
    Param(name="city", kind="string", max_length=5_000),
    Param(name="country", kind="string", max_length=5_000),
    Param(name="line1", kind="string", max_length=5_000),
    Param(name="line2", kind="string", max_length=5_000),
    Param(name="postal_code", kind="string", max_length=5_000),
    Param(name="state", kind="string", max_length=5_000),
)

CHARGE_UPDATE = ParamSpec(
    op_id="PostChargesCharge",
    path=("charge",),
    body=(
        Param(name="customer", kind="id", id_prefixes=CUS),
        Param(name="description", kind="string", max_length=5_000, unset_with_empty_string=True),
        Param(
            name="fraud_details",
            kind="object",
            shape=(Param(name="user_report", kind="literal", choices=("fraudulent", "safe")),),
        ),
        Param(name="receipt_email", kind="string", max_length=5_000),
        Param(
            name="shipping",
            kind="object",
            shape=(
                Param(name="address", kind="object", shape=_ADDRESS_BODY),
                Param(name="carrier", kind="string", max_length=5_000),
                Param(name="name", kind="string", max_length=5_000),
                Param(name="phone", kind="string", max_length=5_000),
                Param(name="tracking_number", kind="string", max_length=5_000),
            ),
        ),
        # `transfer_group` is deliberately absent: data_model §7 rules the
        # field a constant null, so there is no column to write, and
        # accepting the parameter crashed as INTERNAL (CR round 1). Recorded
        # (cassette 04, CR round 2): real Stripe refuses the update too, on
        # any PI-created charge — "This Charge was created by a PaymentIntent
        # (pi_…). You must update the `transfer_group` on the PaymentIntent
        # instead of updating the Charge directly." — while this world, which
        # has cut the parameter, answers `parameter_unknown`; the difference
        # is an allow-list entry, the declared scope cut.
    ),
    metadata=True,
)

#: Accepted so the recorded refusals — not `parameter_unknown` — are what a
#: caller sending the legacy parameters sees (the same ruling as `customer` on
#: payment-method create, Phase 6).
CHARGE_CREATE = ParamSpec(
    op_id="PostCharges",
    body=(
        Param(name="amount", kind="integer"),
        Param(name="currency", kind="string", max_length=3),
        Param(name="customer", kind="id", id_prefixes=CUS),
        Param(name="description", kind="string", max_length=5_000),
        Param(name="payment_method", kind="id", id_prefixes=PM),
        Param(name="source", kind="string", max_length=5_000),
        Param(name="card", kind="object", shape=()),
        Param(name="receipt_email", kind="string", max_length=5_000),
        Param(name="statement_descriptor", kind="string", max_length=22),
        Param(name="statement_descriptor_suffix", kind="string", max_length=22),
    ),
    metadata=True,
)

CHARGE_CAPTURE = ParamSpec(
    op_id="PostChargesChargeCapture",
    path=("charge",),
    body=(
        Param(name="amount", kind="integer"),
        Param(name="receipt_email", kind="string", max_length=5_000),
        Param(name="statement_descriptor", kind="string", max_length=22),
        Param(name="statement_descriptor_suffix", kind="string", max_length=22),
    ),
)

# --- the serializer ----------------------------------------------------------------

always_present, omit_when_none = presence_sets("charge")

FIELDS = FieldMap(
    object="charge",
    table="charges",
    columns={
        "id": "id",
        "created": "created",
        "amount": "amount",
        "amount_captured": "amount_captured",
        "amount_refunded": "amount_refunded",
        "balance_transaction": "balance_transaction",
        "billing_details": "billing_details",
        "calculated_statement_descriptor": "calculated_statement_descriptor",
        "captured": "captured",
        "currency": "currency",
        "customer": "customer",
        "description": "description",
        "disputed": "disputed",
        "failure_balance_transaction": "failure_balance_transaction",
        "failure_code": "failure_code",
        "failure_message": "failure_message",
        "fraud_details": "fraud_details",
        "metadata": "metadata",
        "outcome": "outcome",
        "paid": "paid",
        "payment_intent": "payment_intent",
        "payment_method": "payment_method",
        "payment_method_details": "payment_method_details",
        "receipt_email": "receipt_email",
        "receipt_number": "receipt_number",
        "receipt_url": "receipt_url",
        "refunded": "refunded",
        "shipping": "shipping",
        "statement_descriptor": "statement_descriptor",
        "statement_descriptor_suffix": "statement_descriptor_suffix",
        "status": "status",
    },
    timestamps=frozenset({"created"}),
    json_columns=frozenset(
        {
            "billing_details",
            "fraud_details",
            "metadata",
            "outcome",
            "payment_method_details",
            "shipping",
        }
    ),
    booleans=frozenset({"captured", "disputed", "paid", "refunded"}),
    constants={
        "application": None,
        "application_fee": None,
        "application_fee_amount": None,
        "on_behalf_of": None,
        "review": None,
        "source_transfer": None,
        "transfer_data": None,
        "transfer_group": None,
        # Phase 9 serialization sweep: fields present in the pruned spec
        # but never serialized until now.
        "presentment_details": None,
        # Spec types radar_options as {"type":"null"} — the live API emits {}
        # but the spec-legal value is null, which is what we emit.
        "radar_options": None,
        # Expand-only inline list (absent unexpanded, full envelope under
        # expand[]=refunds).
        "refunds": OMIT,
        # Connect reference, null on non-Connect accounts.
        "transfer": None,
    },
    always_present=always_present,
    omit_when_none=omit_when_none,
)

_serialize = serializer_for(FIELDS)


def serialize(ctx: seahaven.Ctx, row: Mapping[str, Any]) -> dict[str, Any]:
    return _serialize(ctx, row)


#: The authorization window a manual capture holds the funds for, in seconds:
#: recorded as `capture_before = created + 604800` (7 days, Phase 8 probe).
AUTHORIZATION_WINDOW = 7 * 24 * 60 * 60

_OUTCOME_APPROVED: dict[str, object] = {
    "advice_code": None,
    "network_advice_code": None,
    "network_decline_code": None,
    "network_status": "approved_by_network",
    "reason": None,
    "risk_level": "normal",
    "seller_message": "Payment complete.",
    "type": "authorized",
}


def outcome_declined(decline_code: str) -> dict[str, object]:
    """The recorded shape of a declined charge's outcome: `reason` carries the
    decline code and the seller message is the bank-silent one (probed on
    `generic_decline`, Phase 8)."""
    return {
        "advice_code": None,
        "network_advice_code": None,
        "network_decline_code": None,
        "network_status": "declined_by_network",
        "reason": decline_code,
        "risk_level": "normal",
        "seller_message": "The bank did not return any further details with this decline.",
        "type": "issuer_declined",
    }


def payment_method_details(
    pm_row: Mapping[str, Any],
    *,
    amount: int,
    authorized: int | None,
    manual: bool,
    created: str,
) -> dict[str, object]:
    """The whole discriminated `payment_method_details` object for charging
    `pm_row`: the full card shape for cards, the stub `{"type": t, t: {}}` for
    every other rail (data_model §3.9's table, second column).

    The card shape matches the recording field for field except the two
    network randoms (`authorization_code`, `network_transaction_id`), null
    here — the nullability is the spec's own, and the difference from the
    recorded values is an allow-list entry with the reason."""
    if pm_row["type"] != "card":
        return {"type": pm_row["type"], pm_row["type"]: {}}
    loaded: object = _json.loads(pm_row["rail"])
    rail: dict = loaded if isinstance(loaded, dict) else {}
    # Recorded (Phase 8): the charge-time rail reports the CVC as checked —
    # `pass` even when the payment method's own rail still says `unchecked`
    # (an unattached decline card) — and a FAILED attempt authorizes nothing
    # (`amount_authorized` null, no `capture_before`).
    checks = dict(rail.get("checks") or {})
    checks["cvc_check"] = "pass"
    details: dict[str, object] = {
        "amount_authorized": authorized,
        "authorization_code": None,
        "brand": rail["brand"],
        "checks": checks,
        "country": rail["country"],
        "exp_month": rail["exp_month"],
        "exp_year": rail["exp_year"],
        "extended_authorization": {"status": "disabled"},
        "fingerprint": rail["fingerprint"],
        "funding": rail["funding"],
        "incremental_authorization": {"status": "unavailable"},
        "installments": None,
        "last4": rail["last4"],
        "mandate": None,
        "multicapture": {"status": "unavailable"},
        "network": rail["brand"],
        "network_token": {"used": False},
        "network_transaction_id": None,
        # Recorded (Phase 8): the overcapture ceiling names the charge amount
        # even on a failed attempt, where `amount_authorized` is null.
        "overcapture": {"maximum_amount_capturable": amount, "status": "unavailable"},
        "regulated_status": rail.get("regulated_status", "unregulated"),
        "three_d_secure": None,
        "transaction_link_id": None,
        "wallet": rail.get("wallet"),
    }
    if manual and authorized is not None:
        details["capture_before"] = _time.to_unix(created) + AUTHORIZATION_WINDOW
    return {"type": "card", "card": details}


def record_capture_ledger(
    ctx: seahaven.Ctx,
    charge_id: str,
    *,
    amount_captured: int,
    currency: str,
    description: str | None,
) -> dict[str, Any]:
    """The charge's ledger row, written once per capture: `type: charge`,
    `reporting_category: charge` (both recorded, Phase 11 — the type is the
    modern `charge`, resolving the legacy/`payment` pair question), the gross
    the CAPTURED amount, the fee the account schedule, the description the
    charge's own (recorded, cassette 11), and the single
    `Stripe processing fees` detail the recorded native-currency rows carry.

    The one place `charges.balance_transaction` is set; the confirm path (an
    immediate capture) and the capture transition both come through here, so
    the live async window — a fresh auto-captured charge answers
    `balance_transaction: null` and gains its row moments later (cassette 04
    steps 10 vs 49) — is collapsed to synchronous here, a declared flipped
    allow-list entry."""
    spec = ledger.ledger_spec(ctx)
    fee = stripe_fee(amount_captured, spec.fees)
    bt = ledger.record(
        ctx,
        type_="charge",
        amount=amount_captured,
        fee=fee,
        currency=currency,
        source_id=charge_id,
        description=description,
        fee_details=[
            {
                "amount": fee,
                "application": None,
                "currency": currency,
                "description": "Stripe processing fees",
                "type": "stripe_fee",
            }
        ],
        reporting_category="charge",
    )
    ctx.db.execute("UPDATE charges SET balance_transaction = ? WHERE id = ?", bt["id"], charge_id)
    return bt


def insert_charge(
    ctx: seahaven.Ctx,
    *,
    payment_intent: str | None,
    pm_row: Mapping[str, Any],
    amount: int,
    captured: bool,
    currency: str,
    customer: str | None,
    description: str | None,
    receipt_email: str | None,
    shipping: str | None,
    statement_descriptor: str | None,
    statement_descriptor_suffix: str | None,
    metadata: str = "{}",
    failure_code: str | None = None,
    failure_message: str | None = None,
    outcome: dict[str, object] | None = None,
) -> dict[str, Any]:
    """Write one charge row and return it re-read. The one place a `ch_` row
    is born; the PaymentIntent transitions and (later) the billing engine
    both come through here so the shape cannot fork — an invoice-born charge
    (Phase 12's `pay_invoice`) passes `payment_intent=None`, the column's
    NULL. A charge that captures
    inside this call takes its ledger row immediately
    (`record_capture_ledger`); a manual hold's row lands at the capture
    transition."""
    created = ctx.clock.iso()
    id_ = _ids.stripe_id(ctx, "ch_", timestamp=created, version_digit="3")
    failed = failure_code is not None
    cols = {
        "id": id_,
        "x_seq": _seq.next_seq(ctx, "charges"),
        "created": created,
        "amount": amount,
        "amount_captured": amount if captured else 0,
        "amount_refunded": 0,
        "billing_details": pm_row["billing_details"],
        "calculated_statement_descriptor": "Stripe",
        "captured": int(captured and not failed),
        "currency": currency,
        "customer": customer,
        "description": description,
        "disputed": 0,
        "failure_code": failure_code,
        "failure_message": failure_message,
        "fraud_details": "{}",
        "metadata": metadata,
        "outcome": _json.dumps(outcome if outcome is not None else _OUTCOME_APPROVED),
        "paid": 0 if failed else 1,
        "payment_intent": payment_intent,
        "payment_method": pm_row["id"],
        "payment_method_details": _json.dumps(
            payment_method_details(
                pm_row,
                amount=amount,
                authorized=None if failed else amount,
                manual=not captured,
                created=created,
            )
        ),
        "receipt_email": receipt_email,
        "receipt_number": None,
        "receipt_url": None if failed else f"https://pay.stripe.com/receipts/payment/{id_}",
        "refunded": 0,
        "shipping": shipping,
        "statement_descriptor": statement_descriptor,
        "statement_descriptor_suffix": statement_descriptor_suffix,
        "status": "failed" if failed else "succeeded",
    }
    columns = ", ".join(cols)
    placeholders = ", ".join("?" for _ in cols)
    ctx.db.execute(f"INSERT INTO charges ({columns}) VALUES ({placeholders})", *cols.values())
    if captured and not failed:
        record_capture_ledger(
            ctx, id_, amount_captured=amount, currency=currency, description=description
        )
    return _lookup.require_row(ctx, "charges", "charge", id_, param="charge")


# --- the legacy direct-charge surface -----------------------------------------------


def create(ctx: seahaven.Ctx, req: Request) -> dict[str, Any]:
    """`POST /v1/charges`: the legacy direct charge, refused the three ways
    the modern API refuses it (all recorded, Phase 8). Card-token and card-
    object charges answer the end-of-life message; a customer-named charge
    answers 402 `missing`, its message depending on whether the customer has
    a PaymentMethod attached; a bare amount answers `Must provide source or
    customer.`

    Nothing is written before any refusal, so raising is correct under the
    raise-loses rule: there are no rows to keep."""
    if "source" in req.params or "card" in req.params:
        raise invalid_request(
            "Card Token usage on the Customers API and Charges API has reached end of life. "
            "In order to continue processing your payments, you will need to use the Payment "
            "Intents and Payment Methods API. Read more about what you should do here: "
            "https://docs.stripe.com/payments/payment-methods/integration-options. If you "
            "need to continue to use Card Tokens on the Customers and Charges API, enable "
            "this functionality in your API policies settings: "
            "https://dashboard.stripe.com/settings/developers/api-policies",
            pre_execution=True,
        )
    if "customer" in req.params:
        _lookup.require_live_row(
            ctx, "customers", "customer", req.params["customer"], param="customer"
        )
        has_pm = (
            ctx.db.one("SELECT id FROM payment_methods WHERE customer = ?", req.params["customer"])
            is not None
        )
        if has_pm:
            raise card_error(
                "This Customer doesn't have any legacy saved payment details, but does have "
                "a Payment Method attached. Use a Payment Intent instead of creating a "
                "Charge: https://stripe.com/docs/payments/payment-intents/migration",
                code="missing",
                param="card",
            )
        raise card_error(
            "This Customer doesn't have any saved payment details. Attach a legacy Token, "
            "Card, Bank Account, or Source to this Customer and then try this request again, "
            "or use Payment Intents and Payment Methods instead.",
            code="missing",
            param="card",
        )
    # Bare `payment_method` — with no customer — answers the same recorded
    # `Must provide source or customer.` as a bare amount (probed, CR round
    # 1: 400 `parameter_missing`, verbatim); a payment method is not a source.
    raise invalid_request(
        "Must provide source or customer.",
        code="parameter_missing",
        pre_execution=True,
    )


def capture(ctx: seahaven.Ctx, req: Request) -> dict[str, Any]:
    """`POST /v1/charges/{charge}/capture`. Two recorded refusals (Phase 8):
    a PI-created uncaptured charge directs the caller to the PaymentIntent
    (the only uncaptured charges this world can mint), and an already-captured
    charge answers `charge_already_captured`. A capturable legacy charge
    cannot exist here — the legacy create above refuses — so there is no
    success branch to write."""
    id_ = req.path_params["charge"]
    row = _lookup.require_row(ctx, "charges", "charge", id_, param="id")
    if row["captured"] == 1:
        raise invalid_request(
            f"Charge {id_} has already been captured.",
            code="charge_already_captured",
        )
    if row["payment_intent"] is not None:
        raise invalid_request(
            f"This uncaptured Charge was created by a PaymentIntent ({row['payment_intent']}). "
            "You must capture the PaymentIntent instead. For more information, see "
            "https://stripe.com/docs/payments/place-a-hold-on-a-payment-method"
        )
    # Unreachable while the legacy create refuses: an uncaptured charge with
    # no PaymentIntent. Loud rather than an invented success shape.
    raise seahaven.WorldBug(
        f"charge {id_} is uncaptured with no PaymentIntent, which no path in this world writes"
    )


# --- the engine-served rest ----------------------------------------------------------


def _before_update(ctx: seahaven.Ctx, req: Request, sets: dict[str, object]) -> dict[str, object]:
    if "customer" in sets:
        # The reference lookup the engine does not do (mirrors
        # payment_intents._before_update): without it an unknown id dies on
        # the FK constraint as INTERNAL instead of the clean 400 resource_missing (CR round 2).
        _lookup.require_live_row(
            ctx, "customers", "customer", str(sets["customer"]), param="customer"
        )
    if "shipping" in sets:
        row = ctx.db.one("SELECT shipping FROM charges WHERE id = ?", req.path_params["charge"])
        if row is None:  # the engine looked this row up moments ago
            raise seahaven.WorldBug("charges.before_update: row vanished under the engine")
        sets["shipping"] = _merge_canonical(
            row["shipping"], req.params.get("shipping"), _canonical_shipping
        )
    return sets


SPEC = register(
    ResourceSpec(
        object="charge",
        table="charges",
        id_prefix="ch_",
        collection_url="/v1/charges",
        serializer=serialize,
        columns=tuple(FIELDS.columns),
        # Probed (Phase 8): the charge paths name `param: "id"` and say
        # "No such charge", like customers and unlike payment_intents, whose
        # paths keep the `intent` placeholder and say "No such payment_intent".
        missing_path_param="id",
        list_filters=(
            ListFilter(
                name="customer",
                column="customer",
                kind="exact",
                id_prefixes=CUS,
                references="customer",
            ),
            ListFilter(name="created", column="created", kind="range"),
            ListFilter(
                name="payment_intent",
                column="payment_intent",
                kind="exact",
                id_prefixes=PI,
                references="payment_intent",
            ),
        ),
        creatable=CHARGE_CREATE,
        updatable=CHARGE_UPDATE,
        delete=None,
        metadata=True,
        before_update=_before_update,
        updated_event="charge.updated",
    )
)
