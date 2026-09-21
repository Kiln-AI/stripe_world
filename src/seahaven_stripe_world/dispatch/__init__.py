"""The dispatch layer: the entry point behind the four tools, and the route
table, router, generated-CRUD engine and parameter machinery it is made of.

`routes.py` is the single source of routed scope: the pruner that builds
`spec/` reads it, the router compiles it, discovery filters by it, and the
conformance harness will enumerate it.
"""

from collections.abc import Mapping
from typing import Any

import seahaven

from seahaven_stripe_world.dispatch import params as params_mod
from seahaven_stripe_world.dispatch import router as router_mod
from seahaven_stripe_world.dispatch.response import ApiResponse, Request, build

__all__ = ["dispatch"]


def dispatch(
    ctx: seahaven.Ctx,
    method: str,
    path: str,
    params: Mapping[str, Any] | None,
    *,
    idempotency_key: str | None,
) -> ApiResponse:
    """One Stripe API call: route, bind, invoke, build.

    Raises `StripeApiError` — routing, parameter and handler refusals — and
    that is deliberate: the Stripe-envelope middleware, outside the per-call
    transaction, is where it stops being an exception
    (`components/cross_cutting.md` §3.5.3). The rollback has already happened
    by the time it is rendered, which is the whole "raise loses the writes"
    rule.
    """
    match = router_mod.ROUTER.match(method, path)
    request = params_mod.bind(
        match.route,
        match.path_values,
        path,
        params or {},
        idempotency_key=idempotency_key,
    )
    result = _invoke(ctx, request)
    return build(ctx, request, result)


def _invoke(ctx: seahaven.Ctx, request: Request) -> dict[str, Any] | ApiResponse:
    route = request.route
    if route.handler is not None:
        return route.handler(ctx, request)
    if route.resource is not None and route.action is not None:
        # local: `resource` is in this same package, and loading it here at
        # call time — rather than at this package's import — keeps __init__
        # acyclic against routes → resources.customers → resource.
        from seahaven_stripe_world.dispatch import resource

        engine = resource._ENGINE.get(route.action)
        if engine is None:
            raise seahaven.WorldBug(f"route {route.op_id} has no engine action {route.action!r}")
        return engine(ctx, request)
    raise seahaven.WorldBug(
        f"route {route.op_id} is not wired to a handler or resource yet — "
        "its resource phase has not landed"
    )
