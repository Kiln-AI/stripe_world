"""The seven ``/v1/*/search`` endpoints.

Each endpoint accepts ``query`` (required), ``limit``, ``page`` and ``expand``
as query parameters. The response uses the ``search_result`` envelope
(``object``, ``data``, ``has_more``, ``next_page``, ``total_count``, ``url``)
with the ``page`` / ``next_page`` pagination model rather than ``starting_after``
(functional spec §3.3).

The seven resources: charges, customers, invoices, payment_intents, prices,
products, subscriptions.
"""

from __future__ import annotations

from typing import Any

import seahaven

from seahaven_stripe_world.dispatch.params import Param, ParamSpec
from seahaven_stripe_world.dispatch.response import Request
from seahaven_stripe_world.resources import (
    charges,
    customers,
    invoices,
    payment_intents,
    prices,
    products,
    subscriptions,
)
from seahaven_stripe_world.search.executor import execute
from seahaven_stripe_world.search.fields import SEARCH_SPECS
from seahaven_stripe_world.search.parser import ParseError, parse
from seahaven_stripe_world.stripe_errors import invalid_request, missing_parameter

__all__ = [
    "CHARGES_SEARCH",
    "CUSTOMERS_SEARCH",
    "INVOICES_SEARCH",
    "PAYMENT_INTENTS_SEARCH",
    "PRICES_SEARCH",
    "PRODUCTS_SEARCH",
    "SUBSCRIPTIONS_SEARCH",
    "search_charges",
    "search_customers",
    "search_invoices",
    "search_payment_intents",
    "search_prices",
    "search_products",
    "search_subscriptions",
]

# --- Param specs for the search endpoints ------------------------------------

# All seven share the same parameter shape: query (required string),
# limit (optional integer), page (optional string). expand is handled
# centrally by the ParamSpec.expand flag.

_SEARCH_BODY: tuple[Param, ...] = (
    Param(name="query", kind="string", required=True),
    Param(name="page", kind="string"),
)

CHARGES_SEARCH = ParamSpec(
    op_id="GetChargesSearch",
    body=_SEARCH_BODY,
    expand=True,
    paginated=True,
    search=True,
)
CUSTOMERS_SEARCH = ParamSpec(
    op_id="GetCustomersSearch",
    body=_SEARCH_BODY,
    expand=True,
    paginated=True,
    search=True,
)
INVOICES_SEARCH = ParamSpec(
    op_id="GetInvoicesSearch",
    body=_SEARCH_BODY,
    expand=True,
    paginated=True,
    search=True,
)
PAYMENT_INTENTS_SEARCH = ParamSpec(
    op_id="GetPaymentIntentsSearch",
    body=_SEARCH_BODY,
    expand=True,
    paginated=True,
    search=True,
)
PRICES_SEARCH = ParamSpec(
    op_id="GetPricesSearch",
    body=_SEARCH_BODY,
    expand=True,
    paginated=True,
    search=True,
)
PRODUCTS_SEARCH = ParamSpec(
    op_id="GetProductsSearch",
    body=_SEARCH_BODY,
    expand=True,
    paginated=True,
    search=True,
)
SUBSCRIPTIONS_SEARCH = ParamSpec(
    op_id="GetSubscriptionsSearch",
    body=_SEARCH_BODY,
    expand=True,
    paginated=True,
    search=True,
)


# --- Shared search handler ----------------------------------------------------


def _do_search(
    ctx: seahaven.Ctx,
    req: Request,
    resource_key: str,
    serializer_fn: Any,
) -> dict[str, Any]:
    """Common implementation for all seven search handlers."""
    query = req.params.get("query")
    if not query:
        raise missing_parameter("query")

    # Parse the search query.
    try:
        clauses, combinator = parse(str(query))
    except ParseError as e:
        raise invalid_request(
            str(e.stripe_message),
            param="query",
            pre_execution=True,
        ) from e

    spec = SEARCH_SPECS[resource_key]

    # Limit comes from the centrally-lifted Page (paginated=True on the
    # ParamSpec); the page cursor is a body param.
    limit = req.page.limit if req.page is not None else None
    page_cursor = req.params.get("page")
    if isinstance(page_cursor, str) and page_cursor == "":
        page_cursor = None

    return execute(
        ctx,
        spec,
        clauses,
        combinator,
        serializer_fn,
        query_string=str(query),
        limit=limit,
        page_cursor=str(page_cursor) if page_cursor is not None else None,
        include_total_count=req.include_total_count,
    )


# --- Per-resource handlers ----------------------------------------------------


def search_charges(ctx: seahaven.Ctx, req: Request) -> dict[str, Any]:
    return _do_search(ctx, req, "charges", charges.serialize)


def search_customers(ctx: seahaven.Ctx, req: Request) -> dict[str, Any]:
    return _do_search(ctx, req, "customers", customers.SPEC.serializer)


def search_invoices(ctx: seahaven.Ctx, req: Request) -> dict[str, Any]:
    return _do_search(ctx, req, "invoices", invoices.SPEC.serializer)


def search_payment_intents(ctx: seahaven.Ctx, req: Request) -> dict[str, Any]:
    return _do_search(ctx, req, "payment_intents", payment_intents.serialize)


def search_prices(ctx: seahaven.Ctx, req: Request) -> dict[str, Any]:
    return _do_search(ctx, req, "prices", prices.SPEC.serializer)


def search_products(ctx: seahaven.Ctx, req: Request) -> dict[str, Any]:
    return _do_search(ctx, req, "products", products.SPEC.serializer)


def search_subscriptions(ctx: seahaven.Ctx, req: Request) -> dict[str, Any]:
    return _do_search(ctx, req, "subscriptions", subscriptions.serialize)
