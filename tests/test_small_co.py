"""The committed `small_co` fixture (Wrenfield Labs, components/small_co.md §8): its identity, its
counts, its named characters as the tools show them, the account carrying on from `NOW`, and that
the recipe rebuilds its bytes.

Opened with no startup, as an eval opens it: the account row's own mode (test) applies.
"""

import copy
import importlib
import json
from pathlib import Path
from typing import Any

import pytest
import seahaven
from fixtures_src.accounts import WRENFIELD
from fixtures_src.replay.checks import check_fixture
from fixtures_src.small_co import EXPECTATIONS, NOW
from fixtures_src.small_co.cast import CAST

from conftest import api_read, api_write
from seahaven_stripe_world import _time
from seahaven_stripe_world.errors import StripeToolError
from seahaven_stripe_world.world import world

pytestmark = pytest.mark.seahaven(fixture="small_co")

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "small_co"
GOLDEN = Path(__file__).resolve().parent / "golden" / "sandbox_account.json"
TEST_MODE: dict[str, Any] = {"stripe_context": WRENFIELD.id, "livemode": False}
NOW_UNIX = _time.to_unix(NOW)


def _unix(minute: str) -> int:
    """`2026-09-03T16:20` as Unix seconds."""
    return _time.to_unix(f"{minute}:00.000Z")


def _read(inst: seahaven.Instance, path: str, params: dict[str, Any] | None = None) -> Any:
    return api_read(inst, path, params, **TEST_MODE)


def _customer(inst: seahaven.Instance, email: str) -> dict[str, Any]:
    (found,) = _read(inst, "/v1/customers", {"email": email})["data"]
    return found


def _subscriptions(inst: seahaven.Instance, email: str) -> list[dict[str, Any]]:
    customer = _customer(inst, email)["id"]
    return _read(inst, "/v1/subscriptions", {"customer": customer, "status": "all"})["data"]


def _subscription(inst: seahaven.Instance, email: str) -> dict[str, Any]:
    (sub,) = _subscriptions(inst, email)
    return sub


def _price(sub: dict[str, Any]) -> dict[str, Any]:
    (item,) = sub["items"]["data"]
    return item["price"]


def _period_end(sub: dict[str, Any]) -> int:
    (item,) = sub["items"]["data"]
    return item["current_period_end"]


def _default_card_last4(inst: seahaven.Instance, email: str) -> str:
    pm = _customer(inst, email)["invoice_settings"]["default_payment_method"]
    return _read(inst, f"/v1/payment_methods/{pm}")["card"]["last4"]


def test_the_fixture_is_committed_at_now() -> None:
    fixtures = {frozen.id: frozen for frozen in world.fixtures()}
    assert fixtures["small_co"].now == NOW
    assert (FIXTURE_DIR / "state.sqlite").stat().st_size < 2 * 1024 * 1024


def test_expectations(instance: seahaven.Instance) -> None:
    """Functional spec §3.4 invariants, §3.2 guards and small_co.md §6 counts."""
    check_fixture(instance.inspect(), now=NOW, expect=EXPECTATIONS)
    assert len(CAST) == 45


def test_account_identity(instance: seahaven.Instance) -> None:
    sandbox = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert instance.call("get_stripe_account_info") == WRENFIELD.object_over(sandbox)
    (entry,) = instance.call("list_available_accounts_or_orgs")["accounts"]
    assert entry == {"stripe_context": WRENFIELD.id, "livemode": False, "name": "Wrenfield Labs"}


def test_opening_live_is_refused() -> None:
    with pytest.raises(seahaven.WorldBug, match="test-mode data"):
        world.instance("small_co", startup={"livemode": True})


def test_new_ids_carry_wrenfield(instance: seahaven.Instance) -> None:
    customer = api_write(instance, "POST", "/v1/customers", {"name": "Ines Duarte"}, **TEST_MODE)
    assert customer["id"].startswith("cus_") and customer["livemode"] is False
    intent = api_write(
        instance, "POST", "/v1/payment_intents", {"amount": 1200, "currency": "usd"}, **TEST_MODE
    )
    # pi_ + version digit (1) + timestamp group (5) + account fragment (10) + random (8).
    assert intent["id"].removeprefix("pi_")[6:16] == WRENFIELD.id[-10:]


def test_the_founder_test(instance: seahaven.Instance) -> None:
    founder = _customer(instance, "test+launch@wrenfield.app")
    assert founder["name"] == "Test (do not bill)"
    assert _subscriptions(instance, "test+launch@wrenfield.app") == []
    (charge,) = _read(instance, "/v1/charges", {"customer": founder["id"]})["data"]
    assert (charge["amount"], charge["amount_refunded"], charge["refunded"]) == (100, 100, True)
    assert charge["created"] == _unix("2026-03-02T13:05")


@pytest.mark.parametrize(
    ("email", "renews"),
    [
        ("daniela@ruizviolin.com", "2026-09-03T16:20"),
        ("marcus@bellprep.net", "2026-09-04T13:05"),
        ("owen@fitzrunning.co", "2026-09-06T14:10"),
        ("hana@kobayashimath.com", "2026-09-07T01:30"),
        ("tomasz@wojcikguitar.com", "2026-09-08T22:15"),
    ],
)
def test_founding_renewals_this_week(instance: seahaven.Instance, email: str, renews: str) -> None:
    sub = _subscription(instance, email)
    assert sub["status"] == "active" and sub["cancel_at_period_end"] is False
    assert (_price(sub)["lookup_key"], _price(sub)["unit_amount"]) == ("founding_annual", 9600)
    assert _price(sub)["active"] is False  # archived at launch; the founders keep it
    assert _period_end(sub) == _unix(renews)


def test_owens_renewal_will_decline(instance: seahaven.Instance) -> None:
    assert _default_card_last4(instance, "owen@fitzrunning.co") == "0341"


def test_priya_cancels_at_period_end(instance: seahaven.Instance) -> None:
    priya = _subscription(instance, "priya@natarajanpiano.com")
    assert (priya["status"], priya["cancel_at_period_end"]) == ("active", True)
    assert priya["cancel_at"] == _period_end(priya) == _unix("2026-09-05T18:40")
    assert priya["canceled_at"] == _unix("2026-08-11T20:15")  # when she asked
    assert priya["cancellation_details"]["comment"] == "Retiring from teaching"


def test_graces_dispute(instance: seahaven.Instance) -> None:
    """Her one-off setup payment, on a dispute test card, is disputed as fraudulent; the Studio
    annual plan she bought the same afternoon with her own card is not."""
    grace = _customer(instance, "grace@brightsidemusic.studio")
    assert _subscription(instance, "grace@brightsidemusic.studio")["status"] == "active"
    (dispute,) = _read(instance, "/v1/disputes")["data"]
    assert (dispute["status"], dispute["reason"], dispute["amount"]) == (
        "needs_response",
        "fraudulent",
        4900,
    )
    assert dispute["evidence_details"]["due_by"] == _time.to_unix("2026-09-07T23:59:59.000Z")
    charge = _read(instance, f"/v1/charges/{dispute['charge']}")
    assert (charge["customer"], charge["description"]) == (grace["id"], "Done-for-you setup")
    assert charge["payment_method_details"]["card"]["last4"] == "0259"
    assert _default_card_last4(instance, "grace@brightsidemusic.studio") == "4242"


def test_leo_is_past_due_mid_dunning(instance: seahaven.Instance) -> None:
    leo = _subscription(instance, "leo@marchettichem.com")
    assert leo["status"] == "past_due"
    invoice = _read(instance, f"/v1/invoices/{leo['latest_invoice']}")
    assert invoice["status"] == "open" and invoice["attempt_count"] == 2
    assert invoice["created"] == _unix("2026-08-30T10:00")
    assert invoice["next_payment_attempt"] > NOW_UNIX
    assert _default_card_last4(instance, "leo@marchettichem.com") == "0341"


def test_trials_without_a_card(instance: seahaven.Instance) -> None:
    sofia = _subscription(instance, "sofia@lindqvistvoice.com")
    assert sofia["status"] == "trialing"
    assert sofia["trial_end"] == _unix("2026-09-03T15:00")
    assert sofia["trial_settings"]["end_behavior"]["missing_payment_method"] == "pause"
    sofia_card = _customer(instance, "sofia@lindqvistvoice.com")["invoice_settings"]
    assert sofia_card["default_payment_method"] is None
    adaeze = _subscription(instance, "adaeze@okaforpiano.com")
    assert adaeze["status"] == "paused"
    assert adaeze["trial_end"] == _unix("2026-08-15T18:00")


def test_nadia_holds_a_credit(instance: seahaven.Instance) -> None:
    nadia = _subscription(instance, "nadia@haddadfrench.com")
    assert (nadia["status"], _price(nadia)["lookup_key"]) == ("active", "solo_monthly")
    assert _customer(instance, "nadia@haddadfrench.com")["balance"] == -1260


def test_jamals_partial_refund(instance: seahaven.Instance) -> None:
    jamal = _subscription(instance, "jamal@carterdrums.com")
    assert (jamal["status"], _price(jamal)["lookup_key"]) == ("active", "solo_annual")
    customer = _customer(instance, "jamal@carterdrums.com")["id"]
    (charge,) = _read(instance, "/v1/charges", {"customer": customer})["data"]
    assert (charge["amount"], charge["amount_refunded"], charge["refunded"]) == (
        29000,
        17000,
        False,
    )


def test_anikas_setup_fee_rode_her_first_invoice(instance: seahaven.Instance) -> None:
    """Ordered a minute before she subscribed, the setup item was swept onto her first invoice,
    where `LAUNCH` took half off both lines (the item is discountable)."""
    customer = _customer(instance, "anika@sharmatutoring.com")["id"]
    anika = _subscription(instance, "anika@sharmatutoring.com")
    invoices = _read(instance, "/v1/invoices", {"subscription": anika["id"], "limit": 100})["data"]
    first = next(i for i in invoices if i["billing_reason"] == "subscription_create")
    assert [(line["description"], line["amount"]) for line in first["lines"]["data"]] == [
        ("Done-for-you setup", 4900),
        ("1 \u00d7 Wrenfield Studio (at $29.00 / month)", 2900),
    ]
    assert (first["status"], first["subtotal"], first["total"]) == ("paid", 7800, 3900)
    assert (
        _read(instance, "/v1/invoiceitems", {"customer": customer, "pending": True})["data"] == []
    )


def test_elenas_pending_item(instance: seahaven.Instance) -> None:
    """Ordered on 08-28, after her last renewal; her 09-10 renewal, after `NOW`, sweeps it."""
    elena = _subscription(instance, "elena@petrovadance.com")
    assert (elena["status"], _price(elena)["lookup_key"]) == ("active", "studio_monthly")
    assert _period_end(elena) == _unix("2026-09-10T17:00")
    customer = _customer(instance, "elena@petrovadance.com")["id"]
    items = _read(instance, "/v1/invoiceitems", {"customer": customer, "pending": True})["data"]
    assert [(i["amount"], i["description"], i["invoice"]) for i in items] == [
        (4900, "Done-for-you setup", None)
    ]
    from seahaven_stripe_world.billing import subscription_lifecycle

    with instance.bulk() as ctx:
        renewed = subscription_lifecycle.advance_cycle(ctx, elena["id"])
    renewal = _read(instance, f"/v1/invoices/{renewed['latest_invoice']}")
    assert [line["amount"] for line in renewal["lines"]["data"]] == [4900, 2900]
    assert (
        _read(instance, "/v1/invoiceitems", {"customer": customer, "pending": True})["data"] == []
    )


def test_chris_signed_up_twice(instance: seahaven.Instance) -> None:
    first = _subscription(instance, "chris@nguyentutoring.com")
    assert (first["status"], _price(first)["lookup_key"]) == ("active", "solo_monthly")
    second = _subscription(instance, "chris.nguyen@cnlessons.com")
    assert second["status"] == "canceled"
    assert second["cancellation_details"]["comment"] == "Duplicate account"
    assert second["canceled_at"] == _unix("2026-06-17T12:00")
    named = [
        c["email"]
        for c in _read(instance, "/v1/customers", {"limit": 100})["data"]
        if c["name"] == "Chris Nguyen"
    ]
    assert sorted(named) == ["chris.nguyen@cnlessons.com", "chris@nguyentutoring.com"]


def test_victor_expired(instance: seahaven.Instance) -> None:
    victor = _subscription(instance, "victor@alvesswim.com")
    assert victor["status"] == "incomplete_expired"
    assert victor["ended_at"] == _unix("2026-06-23T15:40")  # 23 hours after signing up


def test_tools_carry_on(instance: seahaven.Instance) -> None:
    """Functional spec §6: through the real MCP tools, the account keeps going from NOW."""
    daniela = _customer(instance, "daniela@ruizviolin.com")
    api_write(
        instance,
        "POST",
        "/v1/invoiceitems",
        {"customer": daniela["id"], "amount": 4900, "currency": "usd"},
        **TEST_MODE,
    )
    draft = api_write(
        instance,
        "POST",
        "/v1/invoices",
        {"customer": daniela["id"], "collection_method": "send_invoice", "days_until_due": 7},
        **TEST_MODE,
    )
    invoice = api_write(instance, "POST", f"/v1/invoices/{draft['id']}/finalize", **TEST_MODE)
    expected = f"{daniela['invoice_prefix']}-{daniela['next_invoice_sequence']:04d}"
    assert invoice["number"] == expected

    (top,) = instance.inspect().rows("SELECT max(x_seq) AS n FROM customers")
    newcomer = api_write(instance, "POST", "/v1/customers", {"name": "Ines Duarte"}, **TEST_MODE)
    (row,) = instance.inspect().rows("SELECT x_seq FROM customers WHERE id = ?", newcomer["id"])
    assert row["x_seq"] > top["n"]

    balance = _read(instance, "/v1/balance")
    (available,) = [b["amount"] for b in balance["available"] if b["currency"] == "usd"]
    assert available > 0
    payout = {"currency": "usd", "destination": WRENFIELD.bank_account_id}
    with pytest.raises(StripeToolError) as refused:
        api_write(instance, "POST", "/v1/payouts", {**payout, "amount": available + 1}, **TEST_MODE)
    assert refused.value.stripe_body["error"]["code"] == "balance_insufficient"
    paid_out = api_write(
        instance, "POST", "/v1/payouts", {**payout, "amount": available}, **TEST_MODE
    )
    assert paid_out["amount"] == available

    found = _read(
        instance, "/v1/customers/search", {"query": 'email:"grace@brightsidemusic.studio"'}
    )
    assert [c["name"] for c in found["data"]] == ["Grace Adeyemi"]


def test_rebuilding_gives_the_committed_bytes(tmp_path: Path) -> None:
    """The recipe is deterministic: a rebuild writes the committed state byte for byte."""
    generate = importlib.import_module("fixtures_src.generate")
    elsewhere = copy.copy(world)
    elsewhere.fixtures_dir = tmp_path / "fixtures"
    elsewhere.fixtures_dir.mkdir()
    rebuilt = generate.build("small_co", world=elsewhere)
    assert (rebuilt.dir / "state.sqlite").read_bytes() == (
        FIXTURE_DIR / "state.sqlite"
    ).read_bytes()
