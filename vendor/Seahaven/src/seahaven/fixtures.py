"""Fixtures: the starting state an instance is a copy of.

A fixture is a directory, not an object: `state.sqlite`, which is a compacted,
journal-free, read-only database file, and `fixture.yaml`, which says where it
came from and what its hash is. Nothing opens `state.sqlite` in place -- it is
verified and copied, and the copy is what runs -- so a fixture cannot be damaged
by using it and two instances of one fixture cannot see each other.

`freeze` is the only way to mint one. It is a whole-directory publish: the file
is vacuumed into a `.pending-` sibling, hashed, described and sealed, and only
then renamed into place, so a fixture directory either does not exist or is
complete.

A composite world has one store per node, so its fixture has one *file* per node
and a sidecar that describes all of them: `format_version: 2`, the version-1
fields still describing the root and a `nodes` list for everything the root adds.
A world that adds nothing has one node and keeps writing `format_version: 1`,
byte for byte what it wrote before composition existed, and `load` reads both.
"""

import hashlib
import os
import shutil
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Self

import apsw
import pydantic
import yaml

from seahaven import conformance
from seahaven.clock import Clock
from seahaven.errors import WorldBug
from seahaven.names import CHARACTER_RULE, NAME_RULE, why_not_a_name

if TYPE_CHECKING:
    # `instances.py` and `composition.py` both import this module, so the arrow
    # runs that way round and an annotation is all that is needed here.
    from seahaven.composition import Composition, Node
    from seahaven.instances import Instance, NodeRuntime

__all__ = [
    "PENDING_PREFIX",
    "SIDECAR_NAME",
    "STATE_NAME",
    "Fixture",
    "FixtureMeta",
    "NodeMeta",
    "check_composition",
    "check_id",
    "composition_mismatch",
    "freeze",
    "load",
    "load_all",
    "verify",
]

STATE_NAME = "state.sqlite"
SIDECAR_NAME = "fixture.yaml"
# A fixture under construction. The dot is what keeps it out of `load_all`, so a
# half-written fixture is invisible to everything that lists them.
PENDING_PREFIX = ".pending-"

# The mode a frozen state file is left in: readable by anyone, writable by
# nobody. The copy an instance runs on is a fresh file and does not inherit it.
STATE_MODE = 0o444

# Verified fixtures, as (path, mtime, size, expected hash). Hashing a large
# fixture on every instance creation would be the cost of creating one, and a
# file that has not changed cannot have a different hash.
_verified: set[tuple[str, int, int, str]] = set()


# The sidecar shapes this Seahaven writes and reads. A fixture outlives the
# process that wrote it, so anything else is refused by name rather than by a
# validation error about a field.
FORMAT_VERSIONS = (1, 2)


class NodeMeta(pydantic.BaseModel, frozen=True, extra="forbid"):
    """One added node of a composite fixture: which store this file is, and whose.

    `path` is the node's canonical path and `file` is the name of its state file
    inside the fixture directory. `scope` and `aliases` are what make the fixture
    describe the *shape* of the composition rather than only its contents: a
    `store=` that moved, or an edge that was redirected, changes one of them even
    when every path still exists, and `check_composition` refuses on either.
    """

    path: str
    world: str
    world_version: str
    schema_hash: str
    scope: str | None
    file: str
    file_sha256: str
    # The `<parent path>/<name>` routes that reach this node and are not its path.
    aliases: tuple[str, ...] = ()

    @pydantic.field_validator("file")
    @classmethod
    def _a_name_inside_the_fixture_directory(cls, value: str) -> str:
        """A sidecar's `file` is a name, and is checked as one.

        `Fixture.file_of` joins this onto the fixture directory, and `verify` and
        instance creation both follow the result. `Path` does not normalise `..`
        and lets an absolute component win outright, so an unchecked spelling
        here would have a fixture read -- and copy into a new instance -- a file
        it does not contain. The sidecar supplies the hash as well, so
        `file_sha256` is no defence: both halves come from the same text.

        What this constrains is the name, and only the name. The other half of
        the same threat -- a name inside the directory that is a symlink out of
        it -- is `_verify_file`'s, which refuses one before it hashes it, for the
        root's `state.sqlite` as much as for this.

        This is `names.why_not_a_name`, the one rule for a name that becomes a
        path on an artifact that travels -- `check_id`'s rule for a fixture id and
        `world._check_name`'s for a world's name -- with one difference, and only
        one: no length limit. This name is not chosen by a person. `freeze` mints
        it from the node's path (`composition._file_name`), so a limit here would
        be a limit on how deep a composition may nest, applied when a fixture is
        *read* and not when it is written -- which is `load` refusing a fixture
        Seahaven itself wrote. It is also the only clause that could ever bite a
        generated name: `state.<path with '/' as '__'>.sqlite` over node names of
        `[a-z][a-z0-9_]*` can be long, but it cannot reach the charset, the edges
        or a device name.
        """
        reason = why_not_a_name(value, max_length=None)
        if reason is not None:
            raise ValueError(
                f"file is {value!r}, which is not a file name inside the fixture directory: "
                f"{reason}. A file name is {CHARACTER_RULE}."
            )
        return value


class FixtureMeta(pydantic.BaseModel, frozen=True, extra="forbid"):
    """`fixture.yaml`: where this state came from and what it is.

    `format_version` is on every YAML file Seahaven writes, and `load` refuses
    any value but the ones it knows: a fixture is an artifact that outlives the
    process that wrote it.

    The version-1 fields describe the **root** node and keep their meaning
    exactly; `nodes` lists the added nodes, and only them. A world that adds
    nothing writes version 1 with no `nodes` key at all.
    """

    format_version: Literal[1, 2]
    id: str
    world: str
    world_version: str
    schema_hash: str
    now: str
    parent_id: str | None
    file_sha256: str
    created_at: str
    description: str
    nodes: tuple[NodeMeta, ...] = ()

    @pydantic.model_validator(mode="after")
    def _the_version_is_the_shape(self) -> Self:
        """The two ways of saying "this is a composite" have to agree.

        Neither field is derived from the other on disk, so a hand-edited sidecar
        can claim one and carry the other, and every reader below assumes they
        match.
        """
        if (self.format_version == 2) != bool(self.nodes):
            raise ValueError(
                "format_version 2 describes a composite fixture and lists its added nodes in "
                "nodes; format_version 1 describes one store and lists none"
            )
        return self


@dataclass(frozen=True)
class Fixture:
    """One fixture on disk: its sidecar and the directory it lives in."""

    meta: FixtureMeta
    dir: Path

    @property
    def id(self) -> str:
        return self.meta.id

    @property
    def now(self) -> str:
        """The clock every instance of this fixture starts at."""
        return self.meta.now

    @property
    def description(self) -> str:
        return self.meta.description

    @property
    def parent_id(self) -> str | None:
        """The fixture this one was forked from, or `None` for one frozen from blank."""
        return self.meta.parent_id

    @property
    def state_path(self) -> Path:
        """The root node's state file."""
        return self.dir / STATE_NAME

    @property
    def nodes(self) -> tuple[NodeMeta, ...]:
        """The added nodes this fixture carries. Empty for a world that adds nothing."""
        return self.meta.nodes

    def file_of(self, node: NodeMeta) -> Path:
        """Where one added node's state file is, inside this fixture's directory."""
        return self.dir / node.file


def check_id(fixture_id: str) -> None:
    """A fixture id is a directory name, so it has to be one.

    Ids arrive over the wire (`reset(fixture=...)`), and an id that is a path is
    a path traversal. The rule is `names.why_not_a_name`'s, which `world` applies
    to a world's name for the same reasons, and which refuses a NUL as part of the
    charset: no filename can hold one, and `Path` does not refuse it
    (`"a\\x00b" == Path("a\\x00b").name`), so without that clause the id reached
    `freeze`'s `rmtree` -- whose `ignore_errors=True` suppresses `OSError` and a
    `ValueError` is not one -- and came back as a bare `ValueError` about an
    embedded null character instead of the refusal above.
    """
    reason = why_not_a_name(fixture_id)
    if reason is not None:
        raise WorldBug(
            f"not a fixture id: {fixture_id!r}: {reason}. A fixture id is one directory name: "
            f"{NAME_RULE}."
        )


def load(fixture_dir: Path) -> Fixture:
    """Read one fixture's sidecar. The state file is not opened."""
    sidecar = fixture_dir / SIDECAR_NAME
    try:
        text = sidecar.read_text(encoding="utf-8")
    except OSError as error:
        raise WorldBug(f"{sidecar}: cannot be read: {error}") from error
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise WorldBug(f"{sidecar}: is not valid YAML: {error}") from error
    if not isinstance(data, dict):
        raise WorldBug(f"{sidecar}: is not a mapping")
    # Named before the model is built, because the model can only say which
    # literals were expected, and what a reader needs to know is that this
    # fixture was written by a different version of Seahaven.
    if data.get("format_version") not in FORMAT_VERSIONS:
        raise WorldBug(
            f"{sidecar}: format_version is {data.get('format_version')!r}, not 1 or 2; this "
            f"fixture was written by a different version of Seahaven"
        )
    try:
        meta = FixtureMeta.model_validate(data)
    except pydantic.ValidationError as error:
        raise WorldBug(f"{sidecar}: is not a fixture sidecar: {error}") from error
    return Fixture(meta=meta, dir=fixture_dir)


def load_all(fixtures_dir: Path) -> dict[str, Fixture]:
    """Every fixture in a directory, by id.

    A directory with no fixtures in it, or none at all, is not an error: a world
    that only makes blank instances never mints one.
    """
    found: dict[str, Fixture] = {}
    for child in sorted(_children(fixtures_dir)):
        # Dot-directories are the framework's own: `.pending-<id>` is a freeze in
        # flight, and nothing else in here belongs to a listing of fixtures.
        if not child.is_dir() or child.name.startswith("."):
            continue
        fixture = load(child)
        if fixture.id in found:
            raise WorldBug(
                f"two fixtures claim the id {fixture.id!r}: {found[fixture.id].dir} and {child}"
            )
        found[fixture.id] = fixture
    return found


def verify(fixture: Fixture) -> None:
    """Refuse a fixture whose state files are not the ones its sidecar describes.

    Every node's file, not only the root's: a composite fixture is N files, and
    one of them edited in place is exactly the damage this exists to catch.

    Instance creation verifies before it copies (`instances.World._fixture`), so
    every refusal here is a refusal to create as well -- which is why the symlink
    rule lives in `_verify_file` rather than being repeated beside each copy.
    """
    _verify_file(f"fixture {fixture.id!r}", fixture.state_path, fixture.meta.file_sha256)
    for node in fixture.meta.nodes:
        _verify_file(
            f"fixture {fixture.id!r} node {node.path!r}",
            fixture.file_of(node),
            node.file_sha256,
        )


def _verify_file(subject: str, path: Path, expected: str) -> None:
    """One frozen file against the hash its sidecar recorded, memoised per process.

    A symlink is refused before it is hashed, and named. Every reader of a
    fixture's files would otherwise resolve them as ordinary paths, so a link
    planted at one of these names would be followed to a database the fixture
    does not contain -- and the hash would be taken from the same file the link
    reached, so `file_sha256` agreed and every check reported green while the
    instance came up on the outside file's contents.
    `NodeMeta._a_name_inside_the_fixture_directory` refuses a
    `file` that *spells* its way out of the directory; this refuses one that
    points its way out. The two halves are one rule.

    Refused outright rather than resolved and required to land inside the
    directory: a fixture that points outside itself is a fixture plus an
    invisible dependency, and nothing has asked to share one database between
    two fixtures.

    `lstat`, so the link is seen rather than what it points at. For a regular
    file it is the `stat` this always took, so the memo key is unchanged.

    What this does not reach, so that the claim matches the code: a **hard** link
    to a database outside the directory is a second name for one inode and is
    indistinguishable from an ordinary file here. It is the weaker variant --
    one filesystem only, and an edit through the other name changes the size and
    mtime this hashes against -- but "a fixture's files are the files in its own
    directory" holds against symlinks, not against every way two names can reach
    one file.
    """
    try:
        entry = path.lstat()
    except OSError as error:
        raise WorldBug(f"{subject} has no {path.name}: {error}") from error
    if stat.S_ISLNK(entry.st_mode):
        raise WorldBug(
            f"{subject} at {path} is a symbolic link, and a fixture's files are the files in "
            f"its own directory; a link is followed to a database the fixture does not contain. "
            f"Replace it with the file itself, or freeze the fixture again."
        )
    key = (str(path), entry.st_mtime_ns, entry.st_size, expected)
    if key in _verified:
        return
    if _sha256(path) != expected:
        raise WorldBug(
            f"{subject} at {path} does not match its sidecar's file_sha256; it has "
            f"been modified or truncated. Fixtures are immutable: fork it instead (create an "
            f"instance from it, change it, and freeze the result under a new id)."
        )
    _verified.add(key)


def freeze(instance: Instance, fixture_id: str, description: str, *, fixtures_dir: Path) -> Fixture:
    """Mint a fixture from a live instance: one file per node, one sidecar. Under its lock.

    The instance is untouched: `VACUUM INTO` reads the database (the WAL
    included) and writes a new file, so a freeze is safe to take mid-authoring
    and the instance is usable afterwards.

    All or nothing across the tree. Every node's store is checked against its own
    world's DDL before anything at all is written, so a composite whose third
    node has drifted mints nothing rather than a directory with two good files in
    it, and the refusal names the node.
    """
    check_id(fixture_id)
    target = fixtures_dir / fixture_id
    if target.exists():
        raise WorldBug(
            f"fixture {fixture_id!r} already exists at {target}; fixtures are immutable, so "
            f"freeze under a new id"
        )
    world = instance.world
    # The instance's own half of this module, private for the same reason
    # `control.py` reaches `_control_db()`: the node runtimes are the framework's
    # internals, and freezing is the framework writing down what they hold.
    nodes = instance._nodes()
    for runtime in nodes:
        _check_conformance(runtime)
    pending = fixtures_dir / f"{PENDING_PREFIX}{fixture_id}"
    try:
        shutil.rmtree(pending, ignore_errors=True)
        pending.mkdir(parents=True)
        frozen = [(runtime.node, _vacuum_into(runtime, pending, fixture_id)) for runtime in nodes]
        # By depth and not by position: `Node.depth` states the invariant the
        # order only implies, and `_fixture_file` already reads it.
        root_sha256 = next(digest for node, digest in frozen if node.depth == 0)
        added = tuple(_node_meta(node, digest) for node, digest in frozen if node.depth > 0)
        meta = FixtureMeta(
            # Version 1 whenever there is nothing but the root to describe, so a
            # world that adds nothing writes the directory it always wrote.
            format_version=2 if added else 1,
            id=fixture_id,
            world=world.name,
            world_version=world.version,
            schema_hash=world.schema_hash,
            now=instance.clock.iso(),
            parent_id=instance.fixture,
            file_sha256=root_sha256,
            # The wall clock, and one of the two places in a world that reads it:
            # this says when the fixture was made, not what time it is inside.
            created_at=Clock.wall().iso(),
            description=description,
            nodes=added,
        )
        (pending / SIDECAR_NAME).write_text(
            yaml.safe_dump(_sidecar(meta), sort_keys=True), encoding="utf-8"
        )
        for node, _digest in frozen:
            (pending / _fixture_file(node)).chmod(STATE_MODE)
        # The publish: a fixture directory either does not exist or is whole.
        os.rename(pending, target)
    except BaseException:
        shutil.rmtree(pending, ignore_errors=True)
        raise
    return Fixture(meta=meta, dir=target)


def _check_conformance(runtime: NodeRuntime) -> None:
    """One node's store against its own world's DDL, with the node named on failure.

    Each world checks its own: a composite's nodes are separate databases with
    separate schemas, and the one that drifted is the one an author has to fix.
    """
    try:
        conformance.check(runtime.db.conn, runtime.node.world)
    except WorldBug as error:
        raise WorldBug(f"{runtime.node.path}: {error}") from error


def _fixture_file(node: Node) -> str:
    """The name one node's state file takes inside a *fixture* directory.

    The two arms return the same string today, and the branch is not redundant:
    they are different facts that happen to agree.

    The root's file in a fixture is `STATE_NAME` **by definition of the
    artifact**. It is what `Fixture.state_path` reads, what `lint/fixtures.py`
    looks for, and what is on disk in every fixture ever frozen, including by
    Seahavens that had no `Node`. This module owns that name, so the writer
    spells it from the same constant its three readers do. `node.file_name` is
    the name a node's file takes in an *instance* directory, which is
    `composition.py`'s business (and a separate binding of `STATE_NAME`, taken at
    import); it has no obligation to an artifact already on disk. An added node's
    name is that one, because a fixture had no opinion about it until now -- and
    it is recorded in the sidecar besides, so the artifact still describes
    itself.
    """
    return STATE_NAME if node.depth == 0 else node.file_name


def _vacuum_into(runtime: NodeRuntime, pending: Path, fixture_id: str) -> str:
    """Write one node's compacted file into the pending directory; answer its hash."""
    state = pending / _fixture_file(runtime.node)
    # A compacted, rollback-journal file, and the live database untouched.
    try:
        runtime.db.conn.execute("VACUUM INTO ?", (str(state),))
    except apsw.Error as error:
        # Freezing is an authoring step, not a tool call: a full disk or a
        # fixtures directory this process cannot write to is the author's
        # problem to read, in the same currency as every other refusal here.
        raise WorldBug(f"could not write fixture {fixture_id!r} to {state}: {error}") from error
    return _sha256(state)


def _node_meta(node: Node, file_sha256: str) -> NodeMeta:
    """One added node, as the sidecar records it.

    `world` is written and **not checked at create**, and that is deliberate:
    architecture 11.3 lists what a mismatch refuses -- the node set, the scopes,
    the alias edges, the schema hashes -- and a name is not on it, because
    functional spec 7 makes the schema hash the invalidation signal and a world
    renamed between releases invalidates nothing. It is recorded so the report
    and the log can say which world a store came from, and so a substitution
    that kept the DDL is visible rather than silent: `_version_differences`
    names both sides.
    """
    return NodeMeta(
        path=node.path,
        world=node.world.name,
        world_version=node.world.version,
        schema_hash=node.world.schema_hash,
        scope=node.scope,
        file=_fixture_file(node),
        file_sha256=file_sha256,
        aliases=node.aliases,
    )


def _sidecar(meta: FixtureMeta) -> dict[str, Any]:
    """The sidecar as YAML carries it.

    `mode="json"` because the nested nodes and their alias tuples have to come
    out as plain lists and mappings; and a version-1 sidecar drops `nodes`
    entirely rather than writing an empty list, so a world that adds nothing
    keeps producing the file it produced before composition existed.
    """
    data = meta.model_dump(mode="json")
    if not meta.nodes:
        del data["nodes"]
    return data


def check_composition(meta: FixtureMeta, composition: Composition) -> list[str]:
    """Refuse a fixture that does not describe the composition about to load it.

    Called at create, after the root's file hash is verified and before anything
    is copied. What it compares is the *shape*: which nodes there are, which
    scope each resolved into, which alias edges reach them, and each node's
    schema hash. A `store=` moved on one edge changes every node beneath it, so
    the refusal reports the whole set that moved.

    What comes back is the differences that are **reported and not refused**: a
    node whose installed world is a different version from the one frozen, where
    the schema hash still matches. One package cannot be installed at two
    versions in one environment, so the framework has no version check of its own
    to make; the schema hash is the real invalidation signal and it is checked
    here per node.
    """
    mismatch = composition_mismatch(meta, composition)
    if mismatch is not None:
        raise WorldBug(mismatch)
    _check_schema_hashes(meta, {node.path: node for node in composition.nodes})
    return _version_differences(meta, composition)


def composition_mismatch(meta: FixtureMeta, composition: Composition) -> str | None:
    """Why this fixture does not describe this tree's *shape*, or `None` when it does.

    The refusal `check_composition` raises, returned instead: `lint/fixtures.py`
    reports it as SH406 before a commit, and an author who reads that line and an
    author who hits the refusal in a run are reading the same sentence.

    Schema hashes are deliberately not here. Per node they are the lint's SH403,
    which exists for a leaf world too, and reporting a drifted schema as a shape
    mismatch as well would be one defect under two codes.
    """
    current = {node.path: node for node in composition.nodes}
    try:
        _check_shape(meta, composition)
        _check_node_set(meta, current)
        _check_aliases(meta, current)
    except WorldBug as error:
        return str(error)
    return None


def _check_shape(meta: FixtureMeta, composition: Composition) -> None:
    """The sidecar's shape against the tree's: a composite fixture, or one store."""
    stores = len(composition.nodes)
    if bool(meta.nodes) == (stores > 1):
        return
    raise WorldBug(
        f"fixture {meta.id!r} is a format_version {meta.format_version} sidecar describing "
        f"{_stores(len(meta.nodes) + 1)}, and world {composition.root.world.name!r} now resolves "
        f"to {_stores(stores)}; regenerate it"
    )


def _stores(count: int) -> str:
    return "one store" if count == 1 else f"{count} stores"


def _check_node_set(meta: FixtureMeta, current: Mapping[str, Node]) -> None:
    frozen_scopes = {node.path: node.scope for node in meta.nodes}
    # Every node but the root, which the version-1 fields already describe.
    live_scopes = {path: node.scope for path, node in current.items() if node.depth > 0}
    added = sorted(set(live_scopes) - set(frozen_scopes))
    removed = sorted(set(frozen_scopes) - set(live_scopes))
    shared = sorted(
        path
        for path in set(frozen_scopes) & set(live_scopes)
        if frozen_scopes[path] != live_scopes[path]
    )
    reported = [
        *(f"node added: {path}" for path in added),
        *(f"node removed: {path}" for path in removed),
        # A scope propagates, so one `store=` changing on one edge moves every
        # node beneath it, and all of them are named.
        *(
            f"sharing changed: {path} was frozen in store {frozen_scopes[path]!r} and now "
            f"resolves into {live_scopes[path]!r}"
            for path in shared
        ),
    ]
    if reported:
        raise WorldBug(
            f"fixture {meta.id!r} does not describe the composition of world "
            f"{meta.world!r}: " + "; ".join(reported) + "; regenerate it"
        )


def _check_aliases(meta: FixtureMeta, current: Mapping[str, Node]) -> None:
    frozen = {(node.path, alias) for node in meta.nodes for alias in node.aliases}
    live = {(node.path, alias) for node in current.values() for alias in node.aliases}
    if frozen == live:
        return
    raise WorldBug(
        f"fixture {meta.id!r} does not describe the composition of world {meta.world!r}: the "
        f"alias edges changed, from {sorted(_route(edge) for edge in frozen)} to "
        f"{sorted(_route(edge) for edge in live)}; regenerate it"
    )


def _check_schema_hashes(meta: FixtureMeta, current: Mapping[str, Node]) -> None:
    for node in meta.nodes:
        if node.schema_hash != current[node.path].world.schema_hash:
            raise WorldBug(
                f"{node.path}: fixture {meta.id!r} was frozen from a different schema; "
                f"regenerate it"
            )


def _version_differences(meta: FixtureMeta, composition: Composition) -> list[str]:
    """What a node was frozen from against what is installed, where the two differ.

    The world's *name* as well as its version, because a sidecar's `world` is not
    checked at create (`_node_meta`): a store frozen from one world and loaded
    under a differently-named world with identical DDL is reported here rather
    than passing as a version bump.
    """
    frozen = [(composition.root.path, meta.world, meta.world_version)]
    frozen += [(node.path, node.world, node.world_version) for node in meta.nodes]
    by_path = {node.path: node for node in composition.nodes}
    return [
        f"{path}: frozen from world {world!r} at version {version}, running world "
        f"{by_path[path].world.name!r} at version {by_path[path].world.version}; the schema is "
        f"unchanged, so the fixture still loads"
        for path, world, version in frozen
        if (world, version) != (by_path[path].world.name, by_path[path].world.version)
    ]


def _route(edge: tuple[str, str]) -> str:
    path, alias = edge
    return f"{alias} -> {path}"


def _children(directory: Path) -> list[Path]:
    try:
        return list(directory.iterdir())
    except FileNotFoundError:
        return []


def _sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()
