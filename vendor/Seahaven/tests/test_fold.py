"""The fold: the net diff of an episode, computed from its change log.

`functional_spec.md` §3.6 defines the fold so that any reader computes the same
net diff from a saved document, and SQLite is the oracle it was defined from: a
session of the test's own on every node records the whole episode, and its
changeset is the net diff by construction. Every episode shape the suite
exercises is folded and compared against it, on one node and on `emporium`.
"""

from collections.abc import Callable
from pathlib import Path

import pytest

from seahaven.instances import Instance
from seahaven.world import World
from tests.conftest import build_world
from tests.fold_oracle import recording
from tests.fold_support import fold
from tests.test_changes import BLOB_KEY_SCHEMA, add

# A fixture is what makes an update and a delete possible at all: a row has to
# exist before the episode starts for a call to change one that is not its own.
STARTING_ROWS = (("n1", "first", 1), ("n2", "second", 2))


def write(instance: Instance, sql: str) -> None:
    instance.call("execute", sql=sql)


def insert(instance: Instance) -> None:
    add(instance, "n3", "third", 3)


def insert_then_update(instance: Instance) -> None:
    add(instance, "n3", "third", 3)
    write(instance, "UPDATE notes SET n = 9 WHERE id = 'n3'")


def insert_then_delete(instance: Instance) -> None:
    add(instance, "n3", "third", 3)
    write(instance, "DELETE FROM notes WHERE id = 'n3'")


def update_then_update(instance: Instance) -> None:
    write(instance, "UPDATE notes SET n = 9 WHERE id = 'n1'")
    write(instance, "UPDATE notes SET body = 'rewritten', n = 10 WHERE id = 'n1'")


def update_then_update_back(instance: Instance) -> None:
    write(instance, "UPDATE notes SET n = 9 WHERE id = 'n1'")
    write(instance, "UPDATE notes SET n = 1 WHERE id = 'n1'")


def update_then_delete(instance: Instance) -> None:
    write(instance, "UPDATE notes SET body = 'rewritten' WHERE id = 'n1'")
    write(instance, "DELETE FROM notes WHERE id = 'n1'")


def delete_then_insert_something_else(instance: Instance) -> None:
    write(instance, "DELETE FROM notes WHERE id = 'n1'")
    add(instance, "n1", "replaced", 7)


def delete_then_insert_the_same_row(instance: Instance) -> None:
    write(instance, "DELETE FROM notes WHERE id = 'n1'")
    add(instance, "n1", "first", 1)


def rewrite_a_primary_key(instance: Instance) -> None:
    """A delete plus an insert, in the log and in the changeset alike."""
    write(instance, "UPDATE notes SET id = 'n9' WHERE id = 'n1'")


def calls_around_bulk(instance: Instance) -> None:
    write(instance, "UPDATE notes SET n = 9 WHERE id = 'n1'")
    with instance.bulk() as ctx:
        ctx.db.execute("UPDATE notes SET body = 'in bulk' WHERE id = 'n1'")
        ctx.db.execute("INSERT INTO notes VALUES ('n4', 'fourth', 4)")
    write(instance, "DELETE FROM notes WHERE id = 'n2'")


def a_bit_of_everything(instance: Instance) -> None:
    insert_then_update(instance)
    update_then_delete(instance)
    delete_then_insert_something_else(instance)
    write(instance, "UPDATE notes SET n = 22 WHERE id = 'n2'")


EPISODES: tuple[Callable[[Instance], None], ...] = (
    insert,
    insert_then_update,
    insert_then_delete,
    update_then_update,
    update_then_update_back,
    update_then_delete,
    delete_then_insert_something_else,
    delete_then_insert_the_same_row,
    rewrite_a_primary_key,
    calls_around_bulk,
    a_bit_of_everything,
)


@pytest.fixture
def started(tmp_path: Path) -> World:
    """A world with a fixture holding the rows an episode changes."""
    world = build_world(tmp_path)
    with world.instance(None) as instance:
        with instance.bulk() as ctx:
            for id, body, n in STARTING_ROWS:
                ctx.db.execute("INSERT INTO notes VALUES (?, ?, ?)", id, body, n)
        instance.freeze("start", "two notes")
    return world


@pytest.mark.parametrize("episode", EPISODES, ids=lambda episode: episode.__name__)
def test_the_fold_of_the_log_is_the_cumulative_changeset(
    started: World, episode: Callable[[Instance], None]
) -> None:
    with started.instance("start") as instance, recording(instance) as sqlite:
        episode(instance)

        net = fold(instance.change_log())

        assert net == sqlite.net_diff()


def test_the_oracle_sees_the_rows_the_episode_left_behind(started: World) -> None:
    """Not a comparison of two empty lists: SQLite's own answer has the episode in it."""
    with started.instance("start") as instance, recording(instance) as sqlite:
        a_bit_of_everything(instance)

        net = sqlite.net_diff()

    assert {(change.op, change.key["id"]) for change in net} == {
        ("insert", "n3"),
        ("update", "n1"),
        ("update", "n2"),
    }


def test_the_fold_drops_what_cancelled_out(started: World) -> None:
    """Not a vacuous comparison of two empty lists: the log has records and the fold has none."""
    with started.instance("start") as instance:
        insert_then_delete(instance)

        assert len(instance.change_log()) == 2
        assert fold(instance.change_log()) == []


def test_the_fold_is_shorter_than_the_log_it_folds(started: World) -> None:
    with started.instance("start") as instance:
        a_bit_of_everything(instance)

        assert len(fold(instance.change_log())) < len(instance.change_log())


def test_the_log_and_the_fold_sort_blob_keys_the_same_way(tmp_path: Path) -> None:
    """The one key type whose published text orders differently from its raw value.

    The framework sorts the log and a consumer re-sorts its fold, so the two have
    to agree on the base64 -- which is all a consumer has. The parametrised test
    above cannot catch a disagreement, because both sides of its comparison are
    sorted by the fold's own order.
    """
    world = build_world(tmp_path, BLOB_KEY_SCHEMA)
    with world.instance(None) as instance:
        instance.call("execute", sql="INSERT INTO keyed VALUES (x'00', 'zero'), (x'fb', 'high')")

        log = instance.change_log()

        assert [record.key["k"] for record in log] == ["+w==", "AA=="]
        assert [net.key for net in fold(log)] == [record.key for record in log]


# ------------------------------------------------------------------ composite


def settle_twice(live: Instance) -> None:
    live.call("settle_order", total=250)
    live.call("settle_order", total=99)


def charge_then_refund_in_bulk(live: Instance) -> None:
    """An insert on one node, then a `bulk()` update of it and a delete on another."""
    charge = live.call("pay_create_charge", amount=100)
    order = live.call("shop_place_order", total=100)
    with live.bulk() as ctx:
        ctx.worlds.payments.db.execute("UPDATE charges SET amount = 0 WHERE id = ?", charge["id"])
        ctx.worlds.shop.db.execute("DELETE FROM orders WHERE id = ?", order["id"])


def charge_both_accounts_then_undo_one(live: Instance) -> None:
    """Two stores of one world, and a nested delete that cancels an insert on one of them."""
    us = live.call("pay_create_charge", amount=100)
    live.call("eu_create_charge", amount=200, currency="eur")
    with live.bulk() as ctx:
        ctx.worlds.payments.db.execute("DELETE FROM charges WHERE id = ?", us["id"])


def owner_rewritten_across_nodes(live: Instance) -> None:
    settled = live.call("settle_order", total=250)
    with live.bulk() as ctx:
        ctx.db.execute(
            "UPDATE charge_owners SET owner_id = 'someone else' WHERE charge_id = ?",
            settled["charge"],
        )
        ctx.worlds.payments.db.execute(
            "UPDATE charges SET currency = 'gbp' WHERE id = ?", settled["charge"]
        )


COMPOSITE_EPISODES: tuple[Callable[[Instance], None], ...] = (
    settle_twice,
    charge_then_refund_in_bulk,
    charge_both_accounts_then_undo_one,
    owner_rewritten_across_nodes,
)


@pytest.mark.parametrize("episode", COMPOSITE_EPISODES, ids=lambda episode: episode.__name__)
def test_the_fold_holds_across_every_node(
    emporium_instance: Instance, episode: Callable[[Instance], None]
) -> None:
    with recording(emporium_instance) as sqlite:
        episode(emporium_instance)

        assert fold(emporium_instance.change_log()) == sqlite.net_diff()


def test_the_composite_oracle_saw_every_node(emporium_instance: Instance) -> None:
    """Non-vacuity for the composite case: the net diff names three nodes and two stores."""
    with recording(emporium_instance) as sqlite:
        charge_both_accounts_then_undo_one(emporium_instance)
        settle_twice(emporium_instance)

        net = sqlite.net_diff()

    assert {change.world for change in net} == {"main", "payments", "payments_eu", "shop"}
    assert [change.world for change in net] == sorted(change.world for change in net)
