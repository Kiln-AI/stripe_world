"""The Stripe response boundary: where a Stripe error stops being an exception.

Two rendering paths live here, for the two faces this world exposes:

**MCP tools** (``stripe_api_read``, ``stripe_api_write``) return the bare
response body on success and raise ``StripeToolError`` on failure.  This
matches the real Stripe MCP server, where the agent never sees a status code,
response headers, or a structured error envelope -- only the object on
success and a plain-text error message on failure (functional spec section 4.5).

**The raw-HTTP face** (``call_stripe``) keeps the ``{status, body, headers}``
shape.  It is unregistered -- not the shape Stripe ships -- and exists for
conformance cassettes and for test infrastructure that needs the full HTTP
contract.  Nothing visible to an agent travels this path.

The ``StripeApiError`` catch is the only place that exception type is caught
(``components/cross_cutting.md`` section 3.5.3's correction of the
architecture's "catch at the dispatcher boundary").  This middleware sits
outside the per-call transaction, so by the time it sees the error the
rollback has already happened and it is only formatting: raise loses the
writes, and a returned ``ApiResponse`` -- a 402 decline, an earned failure
that keeps its rows -- passes through as the success it is.

Also mints the ``req_``-prefixed request id and parks it, with the call's
idempotency key, in ``ctx.state["_request"]``: ``emit_event`` reads it so
``event.request.id`` is populated (``cross_cutting.md`` section 3.4.3).
"""

from typing import Any, Final

import seahaven
from seahaven.world import Handler

from seahaven_stripe_world._ids import stripe_id
from seahaven_stripe_world.dispatch.response import ApiResponse
from seahaven_stripe_world.errors import StripeToolError
from seahaven_stripe_world.stripe_errors import StripeApiError
from seahaven_stripe_world.world import world

__all__ = ["stripe_envelope"]

#: The pinned API version, written once -- ``emit_event`` stores it per event,
#: the ``Stripe-Version`` header echoes it per response.
API_VERSION: Final = "2026-08-26.dahlia"

#: The registered MCP tools whose results are unwrapped to bare body on
#: success and raised as ``StripeToolError`` on failure.
_MCP_TOOLS = frozenset(("stripe_api_read", "stripe_api_write"))

#: The raw-HTTP face that keeps the ``{status, body, headers}`` shape.
_RAW_TOOLS = frozenset(("call_stripe",))

#: Every tool that carries an HTTP-shaped response — the union the
#: schema-conformance hook needs to know which calls to capture.
_HTTP_TOOLS = _MCP_TOOLS | _RAW_TOOLS


def _headers(ctx: seahaven.Ctx) -> dict[str, str]:
    """The response headers every raw-HTTP-face call carries.

    ``Stripe-Version`` is always present.  ``Request-Id`` is always present
    (the envelope mints one per call).  ``Idempotency-Key`` is present only
    when the caller sent one -- the real API echoes it back.
    """
    request = ctx.state.get("_request") or {}
    headers: dict[str, str] = {
        "Stripe-Version": API_VERSION,
        "Request-Id": request.get("id", ""),
    }
    ik = request.get("idempotency_key")
    if ik is not None:
        headers["Idempotency-Key"] = ik
    return headers


def _render_tool_error(body: dict, *, status: int) -> StripeToolError:
    """Build the agent-visible error string from a Stripe error envelope.

    The format -- ``Stripe API error: {message}`` -- matches the real MCP
    server's rendering (functional spec section 4.5).
    """
    error = body.get("error", {})
    message = error.get("message", "An error occurred")
    return StripeToolError(f"Stripe API error: {message}", status=status, stripe_body=body)


def _mint_request(ctx: seahaven.Ctx, call: seahaven.Call) -> None:
    """Mint a request id and park it -- shared by both rendering paths."""
    key = call.arguments.get("idempotency_key")
    ctx.state["_request"] = {
        "id": stripe_id(ctx, "req_", timestamp=ctx.clock.iso()),
        "idempotency_key": key if isinstance(key, str) else None,
    }


@world.middleware
def stripe_envelope(ctx: seahaven.Ctx, call: seahaven.Call, next_: Handler) -> Any:
    """Render the HTTP tools' outcomes in the appropriate shape."""
    if call.name in _MCP_TOOLS:
        return _mcp_envelope(ctx, call, next_)
    if call.name in _RAW_TOOLS:
        return _raw_envelope(ctx, call, next_)
    return next_(ctx, call)


def _mcp_envelope(ctx: seahaven.Ctx, call: seahaven.Call, next_: Handler) -> Any:
    """MCP shape: bare body on 2xx, ``StripeToolError`` on anything else."""
    _mint_request(ctx, call)
    try:
        result = next_(ctx, call)
    except StripeApiError as error:
        raise _render_tool_error(error.envelope(), status=error.status) from None
    if isinstance(result, ApiResponse):
        if result.status < 400:
            return result.body
        raise _render_tool_error(result.body, status=result.status)
    return result


def _raw_envelope(ctx: seahaven.Ctx, call: seahaven.Call, next_: Handler) -> Any:
    """Raw-HTTP shape: ``{status, body, headers}``."""
    _mint_request(ctx, call)
    try:
        result = next_(ctx, call)
    except StripeApiError as error:
        return {"status": error.status, "body": error.envelope(), "headers": _headers(ctx)}
    if isinstance(result, ApiResponse):
        return {"status": result.status, "body": result.body, "headers": _headers(ctx)}
    return result
