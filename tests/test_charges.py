"""The charges half of the money path: the body's constants, the card rail,
the outcome objects, the legacy direct-charge refusals and the lists — all
pinned by the Phase 8 probes and cassette 04 at `2026-08-26.dahlia`."""

import pytest
import seahaven

from conftest import BLANK_NOW

pytestmark = pytest.mark.seahaven(fixture=None, now=BLANK_NOW)


def call(instance: seahaven.Instance, method: str, path: str, params: dict | None = None):
    if method == "GET":
        return instance.call("stripe_api_read", path=path, params=params)
    return instance.call("stripe_api_write", method=method, path=path, params=params)


def confirmed_intent(instance: seahaven.Instance, amount: int = 4900) -> tuple[str, str, dict]:
    cus = call(instance, "POST", "/v1/customers", {"email": "ch@example.test"})["body"]["id"]
    pm = call(
        instance, "POST", "/v1/payment_methods", {"type": "card", "card": {"token": "tok_visa"}}
    )["body"]["id"]
    call(instance, "POST", f"/v1/payment_methods/{pm}/attach", {"customer": cus})
    pi = call(
        instance,
        "POST",
        "/v1/payment_intents",
        {
            "amount": amount,
            "currency": "usd",
            "customer": cus,
            "payment_method": pm,
            "confirm": True,
            "description": "a charge",
        },
    )["body"]
    return cus, pm, pi


# --- the body ---------------------------------------------------------------------------------


def test_the_succeeded_charge_body(instance: seahaven.Instance) -> None:
    cus, pm, pi = confirmed_intent(instance)
    ch = call(instance, "GET", f"/v1/charges/{pi['latest_charge']}")["body"]
    assert ch["object"] == "charge"
    assert ch["amount"] == 4900
    assert ch["amount_captured"] == 4900
    assert ch["amount_refunded"] == 0
    assert ch["currency"] == "usd"
    assert ch["customer"] == cus
    assert ch["description"] == "a charge"
    assert ch["payment_intent"] == pi["id"]
    assert ch["payment_method"] == pm
    assert ch["status"] == "succeeded"
    assert ch["captured"] is True and ch["paid"] is True and ch["refunded"] is False
    assert ch["disputed"] is False
    # the constants the recording pins
    assert ch["calculated_statement_descriptor"] == "Stripe"
    assert ch["fraud_details"] == {}
    assert ch["metadata"] == {}
    # the capture's ledger row, written synchronously (Phase 11)
    assert isinstance(ch["balance_transaction"], str)
    assert ch["balance_transaction"].startswith("txn_")
    assert ch["receipt_url"] == f"https://pay.stripe.com/receipts/payment/{ch['id']}"
    # absent-by-recording: refunds is expand-only at this version
    assert "refunds" not in ch
    assert "radar_options" not in ch
    # the outcome of an approval
    assert ch["outcome"] == {
        "advice_code": None,
        "network_advice_code": None,
        "network_decline_code": None,
        "network_status": "approved_by_network",
        "reason": None,
        "risk_level": "normal",
        "seller_message": "Payment complete.",
        "type": "authorized",
    }


def test_the_card_rail_shape(instance: seahaven.Instance) -> None:
    _, _, pi = confirmed_intent(instance)
    ch = call(instance, "GET", f"/v1/charges/{pi['latest_charge']}")["body"]
    card = ch["payment_method_details"]["card"]
    assert ch["payment_method_details"]["type"] == "card"
    assert card["brand"] == "visa"
    assert card["last4"] == "4242"
    assert card["amount_authorized"] == 4900
    assert card["checks"]["cvc_check"] == "pass"  # verified at charge time (recorded)
    assert card["checks"]["address_line1_check"] is None
    assert card["extended_authorization"] == {"status": "disabled"}
    assert card["incremental_authorization"] == {"status": "unavailable"}
    assert card["multicapture"] == {"status": "unavailable"}
    assert card["network_token"] == {"used": False}
    assert card["overcapture"] == {"maximum_amount_capturable": 4900, "status": "unavailable"}
    assert card["authorization_code"] is None  # network randomness, not modeled
    assert card["network_transaction_id"] is None
    assert "capture_before" not in card  # automatic capture holds no authorization window


def test_a_stubbed_rail_charges_as_the_stub_shape(instance: seahaven.Instance) -> None:
    cus = call(instance, "POST", "/v1/customers", {"email": "stub@example.test"})["body"]["id"]
    pm = call(instance, "POST", "/v1/payment_methods", {"type": "klarna"})["body"]["id"]
    call(instance, "POST", f"/v1/payment_methods/{pm}/attach", {"customer": cus})
    pi = call(
        instance,
        "POST",
        "/v1/payment_intents",
        {"amount": 900, "currency": "usd", "customer": cus, "payment_method": pm, "confirm": True},
    )["body"]
    assert pi["status"] == "succeeded"
    ch = call(instance, "GET", f"/v1/charges/{pi['latest_charge']}")["body"]
    assert ch["payment_method_details"] == {"type": "klarna", "klarna": {}}


def test_the_failed_charge_outcome_and_null_receipt(instance: seahaven.Instance) -> None:
    cus = call(instance, "POST", "/v1/customers", {"email": "dec@example.test"})["body"]["id"]
    declined = call(
        instance,
        "POST",
        "/v1/payment_methods",
        {"type": "card", "card": {"number": "4000000000000341", "exp_month": 9, "exp_year": 2027}},
    )["body"]
    result = call(
        instance,
        "POST",
        "/v1/payment_intents",
        {
            "amount": 1000,
            "currency": "usd",
            "customer": cus,
            "payment_method": declined["id"],
            "confirm": True,
        },
    )
    charge_id = result["body"]["error"]["charge"]
    ch = call(instance, "GET", f"/v1/charges/{charge_id}")["body"]
    assert ch["status"] == "failed"
    assert ch["paid"] is False
    assert ch["captured"] is False
    assert ch["failure_code"] == "card_declined"
    assert ch["failure_message"] == "Your card was declined."
    assert ch["receipt_url"] is None  # failed attempts mint no receipt (recorded)
    card = ch["payment_method_details"]["card"]
    assert card["amount_authorized"] is None  # nothing was authorized (recorded)
    assert "capture_before" not in card
    assert ch["outcome"]["type"] == "issuer_declined"
    assert ch["outcome"]["network_status"] == "declined_by_network"
    assert ch["outcome"]["reason"] == "generic_decline"
    assert ch["outcome"]["seller_message"] == (
        "The bank did not return any further details with this decline."
    )


def test_manual_capture_sets_the_authorization_window(instance: seahaven.Instance) -> None:
    cus, pm, _pi = confirmed_intent(instance)
    pi = call(
        instance,
        "POST",
        "/v1/payment_intents",
        {
            "amount": 6500,
            "currency": "usd",
            "customer": cus,
            "payment_method": pm,
            "confirm": True,
            "capture_method": "manual",
        },
    )["body"]
    ch = call(instance, "GET", f"/v1/charges/{pi['latest_charge']}")["body"]
    card = ch["payment_method_details"]["card"]
    assert card["capture_before"] == ch["created"] + 7 * 24 * 60 * 60


# --- the legacy direct-charge surface -----------------------------------------------------------


def test_the_legacy_charge_refusals_are_verbatim(instance: seahaven.Instance) -> None:
    cus, pm, _pi = confirmed_intent(instance)
    with_pm = call(
        instance,
        "POST",
        "/v1/charges",
        {"amount": 3300, "currency": "usd", "customer": cus, "payment_method": pm},
    )
    assert with_pm["status"] == 402
    error = with_pm["body"]["error"]
    assert error["type"] == "card_error"
    assert error["code"] == "missing"
    assert error["param"] == "card"
    assert error["message"] == (
        "This Customer doesn't have any legacy saved payment details, but does have "
        "a Payment Method attached. Use a Payment Intent instead of creating a "
        "Charge: https://stripe.com/docs/payments/payment-intents/migration"
    )

    bare_customer = call(instance, "POST", "/v1/customers", {"email": "bare@example.test"})["body"]
    without_pm = call(
        instance,
        "POST",
        "/v1/charges",
        {"amount": 1400, "currency": "usd", "customer": bare_customer["id"]},
    )
    assert without_pm["status"] == 402
    assert without_pm["body"]["error"]["code"] == "missing"
    assert without_pm["body"]["error"]["message"] == (
        "This Customer doesn't have any saved payment details. Attach a legacy Token, "
        "Card, Bank Account, or Source to this Customer and then try this request again, "
        "or use Payment Intents and Payment Methods instead."
    )

    end_of_life = call(
        instance, "POST", "/v1/charges", {"amount": 1400, "currency": "usd", "source": "tok_visa"}
    )
    assert end_of_life["status"] == 400
    assert end_of_life["body"]["error"]["message"].startswith(
        "Card Token usage on the Customers API and Charges API has reached end of life."
    )

    no_source = call(instance, "POST", "/v1/charges", {"amount": 1400, "currency": "usd"})
    assert no_source["status"] == 400
    assert no_source["body"]["error"]["code"] == "parameter_missing"
    assert no_source["body"]["error"]["message"] == "Must provide source or customer."

    # A bare payment_method is not a source: the same recorded refusal (probed
    # CR round 1 — it is NOT the customer-with-PM message).
    bare_pm = call(
        instance,
        "POST",
        "/v1/charges",
        {"amount": 1400, "currency": "usd", "payment_method": pm},
    )
    assert bare_pm["status"] == 400
    assert bare_pm["body"]["error"]["code"] == "parameter_missing"
    assert bare_pm["body"]["error"]["message"] == "Must provide source or customer."

    # exactly one charge row exists: the confirmed one
    assert instance.inspect().one("SELECT count(*) AS n FROM charges") == {"n": 1}


def test_charge_level_capture_only_refuses(instance: seahaven.Instance) -> None:
    cus, pm, pi = confirmed_intent(instance)
    captured = call(instance, "POST", f"/v1/charges/{pi['latest_charge']}/capture")
    assert captured["status"] == 400
    assert captured["body"]["error"]["code"] == "charge_already_captured"
    assert captured["body"]["error"]["message"] == (
        f"Charge {pi['latest_charge']} has already been captured."
    )

    hold = call(
        instance,
        "POST",
        "/v1/payment_intents",
        {
            "amount": 2000,
            "currency": "usd",
            "customer": cus,
            "payment_method": pm,
            "confirm": True,
            "capture_method": "manual",
        },
    )["body"]
    directed = call(instance, "POST", f"/v1/charges/{hold['latest_charge']}/capture")
    assert directed["status"] == 400
    assert directed["body"]["error"]["message"] == (
        f"This uncaptured Charge was created by a PaymentIntent ({hold['id']}). "
        "You must capture the PaymentIntent instead. For more information, see "
        "https://stripe.com/docs/payments/place-a-hold-on-a-payment-method"
    )


# --- update, events, lists ------------------------------------------------------------------------


def test_charge_update_moves_metadata_and_description(instance: seahaven.Instance) -> None:
    _, _, pi = confirmed_intent(instance)
    moved = call(
        instance,
        "POST",
        f"/v1/charges/{pi['latest_charge']}",
        {"metadata": {"c": "d"}, "description": "charge updated"},
    )
    assert moved["body"]["metadata"] == {"c": "d"}
    assert moved["body"]["description"] == "charge updated"
    types = [
        row["type"] for row in instance.inspect().rows("SELECT type FROM events ORDER BY x_seq")
    ]
    assert types[-1] == "charge.updated"
    # An unknown customer is the clean resource_missing envelope, not the FK
    # crash of CR round 2 — the same answer the PI update path gives.
    refused = call(instance, "POST", f"/v1/charges/{pi['latest_charge']}", {"customer": "cus_nope"})
    assert refused["status"] == 400
    assert refused["body"]["error"]["code"] == "resource_missing"
    assert refused["body"]["error"]["param"] == "customer"


def test_transfer_group_is_not_an_update_parameter(instance: seahaven.Instance) -> None:
    """The scope cut (data_model §7 rules the field a constant null): the
    parameter is not accepted, so the call answers `parameter_unknown` —
    not the INTERNAL of the crash CR round 1 found, and not real Stripe's
    PI-naming refusal (see the CHARGE_UPDATE comment)."""
    _, _, pi = confirmed_intent(instance)
    refused = call(
        instance, "POST", f"/v1/charges/{pi['latest_charge']}", {"transfer_group": "grp_1"}
    )
    assert refused["status"] == 400
    error = refused["body"]["error"]
    assert error["code"] == "parameter_unknown"
    assert error["param"] == "transfer_group"


def test_lists_filter_by_customer_and_intent(instance: seahaven.Instance) -> None:
    cus, _pm, pi = confirmed_intent(instance)
    other = call(instance, "POST", "/v1/customers", {"email": "o2@example.test"})["body"]["id"]
    pm2 = call(
        instance, "POST", "/v1/payment_methods", {"type": "card", "card": {"token": "tok_visa"}}
    )["body"]["id"]
    call(instance, "POST", f"/v1/payment_methods/{pm2}/attach", {"customer": other})
    call(
        instance,
        "POST",
        "/v1/payment_intents",
        {
            "amount": 700,
            "currency": "usd",
            "customer": other,
            "payment_method": pm2,
            "confirm": True,
        },
    )
    page = call(instance, "GET", "/v1/charges", {"customer": cus})
    assert [item["customer"] for item in page["body"]["data"]] == [cus]
    by_intent = call(instance, "GET", "/v1/charges", {"payment_intent": pi["id"]})
    assert [item["id"] for item in by_intent["body"]["data"]] == [pi["latest_charge"]]


def test_a_missing_charge_keeps_the_id_spelling(instance: seahaven.Instance) -> None:
    got = call(instance, "GET", "/v1/charges/ch_nope")
    error = got["body"]["error"]
    assert error["message"] == "No such charge: 'ch_nope'"
    assert error["param"] == "id"  # probed: charges name `id`, unlike intents
