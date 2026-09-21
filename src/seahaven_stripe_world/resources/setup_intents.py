"""The SetupIntent slice: saving a customer's payment credentials for
future use, without an immediate payment (Phase 10).

Every wire shape and refusal here is pinned by live probe at
`2026-08-26.dahlia` (2026-09-20, cassette 10): the created body's card
options default (`request_three_d_secure: "automatic"`, no `installments`
key, unlike the PaymentIntent default), the **dead `usage` parameter**
(`on_session` — and even `bogus` — accepted, ignored, `off_session`
answered every time, create and update alike), the resolution chain
**without** a customer-default fallback (a `customer` whose
`invoice_settings.default_payment_method` is set still refuses with the
missing-method message), the missing-method check running *before* the
status guard (a canceled intent without a payment method answers the
missing-method message; with one, `has been canceled`), the two ownership
spellings (`does not belong to the Customer you supplied` when the intent
names a different customer, `The payment method supplied (…) belongs to`
when it names none — sub-object-free at create, intent-carrying at update
and confirm), confirm's success with auto-attach and its `setatt_` attempt
stub, the returned-402 declines whose rows survive with `last_setup_error`
set and `payment_method` NULLed, the wrong-state family verbatim, and
`verify_microdeposits`' `intent_invalid_state` refusal (microdeposit rails
are out of scope, so the refusal is the whole surface).

Scope cuts (not exercised by the cassette): `confirmation_token`,
`payment_method_data`, `mandate_data`, `return_url`, `use_stripe_sdk`,
`single_use`, `flow_directions`, `allowed_payment_method_types`,
`excluded_payment_method_types`, `on_behalf_of`, `customer_account`
(bodies, and the list query) and `payment_method_configuration` — all
pinned-version parameters this surface answers `parameter_unknown`; only
`flow_directions` is a stored column and the two method-type lists
serializer constants, and no recording shows any of them set live — plus
`client_secret` on confirm, verify_microdeposits and retrieve (this world
confirms server-side only), and the `attach_to_self` list filter;
the 3DS park emits the deterministic
`use_stripe_sdk` stub (the recorded `next_action` carries issuer
certificates — Phase 8's declared structural difference, unit-tested).
`mandate` and `single_use_mandate` stay NULL on the card rail (recorded)
and `latest_attempt` never resolves. And live Stripe burns an *unattached*
method after one setup use (probed: the second use answers "The provided
PaymentMethod was previously used…"); this world tracks no usage count —
the Phase 8 PaymentIntent declaration, applied unchanged, and the cassette
uses each unattached method once so the replay never sees the difference.
"""

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

import seahaven

from seahaven_stripe_world import _ids, _json, _seq
from seahaven_stripe_world.dispatch.params import Param, ParamSpec
from seahaven_stripe_world.dispatch.resource import (
    ListFilter,
    ResourceSpec,
    register,
)
from seahaven_stripe_world.dispatch.response import ApiResponse
from seahaven_stripe_world.resources import _lookup, events, payment_methods
from seahaven_stripe_world.resources.payment_intents import (
    _DECLINE_MESSAGES,
    _behavior_tags,
    _mint_client_secret,
    _payment_method_body,
    _store,
)
from seahaven_stripe_world.serialize.fields import FieldMap, presence_sets, serializer_for
from seahaven_stripe_world.spec import spec_document
from seahaven_stripe_world.stripe_errors import declined, invalid_request

if TYPE_CHECKING:
    from seahaven_stripe_world.dispatch.response import Request

__all__ = ["FIELDS", "SPEC", "cancel", "confirm", "create", "verify_microdeposits"]

CUS = ("cus_",)
PM = ("pm_",)

CANCELLATION_REASONS: tuple[str, ...] = tuple(
    spec_document()["components"]["schemas"]["setup_intent"]["properties"]["cancellation_reason"][
        "enum"
    ]
)

# --- the ParamSpecs -----------------------------------------------------------------

_PAYMENT_METHOD_OPTIONS = Param(
    name="payment_method_options",
    kind="object",
    shape=(
        Param(
            name="card",
            kind="object",
            shape=(
                # The setup-intent card options carry no `installments` key
                # (recorded) and a wider `network` enum with a 3-value
                # `request_three_d_secure` (the spec's own setup shapes).
                Param(name="mandate_options", kind="object", shape=()),
                Param(
                    name="network",
                    kind="literal",
                    choices=(
                        "amex",
                        "cartes_bancaires",
                        "diners",
                        "discover",
                        "eftpos_au",
                        "girocard",
                        "interac",
                        "jcb",
                        "link",
                        "mastercard",
                        "unionpay",
                        "unknown",
                        "visa",
                    ),
                ),
                Param(
                    name="request_three_d_secure",
                    kind="literal",
                    choices=("any", "automatic", "challenge"),
                ),
            ),
        ),
    ),
)

_PAYMENT_METHOD_TYPES = Param(
    name="payment_method_types", kind="array", item=Param(name="", kind="string", max_length=5_000)
)

_DESCRIPTION = Param(
    name="description", kind="string", max_length=5_000, unset_with_empty_string=True
)

#: Accepted and deliberately unread (probed, Phase 10): the parameter is dead
#: at the pinned version — `on_session` and even invalid strings are accepted
#: with 200 and the body answers `off_session` every time. Both `create` and
#: `before_update` pop it so the column keeps its NOT NULL default.
_USAGE = Param(name="usage", kind="string", max_length=5_000)

_ATTACH_TO_SELF = Param(name="attach_to_self", kind="boolean")

SETI_CREATE = ParamSpec(
    op_id="PostSetupIntents",
    body=(
        _ATTACH_TO_SELF,
        Param(
            name="automatic_payment_methods",
            kind="object",
            shape=(
                Param(name="enabled", kind="boolean", required=True),
                Param(name="allow_redirects", kind="literal", choices=("always", "never")),
            ),
        ),
        Param(name="confirm", kind="boolean"),
        Param(name="customer", kind="id", id_prefixes=CUS),
        _DESCRIPTION,
        Param(name="payment_method", kind="id", id_prefixes=PM),
        _PAYMENT_METHOD_OPTIONS,
        _PAYMENT_METHOD_TYPES,
        _USAGE,
    ),
    metadata=True,
)

SETI_UPDATE = ParamSpec(
    op_id="PostSetupIntentsIntent",
    path=("intent",),
    body=(
        _ATTACH_TO_SELF,
        Param(name="customer", kind="id", id_prefixes=CUS),
        _DESCRIPTION,
        Param(name="payment_method", kind="id", id_prefixes=PM),
        _PAYMENT_METHOD_OPTIONS,
        _PAYMENT_METHOD_TYPES,
        _USAGE,
    ),
    metadata=True,
)

SETI_LIST = ParamSpec(
    op_id="GetSetupIntents",
    paginated=True,
)

SETI_RETRIEVE = ParamSpec(
    op_id="GetSetupIntentsIntent",
    path=("intent",),
)

SETI_CONFIRM = ParamSpec(
    op_id="PostSetupIntentsIntentConfirm",
    path=("intent",),
    body=(
        Param(name="payment_method", kind="id", id_prefixes=PM),
        _PAYMENT_METHOD_OPTIONS,
    ),
)

SETI_CANCEL = ParamSpec(
    op_id="PostSetupIntentsIntentCancel",
    path=("intent",),
    body=(Param(name="cancellation_reason", kind="literal", choices=CANCELLATION_REASONS),),
)

SETI_VERIFY_MICRODEPOSITS = ParamSpec(
    op_id="PostSetupIntentsIntentVerifyMicrodeposits",
    path=("intent",),
    body=(
        Param(name="amounts", kind="array", item=Param(name="", kind="integer")),
        Param(name="descriptor_code", kind="string", max_length=5_000),
    ),
)

# --- the serializer -----------------------------------------------------------------

always_present, omit_when_none = presence_sets("setup_intent")

FIELDS = FieldMap(
    object="setup_intent",
    table="setup_intents",
    columns={
        "id": "id",
        "created": "created",
        "automatic_payment_methods": "automatic_payment_methods",
        "cancellation_reason": "cancellation_reason",
        "client_secret": "client_secret",
        "customer": "customer",
        "description": "description",
        "flow_directions": "flow_directions",
        "last_setup_error": "last_setup_error",
        "latest_attempt": "latest_attempt",
        "mandate": "mandate",
        "metadata": "metadata",
        "next_action": "next_action",
        "payment_method": "payment_method",
        "payment_method_options": "payment_method_options",
        "payment_method_types": "payment_method_types",
        "single_use_mandate": "single_use_mandate",
        "status": "status",
        "usage": "usage",
        # `attach_to_self` is a stored column the recorded wire never
        # carries (absent on every recorded body, Phase 10); unmapped, it
        # stays off the wire like an internal column would.
    },
    timestamps=frozenset({"created"}),
    json_columns=frozenset(
        {
            "automatic_payment_methods",
            "flow_directions",
            "last_setup_error",
            "metadata",
            "next_action",
            "payment_method_options",
            "payment_method_types",
        }
    ),
    constants={
        "livemode": False,
        "allowed_payment_method_types": None,
        "application": None,
        "customer_account": None,
        "excluded_payment_method_types": None,
        # The recorded value is `{"enabled": false}` where the pinned spec
        # types the field `string` — the same ruling as the PaymentIntent's
        # field (Phase 8): emit the spec-legal null, allow-list the diff.
        "managed_payments": None,
        "on_behalf_of": None,
        "payment_method_configuration_details": None,
    },
    always_present=always_present,
    omit_when_none=omit_when_none,
)

_serialize = serializer_for(FIELDS)


def serialize(ctx: seahaven.Ctx, row: Mapping[str, Any]) -> dict[str, Any]:
    return _serialize(ctx, row)


#: The recorded card-options default every created SetupIntent carries
#: (probed, Phase 10) — stamped when the caller sent none.
_PM_OPTIONS_DEFAULT: dict[str, object] = {
    "card": {
        "mandate_options": None,
        "network": None,
        "request_three_d_secure": "automatic",
    }
}

# --- the recorded refusal spellings ---------------------------------------------------

_MISSING_METHOD = (
    "You cannot confirm this SetupIntent because it's missing a payment method. "
    "You can either update the SetupIntent with a payment method and then confirm "
    "it again, or confirm it again directly with a payment method or "
    "ConfirmationToken."
)

_CANCEL_ALLOWED = "`requires_payment_method`, `requires_confirmation`, or `requires_action`"


def _refuse_unexpected_state(
    message: str,
    *,
    body: dict[str, Any],
    code: str | None = "setup_intent_unexpected_state",
    param: str | None = None,
) -> None:
    """The wrong-state family, every recorded member of which carries the
    full SetupIntent on the error object."""
    raise invalid_request(message, code=code, param=param, sub_objects={"setup_intent": body})


def _refuse_ownership(
    message: str, *, body: dict[str, Any] | None, pm_id: str, customer_id: str
) -> None:
    """The two ownership spellings (both recorded at create, update and
    confirm): sub-object-free when the call is a create (nothing exists to
    carry), intent-carrying otherwise. No code on either."""
    raise invalid_request(
        message.format(pm=pm_id, cus=customer_id),
        param="payment_method",
        sub_objects=None if body is None else {"setup_intent": body},
    )


#: "…does not belong…" — the intent names a customer the method is not
#: attached to.
_DOES_NOT_BELONG = (
    "The PaymentMethod {pm} does not belong to the Customer you supplied {cus}. "
    "Please use this PaymentMethod with the Customer that it belongs to instead."
)

#: "…belongs to…" — the intent names no customer and the method is attached
#: to one.
_SUPPLIED_BELONGS = (
    "The payment method supplied ({pm}) belongs to the Customer {cus}. "
    "Please include the Customer in the `customer` parameter on the SetupIntent."
)


def _guard_ownership(
    ctx: seahaven.Ctx,
    pm_row: Mapping[str, Any],
    intent_customer: str | None,
    *,
    body: dict[str, Any] | None,
) -> None:
    if pm_row["customer"] is None or pm_row["customer"] == intent_customer:
        return
    if intent_customer is None:
        _refuse_ownership(
            _SUPPLIED_BELONGS, body=body, pm_id=pm_row["id"], customer_id=pm_row["customer"]
        )
    else:
        _refuse_ownership(
            _DOES_NOT_BELONG, body=body, pm_id=pm_row["id"], customer_id=intent_customer
        )


# --- the confirm transition -----------------------------------------------------------


def _re_read(ctx: seahaven.Ctx, id_: str) -> dict[str, Any]:
    # The hand-written paths' display name: one word, unlike the snake-cased
    # discriminator (recorded, cassette 10 — the SPEC's own `error_name`).
    return _lookup.require_row(ctx, "setup_intents", "setupintent", id_, param="intent")


def _mint_attempt(ctx: seahaven.Ctx) -> str:
    """The `setatt_…` stub every confirm attempt mints (recorded); the
    object behind it is out of scope and never resolves."""
    return _ids.stripe_id(ctx, "setatt_")


def _confirm(
    ctx: seahaven.Ctx, row: Mapping[str, Any], params: Mapping[str, Any]
) -> dict[str, Any] | ApiResponse:
    """The confirm transition shared by create-with-confirm and the confirm
    endpoint. Returns the serialized intent (200) or an `ApiResponse` 402 —
    the decline outcome whose rows must survive."""
    # Resolution is parameter → the intent's own column, and nothing else:
    # a customer's default payment method is NOT consulted (recorded,
    # cassette 10 — unlike the PaymentIntent chain).
    pm_id = params.get("payment_method") or row["payment_method"]
    if pm_id is None:
        # Recorded: the missing-method check outranks the status guard — a
        # canceled intent without a method answers this message, not the
        # canceled one.
        _refuse_unexpected_state(_MISSING_METHOD, body=serialize(ctx, row))
    pm_row = _lookup.require_live_row(
        ctx, "payment_methods", "PaymentMethod", pm_id, param="payment_method"
    )
    status = row["status"]
    if status == "succeeded":
        _refuse_unexpected_state(
            "You cannot confirm this SetupIntent because it has already succeeded.",
            body=serialize(ctx, row),
        )
    if status == "canceled":
        _refuse_unexpected_state(
            "You cannot confirm this SetupIntent because it has been canceled.",
            body=serialize(ctx, row),
        )
    if status == "processing":
        # Unreachable (no transition in this world writes it) and unrecorded;
        # the status-list spelling the cancel family recorded, confirm-shaped.
        _refuse_unexpected_state(
            "You cannot confirm this SetupIntent because it has a status of processing. "
            "Only a SetupIntent with one of the following statuses may be confirmed: "
            "requires_payment_method, requires_confirmation, requires_action.",
            body=serialize(ctx, row),
        )
    _guard_ownership(ctx, pm_row, row["customer"], body=serialize(ctx, row))

    tags = _behavior_tags(pm_row)
    if tags.get("three_d_secure") == "required":
        # The 3DS park (recorded: 200, requires_action, latest_attempt
        # minted, no charge); the recorded next_action carries issuer
        # certificates no replica can reproduce, so this world emits the
        # deterministic stub — Phase 8's declared difference.
        ctx.db.execute(
            "UPDATE setup_intents SET status = 'requires_action', payment_method = ?, "
            "next_action = ?, latest_attempt = ? WHERE id = ?",
            pm_row["id"],
            _json.dumps({"type": "use_stripe_sdk", "use_stripe_sdk": {}}),
            _mint_attempt(ctx),
            row["id"],
        )
        body = serialize(ctx, _re_read(ctx, row["id"]))
        events.emit_event(ctx, type="setup_intent.requires_action", obj=body)
        return body

    if "charge_declined" in tags:
        code = tags["charge_declined"]
        # Recorded (cassette 10): the setup decline's decline_code falls back
        # to the code itself when the tag carries none (expired_card answers
        # decline_code "expired_card", not null), and the expired-card
        # envelope alone carries param "exp_month".
        decline_code = tags.get("decline_code") or code
        message = _DECLINE_MESSAGES.get((code, tags.get("decline_code")))
        if message is None:
            raise seahaven.WorldBug(
                f"no recorded message for decline ({code!r}, {tags.get('decline_code')!r}): "
                "add the card's message to _DECLINE_MESSAGES"
            )
        pm_body = _payment_method_body(ctx, pm_row)
        error = {
            "code": code,
            "decline_code": decline_code,
            "doc_url": f"https://stripe.com/docs/error-codes/{code.replace('_', '-')}",
            "message": message,
            "payment_method": pm_body,
            "type": "card_error",
        }
        param: str | None = "exp_month" if code == "expired_card" else None
        if param is not None:
            error["param"] = param
        ctx.db.execute(
            "UPDATE setup_intents SET status = 'requires_payment_method', "
            "payment_method = NULL, last_setup_error = ?, latest_attempt = ? WHERE id = ?",
            _json.dumps(error),
            _mint_attempt(ctx),
            row["id"],
        )
        body = serialize(ctx, _re_read(ctx, row["id"]))
        events.emit_event(ctx, type="setup_intent.setup_failed", obj=body)
        # Returned, not raised: the failed attempt's rows survive (the
        # raise-loses rule, `components/cross_cutting.md` §3.5.4).
        return ApiResponse(
            402,
            declined(
                code=code,
                decline_code=decline_code,
                param=param,
                message=message,
                sub_objects={"setup_intent": body, "payment_method": pm_body},
            ),
        )

    # Success: auto-attach first (the recorded cvc flip rides the attach),
    # then the terminal row, then the event — the attach happens as part of
    # the setup, before the intent's own terminal event.
    if row["customer"] is not None and pm_row["customer"] is None:
        ctx.db.execute(
            "UPDATE payment_methods SET customer = ?, rail = ? WHERE id = ?",
            row["customer"],
            payment_methods._verified_by_attach(pm_row["rail"]),
            pm_row["id"],
        )
        events.emit_event(
            ctx,
            type="payment_method.attached",
            obj=payment_methods._serialize(
                ctx,
                _lookup.require_row(
                    ctx, "payment_methods", "PaymentMethod", pm_row["id"], param="payment_method"
                ),
            ),
        )
    ctx.db.execute(
        "UPDATE setup_intents SET status = 'succeeded', payment_method = ?, "
        "next_action = NULL, latest_attempt = ? WHERE id = ?",
        pm_row["id"],
        _mint_attempt(ctx),
        row["id"],
    )
    body = serialize(ctx, _re_read(ctx, row["id"]))
    events.emit_event(ctx, type="setup_intent.succeeded", obj=body)
    # A resume-flow SetupIntent applies its parked subscription update when
    # it confirms (Phase 12): the documented mechanism — updates land once
    # the customer completes setup. Function-level import: the lifecycle
    # imports this module's neighbors, not this one, but the dodge keeps the
    # billing package's import graph one-directional at module scope.
    from seahaven_stripe_world.billing import subscription_lifecycle

    subscription_lifecycle.apply_pending_update_on_seti_success(ctx, row["id"])
    return body


# --- the handlers --------------------------------------------------------------------


def create(ctx: seahaven.Ctx, req: Request) -> dict[str, Any] | ApiResponse:
    """`POST /v1/setup_intents`: the row plus, when `confirm` was sent, the
    confirm transition — hand-written for the same reason the
    PaymentIntent's create is (the transition writes a second table and
    emits, past the normalizer contract twice over)."""
    params = dict(req.params)
    if "customer" in params:
        _lookup.require_live_row(ctx, "customers", "customer", params["customer"], param="customer")
    if "payment_method" in params:
        pm_row = _lookup.require_live_row(
            ctx,
            "payment_methods",
            "PaymentMethod",
            params["payment_method"],
            param="payment_method",
        )
        # Recorded (cassette 10): the create-time ownership refusals carry no
        # intent — nothing exists to carry.
        _guard_ownership(ctx, pm_row, params.get("customer"), body=None)
    should_confirm = params.pop("confirm", None) is True
    params.pop("usage", None)  # the dead parameter (probed, Phase 10)
    id_ = _ids.stripe_id(ctx, "seti_")
    cols: dict[str, Any] = {
        "id": id_,
        "x_seq": _seq.next_seq(ctx, "setup_intents"),
        "created": ctx.clock.iso(),
        "client_secret": _mint_client_secret(ctx, id_),
        "status": (
            "requires_confirmation" if params.get("payment_method") else "requires_payment_method"
        ),
    }
    cols.update({key: _store(value) for key, value in params.items()})
    cols.setdefault("payment_method_types", '["card"]')
    cols.setdefault("payment_method_options", _json.dumps(_PM_OPTIONS_DEFAULT))
    if req.metadata is not None:
        cols["metadata"] = _json.dumps(dict(req.metadata.apply({})))
    columns = ", ".join(cols)
    placeholders = ", ".join("?" for _ in cols)
    ctx.db.execute(f"INSERT INTO setup_intents ({columns}) VALUES ({placeholders})", *cols.values())
    row = _re_read(ctx, id_)
    # Emitted before any confirm transition, snapshotting the created state
    # (the recorded create+confirm's `created` snapshot carries the
    # post-failure last_setup_error — an async-emission artifact this world
    # does not reproduce; the Phase 8 PaymentIntent precedent).
    events.emit_event(ctx, type="setup_intent.created", obj=serialize(ctx, row))
    if should_confirm:
        return _confirm(ctx, row, req.params)
    return serialize(ctx, row)


def confirm(ctx: seahaven.Ctx, req: Request) -> dict[str, Any] | ApiResponse:
    """`POST /v1/setup_intents/{intent}/confirm`: apply the confirm-time
    `payment_method`, then run the transition."""
    row = _re_read(ctx, req.path_params["intent"])
    # `payment_method` is excluded from the persisted update (the transition
    # owns that column; a refused confirm leaves it untouched) while
    # `payment_method_options` persists like any other column.
    sets = {key: _store(value) for key, value in req.params.items() if key != "payment_method"}
    if sets:
        assignments = ", ".join(f"{column} = ?" for column in sets)
        ctx.db.execute(
            f"UPDATE setup_intents SET {assignments} WHERE id = ?", *sets.values(), row["id"]
        )
        row = _re_read(ctx, row["id"])
    return _confirm(ctx, row, req.params)


def cancel(ctx: seahaven.Ctx, req: Request) -> dict[str, Any]:
    """`POST /v1/setup_intents/{intent}/cancel`: stamps the caller's reason
    (recorded: `null` when omitted); the payment method is kept (recorded,
    cassette 10 — cancel drops nothing but the open state)."""
    row = _re_read(ctx, req.path_params["intent"])
    if row["status"] == "canceled":
        _refuse_unexpected_state(
            "You cannot cancel this SetupIntent because it is already canceled.",
            body=serialize(ctx, row),
        )
    if row["status"] not in ("requires_payment_method", "requires_confirmation", "requires_action"):
        # Recorded verbatim, backticks and all.
        _refuse_unexpected_state(
            "You cannot cancel this SetupIntent because it has a status of "
            f"{row['status']}. Only a SetupIntent with one of the following statuses "
            f"may be canceled: {_CANCEL_ALLOWED}.",
            body=serialize(ctx, row),
        )
    ctx.db.execute(
        "UPDATE setup_intents SET status = 'canceled', cancellation_reason = ?, "
        "next_action = NULL WHERE id = ?",
        req.params.get("cancellation_reason"),
        row["id"],
    )
    body = serialize(ctx, _re_read(ctx, row["id"]))
    events.emit_event(ctx, type="setup_intent.canceled", obj=body)
    return body


def verify_microdeposits(ctx: seahaven.Ctx, req: Request) -> dict[str, Any]:
    """`POST /v1/setup_intents/{intent}/verify_microdeposits`: refused on
    every intent this world can mint (recorded verbatim, the JSON array in
    the message included). Microdeposit rails are out of scope, so the
    refusal — which interpolates the intent's real status — is the whole
    surface; the one status that could pass live (`requires_action`) arises
    here only from 3DS cards, which have no microdeposits to verify."""
    row = _re_read(ctx, req.path_params["intent"])
    raise invalid_request(
        "This SetupIntent cannot be actioned on because it has a status of "
        f"{row['status']}. Only a SetupIntent with one of the following statuses may "
        'be actioned on: ["requires_action"].',
        code="intent_invalid_state",
        sub_objects={"setup_intent": serialize(ctx, row)},
    )


# --- the engine-served update ---------------------------------------------------------


def _before_update(ctx: seahaven.Ctx, req: Request, sets: dict[str, object]) -> dict[str, object]:
    """The reference lookups, the `payment_method` guards and the dead
    `usage` the engine does not know about. Description, metadata and
    customer stay updatable in any status (recorded, cassette 10); only
    `payment_method` is terminal-state refused."""
    row = _re_read(ctx, req.path_params["intent"])
    sets.pop("usage", None)  # the dead parameter (probed, Phase 10)
    if "customer" in sets:
        _lookup.require_live_row(
            ctx, "customers", "customer", str(sets["customer"]), param="customer"
        )
    if "payment_method" in sets:
        body = serialize(ctx, row)
        if row["status"] == "succeeded":
            _refuse_unexpected_state(
                "You cannot update this SetupIntent because it has already succeeded.",
                body=body,
            )
        if row["status"] == "canceled":
            # Unrecorded (cassette 10 records only the succeeded spelling);
            # the confirm family's canceled wording, applied to the update.
            _refuse_unexpected_state(
                "You cannot update this SetupIntent because it has been canceled.",
                body=body,
            )
        pm_row = _lookup.require_live_row(
            ctx,
            "payment_methods",
            "PaymentMethod",
            str(sets["payment_method"]),
            param="payment_method",
        )
        _guard_ownership(
            ctx,
            pm_row,
            str(sets["customer"]) if "customer" in sets else row["customer"],
            body=body,
        )
        if row["status"] == "requires_payment_method":
            # Unrecorded (no cassette step updates a method successfully);
            # the create rule applied to the update — a method placed on an
            # open intent makes it confirmable.
            sets["status"] = "requires_confirmation"
    return sets


SPEC = register(
    ResourceSpec(
        object="setup_intent",
        table="setup_intents",
        id_prefix="seti_",
        collection_url="/v1/setup_intents",
        serializer=serialize,
        columns=tuple(FIELDS.columns),
        # Probed (cassette 10): the paths keep the `intent` placeholder and
        # the message says "No such setupintent" — one word, unlike the
        # PaymentIntent's snake case.
        missing_path_param=None,
        error_name="setupintent",
        list_filters=(
            ListFilter(
                name="customer",
                column="customer",
                kind="exact",
                id_prefixes=CUS,
                references="customer",
            ),
            ListFilter(
                name="payment_method",
                column="payment_method",
                kind="exact",
                id_prefixes=PM,
                references="payment_method",
            ),
            ListFilter(name="created", column="created", kind="range"),
        ),
        creatable=SETI_CREATE,
        updatable=SETI_UPDATE,
        delete=None,
        metadata=True,
        before_update=_before_update,
        # The hand-written create emits setup_intent.created itself, before
        # any confirm transition; there is no setup_intent.updated in the
        # closed set, so the engine emits none either.
        created_event=None,
        updated_event=None,
    )
)
