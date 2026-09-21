"""The SetupIntent slice, through the real four-tool chain. Every
transition, refusal and default here is pinned by the Phase 10 probes and
cassette 10 at `2026-08-26.dahlia`; the 3DS park's `next_action` stub is
unit-tested only (declared structural difference — the recorded body
carries issuer certificates)."""

import pytest
import seahaven

from conftest import BLANK_NOW

pytestmark = pytest.mark.seahaven(fixture=None, now=BLANK_NOW)


def call(instance: seahaven.Instance, method: str, path: str, params: dict | None = None):
    if method == "GET":
        return instance.call("stripe_api_read", path=path, params=params)
    return instance.call("stripe_api_write", method=method, path=path, params=params)


def customer_with_card(instance: seahaven.Instance, token: str = "tok_visa") -> tuple[str, str]:
    cus = call(instance, "POST", "/v1/customers", {"email": "si@example.test"})["body"]["id"]
    pm = call(instance, "POST", "/v1/payment_methods", {"type": "card", "card": {"token": token}})[
        "body"
    ]["id"]
    call(instance, "POST", f"/v1/payment_methods/{pm}/attach", {"customer": cus})
    return cus, pm


def events_of(instance: seahaven.Instance) -> list[str]:
    return [
        row["type"] for row in instance.inspect().rows("SELECT type FROM events ORDER BY x_seq")
    ]


# --- creation ------------------------------------------------------------------------


def test_the_created_body(instance: seahaven.Instance) -> None:
    body = call(instance, "POST", "/v1/setup_intents", {"metadata": {"p": "1"}})["body"]
    assert body["object"] == "setup_intent"
    assert body["id"].startswith("seti_")
    assert body["status"] == "requires_payment_method"
    assert body["usage"] == "off_session"
    assert body["client_secret"].startswith(f"{body['id']}_secret_")
    assert body["payment_method_types"] == ["card"]
    assert body["payment_method_options"] == {
        "card": {"mandate_options": None, "network": None, "request_three_d_secure": "automatic"}
    }
    assert body["metadata"] == {"p": "1"}
    assert body["livemode"] is False
    # the always-present nullables
    for none in (
        "allowed_payment_method_types",
        "application",
        "cancellation_reason",
        "customer",
        "customer_account",
        "excluded_payment_method_types",
        "flow_directions",
        "last_setup_error",
        "latest_attempt",
        "mandate",
        "next_action",
        "on_behalf_of",
        "payment_method",
        "payment_method_configuration_details",
        "single_use_mandate",
    ):
        assert body[none] is None
    assert body["managed_payments"] is None  # spec-legal null (FIELDS' comment)
    # absent while valueless (recorded, cassette 10)
    assert "attach_to_self" not in body
    assert body["description"] is None  # nullable: present as null, not absent
    assert events_of(instance) == ["setup_intent.created"]


def test_the_usage_parameter_is_dead(instance: seahaven.Instance) -> None:
    """Recorded: `on_session` — and even an invalid string — are accepted
    and ignored; the body answers `off_session` every time."""
    for usage in ("on_session", "bogus"):
        body = call(instance, "POST", "/v1/setup_intents", {"usage": usage})["body"]
        assert body["usage"] == "off_session"
    updated = call(
        instance,
        "POST",
        f"/v1/setup_intents/{body['id']}",
        {"usage": "on_session"},
    )["body"]
    assert updated["usage"] == "off_session"


def test_create_with_a_payment_method_lands_requires_confirmation(
    instance: seahaven.Instance,
) -> None:
    cus, pm = customer_with_card(instance)
    body = call(instance, "POST", "/v1/setup_intents", {"customer": cus, "payment_method": pm})[
        "body"
    ]
    assert body["status"] == "requires_confirmation"
    assert body["payment_method"] == pm


def test_create_time_ownership_refusals_carry_no_intent(instance: seahaven.Instance) -> None:
    cus, pm = customer_with_card(instance)
    other = call(instance, "POST", "/v1/customers", {"email": "o@example.test"})["body"]["id"]
    wrong = call(instance, "POST", "/v1/setup_intents", {"customer": other, "payment_method": pm})
    assert wrong["status"] == 400
    err = wrong["body"]["error"]
    assert err["type"] == "invalid_request_error"
    assert "code" not in err
    assert err["param"] == "payment_method"
    assert err["message"] == (
        f"The PaymentMethod {pm} does not belong to the Customer you supplied {other}. "
        "Please use this PaymentMethod with the Customer that it belongs to instead."
    )
    assert "setup_intent" not in err
    customerless = call(instance, "POST", "/v1/setup_intents", {"payment_method": pm})
    assert customerless["status"] == 400
    err = customerless["body"]["error"]
    assert err["param"] == "payment_method"
    assert err["message"] == (
        f"The payment method supplied ({pm}) belongs to the Customer {cus}. "
        "Please include the Customer in the `customer` parameter on the SetupIntent."
    )
    assert "setup_intent" not in err


# --- confirm: success -----------------------------------------------------------------


def test_confirm_succeeds_and_mints_the_attempt_stub(instance: seahaven.Instance) -> None:
    cus, pm = customer_with_card(instance)
    body = call(
        instance,
        "POST",
        "/v1/setup_intents",
        {"customer": cus, "payment_method": pm, "confirm": True},
    )["body"]
    assert body["status"] == "succeeded"
    assert body["payment_method"] == pm
    assert body["latest_attempt"].startswith("setatt_")
    # the card rail mints no mandate (recorded)
    assert body["mandate"] is None
    assert body["single_use_mandate"] is None
    assert body["next_action"] is None
    assert events_of(instance)[-2:] == ["setup_intent.created", "setup_intent.succeeded"]
    # the method was already attached: no attach event, still attached
    assert "payment_method.attached" not in events_of(instance)[-2:]
    fresh = call(instance, "GET", f"/v1/payment_methods/{pm}")["body"]
    assert fresh["customer"] == cus


def test_confirm_auto_attaches_an_unattached_method(instance: seahaven.Instance) -> None:
    cus, _ = customer_with_card(instance)
    pm = call(
        instance, "POST", "/v1/payment_methods", {"type": "card", "card": {"token": "tok_visa"}}
    )["body"]["id"]
    assert call(instance, "GET", f"/v1/payment_methods/{pm}")["body"]["customer"] is None
    body = call(
        instance,
        "POST",
        "/v1/setup_intents",
        {"customer": cus, "payment_method": pm, "confirm": True},
    )["body"]
    assert body["status"] == "succeeded"
    after = call(instance, "GET", f"/v1/payment_methods/{pm}")["body"]
    assert after["customer"] == cus
    # the attach verifies the card (cvc flips to pass) and precedes the
    # terminal event
    assert after["card"]["checks"]["cvc_check"] == "pass"
    assert events_of(instance)[-3:] == [
        "setup_intent.created",
        "payment_method.attached",
        "setup_intent.succeeded",
    ]


def test_the_3ds_card_parks_at_requires_action(instance: seahaven.Instance) -> None:
    """The deterministic stub the recorded `next_action` cannot pin (its
    live body carries issuer certificates) — Phase 8's declared difference,
    applied to the SetupIntent's own recorded transition (200, no charge)."""
    cus, pm = customer_with_card(instance, "tok_threeDSecure2Required")
    body = call(
        instance,
        "POST",
        "/v1/setup_intents",
        {"customer": cus, "payment_method": pm, "confirm": True},
    )["body"]
    assert body["status"] == "requires_action"
    assert body["next_action"] == {"type": "use_stripe_sdk", "use_stripe_sdk": {}}
    assert body["latest_attempt"].startswith("setatt_")
    assert body["last_setup_error"] is None
    assert events_of(instance)[-2:] == ["setup_intent.created", "setup_intent.requires_action"]
    # no charge row exists for a setup
    assert instance.inspect().one("SELECT count(*) AS n FROM charges") == {"n": 0}


# --- confirm: the declines --------------------------------------------------------------


def _declined_flow(instance: seahaven.Instance, token: str, *, inline: bool):
    pm = call(instance, "POST", "/v1/payment_methods", {"type": "card", "card": {"token": token}})[
        "body"
    ]["id"]
    if inline:
        return pm, call(
            instance,
            "POST",
            "/v1/setup_intents",
            {"payment_method": pm, "confirm": True},
        )
    created = call(instance, "POST", "/v1/setup_intents", {"payment_method": pm})["body"]
    return pm, call(instance, "POST", f"/v1/setup_intents/{created['id']}/confirm")


def test_the_generic_decline_is_a_returned_402_whose_rows_survive(
    instance: seahaven.Instance,
) -> None:
    pm, result = _declined_flow(instance, "tok_visa_chargeDeclined", inline=True)
    assert result["status"] == 402
    err = result["body"]["error"]
    assert err["type"] == "card_error"
    assert err["code"] == "card_declined"
    assert err["decline_code"] == "generic_decline"
    assert err["message"] == "Your card was declined."
    assert err["doc_url"] == "https://stripe.com/docs/error-codes/card-declined"
    assert "param" not in err
    assert err["payment_method"]["id"] == pm
    failed = err["setup_intent"]
    assert failed["status"] == "requires_payment_method"
    assert failed["payment_method"] is None  # cleared (recorded)
    assert failed["latest_attempt"].startswith("setatt_")
    assert failed["last_setup_error"]["code"] == "card_declined"
    assert failed["last_setup_error"]["payment_method"]["id"] == pm
    assert "payment_method_type" not in failed["last_setup_error"]
    # the row survives and reads back the same way
    again = call(instance, "GET", f"/v1/setup_intents/{failed['id']}")["body"]
    assert again["status"] == "requires_payment_method"
    assert again["last_setup_error"]["code"] == "card_declined"
    assert events_of(instance)[-2:] == ["setup_intent.created", "setup_intent.setup_failed"]


def test_the_expired_card_decline_carries_its_param_and_fallback_code(
    instance: seahaven.Instance,
) -> None:
    _pm, result = _declined_flow(instance, "tok_chargeDeclinedExpiredCard", inline=False)
    assert result["status"] == 402
    err = result["body"]["error"]
    assert err["code"] == "expired_card"
    # the setup decline's decline_code falls back to the code (recorded)
    assert err["decline_code"] == "expired_card"
    assert err["param"] == "exp_month"
    assert err["message"] == "Your card has expired."
    failed = err["setup_intent"]
    assert failed["status"] == "requires_payment_method"
    assert failed["last_setup_error"]["param"] == "exp_month"


# --- confirm: the refusals ---------------------------------------------------------------


def _refusal(instance: seahaven.Instance, method: str, path: str, params: dict | None = None):
    result = call(instance, method, path, params)
    assert result["status"] == 400
    return result["body"]["error"]


def test_confirm_without_a_method_refuses_and_never_reads_the_customer_default(
    instance: seahaven.Instance,
) -> None:
    cus, pm = customer_with_card(instance)
    call(
        instance,
        "POST",
        f"/v1/customers/{cus}",
        {"invoice_settings": {"default_payment_method": pm}},
    )
    body = call(instance, "POST", "/v1/setup_intents", {"customer": cus})["body"]
    err = _refusal(instance, "POST", f"/v1/setup_intents/{body['id']}/confirm")
    assert err["code"] == "setup_intent_unexpected_state"
    assert err["message"] == (
        "You cannot confirm this SetupIntent because it's missing a payment method. "
        "You can either update the SetupIntent with a payment method and then confirm it "
        "again, or confirm it again directly with a payment method or ConfirmationToken."
    )
    assert err["setup_intent"]["id"] == body["id"]
    # nothing was minted by the refused attempt
    assert err["setup_intent"]["latest_attempt"] is None


def test_confirm_time_ownership_refusals_carry_the_intent(instance: seahaven.Instance) -> None:
    cus, pm = customer_with_card(instance)
    other = call(instance, "POST", "/v1/customers", {"email": "oc@example.test"})["body"]["id"]
    wrong = call(instance, "POST", "/v1/setup_intents", {"customer": other})["body"]
    err = _refusal(
        instance, "POST", f"/v1/setup_intents/{wrong['id']}/confirm", {"payment_method": pm}
    )
    assert "code" not in err
    assert err["param"] == "payment_method"
    assert err["message"] == (
        f"The PaymentMethod {pm} does not belong to the Customer you supplied {other}. "
        "Please use this PaymentMethod with the Customer that it belongs to instead."
    )
    assert err["setup_intent"]["id"] == wrong["id"]
    none = call(instance, "POST", "/v1/setup_intents")["body"]
    err = _refusal(
        instance, "POST", f"/v1/setup_intents/{none['id']}/confirm", {"payment_method": pm}
    )
    assert err["message"] == (
        f"The payment method supplied ({pm}) belongs to the Customer {cus}. "
        "Please include the Customer in the `customer` parameter on the SetupIntent."
    )
    assert err["setup_intent"]["id"] == none["id"]


def test_the_wrong_state_family_verbatim(instance: seahaven.Instance) -> None:
    cus, pm = customer_with_card(instance)
    ok = call(
        instance,
        "POST",
        "/v1/setup_intents",
        {"customer": cus, "payment_method": pm, "confirm": True},
    )["body"]
    err = _refusal(instance, "POST", f"/v1/setup_intents/{ok['id']}/confirm")
    assert err["code"] == "setup_intent_unexpected_state"
    assert err["message"] == "You cannot confirm this SetupIntent because it has already succeeded."
    err = _refusal(instance, "POST", f"/v1/setup_intents/{ok['id']}/cancel")
    assert err["message"] == (
        "You cannot cancel this SetupIntent because it has a status of succeeded. "
        "Only a SetupIntent with one of the following statuses may be canceled: "
        "`requires_payment_method`, `requires_confirmation`, or `requires_action`."
    )


# --- cancel ---------------------------------------------------------------------------


def test_cancel_stamps_the_reason_and_keeps_the_method(instance: seahaven.Instance) -> None:
    cus, pm = customer_with_card(instance)
    body = call(instance, "POST", "/v1/setup_intents", {"customer": cus, "payment_method": pm})[
        "body"
    ]
    canceled = call(
        instance,
        "POST",
        f"/v1/setup_intents/{body['id']}/cancel",
        {"cancellation_reason": "duplicate"},
    )["body"]
    assert canceled["status"] == "canceled"
    assert canceled["cancellation_reason"] == "duplicate"
    assert canceled["payment_method"] == pm  # kept (recorded)
    assert canceled["client_secret"] == body["client_secret"]
    assert events_of(instance)[-1] == "setup_intent.canceled"
    # the wrong states on the canceled intent, both recorded: a method is
    # still resolvable, so the status guards fire
    err = _refusal(instance, "POST", f"/v1/setup_intents/{body['id']}/confirm")
    assert err["message"] == "You cannot confirm this SetupIntent because it has been canceled."
    err = _refusal(instance, "POST", f"/v1/setup_intents/{body['id']}/cancel")
    assert err["message"] == "You cannot cancel this SetupIntent because it is already canceled."


def test_cancel_without_a_reason_stamps_null(instance: seahaven.Instance) -> None:
    body = call(instance, "POST", "/v1/setup_intents")["body"]
    canceled = call(instance, "POST", f"/v1/setup_intents/{body['id']}/cancel")["body"]
    assert canceled["cancellation_reason"] is None


# --- updates ---------------------------------------------------------------------------


def test_updates_merge_metadata_and_move_an_open_intent_to_confirmable(
    instance: seahaven.Instance,
) -> None:
    cus, pm = customer_with_card(instance)
    body = call(
        instance,
        "POST",
        "/v1/setup_intents",
        {"customer": cus, "description": "before", "metadata": {"a": "1"}},
    )["body"]
    updated = call(
        instance,
        "POST",
        f"/v1/setup_intents/{body['id']}",
        {"description": "after", "metadata": {"b": "2"}},
    )["body"]
    assert updated["description"] == "after"
    assert updated["metadata"] == {"a": "1", "b": "2"}
    with_pm = call(instance, "POST", f"/v1/setup_intents/{body['id']}", {"payment_method": pm})[
        "body"
    ]
    assert with_pm["status"] == "requires_confirmation"  # the create rule, applied
    # updates emit no event: there is no setup_intent.updated in the closed set
    assert events_of(instance)[-1] == "setup_intent.created"


def test_a_succeeded_intent_still_takes_description_and_metadata(
    instance: seahaven.Instance,
) -> None:
    cus, pm = customer_with_card(instance)
    ok = call(
        instance,
        "POST",
        "/v1/setup_intents",
        {"customer": cus, "payment_method": pm, "confirm": True},
    )["body"]
    updated = call(
        instance,
        "POST",
        f"/v1/setup_intents/{ok['id']}",
        {"description": "after the fact", "metadata": {"k": "v"}},
    )["body"]
    assert updated["description"] == "after the fact"
    assert updated["metadata"] == {"k": "v"}
    err = _refusal(instance, "POST", f"/v1/setup_intents/{ok['id']}", {"payment_method": pm})
    assert err["code"] == "setup_intent_unexpected_state"
    assert err["message"] == "You cannot update this SetupIntent because it has already succeeded."
    assert err["setup_intent"]["id"] == ok["id"]


def test_update_time_ownership_refusals_carry_the_intent(instance: seahaven.Instance) -> None:
    cus, pm = customer_with_card(instance)
    other = call(instance, "POST", "/v1/customers", {"email": "ou@example.test"})["body"]["id"]
    bare = call(instance, "POST", "/v1/setup_intents")["body"]
    err = _refusal(instance, "POST", f"/v1/setup_intents/{bare['id']}", {"payment_method": pm})
    assert "code" not in err
    assert err["param"] == "payment_method"
    assert err["message"] == (
        f"The payment method supplied ({pm}) belongs to the Customer {cus}. "
        "Please include the Customer in the `customer` parameter on the SetupIntent."
    )
    assert err["setup_intent"]["id"] == bare["id"]
    moved = call(instance, "POST", f"/v1/setup_intents/{bare['id']}", {"customer": other})["body"]
    assert moved["customer"] == other
    err = _refusal(instance, "POST", f"/v1/setup_intents/{bare['id']}", {"payment_method": pm})
    assert err["message"] == (
        f"The PaymentMethod {pm} does not belong to the Customer you supplied {other}. "
        "Please use this PaymentMethod with the Customer that it belongs to instead."
    )


# --- verify_microdeposits and the reads ------------------------------------------------


def test_verify_microdeposits_refuses_the_recorded_shape(instance: seahaven.Instance) -> None:
    body = call(instance, "POST", "/v1/setup_intents")["body"]
    result = call(
        instance,
        "POST",
        f"/v1/setup_intents/{body['id']}/verify_microdeposits",
        {"amounts": [32, 45]},
    )
    assert result["status"] == 400
    err = result["body"]["error"]
    assert err["code"] == "intent_invalid_state"
    assert err["doc_url"] == "https://stripe.com/docs/error-codes/intent-invalid-state"
    assert err["message"] == (
        "This SetupIntent cannot be actioned on because it has a status of "
        "requires_payment_method. Only a SetupIntent with one of the following statuses "
        'may be actioned on: ["requires_action"].'
    )
    assert err["setup_intent"]["id"] == body["id"]


def test_lists_filter_and_the_missing_id_keeps_the_placeholder(
    instance: seahaven.Instance,
) -> None:
    cus, pm = customer_with_card(instance)
    mine = call(instance, "POST", "/v1/setup_intents", {"customer": cus, "payment_method": pm})[
        "body"
    ]
    by_customer = call(instance, "GET", "/v1/setup_intents", {"customer": cus})["body"]
    assert [item["id"] for item in by_customer["data"]] == [mine["id"]]
    assert by_customer["url"] == "/v1/setup_intents"
    by_pm = call(instance, "GET", "/v1/setup_intents", {"payment_method": pm})["body"]
    assert [item["id"] for item in by_pm["data"]] == [mine["id"]]
    missing = call(instance, "GET", "/v1/setup_intents/seti_missing000000000000000")
    assert missing["status"] == 404
    err = missing["body"]["error"]
    assert err["code"] == "resource_missing"
    assert err["param"] == "intent"
    assert err["message"] == "No such setupintent: 'seti_missing000000000000000'"
    # The hand-written paths answer the same one-word spelling (the engine's
    # retrieve reads it off the SPEC; these read it off _re_read).
    canceled = call(instance, "POST", "/v1/setup_intents/seti_missing000000000000000/cancel")
    assert canceled["status"] == 404
    assert (
        canceled["body"]["error"]["message"] == "No such setupintent: 'seti_missing000000000000000'"
    )
    assert canceled["body"]["error"]["param"] == "intent"


def test_expansion_off_a_setup_intent(instance: seahaven.Instance) -> None:
    cus, pm = customer_with_card(instance)
    body = call(
        instance,
        "POST",
        "/v1/setup_intents",
        {"customer": cus, "payment_method": pm},
    )["body"]
    got = call(
        instance,
        "GET",
        f"/v1/setup_intents/{body['id']}",
        {"expand": ["payment_method", "customer"]},
    )["body"]
    assert got["payment_method"]["object"] == "payment_method"
    assert got["payment_method"]["id"] == pm
    assert got["customer"]["object"] == "customer"
    assert got["customer"]["id"] == cus
