"""Scenario 5: the refunds-and-disputes slice, end to end (Phase 9).

Every step is replayable by this world's own tools at the pinned version.
Confirmed intents pin `payment_method_types=["card"]` for the same dashboard
reason scenario 04 documents. The dispute settle pause exists because live
test mode resolves `winning_evidence` asynchronously (~5s): the recording
waits it out so the following GET pins the settled `won` body, while this
world settles synchronously — the declared difference the scenario-scoped
allow-list entries carry.
"""

import time

from tools_dev.scenarios._dsl import ref

SCENARIO = "05_refunds_disputes"
DESCRIPTION = (
    "Refunds and disputes: partial and remainder refunds with the charge "
    "bookkeeping, the over-refund and disputed-charge refusals, the scoped "
    "and legacy charge paths, expand[]=refunds, and the dispute lifecycle "
    "through winning evidence, an inquiry escalation and a close."
)

_EMAIL = "s05@conformance.stripeapi.invalid"

_CONFIRM = {
    "currency": "usd",
    "confirm": True,
    "payment_method_types": ["card"],
}


def record(r):
    customer = r.step(
        "POST",
        "/v1/customers",
        {"email": _EMAIL, "description": "scenario 05", "metadata": {"probe": "s05"}},
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
    confirmed = {
        **_CONFIRM,
        "customer": customer,
        "payment_method": visa,
        "description": "scenario 05 charge",
    }

    # --- refunds: partial, bookkeeping, remainder, refusals ---
    pi_main = r.step(
        "POST", "/v1/payment_intents", {"amount": 5000, **confirmed}, binds_as="pi_main"
    )
    main_charge = ref(pi_main, "latest_charge")
    partial = r.step(
        "POST",
        "/v1/refunds",
        {
            "charge": main_charge,
            "amount": 1500,
            "reason": "duplicate",
            "metadata": {"p": "s05"},
        },
        binds_as="refund_partial",
    )
    r.step("GET", "/v1/charges/{charge}", path_refs={"charge": main_charge})
    r.step(
        "GET",
        "/v1/charges/{charge}",
        {"expand": ["refunds"]},
        path_refs={"charge": main_charge},
    )
    r.step("POST", "/v1/refunds", {"charge": main_charge})
    r.step("POST", "/v1/refunds", {"charge": main_charge, "amount": 1})
    pi_intent = r.step(
        "POST", "/v1/payment_intents", {"amount": 3200, **confirmed}, binds_as="pi_intent"
    )
    by_intent = r.step(
        "POST",
        "/v1/refunds",
        {"payment_intent": pi_intent, "amount": 700},
        binds_as="refund_by_intent",
    )
    r.step("POST", "/v1/refunds", {"payment_intent": pi_intent, "amount": 3000})
    r.step("POST", "/v1/refunds", {"amount": 100})
    r.step("POST", "/v1/refunds", {"charge": "ch_unknown0000000000000000"})
    r.step(
        "POST",
        "/v1/refunds/{refund}",
        {"metadata": {"k": "v"}},
        path_refs={"refund": by_intent},
    )
    r.step("POST", "/v1/refunds/{refund}/cancel", path_refs={"refund": by_intent})
    r.step("GET", "/v1/refunds", {"charge": main_charge, "limit": 100})
    r.step("GET", "/v1/charges/{charge}/refunds", path_refs={"charge": main_charge})
    r.step(
        "GET",
        "/v1/charges/{charge}/refunds/{refund}",
        path_refs={"charge": main_charge, "refund": partial},
    )
    r.step(
        "GET",
        "/v1/charges/{charge}/refunds/{refund}",
        path_refs={"charge": ref(pi_intent, "latest_charge"), "refund": partial},
    )
    # The manual-capture hold, and the refund refusal it answers.
    pi_hold = r.step(
        "POST",
        "/v1/payment_intents",
        {"amount": 3300, "capture_method": "manual", **confirmed},
        binds_as="pi_hold",
    )
    r.step("POST", "/v1/refunds", {"charge": ref(pi_hold, "latest_charge"), "amount": 100})
    # The legacy singular create answers with the charge; the plural with
    # the refund.
    pi_legacy = r.step(
        "POST", "/v1/payment_intents", {"amount": 4100, **confirmed}, binds_as="pi_legacy"
    )
    r.step(
        "POST",
        "/v1/charges/{charge}/refund",
        {"amount": 800},
        path_refs={"charge": ref(pi_legacy, "latest_charge")},
    )
    r.step(
        "POST",
        "/v1/charges/{charge}/refunds",
        {"amount": 300},
        path_refs={"charge": ref(pi_legacy, "latest_charge")},
    )

    # --- disputes: the chargeback card through a win ---
    dispute_card = r.step(
        "POST",
        "/v1/payment_methods",
        {"type": "card", "card": {"token": "tok_visa_createDispute"}},
        binds_as="dispute_card",
    )
    r.step(
        "POST",
        "/v1/payment_methods/{payment_method}/attach",
        {"customer": customer},
        path_refs={"payment_method": dispute_card},
    )
    pi_disputed = r.step(
        "POST",
        "/v1/payment_intents",
        {
            "amount": 6000,
            "currency": "usd",
            "customer": customer,
            "payment_method": dispute_card,
            "confirm": True,
            "payment_method_types": ["card"],
            "description": "scenario 05 disputed",
        },
        binds_as="pi_disputed",
    )
    disputed_charge = ref(pi_disputed, "latest_charge")
    r.step("GET", "/v1/charges/{charge}", path_refs={"charge": disputed_charge})
    r.step("GET", "/v1/disputes", {"charge": disputed_charge}, binds_as="dispute_list")
    dispute = ref("dispute_list", "data[0].id")
    r.step("GET", "/v1/disputes/{dispute}", path_refs={"dispute": dispute})
    r.step("GET", "/v1/charges/{charge}/dispute", path_refs={"charge": disputed_charge})
    r.step("POST", "/v1/refunds", {"charge": disputed_charge, "amount": 100})
    r.step(
        "POST",
        "/v1/disputes/{dispute}",
        {"evidence": {"uncategorized_text": "winning_evidence"}},
        path_refs={"dispute": dispute},
    )
    # Live resolves the magic string asynchronously; wait the settle out so
    # the next step pins the `won` body (this world settles in the call).
    time.sleep(8)
    r.step("GET", "/v1/disputes/{dispute}", path_refs={"dispute": dispute})
    r.step("POST", "/v1/refunds", {"charge": disputed_charge, "amount": 100})

    # A manual-capture hold mints no dispute until the capture (probed in
    # the CR round): confirm, empty dispute list, capture, then the dispute
    # the capture pulls.
    pi_hold_dispute = r.step(
        "POST",
        "/v1/payment_intents",
        {
            "amount": 4400,
            "currency": "usd",
            "customer": customer,
            "payment_method": dispute_card,
            "confirm": True,
            "payment_method_types": ["card"],
            "capture_method": "manual",
            "description": "scenario 05 held dispute",
        },
        binds_as="pi_hold_dispute",
    )
    r.step(
        "GET",
        "/v1/disputes",
        {"charge": ref(pi_hold_dispute, "latest_charge")},
        binds_as="hold_disputes_empty",
    )
    r.step("POST", "/v1/payment_intents/{intent}/capture", path_refs={"intent": pi_hold_dispute})
    r.step(
        "GET",
        "/v1/disputes",
        {"charge": ref(pi_hold_dispute, "latest_charge")},
        binds_as="hold_disputes_one",
    )
    r.step(
        "GET",
        "/v1/disputes/{dispute}",
        path_refs={"dispute": ref("hold_disputes_one", "data[0].id")},
    )

    # --- the inquiry card: refundable, then escalated ---
    inquiry_card = r.step(
        "POST",
        "/v1/payment_methods",
        {"type": "card", "card": {"token": "tok_visa_createDisputeInquiry"}},
        binds_as="inquiry_card",
    )
    r.step(
        "POST",
        "/v1/payment_methods/{payment_method}/attach",
        {"customer": customer},
        path_refs={"payment_method": inquiry_card},
    )
    pi_inquiry = r.step(
        "POST",
        "/v1/payment_intents",
        {
            "amount": 1700,
            "currency": "usd",
            "customer": customer,
            "payment_method": inquiry_card,
            "confirm": True,
            "payment_method_types": ["card"],
            "description": "scenario 05 inquiry",
        },
        binds_as="pi_inquiry",
    )
    r.step(
        "GET",
        "/v1/disputes",
        {"charge": ref(pi_inquiry, "latest_charge")},
        binds_as="inquiry_list",
    )
    inquiry = ref("inquiry_list", "data[0].id")
    r.step("GET", "/v1/disputes/{dispute}", path_refs={"dispute": inquiry})
    r.step(
        "POST",
        "/v1/disputes/{dispute}",
        {"evidence": {"uncategorized_text": "escalate_inquiry_evidence"}},
        path_refs={"dispute": inquiry},
    )

    # --- close, and the already-closed refusals ---
    close_card = r.step(
        "POST",
        "/v1/payment_methods",
        {"type": "card", "card": {"token": "tok_visa_createDispute"}},
        binds_as="close_card",
    )
    r.step(
        "POST",
        "/v1/payment_methods/{payment_method}/attach",
        {"customer": customer},
        path_refs={"payment_method": close_card},
    )
    pi_close = r.step(
        "POST",
        "/v1/payment_intents",
        {
            "amount": 2600,
            "currency": "usd",
            "customer": customer,
            "payment_method": close_card,
            "confirm": True,
            "payment_method_types": ["card"],
            "description": "scenario 05 close",
        },
        binds_as="pi_close",
    )
    r.step(
        "GET",
        "/v1/disputes",
        {"charge": ref(pi_close, "latest_charge")},
        binds_as="close_list",
    )
    closing = ref("close_list", "data[0].id")
    r.step("POST", "/v1/disputes/{dispute}/close", path_refs={"dispute": closing})
    r.step(
        "POST",
        "/v1/disputes/{dispute}",
        {"evidence": {"uncategorized_text": "winning_evidence"}},
        path_refs={"dispute": closing},
    )
    r.step("POST", "/v1/disputes/{dispute}/close", path_refs={"dispute": closing})

    # --- the missing shapes ---
    r.step("GET", "/v1/charges/{charge}/dispute", path_refs={"charge": main_charge})
    r.step("GET", "/v1/disputes/du_nope")


CLEANUP = {"customer": "/v1/customers"}
