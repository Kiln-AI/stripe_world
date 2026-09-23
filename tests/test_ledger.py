"""The balance ledger (Phase 11): the rows every money movement leaves, the
computed `/v1/balance` read over them, the reads' filters and expansions, and
the ledger invariants — pinned by the Phase 11 probes and cassette 11 at
`2026-08-26.dahlia`."""

import pytest
import seahaven

from conftest import BLANK_NOW
from seahaven_stripe_world.errors import StripeToolError

pytestmark = pytest.mark.seahaven(fixture=None, now=BLANK_NOW)


def call(instance: seahaven.Instance, method: str, path: str, params: dict | None = None):
    if method == "GET":
        return instance.call("stripe_api_read", path=path, params=params)
    return instance.call("stripe_api_write", method=method, path=path, params=params)


def confirmed_intent(
    instance: seahaven.Instance, amount: int = 5000, token: str = "tok_visa", **extra
) -> dict:
    cus = call(instance, "POST", "/v1/customers", {"email": "lg@example.test"})["id"]
    pm = call(instance, "POST", "/v1/payment_methods", {"type": "card", "card": {"token": token}})[
        "id"
    ]
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


def settle(instance: seahaven.Instance) -> None:
    """Backdate every ledger row's `available_on` to its own `created` — the
    fixture generator's frozen-history trick, applied to a live test instance
    so the available/pending split is exercisable under the frozen clock."""
    with instance.bulk() as ctx:
        ctx.db.execute("UPDATE balance_transactions SET available_on = created")
        ctx.db.execute(
            "UPDATE balance_transactions SET status = 'available'"
            " WHERE available_on <= ?",  # every row, now that both are past
            ctx.clock.iso(),
        )


def balance_of(instance: seahaven.Instance) -> dict:
    return call(instance, "GET", "/v1/balance")


def ledger_rows(instance: seahaven.Instance, where: str = "1 = 1") -> list[dict]:
    return instance.inspect().rows(
        f"SELECT * FROM balance_transactions WHERE {where} ORDER BY x_seq"
    )


# --- the charge's row -----------------------------------------------------------------


def test_the_charge_bt(instance: seahaven.Instance) -> None:
    pi = confirmed_intent(instance, 4400, description="a widget")
    charge = call(instance, "GET", f"/v1/charges/{pi['latest_charge']}")
    bt = call(instance, "GET", f"/v1/balance_transactions/{charge['balance_transaction']}")
    assert bt["object"] == "balance_transaction"
    assert bt["type"] == "charge"  # the modern type, not `payment`
    assert bt["reporting_category"] == "charge"
    assert bt["amount"] == 4400
    assert bt["currency"] == "usd"
    assert bt["description"] == "a widget"  # the charge's own, recorded
    assert bt["source"] == charge["id"]
    assert bt["balance_type"] == "payments"
    assert bt["exchange_rate"] is None
    assert bt["net"] == bt["amount"] - bt["fee"]  # the spec's own formula
    assert bt["fee_details"] == [
        {
            "amount": bt["fee"],
            "application": None,
            "currency": "usd",
            "description": "Stripe processing fees",
            "type": "stripe_fee",
        }
    ]
    # fresh money is pending: the settlement window has not passed
    assert bt["status"] == "pending"
    assert "livemode" not in bt  # one of the four objects without it


def test_the_default_fee_schedule_is_290_plus_30(instance: seahaven.Instance) -> None:
    pi = confirmed_intent(instance, 10_000)
    charge = call(instance, "GET", f"/v1/charges/{pi['latest_charge']}")
    bt = call(instance, "GET", f"/v1/balance_transactions/{charge['balance_transaction']}")
    assert bt["fee"] == 320  # 2.90% of 100.00 = 290, plus the 30c fixed
    assert bt["net"] == 9680


def test_the_hold_has_no_row_until_capture_and_then_the_captured_amount(
    instance: seahaven.Instance,
) -> None:
    pi = confirmed_intent(instance, 6500, capture_method="manual")
    held = call(instance, "GET", f"/v1/charges/{pi['latest_charge']}")
    assert held["balance_transaction"] is None  # a hold moves nothing
    assert not ledger_rows(instance, f"source = '{held['id']}'")
    call(instance, "POST", f"/v1/payment_intents/{pi['id']}/capture", {"amount_to_capture": 5000})
    captured = call(instance, "GET", f"/v1/charges/{pi['latest_charge']}")
    assert captured["balance_transaction"].startswith("txn_")
    bt = call(instance, "GET", f"/v1/balance_transactions/{captured['balance_transaction']}")
    assert bt["amount"] == 5000  # the captured funds, not the authorized


# --- the refund's row ------------------------------------------------------------------


def test_the_refund_bt(instance: seahaven.Instance) -> None:
    pi = confirmed_intent(instance, 5000, description="scenario eleven")
    refund = call(instance, "POST", "/v1/refunds", {"charge": pi["latest_charge"], "amount": 1400})
    assert refund["balance_transaction"].startswith("txn_")
    bt = call(instance, "GET", f"/v1/balance_transactions/{refund['balance_transaction']}")
    assert bt["type"] == "refund"  # recorded; resolves the legacy pair question
    assert bt["reporting_category"] == "refund"
    assert bt["amount"] == -1400
    assert bt["fee"] == 0
    assert bt["fee_details"] == []
    assert bt["net"] == -1400
    assert bt["source"] == refund["id"]
    assert bt["description"] == "REFUND FOR CHARGE (scenario eleven)"
    # an undescribed charge names no parens — the declared corner
    pi2 = confirmed_intent(instance, 3000)
    refund2 = call(instance, "POST", "/v1/refunds", {"charge": pi2["latest_charge"]})
    bt2 = call(instance, "GET", f"/v1/balance_transactions/{refund2['balance_transaction']}")
    assert bt2["description"] == "REFUND FOR CHARGE"


def test_a_pending_refund_writes_no_row(instance: seahaven.Instance) -> None:
    # the async-success card (…7726) begins refunds pending; a pending refund
    # reserves nothing on a frozen clock, so it carries no ledger row
    cus = call(instance, "POST", "/v1/customers", {"email": "lg-p@example.test"})["id"]
    pm = call(
        instance,
        "POST",
        "/v1/payment_methods",
        {
            "type": "card",
            "card": {
                "number": "4000000000007726",
                "exp_month": 9,
                "exp_year": 2027,
                "cvc": "123",
            },
        },
    )["id"]
    call(instance, "POST", f"/v1/payment_methods/{pm}/attach", {"customer": cus})
    pi = call(
        instance,
        "POST",
        "/v1/payment_intents",
        {"amount": 7777, "currency": "usd", "customer": cus, "payment_method": pm, "confirm": True},
    )
    refund = call(instance, "POST", "/v1/refunds", {"charge": pi["latest_charge"]})
    assert refund["status"] == "pending"
    assert refund["balance_transaction"] is None
    assert not ledger_rows(instance, f"source = '{refund['id']}'")


# --- the dispute's rows ----------------------------------------------------------------


def chargeback(
    instance: seahaven.Instance, amount: int = 3000, token: str = "tok_visa_createDispute"
):
    cus = call(instance, "POST", "/v1/customers", {"email": "lg-d@example.test"})["id"]
    pm = call(instance, "POST", "/v1/payment_methods", {"type": "card", "card": {"token": token}})[
        "id"
    ]
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
        },
    )
    dispute = call(instance, "GET", f"/v1/charges/{pi['latest_charge']}/dispute")
    return pi, dispute


def test_the_chargeback_withdrawal_and_the_win_reversal(instance: seahaven.Instance) -> None:
    _, dispute = chargeback(instance, 3000)
    [withdrawal] = dispute["balance_transactions"]
    assert withdrawal["type"] == "adjustment"
    assert withdrawal["reporting_category"] == "dispute"
    assert withdrawal["amount"] == -3000
    assert withdrawal["fee"] == 1500  # the received fee, recorded
    assert withdrawal["net"] == -4500
    assert withdrawal["description"] == f"Chargeback withdrawal for {dispute['charge']}"
    assert withdrawal["source"] == dispute["id"]
    assert withdrawal["fee_details"] == [
        {
            "amount": 1500,
            "application": None,
            "currency": "usd",
            "description": "Dispute fee",
            "type": "stripe_fee",
        }
    ]
    won = call(
        instance,
        "POST",
        f"/v1/disputes/{dispute['id']}",
        {"evidence": {"uncategorized_text": "winning_evidence"}},
    )
    assert won["status"] == "won"
    assert len(won["balance_transactions"]) == 2
    withdrawal, reversal = won["balance_transactions"]  # x_seq order
    assert withdrawal["amount"] == -3000
    assert reversal["type"] == "adjustment"
    assert reversal["reporting_category"] == "dispute_reversal"
    assert reversal["amount"] == 3000
    assert reversal["fee"] == 0  # the received fee is KEPT — the recording's
    # correction of the functional spec's two-fee reading
    assert reversal["net"] == 3000
    assert reversal["description"] == f"Chargeback reversal for {dispute['charge']}"
    assert reversal["fee_details"] == []


def test_the_inquiry_writes_nothing_until_escalated(instance: seahaven.Instance) -> None:
    _, inquiry = chargeback(instance, 2100, token="tok_visa_createDisputeInquiry")
    assert inquiry["status"] == "warning_needs_response"
    assert inquiry["balance_transactions"] == []
    assert not ledger_rows(instance, f"source = '{inquiry['id']}'")
    escalated = call(
        instance,
        "POST",
        f"/v1/disputes/{inquiry['id']}",
        {"evidence": {"uncategorized_text": "escalate_inquiry_evidence"}},
    )
    assert escalated["status"] == "needs_response"
    [withdrawal] = escalated["balance_transactions"]
    assert withdrawal["amount"] == -2100
    assert withdrawal["fee"] == 1500


def test_a_lost_dispute_keeps_its_withdrawal_only(instance: seahaven.Instance) -> None:
    _, dispute = chargeback(instance, 1500)
    lost = call(instance, "POST", f"/v1/disputes/{dispute['id']}/close")
    assert lost["status"] == "lost"
    assert len(lost["balance_transactions"]) == 1  # no reversal on a loss


# --- the computed balance --------------------------------------------------------------


def test_the_balance_on_an_empty_ledger(instance: seahaven.Instance) -> None:
    body = balance_of(instance)
    assert body == {"object": "balance", "livemode": True, "available": [], "pending": []}


def test_the_balance_split_and_the_draw_down(instance: seahaven.Instance) -> None:
    confirmed_intent(instance, 5000)  # pending under the T+2 default
    body = balance_of(instance)
    assert body["available"] == []
    assert len(body["pending"]) == 1
    entry = body["pending"][0]
    assert entry["currency"] == "usd"
    assert entry["amount"] == 5000 - (5000 * 290 // 10_000 + 30)
    assert entry["source_types"] == {"card": entry["amount"]}
    settle(instance)
    body = balance_of(instance)
    assert body["pending"] == []
    assert body["available"] == [entry]


# --- the reads ------------------------------------------------------------------------


def test_the_source_filter_and_the_dispute_quirk(instance: seahaven.Instance) -> None:
    pi, dispute = chargeback(instance, 3000)
    listed = call(instance, "GET", "/v1/balance_transactions", {"source": pi["latest_charge"]})
    assert [row["source"] for row in listed["data"]] == [pi["latest_charge"]]
    assert listed["url"] == "/v1/balance_transactions"
    # the quirk (recorded, cassette 11): a dispute id never matches, though
    # the withdrawal row's own field names the dispute
    assert (
        call(instance, "GET", "/v1/balance_transactions", {"source": dispute["id"]})["data"] == []
    )


def test_the_type_and_currency_filters_answer_empty_pages(instance: seahaven.Instance) -> None:
    confirmed_intent(instance, 1200)
    assert call(instance, "GET", "/v1/balance_transactions", {"type": "bogus_type"})["data"] == []
    assert call(instance, "GET", "/v1/balance_transactions", {"currency": "eur"})["data"] == []
    listed = call(instance, "GET", "/v1/balance_transactions", {"type": "charge"})
    assert [row["type"] for row in listed["data"]] == ["charge"]


def test_the_payout_filter_validates_existence(instance: seahaven.Instance) -> None:
    with pytest.raises(StripeToolError) as exc_info:
        call(instance, "GET", "/v1/balance_transactions", {"payout": "po_nope"})
    assert exc_info.value.status == 400
    assert exc_info.value.stripe_body["error"]["code"] == "resource_missing"
    assert "No such payout" in exc_info.value.message
    assert exc_info.value.stripe_body["error"]["param"] == "payout"


def test_retrieve_and_its_404(instance: seahaven.Instance) -> None:
    pi = confirmed_intent(instance, 900)
    charge = call(instance, "GET", f"/v1/charges/{pi['latest_charge']}")
    got = call(instance, "GET", f"/v1/balance_transactions/{charge['balance_transaction']}")
    assert got["id"] == charge["balance_transaction"]
    with pytest.raises(StripeToolError) as exc_info:
        call(instance, "GET", "/v1/balance_transactions/txn_nope")
    assert exc_info.value.status == 404
    assert "No such balance transaction" in exc_info.value.message
    assert exc_info.value.stripe_body["error"]["param"] == "id"


def test_expand_source_is_polymorphic(instance: seahaven.Instance) -> None:
    pi, dispute = chargeback(instance, 3000)
    charge = call(instance, "GET", f"/v1/charges/{pi['latest_charge']}")
    bt = call(
        instance,
        "GET",
        f"/v1/balance_transactions/{charge['balance_transaction']}",
        {"expand": ["source"]},
    )
    assert bt["source"]["object"] == "charge"
    assert bt["source"]["id"] == charge["id"]
    [withdrawal] = dispute["balance_transactions"]
    dispute_bt = call(
        instance, "GET", f"/v1/balance_transactions/{withdrawal['id']}", {"expand": ["source"]}
    )
    assert dispute_bt["source"]["object"] == "dispute"
    assert dispute_bt["source"]["id"] == dispute["id"]


# --- the invariants (the queries an eval reward function runs) -------------------------


def test_the_currency_symbol_tables_are_in_sync() -> None:
    """`ledger._CURRENCY_SYMBOLS` mirrors `refunds._CURRENCY_SYMBOLS` by hand
    (importing it would be a cycle — refunds imports the ledger); this is the
    guard that keeps the copy from drifting, so an HKD `balance_insufficient`
    message renders `$40.00` exactly like the refund messages do."""
    from seahaven_stripe_world.billing import ledger
    from seahaven_stripe_world.resources import refunds

    assert dict(ledger._CURRENCY_SYMBOLS) == dict(refunds._CURRENCY_SYMBOLS)


def test_an_unknown_balance_transaction_type_is_a_world_bug(
    instance: seahaven.Instance,
) -> None:
    """`record`'s type check is the never-invent-a-type rule (billing_engine
    §6): a value outside the 50-member set is world code inventing behavior
    Stripe does not have, and fails at the author, never at the agent."""
    from seahaven_stripe_world.billing import ledger

    with (
        instance.bulk() as ctx,
        pytest.raises(seahaven.WorldBug, match=r"unknown balance_transaction\.type"),
    ):
        ledger.record(
            ctx,
            type_="not_a_real_type",
            amount=100,
            fee=0,
            currency="usd",
            source_id=None,
            description=None,
        )


def test_net_is_amount_minus_fee_everywhere(instance: seahaven.Instance) -> None:
    chargeback(instance, 3000)
    clean = confirmed_intent(instance, 2000)
    call(instance, "POST", "/v1/refunds", {"charge": clean["latest_charge"], "amount": 500})
    bad = [row["id"] for row in ledger_rows(instance) if row["net"] != row["amount"] - row["fee"]]
    assert bad == []


def test_every_captured_charge_has_exactly_one_row(instance: seahaven.Instance) -> None:
    confirmed_intent(instance, 4400)
    confirmed_intent(instance, 6500, capture_method="manual")
    rows = instance.inspect().rows(
        "SELECT c.id AS charge, c.amount_captured, c.balance_transaction, COUNT(b.id) AS rows_"
        " FROM charges c LEFT JOIN balance_transactions b ON b.source = c.id"
        " WHERE c.captured = 1 GROUP BY c.id"
    )
    assert len(rows) == 1  # the hold is uncaptured
    charge = rows[0]
    assert charge["rows_"] == 1
    assert charge["balance_transaction"] is not None
    [bt] = instance.inspect().rows(
        "SELECT amount FROM balance_transactions WHERE source = ?", charge["charge"]
    )
    assert bt["amount"] == charge["amount_captured"]


def test_every_source_resolves_in_exactly_one_table(instance: seahaven.Instance) -> None:
    chargeback(instance, 3000)
    clean = confirmed_intent(instance, 800)
    call(instance, "POST", "/v1/refunds", {"charge": clean["latest_charge"], "amount": 100})
    for row in ledger_rows(instance):
        [hit] = instance.inspect().rows(
            "SELECT (SELECT count(*) FROM charges WHERE id = ?)"
            " + (SELECT count(*) FROM refunds WHERE id = ?)"
            " + (SELECT count(*) FROM disputes WHERE id = ?)"
            " + (SELECT count(*) FROM payouts WHERE id = ?) AS n",
            row["source"],
            row["source"],
            row["source"],
            row["source"],
        )
        assert hit["n"] == 1, row["source"]


def test_the_balance_equals_the_ledger_sums(instance: seahaven.Instance) -> None:
    confirmed_intent(instance, 4400)
    chargeback(instance, 3000)
    clean = confirmed_intent(instance, 2000)
    call(instance, "POST", "/v1/refunds", {"charge": clean["latest_charge"], "amount": 700})
    settle(instance)
    body = balance_of(instance)
    [ledger] = instance.inspect().rows("SELECT SUM(net) AS total FROM balance_transactions")
    assert body["available"][0]["amount"] == ledger["total"]
    assert body["pending"] == []
