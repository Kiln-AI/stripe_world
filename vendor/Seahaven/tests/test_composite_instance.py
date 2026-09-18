"""A composite instance: one file, id stream, state and session per node, and the tree's hooks.

The shapes are built inline for the same reason `test_composition.py` builds
them: what is interesting here is a shape -- a node two hosts reach, a sibling
that comes and goes, a hook that writes into another node's store -- and a
committed package per shape would be a directory of them. The committed tree
(`tests/worlds/README.md`) is what the end-to-end assertions use.
"""

import copy
import hashlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import emporium
import pytest

from seahaven.clock import Clock
from seahaven.ctx import Ctx
from seahaven.db import open_instance
from seahaven.errors import WorldBug
from seahaven.ids import BUILD_STREAM, CONTROL_STREAM, INSPECTION_STREAM, INSTANCE_STREAM, Ids
from seahaven.instances import node_seed
from seahaven.world import World
from tests.conftest import INSTANT_ISO, composable_world

pytestmark = pytest.mark.usefixtures("isolated_imports")


def rooted(name: str, tmp_path: Path) -> World:
    """A world that can make instances: its own directories, nobody else's."""
    return composable_world(name, fixtures_dir=tmp_path / "fixtures", work_dir=tmp_path / "work")


def two_levels(tmp_path: Path) -> World:
    """`main` -> `child` -> `child/grand`, three worlds with three tables."""
    host = rooted("host", tmp_path)
    child = composable_world("child")
    child.add_world(composable_world("grand"), name="grand")
    host.add_world(child, name="child")
    return host


def files(instance: Any) -> list[str]:
    return sorted(path.name for path in instance.dir.glob("*.sqlite"))


# ------------------------------------------------------------------ the files


def test_one_blank_file_per_node_named_by_its_path(tmp_path: Path) -> None:
    with two_levels(tmp_path).instance(None) as live:
        assert files(live) == ["state.child.sqlite", "state.child__grand.sqlite", "state.sqlite"]


def test_a_leaf_worlds_directory_is_the_one_file_it_is_today(tmp_path: Path) -> None:
    with rooted("solo", tmp_path).instance(None) as live:
        assert files(live) == ["state.sqlite"]
        assert live.state_path == live.dir / "state.sqlite"


def test_each_node_carries_its_own_worlds_schema(tmp_path: Path) -> None:
    with two_levels(tmp_path).instance(None) as live, live.bulk() as ctx:
        assert _tables(ctx.db) == ["host_rows"]
        assert _tables(ctx.worlds.child.db) == ["child_rows"]
        assert _tables(ctx.worlds.child.worlds.grand.db) == ["grand_rows"]


def test_every_node_is_open_on_the_instances_clock(tmp_path: Path) -> None:
    with two_levels(tmp_path).instance(None, now=INSTANT_ISO) as live, live.bulk() as ctx:
        for db in (ctx.db, ctx.worlds.child.db, ctx.worlds.child.worlds.grand.db):
            assert db.one("SELECT datetime('now') AS n") == {"n": INSTANT_ISO}


def test_each_node_has_its_own_state(tmp_path: Path) -> None:
    with two_levels(tmp_path).instance(None) as live, live.bulk() as ctx:
        ctx.state["who"] = "root"
        ctx.worlds.child.state["who"] = "child"
        assert (ctx.state["who"], ctx.worlds.child.state["who"]) == ("root", "child")
        assert ctx.worlds.child.worlds.grand.state == {}


def _tables(db: Any) -> list[str]:
    return [
        row["name"]
        for row in db.rows("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")
    ]


# ------------------------------------------------------------- the id streams


def test_the_roots_stream_is_untouched_by_the_nodes_below_it(tmp_path: Path) -> None:
    """The root's seed is the instance seed itself, so composition perturbs nothing."""
    alone = rooted("host", tmp_path / "a")
    with alone.instance(None, seed=7) as live:
        expected = live.call("host_write", value="x")["id"]

    with two_levels(tmp_path / "b").instance(None, seed=7) as live:
        assert live.call("host_write", value="x")["id"] == expected


def test_a_nodes_stream_does_not_move_when_a_sibling_is_added(tmp_path: Path) -> None:
    """The salt is the node's own path, so a sibling coming or going changes nothing."""
    with two_levels(tmp_path / "a").instance(None, seed=7) as live:
        expected = live.call("child_write", value="x")["id"]

    grown = two_levels(tmp_path / "b")
    grown.add_world(composable_world("sibling"), name="sibling")
    with grown.instance(None, seed=7) as live:
        assert live.call("child_write", value="x")["id"] == expected


def test_two_nodes_of_one_world_draw_different_ids(tmp_path: Path) -> None:
    host = rooted("host", tmp_path)
    leaf = composable_world("leaf")
    host.add_world(leaf, name="us", tool_prefix="us_")
    host.add_world(leaf, name="eu", tool_prefix="eu_", store="eu")
    with host.instance(None, seed=7) as live:
        assert (
            live.call("us_leaf_write", value="x")["id"]
            != live.call("eu_leaf_write", value="x")["id"]
        )


def test_the_same_seed_reproduces_every_nodes_stream(tmp_path: Path) -> None:
    host = two_levels(tmp_path)

    def ids() -> list[str]:
        with host.instance(None, seed=7) as live:
            return [
                live.call(f"{name}_write", value="x")["id"] for name in ("host", "child", "grand")
            ]

    assert ids() == ids()


def test_the_node_seed_of_the_root_is_the_instance_seed(tmp_path: Path) -> None:
    """Stated directly, because it is what keeps a leaf world's ids what they were."""
    assert node_seed(b"base", "main") == b"base"
    assert node_seed(b"base", "child") != b"base"
    assert node_seed(b"base", "child") == node_seed(b"base", "child")
    assert node_seed(b"base", "child") != node_seed(b"base", "child/grand")


# ------------------------------------------------------------ the SQL streams


def draws(db: Any, count: int = 3) -> list[tuple[int, bytes]]:
    """`count` draws of each seeded SQL function on one node's own connection."""
    rolled = []
    for _ in range(count):
        row = db.one("SELECT random() AS r, randomblob(8) AS b")
        assert row is not None
        rolled.append((row["r"], row["b"]))
    return rolled


def test_each_node_draws_its_sql_randomness_from_a_stream_of_its_own(tmp_path: Path) -> None:
    """`random()` is registered per connection, and there is one connection per node.

    Two nodes sharing a stream in SQL would be the same leak `ctx.ids` is salted
    by path to avoid, one door over: an agent drawing on one store would read out
    what a `DEFAULT` clause is about to write into another.
    """
    with two_levels(tmp_path).instance(None, seed=7) as live, live.bulk() as ctx:
        rolled = [
            draws(ctx.db),
            draws(ctx.worlds.child.db),
            draws(ctx.worlds.child.worlds.grand.db),
        ]
    assert len({tuple(node) for node in rolled}) == 3


def test_the_same_seed_replays_every_nodes_sql_stream(tmp_path: Path) -> None:
    host = two_levels(tmp_path)

    def rolled() -> list[list[tuple[int, bytes]]]:
        with host.instance(None, seed=7) as live, live.bulk() as ctx:
            return [
                draws(ctx.db),
                draws(ctx.worlds.child.db),
                draws(ctx.worlds.child.worlds.grand.db),
            ]

    assert rolled() == rolled()


def test_the_roots_sql_stream_is_untouched_by_the_nodes_below_it(tmp_path: Path) -> None:
    """The other half of "a leaf world is the degenerate case", in SQL.

    The root's node seed is the instance seed itself, so its connection registers
    `random()` and `randomblob()` from exactly the seed a world that adds nothing
    has always registered them from.
    """
    with rooted("host", tmp_path / "a").instance(None, seed=7) as alone, alone.bulk() as ctx:
        expected = draws(ctx.db)

    with two_levels(tmp_path / "b").instance(None, seed=7) as live, live.bulk() as ctx:
        assert draws(ctx.db) == expected
        base = live.seed

    # And pinned against the derivation itself, because the two above move
    # together under any change to the root's seed: `origin/main` registers an
    # instance's writable connection on the instance seed with nothing between.
    plain = open_instance(tmp_path / "plain.sqlite", Clock.from_iso(INSTANT_ISO), base)
    try:
        assert draws(plain) == expected
    finally:
        plain.close()


def test_a_node_named_after_a_sql_door_does_not_draw_that_doors_stream(tmp_path: Path) -> None:
    """A child may be called `instance`, `inspection`, `control` or `build`; so is each door.

    `node_seed` and `ids._stream_seed` both salt the one instance seed as
    `sha256(base + b"\0" + ...)`, and a node's name and a door's label share no
    namespace, so a node of that name would otherwise mint its identifiers out of
    the very stream a connection is handing to SQL -- and an agent's
    `SELECT random()` would read them out. The `node` tag in `node_seed` is what
    separates the two.
    """
    host = rooted("host", tmp_path)
    labels = (INSTANCE_STREAM, INSPECTION_STREAM, CONTROL_STREAM, BUILD_STREAM)
    named = [label.decode() for label in labels]
    for name in named:
        host.add_world(composable_world(name), name=name)

    with host.instance(None, seed=7) as live:
        base = live.seed
        minted = [live.call(f"{name}_write", value="x")["id"] for name in named]

    # The doors' own derivation, written out rather than imported, because it is
    # the shape being separated from and not an implementation detail to follow.
    echoes = [Ids(hashlib.sha256(base + b"\0" + label).digest()).uuid() for label in labels]
    assert minted != echoes
    assert not set(minted) & set(echoes)


# ------------------------------------------------------------- startup hooks


def recording(world: World, into: list[str]) -> None:
    """A hook that says it ran, and writes one row into its own store."""

    @world.instance_startup
    def note(ctx: Ctx) -> None:
        into.append(world.name)
        ctx.db.execute(f"INSERT INTO {world.name}_rows VALUES ('seed', 'seeded')")


def test_hooks_run_root_first_then_each_added_world_in_order(tmp_path: Path) -> None:
    ran: list[str] = []
    host = rooted("host", tmp_path)
    first, second, deep = (composable_world(name) for name in ("first", "second", "deep"))
    first.add_world(deep, name="deep")
    host.add_world(first, name="first")
    host.add_world(second, name="second")
    for world in (host, first, second, deep):
        recording(world, ran)

    with host.instance(None):
        pass

    assert ran == ["host", "first", "deep", "second"]


def test_a_node_two_hosts_reach_runs_its_hooks_once(tmp_path: Path) -> None:
    ran: list[str] = []
    host = rooted("host", tmp_path)
    middle, shared = composable_world("middle"), composable_world("shared")
    middle.add_world(shared, name="shared")
    # Prefixed only so that the two routes to `shared` do not contribute one name
    # twice, which is a seal error and a different test.
    host.add_world(middle, name="middle", tool_prefix="m_")
    host.add_world(shared, name="shared")
    for world in (host, middle, shared):
        recording(world, ran)

    with host.instance(None):
        pass

    assert ran == ["host", "middle", "shared"]


def regional(world: World, seen: dict[str, str]) -> None:
    @world.instance_startup
    def note(ctx: Ctx, *, region: str = "us") -> None:
        seen[world.name] = region
        ctx.state["region"] = region


def test_a_bound_keyword_reaches_its_own_nodes_hook(tmp_path: Path) -> None:
    seen: dict[str, str] = {}
    host = rooted("host", tmp_path)
    child = composable_world("child")
    for world in (host, child):
        regional(world, seen)
    host.add_world(child, name="child", startup={"region": "eu"})

    with host.instance(None):
        pass

    assert seen == {"host": "us", "child": "eu"}


def test_a_bound_keyword_is_merged_across_the_edges_that_reach_one_node(tmp_path: Path) -> None:
    """A dependency's storeless edge binds nothing, and must not stop the host configuring."""
    seen: dict[str, str] = {}
    host = rooted("host", tmp_path)
    middle, shared = composable_world("middle"), composable_world("shared")
    regional(shared, seen)
    middle.add_world(shared, name="payments")
    host.add_world(middle, name="middle", tool_prefix="m_")
    host.add_world(shared, name="payments", startup={"region": "eu"})

    with host.instance(None):
        pass

    assert seen == {"shared": "eu"}


def test_a_bound_keyword_is_not_overridable_by_a_reset_keyword(tmp_path: Path) -> None:
    """Bound is configuration; the same `reset` keyword still reaches every other hook."""
    seen: dict[str, str] = {}
    host = rooted("host", tmp_path)
    child, other = composable_world("child"), composable_world("other")
    for world in (host, child, other):
        regional(world, seen)
    host.add_world(child, name="child", startup={"region": "eu"})
    host.add_world(other, name="other")

    with host.instance(None, region="jp"):
        pass

    assert seen == {"host": "jp", "child": "eu", "other": "jp"}


def test_a_reset_keyword_only_an_added_worlds_hook_names_is_accepted(tmp_path: Path) -> None:
    seen: dict[str, str] = {}
    host = rooted("host", tmp_path)
    child = composable_world("child")
    host.add_world(child, name="child")
    regional(child, seen)

    with host.instance(None, region="jp"):
        pass

    assert seen == {"child": "jp"}


def test_a_keyword_no_hook_in_the_tree_names_is_refused_before_anything_is_made(
    tmp_path: Path,
) -> None:
    host = rooted("host", tmp_path)
    host.add_world(composable_world("child"), name="child")

    with pytest.raises(WorldBug, match=r"unknown reset argument\(s\): \['region'\]"):
        host.instance(None, region="jp")

    assert _no_instance_dirs(tmp_path / "work")


def test_a_root_hook_seeds_a_child_before_that_childs_own_hooks_run(tmp_path: Path) -> None:
    """The one way a per-instance value reaches one node without a broadcast keyword."""
    host = rooted("host", tmp_path)
    child = composable_world("child")
    host.add_world(child, name="child")

    @host.instance_startup
    def hand_it_over(ctx: Ctx, *, principal: str) -> None:
        # Every node's transaction is open before the first hook runs, which is
        # what makes a root hook's write into a child part of creation rather
        # than a committed row creation would have to undo.
        assert ctx.worlds.child.db.in_transaction
        ctx.worlds.child.state["owner"] = principal
        ctx.worlds.child.db.execute("INSERT INTO child_rows VALUES ('seed', ?)", principal)

    seen: dict[str, Any] = {}

    @child.instance_startup
    def read_it(ctx: Ctx) -> None:
        seen["state"] = ctx.state.get("owner")
        seen["rows"] = ctx.db.rows("SELECT value FROM child_rows")

    with host.instance(None, principal="ana") as live:
        assert seen == {"state": "ana", "rows": [{"value": "ana"}]}
        # Committed with every other node's transaction, and starting state
        # rather than a change the agent made.
        assert live.call("child_read") == ["ana"]
        assert live.change_log() == []


def test_a_hook_that_raises_leaves_no_directory(tmp_path: Path) -> None:
    host = rooted("host", tmp_path)
    child = composable_world("child")
    host.add_world(child, name="child")

    @host.instance_startup
    def write_into_the_child(ctx: Ctx) -> None:
        ctx.worlds.child.db.execute("INSERT INTO child_rows VALUES ('seed', 'ana')")

    @child.instance_startup
    def change_my_mind(ctx: Ctx) -> None:
        raise RuntimeError("no")

    with pytest.raises(RuntimeError):
        host.instance(None)

    assert _no_instance_dirs(tmp_path / "work")


def _no_instance_dirs(work: Path) -> bool:
    return not work.is_dir() or not any(path.is_dir() for path in work.iterdir())


# ---------------------------------------------------- the pinned node set


def test_a_world_added_after_an_instance_exists_makes_its_next_call_raise(
    tmp_path: Path,
) -> None:
    host = rooted("host", tmp_path)
    with host.instance(None) as live:
        assert live.call("host_write", value="before")["value"] == "before"
        host.add_world(composable_world("late"), name="late")
        with pytest.raises(WorldBug, match="create a new instance"):
            live.call("host_write", value="after")


def test_a_tool_registered_after_an_instance_exists_is_served(tmp_path: Path) -> None:
    """The node set is pinned; the registries are not."""
    host = rooted("host", tmp_path)
    child = composable_world("child")
    host.add_world(child, name="child")
    with host.instance(None) as live:

        @child.tool
        def late(ctx: Ctx) -> str:
            """Registered after the instance was made."""
            return "here"

        assert live.call("late") == "here"


def test_a_world_added_after_an_instance_exists_is_not_advertised_either(
    tmp_path: Path,
) -> None:
    """`tools()` and `call()` answer for one tree: the one the instance holds."""
    host = rooted("host", tmp_path)
    with host.instance(None) as live:
        assert [tool["name"] for tool in live.tools()] == ["host_write", "host_read"]
        host.add_world(composable_world("late"), name="late")
        with pytest.raises(WorldBug, match="create a new instance"):
            live.tools()


# ------------------------------------------------------------------- bulk


def test_bulk_opens_a_transaction_on_every_node(tmp_path: Path) -> None:
    host = two_levels(tmp_path)
    with host.instance(None) as live:
        with live.bulk() as ctx:
            ctx.db.execute("INSERT INTO host_rows VALUES ('a', 'root')")
            ctx.worlds.child.db.execute("INSERT INTO child_rows VALUES ('a', 'child')")
        assert (live.call("host_read"), live.call("child_read")) == (["root"], ["child"])


def test_a_bulk_block_that_raises_rolls_every_node_back(tmp_path: Path) -> None:
    host = two_levels(tmp_path)
    with host.instance(None) as live:
        with pytest.raises(RuntimeError), live.bulk() as ctx:
            ctx.db.execute("INSERT INTO host_rows VALUES ('a', 'root')")
            ctx.worlds.child.db.execute("INSERT INTO child_rows VALUES ('a', 'child')")
            raise RuntimeError("changed my mind")
        assert (live.call("host_read"), live.call("child_read")) == ([], [])


# ---------------------------------------------------------- the committed tree


@pytest.fixture
def emporium_instance(tmp_path: Path) -> Iterator[Any]:
    """A blank instance of the committed composite world, on its own directory."""
    world = copy.copy(emporium.world)
    world.work_dir = tmp_path / "work"
    with world.instance(None, now=INSTANT_ISO) as live:
        yield live


def test_the_committed_tree_makes_four_stores(emporium_instance: Any) -> None:
    assert files(emporium_instance) == [
        "state.payments.sqlite",
        "state.payments_eu.sqlite",
        "state.shop.sqlite",
        "state.sqlite",
    ]


def test_the_committed_trees_hooks_run_per_node(emporium_instance: Any) -> None:
    """Both payments accounts run the same hook, each against its own state."""
    with emporium_instance.bulk() as ctx:
        assert ctx.worlds.payments.state == {"region": "us"}
        assert ctx.worlds.payments_eu.state == {"region": "us"}
        assert ctx.worlds.shop.worlds.payments.state is ctx.worlds.payments.state
        assert ctx.worlds.payments.state is not ctx.worlds.payments_eu.state
