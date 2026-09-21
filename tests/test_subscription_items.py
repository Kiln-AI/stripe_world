"""The subscription_items slice, through the real four-tool chain: the
oldest-first list, the recorded 404 family, the required filter, and the
item transitions' period semantics — pinned by the Phase 12 probes and
cassette 12 at `2026-08-26.dahlia`."""

import pytest
import seahaven

from conftest import BLANK_NOW

pytestmark = pytest.mark.seahaven(fixture=None, now=BLANK_NOW)


def call(instance: seahaven.Instance, method: str, path: str, params: dict | None = None):
    if method == "GET":
        return instance.call("stripe_api_read", path=path, params=params)
    return instance.call("stripe_api_write", method=method, path=path, params=params)


def setup(instance: seahaven.Instance) -> tuple[str, str, str, str]:
    """(customer, subscription_id, price, price2)"""
    cus = call(instance, "POST", "/v1/customers", {"email": "si@example.test"})["body"]["id"]
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
    prod = call(instance, "POST", "/v1/products", {"name": "items"})["body"]["id"]
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
    return cus, sub["id"], price, price2


def test_the_created_item_spans_to_the_period_end(instance: seahaven.Instance) -> None:
    _, sub, _price, price2 = setup(instance)
    body = call(instance, "POST", "/v1/subscription_items", {"subscription": sub, "price": price2})[
        "body"
    ]
    assert body["object"] == "subscription_item"
    assert body["subscription"] == sub
    assert body["price"]["id"] == price2  # always-inflated, never a bare id
    assert body["quantity"] == 1  # the recorded licensed default
    sub_body = call(instance, "GET", f"/v1/subscriptions/{sub}")["body"]
    assert body["current_period_end"] == sub_body["items"]["data"][0]["current_period_end"]
    assert body["current_period_start"] == sub_body["created"]


def test_the_list_is_oldest_first_and_names_its_filter(instance: seahaven.Instance) -> None:
    _, sub, price, price2 = setup(instance)
    second = call(
        instance, "POST", "/v1/subscription_items", {"subscription": sub, "price": price2}
    )["body"]["id"]
    listed = call(instance, "GET", "/v1/subscription_items", {"subscription": sub})["body"]
    items = call(instance, "GET", f"/v1/subscriptions/{sub}")["body"]["items"]["data"]
    # creation order, not newest-first: the create-time item precedes the added one
    assert (
        [item["id"] for item in listed["data"]]
        == [item["id"] for item in items]
        == [
            items[0]["id"],
            second,
        ]
    )
    assert listed["url"] == f"/v1/subscription_items?subscription={sub}"
    assert listed["data"][0]["price"]["id"] == price


def test_the_list_filter_is_required(instance: seahaven.Instance) -> None:
    result = call(instance, "GET", "/v1/subscription_items")
    error = result["body"]["error"]
    assert result["status"] == 400
    assert error["code"] == "parameter_missing"
    assert error["message"] == "Missing required param: subscription."


def test_the_retrieve_family_is_its_own(instance: seahaven.Instance) -> None:
    result = call(instance, "GET", "/v1/subscription_items/si_nope")
    assert result["status"] == 404  # recorded: 404, no param, no code
    error = result["body"]["error"]
    assert error["message"] == "Invalid subscription_item id: si_nope"
    assert "param" not in error
    assert "code" not in error


def test_update_changes_price_and_quantity(instance: seahaven.Instance) -> None:
    _, sub, _price, price2 = setup(instance)
    item = call(instance, "GET", "/v1/subscription_items", {"subscription": sub})["body"]["data"][0]
    body = call(instance, "POST", f"/v1/subscription_items/{item['id']}", {"quantity": 3})["body"]
    assert body["quantity"] == 3
    body = call(instance, "POST", f"/v1/subscription_items/{item['id']}", {"price": price2})["body"]
    assert body["price"]["id"] == price2
    # the periods never move on an update
    assert body["current_period_start"] == item["current_period_start"]
    assert body["current_period_end"] == item["current_period_end"]


def test_delete_answers_the_stub_and_frees_the_price(instance: seahaven.Instance) -> None:
    _, sub, _price, price2 = setup(instance)
    item = call(instance, "POST", "/v1/subscription_items", {"subscription": sub, "price": price2})[
        "body"
    ]
    result = call(
        instance,
        "DELETE",
        f"/v1/subscription_items/{item['id']}",
        {"proration_behavior": "none"},
    )
    assert result["body"] == {"id": item["id"], "object": "subscription_item", "deleted": True}
    listed = call(instance, "GET", "/v1/subscription_items", {"subscription": sub})["body"]
    assert item["id"] not in [i["id"] for i in listed["data"]]
    # re-adding the freed price is legal again (the duplicate-price rule is
    # per active item set)
    body = call(instance, "POST", "/v1/subscription_items", {"subscription": sub, "price": price2})[
        "body"
    ]
    assert body["id"] != item["id"]


def test_item_writes_refuse_a_canceled_subscription(instance: seahaven.Instance) -> None:
    _, sub, _price, price2 = setup(instance)
    call(instance, "DELETE", f"/v1/subscriptions/{sub}")
    result = call(
        instance, "POST", "/v1/subscription_items", {"subscription": sub, "price": price2}
    )
    # Recorded: the canceled subscription is missing, under `param:
    # subscription`, at 404
    assert result["status"] == 404
    error = result["body"]["error"]
    assert error["code"] == "resource_missing"
    assert error["param"] == "subscription"
    assert error["message"] == f"No such subscription: '{sub}'"


def test_item_writes_emit_the_subscription_event(instance: seahaven.Instance) -> None:
    _, sub, _price, price2 = setup(instance)
    before = {row["type"] for row in instance.inspect().rows("SELECT type FROM events")}
    call(instance, "POST", "/v1/subscription_items", {"subscription": sub, "price": price2})
    types = {row["type"] for row in instance.inspect().rows("SELECT type FROM events")}
    assert types - before == {"customer.subscription.updated"}


def test_the_recorded_addition_guards(instance: seahaven.Instance) -> None:
    """The two recorded item-addition refusals (Phase 12 CR probes): a
    duplicate price and a currency mismatch."""
    _, sub, price, _price2 = setup(instance)
    result = call(instance, "POST", "/v1/subscription_items", {"subscription": sub, "price": price})
    error = result["body"]["error"]
    assert result["status"] == 400
    existing = call(instance, "GET", "/v1/subscription_items", {"subscription": sub})["body"][
        "data"
    ][0]["id"]
    assert error["param"] == "plan"
    assert error["message"] == (
        f"A new item with Price {price} can't be added to this Subscription because an "
        f"existing Subscription Item {existing} is already using that Price. If you want to "
        "update the existing item (e.g., to adjust the quantity), pass the existing "
        "Subscription Item's `id` in your update request: "
        "https://stripe.com/docs/api/subscriptions/update#update_subscription-items-id"
    )
    # the same refusal through the sub-update items[] array
    result2 = call(instance, "POST", f"/v1/subscriptions/{sub}", {"items": [{"price": price}]})
    assert result2["body"]["error"]["param"] == "plan"

    # a foreign-currency price
    prod = call(instance, "POST", "/v1/products", {"name": "usd thing"})["body"]["id"]
    usd_price = call(
        instance,
        "POST",
        "/v1/prices",
        {
            "product": prod,
            "unit_amount": 700,
            "currency": "usd",
            "recurring": {"interval": "month"},
        },
    )["body"]["id"]
    result = call(
        instance, "POST", "/v1/subscription_items", {"subscription": sub, "price": usd_price}
    )
    error = result["body"]["error"]
    assert error["param"] == "price"
    assert error["message"] == (
        "The price specified only supports `usd`. This doesn't match the expected currency: `cad`."
    )
    # and at create, mixing currencies in one items array (a bare customer:
    # the currency guard fires before the payment-method gate)
    bare = call(instance, "POST", "/v1/customers", {"email": "guards@example.test"})["body"]["id"]
    result = call(
        instance,
        "POST",
        "/v1/subscriptions",
        {"customer": bare, "items": [{"price": price}, {"price": usd_price}]},
    )
    error = result["body"]["error"]
    assert error["param"] == "items[1][price]"
    assert error["message"] == (
        "This `price` has `currency=usd`, but other items use `currency=cad`. All items must "
        "have pricing in the same currency. When using multi-currency prices, you can specify "
        "`currency` at the top level, to be used by all items."
    )
