"""The fixture rules, over fixtures that were really frozen and then damaged.

A hand-written sidecar beside a hand-written file would test the reading and not
the rule: what these rules are for is the drift between a fixture and what its
sidecar says about it, and the only way to have that honestly is to freeze one
and then change something.

The composite half is the same, one node further in: a host over one child is
frozen, and then either a node's own file is damaged -- which every rule that runs
per node must find and name -- or the *tree* is changed underneath the fixture,
which is SH406. The trees are built inline for the reason
`test_composite_fixtures.py` gives: what is interesting is a tree that changed
between the freeze and the check, and a committed package per shape would be a
directory of them.

SH405 is the journal companions only, and `test_a_writable_state_file_is_not_a_finding`
is why: see the module docstring of `seahaven/lint/fixtures.py`.
"""

import hashlib
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from seahaven.errors import WorldBug
from seahaven.fixtures import SIDECAR_NAME, STATE_NAME
from seahaven.lint import Finding
from seahaven.lint import fixtures as fixtures_lint
from seahaven.world import World
from tests.conftest import build_world, composable_world, stub_target

WRITABLE = 0o644
CANONICAL = "2026-06-01T09:00:00.000Z"

# What `freeze` names an added node's state file: the node's path, with `/`
# replaced by `__`, between the root's own two halves.
CHILD_STATE = "state.child.sqlite"


@pytest.fixture
def frozen(tmp_path: Path) -> World:
    """A world with one fixture in it, frozen the way `seahaven fixture` does."""
    world = build_world(tmp_path)
    with world.instance(None, now=CANONICAL) as instance:
        instance.freeze("empty", "The schema with no rows.")
    return world


def rooted(name: str, tmp_path: Path) -> World:
    """A world that can freeze and load, on `tmp_path`'s shared directories."""
    return composable_world(name, fixtures_dir=tmp_path / "fixtures", work_dir=tmp_path / "work")


def host_over(tmp_path: Path, **added: Any) -> World:
    """`main` -> `child`, sharing the fixtures directory every other world here uses."""
    host = rooted("host", tmp_path)
    host.add_world(composable_world("child"), name="child", **added)
    return host


@pytest.fixture
def composite(tmp_path: Path) -> World:
    """A host over one child, with one fixture frozen from the pair."""
    host = host_over(tmp_path)
    with host.instance(None, now=CANONICAL) as instance:
        instance.freeze("empty", "Two stores with no rows.")
    return host


def run(world: World) -> list[Finding]:
    return fixtures_lint.run(stub_target(world, world.fixtures_dir))


def damage(world: World, edit: Callable[[dict[str, Any]], object]) -> None:
    """Rewrite a fixture's sidecar, the way a hand edit would.

    `dict[str, Any]` because a version-2 sidecar's `nodes` is a list of
    mappings, and an edit that reaches into one is the only way to damage a
    node rather than the fixture.
    """
    sidecar = world.fixtures_dir / "empty" / SIDECAR_NAME
    data = yaml.safe_load(sidecar.read_text(encoding="utf-8"))
    edit(data)
    sidecar.write_text(yaml.safe_dump(data, sort_keys=True), encoding="utf-8")


def link_out(state: Path, target: Path) -> str:
    """Replace a fixture's state file with a link to `target`; answer the target's digest.

    The digest is what the sidecar has to carry for this to be the case the
    runtime refuses and a hash check cannot see: `_sha256` opens through the
    link, so the two agree.
    """
    state.unlink()
    state.symlink_to(target)
    return hashlib.sha256(target.read_bytes()).hexdigest()


def test_a_freshly_frozen_fixture_is_clean(frozen: World) -> None:
    assert run(frozen) == []


def test_a_world_with_no_fixtures_directory_is_clean(tmp_path: Path) -> None:
    """A world that only ever makes blank instances never mints one."""
    assert run(build_world(tmp_path)) == []


def test_a_sidecar_that_is_not_yaml_is_sh401(frozen: World) -> None:
    (frozen.fixtures_dir / "empty" / SIDECAR_NAME).write_text("{[", encoding="utf-8")
    (finding,) = run(frozen)
    assert finding.code == "SH401"
    assert "not valid YAML" in finding.message


def test_a_sidecar_that_is_not_a_mapping_is_sh401(frozen: World) -> None:
    (frozen.fixtures_dir / "empty" / SIDECAR_NAME).write_text("- one\n- two\n", encoding="utf-8")
    (finding,) = run(frozen)
    assert finding.code == "SH401"
    assert "not a mapping" in finding.message


def test_a_missing_sidecar_is_sh401(frozen: World) -> None:
    (frozen.fixtures_dir / "empty" / SIDECAR_NAME).unlink()
    (finding,) = run(frozen)
    assert finding.code == "SH401"


def test_a_sidecar_missing_a_field_lists_pydantics_error(frozen: World) -> None:
    damage(frozen, lambda data: data.pop("world_version"))
    (finding,) = run(frozen)
    assert finding.code == "SH401"
    assert "world_version" in finding.message


def test_a_format_version_this_seahaven_does_not_write_is_sh401(frozen: World) -> None:
    """The field that says this fixture was written by another Seahaven."""
    damage(frozen, lambda data: data.__setitem__("format_version", 3))
    (finding,) = run(frozen)
    assert finding.code == "SH401"
    assert "format_version" in finding.message


def test_a_broken_sidecar_is_reported_once_and_left(frozen: World) -> None:
    """Every other rule reads a field the sidecar does not have."""
    damage(frozen, lambda data: data.clear())
    assert {finding.code for finding in run(frozen)} == {"SH401"}


def test_a_modified_state_file_is_sh402(frozen: World) -> None:
    state = frozen.fixtures_dir / "empty" / STATE_NAME
    state.chmod(WRITABLE)
    state.write_bytes(state.read_bytes() + b"tampered")
    assert [finding.code for finding in run(frozen)] == ["SH402"]


def test_a_missing_state_file_is_sh402(frozen: World) -> None:
    (frozen.fixtures_dir / "empty" / STATE_NAME).unlink()
    (finding,) = run(frozen)
    assert finding.code == "SH402"
    assert f"no {STATE_NAME}" in finding.message


def test_a_state_file_that_is_a_symlink_is_sh402(frozen: World) -> None:
    """The check `world.instance` makes, made before a commit rather than in a run.

    The sidecar carries the digest of the file the link reaches, so the hash
    agrees with itself: without the link being judged first, `check` reports a
    fixture green that the framework then refuses outright.
    """
    with frozen.instance(None, now=CANONICAL) as instance:
        instance.call("execute", sql="INSERT INTO notes VALUES ('n1', 'outside', 0)")
        outside = instance.freeze("outside", "A database of its own.")
    state = frozen.fixtures_dir / "empty" / STATE_NAME
    digest = link_out(state, outside.state_path)
    damage(frozen, lambda data: data.__setitem__("file_sha256", digest))

    (finding,) = run(frozen)

    assert finding.code == "SH402"
    assert f"{STATE_NAME} that is a symbolic link" in finding.message
    assert finding.path == state


def test_a_schema_hash_from_another_world_is_sh403(frozen: World) -> None:
    damage(frozen, lambda data: data.__setitem__("schema_hash", "0" * 64))
    (finding,) = run(frozen)
    assert finding.code == "SH403"
    assert "seahaven fixture" in finding.fix


@pytest.mark.parametrize(
    "now",
    [
        pytest.param("2026-06-01T09:00:00Z", id="no milliseconds"),
        pytest.param("2026-06-01T09:00:00.000000Z", id="microseconds"),
        pytest.param("2026-06-01T09:00:00+00:00", id="an offset instead of Z"),
        pytest.param("2026-06-01 09:00:00.000Z", id="a space instead of T"),
        pytest.param("not a timestamp", id="not a timestamp at all"),
    ],
)
def test_a_now_that_is_not_canonical_is_sh404(frozen: World, now: str) -> None:
    """One format across every door: the sidecar's `now` is one of those doors."""
    damage(frozen, lambda data: data.__setitem__("now", now))
    assert [finding.code for finding in run(frozen)] == ["SH404"]


def test_a_writable_state_file_is_not_a_finding(frozen: World) -> None:
    """`freeze` seals the file at 0444, and git hands it back at 0644.

    Every committed fixture of every world would fail a mode rule after a clone,
    including the reference world's own. What the seal guards against -- the file
    changing -- is SH402, over a hash version control does preserve.
    """
    (frozen.fixtures_dir / "empty" / STATE_NAME).chmod(WRITABLE)
    assert run(frozen) == []


@pytest.mark.parametrize("suffix", ["-wal", "-shm"])
def test_a_journal_companion_beside_the_state_file_is_sh405(frozen: World, suffix: str) -> None:
    """Either file means the fixture was opened for writing after it was frozen."""
    state = frozen.fixtures_dir / "empty" / STATE_NAME
    state.with_name(state.name + suffix).write_bytes(b"")
    (finding,) = run(frozen)
    assert finding.code == "SH405"
    assert suffix in finding.message


def test_a_pending_directory_is_not_a_fixture(frozen: World) -> None:
    """`.pending-<id>` is a freeze in flight, and nothing in it is published yet."""
    (frozen.fixtures_dir / ".pending-half").mkdir()
    assert run(frozen) == []


def test_a_loose_file_in_the_fixtures_directory_is_not_a_fixture(frozen: World) -> None:
    (frozen.fixtures_dir / "notes.md").write_text("about these fixtures\n", encoding="utf-8")
    assert run(frozen) == []


def test_every_fixture_is_checked(frozen: World) -> None:
    with frozen.instance("empty") as instance:
        instance.freeze("forked", "A fork of empty.")
    for fixture_id in ("empty", "forked"):
        state = frozen.fixtures_dir / fixture_id / STATE_NAME
        state.chmod(WRITABLE)
        state.write_bytes(b"not a database")
    assert [finding.code for finding in run(frozen)] == ["SH402", "SH402"]
    assert {os.path.basename(finding.path) for finding in run(frozen)} == {STATE_NAME}


# --------------------------------------------------- a composite, node by node


def node_state(world: World) -> Path:
    return world.fixtures_dir / "empty" / CHILD_STATE


def test_a_freshly_frozen_composite_fixture_is_clean(composite: World) -> None:
    assert run(composite) == []


def test_a_modified_node_file_is_sh402_naming_the_node(composite: World) -> None:
    """The gap this closes: before it, only the root's file was ever hashed."""
    state = node_state(composite)
    state.chmod(WRITABLE)
    state.write_bytes(state.read_bytes() + b"tampered")
    (finding,) = run(composite)
    assert finding.code == "SH402"
    assert "node 'child'" in finding.message
    assert finding.path.name == CHILD_STATE


def test_a_node_file_that_is_a_symlink_is_sh402_naming_the_node(composite: World) -> None:
    with composite.instance(None, now=CANONICAL) as instance:
        instance.call("child_write", value="outside")
        outside = instance.freeze("outside", "A store of its own.")
    digest = link_out(node_state(composite), outside.dir / CHILD_STATE)
    damage(composite, lambda data: data["nodes"][0].__setitem__("file_sha256", digest))

    (finding,) = run(composite)

    assert finding.code == "SH402"
    assert f"node 'child' has a {CHILD_STATE} that is a symbolic link" in finding.message


def test_a_missing_node_file_is_sh402_naming_the_node(composite: World) -> None:
    node_state(composite).unlink()
    (finding,) = run(composite)
    assert finding.code == "SH402"
    assert f"node 'child' has no {CHILD_STATE}" in finding.message


@pytest.mark.parametrize("suffix", ["-wal", "-shm"])
def test_a_journal_companion_beside_a_node_file_is_sh405(composite: World, suffix: str) -> None:
    state = node_state(composite)
    state.with_name(state.name + suffix).write_bytes(b"")
    (finding,) = run(composite)
    assert finding.code == "SH405"
    assert "node 'child'" in finding.message and suffix in finding.message


def test_a_node_schema_hash_from_another_world_is_sh403_naming_the_node(
    composite: World,
) -> None:
    damage(composite, lambda data: data["nodes"][0].__setitem__("schema_hash", "0" * 64))
    (finding,) = run(composite)
    assert finding.code == "SH403"
    assert "node 'child'" in finding.message
    assert "world 'child' declares" in finding.message


def test_the_roots_schema_hash_is_still_the_roots(composite: World) -> None:
    """One code, two nodes, and each names the world it disagrees with."""
    damage(composite, lambda data: data.__setitem__("schema_hash", "0" * 64))
    (finding,) = run(composite)
    assert finding.code == "SH403"
    assert "node" not in finding.message
    assert "world 'host' declares" in finding.message


def test_two_nodes_listed_at_one_path_is_sh401(composite: World) -> None:
    """Pydantic validates each entry and cannot see that the list repeats itself."""
    damage(composite, lambda data: data["nodes"].append(dict(data["nodes"][0])))
    assert [finding.message for finding in run(composite)] == [
        "more than one node is listed at path 'child'",
        f"more than one node's state is the file {CHILD_STATE!r}",
    ]


def test_two_nodes_sharing_a_state_file_is_sh401(composite: World) -> None:
    """A node whose `file` is the root's is a fixture that loads the wrong store."""
    damage(composite, lambda data: data["nodes"][0].__setitem__("file", STATE_NAME))
    assert [finding.message for finding in run(composite)] == [
        f"more than one node's state is the file {STATE_NAME!r}"
    ]


def test_a_node_list_that_repeats_itself_is_reported_once_and_left(composite: World) -> None:
    """The contract SH401 has always had, now that SH401 has a second half.

    A repeated `file` makes every rule below wrong rather than silent: the node's
    hash is checked against the root's bytes, which is SH402 saying a file was
    modified when nothing touched it and the sidecar is pointing at the wrong
    one. So the fixture is reported once and left, exactly as one whose sidecar
    does not validate is.
    """
    damage(composite, lambda data: data["nodes"][0].__setitem__("file", STATE_NAME))
    assert {finding.code for finding in run(composite)} == {"SH401"}


def test_a_repeat_stops_the_rules_that_would_otherwise_have_fired(composite: World) -> None:
    """Not vacuous: the same sidecar has a bad `now` and a bad schema hash under it."""

    def wreck(data: dict[str, Any]) -> None:
        data["nodes"][0]["file"] = STATE_NAME
        data["now"] = "not a timestamp"
        data["schema_hash"] = "0" * 64

    damage(composite, wreck)
    assert {finding.code for finding in run(composite)} == {"SH401"}


# ------------------------------------------------------------ SH406: the shape


def test_a_node_the_world_no_longer_has_is_sh406(composite: World, tmp_path: Path) -> None:
    """Frozen from a tree with a child; checked against a world that adds nothing."""
    (finding,) = [f for f in run(rooted("host", tmp_path)) if f.code == "SH406"]
    assert finding.severity == "error"
    assert "one store" in finding.message
    assert finding.path.name == SIDECAR_NAME


def test_a_version_1_sidecar_against_a_composite_world_is_sh406(tmp_path: Path) -> None:
    """The other direction: frozen when the world was a leaf, checked now that it is not."""
    leaf = rooted("host", tmp_path)
    with leaf.instance(None, now=CANONICAL) as instance:
        instance.freeze("empty", "One store with no rows.")
    (finding,) = [f for f in run(host_over(tmp_path)) if f.code == "SH406"]
    assert "format_version 1 sidecar describing one store" in finding.message
    assert "now resolves to 2 stores" in finding.message


def test_a_node_added_since_the_freeze_is_sh406(composite: World, tmp_path: Path) -> None:
    later = host_over(tmp_path)
    later.add_world(composable_world("extra"), name="extra")
    (finding,) = [f for f in run(later) if f.code == "SH406"]
    assert "node added: extra" in finding.message


def test_a_store_that_moved_is_sh406(composite: World, tmp_path: Path) -> None:
    """A `store=` on the edge puts the same world in another scope, which is another node."""
    (finding,) = [f for f in run(host_over(tmp_path, store="eu")) if f.code == "SH406"]
    assert "sharing changed: child" in finding.message


def test_an_alias_edge_that_appeared_is_sh406(composite: World, tmp_path: Path) -> None:
    """A second route to one node is not a node, and it is still a different composition."""
    later = host_over(tmp_path)
    # An empty allow list, so the second route contributes nothing and the tree
    # still seals: what changed is the edge set, which is all SH406 is about.
    later.add_world(later.added_worlds[0].world, name="books", tool_allow_list=[])
    (finding,) = [f for f in run(later) if f.code == "SH406"]
    assert "alias edges changed" in finding.message


def test_sh406_says_what_the_refusal_at_create_would_say(composite: World, tmp_path: Path) -> None:
    """One sentence, whether an author meets it before a commit or inside a run."""
    later = rooted("host", tmp_path)
    (finding,) = [f for f in run(later) if f.code == "SH406"]
    with pytest.raises(WorldBug) as refused:
        later.instance("empty")
    assert finding.message in str(refused.value)


def test_a_world_that_does_not_seal_reports_neither_rule_that_reads_the_tree(
    composite: World, tmp_path: Path
) -> None:
    """SH504 has already said the tree is wrong; a fixture against no tree says nothing.

    Both fixture rules that read a composition are shown quiet at once, and the
    sidecar is damaged in the two ways that would otherwise wake each: the node's
    schema hash is wrong, which is SH403 with a tree
    (`test_a_node_schema_hash_from_another_world_is_sh403_naming_the_node`), and
    the tree has gained a node, which is SH406 with one
    (`test_a_node_added_since_the_freeze_is_sh406`). The rules that read no tree
    have nothing to say about this fixture, so the whole list is empty.
    """
    damage(composite, lambda data: data["nodes"][0].__setitem__("schema_hash", "0" * 64))
    unsealable = host_over(tmp_path)
    unsealable.add_world(composable_world("extra"), name="extra", tool_allow_list=["nope"])
    assert stub_target(unsealable, unsealable.fixtures_dir).composition is None
    assert run(unsealable) == []


def test_a_leaf_worlds_fixture_is_checked_with_no_tree_at_all(frozen: World) -> None:
    """Every rule above a leaf world's fixture is what it was before composition."""
    damage(frozen, lambda data: data.__setitem__("schema_hash", "0" * 64))
    assert [finding.code for finding in run(frozen)] == ["SH403"]
