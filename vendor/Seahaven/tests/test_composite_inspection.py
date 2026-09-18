"""The one place two nodes' files meet: a read-only handle with every store attached.

`inst.inspect()` and the control tools' own handle are opened the same way -- the
root as `main`, every added node attached under the schema its path derives -- and
what makes that safe is the order: the write-denying authorizer goes on last, so
it refuses a later `ATTACH` as well as every write. A world's own connection has
nothing attached to it at all, which is the other half of the same rule.

The attach bound itself is `test_composition.py`'s: it is a seal check, and a tree
over it never reaches an instance.
"""

import copy
from collections.abc import Iterator
from pathlib import Path

import emporium
import pytest

from seahaven.db import Db
from seahaven.errors import DbError, ToolError
from seahaven.instances import Instance
from tests.conftest import INSTANT_ISO, composable_world

pytestmark = [
    pytest.mark.usefixtures("isolated_imports"),
    pytest.mark.filterwarnings("ignore:controller_run_sql is deprecated"),
]


@pytest.fixture
def live(tmp_path: Path) -> Iterator[Instance]:
    """A blank instance of the committed composite world, with one order settled."""
    world = copy.copy(emporium.world)
    world.work_dir = tmp_path / "work"
    with world.instance(None, now=INSTANT_ISO) as instance:
        instance.call("settle_order", total=250)
        yield instance


def attached(db: Db) -> list[str]:
    """Every database this connection can see, in SQLite's own order."""
    return [str(row["name"]) for row in db.rows("PRAGMA database_list")]


# ------------------------------------------------------------- what is attached


def test_every_added_node_is_attached_under_its_schema_name(live: Instance) -> None:
    assert attached(live.inspect()) == ["main", "payments", "payments_eu", "shop"]


def test_a_nested_nodes_schema_name_joins_its_path_with_underscores(tmp_path: Path) -> None:
    host = composable_world("host", work_dir=tmp_path / "work")
    child = composable_world("child")
    child.add_world(composable_world("grand"), name="grand")
    host.add_world(child, name="child")

    with host.instance(None) as instance:
        assert attached(instance.inspect()) == ["main", "child", "child__grand"]


def test_a_world_that_adds_nothing_attaches_nothing(tmp_path: Path) -> None:
    with composable_world("solo", work_dir=tmp_path / "work").instance(None) as instance:
        assert attached(instance.inspect()) == ["main"]


def test_the_handle_is_opened_once_and_kept(live: Instance) -> None:
    assert live.inspect() is live.inspect()


# ------------------------------------------------------------------ reading it


def test_a_cross_node_join_answers_in_one_statement(live: Instance) -> None:
    """ "Was the order placed and was the card charged" is one question."""
    settled = live.inspect().rows(
        "SELECT o.id AS order_id, c.amount AS amount "
        "FROM shop.orders o "
        "JOIN charge_owners w ON w.owner_id = o.id "
        "JOIN payments.charges c ON c.id = w.charge_id"
    )

    assert [row["amount"] for row in settled] == [250]


def test_two_stores_of_one_world_are_two_schemas(live: Instance) -> None:
    db = live.inspect()

    assert db.one("SELECT count(*) AS n FROM payments.charges") == {"n": 1}
    assert db.one("SELECT count(*) AS n FROM payments_eu.charges") == {"n": 0}


# --------------------------------------------------------------- what is denied


def refused(db: Db, sql: str) -> str:
    """Run a statement that must not be allowed, and answer SQLite's own reason."""
    with pytest.raises(DbError) as raised:
        db.execute(sql)
    return raised.value.sqlite_message


def test_a_write_to_an_attached_node_is_denied(live: Instance) -> None:
    assert "not authorized" in refused(live.inspect(), "DELETE FROM shop.orders")


def test_a_write_to_the_root_is_denied_as_it_always_was(live: Instance) -> None:
    assert "not authorized" in refused(live.inspect(), "DELETE FROM charge_owners")


def test_attaching_anything_else_through_the_handle_is_denied(
    live: Instance, tmp_path: Path
) -> None:
    """The authorizer went on after the framework's own attaches, and it denies ATTACH."""
    statement = f"ATTACH DATABASE '{tmp_path / 'mine.sqlite'}' AS mine"

    assert "not authorized" in refused(live.inspect(), statement)


def test_detaching_a_node_through_the_handle_is_denied(live: Instance) -> None:
    assert "not authorized" in refused(live.inspect(), "DETACH DATABASE shop")


def test_a_worlds_own_connection_cannot_see_a_sibling_node(live: Instance) -> None:
    """Isolation between nodes is structural: `ctx.db` has nothing attached to it."""
    with live.bulk() as ctx:
        assert "no such table: shop.orders" in refused(ctx.db, "SELECT * FROM shop.orders")
        # Every node's own file is its own `main`, so the host's table is not
        # somewhere else on the child's connection: it is not there at all.
        assert "no such table: main.charge_owners" in refused(
            ctx.worlds.shop.db, "SELECT * FROM main.charge_owners"
        )


# ------------------------------------------------------------- the control tool


def test_controller_run_sql_reads_an_added_node_schema_qualified(live: Instance) -> None:
    answered = live.call(
        "controller_run_sql", sql="SELECT amount FROM payments.charges ORDER BY id"
    )

    assert answered["rows"] == [[250]]


def test_controller_run_sql_joins_across_nodes(live: Instance) -> None:
    answered = live.call(
        "controller_run_sql",
        sql=(
            "SELECT o.total FROM shop.orders o "
            "JOIN charge_owners w ON w.owner_id = o.id "
            "JOIN payments_eu.charges c ON c.id = w.charge_id"
        ),
    )

    # The company account was charged, not the EU one: the join is empty and the
    # statement is legal, which is exactly what a grader needs to be able to ask.
    assert answered["rows"] == []


def test_controller_run_sql_cannot_write_to_an_attached_node(live: Instance) -> None:
    with pytest.raises(ToolError) as raised:
        live.call("controller_run_sql", sql="DELETE FROM shop.orders")

    assert "read-only" in str(raised.value)


def test_the_control_handle_is_not_the_inspection_handle(live: Instance) -> None:
    """Two connections on one set of files: `run_statement` borrows connection state."""
    live.call("controller_run_sql", sql="SELECT 1")

    assert live._control is not None
    assert live._control is not live.inspect()
    assert attached(live._control) == attached(live.inspect())
