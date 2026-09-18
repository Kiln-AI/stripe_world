"""Dispatch through a composition: which store a call runs against, and which chain it descends.

Inline shapes again (`test_composition.py`'s docstring says why), with the
committed tree standing in for the end-to-end case. The middleware tests all use
one device: every world's startup hook writes its own name into its own
`ctx.state`, and every world's middleware appends what *it* can see to one list.
A layer that ran with the wrong node's context says so in that list.
"""

import copy
import logging
import threading
from pathlib import Path
from typing import Any

import emporium
import pytest

from seahaven import instances
from seahaven.call import Call, Handler
from seahaven.ctx import Ctx
from seahaven.errors import ArgumentError, UnknownTool, WorldBug
from seahaven.handles import WorldHandle
from seahaven.instances import Instance
from seahaven.world import World
from tests.conftest import INSTANT_ISO, WAIT, Boom, Caller, composable_world

pytestmark = [
    pytest.mark.usefixtures("isolated_imports"),
    pytest.mark.filterwarnings("ignore:controller_run_sql is deprecated"),
]


def rooted(name: str, tmp_path: Path) -> World:
    return composable_world(name, fixtures_dir=tmp_path / "fixtures", work_dir=tmp_path / "work")


def traced(world: World, trace: list[str]) -> World:
    """A world that knows its own name in its own state, and says what its layer sees."""

    @world.instance_startup
    def name_itself(ctx: Ctx) -> None:
        ctx.state["who"] = world.name

    @world.middleware
    def record(ctx: Ctx, call: Call, next_: Handler) -> Any:
        trace.append(f"{world.name} sees {ctx.state.get('who')}")
        assert call is ctx.call, "every layer runs with its own node's ctx, bound to this call"
        return next_(ctx, call)

    return world


def three_levels(tmp_path: Path, trace: list[str]) -> World:
    """`main` -> `middle` -> `middle/leaf`, each with a middleware and a hook."""
    host = traced(rooted("host", tmp_path), trace)
    middle = traced(composable_world("middle"), trace)
    middle.add_world(traced(composable_world("leaf"), trace), name="leaf")
    host.add_world(middle, name="middle")
    return host


# ----------------------------------------------------------------- the store


def test_a_contributed_tool_runs_against_its_own_nodes_store(tmp_path: Path) -> None:
    host = rooted("host", tmp_path)
    host.add_world(composable_world("leaf"), name="leaf")
    with host.instance(None) as live:
        live.call("leaf_write", value="mine")
        assert live.call("leaf_read") == ["mine"]
        assert live.call("host_read") == []


def test_two_accounts_of_one_world_do_not_share_rows(tmp_path: Path) -> None:
    host = rooted("host", tmp_path)
    leaf = composable_world("leaf")
    host.add_world(leaf, name="us", tool_prefix="us_")
    host.add_world(leaf, name="eu", tool_prefix="eu_", store="eu")
    with host.instance(None) as live:
        live.call("us_leaf_write", value="dollars")
        assert live.call("us_leaf_read") == ["dollars"]
        assert live.call("eu_leaf_read") == []


# ----------------------------------------------------------------- the chain


def test_the_agent_chain_is_the_whole_canonical_route(tmp_path: Path) -> None:
    trace: list[str] = []
    with three_levels(tmp_path, trace).instance(None) as live:
        live.call("leaf_write", value="x")
    assert trace == ["host sees host", "middle sees middle", "leaf sees leaf"]


def test_a_tool_of_the_host_runs_only_the_hosts_layer(tmp_path: Path) -> None:
    trace: list[str] = []
    with three_levels(tmp_path, trace).instance(None) as live:
        live.call("host_write", value="x")
    assert trace == ["host sees host"]


def test_a_leaf_worlds_chain_is_what_it_always_was(tmp_path: Path) -> None:
    trace: list[str] = []
    seen: list[Call] = []
    world = traced(rooted("solo", tmp_path), trace)

    @world.middleware
    def keep(ctx: Ctx, call: Call, next_: Handler) -> Any:
        seen.append(call)
        return next_(ctx, call)

    with world.instance(None) as live:
        live.call("solo_write", value="x")
    assert trace == ["solo sees solo"]
    assert seen[0].node == "main"


def test_call_is_one_object_naming_the_owning_node(tmp_path: Path) -> None:
    seen: list[Call] = []
    host = rooted("host", tmp_path)
    middle = composable_world("middle")
    middle.add_world(composable_world("leaf"), name="leaf")
    host.add_world(middle, name="middle")
    for world in (host, middle):

        @world.middleware
        def keep(ctx: Ctx, call: Call, next_: Handler) -> Any:
            seen.append(call)
            assert ctx.call is call
            return next_(ctx, call)

    with host.instance(None) as live:
        live.call("leaf_write", value="x")

    assert seen[0] is seen[1]
    assert seen[0].node == "middle/leaf"


def test_a_middleware_that_rewrites_arguments_is_seen_by_the_owners_layer(
    tmp_path: Path,
) -> None:
    seen: list[Any] = []
    host = rooted("host", tmp_path)
    leaf = composable_world("leaf")
    host.add_world(leaf, name="leaf")

    @host.middleware
    def rewrite(ctx: Ctx, call: Call, next_: Handler) -> Any:
        if call.name != "leaf_write":
            return next_(ctx, call)
        return next_(ctx, call.with_arguments(value="rewritten"))

    @leaf.middleware
    def observe(ctx: Ctx, call: Call, next_: Handler) -> Any:
        if call.name == "leaf_write":
            seen.append(call.arguments["value"])
            assert ctx.call is call
        return next_(ctx, call)

    with host.instance(None) as live:
        live.call("leaf_write", value="original")
        assert live.call("leaf_read") == ["rewritten"]
    assert seen == ["rewritten"]


def test_a_host_gate_reads_its_own_state_and_the_owner_writes_its_own_store(
    tmp_path: Path,
) -> None:
    """The two examples the design gives for host middleware, both at once."""
    host = rooted("host", tmp_path)
    leaf = composable_world("leaf")
    host.add_world(leaf, name="leaf")

    @host.instance_startup
    def sign_in(ctx: Ctx, *, principal: str) -> None:
        ctx.state["principal"] = principal

    @host.middleware
    def only_ana(ctx: Ctx, call: Call, next_: Handler) -> Any:
        if ctx.state.get("principal") != "ana":
            raise Boom("not you")
        if call.node != "main":
            # The host's own store, while the call belongs to the leaf.
            ctx.db.execute("INSERT INTO host_rows VALUES (?, ?)", call.name, call.node)
        return next_(ctx, call)

    with host.instance(None, principal="ana") as live:
        live.call("leaf_write", value="x")
        assert live.call("host_read") == ["leaf"]
        assert live.call("leaf_read") == ["x"]

    with host.instance(None, principal="bo") as live, pytest.raises(Boom):
        live.call("leaf_write", value="x")


def reachable(worlds: Any, name: str) -> str:
    """Whether one name is a child of the node a `Worlds` belongs to."""
    try:
        worlds[name]
    except WorldBug:
        return f"no {name}"
    return f"has {name}"


def test_a_layers_worlds_is_its_own_nodes(tmp_path: Path) -> None:
    """The other door section 7.6 leaves a host layer that needs the owning node.

    A middleware's `ctx.worlds` reaches its own world's children and nothing else:
    the host cannot name `leaf`, which is the grandchild it is forbidden to know,
    and the intermediate can. Together with `call.node` that is enough for a host
    layer to reach the owner's store, which is what the last line asserts.
    """
    seen: list[str] = []
    host = rooted("host", tmp_path)
    middle = composable_world("middle")
    middle.add_world(composable_world("leaf"), name="leaf")
    host.add_world(middle, name="middle")

    @host.middleware
    def from_the_host(ctx: Ctx, call: Call, next_: Handler) -> Any:
        seen.append(f"host: {reachable(ctx.worlds, 'middle')}, {reachable(ctx.worlds, 'leaf')}")
        if call.name == "leaf_write":
            # `call.node` says who owns this call; `ctx.worlds` is how a layer
            # that is not that node reaches it.
            assert call.node == "middle/leaf"
            owner = ctx.worlds.middle.worlds.leaf
            owner.db.execute("INSERT INTO leaf_rows VALUES ('by the host', ?)", call.node)
        return next_(ctx, call)

    @middle.middleware
    def from_the_middle(ctx: Ctx, call: Call, next_: Handler) -> Any:
        seen.append(f"middle: {reachable(ctx.worlds, 'leaf')}, {reachable(ctx.worlds, 'middle')}")
        return next_(ctx, call)

    with host.instance(None) as live:
        live.call("leaf_write", value="x")
        assert sorted(live.call("leaf_read")) == ["middle/leaf", "x"]

    assert seen[:2] == ["host: has middle, no leaf", "middle: has leaf, no middle"]


# ------------------------------------------------------------- nested calls


def calling_host(tmp_path: Path, trace: list[str] | None = None) -> tuple[World, World]:
    """A host whose one tool reaches into the world it adds, and that world."""
    host = rooted("host", tmp_path)
    child = composable_world("child")
    host.add_world(child, name="child", tool_allow_list=[])
    if trace is not None:
        traced(host, trace)
        traced(child, trace)

    @host.tool
    def relay(ctx: Ctx, value: str) -> dict[str, Any]:
        """Write into the child through its own tool, and note it here."""
        row = ctx.worlds.child.call("child_write", value=value)
        ctx.db.execute("INSERT INTO host_rows VALUES (?, ?)", row["id"], value)
        return row

    return host, child


def test_host_code_can_call_a_tool_the_agent_cannot_see(tmp_path: Path) -> None:
    host, _child = calling_host(tmp_path)
    with host.instance(None) as live:
        assert "child_write" not in [tool["name"] for tool in live.tools()]
        live.call("relay", value="through")
        assert live.call("host_read") == ["through"]


def test_a_nested_call_runs_only_the_owning_worlds_chain(tmp_path: Path) -> None:
    trace: list[str] = []
    host, _child = calling_host(tmp_path, trace)
    with host.instance(None) as live:
        live.call("relay", value="x")
    assert trace == ["host sees host", "child sees child"]


def test_a_nested_call_can_reach_a_grandchild_through_its_parents_worlds(
    tmp_path: Path,
) -> None:
    host = rooted("host", tmp_path)
    child = composable_world("child")
    child.add_world(composable_world("grand"), name="grand")
    host.add_world(child, name="child")

    @host.tool
    def deep(ctx: Ctx, value: str) -> list[str]:
        """Reach two levels down, one name at a time."""
        ctx.worlds.child.worlds.grand.call("grand_write", value=value)
        return ctx.worlds.child.worlds.grand.call("grand_read")

    with host.instance(None) as live:
        assert live.call("deep", value="down") == ["down"]
        assert live.call("grand_read") == ["down"]


def test_host_code_writes_straight_into_an_added_worlds_store(tmp_path: Path) -> None:
    host = rooted("host", tmp_path)
    host.add_world(composable_world("child"), name="child")

    @host.tool
    def straight(ctx: Ctx) -> list[str]:
        """No tool of the child's: its connection, as the host's own."""
        ctx.worlds.child.db.execute("INSERT INTO child_rows VALUES ('a', 'raw')")
        return [row["value"] for row in ctx.worlds.child.db.rows("SELECT value FROM child_rows")]

    with host.instance(None) as live:
        assert live.call("straight") == ["raw"]
        assert live.call("child_read") == ["raw"]


def test_an_added_worlds_error_reaches_the_host_tool_as_itself(tmp_path: Path) -> None:
    host = rooted("host", tmp_path)
    child = composable_world("child")
    host.add_world(child, name="child", tool_allow_list=[])

    @child.tool
    def refuse(ctx: Ctx) -> None:
        """Fail the way the child's own world fails."""
        raise Boom("the child said no")

    @host.tool
    def catching(ctx: Ctx) -> dict[str, str]:
        """Decide what the agent sees, which is the point of raising rather than returning."""
        try:
            ctx.worlds.child.call("refuse")
        except Boom as error:
            return {"caught": error.code, "message": error.message}
        raise AssertionError("the nested call should have raised")

    @host.tool
    def passing(ctx: Ctx) -> None:
        """Let it through, unwrapped."""
        ctx.worlds.child.call("refuse")

    with host.instance(None) as live:
        assert live.call("catching") == {"caught": "boom", "message": "the child said no"}
        with pytest.raises(Boom) as raised:
            live.call("passing")
        assert raised.value.message == "the child said no"


def test_a_failure_after_a_nested_call_leaves_that_write_in_place(tmp_path: Path) -> None:
    """No cross-world atomicity, asserted, because evals depend on knowing it."""
    host = rooted("host", tmp_path)
    child = composable_world("child")
    host.add_world(child, name="child")

    @host.tool
    def half(ctx: Ctx) -> None:
        """Write to the child, then fail here."""
        ctx.worlds.child.call("child_write", value="committed")
        ctx.db.execute("INSERT INTO host_rows VALUES ('a', 'rolled back')")
        raise Boom("too late")

    with host.instance(None) as live:
        with pytest.raises(Boom):
            live.call("half")
        assert live.call("child_read") == ["committed"]
        assert live.call("host_read") == []


def test_a_nested_call_by_a_name_the_node_does_not_have_is_an_unknown_tool(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    host = rooted("host", tmp_path)
    host.add_world(composable_world("child"), name="child")

    @host.tool
    def mistyped(ctx: Ctx) -> None:
        """Name a tool of the child that is not there, and one that is not the child's to call."""
        ctx.worlds.child.call("child_writ")

    @host.tool
    def controlling(ctx: Ctx) -> None:
        """The framework's own tools are not part of a world's surface."""
        ctx.worlds.child.call("controller_run_sql", sql="SELECT 1")

    caplog.set_level(logging.INFO, logger="seahaven.instances")
    with host.instance(None) as live:
        for name in ("mistyped", "controlling"):
            with pytest.raises(UnknownTool):
                live.call(name)

    # Logged against the node the mistake was made to, as an agent's own unknown
    # name is logged against the instance.
    nested = [line for line in caplog.messages if "internal=true" in line]
    assert len(nested) == 2
    assert all(": unknown_tool in" in line for line in nested)
    assert all(line.endswith("(node=child, internal=true)") for line in nested)


def test_a_middleware_registered_after_the_instance_reaches_a_nested_call(
    tmp_path: Path,
) -> None:
    """Nested calls read the live seal, as agent-initiated calls do."""
    trace: list[str] = []
    host, child = calling_host(tmp_path)
    with host.instance(None) as live:

        @child.middleware
        def late(ctx: Ctx, call: Call, next_: Handler) -> Any:
            trace.append(call.name)
            return next_(ctx, call)

        live.call("relay", value="x")

    assert trace == ["child_write"]


def test_a_child_may_be_named_after_a_member_of_the_handle_it_answers(tmp_path: Path) -> None:
    """Nothing public on `Worlds` is a plain name, so no child name is shadowed."""
    host = rooted("host", tmp_path)
    host.add_world(composable_world("db"), name="db")
    host.add_world(composable_world("call"), name="call")
    host.add_world(composable_world("unbound"), name="unbound")

    @host.tool
    def reach(ctx: Ctx) -> list[str]:
        """Three children named after things a handle, or this module, has."""
        for child in ("db", "call", "unbound"):
            assert isinstance(ctx.worlds[child], WorldHandle)
        ctx.worlds.db.call("db_write", value="a")
        ctx.worlds.call.call("call_write", value="b")
        ctx.worlds.unbound.call("unbound_write", value="c")
        return (
            ctx.worlds.db.call("db_read")
            + ctx.worlds["call"].call("call_read")
            + ctx.worlds.unbound.call("unbound_read")
        )

    with host.instance(None) as live:
        assert live.call("reach") == ["a", "b", "c"]


def test_an_unknown_child_name_is_a_world_bug(tmp_path: Path) -> None:
    host = rooted("host", tmp_path)
    host.add_world(composable_world("child"), name="child")

    @host.tool
    def misspelt(ctx: Ctx, how: str) -> None:
        """Both spellings of a name that is not a child of this world."""
        if how == "attribute":
            ctx.worlds.chidl  # noqa: B018
        else:
            ctx.worlds["chidl"]

    with host.instance(None) as live:
        for how in ("attribute", "item"):
            with pytest.raises(WorldBug, match="adds no world named 'chidl'; it adds: child"):
                live.call("misspelt", how=how)


# ------------------------------------------------------- the lock and the gate


class CountingGate(instances._Gate):
    """The process-wide gate, counting how many times it was taken.

    Substituting an instrumented gate is what `_Gate` exists for. Counted rather
    than starved on purpose: a gate of one slot *would* deadlock on a nested call
    that took it -- the semaphore is not reentrant -- and a test that proves the
    bypass by hanging is a test that hangs when it fails.
    """

    def __init__(self, size: int) -> None:
        super().__init__(size)
        self.taken = 0

    def acquire(self, blocking: bool = True, timeout: float | None = None) -> bool:
        self.taken += 1
        return super().acquire(blocking, timeout)


def test_a_nested_call_does_not_take_the_gate(tmp_path: Path) -> None:
    host, _child = calling_host(tmp_path)
    counting = CountingGate(4)
    instances._gate = counting
    with host.instance(None) as live:
        live.call("relay", value="x")
        assert live.call("host_read") == ["x"]
    # Two calls above, and the nested one inside the first took nothing.
    assert counting.taken == 2


def test_a_second_thread_waits_for_the_whole_activation(tmp_path: Path) -> None:
    """The lock is re-entered by the nested call and released once, at the end."""
    host = rooted("host", tmp_path)
    child = composable_world("child")
    host.add_world(child, name="child")
    inside = threading.Event()
    carry_on = threading.Event()
    order: list[str] = []

    @host.tool
    def slow(ctx: Ctx) -> None:
        """Hold the instance across a nested call."""
        inside.set()
        assert carry_on.wait(WAIT)
        ctx.worlds.child.call("child_write", value="first")
        order.append("nested")

    with host.instance(None) as live:
        holder = Caller(lambda: live.call("slow"))
        holder.start()
        assert inside.wait(WAIT)
        second = Caller(lambda: (live.call("child_write", value="second"), order.append("second")))
        second.start()
        carry_on.set()
        holder.finish()
        second.finish()

    assert order == ["nested", "second"]


# ----------------------------------------------------------- handle lifetime


def test_a_handle_kept_past_its_call_raises(tmp_path: Path) -> None:
    kept: list[WorldHandle] = []
    host = rooted("host", tmp_path)
    host.add_world(composable_world("child"), name="child")

    @host.tool
    def keep(ctx: Ctx) -> None:
        """Do the one thing the docs say not to."""
        kept.append(ctx.worlds.child)

    with host.instance(None) as live:
        live.call("keep")
        handle = kept[0]
        for reach in (lambda: handle.db, lambda: handle.state, lambda: handle.worlds):
            with pytest.raises(WorldBug, match="after the call it belongs to returned"):
                reach()
        with pytest.raises(WorldBug, match="after the call it belongs to returned"):
            handle.call("child_read")
        # And a second call does not revive it.
        live.call("keep")
        with pytest.raises(WorldBug, match="after the call it belongs to returned"):
            handle.db  # noqa: B018


def test_a_handle_carried_to_another_thread_is_refused(tmp_path: Path) -> None:
    """Live when the second thread reads it, dead by the time it holds the lock.

    The one way past the epoch check at the top of `call`: the activation that
    made the handle is still running while the thread that took it is queued for
    the instance lock. What refuses it is that the lock was taken afresh rather
    than re-entered, which is a frame this handle knows nothing about.
    """
    host = rooted("host", tmp_path)
    host.add_world(composable_world("child"), name="child")
    handed = threading.Event()
    carry_on = threading.Event()
    kept: list[WorldHandle] = []

    @host.tool
    def hand_it_over(ctx: Ctx) -> None:
        """Give a live handle to another thread and hold the activation open."""
        kept.append(ctx.worlds.child)
        handed.set()
        assert carry_on.wait(WAIT)

    with host.instance(None) as live:
        holder = Caller(lambda: live.call("hand_it_over"))
        holder.start()
        assert handed.wait(WAIT)
        thief = Caller(lambda: kept[0].call("child_read"))
        thief.start()
        carry_on.set()
        holder.finish()
        with pytest.raises(WorldBug, match="after the call it belongs to returned"):
            thief.finish()


def test_a_handle_made_in_a_bulk_block_survives_a_call_inside_that_block(
    tmp_path: Path,
) -> None:
    host = rooted("host", tmp_path)
    host.add_world(composable_world("child"), name="child")
    with host.instance(None) as live:
        with live.bulk() as ctx:
            handle = ctx.worlds.child
            live.call("host_write", value="from inside")
            handle.db.execute("INSERT INTO child_rows VALUES ('a', 'still live')")
        assert live.call("child_read") == ["still live"]
        assert live.call("host_read") == ["from inside"]


def test_a_template_contexts_worlds_raises(tmp_path: Path) -> None:
    host = rooted("host", tmp_path)
    host.add_world(composable_world("child"), name="child")
    with host.instance(None) as live:
        with pytest.raises(WorldBug, match="not bound to a call"):
            live.ctx.worlds.child  # noqa: B018
        with pytest.raises(WorldBug, match="not bound to a call"):
            live.ctx.worlds["child"]


def test_creating_an_instance_from_inside_a_call_is_refused(tmp_path: Path) -> None:
    host = rooted("host", tmp_path)
    other = rooted("other", tmp_path / "other")

    @host.tool
    def sneak(ctx: Ctx) -> None:
        """The door an added world's author might look for. There is not one."""
        other.instance(None)

    with host.instance(None) as live:
        with pytest.raises(WorldBug, match=r"reach added worlds through ctx\.worlds"):
            live.call("sneak")
        # The flag is per call, not per thread for ever.
        with other.instance(None):
            pass


def test_bulk_does_not_close_the_door_on_making_an_instance(tmp_path: Path) -> None:
    """`bulk()` is authoring, not a call: a fixture generator may make another instance."""
    host = rooted("host", tmp_path)
    other = rooted("other", tmp_path / "other")
    with host.instance(None) as live, live.bulk(), other.instance(None):
        pass


# ------------------------------------------------------------------ the log


def test_the_log_line_carries_the_node_and_marks_a_nested_call_internal(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    host, _child = calling_host(tmp_path)
    caplog.set_level(logging.INFO, logger="seahaven.instances")
    with host.instance(None) as live:
        live.call("relay", value="x")
        with pytest.raises(UnknownTool):
            live.call("nope")

    lines = [
        record.getMessage() for record in caplog.records if record.name == "seahaven.instances"
    ]
    assert "call child_write on instance" in lines[0]
    assert lines[0].endswith("(node=child, internal=true)")
    assert "call relay on instance" in lines[1]
    assert lines[1].endswith("(node=main)")
    # Nothing owns a name that is not registered, so there is no node to name.
    assert "call nope on instance" in lines[2] and "node=" not in lines[2]


# ---------------------------------------------------------- the committed tree


@pytest.fixture
def live(tmp_path: Path) -> Any:
    """A blank instance of the committed composite world, on its own directory."""
    world = copy.copy(emporium.world)
    world.work_dir = tmp_path / "work"
    with world.instance(None, now=INSTANT_ISO) as instance:
        yield instance


def test_the_committed_tree_settles_an_order_across_three_stores(live: Instance) -> None:
    settled = live.call("settle_order", total=250)

    assert live.call("shop_place_order", total=1)["total"] == 1
    assert [charge["id"] for charge in live.call("pay_list_charges")] == [settled["charge"]]
    assert live.call("eu_list_charges") == []
    assert live.call("controller_run_sql", sql="SELECT charge_id FROM charge_owners")["rows"] == [
        [settled["charge"]]
    ]


def test_a_contributed_tool_validates_its_arguments_as_the_agent_sent_them(
    live: Instance,
) -> None:
    """The owning world's model, reached through the host's name for the tool."""
    with pytest.raises(ArgumentError) as raised:
        live.call("pay_create_charge", amount="not a number")
    assert raised.value.tool == "create_charge"
