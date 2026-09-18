"""How host code reaches the worlds its world adds, for exactly as long as it may.

A host tool calls an added world's tool, reads its store or seeds its state
through `ctx.worlds.<name>`, which answers a `WorldHandle`: one node's `call`,
`db`, `state` and `worlds`. Nothing here is a reference a world may keep. A
handle belongs to one *activation* of one instance -- the outermost call, or the
`bulk()` block, that was running when it was made -- and using it after that
activation has ended raises rather than quietly addressing another instance.

That is what makes sharing by node identity work at all: because handlers reach
added worlds by name on every call and never hold a store, the framework is free
to resolve one name to different nodes in different instances.

The activation is a `Frame`, opened by `Instance._held()` at lock depth 0 and
carrying the epoch it was opened at. Every member access compares that epoch
against the instance's own, which is one integer compare on the call path and the
whole of the lifetime rule.
"""

import time
from collections import deque
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Concatenate, overload

from seahaven.call import Call, arguments_of, name_of, rebind
from seahaven.errors import UnknownTool, WorldBug

if TYPE_CHECKING:  # every one of these modules sits above this one at run time
    from seahaven.composition import Node, NodeKey
    from seahaven.ctx import Ctx
    from seahaven.db import Db
    from seahaven.instances import Instance, NodeRuntime
    from seahaven.tool import Tool

__all__ = ["Frame", "WorldHandle", "Worlds", "ctx_for", "unbound"]

_STALE = "a world handle was used after the call it belongs to returned"


@dataclass(frozen=True, eq=False)
class Frame:
    """One activation of one instance: what every handle made during it is anchored to.

    Opened by `Instance._held()` on the transition from lock depth 0 to 1 and
    dropped on the way back, so it spans the outermost call and everything nested
    inside it -- a `handle.call`, and an `inst.call(...)` made from inside a
    `bulk()` block -- rather than one dispatch.
    """

    instance: Instance
    epoch: int

    @property
    def live(self) -> bool:
        return self.instance._epoch == self.epoch

    def ctx(self, key: NodeKey, call: Call | None) -> Ctx[Any]:
        """The context one node runs with in this activation: its own, plus a live `worlds`.

        The node's template context carries its `db`, `ids` and `state` and the
        instance's clock; what this adds is the call and a `Worlds` bound to this
        frame, which is the only kind that resolves a name.
        """
        return self.instance._runtime[key].ctx.with_call(call, worlds=Worlds(self, key))


class Worlds:
    """The worlds one node adds, by name: what `ctx.worlds` is.

    Also the public base a world subclasses to declare its children to a type
    checker (architecture section 8.3):

        class CompanyWorlds(seahaven.Worlds):
            stripe: seahaven.WorldHandle

    The subclass is never instantiated -- `ctx.worlds` is always this class -- and
    the annotation is a fiction `__getattr__` satisfies at run time.

    Everything public here is a dunder, and the two attributes are `_`-prefixed,
    because a child may be named anything `^[a-z][a-z0-9_]*$` matches: an ordinary
    method or property called `frame` or `keys` would shadow a child of that name,
    and a world author cannot be asked to know which names this class happens to
    use. That is also why `unbound()` is a function of this module rather than a
    classmethod here: `unbound` is a name a host may perfectly well give a world
    it adds.
    """

    def __init__(self, frame: Frame | None = None, key: NodeKey | None = None) -> None:
        self._frame = frame
        self._key = key

    def __getattr__(self, name: str) -> WorldHandle:
        # Only child names are answered here. A `_` name is either this class's
        # own attribute during construction or a protocol lookup (`copy`,
        # `pickle`, pytest's introspection), and answering those with a handle --
        # or with a `WorldBug` -- breaks them; no child can be spelled that way.
        if name.startswith("_"):
            raise AttributeError(name)
        return self[name]

    def __getitem__(self, name: str) -> WorldHandle:
        frame, key = _bound(self)
        node = _node_of(frame, key)
        child = node.added.get(name)
        if child is None:
            adds = ", ".join(node.added) or "nothing"
            raise WorldBug(
                f"world {node.world.name!r} at {node.path!r} adds no world named {name!r}; "
                f"it adds: {adds}"
            )
        return WorldHandle(frame, child.key)

    def __repr__(self) -> str:
        if self._frame is None or self._key is None:
            return "<Worlds unbound>"
        return f"<Worlds of {self._key[0].name}>"


class WorldHandle:
    """One node of a composition, for one activation of one instance.

    What host code is given for a world its world added: that node's tools, store
    and state, and the worlds it adds in turn. Not a reference -- see the module
    docstring -- and not a way to reach the host, a sibling, or anything else the
    node does not itself add.
    """

    def __init__(self, frame: Frame, key: NodeKey) -> None:
        self._frame = frame
        self._key = key

    @property
    def db(self) -> Db:
        """That node's `Db`: the same wrapper host code has for its own store."""
        return self._runtime().db

    @property
    def state(self) -> dict[str, Any]:
        """That node's per-instance state. Each node has its own."""
        return self._runtime().state

    @property
    def worlds(self) -> Worlds:
        """That node's own children, for a host reaching a grandchild explicitly."""
        return Worlds(_live(self._frame), self._key)

    @overload
    def call[**P, R](
        self,
        tool: Callable[Concatenate[Ctx[Any], P], R],
        /,
        *args: P.args,
        **kwargs: P.kwargs,
    ) -> R: ...
    @overload
    def call(self, tool: str, /, **arguments: Any) -> Any: ...

    def call(self, tool: str | Callable[..., Any], /, *args: Any, **arguments: Any) -> Any:
        """Run one tool of that node, or of a world beneath it, as part of the call in flight.

        By name, it is the node's *own* names, unprefixed and unfiltered: the
        allow and block lists shape the agent's surface, and host code can call
        every tool of a world it adds. By the function itself -- the typed way in
        (architecture section 8.2) -- it is every world in this handle's subtree,
        because a function names a tool exactly and what a handle is for is naming
        the *store*: an account reached twice from the root is one account here.

        Arguments are validated as if the agent had called it, only the owning
        world's chain runs -- the host's is already wrapped around the host tool
        making this call -- and the added world's own `ToolError` subclasses
        propagate, for the host to decide what the agent sees.

        The concurrency gate is not taken: the outermost call holds it, and one
        instance runs one call at a time however many nodes that call touches.
        """
        started = time.perf_counter()
        name = name_of(tool)
        # Outside the try, and so unlogged, deliberately: a handle whose activation
        # has ended, or one whose tree has grown a node since, is not a call that
        # reached a node -- there is nothing for a line to say it happened *to*.
        # Everything from here down logs, as `Instance.call` does, an unknown name
        # included: a host naming a tool an added world does not have is worth the
        # same line an agent's mistake gets -- and so, for want of anywhere else to
        # put it, does the cross-thread refusal below, which needs the lock in hand
        # before it can tell.
        frame = _live(self._frame)
        instance = frame.instance
        node = _node_of(frame, self._key)
        owner = node
        try:
            owner, registered = _owner_of(node, tool)
            name = registered.name
            with instance._held() as current:
                if current is not frame:
                    # The lock was free because this handle's activation had
                    # already ended: only a handle carried to another thread gets
                    # here, and it is as stale as one used after its call.
                    raise WorldBug(_STALE)
                given = arguments_of(registered, tool, args, arguments)
                call = Call(name, given, registered, node=owner.path)
                result = owner.internal_chain(frame.ctx(owner.key, call), call)
        except BaseException as error:
            instance._log_failure(name, started, error, owner.path, internal=True)
            raise
        instance._log_call(name, started, "ok", owner.path, internal=True)
        return result

    def __repr__(self) -> str:
        return f"<WorldHandle {self._key[0].name}>"

    def _runtime(self) -> NodeRuntime:
        return _live(self._frame).instance._runtime[self._key]


def _owner_of(node: Node, tool: str | Callable[..., Any]) -> tuple[Node, Tool]:
    """Which node of `node`'s subtree owns the tool a host named, and the tool itself.

    A name is looked up in `node`'s own registry and nowhere else: a host reaches
    an added world's tools by the names that world gave them, and a name deeper in
    the tree belongs to whichever world declared it (architecture section 8.2).

    A function is looked up in every world of the subtree, because there is no
    name to collide and a reference says exactly which tool is meant; what is left
    for the handle to say is which *store*, and the subtree of an account is that
    account all the way down. Two answers is the one thing this cannot resolve --
    a world added twice beneath this one, or one that registered the function as
    two tools -- and the caller is sent one level further in, or to a name.
    """
    if isinstance(tool, str):
        registered = node.world.tools.get(tool)
        # Control tools are the eval harness's, dispatched around the chain with
        # the instance itself in hand; no part of a world's own surface. They are
        # not in `tools_by_fn` either, so the reference path needs no such check.
        if registered is None or registered.control:
            raise UnknownTool(tool)
        return node, registered
    found = [
        (owner, registered)
        for owner in _subtree(node)
        for registered in owner.world.tools_by_fn.get(tool, ())
    ]
    if len(found) == 1:
        return found[0]
    if not found:
        raise WorldBug(
            f"{name_of(tool)} is not a tool of world {node.world.name!r} or anything it adds"
        )
    where = ", ".join(f"{registered.name} at {owner.path}" for owner, registered in found)
    raise WorldBug(
        f"{name_of(tool)} names more than one tool of world {node.world.name!r} and what it "
        f"adds ({where}); go one level in, to the handle of the world that owns the one you "
        f"mean (ctx.worlds.<name>.worlds.<name>), or call it there by its own name"
    )


def _subtree(node: Node) -> Iterator[Node]:
    """`node` and every node it adds, transitively, each once.

    Breadth-first over a directed acyclic graph, so the seen set is what keeps a
    node two routes reach from being visited twice. A `Node` is hashed by identity,
    which is the identity the tree is built on.
    """
    seen = {node}
    queue = deque([node])
    while queue:
        current = queue.popleft()
        yield current
        for child in current.added.values():
            if child not in seen:
                seen.add(child)
                queue.append(child)


def ctx_for(ctx: Ctx[Any], key: NodeKey, call: Call) -> Ctx[Any]:
    """The context one layer of a chain runs with: the one belonging to its own node.

    Every layer of a chain is paired with a node at the seal, and runs with that
    node's `db`, `state`, `ids` and `worlds` (architecture section 7.6). A layer
    paired with the node the incoming context already belongs to keeps that
    context -- rebound to `call` if a middleware above passed a new one -- so a
    leaf world's chain allocates nothing here and a middleware that passed a
    modified context down still has it honoured.

    A context that belongs to no activation is kept as it is, whatever node the
    layer names. That is a context nobody in the framework builds: every one it
    dispatches with came from a `Frame`. It is the one a *caller* builds, to drive
    a chain with no instance behind it, and keeping that possible is what lets the
    call path be tested with nothing but a `Ctx`.
    """
    worlds = ctx.worlds
    if not isinstance(worlds, Worlds) or worlds._frame is None or worlds._key == key:
        return rebind(ctx, call)
    return _bound(worlds)[0].ctx(key, call)


def _live(frame: Frame) -> Frame:
    if not frame.live:
        raise WorldBug(_STALE)
    return frame


def _bound(worlds: Worlds) -> tuple[Frame, NodeKey]:
    """The activation and node a `Worlds` names, or why it names neither."""
    frame, key = worlds._frame, worlds._key
    if frame is None or key is None:
        raise WorldBug(
            "ctx.worlds is not bound to a call on an instance: this is a context the framework "
            "did not hand out, or one kept past the call it belongs to"
        )
    return _live(frame), key


def _node_of(frame: Frame, key: NodeKey) -> Node:
    """The node this handle names, in the instance's *current* composition.

    Read from the live seal rather than from the tree the instance was created
    with, so a middleware or a tool registered after the instance exists reaches a
    nested call exactly as it reaches an agent-initiated one. The instance's node
    *set* is pinned, and `_current_composition` is what refuses a tree that has
    grown a node since.
    """
    return frame.instance._current_composition().by_key[key]


# One instance, shared by every node's template context: it holds no frame and no
# key, so there is nothing in it to tell two of them apart.
_UNBOUND = Worlds()


def unbound() -> Worlds:
    """The `worlds` of a context that belongs to no activation. Every access raises.

    A node's template context carries one, so a context that escaped the framework
    -- kept from a call that returned, or built by hand -- fails where it reaches
    for another world rather than silently reaching into whatever instance is
    current.
    """
    return _UNBOUND
