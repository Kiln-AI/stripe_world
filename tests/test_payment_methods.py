"""The payment_methods slice, through the real four-tool chain. The card
block, the stubbed-rail shape, the attach/detach semantics and every error
envelope here are pinned by this phase's live probes and cassette 06 at
`2026-08-26.dahlia`; the raw-number path is unit-tested only, because the
recording account refuses raw PANs (declared structural difference)."""

import json

import pytest
import seahaven

from conftest import BLANK_NOW

pytestmark = pytest.mark.seahaven(fixture=None, now=BLANK_NOW)


def call(instance: seahaven.Instance, method: str, path: str, params: dict | None = None):
    if method == "GET":
        return instance.call("stripe_api_read", path=path, params=params)
    return instance.call("stripe_api_write", method=method, path=path, params=params)


def create_card(instance: seahaven.Instance, **card: object) -> dict:
    result = call(instance, "POST", "/v1/payment_methods", {"type": "card", "card": dict(card)})
    assert result["status"] == 200, result
    return result["body"]


def customer(instance: seahaven.Instance) -> str:
    return call(instance, "POST", "/v1/customers", {"email": "pm@example.test"})["body"]["id"]


# --- creation ---------------------------------------------------------------------


def test_token_create_returns_the_full_card_block(instance: seahaven.Instance) -> None:
    pm = create_card(instance, token="tok_visa")
    assert pm["id"].startswith("pm_")
    assert pm["object"] == "payment_method"
    assert pm["type"] == "card"
    assert pm["allow_redisplay"] == "unspecified"
    assert pm["customer"] is None
    assert pm["card"] == {
        "brand": "visa",
        "checks": {
            "address_line1_check": None,
            "address_postal_code_check": None,
            "cvc_check": "unchecked",
        },
        "country": "US",
        "display_brand": "visa",
        "exp_month": 9,
        "exp_year": 2027,
        "fingerprint": pm["card"]["fingerprint"],  # asserted separately below
        "funding": "credit",
        "generated_from": None,
        "last4": "4242",
        "networks": {"available": ["visa"], "preferred": None},
        "regulated_status": "unregulated",
        "three_d_secure_usage": {"supported": True},
        "wallet": None,
    }
    assert pm["billing_details"] == {
        "address": {
            "city": None,
            "country": None,
            "line1": None,
            "line2": None,
            "postal_code": None,
            "state": None,
        },
        "email": None,
        "name": None,
        "phone": None,
        "tax_id": None,
    }


def test_the_same_number_answers_the_same_fingerprint(instance: seahaven.Instance) -> None:
    one = create_card(instance, token="tok_visa")
    two = create_card(instance, number="4242424242424242", exp_month=9, exp_year=2027)
    # The token and its number are the same card, so the fingerprints agree —
    # stability per number is the real API's property (billing/magic_cards).
    assert one["card"]["fingerprint"] == two["card"]["fingerprint"]


def test_raw_number_create_and_magic_families(instance: seahaven.Instance) -> None:
    visa = create_card(instance, number="5555555555554444", exp_month=1, exp_year=2031, cvc="123")
    assert visa["card"]["brand"] == "mastercard"
    assert visa["card"]["last4"] == "4444"
    assert visa["card"]["checks"]["cvc_check"] == "pass"

    declined = create_card(instance, number="4000000000000002", exp_month=1, exp_year=2031)
    assert declined["card"]["brand"] == "visa"
    assert declined["card"]["last4"] == "0002"


def test_a_luhn_invalid_number_is_refused_at_creation(instance: seahaven.Instance) -> None:
    result = call(
        instance,
        "POST",
        "/v1/payment_methods",
        {"type": "card", "card": {"number": "4242424242424241", "exp_month": 1, "exp_year": 2031}},
    )
    assert result["status"] == 402
    error = result["body"]["error"]
    assert error["type"] == "card_error"
    assert error["code"] == "incorrect_number"


def test_a_number_without_expiry_names_the_nested_param(instance: seahaven.Instance) -> None:
    result = call(
        instance,
        "POST",
        "/v1/payment_methods",
        {"type": "card", "card": {"number": "4242424242424242"}},
    )
    assert result["status"] == 400
    error = result["body"]["error"]
    assert error["code"] == "parameter_missing"
    assert error["param"] == "card[exp_month]"


def test_an_unknown_token_is_refused_verbatim(instance: seahaven.Instance) -> None:
    result = call(
        instance,
        "POST",
        "/v1/payment_methods",
        {"type": "card", "card": {"token": "tok_nope"}},
    )
    assert result["status"] == 400
    error = result["body"]["error"]
    assert error["message"] == "Invalid token id: tok_nope"
    assert error["param"] == "token"


def test_the_corrected_3ds_token_spellings_carry_the_tag(instance: seahaven.Instance) -> None:
    """Probed 2026-09-20 at the pinned version: the real spellings are
    `tok_threeDSecure2Required` (…3220) and `tok_threeDSecureRequired`
    (…3063); this table's first `tok_card_threeDSecure*` transcription is not
    a token the live API knows, so it now answers the unknown-token refusal
    like any other miss."""
    required = create_card(instance, token="tok_threeDSecure2Required")
    fresh_3063 = create_card(instance, token="tok_threeDSecureRequired")
    assert required["card"]["last4"] == "3220"
    assert fresh_3063["card"]["last4"] == "3063"

    def tag(pm_id: str) -> str | None:
        row = instance.inspect().one(
            "SELECT x_behavior AS b FROM payment_methods WHERE id = ?", pm_id
        )
        assert row is not None
        return row["b"]

    assert tag(required["id"]) == "three_d_secure=required"
    assert tag(fresh_3063["id"]) == "three_d_secure=required"

    retired = call(
        instance,
        "POST",
        "/v1/payment_methods",
        {"type": "card", "card": {"token": "tok_card_threeDSecure2Required"}},
    )
    assert retired["status"] == 400
    assert retired["body"]["error"]["message"] == (
        "Invalid token id: tok_card_threeDSecure2Required"
    )


def test_the_magic_tag_lands_on_the_row(instance: seahaven.Instance) -> None:
    hard = create_card(instance, token="tok_visa_chargeDeclined")
    attachable = create_card(instance, number="4000000000000341", exp_month=1, exp_year=2031)
    dispute = create_card(instance, token="tok_card_createDispute")
    plain = create_card(instance, token="tok_visa")

    def tag(pm_id: str) -> str | None:
        row = instance.inspect().one(
            "SELECT x_behavior AS b FROM payment_methods WHERE id = ?", pm_id
        )
        assert row is not None
        return row["b"]

    assert (
        tag(hard["id"])
        == "charge_declined=card_declined,decline_code=generic_decline,attach_refused"
    )
    assert tag(attachable["id"]) == "charge_declined=card_declined,decline_code=generic_decline"
    assert tag(dispute["id"]) == "dispute=fraudulent"
    assert tag(plain["id"]) is None


def test_us_bank_account_create_returns_the_full_rail(instance: seahaven.Instance) -> None:
    result = call(
        instance,
        "POST",
        "/v1/payment_methods",
        {
            "type": "us_bank_account",
            "us_bank_account": {
                "account_number": "000123456789",
                "routing_number": "110000000",
            },
            "billing_details": {"name": "Ada", "email": "ada@example.test"},
        },
    )
    assert result["status"] == 200, result
    pm = result["body"]
    assert pm["type"] == "us_bank_account"
    assert pm["us_bank_account"] == {
        "account_holder_type": "individual",
        "account_type": "checking",
        "bank_name": "STRIPE TEST BANK",
        "financial_connections_account": None,
        "fingerprint": pm["us_bank_account"]["fingerprint"],
        "last4": "6789",
        "networks": {"preferred": "ach", "supported": ["ach"]},
        "routing_number": "110000000",
        "status_details": {},
    }
    assert pm["billing_details"]["name"] == "Ada"


def test_a_stubbed_rail_is_an_empty_object_under_its_own_type(
    instance: seahaven.Instance,
) -> None:
    """Recorded on `klarna`: creation needs no rail parameter and the
    response carries exactly `"klarna": {}`."""
    result = call(instance, "POST", "/v1/payment_methods", {"type": "klarna"})
    assert result["status"] == 200, result
    pm = result["body"]
    assert pm["klarna"] == {}
    assert "card" not in pm
    assert "us_bank_account" not in pm


def test_customer_at_creation_is_refused_verbatim(instance: seahaven.Instance) -> None:
    cus = customer(instance)
    result = call(instance, "POST", "/v1/payment_methods", {"type": "klarna", "customer": cus})
    assert result["status"] == 400
    assert result["body"]["error"]["message"] == (
        "You cannot attach a PaymentMethod to a Customer during PaymentMethod creation. "
        "Please instead create the PaymentMethod and then attach it using the attachment "
        "method of the PaymentMethods API."
    )


def test_type_is_required(instance: seahaven.Instance) -> None:
    result = call(instance, "POST", "/v1/payment_methods", {"card": {"token": "tok_visa"}})
    assert result["status"] == 400
    assert result["body"]["error"]["param"] == "type"


# --- update -----------------------------------------------------------------------


def test_update_changes_the_set_leaves_only(instance: seahaven.Instance) -> None:
    pm = create_card(instance, token="tok_visa")
    result = call(
        instance,
        "POST",
        f"/v1/payment_methods/{pm['id']}",
        {
            "allow_redisplay": "limited",
            "billing_details": {"name": "Ada L."},
            "card": {"exp_month": 11, "exp_year": 2032},
            "metadata": {"k": "v"},
        },
    )
    assert result["status"] == 200, result
    body = result["body"]
    assert body["allow_redisplay"] == "limited"
    assert body["billing_details"]["name"] == "Ada L."
    assert body["billing_details"]["email"] is None
    assert body["card"]["exp_month"] == 11
    assert body["card"]["exp_year"] == 2032
    assert body["card"]["last4"] == "4242"  # untouched by the expiry change
    assert body["metadata"] == {"k": "v"}


# --- attach / detach --------------------------------------------------------------


def test_a_nested_billing_address_merges_per_leaf(instance: seahaven.Instance) -> None:
    """The same per-leaf rule the customer's `shipping.address` recorded: a
    partial `billing_details.address` sets its leaves and leaves the stored
    ones alone — it does not replace the address."""
    result = call(
        instance,
        "POST",
        "/v1/payment_methods",
        {
            "type": "card",
            "card": {"token": "tok_visa"},
            "billing_details": {"address": {"line1": "1 Main", "city": "Berlin"}},
        },
    )
    pm = result["body"]
    assert pm["billing_details"]["address"]["line1"] == "1 Main"

    updated = call(
        instance,
        "POST",
        f"/v1/payment_methods/{pm['id']}",
        {"billing_details": {"address": {"city": "Munich"}}},
    )
    address = updated["body"]["billing_details"]["address"]
    assert address == {
        "city": "Munich",
        "country": None,
        "line1": "1 Main",
        "line2": None,
        "postal_code": None,
        "state": None,
    }


def test_attach_and_detach_round_trip_with_events(instance: seahaven.Instance) -> None:
    cus = customer(instance)
    pm = create_card(instance, token="tok_visa")

    attached = call(instance, "POST", f"/v1/payment_methods/{pm['id']}/attach", {"customer": cus})
    assert attached["status"] == 200
    assert attached["body"]["customer"] == cus
    # Attachment verifies the stored card: cvc_check flips to "pass" and
    # stays (recorded in cassette 06 — creation answers "unchecked").
    assert attached["body"]["card"]["checks"]["cvc_check"] == "pass"

    again = call(instance, "POST", f"/v1/payment_methods/{pm['id']}/attach", {"customer": cus})
    assert again["status"] == 200  # idempotent (probed)
    assert again["body"]["card"]["checks"]["cvc_check"] == "pass"

    updated = call(
        instance, "POST", f"/v1/payment_methods/{pm['id']}", {"allow_redisplay": "limited"}
    )
    assert updated["status"] == 200

    detached = call(instance, "POST", f"/v1/payment_methods/{pm['id']}/detach")
    assert detached["status"] == 200
    assert detached["body"]["customer"] is None

    rows = instance.inspect().rows("SELECT type, data FROM events ORDER BY x_seq")
    assert [row["type"] for row in rows] == [
        "customer.created",
        "payment_method.attached",
        "payment_method.updated",
        "payment_method.detached",
    ]
    detached_event = json.loads(rows[3]["data"])
    assert detached_event["previous_attributes"] == {"customer": cus}
    assert json.loads(rows[1]["data"]).get("previous_attributes") is None
    # The updated event's previous_attributes is the changed keys with their
    # prior values — the recorded shape, and the engine's computation.
    assert json.loads(rows[2]["data"])["previous_attributes"] == {"allow_redisplay": "unspecified"}


def test_attach_after_customer_delete_refuses_the_tombstone(
    instance: seahaven.Instance,
) -> None:
    """A request-parameter customer is a live one: attaching after the
    delete answers the same 400 resource_missing the customer filter does
    (recorded in cassette 06 step 24), never a silent 200 onto a tombstone."""
    cus = customer(instance)
    pm = create_card(instance, token="tok_visa")
    instance.call("stripe_api_write", method="DELETE", path=f"/v1/customers/{cus}")

    result = call(instance, "POST", f"/v1/payment_methods/{pm['id']}/attach", {"customer": cus})
    assert result["status"] == 400
    error = result["body"]["error"]
    assert error["code"] == "resource_missing"
    assert error["param"] == "customer"
    assert error["message"] == f"No such customer: '{cus}'"
    # Nothing was written: the method stays unattached and no event fired.
    row = instance.inspect().one("SELECT customer AS c FROM payment_methods WHERE id = ?", pm["id"])
    assert row == {"c": None}
    assert [e["type"] for e in instance.inspect().rows("SELECT type FROM events")] == [
        "customer.created",
        "customer.deleted",
    ]


def test_attach_errors_are_the_probed_envelopes(instance: seahaven.Instance) -> None:
    pm = create_card(instance, token="tok_visa")

    missing = call(instance, "POST", f"/v1/payment_methods/{pm['id']}/attach")
    assert missing["status"] == 400
    error = missing["body"]["error"]
    assert error["message"] == "Must provide customer or customer_account."
    assert error["code"] == "parameter_missing"
    assert "param" not in error

    unknown = call(
        instance, "POST", f"/v1/payment_methods/{pm['id']}/attach", {"customer": "cus_nope"}
    )
    assert unknown["status"] == 400  # a request parameter, not a path id (probed)
    assert unknown["body"]["error"]["code"] == "resource_missing"
    assert unknown["body"]["error"]["param"] == "customer"

    bogus = call(
        instance, "POST", f"/v1/payment_methods/{pm['id']}/attach", {"customer": "ch_nope"}
    )
    assert bogus["status"] == 400
    assert bogus["body"]["error"]["code"] == "resource_missing"


def test_attaching_a_decline_card_is_a_402_that_writes_nothing(
    instance: seahaven.Instance,
) -> None:
    cus = customer(instance)
    hard = create_card(instance, token="tok_visa_chargeDeclined")
    attachable = create_card(instance, number="4000000000000341", exp_month=1, exp_year=2031)

    refused = call(instance, "POST", f"/v1/payment_methods/{hard['id']}/attach", {"customer": cus})
    assert refused["status"] == 402
    error = refused["body"]["error"]
    assert error["type"] == "card_error"
    assert error["code"] == "card_declined"
    assert error["decline_code"] == "generic_decline"
    assert error["message"] == "Your card was declined."
    # The refusal left the method unattached and emitted nothing.
    row = instance.inspect().one(
        "SELECT customer AS c, x_behavior AS b FROM payment_methods WHERE id = ?", hard["id"]
    )
    assert row == {
        "c": None,
        "b": "charge_declined=card_declined,decline_code=generic_decline,attach_refused",
    }
    assert [row["type"] for row in instance.inspect().rows("SELECT type FROM events")] == [
        "customer.created"
    ]
    assert "param" not in error  # the form-boundary artifact is not emitted
    assert "advice_code" not in error
    assert "network_decline_code" not in error

    ok = call(instance, "POST", f"/v1/payment_methods/{attachable['id']}/attach", {"customer": cus})
    assert ok["status"] == 200  # the attachable decline attaches (probed)


def test_detach_of_an_unattached_method_is_refused_verbatim(
    instance: seahaven.Instance,
) -> None:
    pm = create_card(instance, token="tok_visa")
    result = call(instance, "POST", f"/v1/payment_methods/{pm['id']}/detach")
    assert result["status"] == 400
    error = result["body"]["error"]
    assert error["message"] == (
        "The payment method you provided is not attached to a customer so detachment is impossible."
    )
    assert "code" not in error
    assert "param" not in error


# --- lists ------------------------------------------------------------------------


def test_the_top_level_list_needs_a_customer_and_checks_it_exists(
    instance: seahaven.Instance,
) -> None:
    cus = customer(instance)
    pm = create_card(instance, token="tok_visa")
    call(instance, "POST", f"/v1/payment_methods/{pm['id']}/attach", {"customer": cus})

    unfiltered = call(instance, "GET", "/v1/payment_methods")
    assert unfiltered["status"] == 200
    assert unfiltered["body"]["data"] == []  # no customer filter -> empty (probed)

    filtered = call(instance, "GET", "/v1/payment_methods", {"customer": cus})
    assert [item["id"] for item in filtered["body"]["data"]] == [pm["id"]]

    typed = call(instance, "GET", "/v1/payment_methods", {"customer": cus, "type": "card"})
    assert [item["id"] for item in typed["body"]["data"]] == [pm["id"]]
    other_type = call(
        instance, "GET", "/v1/payment_methods", {"customer": cus, "type": "us_bank_account"}
    )
    assert other_type["body"]["data"] == []

    missing = call(instance, "GET", "/v1/payment_methods", {"customer": "cus_nope"})
    assert missing["status"] == 400
    assert missing["body"]["error"]["code"] == "resource_missing"

    deleted = customer(instance)
    call(instance, "DELETE", f"/v1/customers/{deleted}")
    gone = call(instance, "GET", "/v1/payment_methods", {"customer": deleted})
    assert gone["status"] == 400  # a tombstoned customer is as missing (probed)


def test_the_scoped_list_and_retrieve(instance: seahaven.Instance) -> None:
    cus = customer(instance)
    other = customer(instance)
    pm = create_card(instance, token="tok_visa")
    call(instance, "POST", f"/v1/payment_methods/{pm['id']}/attach", {"customer": cus})

    listed = call(instance, "GET", f"/v1/customers/{cus}/payment_methods")
    assert listed["status"] == 200
    assert listed["body"]["url"] == f"/v1/customers/{cus}/payment_methods"
    assert [item["id"] for item in listed["body"]["data"]] == [pm["id"]]
    empty = call(instance, "GET", f"/v1/customers/{other}/payment_methods")
    assert empty["body"]["data"] == []

    retrieved = call(instance, "GET", f"/v1/customers/{cus}/payment_methods/{pm['id']}")
    assert retrieved["status"] == 200
    assert retrieved["body"]["id"] == pm["id"]

    # An existing method that is not this customer's: the recorded
    # message-only 404 naming the customer placeholder.
    mismatch = call(instance, "GET", f"/v1/customers/{other}/payment_methods/{pm['id']}")
    assert mismatch["status"] == 404
    error = mismatch["body"]["error"]
    assert error["message"] == "Invalid request"
    assert error["param"] == "customer"
    assert "code" not in error

    nonexistent = call(instance, "GET", f"/v1/customers/{cus}/payment_methods/pm_nope")
    assert nonexistent["status"] == 404
    assert nonexistent["body"]["error"]["code"] == "resource_missing"
    assert nonexistent["body"]["error"]["param"] == "payment_method"

    # A bad customer on this path names `param: "id"` (probed), unlike the
    # balance_transactions path which keeps the placeholder.
    no_customer = call(instance, "GET", "/v1/customers/cus_nope/payment_methods")
    assert no_customer["status"] == 404
    assert no_customer["body"]["error"]["param"] == "id"


def test_retrieve_expands_the_customer(instance: seahaven.Instance) -> None:
    cus = customer(instance)
    pm = create_card(instance, token="tok_visa")
    call(instance, "POST", f"/v1/payment_methods/{pm['id']}/attach", {"customer": cus})
    result = call(instance, "GET", f"/v1/payment_methods/{pm['id']}", {"expand": ["customer"]})
    assert result["status"] == 200
    assert result["body"]["customer"]["id"] == cus
    assert result["body"]["customer"]["object"] == "customer"


def test_no_payment_method_created_event_is_ever_emitted(instance: seahaven.Instance) -> None:
    """The closed set has attached / automatically_updated / detached /
    updated only — real Stripe emits nothing on creation."""
    create_card(instance, token="tok_visa")
    types = [row["type"] for row in instance.inspect().rows("SELECT type FROM events")]
    assert types == []


def test_a_missing_path_id_names_the_placeholder_at_404(instance: seahaven.Instance) -> None:
    result = call(instance, "GET", "/v1/payment_methods/pm_nope")
    assert result["status"] == 404
    error = result["body"]["error"]
    assert error["code"] == "resource_missing"
    assert error["param"] == "payment_method"
    assert error["message"] == "No such PaymentMethod: 'pm_nope'"
