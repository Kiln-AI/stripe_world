"""`small_co`: Wrenfield Labs (components/small_co.md).

`build(inst)` fills a blank test-mode instance with Wrenfield's history up to `NOW` by replaying
the catalog, the hand-written cast and the Monday payouts through the world's own code, then
checks the result against the invariants, guards and `EXPECTATIONS`.
"""

from collections.abc import Iterator
from datetime import date, timedelta

import seahaven
from fixtures_src import accounts
from fixtures_src.replay.checks import Expectation, check_fixture
from fixtures_src.replay.client import Client
from fixtures_src.replay.finish import refresh_balance_statuses, trim_events
from fixtures_src.replay.payouts import Payouts
from fixtures_src.replay.subscriptions import NoHooks, Subscriptions
from fixtures_src.replay.timeline import Timeline
from fixtures_src.small_co import story
from fixtures_src.small_co.cast import CAST
from fixtures_src.small_co.catalog import Catalog

__all__ = ["EXPECTATIONS", "NOW", "SmallCoHooks", "build", "mondays"]

# generate.NOW; repeated rather than imported so this package does not import the recipe module.
NOW = "2026-09-01T14:00:00.000Z"


class SmallCoHooks(NoHooks):
    """Wrenfield's decisions: none. Card changes are beats at their own instants, nobody in this
    story recovers from a decline before `NOW` (so every retry fails), and nothing is invoiced
    with `send_invoice`."""


def mondays() -> Iterator[str]:
    """Every Monday from 2025-09-08 to 2026-08-31, at 15:00Z."""
    day = date(2025, 9, 8)
    while day <= date(2026, 8, 31):
        yield f"{day.isoformat()}T15:00:00.000Z"
        day += timedelta(days=7)


def _count(name: str, sql: str, low: int, high: int | None = None) -> Expectation:
    return Expectation(name, sql, low, low if high is None else high)


def _status(status: str, n: int) -> Expectation:
    return _count(
        f"subscriptions {status}",
        f"SELECT count(*) AS n FROM subscriptions WHERE status = '{status}'",
        n,
    )


EXPECTATIONS: tuple[Expectation, ...] = (
    _count("customers", "SELECT count(*) AS n FROM customers", 45),
    _count("subscriptions", "SELECT count(*) AS n FROM subscriptions", 42),
    _status("active", 29),
    _status("trialing", 3),
    _status("past_due", 1),
    _status("paused", 1),
    _status("canceled", 7),
    _status("incomplete_expired", 1),
    _count(
        "cancel_at_period_end",
        "SELECT count(*) AS n FROM subscriptions WHERE cancel_at_period_end AND status = 'active'",
        1,
    ),
    _count(
        "open fraudulent disputes",
        "SELECT count(*) AS n FROM disputes WHERE status = 'needs_response'"
        " AND reason = 'fraudulent'",
        1,
    ),
    _count("disputes", "SELECT count(*) AS n FROM disputes", 1),
    _count("refunds", "SELECT count(*) AS n FROM refunds", 2),
    _count("credit balances", "SELECT count(*) AS n FROM customers WHERE balance < 0", 1),
    _count(
        "pending invoice items", "SELECT count(*) AS n FROM invoiceitems WHERE invoice IS NULL", 1
    ),
    # The catalog: 3 products and 6 prices, each price with its lookup key. Each $49 setup
    # invoice item also mints its own one-off price and product, as Stripe does (2 more).
    _count(
        "catalog products",
        "SELECT count(DISTINCT product) AS n FROM prices WHERE lookup_key IS NOT NULL",
        3,
    ),
    _count("catalog prices", "SELECT count(*) AS n FROM prices WHERE lookup_key IS NOT NULL", 6),
    _count("products", "SELECT count(*) AS n FROM products", 5),
    _count("prices", "SELECT count(*) AS n FROM prices", 8),
    _count("inactive prices", "SELECT count(*) AS n FROM prices WHERE NOT active", 1),
    _count("coupons", "SELECT count(*) AS n FROM coupons", 2),
    _count("charges", "SELECT count(*) AS n FROM charges", 80, 130),
    # A quiet Monday (nothing newly available) makes no payout; the spec's 20-40 assumed every
    # week after launch had one.
    _count("payouts", "SELECT count(*) AS n FROM payouts", 15, 40),
    _count("pending payouts", "SELECT count(*) AS n FROM payouts WHERE status = 'pending'", 1, 5),
)


def build(inst: seahaven.Instance) -> None:
    """Fill a blank test-mode instance with Wrenfield Labs' history up to NOW, then check it."""
    with inst.bulk() as ctx:
        # Drawn even though the cast is fixed: the protocol is the same in both builders.
        ctx.ids.random.getrandbits(64)
        accounts.install_account(ctx, accounts.WRENFIELD)
        tl = Timeline(ctx, until=NOW)
        client = Client()
        subs = Subscriptions(tl, client, SmallCoHooks())
        catalog = Catalog()
        catalog.schedule(tl, client)
        story.schedule(tl, client, subs, catalog, CAST)
        Payouts(tl, client, destination=accounts.WRENFIELD.bank_account_id).schedule(mondays())
        tl.run()
        refresh_balance_statuses(ctx, now=NOW)
        trim_events(ctx, now=NOW)
    check_fixture(inst.inspect(), now=NOW, expect=EXPECTATIONS)
