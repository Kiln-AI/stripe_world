"""The subscription schedules slice's routed surface, through the real
four-tool chain.  Bodies, defaults, status transitions, and refusals --
pinned by the Phase 16 implementation (scoped-down phases model)."""

import pytest
import seahaven

from conftest import BLANK_NOW
from seahaven_stripe_world.errors import StripeToolError

pytestmark = pytest.mark.seahaven(fixture=None, now=BLANK_NOW)


def call(instance: seahaven.Instance, method: str, path: str, params: dict | None = None):
    if method == "GET":
        return instance.call("stripe_api_read", path=path, params=params)
    return instance.call("stripe_api_write", method=method, path=path, params=params)


def setup_catalog(instance: seahaven.Instance) -> tuple[str, str, str]:
    """Create a customer with a payment method and a recurring price."""
    cus = call(instance, "POST", "/v1/customers", {"email": "sched@example.test"})["id"]
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
    prod = call(instance, "POST", "/v1/products", {"name": "schedule suite"})["id"]
    price = call(
        instance,
        "POST",
        "/v1/prices",
        {
            "product": prod,
            "unit_amount": 3000,
            "currency": "usd",
            "recurring": {"interval": "month"},
        },
    )["id"]
    return cus, pm, price


# --- the created body's constants ---------------------------------------------------


def test_the_created_body_constants(instance: seahaven.Instance) -> None:
    """A freshly created subscription schedule carries the expected shape."""
    cus, _, price = setup_catalog(instance)
    body = call(
        instance,
        "POST",
        "/v1/subscription_schedules",
        {
            "customer": cus,
            "phases": [{"items": [{"price": price}]}],
        },
    )
    assert body["object"] == "subscription_schedule"
    assert body["id"].startswith("sub_sched_")
    assert body["livemode"] is True
    assert body["customer"] == cus
    assert body["end_behavior"] == "release"  # the default
    assert body["application"] is None
    assert body["customer_account"] is None
    assert body["test_clock"] is None
    assert body["billing_mode"]["type"] == "classic"
    # default_settings carries the expected constants
    ds = body["default_settings"]
    assert ds["application_fee_percent"] is None
    assert ds["automatic_tax"]["enabled"] is False
    assert ds["billing_cycle_anchor"] == "automatic"
    assert ds["billing_thresholds"] is None
    assert ds["on_behalf_of"] is None
    assert ds["transfer_data"] is None
    assert ds["invoice_settings"]["issuer"] == {"type": "self"}


def test_create_with_phases_activates_immediately(instance: seahaven.Instance) -> None:
    """When the first phase starts at or before now, the schedule becomes
    active and creates a subscription."""
    cus, _, price = setup_catalog(instance)
    body = call(
        instance,
        "POST",
        "/v1/subscription_schedules",
        {
            "customer": cus,
            "phases": [{"items": [{"price": price}]}],
        },
    )
    assert body["status"] == "active"
    assert body["subscription"] is not None
    assert body["subscription"].startswith("sub_")
    assert body["current_phase"] is not None
    assert "start_date" in body["current_phase"]
    assert "end_date" in body["current_phase"]
    # The phases array is serialized with the expected shape
    assert len(body["phases"]) == 1
    phase = body["phases"][0]
    assert len(phase["items"]) == 1
    assert phase["items"][0]["price"]["id"] == price
    assert phase["items"][0]["quantity"] == 1
    assert phase["proration_behavior"] == "create_prorations"
    assert phase["currency"] == "usd"


def test_create_with_iterations(instance: seahaven.Instance) -> None:
    """The `iterations` parameter computes the phase end_date from the price's
    recurring interval, preserving multi-interval-count strides (e.g. a
    quarterly price with iterations=2 spans 6 months, not 6 single-month
    additions)."""
    cus, _, _ = setup_catalog(instance)
    prod = call(instance, "POST", "/v1/products", {"name": "quarterly"})["id"]
    quarterly = call(
        instance,
        "POST",
        "/v1/prices",
        {
            "product": prod,
            "unit_amount": 9000,
            "currency": "usd",
            "recurring": {"interval": "month", "interval_count": 3},
        },
    )["id"]
    body = call(
        instance,
        "POST",
        "/v1/subscription_schedules",
        {
            "customer": cus,
            "phases": [{"items": [{"price": quarterly}], "iterations": 2}],
        },
    )
    phase = body["phases"][0]
    # 2 iterations of a quarterly price = 6 months from start
    start = phase["start_date"]
    end = phase["end_date"]
    # 6 months from 2026-09-01 14:00:00 UTC -> 2027-03-01 14:00:00 UTC
    assert end - start > 0
    # Verify it is approximately 6 months (roughly 180 days)
    delta_days = (end - start) / 86400
    assert 180 <= delta_days <= 184


def test_create_multi_phase(instance: seahaven.Instance) -> None:
    """Creating a schedule with multiple phases chains their start/end dates."""
    cus, _, _ = setup_catalog(instance)
    prod = call(instance, "POST", "/v1/products", {"name": "multi"})["id"]
    p1 = call(
        instance,
        "POST",
        "/v1/prices",
        {
            "product": prod,
            "unit_amount": 1000,
            "currency": "usd",
            "recurring": {"interval": "month"},
        },
    )["id"]
    p2 = call(
        instance,
        "POST",
        "/v1/prices",
        {
            "product": prod,
            "unit_amount": 2000,
            "currency": "usd",
            "recurring": {"interval": "month"},
        },
    )["id"]
    body = call(
        instance,
        "POST",
        "/v1/subscription_schedules",
        {
            "customer": cus,
            "phases": [
                {"items": [{"price": p1}]},
                {"items": [{"price": p2}]},
            ],
        },
    )
    assert len(body["phases"]) == 2
    # The second phase starts where the first ends
    assert body["phases"][1]["start_date"] == body["phases"][0]["end_date"]
    # Each phase carries its own price
    assert body["phases"][0]["items"][0]["price"]["id"] == p1
    assert body["phases"][1]["items"][0]["price"]["id"] == p2


def test_create_with_future_start_stays_not_started(instance: seahaven.Instance) -> None:
    """A schedule whose first phase starts in the future stays not_started."""
    cus, _, price = setup_catalog(instance)
    # Far future: 2028-01-01
    body = call(
        instance,
        "POST",
        "/v1/subscription_schedules",
        {
            "customer": cus,
            "start_date": 1830297600,
            "phases": [{"items": [{"price": price}]}],
        },
    )
    assert body["status"] == "not_started"
    assert body["subscription"] is None
    assert body["current_phase"] is None


def test_create_without_phases_stays_not_started(instance: seahaven.Instance) -> None:
    """Creating a schedule without phases results in not_started."""
    cus, _, _price = setup_catalog(instance)
    body = call(
        instance,
        "POST",
        "/v1/subscription_schedules",
        {"customer": cus},
    )
    assert body["status"] == "not_started"
    assert body["subscription"] is None
    assert body["phases"] == []


def test_create_from_subscription(instance: seahaven.Instance) -> None:
    """Creating a schedule from an existing subscription links them and goes
    active, building a single phase from the subscription's current state."""
    cus, _, price = setup_catalog(instance)
    sub = call(
        instance,
        "POST",
        "/v1/subscriptions",
        {"customer": cus, "items": [{"price": price}]},
    )
    body = call(
        instance,
        "POST",
        "/v1/subscription_schedules",
        {"customer": cus, "from_subscription": sub["id"]},
    )
    assert body["status"] == "active"
    assert body["subscription"] == sub["id"]
    assert len(body["phases"]) == 1
    phase = body["phases"][0]
    assert phase["items"][0]["price"]["id"] == price
    # The subscription's schedule field is back-linked
    refreshed = call(instance, "GET", f"/v1/subscriptions/{sub['id']}")
    assert refreshed["schedule"] == body["id"]


def test_from_subscription_refuses_already_scheduled(instance: seahaven.Instance) -> None:
    """A subscription already linked to a schedule cannot be scheduled again."""
    cus, _, price = setup_catalog(instance)
    sub = call(
        instance,
        "POST",
        "/v1/subscriptions",
        {"customer": cus, "items": [{"price": price}]},
    )
    call(
        instance,
        "POST",
        "/v1/subscription_schedules",
        {"customer": cus, "from_subscription": sub["id"]},
    )
    with pytest.raises(StripeToolError) as exc_info:
        call(
            instance,
            "POST",
            "/v1/subscription_schedules",
            {"customer": cus, "from_subscription": sub["id"]},
        )
    assert exc_info.value.status == 400
    assert "already associated" in exc_info.value.stripe_body["error"]["message"]


# --- metadata ---------------------------------------------------------------------


def test_metadata_rides_create_and_update(instance: seahaven.Instance) -> None:
    cus, _, price = setup_catalog(instance)
    body = call(
        instance,
        "POST",
        "/v1/subscription_schedules",
        {
            "customer": cus,
            "phases": [{"items": [{"price": price}]}],
            "metadata": {"a": "1"},
        },
    )
    assert body["metadata"] == {"a": "1"}
    body = call(
        instance,
        "POST",
        f"/v1/subscription_schedules/{body['id']}",
        {"metadata": {"b": "2"}},
    )
    assert body["metadata"] == {"a": "1", "b": "2"}
    body = call(
        instance,
        "POST",
        f"/v1/subscription_schedules/{body['id']}",
        {"metadata": {"a": None}},
    )
    assert body["metadata"] == {"b": "2"}


# --- list and retrieve ------------------------------------------------------------


def test_list_returns_newest_first(instance: seahaven.Instance) -> None:
    cus, _, price = setup_catalog(instance)
    first = call(
        instance,
        "POST",
        "/v1/subscription_schedules",
        {"customer": cus, "phases": [{"items": [{"price": price}]}]},
    )
    second = call(
        instance,
        "POST",
        "/v1/subscription_schedules",
        {"customer": cus, "phases": [{"items": [{"price": price}]}]},
    )
    listed = call(instance, "GET", "/v1/subscription_schedules", {"customer": cus})
    assert listed["object"] == "list"
    assert [item["id"] for item in listed["data"]] == [second["id"], first["id"]]
    assert listed["url"] == "/v1/subscription_schedules"


def test_retrieve_by_id(instance: seahaven.Instance) -> None:
    cus, _, price = setup_catalog(instance)
    created = call(
        instance,
        "POST",
        "/v1/subscription_schedules",
        {"customer": cus, "phases": [{"items": [{"price": price}]}]},
    )
    retrieved = call(instance, "GET", f"/v1/subscription_schedules/{created['id']}")
    assert retrieved["id"] == created["id"]
    assert retrieved["object"] == "subscription_schedule"


def test_retrieve_unknown_id_is_404(instance: seahaven.Instance) -> None:
    with pytest.raises(StripeToolError) as exc_info:
        call(instance, "GET", "/v1/subscription_schedules/sub_sched_nope")
    assert exc_info.value.status == 404
    assert exc_info.value.stripe_body["error"]["code"] == "resource_missing"


# --- update -----------------------------------------------------------------------


def test_update_end_behavior(instance: seahaven.Instance) -> None:
    cus, _, price = setup_catalog(instance)
    body = call(
        instance,
        "POST",
        "/v1/subscription_schedules",
        {"customer": cus, "phases": [{"items": [{"price": price}]}]},
    )
    assert body["end_behavior"] == "release"
    updated = call(
        instance,
        "POST",
        f"/v1/subscription_schedules/{body['id']}",
        {"end_behavior": "cancel"},
    )
    assert updated["end_behavior"] == "cancel"


def test_update_default_settings(instance: seahaven.Instance) -> None:
    cus, _, price = setup_catalog(instance)
    body = call(
        instance,
        "POST",
        "/v1/subscription_schedules",
        {"customer": cus, "phases": [{"items": [{"price": price}]}]},
    )
    updated = call(
        instance,
        "POST",
        f"/v1/subscription_schedules/{body['id']}",
        {
            "default_settings": {
                "collection_method": "send_invoice",
                "description": "monthly plan",
            },
        },
    )
    assert updated["default_settings"]["collection_method"] == "send_invoice"
    assert updated["default_settings"]["description"] == "monthly plan"


def test_update_refuses_terminal_statuses(instance: seahaven.Instance) -> None:
    """A canceled schedule cannot be updated."""
    cus, _, _price = setup_catalog(instance)
    body = call(
        instance,
        "POST",
        "/v1/subscription_schedules",
        {"customer": cus},
    )
    call(instance, "POST", f"/v1/subscription_schedules/{body['id']}/cancel")
    with pytest.raises(StripeToolError) as exc_info:
        call(
            instance,
            "POST",
            f"/v1/subscription_schedules/{body['id']}",
            {"end_behavior": "cancel"},
        )
    assert exc_info.value.status == 400
    assert "cannot update" in exc_info.value.stripe_body["error"]["message"].lower()


# --- cancel -----------------------------------------------------------------------


def test_cancel_not_started(instance: seahaven.Instance) -> None:
    """Canceling a not_started schedule transitions to canceled."""
    cus, _, _price = setup_catalog(instance)
    body = call(
        instance,
        "POST",
        "/v1/subscription_schedules",
        {"customer": cus},
    )
    assert body["status"] == "not_started"
    canceled = call(instance, "POST", f"/v1/subscription_schedules/{body['id']}/cancel")
    assert canceled["status"] == "canceled"
    assert canceled["canceled_at"] is not None


def test_cancel_active_also_cancels_subscription(instance: seahaven.Instance) -> None:
    """Canceling an active schedule cancels the linked subscription too."""
    cus, _, price = setup_catalog(instance)
    body = call(
        instance,
        "POST",
        "/v1/subscription_schedules",
        {"customer": cus, "phases": [{"items": [{"price": price}]}]},
    )
    assert body["status"] == "active"
    sub_id = body["subscription"]
    canceled = call(instance, "POST", f"/v1/subscription_schedules/{body['id']}/cancel")
    assert canceled["status"] == "canceled"
    # The subscription is canceled
    sub = call(instance, "GET", f"/v1/subscriptions/{sub_id}")
    assert sub["status"] == "canceled"
    # And unlinked from the schedule
    assert sub["schedule"] is None


def test_cancel_active_with_prorate_and_invoice_now(instance: seahaven.Instance) -> None:
    """Canceling with prorate=true and invoice_now=true passes through to the
    subscription cancel, minting a final proration invoice."""
    cus, _, price = setup_catalog(instance)
    body = call(
        instance,
        "POST",
        "/v1/subscription_schedules",
        {"customer": cus, "phases": [{"items": [{"price": price}]}]},
    )
    assert body["status"] == "active"
    sub_id = body["subscription"]
    canceled = call(
        instance,
        "POST",
        f"/v1/subscription_schedules/{body['id']}/cancel",
        {"invoice_now": True, "prorate": True},
    )
    assert canceled["status"] == "canceled"
    # The subscription is canceled with a final invoice
    sub = call(instance, "GET", f"/v1/subscriptions/{sub_id}")
    assert sub["status"] == "canceled"


def test_cancel_refuses_already_canceled(instance: seahaven.Instance) -> None:
    cus, _, _price = setup_catalog(instance)
    body = call(
        instance,
        "POST",
        "/v1/subscription_schedules",
        {"customer": cus},
    )
    call(instance, "POST", f"/v1/subscription_schedules/{body['id']}/cancel")
    with pytest.raises(StripeToolError) as exc_info:
        call(instance, "POST", f"/v1/subscription_schedules/{body['id']}/cancel")
    assert exc_info.value.status == 400
    assert "cannot cancel" in exc_info.value.stripe_body["error"]["message"].lower()


# --- release ----------------------------------------------------------------------


def test_release_active_schedule(instance: seahaven.Instance) -> None:
    """Releasing an active schedule frees the subscription from the schedule."""
    cus, _, price = setup_catalog(instance)
    body = call(
        instance,
        "POST",
        "/v1/subscription_schedules",
        {"customer": cus, "phases": [{"items": [{"price": price}]}]},
    )
    assert body["status"] == "active"
    sub_id = body["subscription"]
    released = call(instance, "POST", f"/v1/subscription_schedules/{body['id']}/release")
    assert released["status"] == "released"
    assert released["released_at"] is not None
    assert released["released_subscription"] == sub_id
    assert released["subscription"] is None
    # The subscription remains active and is unlinked
    sub = call(instance, "GET", f"/v1/subscriptions/{sub_id}")
    assert sub["status"] == "active"
    assert sub["schedule"] is None


def test_release_with_preserve_cancel_date(instance: seahaven.Instance) -> None:
    """Releasing with preserve_cancel_date=true keeps the subscription's
    cancel_at intact rather than clearing it."""
    cus, _, price = setup_catalog(instance)
    body = call(
        instance,
        "POST",
        "/v1/subscription_schedules",
        {
            "customer": cus,
            "phases": [{"items": [{"price": price}]}],
            "end_behavior": "cancel",
        },
    )
    sub_id = body["subscription"]
    # Set cancel_at_period_end on the subscription so the release can
    # preserve it
    call(
        instance,
        "POST",
        f"/v1/subscriptions/{sub_id}",
        {"cancel_at_period_end": True},
    )
    released = call(
        instance,
        "POST",
        f"/v1/subscription_schedules/{body['id']}/release",
        {"preserve_cancel_date": True},
    )
    assert released["status"] == "released"
    # The subscription keeps its cancel_at_period_end
    sub = call(instance, "GET", f"/v1/subscriptions/{sub_id}")
    assert sub["cancel_at_period_end"] is True


def test_release_without_preserve_cancel_date_clears_it(instance: seahaven.Instance) -> None:
    """Releasing without preserve_cancel_date clears the subscription's
    pending cancellation."""
    cus, _, price = setup_catalog(instance)
    body = call(
        instance,
        "POST",
        "/v1/subscription_schedules",
        {
            "customer": cus,
            "phases": [{"items": [{"price": price}]}],
        },
    )
    sub_id = body["subscription"]
    call(
        instance,
        "POST",
        f"/v1/subscriptions/{sub_id}",
        {"cancel_at_period_end": True},
    )
    released = call(
        instance,
        "POST",
        f"/v1/subscription_schedules/{body['id']}/release",
    )
    assert released["status"] == "released"
    sub = call(instance, "GET", f"/v1/subscriptions/{sub_id}")
    assert sub["cancel_at_period_end"] is False


def test_release_refuses_not_started(instance: seahaven.Instance) -> None:
    """Only an active schedule can be released."""
    cus, _, _price = setup_catalog(instance)
    body = call(
        instance,
        "POST",
        "/v1/subscription_schedules",
        {"customer": cus},
    )
    assert body["status"] == "not_started"
    with pytest.raises(StripeToolError) as exc_info:
        call(instance, "POST", f"/v1/subscription_schedules/{body['id']}/release")
    assert exc_info.value.status == 400
    assert "active" in exc_info.value.stripe_body["error"]["message"].lower()


# --- events -----------------------------------------------------------------------


def test_events_are_emitted(instance: seahaven.Instance) -> None:
    """Each mutating operation emits the corresponding event."""
    cus, _, price = setup_catalog(instance)
    body = call(
        instance,
        "POST",
        "/v1/subscription_schedules",
        {"customer": cus, "phases": [{"items": [{"price": price}]}]},
    )
    # update
    call(
        instance,
        "POST",
        f"/v1/subscription_schedules/{body['id']}",
        {"end_behavior": "cancel"},
    )
    # release
    call(instance, "POST", f"/v1/subscription_schedules/{body['id']}/release")
    # create another for cancel
    body2 = call(
        instance,
        "POST",
        "/v1/subscription_schedules",
        {"customer": cus},
    )
    call(instance, "POST", f"/v1/subscription_schedules/{body2['id']}/cancel")

    event_types = instance.inspect().rows(
        "SELECT type FROM events WHERE type LIKE 'subscription_schedule.%' ORDER BY x_seq"
    )
    types = [row["type"] for row in event_types]
    assert "subscription_schedule.created" in types
    assert "subscription_schedule.updated" in types
    assert "subscription_schedule.released" in types
    assert "subscription_schedule.canceled" in types


# --- unknown parameter ------------------------------------------------------------


def test_unknown_parameter_refused(instance: seahaven.Instance) -> None:
    cus, _, _price = setup_catalog(instance)
    with pytest.raises(StripeToolError) as exc_info:
        call(
            instance,
            "POST",
            "/v1/subscription_schedules",
            {"customer": cus, "bogus": "x"},
        )
    assert exc_info.value.stripe_body["error"]["code"] == "parameter_unknown"


# --- the idempotent create --------------------------------------------------------


def test_the_idempotent_create_replays(instance: seahaven.Instance) -> None:
    cus, _, price = setup_catalog(instance)
    params = {"customer": cus, "phases": [{"items": [{"price": price}]}]}
    first = instance.call(
        "stripe_api_write",
        method="POST",
        path="/v1/subscription_schedules",
        params=params,
        idempotency_key="sched-once",
    )
    second = instance.call(
        "stripe_api_write",
        method="POST",
        path="/v1/subscription_schedules",
        params=params,
        idempotency_key="sched-once",
    )
    assert second["id"] == first["id"]
    count = instance.inspect().one("SELECT count(*) AS n FROM subscription_schedules")
    assert count == {"n": 1}
