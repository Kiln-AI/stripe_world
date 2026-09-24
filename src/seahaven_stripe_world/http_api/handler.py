"""Stripe's HTTP API over the dispatcher: one `seahaven.http` handler.

The request is the one Stripe's SDKs send (`http_api/wire.py` reads it), the
answer is the one api.stripe.com gives: the bare object or the error envelope,
with Stripe's status and its `Request-Id`, `Stripe-Version` and idempotency
headers.

Under `seahaven.http` a request is one transaction that commits on any
returned response and rolls back only on a raise. This world's own rule is
narrower, "raise loses the writes, return keeps them"
(`stripe_errors.py`), so the dispatch runs in a savepoint: a `StripeApiError`
rolls back this request's writes and is answered as the envelope it carries,
while a returned `ApiResponse`, such as a `402` decline, keeps its rows. Any
other exception is a bug in this world and is left to `seahaven.http`, which
rolls the request back and answers `500`.

Every instance is one account, whatever key a request carries, in the mode its
`startup` gives it: live, the world's default, unless the instance was made
with the startup keyword `livemode` set to false (`serve_http.py`'s docstring
says how). A key's `sk_test_`/`sk_live_` prefix is not checked against the
mode, and `Stripe-Account` is ignored. Nothing here reads `ctx.call`, which is
`None` under the server.
"""

from typing import Final

import seahaven
from seahaven.http import HttpRequest, HttpResponse

from seahaven_stripe_world.dispatch import dispatch
from seahaven_stripe_world.dispatch import router as router_mod
from seahaven_stripe_world.dispatch.response import ApiResponse
from seahaven_stripe_world.http_api import wire
from seahaven_stripe_world.middleware.idempotency import replay_or_run
from seahaven_stripe_world.middleware.stripe_envelope import mint_request, response_headers
from seahaven_stripe_world.stripe_errors import StripeApiError, invalid_request

__all__ = ["handle"]

# Stripe's answer to a request with no key, and the challenge header its 401s
# carry, as public reports of the live API quote them (stripe/stripe-node#950
# for the header). Not recorded: the Stripe MCP always authenticates, and a
# cassette run always sends a key.
_NO_KEY: Final = (
    "You did not provide an API key. You need to provide your API key in the "
    "Authorization header, using Bearer auth (e.g. 'Authorization: Bearer "
    "YOUR_SECRET_KEY'). See https://stripe.com/docs/api#authentication for details, "
    "or we can help at https://support.stripe.com/."
)
_CHALLENGE: Final = ("WWW-Authenticate", 'Bearer realm="Stripe"')
_SCHEMES: Final = frozenset(("bearer", "basic"))


def handle(ctx: seahaven.Ctx, request: HttpRequest) -> HttpResponse:
    """Answer one Stripe API request against this instance."""
    key = request.header("idempotency-key") or None
    mint_request(ctx, key)
    replayed = False
    try:
        if not _has_key(request.header("authorization")):
            raise invalid_request(_NO_KEY, status=401, pre_execution=True)
        params = wire.decode(request)
        if request.method == "POST" and key is not None:
            response, replayed = replay_or_run(
                ctx,
                method=request.method,
                path=request.path,
                params=params.raw(),
                key=key,
                run=lambda: _run(ctx, request, params, key),
            )
        else:
            response = _run(ctx, request, params, key)
    except StripeApiError as error:
        response = ApiResponse(error.status, error.envelope())
    return _render(ctx, response, replayed=replayed)


def _run(
    ctx: seahaven.Ctx, request: HttpRequest, params: wire.WireParams, key: str | None
) -> ApiResponse:
    """Route, coerce and dispatch, in a savepoint a `StripeApiError` rolls back."""
    with ctx.db.transaction():
        # Routed here for the parameter declarations coercion reads, and again
        # inside `dispatch`, which takes a path rather than a route: a second
        # walk of a six-level trie, deliberately, rather than a second entry point.
        route = router_mod.ROUTER.match(request.method, request.path).route
        if route.params is None:
            # A declared simplification: the table carries operations this world
            # has not built (`routes.py`, the Phase 12/13 cuts), which `bind`
            # refuses as a `WorldBug`. Over HTTP they answer the 404 an unrouted
            # path does, as if the table did not carry them at all.
            raise invalid_request(
                f"Unrecognized request URL ({request.method}: {request.path}).",
                status=404,
                pre_execution=True,
            )
        return dispatch(
            ctx,
            request.method,
            request.path,
            params.typed(route),
            idempotency_key=key,
        )


def _has_key(authorization: str | None) -> bool:
    """Whether the header carries anything after its scheme word (`Bearer`, `Basic`)."""
    words = (authorization or "").split(maxsplit=1)
    return len(words) == 2 or (len(words) == 1 and words[0].lower() not in _SCHEMES)


def _render(ctx: seahaven.Ctx, response: ApiResponse, *, replayed: bool) -> HttpResponse:
    headers = list(response_headers(ctx).items())
    if replayed:
        headers.append(("Idempotent-Replayed", "true"))
    if response.status == 401:
        headers.append(_CHALLENGE)
    return HttpResponse.json(response.body, status=response.status, headers=tuple(headers))
