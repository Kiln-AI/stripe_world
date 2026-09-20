"""The coupons slice, through the real four-tool chain: the unprefixed
caller-suppliable id, the percent/amount cross-field refusals, the
never-serialized `applies_to`/`currency_options`, and the tombstone that
zeroes `valid` — all pinned by the Phase 7 live probes at
`2026-08-26.dahlia`."""

import pytest
import seahaven

from conftest import BLANK_NOW

pytestmark = pytest.mark.seahaven(fixture=None, now=BLANK_NOW)


def create(instance: seahaven.Instance, **params: object) -> dict:
    result = instance.call("stripe_api_write", method="POST", path="/v1/coupons", params=params)
    assert result["status"] == 200, result
    return result["body"]


def call(instance: seahaven.Instance, method: str, path: str, params: dict | None = None):
    if method == "GET":
        return instance.call("stripe_api_read", path=path, params=params)
    return instance.call("stripe_api_write", method=method, path=path, params=params)


def test_percent_coupon_defaults(instance: seahaven.Instance) -> None:
    body = create(instance, percent_off=22.5, duration="once")
    assert body["object"] == "coupon"
    assert body["percent_off"] == 22.5
    assert body["amount_off"] is None
    assert body["currency"] is None
    assert body["duration"] == "once"
    assert body["duration_in_months"] is None
    assert body["times_redeemed"] == 0
    assert body["valid"] is True
    assert body["redeem_by"] is None
    assert body["livemode"] is False
    # The minted id: eight mixed-case alphanumerics, unprefixed (recorded:
    # `hbzb1NEf`).
    assert len(body["id"]) == 8
    assert body["id"].isalnum()


def test_amount_coupon_requires_currency_and_repeating_months(instance: seahaven.Instance) -> None:
    body = create(
        instance,
        amount_off=500,
        currency="usd",
        duration="repeating",
        duration_in_months=3,
        name="five off",
        max_redemptions=10,
    )
    assert body["amount_off"] == 500
    assert body["currency"] == "usd"
    assert body["duration"] == "repeating"
    assert body["duration_in_months"] == 3
    assert body["max_redemptions"] == 10
    assert body["percent_off"] is None


def test_cross_field_refusals_are_verbatim(instance: seahaven.Instance) -> None:
    cases = [
        (
            {"duration": "once"},
            "Must provide percent_off or amount_off.",
            "parameter_missing",
            None,
        ),
        (
            {"percent_off": 10, "amount_off": 500, "currency": "usd", "duration": "once"},
            "Received both percent_off and amount_off parameters. Please pass in only one.",
            None,
            None,
        ),
        (
            {"amount_off": 500, "duration": "once"},
            "You must pass currency when passing amount_off",
            None,
            "currency",
        ),
        (
            {"percent_off": 10, "duration": "repeating"},
            "The duration_in_months param must be set when creating a coupon "
            "with a repeating duration",
            None,
            "duration",
        ),
        (
            {"percent_off": 0, "duration": "once"},
            "This value must be greater than or equal to 0.01 (it currently is '0.0').",
            None,
            "percent_off",
        ),
    ]
    for params, message, code, param in cases:
        result = call(instance, "POST", "/v1/coupons", params)
        assert result["status"] == 400, params
        error = result["body"]["error"]
        assert error["message"] == message, params
        assert error.get("code") == code, params
        assert error.get("param") == param, params


def test_redeem_by_in_the_past_refuses(instance: seahaven.Instance) -> None:
    result = call(
        instance, "POST", "/v1/coupons", {"percent_off": 10, "duration": "once", "redeem_by": 1000}
    )
    assert result["status"] == 400
    assert result["body"]["error"]["param"] == "redeem_by"
    assert result["body"]["error"]["message"] == (
        "The parameter `redeem_by` expects a unix timestamp representing a date and "
        "time in the future. You specified the value `1000` which is in the past."
    )


def test_caller_supplied_id_round_trips_and_dupe_refuses(instance: seahaven.Instance) -> None:
    body = create(instance, id="TENOFF", percent_off=10, duration="forever")
    assert body["id"] == "TENOFF"
    result = call(
        instance, "POST", "/v1/coupons", {"id": "TENOFF", "percent_off": 5, "duration": "once"}
    )
    assert result["status"] == 400
    assert result["body"]["error"]["code"] == "resource_already_exists"
    assert result["body"]["error"]["message"] == "Coupon already exists."


def test_applies_to_and_currency_options_are_stored_not_emitted(
    instance: seahaven.Instance,
) -> None:
    prod = instance.call(
        "stripe_api_write", method="POST", path="/v1/products", params={"name": "P"}
    )
    body = create(
        instance,
        amount_off=500,
        currency="usd",
        duration="once",
        applies_to={"products": [prod["body"]["id"]]},
        currency_options={"eur": {"amount_off": 400}},
    )
    assert "applies_to" not in body
    assert "currency_options" not in body
    row = instance.inspect().one(
        "SELECT applies_to, currency_options FROM coupons WHERE id = ?", body["id"]
    )
    assert row is not None
    assert row["applies_to"] == '{{"products":["{}"]}}'.format(prod["body"]["id"])
    assert row["currency_options"] == '{"eur":{"amount_off":400}}'
    # Probed verbatim: a percent coupon carries no per-currency amounts, and
    # this refusal outranks the map's own key validation.
    refused = call(
        instance,
        "POST",
        "/v1/coupons",
        {"percent_off": 5, "duration": "once", "currency_options": {"eur": {"amount_off": 4}}},
    )
    assert refused["status"] == 400
    error = refused["body"]["error"]
    assert error["message"] == (
        "You may only specify one of these parameters: currency_options, percent_off."
    )
    assert error["param"] == "currency_options"


def test_update_merges_currency_options_and_renames(instance: seahaven.Instance) -> None:
    body = create(instance, amount_off=500, currency="usd", duration="once")
    result = call(
        instance,
        "POST",
        f"/v1/coupons/{body['id']}",
        {
            "name": "renamed",
            "metadata": {"k": "v"},
            "currency_options": {"gbp": {"amount_off": 300}},
        },
    )
    assert result["status"] == 200
    assert result["body"]["name"] == "renamed"
    row = instance.inspect().one("SELECT currency_options FROM coupons WHERE id = ?", body["id"])
    assert row is not None
    assert row["currency_options"] == '{"gbp":{"amount_off":300}}'
    event = instance.inspect().one(
        "SELECT json_extract(data, '$.previous_attributes') AS prev FROM events"
        " WHERE type = 'coupon.updated'"
    )
    assert event is not None
    assert event["prev"] == '{"metadata":{"k":null},"name":null}'


def test_currency_options_map_refusals(instance: seahaven.Instance) -> None:
    """The map parameter's own refusals, probed live at the pinned version:
    `Invalid object` for a scalar, the lowercase hint for a supported code in
    the wrong case, and the currencies-list refusal for anything else. The
    154-code list is transcribed in `dispatch/params.py` (see its comment for
    why it is not a cassette step)."""
    scalar = call(
        instance,
        "POST",
        "/v1/coupons",
        {"amount_off": 500, "currency": "usd", "duration": "once", "currency_options": 5},
    )
    assert scalar["status"] == 400
    assert scalar["body"]["error"]["message"] == "Invalid object"
    assert scalar["body"]["error"]["param"] == "currency_options"

    upper = call(
        instance,
        "POST",
        "/v1/coupons",
        {
            "amount_off": 500,
            "currency": "usd",
            "duration": "once",
            "currency_options": {"EUR": {"amount_off": 4}},
        },
    )
    assert upper["status"] == 400
    assert upper["body"]["error"]["message"] == (
        "Currencies must be lowercase when used as keys in a map. Use `eur` instead of `EUR`."
    )

    bad = call(
        instance,
        "POST",
        "/v1/coupons",
        {
            "amount_off": 500,
            "currency": "usd",
            "duration": "once",
            "currency_options": {"xyz": {"amount_off": 4}},
        },
    )
    assert bad["status"] == 400
    message = bad["body"]["error"]["message"]
    assert message.startswith("Invalid currency: xyz. Stripe currently supports these currencies: ")
    assert "usd, aed, afn" in message


def test_delete_zeroes_valid_and_404s_the_retrieve(instance: seahaven.Instance) -> None:
    body = create(instance, percent_off=10, duration="once")
    result = call(instance, "DELETE", f"/v1/coupons/{body['id']}")
    assert result["status"] == 200
    assert result["body"] == {"id": body["id"], "object": "coupon", "deleted": True}
    gone = call(instance, "GET", f"/v1/coupons/{body['id']}")
    assert gone["status"] == 404
    assert gone["body"]["error"]["message"] == f"No such coupon: '{body['id']}'"
    assert gone["body"]["error"]["param"] == "coupon"
    snapshot = instance.inspect().one(
        "SELECT json_extract(data, '$.object.valid') AS v FROM events WHERE type = 'coupon.deleted'"
    )
    assert snapshot is not None
    assert snapshot["v"] == 0
