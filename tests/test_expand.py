"""The expansion resolver's error forms — every one of them pinned by this
phase's live probes at `2026-08-26.dahlia`, correcting
`components/cross_cutting.md` §3.3.5's table where the probes disagreed."""

from typing import Any, cast

import pytest
import seahaven

from conftest import BLANK_NOW

pytestmark = pytest.mark.seahaven(fixture=None, now=BLANK_NOW)


def expand_on(instance: seahaven.Instance, path: str, *paths: str) -> dict:
    return instance.call("stripe_api_read", path=path, params={"expand": list(paths)})


def created(instance: seahaven.Instance) -> str:
    return instance.call("stripe_api_write", method="POST", path="/v1/customers", params={})[
        "body"
    ]["id"]


def test_nonexistent_field_is_the_plain_form(instance: seahaven.Instance) -> None:
    """Probed: `bogus_field_probe` — the `because it doesn't exist` variant of
    the component design does not fire for a top-level field."""
    result = expand_on(instance, f"/v1/customers/{created(instance)}", "bogus_field_probe")
    assert result["status"] == 400
    error = result["body"]["error"]
    assert error == {
        "type": "invalid_request_error",
        "message": "This property cannot be expanded (bogus_field_probe).",
    }


def test_exists_but_unexpandable_is_the_same_plain_form(instance: seahaven.Instance) -> None:
    """Probed: `description_probe` — indistinguishable from a nonexistent
    field, which is why the `exists=` selection never fires here."""
    result = expand_on(instance, f"/v1/customers/{created(instance)}", "description")
    assert result["status"] == 400
    assert result["body"]["error"]["message"] == "This property cannot be expanded (description)."


def test_a_bad_nested_segment_carries_the_whole_path(instance: seahaven.Instance) -> None:
    """Probed: `(invoice_settings.bogus)` — the token is the path, not the
    lone segment the component design describes."""
    result = expand_on(instance, f"/v1/customers/{created(instance)}", "invoice_settings.bogus")
    assert result["status"] == 400
    assert result["body"]["error"]["message"] == (
        "This property cannot be expanded (invoice_settings.bogus)."
    )


def test_an_embedded_expandable_field_is_accepted(instance: seahaven.Instance) -> None:
    """Probed: `expand[]=address` returns 200 — an object in
    `x-expandableFields` is traversed, never inflated, because it is already
    the object."""
    result = expand_on(instance, f"/v1/customers/{created(instance)}", "address")
    assert result["status"] == 200
    assert result["body"]["address"] is None


def test_a_null_reference_stays_null(instance: seahaven.Instance) -> None:
    """Probed: `invoice_settings.default_payment_method` on a customer with
    none — accepted, and the field stays null rather than erroring."""
    result = expand_on(
        instance,
        f"/v1/customers/{created(instance)}",
        "invoice_settings.default_payment_method",
    )
    assert result["status"] == 200
    assert result["body"]["invoice_settings"]["default_payment_method"] is None


def test_a_list_without_the_data_prefix_gets_the_hint(instance: seahaven.Instance) -> None:
    """Probed with `email` and with a bogus name: any bare first segment on a
    list endpoint gets the redirecting hint."""
    created(instance)
    for segment in ("email", "bogus_list_probe"):
        result = expand_on(instance, "/v1/customers", segment)
        assert result["status"] == 400
        assert result["body"]["error"]["message"] == (
            f"This property cannot be expanded ({segment}). "
            f"You may want to try expanding 'data.{segment}' instead."
        )


def test_expand_data_alone_is_accepted(instance: seahaven.Instance) -> None:
    """Probed: `expand[]=data` on a list is a 200, not the `(data)` error the
    component design describes."""
    created(instance)
    result = expand_on(instance, "/v1/customers", "data")
    assert result["status"] == 200
    assert len(result["body"]["data"]) == 1


def test_a_bad_path_outranks_an_empty_filtered_page(instance: seahaven.Instance) -> None:
    """The empty-list-page case validation used to miss: with a filter that
    matches nothing, the live API still answers the 400 hint form, because
    path validation is static rather than payload-coupled (probed live this
    round)."""
    created(instance)
    result = instance.call(
        "stripe_api_read",
        path="/v1/customers",
        params={"email": "no-match@example.test", "expand": ["bogus"]},
    )
    assert result["status"] == 400
    assert result["body"]["error"]["message"] == (
        "This property cannot be expanded (bogus). "
        "You may want to try expanding 'data.bogus' instead."
    )


def test_more_than_four_segments_is_rejected(instance: seahaven.Instance) -> None:
    result = expand_on(instance, f"/v1/customers/{created(instance)}", "a.b.c.d.e")
    assert result["status"] == 400
    assert result["body"]["error"]["type"] == "invalid_request_error"


def test_paths_merge_into_one_trie() -> None:
    from stripeapi.serialize.expand import validate_paths as validate

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
    assert result["status"] == 200
    assert result["body"]["default_source"] is None


def test_expansion_off_a_setup_intent(instance: seahaven.Instance) -> None:
    """The setup-intent slice's reference edges (Phase 10): `payment_method`
    and `customer` inflate through the registered resources, and the
    `setatt_` attempt stub validates but inflates nothing (a pruned
    target)."""
    cus = created(instance)
    pm = instance.call(
        "stripe_api_write",
        method="POST",
        path="/v1/payment_methods",
        params={"type": "card", "card": {"token": "tok_visa"}},
    )["body"]["id"]
    instance.call(
        "stripe_api_write",
        method="POST",
        path=f"/v1/payment_methods/{pm}/attach",
        params={"customer": cus},
    )
    seti = instance.call(
        "stripe_api_write",
        method="POST",
        path="/v1/setup_intents",
        params={"customer": cus, "payment_method": pm, "confirm": True},
    )["body"]
    result = expand_on(
        instance,
        f"/v1/setup_intents/{seti['id']}",
        "payment_method",
        "customer",
        "latest_attempt",
    )
    assert result["status"] == 200
    assert result["body"]["payment_method"]["id"] == pm
    assert result["body"]["payment_method"]["customer"] == cus
    assert result["body"]["customer"]["id"] == cus
    # the stub never resolves: the minted id stays a bare string
    assert result["body"]["latest_attempt"] == seti["latest_attempt"]


def test_a_dangling_reference_is_a_world_bug() -> None:
    """The engine emitted an id from a row it just read; a missing target row
    is a schema or write bug, not something an agent did
    (`components/cross_cutting.md` §3.3.4)."""
    import stripeapi.dispatch.resource as resource
    import stripeapi.serialize.expand as expand

    class FakeCtx:
        class db:
            @staticmethod
            def rows(sql, *params):
                return []

    edge = expand._Edge(kind="reference", target="customer", children={})
    ctx = cast(Any, FakeCtx())
    with pytest.raises(seahaven.WorldBug, match="dangling"):
        expand._fetch(ctx, resource.BY_OBJECT, edge, ["cus_missing"])
