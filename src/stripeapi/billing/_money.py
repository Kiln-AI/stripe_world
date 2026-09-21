"""Shared money primitives: the fee schedule, with no `ctx`, no I/O, no floats.

`components/billing_engine.md` §"Public Interface" places `FeeSchedule` and
`stripe_fee` here. The module's other primitives (`round_cents_half_up`,
`apportion`) belong to the proration and invoicing phases and land with them;
nothing here pre-declares them.

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

__all__ = ["FeeSchedule", "apportion", "round_half_up", "stripe_fee"]


def round_half_up(value: Fraction) -> int:
    """Nearest integer, ties away from zero, exact. Stripe documents half-up
    for *fees* (support.stripe.com/questions/rounding-rules-for-stripe-fees)
    — a different authority from proration's assumed tie-break, which is why
    this lives here and not in the proration module.
    """
    quotient = decimal.Decimal(value.numerator) / decimal.Decimal(value.denominator)
    return int(quotient.quantize(decimal.Decimal("1"), rounding=decimal.ROUND_HALF_UP))


def apportion(total: int, weights: Sequence[int]) -> list[int]:
    """Split `total` across `weights` so the parts sum EXACTLY to `total`.

    Floor every share, then give the entire remainder to the LAST non-zero-weight
    entry. This is Stripe's documented coupon-across-items rule, NOT independent
    rounding: a $5 coupon split 1:2 over a $10 and a $20 item yields [166, 334],
    not [167, 333] (gap-closure-2026-09-18.md item 1, closing paragraph).
    Never call round_cents_half_up here — the two rounding behaviors are
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
