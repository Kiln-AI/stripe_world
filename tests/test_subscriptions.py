"""The subscriptions slice's routed surface, through the real four-tool
chain. Bodies, defaults, filters, refusals (verbatim) and events — pinned
by the Phase 12 probes and cassette 12 at `2026-08-26.dahlia`."""

import pytest
import seahaven

from conftest import BLANK_NOW, api_read, api_write
from seahaven_stripe_world.errors import StripeToolError

pytestmark = pytest.mark.seahaven(fixture=None, now=BLANK_NOW)


def call(instance: seahaven.Instance, method: str, path: str, params: dict | None = None):
    if method == "GET":
        return api_read(instance, path, params)
    return api_write(instance, method, path, params)


def setup_catalog(instance: seahaven.Instance) -> tuple[str, str, str]:
    cus = call(instance, "POST", "/v1/customers", {"email": "s@example.test"})["id"]
    pm = call(
        instance, "POST", "/v1/payment_methods", {"type": "card", "card": {"token": "tok_visa"}}
    )["id"]
    call(instance, "POST", f"/v1/payment_methods/{pm}/attach", {"customer": cus})
    call(
        instance,
        "POST",
        f"/v1/customers/{cus}",
        {"invoice_settings": {"default_payment_method": pm}},
    )
    prod = call(instance, "POST", "/v1/products", {"name": "sub suite"})["id"]
    price = call(
        instance,
        "POST",
        "/v1/prices",
        {
            "product": prod,
            "unit_amount": 2000,
            "currency": "cad",
            "recurring": {"interval": "month"},
        },
    )["id"]
    return cus, pm, price


# --- the created body's constants -------------------------------------------------------


def test_the_created_body_constants(instance: seahaven.Instance) -> None:
    cus, _, price = setup_catalog(instance)
    body = call(
        instance, "POST", "/v1/subscriptions", {"customer": cus, "items": [{"price": price}]}
    )
    assert body["object"] == "subscription"
    assert body["livemode"] is True
    assert body["invoice_settings"] == {
        "account_tax_ids": None,
        "custom_fields": None,
        "description": None,
        "footer": None,
        "issuer": {"type": "self"},
    }
    assert body["payment_settings"] == {
        "payment_method_options": None,
        "payment_method_types": None,
        "save_default_payment_method": "off",
    }
    assert body["cancellation_details"] == {
        "comment": None,
        "feedback": None,
        "feedback_option": None,
        "reason": None,
    }
    assert body["trial_settings"] == {"end_behavior": {"missing_payment_method": "create_invoice"}}
    assert body["billing_schedules"] == []
    assert body["discounts"] == []
    assert body["default_tax_rates"] == []
    assert body["billing_mode"]["type"] == "classic"
    for none in (
        "application",
        "application_fee_percent",
        "cancel_at",
        "canceled_at",
        "customer_account",
        "days_until_due",
        "default_source",
        "description",
        "ended_at",
        "managed_payments",
        "next_pending_invoice_item_invoice",
        "on_behalf_of",
        "pause_collection",
        "pending_setup_intent",
        "pending_update",
        "schedule",
        "test_clock",
        "transfer_data",
    ):
        assert body[none] is None, none
    # the live-only echoes the pinned spec does not declare
    assert "plan" not in body
    assert "quantity" not in body
    assert "current_period_end" not in body


def test_the_items_envelope(instance: seahaven.Instance) -> None:
    cus, _, price = setup_catalog(instance)
    body = call(
        instance, "POST", "/v1/subscriptions", {"customer": cus, "items": [{"price": price}]}
    )
    envelope = body["items"]
    assert envelope["object"] == "list"
    assert envelope["has_more"] is False
    assert envelope["url"] == f"/v1/subscription_items?subscription={body['id']}"
    item = envelope["data"][0]
    assert item["object"] == "subscription_item"
    assert item["subscription"] == body["id"]
    assert item["price"]["id"] == price  # always-inflated
    assert item["price"]["unit_amount"] == 2000
    assert "livemode" not in item
    assert "current_trial" not in item
    assert "plan" not in item


def test_metadata_rides_both_surfaces(instance: seahaven.Instance) -> None:
    cus, _, price = setup_catalog(instance)
    body = call(
        instance,
        "POST",
        "/v1/subscriptions",
        {"customer": cus, "items": [{"price": price}], "metadata": {"a": "1"}},
    )
    assert body["metadata"] == {"a": "1"}
    body = call(instance, "POST", f"/v1/subscriptions/{body['id']}", {"metadata": {"b": "2"}})
    assert body["metadata"] == {"a": "1", "b": "2"}
    body = call(instance, "POST", f"/v1/subscriptions/{body['id']}", {"metadata": {"a": None}})
    assert body["metadata"] == {"b": "2"}


# --- lists -------------------------------------------------------------------------------


def test_lists_and_filters(instance: seahaven.Instance) -> None:
    cus, _, price = setup_catalog(instance)
    body = call(
        instance, "POST", "/v1/subscriptions", {"customer": cus, "items": [{"price": price}]}
    )
    listed = call(instance, "GET", "/v1/subscriptions", {"customer": cus})
    assert [item["id"] for item in listed["data"]] == [body["id"]]
    assert listed["url"] == "/v1/subscriptions"
    listed = call(instance, "GET", "/v1/subscriptions", {"status": "active"})
    assert body["id"] in [item["id"] for item in listed["data"]]
    listed = call(instance, "GET", "/v1/subscriptions", {"status": "all"})
    assert body["id"] in [item["id"] for item in listed["data"]]
    listed = call(instance, "GET", "/v1/subscriptions", {"price": price})
    assert body["id"] in [item["id"] for item in listed["data"]]
    with pytest.raises(StripeToolError) as exc_info:
        call(instance, "GET", "/v1/subscriptions", {"status": "bogus"})
    assert exc_info.value.status == 400
    error = exc_info.value.stripe_body["error"]
    assert "code" not in error
    assert error["message"] == (
        "Invalid status: must be one of active, past_due, unpaid, canceled, incomplete, "
        "incomplete_expired, trialing, paused, all, or ended"
    )


def test_lists_are_newest_first(instance: seahaven.Instance) -> None:
    cus, _, price = setup_catalog(instance)
    first = call(
        instance, "POST", "/v1/subscriptions", {"customer": cus, "items": [{"price": price}]}
    )
    second = call(
        instance, "POST", "/v1/subscriptions", {"customer": cus, "items": [{"price": price}]}
    )
    listed = call(instance, "GET", "/v1/subscriptions", {"customer": cus})
    assert [item["id"] for item in listed["data"]] == [second["id"], first["id"]]


# --- the recorded refusals -----------------------------------------------------------------


def test_the_recorded_refusals(instance: seahaven.Instance) -> None:
    cus, _, price = setup_catalog(instance)
    # pending_if_incomplete is update-only
    with pytest.raises(StripeToolError) as exc_info:
        call(
            instance,
            "POST",
            "/v1/subscriptions",
            {
                "customer": cus,
                "items": [{"price": price}],
                "payment_behavior": "pending_if_incomplete",
            },
        )
    error = exc_info.value.stripe_body["error"]
    assert exc_info.value.status == 400
    assert error["message"] == (
        "Setting `payment_behavior` to `pending_if_incomplete` has no effect when creating "
        "a subscription."
    )
    assert error["param"] == "payment_behavior"
    # a bad literal
    with pytest.raises(StripeToolError) as exc_info:
        call(
            instance,
            "POST",
            "/v1/subscriptions",
            {"customer": cus, "items": [{"price": price}], "payment_behavior": "bogus"},
        )
    error = exc_info.value.stripe_body["error"]
    assert "code" not in error
    assert error["param"] == "payment_behavior"
    # duplicate price
    with pytest.raises(StripeToolError) as exc_info:
        call(
            instance,
            "POST",
            "/v1/subscriptions",
            {"customer": cus, "items": [{"price": price}, {"price": price}]},
        )
    error = exc_info.value.stripe_body["error"]
    assert error["param"] == "plan"
    assert error["message"] == (
        "Cannot create a Subscription with multiple Subscription Items with "
        f"the same Price: {price}"
    )
    # missing items
    with pytest.raises(StripeToolError) as exc_info:
        call(instance, "POST", "/v1/subscriptions", {"customer": cus})
    error = exc_info.value.stripe_body["error"]
    assert error["code"] == "parameter_missing"
    assert error["param"] == "items"
    # unknown price
    with pytest.raises(StripeToolError) as exc_info:
        call(
            instance,
            "POST",
            "/v1/subscriptions",
            {"customer": cus, "items": [{"price": "price_nope"}]},
        )
    error = exc_info.value.stripe_body["error"]
    assert error["code"] == "resource_missing"
    assert error["param"] == "items[0][price]"
    assert error["message"] == "No such price: 'price_nope'"
    # unknown customer: the 404 a path id earns
    with pytest.raises(StripeToolError) as exc_info:
        call(
            instance,
            "POST",
            "/v1/subscriptions",
            {"customer": "cus_nope", "items": [{"price": price}]},
        )
    assert exc_info.value.status == 404
    assert exc_info.value.stripe_body["error"]["param"] == "customer"
    # days_until_due guards
    with pytest.raises(StripeToolError) as exc_info:
        call(
            instance,
            "POST",
            "/v1/subscriptions",
            {"customer": cus, "items": [{"price": price}], "days_until_due": 5},
        )
    assert exc_info.value.stripe_body["error"]["message"] == (
        "You can only specify 'days_until_due' if invoice collection method is 'send_invoice'."
    )
    with pytest.raises(StripeToolError) as exc_info:
        call(
            instance,
            "POST",
            "/v1/subscriptions",
            {"customer": cus, "items": [{"price": price}], "collection_method": "send_invoice"},
        )
    assert exc_info.value.stripe_body["error"]["message"] == (
        "If invoice collection method is 'send_invoice', you must specify 'days_until_due'."
    )
    # trial conflicts
    with pytest.raises(StripeToolError) as exc_info:
        call(
            instance,
            "POST",
            "/v1/subscriptions",
            {
                "customer": cus,
                "items": [{"price": price}],
                "trial_end": 1798761600,
                "trial_from_plan": True,
            },
        )
    assert exc_info.value.stripe_body["error"]["message"] == (
        "You cannot set `trial_end` or `trial_period_days` when `trial_from_plan=true`."
    )
    assert "param" not in exc_info.value.stripe_body["error"]
    with pytest.raises(StripeToolError) as exc_info:
        call(
            instance,
            "POST",
            "/v1/subscriptions",
            {"customer": cus, "items": [{"price": price}], "trial_end": 1000},
        )
    assert exc_info.value.stripe_body["error"]["param"] == "trial_end"
    assert exc_info.value.stripe_body["error"]["message"] == (
        "The parameter `trial_end` expects a unix timestamp representing a date and time in "
        "the future. You specified the value `1000` which is in the past."
    )
    # unknown parameter
    with pytest.raises(StripeToolError) as exc_info:
        call(instance, "POST", "/v1/subscriptions", {"customer": cus, "nope": "x"})
    assert exc_info.value.stripe_body["error"]["code"] == "parameter_unknown"


def test_the_missing_id_404s(instance: seahaven.Instance) -> None:
    for method in ("GET", "POST", "DELETE"):
        with pytest.raises(StripeToolError) as exc_info:
            call(instance, method, "/v1/subscriptions/sub_nope")
        error = exc_info.value.stripe_body["error"]
        assert exc_info.value.status == 404, method
        assert error["code"] == "resource_missing"
        assert error["param"] == "id"
        assert error["message"] == "No such subscription: 'sub_nope'"


def test_trial_from_plan_uses_the_price_trial(instance: seahaven.Instance) -> None:
    cus, _unused, _price = setup_catalog(instance)
    prod = call(instance, "POST", "/v1/products", {"name": "trialled"})["id"]
    trial_price = call(
        instance,
        "POST",
        "/v1/prices",
        {
            "product": prod,
            "unit_amount": 1100,
            "currency": "cad",
            "recurring": {"interval": "month", "trial_period_days": 7},
        },
    )["id"]
    # a trial price alone does not trial (probed); the flag does
    body = call(
        instance, "POST", "/v1/subscriptions", {"customer": cus, "items": [{"price": trial_price}]}
    )
    assert body["status"] == "active"
    body = call(
        instance,
        "POST",
        "/v1/subscriptions",
        {"customer": cus, "items": [{"price": trial_price}], "trial_from_plan": True},
    )
    assert body["status"] == "trialing"
    assert body["trial_end"] - body["trial_start"] == 7 * 86_400


def test_coupon_and_tax_on_creation(instance: seahaven.Instance) -> None:
    cus, _, price = setup_catalog(instance)
    coupon = call(instance, "POST", "/v1/coupons", {"percent_off": 10, "duration": "once"})["id"]
    txr = call(
        instance,
        "POST",
        "/v1/tax_rates",
        {"display_name": "GST", "percentage": 5, "jurisdiction": "CA", "inclusive": False},
    )["id"]
    body = call(
        instance,
        "POST",
        "/v1/subscriptions",
        {
            "customer": cus,
            "items": [{"price": price}],
            "discounts": [{"coupon": coupon}],
            "default_tax_rates": [txr],
        },
    )
    # the recorded arithmetic: 2000 - 200 + 90 = 1890
    invoice = instance.inspect().one("SELECT * FROM invoices WHERE id = ?", body["latest_invoice"])
    assert invoice is not None
    assert invoice["subtotal"] == 2000
    assert invoice["total"] == 1890
    assert body["discounts"] == []  # duration=once never persists
    assert body["default_tax_rates"][0]["id"] == txr  # always-inflated


def test_update_flows_the_items_array(instance: seahaven.Instance) -> None:
    cus, _, price = setup_catalog(instance)
    prod = call(instance, "POST", "/v1/products", {"name": "second"})["id"]
    price2 = call(
        instance,
        "POST",
        "/v1/prices",
        {
            "product": prod,
            "unit_amount": 5000,
            "currency": "cad",
            "recurring": {"interval": "month"},
        },
    )["id"]
    sub = call(
        instance, "POST", "/v1/subscriptions", {"customer": cus, "items": [{"price": price}]}
    )
    # quantity through the sub's own items array
    body = call(
        instance,
        "POST",
        f"/v1/subscriptions/{sub['id']}",
        {"items": [{"id": sub["items"]["data"][0]["id"], "quantity": 5}]},
    )
    assert body["items"]["data"][0]["quantity"] == 5
    # a new item without an id spans [now, the subscription's period end)
    body = call(
        instance,
        "POST",
        f"/v1/subscriptions/{sub['id']}",
        {"items": [{"price": price2}]},
    )
    assert len(body["items"]["data"]) == 2
    new_item = body["items"]["data"][1]
    assert new_item["current_period_end"] == body["items"]["data"][0]["current_period_end"]
    # the update event carries previous_attributes as the serialized diff:
    # no raw-row leakage (x_seq, ISO strings, JSON text) and the real
    # change present
    events = instance.inspect().rows(
        "SELECT data FROM events WHERE type = 'customer.subscription.updated' ORDER BY x_seq"
    )
    assert events
    import json

    previous = json.loads(events[-1]["data"])["previous_attributes"]
    assert "x_seq" not in previous
    for key, value in previous.items():
        assert not (isinstance(value, str) and value.startswith("20") and ":" in value), key
    # the add's prior state: the single item after the quantity update
    assert previous["items"]["data"][0]["quantity"] == 5


def test_default_payment_method_clears_with_the_empty_string(instance: seahaven.Instance) -> None:
    """Recorded (Phase 12 CR probe): `""` clears the field — 200, null —
    never an engine error."""
    cus, pm, price = setup_catalog(instance)
    body = call(
        instance,
        "POST",
        "/v1/subscriptions",
        {"customer": cus, "items": [{"price": price}], "default_payment_method": pm},
    )
    assert body["default_payment_method"] == pm
    body = call(instance, "POST", f"/v1/subscriptions/{body['id']}", {"default_payment_method": ""})
    assert body["default_payment_method"] is None
    body = call(
        instance, "POST", f"/v1/subscriptions/{body['id']}", {"default_payment_method": None}
    )
    assert body["default_payment_method"] is None


def test_create_stores_payment_settings_and_thresholds(instance: seahaven.Instance) -> None:
    cus, _, price = setup_catalog(instance)
    body = call(
        instance,
        "POST",
        "/v1/subscriptions",
        {
            "customer": cus,
            "items": [{"price": price}],
            "payment_settings": {"save_default_payment_method": "on_subscription"},
            "billing_thresholds": {"amount_gte": 5000},
        },
    )
    assert body["payment_settings"]["save_default_payment_method"] == "on_subscription"
    assert body["billing_thresholds"] == {"amount_gte": 5000}


def test_cancel_at_period_end_merges_a_supplied_cancellation_details(
    instance: seahaven.Instance,
) -> None:
    cus, _, price = setup_catalog(instance)
    sub = call(
        instance, "POST", "/v1/subscriptions", {"customer": cus, "items": [{"price": price}]}
    )
    body = call(
        instance,
        "POST",
        f"/v1/subscriptions/{sub['id']}",
        {
            "cancel_at_period_end": True,
            "cancellation_details": {"comment": "too expensive"},
        },
    )
    details = body["cancellation_details"]
    assert details["comment"] == "too expensive"
    assert details["reason"] == "cancellation_requested"


def test_the_idempotent_create_replays(probe) -> None:
    """The keyed POST through ``call_stripe`` (the only face that still
    carries ``idempotency_key`` after Phase 5)."""
    from conftest import dispatch_tool

    world_p = probe(dispatch_tool())
    with world_p.instance(None, now=BLANK_NOW) as inst:
        cs = inst.call("call_stripe", method="POST", path="/v1/customers", params={})
        cus = cs["body"]["id"]
        pm = inst.call(
            "call_stripe",
            method="POST",
            path="/v1/payment_methods",
            params={"type": "card", "card": {"token": "tok_visa"}},
        )["body"]["id"]
        inst.call(
            "call_stripe",
            method="POST",
            path=f"/v1/payment_methods/{pm}/attach",
            params={"customer": cus},
        )
        inst.call(
            "call_stripe",
            method="POST",
            path=f"/v1/customers/{cus}",
            params={"invoice_settings": {"default_payment_method": pm}},
        )
        prod = inst.call("call_stripe", method="POST", path="/v1/products", params={"name": "Sub"})[
            "body"
        ]["id"]
        price = inst.call(
            "call_stripe",
            method="POST",
            path="/v1/prices",
            params={
                "product": prod,
                "unit_amount": 2000,
                "currency": "usd",
                "recurring": {"interval": "month"},
            },
        )["body"]["id"]
        params = {"customer": cus, "items": [{"price": price}]}
        first = inst.call(
            "call_stripe",
            method="POST",
            path="/v1/subscriptions",
            params=params,
            idempotency_key="sub-once",
        )
        second = inst.call(
            "call_stripe",
            method="POST",
            path="/v1/subscriptions",
            params=params,
            idempotency_key="sub-once",
        )
        assert second["body"]["id"] == first["body"]["id"]
        count = inst.inspect().one("SELECT count(*) AS n FROM subscriptions")
        assert count == {"n": 1}


# --- proration (Phase 14, cassette 01) ---------------------------------------------------


def proration_catalog(
    instance: seahaven.Instance, name: str = "proration suite"
) -> tuple[str, str, str, str]:
    """A customer with a paying default card and monthly 10.00 / 18.00 /
    20.00 prices — cassette 01's own setup shape."""
    cus, _, _ = setup_catalog(instance)
    prod = call(instance, "POST", "/v1/products", {"name": name})["id"]
    prices = []
    for unit in (1000, 1800, 2000):
        prices.append(
            call(
                instance,
                "POST",
                "/v1/prices",
                {
                    "product": prod,
                    "unit_amount": unit,
                    "currency": "cad",
                    "recurring": {"interval": "month"},
                },
            )["id"]
        )
    return (cus, *prices)


def _sub(instance: seahaven.Instance, cus: str, price: str) -> dict:
    return call(
        instance, "POST", "/v1/subscriptions", {"customer": cus, "items": [{"price": price}]}
    )


def _item_id(body: dict) -> str:
    return body["items"]["data"][0]["id"]


def _period(body: dict) -> tuple[int, int]:
    item = body["items"]["data"][0]
    return item["current_period_start"], item["current_period_end"]


def _proration_date(body: dict, divisor: int) -> int:
    start, end = _period(body)
    return end - (end - start) // divisor


def test_default_update_writes_pending_proration_items(instance: seahaven.Instance) -> None:
    """`create_prorations` (the default): the credit/debit pair lands as
    PENDING invoiceitems with cassette 01's wire body, and no invoice."""
    cus, p10, p18, _ = proration_catalog(instance)
    sub = _sub(instance, cus, p10)
    call(
        instance,
        "POST",
        f"/v1/subscriptions/{sub['id']}",
        {
            "items": [{"id": _item_id(sub), "price": p18}],
            "proration_date": _proration_date(sub, 400),
        },
    )
    pending = call(instance, "GET", "/v1/invoiceitems", {"customer": cus, "pending": True})["data"]
    # Newest-first: the credit leads (the debit was written first).
    assert [(item["amount"], item["quantity"]) for item in pending] == [(-3, 1), (4, 1)]
    credit, debit = pending[0], pending[1]
    assert credit["description"].startswith("Unused time on")
    assert debit["description"].startswith("Remaining time on")
    for item in pending:
        assert item["proration"] is True
        assert item["discountable"] is False
        assert item["net_amount"] == item["amount"]
        assert item["frozen_fields"] == ["pricing", "quantity", "discounts"]
        assert item["parent"]["type"] == "subscription_details"
        assert item["pricing"]["unit_amount_decimal"] is None
    # The credit's back-links point at the invoice that billed the period.
    assert credit["proration_details"]["credited_items"]["type"] == "invoice_line_items"
    assert (
        credit["proration_details"]["credited_items"]["invoice_line_item_details"]["invoice"]
        == sub["latest_invoice"]
    )
    # And no invoice was minted by the default behavior.
    assert (
        call(instance, "GET", "/v1/subscriptions/{id}".format(id=sub["id"]))["latest_invoice"]
        == sub["latest_invoice"]
    )


def test_always_invoice_pays_the_update_invoice(instance: seahaven.Instance) -> None:
    """RECORDED (cassette 01): the subscription_update invoice carries only
    the proration lines, finalizes and pays inside the call, and a net above
    the minimum chargeable charges at attempt 1."""
    cus, p10, _p18, p20 = proration_catalog(instance)
    sub = _sub(instance, cus, p10)  # 10.00 -> 20.00 at a third: -334 + 666
    updated = call(
        instance,
        "POST",
        f"/v1/subscriptions/{sub['id']}",
        {
            "items": [{"id": _item_id(sub), "price": p20}],
            "proration_behavior": "always_invoice",
            "proration_date": _proration_date(sub, 3),
        },
    )
    invoice = call(instance, "GET", f"/v1/invoices/{updated['latest_invoice']}")
    assert invoice["billing_reason"] == "subscription_update"
    assert invoice["status"] == "paid"
    assert invoice["attempted"] is True
    assert invoice["attempt_count"] == 1
    assert [line["amount"] for line in invoice["lines"]["data"]] == [-334, 666]
    assert invoice["subtotal"] == invoice["total"] == 332


def test_a_sub_minimum_net_rolls_to_the_balance(instance: seahaven.Instance) -> None:
    """RECORDED (cassette 01): a net of +1 is never charged — the invoice
    settles paid with `attempted: true, attempt_count: 0`, the cent lands
    on `customer.balance` as owed through an `invoice_too_small` row, and
    the next invoice draws it (`starting_balance`)."""
    cus, p10, p18, _ = proration_catalog(instance)
    sub = _sub(instance, cus, p10)
    updated = call(
        instance,
        "POST",
        f"/v1/subscriptions/{sub['id']}",
        {
            "items": [{"id": _item_id(sub), "price": p18}],
            "proration_behavior": "always_invoice",
            "proration_date": _proration_date(sub, 400),  # -3 + 4 = +1
        },
    )
    invoice = call(instance, "GET", f"/v1/invoices/{updated['latest_invoice']}")
    assert invoice["total"] == 1
    assert invoice["status"] == "paid"
    assert invoice["attempt_count"] == 0
    assert invoice["attempted"] is True
    assert invoice["amount_due"] == 0
    assert invoice["ending_balance"] == 1
    customer = call(instance, "GET", f"/v1/customers/{cus}")
    assert customer["balance"] == 1
    # The roll's balance row (probe_proration_settlement pins the wire):
    with instance.bulk() as ctx:
        rows = ctx.db.rows(
            "SELECT amount, type, ending_balance FROM customer_balance_transactions"
            " WHERE invoice = ?",
            invoice["id"],
        )
    assert [(row["amount"], row["type"], row["ending_balance"]) for row in rows] == [
        (1, "invoice_too_small", 1)
    ]
    # The next create draws the owed cent onto its invoice.
    next_sub = _sub(instance, cus, p10)
    next_invoice = call(instance, "GET", f"/v1/invoices/{next_sub['latest_invoice']}")
    assert next_invoice["starting_balance"] == 1
    assert next_invoice["amount_due"] == 1001


def test_a_negative_net_credits_the_customer_balance(instance: seahaven.Instance) -> None:
    """The documented -334 (RECORDED, cassette 01): a downgrade's invoice
    settles at amount_due 0 with `attempted: true, attempt_count: 0`, and
    the credit lands as an `applied_to_invoice` balance row — correcting
    billing_engine §3.5's `adjustment` guess."""
    cus, p10, _p18, p20 = proration_catalog(instance)
    sub = _sub(instance, cus, p20)
    updated = call(
        instance,
        "POST",
        f"/v1/subscriptions/{sub['id']}",
        {
            "items": [{"id": _item_id(sub), "price": p10}],
            "proration_behavior": "always_invoice",
            "proration_date": _proration_date(sub, 3),
        },
    )
    invoice = call(instance, "GET", f"/v1/invoices/{updated['latest_invoice']}")
    assert invoice["total"] == -334
    assert invoice["amount_due"] == 0
    assert invoice["status"] == "paid"
    assert invoice["attempt_count"] == 0
    assert invoice["ending_balance"] == -334
    with instance.bulk() as ctx:
        rows = ctx.db.rows(
            "SELECT amount, type FROM customer_balance_transactions WHERE invoice = ?",
            invoice["id"],
        )
    assert [(row["amount"], row["type"]) for row in rows] == [(-334, "applied_to_invoice")]


def test_proration_behavior_none_writes_nothing(instance: seahaven.Instance) -> None:
    cus, p10, p18, _ = proration_catalog(instance)
    sub = _sub(instance, cus, p10)
    before = call(instance, "GET", "/v1/invoiceitems", {"customer": cus, "pending": True})
    updated = call(
        instance,
        "POST",
        f"/v1/subscriptions/{sub['id']}",
        {
            "items": [{"id": _item_id(sub), "price": p18}],
            "proration_behavior": "none",
        },
    )
    after = call(instance, "GET", "/v1/invoiceitems", {"customer": cus, "pending": True})
    assert after["data"] == before["data"] == []
    assert updated["items"]["data"][0]["price"]["id"] == p18


def test_quantity_only_change_reprorates_the_whole_item(instance: seahaven.Instance) -> None:
    """RECORDED (cassette 01): 1 -> 3 at a 1/400 fraction writes the -3
    credit and the +7 whole-item debit (a delta would read +5)."""
    cus, p10, _p18, _ = proration_catalog(instance)
    sub = _sub(instance, cus, p10)
    call(
        instance,
        "POST",
        f"/v1/subscriptions/{sub['id']}",
        {
            "items": [{"id": _item_id(sub), "quantity": 3}],
            "proration_date": _proration_date(sub, 400),
        },
    )
    pending = call(instance, "GET", "/v1/invoiceitems", {"customer": cus, "pending": True})["data"]
    assert [(item["amount"], item["quantity"]) for item in pending] == [(-3, 1), (7, 3)]
    assert pending[1]["description"].startswith("Remaining time on 3 \u00d7")


def test_cancel_with_prorate_mints_the_pending_credit(instance: seahaven.Instance) -> None:
    """RECORDED (cassette 01): the prorate-only DELETE leaves one PENDING
    credit at the billed configuration (a `none`-switched item still
    credits the price that was paid), with the plain wire body — no
    parent, no proration_details."""
    cus, p10, p18, _ = proration_catalog(instance)
    sub = _sub(instance, cus, p10)
    call(
        instance,
        "POST",
        f"/v1/subscriptions/{sub['id']}",
        {"items": [{"id": _item_id(sub), "price": p18}], "proration_behavior": "none"},
    )
    canceled = call(instance, "DELETE", f"/v1/subscriptions/{sub['id']}", {"prorate": True})
    assert canceled["status"] == "canceled"
    pending = call(instance, "GET", "/v1/invoiceitems", {"customer": cus, "pending": True})["data"]
    assert len(pending) == 1
    credit = pending[0]
    assert credit["amount"] == -1000  # the BILLED price, not the switched-to one
    assert credit["description"].startswith("Unused time on")
    assert credit["parent"] is None
    assert "proration_details" not in credit  # omitted, not null (recorded)
    assert credit["frozen_fields"] == ["pricing", "quantity", "discounts"]


def test_cancel_with_invoice_now_mints_the_uncollectible_final_invoice(
    instance: seahaven.Instance,
) -> None:
    """RECORDED (cassette 01): the credit-only final invoice is a
    `subscription_cycle` draft live marks uncollectible seconds later;
    this world collapses the mark inside the call, and the credit never
    reaches the customer balance."""
    cus, p10, _p18, _ = proration_catalog(instance)
    sub = _sub(instance, cus, p10)
    canceled = call(
        instance,
        "DELETE",
        f"/v1/subscriptions/{sub['id']}",
        {"prorate": True, "invoice_now": True},
    )
    invoice = call(instance, "GET", f"/v1/invoices/{canceled['latest_invoice']}")
    assert invoice["billing_reason"] == "subscription_cycle"
    assert invoice["status"] == "uncollectible"
    assert invoice["number"] is None
    assert invoice["total"] == -1000
    assert [line["amount"] for line in invoice["lines"]["data"]] == [-1000]
    customer = call(instance, "GET", f"/v1/customers/{cus}")
    assert customer["balance"] == 0


def test_the_anchor_reset_truncates_rolls_and_re_anchors(instance: seahaven.Instance) -> None:
    """`billing_cycle_anchor: "now"` on update (the classic ruling; the
    recording account's flexible shape lives in the probe trail) — and it
    runs WITHOUT `items` too (the machine table's row is unconditional).
    Under the frozen clock a fresh subscription's reset instant equals its
    creation instant, so the detectable setup BACKDATES the item period
    first: the reset then lands mid-period, the truncation is observable,
    and the anchor column demonstrably moves."""
    cus, p10, _p18, _ = proration_catalog(instance)
    sub = _sub(instance, cus, p10)
    # Backdate the period to [Aug 2, Sep 2): the frozen `now`
    # (Sep 1 14:00) sits one day before its end — a genuine mid-period
    # subscription, the shape any fixture-generated history carries.
    with instance.bulk() as ctx:
        ctx.db.execute(
            "UPDATE subscription_items SET current_period_start = ?, current_period_end = ?"
            " WHERE subscription = ?",
            "2026-08-02T14:00:00.000Z",
            "2026-09-02T14:00:00.000Z",
            sub["id"],
        )
    body = call(
        instance,
        "POST",
        f"/v1/subscriptions/{sub['id']}",
        {"billing_cycle_anchor": "now"},
    )
    assert body["status"] == "active"
    start, end = _period(body)
    # The period RESTARTED at the reset instant: [now, now + interval)
    assert start == 1_788_271_200  # 2026-09-01T14:00:00Z, the frozen now
    assert end == 1_790_863_200  # one month onward (Oct 1 14:00Z)
    assert body["billing_cycle_anchor"] == start  # the column moved with it
    # An unchanged configuration truncates to nothing: no proration items.
    pending = call(instance, "GET", "/v1/invoiceitems", {"customer": cus, "pending": True})["data"]
    assert pending == []
    # `unchanged` is the recorded no-op sibling.
    again = call(
        instance,
        "POST",
        f"/v1/subscriptions/{sub['id']}",
        {"billing_cycle_anchor": "unchanged"},
    )
    assert _period(again) == (start, end)


def test_cancel_with_invoice_now_and_nothing_to_bill_mints_no_invoice(
    instance: seahaven.Instance,
) -> None:
    """`invoice_now=true` with `prorate=false` bills nothing, and the
    graceful Stripe-shaped outcome is NO invoice (the final invoice bills
    the outstanding amount; an empty uncollectible invoice would be a
    stretch of the credit-only recording). Declared in
    `allowed_differences.py`."""
    cus, p10, _p18, _ = proration_catalog(instance)
    sub = _sub(instance, cus, p10)
    invoices_before = instance.inspect().rows("SELECT id FROM invoices")
    canceled = call(
        instance,
        "DELETE",
        f"/v1/subscriptions/{sub['id']}",
        {"invoice_now": True},
    )
    assert canceled["status"] == "canceled"
    assert instance.inspect().rows("SELECT id FROM invoices") == invoices_before
