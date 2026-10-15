"""The two finishing steps (replay.md §6): the only writes this package makes to rows the world
wrote, each applying the world's own rule at `NOW`. Both run inside the builder's `bulk()`."""

import seahaven
from fixtures_src.replay.clock import plus

from seahaven_stripe_world.billing import ledger

__all__ = ["refresh_balance_statuses", "trim_events"]


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


def trim_events(ctx: seahaven.Ctx, *, now: str) -> int:
    """Delete events older than `now` - 30 days (Stripe lists only the last 30 days)."""
    return ctx.db.execute("DELETE FROM events WHERE created < ?", plus(now, days=-30)).rowcount
