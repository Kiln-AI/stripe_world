"""Scenario 4 (functional spec §12): a customer charged, a declined card, an
idempotent retry — plus the money path's transitions around them.

Every step is replayable by this world's own tools at the pinned version.
Confirmed intents pin `payment_method_types=["card"]` because the recording
account's dashboard payment-method configuration answers any confirm without
it with a `return_url` demand this world has no dashboard to satisfy (the
account-config artifacts on those bodies — `automatic_payment_methods`,
`payment_method_configuration_details`, the `link` rail — are allow-listed).
The 3DS on-session flow is deliberately absent: its recorded `next_action`
carries issuer certificates no replica can reproduce (declared structural
difference, unit-tested); the off-session decline beside it is deterministic
and recorded."""

from uuid import uuid4

from tools_dev.scenarios._dsl import ref

#: The idempotency steps embed run-specific ids (the customer, the payment
#: method), so a fixed key would mismatch against the previous recording
#: run's stored entry; each run draws its own.
_RUN = uuid4().hex[:8]

SCENARIO = "04_money_path"
DESCRIPTION = (
    "The money path: PaymentIntent create/confirm/capture/cancel with their "
    "recorded state refusals, the declined card's returned 402, manual "
    "capture with a partial amount, the legacy charge refusals, and the "
    "idempotent create replayed under one key."
)

_EMAIL = "s04@conformance.stripeapi.invalid"


def record(r):
    customer = r.step(
        "POST",
        "/v1/customers",
        {"email": _EMAIL, "description": "scenario 04", "metadata": {"probe": "s04"}},
        binds_as="customer",
    )
    visa = r.step(
        "POST",
        "/v1/payment_methods",
        {"type": "card", "card": {"token": "tok_visa"}},
        binds_as="visa",
    )
    r.step(
        "POST",
        "/v1/payment_methods/{payment_method}/attach",
        {"customer": customer},
        path_refs={"payment_method": visa},
    )
    r.step(
        "POST",
        "/v1/customers/{customer}",
        {"invoice_settings": {"default_payment_method": visa}},
        path_refs={"customer": customer},
    )
    # The plain create: defaults on the wire.
    r.step(
        "POST",
        "/v1/payment_intents",
        {"amount": 4900, "currency": "usd", "metadata": {"p": "s04"}},
        binds_as="pi_plain",
    )
    r.step("GET", "/v1/payment_intents/{intent}", path_refs={"intent": "pi_plain"})
    # Confirmed create under an idempotency key, then the replay and the
    # mismatch — the headline behavior, scenario 4's third clause.
    pi_ok = r.step(
        "POST",
        "/v1/payment_intents",
        {
            "amount": 4900,
            "currency": "usd",
            "customer": customer,
            "payment_method": visa,
            "confirm": True,
            "payment_method_types": ["card"],
            "description": "scenario 04 charge",
        },
        binds_as="pi_ok",
        idempotency_key=f"s04-idem-{_RUN}",
    )
    r.step(
        "POST",
        "/v1/payment_intents",
        {
            "amount": 4900,
            "currency": "usd",
            "customer": customer,
            "payment_method": visa,
            "confirm": True,
            "payment_method_types": ["card"],
            "description": "scenario 04 charge",
        },
        idempotency_key=f"s04-idem-{_RUN}",
    )
    r.step(
        "POST",
        "/v1/payment_intents",
        {
            "amount": 5000,
            "currency": "usd",
            "customer": customer,
            "payment_method": visa,
            "confirm": True,
            "payment_method_types": ["card"],
        },
        idempotency_key=f"s04-idem-{_RUN}",
    )
    r.step(
        "GET",
        "/v1/payment_intents/{intent}",
        {"expand": ["latest_charge"]},
        path_refs={"intent": pi_ok},
    )
    r.step(
        "GET",
        "/v1/charges/{charge}",
        path_refs={"charge": ref(pi_ok, "latest_charge")},
        binds_as="charge_ok",
    )
    # The declined card: an unattached decline PM on a customer intent —
    # the charge is attempted and refused, at 402, rows kept.
    declined = r.step(
        "POST",
        "/v1/payment_methods",
        {"type": "card", "card": {"token": "tok_visa_chargeDeclined"}},
        binds_as="declined",
    )
    r.step(
        "POST",
        "/v1/payment_intents",
        {
            "amount": 1000,
            "currency": "usd",
            "customer": customer,
            "payment_method": declined,
            "confirm": True,
            "payment_method_types": ["card"],
        },
        binds_as="pi_declined",
    )
    r.step(
        "GET",
        "/v1/payment_intents/{intent}",
        path_refs={"intent": ref("pi_declined", "error.payment_intent.id")},
    )
    r.step(
        "GET",
        "/v1/charges/{charge}",
        path_refs={"charge": ref("pi_declined", "error.charge")},
    )
    # Manual capture: the hold, a partial capture, the re-capture refusal,
    # the charge after capture, and an overcapture on a fresh hold.
    pi_manual = r.step(
        "POST",
        "/v1/payment_intents",
        {
            "amount": 6500,
            "currency": "usd",
            "customer": customer,
            "payment_method": visa,
            "confirm": True,
            "capture_method": "manual",
            "payment_method_types": ["card"],
        },
        binds_as="pi_manual",
    )
    r.step(
        "GET",
        "/v1/charges/{charge}",
        path_refs={"charge": ref(pi_manual, "latest_charge")},
    )
    r.step(
        "POST",
        "/v1/payment_intents/{intent}/capture",
        {"amount_to_capture": 5000},
        path_refs={"intent": pi_manual},
    )
    r.step(
        "POST",
        "/v1/payment_intents/{intent}/capture",
        path_refs={"intent": pi_manual},
    )
    r.step(
        "GET",
        "/v1/charges/{charge}",
        path_refs={"charge": ref(pi_manual, "latest_charge")},
    )
    pi_over = r.step(
        "POST",
        "/v1/payment_intents",
        {
            "amount": 5000,
            "currency": "usd",
            "customer": customer,
            "payment_method": visa,
            "confirm": True,
            "capture_method": "manual",
            "payment_method_types": ["card"],
        },
        binds_as="pi_over",
    )
    r.step(
        "POST",
        "/v1/payment_intents/{intent}/capture",
        {"amount_to_capture": 9999},
        path_refs={"intent": pi_over},
    )
    # The capture floor: negative and zero amounts on fresh holds.
    pi_neg = r.step(
        "POST",
        "/v1/payment_intents",
        {
            "amount": 2500,
            "currency": "usd",
            "customer": customer,
            "payment_method": visa,
            "confirm": True,
            "capture_method": "manual",
            "payment_method_types": ["card"],
        },
        binds_as="pi_neg",
    )
    r.step(
        "POST",
        "/v1/payment_intents/{intent}/capture",
        {"amount_to_capture": -100},
        path_refs={"intent": pi_neg},
    )
    pi_zero = r.step(
        "POST",
        "/v1/payment_intents",
        {
            "amount": 2500,
            "currency": "usd",
            "customer": customer,
            "payment_method": visa,
            "confirm": True,
            "capture_method": "manual",
            "payment_method_types": ["card"],
        },
        binds_as="pi_zero",
    )
    r.step(
        "POST",
        "/v1/payment_intents/{intent}/capture",
        {"amount_to_capture": 0},
        path_refs={"intent": pi_zero},
    )
    r.step(
        "POST",
        "/v1/payment_intents/{intent}/capture",
        path_refs={"intent": "pi_plain"},
    )
    # Cancel: echoed reason, then the recorded wrong-state refusals.
    pi_cancel = r.step(
        "POST",
        "/v1/payment_intents",
        {"amount": 1200, "currency": "usd", "customer": customer},
        binds_as="pi_cancel",
    )
    r.step(
        "POST",
        "/v1/payment_intents/{intent}/cancel",
        {"cancellation_reason": "abandoned"},
        path_refs={"intent": pi_cancel},
    )
    r.step(
        "POST",
        "/v1/payment_intents/{intent}/cancel",
        path_refs={"intent": pi_cancel},
    )
    r.step(
        "POST",
        "/v1/payment_intents/{intent}/cancel",
        path_refs={"intent": pi_ok},
    )
    r.step(
        "POST",
        "/v1/payment_intents/{intent}/capture",
        path_refs={"intent": pi_cancel},
    )
    # Confirm with no payment method at all: both recorded message forms.
    pi_bare = r.step(
        "POST",
        "/v1/payment_intents",
        {"amount": 800, "currency": "usd", "payment_method_types": ["card"]},
        binds_as="pi_bare",
    )
    r.step(
        "POST",
        "/v1/payment_intents/{intent}/confirm",
        path_refs={"intent": pi_bare},
    )
    customer2 = r.step(
        "POST",
        "/v1/customers",
        {"email": "s04-b@conformance.stripeapi.invalid"},
        binds_as="customer2",
    )
    pi_c = r.step(
        "POST",
        "/v1/payment_intents",
        {
            "amount": 800,
            "currency": "usd",
            "customer": customer2,
            "payment_method_types": ["card"],
        },
        binds_as="pi_c",
    )
    r.step(
        "POST",
        "/v1/payment_intents/{intent}/confirm",
        path_refs={"intent": pi_c},
    )
    # The ownership corners (probed CR round 1): a customerless intent
    # confirmed with a customer's method is refused; a fresh unattached
    # method on a customerless intent charges.
    pi_bare2 = r.step(
        "POST",
        "/v1/payment_intents",
        {"amount": 1500, "currency": "usd", "payment_method_types": ["card"]},
        binds_as="pi_bare2",
    )
    r.step(
        "POST",
        "/v1/payment_intents/{intent}/confirm",
        {"payment_method": visa},
        path_refs={"intent": pi_bare2},
    )
    fresh = r.step(
        "POST",
        "/v1/payment_methods",
        {"type": "card", "card": {"token": "tok_visa"}},
        binds_as="fresh",
    )
    pi_bare3 = r.step(
        "POST",
        "/v1/payment_intents",
        {"amount": 1500, "currency": "usd", "payment_method_types": ["card"]},
        binds_as="pi_bare3",
    )
    r.step(
        "POST",
        "/v1/payment_intents/{intent}/confirm",
        {"payment_method": fresh},
        path_refs={"intent": pi_bare3},
    )
    # The wrong-customer corner: customer2's intent confirmed with the
    # customer's own method.
    pi_wc = r.step(
        "POST",
        "/v1/payment_intents",
        {
            "amount": 1500,
            "currency": "usd",
            "customer": customer2,
            "payment_method_types": ["card"],
        },
        binds_as="pi_wc",
    )
    r.step(
        "POST",
        "/v1/payment_intents/{intent}/confirm",
        {"payment_method": visa},
        path_refs={"intent": pi_wc},
    )
    # The 3DS card, off-session: the deterministic authentication decline.
    three_ds = r.step(
        "POST",
        "/v1/payment_methods",
        {"type": "card", "card": {"token": "tok_threeDSecure2Required"}},
        binds_as="three_ds",
    )
    r.step(
        "POST",
        "/v1/payment_intents",
        {
            "amount": 3300,
            "currency": "usd",
            "customer": customer,
            "payment_method": three_ds,
            "confirm": True,
            "off_session": True,
            "payment_method_types": ["card"],
        },
    )
    # Updates: what may move after the fact, and what may not.
    r.step(
        "POST",
        "/v1/payment_intents/{intent}",
        {"metadata": {"u": "v"}, "description": "after"},
        path_refs={"intent": pi_ok},
    )
    r.step(
        "POST",
        "/v1/payment_intents/{intent}",
        {"amount": 9999},
        path_refs={"intent": pi_ok},
    )
    r.step(
        "POST",
        "/v1/payment_intents/{intent}",
        {"amount": 5100},
        path_refs={"intent": "pi_plain"},
    )
    r.step(
        "POST",
        "/v1/charges/{charge}",
        {"metadata": {"c": "d"}, "description": "charge updated"},
        path_refs={"charge": "charge_ok"},
    )
    # The transfer_group scope cut, on the record: real Stripe refuses the
    # update on a PI-created charge; this world has cut the parameter.
    r.step(
        "POST",
        "/v1/charges/{charge}",
        {"transfer_group": "order_42"},
        path_refs={"charge": "charge_ok"},
    )
    # Lists.
    r.step("GET", "/v1/payment_intents", {"customer": customer, "limit": 3})
    r.step("GET", "/v1/charges", {"customer": customer, "limit": 3})
    r.step("GET", "/v1/charges", {"payment_intent": pi_ok})
    # The legacy direct-charge surface: all five refusals — a customer with
    # a PaymentMethod attached, a customer with nothing on file (customer2
    # never got a method), a bare payment_method (probed CR round 1: the
    # same `Must provide source or customer.` as a bare amount), the
    # end-of-life token, and the bare amount.
    r.step(
        "POST",
        "/v1/charges",
        {"amount": 3300, "currency": "usd", "customer": customer, "payment_method": visa},
    )
    r.step(
        "POST",
        "/v1/charges",
        {"amount": 1400, "currency": "usd", "customer": customer2},
    )
    r.step(
        "POST",
        "/v1/charges",
        {"amount": 1400, "currency": "usd", "payment_method": visa},
    )
    r.step(
        "POST",
        "/v1/charges",
        {"amount": 1400, "currency": "usd", "source": "tok_visa"},
    )
    r.step("POST", "/v1/charges", {"amount": 1400, "currency": "usd"})
    r.step(
        "POST",
        "/v1/charges/{charge}/capture",
        path_refs={"charge": "charge_ok"},
    )
    # Zero amount, and the missing-id spellings.
    r.step("POST", "/v1/payment_intents", {"amount": 0, "currency": "usd"})
    r.step("GET", "/v1/payment_intents/pi_nope")
    r.step("GET", "/v1/charges/ch_nope")
    r.step("POST", "/v1/payment_intents/pi_nope/confirm")


CLEANUP = {"customer": "/v1/customers"}
