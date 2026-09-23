"""The subscription_items slice, through the real four-tool chain: the
oldest-first list, the recorded 404 family, the required filter, and the
item transitions' period semantics — pinned by the Phase 12 probes and
cassette 12 at `2026-08-26.dahlia`."""

import pytest
import seahaven

from conftest import BLANK_NOW
from seahaven_stripe_world.errors import StripeToolError

pytestmark = pytest.mark.seahaven(fixture=None, now=BLANK_NOW)


def call(instance: seahaven.Instance, method: str, path: str, params: dict | None = None):
    if method == "GET":
        return instance.call("stripe_api_read", path=path, params=params)
    return instance.call("stripe_api_write", method=method, path=path, params=params)


def setup(instance: seahaven.Instance) -> tuple[str, str, str, str]:
    """(customer, subscription_id, price, price2)"""
    cus = call(instance, "POST", "/v1/customers", {"email": "si@example.test"})["id"]
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
    prod = call(instance, "POST", "/v1/products", {"name": "items"})["id"]
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
    return cus, sub["id"], price, price2


def test_the_created_item_spans_to_the_period_end(instance: seahaven.Instance) -> None:
    _, sub, _price, price2 = setup(instance)
    body = call(instance, "POST", "/v1/subscription_items", {"subscription": sub, "price": price2})
    assert body["object"] == "subscription_item"
    assert body["subscription"] == sub
    assert body["price"]["id"] == price2  # always-inflated, never a bare id
    assert body["quantity"] == 1  # the recorded licensed default
    sub_body = call(instance, "GET", f"/v1/subscriptions/{sub}")
    assert body["current_period_end"] == sub_body["items"]["data"][0]["current_period_end"]
    assert body["current_period_start"] == sub_body["created"]


def test_the_list_is_oldest_first_and_names_its_filter(instance: seahaven.Instance) -> None:
    _, sub, price, price2 = setup(instance)
    second = call(
        instance, "POST", "/v1/subscription_items", {"subscription": sub, "price": price2}
    )["id"]
    listed = call(instance, "GET", "/v1/subscription_items", {"subscription": sub})
    items = call(instance, "GET", f"/v1/subscriptions/{sub}")["items"]["data"]
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
    with pytest.raises(StripeToolError) as exc_info:
        call(instance, "GET", "/v1/subscription_items")
    assert exc_info.value.status == 400
    error = exc_info.value.stripe_body["error"]
    assert error["code"] == "parameter_missing"
    assert error["message"] == "Missing required param: subscription."


def test_the_retrieve_family_is_its_own(instance: seahaven.Instance) -> None:
    with pytest.raises(StripeToolError) as exc_info:
        call(instance, "GET", "/v1/subscription_items/si_nope")
    assert exc_info.value.status == 404  # recorded: 404, no param, no code
    error = exc_info.value.stripe_body["error"]
    assert error["message"] == "Invalid subscription_item id: si_nope"
    assert "param" not in error
    assert "code" not in error


def test_update_changes_price_and_quantity(instance: seahaven.Instance) -> None:
    _, sub, _price, price2 = setup(instance)
    item = call(instance, "GET", "/v1/subscription_items", {"subscription": sub})["data"][0]
    body = call(instance, "POST", f"/v1/subscription_items/{item['id']}", {"quantity": 3})
    assert body["quantity"] == 3
    body = call(instance, "POST", f"/v1/subscription_items/{item['id']}", {"price": price2})
    assert body["price"]["id"] == price2
    # the periods never move on an update
    assert body["current_period_start"] == item["current_period_start"]
    assert body["current_period_end"] == item["current_period_end"]


def test_delete_answers_the_stub_and_frees_the_price(instance: seahaven.Instance) -> None:
    _, sub, _price, price2 = setup(instance)
    item = call(instance, "POST", "/v1/subscription_items", {"subscription": sub, "price": price2})
    result = call(
        instance,
        "DELETE",
        f"/v1/subscription_items/{item['id']}",
        {"proration_behavior": "none"},
    )
    assert result == {"id": item["id"], "object": "subscription_item", "deleted": True}
    listed = call(instance, "GET", "/v1/subscription_items", {"subscription": sub})
    assert item["id"] not in [i["id"] for i in listed["data"]]
    # re-adding the freed price is legal again (the duplicate-price rule is
    # per active item set)
    body = call(instance, "POST", "/v1/subscription_items", {"subscription": sub, "price": price2})
    assert body["id"] != item["id"]


def test_item_writes_refuse_a_canceled_subscription(instance: seahaven.Instance) -> None:
    _, sub, _price, price2 = setup(instance)
    call(instance, "DELETE", f"/v1/subscriptions/{sub}")
    # Recorded: the canceled subscription is missing, under `param:
    # subscription`, at 404
    with pytest.raises(StripeToolError) as exc_info:
        call(instance, "POST", "/v1/subscription_items", {"subscription": sub, "price": price2})
    assert exc_info.value.status == 404
    error = exc_info.value.stripe_body["error"]
    assert error["code"] == "resource_missing"
    assert error["param"] == "subscription"
    assert error["message"] == f"No such subscription: '{sub}'"


def test_item_writes_emit_the_subscription_event(instance: seahaven.Instance) -> None:
    _, sub, _price, price2 = setup(instance)
    before = {row["type"] for row in instance.inspect().rows("SELECT type FROM events")}
    call(instance, "POST", "/v1/subscription_items", {"subscription": sub, "price": price2})
    types = {row["type"] for row in instance.inspect().rows("SELECT type FROM events")}
    # Phase 14: the default `create_prorations` adds the debit-only
    # proration item beside the subscription event.
    assert types - before == {"customer.subscription.updated", "invoiceitem.created"}


def test_the_recorded_addition_guards(instance: seahaven.Instance) -> None:
    """The two recorded item-addition refusals (Phase 12 CR probes): a
    duplicate price and a currency mismatch."""
    _, sub, price, _price2 = setup(instance)
    with pytest.raises(StripeToolError) as exc_info:
        call(instance, "POST", "/v1/subscription_items", {"subscription": sub, "price": price})
    assert exc_info.value.status == 400
    error = exc_info.value.stripe_body["error"]
    existing = call(instance, "GET", "/v1/subscription_items", {"subscription": sub})["data"][0][
        "id"
    ]
    assert error["param"] == "plan"
    assert error["message"] == (
        f"A new item with Price {price} can't be added to this Subscription because an "
        f"existing Subscription Item {existing} is already using that Price. If you want to "
        "update the existing item (e.g., to adjust the quantity), pass the existing "
        "Subscription Item's `id` in your update request: "
        "https://stripe.com/docs/api/subscriptions/update#update_subscription-items-id"
    )
    # the same refusal through the sub-update items[] array
    with pytest.raises(StripeToolError) as exc_info:
        call(instance, "POST", f"/v1/subscriptions/{sub}", {"items": [{"price": price}]})
    assert exc_info.value.stripe_body["error"]["param"] == "plan"

    # a foreign-currency price
    prod = call(instance, "POST", "/v1/products", {"name": "usd thing"})["id"]
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
    )["id"]
    with pytest.raises(StripeToolError) as exc_info:
        call(instance, "POST", "/v1/subscription_items", {"subscription": sub, "price": usd_price})
    error = exc_info.value.stripe_body["error"]
    assert error["param"] == "price"
    assert error["message"] == (
        "The price specified only supports `usd`. This doesn't match the expected currency: `cad`."
    )
    # and at create, mixing currencies in one items array (a bare customer:
    # the currency guard fires before the payment-method gate)
    bare = call(instance, "POST", "/v1/customers", {"email": "guards@example.test"})["id"]
    with pytest.raises(StripeToolError) as exc_info:
        call(
            instance,
            "POST",
            "/v1/subscriptions",
            {"customer": bare, "items": [{"price": price}, {"price": usd_price}]},
        )
    error = exc_info.value.stripe_body["error"]
    assert error["param"] == "items[1][price]"
    assert error["message"] == (
        "This `price` has `currency=usd`, but other items use `currency=cad`. All items must "
        "have pricing in the same currency. When using multi-currency prices, you can specify "
        "`currency` at the top level, to be used by all items."
    )
