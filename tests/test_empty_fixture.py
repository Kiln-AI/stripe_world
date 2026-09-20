"""The committed `empty` fixture: what it is on disk and what an instance of it
starts from.

From the dispatcher phase on, "schema only" means the tables exist and the
tracked tables hold no rows; `counters` carries its seeded rows by design (a
static reference seed from the schema file, `components/data_model.md` §5) and
is untracked, so it never appears in a graded change log.
"""

import pytest
import seahaven
from seahaven.db import world_tables

from conftest import FIXTURE_NOW

pytestmark = pytest.mark.seahaven(fixture="empty")


def test_the_empty_fixture_is_committed(world: seahaven.World) -> None:
    fixtures = {frozen.id: frozen for frozen in world.fixtures()}
    assert set(fixtures) == {"empty"}
    empty = fixtures["empty"]
    assert empty.now == FIXTURE_NOW
    assert empty.parent_id is None
    assert empty.description.startswith("Schema only.")


def test_an_instance_of_empty_has_the_schema_and_no_rows(instance: seahaven.Instance) -> None:
    assert world_tables(instance.inspect().conn) == [
        "counters",
        "coupons",
        "customers",
        "events",
        "payment_methods",
        "prices",
        "products",
        "promotion_codes",
        "tax_rates",
    ]
    # No tracked table carries a row: no customer, no event, no payment method,
    # no catalog object.
    for table in (
        "customers",
        "events",
        "payment_methods",
        "products",
        "prices",
        "coupons",
        "promotion_codes",
        "tax_rates",
    ):
        assert instance.inspect().one(f"SELECT count(*) AS n FROM {table}") == {"n": 0}


def test_the_counters_seed_is_present_and_untracked(instance: seahaven.Instance) -> None:
    """One row per listable table, all at zero — the `x_seq` source.

    `counters` is in `untracked_tables`, so these seeded rows (and every bump
    a call makes) stay out of the change log a graded episode reads.
    """
    seeded = instance.inspect().rows("SELECT name, value FROM counters ORDER BY name")
    assert len(seeded) == 22
    assert {row["value"] for row in seeded} == {0}
    assert instance.change_log() == []
