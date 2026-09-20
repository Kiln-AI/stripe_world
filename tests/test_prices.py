"""The prices slice, through the real four-tool chain: the one_time and
recurring shapes, the cross-field refusals, the lookup-key conflict and
transfer, and the filters — all pinned by the Phase 7 live probes at
`2026-08-26.dahlia`."""

import pytest
import seahaven

from conftest import BLANK_NOW

pytestmark = pytest.mark.seahaven(fixture=None, now=BLANK_NOW)


def product(instance: seahaven.Instance) -> str:
    result = instance.call(
        "stripe_api_write", method="POST", path="/v1/products", params={"name": "P"}
    )
    assert result["status"] == 200, result
    return result["body"]["id"]


def create(instance: seahaven.Instance, **params: object) -> dict:
    params.setdefault("currency", "usd")
    if not any(
        key in params
        for key in ("unit_amount", "unit_amount_decimal", "custom_unit_amount", "billing_scheme")
    ):
        params.setdefault("unit_amount", 1000)
    result = instance.call(
        "stripe_api_write", method="POST", path="/v1/prices", params=dict(params)
    )
    assert result["status"] == 200, result
    return result["body"]


def call(instance: seahaven.Instance, method: str, path: str, params: dict | None = None):
    if method == "GET":
        return instance.call("stripe_api_read", path=path, params=params)
    return instance.call("stripe_api_write", method=method, path=path, params=params)


def test_one_time_defaults(instance: seahaven.Instance) -> None:
    prod = product(instance)
    body = create(instance, product=prod, nickname="one time")
    assert body["object"] == "price"
    assert body["type"] == "one_time"
    assert body["billing_scheme"] == "per_unit"
    assert body["active"] is True
    assert body["recurring"] is None
    assert body["tax_behavior"] == "unspecified"
    assert body["unit_amount"] == 1000
    assert body["unit_amount_decimal"] == "1000"
    assert body["lookup_key"] is None
    assert body["product"] == prod
    # Stored but never serialized at the pinned version (probed).
    assert "tiers" not in body
    assert "currency_options" not in body


def test_recurring_canonical_shape(instance: seahaven.Instance) -> None:
    body = create(instance, product=product(instance), recurring={"interval": "month"})
    assert body["type"] == "recurring"
    # `trial_period_days` is the one recurring key the live body carries that
    # the pinned spec does not declare — not emitted here, allow-listed.
    assert body["recurring"] == {
        "interval": "month",
        "interval_count": 1,
        "meter": None,
        "usage_type": "licensed",
    }


def test_decimal_amount_leaves_unit_amount_null(instance: seahaven.Instance) -> None:
    body = create(instance, product=product(instance), unit_amount_decimal="123.45")
    assert body["unit_amount"] is None
    assert body["unit_amount_decimal"] == "123.45"


def test_tiered_requires_recurring_and_hides_amounts(instance: seahaven.Instance) -> None:
    prod = product(instance)
    result = call(
        instance,
        "POST",
        "/v1/prices",
        {
            "currency": "usd",
            "product": prod,
            "billing_scheme": "tiered",
            "tiers_mode": "volume",
            "tiers": [{"up_to": "inf", "unit_amount": 500}],
        },
    )
    assert result["status"] == 400
    # Probed verbatim, including the surprising param.
    assert result["body"]["error"]["message"] == (
        "Prices with `type=one_time` are not supported with tiered billing."
    )
    assert result["body"]["error"]["param"] == "interval"
    body = create(
        instance,
        product=prod,
        billing_scheme="tiered",
        tiers_mode="graduated",
        recurring={"interval": "month"},
        tiers=[{"up_to": 5, "unit_amount": 500}, {"up_to": "inf", "unit_amount": 400}],
    )
    assert body["unit_amount"] is None
    assert body["unit_amount_decimal"] is None
    assert body["tiers_mode"] == "graduated"
    assert "tiers" not in body


def test_custom_unit_amount_shape(instance: seahaven.Instance) -> None:
    body = create(
        instance,
        product=product(instance),
        custom_unit_amount={"enabled": True, "maximum": 10000, "minimum": 100, "preset": 500},
    )
    # Probed: `enabled` does not survive onto the wire.
    assert body["custom_unit_amount"] == {"maximum": 10000, "minimum": 100, "preset": 500}
    assert body["unit_amount"] is None
    assert body["unit_amount_decimal"] is None


def test_transform_quantity_echoes(instance: seahaven.Instance) -> None:
    body = create(
        instance,
        product=product(instance),
        transform_quantity={"divide_by": 5, "round": "up"},
    )
    assert body["transform_quantity"] == {"divide_by": 5, "round": "up"}


def test_product_xor_product_data_refusals(instance: seahaven.Instance) -> None:
    result = call(instance, "POST", "/v1/prices", {"currency": "usd", "unit_amount": 1000})
    assert result["status"] == 400
    assert result["body"]["error"]["message"] == (
        "You must specify either `product` or `product_data` when creating a price."
    )
    prod = product(instance)
    result = call(
        instance,
        "POST",
        "/v1/prices",
        {"currency": "usd", "unit_amount": 1000, "product": prod, "product_data": {"name": "x"}},
    )
    assert result["status"] == 400
    assert result["body"]["error"]["message"] == (
        "You may only specify one of these parameters: product, product_data."
    )
    assert result["body"]["error"]["param"] == "product"


def test_unknown_product_refuses_at_400(instance: seahaven.Instance) -> None:
    result = call(
        instance,
        "POST",
        "/v1/prices",
        {"currency": "usd", "unit_amount": 100, "product": "prod_nope"},
    )
    assert result["status"] == 400
    assert result["body"]["error"]["code"] == "resource_missing"
    assert result["body"]["error"]["param"] == "product"


def test_amount_requirement_and_metered_refusal(instance: seahaven.Instance) -> None:
    prod = product(instance)
    for extra in ({}, {"recurring": {"interval": "month"}}):
        result = call(instance, "POST", "/v1/prices", {"currency": "usd", "product": prod, **extra})
        assert result["status"] == 400
        assert result["body"]["error"]["message"] == (
            "Prices require an `unit_amount` or `unit_amount_decimal` parameter to be set."
        )
    result = call(
        instance,
        "POST",
        "/v1/prices",
        {
            "currency": "usd",
            "product": prod,
            "unit_amount": 5,
            "recurring": {"interval": "month", "usage_type": "metered"},
        },
    )
    assert result["status"] == 400
    assert result["body"]["error"]["message"] == (
        "Starting with Stripe version `2025-03-31.basil`, metered prices must be backed by meters."
    )


def test_product_data_creates_the_inline_product(instance: seahaven.Instance) -> None:
    body = create(instance, product_data={"name": "inline prod"})
    assert body["product"].startswith("prod_")
    product_body = call(instance, "GET", f"/v1/products/{body['product']}")
    assert product_body["status"] == 200
    assert product_body["body"]["name"] == "inline prod"


def test_lookup_key_conflict_and_transfer(instance: seahaven.Instance) -> None:
    prod = product(instance)
    holder = create(instance, product=prod, lookup_key="k1")
    conflict = call(
        instance,
        "POST",
        "/v1/prices",
        {"currency": "usd", "product": prod, "unit_amount": 500, "lookup_key": "k1"},
    )
    assert conflict["status"] == 400
    assert (
        conflict["body"]["error"]["message"]
        == f"A price (`{holder['id']}`) already uses that lookup key."
    )
    assert conflict["body"]["error"]["param"] == "lookup_key"

    # Transfer: the holder's key is cleared, not archived (probed: the holder
    # keeps active). The holder's price.updated is the only update event a
    # create-with-transfer emits — the transferee gets its price.created.
    transferee = create(
        instance, product=prod, unit_amount=555, lookup_key="k1", transfer_lookup_key=True
    )
    assert transferee["lookup_key"] == "k1"
    after = call(instance, "GET", f"/v1/prices/{holder['id']}")
    assert after["body"]["lookup_key"] is None
    assert after["body"]["active"] is True
    events_seen = instance.inspect().rows(
        "SELECT type, json_extract(data, '$.previous_attributes') AS prev,"
        " json_extract(data, '$.object.id') AS id FROM events WHERE type = 'price.updated'"
        " ORDER BY x_seq"
    )
    assert [(row["id"], row["prev"]) for row in events_seen] == [
        (holder["id"], '{"lookup_key":"k1"}'),
    ]


def test_lookup_key_transfer_on_update(instance: seahaven.Instance) -> None:
    prod = product(instance)
    one = create(instance, product=prod, lookup_key="update-key")
    two = create(instance, product=prod, unit_amount=222)
    result = call(
        instance,
        "POST",
        f"/v1/prices/{two['id']}",
        {"lookup_key": "update-key", "transfer_lookup_key": True},
    )
    assert result["status"] == 200
    assert result["body"]["lookup_key"] == "update-key"
    cleared = call(instance, "GET", f"/v1/prices/{one['id']}")
    assert cleared["body"]["lookup_key"] is None
    # Re-sending one's own key is not a conflict.
    again = call(instance, "POST", f"/v1/prices/{two['id']}", {"lookup_key": "update-key"})
    assert again["status"] == 200


def test_inactive_holder_still_conflicts(instance: seahaven.Instance) -> None:
    """Probed: the uniqueness is over every holder, live or not — the
    data-model design's live-only reading was wrong."""
    prod = product(instance)
    holder = create(instance, product=prod, lookup_key="sticky")
    call(instance, "POST", f"/v1/prices/{holder['id']}", {"active": False})
    conflict = call(
        instance,
        "POST",
        "/v1/prices",
        {"currency": "usd", "product": prod, "unit_amount": 1, "lookup_key": "sticky"},
    )
    assert conflict["status"] == 400
    assert "already uses that lookup key" in conflict["body"]["error"]["message"]


def test_filters(instance: seahaven.Instance) -> None:
    prod = product(instance)
    one_time = create(instance, product=prod)
    recurring = create(instance, product=prod, recurring={"interval": "month"})
    listed = call(instance, "GET", "/v1/prices", {"product": prod, "type": "recurring"})
    assert [item["id"] for item in listed["body"]["data"]] == [recurring["id"]]
    by_interval = call(instance, "GET", "/v1/prices", {"recurring": {"interval": "month"}})
    assert recurring["id"] in [item["id"] for item in by_interval["body"]["data"]]
    assert one_time["id"] not in [item["id"] for item in by_interval["body"]["data"]]
    keys = call(instance, "GET", "/v1/prices", {"lookup_keys": ["nope"]})
    assert keys["body"]["data"] == []
    bad_product = call(instance, "GET", "/v1/prices", {"product": "prod_nope"})
    assert bad_product["status"] == 400
    assert bad_product["body"]["error"]["message"] == "No such product: 'prod_nope'"


def test_no_delete_route(instance: seahaven.Instance) -> None:
    """Probed: DELETE on a price is the router's 404, `Unrecognized request
    URL`, not a handler's refusal."""
    body = create(instance, product=product(instance))
    result = call(instance, "DELETE", f"/v1/prices/{body['id']}")
    assert result["status"] == 404
    assert result["body"]["error"]["message"].startswith("Unrecognized request URL")


def test_bogus_price_id_names_the_placeholder(instance: seahaven.Instance) -> None:
    result = call(instance, "GET", "/v1/prices/price_nope")
    assert result["status"] == 404
    assert result["body"]["error"]["message"] == "No such price: 'price_nope'"
    assert result["body"]["error"]["param"] == "price"
