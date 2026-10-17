"""The four finishing steps (replay.md §6): the only writes this package makes to rows the world
wrote, each applying the world's own rule at `NOW`. All run inside the builder's `bulk()`."""

import seahaven
from fixtures_src.replay.clock import plus

from seahaven_stripe_world import _json
from seahaven_stripe_world.billing import ledger, subscription_lifecycle
from seahaven_stripe_world.resources import coupons

__all__ = [
    "prune_ended_discounts",
    "refresh_balance_statuses",
    "refresh_coupon_validity",
    "trim_events",
]


def refresh_balance_statuses(ctx: seahaven.Ctx, *, now: str) -> int:
    """For every balance transaction stored `pending`, store `ledger.bt_status(available_on, now)`
    where that differs. Returns the number changed."""
    changed = 0
    for row in ctx.db.rows(
        "SELECT id, available_on FROM balance_transactions WHERE status = 'pending' ORDER BY x_seq"
    ):
        status = ledger.bt_status(row["available_on"], now)
        if status != "pending":
            ctx.db.execute(
                "UPDATE balance_transactions SET status = ? WHERE id = ?", status, row["id"]
            )
            changed += 1
    return changed


def refresh_coupon_validity(ctx: seahaven.Ctx, *, now: str) -> int:
    """For every coupon, store `coupons.is_valid(row, now)` as `valid` where that differs: a
    `redeem_by` passes with no write to mark it, so the stored column (the rule as of the row's
    last write) would read stale at `NOW`. Returns the number changed."""
    changed = 0
    for row in ctx.db.rows("SELECT * FROM coupons ORDER BY x_seq"):
        valid = int(coupons.is_valid(row, now))
        if valid != row["valid"]:
            ctx.db.execute("UPDATE coupons SET valid = ? WHERE id = ?", valid, row["id"])
            changed += 1
    return changed


def prune_ended_discounts(ctx: seahaven.Ctx, *, now: str) -> int:
    """For every subscription, drop the stored discounts `subscription_lifecycle.discount_ended`
    says have ended by `now`: the API already hides them, and no invoice has billed since to prune
    them from the row. Returns the number of subscriptions changed."""
    changed = 0
    for row in ctx.db.rows("SELECT id, discounts FROM subscriptions ORDER BY x_seq"):
        stored: object = _json.loads(row["discounts"])
        discounts = stored if isinstance(stored, list) else []
        kept = [d for d in discounts if not subscription_lifecycle.discount_ended(d, now)]
        if len(kept) != len(discounts):
            ctx.db.execute(
                "UPDATE subscriptions SET discounts = ? WHERE id = ?", _json.dumps(kept), row["id"]
            )
            changed += 1
    return changed


def trim_events(ctx: seahaven.Ctx, *, now: str) -> int:
    """Delete events older than `now` - 30 days (Stripe lists only the last 30 days)."""
    return ctx.db.execute("DELETE FROM events WHERE created < ?", plus(now, days=-30)).rowcount
