"""The transitions a frozen clock cannot fire (stripe_world billing_engine.md §7), as steps.

Each runs with a null request context: Stripe acting on its own carries no request id.
"""

from typing import Any

import seahaven

from seahaven_stripe_world.billing import dunning, ledger, subscription_lifecycle

__all__ = ["arrive", "automatic", "expire", "fail_retry", "renew"]


def automatic(ctx: seahaven.Ctx) -> None:
    """Park a null request: what follows is Stripe's own action, not an API request."""
    ctx.state["_request"] = {"id": None, "idempotency_key": None}


def renew(ctx: seahaven.Ctx, sub_id: str) -> dict[str, Any]:
    """Roll one billing period at its boundary (renewal, trial end, cancel at period end)."""
    automatic(ctx)
    return subscription_lifecycle.advance_cycle(ctx, sub_id)


def expire(ctx: seahaven.Ctx, sub_id: str) -> dict[str, Any]:
    """`incomplete` -> `incomplete_expired` at the 23-hour mark."""
    automatic(ctx)
    return subscription_lifecycle.expire_incomplete(ctx, sub_id)


def fail_retry(ctx: seahaven.Ctx, invoice_id: str, decline_code: str) -> dict[str, object]:
    """One failed automatic retry; applies the end-of-schedule outcome when exhausted."""
    automatic(ctx)
    return dunning.record_failed_attempt(
        ctx, invoice_id, decline_code=decline_code, automatic=True, at_iso=ctx.clock.iso()
    )


def arrive(ctx: seahaven.Ctx, payout_id: str) -> dict[str, Any]:
    """A payout's bank arrival: `pending` -> `paid`."""
    automatic(ctx)
    return ledger.settle_payout(ctx, payout_id)
