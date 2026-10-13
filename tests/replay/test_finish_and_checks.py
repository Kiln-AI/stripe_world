"""The finishing steps, the checks, and the test cards (replay.md §4, §6, §7, §8)."""

import pytest
from fixtures_src.replay import cards, checks, finish
from fixtures_src.replay.checks import Expectation
from fixtures_src.replay.subscriptions import Hooks

from replay.support import NOW, Story, blank, plus, rows
from seahaven_stripe_world.billing import magic_cards

START = "2026-05-24T10:00:00.000Z"


def _tiny(inst, *, hooks: Hooks | None = None) -> None:
    with inst.bulk() as ctx:
        story = Story(ctx, hooks)
        story.signup(START, "a")
        story.signup(plus(START, days=20), "b")
        story.swap_card("2026-07-20T09:00:00.000Z", "b", cards.DECLINING)
        story.timeline.run()
        finish.refresh_balance_statuses(ctx, now=NOW)
        finish.trim_events(ctx, now=NOW)


def test_refresh_and_trim_touch_exactly_the_stale_rows() -> None:
    with blank() as inst:
        with inst.bulk() as ctx:
            story = Story(ctx)
            story.signup(START, "a")
            story.timeline.run()
            stale = ctx.db.one(
                "SELECT count(*) AS n FROM balance_transactions"
                " WHERE status = 'pending' AND available_on <= ?",
                NOW,
            )
            old = ctx.db.one(
                "SELECT count(*) AS n FROM events WHERE created < ?", plus(NOW, days=-30)
            )
            assert stale is not None and old is not None and stale["n"] > 0 and old["n"] > 0
            assert finish.refresh_balance_statuses(ctx, now=NOW) == stale["n"]
            assert finish.trim_events(ctx, now=NOW) == old["n"]
            assert finish.refresh_balance_statuses(ctx, now=NOW) == 0
        pending = rows(
            inst, "SELECT available_on FROM balance_transactions WHERE status = 'pending'"
        )
        assert all(p["available_on"] > NOW for p in pending)
        assert rows(inst, "SELECT min(created) AS m FROM events")[0]["m"] >= plus(NOW, days=-30)


def test_check_fixture_passes_on_a_tiny_build() -> None:
    with blank() as inst:
        _tiny(inst)
        checks.check_fixture(
            inst.inspect(),
            now=NOW,
            expect=(
                Expectation("customers", "SELECT count(*) AS n FROM customers", 2, 2),
                Expectation(
                    "canceled",
                    "SELECT count(*) AS n FROM subscriptions WHERE status = 'canceled'",
                    1,
                    1,
                ),
            ),
        )


def test_checks_catch_planted_violations() -> None:
    with blank() as inst:
        _tiny(inst)
        with inst.bulk() as ctx:
            ctx.db.execute(
                "UPDATE customers SET email = 'someone@gmail.com', balance = 500"
                " WHERE id = (SELECT id FROM customers ORDER BY x_seq LIMIT 1)"
            )
            ctx.db.execute(
                "UPDATE balance_transactions SET status = 'pending'"
                " WHERE id = (SELECT id FROM balance_transactions ORDER BY x_seq LIMIT 1)"
            )
        failures = checks.violations(
            inst.inspect(),
            now=NOW,
            expect=(Expectation("customers", "SELECT count(*) AS n FROM customers", 5, 9),),
        )
        joined = "\n".join(failures)
        for name in (
            "no webmail or example email domains",
            "customer balance is the latest ending balance",
            "balance status agrees with bt_status at NOW",
            "expectation customers: 2, wanted 5..9",
        ):
            assert name in joined
        with pytest.raises(AssertionError, match="fixture checks failed"):
            checks.check_fixture(inst.inspect(), now=NOW)


def test_cards_match_the_world() -> None:
    for card in cards.GOOD:
        behavior = magic_cards.card_for(card.number)
        assert behavior.brand == card.brand
    assert sum(card.weight for card in cards.GOOD) == 100
    assert magic_cards.card_for(cards.DECLINING.number).brand == "visa"
