"""The four-tool Stripe MCP surface: two HTTP faces over the dispatcher, two
discovery faces over the pruned spec.

Signatures follow `components/dispatcher.md` §2.1: `method` is a `Literal` so
the legal verbs appear in the tool's JSON schema and an unusable verb is a
Seahaven `ArgumentError` — restated by the error handler as this world's
`INVALID_INPUT`, not a Stripe envelope, exactly as functional spec §2.3 asks.
`params` defaults to `None` rather than `{}` because a mutable default is
refused at registration; every value inside it is `ParamSpec`'s business, not
pydantic's, because the legal shape differs per operation.

`call_stripe` is the one-line escape hatch of functional spec §2.5: the same
dispatcher under a generic `method, path, params` face, available for a
harness that wants the raw-HTTP shape. It is deliberately **not** registered —
it is not the shape Stripe ships — and a test calls it directly so it cannot
rot.

No docstring names another tool: a prefixing host renames tools without
rewriting descriptions (lint `SH206`).
"""

from collections.abc import Mapping
from typing import Any, Literal

import seahaven

from stripeapi.discovery import index as discovery
from stripeapi.dispatch import dispatch
from stripeapi.errors import InvalidMethod, InvalidSearchQuery, UnknownOperation
from stripeapi.world import world

__all__ = [
    "call_stripe",
    "stripe_api_details",
    "stripe_api_read",
    "stripe_api_search",
    "stripe_api_write",
]

_METHODS = ("GET", "POST", "DELETE")


@world.tool
def stripe_api_read(
    ctx: seahaven.Ctx,
    path: str,
    params: dict[str, Any] | None = None,
) -> Any:
    """Read data from the Stripe API with a GET method.

    `path` is a Stripe API path — a pattern with placeholders as the API
    reference spells them (`/v1/customers/{customer}`) or a concrete one
    (`/v1/customers/cus_123`). `params` carries the endpoint's own parameters
    as a JSON object: filters such as `limit` and `starting_after`, and
    `expand` as an array of paths.

    Returns `{"status": <HTTP status>, "body": <response body>}`. A non-2xx
    status is an ordinary outcome, not an error: its body is Stripe's error
    envelope.
    """
    return dispatch(ctx, "GET", path, params, idempotency_key=None)


@world.tool
def stripe_api_write(
    ctx: seahaven.Ctx,
    method: Literal["POST", "DELETE"],
    path: str,
    params: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> Any:
    """Write data to the Stripe API with a POST or DELETE method.

    `path` is a Stripe API path — a pattern with placeholders as the API
    reference spells them (`/v1/invoices/{invoice}/finalize`) or a concrete
    one. `params` carries the endpoint's own parameters as a JSON object.
    `idempotency_key` names this request so a retry with the same key and the
    same parameters replays the stored response instead of repeating the write.

    Returns `{"status": <HTTP status>, "body": <response body>}`. A non-2xx
    status is an ordinary outcome, not an error: its body is Stripe's error
    envelope.
    """
    return dispatch(ctx, method, path, params, idempotency_key=idempotency_key)


@world.tool
def stripe_api_search(ctx: seahaven.Ctx, query: str) -> list[dict[str, Any]]:
    """Find Stripe API methods by keyword.

    Matches the routed operations' paths, operation ids, summaries and
    descriptions — exact word matches ranked above substring matches — and
    returns up to ten `{method, path, summary}` results, best first. This
    searches the API method catalogue, not Stripe objects. A query that
    matches nothing returns an empty list.
    """
    try:
        return discovery.search(query)
    except ValueError as error:
        raise InvalidSearchQuery(query) from error


@world.tool
def stripe_api_details(
    ctx: seahaven.Ctx,
    method: Literal["GET", "POST", "DELETE"],
    path: str,
) -> dict[str, Any]:
    """Parameter detail for one Stripe API method.

    `path` may be the pattern spelling (`/v1/customers/{customer}`) or a
    concrete one (`/v1/customers/cus_123`); both resolve to the same
    operation. Returns `{method, path, operation_id, summary, description,
    parameters}` with one entry per accepted parameter — its type, whether it
    is required, the first sentence of its documentation, its closed set of
    values when it has one, and one level of nested object fields.
    """
    if method not in _METHODS:
        raise InvalidMethod(method)
    resolved = _resolve_operation(method, path)
    documented = discovery.details(method, resolved)
    if documented is None:
        raise UnknownOperation(method, path)
    return documented


def _resolve_operation(method: str, path: str) -> str:
    """A concrete path resolves to its route's canonical pattern, so an agent
    that just made a call can ask about the URL it used."""
    from stripeapi.dispatch.router import ROUTER

    route = ROUTER.resolve(method, path)
    return route.pattern if route is not None else path


def call_stripe(
    ctx: seahaven.Ctx,
    method: Literal["GET", "POST", "DELETE"],
    path: str,
    params: Mapping[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> Any:
    """The raw-HTTP face of functional spec §2.5: unregistered, because it is
    not the shape Stripe ships; one decorator would publish it."""
    return dispatch(ctx, method, path, params, idempotency_key=idempotency_key)
