"""A composite fixture: one frozen file per node, and the sidecar that ties them to a tree.

The shapes are built inline, as `test_composition.py` argues for: what is
interesting here is a tree that changed between the freeze and the load -- a node
gone, a `store=` moved, an edge redirected -- and a committed package per shape
would be a directory of them. The committed tree is what the end-to-end
assertions use.
"""

import copy
import hashlib
import logging
import stat as stat_module
from pathlib import Path
from typing import Any

import emporium
import pytest
import yaml

from seahaven.errors import WorldBug
from seahaven.fixtures import SIDECAR_NAME, STATE_NAME, load, verify
from seahaven.world import World
from tests.conftest import INSTANT_ISO, composable_world

pytestmark = pytest.mark.usefixtures("isolated_imports")

# What `composable_world("child")` declares, to the character: a world of another
# name with this DDL has the same schema hash and is therefore the substitution
# no check at create refuses.
CHILDS_DDL = "CREATE TABLE child_rows (id TEXT PRIMARY KEY, value TEXT NOT NULL) STRICT;"


def rooted(name: str, tmp_path: Path, **options: Any) -> World:
    """A world that can freeze and load: its own fixtures and working directories."""
    return composable_world(
        name, fixtures_dir=tmp_path / "fixtures", work_dir=tmp_path / "work", **options
    )


def host_over(tmp_path: Path, child: World, **added: Any) -> World:
    """`main` -> `child`, on the shared fixtures directory of `tmp_path`."""
    host = rooted("host", tmp_path)
    host.add_world(child, name="child", **added)
    return host


def contents(directory: Path) -> list[str]:
    """What a directory holds, for a directory that may never have been made."""
    return sorted(path.name for path in directory.iterdir()) if directory.is_dir() else []


def sidecar_of(fixture: Any) -> dict[str, Any]:
    return yaml.safe_load((fixture.dir / SIDECAR_NAME).read_text())


def frozen_from(host: World, fixture_id: str = "start") -> Any:
    """Freeze a blank instance of `host` under `fixture_id`."""
    with host.instance(None, now=INSTANT_ISO) as live:
        return live.freeze(fixture_id, "for the tests that load it")


# ------------------------------------------------------------------- freezing


def test_freeze_writes_one_state_file_per_node_and_one_sidecar(tmp_path: Path) -> None:
    host = rooted("host", tmp_path)
    grandchild = composable_world("grand")
    child = composable_world("child")
    child.add_world(grandchild, name="grand")
    host.add_world(child, name="child")

    fixture = frozen_from(host)

    assert contents(fixture.dir) == [
        SIDECAR_NAME,
        "state.child.sqlite",
        "state.child__grand.sqlite",
        STATE_NAME,
    ]


def test_a_composite_sidecar_is_version_two_and_lists_the_added_nodes(tmp_path: Path) -> None:
    fixture = frozen_from(host_over(tmp_path, composable_world("child")))

    written = sidecar_of(fixture)

    assert written["format_version"] == 2
    assert [node["path"] for node in written["nodes"]] == ["child"]
    # The version-1 fields still describe the root, and only the root.
    assert written["world"] == "host"
    assert written["file_sha256"] != written["nodes"][0]["file_sha256"]


def test_a_world_that_adds_nothing_writes_version_one_with_no_nodes_key(tmp_path: Path) -> None:
    """A leaf world's fixture directory is what it was before composition existed."""
    fixture = frozen_from(rooted("solo", tmp_path))

    written = sidecar_of(fixture)

    assert written["format_version"] == 1
    assert "nodes" not in written
    assert contents(fixture.dir) == [SIDECAR_NAME, STATE_NAME]


def test_every_added_node_is_described_by_path_world_scope_file_and_aliases(
    tmp_path: Path,
) -> None:
    """The committed tree: two accounts of one world, and a route that is an alias."""
    host = copy.copy(emporium.world)
    host.work_dir = tmp_path / "work"
    host.fixtures_dir = tmp_path / "fixtures"

    with host.instance(None, now=INSTANT_ISO) as live:
        # One account charged and the other not: two stores of one world, so the
        # two files differ only because their contents do.
        live.call("pay_create_charge", amount=100)
        fixture = live.freeze("start", "one charge on the company account")

    described = {node["path"]: node for node in sidecar_of(fixture)["nodes"]}
    assert set(described) == {"payments", "payments_eu", "shop"}
    assert described["payments_eu"]["scope"] == "eu"
    assert described["payments_eu"]["world"] == "payments"
    assert described["payments_eu"]["world_version"] == "1.4.0"
    assert described["payments"]["scope"] is None
    assert described["payments"]["aliases"] == ["shop/payments"]
    assert described["shop"]["file"] == "state.shop.sqlite"
    # One world at two scopes is one schema and two stores.
    assert described["payments"]["schema_hash"] == described["payments_eu"]["schema_hash"]
    assert described["payments"]["file_sha256"] != described["payments_eu"]["file_sha256"]


def test_a_node_whose_schema_has_drifted_mints_nothing_and_names_it(tmp_path: Path) -> None:
    host = host_over(tmp_path, composable_world("child"))

    with host.instance(None) as live:
        with live.bulk() as ctx:
            ctx.worlds.child.db.execute("CREATE TABLE drift (id TEXT PRIMARY KEY) STRICT")
        with pytest.raises(WorldBug, match="child: this instance no longer holds"):
            live.freeze("start", "drifted")

    assert contents(host.fixtures_dir) == []


def test_the_frozen_file_of_every_node_is_read_only(tmp_path: Path) -> None:
    fixture = frozen_from(host_over(tmp_path, composable_world("child")))

    for name in (STATE_NAME, "state.child.sqlite"):
        assert stat_module.S_IMODE((fixture.dir / name).stat().st_mode) == 0o444


# --------------------------------------------------------------- round tripping


def test_every_nodes_rows_survive_a_freeze_and_a_load(tmp_path: Path) -> None:
    host = host_over(tmp_path, composable_world("child"))
    with host.instance(None, now=INSTANT_ISO) as live:
        live.call("host_write", value="in the root")
        live.call("child_write", value="in the child")
        live.freeze("start", "one row in each")

    with host.instance("start") as live:
        assert live.call("host_read") == ["in the root"]
        assert live.call("child_read") == ["in the child"]
        # What the fixture holds is starting state, not a change the agent made.
        assert live.change_log() == []


def test_a_fork_chains_its_parent_and_keeps_every_node(tmp_path: Path) -> None:
    host = host_over(tmp_path, composable_world("child"))
    with host.instance(None, now=INSTANT_ISO) as live:
        live.call("child_write", value="first")
        live.freeze("start", "one row")

    with host.instance("start") as live:
        live.call("host_write", value="second")
        live.call("child_write", value="third")
        forked = live.freeze("later", "two more rows")

    assert forked.parent_id == "start"
    assert load(forked.dir).meta.format_version == 2
    with host.instance("later") as live:
        assert live.call("host_read") == ["second"]
        assert sorted(live.call("child_read")) == ["first", "third"]


def test_an_added_worlds_own_fixtures_are_unreachable_from_a_host(tmp_path: Path) -> None:
    child = rooted("child", tmp_path / "child")
    with child.instance(None, now=INSTANT_ISO) as live:
        live.call("child_write", value="the child's own")
        live.freeze("childs_own", "frozen from the child alone")

    host = rooted("host", tmp_path / "host")
    host.add_world(child, name="child")

    assert [fixture.id for fixture in host.fixtures()] == []
    with pytest.raises(WorldBug, match="has no fixture 'childs_own'"):
        host.instance("childs_own")


# ----------------------------------------------------- what create refuses


def test_an_added_nodes_modified_file_is_refused_by_its_path(tmp_path: Path) -> None:
    host = host_over(tmp_path, composable_world("child"))
    fixture = frozen_from(host)
    damaged = fixture.dir / "state.child.sqlite"
    damaged.chmod(0o644)
    damaged.write_bytes(damaged.read_bytes() + b"not a page")

    with pytest.raises(WorldBug, match=r"fixture 'start' node 'child' at .* file_sha256"):
        host.instance("start")


@pytest.mark.parametrize(
    "spelling",
    [
        "../elsewhere.sqlite",
        "/tmp/elsewhere.sqlite",
        "sub/state.sqlite",
        "..",
        "C:state.sqlite",
        # The rest of `names.why_not_a_name`, which this validator now shares with
        # a world's name and a fixture id: charset, the edges Windows strips, and
        # a device name.
        "state.café.sqlite",
        "state.child.sqlite ",
        "state.child.sqlite.",
        "con.sqlite",
    ],
)
def test_a_sidecar_whose_file_is_not_a_name_in_its_directory_is_refused(
    tmp_path: Path, spelling: str
) -> None:
    """`file` is joined onto the fixture directory, so it has to be a name in it.

    Refused when the sidecar is *read*, which is what puts it in front of both
    readers: `verify` hashes whatever `file` points at, and creation copies it
    into the new instance. A Windows spelling is refused here too, and so is a
    name the shared rule refuses on its charset, its edges or its device names,
    because a fixture is an artifact that moves between machines.
    """
    host = host_over(tmp_path, composable_world("child"))
    fixture = frozen_from(host)
    sidecar = fixture.dir / SIDECAR_NAME
    data = yaml.safe_load(sidecar.read_text())
    data["nodes"][0]["file"] = spelling
    sidecar.write_text(yaml.safe_dump(data, sort_keys=True))

    with pytest.raises(WorldBug, match="not a file name inside the fixture directory"):
        load(fixture.dir)
    with pytest.raises(WorldBug, match="not a file name inside the fixture directory"):
        host.instance("start")


def test_a_file_longer_than_a_fixture_id_may_be_is_still_a_file_name(tmp_path: Path) -> None:
    """`file` is the one part of the name rule that carries no length limit.

    It is minted from the node's path rather than chosen, so a limit here would be
    a limit on how deep a composition may nest -- and one applied where the
    sidecar is read and not where it is written, which is `load` refusing a
    fixture `freeze` had just produced.
    """
    host = host_over(tmp_path, composable_world("child"))
    fixture = frozen_from(host)
    deep = f"state.{'deep__' * 30}child.sqlite"
    (fixture.dir / "state.child.sqlite").rename(fixture.dir / deep)
    sidecar = fixture.dir / SIDECAR_NAME
    data = yaml.safe_load(sidecar.read_text())
    data["nodes"][0]["file"] = deep
    sidecar.write_text(yaml.safe_dump(data, sort_keys=True))

    assert len(deep) > 128
    loaded = load(fixture.dir)
    assert loaded.nodes[0].file == deep
    verify(loaded)


def test_an_added_nodes_file_planted_as_a_symlink_is_refused_by_its_path(tmp_path: Path) -> None:
    """The other half of `file`'s rule: a name inside the directory that points out of it.

    The sidecar carries the *target's* digest, so nothing the fixture says about
    itself disagrees -- and without the refusal `verify` passes, the copy follows
    the link, and the instance comes up on a store the fixture does not contain.
    """
    host = host_over(tmp_path, composable_world("child"))
    fixture = frozen_from(host)
    with host.instance(None, now=INSTANT_ISO) as live:
        live.call("child_write", value="outside the fixture")
        outside = live.freeze("outside", "a store of its own")

    planted = fixture.dir / "state.child.sqlite"
    planted.unlink()
    planted.symlink_to(outside.dir / "state.child.sqlite")
    sidecar = fixture.dir / SIDECAR_NAME
    data = yaml.safe_load(sidecar.read_text())
    data["nodes"][0]["file_sha256"] = hashlib.sha256(planted.read_bytes()).hexdigest()
    sidecar.write_text(yaml.safe_dump(data, sort_keys=True))

    with pytest.raises(WorldBug, match=r"fixture 'start' node 'child' at .* is a symbolic link"):
        verify(load(fixture.dir))
    with pytest.raises(WorldBug, match=r"node 'child' at .* is a symbolic link"):
        host.instance("start")


def test_a_node_added_since_the_freeze_is_refused_by_name(tmp_path: Path) -> None:
    frozen_from(host_over(tmp_path, composable_world("child")))
    grown = host_over(tmp_path, composable_world("child"))
    grown.add_world(composable_world("extra"), name="extra")

    with pytest.raises(WorldBug, match="node added: extra"):
        grown.instance("start")


def test_a_node_removed_since_the_freeze_is_refused_by_name(tmp_path: Path) -> None:
    host = host_over(tmp_path, composable_world("child"))
    host.add_world(composable_world("extra"), name="extra")
    frozen_from(host)

    with pytest.raises(WorldBug, match="node removed: extra"):
        host_over(tmp_path, composable_world("child")).instance("start")


def test_a_store_that_moved_is_refused_as_sharing_changed(tmp_path: Path) -> None:
    """A `store=` is the node's identity, and moving one is not a schema change."""
    frozen_from(host_over(tmp_path, composable_world("child")))
    moved = host_over(tmp_path, composable_world("child"), store="apart")

    with pytest.raises(WorldBug, match="sharing changed: child was frozen in store None"):
        moved.instance("start")


def test_a_redirected_edge_is_refused_even_though_no_node_moved(tmp_path: Path) -> None:
    """Two names for one node: renaming the second changes an alias and nothing else."""

    def host_with(alias: str) -> World:
        host = rooted("host", tmp_path)
        child = composable_world("child")
        host.add_world(child, name="child")
        # The same node reached a second way. Its tools come up the first edge
        # already, so this one contributes none and changes nothing but the graph.
        host.add_world(child, name=alias, tool_allow_list=[])
        return host

    frozen_from(host_with("mirror"))

    with pytest.raises(WorldBug, match="the alias edges changed"):
        host_with("reflection").instance("start")


def test_a_node_frozen_from_a_different_schema_is_refused_by_path(tmp_path: Path) -> None:
    frozen_from(host_over(tmp_path, composable_world("child")))
    changed = host_over(
        tmp_path,
        composable_world("child", extra_schema="CREATE TABLE extra (id TEXT PRIMARY KEY) STRICT;"),
    )

    with pytest.raises(WorldBug, match="child: fixture 'start' was frozen from a different schema"):
        changed.instance("start")


def test_a_version_one_sidecar_against_a_composite_is_refused(tmp_path: Path) -> None:
    frozen_from(rooted("host", tmp_path))

    with pytest.raises(
        WorldBug, match=r"format_version 1 sidecar describing one store.*resolves to 2 stores"
    ):
        host_over(tmp_path, composable_world("child")).instance("start")


def test_a_version_two_sidecar_against_a_leaf_is_refused(tmp_path: Path) -> None:
    frozen_from(host_over(tmp_path, composable_world("child")))

    with pytest.raises(
        WorldBug, match=r"format_version 2 sidecar describing 2 stores.*resolves to one store"
    ):
        rooted("host", tmp_path).instance("start")


def test_nothing_is_copied_when_the_composition_does_not_match(tmp_path: Path) -> None:
    """A creation that cannot succeed leaves no working directory behind."""
    frozen_from(host_over(tmp_path, composable_world("child")))
    grown = host_over(tmp_path, composable_world("child"))
    grown.add_world(composable_world("extra"), name="extra")

    with pytest.raises(WorldBug):
        grown.instance("start")

    assert contents(tmp_path / "work") == []


# --------------------------------------------------- what create reports instead


def test_a_version_difference_with_a_matching_schema_hash_still_loads(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """One package cannot be installed at two versions, so the schema hash is the signal."""
    frozen_from(host_over(tmp_path, composable_world("child", version="1.0.0")))
    upgraded = host_over(tmp_path, composable_world("child", version="2.5.0"))

    with (
        caplog.at_level(logging.INFO, logger="seahaven.instances"),
        upgraded.instance("start") as live,
    ):
        reported = {node.path: node for node in live.composition()}

    assert (
        "child: frozen from world 'child' at version 1.0.0, running world 'child' at version 2.5.0"
        in caplog.text
    )
    # Logged *and* on the report, which is where an eval reads it.
    assert reported["child"].world_version == "2.5.0"
    assert reported["child"].frozen_world_version == "1.0.0"
    assert reported["main"].frozen_world_version is None


def test_a_store_frozen_from_a_differently_named_world_is_reported_not_refused(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A sidecar's `world` is recorded and not checked, so a substitution is said out loud."""
    host = rooted("host", tmp_path)
    host.add_world(composable_world("child"), name="child")
    with host.instance(None, now=INSTANT_ISO) as live:
        live.freeze("start", "frozen from 'child'")

    # The same DDL under another world's name: the schema hash is unchanged, so
    # nothing is refused, and the difference has to be visible somewhere.
    substitute = World("renamed", "1.0.0", CHILDS_DDL, state_format="seahaven.state/1")
    renamed = rooted("host", tmp_path)
    renamed.add_world(substitute, name="child")

    with caplog.at_level(logging.INFO, logger="seahaven.instances"), renamed.instance("start"):
        pass

    assert (
        "child: frozen from world 'child' at version 1.0.0, running world 'renamed'" in caplog.text
    )


def test_a_version_that_did_not_move_is_not_reported(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    host = host_over(tmp_path, composable_world("child"))
    frozen_from(host)

    with caplog.at_level(logging.INFO, logger="seahaven.instances"), host.instance("start"):
        pass

    assert "frozen from world" not in caplog.text


# ------------------------------------------------------------------ the report


def test_the_composition_report_names_every_node_root_first(tmp_path: Path) -> None:
    host = copy.copy(emporium.world)
    host.work_dir = tmp_path / "work"

    with host.instance(None) as live:
        reported = live.composition()

    assert [node.path for node in reported] == ["main", "payments", "payments_eu", "shop"]
    assert [node.world for node in reported] == ["emporium", "payments", "payments", "shop"]
    assert [node.scope for node in reported] == [None, None, "eu", None]
    assert [node.aliases for node in reported] == [(), ("shop/payments",), (), ()]
    assert reported[0].world_version == "0.1.0"
    assert reported[0].schema_hash == emporium.world.schema_hash


def test_the_report_of_a_world_that_adds_nothing_is_the_one_node_it_is(tmp_path: Path) -> None:
    with rooted("solo", tmp_path).instance(None) as live:
        assert [node.path for node in live.composition()] == ["main"]
