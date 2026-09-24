"""The proration engine's arithmetic, pure and recorded: every number here
is a cassette-01 value at `2026-08-26.dahlia` (2026-09-21) — the recorded
floor rule, the documented -667/+333/-334 invoice, the whole-configuration
quantity case, and the window/`proration_date` mechanics."""

from fractions import Fraction

import pytest
import seahaven

from conftest import BLANK_NOW
from seahaven_stripe_world.billing import proration
from seahaven_stripe_world.billing._money import floor_cents

pytestmark = pytest.mark.seahaven(fixture=None, now=BLANK_NOW, clock_mode="fixed")

# A 30-day window (2026-09-01 -> 2026-10-01), the frozen clock's own.
T0 = 1_788_271_200
SPan = 30 * 86_400
T1 = T0 + SPan

NOW = BLANK_NOW


def _config(item: str = "si_1", unit: int = 1000, qty: int = 1) -> proration.ItemConfig:
    return proration.ItemConfig(
        subscription_item_id=item,
        price_id=f"price_{unit}",
        product_id="prod_x",
        product_name="widget",
        unit_amount=unit,
        quantity=qty,
    )


def test_proration_documented_667_333_334() -> None:
    """THE load-bearing one, now RECORDED live (cassette 01's third-remainder
    case answered exactly these numbers): a 2000->1000 switch at an exact
    third credits -667 and debits +333, totalling -334 — the documented
    example, where truncation would give -666 and net-then-round -333."""
    lines = proration.proration_lines(
        [_config(unit=2000)],
        [_config(unit=1000)],
        period_start=T0,
        period_end=T1,
        proration_date=T1 - SPan // 3,
    )
    assert [line.amount for line in lines] == [-667, 333]
    assert sum(line.amount for line in lines) == -334


def test_net_then_round_would_differ() -> None:
    """The test's purpose survives a refactor: a single rounding of the net
    gives -333, and the implementation returns -334."""
    exact = Fraction(-1000, 3) + Fraction(500, 3)  # -500/3 = -166.67
    net_rounded = floor_cents(exact) + 1  # any one-shot rounding differs
    lines = proration.proration_lines(
        [_config(unit=1000)],
        [_config(unit=500)],
        period_start=T0,
        period_end=T1,
        proration_date=T1 - SPan // 3,
    )
    assert sum(line.amount for line in lines) != net_rounded


def test_the_recorded_half_cent_tie_floors_both_sides() -> None:
    """RECORDED (cassette 01): a fraction of exactly 1/400 puts the 10.00
    credit on -2.5 and the 18.00 debit on +4.5, and live floors BOTH —
    (-3, +4) — which half-up (+5 on the debit), half-even (-2 on the
    credit) and truncation (-2 on the credit) each contradict somewhere.
    This closes the design's one open assumption: the rule is floor."""
    lines = proration.proration_lines(
        [_config(unit=1000)],
        [_config(unit=1800)],
        period_start=T0,
        period_end=T1,
        proration_date=T1 - SPan // 400,
    )
    assert [line.amount for line in lines] == [-3, 4]


def test_quantity_change_reprorates_the_whole_item() -> None:
    """RECORDED (cassette 01): quantity 1->3 at 1/400 credits -3 (one old
    unit) and debits +7 (three new units, floor of 7.5) — the delta reading
    would debit +5 exactly, and the recording says 7."""
    lines = proration.proration_lines(
        [_config(unit=1000, qty=1)],
        [_config(unit=1000, qty=3)],
        period_start=T0,
        period_end=T1,
        proration_date=T1 - SPan // 400,
    )
    assert [line.amount for line in lines] == [-3, 7]
    assert lines[0].quantity == 1
    assert lines[1].quantity == 3


def test_descriptions_match_the_recorded_spellings() -> None:
    lines = proration.proration_lines(
        [_config(unit=1000, qty=1)],
        [_config(unit=1000, qty=3)],
        period_start=T0,
        period_end=T1,
        proration_date=T1 - SPan // 400,
    )
    # Recorded: the date is the PRORATION moment's day; the multiplier
    # appears only above quantity 1.
    assert lines[0].description == "Unused time on widget after 01 Oct 2026"
    assert lines[1].description == "Remaining time on 3 \u00d7 widget after 01 Oct 2026"


def test_lines_carry_the_proration_window_and_never_discount() -> None:
    lines = proration.proration_lines(
        [_config()],
        [_config(unit=1800)],
        period_start=T0,
        period_end=T1,
        proration_date=T1 - SPan // 400,
    )
    for line in lines:
        assert line.period_start == T1 - SPan // 400
        assert line.period_end == T1
        assert line.proration is True
        assert line.discountable is False
        assert line.price_id in ("price_1000", "price_1800")


def test_fraction_is_second_precision() -> None:
    one_second_in = proration.proration_fraction(T0, T1, T0 + 1)
    assert one_second_in == Fraction(T1 - T0 - 1, T1 - T0)


def test_fraction_is_an_exact_rational() -> None:
    assert isinstance(proration.proration_fraction(T0, T1, T0 + 86_400), Fraction)


def test_proration_date_is_honoured_and_clamped() -> None:
    before = proration.proration_fraction(T0, T1, T0 - 999)
    after = proration.proration_fraction(T0, T1, T1 + 999)
    assert before == 1
    assert after == 0


def test_zero_fraction_returns_no_lines() -> None:
    assert (
        proration.proration_lines(
            [_config()],
            [_config(unit=1800)],
            period_start=T0,
            period_end=T1,
            proration_date=T1,
        )
        == []
    )


def test_configuration_identical_pairs_contribute_nothing() -> None:
    assert (
        proration.proration_lines(
            [_config()],
            [_config()],
            period_start=T0,
            period_end=T1,
            proration_date=T1 - SPan // 400,
        )
        == []
    )


def test_upgrade_and_downgrade_are_symmetric() -> None:
    def amounts(old: int, new: int) -> list[int]:
        return [
            line.amount
            for line in proration.proration_lines(
                [_config(unit=old)],
                [_config(unit=new)],
                period_start=T0,
                period_end=T1,
                proration_date=T1 - SPan // 3,
            )
        ]

    # floor on both sides: -1000/3 -> -334 (not -333), +2000/3 -> 666.
    # The formula itself is direction-free — the same code path emits
    # credits then debits and the sign rides the side — but independent
    # floor rounding is not sign-symmetric (floor(-x) != -floor(x) by up
    # to one), which is exactly the asymmetry the totals carry.
    assert amounts(1000, 2000) == [-334, 666]
    assert amounts(2000, 1000) == [-667, 333]


def test_removed_item_credits_only_and_added_item_debits_only() -> None:
    removed = proration.proration_lines(
        [_config()], [], period_start=T0, period_end=T1, proration_date=T1 - SPan // 3
    )
    assert [line.amount for line in removed] == [-334]
    added = proration.proration_lines(
        [], [_config()], period_start=T0, period_end=T1, proration_date=T1 - SPan // 3
    )
    assert [line.amount for line in added] == [333]


def test_zero_amount_lines_are_emitted_at_positive_fraction() -> None:
    """RECORDED (the anchor-reset probe): a fraction so small the debit
    rounds to 0 still lands as an item. The credit's floor carries the same
    fraction AWAY from zero (-0.023 -> -1) — the rule applied
    symmetrically, not a magnitude-then-sign rounding."""
    lines = proration.proration_lines(
        [_config()],
        [_config(unit=1800)],
        period_start=T0,
        period_end=T1,
        proration_date=T1 - 60,
    )
    assert [line.amount for line in lines] == [-1, 0]


def test_zero_length_period_is_an_authoring_fault() -> None:
    with pytest.raises(seahaven.WorldBug):
        proration.proration_fraction(T0, T0, T0)


def test_floor_cents_on_negative_rationals() -> None:
    assert floor_cents(Fraction(-5, 2)) == -3
    assert floor_cents(Fraction(-10, 3)) == -4
    assert floor_cents(Fraction(7, 2)) == 3
    assert floor_cents(Fraction(-4, 1)) == -4


def test_a_credit_carries_its_credited_line_ids() -> None:
    lines = proration.proration_lines(
        [_config()],
        [_config(unit=1800)],
        period_start=T0,
        period_end=T1,
        proration_date=T1 - SPan // 400,
        credited_line_ids_by_item={"si_1": ("il_x",)},
    )
    assert lines[0].credited_line_ids == ("il_x",)
    assert lines[1].credited_line_ids == ()
