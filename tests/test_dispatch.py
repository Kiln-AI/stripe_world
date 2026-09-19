"""The dispatcher end to end: the return shape, the raise/return distinction,
and the unwired-route honesty."""

import pytest
import seahaven

from conftest import BLANK_NOW

pytestmark = pytest.mark.seahaven(fixture=None, now=BLANK_NOW)


def test_the_return_shape_is_status_and_body(instance: seahaven.Instance) -> None:
    result = instance.call("stripe_api_read", path="/v1/customers")
    assert set(result) == {"status", "body"}
    assert isinstance(result["status"], int)


def test_a_stripe_error_never_escapes_as_an_exception(instance: seahaven.Instance) -> None:
    """Every refusal this surface can produce is a return value; a raised
    `StripeApiError` leaving the chain would be this world's bug."""
    for call in (
        {"path": "/v1/widgets"},
        {"path": "/v1/charges/ch_1/capture"},
        {"path": "/v1/customers/cus_missing"},
        {"path": "/v1/customers", "params": {"limit": 0}},
    ):
        result = instance.call("stripe_api_read", **call)
        assert isinstance(result, dict) and set(result) == {"status", "body"}


def test_an_unwired_route_is_a_world_bug(instance: seahaven.Instance) -> None:
    """Mid-build honesty: a routed-but-unimplemented operation is this world's
    incompleteness, not a Stripe answer. The error handler re-raises a
    `WorldBug` unchanged — loudly, for the author — rather than dressing it as
    a product error an agent could be blamed for."""
    with pytest.raises(seahaven.WorldBug, match="not wired"):
        instance.call("stripe_api_read", path="/v1/invoices")


def test_a_bad_expand_path_outranks_a_missing_resource(instance: seahaven.Instance) -> None:
    """Path validation is static and pre-execution (cross_cutting.md §3.3.1),
    so it fires before the row lookup: probed live this round,
    `…/cus_missing?expand[]=bogus` answers the 400, not the 404."""
    instance.call("stripe_api_write", method="POST", path="/v1/customers", params={})

    result = instance.call(
        "stripe_api_read", path="/v1/customers/cus_missing", params={"expand": ["bogus"]}
    )
    assert result["status"] == 400
    assert result["body"]["error"]["message"] == "This property cannot be expanded (bogus)."

    # A *valid* path alongside a failing call is not itself an error: the
    # inflation step only runs on a 2xx body.
    result = instance.call(
        "stripe_api_read", path="/v1/customers/cus_missing", params={"expand": ["address"]}
    )
    assert result["status"] == 404
    assert result["body"]["error"]["code"] == "resource_missing"


def test_a_bad_expand_path_on_a_create_writes_nothing(instance: seahaven.Instance) -> None:
    instance.call("stripe_api_write", method="POST", path="/v1/customers", params={})
    rolled = instance.call(
        "stripe_api_write", method="POST", path="/v1/customers", params={"expand": ["bogus"]}
    )
    assert rolled["status"] == 400
    assert instance.inspect().one("SELECT count(*) AS n FROM customers") == {"n": 1}


def test_the_id_prefix_refusal_reads_no_row(instance: seahaven.Instance) -> None:
    """`ch_123` fails at bind time — before any `SELECT` — so it answers 404
    even where the table is empty, and never confuses the engine."""
    result = instance.call("stripe_api_read", path="/v1/customers/ch_123")
    assert result["status"] == 404
    assert result["body"]["error"]["param"] == "customer"
