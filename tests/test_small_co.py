"""The committed `small_co` fixture (Wrenfield Labs, components/small_co.md §8): its headline
counts, its named characters as the tools show them, and that the recipe rebuilds its bytes."""

import copy
import importlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import seahaven
from fixtures_src.accounts import WRENFIELD
from fixtures_src.replay.checks import check_fixture
from fixtures_src.small_co import EXPECTATIONS, NOW
from fixtures_src.small_co.cast import CAST

from seahaven_stripe_world.dispatch.router import ROUTER
from seahaven_stripe_world.world import world

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "small_co"


@pytest.fixture(scope="module")
def inst() -> Iterator[seahaven.Instance]:
    with world.instance("small_co", clock_mode="fixed", startup={"livemode": False}) as instance:
        yield instance


def _read(inst: seahaven.Instance, path: str, params: dict[str, Any] | None = None) -> Any:
    route = ROUTER.resolve("GET", path)
    assert route is not None, path
    merged = dict(params or {})
    pattern_parts, path_parts = route.pattern.strip("/").split("/"), path.strip("/").split("/")
    for part, value in zip(pattern_parts, path_parts, strict=True):
        if part.startswith("{"):
            merged[part[1:-1]] = value
    return inst.call(
        "stripe_api_read",
        stripe_api_operation_id=route.op_id,
        parameters=merged,
        stripe_context=WRENFIELD.id,
        livemode=False,
    )


def _customer(inst: seahaven.Instance, email: str) -> dict[str, Any]:
    found = _read(inst, "/v1/customers", {"email": email})["data"]
    assert len(found) == 1, email
    return found[0]


def _subscriptions(inst: seahaven.Instance, email: str) -> list[dict[str, Any]]:
    customer = _customer(inst, email)["id"]
    return _read(inst, "/v1/subscriptions", {"customer": customer, "status": "all"})["data"]


def test_the_fixture_is_committed_at_now() -> None:
    fixtures = {frozen.id: frozen for frozen in world.fixtures()}
    assert fixtures["small_co"].now == NOW
    assert (FIXTURE_DIR / "state.sqlite").stat().st_size < 2 * 1024 * 1024


def test_expectations(inst: seahaven.Instance) -> None:
    """Functional spec §3.4 invariants, §3.2 guards and small_co.md §6 counts."""
    check_fixture(inst.inspect(), now=NOW, expect=EXPECTATIONS)
    assert len(CAST) == 45


def test_account_identity(inst: seahaven.Instance) -> None:
    account = inst.call("get_stripe_account_info")
    assert account["id"] == WRENFIELD.id
    listed = inst.call("list_available_accounts_or_orgs")
    assert WRENFIELD.id in str(listed)
    assert "Wrenfield" in str(listed)


def test_named_character_states(inst: seahaven.Instance) -> None:
    (daniela,) = _subscriptions(inst, "daniela@ruizviolin.com")
    assert daniela["status"] == "active"
    assert daniela["items"]["data"][0]["price"]["unit_amount"] == 9600
    assert daniela["items"]["data"][0]["current_period_end"] == 1788452400  # 2026-09-03T16:20Z

    (priya,) = _subscriptions(inst, "priya@natarajanpiano.com")
    assert priya["status"] == "active"
    assert priya["cancel_at_period_end"] is True

    (leo,) = _subscriptions(inst, "leo@marchettichem.com")
    assert leo["status"] == "past_due"
    invoice = _read(inst, f"/v1/invoices/{leo['latest_invoice']}")
    assert invoice["attempt_count"] == 2
    assert invoice["next_payment_attempt"] > 1788271200  # after NOW

    assert _subscriptions(inst, "sofia@lindqvistvoice.com")[0]["status"] == "trialing"
    assert _subscriptions(inst, "adaeze@okaforpiano.com")[0]["status"] == "paused"
    assert _subscriptions(inst, "victor@alvesswim.com")[0]["status"] == "incomplete_expired"
    assert _customer(inst, "nadia@haddadfrench.com")["balance"] < 0

    grace = _customer(inst, "grace@brightsidemusic.studio")
    (dispute,) = _read(inst, "/v1/disputes")["data"]
    charge = _read(inst, f"/v1/charges/{dispute['charge']}")
    assert charge["customer"] == grace["id"]
    assert dispute["status"] == "needs_response"
    assert dispute["reason"] == "fraudulent"
    assert dispute["evidence_details"]["due_by"] == 1788825599  # 2026-09-07T23:59:59Z

    jamal_charges = _read(
        inst, "/v1/charges", {"customer": _customer(inst, "jamal@carterdrums.com")["id"]}
    )
    assert [c["amount_refunded"] for c in jamal_charges["data"]] == [17000]

    chris = _read(inst, "/v1/customers", {"limit": 100})["data"]
    assert sum(1 for c in chris if c["name"] == "Chris Nguyen") == 2


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
