"""The change log of a composite: one list over every node, each record saying which node.

What is new here is `LogRecord.world` and the concatenation. Everything a record
*is* -- net per call, rolled-back calls leaving no trace, untracked tables and
FTS5 shadow tables excluded -- is `test_changes.py`'s and `test_change_log.py`'s,
and holds per node because each node records over its own world's tables.
"""

import copy
from collections.abc import Iterator
from pathlib import Path

import emporium
import pytest

from seahaven.changes import LogRecord
from seahaven.instances import Instance
from seahaven.world import World
from tests.conftest import INSTANT_ISO, composable_world

pytestmark = pytest.mark.usefixtures("isolated_imports")


@pytest.fixture
def live(tmp_path: Path) -> Iterator[Instance]:
    """A blank instance of the committed composite world, on its own directory."""
    world = copy.copy(emporium.world)
    world.work_dir = tmp_path / "work"
    with world.instance(None, now=INSTANT_ISO) as instance:
        yield instance


def three_levels(tmp_path: Path) -> World:
    """`main` -> `child` -> `child/grand`, three worlds with three tables."""
    host = composable_world("host", work_dir=tmp_path / "work")
    child = composable_world("child")
    child.add_world(composable_world("grand"), name="grand")
    host.add_world(child, name="child")
    return host


# ----------------------------------------------------------- which node, and what


def test_a_world_that_adds_nothing_says_main(tmp_path: Path) -> None:
    with composable_world("solo", work_dir=tmp_path / "work").instance(None) as live:
        live.call("solo_write", value="a row")

        assert [record.world for record in live.change_log()] == ["main"]


def test_the_log_runs_in_call_order_across_levels(tmp_path: Path) -> None:
    """The log is ordered by call, not by where the tree puts the node that was written."""
    host = three_levels(tmp_path)
    with host.instance(None) as live:
        # Written deepest first, which is the order the log has to report.
        live.call("grand_write", value="deepest")
        live.call("child_write", value="middle")
        live.call("host_write", value="root")

        assert [(record.i, record.world) for record in live.change_log()] == [
            (0, "child/grand"),
            (1, "child"),
            (2, "main"),
        ]


def test_a_log_record_renders_to_a_dict_carrying_its_world(tmp_path: Path) -> None:
    with composable_world("solo", work_dir=tmp_path / "work").instance(None) as live:
        live.call("solo_write", value="a row")

        (record,) = live.change_log()

        assert record.to_dict()["world"] == "main"
        assert record.to_dict()["table"] == "solo_rows"


def test_the_state_document_covers_every_node(live: Instance) -> None:
    """What an eval reads is the whole log, every node of the tree in it."""
    live.call("settle_order", total=250)

    logged = live.state()["state"]["db"]["log"]

    assert {record["world"] for record in logged} == {"main", "payments", "shop"}
    assert logged == [record.to_dict() for record in live.change_log()]


# ------------------------------------------------------- what each node excludes


def test_untracked_tables_are_each_worlds_own(tmp_path: Path) -> None:
    """A host that tracks its audit table and a child that does not, in one log."""
    host = composable_world(
        "host",
        work_dir=tmp_path / "work",
        extra_schema="CREATE TABLE audit (id TEXT PRIMARY KEY) STRICT;",
    )
    child = composable_world(
        "child",
        extra_schema="CREATE TABLE audit (id TEXT PRIMARY KEY) STRICT;",
        untracked_tables=["audit"],
    )
    host.add_world(child, name="child")

    with host.instance(None) as live:
        live.call("host_write", value="tracked")
        live.call("child_write", value="tracked")
        with live.bulk() as ctx:
            ctx.db.execute("INSERT INTO audit (id) VALUES ('kept')")
            ctx.worlds.child.db.execute("INSERT INTO audit (id) VALUES ('dropped')")

        assert {(record.world, record.table) for record in live.change_log()} == {
            ("main", "host_rows"),
            ("main", "audit"),
            ("child", "child_rows"),
        }


def test_an_fts5_shadow_table_is_excluded_on_the_node_that_has_one(tmp_path: Path) -> None:
    host = composable_world("host", work_dir=tmp_path / "work")
    child = composable_world("child", extra_schema="CREATE VIRTUAL TABLE docs USING fts5(body);")
    host.add_world(child, name="child")

    with host.instance(None) as live:
        live.call("host_write", value="a row")
        with live.bulk() as ctx:
            ctx.worlds.child.db.execute("INSERT INTO docs (body) VALUES ('searchable words')")

        assert [(record.world, record.table) for record in live.change_log()] == [
            ("main", "host_rows")
        ]


# ---------------------------------------------------------------- net per call


def test_a_record_is_the_net_of_its_call_per_node(tmp_path: Path) -> None:
    """An insert and an update of one row is one insert, in whichever node holds it."""
    host = composable_world("host", work_dir=tmp_path / "work")
    host.add_world(composable_world("child"), name="child")

    with host.instance(None) as live:
        with live.bulk() as ctx:
            ctx.worlds.child.db.execute("INSERT INTO child_rows VALUES ('c1', 'first')")
            ctx.worlds.child.db.execute("UPDATE child_rows SET value = 'second' WHERE id = 'c1'")

        assert live.change_log() == [
            LogRecord(
                i=None,
                world="child",
                table="child_rows",
                op="insert",
                key={"id": "c1"},
                before=None,
                after={"id": "c1", "value": "second"},
            )
        ]
