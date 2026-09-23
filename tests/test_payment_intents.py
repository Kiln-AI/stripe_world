"""The PaymentIntent half of the money path, through the real four-tool
chain. Every transition, refusal and default here is pinned by the Phase 8
probes and cassette 04 at `2026-08-26.dahlia`; the 3DS on-session stub is
unit-tested only (declared structural difference — the recorded next_action
carries issuer certificates)."""

import pytest
import seahaven

from conftest import BLANK_NOW
from seahaven_stripe_world.errors import StripeToolError

pytestmark = pytest.mark.seahaven(fixture=None, now=BLANK_NOW)


def call(instance: seahaven.Instance, method: str, path: str, params: dict | None = None, **kw):
    if method == "GET":
        return instance.call("stripe_api_read", path=path, params=params)
    return instance.call("stripe_api_write", method=method, path=path, params=params, **kw)


def customer_with_card(instance: seahaven.Instance, token: str = "tok_visa") -> tuple[str, str]:
    cus = call(instance, "POST", "/v1/customers", {"email": "pi@example.test"})["id"]
    pm = call(instance, "POST", "/v1/payment_methods", {"type": "card", "card": {"token": token}})[
        "id"
    ]
    call(instance, "POST", f"/v1/payment_methods/{pm}/attach", {"customer": cus})
    return cus, pm


def create(instance: seahaven.Instance, params: dict, **kw) -> dict:
    return call(instance, "POST", "/v1/payment_intents", params, **kw)


CARD_ONLY = {"payment_method_types": ["card"]}


# --- creation ----------------------------------------------------------------------


def test_plain_create_defaults(instance: seahaven.Instance) -> None:
    pi = create(instance, {"amount": 4900, "currency": "usd", "metadata": {"p": "1"}})
    assert pi["object"] == "payment_intent"
    assert pi["status"] == "requires_payment_method"
    assert pi["capture_method"] == "automatic_async"  # recorded default at dahlia
    assert pi["confirmation_method"] == "automatic"
    assert pi["payment_method_types"] == ["card"]
    assert pi["amount_details"] == {"tip": {}}
    assert pi["amount_capturable"] == 0
    assert pi["amount_received"] == 0
    assert pi["latest_charge"] is None
    assert pi["payment_method"] is None
    assert pi["managed_payments"] is None  # spec-legal null (see FIELDS' comment)
    assert pi["client_secret"].startswith(f"{pi['id']}_secret_")
    # the events: created only
    types = [
        row["type"] for row in instance.inspect().rows("SELECT type FROM events ORDER BY x_seq")
    ]
    assert types == ["payment_intent.created"]


def test_create_with_payment_method_lands_requires_confirmation(
    instance: seahaven.Instance,
) -> None:
    cus, pm = customer_with_card(instance)
    pi = create(
        instance, {"amount": 1000, "currency": "usd", "customer": cus, "payment_method": pm}
    )
    assert pi["status"] == "requires_confirmation"
    assert pi["payment_method"] == pm


def test_zero_amount_is_refused_with_the_recorded_message(instance: seahaven.Instance) -> None:
    with pytest.raises(StripeToolError) as exc_info:
        create(instance, {"amount": 0, "currency": "usd"})
    error = exc_info.value.stripe_body["error"]
    assert error["code"] == "parameter_invalid_integer"
    assert error["param"] == "amount"
    assert error["message"].startswith("The amount must be greater than or equal to the minimum")
    # nothing was written
    assert instance.inspect().one("SELECT count(*) AS n FROM payment_intents") == {"n": 0}


# --- confirm: success ----------------------------------------------------------------


def test_confirm_in_create_succeeds_and_writes_the_pair(instance: seahaven.Instance) -> None:
    cus, pm = customer_with_card(instance)
    pi = create(
        instance,
        {"amount": 4900, "currency": "usd", "customer": cus, "payment_method": pm, "confirm": True},
    )
    assert pi["status"] == "succeeded"
    assert pi["amount_received"] == 4900
    assert pi["latest_charge"].startswith("ch_")
    types = [
        row["type"] for row in instance.inspect().rows("SELECT type FROM events ORDER BY x_seq")
    ]
    assert types == [
        "customer.created",
        "payment_method.attached",
        "payment_intent.created",
        "charge.succeeded",
        "payment_intent.succeeded",
    ]
    # one charge, fully captured
    ch = call(instance, "GET", f"/v1/charges/{pi['latest_charge']}")
    assert ch["status"] == "succeeded"
    assert ch["captured"] is True
    assert ch["paid"] is True
    assert ch["amount_captured"] == 4900


def test_confirm_endpoint_resolves_the_intent_pm_then_the_customer_default(
    instance: seahaven.Instance,
) -> None:
    cus, pm = customer_with_card(instance)
    call(
        instance,
        "POST",
        f"/v1/customers/{cus}",
        {"invoice_settings": {"default_payment_method": pm}},
    )
    pi = create(instance, {"amount": 1200, "currency": "usd", "customer": cus})
    confirmed = call(instance, "POST", f"/v1/payment_intents/{pi['id']}/confirm")
    assert confirmed["status"] == "succeeded"
    assert confirmed["payment_method"] == pm


# --- confirm: decline ----------------------------------------------------------------


def test_the_declined_card_returns_402_and_keeps_its_rows(instance: seahaven.Instance) -> None:
    cus, _ = customer_with_card(instance)
    declined = call(
        instance,
        "POST",
        "/v1/payment_methods",
        {"type": "card", "card": {"number": "4000000000000341", "exp_month": 9, "exp_year": 2027}},
    )
    with pytest.raises(StripeToolError) as exc_info:
        create(
            instance,
            {
                "amount": 1000,
                "currency": "usd",
                "customer": cus,
                "payment_method": declined["id"],
                "confirm": True,
            },
        )
    assert exc_info.value.status == 402  # the outcome whose rows survive
    error = exc_info.value.stripe_body["error"]
    assert error["type"] == "card_error"
    assert error["code"] == "card_declined"
    assert error["decline_code"] == "generic_decline"
    assert error["message"] == "Your card was declined."
    assert error["charge"].startswith("ch_")
    assert "param" not in error
    # the full intent rides the error object, reset to requires_payment_method
    sub = error["payment_intent"]
    assert sub["status"] == "requires_payment_method"
    assert sub["payment_method"] is None
    assert sub["last_payment_error"]["code"] == "card_declined"
    assert sub["last_payment_error"]["charge"] == error["charge"]
    # the rows survived: a failed charge and the intent state
    failed = instance.inspect().one(
        "SELECT status, failure_code, paid, captured FROM charges WHERE id = ?", error["charge"]
    )
    assert failed == {
        "status": "failed",
        "failure_code": "card_declined",
        "paid": 0,
        "captured": 0,
    }
    types = [
        row["type"] for row in instance.inspect().rows("SELECT type FROM events ORDER BY x_seq")
    ]
    assert types[-2:] == ["charge.failed", "payment_intent.payment_failed"]


def test_confirm_with_no_payment_method_refuses_both_recorded_forms(
    instance: seahaven.Instance,
) -> None:
    bare = create(instance, {"amount": 800, "currency": "usd", **CARD_ONLY})
    with pytest.raises(StripeToolError) as exc_info:
        call(instance, "POST", f"/v1/payment_intents/{bare['id']}/confirm")
    assert exc_info.value.status == 400
    error = exc_info.value.stripe_body["error"]
    assert error["code"] == "payment_intent_unexpected_state"
    assert error["message"] == (
        "You cannot confirm this PaymentIntent because it's missing a payment method. "
        "You can either update the PaymentIntent with a payment method and then confirm "
        "it again, or confirm it again directly with a payment method or ConfirmationToken."
    )
    assert error["payment_intent"]["id"] == bare["id"]

    cus = call(instance, "POST", "/v1/customers", {"email": "bare@example.test"})["id"]
    with_customer = create(
        instance, {"amount": 800, "currency": "usd", "customer": cus, **CARD_ONLY}
    )
    with pytest.raises(StripeToolError) as exc_info:
        call(instance, "POST", f"/v1/payment_intents/{with_customer['id']}/confirm")
    assert exc_info.value.status == 400
    assert exc_info.value.stripe_body["error"]["message"] == (
        "You cannot confirm this PaymentIntent because it's missing a payment method. "
        f"To confirm the PaymentIntent with {cus}, specify a payment method attached to "
        "this customer along with the customer ID."
    )


def test_a_customerless_intent_refuses_another_customers_method(
    instance: seahaven.Instance,
) -> None:
    """Probed twice (CR round 1): the ownership refusal, `parameter_missing`
    under a quirky `param: "source"`, the full intent carried."""
    cus, pm = customer_with_card(instance)
    pi = create(instance, {"amount": 500, "currency": "usd", **CARD_ONLY})
    with pytest.raises(StripeToolError) as exc_info:
        call(instance, "POST", f"/v1/payment_intents/{pi['id']}/confirm", {"payment_method": pm})
    assert exc_info.value.status == 400
    error = exc_info.value.stripe_body["error"]
    assert error["code"] == "parameter_missing"
    assert error["param"] == "source"
    assert error["message"] == (
        f"The `payment_method` parameter supplied {pm} belongs to the Customer {cus}. "
        "Please include the Customer in the `customer` parameter on the PaymentIntent."
    )
    assert error["payment_intent"]["id"] == pi["id"]


def test_a_fresh_unattached_method_charges_a_customerless_intent(
    instance: seahaven.Instance,
) -> None:
    """Probed CR round 1: a fresh unattached method on a customerless intent
    charges (200) — real Stripe's rule burns the method after one use, and
    this world does not track prior uses (the code comment's declared
    divergence for the reused case)."""
    fresh = call(
        instance, "POST", "/v1/payment_methods", {"type": "card", "card": {"token": "tok_visa"}}
    )
    pi = create(instance, {"amount": 500, "currency": "usd", **CARD_ONLY})
    confirmed = call(
        instance, "POST", f"/v1/payment_intents/{pi['id']}/confirm", {"payment_method": fresh["id"]}
    )
    assert confirmed["status"] == "succeeded"
    assert confirmed["latest_charge"].startswith("ch_")


def test_another_customers_method_on_a_customer_intent_is_refused(
    instance: seahaven.Instance,
) -> None:
    """Recorded (cassette 04): no code, `param: "payment_method"`, the full
    intent carried on the error object."""
    cus, _pm = customer_with_card(instance)
    other = call(instance, "POST", "/v1/customers", {"email": "o3@example.test"})["id"]
    stranger = call(
        instance, "POST", "/v1/payment_methods", {"type": "card", "card": {"token": "tok_visa"}}
    )
    call(instance, "POST", f"/v1/payment_methods/{stranger['id']}/attach", {"customer": other})
    pi = create(instance, {"amount": 600, "currency": "usd", "customer": cus, **CARD_ONLY})
    with pytest.raises(StripeToolError) as exc_info:
        call(
            instance,
            "POST",
            f"/v1/payment_intents/{pi['id']}/confirm",
            {"payment_method": stranger["id"]},
        )
    assert exc_info.value.status == 400
    error = exc_info.value.stripe_body["error"]
    assert "code" not in error
    assert error["param"] == "payment_method"
    assert error["message"] == (
        f"The PaymentMethod {stranger['id']} does not belong to the Customer you supplied "
        f"{cus}. Please use this PaymentMethod with the Customer that it belongs to instead."
    )
    assert error["payment_intent"]["id"] == pi["id"]


# --- confirm: 3DS ---------------------------------------------------------------------


def test_the_3ds_card_on_session_parks_at_requires_action(instance: seahaven.Instance) -> None:
    cus, _ = customer_with_card(instance)
    three_ds = call(
        instance,
        "POST",
        "/v1/payment_methods",
        {"type": "card", "card": {"token": "tok_threeDSecure2Required"}},
    )
    call(instance, "POST", f"/v1/payment_methods/{three_ds['id']}/attach", {"customer": cus})
    pi = create(
        instance,
        {
            "amount": 4400,
            "currency": "usd",
            "customer": cus,
            "payment_method": three_ds["id"],
            "confirm": True,
        },
    )
    assert pi["status"] == "requires_action"
    assert pi["latest_charge"] is None  # no charge row exists yet (recorded)
    assert pi["next_action"] == {"type": "use_stripe_sdk", "use_stripe_sdk": {}}
    assert instance.inspect().one("SELECT count(*) AS n FROM charges") == {"n": 0}
    types = [
        row["type"] for row in instance.inspect().rows("SELECT type FROM events ORDER BY x_seq")
    ]
    assert types[-1] == "payment_intent.requires_action"


def test_the_3ds_card_off_session_declines_authentication_required(
    instance: seahaven.Instance,
) -> None:
    cus, _ = customer_with_card(instance)
    three_ds = call(
        instance,
        "POST",
        "/v1/payment_methods",
        {"type": "card", "card": {"token": "tok_threeDSecure2Required"}},
    )
    with pytest.raises(StripeToolError) as exc_info:
        create(
            instance,
            {
                "amount": 3300,
                "currency": "usd",
                "customer": cus,
                "payment_method": three_ds["id"],
                "confirm": True,
                "off_session": True,
            },
        )
    assert exc_info.value.status == 402
    error = exc_info.value.stripe_body["error"]
    assert error["code"] == "authentication_required"
    assert error["decline_code"] == "authentication_required"
    assert error["message"] == "Your card was declined. This transaction requires authentication."
    # recorded: no charge row, the intent reset, the method on the error
    assert "charge" not in error
    assert error["payment_intent"]["status"] == "requires_payment_method"
    assert error["payment_method"]["id"] == three_ds["id"]
    assert instance.inspect().one("SELECT count(*) AS n FROM charges") == {"n": 0}


# --- capture ---------------------------------------------------------------------------


def test_manual_capture_holds_then_partial_capture_succeeds(instance: seahaven.Instance) -> None:
    cus, pm = customer_with_card(instance)
    pi = create(
        instance,
        {
            "amount": 6500,
            "currency": "usd",
            "customer": cus,
            "payment_method": pm,
            "confirm": True,
            "capture_method": "manual",
        },
    )
    assert pi["status"] == "requires_capture"
    assert pi["amount_capturable"] == 6500
    assert pi["amount_received"] == 0
    held = call(instance, "GET", f"/v1/charges/{pi['latest_charge']}")
    assert held["status"] == "succeeded"  # authorized, paid…
    assert held["paid"] is True
    assert held["captured"] is False  # …but uncaptured (recorded)
    assert held["amount_captured"] == 0
    assert held["payment_method_details"]["card"]["capture_before"] > 0
    types = [
        row["type"] for row in instance.inspect().rows("SELECT type FROM events ORDER BY x_seq")
    ]
    assert types[-2:] == ["charge.succeeded", "payment_intent.amount_capturable_updated"]

    captured = call(
        instance,
        "POST",
        f"/v1/payment_intents/{pi['id']}/capture",
        {"amount_to_capture": 5000, "metadata": {"a": "b"}},
    )
    assert captured["status"] == "succeeded"
    assert captured["amount_received"] == 5000
    assert captured["amount_capturable"] == 0
    assert captured["metadata"] == {"a": "b"}  # capture merges metadata (CR round 1)
    after = call(instance, "GET", f"/v1/payment_intents/{pi['id']}")
    assert after["metadata"] == {"a": "b"}  # and it stuck
    after = call(instance, "GET", f"/v1/charges/{pi['latest_charge']}")
    assert after["captured"] is True
    assert after["amount_captured"] == 5000
    types = [
        row["type"] for row in instance.inspect().rows("SELECT type FROM events ORDER BY x_seq")
    ]
    assert types[-2:] == ["charge.captured", "payment_intent.succeeded"]


def test_capture_refusals_are_the_recorded_shapes(instance: seahaven.Instance) -> None:
    cus, pm = customer_with_card(instance)
    ok = create(
        instance,
        {"amount": 1500, "currency": "usd", "customer": cus, "payment_method": pm, "confirm": True},
    )
    with pytest.raises(StripeToolError) as exc_info:
        call(instance, "POST", f"/v1/payment_intents/{ok['id']}/capture")
    assert exc_info.value.status == 400
    assert exc_info.value.stripe_body["error"]["message"] == (
        "The remaining amount on this PaymentIntent could not be captured because "
        "the remainder of the authorized amount has been released."
    )

    hold = create(
        instance,
        {
            "amount": 5000,
            "currency": "usd",
            "customer": cus,
            "payment_method": pm,
            "confirm": True,
            "capture_method": "manual",
        },
    )
    with pytest.raises(StripeToolError) as exc_info:
        call(
            instance,
            "POST",
            f"/v1/payment_intents/{hold['id']}/capture",
            {"amount_to_capture": 9999},
        )
    assert exc_info.value.status == 400
    error = exc_info.value.stripe_body["error"]
    assert error["code"] == "amount_too_large"
    assert error["message"].startswith("The payment could not be captured because the requested")
    assert error["payment_intent"]["id"] == hold["id"]

    # The capture floor (recorded, cassette 04, CR round 2): negative answers
    # a codeless refusal naming the parameter; zero answers the minimum-charge
    # refusal, oddly under `param: "amount"`.
    neg_hold = create(
        instance,
        {
            "amount": 2500,
            "currency": "usd",
            "customer": cus,
            "payment_method": pm,
            "confirm": True,
            "capture_method": "manual",
        },
    )
    with pytest.raises(StripeToolError) as exc_info:
        call(
            instance,
            "POST",
            f"/v1/payment_intents/{neg_hold['id']}/capture",
            {"amount_to_capture": -100},
        )
    assert exc_info.value.status == 400
    neg_error = exc_info.value.stripe_body["error"]
    assert "code" not in neg_error
    assert neg_error["param"] == "amount_to_capture"
    assert neg_error["message"] == "Invalid non-negative integer"
    assert neg_error["payment_intent"]["id"] == neg_hold["id"]

    zero_hold = create(
        instance,
        {
            "amount": 2500,
            "currency": "usd",
            "customer": cus,
            "payment_method": pm,
            "confirm": True,
            "capture_method": "manual",
        },
    )
    with pytest.raises(StripeToolError) as exc_info:
        call(
            instance,
            "POST",
            f"/v1/payment_intents/{zero_hold['id']}/capture",
            {"amount_to_capture": 0},
        )
    assert exc_info.value.status == 400
    zero_error = exc_info.value.stripe_body["error"]
    assert zero_error["code"] == "parameter_invalid_integer"
    assert zero_error["param"] == "amount"
    assert zero_error["message"] == "This value must be greater than or equal to 1."
    assert zero_error["payment_intent"]["id"] == zero_hold["id"]
    # and neither hold moved
    for hold_id in (neg_hold["id"], zero_hold["id"]):
        assert call(instance, "GET", f"/v1/payment_intents/{hold_id}")["status"] == (
            "requires_capture"
        )

    plain = create(instance, {"amount": 1000, "currency": "usd"})
    with pytest.raises(StripeToolError) as exc_info:
        call(instance, "POST", f"/v1/payment_intents/{plain['id']}/capture")
    assert exc_info.value.status == 400
    assert exc_info.value.stripe_body["error"]["message"] == (
        "This PaymentIntent could not be captured because it has a status of "
        "requires_payment_method. Only a PaymentIntent with one of the following "
        "statuses may be captured: requires_capture."
    )


# --- cancel ------------------------------------------------------------------------------


def test_cancel_echoes_the_reason_and_refuses_the_wrong_states(
    instance: seahaven.Instance,
) -> None:
    pi = create(instance, {"amount": 1200, "currency": "usd"})
    canceled = call(
        instance,
        "POST",
        f"/v1/payment_intents/{pi['id']}/cancel",
        {"cancellation_reason": "abandoned"},
    )
    assert canceled["status"] == "canceled"
    assert canceled["cancellation_reason"] == "abandoned"
    assert canceled["canceled_at"] is not None

    with pytest.raises(StripeToolError) as exc_info:
        call(instance, "POST", f"/v1/payment_intents/{pi['id']}/cancel")
    assert exc_info.value.status == 400
    assert exc_info.value.stripe_body["error"]["message"] == (
        "You cannot cancel this PaymentIntent because it has a status of canceled. "
        "Only a PaymentIntent with one of the following statuses may be canceled: "
        "requires_payment_method, requires_capture, requires_reauthorization, "
        "requires_confirmation, requires_action, expired, processing."
    )

    cus, pm = customer_with_card(instance)
    ok = create(
        instance,
        {"amount": 500, "currency": "usd", "customer": cus, "payment_method": pm, "confirm": True},
    )
    with pytest.raises(StripeToolError) as exc_info:
        call(instance, "POST", f"/v1/payment_intents/{ok['id']}/cancel")
    assert exc_info.value.status == 400
    assert exc_info.value.stripe_body["error"]["message"].startswith(
        "You cannot cancel this PaymentIntent because it has a status of succeeded."
    )


def test_cancel_without_a_reason_leaves_it_null_and_releases_the_hold(
    instance: seahaven.Instance,
) -> None:
    cus, pm = customer_with_card(instance)
    hold = create(
        instance,
        {
            "amount": 6100,
            "currency": "usd",
            "customer": cus,
            "payment_method": pm,
            "confirm": True,
            "capture_method": "manual",
        },
    )
    canceled = call(instance, "POST", f"/v1/payment_intents/{hold['id']}/cancel")
    assert canceled["cancellation_reason"] is None  # recorded: null when omitted
    charge = call(instance, "GET", f"/v1/charges/{hold['latest_charge']}")
    assert charge["status"] == "succeeded"  # the uncaptured shape is kept (recorded)
    assert charge["captured"] is False


# --- update --------------------------------------------------------------------------------


def test_amount_updates_are_status_guarded_and_metadata_is_not(instance: seahaven.Instance) -> None:
    cus, pm = customer_with_card(instance)
    ok = create(
        instance,
        {"amount": 1500, "currency": "usd", "customer": cus, "payment_method": pm, "confirm": True},
    )
    with pytest.raises(StripeToolError) as exc_info:
        call(instance, "POST", f"/v1/payment_intents/{ok['id']}", {"amount": 9999})
    assert exc_info.value.status == 400
    error = exc_info.value.stripe_body["error"]
    assert error["code"] == "payment_intent_unexpected_state"
    assert error["param"] == "amount"
    assert error["message"] == (
        "This PaymentIntent's amount could not be updated because it has a status of "
        "succeeded. You may only update the amount of a PaymentIntent with one of the "
        "following statuses: requires_payment_method, requires_confirmation, requires_action."
    )
    assert error["payment_intent"]["id"] == ok["id"]

    moved = call(
        instance,
        "POST",
        f"/v1/payment_intents/{ok['id']}",
        {"metadata": {"u": "v"}, "description": "after"},
    )
    assert moved["metadata"] == {"u": "v"}

    plain = create(instance, {"amount": 1000, "currency": "usd"})
    upped = call(instance, "POST", f"/v1/payment_intents/{plain['id']}", {"amount": 5100})
    assert upped["amount"] == 5100


# --- retrieve, expand, list --------------------------------------------------------------------


def test_latest_charge_expands(instance: seahaven.Instance) -> None:
    cus, pm = customer_with_card(instance)
    pi = create(
        instance,
        {"amount": 2200, "currency": "usd", "customer": cus, "payment_method": pm, "confirm": True},
    )
    got = call(instance, "GET", f"/v1/payment_intents/{pi['id']}", {"expand": ["latest_charge"]})
    assert got["latest_charge"]["object"] == "charge"
    assert got["latest_charge"]["payment_intent"] == pi["id"]


def test_lists_filter_and_order_by_recency(instance: seahaven.Instance) -> None:
    cus, _ = customer_with_card(instance)
    other = call(instance, "POST", "/v1/customers", {"email": "other@example.test"})["id"]
    for amount in (1100, 2200):
        create(instance, {"amount": amount, "currency": "usd", "customer": cus})
    create(instance, {"amount": 3300, "currency": "usd", "customer": other})
    page = call(instance, "GET", "/v1/payment_intents", {"customer": cus})
    assert page["object"] == "list"
    assert page["url"] == "/v1/payment_intents"
    assert len(page["data"]) == 2
    amounts = [item["amount"] for item in page["data"]]
    assert amounts == [2200, 1100]  # x_seq DESC: newest first


def test_missing_ids_keep_their_recorded_spellings(instance: seahaven.Instance) -> None:
    with pytest.raises(StripeToolError) as exc_info:
        call(instance, "GET", "/v1/payment_intents/pi_nope")
    error = exc_info.value.stripe_body["error"]
    assert error["message"] == "No such payment_intent: 'pi_nope'"
    assert error["param"] == "intent"  # the placeholder, not `id` (probed)
    with pytest.raises(StripeToolError) as exc_info:
        call(instance, "POST", "/v1/payment_intents/pi_nope/confirm")
    assert exc_info.value.stripe_body["error"]["param"] == "intent"
