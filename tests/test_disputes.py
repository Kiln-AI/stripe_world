"""The disputes half of Phase 9: creation inside the charge attempt, the
three card flavors, evidence submission and the magic strings, close, the
refund gate, and the scoped reads — pinned by the Phase 9 probes and
cassette 05 at `2026-08-26.dahlia`."""

import pytest
import seahaven

from conftest import BLANK_NOW

pytestmark = pytest.mark.seahaven(fixture=None, now=BLANK_NOW)


def call(instance: seahaven.Instance, method: str, path: str, params: dict | None = None):
    if method == "GET":
        return instance.call("stripe_api_read", path=path, params=params)
    return instance.call("stripe_api_write", method=method, path=path, params=params)


def dispute(
    instance: seahaven.Instance, token: str = "tok_visa_createDispute", amount: int = 6000
) -> tuple[dict, dict]:
    cus = call(
        instance, "POST", "/v1/customers", {"email": "dp@example.test", "name": "Dee Puted"}
    )["body"]
    pm = call(instance, "POST", "/v1/payment_methods", {"type": "card", "card": {"token": token}})[
        "body"
    ]
    call(instance, "POST", f"/v1/payment_methods/{pm['id']}/attach", {"customer": cus["id"]})
    pi = call(
        instance,
        "POST",
        "/v1/payment_intents",
        {
            "amount": amount,
            "currency": "usd",
            "customer": cus["id"],
            "payment_method": pm["id"],
            "confirm": True,
            "description": "a disputed charge",
        },
    )["body"]
    listed = call(instance, "GET", "/v1/disputes", {"charge": pi["latest_charge"]})["body"]
    assert len(listed["data"]) == 1
    return pi, listed["data"][0]


def events_of(instance: seahaven.Instance) -> list[tuple[str, str]]:
    return [
        (row["type"], row["object"])
        for row in instance.inspect().rows(
            "SELECT type, data->>'$.object.id' AS object FROM events ORDER BY x_seq"
        )
    ]


# --- creation --------------------------------------------------------------------------


def test_the_chargeback_card_creates_the_dispute_inside_the_charge(
    instance: seahaven.Instance,
) -> None:
    pi, dp = dispute(instance)
    assert dp["object"] == "dispute"
    assert dp["id"].startswith("du_")  # probed: du_, not dp_
    assert dp["status"] == "needs_response"
    assert dp["reason"] == "fraudulent"
    assert dp["amount"] == 6000
    assert dp["currency"] == "usd"
    assert dp["charge"] == pi["latest_charge"]
    assert dp["payment_intent"] == pi["id"]
    assert dp["is_charge_refundable"] is False
    assert dp["livemode"] is True
    assert dp["metadata"] == {}
    assert dp["enhanced_eligibility_types"] == []
    # the chargeback's ledger withdrawal, derived from the ledger (Phase 11)
    [withdrawal] = dp["balance_transactions"]
    assert withdrawal["object"] == "balance_transaction"
    assert withdrawal["type"] == "adjustment"
    assert withdrawal["reporting_category"] == "dispute"
    assert withdrawal["amount"] == -dp["amount"]
    assert withdrawal["fee"] == 1500
    assert withdrawal["net"] == -(dp["amount"] + 1500)
    assert withdrawal["source"] == dp["id"]
    assert "customer" not in dp  # undeclared on dispute at this version
    assert dp["payment_method_details"] == {
        "card": {
            "brand": "visa",
            "case_type": "chargeback",
            "network": "visa",
            "network_reason_code": "10.4",
        },
        "type": "card",
    }
    evidence = dp["evidence"]
    assert evidence["enhanced_evidence"] == {}
    assert all(value is None for key, value in evidence.items() if key != "enhanced_evidence")
    details = dp["evidence_details"]
    assert details == {
        "due_by": details["due_by"],  # asserted by shape below
        "enhanced_eligibility": {},
        "has_evidence": False,
        "past_due": False,
        "submission_count": 0,
    }
    assert isinstance(details["due_by"], int)


def test_due_by_is_the_end_of_the_day_eight_days_out(instance: seahaven.Instance) -> None:
    _pi, dp = dispute(instance)
    created_unix = dp["created"]
    day = created_unix // 86_400
    assert dp["evidence_details"]["due_by"] == (day + 9) * 86_400 - 1


def test_the_charge_carries_the_disputed_flag_and_the_recorded_event_order(
    instance: seahaven.Instance,
) -> None:
    pi, dp = dispute(instance)
    charge = call(instance, "GET", f"/v1/charges/{pi['latest_charge']}")["body"]
    assert charge["disputed"] is True
    assert "dispute" not in charge  # undeclared on charge; omitted here
    kinds = [kind for kind, _ in events_of(instance)]
    assert kinds[-4:] == [
        "charge.succeeded",
        "charge.dispute.created",
        "charge.dispute.funds_withdrawn",
        "payment_intent.succeeded",
    ]
    # every dispute event carries the dispute itself as data.object
    for kind, obj in events_of(instance):
        if kind.startswith("charge.dispute."):
            assert obj == dp["id"]


def test_the_product_not_received_and_inquiry_flavors(instance: seahaven.Instance) -> None:
    _, pnr = dispute(instance, token="tok_visa_createDisputeProductNotReceived")
    assert pnr["reason"] == "product_not_received"
    assert pnr["payment_method_details"]["card"]["network_reason_code"] == "13.1"
    assert pnr["status"] == "needs_response"
    _, inquiry = dispute(instance, token="tok_visa_createDisputeInquiry", amount=1700)
    assert inquiry["status"] == "warning_needs_response"
    assert inquiry["is_charge_refundable"] is True  # inquiries pull no funds
    assert inquiry["payment_method_details"]["card"]["case_type"] == "inquiry"
    assert inquiry["payment_method_details"]["card"]["network_reason_code"] == "10"


def test_a_clean_charge_has_no_dispute_row(instance: seahaven.Instance) -> None:
    cus = call(instance, "POST", "/v1/customers", {"email": "c@example.test"})["body"]
    pm = call(
        instance, "POST", "/v1/payment_methods", {"type": "card", "card": {"token": "tok_visa"}}
    )["body"]
    call(instance, "POST", f"/v1/payment_methods/{pm['id']}/attach", {"customer": cus["id"]})
    pi = call(
        instance,
        "POST",
        "/v1/payment_intents",
        {
            "amount": 1200,
            "currency": "usd",
            "customer": cus["id"],
            "payment_method": pm["id"],
            "confirm": True,
        },
    )["body"]
    charge = call(instance, "GET", f"/v1/charges/{pi['latest_charge']}")["body"]
    assert charge["disputed"] is False
    listed = call(instance, "GET", "/v1/disputes", {"charge": pi["latest_charge"]})["body"]
    assert listed["data"] == []


# --- the refund gate -------------------------------------------------------------------


def test_an_open_dispute_blocks_refunds_and_a_won_one_reopens_them(
    instance: seahaven.Instance,
) -> None:
    pi, dp = dispute(instance)
    error = call(instance, "POST", "/v1/refunds", {"charge": pi["latest_charge"], "amount": 100})
    assert error["status"] == 400
    err = error["body"]["error"]
    assert err["code"] == "charge_disputed"
    assert (
        err["message"]
        == f"Charge {pi['latest_charge']} has been charged back; cannot issue a refund."
    )
    won = call(
        instance,
        "POST",
        f"/v1/disputes/{dp['id']}",
        {"evidence": {"uncategorized_text": "winning_evidence"}},
    )["body"]
    assert won["status"] == "won"
    assert won["is_charge_refundable"] is True
    refund = call(instance, "POST", "/v1/refunds", {"charge": pi["latest_charge"], "amount": 100})[
        "body"
    ]
    assert refund["status"] == "succeeded"
    # `disputed` stays true after resolution — the charge WAS disputed.
    charge = call(instance, "GET", f"/v1/charges/{pi['latest_charge']}")["body"]
    assert charge["disputed"] is True


def test_an_inquiry_does_not_block_refunds(instance: seahaven.Instance) -> None:
    pi, _ = dispute(instance, token="tok_visa_createDisputeInquiry", amount=1700)
    refund = call(instance, "POST", "/v1/refunds", {"charge": pi["latest_charge"], "amount": 100})
    assert refund["status"] == 200


def test_a_lost_dispute_keeps_the_gate_closed(instance: seahaven.Instance) -> None:
    pi, dp = dispute(instance)
    call(instance, "POST", f"/v1/disputes/{dp['id']}/close")
    error = call(instance, "POST", "/v1/refunds", {"charge": pi["latest_charge"], "amount": 100})
    assert error["status"] == 400
    assert error["body"]["error"]["code"] == "charge_disputed"


# --- evidence and the state machine -----------------------------------------------------


def test_plain_evidence_moves_under_review_and_stays(instance: seahaven.Instance) -> None:
    _, dp = dispute(instance)
    body = call(
        instance,
        "POST",
        f"/v1/disputes/{dp['id']}",
        {"evidence": {"product_description": "a widget"}},
    )["body"]
    assert body["status"] == "under_review"
    assert body["evidence"]["product_description"] == "a widget"
    assert body["evidence_details"]["has_evidence"] is True
    assert body["evidence_details"]["submission_count"] == 1
    # the update pair, in the recorded order
    kinds = [kind for kind, _ in events_of(instance)]
    assert kinds[-2:] == ["charge.dispute.updated", "charge.updated"]


def test_winning_evidence_settles_won_with_the_terminal_pair(instance: seahaven.Instance) -> None:
    _pi, dp = dispute(instance)
    body = call(
        instance,
        "POST",
        f"/v1/disputes/{dp['id']}",
        {"evidence": {"uncategorized_text": "winning_evidence"}},
    )["body"]
    assert body["status"] == "won"
    assert body["evidence_details"]["submission_count"] == 1
    kinds = [kind for kind, _ in events_of(instance)]
    assert kinds[-4:] == [
        "charge.dispute.updated",
        "charge.updated",
        "charge.dispute.funds_reinstated",
        "charge.dispute.closed",
    ]
    assert call(instance, "GET", f"/v1/disputes/{dp['id']}")["body"]["status"] == "won"


def test_losing_evidence_settles_lost_without_a_reinstatement(instance: seahaven.Instance) -> None:
    _pi, dp = dispute(instance)
    body = call(
        instance,
        "POST",
        f"/v1/disputes/{dp['id']}",
        {"evidence": {"uncategorized_text": "losing_evidence"}},
    )["body"]
    assert body["status"] == "lost"
    assert body["is_charge_refundable"] is False
    kinds = [kind for kind, _ in events_of(instance)]
    assert kinds[-3:] == ["charge.dispute.updated", "charge.updated", "charge.dispute.closed"]


def test_escalate_inquiry_evidence_lands_needs_response(instance: seahaven.Instance) -> None:
    _, dp = dispute(instance, token="tok_visa_createDisputeInquiry", amount=1700)
    body = call(
        instance,
        "POST",
        f"/v1/disputes/{dp['id']}",
        {"evidence": {"uncategorized_text": "escalate_inquiry_evidence"}},
    )["body"]
    assert body["status"] == "needs_response"
    assert body["is_charge_refundable"] is False  # escalated: funds now pulled


def test_close_is_synchronously_lost(instance: seahaven.Instance) -> None:
    _pi, dp = dispute(instance)
    body = call(instance, "POST", f"/v1/disputes/{dp['id']}/close")["body"]
    assert body["status"] == "lost"
    assert body["is_charge_refundable"] is False
    kinds = [kind for kind, _ in events_of(instance)]
    assert kinds[-2:] == ["charge.dispute.closed", "charge.updated"]


def test_a_closed_dispute_refuses_further_updates(instance: seahaven.Instance) -> None:
    _, dp = dispute(instance)
    call(instance, "POST", f"/v1/disputes/{dp['id']}/close")
    for path, params in (
        (f"/v1/disputes/{dp['id']}", {"evidence": {"uncategorized_text": "winning_evidence"}}),
        (f"/v1/disputes/{dp['id']}/close", None),
    ):
        error = call(instance, "POST", path, params)
        assert error["status"] == 400
        err = error["body"]["error"]
        assert err["message"] == "This dispute is already closed"
        assert "code" not in err and "param" not in err


def test_dispute_metadata_updates_and_merges(instance: seahaven.Instance) -> None:
    _, dp = dispute(instance)
    body = call(instance, "POST", f"/v1/disputes/{dp['id']}", {"metadata": {"a": "1"}})["body"]
    assert body["metadata"] == {"a": "1"}
    assert body["status"] == "needs_response"  # metadata alone moves nothing
    assert body["evidence_details"]["submission_count"] == 0
    body = call(instance, "POST", f"/v1/disputes/{dp['id']}", {"metadata": {"b": "2"}})["body"]
    assert body["metadata"] == {"a": "1", "b": "2"}


def test_the_file_holding_evidence_fields_are_cut(instance: seahaven.Instance) -> None:
    _, dp = dispute(instance)
    error = call(
        instance,
        "POST",
        f"/v1/disputes/{dp['id']}",
        {"evidence": {"customer_communication": "some emails"}},
    )
    assert error["status"] == 400
    assert error["body"]["error"]["message"] == (
        "Received unknown parameter: evidence[customer_communication]"
    )


# --- the reads --------------------------------------------------------------------------


def test_retrieve_and_list(instance: seahaven.Instance) -> None:
    pi, dp = dispute(instance)
    got = call(instance, "GET", f"/v1/disputes/{dp['id']}")["body"]
    assert got["id"] == dp["id"]
    listed = call(instance, "GET", "/v1/disputes", {"payment_intent": pi["id"]})["body"]
    assert [item["id"] for item in listed["data"]] == [dp["id"]]
    everything = call(instance, "GET", "/v1/disputes", {"limit": 100})["body"]
    assert dp["id"] in [item["id"] for item in everything["data"]]


def test_the_missing_shapes(instance: seahaven.Instance) -> None:
    _pi, _dp = dispute(instance)
    missing = call(instance, "GET", "/v1/disputes/du_missing000000000000000")
    assert missing["status"] == 404
    err = missing["body"]["error"]
    assert err["message"] == "No such dispute: 'du_missing000000000000000'"
    assert err["param"] == "dispute"
    bogus_parent = call(instance, "GET", "/v1/charges/ch_missing00000000000000000/dispute")
    assert bogus_parent["status"] == 404
    assert bogus_parent["body"]["error"]["message"] == (
        "No such charge: 'ch_missing00000000000000000'"
    )


def test_a_manual_capture_hold_creates_no_dispute_until_the_capture(
    instance: seahaven.Instance,
) -> None:
    """Probed (Phase 9 CR round): an authorized-but-uncaptured hold on a
    dispute card mints no dispute — the issuer disputes captured funds —
    and the capture transition creates it, in the same recorded event
    order (`charge.captured`, the dispute pair, `payment_intent.succeeded`)."""
    cus = call(instance, "POST", "/v1/customers", {"email": "m@example.test"})["body"]
    pm = call(
        instance,
        "POST",
        "/v1/payment_methods",
        {"type": "card", "card": {"token": "tok_visa_createDispute"}},
    )["body"]
    call(instance, "POST", f"/v1/payment_methods/{pm['id']}/attach", {"customer": cus["id"]})
    pi = call(
        instance,
        "POST",
        "/v1/payment_intents",
        {
            "amount": 4400,
            "currency": "usd",
            "customer": cus["id"],
            "payment_method": pm["id"],
            "confirm": True,
            "capture_method": "manual",
        },
    )["body"]
    assert pi["status"] == "requires_capture"
    charge = call(instance, "GET", f"/v1/charges/{pi['latest_charge']}")["body"]
    assert charge["disputed"] is False
    listed = call(instance, "GET", "/v1/disputes", {"charge": pi["latest_charge"]})["body"]
    assert listed["data"] == []
    captured = call(instance, "POST", f"/v1/payment_intents/{pi['id']}/capture")["body"]
    assert captured["status"] == "succeeded"
    listed = call(instance, "GET", "/v1/disputes", {"charge": pi["latest_charge"]})["body"]
    assert len(listed["data"]) == 1
    dp = listed["data"][0]
    assert dp["status"] == "needs_response"
    assert dp["amount"] == 4400
    charge = call(instance, "GET", f"/v1/charges/{pi['latest_charge']}")["body"]
    assert charge["disputed"] is True
    kinds = [kind for kind, _ in events_of(instance)]
    assert kinds[-4:] == [
        "charge.captured",
        "charge.dispute.created",
        "charge.dispute.funds_withdrawn",
        "payment_intent.succeeded",
    ]
    # a partial capture disputes the captured amount (the captured-funds rule)
    pi2 = call(
        instance,
        "POST",
        "/v1/payment_intents",
        {
            "amount": 5000,
            "currency": "usd",
            "customer": cus["id"],
            "payment_method": pm["id"],
            "confirm": True,
            "capture_method": "manual",
        },
    )["body"]
    call(
        instance,
        "POST",
        f"/v1/payment_intents/{pi2['id']}/capture",
        {"amount_to_capture": 2000},
    )
    listed = call(instance, "GET", "/v1/disputes", {"charge": pi2["latest_charge"]})["body"]
    assert listed["data"][0]["amount"] == 2000


def test_the_charge_scoped_read_and_aliases(instance: seahaven.Instance) -> None:
    pi, dp = dispute(instance)
    ch = pi["latest_charge"]
    scoped = call(instance, "GET", f"/v1/charges/{ch}/dispute")["body"]
    assert scoped["id"] == dp["id"]
    # the POST aliases drive the same machine through the charge path
    updated = call(
        instance,
        "POST",
        f"/v1/charges/{ch}/dispute",
        {"evidence": {"uncategorized_text": "winning_evidence"}},
    )["body"]
    assert updated["status"] == "won"
    again = call(instance, "POST", f"/v1/charges/{ch}/dispute/close")
    assert again["status"] == 400
    assert again["body"]["error"]["message"] == "This dispute is already closed"


def test_the_charge_scoped_read_of_a_clean_charge(instance: seahaven.Instance) -> None:
    cus = call(instance, "POST", "/v1/customers", {"email": "n@example.test"})["body"]
    pm = call(
        instance, "POST", "/v1/payment_methods", {"type": "card", "card": {"token": "tok_visa"}}
    )["body"]
    call(instance, "POST", f"/v1/payment_methods/{pm['id']}/attach", {"customer": cus["id"]})
    pi = call(
        instance,
        "POST",
        "/v1/payment_intents",
        {
            "amount": 1000,
            "currency": "usd",
            "customer": cus["id"],
            "payment_method": pm["id"],
            "confirm": True,
        },
    )["body"]
    error = call(instance, "GET", f"/v1/charges/{pi['latest_charge']}/dispute")
    assert error["status"] == 404
    err = error["body"]["error"]
    assert err["message"] == f"No dispute for charge: {pi['latest_charge']}"
    assert "code" not in err and "param" not in err
