"""The PaymentIntent half of the money path: the status machine
(`requires_payment_method` → `requires_confirmation` → confirm → succeeded /
requires_capture / requires_action / requires_payment_method / canceled) and
the charge attempt that drives it.

Every transition, refusal and default here is pinned by live probe at
`2026-08-26.dahlia` (Phase 8, 2026-09-20): `capture_method` defaults to
`automatic_async`; a create carrying `payment_method` lands
`requires_confirmation`; confirm resolves the parameter, the intent's own
column, then the customer's default; declines are **returned** 402s whose rows
survive (the failed charge, the intent reset to `requires_payment_method`
with `last_payment_error` set, the `charge.failed` + `payment_intent
.payment_failed` pair) per the raise-loses rule; the wrong-state capture and
cancel refusals name their allowed status lists verbatim; and the 3DS cards
split on `off_session` — on-session the intent parks at `requires_action`
with no charge row, off-session it declines `authentication_required`
(recorded, message verbatim).

The charge row itself is written through `resources/charges.py`, the one
place its shape lives.
"""

import string
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, NoReturn

import seahaven

from seahaven_stripe_world import _ids, _json, _seq
from seahaven_stripe_world.dispatch.params import Param, ParamSpec
from seahaven_stripe_world.dispatch.resource import (
    ListFilter,
    ResourceSpec,
    register,
)
from seahaven_stripe_world.dispatch.response import ApiResponse
from seahaven_stripe_world.resources import _lookup, charges, disputes, events, payment_methods
from seahaven_stripe_world.resources.customers import _canonical_shipping
from seahaven_stripe_world.serialize.fields import FieldMap, presence_sets, serializer_for
from seahaven_stripe_world.spec import spec_document
from seahaven_stripe_world.stripe_errors import declined, invalid_request

if TYPE_CHECKING:
    from seahaven_stripe_world.dispatch.response import Request

__all__ = ["FIELDS", "SPEC", "cancel", "capture", "confirm", "create"]

CUS = ("cus_",)
PM = ("pm_",)

CANCELLATION_REASONS: tuple[str, ...] = tuple(
    spec_document()["components"]["schemas"]["payment_intent"]["properties"]["cancellation_reason"][
        "enum"
    ]
)

# --- the ParamSpecs -----------------------------------------------------------------

_ADDRESS_BODY = (
    Param(name="city", kind="string", max_length=5_000),
    Param(name="country", kind="string", max_length=5_000),
    Param(name="line1", kind="string", max_length=5_000),
    Param(name="line2", kind="string", max_length=5_000),
    Param(name="postal_code", kind="string", max_length=5_000),
    Param(name="state", kind="string", max_length=5_000),
)

_SHIPPING = Param(
    name="shipping",
    kind="object",
    shape=(
        Param(name="address", kind="object", shape=_ADDRESS_BODY),
        Param(name="carrier", kind="string", max_length=5_000),
        Param(name="name", kind="string", max_length=5_000),
        Param(name="phone", kind="string", max_length=5_000),
        Param(name="tracking_number", kind="string", max_length=5_000),
    ),
)

_PAYMENT_METHOD_OPTIONS = Param(
    name="payment_method_options",
    kind="object",
    shape=(
        Param(
            name="card",
            kind="object",
            shape=(
                Param(name="installments", kind="object", shape=()),
                Param(name="mandate_options", kind="object", shape=()),
                Param(
                    name="network",
                    kind="literal",
                    choices=(
                        "amex",
                        "cartes_bancaires",
                        "diners",
                        "discover",
                        "interac",
                        "jcb",
                        "mastercard",
                        "visa",
                    ),
                ),
                Param(name="request_three_d_secure", kind="literal", choices=("automatic", "any")),
            ),
        ),
    ),
)

_PAYMENT_METHOD_TYPES = Param(
    name="payment_method_types", kind="array", item=Param(name="", kind="string", max_length=5_000)
)

_STATEMENT_DESCRIPTOR = Param(name="statement_descriptor", kind="string", max_length=22)
_STATEMENT_DESCRIPTOR_SUFFIX = Param(
    name="statement_descriptor_suffix", kind="string", max_length=22
)
_RECEIPT_EMAIL = Param(name="receipt_email", kind="string", max_length=5_000)
_DESCRIPTION = Param(
    name="description", kind="string", max_length=5_000, unset_with_empty_string=True
)

PI_CREATE = ParamSpec(
    op_id="PostPaymentIntents",
    body=(
        Param(name="amount", kind="integer", required=True),
        Param(name="currency", kind="string", max_length=3, required=True),
        Param(
            name="automatic_payment_methods",
            kind="object",
            shape=(
                Param(name="enabled", kind="boolean", required=True),
                Param(name="allow_redirects", kind="literal", choices=("always", "never")),
            ),
        ),
        Param(
            name="capture_method",
            kind="literal",
            choices=("automatic", "automatic_async", "manual"),
        ),
        Param(name="confirm", kind="boolean"),
        Param(name="confirmation_method", kind="literal", choices=("automatic", "manual")),
        Param(name="customer", kind="id", id_prefixes=CUS),
        _DESCRIPTION,
        # Consumed by the confirm transition, never stored.
        Param(name="off_session", kind="boolean"),
        Param(name="payment_method", kind="id", id_prefixes=PM),
        _PAYMENT_METHOD_OPTIONS,
        _PAYMENT_METHOD_TYPES,
        _RECEIPT_EMAIL,
        Param(name="setup_future_usage", kind="literal", choices=("off_session", "on_session")),
        _SHIPPING,
        _STATEMENT_DESCRIPTOR,
        _STATEMENT_DESCRIPTOR_SUFFIX,
    ),
    metadata=True,
)

PI_UPDATE = ParamSpec(
    op_id="PostPaymentIntentsIntent",
    path=("intent",),
    body=(
        Param(name="amount", kind="integer"),
        Param(name="currency", kind="string", max_length=3),
        Param(name="customer", kind="id", id_prefixes=CUS),
        _DESCRIPTION,
        Param(name="payment_method", kind="id", id_prefixes=PM),
        _PAYMENT_METHOD_OPTIONS,
        _PAYMENT_METHOD_TYPES,
        _RECEIPT_EMAIL,
        Param(name="setup_future_usage", kind="literal", choices=("off_session", "on_session")),
        _SHIPPING,
        _STATEMENT_DESCRIPTOR,
        _STATEMENT_DESCRIPTOR_SUFFIX,
    ),
    metadata=True,
)

PI_LIST = ParamSpec(
    op_id="GetPaymentIntents",
    paginated=True,
)

PI_RETRIEVE = ParamSpec(
    op_id="GetPaymentIntentsIntent",
    path=("intent",),
)

PI_CONFIRM = ParamSpec(
    op_id="PostPaymentIntentsIntentConfirm",
    path=("intent",),
    body=(
        Param(
            name="capture_method",
            kind="literal",
            choices=("automatic", "automatic_async", "manual"),
        ),
        Param(name="off_session", kind="boolean"),
        Param(name="payment_method", kind="id", id_prefixes=PM),
        _PAYMENT_METHOD_TYPES,
        _RECEIPT_EMAIL,
        _SHIPPING,
        _STATEMENT_DESCRIPTOR,
        _STATEMENT_DESCRIPTOR_SUFFIX,
    ),
)

PI_CAPTURE = ParamSpec(
    op_id="PostPaymentIntentsIntentCapture",
    path=("intent",),
    body=(
        Param(name="amount_to_capture", kind="integer"),
        _STATEMENT_DESCRIPTOR,
        _STATEMENT_DESCRIPTOR_SUFFIX,
    ),
    metadata=True,
)

PI_CANCEL = ParamSpec(
    op_id="PostPaymentIntentsIntentCancel",
    path=("intent",),
    body=(Param(name="cancellation_reason", kind="literal", choices=CANCELLATION_REASONS),),
)

# --- the serializer -----------------------------------------------------------------

always_present, omit_when_none = presence_sets("payment_intent")

FIELDS = FieldMap(
    object="payment_intent",
    table="payment_intents",
    columns={
        "id": "id",
        "created": "created",
        "amount": "amount",
        "amount_capturable": "amount_capturable",
        "amount_received": "amount_received",
        "automatic_payment_methods": "automatic_payment_methods",
        "canceled_at": "canceled_at",
        "cancellation_reason": "cancellation_reason",
        "capture_method": "capture_method",
        "client_secret": "client_secret",
        "confirmation_method": "confirmation_method",
        "currency": "currency",
        "customer": "customer",
        "description": "description",
        "last_payment_error": "last_payment_error",
        "latest_charge": "latest_charge",
        "metadata": "metadata",
        "next_action": "next_action",
        "payment_method": "payment_method",
        "payment_method_options": "payment_method_options",
        "payment_method_types": "payment_method_types",
        "receipt_email": "receipt_email",
        "setup_future_usage": "setup_future_usage",
        "shipping": "shipping",
        "statement_descriptor": "statement_descriptor",
        "statement_descriptor_suffix": "statement_descriptor_suffix",
        "status": "status",
    },
    timestamps=frozenset({"created", "canceled_at"}),
    json_columns=frozenset(
        {
            "automatic_payment_methods",
            "last_payment_error",
            "metadata",
            "next_action",
            "payment_method_options",
            "payment_method_types",
            "shipping",
        }
    ),
    constants={
        # Recorded on every PI at the pinned version; tips are the only
        # sub-object and this world models none, so the recorded constant is
        # the whole field.
        "amount_details": {"tip": {}},
        "allowed_payment_method_types": None,
        "application": None,
        "application_fee_amount": None,
        "customer_account": None,
        "excluded_payment_method_types": None,
        # The recorded value is `{"enabled": false}` — an object where the
        # pinned spec types the field `string` — so this world emits the
        # spec-legal null and the difference is an allow-list entry with the
        # recording cited, rather than a schema-conformance failure.
        "managed_payments": None,
        "on_behalf_of": None,
        "payment_method_configuration_details": None,
        "processing": None,
        "review": None,
        "transfer_data": None,
        "transfer_group": None,
        # Phase 9 serialization sweep: fields present in the pruned spec
        # but never serialized until now.
        "hooks": None,
        "payment_details": None,
        "presentment_details": None,
    },
    always_present=always_present,
    omit_when_none=omit_when_none,
)

_serialize_fieldmap = serializer_for(FIELDS)


def serialize(ctx: seahaven.Ctx, row: Mapping[str, Any]) -> dict[str, Any]:
    return _serialize_fieldmap(ctx, row)


#: The default card options the live API stamps on every card-confirmed
#: intent (recorded, Phase 8); used when the caller sent none.
_PM_OPTIONS_DEFAULT: dict[str, object] = {
    "card": {
        "installments": None,
        "mandate_options": None,
        "network": None,
        "request_three_d_secure": "automatic",
    }
}

#: The decline table's `(code, decline_code)` → the card-error message.
#: `generic_decline` is cassette-pinned; the rest are Stripe's documented
#: card-error texts for the magic table's decline cards.
_DECLINE_MESSAGES: dict[tuple[str, str | None], str] = {
    ("card_declined", "generic_decline"): "Your card was declined.",
    ("card_declined", "insufficient_funds"): "Your card has insufficient funds.",
    ("card_declined", "lost_card"): "Your card was declined.",
    ("card_declined", "stolen_card"): "Your card was declined.",
    ("card_declined", "card_velocity_exceeded"): "Your card was declined.",
    ("expired_card", None): "Your card has expired.",
    ("incorrect_cvc", None): "Your card's security code is incorrect.",
    (
        "processing_error",
        None,
    ): "An error occurred while processing your card. Try again in a little bit.",
}

_AUTHENTICATION_REQUIRED = "Your card was declined. This transaction requires authentication."

# --- shared refusal helpers (recorded spellings, Phase 8) -----------------------------


def _re_read(ctx: seahaven.Ctx, id_: str) -> dict[str, Any]:
    return _lookup.require_row(ctx, "payment_intents", "payment_intent", id_, param="intent")


def _refuse_unexpected_state(
    message: str,
    *,
    body: dict[str, Any],
    code: str | None = "payment_intent_unexpected_state",
    param: str | None = None,
) -> NoReturn:
    """The wrong-state family, every recorded member of which carries the
    full intent on the error object."""
    raise invalid_request(message, code=code, param=param, sub_objects={"payment_intent": body})


def _merge_shipping(current_text: str | None, update: Mapping[str, Any] | None) -> str:
    """The leaf-wise nested-object merge every resource on this surface
    applies, against the intent's stored shipping."""
    loaded: object = _json.loads(current_text) if current_text is not None else None
    current: dict = loaded if isinstance(loaded, dict) else {}
    merged = {**current, **(update or {})}
    if isinstance(current.get("address"), dict) and isinstance((update or {}).get("address"), dict):
        merged["address"] = {**current["address"], **(update or {})["address"]}
    return _json.dumps(_canonical_shipping(merged))


def _store(value: Any) -> Any:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, dict | list):
        return _json.dumps(value)
    return value


# --- the confirm transition -----------------------------------------------------------


def _behavior_tags(row: Mapping[str, Any]) -> dict[str, str]:
    tags: dict[str, str] = {}
    for token in (row["x_behavior"] or "").split(","):
        if "=" in token:
            key, value = token.split("=", 1)
            tags[key] = value
        elif token:
            tags[token] = ""
    return tags


def _resolve_payment_method(
    ctx: seahaven.Ctx, pi_row: Mapping[str, Any], params: Mapping[str, Any]
) -> dict[str, Any] | None:
    """Parameter → the intent's own column → the customer's default."""
    pm_id = params.get("payment_method") or pi_row["payment_method"]
    if pm_id is not None:
        return _lookup.require_live_row(
            ctx, "payment_methods", "PaymentMethod", pm_id, param="payment_method"
        )
    if pi_row["customer"] is not None:
        customer = _lookup.require_row(
            ctx, "customers", "customer", pi_row["customer"], param="customer"
        )
        settings: object = _json.loads(customer["invoice_settings"])
        default = settings.get("default_payment_method") if isinstance(settings, dict) else None
        if default is not None:
            return _lookup.require_live_row(
                ctx, "payment_methods", "PaymentMethod", default, param="payment_method"
            )
    return None


def _payment_method_body(ctx: seahaven.Ctx, pm_row: Mapping[str, Any]) -> dict[str, Any]:
    """The serialized payment method as the decline snapshots carry it: the
    charge attempt verified the CVC, so the snapshot's card rail reports
    `pass` even when the stored rail still says `unchecked` (recorded,
    Phase 8 — the unattached decline card's `last_payment_error`)."""
    body = payment_methods._serialize(ctx, pm_row)
    card = body.get("card")
    if isinstance(card, dict) and isinstance(card.get("checks"), dict):
        card["checks"]["cvc_check"] = "pass"
    return body


def _last_payment_error(
    *,
    code: str,
    decline_code: str | None,
    message: str,
    charge: str | None,
    pm_body: dict[str, Any],
) -> dict[str, object]:
    """The recorded shape: `charge` present only when a charge row exists
    (the 3DS off-session decline writes none), the payment method always the
    full object."""
    error: dict[str, object] = {
        "code": code,
        "decline_code": decline_code,
        "doc_url": f"https://stripe.com/docs/error-codes/{code.replace('_', '-')}",
        "message": message,
        "payment_method": pm_body,
        "payment_method_type": pm_body["type"],
        "type": "card_error",
    }
    if charge is not None:
        error["charge"] = charge
    return error


def _confirm(
    ctx: seahaven.Ctx, pi_row: Mapping[str, Any], params: Mapping[str, Any]
) -> dict[str, Any] | ApiResponse:
    """The confirm transition shared by create-with-confirm and the confirm
    endpoint. Returns the serialized intent (200) or an `ApiResponse` 402 —
    the decline outcome whose rows must survive (`components/cross_cutting.md`
    §3.5.4)."""
    status = pi_row["status"]
    if status not in ("requires_payment_method", "requires_confirmation", "requires_action"):
        # Built on the recorded capture/cancel pattern; the confirm spelling
        # itself is unrecorded (the probe account's dashboard payment-method
        # config blocked every confirm meant to reach it), noted here rather
        # than passed off as observed.
        _refuse_unexpected_state(
            "This PaymentIntent could not be confirmed because it has a status of "
            f"{status}. Only a PaymentIntent with one of the following statuses may be "
            "confirmed: requires_payment_method, requires_confirmation, requires_action.",
            body=serialize(ctx, pi_row),
        )

    pm_row = _resolve_payment_method(ctx, pi_row, params)
    if pm_row is None:
        if pi_row["customer"] is not None:
            message = (
                "You cannot confirm this PaymentIntent because it's missing a payment "
                f"method. To confirm the PaymentIntent with {pi_row['customer']}, specify "
                "a payment method attached to this customer along with the customer ID."
            )
        else:
            message = (
                "You cannot confirm this PaymentIntent because it's missing a payment "
                "method. You can either update the PaymentIntent with a payment method "
                "and then confirm it again, or confirm it again directly with a payment "
                "method or ConfirmationToken."
            )
        _refuse_unexpected_state(message, body=serialize(ctx, pi_row))
    if pi_row["customer"] is None:
        if pm_row["customer"] is not None:
            # Recorded (cassette 04): a customerless intent confirmed with a
            # customer's method is refused with the ownership message —
            # `parameter_missing` under a quirky `param: "source"` —
            # carrying the full intent.
            _refuse_unexpected_state(
                f"The `payment_method` parameter supplied {pm_row['id']} belongs to the "
                f"Customer {pm_row['customer']}. Please include the Customer in the "
                "`customer` parameter on the PaymentIntent.",
                body=serialize(ctx, pi_row),
                code="parameter_missing",
                param="source",
            )
        # A fresh unattached method on a customerless intent charges (recorded,
        # cassette 04: 200, amount_received set) — real Stripe's rule burns the
        # method after one use, and this world does not track prior uses, so a
        # *reused* unattached method charges here where real Stripe refuses
        # "may not be used again"; that divergence is this comment's
        # declaration, kept because usage-count state is no recorded behavior
        # this phase pins.
    elif pm_row["customer"] is not None and pm_row["customer"] != pi_row["customer"]:
        # Recorded (cassette 04): no code, `param: "payment_method"`, the
        # full intent carried on the error object.
        _refuse_unexpected_state(
            f"The PaymentMethod {pm_row['id']} does not belong to the Customer you supplied "
            f"{pi_row['customer']}. Please use this PaymentMethod with the Customer that "
            "it belongs to instead.",
            body=serialize(ctx, pi_row),
            code=None,
            param="payment_method",
        )

    tags = _behavior_tags(pm_row)
    off_session = params.get("off_session") is True
    manual = pi_row["capture_method"] == "manual"

    if tags.get("three_d_secure") == "required" and not off_session:
        # On-session 3DS: the intent parks at requires_action with no charge
        # row (recorded: `latest_charge: null`, `payment_method` kept). The
        # recorded next_action carries issuer certificates no replica can
        # reproduce, so this world emits the deterministic stub — a declared
        # structural difference, unit-tested, not in the cassette.
        next_action = {"type": "use_stripe_sdk", "use_stripe_sdk": {}}
        ctx.db.execute(
            "UPDATE payment_intents SET status = 'requires_action', payment_method = ?, "
            "next_action = ? WHERE id = ?",
            pm_row["id"],
            _json.dumps(next_action),
            pi_row["id"],
        )
        body = serialize(ctx, _re_read(ctx, pi_row["id"]))
        events.emit_event(ctx, type="payment_intent.requires_action", obj=body)
        return body

    declined_charge = "charge_declined" in tags
    if tags.get("three_d_secure") == "required" or declined_charge:
        if tags.get("three_d_secure") == "required":
            code = "authentication_required"
            decline_code: str | None = "authentication_required"
            message = _AUTHENTICATION_REQUIRED
        else:
            code = tags["charge_declined"]
            decline_code = tags.get("decline_code")
            message = _DECLINE_MESSAGES.get((code, decline_code))
            if message is None:
                # The §3.4.4 reason: a magic card without its message row is
                # world code inventing behavior Stripe does not have — fail
                # at the author, not as an agent-facing INTERNAL.
                raise seahaven.WorldBug(
                    f"no recorded message for decline ({code!r}, {decline_code!r}): "
                    "add the card's message to _DECLINE_MESSAGES"
                )
        pm_body = _payment_method_body(ctx, pm_row)
        charge_id: str | None = None
        if declined_charge:
            # A network decline attempted the charge: the failed row survives.
            charge = charges.insert_charge(
                ctx,
                payment_intent=pi_row["id"],
                pm_row=pm_row,
                amount=pi_row["amount"],
                captured=False,
                currency=pi_row["currency"],
                customer=pi_row["customer"],
                description=pi_row["description"],
                receipt_email=pi_row["receipt_email"],
                shipping=pi_row["shipping"],
                statement_descriptor=pi_row["statement_descriptor"],
                statement_descriptor_suffix=pi_row["statement_descriptor_suffix"],
                metadata=pi_row["metadata"],
                failure_code=code,
                failure_message=message,
                outcome=charges.outcome_declined(decline_code or "generic_decline"),
            )
            charge_id = charge["id"]
        error = _last_payment_error(
            code=code,
            decline_code=decline_code,
            message=message,
            charge=charge_id,
            pm_body=pm_body,
        )
        ctx.db.execute(
            "UPDATE payment_intents SET status = 'requires_payment_method', "
            "payment_method = NULL, last_payment_error = ?, latest_charge = ? WHERE id = ?",
            _json.dumps(error),
            charge_id,
            pi_row["id"],
        )
        body = serialize(ctx, _re_read(ctx, pi_row["id"]))
        if charge_id is not None:
            events.emit_event(
                ctx,
                type="charge.failed",
                obj=charges.serialize(
                    ctx, _lookup.require_row(ctx, "charges", "charge", charge_id, param="charge")
                ),
            )
        events.emit_event(ctx, type="payment_intent.payment_failed", obj=body)
        # Recorded (Phase 8, cassette 04 steps 12 and 34): both decline
        # variants carry the payment method on the error object.
        sub_objects: dict[str, dict] = {"payment_intent": body, "payment_method": pm_body}
        return ApiResponse(
            402,
            declined(
                code=code,
                decline_code=decline_code,
                message=message,
                charge=charge_id,
                sub_objects=sub_objects,
            ),
        )

    # Success: charge row first, then the intent, then the event pair — the
    # recorded order (charge.succeeded precedes payment_intent.succeeded, and
    # amount_capturable_updated under manual capture).
    charge = charges.insert_charge(
        ctx,
        payment_intent=pi_row["id"],
        pm_row=pm_row,
        amount=pi_row["amount"],
        captured=not manual,
        currency=pi_row["currency"],
        customer=pi_row["customer"],
        description=pi_row["description"],
        receipt_email=pi_row["receipt_email"],
        shipping=pi_row["shipping"],
        statement_descriptor=pi_row["statement_descriptor"],
        statement_descriptor_suffix=pi_row["statement_descriptor_suffix"],
        metadata=pi_row["metadata"],
    )
    if manual:
        ctx.db.execute(
            "UPDATE payment_intents SET status = 'requires_capture', payment_method = ?, "
            "amount_capturable = ?, latest_charge = ?, next_action = NULL WHERE id = ?",
            pm_row["id"],
            pi_row["amount"],
            charge["id"],
            pi_row["id"],
        )
    else:
        ctx.db.execute(
            "UPDATE payment_intents SET status = 'succeeded', payment_method = ?, "
            "amount_received = ?, latest_charge = ?, next_action = NULL WHERE id = ?",
            pm_row["id"],
            pi_row["amount"],
            charge["id"],
            pi_row["id"],
        )
    body = serialize(ctx, _re_read(ctx, pi_row["id"]))
    events.emit_event(ctx, type="charge.succeeded", obj=charges.serialize(ctx, charge))
    if not manual:
        # A dispute-tagged method opens its dispute here — after the charge's
        # own event, before the intent's (the recorded order, Phase 9): the
        # dispute is created by the payment itself, not by a later call.
        # Manual capture is gated out (probed, Phase 9 CR round): an
        # authorized-but-uncaptured hold creates NO dispute — the issuer
        # disputes captured funds — and the capture transition below creates
        # it instead, in the same recorded order.
        disputes.maybe_create_dispute(ctx, charge, pm_row)
    if manual:
        events.emit_event(ctx, type="payment_intent.amount_capturable_updated", obj=body)
    else:
        events.emit_event(ctx, type="payment_intent.succeeded", obj=body)
    return body


# --- the handlers --------------------------------------------------------------------


def _mint_client_secret(ctx: seahaven.Ctx, id_: str) -> str:
    alphabet = string.ascii_letters + string.digits
    suffix = "".join(ctx.ids.random.choice(alphabet) for _ in range(25))
    return f"{id_}_secret_{suffix}"


def create(ctx: seahaven.Ctx, req: Request) -> dict[str, Any] | ApiResponse:
    """`POST /v1/payment_intents`: the row plus, when `confirm` was sent, the
    confirm transition — hand-written because confirming writes the charge
    row, moves the intent and emits the event pair, three things the engine's
    normalizer contract exists to keep out of."""
    params = dict(req.params)
    if params["amount"] <= 0:
        # Recorded verbatim (Phase 8): amount=0 names the minimum-charge rule
        # and points at Setup Intents.
        raise invalid_request(
            "The amount must be greater than or equal to the minimum charge amount allowed "
            "for your account and the currency set "
            "(https://docs.stripe.com/currencies#minimum-and-maximum-charge-amounts).  "
            "If you want to save a Payment Method for future use without an immediate "
            "payment, use a Setup Intent instead: https://docs.stripe.com/payments/setup-intents",
            code="parameter_invalid_integer",
            param="amount",
            pre_execution=True,
        )
    if "customer" in params:
        _lookup.require_live_row(ctx, "customers", "customer", params["customer"], param="customer")
    if "payment_method" in params:
        _lookup.require_live_row(
            ctx,
            "payment_methods",
            "PaymentMethod",
            params["payment_method"],
            param="payment_method",
        )
    should_confirm = params.pop("confirm", None) is True
    params.pop("off_session", None)
    pi_created = ctx.clock.iso()
    id_ = _ids.stripe_id(ctx, "pi_", timestamp=pi_created)
    cols: dict[str, Any] = {
        "id": id_,
        "x_seq": _seq.next_seq(ctx, "payment_intents"),
        "created": pi_created,
        "client_secret": _mint_client_secret(ctx, id_),
        # Recorded: `automatic_async` is the default at the pinned version.
        "capture_method": "automatic_async",
        "confirmation_method": "automatic",
        "status": (
            "requires_confirmation" if params.get("payment_method") else "requires_payment_method"
        ),
        "amount_capturable": 0,
        "amount_received": 0,
    }
    cols.update({key: _store(value) for key, value in params.items()})
    if "shipping" in cols:
        cols["shipping"] = _json.dumps(_canonical_shipping(params.get("shipping")))
    cols.setdefault("payment_method_types", '["card"]')
    cols.setdefault("payment_method_options", _json.dumps(_PM_OPTIONS_DEFAULT))
    if req.metadata is not None:
        cols["metadata"] = _json.dumps(dict(req.metadata.apply({})))
    columns = ", ".join(cols)
    placeholders = ", ".join("?" for _ in cols)
    ctx.db.execute(
        f"INSERT INTO payment_intents ({columns}) VALUES ({placeholders})", *cols.values()
    )
    row = _re_read(ctx, id_)
    # Emitted before any confirm transition, snapshotting the created state.
    events.emit_event(ctx, type="payment_intent.created", obj=serialize(ctx, row))
    if should_confirm:
        return _confirm(ctx, row, req.params)
    return serialize(ctx, row)


def confirm(ctx: seahaven.Ctx, req: Request) -> dict[str, Any] | ApiResponse:
    """`POST /v1/payment_intents/{intent}/confirm`: apply the confirm-time
    parameters, then run the transition."""
    row = _re_read(ctx, req.path_params["intent"])
    # `payment_method` is excluded: the transition owns that column (every
    # accepting path writes it), and a refused confirm must leave it
    # untouched — recorded (CR round 1): the ownership refusal's carried
    # intent shows `payment_method: null`, so persisting the parameter
    # before the refusal is decided is a divergence.
    sets = {
        key: _store(value)
        for key, value in req.params.items()
        if key not in ("off_session", "payment_method")
    }
    if "shipping" in sets:
        sets["shipping"] = _merge_shipping(row["shipping"], req.params.get("shipping"))
    if sets:
        assignments = ", ".join(f"{column} = ?" for column in sets)
        ctx.db.execute(
            f"UPDATE payment_intents SET {assignments} WHERE id = ?", *sets.values(), row["id"]
        )
        row = _re_read(ctx, row["id"])
    return _confirm(ctx, row, req.params)


def capture(ctx: seahaven.Ctx, req: Request) -> dict[str, Any]:
    """`POST /v1/payment_intents/{intent}/capture`: wrong-state refusal
    recorded verbatim; partial capture supported; overcapture refused with
    the recorded `amount_too_large`."""
    row = _re_read(ctx, req.path_params["intent"])
    if row["status"] == "succeeded":
        # Recorded verbatim (Phase 8): a fully-captured intent's re-capture
        # answer is not the generic wrong-state message but this one.
        _refuse_unexpected_state(
            "The remaining amount on this PaymentIntent could not be captured because "
            "the remainder of the authorized amount has been released.",
            body=serialize(ctx, row),
        )
    if row["status"] != "requires_capture":
        _refuse_unexpected_state(
            "This PaymentIntent could not be captured because it has a status of "
            f"{row['status']}. Only a PaymentIntent with one of the following statuses "
            "may be captured: requires_capture.",
            body=serialize(ctx, row),
        )
    charge_id = row["latest_charge"]
    if charge_id is None:
        raise seahaven.WorldBug(
            f"payment_intent {row['id']} is requires_capture with no latest_charge; "
            "no transition in this world writes that state"
        )
    amount_to_capture = req.params.get("amount_to_capture", row["amount"])
    if isinstance(amount_to_capture, int) and amount_to_capture < 0:
        # Recorded (cassette 04, CR round 2): no code, `param` names the
        # parameter, the intent carried — and without this floor a negative
        # value reached the UPDATE and died on a CHECK constraint as INTERNAL.
        raise invalid_request(
            "Invalid non-negative integer",
            param="amount_to_capture",
            sub_objects={"payment_intent": serialize(ctx, row)},
        )
    if amount_to_capture == 0:
        # Recorded (cassette 04, CR round 2): zero answers the minimum-charge
        # refusal, oddly under `param: "amount"` rather than the parameter
        # sent — verbatim, like the rest of the family.
        raise invalid_request(
            "This value must be greater than or equal to 1.",
            code="parameter_invalid_integer",
            param="amount",
            sub_objects={"payment_intent": serialize(ctx, row)},
        )
    if amount_to_capture > row["amount"]:
        raise invalid_request(
            "The payment could not be captured because the requested capture amount is "
            "greater than the amount you can capture for this charge. Contact us via "
            "https://support.stripe.com/contact/ for help.",
            code="amount_too_large",
            sub_objects={"payment_intent": serialize(ctx, row)},
        )
    ctx.db.execute(
        "UPDATE charges SET captured = 1, amount_captured = ? WHERE id = ?",
        amount_to_capture,
        charge_id,
    )
    # The hold's ledger row lands here — the captured funds are the only
    # funds that ever move (the recorded manual-capture charge carries its
    # txn in the capture's own response, cassette 04 step 19).
    charges.record_capture_ledger(
        ctx,
        charge_id,
        amount_captured=amount_to_capture,
        currency=row["currency"],
        description=row["description"],
    )
    # `metadata` is a declared parameter of the capture operation (pinned
    # spec) and merges like every update's (the engine's own two-line
    # pattern, dispatch/resource.py) — CR round 1 caught it being accepted
    # and dropped.
    metadata_text = row["metadata"]
    if req.metadata is not None:
        current = _json.loads(metadata_text)
        metadata_text = _json.dumps(dict(req.metadata.apply(current or {})))
    ctx.db.execute(
        "UPDATE payment_intents SET status = 'succeeded', amount_capturable = 0, "
        "amount_received = ?, metadata = ? WHERE id = ?",
        amount_to_capture,
        metadata_text,
        row["id"],
    )
    charge_row = _lookup.require_row(ctx, "charges", "charge", charge_id, param="charge")
    body = serialize(ctx, _re_read(ctx, row["id"]))
    events.emit_event(ctx, type="charge.captured", obj=charges.serialize(ctx, charge_row))
    if charge_row["payment_method"] is not None:
        pm_row = ctx.db.one(
            "SELECT * FROM payment_methods WHERE id = ?", charge_row["payment_method"]
        )
        if pm_row is not None:
            # The manual-capture twin of the confirm path's dispute creation
            # (probed, Phase 9 CR round): the hold created nothing, the
            # capture pulls the funds the issuer disputes — same recorded
            # event order, `charge.captured` then the dispute pair. The
            # disputed amount is the captured amount: a partial capture is
            # the only funds the customer could dispute (unrecorded edge,
            # the captured-funds rule it follows).
            disputes.maybe_create_dispute(ctx, charge_row, pm_row)
    events.emit_event(ctx, type="payment_intent.succeeded", obj=body)
    return body


def cancel(ctx: seahaven.Ctx, req: Request) -> dict[str, Any]:
    """`POST /v1/payment_intents/{intent}/cancel`: stamps `canceled_at` and
    the caller's reason (recorded: `null` when omitted); a held authorization
    is released and its charge row keeps its uncaptured shape (recorded)."""
    row = _re_read(ctx, req.path_params["intent"])
    if row["status"] not in (
        "requires_payment_method",
        "requires_capture",
        "requires_confirmation",
        "requires_action",
        "processing",
    ):
        # Recorded verbatim, including the legacy statuses in the allowed
        # list that this world's status enum does not carry.
        _refuse_unexpected_state(
            f"You cannot cancel this PaymentIntent because it has a status of "
            f"{row['status']}. Only a PaymentIntent with one of the following statuses "
            "may be canceled: requires_payment_method, requires_capture, "
            "requires_reauthorization, requires_confirmation, requires_action, expired, "
            "processing.",
            body=serialize(ctx, row),
        )
    ctx.db.execute(
        "UPDATE payment_intents SET status = 'canceled', canceled_at = ?, "
        "cancellation_reason = ?, next_action = NULL WHERE id = ?",
        ctx.clock.iso(),
        req.params.get("cancellation_reason"),
        row["id"],
    )
    body = serialize(ctx, _re_read(ctx, row["id"]))
    events.emit_event(ctx, type="payment_intent.canceled", obj=body)
    return body


# --- the engine-served update ---------------------------------------------------------

_AMOUNT_EDITABLE = ("requires_payment_method", "requires_confirmation", "requires_action")


def _before_update(ctx: seahaven.Ctx, req: Request, sets: dict[str, object]) -> dict[str, object]:
    """The recorded amount guard — updateable only before the intent is in
    flight, the full intent carried on the error object — plus the reference
    lookups and the shipping leaf-merge the engine does not do."""
    row = _re_read(ctx, req.path_params["intent"])
    if "customer" in sets:
        _lookup.require_live_row(
            ctx, "customers", "customer", str(sets["customer"]), param="customer"
        )
    if "payment_method" in sets:
        _lookup.require_live_row(
            ctx,
            "payment_methods",
            "PaymentMethod",
            str(sets["payment_method"]),
            param="payment_method",
        )
    if "amount" in sets and row["status"] not in _AMOUNT_EDITABLE:
        raise invalid_request(
            "This PaymentIntent's amount could not be updated because it has a status "
            f"of {row['status']}. You may only update the amount of a PaymentIntent with "
            "one of the following statuses: requires_payment_method, requires_confirmation, "
            "requires_action.",
            code="payment_intent_unexpected_state",
            param="amount",
            sub_objects={"payment_intent": serialize(ctx, row)},
        )
    if "shipping" in sets:
        sets["shipping"] = _merge_shipping(row["shipping"], req.params.get("shipping"))
    return sets


SPEC = register(
    ResourceSpec(
        object="payment_intent",
        table="payment_intents",
        id_prefix="pi_",
        collection_url="/v1/payment_intents",
        serializer=serialize,
        columns=tuple(FIELDS.columns),
        # Probed (Phase 8): every PI path keeps the `intent` placeholder and
        # the message says "No such payment_intent" (snake case, unlike
        # PaymentMethod's CamelCase).
        missing_path_param=None,
        list_filters=(
            ListFilter(
                name="customer",
                column="customer",
                kind="exact",
                id_prefixes=CUS,
                references="customer",
            ),
            ListFilter(name="created", column="created", kind="range"),
        ),
        creatable=PI_CREATE,
        updatable=PI_UPDATE,
        delete=None,
        metadata=True,
        before_update=_before_update,
        # The hand-written create emits payment_intent.created itself, before
        # any confirm transition, so the snapshot is the created state. And
        # there is no `payment_intent.updated` in the 266-entry closed set —
        # Stripe genuinely emits none for intent updates (charge.updated
        # exists; payment_intent.updated does not), so the engine emits none
        # either.
        created_event=None,
        updated_event=None,
    )
)
