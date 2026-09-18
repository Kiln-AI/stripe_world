"""The tree a world resolves to: its nodes, their names, and the tool surface they add up to.

A world may add other worlds, and what a host declares with `add_world` is a
*graph* -- the same world reached by two routes is one store, a world added under
a named scope is another. This module turns that graph into the one thing the
rest of the framework reads: a `Composition`, the sealed tree of `Node`s with
their canonical paths, their files, their bound startup keywords, the chains
their calls descend, and the flat, insertion-ordered tool list an agent sees.

A leaf world is a composition of exactly one node, with path `main`, file
`state.sqlite` and its own tools in registration order, so nothing downstream
needs to ask whether a world is composite.

Resolution is *lazy*. Everything here is a whole-tree property -- a name
collision after prefixing, a list naming a tool that does not exist, the attach
bound -- and none of it is knowable at the `add_world` line: a host's own tools
are registered by imports that run after `world.py`, and a world holds no
reference to the hosts that added it, so a tool registered on a dependency
afterwards could never be pushed to them. The seal therefore runs on the first
use of the tree and is invalidated by a registration anywhere in the process.
"""

import functools
import re
import threading
from collections import deque
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

import apsw

from seahaven.call import Call, Handler, Middleware, build_chain, invoke, name_of
from seahaven.ctx import Ctx
from seahaven.errors import WorldBug
from seahaven.fixtures import STATE_NAME
from seahaven.handles import ctx_for
from seahaven.tool import Tool

if TYPE_CHECKING:  # `world.py` imports this module; the annotation is all that is needed here
    from seahaven.world import World

__all__ = [
    "ROOT_PATH",
    "AddedWorld",
    "Composition",
    "Contributed",
    "Node",
    "NodeKey",
    "NodeReport",
    "attached_limit",
    "build_route_chain",
    "bump",
    "canonical_tree",
    "epoch",
    "resolve",
]

# The root's path, SQLite's own schema name for the file a connection is opened
# on, and therefore a name no added world may take.
ROOT_PATH = "main"

# A node's name. Identifier-like and lowercase, because it becomes a path segment,
# a file name and an attached schema name. `__` is excluded for one reason that is
# not cosmetic: a schema name is the path with `/` replaced by `__`, so permitting
# `__` inside a segment would let `a__b` and `a/b` name the same schema.
NAME = re.compile(r"^[a-z][a-z0-9_]*$")

# SQLite's own schema names. `main` is the root's file and `temp` is the
# connection's temporary database; a node named either could not be attached.
RESERVED_NODE_NAMES = frozenset({"main", "temp"})

# What OpenEnv's tool listing accepts. A prefix is free text, so this is the check
# that a prefixed name is still callable over the wire. The second pattern is the
# same alphabet over any length, including none, which is what a `tool_prefix` has
# to be made of for the name it produces to still be one.
TOOL_NAME = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
TOOL_NAME_CHARACTERS = re.compile(r"^[A-Za-z0-9_-]*$")

type NodeKey = tuple[World, str | None]

_epoch_lock = threading.Lock()
_epoch = 0


def bump() -> int:
    """Invalidate every sealed composition in the process. Called by every registration verb.

    One counter rather than a dirty flag per world, because a `World` holds no
    back-reference to the hosts that added it: registering a tool on a leaf world
    can never notify the host that added it. A global counter over-invalidates --
    one registration anywhere reseals everything on next use -- and that is the
    correct trade, since registration finishes at import and a reseal is a walk of
    a tree bounded at the attach limit, not a copy of anything.
    """
    global _epoch
    with _epoch_lock:
        _epoch += 1
        return _epoch


def epoch() -> int:
    """The current registration epoch: what a cached seal is compared against.

    Read without the lock, which guards the counter's increment and nothing else.
    A reader racing a registration may see the value from either side of it and
    seal once more or once less than it strictly had to; the guarantee registration
    makes is that the next *use* sees it, and there is no use without a read.
    Every call of every instance passes through here, so it is one load.
    """
    return _epoch


@functools.cache
def attached_limit() -> int:
    """How many databases the installed SQLite can `ATTACH` to one connection.

    Probed rather than hard-coded (125 on apsw 3.53.4, for both the runtime
    default and the compile-time maximum) so that a differently built SQLite is
    refused at the seal with its own number, rather than at the ATTACH inside
    `Instance.inspect()` that happens to be one too many.
    """
    connection = apsw.Connection(":memory:")
    try:
        return connection.limit(apsw.SQLITE_LIMIT_ATTACHED)
    finally:
        connection.close()


@dataclass(frozen=True)
class AddedWorld:
    """One `add_world` call, as the host recorded it. Code, never state."""

    world: World
    name: str
    store: str | None
    tool_prefix: str | None
    tool_allow_list: tuple[str, ...] | None
    tool_block_list: tuple[str, ...] | None
    startup: Mapping[str, Any]


@dataclass(frozen=True, eq=False)
class Node:
    """One store in a composite: a world, the scope it resolved into, and its identity in the tree.

    Equality is identity. A node's `added` reaches its children, so a generated
    `__eq__` would walk the whole subtree to answer a question nothing asks.
    """

    key: NodeKey
    path: str
    world: World
    scope: str | None
    depth: int
    # Child name -> node. A live view of a dict filled once every node exists: the
    # node graph is a directed acyclic graph and a child is often built after the
    # parent that names it.
    added: Mapping[str, Node]
    # The `<parent path>/<name>` routes that reach this node and are not its path.
    aliases: tuple[str, ...]
    file_name: str
    schema_name: str
    bound_startup: Mapping[str, Any]
    # What an agent-initiated call to a tool this node owns descends -- every
    # node's middlewares along the canonical route, outermost first -- and what a
    # call from host code through a handle descends, which is this node's own
    # middlewares and nothing above them. Both end in `invoke` on this node.
    agent_chain: Handler
    internal_chain: Handler

    def __repr__(self) -> str:
        return f"<Node {self.path} of {self.world.name} scope={self.scope!r}>"


@dataclass(frozen=True)
class NodeReport:
    """One node of a live instance, as an eval asks what it is running against.

    Data and not a `Node`: a `Node` reaches its children, its world and the
    chains its calls descend, and what an eval wants is a description it can
    print, compare and serialise. Nothing agent-facing carries any of it.
    """

    path: str
    world: str
    world_version: str
    scope: str | None
    aliases: tuple[str, ...]
    schema_hash: str
    # What the fixture this instance was created from recorded for this node,
    # when that is not the version installed: `None` for a blank instance, and
    # `None` where the two agree. A version difference under a matching schema
    # hash is reported and never refused (architecture 11.3), and this is where
    # an eval reads it; `instances.py` logs the same fact at INFO at create.
    frozen_world_version: str | None = None

    @classmethod
    def of(cls, node: Node, frozen_world_version: str | None = None) -> NodeReport:
        return cls(
            path=node.path,
            world=node.world.name,
            world_version=node.world.version,
            scope=node.scope,
            aliases=node.aliases,
            schema_hash=node.world.schema_hash,
            frozen_world_version=frozen_world_version,
        )


@dataclass(frozen=True, eq=False)
class Contributed:
    """One entry of the composite tool list: the name the agent sees, and who owns it."""

    name: str
    tool: Tool
    node: Node


@dataclass(frozen=True, eq=False)
class Composition:
    """The sealed tree. Rebuilt from scratch by every seal; never mutated."""

    root: Node
    # Breadth-first, root first: the order every per-node loop in the framework
    # runs in, and the order a fixture's sidecar lists its nodes.
    nodes: tuple[Node, ...]
    by_key: Mapping[NodeKey, Node]
    tools: Mapping[str, Contributed]
    by_fn: Mapping[Callable[..., Any], tuple[Contributed, ...]]
    # The union across the tree, or `None` -- meaning "any" -- when a hook
    # anywhere takes `**kwargs`.
    accepted_startup_kwargs: frozenset[str] | None
    # (parent path, child name, child path) for every `add_world` in the tree. The
    # edge set identifies the composition exactly and is bounded by nodes x
    # children; enumerating alias *routes* is exponential in a diamond.
    edges: tuple[tuple[str, str, str], ...]

    def entry_for(self, fn: Callable[..., Any]) -> Contributed:
        """The one contributed tool built from `fn`, for a call by function reference.

        A function reaches more than one entry when its world is a node of this
        tree twice -- two accounts of one payments world -- or when that world
        registered it as two tools; either way it does not name one store and one
        surface name, so the caller is sent to the handle or the name that does.
        It reaches none when the function is not a tool at all, or is one of a
        world this tree does not contain, or is filtered off the surface by an
        allow or block list: `ctx.worlds.<name>.call(fn)` is the way to the last
        of those, and this list is the agent's.
        """
        entries = self.by_fn.get(fn, ())
        if len(entries) == 1:
            return entries[0]
        if not entries:
            raise WorldBug(
                f"{name_of(fn)} is not a tool of world {self.root.world.name!r} or anything it "
                f"adds, or is one an allow or block list keeps off this surface; reach that one "
                f"with ctx.worlds.<name>.call(...)"
            )
        where = ", ".join(f"{entry.name} at {entry.node.path}" for entry in entries)
        raise WorldBug(
            f"{name_of(fn)} names more than one tool of world {self.root.world.name!r} and "
            f"what it adds ({where}); name the one you mean, with ctx.worlds.<name>.call(...) "
            f"or with its exposed name"
        )


def resolve(root: World) -> Composition:
    """The sealed composition of `root`, or the `WorldBug` that says why there is not one.

    Pure: it reads the registrations of every world in the tree and mutates
    nothing. Every whole-tree failure of the design's seal table is raised here,
    in that table's order, with a message naming the path and the `add_world`
    behind it.
    """
    order, children, paths, depths, parents = _walk(root)
    edges, aliases = _edges(children, paths)
    bound, conflicts = _bound_startup(children, paths)
    nodes = _nodes(order, paths, depths, parents, children, aliases, bound)
    surface = _contribute(order[0], nodes, children)

    _raise_unknown_list_names(surface.unknown)
    _raise_clashes(surface.candidates)
    _raise_reserved_or_invalid_names(surface.candidates)
    _raise_conflicts(conflicts)
    _raise_over_the_attach_limit(nodes)

    tools = {name: candidate.entry for name, candidate in surface.candidates.items()}
    return Composition(
        root=nodes[order[0]],
        nodes=tuple(nodes[key] for key in order),
        by_key=MappingProxyType(dict(nodes)),
        tools=MappingProxyType(tools),
        by_fn=MappingProxyType(_by_fn(tools)),
        accepted_startup_kwargs=_accepted_startup_kwargs(nodes),
        edges=edges,
    )


# The child edges of one node key, as (name, declaration, child key) triples.
type _Children = Mapping[NodeKey, tuple[tuple[str, AddedWorld, NodeKey], ...]]


def _walk(
    root: World,
) -> tuple[
    list[NodeKey], _Children, dict[NodeKey, str], dict[NodeKey, int], dict[NodeKey, NodeKey]
]:
    """One breadth-first walk: the nodes in order, their edges, paths, depths and parents.

    A node is `(world object, scope)`. An edge with `store=None` passes the
    adder's scope through and an edge with `store="eu"` opens that scope for the
    added world *and everything beneath it*, which is what makes a host's second
    account a second account all the way down. Scope names are flat and global, so
    two worlds naming the same scope for the same object share the store.

    A node's path is the shallowest route to it, ties broken by registration
    order, and that is what the queue gives with no tie-breaking pass: depth is
    breadth-first's primary order, and within one depth it visits in (parent's
    position at depth-1, child index) order -- which by induction on depth is
    lexicographic order on the registration indices along the path, and that is
    exactly the order a depth-first preorder walk visits the nodes of that depth.

    Termination: scopes are drawn from the finite set of declared `store` strings
    plus `None`, and `add_world` refuses a world that appears in its own subtree,
    so the key set is bounded by |worlds| x |scopes|.
    """
    root_key: NodeKey = (root, None)
    order = [root_key]
    children: dict[NodeKey, tuple[tuple[str, AddedWorld, NodeKey], ...]] = {}
    paths = {root_key: ROOT_PATH}
    depths = {root_key: 0}
    # The node each one was first reached from: its parent on the canonical
    # route, and what the chain of a node is read off.
    parents: dict[NodeKey, NodeKey] = {}
    pending: deque[NodeKey] = deque([root_key])
    while pending:
        key = pending.popleft()
        world, scope = key
        edges = []
        for added in world.added_worlds:
            child: NodeKey = (added.world, scope if added.store is None else added.store)
            edges.append((added.name, added, child))
            # A path is fixed by the first route that reaches the node, which is
            # what makes every other route an alias.
            if child not in paths:
                paths[child] = _route(paths[key], added.name)
                depths[child] = depths[key] + 1
                parents[child] = key
                order.append(child)
                pending.append(child)
        children[key] = tuple(edges)
    return order, children, paths, depths, parents


def canonical_tree(root: Node) -> Iterator[Node]:
    """Depth-first preorder over the canonical tree: every node once, under its own path.

    The tree, not the graph: an edge whose route is an alias is not descended,
    because the node it reaches is visited under the parent its path names. So a
    node shared by two hosts is walked once, which is what "each node's startup
    hooks run once, however many routes reach it" asks for.
    """
    yield root
    for name, child in root.added.items():
        if child.path == _route(root.path, name):
            yield from canonical_tree(child)


def _route(parent_path: str, name: str) -> str:
    """The path an edge spells: a child of the root is bare, everything deeper is qualified."""
    return name if parent_path == ROOT_PATH else f"{parent_path}/{name}"


def _edges(
    children: _Children, paths: Mapping[NodeKey, str]
) -> tuple[tuple[tuple[str, str, str], ...], dict[NodeKey, tuple[str, ...]]]:
    """Every edge in the tree, and the non-canonical routes each node is reached by."""
    edges: list[tuple[str, str, str]] = []
    aliases: dict[NodeKey, list[str]] = {key: [] for key in children}
    for key, child_edges in children.items():
        for name, _added, child in child_edges:
            edges.append((paths[key], name, paths[child]))
            route = _route(paths[key], name)
            if route != paths[child]:
                aliases[child].append(route)
    return tuple(edges), {key: tuple(routes) for key, routes in aliases.items()}


# One node's startup keyword bound to two values, with the route of each edge.
type _Conflict = tuple[str, str, tuple[str, Any], tuple[str, Any]]


def _bound_startup(
    children: _Children, paths: Mapping[NodeKey, str]
) -> tuple[dict[NodeKey, dict[str, Any]], list[_Conflict]]:
    """Each node's bound startup keywords, merged key-wise over every edge that reaches it.

    Key-wise rather than an equality test across edges, because a dependency's
    plain `add_world(payments.world)` binds nothing and must not stop the host from
    configuring the node they share. An edge that binds nothing has no opinion; the
    same key bound to two different values does, and a node has one configuration.
    """
    bound: dict[NodeKey, dict[str, Any]] = {key: {} for key in children}
    source: dict[NodeKey, dict[str, str]] = {key: {} for key in children}
    conflicts: list[_Conflict] = []
    for key, child_edges in children.items():
        for name, added, child in child_edges:
            # The `add_world` call, spelled with its parent's path however shallow
            # it is (`main/payments`), because what a conflict names is a call
            # site and not a node -- two of which can share a path segment.
            route = f"{paths[key]}/{name}"
            for keyword, value in added.startup.items():
                if keyword in bound[child] and bound[child][keyword] != value:
                    conflicts.append(
                        (
                            paths[child],
                            keyword,
                            (source[child][keyword], bound[child][keyword]),
                            (route, value),
                        )
                    )
                    continue
                bound[child][keyword] = value
                source[child][keyword] = route
    return bound, conflicts


def _nodes(
    order: Sequence[NodeKey],
    paths: Mapping[NodeKey, str],
    depths: Mapping[NodeKey, int],
    parents: Mapping[NodeKey, NodeKey],
    children: _Children,
    aliases: Mapping[NodeKey, tuple[str, ...]],
    bound: Mapping[NodeKey, Mapping[str, Any]],
) -> dict[NodeKey, Node]:
    """The `Node` objects, with their child tables filled once they all exist."""
    tables: dict[NodeKey, dict[str, Node]] = {key: {} for key in order}
    nodes = {
        key: Node(
            key=key,
            path=paths[key],
            world=key[0],
            scope=key[1],
            depth=depths[key],
            added=MappingProxyType(tables[key]),
            aliases=aliases[key],
            file_name=_file_name(paths[key]),
            schema_name=_schema_name(paths[key]),
            bound_startup=MappingProxyType(dict(bound[key])),
            agent_chain=build_route_chain(_route_layers(key, parents), key),
            internal_chain=build_route_chain(_own_layers(key), key),
        )
        for key in order
    }
    for key in order:
        for name, _added, child in children[key]:
            tables[key][name] = nodes[child]
    return nodes


def build_route_chain(layers: Sequence[tuple[NodeKey, Middleware]], owner_key: NodeKey) -> Handler:
    """The framework's chain, with each layer paired with the node it belongs to.

    `build_chain` over middlewares each wrapped in its node -- one chain builder
    and one context-normalisation rule in this framework, and this is the same one
    a world without a composition has always run.

    The one addition is the wrapper: a layer runs with the `Ctx` of *its own*
    node -- its own `db`, `state`, `ids` and `worlds` -- rather than with the
    owner's, which is what lets a host's access-control middleware read the
    principal its own startup hook wrote while the tool it is guarding belongs to
    a world two levels down. `call` is `build_chain`'s own, so `call is ctx.call`
    in every layer.

    A leaf world's chain pairs every layer with the root, and `ctx_for` hands each
    one the context `build_chain` already gave it: the chain the framework has
    today, with one wrapper in front of each layer and nothing allocated in it.
    """
    return build_chain(
        [_on_node(key, middleware) for key, middleware in layers],
        _owner_layer(owner_key, invoke),
    )


def _on_node(key: NodeKey, middleware: Middleware) -> Middleware:
    """One middleware, run with the context of the node it was paired with at the seal."""

    def on_node(ctx: Ctx[Any], call: Call, next_: Handler) -> Any:
        return middleware(ctx_for(ctx, key, call), call, next_)

    return on_node


def _owner_layer(key: NodeKey, innermost: Handler) -> Handler:
    """The bottom of every chain: the owner's own context, whatever ran above it.

    Needed because the layer above `invoke` may belong to any node on the route --
    a host with middleware and an owner with none is the ordinary case -- and
    `invoke` opens its transaction on the context it is handed.
    """

    def call_innermost(ctx: Ctx[Any], call: Call) -> Any:
        return innermost(ctx_for(ctx, key, call), call)

    return call_innermost


def _route_layers(
    key: NodeKey, parents: Mapping[NodeKey, NodeKey]
) -> list[tuple[NodeKey, Middleware]]:
    """Every middleware on the canonical route to a node, outermost first.

    The whole route rather than the host's chain and the owner's: applied
    recursively through the nesting a composite is, that is what "the host's chain
    outermost, then the owning world's" says, and it is the reading under which an
    intermediate composite's error handler still shapes the errors of the tools it
    contributes.
    """
    route = [key]
    while route[-1] in parents:
        route.append(parents[route[-1]])
    return [
        (node_key, middleware)
        for node_key in reversed(route)
        for middleware in node_key[0].middlewares
    ]


def _own_layers(key: NodeKey) -> list[tuple[NodeKey, Middleware]]:
    """A node's own middlewares, for a call host code makes into it.

    Only the owning world's chain runs: the host's is already wrapped around the
    host tool making the call.
    """
    return [(key, middleware) for middleware in key[0].middlewares]


def _schema_name(path: str) -> str:
    """The name a node's file is attached under: its path, flattened."""
    return path.replace("/", "__")


def _file_name(path: str) -> str:
    """The node's file inside an instance directory.

    The root keeps `state.sqlite`, so a leaf world's instance directory is
    byte-for-byte the layout it has today.
    """
    return STATE_NAME if path == ROOT_PATH else f"state.{_schema_name(path)}.sqlite"


@dataclass(frozen=True)
class _UnknownListName:
    """An allow or block list naming a tool the added world does not contribute."""

    added: AddedWorld
    which: str
    name: str
    contributed: tuple[str, ...]


@dataclass(frozen=True)
class _Candidate:
    """One contributed tool on its way up the tree, and where it was declared.

    The route -- the `/`-joined `add_world` names from the node whose list this
    is -- and not the owning node's path, is what a diagnostic has to name: two
    views of one node share a path, and the author's fix is on one of the two
    `add_world` lines. `prefixes` is every `tool_prefix` applied on that route,
    outermost first, each paired with the route of the `add_world` that carries
    it, so an invalid name can name the line to fix.

    `shadowed` is the route of a second declaration of this same name, found when
    the two were folded together (see `_contribute`). It rides along rather than
    being reported at once, because the pair may yet be filtered out above.
    """

    entry: Contributed
    route: str
    prefixes: tuple[tuple[str, str], ...]
    shadowed: str | None

    @property
    def declared_at(self) -> str:
        """The route, with the root's own tools spelled as the root."""
        return self.route or ROOT_PATH


@dataclass(frozen=True)
class _Surface:
    """The flat tool list and what the seal has to say about it."""

    candidates: dict[str, _Candidate]
    unknown: list[_UnknownListName]


def _contribute(root_key: NodeKey, nodes: Mapping[NodeKey, Node], children: _Children) -> _Surface:
    """The flat, insertion-ordered tool list, memoised per node key and built bottom-up.

    A node's contribution is its own non-control tools in registration order, then
    each `add_world`'s contribution in `add_world` order -- already filtered and
    already prefixed below that edge -- filtered against the child's *contributed*
    names and prefixed as one list. Three things follow by construction: a host's
    prefix and lists apply to a whole subtree as one list and never name a
    grandchild; "the added world's own names, before any prefix" means the names
    one level down actually contributes; and the owning node travels with the tool,
    so a grandchild's tool reached through the host still runs against the
    grandchild's store.

    The memo key is the node key rather than the world, because a node's own tools
    belong to whichever key the walk is under: two accounts of one world contribute
    the same `Tool` objects against different nodes.

    **Each node's list holds one entry per name.** Memoising the *distinct* lists
    is not enough on its own: a node that reaches one grandchild through two
    children concatenates that grandchild's tools twice, so a diamond doubles the
    list at every level and a tree of ninety nodes -- well under the attach bound,
    and passing every `add_world` check -- would take hours to seal rather than
    failing. Folding a repeat into the entry already there bounds every list by
    the number of distinct names, which is what makes the seal O(nodes x children
    x tools) as the design says it is.

    Folding is safe because everything above a node treats two entries of one name
    identically: a prefix renames both the same way, and an allow or block list
    matches on the name, so it keeps both or drops both. A repeat is therefore a
    real collision exactly when the entry that absorbed it reaches the root, which
    is why `shadowed` is carried up rather than reported where it is found.
    """
    unknown: list[_UnknownListName] = []
    memo: dict[NodeKey, dict[str, _Candidate]] = {}

    def contribution(key: NodeKey) -> dict[str, _Candidate]:
        if key not in memo:
            node = nodes[key]
            candidates: dict[str, _Candidate] = {}
            for tool in node.world.tools.values():
                if not tool.control:
                    _fold(candidates, _Candidate(Contributed(tool.name, tool, node), "", (), None))
            for _name, added, child in children[key]:
                # A new candidate per edge, so the route and the prefixes below are
                # this edge's own even when the child's list came out of the memo.
                for inner in _filtered(added, contribution(child), unknown):
                    _fold(candidates, _through(added, inner))
            memo[key] = candidates
        return memo[key]

    return _Surface(candidates=contribution(root_key), unknown=unknown)


def _through(added: AddedWorld, inner: _Candidate) -> _Candidate:
    """One candidate as its host contributes it: renamed, and one route deeper."""
    prefix = added.tool_prefix

    def outward(route: str) -> str:
        return added.name if not route else f"{added.name}/{route}"

    carried = tuple((applied, outward(where)) for applied, where in inner.prefixes)
    return _Candidate(
        entry=Contributed(
            name=_prefixed(prefix, inner.entry.name), tool=inner.entry.tool, node=inner.entry.node
        ),
        route=outward(inner.route),
        prefixes=carried if prefix is None else ((prefix, added.name), *carried),
        shadowed=None if inner.shadowed is None else outward(inner.shadowed),
    )


def _fold(candidates: dict[str, _Candidate], candidate: _Candidate) -> None:
    """Add a candidate, or record it as the second declaration of a name already there."""
    standing = candidates.get(candidate.entry.name)
    if standing is None:
        candidates[candidate.entry.name] = candidate
        return
    if standing.shadowed is None:
        # The first repeat is the one reported, as the first of anything else the
        # seal finds is: a second is one more line about the same mistake.
        candidates[candidate.entry.name] = replace(standing, shadowed=candidate.declared_at)


def _filtered(
    added: AddedWorld, candidates: Mapping[str, _Candidate], unknown: list[_UnknownListName]
) -> Iterator[_Candidate]:
    """One edge's allow or block list applied to what the child contributes."""
    for which, listed in (
        ("tool_allow_list", added.tool_allow_list),
        ("tool_block_list", added.tool_block_list),
    ):
        for name in listed or ():
            if name not in candidates:
                unknown.append(_UnknownListName(added, which, name, tuple(candidates)))
    if added.tool_allow_list is not None:
        allowed = set(added.tool_allow_list)
        return (c for name, c in candidates.items() if name in allowed)
    if added.tool_block_list is not None:
        blocked = set(added.tool_block_list)
        return (c for name, c in candidates.items() if name not in blocked)
    return iter(candidates.values())


def _prefixed(prefix: str | None, name: str) -> str:
    return name if prefix is None else f"{prefix}{name}"


def _by_fn(tools: Mapping[str, Contributed]) -> dict[Callable[..., Any], tuple[Contributed, ...]]:
    """Each tool function to the entries carrying it, for dispatch by function reference.

    A function reaches more than one entry exactly when its world is a node twice,
    which is the ambiguity a caller has to resolve through a handle.
    """
    grouped: dict[Callable[..., Any], list[Contributed]] = {}
    for entry in tools.values():
        grouped.setdefault(entry.tool.fn, []).append(entry)
    return {fn: tuple(entries) for fn, entries in grouped.items()}


def _accepted_startup_kwargs(nodes: Mapping[NodeKey, Node]) -> frozenset[str] | None:
    """Every keyword some startup hook in the tree names, or `None` if one takes `**kwargs`.

    `reset`'s broadcast rule as a set: a keyword beyond `fixture`, `seed` and `now`
    reaches every hook in the tree that names it, so the union is what an unknown
    argument is checked against.
    """
    accepted: set[str] = set()
    for node in nodes.values():
        for hook in node.world.startup_hooks:
            if hook.takes_var_kwargs:
                return None
            accepted |= hook.accepts
    return frozenset(accepted)


def _raise_unknown_list_names(unknown: Sequence[_UnknownListName]) -> None:
    if not unknown:
        return
    problem = unknown[0]
    added = problem.added
    listed = ", ".join(problem.contributed) or "nothing"
    raise WorldBug(
        f"add_world({added.world.name}, name={added.name!r}): {problem.which} names "
        f"{problem.name!r}, which {added.world.name!r} does not contribute; "
        f"it contributes: {listed}"
    )


def _raise_clashes(candidates: Mapping[str, _Candidate]) -> None:
    """A name declared twice, by whichever two `add_world` calls survived to the root."""
    for name, candidate in candidates.items():
        if candidate.shadowed is not None:
            raise WorldBug(
                f"tool {name!r} is contributed by both {candidate.declared_at!r} and "
                f"{candidate.shadowed!r}; give one a different tool_prefix"
            )


def _raise_reserved_or_invalid_names(candidates: Mapping[str, _Candidate]) -> None:
    # Imported here rather than at the top: `world.py` imports this module, so the
    # two name sets it owns are read at seal time instead of at import time.
    from seahaven.world import CONTROL_TOOL_NAMES, RESERVED_TOOL_NAMES

    for name, candidate in candidates.items():
        where = _declared_at(candidate)
        if name in RESERVED_TOOL_NAMES:
            raise WorldBug(
                f"{where} contributes {name!r}, a name OpenEnv reserves for the environment "
                f"({', '.join(sorted(RESERVED_TOOL_NAMES))})"
            )
        if name in CONTROL_TOOL_NAMES:
            raise WorldBug(f"{where} contributes {name!r}, which is a control tool's name")
    for name, candidate in candidates.items():
        if TOOL_NAME.fullmatch(name):
            continue
        culprit = _the_prefix_to_blame(candidate.prefixes)
        blame = (
            f"{_declared_at(candidate)} contributes"
            if culprit is None
            else f"tool_prefix {culprit[0]!r} on the add_world at {culprit[1]!r} produces"
        )
        raise WorldBug(
            f"{blame} {name!r}, which is not a valid tool name: a tool name is 1 to 128 "
            f"characters of letters, digits, '_' and '-'"
        )


def _declared_at(candidate: _Candidate) -> str:
    """Which declaration a message is about: an `add_world`, or the root's own registry."""
    if candidate.route == "":
        return "this world"
    return f"the add_world at {candidate.declared_at!r}"


def _the_prefix_to_blame(prefixes: Sequence[tuple[str, str]]) -> tuple[str, str] | None:
    """The outermost `tool_prefix` on the route that is not made of tool-name characters.

    The outermost first, because that is the one the author of *this* world wrote:
    a prefix deeper in the tree belongs to a package they may not own, and naming
    it would send them to an innocent line. `None` means no prefix is at fault and
    the tool's own name is what the listing cannot carry.
    """
    for prefix, where in prefixes:
        if not TOOL_NAME_CHARACTERS.fullmatch(prefix):
            return prefix, where
    return None


def _raise_conflicts(conflicts: Sequence[_Conflict]) -> None:
    if not conflicts:
        return
    path, keyword, (first_route, first_value), (second_route, second_value) = conflicts[0]
    raise WorldBug(
        f"node {path!r} has startup {keyword!r} bound to {first_value!r} by add_world at "
        f"{first_route!r} and to {second_value!r} by add_world at {second_route!r}"
    )


def _raise_over_the_attach_limit(nodes: Mapping[NodeKey, Node]) -> None:
    added = len(nodes) - 1
    limit = attached_limit()
    if added > limit:
        raise WorldBug(
            f"this world has {added} added stores; SQLite can attach {limit}. Every node is a "
            f"file the eval's inspection connection attaches, so the tree cannot be larger"
        )
