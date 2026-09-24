"""Phase 2 tests: account startup, the livemode sweep, and the four-object
carve-out.

The source-grep guard runs first (it would have driven the sweep had it been
written before the code). The remaining tests exercise the runtime behaviour
of the centralized livemode injection, the account object shape, and the
derived error string in invoiceitems.
"""

import re
from pathlib import Path

import pytest
import seahaven

from conftest import BLANK_NOW, api_read, api_write, extract_path_params
from seahaven_stripe_world.errors import StripeToolError
from seahaven_stripe_world.serialize.fields import NO_LIVEMODE_OBJECTS
from seahaven_stripe_world.startup import ACCOUNT_ID
from seahaven_stripe_world.world import world

pytestmark = pytest.mark.seahaven(fixture=None, now=BLANK_NOW, clock_mode="fixed")

SRC_DIR = Path(__file__).resolve().parent.parent / "src" / "seahaven_stripe_world"

# --- helpers ----------------------------------------------------------------


def call(
    instance: seahaven.Instance,
    method: str,
    path: str,
    params: dict | None = None,
    *,
    livemode: bool = True,
):
    if livemode is True:
        # Default path uses conftest helpers (livemode=True).
        if method == "GET":
            return api_read(instance, path, params)
        return api_write(instance, method, path, params)
    # Sandbox path: resolve op_id and pass livemode=False directly.
    from seahaven_stripe_world.dispatch.router import ROUTER

    route = ROUTER.resolve(method, path)
    assert route is not None, f"no {method} route for {path}"
    merged = dict(params or {})
    merged.update(extract_path_params(route.pattern, path))
    tool_name = "stripe_api_read" if method == "GET" else "stripe_api_write"
    return instance.call(
        tool_name,
        stripe_api_operation_id=route.op_id,
        parameters=merged,
        stripe_context=ACCOUNT_ID,
        livemode=False,
    )


# --- source-grep guard -----------------------------------------------------


def test_no_hardcoded_livemode() -> None:
    """No resource module or billing module hardcodes ``"livemode": False``
    or ``"livemode": True`` in constants or inline dicts. The pattern is
    broad enough to catch both ``"livemode": False`` and ``"livemode":False``
    (with or without a space).

    Allowed files: this test file, the serializer (defines the exclusion set
    and the injection), and conformance cassettes (recorded data).
    """
    pattern = re.compile(r'"livemode"\s*:\s*(True|False|true|false)')
    allowed_stems = {"test_phase2_livemode", "fields"}
    hits: list[str] = []
    for py in sorted(SRC_DIR.rglob("*.py")):
        if py.stem in allowed_stems:
            continue
        text = py.read_text()
        for i, line in enumerate(text.splitlines(), 1):
            if pattern.search(line):
                hits.append(f"{py.relative_to(SRC_DIR)}:{i}: {line.strip()}")
    assert hits == [], "hardcoded livemode literals found:\n" + "\n".join(hits)


# --- livemode on regular objects -------------------------------------------


def test_livemode_true_on_objects(instance: seahaven.Instance) -> None:
    """Default instance (livemode=True): a customer carries livemode=True."""
    body = call(instance, "POST", "/v1/customers", {"email": "live@test"})
    assert body["livemode"] is True


def test_livemode_false_on_sandbox_instance() -> None:
    """An instance started with ``livemode=False`` produces objects with
    ``livemode=False``."""
    with world.instance(None, now=BLANK_NOW, startup={"livemode": False}) as inst:
        body = call(inst, "POST", "/v1/customers", {"email": "sandbox@test"}, livemode=False)
        assert body["livemode"] is False


# --- excluded objects carry no livemode ------------------------------------


def test_livemode_absent_on_excluded_objects(instance: seahaven.Instance) -> None:
    """The four excluded object types (balance_transaction, refund,
    subscription_item, discount) carry no ``livemode`` key."""
    assert (
        frozenset({"balance_transaction", "refund", "subscription_item", "discount"})
        == NO_LIVEMODE_OBJECTS
    )
    # Create a confirmed payment intent so a balance_transaction exists.
    cust = call(instance, "POST", "/v1/customers", {"email": "bt@test"})["id"]
    pm = call(
        instance,
        "POST",
        "/v1/payment_methods",
        {"type": "card", "card": {"token": "tok_visa"}},
    )["id"]
    call(instance, "POST", f"/v1/payment_methods/{pm}/attach", {"customer": cust})
    pi = call(
        instance,
        "POST",
        "/v1/payment_intents",
        {
            "customer": cust,
            "amount": 1000,
            "currency": "usd",
            "payment_method": pm,
            "confirm": True,
        },
    )
    charge = call(instance, "GET", f"/v1/charges/{pi['latest_charge']}")
    bt = call(instance, "GET", f"/v1/balance_transactions/{charge['balance_transaction']}")
    assert bt["object"] == "balance_transaction"
    assert "livemode" not in bt


# --- account object --------------------------------------------------------


def test_account_object_fresh_sandbox(instance: seahaven.Instance) -> None:
    """The account object matches the consistent fresh sandbox shape:
    charges/payouts disabled, empty capabilities, no metadata key."""
    result = instance.call("get_stripe_account_info")
    assert result["object"] == "account"
    assert result["charges_enabled"] is False
    assert result["payouts_enabled"] is False
    assert result["details_submitted"] is False
    assert result["capabilities"] == {}
    assert result["business_profile"]["name"] is None
    assert result["default_currency"] == "usd"
    assert result["country"] == "US"
    assert result["type"] == "standard"
    assert "metadata" not in result


def test_account_id_shape() -> None:
    """The account id is ``acct_`` followed by 16 alphanumeric characters."""
    assert re.fullmatch(r"acct_[A-Za-z0-9]{16}", ACCOUNT_ID)


# --- invoiceitem error derives livemode ------------------------------------


def test_invoiceitem_error_derives_livemode(instance: seahaven.Instance) -> None:
    """The 404 message for invoice items says ``livemode=true`` under the
    default live instance."""
    with pytest.raises(StripeToolError) as exc_info:
        call(instance, "GET", "/v1/invoiceitems/ii_nope")
    assert exc_info.value.status == 404
    assert "livemode=true" in exc_info.value.stripe_body["error"]["message"]


def test_invoiceitem_error_derives_livemode_sandbox() -> None:
    """Under a sandbox instance (``livemode=False``), the 404 says
    ``livemode=false``."""
    with world.instance(None, now=BLANK_NOW, startup={"livemode": False}) as inst:
        with pytest.raises(StripeToolError) as exc_info:
            call(inst, "GET", "/v1/invoiceitems/ii_nope", livemode=False)
        assert exc_info.value.status == 404
        assert "livemode=false" in exc_info.value.stripe_body["error"]["message"]
