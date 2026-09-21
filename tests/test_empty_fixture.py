"""The committed `empty` fixture: what it is on disk and what an instance of it
starts from.

From the dispatcher phase on, "schema only" means the tables exist and the
tracked tables hold no rows; `counters` carries its seeded rows by design (a
static reference seed from the schema file, `components/data_model.md` §5) and
is untracked, so it never appears in a graded change log — as is
`idempotency_keys`, whose rows are middleware bookkeeping a graded episode
must not see (`components/cross_cutting.md` §3.1.8).
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
        "balance_transactions",
        "charges",
        "counters",
        "coupons",
        "customer_balance_transactions",
        "customers",
        "disputes",
        "events",
        "idempotency_keys",
        "invoices",
        "payment_intents",
        "payment_methods",
        "payouts",
        "prices",
        "products",
        "promotion_codes",
        "refunds",
        "setup_intents",
        "subscription_items",
        "subscriptions",
        "tax_rates",
    ]
    # No tracked table carries a row: no customer, no event, no payment
    # method, no catalog object, nothing on the money path, nothing billed.
    for table in (
        "customers",
        "events",
        "payment_methods",
        "products",
        "prices",
        "coupons",
        "promotion_codes",
        "tax_rates",
        "payment_intents",
        "charges",
        "setup_intents",
        "balance_transactions",
        "payouts",
        "subscriptions",
        "subscription_items",
        "invoices",
        "customer_balance_transactions",
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


def test_idempotency_keys_is_empty_and_untracked(instance: seahaven.Instance) -> None:
    """The keyed-POST store starts empty and never reaches the change log."""
    assert instance.inspect().one("SELECT count(*) AS n FROM idempotency_keys") == {"n": 0}
    assert "idempotency_keys" not in {row.table for row in instance.change_log()}
