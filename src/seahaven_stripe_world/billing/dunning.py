"""Dunning: Smart Retries' configuration envelope, the `attempt_count`
counter semantics, hard-decline gating, and the three end-of-schedule
outcomes (`components/billing_engine.md` §5).

Smart Retries is ML-scheduled; there is no retry-day table in Stripe's
documentation, and inventing one would be inventing behavior. This module
models the four documented facts and nothing else:

1. The envelope — N tries within a window (Stripe's recommended default of
   8 within 2 weeks), with the three end-of-schedule outcomes.
2. `attempt_count` — the first attempt (however made) sets 1; after that,
   only AUTOMATIC retries increment. `spec3.json`'s own
   `invoice.attempt_count` description is the most implementation-precise
   statement in the whole area.
3. Hard-decline gating — nine decline codes for which the schedule keeps
   running and the counter keeps climbing while nothing reaches the issuer.
4. The outcomes — cancel / mark_unpaid / leave_past_due.

What a frozen-clock world cannot model, stated plainly: no automatic retry
can ever fire inside a live instance (`ctx.clock` never advances), so
everything past attempt 1 is reachable only through `record_failed_attempt`
/ `apply_end_of_schedule` — the fixture-generator and test surface — and
`next_payment_attempt` is an honest future timestamp that never arrives
(same contract as `automatically_finalizes_at`). `next_payment_attempt`'s
spacing is this world's declared even-spacing stand-in, never a guess at
Stripe's ML schedule.
"""

import seahaven

from seahaven_stripe_world import _json, _time
from seahaven_stripe_world.resources import _lookup, events

__all__ = [
    "RETRY_BLOCKING_DECLINE_CODES",
    "WINDOW_SECONDS",
    "RetryPolicy",
    "apply_end_of_schedule",
    "next_attempt_at",
    "next_attempt_number",
    "policy",
    "record_failed_attempt",
    "schedule_exhausted",
    "will_reach_network",
]

#: The nine hard-decline codes (docs.stripe.com/billing/revenue-recovery/
#: smart-retries, read directly 2026-09-18): for these the schedule keeps
#: running and `attempt_count` keeps incrementing, but no authorization is
#: sent to the issuer until a new payment method is attached.
#:
#: Eight of the nine are members of the spec's 50-value `decline_code`
#: enumeration; `highest_risk_level` is NOT — a real docs/spec divergence
#: at the pinned version (the docs' Smart Retries page lists it; the
#: extracted enum does not carry it). The docs win for the gating list and
#: the dunning test pins the divergence by name, so a future spec version
#: adding it shows up as exactly one assertion to relax.
RETRY_BLOCKING_DECLINE_CODES: frozenset[str] = frozenset(
    {
        "incorrect_number",
        "lost_card",
        "pickup_card",
        "stolen_card",
        "revocation_of_authorization",
        "revocation_of_all_authorizations",
        "authentication_required",
        "highest_risk_level",
        "transaction_not_allowed",
    }
)

#: The documented window choices, in seconds. A "month" here is a fixed
#: 30 days — the window is a Dashboard-selected span, not a calendar walk,
#: and a deterministic constant is the honest spelling.
WINDOW_SECONDS: dict[str, int] = {
    "1w": 7 * 86_400,
    "2w": 14 * 86_400,
    "3w": 21 * 86_400,
    "1mo": 30 * 86_400,
    "2mo": 60 * 86_400,
}


class RetryPolicy:
    """The account-level retry configuration. Plain attributes rather than a
    frozen dataclass: `ctx.state["account"]["dunning"]` is the
    instance-config override seam (the same shape `ledger.ledger_spec`
    reads), and Stripe exposes no such field on the subscription object —
    a column would be a fidelity regression, not a feature.

    `end_behavior` has no documented Stripe default (a Dashboard setting no
    research pass recovered); this world's `cancel` default is a declared
    choice in `allowed_differences.py`, not a transcription.
    """

    def __init__(
        self,
        *,
        max_attempts: int = 8,
        window: str = "2w",
        end_behavior: str = "cancel",
    ) -> None:
        self.max_attempts = max_attempts
        self.window = window
        self.end_behavior = end_behavior


END_BEHAVIORS = ("cancel", "mark_unpaid", "leave_past_due")


def policy(ctx: seahaven.Ctx) -> RetryPolicy:
    """The instance's dunning config: `ctx.state["account"]["dunning"]` keys
    over the defaults (Stripe's recommended 8 tries within 2 weeks), so a
    harness can reconfigure an instance without a schema change. Invalid
    keys are an authoring fault HERE, at the seam — not a `KeyError` from
    `WINDOW_SECONDS` deep inside a walker, and not a typo'd `end_behavior`
    silently behaving as `leave_past_due`."""
    account = ctx.state.get("account")
    config = account.get("dunning") if isinstance(account, dict) else None
    if not isinstance(config, dict):
        return RetryPolicy()
    window = str(config.get("window", "2w"))
    if window not in WINDOW_SECONDS:
        raise seahaven.WorldBug(
            f"account.dunning.window {window!r} is not one of {sorted(WINDOW_SECONDS)}"
        )
    end_behavior = str(config.get("end_behavior", "cancel"))
    if end_behavior not in END_BEHAVIORS:
        raise seahaven.WorldBug(
            f"account.dunning.end_behavior {end_behavior!r} is not one of {END_BEHAVIORS}"
        )
    return RetryPolicy(
        max_attempts=int(config.get("max_attempts", 8)),
        window=window,
        end_behavior=end_behavior,
    )


# --- the pure core ---------------------------------------------------------------------


def next_attempt_number(attempt_count: int, *, automatic: bool) -> int:
    """`spec3.json`'s own rules: ANY first attempt is attempt 1; after that,
    only automatic retries increment — a manual `POST
    /v1/invoices/{id}/pay` neither consumes a schedule slot nor bumps the
    counter."""
    if attempt_count == 0:
        return 1
    return attempt_count + 1 if automatic else attempt_count


def will_reach_network(last_decline_code: str | None) -> bool:
    """False for the nine hard declines: the retry is scheduled (and
    counted) but never sent to the issuer until a new payment method
    arrives."""
    return last_decline_code not in RETRY_BLOCKING_DECLINE_CODES


def schedule_exhausted(attempt_count: int, p: RetryPolicy) -> bool:
    return attempt_count >= p.max_attempts


def next_attempt_at(from_iso: str, attempt_count: int, p: RetryPolicy) -> str | None:
    """The scheduled instant of the next retry, or None when the schedule
    is exhausted (the field clears — the invoice will not be tried again).

    The spacing is this world's declared even-spacing stand-in
    (`window / max_attempts` per step), not a guess at Stripe's ML schedule:
    `allowed_differences.py` declares that it will not match what the real
    API would have chosen.
    """
    if schedule_exhausted(attempt_count, p):
        return None
    spacing = WINDOW_SECONDS[p.window] // max(p.max_attempts, 1)
    return _time.from_unix(_time.to_unix(from_iso) + spacing)


# --- the stateful walkers (fixture-generator and test surface) -------------------------


def _subscription_of(ctx: seahaven.Ctx, invoice_id: str) -> dict[str, object] | None:
    invoice = ctx.db.one("SELECT * FROM invoices WHERE id = ?", invoice_id)
    if invoice is None or invoice["parent_subscription"] is None:
        return None
    sub = ctx.db.one("SELECT * FROM subscriptions WHERE id = ?", invoice["parent_subscription"])
    if sub is None or sub["latest_invoice"] != invoice_id:
        return None
    return dict(sub)


def record_failed_attempt(
    ctx: seahaven.Ctx,
    invoice_id: str,
    *,
    decline_code: str | None,
    automatic: bool = True,
    at_iso: str | None = None,
) -> dict[str, object]:
    """Record one failed attempt on an open invoice: advance the counter per
    `next_attempt_number`, move `next_payment_attempt`, and run the
    end-of-schedule outcome when the schedule is exhausted.

    A retry blocked by a hard decline (`will_reach_network` False) writes
    NO charge row and emits NO event — the counter moves and the schedule
    advances, and that is all. "Nothing in the change log because nothing
    was attempted" is the observable distinction a careless mock erases.

    Unrouted by design: within a frozen clock only the fixture generator
    and tests reach past attempt 1 (billing_engine §5).
    """
    row = _lookup.require_row(ctx, "invoices", "invoice", invoice_id, param="invoice")
    if row["status"] != "open":
        raise seahaven.WorldBug(f"record_failed_attempt on a {row['status']!r} invoice")
    now = at_iso if at_iso is not None else ctx.clock.iso()
    p = policy(ctx)
    count = next_attempt_number(row["attempt_count"], automatic=automatic)
    reached_network = will_reach_network(decline_code)
    ctx.db.execute(
        "UPDATE invoices SET attempted = 1, attempt_count = ?, next_payment_attempt = ?"
        " WHERE id = ?",
        count,
        next_attempt_at(now, count, p),
        invoice_id,
    )
    if reached_network:
        from seahaven_stripe_world.billing import invoicing

        invoicing.emit_invoice_event(ctx, "invoice.payment_failed", _re_read(ctx, invoice_id))
    sub = _subscription_of(ctx, invoice_id)
    if sub is not None and schedule_exhausted(count, p):
        apply_end_of_schedule(ctx, str(sub["id"]))
    return _re_read(ctx, invoice_id)


def _re_read(ctx: seahaven.Ctx, invoice_id: str) -> dict[str, object]:
    return _lookup.require_row(ctx, "invoices", "invoice", invoice_id, param="invoice")


def apply_end_of_schedule(ctx: seahaven.Ctx, subscription_id: str) -> dict[str, object]:
    """The three documented outcomes of an exhausted retry schedule
    (billing_engine §1's `past_due` rows). The open invoice that caused the
    dunning is LEFT OPEN in every branch — Stripe does not void it; it is
    the invoice whose payment recovers the subscription."""
    from seahaven_stripe_world.billing import subscription_lifecycle

    row = _lookup.require_row(ctx, "subscriptions", "subscription", subscription_id, param="id")
    if row["status"] != "past_due":
        raise seahaven.WorldBug(
            f"apply_end_of_schedule on a {row['status']!r} subscription: {subscription_id!r}"
        )
    old_body = subscription_lifecycle._serialize(ctx, row)
    p = policy(ctx)
    now = ctx.clock.iso()
    if p.end_behavior == "cancel":
        ctx.db.execute(
            "UPDATE subscriptions SET status = 'canceled', canceled_at = ?, ended_at = ?,"
            " cancellation_details = ? WHERE id = ?",
            now,
            now,
            _json.dumps(
                {
                    "comment": None,
                    "feedback": None,
                    "feedback_option": None,
                    "reason": "payment_failed",
                }
            ),
            subscription_id,
        )
        fresh = _lookup.require_row(
            ctx, "subscriptions", "subscription", subscription_id, param="id"
        )
        events.emit_event(
            ctx,
            type="customer.subscription.deleted",
            obj=subscription_lifecycle._serialize(ctx, fresh),
        )
        return dict(fresh)
    if p.end_behavior == "mark_unpaid":
        ctx.db.execute("UPDATE subscriptions SET status = 'unpaid' WHERE id = ?", subscription_id)
    else:
        # leave_past_due: nothing changes and NO event fires — billing_engine
        # §1's `past_due` table lists none for this row (a degenerate
        # "updated" with empty previous_attributes would be noise). Future
        # cycle invoices run their own schedules (advance_cycle's walkers).
        return dict(
            _lookup.require_row(ctx, "subscriptions", "subscription", subscription_id, param="id")
        )
    fresh = _lookup.require_row(ctx, "subscriptions", "subscription", subscription_id, param="id")
    body = subscription_lifecycle._serialize(ctx, fresh)
    events.emit_event(
        ctx,
        type="customer.subscription.updated",
        obj=body,
        previous=subscription_lifecycle._previous(old_body, body),
    )
    return dict(fresh)
