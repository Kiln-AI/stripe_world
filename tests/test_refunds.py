"""The refunds half of Phase 9: the body's shape, the bookkeeping, the
recorded refusals, the lists and scoped paths, the legacy alias, and
`expand[]=refunds` — pinned by the Phase 9 probes and cassette 05 at
`2026-08-26.dahlia`."""

import pytest
import seahaven

from conftest import BLANK_NOW

pytestmark = pytest.mark.seahaven(fixture=None, now=BLANK_NOW)


def call(instance: seahaven.Instance, method: str, path: str, params: dict | None = None):
    if method == "GET":
        return instance.call("stripe_api_read", path=path, params=params)
    return instance.call("stripe_api_write", method=method, path=path, params=params)


def confirmed_intent(instance: seahaven.Instance, amount: int = 5000, **extra) -> dict:
    cus = call(instance, "POST", "/v1/customers", {"email": "rf@example.test"})["body"]["id"]
    pm = call(
        instance, "POST", "/v1/payment_methods", {"type": "card", "card": {"token": "tok_visa"}}
    )["body"]["id"]
    call(instance, "POST", f"/v1/payment_methods/{pm}/attach", {"customer": cus})
    return call(
        instance,
        "POST",
        "/v1/payment_intents",
        {
            "amount": amount,
            "currency": "usd",
            "customer": cus,
            "payment_method": pm,
            "confirm": True,
            **extra,
        },
    )["body"]


def events_of(instance: seahaven.Instance) -> list[str]:
    return [
        row["type"] for row in instance.inspect().rows("SELECT type FROM events ORDER BY x_seq")
    ]


# --- the body and the bookkeeping ------------------------------------------------------


def test_the_refund_body(instance: seahaven.Instance) -> None:
    pi = confirmed_intent(instance)
    refund = call(
        instance,
        "POST",
        "/v1/refunds",
        {
            "charge": pi["latest_charge"],
            "amount": 1500,
            "reason": "duplicate",
            "metadata": {"p": "9"},
        },
    )["body"]
    assert refund["object"] == "refund"
    assert refund["amount"] == 1500
    assert refund["charge"] == pi["latest_charge"]
    assert refund["payment_intent"] == pi["id"]
    assert refund["payment_method"] == pi["payment_method"]
    assert refund["customer"] == pi["customer"]
    assert refund["currency"] == "usd"
    assert refund["status"] == "succeeded"
    assert refund["reason"] == "duplicate"
    assert refund["metadata"] == {"p": "9"}
    assert "livemode" not in refund  # one of the four objects without it
    # absent while valueless (recorded, cassette 05)
    for absent in (
        "description",
        "failure_reason",
        "instructions_email",
        "next_action",
        "presentment_details",
        "pending_reason",
    ):
        assert absent not in refund
    # the always-present nullables
    assert refund["receipt_number"] is None
    assert refund["customer_account"] is None
    assert refund["transfer_reversal"] is None
    assert refund["source_transfer_reversal"] is None
    assert refund["balance_transaction"] is None  # the ledger is Phase 11's
    assert refund["destination_details"] == {
        "card": {
            "reference_status": "pending",
            "reference_type": "acquirer_reference_number",
            "type": "refund",
        },
        "type": "card",
    }


def test_partial_then_remainder_then_refused(instance: seahaven.Instance) -> None:
    pi = confirmed_intent(instance, 5000)
    ch = pi["latest_charge"]
    first = call(instance, "POST", "/v1/refunds", {"charge": ch, "amount": 1500})["body"]
    assert first["amount"] == 1500
    charge = call(instance, "GET", f"/v1/charges/{ch}")["body"]
    assert charge["amount_refunded"] == 1500
    assert charge["refunded"] is False
    assert "refunds" not in charge  # expand-only at this version
    second = call(instance, "POST", "/v1/refunds", {"charge": ch})["body"]
    assert second["amount"] == 3500  # the remainder, without an amount
    charge = call(instance, "GET", f"/v1/charges/{ch}")["body"]
    assert charge["amount_refunded"] == 5000
    assert charge["refunded"] is True
    error = call(instance, "POST", "/v1/refunds", {"charge": ch, "amount": 1})
    assert error["status"] == 400
    assert error["body"]["error"]["code"] == "charge_already_refunded"
    assert error["body"]["error"]["message"] == f"Charge {ch} has already been refunded."


def test_the_over_refund_on_a_partial_charge_is_the_recorded_form(
    instance: seahaven.Instance,
) -> None:
    pi = confirmed_intent(instance, 8000)
    call(instance, "POST", "/v1/refunds", {"charge": pi["latest_charge"], "amount": 5000})
    error = call(instance, "POST", "/v1/refunds", {"charge": pi["latest_charge"], "amount": 4000})
    assert error["status"] == 400
    err = error["body"]["error"]
    assert err["type"] == "invalid_request_error"
    assert err["param"] == "amount"
    assert "code" not in err
    assert err["message"] == (
        "Refund amount ($40.00) is greater than unrefunded amount on charge ($30.00)"
    )


def test_the_events_are_the_recorded_pair_in_order(instance: seahaven.Instance) -> None:
    pi = confirmed_intent(instance, 1000)
    call(instance, "POST", "/v1/refunds", {"charge": pi["latest_charge"], "amount": 400})
    kinds = events_of(instance)
    assert kinds[-2:] == ["refund.created", "charge.refunded"]
    # `charge.refunded` carries the charge with its fresh bookkeeping.
    assert instance.inspect().one(
        "SELECT data->>'$.object.object' AS kind,"
        " CAST(data->>'$.object.amount_refunded' AS INTEGER) AS refunded"
        " FROM events WHERE type = 'charge.refunded'"
    ) == {"kind": "charge", "refunded": 400}


# --- the refusals ----------------------------------------------------------------------


def test_zero_and_negative_amounts_refuse_the_recorded_form(instance: seahaven.Instance) -> None:
    pi = confirmed_intent(instance, 2000)
    for bad in (0, -5):
        error = call(
            instance, "POST", "/v1/refunds", {"charge": pi["latest_charge"], "amount": bad}
        )
        assert error["status"] == 400
        assert error["body"]["error"]["code"] == "parameter_invalid_integer"
        assert error["body"]["error"]["param"] == "amount"
        assert error["body"]["error"]["message"] == "This value must be greater than or equal to 1."


def test_neither_charge_nor_intent_refuses_with_the_recorded_message(
    instance: seahaven.Instance,
) -> None:
    error = call(instance, "POST", "/v1/refunds", {"amount": 100})
    assert error["status"] == 400
    err = error["body"]["error"]
    assert err["message"] == (
        "One of the following params should be provided for this request: payment_intent or charge."
    )
    assert "code" not in err and "param" not in err


def test_unknown_charge_names_param_id(instance: seahaven.Instance) -> None:
    error = call(instance, "POST", "/v1/refunds", {"charge": "ch_missing00000000000000000"})
    assert error["status"] == 404
    err = error["body"]["error"]
    assert err["code"] == "resource_missing"
    assert err["param"] == "id"
    assert err["message"] == "No such charge: 'ch_missing00000000000000000'"


def test_reason_is_create_limited_to_the_caller_enum(instance: seahaven.Instance) -> None:
    pi = confirmed_intent(instance, 1200)
    error = call(
        instance,
        "POST",
        "/v1/refunds",
        {"charge": pi["latest_charge"], "reason": "expired_uncaptured_charge"},
    )
    assert error["status"] == 400
    err = error["body"]["error"]
    assert err["param"] == "reason"
    assert err["message"] == (
        "Invalid reason: must be one of duplicate, fraudulent, or requested_by_customer"
    )


def test_the_uncaptured_hold_refuses_with_the_recorded_message(instance: seahaven.Instance) -> None:
    pi = confirmed_intent(instance, 3300, capture_method="manual")
    error = call(instance, "POST", "/v1/refunds", {"charge": pi["latest_charge"], "amount": 100})
    assert error["status"] == 400
    err = error["body"]["error"]
    assert err["message"] == (
        f"This uncaptured Charge was created by a PaymentIntent ({pi['id']}). "
        "You must cancel the PaymentIntent to reverse the authorization instead of refunding "
        "the Charge directly. For more information, see "
        "https://stripe.com/docs/payments/place-a-hold-on-a-payment-method"
    )


def test_a_failed_charge_refuses_naming_its_intent(instance: seahaven.Instance) -> None:
    cus = call(instance, "POST", "/v1/customers", {"email": "f@example.test"})["body"]["id"]
    declined = call(
        instance,
        "POST",
        "/v1/payment_methods",
        {"type": "card", "card": {"token": "tok_visa_chargeDeclinedInsufficientFunds"}},
    )["body"]
    call(instance, "POST", f"/v1/payment_methods/{declined['id']}/attach", {"customer": cus})
    error = call(
        instance,
        "POST",
        "/v1/payment_intents",
        {
            "amount": 1500,
            "currency": "usd",
            "customer": cus,
            "payment_method": declined["id"],
            "confirm": True,
        },
    )
    failed_charge = error["body"]["error"]["charge"]
    refused = call(instance, "POST", "/v1/refunds", {"charge": failed_charge, "amount": 100})
    assert refused["status"] == 400
    assert refused["body"]["error"]["message"] == (
        f"This PaymentIntent ({error['body']['error']['payment_intent']['id']}) does not have "
        "a successful charge to refund."
    )


def test_by_intent_refund_cross_fills_the_charge(instance: seahaven.Instance) -> None:
    pi = confirmed_intent(instance, 3200)
    refund = call(instance, "POST", "/v1/refunds", {"payment_intent": pi["id"], "amount": 700})[
        "body"
    ]
    assert refund["charge"] == pi["latest_charge"]
    assert refund["payment_intent"] == pi["id"]


def test_a_plain_intent_with_no_charge_refuses(instance: seahaven.Instance) -> None:
    pi = call(instance, "POST", "/v1/payment_intents", {"amount": 900, "currency": "usd"})["body"]
    error = call(instance, "POST", "/v1/refunds", {"payment_intent": pi["id"]})
    assert error["status"] == 400
    assert error["body"]["error"]["message"] == (
        f"This PaymentIntent ({pi['id']}) does not have a successful charge to refund."
    )


# --- reads, updates, cancel ------------------------------------------------------------


def test_metadata_update_and_the_cancel_refusal(instance: seahaven.Instance) -> None:
    pi = confirmed_intent(instance, 2200)
    refund = call(instance, "POST", "/v1/refunds", {"charge": pi["latest_charge"]})["body"]
    updated = call(instance, "POST", f"/v1/refunds/{refund['id']}", {"metadata": {"k": "v"}})[
        "body"
    ]
    assert updated["metadata"] == {"k": "v"}
    error = call(instance, "POST", f"/v1/refunds/{refund['id']}/cancel")
    assert error["status"] == 400
    err = error["body"]["error"]
    assert err["message"] == "Canceling this refund is unsupported."
    assert "code" not in err


def test_the_async_success_card_begins_pending_and_cancels(instance: seahaven.Instance) -> None:
    cus = call(instance, "POST", "/v1/customers", {"email": "a@example.test"})["body"]["id"]
    # The async-success number behind the token table's …7726 row.
    async_pm = call(
        instance,
        "POST",
        "/v1/payment_methods",
        {"type": "card", "card": {"number": "4000000000007726", "exp_month": 9, "exp_year": 2027}},
    )["body"]
    call(instance, "POST", f"/v1/payment_methods/{async_pm['id']}/attach", {"customer": cus})
    pi = call(
        instance,
        "POST",
        "/v1/payment_intents",
        {
            "amount": 2400,
            "currency": "usd",
            "customer": cus,
            "payment_method": async_pm["id"],
            "confirm": True,
        },
    )["body"]
    refund = call(instance, "POST", "/v1/refunds", {"charge": pi["latest_charge"]})["body"]
    assert refund["status"] == "pending"
    # A pending refund books nothing (I7) and emits only its creation.
    charge = call(instance, "GET", f"/v1/charges/{pi['latest_charge']}")["body"]
    assert charge["amount_refunded"] == 0 and charge["refunded"] is False
    assert events_of(instance)[-1] == "refund.created"
    # The 30-minute test-mode window never closes under a frozen clock.
    canceled = call(instance, "POST", f"/v1/refunds/{refund['id']}/cancel")["body"]
    assert canceled["status"] == "canceled"
    charge = call(instance, "GET", f"/v1/charges/{pi['latest_charge']}")["body"]
    assert charge["amount_refunded"] == 0  # still nothing settled


def test_the_async_failure_card_stays_succeeded_and_books(instance: seahaven.Instance) -> None:
    """The declared behavior of `4000000000005126`: the refund begins
    `succeeded` and its async `failed` flip never fires on a frozen clock —
    so the bookkeeping applies and stays applied (STRUCTURAL_DIFFERENCES,
    Phase 9)."""
    cus = call(instance, "POST", "/v1/customers", {"email": "af@example.test"})["body"]
    pm = call(
        instance,
        "POST",
        "/v1/payment_methods",
        {"type": "card", "card": {"number": "4000000000005126", "exp_month": 9, "exp_year": 2027}},
    )["body"]
    call(instance, "POST", f"/v1/payment_methods/{pm['id']}/attach", {"customer": cus["id"]})
    pi = call(
        instance,
        "POST",
        "/v1/payment_intents",
        {
            "amount": 3100,
            "currency": "usd",
            "customer": cus["id"],
            "payment_method": pm["id"],
            "confirm": True,
        },
    )["body"]
    refund = call(instance, "POST", "/v1/refunds", {"charge": pi["latest_charge"], "amount": 1100})[
        "body"
    ]
    assert refund["status"] == "succeeded"
    assert "failure_reason" not in refund
    charge = call(instance, "GET", f"/v1/charges/{pi['latest_charge']}")["body"]
    assert charge["amount_refunded"] == 1100
    assert charge["refunded"] is False


def test_lists_filter_and_the_scoped_paths(instance: seahaven.Instance) -> None:
    one = confirmed_intent(instance, 5000)
    two = confirmed_intent(instance, 3200)
    r_one = call(instance, "POST", "/v1/refunds", {"charge": one["latest_charge"], "amount": 1500})[
        "body"
    ]
    call(instance, "POST", "/v1/refunds", {"payment_intent": two["id"], "amount": 700})
    by_charge = call(instance, "GET", "/v1/refunds", {"charge": one["latest_charge"]})["body"]
    assert [item["id"] for item in by_charge["data"]] == [r_one["id"]]
    assert by_charge["url"] == "/v1/refunds"
    by_intent = call(instance, "GET", "/v1/refunds", {"payment_intent": two["id"]})["body"]
    assert len(by_intent["data"]) == 1
    scoped = call(instance, "GET", f"/v1/charges/{one['latest_charge']}/refunds")["body"]
    assert [item["id"] for item in scoped["data"]] == [r_one["id"]]
    assert scoped["url"] == f"/v1/charges/{one['latest_charge']}/refunds"
    got = call(instance, "GET", f"/v1/charges/{one['latest_charge']}/refunds/{r_one['id']}")["body"]
    assert got["id"] == r_one["id"]
    # A refund of another charge is the scoped 404 (recorded).
    missing = call(instance, "GET", f"/v1/charges/{two['latest_charge']}/refunds/{r_one['id']}")
    assert missing["status"] == 404
    assert missing["body"]["error"]["message"] == f"No such refund: '{r_one['id']}'"
    assert missing["body"]["error"]["param"] == "refund"


def test_the_scoped_update_merges_metadata(instance: seahaven.Instance) -> None:
    pi = confirmed_intent(instance, 2600)
    refund = call(
        instance,
        "POST",
        "/v1/refunds",
        {"charge": pi["latest_charge"], "metadata": {"a": "1"}},
    )["body"]
    updated = call(
        instance,
        "POST",
        f"/v1/charges/{pi['latest_charge']}/refunds/{refund['id']}",
        {"metadata": {"b": "2"}},
    )["body"]
    assert updated["metadata"] == {"a": "1", "b": "2"}


def test_missing_refund_keeps_the_placeholder_param(instance: seahaven.Instance) -> None:
    error = call(instance, "GET", "/v1/refunds/re_missing0000000000000000")
    assert error["status"] == 404
    err = error["body"]["error"]
    assert err["message"] == "No such refund: 're_missing0000000000000000'"
    assert err["param"] == "refund"


# --- the legacy alias and the inline list ----------------------------------------------


def test_the_legacy_singular_create_answers_with_the_charge(instance: seahaven.Instance) -> None:
    pi = confirmed_intent(instance, 4100)
    result = call(instance, "POST", f"/v1/charges/{pi['latest_charge']}/refund", {"amount": 800})[
        "body"
    ]
    assert result["object"] == "charge"
    assert result["id"] == pi["latest_charge"]
    assert result["amount_refunded"] == 800
    assert result["refunded"] is False


def test_the_legacy_plural_create_answers_with_the_refund(instance: seahaven.Instance) -> None:
    pi = confirmed_intent(instance, 1300)
    result = call(instance, "POST", f"/v1/charges/{pi['latest_charge']}/refunds", {"amount": 300})[
        "body"
    ]
    assert result["object"] == "refund"
    assert result["amount"] == 300
    assert result["charge"] == pi["latest_charge"]


def test_expand_refunds_builds_the_inline_envelope(instance: seahaven.Instance) -> None:
    pi = confirmed_intent(instance, 5000)
    first = call(instance, "POST", "/v1/refunds", {"charge": pi["latest_charge"], "amount": 1500})[
        "body"
    ]
    body = call(instance, "GET", f"/v1/charges/{pi['latest_charge']}", {"expand": ["refunds"]})[
        "body"
    ]
    envelope = body["refunds"]
    assert envelope["object"] == "list"
    assert [item["id"] for item in envelope["data"]] == [first["id"]]
    assert envelope["has_more"] is False
    assert envelope["url"] == f"/v1/charges/{pi['latest_charge']}/refunds"
    assert "total_count" not in envelope  # undeclared by the pinned spec


def test_expand_refunds_caps_at_ten_and_marks_has_more(instance: seahaven.Instance) -> None:
    pi = confirmed_intent(instance, 1200)
    for _ in range(11):
        call(instance, "POST", "/v1/refunds", {"charge": pi["latest_charge"], "amount": 100})
    body = call(instance, "GET", f"/v1/charges/{pi['latest_charge']}", {"expand": ["refunds"]})[
        "body"
    ]
    assert len(body["refunds"]["data"]) == 10
    assert body["refunds"]["has_more"] is True
    # unexpanded stays absent
    assert "refunds" not in call(instance, "GET", f"/v1/charges/{pi['latest_charge']}")["body"]


def test_expand_refunds_on_a_list_and_nested_under_latest_charge(
    instance: seahaven.Instance,
) -> None:
    pi = confirmed_intent(instance, 900)
    refund = call(instance, "POST", "/v1/refunds", {"charge": pi["latest_charge"]})["body"]
    listed = call(instance, "GET", "/v1/charges", {"expand": ["data.refunds"]})["body"]
    mine = next(item for item in listed["data"] if item["id"] == pi["latest_charge"])
    assert [item["id"] for item in mine["refunds"]["data"]] == [refund["id"]]
    nested = call(
        instance, "GET", f"/v1/payment_intents/{pi['id']}", {"expand": ["latest_charge.refunds"]}
    )["body"]
    assert [item["id"] for item in nested["latest_charge"]["refunds"]["data"]] == [refund["id"]]


def test_nested_expansion_under_the_inline_page_is_not_silently_ignored(
    instance: seahaven.Instance,
) -> None:
    """`refunds.data.charge` (and the four-segment `data.refunds.data.charge`
    on the list) descends into the page the envelope carries — a validated
    path that inflated nothing would be the silent-ignore functional spec
    §6.3 forbids."""
    pi = confirmed_intent(instance, 700)
    call(instance, "POST", "/v1/refunds", {"charge": pi["latest_charge"], "amount": 200})
    body = call(
        instance, "GET", f"/v1/charges/{pi['latest_charge']}", {"expand": ["refunds.data.charge"]}
    )["body"]
    item = body["refunds"]["data"][0]
    assert item["charge"]["object"] == "charge"
    assert item["charge"]["id"] == pi["latest_charge"]
    assert item["charge"]["amount"] == 700
    listed = call(instance, "GET", "/v1/charges", {"expand": ["data.refunds.data.charge"]})["body"]
    mine = next(item for item in listed["data"] if item["id"] == pi["latest_charge"])
    assert mine["refunds"]["data"][0]["charge"]["id"] == pi["latest_charge"]
    # Rows past the 10-item page are never expanded — only the response's
    # own page is.
    pi_many = confirmed_intent(instance, 1500)
    for _ in range(11):
        call(instance, "POST", "/v1/refunds", {"charge": pi_many["latest_charge"], "amount": 100})
    page = call(
        instance,
        "GET",
        f"/v1/charges/{pi_many['latest_charge']}",
        {"expand": ["refunds.data.charge"]},
    )["body"]["refunds"]
    assert len(page["data"]) == 10
    assert all(entry["charge"]["id"] == pi_many["latest_charge"] for entry in page["data"])


def test_no_over_refund_across_succeeded_refunds(instance: seahaven.Instance) -> None:
    """Invariant I7 in motion: the ceiling is the captured amount however
    many refunds reach it, and `amount_refunded` always equals the sum of
    settled refunds."""
    pi = confirmed_intent(instance, 1000)
    ch = pi["latest_charge"]
    call(instance, "POST", "/v1/refunds", {"charge": ch, "amount": 300})
    call(instance, "POST", "/v1/refunds", {"charge": ch, "amount": 300})
    error = call(instance, "POST", "/v1/refunds", {"charge": ch, "amount": 500})
    assert error["status"] == 400
    assert error["body"]["error"]["param"] == "amount"
    assert instance.inspect().one(
        "SELECT c.amount_refunded AS refunded,"
        " (SELECT COALESCE(SUM(amount), 0) FROM refunds r"
        "  WHERE r.charge = c.id AND r.status = 'succeeded') AS settled"
        " FROM charges c WHERE c.id = ?",
        ch,
    ) == {"refunded": 600, "settled": 600}
