"""The customers slice, through the real four-tool chain. Every asserted
default and nested shape is pinned by cassette 02 / this phase's live probes
at `2026-08-26.dahlia`; the throwaway-phase defaults are asserted here too,
so widening the body cannot quietly change them."""

import pytest
import seahaven

from conftest import BLANK_NOW

pytestmark = pytest.mark.seahaven(fixture=None, now=BLANK_NOW)


def create(instance: seahaven.Instance, **params: object) -> dict:
    result = instance.call("stripe_api_write", method="POST", path="/v1/customers", params=params)
    assert result["status"] == 200, result
    return result["body"]


def test_nested_objects_serialize_canonically(instance: seahaven.Instance) -> None:
    """A provided address/shipping carries every key, missing leaves null —
    the recorded shape (probed: partial input, full output)."""
    created = create(
        instance,
        address={"line1": "1 Main", "city": "Berlin"},
        shipping={"name": "Ada", "address": {"line1": "1 Main"}},
        preferred_locales=["en-US", "de-DE"],
    )
    assert created["address"] == {
        "city": "Berlin",
        "country": None,
        "line1": "1 Main",
        "line2": None,
        "postal_code": None,
        "state": None,
    }
    # carrier / tracking_number stay absent until set — recorded, despite the
    # schema marking both nullable.
    assert created["shipping"] == {
        "address": {
            "city": None,
            "country": None,
            "line1": "1 Main",
            "line2": None,
            "postal_code": None,
            "state": None,
        },
        "name": "Ada",
        "phone": None,
    }
    assert created["preferred_locales"] == ["en-US", "de-DE"]


def test_nested_update_merges_per_leaf(instance: seahaven.Instance) -> None:
    """`shipping: {}` is a no-op and a single leaf sets only itself (probed).
    A present shipping always carries a full address object — the spec types
    `shipping.address` non-null and the recording agrees."""
    null_address = {
        "city": None,
        "country": None,
        "line1": None,
        "line2": None,
        "postal_code": None,
        "state": None,
    }
    cus = create(instance, shipping={"name": "Ada", "carrier": "DHL"}, phone="123")
    assert cus["shipping"] == {
        "address": dict(null_address),
        "name": "Ada",
        "phone": None,
        "carrier": "DHL",
    }
    no_op = instance.call(
        "stripe_api_write",
        method="POST",
        path=f"/v1/customers/{cus['id']}",
        params={"shipping": {}},
    )
    assert no_op["status"] == 200
    assert no_op["body"]["shipping"] == cus["shipping"]

    one_leaf = instance.call(
        "stripe_api_write",
        method="POST",
        path=f"/v1/customers/{cus['id']}",
        params={"shipping": {"tracking_number": "TRACK1"}},
    )
    assert one_leaf["body"]["shipping"]["carrier"] == "DHL"
    assert one_leaf["body"]["shipping"]["tracking_number"] == "TRACK1"

    address_leaf = instance.call(
        "stripe_api_write",
        method="POST",
        path=f"/v1/customers/{cus['id']}",
        params={"address": {"country": "DE"}},
    )
    assert address_leaf["body"]["address"] == {**null_address, "country": "DE"}


def test_a_nonzero_balance_stamps_the_account_default_currency(
    instance: seahaven.Instance,
) -> None:
    """Probed: balance=0 leaves currency null; a nonzero balance stamps the
    account default (the recording account's is 'cad'; this world's is 'usd'
    — declared difference, see customers.ACCOUNT_DEFAULT_CURRENCY)."""
    assert create(instance, balance=0)["currency"] is None
    assert create(instance)["currency"] is None
    stamped = create(instance, balance=500)
    assert stamped["balance"] == 500
    assert stamped["currency"] == "usd"

    updated = instance.call(
        "stripe_api_write",
        method="POST",
        path=f"/v1/customers/{create(instance)['id']}",
        params={"balance": -250},
    )
    assert updated["body"]["balance"] == -250
    assert updated["body"]["currency"] == "usd"


def test_currency_is_not_a_settable_parameter(instance: seahaven.Instance) -> None:
    result = instance.call(
        "stripe_api_write",
        method="POST",
        path="/v1/customers",
        params={"currency": "usd"},
    )
    assert result["status"] == 400
    assert result["body"]["error"]["code"] == "parameter_unknown"


def test_invoice_settings_update_keeps_unset_keys(instance: seahaven.Instance) -> None:
    cus = create(instance)
    updated = instance.call(
        "stripe_api_write",
        method="POST",
        path=f"/v1/customers/{cus['id']}",
        params={"invoice_settings": {"footer": "Thanks"}},
    )
    assert updated["body"]["invoice_settings"] == {
        "custom_fields": None,
        "default_payment_method": None,
        "footer": "Thanks",
        "rendering_options": None,
    }


def test_invoice_prefix_unique(instance: seahaven.Instance) -> None:
    """A taken prefix is refused with Stripe's 400 envelope, never the raw
    constraint error the UNIQUE index would raise (probed on create and on
    update, cassette `probe_invoice_prefix`; the message names the holder).
    A customer re-sending its own prefix is not a conflict, and a deleted
    customer keeps its prefix reserved — the tombstone still holds it."""
    holder = create(instance, invoice_prefix="TAKEN1")

    duplicate = instance.call(
        "stripe_api_write", method="POST", path="/v1/customers", params={"invoice_prefix": "TAKEN1"}
    )
    assert duplicate["status"] == 400
    assert duplicate["body"]["error"] == {
        "type": "invalid_request_error",
        "param": "invoice_prefix",
        "message": f"This invoice number prefix is taken by customer: {holder['id']}. "
        "Please enter a different prefix.",
    }

    other = create(instance)
    onto_existing = instance.call(
        "stripe_api_write",
        method="POST",
        path=f"/v1/customers/{other['id']}",
        params={"invoice_prefix": "TAKEN1"},
    )
    assert onto_existing["status"] == 400
    assert onto_existing["body"]["error"]["param"] == "invoice_prefix"

    own_again = instance.call(
        "stripe_api_write",
        method="POST",
        path=f"/v1/customers/{holder['id']}",
        params={"invoice_prefix": "TAKEN1"},
    )
    assert own_again["status"] == 200

    instance.call("stripe_api_write", method="DELETE", path=f"/v1/customers/{holder['id']}")
    after_delete = instance.call(
        "stripe_api_write", method="POST", path="/v1/customers", params={"invoice_prefix": "TAKEN1"}
    )
    assert after_delete["status"] == 400  # the tombstone still holds the prefix


def test_a_minted_prefix_collision_re_mints(probe, monkeypatch) -> None:
    """The remote half of the uniqueness fix: a minted prefix that lands on a
    taken one re-mints instead of crashing on the UNIQUE index. Forcing a
    genuine collision through the seeded stream is impractical, so the mint
    function is stubbed to collide twice first."""
    from conftest import BLANK_NOW as NOW
    from seahaven_stripe_world.resources import customers

    draws = iter(["COLLIDE", "COLLIDE", "FREEONE"])
    monkeypatch.setattr(customers, "_mint_prefix", lambda ctx: next(draws))
    world = probe()
    with world.instance(None, now=NOW) as instance, instance.bulk() as ctx:
        ctx.db.execute(
            "INSERT INTO customers (id, x_seq, created, deleted, balance, invoice_prefix,"
            " invoice_settings) VALUES ('cus_holder', 1, ?, 1, 0, 'COLLIDE', '{}')",
            NOW,
        )
        assert customers._mint_untaken_prefix(ctx) == "FREEONE"
        assert (
            ctx.db.one("SELECT id FROM customers WHERE invoice_prefix = 'FREEONE'") is None
        )  # the winner was checked against the table, not assumed


def test_invoice_prefix_contract_and_create_time_round_trip(
    instance: seahaven.Instance,
) -> None:
    """A caller-supplied `invoice_prefix` round-trips and
    `next_invoice_sequence` is honored at creation; every violation of the
    prefix contract refuses with the one bespoke message, verbatim (probed:
    lowercase, punctuation, empty-ish and 13 characters all refuse
    identically, with no `code` and no `param`)."""
    created = create(instance, invoice_prefix="AB1", next_invoice_sequence=42)
    assert created["invoice_prefix"] == "AB1"
    assert created["next_invoice_sequence"] == 42
    assert create(instance, invoice_prefix="A")["invoice_prefix"] == "A"
    assert create(instance, invoice_prefix="ABCDEFGHIJKL")["invoice_prefix"] == "ABCDEFGHIJKL"

    for bad in ("a", "abc", "AB_1", "ABCDEFGHIJKLM", "lower"):
        result = instance.call(
            "stripe_api_write", method="POST", path="/v1/customers", params={"invoice_prefix": bad}
        )
        assert result["status"] == 400, bad
        error = result["body"]["error"]
        assert error == {
            "type": "invalid_request_error",
            # The en dash is Stripe's own, probed verbatim.
            "message": "Invoice number prefix must be 1–12 uppercase letters or numbers.",  # noqa: RUF001
        }, bad

    # The same contract holds on update.
    cus = created["id"]
    refused = instance.call(
        "stripe_api_write",
        method="POST",
        path=f"/v1/customers/{cus}",
        params={"invoice_prefix": "x"},
    )
    assert refused["status"] == 400
    changed = instance.call(
        "stripe_api_write",
        method="POST",
        path=f"/v1/customers/{cus}",
        params={"invoice_prefix": "ZZ9"},
    )
    assert changed["body"]["invoice_prefix"] == "ZZ9"


def test_scope_cut_parameters_are_refused(instance: seahaven.Instance) -> None:
    """The dahlia body minus the scope-boundary cuts: sending a cut parameter
    is `parameter_unknown`, the closed-set contract (architecture.md §3.3)."""
    for cut in ("cash_balance", "tax", "tax_id_data", "test_clock", "source"):
        result = instance.call(
            "stripe_api_write", method="POST", path="/v1/customers", params={cut: {}}
        )
        assert result["status"] == 400, cut
        assert result["body"]["error"]["code"] == "parameter_unknown", cut


def test_customer_deleted_event_is_the_full_pre_delete_object(
    instance: seahaven.Instance,
) -> None:
    """The carried Phase 3 question, settled by recording: `customer.deleted`
    carries the FULL object with no `deleted` key, and the event is emitted
    after the write (the cross-cutting ordering rule; the snapshot cannot
    distinguish, since `deleted` is not serialized)."""
    import json

    cus = create(instance, name="Last State", metadata={"k": "v"})
    instance.call(
        "stripe_api_write", method="POST", path=f"/v1/customers/{cus['id']}", params={"name": "X"}
    )
    deleted = instance.call("stripe_api_write", method="DELETE", path=f"/v1/customers/{cus['id']}")
    assert deleted["body"] == {"id": cus["id"], "object": "customer", "deleted": True}

    rows = instance.inspect().rows("SELECT type, data FROM events ORDER BY x_seq")
    deleted_rows = [row for row in rows if row["type"] == "customer.deleted"]
    assert len(deleted_rows) == 1
    data = json.loads(deleted_rows[0]["data"])
    obj = data["object"]
    assert "deleted" not in obj
    assert obj["name"] == "X"  # the state as it stood, after the update
    assert obj["metadata"] == {"k": "v"}
    assert "previous_attributes" not in data
