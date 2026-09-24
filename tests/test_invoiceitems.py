"""The invoiceitem resource through the real four-tool chain: the one-off
price mint behind `pricing`, the pending filter's null-test, the recorded
Invoice Item 404 family, and the dead-invoice refusal pair — pinned by
cassette 13 at `2026-08-26.dahlia`."""

import pytest
import seahaven

from conftest import BLANK_NOW, api_read, api_write
from seahaven_stripe_world.errors import StripeToolError

pytestmark = pytest.mark.seahaven(fixture=None, now=BLANK_NOW, clock_mode="fixed")


def call(instance: seahaven.Instance, method: str, path: str, params: dict | None = None):
    if method == "GET":
        return api_read(instance, path, params)
    return api_write(instance, method, path, params)


def item(instance: seahaven.Instance, customer: str, amount: int, description: str) -> str:
    return call(
        instance,
        "POST",
        "/v1/invoiceitems",
        {
            "customer": customer,
            "amount": amount,
            "currency": "usd",
            "description": description,
        },
    )["id"]


@pytest.fixture()
def customer(instance: seahaven.Instance) -> str:
    return call(instance, "POST", "/v1/customers", {"email": "ii@example.test"})["id"]


def test_create_mints_the_one_off_price_behind_pricing(instance, customer) -> None:
    body = call(
        instance,
        "POST",
        "/v1/invoiceitems",
        {"customer": customer, "amount": 2000, "currency": "usd", "description": "Setup fee"},
    )
    assert body["object"] == "invoiceitem"
    assert body["amount"] == 2000
    assert body["discountable"] is True
    assert body["discounts"] == []
    assert body["frozen_fields"] == []
    assert body["invoice"] is None
    assert body["parent"] is None
    assert body["proration"] is False
    assert body["quantity"] == 1
    assert body["quantity_decimal"] == "1"
    assert body["tax_rates"] == []
    assert body["livemode"] is True
    assert "invoicing_rules" not in body  # spec-undeclared live echo
    assert "net_amount" not in body  # null-and-omittable
    # the recorded mechanism: amount+currency mints a one-off price/product
    pricing = body["pricing"]
    assert pricing["type"] == "price_details"
    assert pricing["unit_amount_decimal"] == "2000"
    price_id = pricing["price_details"]["price"]
    product_id = pricing["price_details"]["product"]
    price = call(instance, "GET", f"/v1/prices/{price_id}")
    assert price["product"] == product_id
    assert price["unit_amount"] == 2000
    assert price["type"] == "one_time"
    call(instance, "GET", f"/v1/products/{product_id}")
    # the minted rows are catalog rows: the catalog's own events fire for
    # them, product first (the recorded order), then the item's own
    assert [
        row["type"] for row in instance.inspect().rows("SELECT type FROM events ORDER BY x_seq")
    ][-3:] == ["product.created", "price.created", "invoiceitem.created"]
    # the period pair defaults to the creation instant (zero-width)
    assert body["period"]["start"] == body["date"] == body["period"]["end"]


def test_quantity_multiplies_on_create_and_the_sweep(instance, customer) -> None:
    """The row's `amount` is the unit amount; the swept line bills unit x
    quantity (the manager-confirmed arithmetic for the quantity parameter)."""
    one = call(
        instance,
        "POST",
        "/v1/invoiceitems",
        {"customer": customer, "amount": 500, "currency": "usd", "quantity": 3},
    )
    assert one["quantity"] == 3
    assert one["amount"] == 500
    invoice = call(
        instance,
        "POST",
        "/v1/invoices",
        {"customer": customer, "pending_invoice_items_behavior": "include"},
    )
    line = invoice["lines"]["data"][0]
    assert line["amount"] == 1500
    assert line["quantity"] == 3
    assert line["pricing"]["unit_amount_decimal"] == "500"
    assert invoice["total"] == 1500


def test_create_against_a_draft_invoice_joins_its_lines(instance, customer) -> None:
    invoice = call(instance, "POST", "/v1/invoices", {"customer": customer})
    body = call(
        instance,
        "POST",
        "/v1/invoiceitems",
        {
            "customer": customer,
            "amount": 700,
            "currency": "usd",
            "description": "attached at birth",
            "invoice": invoice["id"],
        },
    )
    assert body["invoice"] == invoice["id"]
    # the draft's lines include the new item in the same call
    fresh = call(instance, "GET", f"/v1/invoices/{invoice['id']}")
    assert [(line["amount"], line["description"]) for line in fresh["lines"]["data"]] == [
        (700, "attached at birth")
    ]
    assert fresh["total"] == 700
    types = [
        row["type"]
        for row in instance.inspect().rows(
            "SELECT type FROM events WHERE type LIKE 'invoice.%' ORDER BY x_seq"
        )
    ]
    assert types == ["invoice.created", "invoice.updated"]


def test_the_list_is_newest_first_and_the_pending_filter_is_a_null_test(instance, customer) -> None:
    first = item(instance, customer, 2000, "first")
    second = item(instance, customer, 1500, "second")
    listed = call(instance, "GET", "/v1/invoiceitems", {"customer": customer})
    assert [row["id"] for row in listed["data"]] == [second, first]
    pending = call(instance, "GET", "/v1/invoiceitems", {"customer": customer, "pending": True})
    assert [row["id"] for row in pending["data"]] == [second, first]
    # a sweep takes both out of the pending view, not out of the list
    invoice = call(
        instance,
        "POST",
        "/v1/invoices",
        {"customer": customer, "pending_invoice_items_behavior": "include"},
    )
    pending = call(instance, "GET", "/v1/invoiceitems", {"customer": customer, "pending": True})
    assert pending["data"] == []
    swept = call(instance, "GET", "/v1/invoiceitems", {"customer": customer, "pending": False})
    assert {row["id"] for row in swept["data"]} == {first, second}
    by_invoice = call(instance, "GET", "/v1/invoiceitems", {"invoice": invoice["id"]})
    assert {row["id"] for row in by_invoice["data"]} == {first, second}


def test_the_recorded_404_family(instance, customer) -> None:
    with pytest.raises(StripeToolError) as exc_info:
        call(instance, "GET", "/v1/invoiceitems/ii_nope")
    assert exc_info.value.status == 404
    assert exc_info.value.stripe_body["error"]["message"] == (
        "No such Invoice Item: 'ii_nope'(livemode=true)"
    )
    assert exc_info.value.stripe_body["error"]["code"] == "resource_missing"
    assert exc_info.value.stripe_body["error"]["param"] == "id"


def test_update_and_delete_of_a_pending_item(instance, customer) -> None:
    one = item(instance, customer, 900, "revisable")
    updated = call(instance, "POST", f"/v1/invoiceitems/{one}", {"description": "revised"})
    assert updated["description"] == "revised"
    assert updated["pricing"]["price_details"]["price"].startswith("price_")
    deleted = call(instance, "DELETE", f"/v1/invoiceitems/{one}")
    assert deleted == {"id": one, "object": "invoiceitem", "deleted": True}
    with pytest.raises(StripeToolError) as exc_info:
        call(instance, "GET", f"/v1/invoiceitems/{one}")
    assert exc_info.value.status == 404


def test_an_amount_edit_re_mints_the_price_against_the_same_product(instance, customer) -> None:
    """Recorded (cassette 13 step 33): only the price id changes on the
    wire — the catalog row, the pricing echo and the invoice line's echo
    all keep naming ONE product, and no phantom product row or event
    appears on the edit."""
    one = call(
        instance,
        "POST",
        "/v1/invoiceitems",
        {"customer": customer, "amount": 500, "currency": "usd", "description": "re-priced"},
    )
    old_product = one["pricing"]["price_details"]["product"]
    before = instance.inspect().one("SELECT COUNT(*) AS n FROM products")["n"]
    events_before = instance.inspect().one(
        "SELECT COUNT(*) AS n FROM events WHERE type = 'product.created'"
    )["n"]
    edited = call(instance, "POST", f"/v1/invoiceitems/{one['id']}", {"amount": 700})
    new_price = edited["pricing"]["price_details"]["price"]
    assert new_price != one["pricing"]["price_details"]["price"]
    assert edited["pricing"]["price_details"]["product"] == old_product
    # the catalog row agrees with the echo — no drift, no phantom product
    price_row = call(instance, "GET", f"/v1/prices/{new_price}")
    assert price_row["product"] == old_product
    assert price_row["unit_amount"] == 700
    assert instance.inspect().one("SELECT COUNT(*) AS n FROM products")["n"] == before
    assert (
        instance.inspect().one("SELECT COUNT(*) AS n FROM events WHERE type = 'product.created'")[
            "n"
        ]
        == events_before
    )
    # the re-mint's price event stands under the catalog-rows ruling
    assert (
        instance.inspect().one("SELECT COUNT(*) AS n FROM events WHERE type = 'price.created'")["n"]
        == 2
    )


def test_the_dead_invoice_refusal_pair(instance, customer) -> None:
    """Recorded (cassette 13): items attached to a deleted invoice refuse
    delete and read as deleted on update."""
    one = item(instance, customer, 700, "swept")
    invoice = call(
        instance,
        "POST",
        "/v1/invoices",
        {"customer": customer, "pending_invoice_items_behavior": "include"},
    )
    call(instance, "DELETE", f"/v1/invoices/{invoice['id']}")
    with pytest.raises(StripeToolError) as exc_info:
        call(instance, "DELETE", f"/v1/invoiceitems/{one}")
    assert exc_info.value.status == 400
    assert exc_info.value.stripe_body["error"]["message"] == (
        "Can't delete an invoice item that is attached to an invoice that is no longer editable"
    )
    with pytest.raises(StripeToolError) as exc_info:
        call(instance, "POST", f"/v1/invoiceitems/{one}", {"description": "nope"})
    assert exc_info.value.status == 400
    assert exc_info.value.stripe_body["error"]["message"] == "This invoice item has been deleted."
    # the finalized-invoice flavor of the same gate
    two = item(instance, customer, 300, "finalized")
    invoice2 = call(
        instance,
        "POST",
        "/v1/invoices",
        {"customer": customer, "pending_invoice_items_behavior": "include"},
    )
    call(instance, "POST", f"/v1/invoices/{invoice2['id']}/finalize", {})
    with pytest.raises(StripeToolError) as exc_info:
        call(instance, "DELETE", f"/v1/invoiceitems/{two}")
    assert exc_info.value.stripe_body["error"]["message"] == (
        "Can't delete an invoice item that is attached to an invoice that is no longer editable"
    )


def test_item_events_are_created_and_deleted_only(instance, customer) -> None:
    item(instance, customer, 500, "watched")
    one = item(instance, customer, 510, "gone")
    call(instance, "POST", f"/v1/invoiceitems/{one}", {"description": "no event for this"})
    call(instance, "DELETE", f"/v1/invoiceitems/{one}")
    types = [
        row["type"]
        for row in instance.inspect().rows("SELECT type FROM events ORDER BY x_seq")
        if row["type"].startswith("invoiceitem")
    ]
    # invoiceitem.updated does not exist in the closed event set — Stripe's
    # own catalog emits none, and neither does this world
    assert types == ["invoiceitem.created", "invoiceitem.created", "invoiceitem.deleted"]
