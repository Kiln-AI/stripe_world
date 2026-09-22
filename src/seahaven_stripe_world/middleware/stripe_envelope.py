"""The Stripe response boundary: where a Stripe error stops being an exception.

The only place `StripeApiError` is caught (`components/cross_cutting.md`
§3.5.3 — the correction of the architecture's "catch at the dispatcher
boundary", which would have committed the partial writes of every failed
call). This middleware sits outside the per-call transaction, so by the time
it sees the error the rollback has already happened and it is only formatting:
raise loses the writes, and a returned `ApiResponse` — a 402 decline, an
earned failure that keeps its rows — passes through as the success it is.

Also mints the `req_`-prefixed request id and parks it, with the call's
idempotency key, in `ctx.state["_request"]`: `emit_event` reads it so
`event.request.id` is populated (`cross_cutting.md` §3.4.3).

Applies only to the two HTTP faces, by tool name; the discovery tools are not
HTTP faces and pass through untouched. The rendered shape is three keys,
`{status, body, headers}` — `cross_cutting.md` §7.4's recommended resolution
taken in Phase 18. `headers` carries `Stripe-Version`, `Request-Id`, and
`Idempotency-Key` (when one was sent), so the response headers that the real
API returns as HTTP headers are representable even though this world has no
HTTP layer.
"""

from typing import Any, Final

import seahaven
from seahaven.world import Handler

from seahaven_stripe_world._ids import stripe_id
from seahaven_stripe_world.dispatch.response import ApiResponse
from seahaven_stripe_world.stripe_errors import StripeApiError
from seahaven_stripe_world.world import world

__all__ = ["stripe_envelope"]

#: The pinned API version, written once — `emit_event` stores it per event,
#: the `Stripe-Version` header echoes it per response.
API_VERSION: Final = "2026-08-26.dahlia"

#: The tools whose results are HTTP responses, by name: the two registered
#: faces and the escape hatch, so a host that publishes the raw-HTTP surface
#: under its own registration keeps the boundary (`components/cross_cutting.md`
#: §2.3). The discovery tools are not HTTP faces and pass through untouched.
_HTTP_TOOLS = frozenset(("stripe_api_read", "stripe_api_write", "call_stripe"))


def _headers(ctx: seahaven.Ctx) -> dict[str, str]:
    """The response headers every HTTP-face call carries.

    `Stripe-Version` is always present. `Request-Id` is always present (the
    envelope mints one per call). `Idempotency-Key` is present only when the
    caller sent one — the real API echoes it back.
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


@world.middleware
def stripe_envelope(ctx: seahaven.Ctx, call: seahaven.Call, next_: Handler) -> Any:
    """Render the two HTTP tools' outcomes as `{status, body, headers}`."""
    if call.name not in _HTTP_TOOLS:
        return next_(ctx, call)
    key = call.arguments.get("idempotency_key")
    ctx.state["_request"] = {
        "id": stripe_id(ctx, "req_"),
        "idempotency_key": key if isinstance(key, str) else None,
    }
    try:
        result = next_(ctx, call)
    except StripeApiError as error:
        # The per-call transaction has already rolled back; nothing commits.
        # `invoke` has already logged this raise at ERROR level — a framework
        # property, not a choice this world can make from out here; see
        # `SEAHAVEN_FINDINGS.md` Entry 8 for the ops note.
        return {"status": error.status, "body": error.envelope(), "headers": _headers(ctx)}
    if isinstance(result, ApiResponse):
        return {"status": result.status, "body": result.body, "headers": _headers(ctx)}
    return result
