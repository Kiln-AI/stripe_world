"""The Stripe MCP tool surface: four registered tools matching the real
Stripe MCP server's schemas and descriptions exactly.

Two HTTP faces over the dispatcher (``stripe_api_read``,
``stripe_api_write``) and two discovery faces over the pruned spec
(``stripe_api_search``, ``stripe_api_details``).  All four take
``stripe_context`` and ``livemode``, validated by ``_context.check``
before any dispatch.

Signatures are fixed by the captured real schemas in
``tests/surface/real_tool_schemas.json`` (probed 2026-09-22).  Operation
IDs resolve to routes through the routing table; the verb is implied by
the operation ID, not passed separately.

``call_stripe`` is the deliberately unregistered escape hatch of functional
spec section 2.5: the same dispatcher under a generic ``method, path,
params`` face, available for conformance cassettes and harnesses that want
the raw-HTTP shape.  It keeps ``idempotency_key`` (functional spec
section 16).  Test callers register it on probe worlds via
``conftest.dispatch_tool()``.
"""

from typing import Annotated, Any

import seahaven
from pydantic import Field

from seahaven_stripe_world.discovery import index as discovery
from seahaven_stripe_world.dispatch import dispatch
from seahaven_stripe_world.errors import CatalogueRefusal, UnknownOperation
from seahaven_stripe_world.spec.catalogue import is_absent, is_catalogued
from seahaven_stripe_world.spec.catalogue import permissions as catalogue_permissions
from seahaven_stripe_world.spec.products import product_for_path
from seahaven_stripe_world.tools import _context, _descriptions
from seahaven_stripe_world.world import world

__all__ = [
    "call_stripe",
    "stripe_api_details",
    "stripe_api_read",
    "stripe_api_search",
    "stripe_api_write",
]

# --- Annotated types matching the real Stripe MCP schemas ---

OperationId = Annotated[
    str,
    Field(
        description="The operation ID to execute",
        examples=["PostCustomers", "GetPaymentIntents"],
    ),
]

DetailsOperationId = Annotated[
    str,
    Field(
        description="The operation ID to get details for "
        "(e.g. 'PostCustomers', 'GetPaymentIntents')",
    ),
]

Parameters = Annotated[
    dict[str, Any],
    Field(
        description="Parameters for the API call. Include path parameters "
        "(e.g. 'customer' for /v1/customers/{customer}), query parameters, "
        "and body parameters. Array fields (e.g. line_items) must be passed "
        "as a JSON array value, not as a plain string.",
    ),
]

StripeContext = Annotated[
    str,
    Field(
        description="The account to target for this request. Use the "
        "`stripe_context` value returned by list_available_accounts_or_orgs.",
    ),
]

LiveMode = Annotated[
    bool,
    Field(
        description="Whether to operate in livemode (true) or test mode/ "
        "sandbox (false). Must match the livemode of the stripe_context account.",
    ),
]

Intent = Annotated[
    str,
    Field(
        description="The intent of the operation",
        examples=["create", "list", "refund"],
    ),
]

Resource = Annotated[
    str,
    Field(
        description="Target resource the operation is looking to manipulate",
        examples=["customer", "payout methods", "issuing card transactions"],
    ),
]

Limit = Annotated[
    int,
    Field(
        ge=1,
        le=20,
        description="Maximum number of results to return",
    ),
]


#: The ``human_confirmation`` parameter's nested ``properties`` block,
#: matching the real Stripe MCP server's schema exactly.
_HUMAN_CONFIRMATION_PROPERTIES: dict[str, Any] = {
    "approval_token": {
        "type": "string",
        "description": (
            "The ID of the approval token for the request. Find it in "
            'the response from the previous tool call in the "needs_confirmation" field.'
        ),
    },
}


# --- Catalogue gating (architecture section 4.3) ---


def _gate(op_id: str) -> None:
    """Check the catalogue before route matching.

    The ordering is: absent -> bucket A, catalogued-but-unrouted -> bucket B.
    The catalogue is consulted before the route table so that an operation
    this world routes but the real MCP does not expose (e.g. GetEvents)
    answers bucket A even though a handler exists (functional spec section 9).
    """
    if is_absent(op_id):
        raise UnknownOperation(op_id)
    if is_catalogued(op_id):
        from seahaven_stripe_world.dispatch.router import ROUTER

        route = ROUTER.resolve_op_id(op_id)
        if route is None:
            # Catalogued but no route: determine the path from the full spec
            # for product lookup, then raise B refusal
            _raise_bucket_b(op_id)


def _raise_bucket_b(op_id: str) -> None:
    """Raise the appropriate bucket-B refusal for a catalogued-but-unrouted op."""
    from seahaven_stripe_world.spec import full_spec_document

    spec = full_spec_document()
    path = _find_path_for_op(op_id, spec)
    product = product_for_path(path) if path else None
    perms = catalogue_permissions(op_id)
    raise CatalogueRefusal(op_id, product=product, permissions=perms)


def _find_path_for_op(op_id: str, spec: dict[str, Any]) -> str | None:
    """Find the path for an operation ID in the full OpenAPI spec."""
    for path, methods in spec.get("paths", {}).items():
        for _method, op_data in methods.items():
            if isinstance(op_data, dict) and op_data.get("operationId") == op_id:
                return path
    return None


# --- Operation ID resolution ---


def _resolve_read(op_id: str, parameters: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Resolve a GET operation ID to (concrete_path, remaining_params)."""
    _gate(op_id)
    from seahaven_stripe_world.dispatch.router import ROUTER

    route = ROUTER.resolve_op_id(op_id)
    if route is None or route.method != "GET":
        raise UnknownOperation(op_id)
    return _build_path(route.pattern, parameters)


def _resolve_write(op_id: str, parameters: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    """Resolve a POST/DELETE operation ID to (method, concrete_path, remaining_params)."""
    _gate(op_id)
    from seahaven_stripe_world.dispatch.router import ROUTER

    route = ROUTER.resolve_op_id(op_id)
    if route is None or route.method not in ("POST", "DELETE"):
        raise UnknownOperation(op_id)
    path, remaining = _build_path(route.pattern, parameters)
    return route.method, path, remaining


def _build_path(pattern: str, parameters: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Extract path parameter values from ``parameters``, build the concrete
    path, and return (concrete_path, remaining_params).

    Path placeholder names in the route pattern (e.g. ``{customer}``) are
    pulled from the parameters dict.  If a path parameter is missing, the
    placeholder stays in the path and the dispatcher will handle the error.
    """
    remaining = dict(parameters)
    segments = pattern.split("/")
    concrete = []
    for segment in segments:
        if segment.startswith("{") and segment.endswith("}"):
            name = segment[1:-1]
            value = remaining.pop(name, None)
            if value is not None:
                concrete.append(str(value))
            else:
                concrete.append(segment)
        else:
            concrete.append(segment)
    return "/".join(concrete), remaining


# --- The four registered tools ---


@world.tool(description=_descriptions.STRIPE_API_READ)
def stripe_api_read(
    ctx: seahaven.Ctx,
    stripe_api_operation_id: OperationId,
    parameters: Parameters,
    stripe_context: StripeContext,
    livemode: LiveMode,
) -> Any:
    """Dispatch a Stripe GET operation by operation ID."""
    _context.check(ctx, stripe_context, livemode)
    path, params = _resolve_read(stripe_api_operation_id, parameters)
    return dispatch(ctx, "GET", path, params, idempotency_key=None)


@world.tool(description=_descriptions.STRIPE_API_WRITE)
def stripe_api_write(
    ctx: seahaven.Ctx,
    stripe_api_operation_id: OperationId,
    parameters: Parameters,
    stripe_context: StripeContext,
    livemode: LiveMode,
    human_confirmation: Annotated[
        dict[str, Any],
        Field(
            default_factory=dict,
            description="This tool might require human confirmation before "
            "execution. If the previous tool call returned needs_confirmation "
            "with a token, pass it here to confirm and execute.",
            json_schema_extra={"properties": _HUMAN_CONFIRMATION_PROPERTIES},
        ),
    ],
) -> Any:
    """Dispatch a Stripe POST or DELETE operation by operation ID."""
    _context.check(ctx, stripe_context, livemode)
    method, path, params = _resolve_write(stripe_api_operation_id, parameters)
    # human_confirmation is accepted and ignored — this world never demands
    # approval (architecture section 2.2).
    return dispatch(ctx, method, path, params, idempotency_key=None)


@world.tool(description=_descriptions.STRIPE_API_SEARCH)
def stripe_api_search(
    ctx: seahaven.Ctx,
    intent: Intent,
    resource: Resource,
    stripe_context: StripeContext,
    livemode: LiveMode,
    limit: Limit = 5,
) -> Any:
    """Search for Stripe API operations by intent and resource."""
    _context.check(ctx, stripe_context, livemode)
    # Returns the wrapped envelope: {openapi_spec_version, data}
    return discovery.search(intent, resource, limit=limit)


@world.tool(description=_descriptions.STRIPE_API_DETAILS)
def stripe_api_details(
    ctx: seahaven.Ctx,
    stripe_api_operation_id: DetailsOperationId,
    stripe_context: StripeContext,
    livemode: LiveMode,
) -> Any:
    """Get parameter details for a specific Stripe API operation."""
    _context.check(ctx, stripe_context, livemode)
    # Absent operations are bucket A regardless of context.
    if is_absent(stripe_api_operation_id):
        raise UnknownOperation(stripe_api_operation_id)
    # The discovery index covers all catalogued operations, so both
    # routed and catalogued-but-unrouted operations get a details document.
    documented = discovery.details(stripe_api_operation_id)
    if documented is None:
        raise UnknownOperation(stripe_api_operation_id)
    return documented


# --- The unregistered raw-HTTP face ---


def call_stripe(
    ctx: seahaven.Ctx,
    method: str,
    path: str,
    params: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> Any:
    """The raw-HTTP face of functional spec section 2.5: deliberately
    unregistered, because it is not the shape Stripe ships.  Test-only
    callers (conformance replay, probe worlds) register it on a throwaway
    world via ``conftest.dispatch_tool()``."""
    return dispatch(ctx, method, path, params, idempotency_key=idempotency_key)
