"""The payouts (Phase 11): the draw-down, the sweep, cancel, settle, reverse
and fail — spec-derived surfaces (the recording account cannot mint a payout;
see `allowed_differences.py`'s structural section), with the recorded
refusals, spellings and filters from cassette 11."""

import pytest
import seahaven

from conftest import BLANK_NOW

pytestmark = pytest.mark.seahaven(fixture=None, now=BLANK_NOW)


def call(instance: seahaven.Instance, method: str, path: str, params: dict | None = None):
    if method == "GET":
        return instance.call("stripe_api_read", path=path, params=params)
    return instance.call("stripe_api_write", method=method, path=path, params=params)


def funded(instance: seahaven.Instance, amount: int = 10_000) -> int:
    """Charge and settle, so the charge's NET is available to pay out (fresh
    charges sit in the T+2 pending half; `settle` backdates their rows the
    way the fixture generator freezes history). Returns what became
    available: `amount` minus the default schedule's fee."""
    cus = call(instance, "POST", "/v1/customers", {"email": "po@example.test"})["body"]["id"]
    pm = call(
        instance, "POST", "/v1/payment_methods", {"type": "card", "card": {"token": "tok_visa"}}
    )["body"]["id"]
    call(instance, "POST", f"/v1/payment_methods/{pm}/attach", {"customer": cus})
    call(
        instance,
        "POST",
        "/v1/payment_intents",
        {
            "amount": amount,
            "currency": "usd",
            "customer": cus,
            "payment_method": pm,
            "confirm": True,
        },
    )
    with instance.bulk() as ctx:
        ctx.db.execute("UPDATE balance_transactions SET available_on = created")
        ctx.db.execute(
            "UPDATE balance_transactions SET status = 'available' WHERE available_on <= ?",
            ctx.clock.iso(),
        )
    fee = amount * 290 // 10_000 + 30
    return amount - fee


def balance_available(instance: seahaven.Instance) -> int:
    body = call(instance, "GET", "/v1/balance")["body"]
    return body["available"][0]["amount"] if body["available"] else 0


def events_of(instance: seahaven.Instance) -> list[str]:
    return [
        row["type"] for row in instance.inspect().rows("SELECT type FROM events ORDER BY x_seq")
    ]


# --- create -----------------------------------------------------------------------------


def test_create_draws_down_and_sweeps(instance: seahaven.Instance) -> None:
    available = funded(instance, 10_000)
    payout = call(
        instance,
        "POST",
        "/v1/payouts",
        {"amount": 5000, "currency": "usd", "description": "rent", "statement_descriptor": "RENT"},
    )["body"]
    assert payout["object"] == "payout"
    assert payout["status"] == "pending"  # born pending; the clock never advances it
    assert payout["automatic"] is False
    assert payout["method"] == "standard"
    assert payout["source_type"] == "bank_account"
    assert payout["type"] == "bank_account"
    assert payout["reconciliation_status"] == "completed"
    assert payout["destination"].startswith("ba_")
    assert payout["amount"] == 5000
    assert payout["currency"] == "usd"
    assert payout["description"] == "rent"
    assert payout["statement_descriptor"] == "RENT"
    assert payout["livemode"] is False
    assert payout["metadata"] == {}
    assert payout["failure_code"] is None
    assert payout["original_payout"] is None
    assert payout["reversed_by"] is None
    assert "trace_id" not in payout  # neither required nor nullable: omitted
    assert payout["balance_transaction"].startswith("txn_")
    # the draw-down: the charge's net minus the payout debit
    assert balance_available(instance) == available - 5000
    # the sweep: the charge's row answers the payout filter
    swept = call(instance, "GET", "/v1/balance_transactions", {"payout": payout["id"]})["body"]
    assert [row["type"] for row in swept["data"]] == ["charge"]
    # the payout's own debit is not swept, and the event fired
    bt = call(instance, "GET", f"/v1/balance_transactions/{payout['balance_transaction']}")["body"]
    assert bt["type"] == "payout"
    assert bt["amount"] == -5000
    assert bt["fee"] == 0
    assert bt["net"] == -5000
    assert bt["source"] == payout["id"]
    assert bt["status"] == "available"  # committed funds leave immediately
    assert bt["reporting_category"] == "payout"
    assert events_of(instance)[-1] == "payout.created"


def test_create_refuses_beyond_the_available_balance(instance: seahaven.Instance) -> None:
    available = funded(instance, 3000)
    result = call(instance, "POST", "/v1/payouts", {"amount": 5000, "currency": "usd"})
    assert result["status"] == 400
    error = result["body"]["error"]
    assert error["code"] == "balance_insufficient"
    assert error["message"] == (
        f"Payout amount ($50.00) is greater than your available balance "
        f"(${available // 100}.{available % 100:02d})."
    )
    assert not instance.inspect().rows("SELECT id FROM payouts")


def test_the_recorded_create_refusals(instance: seahaven.Instance) -> None:
    for amount in (-100, 0):
        result = call(instance, "POST", "/v1/payouts", {"amount": amount, "currency": "usd"})
        assert result["status"] == 400
        error = result["body"]["error"]
        assert error["code"] == "parameter_invalid_integer"
        assert error["message"] == "This value must be greater than or equal to 1."
        assert error["param"] == "amount"
    missing = call(instance, "POST", "/v1/payouts", {"currency": "usd"})
    assert missing["body"]["error"]["code"] == "parameter_missing"
    bogus = call(instance, "POST", "/v1/payouts", {"amount": 1000, "currency": "xxd"})
    assert bogus["body"]["error"]["message"].startswith(
        "Invalid currency: xxd. Stripe currently supports these currencies: usd"
    )


# --- the lifecycle -----------------------------------------------------------------------


def payout_id(instance: seahaven.Instance, amount: int = 4000) -> str:
    funded(instance, 10_000)
    return call(instance, "POST", "/v1/payouts", {"amount": amount, "currency": "usd"})["body"][
        "id"
    ]


def test_cancel_returns_the_funds_and_unsweeps(instance: seahaven.Instance) -> None:
    po = payout_id(instance, 4000)
    after_create = balance_available(instance)
    canceled = call(instance, "POST", f"/v1/payouts/{po}/cancel")["body"]
    assert canceled["status"] == "canceled"
    assert canceled["failure_balance_transaction"].startswith("txn_")
    reversal = call(
        instance,
        "GET",
        f"/v1/balance_transactions/{canceled['failure_balance_transaction']}",
    )["body"]
    assert reversal["type"] == "payout_cancel"
    assert reversal["amount"] == 4000
    assert reversal["source"] == po
    # the funds are back (the net of the original charge) and the sweep is
    # empty again
    assert balance_available(instance) == after_create + 4000
    assert call(instance, "GET", "/v1/balance_transactions", {"payout": po})["body"]["data"] == []
    assert "payout.canceled" in events_of(instance)
    # and a second cancel refuses the wrong state
    again = call(instance, "POST", f"/v1/payouts/{po}/cancel")
    assert again["status"] == 400
    assert again["body"]["error"]["message"] == (
        "This payout could not be canceled because it has a status of canceled. "
        "Only a payout with one of the following statuses may be canceled: pending."
    )


def test_settle_then_reverse(instance: seahaven.Instance) -> None:
    po = payout_id(instance, 4000)
    with instance.bulk() as ctx:
        from seahaven_stripe_world.billing import ledger

        ledger.settle_payout(ctx, po)
    original = call(instance, "GET", f"/v1/payouts/{po}")["body"]
    assert original["status"] == "paid"
    reversal = call(instance, "POST", f"/v1/payouts/{po}/reverse")["body"]
    assert reversal["amount"] == -4000  # the reversing payout is negative
    assert reversal["status"] == "paid"
    assert reversal["original_payout"] == po
    assert reversal["balance_transaction"].startswith("txn_")
    linked = call(instance, "GET", f"/v1/payouts/{po}")["body"]
    assert linked["reversed_by"] == reversal["id"]
    # the documented event order: updated for the original, created + paid
    # for the reversal
    tail = events_of(instance)[-3:]
    assert tail == ["payout.updated", "payout.created", "payout.paid"]
    # reverse refuses anything not paid
    fresh = payout_id(instance, 1000)
    refused = call(instance, "POST", f"/v1/payouts/{fresh}/reverse")
    assert refused["status"] == 400
    assert "has a status of pending" in refused["body"]["error"]["message"]


def test_fail_payout_is_the_fixture_surface(instance: seahaven.Instance) -> None:
    po = payout_id(instance, 4000)
    with instance.bulk() as ctx:
        from seahaven_stripe_world.billing import ledger

        ledger.fail_payout(ctx, po, failure_code="no_account")
    failed = call(instance, "GET", f"/v1/payouts/{po}")["body"]
    assert failed["status"] == "failed"
    assert failed["failure_code"] == "no_account"
    assert failed["failure_message"] == "Payout failed by no account."
    assert failed["failure_balance_transaction"].startswith("txn_")
    # the full charge net is back
    assert balance_available(instance) == 9680
    assert "payout.failed" in events_of(instance)
    # a paid payout is not failable — unwinding one is reverse's job
    settled = payout_id(instance, 1000)
    from seahaven_stripe_world.billing import ledger
    from seahaven_stripe_world.stripe_errors import StripeApiError

    with instance.bulk() as ctx:
        ledger.settle_payout(ctx, settled)
    with instance.bulk() as ctx, pytest.raises(StripeApiError, match="has a status of paid"):
        ledger.fail_payout(ctx, settled, failure_code="no_account")


# --- the reads ---------------------------------------------------------------------------


def test_update_is_metadata_only(instance: seahaven.Instance) -> None:
    po = payout_id(instance, 1000)
    updated = call(instance, "POST", f"/v1/payouts/{po}", {"metadata": {"k": "v"}})["body"]
    assert updated["metadata"] == {"k": "v"}
    assert updated["amount"] == 1000
    refused = call(instance, "POST", f"/v1/payouts/{po}", {"description": "nope"})
    assert refused["status"] == 400
    assert refused["body"]["error"]["message"] == "Received unknown parameter: description"


def test_lists_filters_and_404s(instance: seahaven.Instance) -> None:
    po = payout_id(instance, 1000)
    listed = call(instance, "GET", "/v1/payouts")["body"]
    assert listed["url"] == "/v1/payouts"
    assert [row["id"] for row in listed["data"]] == [po]
    assert call(instance, "GET", "/v1/payouts", {"status": "paid"})["body"]["data"] == []
    assert call(instance, "GET", "/v1/payouts", {"status": "bogus"})["body"]["data"] == []
    got = call(instance, "GET", f"/v1/payouts/{po}")["body"]
    assert got["id"] == po
    for method, path in (
        ("GET", "/v1/payouts/po_nope"),
        ("POST", "/v1/payouts/po_nope/cancel"),
        ("POST", "/v1/payouts/po_nope/reverse"),
    ):
        missing = call(instance, method, path)
        assert missing["status"] == 404
        assert missing["body"]["error"]["message"] == "No such payout: 'po_nope'"
        assert missing["body"]["error"]["param"] == "payout"
