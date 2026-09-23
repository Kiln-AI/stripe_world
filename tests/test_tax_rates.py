"""The tax_rates slice, through the real four-tool chain: the TEXT-decimal
rate, the read-only response fields a percentage rate answers with, and the
boolean filters — pinned by the Phase 7 live probes at `2026-08-26.dahlia`."""

import pytest
import seahaven

from conftest import BLANK_NOW
from seahaven_stripe_world.errors import StripeToolError

pytestmark = pytest.mark.seahaven(fixture=None, now=BLANK_NOW)


def create(instance: seahaven.Instance, **params: object) -> dict:
    params.setdefault("display_name", "CA sales tax")
    params.setdefault("inclusive", False)
    params.setdefault("percentage", 8.875)
    return instance.call("stripe_api_write", method="POST", path="/v1/tax_rates", params=params)


def call(instance: seahaven.Instance, method: str, path: str, params: dict | None = None):
    if method == "GET":
        return instance.call("stripe_api_read", path=path, params=params)
    return instance.call("stripe_api_write", method=method, path=path, params=params)


def test_create_defaults_and_read_only_fields(instance: seahaven.Instance) -> None:
    body = create(instance, country="US", state="CA", jurisdiction="US-CA")
    assert body["object"] == "tax_rate"
    assert body["id"].startswith("txr_")
    assert body["active"] is True
    assert body["percentage"] == 8.875
    assert body["effective_percentage"] == 8.875
    assert body["rate_type"] == "percentage"
    assert body["flat_amount"] is None
    assert body["jurisdiction_level"] is None
    assert body["jurisdiction"] == "US-CA"
    assert body["country"] == "US"
    assert body["state"] == "CA"
    assert body["tax_type"] is None
    assert body["description"] is None
    assert body["livemode"] is True
    # The rate is stored as exact TEXT decimal digits, never a float.
    row = instance.inspect().one("SELECT percentage FROM tax_rates WHERE id = ?", body["id"])
    assert row is not None
    assert row["percentage"] == "8.875"


def test_an_integer_percentage_round_trips_as_given(instance: seahaven.Instance) -> None:
    body = create(instance, display_name="VAT", inclusive=True, percentage=20, tax_type="vat")
    assert body["inclusive"] is True
    assert body["tax_type"] == "vat"
    assert body["percentage"] == 20


def test_required_parameters(instance: seahaven.Instance) -> None:
    with pytest.raises(StripeToolError) as exc_info:
        call(instance, "POST", "/v1/tax_rates", {"display_name": "T", "inclusive": True})
    assert exc_info.value.stripe_body["error"]["code"] == "parameter_missing"
    assert exc_info.value.stripe_body["error"]["message"] == "Missing required param: percentage."

    with pytest.raises(StripeToolError) as exc_info:
        call(instance, "POST", "/v1/tax_rates", {"display_name": "T", "percentage": 10})
    assert exc_info.value.stripe_body["error"]["param"] == "inclusive"


def test_update_and_filters(instance: seahaven.Instance) -> None:
    one = create(instance)
    two = create(instance, display_name="VAT", inclusive=True)
    result = call(
        instance, "POST", f"/v1/tax_rates/{one['id']}", {"active": False, "description": "archived"}
    )
    assert result["active"] is False
    assert result["description"] == "archived"
    event = instance.inspect().one(
        "SELECT json_extract(data, '$.previous_attributes') AS prev FROM events"
        " WHERE type = 'tax_rate.updated'"
    )
    assert event is not None
    assert event["prev"] == '{"active":true,"description":null}'

    active = call(instance, "GET", "/v1/tax_rates", {"active": True})
    assert [item["id"] for item in active["data"]] == [two["id"]]
    inclusive = call(instance, "GET", "/v1/tax_rates", {"inclusive": True})
    assert [item["id"] for item in inclusive["data"]] == [two["id"]]


def test_bogus_id_message_names_two_words(instance: seahaven.Instance) -> None:
    with pytest.raises(StripeToolError) as exc_info:
        call(instance, "GET", "/v1/tax_rates/txr_nope")
    assert exc_info.value.status == 404
    assert exc_info.value.stripe_body["error"]["message"] == "No such tax rate: 'txr_nope'"
    assert exc_info.value.stripe_body["error"]["param"] == "tax_rate"
