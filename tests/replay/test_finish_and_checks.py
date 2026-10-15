"""The finishing steps, the checks, and the test cards (replay.md §4, §6, §7, §8)."""

from collections.abc import Iterator

import pytest
import seahaven
from fixtures_src import accounts
from fixtures_src.replay import cards, checks, finish
from fixtures_src.replay.checks import Expectation
from fixtures_src.replay.payouts import Payouts

from replay.support import NOW, Story, blank, plus, rows
from seahaven_stripe_world import _time
from seahaven_stripe_world.billing import magic_cards

START = "2026-05-24T10:00:00.000Z"
LATER = "2027-01-01T00:00:00.000Z"  # after NOW
EARLIER = "2026-01-01T00:00:00.000Z"  # before anything in the tiny build


def _tiny(inst: seahaven.Instance) -> None:
    """A small account with one of everything the checks look at: renewals, a converted trial,
    dunning to cancellation, a refund, a disputed one-off payment and weekly payouts."""
    with inst.bulk() as ctx:
        story = Story(ctx)
        story.signup(START, "a")
        story.signup(plus(START, days=20), "b")
        story.swap_card("2026-07-20T09:00:00.000Z", "b", cards.DECLINING)
        story.signup(START, "c", trial_days=14)
        story.refund("2026-08-26T10:00:00.000Z", "a", 300)
        story.one_off("2026-08-27T10:00:00.000Z", "a", cards.DISPUTE_FRAUDULENT)
        mondays = [plus("2026-06-01T15:00:00.000Z", days=7 * k) for k in range(14)]
        destination = accounts.WRENFIELD.bank_account_id
        Payouts(story.timeline, story.client, destination=destination).schedule(mondays)
        story.timeline.run()
        finish.refresh_balance_statuses(ctx, now=NOW)
        finish.trim_events(ctx, now=NOW)


@pytest.fixture
def tiny() -> Iterator[seahaven.Instance]:
    with blank() as inst:
        _tiny(inst)
        yield inst


def _failing(inst: seahaven.Instance) -> set[str]:
    """The names of the checks that fail (a name may itself hold a colon)."""
    failures = checks.violations(inst.inspect(), now=NOW)
    named = {
        name for name, _ in checks.INVARIANTS if any(f.startswith(f"{name}: ") for f in failures)
    }
    assert len(named) == len(failures), failures
    return named


def _plant(inst: seahaven.Instance, *statements: str) -> None:
    with inst.bulk() as ctx:
        for sql in statements:
            assert ctx.db.execute(sql).rowcount >= 1, sql


def _first(table: str, where: str = "1") -> str:
    return f"(SELECT id FROM {table} WHERE {where} ORDER BY x_seq LIMIT 1)"


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


def test_check_fixture_passes_on_a_tiny_build(tiny: seahaven.Instance) -> None:
    checks.check_fixture(
        tiny.inspect(),
        now=NOW,
        expect=(
            Expectation("customers", "SELECT count(*) AS n FROM customers", 3, 3),
            Expectation("disputes", "SELECT count(*) AS n FROM disputes", 1, 1),
            Expectation("refunds", "SELECT count(*) AS n FROM refunds", 1, 1),
            Expectation(
                "canceled",
                "SELECT count(*) AS n FROM subscriptions WHERE status = 'canceled'",
                1,
                1,
            ),
        ),
    )


def test_an_expectation_out_of_range_fails(tiny: seahaven.Instance) -> None:
    failures = checks.violations(
        tiny.inspect(),
        now=NOW,
        expect=(Expectation("customers", "SELECT count(*) AS n FROM customers", 5, 9),),
    )
    assert failures == ["expectation customers: 3, wanted 5..9"]
    with pytest.raises(AssertionError, match="fixture checks failed"):
        checks.check_fixture(
            tiny.inspect(),
            now=NOW,
            expect=(Expectation("customers", "SELECT count(*) AS n FROM customers", 5, 5),),
        )


# One planted violation per invariant and guard (replay.md §8): each plant must trip its own
# check and nothing else.
PLANTS: dict[str, tuple[str, tuple[str, ...]]] = {
    "stored balance status": (
        "balance status agrees with bt_status at NOW",
        (
            "UPDATE balance_transactions SET status = 'pending'"
            f" WHERE id = {_first('balance_transactions')}",
        ),
    ),
    "payout overdraw": (
        "no payout overdrew",
        (
            "UPDATE balance_transactions SET amount = amount - 10000000, net = net - 10000000"
            f" WHERE id = {_first('balance_transactions', "type = 'payout'")}",
        ),
    ),
    "lines against subtotal": (
        "lines sum to subtotal",
        (f"UPDATE invoices SET subtotal = subtotal + 1 WHERE id = {_first('invoices')}",),
    ),
    "overlapping periods": (
        "no overlapping cycle periods",
        (
            "UPDATE invoices SET period_start = '2026-06-01T00:00:00.000Z' WHERE id = (SELECT i.id"
            " FROM invoices i JOIN subscriptions s ON s.id = i.parent_subscription"
            " WHERE s.trial_end IS NULL AND i.billing_reason = 'subscription_cycle'"
            " ORDER BY i.x_seq DESC LIMIT 1)",
        ),
    ),
    "charge before its intent": (
        "causality: charge after its payment intent",
        (f"UPDATE charges SET created = '{EARLIER}' WHERE payment_intent IS NOT NULL",),
    ),
    "refund before its charge": (
        "causality: refund and dispute after their charge",
        (f"UPDATE refunds SET created = '{EARLIER}'",),
    ),
    "dispute before its charge": (
        "causality: refund and dispute after their charge",
        (f"UPDATE disputes SET created = '{EARLIER}'",),
    ),
    "balance transaction before its source": (
        "causality: balance transaction after its source",
        (
            f"UPDATE balance_transactions SET created = '{EARLIER}'"
            f" WHERE id = {_first('balance_transactions', "type = 'charge'")}",
        ),
    ),
    "customer balance": (
        "customer balance is the latest ending balance",
        (f"UPDATE customers SET balance = 500 WHERE id = {_first('customers')}",),
    ),
    "invoice sequence": (
        "next invoice sequence follows the highest number",
        (f"UPDATE customers SET next_invoice_sequence = 99 WHERE id = {_first('customers')}",),
    ),
    "status against latest invoice": (
        "subscription status agrees with its latest invoice",
        (
            "UPDATE subscriptions SET status = 'past_due'"
            f" WHERE id = {_first('subscriptions', "status = 'active'")}",
        ),
    ),
    "a text timestamp after NOW": (
        "nothing after NOW",
        (f"UPDATE subscriptions SET start_date = '{LATER}' WHERE id = {_first('subscriptions')}",),
    ),
    "a JSON timestamp after NOW": (
        "nothing after NOW",
        (
            "UPDATE invoices SET status_transitions = json_set(status_transitions, '$.paid_at',"
            f" {_time.to_unix(LATER)}) WHERE id = {_first('invoices', "status = 'paid'")}",
        ),
    ),
    "an event older than 30 days": (
        "events cover the last 30 days",
        (f"UPDATE events SET created = '{EARLIER}' WHERE id = {_first('events')}",),
    ),
    "an unknown card": (
        "cards are known test cards",
        (
            "UPDATE payment_methods SET rail = json_set(rail, '$.fingerprint', 'zzzz')"
            f" WHERE id = {_first('payment_methods')}",
        ),
    ),
    "a webmail subdomain": (
        "no webmail or example email domains",
        (f"UPDATE customers SET email = 'a@mail.yahoo.com' WHERE id = {_first('customers')}",),
    ),
    "a cliche name": (
        "no sample-data cliche names",
        ("UPDATE products SET name = 'Acme Widgets'",),
    ),
}


@pytest.mark.parametrize("plant", sorted(PLANTS))
def test_each_check_catches_its_violation(tiny: seahaven.Instance, plant: str) -> None:
    check, statements = PLANTS[plant]
    assert _failing(tiny) == set()
    _plant(tiny, *statements)
    assert _failing(tiny) == {check}


def test_every_check_has_a_plant() -> None:
    """The two the schema enforces are planted below, against the schema itself."""
    planted = {check for check, _ in PLANTS.values()}
    schema_enforced = {"net is amount - fee", "no over-refund"}
    assert planted | schema_enforced == {name for name, _ in checks.INVARIANTS}


@pytest.mark.parametrize(
    ("plant", "constraint"),
    [
        (
            "UPDATE balance_transactions SET net = net + 1"
            f" WHERE id = {_first('balance_transactions')}",
            "net = amount - fee",
        ),
        (
            "UPDATE charges SET amount_captured = 0 WHERE id = (SELECT charge FROM refunds)",
            "amount_refunded <= amount_captured",
        ),
    ],
    ids=["net is amount - fee", "no over-refund"],
)
def test_the_schema_refuses_what_two_checks_guard(
    tiny: seahaven.Instance, plant: str, constraint: str
) -> None:
    """Two invariants cannot be planted: the schema's CHECKs refuse the row first."""
    with pytest.raises(seahaven.DbError) as refused:
        _plant(tiny, plant)
    assert f"CHECK constraint failed: {constraint}" in str(refused.value.__cause__)


def test_after_now_names_each_row_by_id(tiny: seahaven.Instance) -> None:
    (sub,) = rows(tiny, f"SELECT id FROM subscriptions WHERE id = {_first('subscriptions')}")
    (invoice,) = rows(tiny, f"SELECT id FROM invoices WHERE id = {_first('invoices')}")
    _plant(
        tiny,
        f"UPDATE subscriptions SET trial_start = '{LATER}', trial_end = '{LATER}'"
        f" WHERE id = '{sub['id']}'",
        "UPDATE invoices SET lines = json_set(lines, '$[0].period.start',"
        f" {_time.to_unix(LATER)}) WHERE id = '{invoice['id']}'",
    )
    (failure,) = checks.violations(tiny.inspect(), now=NOW)
    assert failure.startswith("nothing after NOW: 2 rows")
    assert f"'id': '{sub['id']}'" in failure and "'column': 'trial_start'" in failure
    assert f"'id': '{invoice['id']}'" in failure and "'column': 'lines[0].period.start'" in failure


def test_nothing_after_now_spares_the_forward_fields(tiny: seahaven.Instance) -> None:
    """Period ends, trial ends, cancellations, due dates and arrivals point forward by design."""
    unix = _time.to_unix(LATER)
    _plant(
        tiny,
        f"UPDATE subscription_items SET current_period_end = '{LATER}'",
        f"UPDATE subscriptions SET cancel_at = '{LATER}', billing_cycle_anchor = '{LATER}'",
        f"UPDATE invoices SET due_date = '{LATER}', lines = json_set(lines, '$[0].period.end',"
        f" {unix})",
        f"UPDATE payouts SET arrival_date = '{LATER}'",
        f"UPDATE disputes SET evidence_details = json_set(evidence_details, '$.due_by', {unix})",
    )
    assert "nothing after NOW" not in _failing(tiny)


def test_a_trial_end_invoice_may_share_its_span_with_the_next_renewal_only(
    tiny: seahaven.Instance,
) -> None:
    """World gap #12: the converted trial's first two cycle invoices carry one span. That pair
    passes; a span that is merely close, or a later renewal that overlaps, does not."""
    (trial,) = rows(tiny, "SELECT id, trial_end FROM subscriptions WHERE trial_end IS NOT NULL")
    cycle = rows(
        tiny,
        "SELECT id, created, period_start, period_end FROM invoices"
        " WHERE parent_subscription = ? AND billing_reason = 'subscription_cycle' ORDER BY x_seq",
        trial["id"],
    )
    trial_end, renewal, later = cycle[0], cycle[1], cycle[2]
    assert trial_end["created"] == trial["trial_end"]
    assert (renewal["period_start"], renewal["period_end"]) == (
        trial_end["period_start"],
        trial_end["period_end"],
    )
    assert "no overlapping cycle periods" not in _failing(tiny)

    with blank() as other:  # the trial-end invoice's own span no longer matches the renewal's
        _tiny(other)
        _plant(
            other,
            f"UPDATE invoices SET period_end = '{plus(trial_end['period_end'], hours=1)}'"
            f" WHERE id = '{trial_end['id']}'",
        )
        assert _failing(other) == {"no overlapping cycle periods"}
    with blank() as other:  # the trial-end span repeated by a renewal that is not the next one
        _tiny(other)
        _plant(
            other,
            "UPDATE invoices SET period_start = '2026-01-01T00:00:00.000Z',"
            f" period_end = '2026-01-02T00:00:00.000Z' WHERE id = '{renewal['id']}'",
            f"UPDATE invoices SET period_start = '{trial_end['period_start']}',"
            f" period_end = '{trial_end['period_end']}' WHERE id = '{later['id']}'",
        )
        assert _failing(other) == {"no overlapping cycle periods"}
    _plant(
        tiny,
        f"UPDATE invoices SET period_start = '{trial_end['period_start']}'"
        f" WHERE id = '{later['id']}'",
    )
    assert _failing(tiny) == {"no overlapping cycle periods"}


def test_cards_match_the_world() -> None:
    for card in cards.GOOD:
        behavior = magic_cards.card_for(card.number)
        assert behavior.brand == card.brand
        assert behavior.charge_decline is None and not behavior.attach_declines
        assert behavior.dispute is None and behavior.three_d_secure is None
    assert sum(card.weight for card in cards.GOOD) == 100
    declining = magic_cards.card_for(cards.DECLINING.number)
    assert declining.brand == "visa" and not declining.attach_declines
    assert declining.charge_decline == ("card_declined", cards.DECLINING_CODE)
    for card, reason, track in (
        (cards.DISPUTE_FRAUDULENT, "fraudulent", "chargeback"),
        (cards.DISPUTE_NOT_RECEIVED, "product_not_received", "chargeback"),
        (cards.DISPUTE_INQUIRY, "fraudulent", "inquiry"),
    ):
        behavior = magic_cards.card_for(card.number)
        assert (behavior.dispute, behavior.dispute_track) == (reason, track)
    assert (
        magic_cards.card_for(cards.AUTH_REQUIRED.number).three_d_secure == "authentication_required"
    )
