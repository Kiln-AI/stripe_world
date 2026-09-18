"""`world.add_world`: what a host declares, and what it is told at the line it declared it on.

Only the checks that are knowable from the two `World` objects in hand live here.
Everything that needs the whole tree -- a name collision after prefixing, a list
naming a tool that does not exist, the attach bound -- is the seal's, and is in
`test_composition.py`.
"""

from types import MappingProxyType

import pytest

from seahaven.ctx import Ctx
from seahaven.errors import WorldBug
from seahaven.world import World

SCHEMA = "CREATE TABLE t (id TEXT PRIMARY KEY) STRICT;"


def make_world(name: str) -> World:
    return World(name, "1.0.0", SCHEMA, state_format="seahaven.state/1")


def test_the_name_defaults_to_the_added_worlds_own_name() -> None:
    host, leaf = make_world("host"), make_world("payments")
    host.add_world(leaf)
    (added,) = host.added_worlds
    assert added.name == "payments"


@pytest.mark.parametrize(
    "name", ["Payments", "1payments", "_payments", "", "pay/ments", "pay ments"]
)
def test_a_name_that_is_not_an_identifier_is_refused(name: str) -> None:
    host, leaf = make_world("host"), make_world("payments")
    with pytest.raises(WorldBug) as raised:
        host.add_world(leaf, name=name)
    assert repr(name) in str(raised.value)
    assert not host.added_worlds


def test_a_name_holding_a_double_underscore_is_refused() -> None:
    """`a__b` and `a/b` would name one attached schema, and one of them is a path."""
    host, leaf = make_world("host"), make_world("payments")
    with pytest.raises(WorldBug) as raised:
        host.add_world(leaf, name="pay__ments")
    assert "__" in str(raised.value)


@pytest.mark.parametrize("name", ["main", "temp"])
def test_sqlites_own_schema_names_are_refused(name: str) -> None:
    host, leaf = make_world("host"), make_world("payments")
    with pytest.raises(WorldBug) as raised:
        host.add_world(leaf, name=name)
    assert "SQLite" in str(raised.value)


def test_both_lists_at_once_is_refused() -> None:
    host, leaf = make_world("host"), make_world("payments")
    with pytest.raises(WorldBug) as raised:
        host.add_world(leaf, tool_allow_list=["a"], tool_block_list=["b"])
    assert "tool_allow_list" in str(raised.value)
    assert "tool_block_list" in str(raised.value)


def test_an_allow_list_given_as_a_bare_string_is_refused() -> None:
    """A `str` is a `Sequence[str]`, so nothing but this catches one tool per character."""
    host, leaf = make_world("host"), make_world("payments")
    with pytest.raises(WorldBug) as raised:
        host.add_world(leaf, tool_allow_list="charge")
    assert "tool_allow_list is a string" in str(raised.value)


def test_a_block_list_given_as_a_bare_string_is_refused() -> None:
    host, leaf = make_world("host"), make_world("payments")
    with pytest.raises(WorldBug) as raised:
        host.add_world(leaf, tool_block_list="charge")
    assert "tool_block_list is a string" in str(raised.value)


@pytest.mark.parametrize("store", ["", "   "])
def test_a_blank_store_is_refused(store: str) -> None:
    """`None` is the only way to say "the adder's scope"; a blank string opens its own."""
    host, leaf = make_world("host"), make_world("payments")
    with pytest.raises(WorldBug) as raised:
        host.add_world(leaf, store=store)
    assert "is not a scope name" in str(raised.value)


def test_two_added_worlds_may_not_share_a_name() -> None:
    host, leaf, other = make_world("host"), make_world("payments"), make_world("shop")
    host.add_world(leaf, name="one")
    with pytest.raises(WorldBug) as raised:
        host.add_world(other, name="one")
    assert "'one'" in str(raised.value)
    assert len(host.added_worlds) == 1


def test_a_startup_keyword_the_added_world_does_not_accept_is_refused() -> None:
    host, leaf = make_world("host"), make_world("payments")

    @leaf.instance_startup
    def hook(ctx: Ctx, *, region: str = "us") -> None:
        """Take one keyword and no other."""

    with pytest.raises(WorldBug) as raised:
        host.add_world(leaf, startup={"reigon": "eu"})
    assert "'reigon'" in str(raised.value)
    assert "region" in str(raised.value)


def test_a_startup_keyword_the_added_world_does_accept_is_bound() -> None:
    host, leaf = make_world("host"), make_world("payments")

    @leaf.instance_startup
    def hook(ctx: Ctx, *, region: str = "us") -> None:
        """Take one keyword and no other."""

    host.add_world(leaf, startup={"region": "eu"})
    assert host.composition().by_key[leaf, None].bound_startup == {"region": "eu"}


def test_a_hook_taking_var_kwargs_accepts_any_startup_keyword() -> None:
    host, leaf = make_world("host"), make_world("payments")

    @leaf.instance_startup
    def hook(ctx: Ctx, **kwargs: object) -> None:
        """Take anything at all."""

    host.add_world(leaf, startup={"anything": 1})
    assert host.added_worlds[0].startup == {"anything": 1}


def test_a_deeper_worlds_keywords_do_not_count() -> None:
    """`startup=` binds this node's hooks and nothing below it."""
    host, leaf, deeper = make_world("host"), make_world("payments"), make_world("tax")

    @deeper.instance_startup
    def hook(ctx: Ctx, *, rate: int = 0) -> None:
        """A keyword only the grandchild accepts."""

    leaf.add_world(deeper)
    with pytest.raises(WorldBug):
        host.add_world(leaf, startup={"rate": 1})


def test_a_world_cannot_add_itself() -> None:
    host = make_world("host")
    with pytest.raises(WorldBug) as raised:
        host.add_world(host)
    assert "inside itself" in str(raised.value)


def test_a_world_cannot_appear_in_its_own_subtree() -> None:
    host, middle, leaf = make_world("host"), make_world("shop"), make_world("payments")
    middle.add_world(leaf)
    leaf.add_world(host)
    with pytest.raises(WorldBug) as raised:
        host.add_world(middle)
    assert "'host'" in str(raised.value)


def test_the_startup_mapping_is_copied() -> None:
    """A caller that keeps the dict it passed cannot change the composition later."""
    host, leaf = make_world("host"), make_world("payments")

    @leaf.instance_startup
    def hook(ctx: Ctx, **kwargs: object) -> None:
        """Take anything at all."""

    passed = {"region": "eu"}
    host.add_world(leaf, startup=passed)
    passed["region"] = "us"
    assert host.added_worlds[0].startup == {"region": "eu"}
    assert isinstance(host.added_worlds[0].startup, MappingProxyType)


def test_added_worlds_are_recorded_in_registration_order() -> None:
    host = make_world("host")
    for name in ("c", "a", "b"):
        host.add_world(make_world(name))
    assert [added.name for added in host.added_worlds] == ["c", "a", "b"]


def test_the_lists_are_copied_from_whatever_sequence_was_given() -> None:
    host, leaf = make_world("host"), make_world("payments")
    given = ["one"]
    host.add_world(leaf, tool_allow_list=given)
    given.append("two")
    assert host.added_worlds[0].tool_allow_list == ("one",)


def test_the_same_world_under_two_names_and_one_store_is_one_node_with_two_views() -> None:
    host, leaf = make_world("host"), make_world("payments")
    host.add_world(leaf, name="one", tool_prefix="one_")
    host.add_world(leaf, name="two", tool_prefix="two_")
    composition = host.composition()
    assert [node.path for node in composition.nodes] == ["main", "one"]
    assert composition.by_key[leaf, None].aliases == ("two",)


def test_the_same_world_under_two_stores_is_two_nodes() -> None:
    host, leaf = make_world("host"), make_world("payments")
    host.add_world(leaf, name="one")
    host.add_world(leaf, name="two", store="eu")
    composition = host.composition()
    assert [node.path for node in composition.nodes] == ["main", "one", "two"]
    assert [node.scope for node in composition.nodes] == [None, None, "eu"]
