"""Calling a tool by the function itself: which node answers, and what comes back.

Architecture section 8. Two things are being tested and they are not the same
thing. At run time: resolution -- which node of the tree owns a function, and what
is said when none does or two do -- and the object a call answers with. At *check*
time: that `ty` reads the overloads and resolves the result type, which is
`typed_calls_fixture.py` and the one test at the bottom of this module that runs
the checker over it.

Shapes are built inline, as `test_composition.py` argues for, with the committed
tree standing in where the point is that a real composition behaves this way.
"""

import copy
import logging
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

import emporium
import emporium.tools.billing as billing
import payments.tools.charges as charges
import pytest
from pydantic import BaseModel, Field

from seahaven.call import Call, Handler
from seahaven.ctx import Ctx
from seahaven.errors import ArgumentError, WorldBug
from seahaven.world import World
from tests.conftest import INSTANT_ISO

pytestmark = [
    pytest.mark.usefixtures("isolated_imports"),
    pytest.mark.filterwarnings("ignore:controller_run_sql is deprecated"),
]

REPOSITORY = Path(__file__).resolve().parent.parent

# The two functions come back untyped on purpose: what these tests drive is
# *resolution*, and `typed_calls_fixture.py` is where the signatures are checked.
type Tools = tuple[World, Callable[..., Any], Callable[..., Any]]


def rowed(name: str, **options: Any) -> Tools:
    """A world with a table of its own, and the two functions its tools were built from.

    The functions themselves, not their names: a host reaches an added world's
    tool by importing it, and this is what an import would hand back.
    """
    options.setdefault("state_format", "seahaven.state/1")
    world = World(
        name,
        "1.0.0",
        f"CREATE TABLE {name}_rows (id TEXT PRIMARY KEY, value TEXT NOT NULL) STRICT;",
        **options,
    )

    @world.tool(name=f"{name}_write")
    def write(ctx: Ctx, value: str) -> dict[str, str]:
        """Write one row into this world's own store."""
        row = {"id": ctx.ids.uuid(), "value": value}
        ctx.db.execute(
            f"INSERT INTO {name}_rows (id, value) VALUES (?, ?)", row["id"], row["value"]
        )
        return row

    @world.tool(name=f"{name}_read")
    def read(ctx: Ctx) -> list[str]:
        """Every value in this world's own store, oldest first."""
        return [row["value"] for row in ctx.db.rows(f"SELECT value FROM {name}_rows ORDER BY id")]

    return world, write, read


def rooted(name: str, tmp_path: Path) -> Tools:
    return rowed(name, fixtures_dir=tmp_path / "fixtures", work_dir=tmp_path / "work")


# ------------------------------------------------- resolution at the instance


def test_a_tool_of_the_root_is_called_by_its_function(tmp_path: Path) -> None:
    host, write, read = rooted("host", tmp_path)
    with host.instance(None) as live:
        live.call(write, value="mine")
        assert live.call(read) == ["mine"]


def test_a_contributed_tool_is_called_by_its_function_and_runs_on_its_own_store(
    tmp_path: Path,
) -> None:
    host, _write, host_read = rooted("host", tmp_path)
    leaf, leaf_write, leaf_read = rowed("leaf")
    host.add_world(leaf, name="leaf", tool_prefix="p_")
    with host.instance(None) as live:
        live.call(leaf_write, value="theirs")
        assert live.call(leaf_read) == ["theirs"]
        assert live.call(host_read) == []


def test_a_call_by_reference_is_logged_under_the_exposed_name(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """An eval groups its log on the name the agent would have used, either way in."""
    host, _write, _read = rooted("host", tmp_path)
    leaf, leaf_write, _leaf_read = rowed("leaf")
    host.add_world(leaf, name="leaf", tool_prefix="p_")
    caplog.set_level(logging.INFO, logger="seahaven.instances")
    with host.instance(None) as live:
        live.call(leaf_write, value="x")

    line = next(
        record.getMessage() for record in caplog.records if record.name == "seahaven.instances"
    )
    assert "call p_leaf_write on instance" in line
    assert line.endswith("(node=leaf)")


def test_a_failure_before_resolution_is_logged_under_the_function(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    host, _write, _read = rooted("host", tmp_path)
    _stranger, outsider, _read_it = rowed("stranger")
    caplog.set_level(logging.INFO, logger="seahaven.instances")
    with host.instance(None) as live, pytest.raises(WorldBug):
        live.call(outsider, value="x")

    line = next(
        record.getMessage() for record in caplog.records if record.name == "seahaven.instances"
    )
    assert "call rowed.<locals>.write on instance" in line


def test_a_function_of_a_world_outside_the_tree_is_refused(tmp_path: Path) -> None:
    host, _write, _read = rooted("host", tmp_path)
    _stranger, outsider, _read_it = rowed("stranger")
    with (
        host.instance(None) as live,
        pytest.raises(WorldBug, match="is not a tool of world 'host' or anything it adds"),
    ):
        live.call(outsider, value="x")


def test_a_function_that_is_not_a_tool_at_all_is_refused(tmp_path: Path) -> None:
    host, _write, _read = rooted("host", tmp_path)

    def loose(ctx: Ctx) -> None:
        """Never registered anywhere."""

    with host.instance(None) as live, pytest.raises(WorldBug, match="loose is not a tool"):
        live.call(loose)


def test_a_tool_kept_off_the_agents_surface_is_not_reachable_by_reference(
    tmp_path: Path,
) -> None:
    """`Instance.call` is the agent's surface however it is spelled; the handle is not."""
    host, _write, _read = rooted("host", tmp_path)
    leaf, leaf_write, _leaf_read = rowed("leaf")
    host.add_world(leaf, name="leaf", tool_allow_list=[])
    with (
        host.instance(None) as live,
        pytest.raises(WorldBug, match="is not a tool of world 'host'"),
    ):
        live.call(leaf_write, value="x")


def test_a_world_that_is_a_node_twice_makes_its_functions_ambiguous(tmp_path: Path) -> None:
    host, _write, _read = rooted("host", tmp_path)
    leaf, leaf_write, _leaf_read = rowed("leaf")
    host.add_world(leaf, name="us", tool_prefix="us_")
    host.add_world(leaf, name="eu", tool_prefix="eu_", store="eu")
    with host.instance(None) as live, pytest.raises(WorldBug) as raised:
        live.call(leaf_write, value="x")

    message = str(raised.value)
    assert "names more than one tool of world 'host'" in message
    assert "us_leaf_write at us" in message
    assert "eu_leaf_write at eu" in message
    assert "ctx.worlds.<name>.call(...)" in message


def test_one_function_registered_as_two_tools_is_ambiguous_too(tmp_path: Path) -> None:
    """The reason the per-world map is multi-valued, asserted from the outside."""
    host, write, _read = rooted("host", tmp_path)
    host.tool(write, name="host_write_again")
    with host.instance(None) as live, pytest.raises(WorldBug, match="names more than one tool"):
        live.call(write, value="x")


def test_a_control_tools_function_is_not_reachable_by_reference(tmp_path: Path) -> None:
    """Control tools are the harness's, not a world's surface, from either way in."""
    host, _write, _read = rooted("host", tmp_path)
    leaf, _leaf_write, _leaf_read = rowed("leaf")
    host.add_world(leaf, name="leaf")
    run_sql = leaf.tools["controller_run_sql"].fn

    @host.tool
    def reach(ctx: Ctx) -> None:
        """Name the framework's own tool on the world this one adds."""
        ctx.worlds.leaf.call(run_sql, sql="SELECT 1")

    with host.instance(None) as live:
        with pytest.raises(WorldBug, match="is not a tool"):
            live.call(host.tools["controller_run_sql"].fn, sql="SELECT 1")
        with pytest.raises(WorldBug, match="is not a tool of world 'leaf'"):
            live.call("reach")
        # By name on the instance they still run: it is the harness's own way in.
        assert live.call("controller_run_sql", sql="SELECT 1")["rows"] == [[1]]


# ---------------------------------------------------------- positional binding


def test_positional_arguments_bind_to_their_names(tmp_path: Path) -> None:
    """`*args: P.args` makes them legal to a type checker, so they have to run."""
    host, write, read = rooted("host", tmp_path)
    with host.instance(None) as live:
        live.call(write, "positional")
        assert live.call(read) == ["positional"]


def test_more_positional_arguments_than_the_tool_takes_is_refused(tmp_path: Path) -> None:
    host, write, _read = rooted("host", tmp_path)
    with host.instance(None) as live, pytest.raises(WorldBug, match="takes 1 argument"):
        live.call(write, "one", "two")


def test_an_argument_given_twice_is_refused(tmp_path: Path) -> None:
    host, write, _read = rooted("host", tmp_path)
    with (
        host.instance(None) as live,
        pytest.raises(WorldBug, match="value given both positionally and by name"),
    ):
        live.call(write, "one", value="two")


def test_an_aliased_argument_is_sent_under_the_name_the_tool_list_publishes(
    tmp_path: Path,
) -> None:
    """A `ParamSpec` can only spell the parameter, so `arguments` has to translate.

    An alias replaces the Python name on the wire (`docs/authoring.md`), and by
    name that is exactly what a caller must send. By reference there is no wire:
    the caller wrote the function's own parameter, and a call that type-checks
    has to run.
    """
    host, _write, _read = rooted("host", tmp_path)

    @host.tool
    def transfer(ctx: Ctx, from_: Annotated[str, Field(alias="from")], to: str) -> dict[str, str]:
        """Move money between two accounts."""
        return {"from": from_, "to": to}

    moved = {"from": "a", "to": "b"}
    with host.instance(None) as live:
        assert live.call(transfer, from_="a", to="b") == moved
        assert live.call(transfer, "a", "b") == moved
        assert live.call(transfer, "a", to="b") == moved
        # By name it is the wire's spelling, unchanged and untranslated.
        assert live.call("transfer", **moved) == moved
        with pytest.raises(ArgumentError):
            live.call("transfer", from_="a", to="b")


def test_a_tool_named_as_a_string_takes_its_arguments_by_name(tmp_path: Path) -> None:
    """The string overload has no positional arguments after the name; nor has the runtime."""
    host, _write, _read = rooted("host", tmp_path)
    with host.instance(None) as live, pytest.raises(WorldBug, match="passed by name"):
        live.call("host_write", "positionally")  # ty: ignore[invalid-argument-type]


# ------------------------------------------------------ resolution in a handle


def test_a_handle_resolves_the_account_the_instance_could_not(tmp_path: Path) -> None:
    host, _write, _read = rooted("host", tmp_path)
    leaf, leaf_write, leaf_read = rowed("leaf")
    host.add_world(leaf, name="us", tool_prefix="us_")
    host.add_world(leaf, name="eu", tool_prefix="eu_", store="eu")

    @host.tool
    def spend(ctx: Ctx, where: str, value: str) -> list[str]:
        """Write through one of the two accounts and read that account back."""
        ctx.worlds[where].call(leaf_write, value=value)
        return ctx.worlds[where].call(leaf_read)

    with host.instance(None) as live:
        assert live.call("spend", where="eu", value="euros") == ["euros"]
        assert live.call("spend", where="us", value="dollars") == ["dollars"]


def test_a_handle_reaches_a_tool_the_agent_cannot_see(tmp_path: Path) -> None:
    host, _write, host_read = rooted("host", tmp_path)
    leaf, leaf_write, _leaf_read = rowed("leaf")
    host.add_world(leaf, name="leaf", tool_allow_list=[])

    @host.tool
    def relay(ctx: Ctx, value: str) -> dict[str, str]:
        """Write into the child by reference, through its own node."""
        return ctx.worlds.leaf.call(leaf_write, value=value)

    with host.instance(None) as live:
        assert live.call("relay", value="hidden")["value"] == "hidden"
        assert live.call(host_read) == []


def test_a_handle_reaches_a_function_of_a_world_in_its_subtree(tmp_path: Path) -> None:
    """A reference names the tool exactly; the handle only has to name the store."""
    host, _write, _read = rooted("host", tmp_path)
    middle, _middle_write, _middle_read = rowed("middle")
    leaf, leaf_write, leaf_read = rowed("leaf")
    middle.add_world(leaf, name="leaf")
    host.add_world(middle, name="middle")

    @host.tool
    def reach(ctx: Ctx, value: str) -> list[str]:
        """Reach the grandchild through the child, by reference."""
        ctx.worlds.middle.call(leaf_write, value=value)
        return ctx.worlds.middle.call(leaf_read)

    with host.instance(None) as live:
        assert live.call("reach", value="deep") == ["deep"]
        assert live.call("leaf_read") == ["deep"]


def test_a_handle_refuses_a_function_no_world_beneath_it_owns(tmp_path: Path) -> None:
    host, _write, _read = rooted("host", tmp_path)
    leaf, _leaf_write, _leaf_read = rowed("leaf")
    host.add_world(leaf, name="leaf")
    _stranger, outsider, _read_it = rowed("stranger")

    @host.tool
    def reach(ctx: Ctx) -> None:
        """Name something the child has never heard of."""
        ctx.worlds.leaf.call(outsider, value="x")

    with (
        host.instance(None) as live,
        pytest.raises(WorldBug, match="is not a tool of world 'leaf' or anything it adds"),
    ):
        live.call("reach")


def test_a_handle_refuses_a_function_two_nodes_beneath_it_own(tmp_path: Path) -> None:
    host, _write, _read = rooted("host", tmp_path)
    middle, _middle_write, _middle_read = rowed("middle")
    leaf, leaf_write, _leaf_read = rowed("leaf")
    middle.add_world(leaf, name="us", tool_prefix="us_")
    middle.add_world(leaf, name="eu", tool_prefix="eu_", store="eu")
    host.add_world(middle, name="middle")

    @host.tool
    def reach(ctx: Ctx) -> None:
        """Two accounts beneath one handle: the handle is the wrong one to ask."""
        ctx.worlds.middle.call(leaf_write, value="x")

    with host.instance(None) as live, pytest.raises(WorldBug) as raised:
        live.call("reach")

    message = str(raised.value)
    assert "names more than one tool of world 'middle'" in message
    assert "leaf_write at middle/us" in message
    assert "leaf_write at middle/eu" in message


def test_a_nested_call_by_reference_runs_only_the_owning_chain(tmp_path: Path) -> None:
    seen: list[str] = []
    host, _write, _read = rooted("host", tmp_path)
    leaf, leaf_write, _leaf_read = rowed("leaf")
    host.add_world(leaf, name="leaf")

    for world in (host, leaf):

        @world.middleware
        def record(ctx: Ctx, call: Call, next_: Handler, world: World = world) -> Any:
            seen.append(f"{world.name}:{call.name}")
            return next_(ctx, call)

    @host.tool
    def relay(ctx: Ctx, value: str) -> dict[str, str]:
        """One nested call by reference."""
        return ctx.worlds.leaf.call(leaf_write, value=value)

    with host.instance(None) as live:
        live.call("relay", value="x")

    assert seen == ["host:relay", "leaf:leaf_write"]


def test_a_call_by_reference_to_a_grandchild_runs_only_the_owners_chain(tmp_path: Path) -> None:
    """Through the child's handle the child's own middleware does not see the call."""
    seen: list[str] = []
    host, _write, _read = rooted("host", tmp_path)
    middle, _middle_write, _middle_read = rowed("middle")
    leaf, leaf_write, _leaf_read = rowed("leaf")
    middle.add_world(leaf, name="leaf")
    host.add_world(middle, name="middle")

    for world in (host, middle, leaf):

        @world.middleware
        def record(ctx: Ctx, call: Call, next_: Handler, world: World = world) -> Any:
            seen.append(f"{world.name}:{call.name}")
            return next_(ctx, call)

    @host.tool
    def reach(ctx: Ctx) -> dict[str, str]:
        """Reach the grandchild through the child, by reference."""
        return ctx.worlds.middle.call(leaf_write, value="deep")

    with host.instance(None) as live:
        live.call("reach")
        assert seen == ["host:reach", "leaf:leaf_write"]
        seen.clear()
        # The agent's way in descends every node on the route instead.
        live.call("leaf_write", value="agent")
        assert seen == ["host:leaf_write", "middle:leaf_write", "leaf:leaf_write"]


def test_a_handle_translates_and_binds_the_same_way_the_instance_does(tmp_path: Path) -> None:
    """`arguments_of` serves both ways in, and the handle's `*args` reach it too."""
    host, _write, _read = rooted("host", tmp_path)
    leaf, _leaf_write, _leaf_read = rowed("leaf")
    host.add_world(leaf, name="leaf", tool_allow_list=[])

    @leaf.tool
    def transfer(ctx: Ctx, from_: Annotated[str, Field(alias="from")], to: str) -> dict[str, str]:
        """Move money between two accounts."""
        return {"from": from_, "to": to}

    @host.tool
    def relay(ctx: Ctx) -> list[dict[str, str]]:
        """The three spellings a type checker allows, through the child's handle."""
        return [
            ctx.worlds.leaf.call(transfer, from_="a", to="b"),
            ctx.worlds.leaf.call(transfer, "a", "b"),
            ctx.worlds.leaf.call(transfer, "a", to="b"),
        ]

    with host.instance(None) as live:
        assert live.call("relay") == [{"from": "a", "to": "b"}] * 3


# --------------------------------------------------- what a call answers with


class Charge(BaseModel):
    """What a world returns when it returns a model rather than a bare dict."""

    id: str
    amount: int


def test_a_call_answers_with_the_object_the_tool_returned(tmp_path: Path) -> None:
    host, _write, _read = rooted("host", tmp_path)

    @host.tool
    def charge(ctx: Ctx, amount: int) -> Charge:
        """A tool that returns a model, as the authoring guidance asks for."""
        return Charge(id="ch_1", amount=amount)

    with host.instance(None) as live:
        assert live.call(charge, amount=5) == Charge(id="ch_1", amount=5)
        assert live.call("charge", amount=5) == Charge(id="ch_1", amount=5)


def test_a_host_tool_receives_an_added_worlds_model_as_itself(tmp_path: Path) -> None:
    host, _write, _read = rooted("host", tmp_path)
    leaf, _leaf_write, _leaf_read = rowed("leaf")
    host.add_world(leaf, name="leaf", tool_allow_list=[])

    @leaf.tool
    def mint(ctx: Ctx, amount: int) -> Charge:
        """The added world's own model."""
        return Charge(id="ch_1", amount=amount)

    @host.tool
    def relay(ctx: Ctx) -> dict[str, Any]:
        """What the host gets back is the model, not its rendering."""
        got = ctx.worlds.leaf.call(mint, amount=7)
        assert isinstance(got, Charge)
        return {"amount": got.amount}

    with host.instance(None) as live:
        assert live.call("relay") == {"amount": 7}


def test_a_result_that_cannot_be_rendered_still_rolls_the_call_back(tmp_path: Path) -> None:
    """The proof stays inside the transaction; only the rendering is discarded."""
    host, _write, read = rooted("host", tmp_path)

    @host.tool
    def write_then_answer_with_bytes(ctx: Ctx) -> Any:
        """Write a row, then answer with something no wire carries."""
        ctx.db.execute("INSERT INTO host_rows VALUES ('r1', 'written')")
        return {"blob": b"\x00"}

    with host.instance(None) as live:
        with pytest.raises(WorldBug, match="bytes"):
            live.call(write_then_answer_with_bytes)
        assert live.call(read) == []


# -------------------------------------------------------- the committed tree


def test_the_committed_tree_is_reached_by_reference(tmp_path: Path) -> None:
    world = copy.copy(emporium.world)
    world.work_dir = tmp_path / "work"
    with world.instance(None, now=INSTANT_ISO) as live:
        settled = live.call(billing.settle_order, total=100)

        # `settle_order` belongs to one node, and reaches two more by name.
        assert live.call("pay_list_charges") == [
            {
                "id": settled["charge"],
                "amount": 100,
                "currency": "usd",
                "created_at": INSTANT_ISO,
            }
        ]
        # Two payments accounts, so a payments function alone does not name a store.
        with pytest.raises(WorldBug, match="names more than one tool"):
            live.call(charges.create_charge, amount=100)


# ------------------------------------------------------------------ the gate


def test_ty_resolves_the_result_type_of_a_call_by_reference() -> None:
    """The section 8.1 gate: a checker that cannot read the overloads fails here.

    `typed_calls_fixture.py` is the fixture and its docstring says what each line
    of it asserts. It is inside the repository's `ty check` as well, so CI fails
    on it whether or not this test runs; this is the same gate said as a test, so
    that a change to the overloads is answered by the suite and not only by CI.
    """
    checked = subprocess.run(
        [sys.executable, "-m", "ty", "check", "tests/typed_calls_fixture.py"],
        cwd=REPOSITORY,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert checked.returncode == 0, checked.stdout + checked.stderr
