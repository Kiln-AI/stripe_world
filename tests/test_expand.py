"""The expansion resolver's error forms — every one of them pinned by this
phase's live probes at `2026-08-26.dahlia`, correcting
`components/cross_cutting.md` §3.3.5's table where the probes disagreed."""

from typing import Any, cast

import pytest
import seahaven

from conftest import BLANK_NOW, api_read, api_write
from seahaven_stripe_world.errors import StripeToolError

pytestmark = pytest.mark.seahaven(fixture=None, now=BLANK_NOW)


def expand_on(instance: seahaven.Instance, path: str, *paths: str) -> dict:
    return api_read(instance, path, {"expand": list(paths)})


def created(instance: seahaven.Instance) -> str:
    return api_write(instance, "POST", "/v1/customers", {})["id"]


def test_nonexistent_field_is_the_plain_form(instance: seahaven.Instance) -> None:
    """Probed: `bogus_field_probe` — the `because it doesn't exist` variant of
    the component design does not fire for a top-level field."""
    with pytest.raises(StripeToolError) as exc_info:
        expand_on(instance, f"/v1/customers/{created(instance)}", "bogus_field_probe")
    assert exc_info.value.status == 400
    error = exc_info.value.stripe_body["error"]
    assert error == {
        "type": "invalid_request_error",
        "message": "This property cannot be expanded (bogus_field_probe).",
    }


def test_exists_but_unexpandable_is_the_same_plain_form(instance: seahaven.Instance) -> None:
    """Probed: `description_probe` — indistinguishable from a nonexistent
    field, which is why the `exists=` selection never fires here."""
    with pytest.raises(StripeToolError) as exc_info:
        expand_on(instance, f"/v1/customers/{created(instance)}", "description")
    assert exc_info.value.status == 400
    assert (
        exc_info.value.stripe_body["error"]["message"]
        == "This property cannot be expanded (description)."
    )


def test_a_bad_nested_segment_carries_the_whole_path(instance: seahaven.Instance) -> None:
    """Probed: `(invoice_settings.bogus)` — the token is the path, not the
    lone segment the component design describes."""
    with pytest.raises(StripeToolError) as exc_info:
        expand_on(instance, f"/v1/customers/{created(instance)}", "invoice_settings.bogus")
    assert exc_info.value.status == 400
    assert exc_info.value.stripe_body["error"]["message"] == (
        "This property cannot be expanded (invoice_settings.bogus)."
    )


def test_an_embedded_expandable_field_is_accepted(instance: seahaven.Instance) -> None:
    """Probed: `expand[]=address` returns 200 — an object in
    `x-expandableFields` is traversed, never inflated, because it is already
    the object."""
    result = expand_on(instance, f"/v1/customers/{created(instance)}", "address")
    assert result["address"] is None


def test_a_null_reference_stays_null(instance: seahaven.Instance) -> None:
    """Probed: `invoice_settings.default_payment_method` on a customer with
    none — accepted, and the field stays null rather than erroring."""
    result = expand_on(
        instance,
        f"/v1/customers/{created(instance)}",
        "invoice_settings.default_payment_method",
    )
    assert result["invoice_settings"]["default_payment_method"] is None


def test_a_list_without_the_data_prefix_gets_the_hint(instance: seahaven.Instance) -> None:
    """Probed with `email` and with a bogus name: any bare first segment on a
    list endpoint gets the redirecting hint."""
    created(instance)
    for segment in ("email", "bogus_list_probe"):
        with pytest.raises(StripeToolError) as exc_info:
            expand_on(instance, "/v1/customers", segment)
        assert exc_info.value.status == 400
        assert exc_info.value.stripe_body["error"]["message"] == (
            f"This property cannot be expanded ({segment}). "
            f"You may want to try expanding 'data.{segment}' instead."
        )


def test_expand_data_alone_is_accepted(instance: seahaven.Instance) -> None:
    """Probed: `expand[]=data` on a list is a 200, not the `(data)` error the
    component design describes."""
    created(instance)
    result = expand_on(instance, "/v1/customers", "data")
    assert len(result["data"]) == 1


def test_a_bad_path_outranks_an_empty_filtered_page(instance: seahaven.Instance) -> None:
    """The empty-list-page case validation used to miss: with a filter that
    matches nothing, the live API still answers the 400 hint form, because
    path validation is static rather than payload-coupled (probed live this
    round)."""
    created(instance)
    with pytest.raises(StripeToolError) as exc_info:
        api_read(
            instance,
            "/v1/customers",
            {"email": "no-match@example.test", "expand": ["bogus"]},
        )
    assert exc_info.value.status == 400
    assert exc_info.value.stripe_body["error"]["message"] == (
        "This property cannot be expanded (bogus). "
        "You may want to try expanding 'data.bogus' instead."
    )


def test_more_than_four_segments_is_rejected(instance: seahaven.Instance) -> None:
    with pytest.raises(StripeToolError) as exc_info:
        expand_on(instance, f"/v1/customers/{created(instance)}", "a.b.c.d.e")
    assert exc_info.value.status == 400
    assert exc_info.value.stripe_body["error"]["type"] == "invalid_request_error"


def test_paths_merge_into_one_trie() -> None:
    from seahaven_stripe_world.serialize.expand import validate_paths as validate

    trie = validate(
        ("invoice_settings.default_payment_method", "discount"),
        object_name="customer",
        is_list=False,
    )
    assert set(trie) == {"invoice_settings", "discount"}
    assert set(trie["invoice_settings"].children) == {"default_payment_method"}


def test_a_pruned_target_reference_is_a_no_op(instance: seahaven.Instance) -> None:
    """`default_source`'s union has no `$ref` left after the scope prune, so
    its edge is a terminal no-op: the id stays as-is rather than crashing the
    resolver."""
    result = expand_on(instance, f"/v1/customers/{created(instance)}", "default_source")
    assert result["default_source"] is None


def test_expansion_off_a_setup_intent(instance: seahaven.Instance) -> None:
    """The setup-intent slice's reference edges (Phase 10): `payment_method`
    and `customer` inflate through the registered resources, and the
    `setatt_` attempt stub validates but inflates nothing (a pruned
    target)."""
    cus = created(instance)
    pm = api_write(
        instance,
        "POST",
        "/v1/payment_methods",
        {"type": "card", "card": {"token": "tok_visa"}},
    )["id"]
    api_write(
        instance,
        "POST",
        f"/v1/payment_methods/{pm}/attach",
        {"customer": cus},
    )
    seti = api_write(
        instance,
        "POST",
        "/v1/setup_intents",
        {"customer": cus, "payment_method": pm, "confirm": True},
    )
    result = expand_on(
        instance,
        f"/v1/setup_intents/{seti['id']}",
        "payment_method",
        "customer",
        "latest_attempt",
    )
    assert result["payment_method"]["id"] == pm
    assert result["payment_method"]["customer"] == cus
    assert result["customer"]["id"] == cus
    # the stub never resolves: the minted id stays a bare string
    assert result["latest_attempt"] == seti["latest_attempt"]


def test_a_dangling_reference_is_a_world_bug() -> None:
    """The engine emitted an id from a row it just read; a missing target row
    is a schema or write bug, not something an agent did
    (`components/cross_cutting.md` §3.3.4)."""
    import seahaven_stripe_world.dispatch.resource as resource
    import seahaven_stripe_world.serialize.expand as expand

    class FakeCtx:
        class db:
            @staticmethod
            def rows(sql, *params):
                return []

    edge = expand._Edge(kind="reference", target="customer", children={})
    ctx = cast(Any, FakeCtx())
    with pytest.raises(seahaven.WorldBug, match="dangling"):
        expand._fetch(ctx, resource.BY_OBJECT, edge, ["cus_missing"])


# --- missing tests from §5.3 ------------------------------------------------------------------


def test_unexpanded_reference_is_a_bare_id(instance: seahaven.Instance) -> None:
    """Without `expand[]`, a reference field is a bare id string."""
    cus = created(instance)
    pi = api_write(
        instance,
        "POST",
        "/v1/payment_intents",
        {"amount": 1000, "currency": "usd", "customer": cus},
    )
    assert pi["customer"] == cus
    assert isinstance(pi["customer"], str)


def test_single_hop(instance: seahaven.Instance) -> None:
    """`expand[]=customer` on a payment intent inflates the customer."""
    cus = created(instance)
    pi = api_write(
        instance,
        "POST",
        "/v1/payment_intents",
        {"amount": 1000, "currency": "usd", "customer": cus},
    )
    result = expand_on(instance, f"/v1/payment_intents/{pi['id']}", "customer")
    assert isinstance(result["customer"], dict)
    assert result["customer"]["id"] == cus
    assert result["customer"]["object"] == "customer"


def test_recursive_hop(instance: seahaven.Instance) -> None:
    """`payment_intent.customer` expands through two references."""
    cus = created(instance)
    pm = api_write(
        instance,
        "POST",
        "/v1/payment_methods",
        {"type": "card", "card": {"token": "tok_visa"}},
    )["id"]
    api_write(
        instance,
        "POST",
        f"/v1/payment_methods/{pm}/attach",
        {"customer": cus},
    )
    pi = api_write(
        instance,
        "POST",
        "/v1/payment_intents",
        {
            "amount": 1000,
            "currency": "usd",
            "customer": cus,
            "payment_method": pm,
            "confirm": True,
        },
    )
    ch_id = pi["latest_charge"]
    result = expand_on(instance, f"/v1/charges/{ch_id}", "payment_intent", "customer")
    assert isinstance(result["payment_intent"], dict)
    assert isinstance(result["customer"], dict)
    assert result["customer"]["id"] == cus


def test_four_segments_allowed_on_a_list(instance: seahaven.Instance) -> None:
    """The quoted deepest example: `data.payment_intent.customer.default_source`
    on a charges list (§3.3.3)."""
    cus = created(instance)
    pm = api_write(
        instance,
        "POST",
        "/v1/payment_methods",
        {"type": "card", "card": {"token": "tok_visa"}},
    )["id"]
    api_write(
        instance,
        "POST",
        f"/v1/payment_methods/{pm}/attach",
        {"customer": cus},
    )
    api_write(
        instance,
        "POST",
        "/v1/payment_intents",
        {
            "amount": 500,
            "currency": "usd",
            "customer": cus,
            "payment_method": pm,
            "confirm": True,
        },
    )
    result = api_read(
        instance,
        "/v1/charges",
        {"expand": ["data.payment_intent.customer.default_source"]},
    )
    charge = result["data"][0]
    assert isinstance(charge["payment_intent"], dict)
    assert isinstance(charge["payment_intent"]["customer"], dict)


def test_nested_list_needs_data(instance: seahaven.Instance) -> None:
    """A nested list envelope requires `data.` before the child field:
    `lines.discounts` errors because `discounts` is not `data`, while
    `lines.data.discounts` passes validation."""
    from seahaven_stripe_world.serialize.expand import validate_paths
    from seahaven_stripe_world.stripe_errors import StripeApiError

    # Without `data.`, the path is rejected at validation
    with pytest.raises(StripeApiError, match="cannot be expanded"):
        validate_paths(("lines.discounts",), object_name="invoice", is_list=False)

    # With `data.`, the path is accepted
    trie = validate_paths(("lines.data.discounts",), object_name="invoice", is_list=False)
    assert "lines" in trie


def test_expand_on_create(instance: seahaven.Instance) -> None:
    """POST with `expand[]` sees the just-created object (§3.3.1)."""
    result = api_write(
        instance,
        "POST",
        "/v1/customers",
        {"expand": ["default_source"]},
    )
    assert result["default_source"] is None
    assert result["object"] == "customer"


def test_bad_path_on_create_writes_nothing(instance: seahaven.Instance) -> None:
    """A bad expand path on a POST creates nothing: static validation
    precedes the handler (§3.3.1)."""
    with pytest.raises(StripeToolError) as exc_info:
        api_write(
            instance,
            "POST",
            "/v1/customers",
            {"email": "nobad@example.test", "expand": ["bogus_field"]},
        )
    assert exc_info.value.status == 400
    assert instance.inspect().one("SELECT count(*) AS n FROM customers") == {"n": 0}


def test_bad_expand_on_create_does_not_prevent_a_retry(instance: seahaven.Instance) -> None:
    """A bad expand path on a create fails without writing. A subsequent
    create with the same email (without the bad path) succeeds — the error
    is pre-execution and nothing was recorded. The idempotency interaction
    is tested in ``test_idempotency.py``."""
    with pytest.raises(StripeToolError):
        api_write(
            instance,
            "POST",
            "/v1/customers",
            {"email": "retry@example.test", "expand": ["bogus_field"]},
        )
    assert instance.inspect().one("SELECT count(*) AS n FROM customers") == {"n": 0}
    ok = api_write(instance, "POST", "/v1/customers", {"email": "retry@example.test"})
    assert ok["email"] == "retry@example.test"


def test_expansion_writes_nothing(instance: seahaven.Instance) -> None:
    """Expansion is strictly read-only: the change log gains nothing."""
    cus_id = created(instance)
    marker = len(instance.change_log())
    expand_on(instance, f"/v1/customers/{cus_id}", "default_source")
    assert len(instance.change_log()) == marker
