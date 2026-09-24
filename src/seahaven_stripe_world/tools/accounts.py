"""The account tools: three faces over one account configuration.

``list_available_accounts_or_orgs`` and ``get_stripe_account_info`` are
read-only projections of ``ctx.state["account"]`` (built in ``startup.py``).
``manage_stripe_accounts`` returns a deterministic reconsent URL.

Neither ``list_available_accounts_or_orgs`` nor ``manage_stripe_accounts``
takes ``stripe_context`` or ``livemode`` -- the real Stripe MCP server does
not require them on these two tools (functional spec section 4.3).
"""

import string
from typing import Any

import seahaven

from seahaven_stripe_world.tools import _descriptions
from seahaven_stripe_world.world import world

__all__ = [
    "get_stripe_account_info",
    "list_available_accounts_or_orgs",
    "manage_stripe_accounts",
]

_RECONSENT_URL_PREFIX = "https://access.stripe.com/mcp/oauth2/authorize/sessions/"
_OASES_PREFIX = "oases_"
_OASES_SUFFIX_LEN = 24
_OASES_ALPHABET = string.ascii_letters + string.digits


@world.tool(description=_descriptions.LIST_AVAILABLE_ACCOUNTS_OR_ORGS)
def list_available_accounts_or_orgs(ctx: seahaven.Ctx) -> dict[str, Any]:
    """Return the accounts available in this session.

    A one-element list projected from the same account configuration
    that ``get_stripe_account_info`` serves (functional spec section 4.1).
    """
    account = ctx.state.get("account")
    if not isinstance(account, dict):
        raise seahaven.WorldBug("list_available_accounts_or_orgs: no account in ctx.state")
    return {
        "accounts": [
            {
                "stripe_context": account["id"],
                "livemode": account["livemode"],
                "name": account["name"],
            }
        ]
    }


@world.tool(description=_descriptions.GET_STRIPE_ACCOUNT_INFO)
def get_stripe_account_info(ctx: seahaven.Ctx) -> dict[str, Any]:
    """Retrieve the account object for this Stripe account.

    Returns the full account object with billing-relevant fields. Kept
    deliberately even though the live MCP no longer exposes it (functional
    spec section 4.1.1).
    """
    account = ctx.state.get("account")
    if not isinstance(account, dict) or "object" not in account:
        raise seahaven.WorldBug("get_stripe_account_info: no account in ctx.state")
    return account["object"]


@world.tool(description=_descriptions.MANAGE_STRIPE_ACCOUNTS)
def manage_stripe_accounts(ctx: seahaven.Ctx) -> dict[str, str]:
    """Return a URL where the user can manage connected accounts.

    The URL points to Stripe's OAuth consent flow. The ``oases_`` session
    id is drawn from the instance's seeded stream for determinism.
    """
    suffix = "".join(ctx.ids.random.choice(_OASES_ALPHABET) for _ in range(_OASES_SUFFIX_LEN))
    session_id = f"{_OASES_PREFIX}{suffix}"
    return {"reconsent_url": f"{_RECONSENT_URL_PREFIX}{session_id}"}
