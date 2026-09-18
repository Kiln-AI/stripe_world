"""Resolution, contribution and the seal: the tree a world's declarations add up to.

The shapes here are built inline, because the interesting ones are shapes --
a diamond three levels deep, one world under two scopes, a grandchild two hosts
disagree about -- and a committed package per shape would be a directory of them.
The one committed tree, `emporium` (`tests/worlds/README.md`), is asserted whole:
it is what every later phase's tests are written against, and it is the case a
reader can go and read.
"""

import copy
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import apsw
import emporium
import payments
import pytest
import shop

from seahaven.composition import attached_limit
from seahaven.ctx import Ctx
from seahaven.errors import WorldBug
from seahaven.world import World

SCHEMA = "CREATE TABLE t (id TEXT PRIMARY KEY) STRICT;"


def make_world(name: str, *tools: str, **options: Any) -> World:
    """A world with nothing in it but the tools it is asked for, one per name."""
    options.setdefault("state_format", "seahaven.state/1")
    world = World(name, "1.0.0", SCHEMA, **options)
    for tool_name in tools:
        register(world, tool_name)
    return world


def register(world: World, name: str) -> Callable[..., Any]:
    """One tool that says which name it was registered under, and nothing else."""

    def tool(ctx: Ctx) -> str:
        """Say who this is."""
        return name

    tool.__name__ = name
    return world.tool(tool)


def paths(world: World) -> list[str]:
    return [node.path for node in world.composition().nodes]


# ---------------------------------------------------------------- one node


def test_a_leaf_world_is_one_node() -> None:
    world = make_world("solo", "a")
    (node,) = world.composition().nodes
    assert (node.path, node.file_name, node.schema_name) == ("main", "state.sqlite", "main")
    assert (node.scope, node.depth, node.aliases, dict(node.added)) == (None, 0, (), {})
    assert world.composition().edges == ()


def test_a_leaf_worlds_tool_list_is_its_own_registry_in_order() -> None:
    world = make_world("solo", "b", "a")
    assert list(world.composition().tools) == ["b", "a"]


def test_control_tools_are_never_contributed() -> None:
    world = make_world("solo", "a")
    assert "controller_run_sql" in world.tools
    assert "controller_run_sql" not in world.composition().tools


# ------------------------------------------------------------ canonical paths


def test_the_canonical_path_is_the_shallowest_route() -> None:
    host, middle, leaf = make_world("host"), make_world("middle"), make_world("leaf")
    middle.add_world(leaf, name="deep")
    host.add_world(middle, name="middle")
    host.add_world(leaf, name="shallow")
    assert paths(host) == ["main", "middle", "shallow"]
    assert host.composition().by_key[leaf, None].aliases == ("middle/deep",)


def test_equal_depth_is_broken_by_registration_order() -> None:
    host, first, second, leaf = (make_world(n) for n in ("host", "first", "second", "leaf"))
    first.add_world(leaf, name="from_first")
    second.add_world(leaf, name="from_second")
    host.add_world(first)
    host.add_world(second)
    assert host.composition().by_key[leaf, None].path == "first/from_first"
    assert host.composition().by_key[leaf, None].aliases == ("second/from_second",)


def test_depth_beats_registration_order() -> None:
    """A route registered first but one level deeper does not win the name."""
    host, deep, middle, leaf = (make_world(n) for n in ("host", "deep", "middle", "leaf"))
    middle.add_world(leaf, name="under_middle")
    deep.add_world(middle, name="under_deep")
    host.add_world(deep, name="deep")
    host.add_world(leaf, name="direct")
    assert host.composition().by_key[leaf, None].path == "direct"
    assert host.composition().by_key[leaf, None].aliases == ("deep/under_deep/under_middle",)


def deep_diamond(depth: int, *tools: str) -> list[World]:
    """A spine whose every level is reached by two branches: 2**depth routes to the last.

    `tools` are registered on the deepest world alone, so what a test does with
    them says something about contribution rather than about the shape.
    """
    spine = [make_world(f"s{level}") for level in range(depth)]
    spine.append(make_world(f"s{depth}", *tools))
    for level in range(depth):
        for side in ("l", "r"):
            branch = make_world(f"{side}{level}")
            branch.add_world(spine[level + 1], name=f"s{level + 1}")
            spine[level].add_world(branch, name=f"{side}{level}")
    return spine


def test_a_diamond_resolves_without_enumerating_its_routes() -> None:
    """Twenty levels of two-way branching is a million routes and sixty-one nodes.

    Enumerating alias *routes* would not finish. The edge set is bounded by nodes
    times children and identifies the composition just as completely, which is
    what this asserts by arriving at all. These worlds have no tools, so it says
    nothing about contribution; `test_a_diamond_does_not_multiply_its_tools` does.
    """
    depth = 20
    spine = deep_diamond(depth)
    composition = spine[0].composition()
    assert len(composition.nodes) == 1 + 3 * depth
    assert len(composition.edges) == 4 * depth
    # The leftmost route wins the name at every level, and the one edge that
    # reaches the leaf any other way is its single alias -- an edge, not a route.
    down_the_left = [f"l{level}/s{level + 1}" for level in range(depth)]
    leaf = composition.by_key[spine[depth], None]
    assert leaf.path == "/".join(down_the_left)
    assert leaf.aliases == ("/".join([*down_the_left[:-1], f"r{depth - 1}/s{depth}"]),)


def test_a_diamond_does_not_multiply_its_tools() -> None:
    """One tool at the bottom of a diamond is one entry per level, not two.

    Memoising the distinct contributions is not enough: a node reaching one
    grandchild through two children would concatenate its tools twice, doubling
    the list at every level. At this depth that is sixteen million entries and
    hours of work on a tree of seventy-four nodes, all of it under the attach
    bound and past every `add_world` check -- a hang rather than an error. The
    host blocks the tool, which is also the case that proves a repeat folded away
    deep in the tree is not reported when it never reaches the root.
    """
    depth = 24
    host = make_world("host")
    host.add_world(deep_diamond(depth, "charge")[0], name="deep", tool_block_list=["charge"])
    composition = host.composition()
    assert list(composition.tools) == []
    assert len(composition.nodes) == 2 + 3 * depth


def test_a_tool_reached_through_two_branches_is_a_collision() -> None:
    """Two routes and no renaming is two declarations of one name, wherever it is folded."""
    host, left, right, leaf = (make_world(n) for n in ("host", "left", "right", "leaf"))
    register(leaf, "charge")
    left.add_world(leaf, name="payments")
    right.add_world(leaf, name="payments")
    host.add_world(left, name="left")
    host.add_world(right, name="right")
    with pytest.raises(WorldBug) as raised:
        host.composition()
    assert "by both 'left/payments' and 'right/payments'" in str(raised.value)


def test_a_collision_a_host_never_serves_is_not_reported() -> None:
    """The repeat folded away inside the subtree goes with the entry that absorbed it."""
    host, middle, left, right, leaf = (
        make_world(n) for n in ("host", "middle", "left", "right", "leaf")
    )
    register(leaf, "charge")
    left.add_world(leaf, name="payments")
    right.add_world(leaf, name="payments")
    middle.add_world(left, name="left")
    middle.add_world(right, name="right")
    host.add_world(middle, name="middle", tool_block_list=["charge"])
    assert list(host.composition().tools) == []


# ------------------------------------------------------------ node identity


def test_the_same_object_with_the_default_store_is_one_node() -> None:
    host, first, second, leaf = (make_world(n) for n in ("host", "first", "second", "leaf"))
    first.add_world(leaf)
    second.add_world(leaf)
    host.add_world(first)
    host.add_world(second)
    assert paths(host) == ["main", "first", "second", "first/leaf"]


def test_a_named_store_is_a_second_node() -> None:
    host, leaf = make_world("host"), make_world("leaf")
    host.add_world(leaf, name="ours")
    host.add_world(leaf, name="theirs", store="theirs")
    assert paths(host) == ["main", "ours", "theirs"]


def test_two_worlds_naming_one_store_share_it() -> None:
    """Scope names are flat and global, so the same string is the same account."""
    host, first, second, leaf = (make_world(n) for n in ("host", "first", "second", "leaf"))
    first.add_world(leaf, store="eu")
    second.add_world(leaf, store="eu")
    host.add_world(first)
    host.add_world(second)
    assert paths(host) == ["main", "first", "second", "first/leaf"]
    assert host.composition().by_key[leaf, "eu"].aliases == ("second/leaf",)


def test_a_scope_propagates_to_the_whole_subtree() -> None:
    """Two accounts of one world do not share the store beneath them."""
    host, middle, leaf = make_world("host"), make_world("middle"), make_world("leaf")
    middle.add_world(leaf, name="tax")
    host.add_world(middle, name="ours")
    host.add_world(middle, name="theirs", store="theirs")
    assert paths(host) == ["main", "ours", "theirs", "ours/tax", "theirs/tax"]
    assert host.composition().by_key[leaf, None].path == "ours/tax"
    assert host.composition().by_key[leaf, "theirs"].path == "theirs/tax"


def test_the_marketplace_case() -> None:
    """A merchant's shop carries the merchant's payments, not the company's.

    The regression test for scope inheritance: with the scope read per edge, the
    shop's own storeless `add_world(payments)` resolved to the company's account
    and there was no way for the host to separate them.
    """
    company, storefront, wallet = (make_world(n) for n in ("company", "storefront", "wallet"))
    storefront.add_world(wallet, name="payments")
    company.add_world(wallet, name="payments")
    company.add_world(storefront, name="merchant_shop", store="merchant")
    assert paths(company) == ["main", "payments", "merchant_shop", "merchant_shop/payments"]
    assert company.composition().by_key[wallet, "merchant"].path == "merchant_shop/payments"
    assert company.composition().by_key[wallet, None].path == "payments"


def test_identity_is_resolved_within_one_root() -> None:
    """Two roots that add the same object each get their own tree; a world holds no state."""
    leaf = make_world("leaf")
    first, second = make_world("first"), make_world("second")
    first.add_world(leaf, name="here")
    second.add_world(leaf, name="there", store="eu")
    assert first.composition().by_key[leaf, None].path == "here"
    assert second.composition().by_key[leaf, "eu"].path == "there"
    assert (leaf, "eu") not in first.composition().by_key


# ------------------------------------------------------------- contribution


def test_a_child_is_filtered_by_the_allow_list() -> None:
    host, leaf = make_world("host", "own"), make_world("leaf", "kept", "dropped")
    host.add_world(leaf, tool_allow_list=["kept"])
    assert list(host.composition().tools) == ["own", "kept"]


def test_a_child_is_filtered_by_the_block_list() -> None:
    host, leaf = make_world("host"), make_world("leaf", "kept", "dropped")
    host.add_world(leaf, tool_block_list=["dropped"])
    assert list(host.composition().tools) == ["kept"]


def test_an_empty_allow_list_contributes_nothing() -> None:
    host, leaf = make_world("host", "own"), make_world("leaf", "hidden")
    host.add_world(leaf, tool_allow_list=[])
    assert list(host.composition().tools) == ["own"]


def test_a_prefix_renames_and_nothing_else() -> None:
    host, leaf = make_world("host"), make_world("leaf", "charge")
    host.add_world(leaf, tool_prefix="pay_")
    entry = host.composition().tools["pay_charge"]
    assert entry.tool.listing()["description"] == leaf.tools["charge"].listing()["description"]
    assert entry.tool.listing()["input_schema"] == leaf.tools["charge"].listing()["input_schema"]


def test_a_hosts_list_is_matched_against_the_childs_contributed_names() -> None:
    """A host names what the child contributes: already prefixed one level down."""
    host, middle, leaf = make_world("host"), make_world("middle"), make_world("leaf", "charge")
    middle.add_world(leaf, tool_prefix="inner_")
    host.add_world(middle, tool_allow_list=["inner_charge"], tool_prefix="outer_")
    assert list(host.composition().tools) == ["outer_inner_charge"]


def test_a_prefix_applies_to_the_whole_contribution() -> None:
    """Two prefixes stack, and the grandchild still owns the tool."""
    host, middle, leaf = make_world("host"), make_world("middle", "own"), make_world("leaf", "deep")
    middle.add_world(leaf, name="leaf", tool_prefix="inner_")
    host.add_world(middle, name="middle", tool_prefix="outer_")
    tools = host.composition().tools
    assert list(tools) == ["outer_own", "outer_inner_deep"]
    assert tools["outer_inner_deep"].node.path == "middle/leaf"
    assert tools["outer_own"].node.path == "middle"


def test_the_flat_list_is_the_host_then_each_child_in_order() -> None:
    host = make_world("host", "own_one", "own_two")
    for name in ("second", "first"):
        host.add_world(make_world(name, f"{name}_a", f"{name}_b"), tool_prefix=f"{name}_")
    assert list(host.composition().tools) == [
        "own_one",
        "own_two",
        "second_second_a",
        "second_second_b",
        "first_first_a",
        "first_first_b",
    ]


def test_by_fn_carries_every_entry_a_function_reaches() -> None:
    host, leaf = make_world("host"), make_world("leaf")
    fn = register(leaf, "charge")
    host.add_world(leaf, name="ours", tool_prefix="ours_")
    host.add_world(leaf, name="theirs", tool_prefix="theirs_", store="theirs")
    entries = host.composition().by_fn[fn]
    assert [entry.name for entry in entries] == ["ours_charge", "theirs_charge"]
    assert [entry.node.path for entry in entries] == ["ours", "theirs"]


# ------------------------------------------------------------- bound startup


def hook_taking(world: World, keyword: str) -> None:
    def hook(ctx: Ctx, **kwargs: Any) -> None:
        """Take anything, so binding either keyword is legal at the call site."""

    hook.__name__ = f"take_{keyword}"
    world.instance_startup(hook)


def test_bound_startup_is_merged_key_wise() -> None:
    """A dependency's storeless edge binds nothing, and must not make the node unconfigurable."""
    host, middle, leaf = make_world("host"), make_world("middle"), make_world("leaf")
    hook_taking(leaf, "region")
    middle.add_world(leaf, name="payments")
    host.add_world(middle, name="shop")
    host.add_world(leaf, name="payments", startup={"region": "eu"})
    assert host.composition().by_key[leaf, None].bound_startup == {"region": "eu"}


def test_two_edges_binding_different_keys_both_apply() -> None:
    host, middle, leaf = make_world("host"), make_world("middle"), make_world("leaf")
    hook_taking(leaf, "anything")
    middle.add_world(leaf, name="payments", startup={"tier": "gold"})
    host.add_world(middle, name="shop")
    host.add_world(leaf, name="payments", startup={"region": "eu"})
    assert host.composition().by_key[leaf, None].bound_startup == {"region": "eu", "tier": "gold"}


def test_accepted_startup_kwargs_is_the_union_across_the_tree() -> None:
    host, leaf = make_world("host"), make_world("leaf")

    @host.instance_startup
    def host_hook(ctx: Ctx, *, principal: str = "") -> None:
        """One keyword."""

    @leaf.instance_startup
    def leaf_hook(ctx: Ctx, *, region: str = "us") -> None:
        """Another."""

    host.add_world(leaf)
    assert host.composition().accepted_startup_kwargs == frozenset({"principal", "region"})


def test_accepted_startup_kwargs_is_none_when_a_hook_takes_var_kwargs() -> None:
    host, leaf = make_world("host"), make_world("leaf")
    hook_taking(leaf, "anything")
    host.add_world(leaf)
    assert host.composition().accepted_startup_kwargs is None


# ------------------------------------------------------------- the seal


def test_an_allow_list_naming_an_unknown_tool_is_refused() -> None:
    host, leaf = make_world("host"), make_world("leaf", "create_charge")
    host.add_world(leaf, name="payments", tool_allow_list=["crate_charge"])
    with pytest.raises(WorldBug) as raised:
        host.composition()
    assert "'crate_charge'" in str(raised.value)
    assert "it contributes: create_charge" in str(raised.value)


def test_a_block_list_naming_an_unknown_tool_is_refused() -> None:
    host, leaf = make_world("host"), make_world("leaf", "create_charge")
    host.add_world(leaf, name="payments", tool_block_list=["nope"])
    with pytest.raises(WorldBug) as raised:
        host.composition()
    assert "tool_block_list names 'nope'" in str(raised.value)


def test_a_duplicate_contributed_name_is_refused() -> None:
    host, leaf = make_world("host"), make_world("leaf", "charge")
    host.add_world(leaf, name="payments", tool_prefix="pay_")
    host.add_world(leaf, name="payments_eu", tool_prefix="pay_", store="eu")
    with pytest.raises(WorldBug) as raised:
        host.composition()
    assert "'pay_charge'" in str(raised.value)
    assert "'payments'" in str(raised.value)
    assert "'payments_eu'" in str(raised.value)


def test_two_unprefixed_views_of_one_node_collide() -> None:
    """One node reached twice under two names contributes its tools twice."""
    host, leaf = make_world("host"), make_world("leaf", "charge")
    host.add_world(leaf, name="one")
    host.add_world(leaf, name="two")
    with pytest.raises(WorldBug) as raised:
        host.composition()
    # Both routes reach one node, so the paths are equal and only the `add_world`
    # names tell the author which two lines to look at.
    assert "by both 'one' and 'two'" in str(raised.value)


def test_a_contributed_name_colliding_with_the_hosts_own_is_refused() -> None:
    host, leaf = make_world("host", "charge"), make_world("leaf", "charge")
    host.add_world(leaf, name="payments")
    with pytest.raises(WorldBug) as raised:
        host.composition()
    assert "'main'" in str(raised.value)


def test_a_contributed_name_that_openenv_reserves_is_refused() -> None:
    host, leaf = make_world("host"), make_world("leaf", "set")
    host.add_world(leaf, name="payments", tool_prefix="re")
    with pytest.raises(WorldBug) as raised:
        host.composition()
    assert "'reset'" in str(raised.value)
    assert "'payments'" in str(raised.value)


def test_a_contributed_name_that_is_a_control_tools_is_refused() -> None:
    host, leaf = make_world("host"), make_world("leaf", "run_sql")
    host.add_world(leaf, name="payments", tool_prefix="controller_")
    with pytest.raises(WorldBug) as raised:
        host.composition()
    assert "control tool" in str(raised.value)


def test_a_prefix_that_makes_an_invalid_tool_name_is_refused() -> None:
    host, leaf = make_world("host"), make_world("leaf", "create_charge")
    host.add_world(leaf, name="payments", tool_prefix="pay.")
    with pytest.raises(WorldBug) as raised:
        host.composition()
    assert "tool_prefix 'pay.' on the add_world at 'payments'" in str(raised.value)
    assert "'pay.create_charge'" in str(raised.value)


def test_a_bad_prefix_is_named_and_not_the_grandchild_that_owns_the_tool() -> None:
    """The author's mistake is on their own `add_world`, not in the package they added."""
    host, middle, leaf = make_world("host"), make_world("middle"), make_world("leaf", "charge")
    middle.add_world(leaf, name="leaf", tool_prefix="inner_")
    host.add_world(middle, name="middle", tool_prefix="bad.")
    with pytest.raises(WorldBug) as raised:
        host.composition()
    message = str(raised.value)
    assert "tool_prefix 'bad.' on the add_world at 'middle'" in message
    assert "inner_" not in message.split("produces")[0]


def test_a_reserved_name_from_a_grandchild_names_the_route_that_made_it() -> None:
    host, middle, leaf = make_world("host"), make_world("middle"), make_world("leaf", "set")
    middle.add_world(leaf, name="leaf")
    host.add_world(middle, name="middle", tool_prefix="re")
    with pytest.raises(WorldBug) as raised:
        host.composition()
    assert "the add_world at 'middle/leaf' contributes 'reset'" in str(raised.value)


def test_one_startup_key_bound_to_two_values_is_refused() -> None:
    host, middle, leaf = make_world("host"), make_world("middle"), make_world("leaf")
    hook_taking(leaf, "region")
    middle.add_world(leaf, name="payments", startup={"region": "us"})
    host.add_world(middle, name="shop")
    host.add_world(leaf, name="payments", startup={"region": "eu"})
    with pytest.raises(WorldBug) as raised:
        host.composition()
    assert "'region'" in str(raised.value)
    assert "'main/payments'" in str(raised.value)
    assert "'shop/payments'" in str(raised.value)


def test_the_attach_limit_is_what_the_installed_sqlite_reports() -> None:
    """Probed, never assumed: a differently built SQLite is refused with its own number."""
    probe = apsw.Connection(":memory:")
    try:
        assert attached_limit() == probe.limit(apsw.SQLITE_LIMIT_ATTACHED)
    finally:
        probe.close()


def test_more_nodes_than_sqlite_can_attach_is_refused() -> None:
    """One world under one scope per store name is the cheapest way over the bound."""
    limit = attached_limit()
    host, leaf = make_world("host"), make_world("leaf")
    for index in range(limit + 1):
        host.add_world(leaf, name=f"n{index}", store=f"s{index}")
    with pytest.raises(WorldBug) as raised:
        host.composition()
    assert f"{limit + 1} added stores" in str(raised.value)
    assert f"attach {limit}" in str(raised.value)


def test_exactly_the_attach_limit_is_allowed() -> None:
    limit = attached_limit()
    host, leaf = make_world("host"), make_world("leaf")
    for index in range(limit):
        host.add_world(leaf, name=f"n{index}", store=f"s{index}")
    assert len(host.composition().nodes) == limit + 1


# ------------------------------------------------------------- the epoch


def test_the_seal_is_cached_until_a_registration() -> None:
    host, leaf = make_world("host"), make_world("leaf", "charge")
    host.add_world(leaf, name="payments")
    sealed = host.composition()
    assert host.composition() is sealed
    register(leaf, "refund")
    assert host.composition() is not sealed


def test_a_failed_seal_is_retried_and_not_remembered() -> None:
    """The author sees the error on every use until they fix it."""
    host, leaf = make_world("host"), make_world("leaf", "charge")
    host.add_world(leaf, name="one")
    host.add_world(leaf, name="two")
    for _ in range(2):
        with pytest.raises(WorldBug):
            host.composition()
    assert host._seal is None


def test_a_tool_registered_on_a_leaf_after_the_host_sealed_is_served() -> None:
    """A world cannot observe its consumers, so the epoch is global and reseals everyone."""
    host, leaf = make_world("host"), make_world("leaf", "charge")
    host.add_world(leaf, name="payments", tool_prefix="pay_")
    assert list(host.composition().tools) == ["pay_charge"]
    register(leaf, "refund")
    assert list(host.composition().tools) == ["pay_charge", "pay_refund"]


def test_a_copy_of_a_world_keeps_its_added_worlds_and_reseals() -> None:
    host, leaf = make_world("host"), make_world("leaf", "charge")
    host.add_world(leaf, name="payments", tool_prefix="pay_")
    sealed = host.composition()
    twin = copy.copy(host)
    assert [added.name for added in twin.added_worlds] == ["payments"]
    assert twin.composition() is not sealed
    assert list(twin.composition().tools) == ["pay_charge"]
    # A copy is a distinct object, so it is a distinct node: its own tree, not the
    # original's.
    assert twin.composition().root.key != sealed.root.key


def test_a_world_added_after_the_host_sealed_is_seen_on_the_next_use() -> None:
    host, leaf = make_world("host"), make_world("leaf", "charge")
    assert paths(host) == ["main"]
    host.add_world(leaf, name="payments")
    assert paths(host) == ["main", "payments"]


# ------------------------------------------------------- the committed tree


def test_the_committed_composite_world_resolves() -> None:
    composition = emporium.world.composition()
    assert [(node.path, node.scope) for node in composition.nodes] == [
        ("main", None),
        ("payments", None),
        ("payments_eu", "eu"),
        ("shop", None),
    ]
    assert composition.by_key[payments.world, None].aliases == ("shop/payments",)
    assert composition.edges == (
        ("main", "payments", "payments"),
        ("main", "payments_eu", "payments_eu"),
        ("main", "shop", "shop"),
        ("shop", "payments", "payments"),
    )
    assert [node.file_name for node in composition.nodes] == [
        "state.sqlite",
        "state.payments.sqlite",
        "state.payments_eu.sqlite",
        "state.shop.sqlite",
    ]


def test_the_committed_composite_world_has_one_flat_tool_surface() -> None:
    composition = emporium.world.composition()
    assert list(composition.tools) == [
        "record_charge_owner",
        "settle_order",
        "pay_create_charge",
        "pay_list_charges",
        "eu_create_charge",
        "eu_list_charges",
        "shop_place_order",
    ]
    # `shop` adds payments with an empty allow list, so nothing of it reaches the
    # agent through the shop -- and the shop's own tool still does.
    assert [entry.node.path for entry in composition.tools.values()] == [
        "main",
        "main",
        "payments",
        "payments",
        "payments_eu",
        "payments_eu",
        "shop",
    ]


def test_the_shop_alone_hides_the_world_it_adds() -> None:
    assert list(shop.world.composition().tools) == ["place_order"]
    assert [node.path for node in shop.world.composition().nodes] == ["main", "payments"]


# ------------------------------------------------------------ the instance


def composite(tmp_path: Path, *, tools: Sequence[str] = ("own",)) -> World:
    host = make_world("host", *tools, fixtures_dir=tmp_path / "f", work_dir=tmp_path / "w")
    host.add_world(make_world("leaf", "charge"), name="payments", tool_prefix="pay_")
    return host


def test_instance_tools_serves_the_composite_list(tmp_path: Path) -> None:
    host = composite(tmp_path)
    with host.instance(None) as live:
        listed = live.tools()
    assert [tool["name"] for tool in listed] == ["own", "pay_charge"]


def test_a_contributed_listing_is_the_added_worlds_own_but_for_the_name(tmp_path: Path) -> None:
    host = composite(tmp_path)
    leaf = host.added_worlds[0].world
    with host.instance(None) as live:
        contributed = next(tool for tool in live.tools() if tool["name"] == "pay_charge")
    assert contributed == leaf.tools["charge"].listing() | {"name": "pay_charge"}
