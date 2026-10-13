"""The subscription driver: keeps every subscription moving from its creation to `until`,
asking the company's hooks at each decision point (replay.md §5)."""

from collections.abc import Callable
from typing import Any, Protocol

import seahaven
from fixtures_src.replay import engine
from fixtures_src.replay.cards import DECLINING_CODE
from fixtures_src.replay.client import Client
from fixtures_src.replay.clock import plus
from fixtures_src.replay.timeline import Timeline

from seahaven_stripe_world.billing.subscription_lifecycle import RENEWING_STATUSES

__all__ = ["Hooks", "NoHooks", "Subscriptions", "finalize_when_due"]

_STOPPED = ("canceled", "incomplete_expired", "paused")


class Hooks(Protocol):
    def before_boundary(self, ctx: seahaven.Ctx, sub_id: str, n: int) -> None: ...
    def after_boundary(self, ctx: seahaven.Ctx, sub_id: str, n: int) -> None: ...
    def on_open_invoice(self, ctx: seahaven.Ctx, sub_id: str, invoice_id: str) -> None: ...

    def on_retry(self, ctx: seahaven.Ctx, sub_id: str, invoice_id: str, attempt: int) -> bool:
        """True: the hook made the payment possible and the driver pays; False: it fails."""
        ...

    def on_status_change(self, ctx: seahaven.Ctx, sub_id: str, old: str, new: str) -> None: ...


class NoHooks:
    """Hooks that decide nothing: every retry fails, nothing else happens."""

    def before_boundary(self, ctx: seahaven.Ctx, sub_id: str, n: int) -> None:
        pass

    def after_boundary(self, ctx: seahaven.Ctx, sub_id: str, n: int) -> None:
        pass

    def on_open_invoice(self, ctx: seahaven.Ctx, sub_id: str, invoice_id: str) -> None:
        pass

    def on_retry(self, ctx: seahaven.Ctx, sub_id: str, invoice_id: str, attempt: int) -> bool:
        return False

    def on_status_change(self, ctx: seahaven.Ctx, sub_id: str, old: str, new: str) -> None:
        pass


def _period_end(ctx: seahaven.Ctx, sub_id: str) -> str | None:
    row = ctx.db.one(
        "SELECT MIN(current_period_end) AS end_at FROM subscription_items WHERE subscription = ?",
        sub_id,
    )
    return None if row is None else row["end_at"]


def _invoice(ctx: seahaven.Ctx, invoice_id: str | None) -> dict[str, Any] | None:
    if invoice_id is None:
        return None
    row = ctx.db.one("SELECT * FROM invoices WHERE id = ?", invoice_id)
    return None if row is None else dict(row)


def _finalize_step(
    client: Client, invoice_id: str, on_open: Callable[[seahaven.Ctx, str], None]
) -> Callable[[seahaven.Ctx], None]:
    def run(ctx: seahaven.Ctx) -> None:
        row = _invoice(ctx, invoice_id)
        if row is None or row["status"] != "draft":
            return
        client.call(ctx, "POST", f"/v1/invoices/{invoice_id}/finalize", automatic=True)
        fresh = _invoice(ctx, invoice_id)
        if fresh is not None and fresh["status"] == "open":
            on_open(ctx, invoice_id)

    return run


def finalize_when_due(
    timeline: Timeline,
    client: Client,
    invoice: dict[str, Any],
    on_open: Callable[[seahaven.Ctx, str], None],
) -> bool:
    """Schedule the automatic finalization of a draft made outside a subscription (an onboarding
    invoice) at its `automatically_finalizes_at`; `on_open` runs once it is open. `invoice` is the
    API body (Unix seconds) or a row (canonical text)."""
    when = invoice.get("automatically_finalizes_at")
    if when is None:
        return False
    when_iso = _iso(when)
    return timeline.add(
        when_iso, f"finalize {invoice['id']}", _finalize_step(client, invoice["id"], on_open)
    )


def _iso(value: str | int) -> str:
    if isinstance(value, int):
        return plus("1970-01-01T00:00:00.000Z", seconds=value)
    return value


class Subscriptions:
    """Schedules each followed subscription's next boundary, retry, expiry or finalization.

    Every scheduled step re-reads the row when it runs and does nothing when it has gone stale
    (the period moved, the invoice was paid, the subscription stopped), so `follow` can be called
    again after any change a story makes. Per-subscription state lives in dicts read only by key.
    """

    def __init__(self, timeline: Timeline, client: Client, hooks: Hooks) -> None:
        self.timeline = timeline
        self.client = client
        self.hooks = hooks
        self._boundaries: dict[str, int] = {}
        self._status: dict[str, str] = {}
        self._scheduled: dict[tuple[str, str, str], bool] = {}

    def follow(self, ctx: seahaven.Ctx, sub_id: str) -> None:
        """Call right after a subscription is created or changed. Schedules what happens next."""
        sub = ctx.db.one(
            "SELECT id, status, created, latest_invoice FROM subscriptions WHERE id = ?", sub_id
        )
        if sub is None:
            raise seahaven.WorldBug(f"follow: no subscription {sub_id!r}")
        status = str(sub["status"])
        self._boundaries.setdefault(sub_id, 0)
        self._observe(ctx, sub_id, status)
        if status in _STOPPED:
            return
        if status == "incomplete":
            self._schedule("expire", sub_id, plus(sub["created"], hours=23), self._expire)
        if status in RENEWING_STATUSES:
            end = _period_end(ctx, sub_id)
            if end is not None:
                self._schedule("boundary", sub_id, end, self._boundary)
        latest = _invoice(ctx, sub["latest_invoice"])
        if latest is None:
            return
        if (
            status == "past_due"
            and latest["status"] == "open"
            and latest["next_payment_attempt"] is not None
        ):
            self._schedule("retry", sub_id, latest["next_payment_attempt"], self._retry)
        if (
            latest["status"] == "draft"
            and latest["auto_advance"]
            and latest["automatically_finalizes_at"] is not None
        ):
            key = ("finalize", latest["id"], latest["automatically_finalizes_at"])
            if key not in self._scheduled:
                self._scheduled[key] = True
                invoice_id = str(latest["id"])
                self.timeline.add(
                    latest["automatically_finalizes_at"],
                    f"finalize {invoice_id}",
                    self._after(
                        sub_id,
                        _finalize_step(
                            self.client, invoice_id, self._open_hook(sub_id, invoice_id)
                        ),
                    ),
                )

    # --- scheduling ---------------------------------------------------------------------------

    def _schedule(
        self,
        kind: str,
        sub_id: str,
        when: str,
        make: Callable[[str, str], Callable[[seahaven.Ctx], None]],
    ) -> None:
        key = (kind, sub_id, when)
        if key in self._scheduled:
            return
        self._scheduled[key] = True
        self.timeline.add(when, f"{kind} {sub_id}", self._after(sub_id, make(sub_id, when)))

    def _after(
        self, sub_id: str, run: Callable[[seahaven.Ctx], None]
    ) -> Callable[[seahaven.Ctx], None]:
        """Run the step, then report a status move and schedule what comes next."""

        def step(ctx: seahaven.Ctx) -> None:
            run(ctx)
            self.follow(ctx, sub_id)

        return step

    def _observe(self, ctx: seahaven.Ctx, sub_id: str, status: str) -> None:
        old = self._status.get(sub_id)
        self._status[sub_id] = status
        if old is not None and old != status:
            self.hooks.on_status_change(ctx, sub_id, old, status)

    def _open_hook(self, sub_id: str, invoice_id: str) -> Callable[[seahaven.Ctx, str], None]:
        def on_open(ctx: seahaven.Ctx, _invoice_id: str) -> None:
            row = _invoice(ctx, invoice_id)
            if row is not None and row["collection_method"] == "send_invoice":
                self.hooks.on_open_invoice(ctx, sub_id, invoice_id)

        return on_open

    def _status_of(self, ctx: seahaven.Ctx, sub_id: str) -> str:
        row = ctx.db.one("SELECT status FROM subscriptions WHERE id = ?", sub_id)
        return "" if row is None else str(row["status"])

    # --- the steps ----------------------------------------------------------------------------

    def _boundary(self, sub_id: str, when: str) -> Callable[[seahaven.Ctx], None]:
        def run(ctx: seahaven.Ctx) -> None:
            if self._status_of(ctx, sub_id) not in RENEWING_STATUSES:
                return
            if _period_end(ctx, sub_id) != when:
                return  # stale: the period moved since this was scheduled
            self._boundaries[sub_id] += 1
            n = self._boundaries[sub_id]
            self.hooks.before_boundary(ctx, sub_id, n)
            if self._status_of(ctx, sub_id) not in RENEWING_STATUSES:
                return
            if _period_end(ctx, sub_id) != when:
                return
            renewed = engine.renew(ctx, sub_id)
            self.hooks.after_boundary(ctx, sub_id, n)
            latest = _invoice(ctx, renewed["latest_invoice"])
            if (
                latest is not None
                and latest["status"] == "open"
                and latest["collection_method"] == "send_invoice"
                and latest["created"] == ctx.clock.iso()
            ):
                self.hooks.on_open_invoice(ctx, sub_id, str(latest["id"]))

        return run

    def _retry(self, sub_id: str, when: str) -> Callable[[seahaven.Ctx], None]:
        def run(ctx: seahaven.Ctx) -> None:
            sub = ctx.db.one("SELECT latest_invoice FROM subscriptions WHERE id = ?", sub_id)
            invoice = _invoice(ctx, None if sub is None else sub["latest_invoice"])
            if (
                invoice is None
                or invoice["status"] != "open"
                or invoice["next_payment_attempt"] != when
            ):
                return
            invoice_id = str(invoice["id"])
            attempt = int(invoice["attempt_count"]) + 1
            if self.hooks.on_retry(ctx, sub_id, invoice_id, attempt):
                self.client.pay_invoice(ctx, invoice_id)
            else:
                engine.fail_retry(ctx, invoice_id, DECLINING_CODE)

        return run

    def _expire(self, sub_id: str, _when: str) -> Callable[[seahaven.Ctx], None]:
        def run(ctx: seahaven.Ctx) -> None:
            if self._status_of(ctx, sub_id) == "incomplete":
                engine.expire(ctx, sub_id)

        return run
