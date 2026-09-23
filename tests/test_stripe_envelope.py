"""The Stripe envelope boundary: two rendering paths verified.

The raw-HTTP face (``call_stripe``) keeps ``{status, body, headers}`` -- the
full HTTP contract used by conformance cassettes. The MCP faces
(``stripe_api_read``, ``stripe_api_write``) return bare body on success and
raise ``StripeToolError`` on failure -- the shape the real Stripe MCP server
presents to agents.
"""

import pytest
import seahaven

from conftest import BLANK_NOW, dispatch_tool
from seahaven_stripe_world.errors import StripeToolError

NOW = BLANK_NOW


def test_a_raised_error_is_rendered_and_rolls_the_call_back(probe, monkeypatch) -> None:
    """The subtle one, through a real chain: a handler that writes then raises
    leaves **no** change-log records, and the raw face shows the envelope."""
    from seahaven_stripe_world.dispatch import routes as routes_module
    from seahaven_stripe_world.dispatch.params import ParamSpec
    from seahaven_stripe_world.dispatch.response import Request
    from seahaven_stripe_world.dispatch.router import Router
    from seahaven_stripe_world.dispatch.routes import Route
    from seahaven_stripe_world.stripe_errors import missing_parameter

    def half_writes_then_refuses(ctx: seahaven.Ctx, req: Request) -> dict:
        ctx.db.execute(
            "INSERT INTO events (id, x_seq, created, api_version, data, type)"
            " VALUES ('evt_probe', 1, ?, '2026-08-26.dahlia', '{}', 'customer.created')",
            NOW,
        )
        raise missing_parameter("amount")

    routes = (
        Route(
            method="POST",
            pattern="/v1/probe/refuse",
            op_id="PostProbeRefuse",
            params=ParamSpec(op_id="PostProbeRefuse", expand=False),
            handler=half_writes_then_refuses,
        ),
    )
    monkeypatch.setattr(
        "seahaven_stripe_world.dispatch.router.ROUTER", Router((*routes_module.ALL, *routes))
    )
    world = probe(dispatch_tool())
    with world.instance(None, now=NOW) as instance:
        result = instance.call("call_stripe", method="POST", path="/v1/probe/refuse")
        assert result["status"] == 400
        assert result["body"]["error"]["code"] == "parameter_missing"
        assert set(result) == {"status", "body", "headers"}
        assert instance.change_log() == []


def test_the_error_envelope_omits_null_fields(probe, monkeypatch) -> None:
    from seahaven_stripe_world.dispatch import routes as routes_module
    from seahaven_stripe_world.dispatch.params import ParamSpec
    from seahaven_stripe_world.dispatch.response import Request
    from seahaven_stripe_world.dispatch.router import Router
    from seahaven_stripe_world.dispatch.routes import Route
    from seahaven_stripe_world.stripe_errors import unknown_parameter

    def refusing(ctx: seahaven.Ctx, req: Request) -> dict:
        raise unknown_parameter("nope")

    routes = (
        Route(
            method="POST",
            pattern="/v1/probe/unknown",
            op_id="PostProbeUnknown",
            params=ParamSpec(op_id="PostProbeUnknown", expand=False),
            handler=refusing,
        ),
    )
    monkeypatch.setattr(
        "seahaven_stripe_world.dispatch.router.ROUTER", Router((*routes_module.ALL, *routes))
    )
    world = probe(dispatch_tool())
    with world.instance(None, now=NOW) as instance:
        result = instance.call("call_stripe", method="POST", path="/v1/probe/unknown")
        assert result["status"] == 400
        assert result["body"] == {
            "error": {
                "type": "invalid_request_error",
                "code": "parameter_unknown",
                "param": "nope",
                "message": "Received unknown parameter: nope",
                "doc_url": "https://stripe.com/docs/error-codes/parameter-unknown",
            }
        }
        assert "headers" in result
        assert result["headers"]["Stripe-Version"] == "2026-08-26.dahlia"


def test_the_discovery_tools_pass_through_untouched() -> None:
    """They are not HTTP faces: their results are their own shapes, never an
    envelope, and a request id is not minted for them."""
    import seahaven_stripe_world

    with seahaven_stripe_world.world.instance(None, now=NOW) as instance:
        results = instance.call("stripe_api_search", query="customer")
        assert isinstance(results, list) and results
        assert set(results[0]) == {"method", "path", "summary"}

        documented = instance.call("stripe_api_details", method="GET", path="/v1/customers")
        assert set(documented) == {
            "method",
            "path",
            "operation_id",
            "summary",
            "description",
            "parameters",
        }


def test_the_request_id_is_minted_per_call_and_carries_the_key() -> None:
    import seahaven_stripe_world

    with seahaven_stripe_world.world.instance(None, now=NOW) as instance:
        instance.call(
            "stripe_api_write",
            method="POST",
            path="/v1/customers",
            params={},
            idempotency_key="ik_123",
        )
        instance.call("stripe_api_write", method="POST", path="/v1/customers", params={})
        rows = instance.inspect().rows(
            "SELECT request_id, request_idempotency_key FROM events ORDER BY x_seq"
        )
        assert rows[0]["request_id"].startswith("req_")
        assert rows[0]["request_idempotency_key"] == "ik_123"
        assert rows[1]["request_idempotency_key"] is None
        assert rows[0]["request_id"] != rows[1]["request_id"]


def test_a_bug_in_the_boundary_is_the_error_handlers_internal(probe) -> None:
    """The boundary registers inside the error handler, so its own bugs reach
    the agent as this world's `INTERNAL`, never as a raw traceback."""
    from seahaven_stripe_world.errors import Internal

    def exploding(ctx: seahaven.Ctx) -> None:
        raise RuntimeError("the boundary itself broke")

    from conftest import a_tool

    world = probe(a_tool(exploding, "exploding"))
    with world.instance(None, now=NOW) as instance, pytest.raises(Internal):
        instance.call("exploding")


def test_mcp_success_returns_bare_body() -> None:
    """The MCP tools return the object directly -- no ``{status, body}`` wrapper."""
    import seahaven_stripe_world

    with seahaven_stripe_world.world.instance(None, now=NOW) as instance:
        result = instance.call("stripe_api_write", method="POST", path="/v1/customers", params={})
        assert isinstance(result, dict)
        assert result["object"] == "customer"
        # No wrapper keys.
        assert "status" not in result
        assert "body" not in result
        assert "headers" not in result


def test_mcp_error_raises_stripe_tool_error() -> None:
    """A non-2xx from an MCP tool raises ``StripeToolError`` with the message
    text the real MCP server would show: ``Stripe API error: {message}``."""
    import seahaven_stripe_world

    with seahaven_stripe_world.world.instance(None, now=NOW) as instance:
        with pytest.raises(StripeToolError) as exc_info:
            instance.call("stripe_api_read", path="/v1/customers/cus_nope")
        assert exc_info.value.status == 404
        assert "Stripe API error:" in exc_info.value.message
        assert "No such customer" in exc_info.value.message


def test_a_402_decline_raises_but_keeps_the_rows() -> None:
    """The transaction-semantics property: a 402 decline returned by a handler
    commits its writes (the failed charge, the event), and the middleware then
    raises so the agent sees a tool error, not a dict."""
    import seahaven_stripe_world

    with seahaven_stripe_world.world.instance(None, now=NOW) as instance:
        cus = instance.call(
            "stripe_api_write",
            method="POST",
            path="/v1/customers",
            params={"email": "dec@example.test"},
        )["id"]
        pm = instance.call(
            "stripe_api_write",
            method="POST",
            path="/v1/payment_methods",
            params={
                "type": "card",
                "card": {"number": "4000000000000341", "exp_month": 9, "exp_year": 2027},
            },
        )["id"]
        instance.call(
            "stripe_api_write",
            method="POST",
            path=f"/v1/payment_methods/{pm}/attach",
            params={"customer": cus},
        )
        with pytest.raises(StripeToolError) as exc_info:
            instance.call(
                "stripe_api_write",
                method="POST",
                path="/v1/payment_intents",
                params={
                    "amount": 1000,
                    "currency": "usd",
                    "customer": cus,
                    "payment_method": pm,
                    "confirm": True,
                },
            )
        assert exc_info.value.status == 402
        # The failed charge row survived the raise.
        assert instance.inspect().one(
            "SELECT count(*) AS n FROM charges WHERE status = 'failed'"
        ) == {"n": 1}
