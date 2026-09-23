"""The refunds half of Phase 9: the body's shape, the bookkeeping, the
recorded refusals, the lists and scoped paths, the legacy alias, and
`expand[]=refunds` — pinned by the Phase 9 probes and cassette 05 at
`2026-08-26.dahlia`."""

import pytest
import seahaven

from conftest import BLANK_NOW, api_read, api_write
from seahaven_stripe_world.errors import StripeToolError

pytestmark = pytest.mark.seahaven(fixture=None, now=BLANK_NOW)


def call(instance: seahaven.Instance, method: str, path: str, params: dict | None = None):
    if method == "GET":
        return api_read(instance, path, params)
    return api_write(instance, method, path, params)


def confirmed_intent(instance: seahaven.Instance, amount: int = 5000, **extra) -> dict:
    cus = call(instance, "POST", "/v1/customers", {"email": "rf@example.test"})["id"]
    pm = call(
        instance, "POST", "/v1/payment_methods", {"type": "card", "card": {"token": "tok_visa"}}
    )["id"]
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
    )


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
    )
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
    for absent in (
        "description",
        "failure_reason",
        "instructions_email",
        "next_action",
        "presentment_details",
        "pending_reason",
    ):
        assert absent not in refund
    assert refund["receipt_number"] is None
    assert refund["customer_account"] is None
    assert refund["transfer_reversal"] is None
    assert refund["source_transfer_reversal"] is None
    assert isinstance(refund["balance_transaction"], str)
    assert refund["balance_transaction"].startswith("txn_")
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
    first = call(instance, "POST", "/v1/refunds", {"charge": ch, "amount": 1500})
    assert first["amount"] == 1500
    charge = call(instance, "GET", f"/v1/charges/{ch}")
    assert charge["amount_refunded"] == 1500
    assert charge["refunded"] is False
    assert "refunds" not in charge
    second = call(instance, "POST", "/v1/refunds", {"charge": ch})
    assert second["amount"] == 3500
    charge = call(instance, "GET", f"/v1/charges/{ch}")
    assert charge["amount_refunded"] == 5000
    assert charge["refunded"] is True
    with pytest.raises(StripeToolError) as exc_info:
        call(instance, "POST", "/v1/refunds", {"charge": ch, "amount": 1})
    assert exc_info.value.status == 400
    assert exc_info.value.stripe_body["error"]["code"] == "charge_already_refunded"
    assert (
        exc_info.value.stripe_body["error"]["message"] == f"Charge {ch} has already been refunded."
    )


def test_the_over_refund_on_a_partial_charge_is_the_recorded_form(
    instance: seahaven.Instance,
) -> None:
    pi = confirmed_intent(instance, 8000)
    call(instance, "POST", "/v1/refunds", {"charge": pi["latest_charge"], "amount": 5000})
    with pytest.raises(StripeToolError) as exc_info:
        call(instance, "POST", "/v1/refunds", {"charge": pi["latest_charge"], "amount": 4000})
    assert exc_info.value.status == 400
    err = exc_info.value.stripe_body["error"]
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
    assert instance.inspect().one(
        "SELECT data->>'$.object.object' AS kind,"
        " CAST(data->>'$.object.amount_refunded' AS INTEGER) AS refunded"
        " FROM events WHERE type = 'charge.refunded'"
    ) == {"kind": "charge", "refunded": 400}


# --- the refusals ----------------------------------------------------------------------


def test_zero_and_negative_amounts_refuse_the_recorded_form(
    instance: seahaven.Instance,
) -> None:
    pi = confirmed_intent(instance, 2000)
    for bad in (0, -5):
        with pytest.raises(StripeToolError) as exc_info:
            call(
                instance,
                "POST",
                "/v1/refunds",
                {"charge": pi["latest_charge"], "amount": bad},
            )
        assert exc_info.value.status == 400
        assert exc_info.value.stripe_body["error"]["code"] == "parameter_invalid_integer"
        assert exc_info.value.stripe_body["error"]["param"] == "amount"
        assert (
            exc_info.value.stripe_body["error"]["message"]
            == "This value must be greater than or equal to 1."
        )


def test_neither_charge_nor_intent_refuses_with_the_recorded_message(
    instance: seahaven.Instance,
) -> None:
    with pytest.raises(StripeToolError) as exc_info:
        call(instance, "POST", "/v1/refunds", {"amount": 100})
    assert exc_info.value.status == 400
    err = exc_info.value.stripe_body["error"]
    assert err["message"] == (
        "One of the following params should be provided for this request: payment_intent or charge."
    )
    assert "code" not in err and "param" not in err


def test_unknown_charge_names_param_id(instance: seahaven.Instance) -> None:
    with pytest.raises(StripeToolError) as exc_info:
        call(instance, "POST", "/v1/refunds", {"charge": "ch_missing00000000000000000"})
    assert exc_info.value.status == 404
    err = exc_info.value.stripe_body["error"]
    assert err["code"] == "resource_missing"
    assert err["param"] == "id"
    assert err["message"] == "No such charge: 'ch_missing00000000000000000'"


def test_reason_is_create_limited_to_the_caller_enum(
    instance: seahaven.Instance,
) -> None:
    pi = confirmed_intent(instance, 1200)
    with pytest.raises(StripeToolError) as exc_info:
        call(
            instance,
            "POST",
            "/v1/refunds",
            {"charge": pi["latest_charge"], "reason": "expired_uncaptured_charge"},
        )
    assert exc_info.value.status == 400
    err = exc_info.value.stripe_body["error"]
    assert err["param"] == "reason"
    assert err["message"] == (
        "Invalid reason: must be one of duplicate, fraudulent, or requested_by_customer"
    )


def test_the_uncaptured_hold_refuses_with_the_recorded_message(
    instance: seahaven.Instance,
) -> None:
    pi = confirmed_intent(instance, 3300, capture_method="manual")
    with pytest.raises(StripeToolError) as exc_info:
        call(
            instance,
            "POST",
            "/v1/refunds",
            {"charge": pi["latest_charge"], "amount": 100},
        )
    assert exc_info.value.status == 400
    err = exc_info.value.stripe_body["error"]
    assert err["message"] == (
        f"This uncaptured Charge was created by a PaymentIntent ({pi['id']}). "
        "You must cancel the PaymentIntent to reverse the authorization instead of "
        "refunding the Charge directly. For more information, see "
        "https://stripe.com/docs/payments/place-a-hold-on-a-payment-method"
    )


def test_a_failed_charge_refuses_naming_its_intent(
    instance: seahaven.Instance,
) -> None:
    cus = call(instance, "POST", "/v1/customers", {"email": "f@example.test"})["id"]
    declined = call(
        instance,
        "POST",
        "/v1/payment_methods",
        {"type": "card", "card": {"number": "4000000000000341", "exp_month": 9, "exp_year": 2027}},
    )
    call(
        instance,
        "POST",
        f"/v1/payment_methods/{declined['id']}/attach",
        {"customer": cus},
    )
    with pytest.raises(StripeToolError) as exc_info:
        call(
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
    failed_charge = exc_info.value.stripe_body["error"]["charge"]
    pi_id = exc_info.value.stripe_body["error"]["payment_intent"]["id"]
    with pytest.raises(StripeToolError) as exc_info:
        call(
            instance,
            "POST",
            "/v1/refunds",
            {"charge": failed_charge, "amount": 100},
        )
    assert exc_info.value.status == 400
    assert exc_info.value.stripe_body["error"]["message"] == (
        f"This PaymentIntent ({pi_id}) does not have a successful charge to refund."
    )


def test_by_intent_refund_cross_fills_the_charge(
    instance: seahaven.Instance,
) -> None:
    pi = confirmed_intent(instance, 3200)
    refund = call(
        instance,
        "POST",
        "/v1/refunds",
        {"payment_intent": pi["id"], "amount": 700},
    )
    assert refund["charge"] == pi["latest_charge"]
    assert refund["payment_intent"] == pi["id"]


def test_a_plain_intent_with_no_charge_refuses(
    instance: seahaven.Instance,
) -> None:
    pi = call(
        instance,
        "POST",
        "/v1/payment_intents",
        {"amount": 900, "currency": "usd"},
    )
    with pytest.raises(StripeToolError) as exc_info:
        call(instance, "POST", "/v1/refunds", {"payment_intent": pi["id"]})
    assert exc_info.value.status == 400
    assert exc_info.value.stripe_body["error"]["message"] == (
        f"This PaymentIntent ({pi['id']}) does not have a successful charge to refund."
    )


# --- reads, updates, cancel ------------------------------------------------------------


def test_metadata_update_and_the_cancel_refusal(
    instance: seahaven.Instance,
) -> None:
    pi = confirmed_intent(instance, 2200)
    refund = call(instance, "POST", "/v1/refunds", {"charge": pi["latest_charge"]})
    updated = call(
        instance,
        "POST",
        f"/v1/refunds/{refund['id']}",
        {"metadata": {"k": "v"}},
    )
    assert updated["metadata"] == {"k": "v"}
    with pytest.raises(StripeToolError) as exc_info:
        call(instance, "POST", f"/v1/refunds/{refund['id']}/cancel")
    assert exc_info.value.status == 400
    err = exc_info.value.stripe_body["error"]
    assert err["message"] == "Canceling this refund is unsupported."
    assert "code" not in err


def test_the_async_success_card_begins_pending_and_cancels(
    instance: seahaven.Instance,
) -> None:
    cus = call(instance, "POST", "/v1/customers", {"email": "a@example.test"})["id"]
    async_pm = call(
        instance,
        "POST",
        "/v1/payment_methods",
        {
            "type": "card",
            "card": {
                "number": "4000000000007726",
                "exp_month": 9,
                "exp_year": 2027,
            },
        },
    )
    call(
        instance,
        "POST",
        f"/v1/payment_methods/{async_pm['id']}/attach",
        {"customer": cus},
    )
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
    )
    refund = call(
        instance,
        "POST",
        "/v1/refunds",
        {"charge": pi["latest_charge"]},
    )
    assert refund["status"] == "pending"
    charge = call(instance, "GET", f"/v1/charges/{pi['latest_charge']}")
    assert charge["amount_refunded"] == 0 and charge["refunded"] is False
    assert events_of(instance)[-1] == "refund.created"
    canceled = call(instance, "POST", f"/v1/refunds/{refund['id']}/cancel")
    assert canceled["status"] == "canceled"
    charge = call(instance, "GET", f"/v1/charges/{pi['latest_charge']}")
    assert charge["amount_refunded"] == 0


def test_the_async_failure_card_stays_succeeded_and_books(
    instance: seahaven.Instance,
) -> None:
    """The declared behavior of `4000000000005126`: the refund begins
    `succeeded` and its async `failed` flip never fires on a frozen clock —
    so the bookkeeping applies and stays applied (STRUCTURAL_DIFFERENCES,
    Phase 9)."""
    cus = call(instance, "POST", "/v1/customers", {"email": "af@example.test"})
    pm = call(
        instance,
        "POST",
        "/v1/payment_methods",
        {
            "type": "card",
            "card": {
                "number": "4000000000005126",
                "exp_month": 9,
                "exp_year": 2027,
            },
        },
    )
    call(
        instance,
        "POST",
        f"/v1/payment_methods/{pm['id']}/attach",
        {"customer": cus["id"]},
    )
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
    )
    refund = call(
        instance,
        "POST",
        "/v1/refunds",
        {"charge": pi["latest_charge"], "amount": 1100},
    )
    assert refund["status"] == "succeeded"
    assert "failure_reason" not in refund
    charge = call(instance, "GET", f"/v1/charges/{pi['latest_charge']}")
    assert charge["amount_refunded"] == 1100
    assert charge["refunded"] is False


def test_lists_filter_and_the_scoped_paths(instance: seahaven.Instance) -> None:
    one = confirmed_intent(instance, 5000)
    two = confirmed_intent(instance, 3200)
    r_one = call(
        instance,
        "POST",
        "/v1/refunds",
        {"charge": one["latest_charge"], "amount": 1500},
    )
    call(
        instance,
        "POST",
        "/v1/refunds",
        {"payment_intent": two["id"], "amount": 700},
    )
    by_charge = call(instance, "GET", "/v1/refunds", {"charge": one["latest_charge"]})
    assert [item["id"] for item in by_charge["data"]] == [r_one["id"]]
    assert by_charge["url"] == "/v1/refunds"
    by_intent = call(instance, "GET", "/v1/refunds", {"payment_intent": two["id"]})
    assert len(by_intent["data"]) == 1
    scoped = call(instance, "GET", f"/v1/charges/{one['latest_charge']}/refunds")
    assert [item["id"] for item in scoped["data"]] == [r_one["id"]]
    assert scoped["url"] == f"/v1/charges/{one['latest_charge']}/refunds"
    got = call(
        instance,
        "GET",
        f"/v1/charges/{one['latest_charge']}/refunds/{r_one['id']}",
    )
    assert got["id"] == r_one["id"]
    with pytest.raises(StripeToolError) as exc_info:
        call(
            instance,
            "GET",
            f"/v1/charges/{two['latest_charge']}/refunds/{r_one['id']}",
        )
    assert exc_info.value.status == 404
    assert exc_info.value.stripe_body["error"]["message"] == f"No such refund: '{r_one['id']}'"
    assert exc_info.value.stripe_body["error"]["param"] == "refund"


def test_the_scoped_update_merges_metadata(instance: seahaven.Instance) -> None:
    pi = confirmed_intent(instance, 2600)
    refund = call(
        instance,
        "POST",
        "/v1/refunds",
        {"charge": pi["latest_charge"], "metadata": {"a": "1"}},
    )
    updated = call(
        instance,
        "POST",
        f"/v1/charges/{pi['latest_charge']}/refunds/{refund['id']}",
        {"metadata": {"b": "2"}},
    )
    assert updated["metadata"] == {"a": "1", "b": "2"}


def test_missing_refund_keeps_the_placeholder_param(
    instance: seahaven.Instance,
) -> None:
    with pytest.raises(StripeToolError) as exc_info:
        call(instance, "GET", "/v1/refunds/re_missing0000000000000000")
    assert exc_info.value.status == 404
    err = exc_info.value.stripe_body["error"]
    assert err["message"] == "No such refund: 're_missing0000000000000000'"
    assert err["param"] == "refund"


# --- the legacy alias and the inline list ----------------------------------------------


def test_the_legacy_singular_create_answers_with_the_charge(
    instance: seahaven.Instance,
) -> None:
    pi = confirmed_intent(instance, 4100)
    result = call(
        instance,
        "POST",
        f"/v1/charges/{pi['latest_charge']}/refund",
        {"amount": 800},
    )
    assert result["object"] == "charge"
    assert result["id"] == pi["latest_charge"]
    assert result["amount_refunded"] == 800
    assert result["refunded"] is False


def test_the_legacy_plural_create_answers_with_the_refund(
    instance: seahaven.Instance,
) -> None:
    pi = confirmed_intent(instance, 1300)
    result = call(
        instance,
        "POST",
        f"/v1/charges/{pi['latest_charge']}/refunds",
        {"amount": 300},
    )
    assert result["object"] == "refund"
    assert result["amount"] == 300
    assert result["charge"] == pi["latest_charge"]


def test_expand_refunds_builds_the_inline_envelope(
    instance: seahaven.Instance,
) -> None:
    pi = confirmed_intent(instance, 5000)
    first = call(
        instance,
        "POST",
        "/v1/refunds",
        {"charge": pi["latest_charge"], "amount": 1500},
    )
    body = call(
        instance,
        "GET",
        f"/v1/charges/{pi['latest_charge']}",
        {"expand": ["refunds"]},
    )
    envelope = body["refunds"]
    assert envelope["object"] == "list"
    assert [item["id"] for item in envelope["data"]] == [first["id"]]
    assert envelope["has_more"] is False
    assert envelope["url"] == f"/v1/charges/{pi['latest_charge']}/refunds"
    assert "total_count" not in envelope


def test_expand_refunds_caps_at_ten_and_marks_has_more(
    instance: seahaven.Instance,
) -> None:
    pi = confirmed_intent(instance, 1200)
    for _ in range(11):
        call(
            instance,
            "POST",
            "/v1/refunds",
            {"charge": pi["latest_charge"], "amount": 100},
        )
    body = call(
        instance,
        "GET",
        f"/v1/charges/{pi['latest_charge']}",
        {"expand": ["refunds"]},
    )
    assert len(body["refunds"]["data"]) == 10
    assert body["refunds"]["has_more"] is True
    assert "refunds" not in call(instance, "GET", f"/v1/charges/{pi['latest_charge']}")


def test_expand_refunds_on_a_list_and_nested_under_latest_charge(
    instance: seahaven.Instance,
) -> None:
    pi = confirmed_intent(instance, 900)
    refund = call(
        instance,
        "POST",
        "/v1/refunds",
        {"charge": pi["latest_charge"]},
    )
    listed = call(instance, "GET", "/v1/charges", {"expand": ["data.refunds"]})
    mine = next(item for item in listed["data"] if item["id"] == pi["latest_charge"])
    assert [item["id"] for item in mine["refunds"]["data"]] == [refund["id"]]
    nested = call(
        instance,
        "GET",
        f"/v1/payment_intents/{pi['id']}",
        {"expand": ["latest_charge.refunds"]},
    )
    assert [item["id"] for item in nested["latest_charge"]["refunds"]["data"]] == [refund["id"]]


def test_nested_expansion_under_the_inline_page_is_not_silently_ignored(
    instance: seahaven.Instance,
) -> None:
    """`refunds.data.charge` (and the four-segment `data.refunds.data.charge`
    on the list) descends into the page the envelope carries — a validated
    path that inflated nothing would be the silent-ignore functional spec
    §6.3 forbids."""
    pi = confirmed_intent(instance, 700)
    call(
        instance,
        "POST",
        "/v1/refunds",
        {"charge": pi["latest_charge"], "amount": 200},
    )
    body = call(
        instance,
        "GET",
        f"/v1/charges/{pi['latest_charge']}",
        {"expand": ["refunds.data.charge"]},
    )
    item = body["refunds"]["data"][0]
    assert item["charge"]["object"] == "charge"
    assert item["charge"]["id"] == pi["latest_charge"]
    assert item["charge"]["amount"] == 700
    listed = call(
        instance,
        "GET",
        "/v1/charges",
        {"expand": ["data.refunds.data.charge"]},
    )
    mine = next(item for item in listed["data"] if item["id"] == pi["latest_charge"])
    assert mine["refunds"]["data"][0]["charge"]["id"] == pi["latest_charge"]
    pi_many = confirmed_intent(instance, 1500)
    for _ in range(11):
        call(
            instance,
            "POST",
            "/v1/refunds",
            {"charge": pi_many["latest_charge"], "amount": 100},
        )
    page = call(
        instance,
        "GET",
        f"/v1/charges/{pi_many['latest_charge']}",
        {"expand": ["refunds.data.charge"]},
    )["refunds"]
    assert len(page["data"]) == 10
    assert all(entry["charge"]["id"] == pi_many["latest_charge"] for entry in page["data"])


def test_no_over_refund_across_succeeded_refunds(
    instance: seahaven.Instance,
) -> None:
    """Invariant I7 in motion: the ceiling is the captured amount however
    many refunds reach it, and `amount_refunded` always equals the sum of
    settled refunds."""
    pi = confirmed_intent(instance, 1000)
    ch = pi["latest_charge"]
    call(instance, "POST", "/v1/refunds", {"charge": ch, "amount": 300})
    call(instance, "POST", "/v1/refunds", {"charge": ch, "amount": 300})
    with pytest.raises(StripeToolError) as exc_info:
        call(instance, "POST", "/v1/refunds", {"charge": ch, "amount": 500})
    assert exc_info.value.status == 400
    assert exc_info.value.stripe_body["error"]["param"] == "amount"
    assert instance.inspect().one(
        "SELECT c.amount_refunded AS refunded,"
        " (SELECT COALESCE(SUM(amount), 0) FROM refunds r"
        "  WHERE r.charge = c.id AND r.status = 'succeeded') AS settled"
        " FROM charges c WHERE c.id = ?",
        ch,
    ) == {"refunded": 600, "settled": 600}
