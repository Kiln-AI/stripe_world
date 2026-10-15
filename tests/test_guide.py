"""The fixture guide is true (functional spec §3.6, §6): every landmark query in
`docs/fixtures.md` returns, from the committed fixture, exactly the rows the guide shows."""

import pytest
import seahaven
from fixtures_src import guide

TEXT = guide.GUIDE.read_text(encoding="utf-8")


def test_every_landmark_returns_its_documented_rows(world: seahaven.World) -> None:
    assert guide.check(world, TEXT) == []


def test_every_small_co_loose_end_has_a_landmark() -> None:
    """small_co.md §7: the cast, and each loose end of functional spec §4.6, is one query away."""
    named = {landmark.key for landmark in guide.landmarks(TEXT)}
    assert named >= {
        f"small_co/{name}"
        for name in (
            "cast",
            "open_dispute",
            "past_due",
            "trial_ending_without_card",
            "paused",
            "renewals_this_week",
            "cancel_at_period_end",
            "credit_balance",
            "partial_refund",
            "pending_invoice_item",
            "duplicate_customers",
            "founder_test",
        )
    }


def test_every_big_co_loose_end_has_a_landmark() -> None:
    """big_co.md §7: each loose end of functional spec §5.7 is one query away."""
    named = {landmark.key for landmark in guide.landmarks(TEXT)}
    assert named >= {
        f"big_co/{name}"
        for name in (
            "dispute_queue",
            "dispute_stages",
            "overdue_enterprise",
            "next_ramp_step",
            "churn_risk",
            "status_counts",
            "draft_invoices",
            "pending_payouts",
            "duplicate_customers",
            "migration_cohort",
            "sla_credits",
        )
    }


@pytest.mark.parametrize("key", ["small_co/status_counts", "big_co/status_counts"])
def test_a_changed_row_is_reported(world: seahaven.World, key: str) -> None:
    """The check compares values, not just shapes: one altered number is a difference."""
    (landmark,) = [lm for lm in guide.landmarks(TEXT) if lm.key == key]
    rows = landmark.documented.replace('{"status": "active", "n": ', '{"status": "active", "n": 1')
    assert rows != landmark.documented
    altered = TEXT[: landmark.rows_start] + rows + TEXT[landmark.rows_end :]
    (problem,) = guide.check(world, altered)
    assert problem.startswith(landmark.key)


def test_write_restores_the_documented_rows(world: seahaven.World) -> None:
    emptied = TEXT.replace(guide.landmarks(TEXT)[0].documented, guide.render_rows([]), 1)
    assert guide.write(world, emptied) == TEXT


def test_a_query_without_order_by_is_refused(world: seahaven.World) -> None:
    text = (
        "```sql landmark=big_co/unordered\nSELECT id FROM payouts WHERE status = 'pending'\n```"
        "\n\n```json rows\n[]\n```\n"
    )
    assert any("no ORDER BY" in problem for problem in guide.check(world, text))


def test_a_malformed_landmark_is_reported(world: seahaven.World) -> None:
    text = (
        "```sql landmark=big_co/bad-name\nSELECT 1 AS n ORDER BY n\n```\n\n```json rows\n[]\n```\n"
    )
    assert any("parse" in problem for problem in guide.check(world, text))
