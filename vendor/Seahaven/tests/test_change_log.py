"""The change log across calls and nodes: what belongs to which call, and what never reaches it.

One record per row per call, in call order, over every node of the instance.
The shape of a single record is `test_changes.py`'s; what is pinned here is the
boundary of a call -- net inside it, never folded across it, on every node it
touched -- the ordinal that joins the log to a harness's trace, and the things
the log is not: the startup hooks' writes, a control tool, and a database read.
"""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import apsw
import pytest

from seahaven.call import Call, Handler
from seahaven.ctx import Ctx
from seahaven.errors import UnknownTool, WorldBug
from seahaven.instances import Instance
from seahaven.world import World
from tests.conftest import INSTANT_ISO, Boom, build_world, composable_world
from tests.test_changes import add

pytestmark = pytest.mark.filterwarnings("ignore:controller_run_sql is deprecated")


def keys(instance: Instance) -> list[tuple[int | None, str]]:
    """Each record as the call it belongs to and the row it changed."""
    return [(record.i, str(record.key["id"])) for record in instance.change_log()]


def placed(instance: Instance) -> list[tuple[int | None, str, str]]:
    """Each record as the call it belongs to, the node it landed on, and the table."""
    return [(record.i, record.world, record.table) for record in instance.change_log()]


@contextmanager
def counting_statements(*connections: apsw.Connection) -> Iterator[list[str]]:
    """Every statement run on any of `connections` for the length of the block."""
    statements: list[str] = []

    def trace(_cursor: apsw.Cursor, sql: str, _bindings: object) -> bool:
        statements.append(sql)
        return True

    for conn in connections:
        conn.exec_trace = trace
    try:
        yield statements
    finally:
        for conn in connections:
            conn.exec_trace = None


# ------------------------------------------------------------- one node, per call


def test_the_log_is_empty_before_any_call(instance: Instance) -> None:
    assert instance.change_log() == []
    assert instance.call_count == 0


def test_a_read_only_call_logs_nothing(instance: Instance) -> None:
    add(instance, "n1")

    instance.call("rows", sql="SELECT * FROM notes")

    assert keys(instance) == [(0, "n1")]
    assert instance.call_count == 2


def test_a_call_that_changed_nothing_logs_nothing(instance: Instance) -> None:
    add(instance, "n1", "hello", 3)

    instance.call("execute", sql="UPDATE notes SET n = 3 WHERE id = 'n1'")

    assert keys(instance) == [(0, "n1")]


def test_a_rolled_back_call_logs_nothing(instance: Instance) -> None:
    with pytest.raises(Boom):
        instance.call("write_then_fail", sql="INSERT INTO notes VALUES ('n1', 'body', 0)")

    assert instance.change_log() == []


def test_a_rolled_back_bulk_logs_nothing(instance: Instance) -> None:
    """The recording is outside the transaction, so it reads a changeset that rolled back."""
    with pytest.raises(Boom), instance.bulk() as ctx:
        ctx.db.execute("INSERT INTO notes VALUES ('b1', 'bulk', 0)")
        raise Boom("it did not work out")

    assert instance.change_log() == []


def test_rows_a_startup_hook_wrote_are_not_in_the_log(tmp_path: Path) -> None:
    """The hooks run before any session exists: seed rows are starting state."""
    world = build_world(tmp_path)

    @world.instance_startup
    def seed(ctx: Ctx) -> None:
        ctx.db.execute("INSERT INTO notes VALUES ('seeded', 'from a hook', 0)")

    with world.instance(None) as instance:
        assert instance.inspect().rows("SELECT id FROM notes") == [{"id": "seeded"}]
        assert instance.change_log() == []


def test_an_insert_then_an_update_in_one_call_is_one_insert(instance: Instance) -> None:
    instance.call(
        "execute",
        sql="INSERT INTO notes VALUES ('n1', 'hello', 0); UPDATE notes SET n = 9 WHERE id = 'n1';",
    )

    (record,) = instance.change_log()

    assert record.op == "insert"
    assert record.after == {"id": "n1", "body": "hello", "n": 9}


def test_an_insert_then_a_delete_in_one_call_logs_nothing(instance: Instance) -> None:
    instance.call(
        "execute",
        sql="INSERT INTO notes VALUES ('n1', 'hello', 0); DELETE FROM notes WHERE id = 'n1';",
    )

    assert instance.change_log() == []


def test_an_update_back_to_the_original_in_one_call_logs_nothing(instance: Instance) -> None:
    add(instance, "n1", "hello", 3)

    instance.call(
        "execute",
        sql="UPDATE notes SET n = 9 WHERE id = 'n1'; UPDATE notes SET n = 3 WHERE id = 'n1';",
    )

    assert keys(instance) == [(0, "n1")]


def test_the_same_row_in_two_calls_is_two_records(instance: Instance) -> None:
    """Not net across calls: counting rows straight off the log overcounts."""
    add(instance, "n1", "hello", 0)
    instance.call("execute", sql="UPDATE notes SET n = 9 WHERE id = 'n1'")

    assert [(record.i, record.op) for record in instance.change_log()] == [
        (0, "insert"),
        (1, "update"),
    ]


def test_an_update_back_to_the_original_across_two_calls_is_two_records(
    instance: Instance,
) -> None:
    add(instance, "n1", "hello", 3)
    instance.call("execute", sql="UPDATE notes SET n = 9 WHERE id = 'n1'")
    instance.call("execute", sql="UPDATE notes SET n = 3 WHERE id = 'n1'")

    assert [(record.i, record.before, record.after) for record in instance.change_log()[1:]] == [
        (1, {"n": 3}, {"n": 9}),
        (2, {"n": 9}, {"n": 3}),
    ]


def test_a_primary_key_rewrite_is_a_delete_and_an_insert(instance: Instance) -> None:
    add(instance, "n1", "hello", 3)
    instance.call("execute", sql="UPDATE notes SET id = 'n9' WHERE id = 'n1'")

    assert [(record.op, record.key["id"]) for record in instance.change_log()[1:]] == [
        ("delete", "n1"),
        ("insert", "n9"),
    ]


def test_records_are_ordered_by_call(instance: Instance) -> None:
    add(instance, "n9")
    add(instance, "n1")
    add(instance, "n5")

    assert keys(instance) == [(0, "n9"), (1, "n1"), (2, "n5")]


def test_bulk_writes_carry_no_ordinal(instance: Instance) -> None:
    """`bulk` is authoring, not a call: it happens after creation, and it counts."""
    add(instance, "n1")
    with instance.bulk() as ctx:
        ctx.db.execute("INSERT INTO notes VALUES ('b1', 'bulk', 0)")
    add(instance, "n2")

    assert keys(instance) == [(0, "n1"), (None, "b1"), (1, "n2")]
    assert instance.call_count == 2


def test_a_call_made_inside_bulk_is_logged_once_and_with_the_blocks_ordinal(
    instance: Instance,
) -> None:
    """A supported pattern (`docs/composition.md`): the call writes inside the block.

    Its rows commit with the block and not with the call, so they belong to the
    block: one record each, carrying the block's `i: None`. The call still took
    its ordinal and its call-log entry.
    """
    with instance.bulk() as ctx:
        ctx.db.execute("INSERT INTO notes VALUES ('b1', 'bulk', 0)")
        instance.call("execute", sql="INSERT INTO notes VALUES ('n1', 'called', 0)")

    assert keys(instance) == [(None, "b1"), (None, "n1")]
    assert instance.call_count == 1
    assert len(instance.call_log()) == 1


def test_a_call_inside_a_bulk_block_that_rolls_back_logs_nothing(instance: Instance) -> None:
    """The one recording is outside the transactions, so what rolled back was never read."""
    with pytest.raises(Boom), instance.bulk() as ctx:
        instance.call("execute", sql="INSERT INTO notes VALUES ('n1', 'called', 0)")
        ctx.db.execute("INSERT INTO notes VALUES ('b1', 'bulk', 0)")
        raise Boom("it did not work out")

    assert instance.change_log() == []
    assert instance.inspect().rows("SELECT id FROM notes") == []
    assert instance.call_count == 1


def test_a_call_inside_bulk_on_two_nodes_is_logged_once_per_row(
    emporium_instance: Instance,
) -> None:
    """The same rule across nodes: the block owns every row it and its calls wrote."""
    live = emporium_instance
    with live.bulk() as ctx:
        ctx.db.execute("INSERT INTO charge_owners VALUES ('ch_1', 'alice')")
        live.call("shop_place_order", total=10)

    assert placed(live) == [(None, "main", "charge_owners"), (None, "shop", "orders")]


# --------------------------------------------------------- the ordinal and its count


def test_a_tool_error_consumes_an_ordinal(instance: Instance) -> None:
    """A failed call is a call the harness made, so it is a call here."""
    with pytest.raises(Boom):
        instance.call("write_then_fail", sql="INSERT INTO notes VALUES ('n1', 'body', 0)")
    with pytest.raises(ValueError, match="a bug in world code"):
        instance.call("crash")
    add(instance, "n2")

    assert instance.call_count == 3
    assert keys(instance) == [(2, "n2")]


def test_a_middleware_short_circuit_consumes_an_ordinal(world: World) -> None:
    @world.middleware
    def refuse_writes(ctx: Ctx, call: Call, next_: Handler) -> object:
        if call.name == "execute":
            raise Boom("no writes today")
        return next_(ctx, call)

    with world.instance(None) as instance:
        with pytest.raises(Boom):
            add(instance, "n1")
        instance.call("rows", sql="SELECT 1")

        assert instance.call_count == 2
        assert instance.change_log() == []


def test_a_refused_name_takes_no_ordinal(instance: Instance) -> None:
    """A name the world does not have never reached it: the harness records that one."""
    with pytest.raises(UnknownTool):
        instance.call("no_such_tool")
    add(instance, "n1")

    assert instance.call_count == 1
    assert keys(instance) == [(0, "n1")]


def test_a_function_the_typed_call_cannot_resolve_takes_no_ordinal(instance: Instance) -> None:
    def not_a_tool(ctx: Ctx) -> None:
        """Never registered anywhere."""

    with pytest.raises(WorldBug, match="is not a tool"):
        instance.call(not_a_tool)

    assert instance.call_count == 0


def test_a_control_tool_and_tool_listing_are_not_calls(instance: Instance) -> None:
    instance.tools()
    instance.call("controller_run_sql", sql="SELECT 1")

    assert instance.call_count == 0
    assert instance.change_log() == []


# -------------------------------------------------------------------- determinism


def test_two_identical_episodes_serialise_byte_for_byte(world: World) -> None:
    def episode() -> str:
        with world.instance(None, seed=7, now=INSTANT_ISO) as instance:
            add(instance, "n2", "second", 1)
            add(instance, "n1", "first", 2)
            instance.call("execute", sql="UPDATE notes SET n = 9 WHERE id = 'n2'")
            with pytest.raises(Boom):
                instance.call("write_then_fail", sql="DELETE FROM notes")
            return json.dumps(
                [record.to_dict() for record in instance.change_log()], ensure_ascii=False
            )

    assert episode() == episode()


# ------------------------------------------------------------------ every node


def test_one_call_that_writes_to_three_nodes_is_one_ordinal(emporium_instance: Instance) -> None:
    """`settle_order` places an order, charges the account and notes the owner: one `i`."""
    live = emporium_instance
    live.call("settle_order", total=250)

    assert placed(live) == [
        (0, "main", "charge_owners"),
        (0, "payments", "charges"),
        (0, "shop", "orders"),
    ]
    assert live.call_count == 1


def test_a_nested_call_carries_the_outer_ordinal(emporium_instance: Instance) -> None:
    """The order and the charge are nested calls through `ctx.worlds`, with no `i` of their own."""
    live = emporium_instance
    live.call("shop_place_order", total=10)
    live.call("settle_order", total=250)

    assert [(record.i, record.world) for record in live.change_log()] == [
        (0, "shop"),
        (1, "main"),
        (1, "payments"),
        (1, "shop"),
    ]
    assert live.call_count == 2


def test_two_stores_of_one_world_are_told_apart_by_their_paths(
    emporium_instance: Instance,
) -> None:
    """Same world, same table, two nodes: the path is the only thing that differs."""
    live = emporium_instance
    live.call("pay_create_charge", amount=100)
    live.call("eu_create_charge", amount=200)

    charged = {
        record.world: (record.i, (record.after or {})["amount"]) for record in live.change_log()
    }

    assert charged == {"payments": (0, 100), "payments_eu": (1, 200)}


def test_a_bulk_block_reaching_two_nodes_carries_no_ordinal_on_either(
    emporium_instance: Instance,
) -> None:
    live = emporium_instance
    with live.bulk() as ctx:
        ctx.db.execute("INSERT INTO charge_owners VALUES ('ch_1', 'alice')")
        ctx.worlds.payments_eu.db.execute(
            "INSERT INTO charges VALUES ('ch_1', 5, 'eur', '2024-01-01T00:00:00Z')"
        )

    assert placed(live) == [(None, "main", "charge_owners"), (None, "payments_eu", "charges")]


def test_a_nested_call_that_rolls_back_leaves_nothing_on_either_node(tmp_path: Path) -> None:
    host = composable_world("host", work_dir=tmp_path / "work")
    child = composable_world("child")
    host.add_world(child, name="child")

    @child.tool(name="child_refuse")
    def refuse(ctx: Ctx) -> None:
        """Write, then fail: the child's own transaction is what undoes it."""
        ctx.db.execute("INSERT INTO child_rows (id, value) VALUES ('c1', 'never')")
        raise Boom("the child said no")

    @host.tool(name="host_try")
    def attempt(ctx: Ctx) -> None:
        """Write into the host's store, then call a child tool that refuses."""
        ctx.db.execute("INSERT INTO host_rows (id, value) VALUES ('h1', 'never')")
        ctx.worlds.child.call("child_refuse")

    with host.instance(None) as live:
        with pytest.raises(Boom, match="the child said no"):
            live.call("host_try")

        assert live.change_log() == []
        assert live.call_count == 1


def test_within_a_call_the_root_sorts_among_the_child_paths(tmp_path: Path) -> None:
    """`main` is a path like any other: alphabetical, so `child` comes before it."""
    host = composable_world("host", work_dir=tmp_path / "work")
    host.add_world(composable_world("child"), name="child")

    @host.tool(name="host_both")
    def both(ctx: Ctx) -> None:
        """Write to the host's own store first, then to the child's."""
        ctx.db.execute("INSERT INTO host_rows (id, value) VALUES ('h1', 'root')")
        ctx.worlds.child.call("child_write", value="nested")

    with host.instance(None) as live:
        live.call("host_both")

        assert [record.world for record in live.change_log()] == ["child", "main"]


# ---------------------------------------------------- what reading the log costs


def test_the_change_log_does_no_database_work(emporium_instance: Instance) -> None:
    """The records were rendered when their call committed; reading them is memory."""
    live = emporium_instance
    live.call("settle_order", total=250)
    connections = [runtime.db.conn for runtime in live._runtime.values()]

    with counting_statements(*connections) as watched:
        assert live.db.conn.execute("SELECT 1").get == 1
    assert watched  # the counter sees a statement when there is one

    with counting_statements(*connections) as statements:
        assert live.change_log()

    assert statements == []


def test_a_destroyed_instance_has_no_change_log(instance: Instance) -> None:
    """The log goes with the instance, as everything the lock guards does."""
    add(instance, "n1")
    instance.destroy()

    with pytest.raises(WorldBug, match="has been destroyed"):
        instance.change_log()


def test_the_log_is_the_instances_own_and_a_read_is_a_fresh_list(instance: Instance) -> None:
    add(instance, "n1")
    first = instance.change_log()
    first.clear()

    assert len(instance.change_log()) == 1
