"""Session validation: stripe_context and livemode checked before dispatch.

Called first in every tool that takes the two context parameters.  Both
refusals are verbatim from the probe (2026-09-22); the live-side
livemode message is inferred (functional spec section 4.7, declared
residue).

Reads ``ctx.state["account"]`` built by ``startup.py``.  A call with a
wrong context or mode never reaches the dispatcher -- no rows are
written, no request id is minted.
"""

import seahaven

from seahaven_stripe_world.errors import SessionValidation

__all__ = ["check"]


def check(ctx: seahaven.Ctx, stripe_context: str, livemode: bool) -> None:
    """Raise the session-validation refusal if either does not match."""
    account = ctx.state.get("account")
    if not isinstance(account, dict):
        raise seahaven.WorldBug("_context.check: no account in ctx.state")

    account_id = account["id"]
    account_livemode = account["livemode"]

    if stripe_context != account_id:
        raise SessionValidation(
            "No account found for the provided stripe_context and livemode. "
            "Use the list_available_accounts_or_orgs tool to see the accounts you can access."
        )

    if livemode != account_livemode:
        if account_livemode:
            # Live instance, agent sent livemode=false (inferred wording)
            raise SessionValidation(
                f"The provided account {account_id} is a live account. "
                "Retry with livemode set to true."
            )
        else:
            # Sandbox instance, agent sent livemode=true (probed verbatim)
            raise SessionValidation(
                f"The provided account {account_id} is a sandbox account. "
                "Retry with livemode set to false."
            )
