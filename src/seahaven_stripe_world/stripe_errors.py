"""Stripe's error envelope: the API's own refusals, as return values.

A `StripeApiError` raised out of a handler propagates through `invoke`, the
per-call transaction rolls back, and the Stripe-envelope middleware
(`middleware/stripe_envelope.py`, per `components/cross_cutting.md` §3.5.3)
renders it as `{status, body}`. **Raise loses the writes**: a raised Stripe
error is always a rollback, which is the right behavior for a request that
should never have been accepted — a bad parameter, a missing resource. An
error status a call legitimately *earned* — a `402` decline — keeps its rows,
so it is returned as an `ApiResponse` built around `declined()`, never raised.

The wire `type` enum has exactly four values. `rate_limit_error`,
`authentication_error` and `permission_error` are stripe-*python* conveniences
derived from the HTTP status and are never emitted on the wire — model recall
gets this wrong reliably, and the conformance tests exist to catch it.
"""

from typing import Literal

import seahaven

from seahaven_stripe_world.spec import DECLINE_CODES

__all__ = [
    "ERROR_TYPES",
    "StripeApiError",
    "StripeErrorType",
    "cannot_expand",
    "card_error",
    "declined",
    "idempotency_key_in_use",
    "idempotency_mismatch",
    "internal",
    "invalid_request",
    "missing_parameter",
    "resource_missing",
    "unknown_parameter",
]

StripeErrorType = Literal["api_error", "card_error", "idempotency_error", "invalid_request_error"]

#: The complete wire enum (`research/…/cross-cutting-semantics/errors.md`,
#: "The `type` enum — only 4 wire values").
ERROR_TYPES: frozenset[str] = frozenset(
    ("api_error", "card_error", "idempotency_error", "invalid_request_error")
)

# `doc_url` is emitted only for these two types and only when a `code` is set:
# a coded card or request fault is the thing Stripe links its error-code pages
# from. The hyphenation is Stripe's own (`invalid_parameter` →
# `invalid-parameter`). The exact URL form is conformance allow-list entry 15.
_DOC_URL_TYPES = frozenset(("invalid_request_error", "card_error"))
_DOC_URL = "https://stripe.com/docs/error-codes/"


class StripeApiError(Exception):
    """A Stripe error that abandons the call. Raising one is a rollback.

    The status is carried on the error, not derived from `type`: 400 and 404
    share `invalid_request_error` and nothing in the four-value enum
    distinguishes them. The constructors below are where the pairing is fixed,
    so no handler ever chooses a status by hand.

    ``pre_execution`` marks a fault from *before* the endpoint began — route
    matching, parameter validation. Stripe does not cache these under an
    idempotency key ("you can retry these requests"), and the idempotency layer
    reads this flag to decide: `True` means the key's reservation is retracted.
    """

    def __init__(
        self,
        status: int,
        type: StripeErrorType,
        message: str,
        *,
        code: str | None = None,
        decline_code: str | None = None,
        param: str | None = None,
        doc_url: str | None = None,
        charge: str | None = None,
        advice_code: str | None = None,
        network_advice_code: str | None = None,
        network_decline_code: str | None = None,
        payment_method_type: str | None = None,
        sub_objects: dict[str, dict] | None = None,
        pre_execution: bool = False,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.type: StripeErrorType = type
        self.message = message
        self.code = code
        self.decline_code = decline_code
        self.param = param
        self.charge = charge
        self.advice_code = advice_code
        self.network_advice_code = network_advice_code
        self.network_decline_code = network_decline_code
        self.payment_method_type = payment_method_type
        self.sub_objects = sub_objects
        self.pre_execution = pre_execution
        # The constructor may be handed a doc_url, but the default is derived
        # (code set, doc-bearing type) so no call site has to spell the
        # hyphenation rule out.
        self.doc_url = doc_url if doc_url is not None else self._derived_doc_url(code, type)

    @staticmethod
    def _derived_doc_url(code: str | None, type: StripeErrorType) -> str | None:
        if code is None or type not in _DOC_URL_TYPES:
            return None
        return _DOC_URL + code.replace("_", "-")

    def envelope(self) -> dict:
        """`{"error": {...}}`, omitting every field that is None.

        "The API will omit attributes in error objects when they have a null
        value" (stripe-python's own defaults block, quoted in the errors
        research). Only `type` is required. `request_log_url` is never emitted;
        its absence is conformance allow-list entry 16.
        """
        error: dict = {"type": self.type, "message": self.message}
        for key in (
            "code",
            "decline_code",
            "param",
            "doc_url",
            "charge",
            "advice_code",
            "network_advice_code",
            "network_decline_code",
            "payment_method_type",
        ):
            value = getattr(self, key)
            if value is not None:
                error[key] = value
        if self.sub_objects:
            error.update(self.sub_objects)
        return {"error": error}


# --- Constructors. Handler code uses these; `StripeApiError(...)` is called
# directly nowhere outside this module.


def invalid_request(
    message: str,
    *,
    code: str | None = None,
    param: str | None = None,
    status: int = 400,
    sub_objects: dict[str, dict] | None = None,
    pre_execution: bool = False,
) -> StripeApiError:
    """A 400-shaped request fault. `status` is overridable for the odd 403/405.

    `sub_objects` is the money path's shape: Stripe's
    `payment_intent_unexpected_state` refusals carry the full PaymentIntent on
    the error object (`error.payment_intent`), which is how a client learns
    the intent's state — probed at the pinned version on confirm, capture,
    cancel and the amount-update guard (Phase 8, 2026-09-20).
    """
    return StripeApiError(
        status,
        "invalid_request_error",
        message,
        code=code,
        param=param,
        sub_objects=sub_objects,
        pre_execution=pre_execution,
    )


def resource_missing(
    object_name: str,
    id_: str,
    *,
    param: str | None = None,
    status: int = 404,
) -> StripeApiError:
    """The named object does not exist: 404 on a path id, 400 on a query one.

    No trailing period: live probes return `No such customer: 'cus_…'` verbatim
    (`errors.md` shows the same), so the period the component design carried
    was an invention and `cross_cutting.md` §2.1 is corrected to match. The
    status split is recorded fact too (Phase 5 cassettes, scenario 09): a
    bogus pagination cursor answers 400 `resource_missing`, unlike the 404 a
    path id earns.
    """
    return StripeApiError(
        status,
        "invalid_request_error",
        f"No such {object_name}: '{id_}'",
        code="resource_missing",
        param=param,
    )


def unknown_parameter(param: str) -> StripeApiError:
    """400: Stripe rejects unknown parameters by name, before execution.

    No trailing period — wire-verbatim, unlike `missing_parameter`, whose
    period is corroborated (`gap-closure-2026-09-18.md`).
    """
    return invalid_request(
        f"Received unknown parameter: {param}",
        code="parameter_unknown",
        param=param,
        pre_execution=True,
    )


def missing_parameter(param: str) -> StripeApiError:
    """400: a required parameter absent, before execution."""
    return invalid_request(
        f"Missing required param: {param}.",
        code="parameter_missing",
        param=param,
        pre_execution=True,
    )


def cannot_expand(segment: str, *, exists: bool, hint: str | None = None) -> StripeApiError:
    """400: a bad or non-expandable `expand[]` path. Never silently ignored.

    Three message forms, of which two fire at `2026-08-26.dahlia`: the plain
    form (wire-quoted, `gap-closure-2026-09-18.md` item 4) and the
    list-endpoint hint. The `exists=False` "because it doesn't exist" variant
    never fires on this surface — probed this phase; see
    `serialize/expand.py`'s module docstring for the correction — and every
    call site passes `exists=True`. The branch is retained for the shape the
    research quoted, not because any call site reaches it. No `code` and no
    `param` — the one complete wire body in the research carries neither.
    """
    if not exists:
        message = f"This property cannot be expanded because it doesn't exist: {segment}."
    else:
        message = f"This property cannot be expanded ({segment})."
        if hint is not None:
            message += f" You may want to try expanding '{hint}.{segment}' instead."
    return StripeApiError(400, "invalid_request_error", message, pre_execution=True)


def card_error(
    message: str,
    *,
    code: str | None = None,
    decline_code: str | None = None,
    param: str | None = None,
    charge: str | None = None,
    status: int = 402,
) -> StripeApiError:
    """A 402-shaped card fault that *abandons* the call — raised, so its
    (nonexistent) writes roll back. `declined()` is the keep-the-rows twin;
    the two are distinct because Stripe itself distinguishes them: a legacy
    charge with no card on file is a missing-parameter-shaped `card_error`
    that created nothing, while a decline is an outcome rows were written for."""
    return StripeApiError(
        status,
        "card_error",
        message,
        code=code,
        decline_code=decline_code,
        param=param,
        charge=charge,
    )


def declined(
    *,
    code: str,
    message: str,
    decline_code: str | None = None,
    param: str | None = None,
    charge: str | None = None,
    sub_objects: dict[str, dict] | None = None,
) -> dict:
    """The body of a 402 card decline — an envelope, returned, never raised.

    A decline is an outcome, not an abandonment: the handler has already
    written the failed charge, the PaymentIntent's status change and the
    event pair, and those rows must survive, so the handler pairs this with
    `ApiResponse(402, declined(...))` (`components/cross_cutting.md` §3.5.4).
    Returning a dict rather than raising an exception is what carries the
    rule — there is no `raise` form and a lint test holds that line.

    `param` is None on every PaymentIntent decline (recorded, Phase 8 — the
    correction of §2.1's `param: "payment_method"` default). The one
    recorded decline that carries one is a SetupIntent's expired card
    (`param: "exp_month"`, probed Phase 10); `sub_objects` carries the full
    SetupIntent under `error.setup_intent` the same way the money path
    carries its PaymentIntent (§3.5.1), `charge` the failed charge's id.
    """
    if decline_code is not None and decline_code not in DECLINE_CODES:
        # The §3.4.4 reason: an unlisted decline code is world code inventing
        # behavior Stripe does not have, and grading an agent for it would
        # grade our bug as theirs.
        raise seahaven.WorldBug(f"unknown decline_code {decline_code!r}: not in spec/enums.py")
    return StripeApiError(
        402,
        "card_error",
        message,
        code=code,
        decline_code=decline_code,
        param=param,
        charge=charge,
        sub_objects=sub_objects,
    ).envelope()


def idempotency_mismatch(key: str) -> StripeApiError:
    """400: a stored key replayed with different parameters.

    The message is the wire form probed at the pinned version (Phase 8,
    2026-09-20): the base string cross_cutting §2.1 quoted from client-library
    issues, plus the key hint live Stripe appends — `Try using a key other
    than '<key>' if you meant to execute a different request.`
    """
    return StripeApiError(
        400,
        "idempotency_error",
        "Keys for idempotent requests can only be used with the same parameters "
        f"they were first used with. Try using a key other than '{key}' "
        "if you meant to execute a different request.",
    )


def idempotency_key_in_use(key: str) -> StripeApiError:
    """409: the key belongs to a request that never finished.

    The wire `type` for a 409 is unobserved — stripe-python has no 409 branch —
    so `idempotency_error` is this world's reading, declared as conformance
    allow-list entry 3.
    """
    return StripeApiError(
        409,
        "idempotency_error",
        "There is currently another in-progress request using this Idempotent Key "
        "(that probably means you submitted twice, and the other request is still "
        "going through)",
        code="idempotency_key_in_use",
    )


def internal(message: str) -> StripeApiError:
    """500: an `api_error`. Emitted by nothing in live behavior — see §3.5.5
    of `components/cross_cutting.md` — but the fourth enum value is real and
    the constructor exists so the set is exercisable.
    """
    return StripeApiError(500, "api_error", message)
