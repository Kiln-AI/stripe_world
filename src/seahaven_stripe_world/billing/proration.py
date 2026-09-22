"""The proration engine: the credit/debit line pairs a mid-cycle change
mints (`components/billing_engine.md` §4, corrected by the Phase 14
recordings).

Everything here is a pure function of plain values — no `ctx`, no db, no
clock, no ids (`billing_engine.md`'s organising rule). The caller in
`subscription_lifecycle.py` mints the `ii_` rows.

Two rules carry the arithmetic, both recorded at `2026-08-26.dahlia`
(2026-09-21, cassette 01 and its probe trails):

- **The fraction is exact and second-precision**:
  `Fraction(period_end - proration_date, period_end - period_start)` over
  unix-seconds differences — never day counts, never a float.
- **Each line is the FLOOR of its own exact rational** (`_money.floor_cents`
  carries the evidence): credit -2.5¢ → -3, debit +4.5¢ → +4, the
  documented -666.67¢ → -667 and +333.33¢ → +333. The lines round
  independently and the invoice total is the sum of the rounded lines —
  the documented -667 + 333 = -334, which a net-then-round implementation
  misses by a cent.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from fractions import Fraction

import seahaven

from seahaven_stripe_world.billing._money import floor_cents

__all__ = [
    "ItemConfig",
    "ProrationLine",
    "proration_date_spelling",
    "proration_fraction",
    "proration_lines",
]


@dataclass(frozen=True, slots=True)
class ItemConfig:
    """One subscription item's billing configuration at one side of the
    change. `product_name`/`product_id` ride along because the recorded
    descriptions and `pricing.price_details` blocks name them."""

    subscription_item_id: str | None  # None for a brand-new item
    price_id: str
    product_id: str
    product_name: str
    unit_amount: int  # integer minor units, per unit
    quantity: int


@dataclass(frozen=True, slots=True)
class ProrationLine:
    """One proration line, pre-row. `amount` is signed: negative = credit,
    positive = debit. `period_start` is the proration moment and
    `period_end` the billing period's end — the recorded window."""

    amount: int
    description: str
    period_start: int  # unix seconds; == the proration date
    period_end: int  # unix seconds; == the period end
    price_id: str
    product_id: str
    quantity: int
    subscription_item_id: str | None
    proration: bool = True
    discountable: bool = False  # spec3.json: "Always false for prorations."
    credited_line_ids: tuple[str, ...] = ()  # -> proration_details.credited_items


def proration_fraction(period_start: int, period_end: int, proration_date: int) -> Fraction:
    """Exact `(period_end - proration_date) / (period_end - period_start)`,
    in SECONDS. `proration_date` is clamped into the period; a zero-length
    period is an authoring fault, not an agent error."""
    if period_end <= period_start:
        raise seahaven.WorldBug(
            f"proration over a zero-length period: [{period_start}, {period_end})"
        )
    clamped = min(max(proration_date, period_start), period_end)
    return Fraction(period_end - clamped, period_end - period_start)


def proration_date_spelling(moment: int) -> str:
    """`1790179200` -> `21 Sep 2026` — the date the recorded descriptions
    name (the proration moment, not the period end: a change dated 11 Oct
    against a 21 Oct period end says "after 11 Oct 2026")."""
    return datetime.fromtimestamp(moment, tz=UTC).strftime("%d %b %Y")


def _description(kind: str, config: ItemConfig, after: str) -> str:
    # Recorded (cassette 01): no multiplier at quantity 1, "N x " above it —
    # "Unused time on <product> after …", "Remaining time on 3 x <product>
    # after …" (the quantity case's own recording).
    subject = (
        config.product_name
        if config.quantity == 1
        else f"{config.quantity} \u00d7 {config.product_name}"
    )
    return f"{kind} time on {subject} after {after}"


def _config_changed(old: ItemConfig, new: ItemConfig) -> bool:
    return (
        old.price_id != new.price_id
        or old.unit_amount != new.unit_amount
        or old.quantity != new.quantity
    )


def proration_lines(
    old_items: Sequence[ItemConfig],
    new_items: Sequence[ItemConfig],
    *,
    period_start: int,
    period_end: int,
    proration_date: int,
    credited_line_ids_by_item: Mapping[str, tuple[str, ...]] | None = None,
) -> list[ProrationLine]:
    """The whole proration algorithm. Pure: no ctx, no db, no clock, no ids.

    Credits first (matching the recorded line order), then debits. Pairs
    whose configuration did not change contribute nothing; a brand-new item
    (no old config) debits only; a removed item (no new config) credits
    only. Lines are emitted whenever the fraction is positive — including a
    rounded amount of 0, which the recorded anchor-reset stub carries.

    Whole-configuration reproration, never a delta: a `quantity: 2 → 3`
    change credits `floor(f x 2 x old_price)` and debits
    `floor(f x 3 x new_price)` — recorded (cassette 01: -3 and +7 at
    f = 1/400, where the one-added-unit delta would be +5).
    """
    fraction = proration_fraction(period_start, period_end, proration_date)
    if fraction == 0:
        return []
    credited = credited_line_ids_by_item or {}
    after = proration_date_spelling(proration_date)
    new_by_item = {item.subscription_item_id: item for item in new_items}
    old_by_item = {item.subscription_item_id: item for item in old_items}

    lines: list[ProrationLine] = []
    for old in old_items:
        successor = new_by_item.get(old.subscription_item_id)
        if successor is not None and not _config_changed(old, successor):
            continue
        gross = fraction * (old.unit_amount * old.quantity)
        lines.append(
            ProrationLine(
                amount=floor_cents(-gross),
                description=_description("Unused", old, after),
                period_start=proration_date,
                period_end=period_end,
                price_id=old.price_id,
                product_id=old.product_id,
                quantity=old.quantity,
                subscription_item_id=old.subscription_item_id,
                credited_line_ids=credited.get(old.subscription_item_id or "", ()),
            )
        )
    for new in new_items:
        predecessor = old_by_item.get(new.subscription_item_id)
        if predecessor is not None and not _config_changed(predecessor, new):
            continue
        gross = fraction * (new.unit_amount * new.quantity)
        lines.append(
            ProrationLine(
                amount=floor_cents(gross),
                description=_description("Remaining", new, after),
                period_start=proration_date,
                period_end=period_end,
                price_id=new.price_id,
                product_id=new.product_id,
                quantity=new.quantity,
                subscription_item_id=new.subscription_item_id,
            )
        )
    return lines
