"""Response construction: what a handler produced becomes `{status, body}`.

A handler returns a `dict` — the API object, already through its resource's
serializer, answered as 200 — or an `ApiResponse` for the one case a dict
cannot express: an outcome that is both a real state change *and* a non-2xx
response. A declined charge writes its rows and answers 402; those rows must
commit, so the decline is returned, never raised
(`components/cross_cutting.md` §3.5.4: raise loses the writes, return keeps
them).

Where the Stripe error boundary sits: `middleware/stripe_envelope.py`, outside
the per-call transaction — not here. A `StripeApiError` raised anywhere under
`dispatch` (routing, binding, a handler, this module's expansion step)
propagates out of the tool function, Seahaven's transaction rolls back, and
the middleware renders the envelope. That is `components/cross_cutting.md`
§3.5.3's correction of the architecture, and the mechanism that makes "raise
loses" true without a savepoint. The wire shape is three keys,
`{status, body, headers}` — `cross_cutting.md` §7.4's recommended resolution,
taken in Phase 18; `headers` is constructed by `stripe_envelope` from `ctx`.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import seahaven

from seahaven_stripe_world.serialize import expand

if TYPE_CHECKING:
    from seahaven_stripe_world.dispatch.routes import Route

__all__ = ["ApiResponse", "Handler", "Page", "Request", "build"]


@dataclass(frozen=True, slots=True)
class Page:
    """The lifted pagination parameters, already clamped and checked."""

    limit: int  # 1..100, default 10
    starting_after: str | None
    ending_before: str | None


@dataclass(frozen=True, slots=True)
class Request:
    """One bound call: everything a handler needs and nothing it must reparse.

    `params` never contains `expand`, `limit`, `starting_after`,
    `ending_before` or `metadata`; they are lifted into their own fields. A
    handler that reaches for `req.params["expand"]` has a bug, and the
    `KeyError` is the right kind of loud.
    """

    method: str
    path: str  # the concrete path as called
    route: Route
    path_params: Mapping[str, str]  # by the pattern's own placeholder names
    params: Mapping[str, Any]  # validated, coerced, allowlisted; keyed by column
    expand: tuple[str, ...]
    metadata: Any | None  # a MetadataUpdate, when the operation accepts one
    page: Page | None
    idempotency_key: str | None
    # Search-only: ``expand[]=total_count`` opts into computing and returning
    # ``total_count`` in the search-result envelope (functional_spec §6.2).
    # Set by params.py when the expand list includes ``total_count``.
    include_total_count: bool = False


@dataclass(frozen=True, slots=True)
class ApiResponse:
    """What a handler returns: `status` and `body`, named as the wire keys are.

    There is no 201 anywhere — Stripe v1 answers 200 on create as well as read.
    """

    status: int
    body: dict[str, Any]

    @classmethod
    def ok(cls, body: dict[str, Any]) -> ApiResponse:
        return cls(200, body)


#: Every hand-written handler has this signature, no exceptions
#: (`components/dispatcher.md` §3.6).
Handler = Callable[[seahaven.Ctx, Request], dict[str, Any] | ApiResponse]


def build(ctx: seahaven.Ctx, req: Request, result: dict[str, Any] | ApiResponse) -> ApiResponse:
    """Normalize a handler's result and apply expansion.

    The paths themselves were validated at bind, statically against the
    route's declared response object, before any row was written
    (`components/cross_cutting.md` §3.3.1); what happens here is only the
    inflation, and only on a 2xx body — an error envelope has nothing in it
    to expand.
    """
    response = result if isinstance(result, ApiResponse) else ApiResponse.ok(result)
    if req.expand and 200 <= response.status < 300:
        route = req.route
        if route.response_object is None or route.envelope is None:
            raise seahaven.WorldBug(
                f"route {route.op_id} reached build with expand but no response declaration"
            )
        response = ApiResponse(
            response.status,
            expand.apply(
                ctx,
                response.body,
                req.expand,
                object_name=route.response_object,
                is_list=(route.envelope == "list"),
            ),
        )
    return response
