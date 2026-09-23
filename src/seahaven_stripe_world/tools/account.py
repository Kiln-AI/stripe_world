"""The `get_stripe_account_info` tool: the account object from startup state.

The account is not stored in the database and is not routable through the
dispatcher. It is built once in `startup.py` and read from
`ctx.state["account"]["object"]` here — so there is one source of truth for
the account id, mode, and every field the two account tools present.

No docstring names another tool (lint SH206).
"""

from typing import Any

import seahaven

from seahaven_stripe_world.world import world

__all__ = ["get_stripe_account_info"]


@world.tool
def get_stripe_account_info(ctx: seahaven.Ctx) -> dict[str, Any]:
    """Retrieve the account object for this Stripe account.

    Returns the account object with billing-relevant fields: business
    profile, capabilities, default currency, and payout settings. The
    account is static within an instance.
    """
    account = ctx.state.get("account")
    if not isinstance(account, dict) or "object" not in account:
        raise seahaven.WorldBug("get_stripe_account_info: no account in ctx.state")
    return account["object"]
