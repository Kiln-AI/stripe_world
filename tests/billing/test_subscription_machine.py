"""The eight-status machine, through the real four-tool chain and the
unrouted walkers. Every transition here is pinned by the Phase 12 probes
and cassette 12 at `2026-08-26.dahlia`; the decline-card creation branch
and the seti-confirm resume are unit-tested only (declared structural
differences — no attachable decline token exists, and live's minted seti
reads canceled within moments)."""

import pytest
import seahaven

from conftest import BLANK_NOW

pytestmark = pytest.mark.seahaven(fixture=None, now=BLANK_NOW)


def call(instance: seahaven.Instance, method: str, path: str, params: dict | None = None):
    if method == "GET":
        return instance.call("stripe_api_read", path=path, params=params)
    return instance.call("stripe_api_write", method=method, path=path, params=params)


def setup_catalog(instance: seahaven.Instance) -> tuple[str, str, str]:
    cus = call(instance, "POST", "/v1/customers", {"email": "sm@example.test"})["body"]["id"]
    pm = call(
        instance, "POST", "/v1/payment_methods", {"type": "card", "card": {"token": "tok_visa"}}
    )["body"]["id"]
    call(instance, "POST", f"/v1/payment_methods/{pm}/attach", {"customer": cus})
    call(
        instance,
        "POST",
        f"/v1/customers/{cus}",
        {"invoice_settings": {"default_payment_method": pm}},
    )
    prod = call(instance, "POST", "/v1/products", {"name": "machine"})["body"]["id"]
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
    )["body"]["id"]
    return cus, pm, price


def one_row(instance: seahaven.Instance, sql: str, *params):
    return instance.inspect().one(sql, *params)


def events_of(instance: seahaven.Instance) -> list[str]:
    return [
        row["type"] for row in instance.inspect().rows("SELECT type FROM events ORDER BY x_seq")
    ]


# --- creation branches -----------------------------------------------------------------


def test_create_active_with_paid_first_invoice(instance: seahaven.Instance) -> None:
    cus, _, price = setup_catalog(instance)
    body = call(
        instance, "POST", "/v1/subscriptions", {"customer": cus, "items": [{"price": price}]}
    )["body"]
    assert body["status"] == "active"
    assert body["collection_method"] == "charge_automatically"
    assert body["currency"] == "cad"
    assert body["billing_mode"] == {
        "type": "classic",
        "flexible": None,
        "updated_at": body["created"],
    }
    assert body["latest_invoice"].startswith("in_")
    invoice = one_row(instance, "SELECT * FROM invoices WHERE id = ?", body["latest_invoice"])
    assert invoice["status"] == "paid"
    assert invoice["billing_reason"] == "subscription_create"
    assert invoice["attempted"] == 1
    assert invoice["attempt_count"] == 1
    assert invoice["auto_advance"] == 0
    assert invoice["subtotal"] == 2000
    assert invoice["total"] == 2000
    assert invoice["amount_paid"] == 2000
    assert invoice["number"].endswith("-0001")
    # the item carries the period; the subscription does not
    assert "current_period_end" not in body
    item = body["items"]["data"][0]
    assert item["current_period_start"] == body["created"]
    assert item["current_period_end"] > item["current_period_start"]
    assert item["quantity"] == 1
    assert item["price"]["id"] == price
    # the subscription's own events, after the catalog's — the invoice
    # family now emits (Phase 13's serializer): created, finalized, the
    # charge, the payment pair, then the subscription's own creation last.
    assert events_of(instance)[-6:] == [
        "invoice.created",
        "invoice.finalized",
        "charge.succeeded",
        "invoice.payment_succeeded",
        "invoice.paid",
        "customer.subscription.created",
    ]


def test_create_trialing_carries_the_paid_zero_invoice(instance: seahaven.Instance) -> None:
    cus, _, price = setup_catalog(instance)
    body = call(
        instance,
        "POST",
        "/v1/subscriptions",
        {"customer": cus, "items": [{"price": price}], "trial_end": 1798761600},
    )["body"]
    assert body["status"] == "trialing"
    assert body["trial_start"] == body["created"]
    assert body["trial_end"] == 1798761600
    assert body["billing_cycle_anchor"] == 1798761600
    item = body["items"]["data"][0]
    assert item["current_period_start"] == body["created"]
    assert item["current_period_end"] == 1798761600
    # Recorded: the trial's first invoice is a paid $0 subscription_create
    invoice = one_row(instance, "SELECT * FROM invoices WHERE id = ?", body["latest_invoice"])
    assert invoice["status"] == "paid"
    assert invoice["total"] == 0
    assert invoice["billing_reason"] == "subscription_create"


def test_create_send_invoice_leaves_the_draft(instance: seahaven.Instance) -> None:
    cus, _, price = setup_catalog(instance)
    body = call(
        instance,
        "POST",
        "/v1/subscriptions",
        {
            "customer": cus,
            "items": [{"price": price}],
            "collection_method": "send_invoice",
            "days_until_due": 30,
        },
    )["body"]
    # Recorded: activation does not wait on the invoice, which stays draft
    assert body["status"] == "active"
    invoice = one_row(instance, "SELECT * FROM invoices WHERE id = ?", body["latest_invoice"])
    assert invoice["status"] == "draft"
    assert invoice["auto_advance"] == 1
    assert invoice["attempted"] == 0
    assert invoice["number"] is None
    assert invoice["due_date"] is not None


def test_create_incomplete_on_three_ds(instance: seahaven.Instance) -> None:
    cus, _pm, price = setup_catalog(instance)
    tds = call(
        instance,
        "POST",
        "/v1/payment_methods",
        {"type": "card", "card": {"token": "tok_threeDSecure2Required"}},
    )["body"]["id"]
    call(instance, "POST", f"/v1/payment_methods/{tds}/attach", {"customer": cus})
    body = call(
        instance,
        "POST",
        "/v1/subscriptions",
        {"customer": cus, "items": [{"price": price}], "default_payment_method": tds},
    )["body"]
    assert body["status"] == "incomplete"
    invoice = one_row(instance, "SELECT * FROM invoices WHERE id = ?", body["latest_invoice"])
    assert invoice["status"] == "open"
    # Recorded: the action gate sets attempted without counting an attempt
    assert invoice["attempted"] == 1
    assert invoice["attempt_count"] == 0


def test_error_if_incomplete_raises_and_writes_nothing(instance: seahaven.Instance) -> None:
    cus, _pm, price = setup_catalog(instance)
    tds = call(
        instance,
        "POST",
        "/v1/payment_methods",
        {"type": "card", "card": {"token": "tok_threeDSecure2Required"}},
    )["body"]["id"]
    call(instance, "POST", f"/v1/payment_methods/{tds}/attach", {"customer": cus})
    result = call(
        instance,
        "POST",
        "/v1/subscriptions",
        {
            "customer": cus,
            "items": [{"price": price}],
            "default_payment_method": tds,
            "payment_behavior": "error_if_incomplete",
        },
    )
    # Recorded: the 402 card_error with its long message
    assert result["status"] == 402
    error = result["body"]["error"]
    assert error["code"] == "subscription_payment_intent_requires_action"
    assert "requires additional user action" in error["message"]
    # nothing survives the refusal — asserted on the change log and the
    # rows alike (the catalog setup's events stand; no new ones fire)
    before = len(events_of(instance))
    assert one_row(instance, "SELECT count(*) AS n FROM subscriptions") == {"n": 0}
    assert len(events_of(instance)) == before


def test_create_without_a_payment_method_refuses(instance: seahaven.Instance) -> None:
    _cus, _, price = setup_catalog(instance)
    bare = call(instance, "POST", "/v1/customers", {"email": "bare@example.test"})["body"]["id"]
    result = call(
        instance, "POST", "/v1/subscriptions", {"customer": bare, "items": [{"price": price}]}
    )
    assert result["status"] == 400
    error = result["body"]["error"]
    assert error["code"] == "resource_missing"
    assert error["message"].startswith("This customer has no attached payment source")
    assert "param" not in error
    assert one_row(instance, "SELECT count(*) AS n FROM subscriptions") == {"n": 0}


def test_the_customer_default_is_never_copied_onto_the_row(instance: seahaven.Instance) -> None:
    cus, _pm, price = setup_catalog(instance)
    body = call(
        instance, "POST", "/v1/subscriptions", {"customer": cus, "items": [{"price": price}]}
    )["body"]
    # Recorded: the charge resolves through the customer's default, but the
    # subscription's own field stays null
    assert body["default_payment_method"] is None


# --- trial end --------------------------------------------------------------------------


def test_trial_end_now_with_a_method_becomes_active(instance: seahaven.Instance) -> None:
    cus, _, price = setup_catalog(instance)
    sub = call(
        instance,
        "POST",
        "/v1/subscriptions",
        {"customer": cus, "items": [{"price": price}], "trial_end": 1798761600},
    )["body"]
    body = call(instance, "POST", f"/v1/subscriptions/{sub['id']}", {"trial_end": "now"})["body"]
    assert body["status"] == "active"
    invoice = one_row(instance, "SELECT * FROM invoices WHERE id = ?", body["latest_invoice"])
    assert invoice["status"] == "paid"
    # Probed in both billing modes (round 4's trail): the update-driven
    # trial end bills `subscription_update`, its line spanning the NEW
    # period `[now, now + interval)`.
    assert invoice["billing_reason"] == "subscription_update"
    import json

    line = json.loads(invoice["lines"])[0]
    assert line["period"]["start"] == body["items"]["data"][0]["current_period_start"]
    assert line["period"]["end"] == body["items"]["data"][0]["current_period_end"]
    # the trialing -> active move emits its update event, with the old
    # status in previous_attributes
    last = one_row(
        instance,
        "SELECT data FROM events WHERE type = 'customer.subscription.updated'"
        " ORDER BY x_seq DESC LIMIT 1",
    )
    data = json.loads(last["data"])
    assert data["object"]["status"] == "active"
    assert data["previous_attributes"]["status"] == "trialing"


def test_trial_end_now_without_a_method_refuses(instance: seahaven.Instance) -> None:
    bare = call(instance, "POST", "/v1/customers", {"email": "bare@example.test"})["body"]["id"]
    _, _, price = setup_catalog(instance)
    sub = call(
        instance,
        "POST",
        "/v1/subscriptions",
        {
            "customer": bare,
            "items": [{"price": price}],
            "trial_end": 1798761600,
            # the default create_invoice behavior — recorded: it refuses
        },
    )["body"]
    result = call(instance, "POST", f"/v1/subscriptions/{sub['id']}", {"trial_end": "now"})
    assert result["status"] == 400
    assert result["body"]["error"]["code"] == "resource_missing"


def test_trial_end_now_pauses_without_a_method(instance: seahaven.Instance) -> None:
    _cus, _pm_unused, price = setup_catalog(instance)
    bare = call(instance, "POST", "/v1/customers", {"email": "bare@example.test"})["body"]["id"]
    sub = call(
        instance,
        "POST",
        "/v1/subscriptions",
        {
            "customer": bare,
            "items": [{"price": price}],
            "trial_end": 1798761600,
            "trial_settings": {"end_behavior": {"missing_payment_method": "pause"}},
        },
    )["body"]
    # the no-method trial mints the add-a-method SetupIntent immediately
    assert sub["pending_setup_intent"].startswith("seti_")
    body = call(instance, "POST", f"/v1/subscriptions/{sub['id']}", {"trial_end": "now"})["body"]
    assert body["status"] == "paused"
    assert body["pending_setup_intent"] == sub["pending_setup_intent"]
    # no cycle invoice exists while paused
    assert one_row(
        instance,
        "SELECT count(*) AS n FROM invoices WHERE billing_reason = 'subscription_cycle'",
    ) == {"n": 0}
    assert "customer.subscription.paused" in events_of(instance)


def test_trial_end_now_cancels_when_configured(instance: seahaven.Instance) -> None:
    _, _, price = setup_catalog(instance)
    bare = call(instance, "POST", "/v1/customers", {"email": "bare@example.test"})["body"]["id"]
    sub = call(
        instance,
        "POST",
        "/v1/subscriptions",
        {
            "customer": bare,
            "items": [{"price": price}],
            "trial_end": 1798761600,
            "trial_settings": {"end_behavior": {"missing_payment_method": "cancel"}},
        },
    )["body"]
    body = call(instance, "POST", f"/v1/subscriptions/{sub['id']}", {"trial_end": "now"})["body"]
    # Recorded: the cancel-behavior stamp, and the reason is
    # cancellation_requested — not payment_failed
    assert body["status"] == "canceled"
    assert body["canceled_at"] is not None
    assert body["ended_at"] is not None
    assert body["cancellation_details"]["reason"] == "cancellation_requested"
    assert "customer.subscription.deleted" in events_of(instance)


# --- pause and resume --------------------------------------------------------------------


def test_resume_parks_a_pending_update_and_the_confirm_applies_it(
    instance: seahaven.Instance,
) -> None:
    _, _, price = setup_catalog(instance)
    bare = call(instance, "POST", "/v1/customers", {"email": "bare@example.test"})["body"]["id"]
    sub = call(
        instance,
        "POST",
        "/v1/subscriptions",
        {
            "customer": bare,
            "items": [{"price": price}],
            "trial_end": 1798761600,
            "trial_settings": {"end_behavior": {"missing_payment_method": "pause"}},
        },
    )["body"]
    call(instance, "POST", f"/v1/subscriptions/{sub['id']}", {"trial_end": "now"})
    # Recorded: resume answers 200 with the status STILL paused, the seti
    # exposed and the update parked for 23 hours
    body = call(instance, "POST", f"/v1/subscriptions/{sub['id']}/resume", {})["body"]
    assert body["status"] == "paused"
    assert body["pending_setup_intent"].startswith("seti_")
    # the parked anchor is the default resume instant (the recording parks
    # null — the declared divergence this world's entry carries)
    assert body["pending_update"]["billing_cycle_anchor"] == body["created"]
    assert body["pending_update"]["expires_at"] > body["created"]
    invoice = one_row(instance, "SELECT * FROM invoices WHERE id = ?", body["latest_invoice"])
    assert invoice["status"] == "open"
    assert invoice["billing_reason"] == "subscription_cycle"
    # the confirm applies the parked update: the invoice pays, the status
    # moves, and both resume events fire (this world's mechanism — live's
    # seti reads canceled within moments; declared structural difference)
    pm = call(
        instance, "POST", "/v1/payment_methods", {"type": "card", "card": {"token": "tok_visa"}}
    )["body"]["id"]
    call(instance, "POST", f"/v1/payment_methods/{pm}/attach", {"customer": bare})
    confirmed = call(
        instance,
        "POST",
        f"/v1/setup_intents/{body['pending_setup_intent']}/confirm",
        {"payment_method": pm},
    )["body"]
    assert confirmed["status"] == "succeeded"
    fresh = call(instance, "GET", f"/v1/subscriptions/{sub['id']}")["body"]
    assert fresh["status"] == "active"
    assert fresh["pending_setup_intent"] is None
    assert fresh["pending_update"] is None
    invoice = one_row(instance, "SELECT * FROM invoices WHERE id = ?", fresh["latest_invoice"])
    assert invoice["status"] == "paid"
    types = events_of(instance)
    assert "customer.subscription.resumed" in types
    assert "customer.subscription.pending_update_applied" in types


def test_resume_unchanged_anchor_restarts_at_the_stored_anchor(instance: seahaven.Instance) -> None:
    """`billing_cycle_anchor=unchanged` parks the stored anchor, and the
    confirming SetupIntent restarts the periods there — not at the confirm
    moment (the frozen clock makes the two coincide only when they do)."""
    _, _, price = setup_catalog(instance)
    bare = call(instance, "POST", "/v1/customers", {"email": "bare2@example.test"})["body"]["id"]
    sub = call(
        instance,
        "POST",
        "/v1/subscriptions",
        {
            "customer": bare,
            "items": [{"price": price}],
            "trial_end": 1798761600,
            "trial_settings": {"end_behavior": {"missing_payment_method": "pause"}},
        },
    )["body"]
    stored_anchor = sub["billing_cycle_anchor"]
    call(instance, "POST", f"/v1/subscriptions/{sub['id']}", {"trial_end": "now"})
    body = call(
        instance,
        "POST",
        f"/v1/subscriptions/{sub['id']}/resume",
        {"billing_cycle_anchor": "unchanged"},
    )["body"]
    # the parked target is the stored anchor, not the resume moment
    assert body["pending_update"]["billing_cycle_anchor"] == stored_anchor
    pm = call(
        instance, "POST", "/v1/payment_methods", {"type": "card", "card": {"token": "tok_visa"}}
    )["body"]["id"]
    call(instance, "POST", f"/v1/payment_methods/{pm}/attach", {"customer": bare})
    call(
        instance,
        "POST",
        f"/v1/setup_intents/{body['pending_setup_intent']}/confirm",
        {"payment_method": pm},
    )
    fresh = call(instance, "GET", f"/v1/subscriptions/{sub['id']}")["body"]
    assert fresh["status"] == "active"
    assert fresh["billing_cycle_anchor"] == stored_anchor
    item = fresh["items"]["data"][0]
    assert item["current_period_start"] == stored_anchor


def test_resume_refuses_a_non_paused_subscription(instance: seahaven.Instance) -> None:
    cus, _, price = setup_catalog(instance)
    sub = call(
        instance, "POST", "/v1/subscriptions", {"customer": cus, "items": [{"price": price}]}
    )["body"]
    result = call(instance, "POST", f"/v1/subscriptions/{sub['id']}/resume", {})
    assert result["status"] == 400
    assert (
        result["body"]["error"]["message"]
        == "You can only resume a subscription if it is `paused`."
    )


# --- cancel ---------------------------------------------------------------------------------


def test_cancel_is_immediate_and_a_second_cancel_is_missing(instance: seahaven.Instance) -> None:
    cus, _, price = setup_catalog(instance)
    sub = call(
        instance, "POST", "/v1/subscriptions", {"customer": cus, "items": [{"price": price}]}
    )["body"]
    body = call(instance, "DELETE", f"/v1/subscriptions/{sub['id']}")["body"]
    assert body["object"] == "subscription"  # the full body, not a stub
    assert body["status"] == "canceled"
    assert body["canceled_at"] is not None
    assert body["ended_at"] is not None
    assert body["cancellation_details"]["reason"] == "cancellation_requested"
    assert "customer.subscription.deleted" in events_of(instance)
    again = call(instance, "DELETE", f"/v1/subscriptions/{sub['id']}")
    assert again["status"] == 404
    assert again["body"]["error"]["message"] == f"No such subscription: '{sub['id']}'"


def test_canceled_subscription_updates_follow_the_recorded_rule(
    instance: seahaven.Instance,
) -> None:
    cus, _, price = setup_catalog(instance)
    sub = call(
        instance, "POST", "/v1/subscriptions", {"customer": cus, "items": [{"price": price}]}
    )["body"]
    call(instance, "DELETE", f"/v1/subscriptions/{sub['id']}")
    body = call(instance, "POST", f"/v1/subscriptions/{sub['id']}", {"metadata": {"k": "v"}})[
        "body"
    ]
    assert body["metadata"] == {"k": "v"}
    for offending in ({"description": "x"}, {"cancel_at_period_end": True}):
        result = call(instance, "POST", f"/v1/subscriptions/{sub['id']}", offending)
        assert result["status"] == 400
        error = result["body"]["error"]
        assert error["code"] == "invalid_canceled_subscription_fields"
        assert error["message"] == (
            "A canceled subscription can only update its cancellation_details and metadata."
        )


def test_cancel_at_period_end_stamps_without_moving_the_status(instance: seahaven.Instance) -> None:
    cus, _, price = setup_catalog(instance)
    sub = call(
        instance, "POST", "/v1/subscriptions", {"customer": cus, "items": [{"price": price}]}
    )["body"]
    body = call(instance, "POST", f"/v1/subscriptions/{sub['id']}", {"cancel_at_period_end": True})[
        "body"
    ]
    assert body["status"] == "active"
    assert body["cancel_at_period_end"] is True
    assert body["cancel_at"] == body["items"]["data"][0]["current_period_end"]
    # Recorded: scheduling the cancel stamps the reason; un-scheduling clears it
    assert body["cancellation_details"]["reason"] == "cancellation_requested"
    body = call(
        instance, "POST", f"/v1/subscriptions/{sub['id']}", {"cancel_at_period_end": False}
    )["body"]
    assert body["cancel_at_period_end"] is False
    assert body["cancel_at"] is None
    assert body["cancellation_details"]["reason"] is None


def test_pause_collection_leaves_the_status_unchanged(instance: seahaven.Instance) -> None:
    cus, _, price = setup_catalog(instance)
    sub = call(
        instance, "POST", "/v1/subscriptions", {"customer": cus, "items": [{"price": price}]}
    )["body"]
    body = call(
        instance,
        "POST",
        f"/v1/subscriptions/{sub['id']}",
        {"pause_collection": {"behavior": "void"}},
    )["body"]
    assert body["status"] == "active"
    assert body["pause_collection"] == {"behavior": "void", "resumes_at": None}
    body = call(instance, "POST", f"/v1/subscriptions/{sub['id']}", {"pause_collection": ""})[
        "body"
    ]
    assert body["pause_collection"] is None


# --- the unrouted walkers --------------------------------------------------------------------


def subscription_row(instance: seahaven.Instance, id_: str):
    return one_row(instance, "SELECT * FROM subscriptions WHERE id = ?", id_)


def test_advance_cycle_renews_and_the_deferred_cancel_fires(instance: seahaven.Instance) -> None:
    import seahaven_stripe_world.billing.subscription_lifecycle as lifecycle

    cus, _, price = setup_catalog(instance)
    sub = call(
        instance, "POST", "/v1/subscriptions", {"customer": cus, "items": [{"price": price}]}
    )["body"]
    first_invoice = sub["latest_invoice"]
    item_before = one_row(
        instance, "SELECT * FROM subscription_items WHERE subscription = ?", sub["id"]
    )
    with instance.bulk() as ctx:
        lifecycle.advance_cycle(ctx, sub["id"])
    fresh = call(instance, "GET", f"/v1/subscriptions/{sub['id']}")["body"]
    assert fresh["status"] == "active"
    assert fresh["latest_invoice"] != first_invoice
    invoice = one_row(instance, "SELECT * FROM invoices WHERE id = ?", fresh["latest_invoice"])
    assert invoice["status"] == "paid"
    assert invoice["billing_reason"] == "subscription_cycle"
    # the renewal bills the interval that closed, unlike the create
    # invoice's recorded degenerate instant
    assert invoice["period_start"] == item_before["current_period_start"]
    assert invoice["period_end"] == item_before["current_period_end"]
    # the items rolled forward to the old boundary, one interval onward
    item_after = one_row(
        instance, "SELECT * FROM subscription_items WHERE subscription = ?", sub["id"]
    )
    assert item_after["current_period_start"] == item_before["current_period_end"]
    assert item_after["current_period_end"] > item_after["current_period_start"]
    # the deferred cancel: no renewal invoice at the next boundary
    call(instance, "POST", f"/v1/subscriptions/{sub['id']}", {"cancel_at_period_end": True})
    before = one_row(instance, "SELECT count(*) AS n FROM invoices")["n"]
    with instance.bulk() as ctx:
        lifecycle.advance_cycle(ctx, sub["id"])
    ended = subscription_row(instance, sub["id"])
    assert ended["status"] == "canceled"
    assert one_row(instance, "SELECT count(*) AS n FROM invoices")["n"] == before


def test_expire_incomplete_voids_and_is_terminal(instance: seahaven.Instance) -> None:
    import seahaven_stripe_world.billing.subscription_lifecycle as lifecycle

    cus, _pm, price = setup_catalog(instance)
    tds = call(
        instance,
        "POST",
        "/v1/payment_methods",
        {"type": "card", "card": {"token": "tok_threeDSecure2Required"}},
    )["body"]["id"]
    call(instance, "POST", f"/v1/payment_methods/{tds}/attach", {"customer": cus})
    sub = call(
        instance,
        "POST",
        "/v1/subscriptions",
        {"customer": cus, "items": [{"price": price}], "default_payment_method": tds},
    )["body"]
    with instance.bulk() as ctx:
        lifecycle.expire_incomplete(ctx, sub["id"])
    expired = subscription_row(instance, sub["id"])
    assert expired["status"] == "incomplete_expired"
    assert expired["ended_at"] is not None
    invoice = one_row(instance, "SELECT * FROM invoices WHERE id = ?", sub["latest_invoice"])
    assert invoice["status"] == "void"
    # terminal: update refuses
    result = call(instance, "POST", f"/v1/subscriptions/{sub['id']}", {"description": "x"})
    assert result["status"] == 400


def test_on_invoice_paid_recovers_incomplete(instance: seahaven.Instance) -> None:
    """The single recovery hook: paying the latest invoice moves
    incomplete to active with no subscriptions.update anywhere."""
    import seahaven_stripe_world.billing.invoicing as invoicing

    cus, pm, price = setup_catalog(instance)
    tds = call(
        instance,
        "POST",
        "/v1/payment_methods",
        {"type": "card", "card": {"token": "tok_threeDSecure2Required"}},
    )["body"]["id"]
    call(instance, "POST", f"/v1/payment_methods/{tds}/attach", {"customer": cus})
    sub = call(
        instance,
        "POST",
        "/v1/subscriptions",
        {"customer": cus, "items": [{"price": price}], "default_payment_method": tds},
    )["body"]
    assert sub["status"] == "incomplete"
    visa_row = one_row(instance, "SELECT * FROM payment_methods WHERE id = ?", pm)
    with instance.bulk() as ctx:
        outcome = invoicing.pay_invoice(ctx, sub["latest_invoice"], pm_row=visa_row)
    assert outcome["outcome"] == "paid"
    assert subscription_row(instance, sub["id"])["status"] == "active"
    assert "customer.subscription.updated" in events_of(instance)


def test_the_decline_card_lands_incomplete_with_its_attempt_counted(instance: seahaven.Instance):
    """The unrecordable branch (declared structural difference): a
    subscription whose first charge declines stays incomplete with the
    invoice open and the attempt counted."""
    # the world's payment-method create accepts the raw attachable PAN
    cus, _, price = setup_catalog(instance)
    dec = call(
        instance,
        "POST",
        "/v1/payment_methods",
        {
            "type": "card",
            "card": {"number": "4000000000000341", "exp_month": 9, "exp_year": 2027, "cvc": "123"},
        },
    )["body"]["id"]
    call(instance, "POST", f"/v1/payment_methods/{dec}/attach", {"customer": cus})
    call(
        instance,
        "POST",
        f"/v1/customers/{cus}",
        {"invoice_settings": {"default_payment_method": dec}},
    )
    body = call(
        instance, "POST", "/v1/subscriptions", {"customer": cus, "items": [{"price": price}]}
    )["body"]
    assert body["status"] == "incomplete"
    invoice = one_row(instance, "SELECT * FROM invoices WHERE id = ?", body["latest_invoice"])
    assert invoice["status"] == "open"
    assert invoice["attempted"] == 1
    assert invoice["attempt_count"] == 1
    charge = one_row(
        instance, "SELECT * FROM charges WHERE payment_intent IS NULL AND status = 'failed'"
    )
    assert charge is not None
    assert "invoice.payment_failed" in events_of(instance)  # Phase 13's event


def test_terminal_states_are_stamped(instance: seahaven.Instance) -> None:
    """I12 as SQL: canceled rows carry both stamps, nothing non-terminal
    carries an ended_at."""
    cus, _, price = setup_catalog(instance)
    sub = call(
        instance, "POST", "/v1/subscriptions", {"customer": cus, "items": [{"price": price}]}
    )["body"]
    call(instance, "DELETE", f"/v1/subscriptions/{sub['id']}")
    rows = instance.inspect().rows("SELECT * FROM subscriptions")
    assert rows, "the canceled row survives"
    for row in rows:
        if row["status"] == "canceled":
            assert row["canceled_at"] is not None and row["ended_at"] is not None
        else:
            assert row["ended_at"] is None


# --- the transition table ----------------------------------------------------------------


def test_transition_table_has_no_edges_out_of_terminal_states() -> None:
    """§Terminal-states as data: no row's source is `canceled` or
    `incomplete_expired`, and every row's target is a real status."""
    from seahaven_stripe_world.billing import subscription_lifecycle as lifecycle

    for row in lifecycle.TRANSITIONS:
        assert row.source not in lifecycle.TERMINAL_STATUSES, row
        assert row.target in (
            "active",
            "canceled",
            "incomplete",
            "incomplete_expired",
            "past_due",
            "paused",
            "trialing",
            "unpaid",
        ), row
        assert lifecycle.transition(row.source, row.trigger, row.guard) is not None


def test_transition_lookup_answers_none_for_unknown_rows() -> None:
    from seahaven_stripe_world.billing import subscription_lifecycle as lifecycle

    assert lifecycle.transition("canceled", "cycle_boundary", "") is None
    assert lifecycle.transition("active", "bogus_trigger", "") is None


# --- the round-3 corrections ----------------------------------------------------------------


def test_trial_end_future_on_trialing_emits_its_update(instance: seahaven.Instance) -> None:
    cus, _, price = setup_catalog(instance)
    sub = call(
        instance,
        "POST",
        "/v1/subscriptions",
        {"customer": cus, "items": [{"price": price}], "trial_end": 1798761600},
    )["body"]
    result = call(instance, "POST", f"/v1/subscriptions/{sub['id']}", {"trial_end": 1801353600})
    assert result["status"] == 200
    import json

    last = one_row(
        instance,
        "SELECT data FROM events WHERE type = 'customer.subscription.updated'"
        " ORDER BY x_seq DESC LIMIT 1",
    )
    data = json.loads(last["data"])
    assert data["object"]["trial_end"] == 1801353600
    assert data["previous_attributes"]["trial_end"] == 1798761600


def test_incomplete_accepts_default_payment_method_updates(instance: seahaven.Instance) -> None:
    """Probed live on an incomplete (3DS) subscription, Phase 12 CR round 2:
    the field accepts a set and the empty-string clear — the rescue move."""
    cus, pm, price = setup_catalog(instance)
    tds = call(
        instance,
        "POST",
        "/v1/payment_methods",
        {"type": "card", "card": {"token": "tok_threeDSecure2Required"}},
    )["body"]["id"]
    call(instance, "POST", f"/v1/payment_methods/{tds}/attach", {"customer": cus})
    sub = call(
        instance,
        "POST",
        "/v1/subscriptions",
        {"customer": cus, "items": [{"price": price}], "default_payment_method": tds},
    )["body"]
    assert sub["status"] == "incomplete"
    body = call(instance, "POST", f"/v1/subscriptions/{sub['id']}", {"default_payment_method": pm})[
        "body"
    ]
    assert body["default_payment_method"] == pm
    body = call(instance, "POST", f"/v1/subscriptions/{sub['id']}", {"default_payment_method": ""})[
        "body"
    ]
    assert body["default_payment_method"] is None
    # a wider change still refuses
    result = call(
        instance, "POST", f"/v1/subscriptions/{sub['id']}", {"cancel_at_period_end": True}
    )
    assert result["status"] == 400


def test_trial_end_now_keeps_cosupplied_field_deltas(instance: seahaven.Instance) -> None:
    cus, _, price = setup_catalog(instance)
    sub = call(
        instance,
        "POST",
        "/v1/subscriptions",
        {"customer": cus, "items": [{"price": price}], "trial_end": 1798761600},
    )["body"]
    call(
        instance,
        "POST",
        f"/v1/subscriptions/{sub['id']}",
        {"metadata": {"k": "v"}, "trial_end": "now"},
    )
    import json

    last = one_row(
        instance,
        "SELECT data FROM events WHERE type = 'customer.subscription.updated'"
        " ORDER BY x_seq DESC LIMIT 1",
    )
    previous = json.loads(last["data"])["previous_attributes"]
    # the co-supplied metadata's delta survived: the per-key diff shows the
    # newly-set key (the engine's `{"k": null}` convention)
    assert previous["metadata"] == {"k": None}


def test_resume_and_item_writes_carry_previous_attributes(instance: seahaven.Instance) -> None:
    _, _, price = setup_catalog(instance)
    bare = call(instance, "POST", "/v1/customers", {"email": "pa@example.test"})["body"]["id"]
    sub = call(
        instance,
        "POST",
        "/v1/subscriptions",
        {
            "customer": bare,
            "items": [{"price": price}],
            "trial_end": 1798761600,
            "trial_settings": {"end_behavior": {"missing_payment_method": "pause"}},
        },
    )["body"]
    call(instance, "POST", f"/v1/subscriptions/{sub['id']}", {"trial_end": "now"})
    resumed = call(instance, "POST", f"/v1/subscriptions/{sub['id']}/resume", {})["body"]
    import json

    last = one_row(
        instance,
        "SELECT data FROM events WHERE type = 'customer.subscription.updated'"
        " ORDER BY x_seq DESC LIMIT 1",
    )
    data = json.loads(last["data"])
    # the resume's own emission: parked fields in previous_attributes
    assert data["previous_attributes"]["pending_update"] is None
    assert data["object"]["pending_setup_intent"] == resumed["pending_setup_intent"]

    # an item change through the sub-update path: the added item's absence
    # in the previous items
    prod = call(instance, "POST", "/v1/products", {"name": "pa2"})["body"]["id"]
    price2 = call(
        instance,
        "POST",
        "/v1/prices",
        {
            "product": prod,
            "unit_amount": 900,
            "currency": "cad",
            "recurring": {"interval": "month"},
        },
    )["body"]["id"]
    call(instance, "POST", f"/v1/subscriptions/{sub['id']}", {"items": [{"price": price2}]})
    last = one_row(
        instance,
        "SELECT data FROM events WHERE type = 'customer.subscription.updated'"
        " ORDER BY x_seq DESC LIMIT 1",
    )
    previous = json.loads(last["data"])["previous_attributes"]
    assert [item["price"]["id"] for item in previous["items"]["data"]] == [price]


def test_advance_cycle_refuses_non_renewing_statuses(instance: seahaven.Instance) -> None:
    import pytest as _pytest
    import seahaven

    import seahaven_stripe_world.billing.subscription_lifecycle as lifecycle

    _, _, price = setup_catalog(instance)
    sub = call(
        instance,
        "POST",
        "/v1/subscriptions",
        {"customer": cus_of(instance), "items": [{"price": price}]},
    )["body"]
    call(instance, "DELETE", f"/v1/subscriptions/{sub['id']}")
    with instance.bulk() as ctx, _pytest.raises(seahaven.WorldBug):
        lifecycle.advance_cycle(ctx, sub["id"])


def cus_of(instance: seahaven.Instance) -> str:
    cus = call(instance, "POST", "/v1/customers", {"email": "ac@example.test"})["body"]["id"]
    pm = call(
        instance, "POST", "/v1/payment_methods", {"type": "card", "card": {"token": "tok_visa"}}
    )["body"]["id"]
    call(instance, "POST", f"/v1/payment_methods/{pm}/attach", {"customer": cus})
    call(
        instance,
        "POST",
        f"/v1/customers/{cus}",
        {"invoice_settings": {"default_payment_method": pm}},
    )
    return cus


# --- the round-4 corrections ----------------------------------------------------------------


def test_create_with_trial_end_now_collapses_to_no_trial(instance: seahaven.Instance) -> None:
    """Probed (round 4): a zero-length trial is no trial — 200, active,
    null stamps, anchor = created, item period `[now, +1 interval)`."""
    cus, _, price = setup_catalog(instance)
    body = call(
        instance,
        "POST",
        "/v1/subscriptions",
        {"customer": cus, "items": [{"price": price}], "trial_end": "now"},
    )["body"]
    assert body["status"] == "active"
    assert body["trial_start"] is None
    assert body["trial_end"] is None
    assert body["billing_cycle_anchor"] == body["created"]
    item = body["items"]["data"][0]
    assert item["current_period_start"] == body["created"]
    assert item["current_period_end"] > item["current_period_start"]


def test_future_trial_end_converts_an_active_subscription(instance: seahaven.Instance) -> None:
    """Probed (round 4, uncommitted probe trail): status flips to trialing,
    trial_start = now, item period rebuilt to `[now, trial_end)`, anchor
    moved to the trial end, and one paid `subscription_update` invoice is
    minted — a full-price `Unused time on …` credit plus the $0 trial line,
    attempt_count 0."""
    cus, _, price = setup_catalog(instance)
    sub = call(
        instance, "POST", "/v1/subscriptions", {"customer": cus, "items": [{"price": price}]}
    )["body"]
    first_invoice = sub["latest_invoice"]
    body = call(instance, "POST", f"/v1/subscriptions/{sub['id']}", {"trial_end": 1798761600})[
        "body"
    ]
    assert body["status"] == "trialing"
    assert body["trial_start"] == body["created"]
    assert body["trial_end"] == 1798761600
    assert body["billing_cycle_anchor"] == 1798761600
    item = body["items"]["data"][0]
    assert item["current_period_start"] == body["created"]
    assert item["current_period_end"] == 1798761600
    assert body["latest_invoice"] != first_invoice
    invoice = one_row(instance, "SELECT * FROM invoices WHERE id = ?", body["latest_invoice"])
    assert invoice["status"] == "paid"
    assert invoice["billing_reason"] == "subscription_update"
    assert invoice["subtotal"] == -2000
    assert invoice["total"] == -2000
    assert invoice["attempted"] == 1
    assert invoice["attempt_count"] == 0
    import json

    lines = json.loads(invoice["lines"])
    descriptions = sorted(line["description"] for line in lines)
    assert any(d.startswith("Unused time on machine after ") for d in descriptions)
    assert any(d.startswith("Free trial for 1 ") for d in descriptions)
    # the abandoned period's remainder is credited as customer balance
    customer = one_row(instance, "SELECT * FROM customers WHERE id = ?", cus)
    assert customer["balance"] == -2000


def test_renewals_apply_the_persisted_discounts_and_tax(instance: seahaven.Instance) -> None:
    """A `forever` coupon and the default tax rates bill on every invoice,
    and the invoice's own `discounts` column carries what its totals
    applied."""
    import seahaven_stripe_world.billing.subscription_lifecycle as lifecycle

    cus, _, price = setup_catalog(instance)
    coupon = call(instance, "POST", "/v1/coupons", {"percent_off": 10, "duration": "forever"})[
        "body"
    ]["id"]
    txr = call(
        instance,
        "POST",
        "/v1/tax_rates",
        {"display_name": "GST", "percentage": 5, "jurisdiction": "CA", "inclusive": False},
    )["body"]["id"]
    sub = call(
        instance,
        "POST",
        "/v1/subscriptions",
        {
            "customer": cus,
            "items": [{"price": price}],
            "discounts": [{"coupon": coupon}],
            "default_tax_rates": [txr],
        },
    )["body"]
    assert sub["discounts"]  # forever persists on the row
    first = one_row(instance, "SELECT * FROM invoices WHERE id = ?", sub["latest_invoice"])
    assert first["total"] == 1890  # 2000 - 200 + 90
    with instance.bulk() as ctx:
        lifecycle.advance_cycle(ctx, sub["id"])
    fresh = call(instance, "GET", f"/v1/subscriptions/{sub['id']}")["body"]
    renewal = one_row(instance, "SELECT * FROM invoices WHERE id = ?", fresh["latest_invoice"])
    assert renewal["billing_reason"] == "subscription_cycle"
    assert renewal["total"] == 1890  # the persisted state bills again
    import json

    assert json.loads(renewal["total_taxes"])[0]["amount"] == 90
    assert [d["id"] for d in json.loads(first["discounts"])] == sub["discounts"]
    assert json.loads(renewal["discounts"])


def test_the_incomplete_refusal_names_the_real_allow_set(instance: seahaven.Instance) -> None:
    cus, _pm, price = setup_catalog(instance)
    tds = call(
        instance,
        "POST",
        "/v1/payment_methods",
        {"type": "card", "card": {"token": "tok_threeDSecure2Required"}},
    )["body"]["id"]
    call(instance, "POST", f"/v1/payment_methods/{tds}/attach", {"customer": cus})
    sub = call(
        instance,
        "POST",
        "/v1/subscriptions",
        {"customer": cus, "items": [{"price": price}], "default_payment_method": tds},
    )["body"]
    result = call(
        instance,
        "POST",
        f"/v1/subscriptions/{sub['id']}",
        {"cancel_at_period_end": True},
    )
    error = result["body"]["error"]
    assert error["code"] == "status_transition_invalid"
    assert error["message"] == (
        "Cannot update cancel_at_period_end on an incomplete subscription. Only "
        "metadata, description, default_source and default_payment_method can be updated."
    )


def test_recovery_events_carry_previous_attributes(instance: seahaven.Instance) -> None:
    """on_invoice_paid and expire_incomplete emit `previous_attributes`
    like every other subscription writer."""
    import json

    import seahaven_stripe_world.billing.invoicing as invoicing
    import seahaven_stripe_world.billing.subscription_lifecycle as lifecycle

    cus, pm, price = setup_catalog(instance)
    tds = call(
        instance,
        "POST",
        "/v1/payment_methods",
        {"type": "card", "card": {"token": "tok_threeDSecure2Required"}},
    )["body"]["id"]
    call(instance, "POST", f"/v1/payment_methods/{tds}/attach", {"customer": cus})
    sub = call(
        instance,
        "POST",
        "/v1/subscriptions",
        {"customer": cus, "items": [{"price": price}], "default_payment_method": tds},
    )["body"]
    visa_row = one_row(instance, "SELECT * FROM payment_methods WHERE id = ?", pm)
    with instance.bulk() as ctx:
        invoicing.pay_invoice(ctx, sub["latest_invoice"], pm_row=visa_row)
    last = one_row(
        instance,
        "SELECT data FROM events WHERE type = 'customer.subscription.updated'"
        " ORDER BY x_seq DESC LIMIT 1",
    )
    data = json.loads(last["data"])
    assert data["previous_attributes"]["status"] == "incomplete"
    assert data["object"]["status"] == "active"

    # expire_incomplete on a fresh incomplete row
    sub2 = call(
        instance,
        "POST",
        "/v1/subscriptions",
        {"customer": cus, "items": [{"price": price}], "default_payment_method": tds},
    )["body"]
    with instance.bulk() as ctx:
        lifecycle.expire_incomplete(ctx, sub2["id"])
    last = one_row(
        instance,
        "SELECT data FROM events WHERE type = 'customer.subscription.updated'"
        " ORDER BY x_seq DESC LIMIT 1",
    )
    data = json.loads(last["data"])
    assert data["previous_attributes"]["status"] == "incomplete"
    assert data["object"]["status"] == "incomplete_expired"


# --- the round-5 corrections ----------------------------------------------------------------


def test_once_coupon_parks_on_a_trial_and_consumes_at_trial_end(
    instance: seahaven.Instance,
) -> None:
    """Probed (round 4's trail, coupon sjNdOctl): a once-coupon persists on
    the trialing row, applies to the first PAID invoice at trial end, and
    the row clears to [] after."""
    cus, _, price = setup_catalog(instance)
    coupon = call(instance, "POST", "/v1/coupons", {"percent_off": 10, "duration": "once"})["body"][
        "id"
    ]
    sub = call(
        instance,
        "POST",
        "/v1/subscriptions",
        {
            "customer": cus,
            "items": [{"price": price}],
            "trial_end": 1798761600,
            "discounts": [{"coupon": coupon}],
        },
    )["body"]
    assert sub["status"] == "trialing"
    assert len(sub["discounts"]) == 1  # parked, not dropped
    trial_invoice = one_row(instance, "SELECT * FROM invoices WHERE id = ?", sub["latest_invoice"])
    assert trial_invoice["total"] == 0  # the $0 trial invoice bills nothing
    body = call(instance, "POST", f"/v1/subscriptions/{sub['id']}", {"trial_end": "now"})["body"]
    assert body["status"] == "active"
    invoice = one_row(instance, "SELECT * FROM invoices WHERE id = ?", body["latest_invoice"])
    assert invoice["subtotal"] == 2000
    assert invoice["total"] == 1800  # 10% off the first paid invoice
    import json

    line = json.loads(invoice["lines"])[0]
    assert line["discount_amounts"] == [{"amount": 200, "discount": sub["discounts"][0]}]
    applied = json.loads(invoice["discounts"])
    assert [d["id"] for d in applied] == sub["discounts"]
    assert all(d["object"] == "discount" for d in applied)
    # and the row cleared
    fresh = call(instance, "GET", f"/v1/subscriptions/{sub['id']}")["body"]
    assert fresh["discounts"] == []


def test_the_conversion_credit_line_carries_the_probed_flags(instance: seahaven.Instance) -> None:
    """Round 4's trail (in_1UHyhhHIBYZYyePrmHqO3AOg and its flexible-mode
    sibling): the credit is proration-shaped — `discountable: false`,
    `proration: true`, no `unit_amount_decimal` — while the trial line
    stays a plain line. The ii_/credited_items back-links are the declared
    Phase 13/14 deferral."""
    import json

    cus, _, price = setup_catalog(instance)
    sub = call(
        instance, "POST", "/v1/subscriptions", {"customer": cus, "items": [{"price": price}]}
    )["body"]
    body = call(instance, "POST", f"/v1/subscriptions/{sub['id']}", {"trial_end": 1798761600})[
        "body"
    ]
    invoice = one_row(instance, "SELECT * FROM invoices WHERE id = ?", body["latest_invoice"])
    lines = json.loads(invoice["lines"])
    credit = next(line for line in lines if line["description"].startswith("Unused time on"))
    trial = next(line for line in lines if line["description"].startswith("Free trial for"))
    assert credit["discountable"] is False
    assert credit["parent"]["subscription_item_details"]["proration"] is True
    assert credit["pricing"]["unit_amount_decimal"] is None
    assert credit["amount"] == -2000
    assert trial["discountable"] is True
    assert trial["parent"]["subscription_item_details"]["proration"] is False
    assert trial["pricing"]["unit_amount_decimal"] == "0"


def test_conversion_refuses_unprobed_source_statuses(instance: seahaven.Instance) -> None:
    """Only `active` (the probed source) converts; paused refuses."""
    _, _, price = setup_catalog(instance)
    bare = call(instance, "POST", "/v1/customers", {"email": "guard@example.test"})["body"]["id"]
    sub = call(
        instance,
        "POST",
        "/v1/subscriptions",
        {
            "customer": bare,
            "items": [{"price": price}],
            "trial_end": 1798761600,
            "trial_settings": {"end_behavior": {"missing_payment_method": "pause"}},
        },
    )["body"]
    call(instance, "POST", f"/v1/subscriptions/{sub['id']}", {"trial_end": "now"})
    paused = call(instance, "GET", f"/v1/subscriptions/{sub['id']}")["body"]
    assert paused["status"] == "paused"
    result = call(instance, "POST", f"/v1/subscriptions/{sub['id']}", {"trial_end": 1798761600})
    assert result["status"] == 400
    error = result["body"]["error"]
    # Probed (round 5): the paused refusal carries no code and no param
    assert "code" not in error
    assert "param" not in error
    assert error["message"] == (
        "You cannot set `trial_end` while a subscription is `paused`. Resume the "
        "subscription first before setting `trial_end`."
    )


# --- the round-6 corrections ----------------------------------------------------------------


def test_once_coupon_set_via_update_parks_and_consumes(instance: seahaven.Instance) -> None:
    """Probed (round 5): an update parks the discount — once-coupons
    included, on both active and trialing rows — and the next invoice that
    pays consumes it."""
    cus, _, price = setup_catalog(instance)
    coupon = call(instance, "POST", "/v1/coupons", {"percent_off": 10, "duration": "once"})["body"][
        "id"
    ]
    # the trialing row: parked via update, billed at trial end, cleared
    sub = call(
        instance,
        "POST",
        "/v1/subscriptions",
        {"customer": cus, "items": [{"price": price}], "trial_end": 1798761600},
    )["body"]
    body = call(
        instance, "POST", f"/v1/subscriptions/{sub['id']}", {"discounts": [{"coupon": coupon}]}
    )["body"]
    assert len(body["discounts"]) == 1
    body = call(instance, "POST", f"/v1/subscriptions/{sub['id']}", {"trial_end": "now"})["body"]
    invoice = one_row(instance, "SELECT * FROM invoices WHERE id = ?", body["latest_invoice"])
    assert invoice["total"] == 1800
    assert call(instance, "GET", f"/v1/subscriptions/{sub['id']}")["body"]["discounts"] == []
    # the active row: parked via update, consumed by the paid renewal
    sub2 = call(
        instance, "POST", "/v1/subscriptions", {"customer": cus, "items": [{"price": price}]}
    )["body"]
    body = call(
        instance, "POST", f"/v1/subscriptions/{sub2['id']}", {"discounts": [{"coupon": coupon}]}
    )["body"]
    assert len(body["discounts"]) == 1
    import seahaven_stripe_world.billing.subscription_lifecycle as lifecycle

    with instance.bulk() as ctx:
        lifecycle.advance_cycle(ctx, sub2["id"])
    renewal = one_row(
        instance,
        "SELECT * FROM invoices WHERE id = ?",
        call(instance, "GET", f"/v1/subscriptions/{sub2['id']}")["body"]["latest_invoice"],
    )
    assert renewal["billing_reason"] == "subscription_cycle"
    assert renewal["total"] == 1800  # parked, then consumed — not forever
    assert call(instance, "GET", f"/v1/subscriptions/{sub2['id']}")["body"]["discounts"] == []


def test_send_invoice_trial_end_leaves_the_draft(instance: seahaven.Instance) -> None:
    """Probed (round 5): a send_invoice trial end answers 200 active with
    the invoice left draft and no charge — with or without a PM."""
    cus, _pm, price = setup_catalog(instance)
    sub = call(
        instance,
        "POST",
        "/v1/subscriptions",
        {
            "customer": cus,
            "items": [{"price": price}],
            "trial_end": 1798761600,
            "collection_method": "send_invoice",
            "days_until_due": 30,
        },
    )["body"]
    assert sub["status"] == "trialing"
    body = call(instance, "POST", f"/v1/subscriptions/{sub['id']}", {"trial_end": "now"})["body"]
    assert body["status"] == "active"
    invoice = one_row(instance, "SELECT * FROM invoices WHERE id = ?", body["latest_invoice"])
    assert invoice["status"] == "draft"
    assert invoice["auto_advance"] == 1
    assert invoice["attempted"] == 0
    assert invoice["attempt_count"] == 0
    assert invoice["due_date"] is not None
    # no charge was made despite the customer's default PM
    charges = one_row(instance, "SELECT count(*) AS n FROM charges WHERE payment_intent IS NULL")
    assert charges["n"] == 0


def test_send_invoice_trial_end_without_a_pm_answers_active(instance: seahaven.Instance) -> None:
    bare = call(instance, "POST", "/v1/customers", {"email": "sib@example.test"})["body"]["id"]
    _cus, _pm, price = setup_catalog(instance)
    sub = call(
        instance,
        "POST",
        "/v1/subscriptions",
        {
            "customer": bare,
            "items": [{"price": price}],
            "trial_end": 1798761600,
            "collection_method": "send_invoice",
            "days_until_due": 30,
        },
    )["body"]
    result = call(instance, "POST", f"/v1/subscriptions/{sub['id']}", {"trial_end": "now"})
    assert result["status"] == 200
    body = result["body"]
    assert body["status"] == "active"
    invoice = one_row(instance, "SELECT * FROM invoices WHERE id = ?", body["latest_invoice"])
    assert invoice["status"] == "draft"


def test_conversion_applies_the_rows_coupon_and_tax(instance: seahaven.Instance) -> None:
    """Probed (round 5, forever 10% + 5% GST): the coupon nets into the
    credit (-1800 with the `(with 10.0% off)` suffix), the discount shows
    as amount-0 entries, tax computes on the negative base (-90), total
    -1890."""
    import json

    cus, _, price = setup_catalog(instance)
    coupon = call(instance, "POST", "/v1/coupons", {"percent_off": 10, "duration": "forever"})[
        "body"
    ]["id"]
    txr = call(
        instance,
        "POST",
        "/v1/tax_rates",
        {"display_name": "GST", "percentage": 5, "jurisdiction": "CA", "inclusive": False},
    )["body"]["id"]
    sub = call(
        instance,
        "POST",
        "/v1/subscriptions",
        {
            "customer": cus,
            "items": [{"price": price}],
            "discounts": [{"coupon": coupon}],
            "default_tax_rates": [txr],
        },
    )["body"]
    body = call(instance, "POST", f"/v1/subscriptions/{sub['id']}", {"trial_end": 1798761600})[
        "body"
    ]
    invoice = one_row(instance, "SELECT * FROM invoices WHERE id = ?", body["latest_invoice"])
    assert invoice["subtotal"] == -1800
    assert invoice["total"] == -1890
    credit = next(
        line for line in json.loads(invoice["lines"]) if line["description"].startswith("Unused")
    )
    assert credit["description"].startswith("Unused time on machine (with 10.0% off) after ")
    assert credit["amount"] == -1800
    assert credit["discount_amounts"] == [{"amount": 0, "discount": sub["discounts"][0]}]
    tax = json.loads(invoice["total_taxes"])[0]
    assert tax["amount"] == -90
    assert tax["taxable_amount"] == -1800
    # a forever coupon survives the conversion
    assert call(instance, "GET", f"/v1/subscriptions/{sub['id']}")["body"]["discounts"] == [
        sub["discounts"][0]
    ]


def test_advance_cycles_trial_at_the_boundary(instance: seahaven.Instance) -> None:
    import seahaven_stripe_world.billing.subscription_lifecycle as lifecycle

    cus, _, price = setup_catalog(instance)
    sub = call(
        instance,
        "POST",
        "/v1/subscriptions",
        {"customer": cus, "items": [{"price": price}], "trial_end": 1798761600},
    )["body"]
    with instance.bulk() as ctx:
        lifecycle.advance_cycle(ctx, sub["id"])
    fresh = call(instance, "GET", f"/v1/subscriptions/{sub['id']}")["body"]
    assert fresh["status"] == "active"
    item = fresh["items"]["data"][0]
    # the boundary is the trial end: the new period starts there, one
    # interval onward — not at the frozen creation instant
    assert item["current_period_start"] == 1798761600
    invoice = one_row(instance, "SELECT * FROM invoices WHERE id = ?", fresh["latest_invoice"])
    assert invoice["billing_reason"] == "subscription_cycle"


# --- the round-7 corrections ----------------------------------------------------------------


def test_keep_as_draft_pause_leaves_the_renewal_in_draft(instance: seahaven.Instance) -> None:
    """billing_engine §1: a keep_as_draft pause takes the cycle invoice to
    `draft` (kept) — never finalized, never attempted, no charge."""
    import seahaven_stripe_world.billing.subscription_lifecycle as lifecycle

    cus, _, price = setup_catalog(instance)
    sub = call(
        instance, "POST", "/v1/subscriptions", {"customer": cus, "items": [{"price": price}]}
    )["body"]
    call(
        instance,
        "POST",
        f"/v1/subscriptions/{sub['id']}",
        {"pause_collection": {"behavior": "keep_as_draft"}},
    )
    with instance.bulk() as ctx:
        lifecycle.advance_cycle(ctx, sub["id"])
    fresh = call(instance, "GET", f"/v1/subscriptions/{sub['id']}")["body"]
    invoice = one_row(instance, "SELECT * FROM invoices WHERE id = ?", fresh["latest_invoice"])
    assert invoice["status"] == "draft"
    assert invoice["auto_advance"] == 0  # the pause owns this invoice now
    assert invoice["attempted"] == 0
    assert invoice["attempt_count"] == 0
    assert one_row(instance, "SELECT count(*) AS n FROM charges WHERE payment_intent IS NULL") == {
        "n": 1
    }  # only the create invoice's charge
    assert fresh["status"] == "active"  # pause_collection never moves the status


def test_send_invoice_trial_end_bills_the_new_period(instance: seahaven.Instance) -> None:
    """Roll-then-invoice, exactly as the PM branch: the draft's line spans
    the NEW period, never the trial span the $0 invoice already covered."""
    import json

    cus, _pm, price = setup_catalog(instance)
    sub = call(
        instance,
        "POST",
        "/v1/subscriptions",
        {
            "customer": cus,
            "items": [{"price": price}],
            "trial_end": 1798761600,
            "collection_method": "send_invoice",
            "days_until_due": 30,
        },
    )["body"]
    body = call(instance, "POST", f"/v1/subscriptions/{sub['id']}", {"trial_end": "now"})["body"]
    invoice = one_row(instance, "SELECT * FROM invoices WHERE id = ?", body["latest_invoice"])
    line = json.loads(invoice["lines"])[0]
    item = body["items"]["data"][0]
    assert line["period"]["start"] == item["current_period_start"]
    assert line["period"]["end"] == item["current_period_end"]
    assert (
        line["period"]["end"] - line["period"]["start"] == 30 * 86_400
    )  # one month, not the trial
    # the period columns are ISO in storage; unix on the wire
    from seahaven_stripe_world import _time

    assert _time.to_unix(invoice["period_start"]) == item["current_period_start"]
    assert _time.to_unix(invoice["period_end"]) == item["current_period_end"]


def test_resume_cycle_invoice_bills_the_parked_anchor_period(instance: seahaven.Instance) -> None:
    """Unprobed on live (declared in the code): the resume's open cycle
    invoice bills `[anchor, anchor + interval)` — not the paused trial span
    — with non-degenerate period columns."""
    import json

    _, _, price = setup_catalog(instance)
    bare = call(instance, "POST", "/v1/customers", {"email": "r7@example.test"})["body"]["id"]
    sub = call(
        instance,
        "POST",
        "/v1/subscriptions",
        {
            "customer": bare,
            "items": [{"price": price}],
            "trial_end": 1798761600,
            "trial_settings": {"end_behavior": {"missing_payment_method": "pause"}},
        },
    )["body"]
    call(instance, "POST", f"/v1/subscriptions/{sub['id']}", {"trial_end": "now"})
    body = call(instance, "POST", f"/v1/subscriptions/{sub['id']}/resume", {})["body"]
    invoice = one_row(instance, "SELECT * FROM invoices WHERE id = ?", body["latest_invoice"])
    assert invoice["status"] == "open"
    line = json.loads(invoice["lines"])[0]
    assert line["period"]["start"] == body["created"]  # the parked `now` anchor
    assert line["period"]["end"] - line["period"]["start"] == 30 * 86_400
    from seahaven_stripe_world import _time

    assert _time.to_unix(invoice["period_start"]) == line["period"]["start"]
    assert _time.to_unix(invoice["period_end"]) == line["period"]["end"]


def test_a_deleted_coupons_parked_discount_keeps_billing(instance: seahaven.Instance) -> None:
    """Stripe documents that deleting a coupon does not affect discounts
    already applied: the parked discount bills on, and never 400s a call
    that never passed `discounts`."""
    cus, _, price = setup_catalog(instance)
    coupon = call(instance, "POST", "/v1/coupons", {"percent_off": 10, "duration": "forever"})[
        "body"
    ]["id"]
    sub = call(
        instance,
        "POST",
        "/v1/subscriptions",
        {"customer": cus, "items": [{"price": price}], "discounts": [{"coupon": coupon}]},
    )["body"]
    call(instance, "DELETE", f"/v1/coupons/{coupon}")
    result = call(instance, "POST", f"/v1/subscriptions/{sub['id']}", {"trial_end": 1798761600})
    assert result["status"] == 200  # the conversion nets it, refusal-free
    invoice = one_row(
        instance, "SELECT * FROM invoices WHERE id = ?", result["body"]["latest_invoice"]
    )
    assert invoice["subtotal"] == -1800  # the tombstoned coupon still applied


def test_cancel_at_clears_with_the_empty_string(instance: seahaven.Instance) -> None:
    cus, _, price = setup_catalog(instance)
    sub = call(
        instance, "POST", "/v1/subscriptions", {"customer": cus, "items": [{"price": price}]}
    )["body"]
    body = call(instance, "POST", f"/v1/subscriptions/{sub['id']}", {"cancel_at": 1798761600})[
        "body"
    ]
    assert body["cancel_at"] == 1798761600
    body = call(instance, "POST", f"/v1/subscriptions/{sub['id']}", {"cancel_at": ""})["body"]
    assert body["cancel_at"] is None
    body = call(instance, "POST", f"/v1/subscriptions/{sub['id']}", {"cancel_at": None})["body"]
    assert body["cancel_at"] is None


def test_advance_cycle_emits_from_the_true_pre_stamp_body(instance: seahaven.Instance) -> None:
    """The walker's update event diffs against the PRE-stamp capture. For a
    trial the boundary IS the trial end, so `trial_end` correctly shows no
    delta — the assertion is that the capture reflects the pre-write row
    (the status delta) and never invents one."""
    import json

    import seahaven_stripe_world.billing.subscription_lifecycle as lifecycle

    cus, _, price = setup_catalog(instance)
    sub = call(
        instance,
        "POST",
        "/v1/subscriptions",
        {"customer": cus, "items": [{"price": price}], "trial_end": 1798761600},
    )["body"]
    with instance.bulk() as ctx:
        lifecycle.advance_cycle(ctx, sub["id"])
    last = one_row(
        instance,
        "SELECT data FROM events WHERE type = 'customer.subscription.updated'"
        " ORDER BY x_seq DESC LIMIT 1",
    )
    data = json.loads(last["data"])
    assert data["previous_attributes"]["status"] == "trialing"
    assert "trial_end" not in data["previous_attributes"]  # boundary == trial end: no delta


# --- the round-8 corrections ----------------------------------------------------------------


def _unpaid_subscription(instance: seahaven.Instance, price: str) -> str:
    """An `unpaid` row laid down directly — the walker the fixture
    generator will drive once dunning lands (Phase 14); the status is
    stored state, not a routed outcome."""
    import seahaven_stripe_world.billing.subscription_lifecycle as lifecycle

    cus = call(instance, "POST", "/v1/customers", {"email": "u8@example.test"})["body"]["id"]
    dec = call(
        instance,
        "POST",
        "/v1/payment_methods",
        {
            "type": "card",
            "card": {"number": "4000000000000341", "exp_month": 9, "exp_year": 2027, "cvc": "123"},
        },
    )["body"]["id"]
    call(instance, "POST", f"/v1/payment_methods/{dec}/attach", {"customer": cus})
    call(
        instance,
        "POST",
        f"/v1/customers/{cus}",
        {"invoice_settings": {"default_payment_method": dec}},
    )
    sub = call(
        instance, "POST", "/v1/subscriptions", {"customer": cus, "items": [{"price": price}]}
    )["body"]
    with instance.bulk() as ctx:
        lifecycle._set_status(ctx, sub["id"], "unpaid")
    return sub["id"]


def test_unpaid_wins_over_pause_behaviors(instance: seahaven.Instance) -> None:
    """§1's unpaid row: the renewal stays draft under ANY pause behavior —
    no crash, no finalization, no stamp."""
    import json

    import seahaven_stripe_world.billing.subscription_lifecycle as lifecycle

    _cus, _pm, price = setup_catalog(instance)
    for behavior in ("void", "mark_uncollectible"):
        sub_id = _unpaid_subscription(instance, price)
        call(
            instance,
            "POST",
            f"/v1/subscriptions/{sub_id}",
            {"pause_collection": {"behavior": behavior}},
        )
        with instance.bulk() as ctx:
            lifecycle.advance_cycle(ctx, sub_id)
        fresh = call(instance, "GET", f"/v1/subscriptions/{sub_id}")["body"]
        invoice = one_row(instance, "SELECT * FROM invoices WHERE id = ?", fresh["latest_invoice"])
        assert invoice["status"] == "draft", behavior
        assert invoice["attempted"] == 0, behavior
        assert invoice["number"] is None, behavior
        transitions = json.loads(invoice["status_transitions"])
        assert transitions["marked_uncollectible_at"] is None, behavior
        assert transitions["voided_at"] is None, behavior


def test_mark_uncollectible_stamp_on_the_standard_cell(instance: seahaven.Instance) -> None:
    """From open (a paid-eligible row), the pause behavior finalizes then
    marks uncollectible WITH the §2 stamp; void stamps voided_at."""
    import json

    import seahaven_stripe_world.billing.subscription_lifecycle as lifecycle

    cus, _pm, price = setup_catalog(instance)
    sub = call(
        instance, "POST", "/v1/subscriptions", {"customer": cus, "items": [{"price": price}]}
    )["body"]
    call(
        instance,
        "POST",
        f"/v1/subscriptions/{sub['id']}",
        {"pause_collection": {"behavior": "mark_uncollectible"}},
    )
    with instance.bulk() as ctx:
        lifecycle.advance_cycle(ctx, sub["id"])
    fresh = call(instance, "GET", f"/v1/subscriptions/{sub['id']}")["body"]
    invoice = one_row(instance, "SELECT * FROM invoices WHERE id = ?", fresh["latest_invoice"])
    assert invoice["status"] == "uncollectible"
    transitions = json.loads(invoice["status_transitions"])
    assert transitions["marked_uncollectible_at"] is not None

    sub2 = call(
        instance, "POST", "/v1/subscriptions", {"customer": cus, "items": [{"price": price}]}
    )["body"]
    call(
        instance,
        "POST",
        f"/v1/subscriptions/{sub2['id']}",
        {"pause_collection": {"behavior": "void"}},
    )
    with instance.bulk() as ctx:
        lifecycle.advance_cycle(ctx, sub2["id"])
    fresh = call(instance, "GET", f"/v1/subscriptions/{sub2['id']}")["body"]
    invoice = one_row(instance, "SELECT * FROM invoices WHERE id = ?", fresh["latest_invoice"])
    assert invoice["status"] == "void"
    assert json.loads(invoice["status_transitions"])["voided_at"] is not None


def test_the_itemless_subscription_answers_graceful_shapes(instance: seahaven.Instance) -> None:
    """The spec-blessed delete-last-item state: updates succeed, a new item
    spans its own interval, a trial end bills nothing — never an INTERNAL."""
    cus, _, price = setup_catalog(instance)
    sub = call(
        instance, "POST", "/v1/subscriptions", {"customer": cus, "items": [{"price": price}]}
    )["body"]
    only = sub["items"]["data"][0]["id"]
    assert call(instance, "DELETE", f"/v1/subscription_items/{only}")["status"] == 200
    assert call(instance, "GET", f"/v1/subscriptions/{sub['id']}")["body"]["items"]["data"] == []
    # the flag stands, the scheduled instant is null
    body = call(instance, "POST", f"/v1/subscriptions/{sub['id']}", {"cancel_at_period_end": True})[
        "body"
    ]
    assert body["cancel_at_period_end"] is True
    assert body["cancel_at"] is None
    # a new item spans its own [now, +interval)
    body = call(instance, "POST", f"/v1/subscriptions/{sub['id']}", {"items": [{"price": price}]})[
        "body"
    ]
    item = body["items"]["data"][0]
    assert item["current_period_start"] == body["created"]
    assert item["current_period_end"] - item["current_period_start"] == 30 * 86_400


def test_an_itemless_trial_end_bills_nothing(instance: seahaven.Instance) -> None:
    cus, _, price = setup_catalog(instance)
    sub = call(
        instance,
        "POST",
        "/v1/subscriptions",
        {"customer": cus, "items": [{"price": price}], "trial_end": 1798761600},
    )["body"]
    invoices_before = one_row(instance, "SELECT count(*) AS n FROM invoices")["n"]
    only = sub["items"]["data"][0]["id"]
    call(instance, "DELETE", f"/v1/subscription_items/{only}")
    body = call(instance, "POST", f"/v1/subscriptions/{sub['id']}", {"trial_end": "now"})["body"]
    assert body["status"] == "active"
    assert one_row(instance, "SELECT count(*) AS n FROM invoices")["n"] == invoices_before


# --- the round-9 pins ----------------------------------------------------------------


def test_the_itemless_resume_chain_confirms_without_crashing(instance: seahaven.Instance) -> None:
    """The full routed chain the round-9 Moderate executed: itemless pause
    -> resume (no invoice minted) -> attach + confirm -> the parked update
    applies against the non-open latest, with the resumed events."""
    _, _, price = setup_catalog(instance)
    bare = call(instance, "POST", "/v1/customers", {"email": "r9@example.test"})["body"]["id"]
    sub = call(
        instance,
        "POST",
        "/v1/subscriptions",
        {
            "customer": bare,
            "items": [{"price": price}],
            "trial_end": 1798761600,
            "trial_settings": {"end_behavior": {"missing_payment_method": "pause"}},
        },
    )["body"]
    call(instance, "DELETE", f"/v1/subscription_items/{sub['items']['data'][0]['id']}")
    paused = call(instance, "POST", f"/v1/subscriptions/{sub['id']}", {"trial_end": "now"})["body"]
    assert paused["status"] == "paused"
    invoices_before = one_row(instance, "SELECT count(*) AS n FROM invoices")["n"]
    resumed = call(instance, "POST", f"/v1/subscriptions/{sub['id']}/resume", {})["body"]
    assert resumed["status"] == "paused"
    assert one_row(instance, "SELECT count(*) AS n FROM invoices")["n"] == invoices_before
    # the latest is still the paid $0 trial invoice — the confirm must not
    # try to pay it
    latest = one_row(instance, "SELECT * FROM invoices WHERE id = ?", resumed["latest_invoice"])
    assert latest["status"] == "paid"
    pm = call(
        instance, "POST", "/v1/payment_methods", {"type": "card", "card": {"token": "tok_visa"}}
    )["body"]["id"]
    call(instance, "POST", f"/v1/payment_methods/{pm}/attach", {"customer": bare})
    confirmed = call(
        instance,
        "POST",
        f"/v1/setup_intents/{resumed['pending_setup_intent']}/confirm",
        {"payment_method": pm},
    )
    assert confirmed["status"] == 200
    fresh = call(instance, "GET", f"/v1/subscriptions/{sub['id']}")["body"]
    assert fresh["status"] == "active"
    assert fresh["pending_setup_intent"] is None
    assert fresh["pending_update"] is None
    types = [row["type"] for row in instance.inspect().rows("SELECT type FROM events")]
    assert "customer.subscription.resumed" in types


def test_the_itemless_conversion_mints_no_invoice(instance: seahaven.Instance) -> None:
    cus, _, price = setup_catalog(instance)
    sub = call(
        instance, "POST", "/v1/subscriptions", {"customer": cus, "items": [{"price": price}]}
    )["body"]
    call(instance, "DELETE", f"/v1/subscription_items/{sub['items']['data'][0]['id']}")
    invoices_before = one_row(instance, "SELECT count(*) AS n FROM invoices")["n"]
    result = call(instance, "POST", f"/v1/subscriptions/{sub['id']}", {"trial_end": 1798761600})
    assert result["status"] == 200
    body = result["body"]
    assert body["status"] == "trialing"
    assert one_row(instance, "SELECT count(*) AS n FROM invoices")["n"] == invoices_before


def test_advance_cycle_refuses_the_itemless_state(instance: seahaven.Instance) -> None:
    import pytest
    import seahaven

    import seahaven_stripe_world.billing.subscription_lifecycle as lifecycle

    cus, _, price = setup_catalog(instance)
    sub = call(
        instance, "POST", "/v1/subscriptions", {"customer": cus, "items": [{"price": price}]}
    )["body"]
    call(instance, "DELETE", f"/v1/subscription_items/{sub['items']['data'][0]['id']}")
    with instance.bulk() as ctx, pytest.raises(seahaven.WorldBug):
        lifecycle.advance_cycle(ctx, sub["id"])
