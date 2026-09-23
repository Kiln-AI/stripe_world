"""The ``stripe_analytics`` tool: always answers with a Sigma B1 refusal.

Registered with the real Stripe MCP schema and description (captured
in ``tests/surface/real_tool_schemas.json``).  Every intent gets the
product-activation refusal for Sigma -- the measured B1 form
(functional spec section 6, architecture section 4.4).

This is the honest answer: Sigma is a Stripe product an account turns
on, and a key without the analytics permission is a realistic account
state.  Reproducing the real thing would mean a Trino-compatible engine
over Sigma reporting tables, which is not tractable.
"""

from typing import Annotated, Any

import seahaven
from pydantic import Field

from seahaven_stripe_world.errors import CatalogueRefusal
from seahaven_stripe_world.spec.products import SIGMA_PRODUCT
from seahaven_stripe_world.tools import _context, _descriptions
from seahaven_stripe_world.world import world

__all__ = ["stripe_analytics"]

AnalyticsIntent = Annotated[
    str,
    Field(
        description="The intent of the operation",
        json_schema_extra={
            "enum": [
                "execute_query_run",
                "retrieve_query_run",
                "search_query_tables",
                "retrieve_query_table",
                "execute_query_template",
                "retrieve_query_template",
            ]
        },
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

Params = Annotated[
    dict[str, Any],
    Field(
        default_factory=dict,
        description="Parameters for the operation. Required fields depend on the intent.",
    ),
]


@world.tool(description=_descriptions.STRIPE_ANALYTICS)
def stripe_analytics(
    ctx: seahaven.Ctx,
    intent: AnalyticsIntent,
    stripe_context: StripeContext,
    livemode: LiveMode,
    params: Params,
) -> Any:
    """Answer every analytics intent with a Sigma product-activation refusal.

    The B1 form is used: the measured product-activation message naming
    Sigma and its dashboard URL (functional spec section 6).
    """
    _context.check(ctx, stripe_context, livemode)
    # Every intent gets the Sigma B1 refusal -- architecture section 4.4.
    raise CatalogueRefusal(
        "stripe_analytics",
        product=SIGMA_PRODUCT,
        permissions=[],
    )
