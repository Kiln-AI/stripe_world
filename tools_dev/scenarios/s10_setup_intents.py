"""Scenario 10: the setup-intents slice, end to end (Phase 10).

Every step is replayable by this world's own tools at the pinned version.
Confirmed setup intents pin `payment_method_types=["card"]` because the
recording account's dashboard configuration answers any confirm without it
with a `return_url` demand (scenario 04's rule, met again here); the one
unpinned create is step one, whose dashboard-filled `payment_method_types`
is the scenario-scoped allow-list entry. The 3DS flow is deliberately
absent: its recorded `next_action` carries issuer certificates no replica
can reproduce (Phase 8's declared structural difference, unit-tested).
"""

from tools_dev.scenarios._dsl import ref

SCENARIO = "10_setup_intents"
DESCRIPTION = (
    "Setup intents: the created body's defaults (the dead usage parameter "
    "included), ownership refusals at create, update and confirm, confirm "
    "success with auto-attach, the returned-402 declines whose rows "
    "survive, the missing-method and wrong-state refusals, cancel with its "
    "reason, verify_microdeposits' refusal, the lists and the missing id."
)

_EMAIL = "s10@conformance.stripeapi.invalid"

_CARD = {"payment_method_types": ["card"]}


def record(r):
    customer = r.step(
        "POST",
        "/v1/customers",
        {"email": _EMAIL, "description": "scenario 10", "metadata": {"probe": "s10"}},
        binds_as="customer",
    )
    other = r.step(
        "POST",
        "/v1/customers",
        {"email": "s10b@" + _EMAIL.split("@", 1)[1], "description": "scenario 10 other"},
        binds_as="other",
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

    # --- creation: defaults on the wire, the dead usage parameter ---
    bare = r.step("POST", "/v1/setup_intents", {}, binds_as="bare")
    r.step("GET", "/v1/setup_intents/{intent}", path_refs={"intent": bare})
    r.step("POST", "/v1/setup_intents", {"usage": "on_session", **_CARD})
    with_pm = r.step(
        "POST",
        "/v1/setup_intents",
        {"customer": customer, "payment_method": visa, **_CARD},
        binds_as="with_pm",
    )

    # --- ownership refusals at create ---
    r.step(
        "POST",
        "/v1/setup_intents",
        {"customer": other, "payment_method": visa, **_CARD},
    )
    r.step("POST", "/v1/setup_intents", {"payment_method": visa, **_CARD})

    # --- confirm: success, then the wrong states on a terminal intent ---
    r.step("POST", "/v1/setup_intents/{intent}/confirm", path_refs={"intent": with_pm})
    r.step("POST", "/v1/setup_intents/{intent}/confirm", path_refs={"intent": with_pm})
    r.step("POST", "/v1/setup_intents/{intent}/cancel", path_refs={"intent": with_pm})
    r.step(
        "POST",
        "/v1/setup_intents/{intent}",
        {"description": "after the fact"},
        path_refs={"intent": with_pm},
    )
    r.step(
        "POST",
        "/v1/setup_intents/{intent}",
        {"payment_method": visa},
        path_refs={"intent": with_pm},
    )
    r.step(
        "POST",
        "/v1/setup_intents/{intent}",
        {"metadata": {"k": "v"}},
        path_refs={"intent": with_pm},
    )

    # --- the missing-method refusal against a customer default ---
    r.step(
        "POST",
        "/v1/customers/{customer}",
        {"invoice_settings": {"default_payment_method": visa}},
        path_refs={"customer": customer},
    )
    with_default = r.step(
        "POST", "/v1/setup_intents", {"customer": customer, **_CARD}, binds_as="with_default"
    )
    r.step("POST", "/v1/setup_intents/{intent}/confirm", path_refs={"intent": with_default})

    # --- confirm success auto-attaches an unattached method ---
    visa2 = r.step(
        "POST",
        "/v1/payment_methods",
        {"type": "card", "card": {"token": "tok_visa"}},
        binds_as="visa2",
    )
    r.step(
        "POST",
        "/v1/setup_intents",
        {"customer": customer, "payment_method": visa2, "confirm": True, **_CARD},
    )
    r.step(
        "GET",
        "/v1/payment_methods/{payment_method}",
        path_refs={"payment_method": visa2},
    )

    # --- the declines: returned 402s whose rows survive ---
    decline_pm = r.step(
        "POST",
        "/v1/payment_methods",
        {"type": "card", "card": {"token": "tok_visa_chargeDeclined"}},
        binds_as="decline_pm",
    )
    declined = r.step(
        "POST",
        "/v1/setup_intents",
        {"payment_method": decline_pm, "confirm": True, **_CARD},
        binds_as="declined",
    )
    r.step(
        "GET",
        "/v1/setup_intents/{intent}",
        path_refs={"intent": ref(declined, "error.setup_intent.id")},
    )
    expired_pm = r.step(
        "POST",
        "/v1/payment_methods",
        {"type": "card", "card": {"token": "tok_chargeDeclinedExpiredCard"}},
        binds_as="expired_pm",
    )
    expired = r.step(
        "POST",
        "/v1/setup_intents",
        {"payment_method": expired_pm, **_CARD},
        binds_as="expired",
    )
    r.step(
        "POST",
        "/v1/setup_intents/{intent}/confirm",
        path_refs={"intent": expired},
    )
    r.step("GET", "/v1/setup_intents/{intent}", path_refs={"intent": expired})

    # --- updates: the mutable set and the ownership refusals ---
    updatable = r.step(
        "POST",
        "/v1/setup_intents",
        {"description": "before", "metadata": {"a": "1"}, **_CARD},
        binds_as="updatable",
    )
    r.step(
        "POST",
        "/v1/setup_intents/{intent}",
        {"description": "after", "metadata": {"b": "2"}},
        path_refs={"intent": updatable},
    )
    r.step(
        "POST",
        "/v1/setup_intents/{intent}",
        {"payment_method": visa},
        path_refs={"intent": updatable},
    )
    r.step(
        "POST",
        "/v1/setup_intents/{intent}",
        {"customer": other},
        path_refs={"intent": updatable},
    )
    r.step(
        "POST",
        "/v1/setup_intents/{intent}",
        {"payment_method": visa},
        path_refs={"intent": updatable},
    )

    # --- confirm-time ownership: both directions ---
    wrong_customer = r.step(
        "POST",
        "/v1/setup_intents",
        {"customer": other, **_CARD},
        binds_as="wrong_customer",
    )
    r.step(
        "POST",
        "/v1/setup_intents/{intent}/confirm",
        {"payment_method": visa},
        path_refs={"intent": wrong_customer},
    )
    no_customer = r.step("POST", "/v1/setup_intents", {**_CARD}, binds_as="no_customer")
    r.step(
        "POST",
        "/v1/setup_intents/{intent}/confirm",
        {"payment_method": visa},
        path_refs={"intent": no_customer},
    )

    # --- cancel: with a payment method, then the wrong states ---
    to_cancel = r.step(
        "POST",
        "/v1/setup_intents",
        {"customer": customer, "payment_method": visa, **_CARD},
        binds_as="to_cancel",
    )
    r.step(
        "POST",
        "/v1/setup_intents/{intent}/cancel",
        {"cancellation_reason": "duplicate"},
        path_refs={"intent": to_cancel},
    )
    r.step("POST", "/v1/setup_intents/{intent}/confirm", path_refs={"intent": to_cancel})
    r.step("POST", "/v1/setup_intents/{intent}/cancel", path_refs={"intent": to_cancel})

    # --- verify_microdeposits refuses, and the reads ---
    r.step(
        "POST",
        "/v1/setup_intents/{intent}/verify_microdeposits",
        {"amounts": [32, 45]},
        path_refs={"intent": updatable},
    )
    r.step("GET", "/v1/setup_intents", {"customer": customer, "limit": 100})
    r.step(
        "GET",
        "/v1/setup_intents",
        {"payment_method": visa, "limit": 100},
    )
    r.step("GET", "/v1/setup_intents/seti_nope000000000000000000")


CLEANUP = {"customer": "/v1/customers"}
