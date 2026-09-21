"""The subscriptions slice's routed surface, through the real four-tool
chain. Bodies, defaults, filters, refusals (verbatim) and events — pinned
by the Phase 12 probes and cassette 12 at `2026-08-26.dahlia`."""

import pytest
import seahaven

from conftest import BLANK_NOW

pytestmark = pytest.mark.seahaven(fixture=None, now=BLANK_NOW)


def call(instance: seahaven.Instance, method: str, path: str, params: dict | None = None):
    if method == "GET":
        return instance.call("stripe_api_read", path=path, params=params)
    return instance.call("stripe_api_write", method=method, path=path, params=params)


def setup_catalog(instance: seahaven.Instance) -> tuple[str, str, str]:
    cus = call(instance, "POST", "/v1/customers", {"email": "s@example.test"})["body"]["id"]
    pm = call(
        instance, "POST", "/v1/payment_methods", {"type": "card", "card": {"token": "tok_visa"}}
    )["body"]["id"]
    call(instance, "POST", f"/v1/payment_methods/{pm}/attach", {"customer": cus})
    call(
        instance,
        "POST",
        f"/v1/customers/{cus}",
        {"invoice_settings": {"default_payment_method": pm}},
    )
    prod = call(instance, "POST", "/v1/products", {"name": "sub suite"})["body"]["id"]
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
    )["body"]["id"]
    return cus, pm, price


def error_of(result: dict) -> dict:
    return result["body"]["error"]


# --- the created body's constants -------------------------------------------------------


def test_the_created_body_constants(instance: seahaven.Instance) -> None:
    cus, _, price = setup_catalog(instance)
    body = call(
        instance, "POST", "/v1/subscriptions", {"customer": cus, "items": [{"price": price}]}
    )["body"]
    assert body["object"] == "subscription"
    assert body["livemode"] is False
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
    )["body"]
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
    )["body"]
    assert body["metadata"] == {"a": "1"}
    body = call(instance, "POST", f"/v1/subscriptions/{body['id']}", {"metadata": {"b": "2"}})[
        "body"
    ]
    assert body["metadata"] == {"a": "1", "b": "2"}
    body = call(instance, "POST", f"/v1/subscriptions/{body['id']}", {"metadata": {"a": None}})[
        "body"
    ]
    assert body["metadata"] == {"b": "2"}


# --- lists -------------------------------------------------------------------------------


def test_lists_and_filters(instance: seahaven.Instance) -> None:
    cus, _, price = setup_catalog(instance)
    body = call(
        instance, "POST", "/v1/subscriptions", {"customer": cus, "items": [{"price": price}]}
    )["body"]
    listed = call(instance, "GET", "/v1/subscriptions", {"customer": cus})["body"]
    assert [item["id"] for item in listed["data"]] == [body["id"]]
    assert listed["url"] == "/v1/subscriptions"
    listed = call(instance, "GET", "/v1/subscriptions", {"status": "active"})["body"]
    assert body["id"] in [item["id"] for item in listed["data"]]
    listed = call(instance, "GET", "/v1/subscriptions", {"status": "all"})["body"]
    assert body["id"] in [item["id"] for item in listed["data"]]
    listed = call(instance, "GET", "/v1/subscriptions", {"price": price})["body"]
    assert body["id"] in [item["id"] for item in listed["data"]]
    result = call(instance, "GET", "/v1/subscriptions", {"status": "bogus"})
    error = error_of(result)
    assert result["status"] == 400
    assert "code" not in error
    assert error["message"] == (
        "Invalid status: must be one of active, past_due, unpaid, canceled, incomplete, "
        "incomplete_expired, trialing, paused, all, or ended"
    )


def test_lists_are_newest_first(instance: seahaven.Instance) -> None:
    cus, _, price = setup_catalog(instance)
    first = call(
        instance, "POST", "/v1/subscriptions", {"customer": cus, "items": [{"price": price}]}
    )["body"]
    second = call(
        instance, "POST", "/v1/subscriptions", {"customer": cus, "items": [{"price": price}]}
    )["body"]
    listed = call(instance, "GET", "/v1/subscriptions", {"customer": cus})["body"]
    assert [item["id"] for item in listed["data"]] == [second["id"], first["id"]]


# --- the recorded refusals -----------------------------------------------------------------


def test_the_recorded_refusals(instance: seahaven.Instance) -> None:
    cus, _, price = setup_catalog(instance)
    # pending_if_incomplete is update-only
    result = call(
        instance,
        "POST",
        "/v1/subscriptions",
        {
            "customer": cus,
            "items": [{"price": price}],
            "payment_behavior": "pending_if_incomplete",
        },
    )
    error = error_of(result)
    assert result["status"] == 400
    assert error["message"] == (
        "Setting `payment_behavior` to `pending_if_incomplete` has no effect when creating "
        "a subscription."
    )
    assert error["param"] == "payment_behavior"
    # a bad literal
    result = call(
        instance,
        "POST",
        "/v1/subscriptions",
        {"customer": cus, "items": [{"price": price}], "payment_behavior": "bogus"},
    )
    assert "code" not in error_of(result)
    assert error_of(result)["param"] == "payment_behavior"
    # duplicate price
    result = call(
        instance,
        "POST",
        "/v1/subscriptions",
        {"customer": cus, "items": [{"price": price}, {"price": price}]},
    )
    error = error_of(result)
    assert error["param"] == "plan"
    assert error["message"] == (
        "Cannot create a Subscription with multiple Subscription Items with "
        f"the same Price: {price}"
    )
    # missing items
    result = call(instance, "POST", "/v1/subscriptions", {"customer": cus})
    error = error_of(result)
    assert error["code"] == "parameter_missing"
    assert error["param"] == "items"
    # unknown price
    result = call(
        instance, "POST", "/v1/subscriptions", {"customer": cus, "items": [{"price": "price_nope"}]}
    )
    error = error_of(result)
    assert error["code"] == "resource_missing"
    assert error["param"] == "items[0][price]"
    assert error["message"] == "No such price: 'price_nope'"
    # unknown customer: the 404 a path id earns
    result = call(
        instance, "POST", "/v1/subscriptions", {"customer": "cus_nope", "items": [{"price": price}]}
    )
    assert result["status"] == 404
    assert error_of(result)["param"] == "customer"
    # days_until_due guards
    result = call(
        instance,
        "POST",
        "/v1/subscriptions",
        {"customer": cus, "items": [{"price": price}], "days_until_due": 5},
    )
    assert error_of(result)["message"] == (
        "You can only specify 'days_until_due' if invoice collection method is 'send_invoice'."
    )
    result = call(
        instance,
        "POST",
        "/v1/subscriptions",
        {"customer": cus, "items": [{"price": price}], "collection_method": "send_invoice"},
    )
    assert error_of(result)["message"] == (
        "If invoice collection method is 'send_invoice', you must specify 'days_until_due'."
    )
    # trial conflicts
    result = call(
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
    assert error_of(result)["message"] == (
        "You cannot set `trial_end` or `trial_period_days` when `trial_from_plan=true`."
    )
    assert "param" not in error_of(result)
    result = call(
        instance,
        "POST",
        "/v1/subscriptions",
        {"customer": cus, "items": [{"price": price}], "trial_end": 1000},
    )
    assert error_of(result)["param"] == "trial_end"
    assert error_of(result)["message"] == (
        "The parameter `trial_end` expects a unix timestamp representing a date and time in "
        "the future. You specified the value `1000` which is in the past."
    )
    # unknown parameter
    result = call(instance, "POST", "/v1/subscriptions", {"customer": cus, "nope": "x"})
    assert error_of(result)["code"] == "parameter_unknown"


def test_the_missing_id_404s(instance: seahaven.Instance) -> None:
    for method in ("GET", "POST", "DELETE"):
        result = call(instance, method, "/v1/subscriptions/sub_nope")
        assert result["status"] == 404, method
        error = error_of(result)
        assert error["code"] == "resource_missing"
        assert error["param"] == "id"
        assert error["message"] == "No such subscription: 'sub_nope'"


def test_trial_from_plan_uses_the_price_trial(instance: seahaven.Instance) -> None:
    cus, _unused, _price = setup_catalog(instance)
    prod = call(instance, "POST", "/v1/products", {"name": "trialled"})["body"]["id"]
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
    )["body"]["id"]
    # a trial price alone does not trial (probed); the flag does
    body = call(
        instance, "POST", "/v1/subscriptions", {"customer": cus, "items": [{"price": trial_price}]}
    )["body"]
    assert body["status"] == "active"
    body = call(
        instance,
        "POST",
        "/v1/subscriptions",
        {"customer": cus, "items": [{"price": trial_price}], "trial_from_plan": True},
    )["body"]
    assert body["status"] == "trialing"
    assert body["trial_end"] - body["trial_start"] == 7 * 86_400


def test_coupon_and_tax_on_creation(instance: seahaven.Instance) -> None:
    cus, _, price = setup_catalog(instance)
    coupon = call(instance, "POST", "/v1/coupons", {"percent_off": 10, "duration": "once"})["body"][
        "id"
    ]
    txr = call(
        instance,
        "POST",
        "/v1/tax_rates",
        {"display_name": "GST", "percentage": 5, "jurisdiction": "CA", "inclusive": False},
    )["body"]["id"]
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
    )["body"]
    # the recorded arithmetic: 2000 - 200 + 90 = 1890
    invoice = instance.inspect().one("SELECT * FROM invoices WHERE id = ?", body["latest_invoice"])
    assert invoice is not None
    assert invoice["subtotal"] == 2000
    assert invoice["total"] == 1890
    assert body["discounts"] == []  # duration=once never persists
    assert body["default_tax_rates"][0]["id"] == txr  # always-inflated


def test_update_flows_the_items_array(instance: seahaven.Instance) -> None:
    cus, _, price = setup_catalog(instance)
    prod = call(instance, "POST", "/v1/products", {"name": "second"})["body"]["id"]
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
    )["body"]["id"]
    sub = call(
        instance, "POST", "/v1/subscriptions", {"customer": cus, "items": [{"price": price}]}
    )["body"]
    # quantity through the sub's own items array
    body = call(
        instance,
        "POST",
        f"/v1/subscriptions/{sub['id']}",
        {"items": [{"id": sub["items"]["data"][0]["id"], "quantity": 5}]},
    )["body"]
    assert body["items"]["data"][0]["quantity"] == 5
    # a new item without an id spans [now, the subscription's period end)
    body = call(
        instance,
        "POST",
        f"/v1/subscriptions/{sub['id']}",
        {"items": [{"price": price2}]},
    )["body"]
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
    )["body"]
    assert body["default_payment_method"] == pm
    body = call(
        instance, "POST", f"/v1/subscriptions/{body['id']}", {"default_payment_method": ""}
    )["body"]
    assert body["default_payment_method"] is None
    body = call(
        instance, "POST", f"/v1/subscriptions/{body['id']}", {"default_payment_method": None}
    )["body"]
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
    )["body"]
    assert body["payment_settings"]["save_default_payment_method"] == "on_subscription"
    assert body["billing_thresholds"] == {"amount_gte": 5000}


def test_cancel_at_period_end_merges_a_supplied_cancellation_details(
    instance: seahaven.Instance,
) -> None:
    cus, _, price = setup_catalog(instance)
    sub = call(
        instance, "POST", "/v1/subscriptions", {"customer": cus, "items": [{"price": price}]}
    )["body"]
    body = call(
        instance,
        "POST",
        f"/v1/subscriptions/{sub['id']}",
        {
            "cancel_at_period_end": True,
            "cancellation_details": {"comment": "too expensive"},
        },
    )["body"]
    details = body["cancellation_details"]
    assert details["comment"] == "too expensive"
    assert details["reason"] == "cancellation_requested"


def test_the_idempotent_create_replays(instance: seahaven.Instance) -> None:
    """The keyed POST: the middleware's four outcomes, on this slice."""
    cus, _, price = setup_catalog(instance)
    params = {"customer": cus, "items": [{"price": price}]}
    first = instance.call(
        "stripe_api_write",
        method="POST",
        path="/v1/subscriptions",
        params=params,
        idempotency_key="sub-once",
    )
    second = instance.call(
        "stripe_api_write",
        method="POST",
        path="/v1/subscriptions",
        params=params,
        idempotency_key="sub-once",
    )
    assert second["body"]["id"] == first["body"]["id"]
    count = instance.inspect().one("SELECT count(*) AS n FROM subscriptions")
    assert count == {"n": 1}
