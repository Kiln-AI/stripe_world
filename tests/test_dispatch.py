"""The dispatcher end to end: the return shape, the raise/return distinction,
and the unwired-route honesty."""

import pytest
import seahaven

from conftest import BLANK_NOW, api_read, api_write
from seahaven_stripe_world.errors import StripeToolError, UnknownOperation
from seahaven_stripe_world.startup import ACCOUNT_ID

pytestmark = pytest.mark.seahaven(fixture=None, now=BLANK_NOW)


def test_the_return_shape_is_a_bare_body(instance: seahaven.Instance) -> None:
    result = api_read(instance, "/v1/customers")
    assert isinstance(result, dict)
    assert result["object"] == "list"
    assert "data" in result
    # No {status, body, headers} wrapper — the MCP surface returns the body directly.
    assert "status" not in result
    assert "headers" not in result


def test_a_stripe_error_raises_a_tool_error(instance: seahaven.Instance) -> None:
    """Every refusal this surface can produce raises a ToolError subclass; the
    agent never sees a structured error envelope from the registered tools."""
    for op_id in ("GetWidgets", "PostChargesChargeCapture"):
        with pytest.raises(UnknownOperation):
            instance.call(
                "stripe_api_read",
                stripe_api_operation_id=op_id,
                parameters={},
                stripe_context=ACCOUNT_ID,
                livemode=True,
            )
    with pytest.raises(StripeToolError):
        api_read(instance, "/v1/customers/cus_missing")


def test_an_unwired_route_is_a_world_bug(instance: seahaven.Instance) -> None:
    """Mid-build honesty: a routed-but-unimplemented operation is this world's
    incompleteness, not a Stripe answer. The error handler re-raises a
    `WorldBug` unchanged — loudly, for the author — rather than dressing it as
    a product error an agent could be blamed for."""
    with pytest.raises(seahaven.WorldBug, match="not wired"):
        api_write(instance, "POST", "/v1/invoices/create_preview", {})


def test_a_bad_expand_path_outranks_a_missing_resource(instance: seahaven.Instance) -> None:
    """Path validation is static and pre-execution (cross_cutting.md §3.3.1),
    so it fires before the row lookup: probed live this round,
    `…/cus_missing?expand[]=bogus` answers the 400, not the 404."""
    api_write(instance, "POST", "/v1/customers", {})

    with pytest.raises(StripeToolError) as exc_info:
        api_read(instance, "/v1/customers/cus_missing", {"expand": ["bogus"]})
    assert "This property cannot be expanded (bogus)." in exc_info.value.message

    # A *valid* path alongside a failing call is not itself an error: the
    # inflation step only runs on a 2xx body.
    with pytest.raises(StripeToolError) as exc_info:
        api_read(instance, "/v1/customers/cus_missing", {"expand": ["address"]})
    assert exc_info.value.status == 404
    assert exc_info.value.stripe_body["error"]["code"] == "resource_missing"


def test_a_bad_expand_path_on_a_create_writes_nothing(instance: seahaven.Instance) -> None:
    api_write(instance, "POST", "/v1/customers", {})
    with pytest.raises(StripeToolError) as exc_info:
        api_write(instance, "POST", "/v1/customers", {"expand": ["bogus"]})
    assert exc_info.value.status == 400
    assert instance.inspect().one("SELECT count(*) AS n FROM customers") == {"n": 1}


def test_the_id_prefix_refusal_reads_no_row(instance: seahaven.Instance) -> None:
    """`ch_123` fails at bind time — before any `SELECT` — so it answers 404
    even where the table is empty, and never confuses the engine. The `param`
    is `id`, the recorded spelling for top-level customers routes."""
    with pytest.raises(StripeToolError) as exc_info:
        api_read(instance, "/v1/customers/ch_123")
    assert exc_info.value.status == 404
    assert exc_info.value.stripe_body["error"]["param"] == "id"
