"""The subscription and payout drivers on a blank test-mode instance (replay.md §5, §6, §8)."""

import pytest
import seahaven
from fixtures_src import accounts
from fixtures_src.replay import cards, checks
from fixtures_src.replay.payouts import Payouts
from fixtures_src.replay.subscriptions import NoHooks, finalize_when_due
from fixtures_src.replay.timeline import FixtureBuildError

from replay.support import NOW, Story, blank, plus, rows
from seahaven_stripe_world import _time

START = "2026-05-24T10:00:00.000Z"  # 100 days before NOW


def test_monthly_subscription_renews_to_now() -> None:
    with blank() as inst:
        with inst.bulk() as ctx:
            story = Story(ctx)
            story.signup(START, "a")
            story.timeline.run()
        sub = story.ids["a"]
        invoices = rows(
            inst,
            "SELECT status, billing_reason, period_start, period_end FROM invoices"
            " WHERE parent_subscription = ? AND billing_reason = 'subscription_cycle'"
            " ORDER BY x_seq",
            sub,
        )
        assert [i["status"] for i in invoices] == ["paid"] * 3
        assert [(i["period_start"], i["period_end"]) for i in invoices] == [
            ("2026-05-24T10:00:00.000Z", "2026-06-24T10:00:00.000Z"),
            ("2026-06-24T10:00:00.000Z", "2026-07-24T10:00:00.000Z"),
            ("2026-07-24T10:00:00.000Z", "2026-08-24T10:00:00.000Z"),
        ]
        item = rows(
            inst, "SELECT current_period_end FROM subscription_items WHERE subscription = ?", sub
        )
        assert item == [{"current_period_end": "2026-09-24T10:00:00.000Z"}]
        # The renewal is Stripe's own action: its events carry no request.
        renewal_events = rows(
            inst,
            "SELECT request_id FROM events WHERE created = '2026-08-24T10:00:00.000Z'"
            " ORDER BY x_seq",
        )
        assert renewal_events and all(e["request_id"] is None for e in renewal_events)


def test_trial_without_card_pauses_at_its_end() -> None:
    with blank() as inst:
        with inst.bulk() as ctx:
            story = Story(ctx)
            story.signup(START, "t", card=None, trial_days=14)
            story.timeline.run()
        status = rows(
            inst, "SELECT status, trial_end FROM subscriptions WHERE id = ?", story.ids["t"]
        )
        assert status == [{"status": "paused", "trial_end": plus(START, days=14)}]
        paused = rows(
            inst,
            "SELECT created, request_id FROM events WHERE type = 'customer.subscription.paused'",
        )
        assert paused == [{"created": plus(START, days=14), "request_id": None}]


def test_declining_card_walks_dunning_to_cancel() -> None:
    with blank() as inst:
        with inst.bulk() as ctx:
            story = Story(ctx)
            story.signup(START, "d")
            story.swap_card("2026-07-20T09:00:00.000Z", "d", cards.DECLINING)
            story.timeline.run()
        sub = rows(
            inst,
            "SELECT status, canceled_at, latest_invoice FROM subscriptions WHERE id = ?",
            story.ids["d"],
        )[0]
        # Wrenfield: 8 tries within 2 weeks, then cancel; retries every 14d / 8 = 42 hours.
        assert sub["status"] == "canceled"
        assert sub["canceled_at"] == plus("2026-07-24T10:00:00.000Z", hours=7 * 42)
        invoice = rows(
            inst,
            "SELECT status, attempt_count FROM invoices WHERE id = ?",
            sub["latest_invoice"],
        )
        assert invoice == [{"status": "open", "attempt_count": 8}]


def test_declining_card_walks_dunning_to_mark_unpaid() -> None:
    """Ostrander's dunning: 8 tries within 3 weeks, then the subscription is left `unpaid` with
    its invoice open and no further attempt."""
    with blank() as inst:
        with inst.bulk() as ctx:
            story = Story(ctx, account=accounts.OSTRANDER)
            story.signup(START, "u")
            story.swap_card("2026-07-20T09:00:00.000Z", "u", cards.DECLINING)
            story.timeline.run()
        sub = rows(inst, "SELECT status, ended_at FROM subscriptions WHERE id = ?", story.ids["u"])
        assert sub == [{"status": "unpaid", "ended_at": None}]
        # The declined 07-24 renewal: every try spent, still open. The unpaid subscription's
        # 08-24 renewal is left a draft.
        invoices = rows(
            inst,
            "SELECT created, status, attempt_count, next_payment_attempt FROM invoices"
            " WHERE parent_subscription = ? AND created >= '2026-07-24' ORDER BY x_seq",
            story.ids["u"],
        )
        assert [(i["created"], i["status"]) for i in invoices] == [
            ("2026-07-24T10:00:00.000Z", "open"),
            ("2026-08-24T10:00:00.000Z", "draft"),
        ]
        assert (invoices[0]["attempt_count"], invoices[0]["next_payment_attempt"]) == (8, None)


class _RecoverOnThirdTry(NoHooks):
    def __init__(self) -> None:
        self.story: Story | None = None
        self.moves: list[tuple[str, str]] = []

    def on_retry(self, ctx: seahaven.Ctx, sub_id: str, invoice_id: str, attempt: int) -> bool:
        if attempt < 3 or self.story is None:
            return False
        customer = self.story.ids["r.customer"]
        self.story.client.add_card(ctx, customer, cards.GOOD[2], exp=(9, 2030))
        return True

    def on_status_change(self, ctx: seahaven.Ctx, sub_id: str, old: str, new: str) -> None:
        self.moves.append((old, new))


def test_retry_recovers_when_hook_pays() -> None:
    hooks = _RecoverOnThirdTry()
    with blank() as inst:
        with inst.bulk() as ctx:
            story = Story(ctx, hooks)
            hooks.story = story
            story.signup(START, "r")
            story.swap_card("2026-07-20T09:00:00.000Z", "r", cards.DECLINING)
            story.timeline.run()
        sub = rows(
            inst, "SELECT status, latest_invoice FROM subscriptions WHERE id = ?", story.ids["r"]
        )[0]
        assert sub["status"] == "active"
        assert ("active", "past_due") in hooks.moves and ("past_due", "active") in hooks.moves
        paid = rows(
            inst,
            "SELECT status FROM invoices WHERE parent_subscription = ? AND created = ?",
            story.ids["r"],
            "2026-07-24T10:00:00.000Z",
        )
        assert paid == [{"status": "paid"}]


def test_incomplete_expires_after_23_hours() -> None:
    with blank() as inst:
        with inst.bulk() as ctx:
            story = Story(ctx)
            story.signup(
                START, "i", card=cards.AUTH_REQUIRED, payment_behavior="default_incomplete"
            )
            story.timeline.run()
        sub = rows(inst, "SELECT status, ended_at FROM subscriptions WHERE id = ?", story.ids["i"])
        assert sub == [
            {"status": "incomplete_expired", "ended_at": plus(START, hours=23)},
        ]


def test_payouts_arrive_or_stay_pending() -> None:
    with blank() as inst:
        with inst.bulk() as ctx:
            story = Story(ctx)
            story.signup(START, "a")
            mondays = [plus("2026-06-01T15:00:00.000Z", days=7 * k) for k in range(14)]
            Payouts(
                story.timeline, story.client, destination=accounts.WRENFIELD.bank_account_id
            ).schedule(mondays)
            story.timeline.run()
        payouts = rows(inst, "SELECT status, arrival_date, destination FROM payouts ORDER BY x_seq")
        assert payouts
        for payout in payouts:
            assert payout["destination"] == accounts.WRENFIELD.bank_account_id
            assert payout["status"] == ("paid" if payout["arrival_date"] <= NOW else "pending")
        assert payouts[-1]["status"] == "pending"  # 2026-08-31's arrives after NOW
        failures = checks.violations(inst.inspect(), now=NOW)
        assert [f for f in failures if f.startswith("no payout overdrew")] == []


def test_automatic_finalization_carries_no_request() -> None:
    """A draft finalized when its `automatically_finalizes_at` comes is Stripe acting on its own:
    the finalization carries no request, where the call that made the draft carried one."""
    opened: list[str] = []
    due: list[str] = []
    with blank() as inst:
        with inst.bulk() as ctx:
            story = Story(ctx)

            def onboarding(ctx: seahaven.Ctx) -> None:
                customer = story.client.create_customer(
                    ctx, name="Customer o", email="o@okaforpiano.com"
                )
                story.client.call(
                    ctx,
                    "POST",
                    "/v1/invoiceitems",
                    {"customer": customer, "amount": 4900, "currency": "usd"},
                )
                draft = story.client.call(
                    ctx,
                    "POST",
                    "/v1/invoices",
                    {
                        "customer": customer,
                        "collection_method": "send_invoice",
                        "days_until_due": 30,
                        "pending_invoice_items_behavior": "include",
                        "auto_advance": True,
                    },
                )
                due.append(_time.from_unix(draft["automatically_finalizes_at"]))
                assert finalize_when_due(
                    story.timeline, story.client, draft, lambda _ctx, inv: opened.append(inv)
                )

            story.timeline.add(START, "onboarding", onboarding)
            story.timeline.run()
        (invoice,) = rows(inst, "SELECT id, status FROM invoices")
        assert invoice["status"] == "open" and opened == [invoice["id"]]
        events = rows(
            inst,
            "SELECT type, created, request_id FROM events WHERE type IN"
            " ('invoice.created', 'invoice.finalized') ORDER BY x_seq",
        )
        assert [(e["type"], e["created"]) for e in events] == [
            ("invoice.created", START),
            ("invoice.finalized", *due),
        ]
        assert str(events[0]["request_id"]).startswith("req_")
        assert events[1]["request_id"] is None


class _ResetAnchorOnce(NoHooks):
    """At the first boundary's before hook, restart the billing cycle at that instant, which moves
    the period and so abandons the boundary; record every `n` the driver hands out."""

    def __init__(self) -> None:
        self.story: Story | None = None
        self.before: list[int] = []
        self.after: list[int] = []

    def before_boundary(self, ctx: seahaven.Ctx, sub_id: str, n: int) -> None:
        self.before.append(n)
        if len(self.before) == 1 and self.story is not None:
            self.story.client.update_subscription(
                ctx, sub_id, {"billing_cycle_anchor": "now", "proration_behavior": "none"}
            )

    def after_boundary(self, ctx: seahaven.Ctx, sub_id: str, n: int) -> None:
        self.after.append(n)


def test_an_abandoned_boundary_uses_no_number() -> None:
    hooks = _ResetAnchorOnce()
    with blank() as inst, inst.bulk() as ctx:
        story = Story(ctx, hooks)
        hooks.story = story
        story.signup(START, "n")
        story.timeline.run()
    # 06-24 is abandoned (the period moved to end 07-24); 07-24 and 08-24 renew as 1 and 2.
    assert hooks.before == [1, 1, 2]
    assert hooks.after == [1, 2]


def test_a_retry_on_a_superseded_invoice_stops_the_build() -> None:
    """An open invoice whose subscription has moved on to a newer latest invoice would lose its
    remaining retries; the driver refuses to drop them silently."""
    with blank() as inst, inst.bulk() as ctx:
        story = Story(ctx)
        story.signup(START, "s")
        story.swap_card("2026-07-20T09:00:00.000Z", "s", cards.DECLINING)

        def upgrade_at_once(ctx: seahaven.Ctx) -> None:
            """A plan change invoiced at once: its invoice becomes the subscription's latest
            while the declined renewal is still open, with retries to come."""
            product = story.client.call(ctx, "POST", "/v1/products", {"name": "Wrenfield Studio"})
            price = story.client.call(
                ctx,
                "POST",
                "/v1/prices",
                {
                    "product": product["id"],
                    "unit_amount": 2900,
                    "currency": "usd",
                    "recurring": {"interval": "month"},
                },
            )
            item = ctx.db.one(
                "SELECT id FROM subscription_items WHERE subscription = ?", story.ids["s"]
            )
            assert item is not None
            story.client.update_subscription(
                ctx,
                story.ids["s"],
                {
                    "items": [{"id": item["id"], "price": price["id"]}],
                    "proration_behavior": "always_invoice",
                },
            )

        story.timeline.add("2026-07-25T09:00:00.000Z", "upgrade at once", upgrade_at_once)
        superseded = r"still open but no longer sub_\w+'s latest invoice"
        with pytest.raises(FixtureBuildError, match=superseded):
            story.timeline.run()
