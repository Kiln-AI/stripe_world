"""What a tool is handed, and everything it is allowed to reach for.

One `Ctx` exists per node per instance and carries that node's database, state
and seeded randomness, the instance's clock, and the worlds its node adds. A call
gets a shallow copy of it with `call` set, so a tool sees its own `Call` and never
another's, while `db` and `state` stay the one object the whole node shares.

`Ctx` is generic in `worlds` so that a world can declare its children to a type
checker (`Ctx[CompanyWorlds]`); bare `Ctx` is the same annotation it has always
been, and nothing in the runtime reads the parameter.
"""

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Self, overload

from seahaven.clock import Clock
from seahaven.db import Db
from seahaven.ids import Ids

if TYPE_CHECKING:  # both modules sit above this one; the annotations are all that is needed here
    from seahaven.call import Call
    from seahaven.handles import Worlds

__all__ = ["Ctx", "InstanceInfo"]


@dataclass(frozen=True)
class InstanceInfo:
    """Which instance this is, for a tool that wants to say so.

    `seed` is the derived instance seed, not the `seed=` the caller passed: the
    caller's value is one of two inputs to it, and the derived bytes are what
    actually drove `ctx.ids`.
    """

    id: str
    fixture: str | None
    seed: bytes


@dataclass(frozen=True)
class Ctx[W: Worlds = Worlds]:
    """The node context: `ctx` in every tool, middleware and startup hook."""

    db: Db
    clock: Clock
    ids: Ids
    # A plain dict on purpose. What a world keeps in it -- a principal, a
    # compiled schema, a counter -- and how it types that is the world's
    # business, and a framework model here would only be in the way.
    state: dict[str, Any]
    instance: InstanceInfo
    # No default, deliberately: every context is built by the framework, and one
    # built without this would answer `ctx.worlds` with an `AttributeError`
    # rather than with the framework's own refusal. A context that belongs to no
    # activation gets `handles.unbound()`, whose every access raises.
    worlds: W
    call: Call | None = None

    # Two overloads because only one of the two answers widens. Binding a call
    # leaves `worlds` -- and so the parameter -- exactly as it was, which is what
    # keeps `Ctx[CompanyWorlds]` a `Ctx[CompanyWorlds]` inside that world's own
    # middleware; handing in another activation's `Worlds` is what loses it.
    @overload
    def with_call(self, call: Call | None) -> Self: ...
    @overload
    def with_call(self, call: Call | None, *, worlds: Worlds) -> Ctx[Any]: ...

    def with_call(self, call: Call | None, *, worlds: Worlds | None = None) -> Ctx[Any]:
        """A copy of this context bound to one call, and optionally to one activation.

        Shallow: `db`, `state` and `ids` are the same objects, so a tool writing
        to `ctx.state` writes to the node's state.

        `worlds` is a keyword because the chain, not the context, knows which node
        a layer belongs to and under which activation (architecture section 6.3).
        `call` may be `None`: a startup hook and a `bulk()` block run against a
        live `worlds` with no call at all.
        """
        if worlds is None:
            return replace(self, call=call)
        return replace(self, call=call, worlds=worlds)
