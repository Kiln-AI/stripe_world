"""The subscription and payout drivers on a blank test-mode instance (replay.md §5, §6, §8)."""

import seahaven
from fixtures_src import accounts
from fixtures_src.replay import cards
from fixtures_src.replay.payouts import Payouts
from fixtures_src.replay.subscriptions import NoHooks

from replay.support import NOW, Story, blank, plus, rows

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


def test_trial_without_card_pauses() -> None:
    with blank() as inst:
        with inst.bulk() as ctx:
            story = Story(ctx)
            story.signup(START, "t", card=None, trial_days=14)
            story.timeline.run()
        status = rows(inst, "SELECT status FROM subscriptions WHERE id = ?", story.ids["t"])
        assert status == [{"status": "paused"}]


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
