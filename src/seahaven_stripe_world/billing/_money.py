"""Shared money primitives: the fee schedule, with no `ctx`, no I/O, no floats.

`components/billing_engine.md` §"Public Interface" places `FeeSchedule` and
`stripe_fee` here. The proration phase adds `floor_cents` — the recorded
proration rounding rule — beside the fee rule, so the two rounding
authorities live in one auditable place.

The processing fee is **account pricing**, not spec behavior: no Stripe page
states a canonical rate as an API-discoverable value
(billing-and-money-behavior/balance-ledger-and-payouts.md, gap 1), so the
schedule is a `LedgerSpec` constant a fixture or instance config may override
and the recorded values are allow-listed rather than matched.
"""

import decimal
from collections.abc import Sequence
from dataclasses import dataclass
from fractions import Fraction

__all__ = ["FeeSchedule", "apportion", "floor_cents", "round_half_up", "stripe_fee"]


def floor_cents(value: Fraction) -> int:
    """Floor of an exact rational, toward negative infinity — the proration rule.

    RECORDED (Phase 14, cassette 01 + the probe trails, 2026-09-21): an
    engineered half-cent tie floors on both sides — a credit of -2.5¢ lands
    as -3 and a debit of +4.5¢ as +4 — which no nearest-integer rule
    (half-up, half-even, truncation) reproduces. The documented -667/+333
    case agrees: floor(-666.67) = -667, floor(333.33) = 333, where
    truncation would give -666 and fail. This corrects billing_engine's
    original round-half-up assumption; every other value (fees, coupons,
    taxes) keeps its own documented rule.
    """
    return value.numerator // value.denominator


def round_half_up(value: Fraction) -> int:
    """Nearest integer, ties away from zero, exact. Stripe documents half-up
    for *fees* (support.stripe.com/questions/rounding-rules-for-stripe-fees)
    — a different authority from proration's recorded floor, which is why
    both live here, separately named, and never blur.
    """
    quotient = decimal.Decimal(value.numerator) / decimal.Decimal(value.denominator)
    return int(quotient.quantize(decimal.Decimal("1"), rounding=decimal.ROUND_HALF_UP))


def apportion(total: int, weights: Sequence[int]) -> list[int]:
    """Split `total` across `weights` so the parts sum EXACTLY to `total`.

    Floor every share, then give the entire remainder to the LAST non-zero-weight
    entry. This is Stripe's documented coupon-across-items rule, NOT independent
    rounding: a $5 coupon split 1:2 over a $10 and a $20 item yields [166, 334],
    not [167, 333] (gap-closure-2026-09-18.md item 1, closing paragraph).
    Never call floor_cents here — the two rounding behaviors are
    documented separately and must not blur.
    """
    if not weights:
        return []
    weight_sum = sum(weights)
    if weight_sum <= 0:
        return [0] * len(weights)
    shares = [total * weight // weight_sum for weight in weights]
    remainder = total - sum(shares)
    for index in range(len(weights) - 1, -1, -1):
        if weights[index] > 0:
            shares[index] += remainder
            break
    return shares


@dataclass(frozen=True, slots=True)
class FeeSchedule:
    """Percent basis points plus a fixed minor-unit part. 290 + 30 is the
    commonly cited US standard-card pricing — this world's declared constant,
    not a transcribed Stripe fact."""

    percent_bps: int = 290  # 2.90%
    fixed: int = 30  # 30c


def stripe_fee(amount: int, schedule: FeeSchedule) -> int:
    """Percent part (round half up, documented for fees) + fixed part.

    Raises ValueError on a negative amount: a fee is assessed on funds
    charged, and a negative gross is the caller's sign bug.
    """
    if amount < 0:
        raise ValueError(f"stripe_fee: negative amount {amount}")
    return round_half_up(Fraction(amount * schedule.percent_bps, 10_000)) + schedule.fixed
