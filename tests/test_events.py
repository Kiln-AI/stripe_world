"""The `/v1/events` surface: list and retrieve of events emitted by earlier
resource phases.

Events are written by `emit_event` inside each resource's state transitions;
this module tests the read surface: serialization shape, filtering (type
wildcard, types array, created range, delivery_success quirk), pagination,
and the retrieve-by-id path.
"""

import pytest
import seahaven

from conftest import BLANK_NOW

pytestmark = pytest.mark.seahaven(fixture=None, now=BLANK_NOW)


# -- helpers ------------------------------------------------------------------


def _read(instance: seahaven.Instance, path: str, params: dict | None = None) -> dict:
    return instance.call("stripe_api_read", path=path, params=params)


def _write(
    instance: seahaven.Instance,
    path: str,
    params: dict | None = None,
    idempotency_key: str | None = None,
) -> dict:
    return instance.call(
        "stripe_api_write",
        method="POST",
        path=path,
        params=params,
        **({"idempotency_key": idempotency_key} if idempotency_key else {}),
    )


def _create_customer(instance: seahaven.Instance, name: str = "Test") -> dict:
    result = _write(instance, "/v1/customers", {"name": name})
    assert result["status"] == 200
    return result["body"]


# -- shape tests --------------------------------------------------------------


def test_customer_created_event_shape(instance: seahaven.Instance) -> None:
    """A customer.created event carries the full event object shape."""
    cus = _create_customer(instance, "Alice")
    events = _read(instance, "/v1/events", {"type": "customer.created"})
    assert events["status"] == 200
    assert len(events["body"]["data"]) == 1
    evt = events["body"]["data"][0]

    # Required fields per spec
    assert evt["id"].startswith("evt_")
    assert evt["object"] == "event"
    assert evt["api_version"] == "2026-08-26.dahlia"
    assert isinstance(evt["created"], int)
    assert evt["livemode"] is False
    assert evt["pending_webhooks"] == 0
    assert evt["type"] == "customer.created"

    # data.object is the customer snapshot
    assert evt["data"]["object"]["id"] == cus["id"]
    assert evt["data"]["object"]["object"] == "customer"
    assert evt["data"]["object"]["name"] == "Alice"

    # request field
    assert evt["request"]["id"].startswith("req_")
    assert evt["request"]["idempotency_key"] is None


def test_event_previous_attributes(instance: seahaven.Instance) -> None:
    """An update event carries data.previous_attributes for the changed fields."""
    cus = _create_customer(instance, "Before")
    _write(instance, f"/v1/customers/{cus['id']}", {"name": "After"})
    events = _read(instance, "/v1/events", {"type": "customer.updated"})
    assert events["status"] == 200
    assert len(events["body"]["data"]) == 1
    evt = events["body"]["data"][0]
    assert evt["type"] == "customer.updated"
    assert evt["data"]["previous_attributes"]["name"] == "Before"
    assert evt["data"]["object"]["name"] == "After"


def test_event_request_field_with_idempotency_key(instance: seahaven.Instance) -> None:
    """event.request.idempotency_key is populated when the call had one."""
    _write(instance, "/v1/customers", {"name": "Keyed"}, idempotency_key="key-123")
    events = _read(instance, "/v1/events", {"type": "customer.created"})
    assert events["status"] == 200
    evt = events["body"]["data"][0]
    assert evt["request"]["idempotency_key"] == "key-123"


# -- list filtering ------------------------------------------------------------


def test_list_events_newest_first(instance: seahaven.Instance) -> None:
    """Events list in reverse chronological order (newest first)."""
    _create_customer(instance, "First")
    _create_customer(instance, "Second")
    events = _read(instance, "/v1/events", {"type": "customer.created"})
    assert events["status"] == 200
    data = events["body"]["data"]
    assert len(data) == 2
    # Second customer was created after the first, so it appears first in the list
    assert data[0]["data"]["object"]["name"] == "Second"
    assert data[1]["data"]["object"]["name"] == "First"


def test_list_events_type_filter(instance: seahaven.Instance) -> None:
    """Exact type filter returns only matching events."""
    _create_customer(instance)
    _write(instance, "/v1/products", {"name": "Widget"})
    events = _read(instance, "/v1/events", {"type": "customer.created"})
    assert events["status"] == 200
    for evt in events["body"]["data"]:
        assert evt["type"] == "customer.created"


def test_list_events_type_wildcard(instance: seahaven.Instance) -> None:
    """Wildcard `customer.*` matches customer.created, customer.updated, etc."""
    cus = _create_customer(instance, "Wild")
    _write(instance, f"/v1/customers/{cus['id']}", {"name": "Wilder"})
    # Also create a product to make sure it is excluded
    _write(instance, "/v1/products", {"name": "Widget"})
    events = _read(instance, "/v1/events", {"type": "customer.*"})
    assert events["status"] == 200
    types = {evt["type"] for evt in events["body"]["data"]}
    assert "customer.created" in types
    assert "customer.updated" in types
    assert "product.created" not in types


def test_list_events_types_array(instance: seahaven.Instance) -> None:
    """The `types` array filter matches multiple specific types."""
    cus = _create_customer(instance, "Multi")
    _write(instance, "/v1/products", {"name": "Widget"})
    _write(instance, f"/v1/customers/{cus['id']}", {"name": "Renamed"})
    events = _read(
        instance,
        "/v1/events",
        {"types": ["customer.created", "product.created"]},
    )
    assert events["status"] == 200
    types = {evt["type"] for evt in events["body"]["data"]}
    assert types == {"customer.created", "product.created"}


def test_list_events_type_and_types_mutually_exclusive(instance: seahaven.Instance) -> None:
    """Sending both `type` and `types` is rejected."""
    _create_customer(instance)
    result = _read(
        instance,
        "/v1/events",
        {"type": "customer.created", "types": ["customer.created"]},
    )
    assert result["status"] == 400
    assert "mutually exclusive" in result["body"]["error"]["message"]


def test_list_events_created_range(instance: seahaven.Instance) -> None:
    """Range filter on created timestamp."""
    from seahaven_stripe_world._time import to_unix

    _create_customer(instance)
    # BLANK_NOW is "2026-09-01T14:00:00.000Z" -> Unix 1788361200
    now_unix = to_unix(BLANK_NOW)
    # All events were created at BLANK_NOW, so gte=now should include them
    events = _read(instance, "/v1/events", {"created": {"gte": now_unix}})
    assert events["status"] == 200
    assert len(events["body"]["data"]) >= 1
    # And gt=now should exclude them (strict greater)
    events2 = _read(instance, "/v1/events", {"created": {"gt": now_unix}})
    assert events2["status"] == 200
    assert len(events2["body"]["data"]) == 0


def test_list_events_delivery_success_true(instance: seahaven.Instance) -> None:
    """delivery_success=true returns all events (no actual delivery)."""
    _create_customer(instance)
    events = _read(instance, "/v1/events", {"delivery_success": True})
    assert events["status"] == 200
    assert len(events["body"]["data"]) >= 1


def test_list_events_delivery_success_false(instance: seahaven.Instance) -> None:
    """delivery_success=false returns nothing (no actual delivery)."""
    _create_customer(instance)
    events = _read(instance, "/v1/events", {"delivery_success": False})
    assert events["status"] == 200
    assert events["body"]["data"] == []
    assert events["body"]["has_more"] is False


# -- pagination ----------------------------------------------------------------


def test_list_events_pagination(instance: seahaven.Instance) -> None:
    """Pagination with starting_after and ending_before."""
    for i in range(5):
        _create_customer(instance, f"Cus{i}")
    all_events = _read(instance, "/v1/events", {"limit": 100})
    assert all_events["status"] == 200
    all_data = all_events["body"]["data"]
    assert len(all_data) >= 5

    # Page with limit=2
    page1 = _read(instance, "/v1/events", {"limit": 2})
    assert page1["status"] == 200
    assert len(page1["body"]["data"]) == 2
    assert page1["body"]["has_more"] is True

    # Next page
    last_id = page1["body"]["data"][-1]["id"]
    page2 = _read(instance, "/v1/events", {"limit": 2, "starting_after": last_id})
    assert page2["status"] == 200
    assert len(page2["body"]["data"]) == 2
    # Pages should not overlap
    page1_ids = {e["id"] for e in page1["body"]["data"]}
    page2_ids = {e["id"] for e in page2["body"]["data"]}
    assert page1_ids.isdisjoint(page2_ids)


# -- retrieve ------------------------------------------------------------------


def test_retrieve_event_by_id(instance: seahaven.Instance) -> None:
    """Retrieve a specific event by id."""
    _create_customer(instance, "Retrievable")
    events = _read(instance, "/v1/events")
    assert events["status"] == 200
    evt_id = events["body"]["data"][0]["id"]

    result = _read(instance, f"/v1/events/{evt_id}")
    assert result["status"] == 200
    assert result["body"]["id"] == evt_id
    assert result["body"]["object"] == "event"


def test_retrieve_unknown_event_404(instance: seahaven.Instance) -> None:
    """A missing event id returns 404."""
    result = _read(instance, "/v1/events/evt_nonexistent")
    assert result["status"] == 404
    assert result["body"]["error"]["type"] == "invalid_request_error"
    assert "No such event" in result["body"]["error"]["message"]


# -- error handling ------------------------------------------------------------


def test_unknown_parameter_refused(instance: seahaven.Instance) -> None:
    """An unknown parameter returns parameter_unknown."""
    result = _read(instance, "/v1/events", {"bogus": "nope"})
    assert result["status"] == 400
    assert result["body"]["error"]["code"] == "parameter_unknown"


def test_list_url_in_envelope(instance: seahaven.Instance) -> None:
    """The list envelope's url field is /v1/events."""
    _create_customer(instance)
    events = _read(instance, "/v1/events")
    assert events["status"] == 200
    assert events["body"]["url"] == "/v1/events"
