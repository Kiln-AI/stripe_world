"""The products slice, through the real four-tool chain. Every default,
wire shape and refusal here is pinned by the Phase 7 live probes at
`2026-08-26.dahlia`; the undeclared-on-spec fields the live body carries
(`attributes`, `type`, `tax_details`) are asserted absent — the allow-list
carries the difference, not the serializer."""

import pytest
import seahaven

from conftest import BLANK_NOW
from seahaven_stripe_world.errors import StripeToolError

pytestmark = pytest.mark.seahaven(fixture=None, now=BLANK_NOW)


def create(instance: seahaven.Instance, **params: object) -> dict:
    return instance.call("stripe_api_write", method="POST", path="/v1/products", params=params)


def call(instance: seahaven.Instance, method: str, path: str, params: dict | None = None):
    if method == "GET":
        return instance.call("stripe_api_read", path=path, params=params)
    return instance.call("stripe_api_write", method=method, path=path, params=params)


def test_create_defaults(instance: seahaven.Instance) -> None:
    body = create(instance, name="Widget")
    assert body["id"].startswith("prod_")
    assert body["object"] == "product"
    assert body["active"] is True
    assert body["images"] == []
    assert body["marketing_features"] == []
    assert body["metadata"] == {}
    assert body["default_price"] is None
    assert body["shippable"] is None
    assert body["created"] == body["updated"]
    # The pinned spec's property set is the authority: the live body's
    # `attributes`, `type` and `tax_details` are deliberately not emitted.
    for undeclared in ("attributes", "type", "tax_details"):
        assert undeclared not in body


def test_full_body_round_trips(instance: seahaven.Instance) -> None:
    body = create(
        instance,
        name="Full",
        shippable=True,
        statement_descriptor="FULL DESC",
        unit_label="widget",
        url="https://example.test/full",
        tax_code="txcd_10103000",
        description="d",
        images=["https://example.test/i.png"],
        marketing_features=[{"name": "feature one"}],
        package_dimensions={"height": 1, "length": 2, "weight": 3, "width": 4},
        metadata={"k": "v"},
    )
    assert body["shippable"] is True
    assert body["images"] == ["https://example.test/i.png"]
    assert body["marketing_features"] == [{"name": "feature one"}]
    assert body["package_dimensions"] == {"height": 1, "length": 2, "weight": 3, "width": 4}
    assert body["tax_code"] == "txcd_10103000"
    assert body["metadata"] == {"k": "v"}


def test_default_price_data_sets_default_price_and_emits_both_events(
    instance: seahaven.Instance,
) -> None:
    body = create(
        instance, name="Priced", default_price_data={"currency": "usd", "unit_amount": 1500}
    )
    assert body["default_price"].startswith("price_")
    price = call(instance, "GET", f"/v1/prices/{body['default_price']}")
    assert price["unit_amount"] == 1500
    assert price["product"] == body["id"]
    # The recorded event order: product.created first (its snapshot carries
    # default_price already set), then the inline price's price.created.
    types = instance.inspect().rows(
        "SELECT type FROM events WHERE type IN ('product.created', 'price.created') ORDER BY x_seq"
    )
    assert [row["type"] for row in types] == ["product.created", "price.created"]
    snapshot = instance.inspect().one(
        "SELECT json_extract(data, '$.object.default_price') AS dp FROM events"
        " WHERE type = 'product.created'"
    )
    assert snapshot is not None
    assert snapshot["dp"] == body["default_price"]


def test_update_stamps_updated_and_previous_attributes(instance: seahaven.Instance) -> None:
    product = create(instance, name="Before")
    result = call(
        instance,
        "POST",
        f"/v1/products/{product['id']}",
        {"description": "after", "metadata": {"a": "1"}},
    )
    assert result["description"] == "after"
    event = instance.inspect().one(
        "SELECT json_extract(data, '$.previous_attributes') AS prev FROM events"
        " WHERE type = 'product.updated'"
    )
    assert event is not None
    # Per-key metadata diff, exactly as recorded: the newly-set key maps to
    # null, not the whole prior map.
    assert event["prev"] == '{"description":null,"metadata":{"a":null}}'


def test_update_default_price_requires_a_live_price(instance: seahaven.Instance) -> None:
    product = create(instance, name="DP")
    with pytest.raises(StripeToolError) as exc_info:
        call(instance, "POST", f"/v1/products/{product['id']}", {"default_price": "price_nope"})
    assert exc_info.value.status == 400
    assert exc_info.value.stripe_body["error"]["code"] == "resource_missing"
    assert exc_info.value.stripe_body["error"]["message"] == "No such price: 'price_nope'"


def test_delete_zeroes_active_tombstones_and_404s_the_retrieve(instance: seahaven.Instance) -> None:
    product = create(instance, name="Gone")
    result = call(instance, "DELETE", f"/v1/products/{product['id']}")
    assert result == {"id": product["id"], "object": "product", "deleted": True}
    # Probed: products 404 after delete (unlike customers' stub), naming
    # `param: id`.
    with pytest.raises(StripeToolError) as exc_info:
        call(instance, "GET", f"/v1/products/{product['id']}")
    assert exc_info.value.status == 404
    assert exc_info.value.stripe_body["error"]["message"] == f"No such product: '{product['id']}'"
    assert exc_info.value.stripe_body["error"]["param"] == "id"
    # The deleted-event snapshot carries active: false (probed).
    snapshot = instance.inspect().one(
        "SELECT json_extract(data, '$.object.active') AS a"
        " FROM events WHERE type = 'product.deleted'"
    )
    assert snapshot is not None
    assert snapshot["a"] == 0
    listed = call(instance, "GET", "/v1/products")
    assert all(item["id"] != product["id"] for item in listed["data"])


def test_list_filters(instance: seahaven.Instance) -> None:
    one = create(instance, name="one", shippable=True, url="https://example.test/one")
    create(instance, name="two")
    ids = call(instance, "GET", "/v1/products", {"ids": [one["id"], "prod_nope"]})
    assert [item["id"] for item in ids["data"]] == [one["id"]]
    shippable = call(instance, "GET", "/v1/products", {"shippable": True})
    assert one["id"] in [item["id"] for item in shippable["data"]]
    by_url = call(instance, "GET", "/v1/products", {"url": "https://example.test/one"})
    assert [item["id"] for item in by_url["data"]] == [one["id"]]
    # A deactivated product drops out of ?active=true (probed shape).
    call(instance, "POST", f"/v1/products/{one['id']}", {"active": False})
    active = call(instance, "GET", "/v1/products", {"ids": [one["id"]], "active": True})
    assert active["data"] == []


def test_a_product_with_prices_refuses_its_delete(instance: seahaven.Instance) -> None:
    product = create(instance, name="priced")
    instance.call(
        "stripe_api_write",
        method="POST",
        path="/v1/prices",
        params={"currency": "usd", "unit_amount": 100, "product": product["id"]},
    )
    with pytest.raises(StripeToolError) as exc_info:
        call(instance, "DELETE", f"/v1/products/{product['id']}")
    assert exc_info.value.status == 400
    error = exc_info.value.stripe_body["error"]
    assert error["message"] == (
        "This product cannot be deleted because it has one or more user-created prices."
    )
    assert "code" not in error
    assert "param" not in error
    # The refusal wrote nothing: the product is still retrievable.
    still = call(instance, "GET", f"/v1/products/{product['id']}")
    assert still["object"] == "product"


def test_missing_name_is_the_standard_missing_parameter(instance: seahaven.Instance) -> None:
    with pytest.raises(StripeToolError) as exc_info:
        call(instance, "POST", "/v1/products", {})
    assert exc_info.value.status == 400
    assert exc_info.value.stripe_body["error"]["code"] == "parameter_missing"
    assert exc_info.value.stripe_body["error"]["message"] == "Missing required param: name."
