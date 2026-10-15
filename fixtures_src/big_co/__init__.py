"""`big_co`: Ostrander Systems (components/big_co.md), at the population the user chose.

`build(inst)` replays 18 months of a field-service SaaS company through the world's own code: the
catalog, a drawn population (a migration cohort plus organic and sales-led growth), each
subscription's life, the scripted loose ends, the SLA credit batch and the business-day payouts,
then checks the result against the invariants, the guards and `EXPECTATIONS`.
"""

import random

import seahaven
from fixtures_src import accounts
from fixtures_src.big_co.loose_ends import scripted
from fixtures_src.big_co.population import NOW, draw_population
from fixtures_src.big_co.shape import SHAPE, scaled
from fixtures_src.big_co.simulate import SLA_MEMO, Catalog, Lives, business_days
from fixtures_src.replay.checks import Expectation, check_fixture
from fixtures_src.replay.client import Client
from fixtures_src.replay.clock import plus
from fixtures_src.replay.finish import refresh_balance_statuses, trim_events
from fixtures_src.replay.payouts import Payouts
from fixtures_src.replay.subscriptions import Subscriptions
from fixtures_src.replay.timeline import Timeline

__all__ = ["EXPECTATIONS", "NOW", "build"]

_ANY = 1_000_000
_STATUSES = ("trialing", "past_due", "canceled", "unpaid", "paused", "incomplete_expired")
_DISPUTE_STAGES = (
    "needs_response",
    "warning_needs_response",
    "under_review",
    "warning_under_review",
    "won",
    "lost",
)


def _count(name: str, table: str, where: str, low: int, high: int = _ANY) -> Expectation:
    return Expectation(name, f"SELECT count(*) AS n FROM {table} WHERE {where}", low, high)


_LOOSE_ENDS = len(scripted(SHAPE))
# The queue of four, two inquiries and one under review (big_co.md §5), and one each won and lost.
_SCRIPTED_DISPUTES = 9


# big_co.md §8. The ranges that grow with the population are the spec's at `SCALE`, plus the
# scripted loose ends, which do not scale. Two are re-centred on what the specified rates produce:
# active subscriptions (the rates leave under 60% of customers active at NOW, where the spec
# expected 70%: a 2% unrecovered decline per renewal plus churn), and disputes (0.3% of a few
# thousand card-paid periods is single digits, beside the nine scripted).
EXPECTATIONS: tuple[Expectation, ...] = (
    _count("customers", "customers", "1", scaled(2800) + _LOOSE_ENDS, scaled(3200) + _LOOSE_ENDS),
    _count("subscriptions active", "subscriptions", "status = 'active'", 190, 260),
    *(_count(f"subscriptions {s}", "subscriptions", f"status = '{s}'", 3) for s in _STATUSES),
    _count("subscriptions incomplete", "subscriptions", "status = 'incomplete'", 1),
    _count("disputes", "disputes", "1", 10, scaled(110) + _SCRIPTED_DISPUTES),
    *(_count(f"disputes {s}", "disputes", f"status = '{s}'", 1) for s in _DISPUTE_STAGES),
    _count("payouts", "payouts", "1", 350, 400),
    _count("payouts pending", "payouts", "status = 'pending'", 1),
    _count("SLA credit notes", "credit_notes", f"memo = '{SLA_MEMO}'", scaled(100)),
    _count(
        "SLA-reason credit notes without the SLA memo",
        "credit_notes",
        f"reason = 'product_unsatisfactory' AND memo IS NOT '{SLA_MEMO}'",
        0,
        0,
    ),
    _count(
        "draft invoices in the last hour",
        "invoices",
        f"status = 'draft' AND created > '{plus(NOW, hours=-1)}'"
        f" AND automatically_finalizes_at > '{NOW}'",
        5,
    ),
    _count(
        "ramp schedules",
        "subscription_schedules",
        "status = 'active' AND json_array_length(phases) = 3",
        1,
    ),
    _count(
        "scheduled downgrades",
        "subscription_schedules",
        "status = 'active' AND json_array_length(phases) = 2",
        SHAPE.scheduled_downgrades,
        SHAPE.scheduled_downgrades,
    ),
    _count(
        "tax-exempt customers",
        "customers",
        "tax_exempt = 'exempt'",
        SHAPE.tax_exempt_customers,
        SHAPE.tax_exempt_customers,
    ),
)


def build(inst: seahaven.Instance) -> None:
    """Fill a blank test-mode instance with Ostrander's history up to NOW, then check it."""
    with inst.bulk() as ctx:
        rng = random.Random(ctx.ids.random.getrandbits(64))
        accounts.install_account(ctx, accounts.OSTRANDER)
        tl = Timeline(ctx, until=NOW)
        client = Client()
        catalog = Catalog()
        catalog.schedule(tl, client)
        lives = Lives(tl, client, catalog, rng, SHAPE)
        lives.subs = Subscriptions(tl, client, lives)
        lives.schedule(draw_population(rng, SHAPE) + scripted(SHAPE))
        lives.schedule_sla_batch()
        Payouts(tl, client, destination=accounts.OSTRANDER.bank_account_id).schedule(
            business_days()
        )
        tl.run()
        refresh_balance_statuses(ctx, now=NOW)
        trim_events(ctx, now=NOW)
    check_fixture(inst.inspect(), now=NOW, expect=EXPECTATIONS)
