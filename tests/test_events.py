"""The `/v1/events` surface: list and retrieve of events emitted by earlier
resource phases.

Events are written by `emit_event` inside each resource's state transitions;
this module tests the read surface: serialization shape, filtering (type
wildcard, types array, created range, delivery_success quirk), pagination,
and the retrieve-by-id path.
"""

import pytest
import seahaven

from conftest import BLANK_NOW, api_read, api_write, dispatch_tool
from seahaven_stripe_world.errors import StripeToolError

pytestmark = pytest.mark.seahaven(fixture=None, now=BLANK_NOW, clock_mode="fixed")


# -- helpers ------------------------------------------------------------------


def _read(instance: seahaven.Instance, path: str, params: dict | None = None) -> dict:
    return api_read(instance, path, params)


def _write(
    instance: seahaven.Instance,
    path: str,
    params: dict | None = None,
) -> dict:
    return api_write(instance, "POST", path, params)


def _create_customer(instance: seahaven.Instance, name: str = "Test") -> dict:
    return _write(instance, "/v1/customers", {"name": name})


# -- shape tests --------------------------------------------------------------


def test_customer_created_event_shape(instance: seahaven.Instance) -> None:
    """A customer.created event carries the full event object shape."""
    cus = _create_customer(instance, "Alice")
    events = _read(instance, "/v1/events", {"type": "customer.created"})
    assert len(events["data"]) == 1
    evt = events["data"][0]

    # Required fields per spec
    assert evt["id"].startswith("evt_")
    assert evt["object"] == "event"
    assert evt["api_version"] == "2026-08-26.dahlia"
    assert isinstance(evt["created"], int)
    assert evt["livemode"] is True
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
    assert len(events["data"]) == 1
    evt = events["data"][0]
    assert evt["type"] == "customer.updated"
    assert evt["data"]["previous_attributes"]["name"] == "Before"
    assert evt["data"]["object"]["name"] == "After"


def test_event_request_field_with_idempotency_key(probe) -> None:
    """event.request.idempotency_key is populated when the call had one.
    Uses ``call_stripe`` because ``stripe_api_write`` no longer carries
    ``idempotency_key`` on the MCP surface."""
    world_p = probe(dispatch_tool())
    with world_p.instance(None, now=BLANK_NOW) as instance:
        instance.call(
            "call_stripe",
            method="POST",
            path="/v1/customers",
            params={"name": "Keyed"},
            idempotency_key="key-123",
        )
        row = instance.inspect().one(
            "SELECT request_idempotency_key FROM events ORDER BY x_seq DESC LIMIT 1"
        )
        assert row is not None
        assert row["request_idempotency_key"] == "key-123"


# -- list filtering ------------------------------------------------------------


def test_list_events_newest_first(instance: seahaven.Instance) -> None:
    """Events list in reverse chronological order (newest first)."""
    _create_customer(instance, "First")
    _create_customer(instance, "Second")
    events = _read(instance, "/v1/events", {"type": "customer.created"})
    data = events["data"]
    assert len(data) == 2
    # Second customer was created after the first, so it appears first in the list
    assert data[0]["data"]["object"]["name"] == "Second"
    assert data[1]["data"]["object"]["name"] == "First"


def test_list_events_type_filter(instance: seahaven.Instance) -> None:
    """Exact type filter returns only matching events."""
    _create_customer(instance)
    _write(instance, "/v1/products", {"name": "Widget"})
    events = _read(instance, "/v1/events", {"type": "customer.created"})
    for evt in events["data"]:
        assert evt["type"] == "customer.created"


def test_list_events_type_wildcard(instance: seahaven.Instance) -> None:
    """Wildcard `customer.*` matches customer.created, customer.updated, etc."""
    cus = _create_customer(instance, "Wild")
    _write(instance, f"/v1/customers/{cus['id']}", {"name": "Wilder"})
    # Also create a product to make sure it is excluded
    _write(instance, "/v1/products", {"name": "Widget"})
    events = _read(instance, "/v1/events", {"type": "customer.*"})
    types = {evt["type"] for evt in events["data"]}
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
    types = {evt["type"] for evt in events["data"]}
    assert types == {"customer.created", "product.created"}


def test_list_events_type_and_types_mutually_exclusive(instance: seahaven.Instance) -> None:
    """Sending both `type` and `types` is rejected."""
    _create_customer(instance)
    with pytest.raises(StripeToolError) as exc_info:
        _read(
            instance,
            "/v1/events",
            {"type": "customer.created", "types": ["customer.created"]},
        )
    assert exc_info.value.status == 400
    assert "mutually exclusive" in exc_info.value.message


# The assertions read every event as created at exactly BLANK_NOW, which only a
# fixed clock gives; the core's default clock (`running`) moves on from it.
@pytest.mark.seahaven(fixture=None, now=BLANK_NOW, clock_mode="fixed")
def test_list_events_created_range(instance: seahaven.Instance) -> None:
    """Range filter on created timestamp."""
    from seahaven_stripe_world._time import to_unix

    _create_customer(instance)
    # BLANK_NOW is "2026-09-01T14:00:00.000Z" -> Unix 1788361200
    now_unix = to_unix(BLANK_NOW)
    # All events were created at BLANK_NOW, so gte=now should include them
    events = _read(instance, "/v1/events", {"created": {"gte": now_unix}})
    assert len(events["data"]) >= 1
    # And gt=now should exclude them (strict greater)
    events2 = _read(instance, "/v1/events", {"created": {"gt": now_unix}})
    assert len(events2["data"]) == 0


def test_list_events_delivery_success_true(instance: seahaven.Instance) -> None:
    """delivery_success=true returns all events (no actual delivery)."""
    _create_customer(instance)
    events = _read(instance, "/v1/events", {"delivery_success": True})
    assert len(events["data"]) >= 1


def test_list_events_delivery_success_false(instance: seahaven.Instance) -> None:
    """delivery_success=false returns nothing (no actual delivery)."""
    _create_customer(instance)
    events = _read(instance, "/v1/events", {"delivery_success": False})
    assert events["data"] == []
    assert events["has_more"] is False


# -- pagination ----------------------------------------------------------------


def test_list_events_pagination(instance: seahaven.Instance) -> None:
    """Pagination with starting_after and ending_before."""
    for i in range(5):
        _create_customer(instance, f"Cus{i}")
    all_events = _read(instance, "/v1/events", {"limit": 100})
    all_data = all_events["data"]
    assert len(all_data) >= 5

    # Page with limit=2
    page1 = _read(instance, "/v1/events", {"limit": 2})
    assert len(page1["data"]) == 2
    assert page1["has_more"] is True

    # Next page
    last_id = page1["data"][-1]["id"]
    page2 = _read(instance, "/v1/events", {"limit": 2, "starting_after": last_id})
    assert len(page2["data"]) == 2
    # Pages should not overlap
    page1_ids = {e["id"] for e in page1["data"]}
    page2_ids = {e["id"] for e in page2["data"]}
    assert page1_ids.isdisjoint(page2_ids)


# -- retrieve ------------------------------------------------------------------


def test_retrieve_event_by_id(instance: seahaven.Instance) -> None:
    """Retrieve a specific event by id."""
    _create_customer(instance, "Retrievable")
    events = _read(instance, "/v1/events")
    evt_id = events["data"][0]["id"]

    result = _read(instance, f"/v1/events/{evt_id}")
    assert result["id"] == evt_id
    assert result["object"] == "event"


def test_retrieve_unknown_event_404(instance: seahaven.Instance) -> None:
    """A missing event id returns 404."""
    with pytest.raises(StripeToolError) as exc_info:
        _read(instance, "/v1/events/evt_nonexistent")
    assert exc_info.value.status == 404
    assert "No such event" in exc_info.value.message


# -- error handling ------------------------------------------------------------


def test_unknown_parameter_refused(instance: seahaven.Instance) -> None:
    """An unknown parameter returns parameter_unknown."""
    with pytest.raises(StripeToolError) as exc_info:
        _read(instance, "/v1/events", {"bogus": "nope"})
    assert exc_info.value.stripe_body["error"]["code"] == "parameter_unknown"


def test_list_url_in_envelope(instance: seahaven.Instance) -> None:
    """The list envelope's url field is /v1/events."""
    _create_customer(instance)
    events = _read(instance, "/v1/events")
    assert events["url"] == "/v1/events"


# -- cross-cutting event tests from §5.4 ------------------------------------


def test_unknown_type_is_a_world_bug() -> None:
    """An unknown event type is a WorldBug, not a ToolError (§3.4.4)."""
    import seahaven_stripe_world
    from seahaven_stripe_world.resources.events import emit_event

    with seahaven_stripe_world.world.instance(None, now=BLANK_NOW) as inst, inst.bulk() as ctx:
        ctx.state["_request"] = {"id": "req_test", "idempotency_key": None}
        with pytest.raises(seahaven.WorldBug, match="unknown event type"):
            emit_event(ctx, type="bogus.nonexistent", obj={"id": "x", "object": "x"})


def test_data_object_is_the_snapshot(instance: seahaven.Instance) -> None:
    """The event's data.object is the snapshot at the time of emission, not
    the object's current state (§3.4.3)."""
    cus = _create_customer(instance, "Before")
    _write(instance, f"/v1/customers/{cus['id']}", {"name": "After"})
    # The customer.created event still shows the name at creation time
    events = _read(instance, "/v1/events", {"type": "customer.created"})
    evt = events["data"][0]
    assert evt["data"]["object"]["name"] == "Before"
    # Current state shows the updated name
    current = _read(instance, f"/v1/customers/{cus['id']}")
    assert current["name"] == "After"


def test_rolled_back_call_emits_no_event(instance: seahaven.Instance) -> None:
    """A handler that raises loses its writes, including its events: the
    insert rolls back with everything else (§3.4.2)."""
    before = _read(instance, "/v1/events", {"limit": 100})
    before_count = len(before["data"])
    # A bad-expand POST to customers raises pre-execution, nothing commits
    with pytest.raises(StripeToolError):
        _write(instance, "/v1/customers", {"email": "fail@example.test", "expand": ["bogus"]})
    after = _read(instance, "/v1/events", {"limit": 100})
    assert len(after["data"]) == before_count


def test_events_ordered_by_seq_within_one_instant(instance: seahaven.Instance) -> None:
    """Events emitted in one call list in emission order, not random id
    order (§3.4.5): the `seq` column provides a total order under the
    frozen clock."""
    # Creating a customer emits customer.created
    _create_customer(instance, "First")
    # Creating another customer emits another customer.created
    _create_customer(instance, "Second")
    events = _read(instance, "/v1/events", {"type": "customer.created"})
    data = events["data"]
    # Newest first: "Second" appears before "First"
    assert data[0]["data"]["object"]["name"] == "Second"
    assert data[1]["data"]["object"]["name"] == "First"


def test_no_event_update_or_delete_route(instance: seahaven.Instance) -> None:
    """Events are immutable: the route table has no update or delete for
    events (§3.4.5)."""
    from seahaven_stripe_world.dispatch.router import ROUTER

    assert ROUTER.resolve("POST", "/v1/events/{id}") is None
    assert ROUTER.resolve("DELETE", "/v1/events/{id}") is None


def test_every_emitted_type_is_in_the_closed_set() -> None:
    """Every `emit_event` literal in `resources/` and `billing/` is in
    `event_types.py`'s closed set (§3.4.4)."""
    import re
    from pathlib import Path

    from seahaven_stripe_world.spec import EVENT_TYPES

    src = Path(__file__).resolve().parent.parent / "src" / "seahaven_stripe_world"
    emitted: set[str] = set()
    for subdir in ("resources", "billing"):
        for py in sorted((src / subdir).rglob("*.py")):
            if "__pycache__" in str(py):
                continue
            text = py.read_text()
            # Find all emit_event calls with type= keyword
            for match in re.finditer(r'emit_event\([^)]*type\s*=\s*["\']([^"\']+)["\']', text):
                emitted.add(match.group(1))
    assert emitted, "no emit_event calls found — something is wrong"
    unknown = emitted - EVENT_TYPES
    assert unknown == set(), f"event types not in the closed set: {unknown}"
