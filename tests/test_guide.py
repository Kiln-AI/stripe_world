"""The fixture guide is true (functional spec §3.6, §6): every landmark query in
`docs/fixtures.md` returns, from the committed fixture, exactly the rows the guide shows."""

import seahaven
from fixtures_src import guide

TEXT = guide.GUIDE.read_text(encoding="utf-8")


def test_every_landmark_returns_its_documented_rows(world: seahaven.World) -> None:
    assert guide.check(world, TEXT) == []


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


def test_a_changed_row_is_reported(world: seahaven.World) -> None:
    """The check compares values, not just shapes: one altered number is a difference."""
    (landmark,) = [lm for lm in guide.landmarks(TEXT) if lm.key == "big_co/status_counts"]
    altered = TEXT.replace('{"status": "active", "n": ', '{"status": "active", "n": 1', 1)
    assert altered != TEXT
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
