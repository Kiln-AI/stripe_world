"""The payment_methods slice: generated CRUD plus hand-written attach/detach.

Every behavior here is pinned by live probe at `2026-08-26.dahlia` (this
phase, 2026-09-19) or by cassette 06: the full `card` block a token or number
produces, the `{}` stub of an unmodeled rail (probed on `klarna`), the
`billing_details` canonical shape, the attach/detach semantics and their
error envelopes, and the `customer`-shaped list filters. The magic-card
interpretation lives in `billing/magic_cards.py`; this module turns a
validated `card` / `us_bank_account` parameter into the rail JSON and the
`x_behavior` tag the money-path phases read.
"""

from collections.abc import Mapping
from typing import TYPE_CHECKING

import seahaven

from seahaven_stripe_world import _json
from seahaven_stripe_world.billing import magic_cards
from seahaven_stripe_world.dispatch.params import Param, ParamSpec
from seahaven_stripe_world.dispatch.resource import (
    ListFilter,
    ResourceSpec,
    Scope,
    register,
)
from seahaven_stripe_world.resources import _lookup, customers, events
from seahaven_stripe_world.serialize import fields as serialize_fields
from seahaven_stripe_world.serialize.fields import FieldMap, presence_sets
from seahaven_stripe_world.spec import spec_document
from seahaven_stripe_world.stripe_errors import StripeApiError, invalid_request, missing_parameter

if TYPE_CHECKING:
    from seahaven_stripe_world.dispatch.response import Request

__all__ = ["SPEC", "attach", "detach"]

CUS = ("cus_",)

#: The 57-value `type` enum, read from the committed spec rather than
#: retyped: the DDL CHECK, the ParamSpec literal and the spec cannot disagree.
PM_TYPES: tuple[str, ...] = tuple(
    spec_document()["components"]["schemas"]["payment_method"]["properties"]["type"]["enum"]
)


# --- the rail JSON ----------------------------------------------------------------


def _card_rail(card: magic_cards.CardBehavior, exp_month: int, exp_year: int) -> dict[str, object]:
    return {
        "brand": card.brand,
        "checks": {
            "address_line1_check": None,
            "address_postal_code_check": None,
            "cvc_check": card.cvc_check,
        },
        "country": card.country,
        "display_brand": card.display_brand,
        "exp_month": exp_month,
        "exp_year": exp_year,
        "fingerprint": magic_cards.fingerprint(card.number),
        "funding": card.funding,
        "generated_from": None,
        "last4": card.number[-4:],
        "networks": {"available": [card.brand], "preferred": None},
        "regulated_status": "unregulated",
        "three_d_secure_usage": {"supported": True},
        "wallet": None,
    }


_US_BANK_RAIL_DEFAULTS = {
    "account_type": "checking",
    "bank_name": "STRIPE TEST BANK",
    "financial_connections_account": None,
    "networks": {"preferred": "ach", "supported": ["ach"]},
    "status_details": {},
}


def _billing_address_keys() -> tuple[str, ...]:
    return ("city", "country", "line1", "line2", "postal_code", "state")


def _canonical_billing_details(value: dict | None) -> dict[str, object]:
    """Recorded: `billing_details.address` is always a full null-keyed object,
    unlike `customer.address` which is null when unset."""
    source = value or {}
    address = source.get("address")
    if not isinstance(address, dict):
        address = {}
    return {
        "address": {key: address.get(key) for key in _billing_address_keys()},
        "email": source.get("email"),
        "name": source.get("name"),
        "phone": source.get("phone"),
        "tax_id": None,
    }


# --- the normalizers ----------------------------------------------------------------

_CARD_CREATE = (
    Param(name="number", kind="string", max_length=5_000),
    Param(name="exp_month", kind="integer", minimum=1, maximum=12),
    Param(name="exp_year", kind="integer", minimum=1970),
    Param(name="cvc", kind="string", max_length=4),
    Param(
        name="networks",
        kind="object",
        shape=(
            Param(
                name="preferred",
                kind="literal",
                choices=("cartes_bancaires", "mastercard", "visa"),
            ),
        ),
    ),
    Param(name="token", kind="string", max_length=5_000),
)

_US_BANK_CREATE = (
    Param(name="account_number", kind="string", max_length=17, required=True),
    Param(name="routing_number", kind="string", max_length=9, required=True),
    Param(name="account_holder_type", kind="literal", choices=("individual", "company")),
)

_ADDRESS_BODY = tuple(
    Param(name=key, kind="string", max_length=5_000) for key in _billing_address_keys()
)

_BILLING_DETAILS = Param(
    name="billing_details",
    kind="object",
    shape=(
        Param(name="address", kind="object", shape=_ADDRESS_BODY),
        Param(name="email", kind="string", max_length=5_000),
        Param(name="name", kind="string", max_length=5_000),
        Param(name="phone", kind="string", max_length=5_000),
    ),
)

PM_CREATE = ParamSpec(
    op_id="PostPaymentMethods",
    body=(
        Param(name="type", kind="literal", choices=PM_TYPES, required=True),
        Param(name="card", kind="object", shape=_CARD_CREATE),
        Param(name="us_bank_account", kind="object", shape=_US_BANK_CREATE),
        _BILLING_DETAILS,
        Param(name="allow_redisplay", kind="literal", choices=("always", "limited", "unspecified")),
        # Accepted so the probed refusal — not `parameter_unknown` — is what a
        # caller sending it sees (recorded this phase).
        Param(name="customer", kind="id", id_prefixes=CUS),
    ),
    metadata=True,
)

PM_RETRIEVE = ParamSpec(
    op_id="GetPaymentMethodsPaymentMethod",
    path=("payment_method",),
)

PM_UPDATE = ParamSpec(
    op_id="PostPaymentMethodsPaymentMethod",
    path=("payment_method",),
    body=(
        Param(name="allow_redisplay", kind="literal", choices=("always", "limited", "unspecified")),
        _BILLING_DETAILS,
        Param(
            name="card",
            kind="object",
            shape=(
                Param(name="exp_month", kind="integer", minimum=1, maximum=12),
                Param(name="exp_year", kind="integer", minimum=1970),
            ),
        ),
    ),
    metadata=True,
)

PM_ATTACH = ParamSpec(
    op_id="PostPaymentMethodsPaymentMethodAttach",
    path=("payment_method",),
    body=(Param(name="customer", kind="id", id_prefixes=CUS),),
)

PM_DETACH = ParamSpec(
    op_id="PostPaymentMethodsPaymentMethodDetach",
    path=("payment_method",),
)

PM_LIST = ParamSpec(
    op_id="GetPaymentMethods",
    paginated=True,
)

CUSTOMER_PAYMENT_METHODS = ParamSpec(
    op_id="GetCustomersCustomerPaymentMethods",
    path=("customer",),
    paginated=True,
)

CUSTOMER_PAYMENT_METHOD = ParamSpec(
    op_id="GetCustomersCustomerPaymentMethodsPaymentMethod",
    path=("customer", "payment_method"),
)


def _card_inputs(card: dict) -> tuple[magic_cards.CardBehavior, int, int]:
    """One validated `card` parameter -> the behavior and the expiry.

    The token path is what the cassettes exercise: the recording account
    refuses raw card numbers ("Sending credit card numbers directly to the
    Stripe API is generally unsafe"), so tokens pin the response shapes while
    the world keeps accepting numbers — the magic-card table is the spec'd
    failure-injection mechanism and the raw path could not be recorded
    (declared structural difference).
    """
    token = card.get("token")
    cvc_provided = card.get("cvc") is not None
    if token is not None:
        number = magic_cards.number_for_token(token)
        if number is None:
            # Probed verbatim: `Invalid token id: pm_card_visa`, param token.
            raise invalid_request(f"Invalid token id: {token}", param="token", pre_execution=True)
        exp_month, exp_year = magic_cards.TOKEN_EXPIRY
    else:
        number = card.get("number")
        if number is None:
            raise missing_parameter("card[number]")
        if not magic_cards.is_pan(number):
            raise invalid_request(
                "Invalid string: " + number,
                code="parameter_invalid_string",
                param="card[number]",
                pre_execution=True,
            )
        if not magic_cards.luhn_valid(number):
            # The raw-number path is unrecordable on this account (see the
            # docstring); the code is the table's, the message is Stripe's
            # documented `incorrect_number` text.
            raise StripeApiError(
                402, "card_error", "Your card number is incorrect.", code="incorrect_number"
            )
        exp_month = card.get("exp_month")
        exp_year = card.get("exp_year")
        if exp_month is None:
            raise missing_parameter("card[exp_month]")
        if exp_year is None:
            raise missing_parameter("card[exp_year]")
    return magic_cards.card_for(number, cvc_provided=cvc_provided, token=token), exp_month, exp_year


def _x_behavior(card: magic_cards.CardBehavior, card_dict: dict) -> str | None:
    """The world-internal tag row: comma-separated tokens, never serialised.

    `charge_declined=<code>` is the charge-time refusal, paired with
    `decline_code=<code>` when the table carries one — the money path needs
    both (a 402's `code` and `decline_code` are independent facts: recorded
    `card_declined`/`generic_decline` against `authentication_required`/
    `authentication_required`). `attach_refused` marks the decline-table
    cards Stripe will not attach to a customer at all (probed: attaching
    `tok_visa_chargeDeclined` answers 402 while `…0341` attaches).
    `preferred_network` records the caller's choice for the rail.
    """
    tokens: list[str] = []
    if card.charge_decline is not None:
        tokens.append(f"charge_declined={card.charge_decline[0]}")
        if card.charge_decline[1] is not None:
            tokens.append(f"decline_code={card.charge_decline[1]}")
        if card.attach_declines:
            tokens.append("attach_refused")
    if card.dispute is not None:
        tokens.append(f"dispute={card.dispute}")
        if card.dispute_track == "inquiry":
            # The inquiry variant lands a `warning_*` dispute instead of a
            # chargeback (probed, Phase 9); the tag carries which.
            tokens.append("dispute_track=inquiry")
    if card.refund_async is not None:
        tokens.append(f"refund_async={card.refund_async}")
    if card.three_d_secure == "authentication_required":
        tokens.append("three_d_secure=required")
    preferred = (card_dict.get("networks") or {}).get("preferred")
    if preferred is not None:
        tokens.append(f"preferred_network={preferred}")
    return ",".join(tokens) or None


def _before_create(ctx: seahaven.Ctx, req: Request, cols: dict[str, object]) -> dict[str, object]:
    type_ = cols["type"]
    if cols.get("customer") is not None:
        # Probed verbatim: creation refuses the attach shortcut outright.
        raise invalid_request(
            "You cannot attach a PaymentMethod to a Customer during PaymentMethod creation. "
            "Please instead create the PaymentMethod and then attach it using the attachment "
            "method of the PaymentMethods API.",
            pre_execution=True,
        )
    if type_ == "card":
        card = req.params.get("card")
        if card is None:
            raise missing_parameter("card")
        behavior, exp_month, exp_year = _card_inputs(card)
        rail = _card_rail(behavior, exp_month, exp_year)
        preferred = (card.get("networks") or {}).get("preferred")
        if preferred is not None:
            rail["networks"] = {"available": [behavior.brand], "preferred": preferred}
        cols["rail"] = _json.dumps(rail)
        cols["x_behavior"] = _x_behavior(behavior, card)
        del cols["card"]
    elif type_ == "us_bank_account":
        usb = req.params.get("us_bank_account")
        if usb is None:
            raise missing_parameter("us_bank_account")
        account_number = usb["account_number"]
        rail = {
            "account_holder_type": usb.get("account_holder_type", "individual"),
            "fingerprint": magic_cards.fingerprint(account_number),
            "last4": account_number[-4:],
            "routing_number": usb["routing_number"],
            **_US_BANK_RAIL_DEFAULTS,
        }
        cols["rail"] = _json.dumps(rail)
        cols["x_behavior"] = magic_cards.US_BANK_BEHAVIORS.get(account_number)
        del cols["us_bank_account"]
    else:
        # A stubbed rail: `{}` under the type's own key (probed on `klarna`,
        # which creates with no rail parameter at all).
        cols["rail"] = "{}"
    cols["billing_details"] = _json.dumps(
        _canonical_billing_details(req.params.get("billing_details"))
    )
    cols.setdefault("allow_redisplay", "unspecified")
    return cols


def _before_update(ctx: seahaven.Ctx, req: Request, sets: dict[str, object]) -> dict[str, object]:
    row = ctx.db.one(
        "SELECT * FROM payment_methods WHERE id = ?", req.path_params["payment_method"]
    )
    if row is None:  # the engine looked this row up moments ago
        raise seahaven.WorldBug("payment_methods.before_update: row vanished under the engine")
    if "billing_details" in sets:
        loaded: object = _json.loads(row["billing_details"])
        current: dict = loaded if isinstance(loaded, dict) else {}
        update: dict = req.params.get("billing_details") or {}
        merged = {**current, **update}
        # The nested address merges per leaf like every other nested object
        # this phase recorded (`customers._merge_canonical` does the same for
        # `shipping.address`); without this, a partial address replaces the
        # stored one and the canonical re-key turns the missing leaves null.
        if isinstance(current.get("address"), dict) and isinstance(update.get("address"), dict):
            merged["address"] = {**current["address"], **update["address"]}
        sets["billing_details"] = _json.dumps(_canonical_billing_details(merged))
    if "card" in sets:
        loaded_rail: object = _json.loads(row["rail"])
        rail: dict = loaded_rail if isinstance(loaded_rail, dict) else {}
        card = req.params.get("card") or {}
        for key in ("exp_month", "exp_year"):
            if card.get(key) is not None:
                rail[key] = card[key]
        sets["rail"] = _json.dumps(rail)
        del sets["card"]
    return sets


# --- the serializer ----------------------------------------------------------------

always_present, omit_when_none = presence_sets("payment_method")

_FIELDS = FieldMap(
    object="payment_method",
    table="payment_methods",
    columns={
        "id": "id",
        "created": "created",
        "type": "type",
        "allow_redisplay": "allow_redisplay",
        "billing_details": "billing_details",
        "customer": "customer",
        "metadata": "metadata",
    },
    timestamps=frozenset({"created"}),
    json_columns=frozenset({"billing_details", "metadata"}),
    constants={
        "livemode": False,
        "customer_account": None,
        "radar_options": None,
    },
    always_present=always_present,
    omit_when_none=omit_when_none,
)


def _serialize(ctx: seahaven.Ctx, row) -> dict[str, object]:
    """The base object plus the rail column under the row's own `type` key —
    the one place a column name is not an API field name (data_model §3.9)."""
    body = serialize_fields.to_api(ctx, _FIELDS, row)
    body[row["type"]] = _json.loads(row["rail"])
    return body


# --- attach / detach ---------------------------------------------------------------


def _declined(card_error: Mapping[str, object]) -> StripeApiError:
    """The 402 an attachment refusal raises, probed on
    `tok_visa_chargeDeclined`. Raised, not returned: no write precedes it, so
    rollback discards nothing — the payment method stays unattached, which is
    the recorded end state."""
    return StripeApiError(
        402,
        "card_error",
        str(card_error["message"]),
        code=str(card_error["code"]),
        decline_code=str(card_error["decline_code"]),
    )


def _verified_by_attach(rail_text: str) -> str:
    """Attachment verifies the stored card: `checks.cvc_check` flips from
    `unchecked` to `pass` on attach and stays there (recorded in cassette 06:
    creation answers `unchecked`, every response after the attach `pass`)."""
    loaded: object = _json.loads(rail_text)
    rail: dict = loaded if isinstance(loaded, dict) else {}
    checks = rail.get("checks")
    if isinstance(checks, dict) and checks.get("cvc_check") == "unchecked":
        checks["cvc_check"] = "pass"
    return _json.dumps(rail)


def attach(ctx: seahaven.Ctx, req: Request) -> dict[str, object]:
    """`POST /v1/payment_methods/{pm}/attach`, probed end to end:
    idempotent on repeat, 400 `parameter_missing` (the message verbatim, no
    `param`) without a customer, 400 `resource_missing` for an unknown one,
    402 for a card the decline table refuses to attach."""
    pm = req.path_params["payment_method"]
    row = _lookup.require_row(ctx, "payment_methods", _display_name(), pm, param="payment_method")
    customer = req.params.get("customer")
    if customer is None:
        raise invalid_request(
            "Must provide customer or customer_account.",
            code="parameter_missing",
            pre_execution=True,
        )
    _lookup.require_live_row(ctx, "customers", "customer", customer, param="customer")
    behavior = row["x_behavior"] or ""
    if "attach_refused" in behavior:
        raise _declined(magic_cards.ATTACH_REFUSED_ENVELOPE)
    if row["customer"] == customer:
        # Re-attaching to the same customer is a 200 no-op (probed); a repeat
        # attach emits no event — this world's ruling for *attach* no-ops
        # (the recording cannot distinguish the two; the engine's update path
        # by contrast emits its event even for a no-op update, so the two
        # behaviors are deliberately per-action, not one global rule).
        return _serialize(ctx, row)
    ctx.db.execute(
        "UPDATE payment_methods SET customer = ?, rail = ? WHERE id = ?",
        customer,
        _verified_by_attach(row["rail"]),
        pm,
    )
    fresh = _lookup.require_row(ctx, "payment_methods", _display_name(), pm, param="payment_method")
    body = _serialize(ctx, fresh)
    events.emit_event(ctx, type="payment_method.attached", obj=body)
    return body


def detach(ctx: seahaven.Ctx, req: Request) -> dict[str, object]:
    """`POST /v1/payment_methods/{pm}/detach`: `customer` to null, the event
    carrying `previous_attributes: {"customer": …}` (probed), 400 verbatim on
    an unattached method."""
    pm = req.path_params["payment_method"]
    row = _lookup.require_row(ctx, "payment_methods", _display_name(), pm, param="payment_method")
    if row["customer"] is None:
        raise invalid_request(
            "The payment method you provided is not attached to a customer "
            "so detachment is impossible.",
            pre_execution=True,
        )
    previous_customer = row["customer"]
    ctx.db.execute("UPDATE payment_methods SET customer = NULL WHERE id = ?", pm)
    fresh = _lookup.require_row(ctx, "payment_methods", _display_name(), pm, param="payment_method")
    body = _serialize(ctx, fresh)
    events.emit_event(
        ctx, type="payment_method.detached", obj=body, previous={"customer": previous_customer}
    )
    return body


SPEC = register(
    ResourceSpec(
        object="payment_method",
        table="payment_methods",
        id_prefix="pm_",
        collection_url="/v1/payment_methods",
        serializer=_serialize,
        columns=(*_FIELDS.columns, "rail"),
        # Probed: a missing payment-method path id names the placeholder
        # (`No such PaymentMethod: 'pm_nope'`, param payment_method), unlike
        # customers and charges which name `id`.
        missing_path_param=None,
        error_name="PaymentMethod",
        list_filters=(
            ListFilter(name="type", column="type", kind="literal", choices=PM_TYPES),
            ListFilter(
                name="allow_redisplay",
                column="allow_redisplay",
                kind="literal",
                choices=("always", "limited", "unspecified"),
            ),
            ListFilter(
                name="customer",
                column="customer",
                kind="exact",
                id_prefixes=CUS,
                references="customer",
                empty_without=True,
            ),
        ),
        creatable=PM_CREATE,
        updatable=PM_UPDATE,
        # No DeleteSpec: Stripe has no DELETE on payment methods; detach is
        # the retirement path.
        delete=None,
        metadata=True,
        before_create=_before_create,
        before_update=_before_update,
        # No `created_event`: real Stripe emits none for payment_method
        # creation (the closed set has attached / automatically_updated /
        # detached / updated only).
        updated_event="payment_method.updated",
    )
)

CUSTOMER_SCOPE = Scope(
    path_param="customer",
    column="customer",
    parent=customers.SPEC,
    # Probed: a bad customer on this path names `param: "id"` — unlike the
    # balance_transactions path, which keeps the placeholder.
    missing_param="id",
)


def _display_name() -> str:
    """The resource's name in Stripe's own messages, read off the registered
    spec so the handlers and the spec cannot disagree on the spelling."""
    return SPEC.error_name or SPEC.object
