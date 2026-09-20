"""The promotion_codes slice, through the real four-tool chain: the pinned
version's `promotion: {type, coupon}` nesting (no top-level `coupon` field,
no top-level `coupon` parameter), the minted code shape, and every recorded
refusal — Phase 7 live probes at `2026-08-26.dahlia`."""

import pytest
import seahaven

from conftest import BLANK_NOW

pytestmark = pytest.mark.seahaven(fixture=None, now=BLANK_NOW)


def coupon(instance: seahaven.Instance) -> str:
    result = instance.call(
        "stripe_api_write",
        method="POST",
        path="/v1/coupons",
        params={"amount_off": 500, "currency": "usd", "duration": "once"},
    )
    assert result["status"] == 200, result
    return result["body"]["id"]


def create(instance: seahaven.Instance, **params: object) -> dict:
    result = instance.call(
        "stripe_api_write", method="POST", path="/v1/promotion_codes", params=params
    )
    assert result["status"] == 200, result
    return result["body"]


def call(instance: seahaven.Instance, method: str, path: str, params: dict | None = None):
    if method == "GET":
        return instance.call("stripe_api_read", path=path, params=params)
    return instance.call("stripe_api_write", method=method, path=path, params=params)


def test_create_nests_the_coupon_under_promotion(instance: seahaven.Instance) -> None:
    coupon_id = coupon(instance)
    body = create(instance, promotion={"type": "coupon", "coupon": coupon_id})
    assert body["object"] == "promotion_code"
    assert body["promotion"] == {"coupon": coupon_id, "type": "coupon"}
    # No top-level coupon field exists at the pinned version.
    assert "coupon" not in body
    assert body["active"] is True
    assert body["customer"] is None
    assert body["expires_at"] is None
    assert body["max_redemptions"] is None
    assert body["times_redeemed"] == 0
    assert body["restrictions"] == {
        "first_time_transaction": False,
        "minimum_amount": None,
        "minimum_amount_currency": None,
    }
    # The minted code: eight uppercase alphanumerics (recorded: `BFDACGQS`).
    assert len(body["code"]) == 8
    assert body["code"].isupper() and body["code"].isalnum()


def test_caller_code_round_trips_and_restrictions_canonicalize(
    instance: seahaven.Instance,
) -> None:
    coupon_id = coupon(instance)
    body = create(
        instance,
        promotion={"type": "coupon", "coupon": coupon_id},
        code="Phase7-Code",
        restrictions={
            "first_time_transaction": True,
            "minimum_amount": 1000,
            "minimum_amount_currency": "usd",
        },
    )
    assert body["code"] == "Phase7-Code"
    assert body["restrictions"] == {
        "first_time_transaction": True,
        "minimum_amount": 1000,
        "minimum_amount_currency": "usd",
    }


def test_promotion_parameter_is_required_and_coupon_must_exist(
    instance: seahaven.Instance,
) -> None:
    result = call(instance, "POST", "/v1/promotion_codes", {"code": "XCODE1"})
    assert result["status"] == 400
    assert result["body"]["error"]["code"] == "parameter_missing"
    assert result["body"]["error"]["message"] == "Missing required param: promotion."

    result = call(instance, "POST", "/v1/promotion_codes", {"promotion": {"type": "coupon"}})
    assert result["status"] == 400
    assert (
        result["body"]["error"]["message"]
        == "You must pass promotion.coupon when passing promotion"
    )
    assert result["body"]["error"]["param"] == "promotion.coupon"

    result = call(
        instance,
        "POST",
        "/v1/promotion_codes",
        {"promotion": {"type": "coupon", "coupon": "NOPE1234"}},
    )
    assert result["status"] == 400
    assert result["body"]["error"]["code"] == "resource_missing"
    assert result["body"]["error"]["message"] == "No such coupon: 'NOPE1234'"
    # Bracket-spelled, unlike the dot-spelled missing-param refusal above.
    assert result["body"]["error"]["param"] == "promotion[coupon]"


def test_top_level_coupon_parameter_is_unknown(instance: seahaven.Instance) -> None:
    coupon_id = coupon(instance)
    result = call(instance, "POST", "/v1/promotion_codes", {"coupon": coupon_id})
    assert result["status"] == 400
    assert result["body"]["error"]["code"] == "parameter_unknown"
    assert result["body"]["error"]["message"] == "Received unknown parameter: coupon"


def test_code_validations(instance: seahaven.Instance) -> None:
    coupon_id = coupon(instance)
    result = call(
        instance,
        "POST",
        "/v1/promotion_codes",
        {"promotion": {"type": "coupon", "coupon": coupon_id}, "code": "has spaces!"},
    )
    assert result["status"] == 400
    assert result["body"]["error"]["param"] == "code"
    assert result["body"]["error"]["message"] == (
        "This value must match the regex pattern. (/\\A[a-zA-Z0-9\\-_]+\\z/ does not "
        "match for the value has spaces!)."
    )
    create(instance, promotion={"type": "coupon", "coupon": coupon_id}, code="TAKEN1")
    result = call(
        instance,
        "POST",
        "/v1/promotion_codes",
        {"promotion": {"type": "coupon", "coupon": coupon_id}, "code": "TAKEN1"},
    )
    assert result["status"] == 400
    assert (
        result["body"]["error"]["message"]
        == "An active promotion code with `code: TAKEN1` already exists."
    )


def test_customer_and_expires_at_validations(instance: seahaven.Instance) -> None:
    coupon_id = coupon(instance)
    result = call(
        instance,
        "POST",
        "/v1/promotion_codes",
        {"promotion": {"type": "coupon", "coupon": coupon_id}, "customer": "cus_nope"},
    )
    assert result["status"] == 400
    assert result["body"]["error"]["code"] == "resource_missing"
    assert result["body"]["error"]["param"] == "customer"

    result = call(
        instance,
        "POST",
        "/v1/promotion_codes",
        {"promotion": {"type": "coupon", "coupon": coupon_id}, "expires_at": 2000},
    )
    assert result["status"] == 400
    assert result["body"]["error"]["param"] == "expires_at"
    assert result["body"]["error"]["message"].endswith(
        "You specified the value `2000` which is in the past."
    )

    result = call(
        instance,
        "POST",
        "/v1/promotion_codes",
        {"promotion": {"type": "coupon", "coupon": coupon_id}, "expires_at": 4102444800},
    )
    assert result["status"] == 400
    assert result["body"]["error"]["message"] == (
        "Invalid timestamp: can be no more than five years in the future."
    )


def test_update_and_filter(instance: seahaven.Instance) -> None:
    customer = instance.call(
        "stripe_api_write", method="POST", path="/v1/customers", params={"email": "p7@example.test"}
    )
    coupon_id = coupon(instance)
    body = create(
        instance,
        promotion={"type": "coupon", "coupon": coupon_id},
        customer=customer["body"]["id"],
        expires_at=1800000000,
    )
    result = call(instance, "POST", f"/v1/promotion_codes/{body['id']}", {"active": False})
    assert result["status"] == 200
    assert result["body"]["active"] is False
    event = instance.inspect().one(
        "SELECT json_extract(data, '$.previous_attributes') AS prev FROM events"
        " WHERE type = 'promotion_code.updated'"
    )
    assert event is not None
    assert event["prev"] == '{"active":true}'

    for params, expected in (
        ({"coupon": coupon_id}, [body["id"]]),
        ({"code": body["code"]}, [body["id"]]),
        ({"customer": customer["body"]["id"]}, [body["id"]]),
        ({"coupon": coupon_id, "active": True}, []),
    ):
        listed = call(instance, "GET", "/v1/promotion_codes", params)
        assert [item["id"] for item in listed["body"]["data"]] == expected, params

    unknown = call(instance, "GET", "/v1/promotion_codes", {"coupon": "UNKNOWN1"})
    assert unknown["status"] == 400
    assert unknown["body"]["error"]["message"] == "No such coupon: 'UNKNOWN1'"
    assert unknown["body"]["error"]["param"] == "coupon"


def test_expanding_promotion_coupon_inflates_the_coupon(instance: seahaven.Instance) -> None:
    coupon_id = coupon(instance)
    body = create(instance, promotion={"type": "coupon", "coupon": coupon_id})
    result = call(
        instance, "GET", f"/v1/promotion_codes/{body['id']}", {"expand": ["promotion.coupon"]}
    )
    assert result["status"] == 200
    inflated = result["body"]["promotion"]["coupon"]
    assert inflated["object"] == "coupon"
    assert inflated["id"] == coupon_id
    # Expanding `promotion` alone is an accepted no-op — it is an embedded
    # object the x-expandableFields list names, the recorded `address`
    # precedent (`serialize/expand.py`'s module docstring).
    bare = call(instance, "GET", f"/v1/promotion_codes/{body['id']}", {"expand": ["promotion"]})
    assert bare["status"] == 200
    assert bare["body"]["promotion"] == {"coupon": coupon_id, "type": "coupon"}


def test_bogus_id_message_is_two_words(instance: seahaven.Instance) -> None:
    result = call(instance, "GET", "/v1/promotion_codes/promo_nope")
    assert result["status"] == 404
    assert result["body"]["error"]["message"] == "No such promotion code: 'promo_nope'"
    assert result["body"]["error"]["param"] == "promotion_code"
