"""The committed `big_co` fixture: Ostrander Systems (components/big_co.md §9).

The population is the user's scaled choice (`fixtures_src/big_co/shape.py`): about 380 customers,
the frozen file at 25 MB or less. Every scripted loose end of big_co.md §5 is asserted by company
name, as the tools and the database show it.
"""

import random
from pathlib import Path
from typing import Any

import pytest
import seahaven
from fixtures_src.accounts import OSTRANDER
from fixtures_src.big_co import EXPECTATIONS, NOW
from fixtures_src.big_co.shape import SERVICES
from fixtures_src.replay.checks import check_fixture
from fixtures_src.replay.clock import plus

from conftest import api_read, api_write
from seahaven_stripe_world import _time
from seahaven_stripe_world.errors import StripeToolError

pytestmark = pytest.mark.seahaven(fixture="big_co")

STATE = Path(__file__).resolve().parent.parent / "fixtures" / "big_co" / "state.sqlite"
TEST_MODE: dict[str, Any] = {"stripe_context": OSTRANDER.id, "livemode": False}


def _rows(inst: seahaven.Instance, sql: str, *params: Any) -> list[dict[str, Any]]:
    return inst.inspect().rows(sql, *params)


def _subs_of(inst: seahaven.Instance, company: str) -> list[dict[str, Any]]:
    return _rows(
        inst,
        "SELECT s.* FROM subscriptions s JOIN customers c ON c.id = s.customer"
        " WHERE c.name = ? ORDER BY s.x_seq",
        company,
    )


def _sub_of(inst: seahaven.Instance, company: str) -> dict[str, Any]:
    (sub,) = _subs_of(inst, company)
    return sub


def _disputes_of(inst: seahaven.Instance, company: str) -> list[dict[str, Any]]:
    return _rows(
        inst,
        "SELECT d.status, d.reason, d.created, d.amount, ch.description,"
        " json_extract(d.evidence_details, '$.due_by') AS due_by,"
        " json_extract(d.evidence_details, '$.has_evidence') AS has_evidence"
        " FROM disputes d JOIN charges ch ON ch.id = d.charge"
        " JOIN customers c ON c.id = ch.customer WHERE c.name = ? ORDER BY d.x_seq",
        company,
    )


def _end_of_day_unix(day: str) -> int:
    return _time.to_unix(f"{day}T23:59:59.000Z")


def test_file_size() -> None:
    assert STATE.stat().st_size <= 25_000_000


def test_expectations(instance: seahaven.Instance) -> None:
    """big_co.md §8 and the invariants and guards of functional spec §3.2, §3.4."""
    check_fixture(instance.inspect(), now=NOW, expect=EXPECTATIONS)


def test_account_identity(instance: seahaven.Instance) -> None:
    (entry,) = instance.call("list_available_accounts_or_orgs")["accounts"]
    assert entry["stripe_context"] == OSTRANDER.id
    assert entry["livemode"] is False
    info = instance.call("get_stripe_account_info")
    assert info["id"] == OSTRANDER.id
    assert info["settings"]["dashboard"]["display_name"] == "Ostrander Systems"
    assert info["business_profile"]["url"] == "https://ostranderfieldops.com"
    product = api_write(instance, "POST", "/v1/products", {"name": "Field kit"}, **TEST_MODE)
    price = api_write(
        instance,
        "POST",
        "/v1/prices",
        {"product": product["id"], "unit_amount": 1000, "currency": "usd"},
        **TEST_MODE,
    )
    assert price["livemode"] is False
    assert OSTRANDER.id.removeprefix("acct_")[-10:] in price["id"]


def test_opening_live_is_refused(world: seahaven.World) -> None:
    with pytest.raises(seahaven.WorldBug) as raised:
        world.instance("big_co", startup={"livemode": True})
    assert "test-mode data" in str(raised.value)


def test_dispute_queue(instance: seahaven.Instance) -> None:
    """Four disputed services payments awaiting a response, evidence due within seven days."""
    for company, reason, service, due_day in (
        ("Arroyo Air & Heat", "fraudulent", "On-site crew training day", "2026-09-03"),
        ("Tamarack Plumbing", "product_not_received", "Legacy data import", "2026-09-04"),
        ("Gridline Electric Co.", "fraudulent", "On-site crew training day", "2026-09-05"),
        ("Sandstone Mechanical", "product_not_received", "Technician app rollout session",
         "2026-09-06"),
    ):  # fmt: skip
        (dispute,) = _disputes_of(instance, company)
        assert dispute["status"] == "needs_response", company
        assert dispute["reason"] == reason
        assert dispute["description"] == service
        assert dispute["due_by"] == _end_of_day_unix(due_day)
        assert _sub_of(instance, company)["status"] == "active"


def test_every_dispute_is_on_a_services_payment(instance: seahaven.Instance) -> None:
    """Disputes come only from one-off payments the world confirmed on a dispute test card: each
    disputed charge has its PaymentIntent, a services description and no invoice."""
    disputed = _rows(
        instance,
        "SELECT ch.description, pi.status AS intent, ch.amount, d.amount AS disputed,"
        " (SELECT count(*) FROM invoices i WHERE i._charge = ch.id) AS invoices"
        " FROM disputes d JOIN charges ch ON ch.id = d.charge"
        " JOIN payment_intents pi ON pi.id = d.payment_intent ORDER BY d.x_seq",
    )
    (total,) = _rows(instance, "SELECT count(*) AS n FROM disputes")
    assert len(disputed) == total["n"]
    assert {d["description"] for d in disputed} <= {name for name, _amount in SERVICES}
    assert all(d["intent"] == "succeeded" and d["invoices"] == 0 for d in disputed)
    assert all(d["amount"] == d["disputed"] for d in disputed)
    (undisputed,) = _rows(
        instance,
        "SELECT count(*) AS n FROM payment_intents"
        " WHERE id NOT IN (SELECT payment_intent FROM disputes)",
    )
    assert undisputed["n"] > total["n"]  # most services payments are not disputed


def test_settled_disputes(instance: seahaven.Instance) -> None:
    (won,) = _disputes_of(instance, "Kestrel Ridge Heating")
    assert (won["status"], won["created"]) == ("won", "2026-07-08T16:00:00.000Z")
    (lost,) = _disputes_of(instance, "Millrace Electric")
    assert (lost["status"], lost["reason"]) == ("lost", "product_not_received")


def test_inquiries_and_review(instance: seahaven.Instance) -> None:
    (ridgeway,) = _disputes_of(instance, "Ridgeway Heating")
    assert (ridgeway["status"], ridgeway["has_evidence"]) == ("warning_needs_response", 0)
    assert ridgeway["created"] == "2026-08-15T16:00:00.000Z"
    (larkspur,) = _disputes_of(instance, "Larkspur Electric")
    assert (larkspur["status"], larkspur["has_evidence"]) == ("warning_under_review", 1)
    (coldwater,) = _disputes_of(instance, "Coldwater Plumbing Supply")
    assert (coldwater["status"], coldwater["reason"]) == ("under_review", "fraudulent")
    assert coldwater["has_evidence"] == 1


def test_overdue_enterprise_invoice(instance: seahaven.Instance) -> None:
    """A 60-seat annual contract at $85/tech/month, its renewal unpaid 45 days past due."""
    sub = _sub_of(instance, "Halvorsen Mechanical Group")
    assert (sub["status"], sub["collection_method"], sub["days_until_due"]) == (
        "active",
        "send_invoice",
        30,
    )
    invoices = _rows(
        instance,
        "SELECT i.status, i.total, i.due_date, json_extract(i.status_transitions, '$.paid_at')"
        " AS paid_at FROM invoices i JOIN customers c ON c.id = i.customer"
        " WHERE c.name = 'Halvorsen Mechanical Group' ORDER BY i.x_seq",
    )
    assert [(i["status"], i["total"]) for i in invoices] == [
        ("paid", 6_120_000),
        ("open", 6_120_000),
    ]
    assert invoices[0]["paid_at"] == _time.to_unix("2025-07-10T16:00:00.000Z")
    due = invoices[1]["due_date"]
    assert due == "2026-07-18T17:00:00.000Z"
    assert plus(due, days=44) < NOW < plus(due, days=45)


def test_next_ramp_step(instance: seahaven.Instance) -> None:
    """Cascade's ramp is in its first phase and steps from 80 to 120 seats within three days."""
    sub = _sub_of(instance, "Cascade Comfort Systems")
    assert (sub["status"], sub["collection_method"]) == ("active", "send_invoice")
    (schedule,) = api_read(
        instance, "/v1/subscription_schedules", {"customer": sub["customer"]}, **TEST_MODE
    )["data"]
    assert schedule["subscription"] == sub["id"]
    assert schedule["status"] == "active" and schedule["end_behavior"] == "release"
    assert schedule["default_settings"]["collection_method"] == "send_invoice"
    phases = schedule["phases"]
    assert [p["items"][0]["quantity"] for p in phases] == [80, 120, 160]
    assert [_time.from_unix(p["end_date"])[:10] for p in phases] == [
        "2026-09-04",
        "2026-12-04",
        "2027-06-04",
    ]
    step = _time.from_unix(schedule["current_phase"]["end_date"])
    assert step > NOW and step[:10] == plus(NOW, days=3)[:10]  # three days out, by the calendar


def test_churn_risk(instance: seahaven.Instance) -> None:
    sub = _sub_of(instance, "Copperline Mechanical")
    assert sub["status"] == "active" and sub["cancel_at_period_end"] == 1
    assert sub["cancel_at"] == "2026-09-13T15:00:00.000Z" == plus(NOW, days=12, hours=1)
    (seats,) = _rows(
        instance,
        "SELECT i.quantity, p.lookup_key FROM subscription_items i JOIN prices p"
        " ON p.id = i.price WHERE i.subscription = ? ORDER BY i.x_seq LIMIT 1",
        sub["id"],
    )
    assert seats == {"quantity": 48, "lookup_key": "business_annual"}
    assert '"feedback":"switched_service"' in sub["cancellation_details"]


def test_every_status_from_its_story(instance: seahaven.Instance) -> None:
    incomplete = _sub_of(instance, "Bristlecone Electric")
    assert incomplete["status"] == "incomplete"
    assert incomplete["created"] == plus(NOW, hours=-3)
    for company in ("Juniper Flats Plumbing", "Kettle Creek Electric", "Mesquite Drain & Sewer"):
        assert _sub_of(instance, company)["status"] == "incomplete_expired"
    for company in (
        "Bellwether Climate Services",
        "Northgate Electrical Contractors",
        "Pinyon Ridge Mechanical",
    ):
        paused = _sub_of(instance, company)
        assert paused["status"] == "paused"
        assert not _rows(
            instance, "SELECT id FROM payment_methods WHERE customer = ?", paused["customer"]
        )
    for company in ("Foxglove Plumbing", "Driftwood Heating & Air", "Yellowjacket Electric"):
        assert _sub_of(instance, company)["status"] == "unpaid"
    for company in ("Saltgrass Mechanical", "Quillback Plumbing", "Harrowgate Electric"):
        past_due = _sub_of(instance, company)
        assert past_due["status"] == "past_due"
        (invoice,) = _rows(
            instance,
            "SELECT status, next_payment_attempt FROM invoices WHERE id = ?",
            past_due["latest_invoice"],
        )
        assert invoice["status"] == "open" and invoice["next_payment_attempt"] > NOW


def test_draft_onboarding_invoices(instance: seahaven.Instance) -> None:
    drafts = _rows(
        instance,
        "SELECT c.name, i.total, i.created, i.automatically_finalizes_at FROM invoices i"
        " JOIN customers c ON c.id = i.customer WHERE i.status = 'draft'"
        " AND i.created > ? ORDER BY i.created",
        plus(NOW, hours=-1),
    )
    assert [d["name"] for d in drafts] == [
        "Thornfield Air Systems",
        "Wolf Creek Plumbing & Heating",
        "Lodestar Electrical Services",
        "Ironbark Mechanical",
        "Bluestem Comfort Co.",
    ]
    assert all(d["total"] == 149_900 and d["automatically_finalizes_at"] > NOW for d in drafts)


def test_latest_payout_is_pending(instance: seahaven.Instance) -> None:
    (latest,) = _rows(
        instance, "SELECT status, created, arrival_date FROM payouts ORDER BY x_seq DESC LIMIT 1"
    )
    assert latest["status"] == "pending"
    assert latest["created"] == "2026-08-31T15:00:00.000Z" and latest["arrival_date"] > NOW


def _active_customer(instance: seahaven.Instance) -> dict[str, Any]:
    active = _rows(
        instance,
        "SELECT c.id, c.name FROM customers c WHERE EXISTS (SELECT 1 FROM subscriptions s"
        " WHERE s.customer = c.id AND s.status = 'active') ORDER BY c.id",
    )
    return random.Random(2026).choice(active)


@pytest.mark.parametrize("pick", ["Halvorsen Mechanical Group", "a random active customer"])
def test_tools_carry_on(instance: seahaven.Instance, pick: str) -> None:
    """Functional spec §6: through the real MCP tools, the account keeps going from NOW."""
    if pick == "Halvorsen Mechanical Group":
        (customer,) = _rows(instance, "SELECT id, name FROM customers WHERE name = ?", pick)
    else:
        customer = _active_customer(instance)
    (before,) = _rows(
        instance,
        "SELECT invoice_prefix, next_invoice_sequence FROM customers WHERE id = ?",
        customer["id"],
    )
    (top,) = _rows(instance, "SELECT max(x_seq) AS n FROM invoices")

    api_write(
        instance,
        "POST",
        "/v1/invoiceitems",
        {"customer": customer["id"], "amount": 25_000, "currency": "usd"},
        **TEST_MODE,
    )
    draft = api_write(
        instance,
        "POST",
        "/v1/invoices",
        {"customer": customer["id"], "collection_method": "send_invoice", "days_until_due": 30},
        **TEST_MODE,
    )
    invoice = api_write(instance, "POST", f"/v1/invoices/{draft['id']}/finalize", **TEST_MODE)
    assert invoice["number"] == f"{before['invoice_prefix']}-{before['next_invoice_sequence']:04d}"
    assert invoice["livemode"] is False
    (row,) = _rows(instance, "SELECT x_seq FROM invoices WHERE id = ?", invoice["id"])
    assert row["x_seq"] > top["n"]

    found = api_read(
        instance, "/v1/customers/search", {"query": f'name:"{customer["name"]}"'}, **TEST_MODE
    )
    assert customer["id"] in [c["id"] for c in found["data"]]

    balance = api_read(instance, "/v1/balance", **TEST_MODE)
    (available,) = [b["amount"] for b in balance["available"] if b["currency"] == "usd"]
    assert available > 0
    payout = {"currency": "usd", "destination": OSTRANDER.bank_account_id}
    with pytest.raises(StripeToolError) as refused:
        api_write(instance, "POST", "/v1/payouts", {**payout, "amount": available + 1}, **TEST_MODE)
    assert refused.value.stripe_body["error"]["code"] == "balance_insufficient"
    paid_out = api_write(
        instance, "POST", "/v1/payouts", {**payout, "amount": available}, **TEST_MODE
    )
    assert paid_out["amount"] == available
