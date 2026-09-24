"""The customers slice, through the real four-tool chain. Every asserted
default and nested shape is pinned by cassette 02 / this phase's live probes
at `2026-08-26.dahlia`; the throwaway-phase defaults are asserted here too,
so widening the body cannot quietly change them."""

import pytest
import seahaven

from conftest import BLANK_NOW, api_write
from seahaven_stripe_world.errors import StripeToolError

pytestmark = pytest.mark.seahaven(fixture=None, now=BLANK_NOW, clock_mode="fixed")


def create(instance: seahaven.Instance, **params: object) -> dict:
    return api_write(instance, "POST", "/v1/customers", params)


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
    no_op = api_write(
        instance,
        "POST",
        f"/v1/customers/{cus['id']}",
        {"shipping": {}},
    )
    assert no_op["shipping"] == cus["shipping"]

    one_leaf = api_write(
        instance,
        "POST",
        f"/v1/customers/{cus['id']}",
        {"shipping": {"tracking_number": "TRACK1"}},
    )
    assert one_leaf["shipping"]["carrier"] == "DHL"
    assert one_leaf["shipping"]["tracking_number"] == "TRACK1"

    address_leaf = api_write(
        instance,
        "POST",
        f"/v1/customers/{cus['id']}",
        {"address": {"country": "DE"}},
    )
    assert address_leaf["address"] == {**null_address, "country": "DE"}


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

    updated = api_write(
        instance,
        "POST",
        f"/v1/customers/{create(instance)['id']}",
        {"balance": -250},
    )
    assert updated["balance"] == -250
    assert updated["currency"] == "usd"


def test_currency_is_not_a_settable_parameter(instance: seahaven.Instance) -> None:
    with pytest.raises(StripeToolError) as exc_info:
        api_write(
            instance,
            "POST",
            "/v1/customers",
            {"currency": "usd"},
        )
    assert exc_info.value.status == 400
    assert exc_info.value.stripe_body["error"]["code"] == "parameter_unknown"


def test_invoice_settings_update_keeps_unset_keys(instance: seahaven.Instance) -> None:
    cus = create(instance)
    updated = api_write(
        instance,
        "POST",
        f"/v1/customers/{cus['id']}",
        {"invoice_settings": {"footer": "Thanks"}},
    )
    assert updated["invoice_settings"] == {
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

    with pytest.raises(StripeToolError) as exc_info:
        api_write(
            instance,
            "POST",
            "/v1/customers",
            {"invoice_prefix": "TAKEN1"},
        )
    assert exc_info.value.status == 400
    assert exc_info.value.stripe_body["error"] == {
        "type": "invalid_request_error",
        "param": "invoice_prefix",
        "message": f"This invoice number prefix is taken by customer: {holder['id']}. "
        "Please enter a different prefix.",
    }

    other = create(instance)
    with pytest.raises(StripeToolError) as exc_info:
        api_write(
            instance,
            "POST",
            f"/v1/customers/{other['id']}",
            {"invoice_prefix": "TAKEN1"},
        )
    assert exc_info.value.status == 400
    assert exc_info.value.stripe_body["error"]["param"] == "invoice_prefix"

    # Re-sending own prefix is not a conflict.
    own_again = api_write(
        instance,
        "POST",
        f"/v1/customers/{holder['id']}",
        {"invoice_prefix": "TAKEN1"},
    )
    assert own_again["invoice_prefix"] == "TAKEN1"

    api_write(instance, "DELETE", f"/v1/customers/{holder['id']}")
    with pytest.raises(StripeToolError) as exc_info:
        api_write(
            instance,
            "POST",
            "/v1/customers",
            {"invoice_prefix": "TAKEN1"},
        )
    assert exc_info.value.status == 400  # the tombstone still holds the prefix


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
        with pytest.raises(StripeToolError) as exc_info:
            api_write(
                instance,
                "POST",
                "/v1/customers",
                {"invoice_prefix": bad},
            )
        assert exc_info.value.status == 400, bad
        assert exc_info.value.stripe_body["error"] == {
            "type": "invalid_request_error",
            # The en dash is Stripe's own, probed verbatim.
            "message": "Invoice number prefix must be 1–12 uppercase letters or numbers.",  # noqa: RUF001
        }, bad

    # The same contract holds on update.
    cus = created["id"]
    with pytest.raises(StripeToolError):
        api_write(
            instance,
            "POST",
            f"/v1/customers/{cus}",
            {"invoice_prefix": "x"},
        )
    changed = api_write(
        instance,
        "POST",
        f"/v1/customers/{cus}",
        {"invoice_prefix": "ZZ9"},
    )
    assert changed["invoice_prefix"] == "ZZ9"


def test_scope_cut_parameters_are_refused(instance: seahaven.Instance) -> None:
    """The dahlia body minus the scope-boundary cuts: sending a cut parameter
    is `parameter_unknown`, the closed-set contract (architecture.md §3.3)."""
    for cut in ("cash_balance", "tax", "tax_id_data", "test_clock", "source"):
        with pytest.raises(StripeToolError) as exc_info:
            api_write(instance, "POST", "/v1/customers", {cut: {}})
        assert exc_info.value.status == 400, cut
        assert exc_info.value.stripe_body["error"]["code"] == "parameter_unknown", cut


def test_customer_deleted_event_is_the_full_pre_delete_object(
    instance: seahaven.Instance,
) -> None:
    """The carried Phase 3 question, settled by recording: `customer.deleted`
    carries the FULL object with no `deleted` key, and the event is emitted
    after the write (the cross-cutting ordering rule; the snapshot cannot
    distinguish, since `deleted` is not serialized)."""
    import json

    cus = create(instance, name="Last State", metadata={"k": "v"})
    api_write(instance, "POST", f"/v1/customers/{cus['id']}", {"name": "X"})
    deleted = api_write(instance, "DELETE", f"/v1/customers/{cus['id']}")
    assert deleted == {"id": cus["id"], "object": "customer", "deleted": True}

    rows = instance.inspect().rows("SELECT type, data FROM events ORDER BY x_seq")
    deleted_rows = [row for row in rows if row["type"] == "customer.deleted"]
    assert len(deleted_rows) == 1
    data = json.loads(deleted_rows[0]["data"])
    obj = data["object"]
    assert "deleted" not in obj
    assert obj["name"] == "X"  # the state as it stood, after the update
    assert obj["metadata"] == {"k": "v"}
    assert "previous_attributes" not in data
