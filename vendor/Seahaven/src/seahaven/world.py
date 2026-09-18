"""The `World`: a name, a schema, and everything registered against them.

One `World` per world package, built at import time in the package's `world.py`,
and the object every tool module imports to register against. It owns the tool
registry, the middleware list, the startup hooks and the state formats, and it is
where a mistake in any of them is found: registration validates immediately and
fails with a `WorldBug` naming what is wrong.

Registration is open for the life of the world. The registry and the middleware
chain are read at call time, so a tool or a middleware registered after instances
exist applies to them from their next call. Import-time registration is the
convention the scaffold encourages, not a rule enforced here; registering while
calls are in flight is unsupported.
"""

import hashlib
import importlib
import importlib.resources
import inspect
import re
import sys
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from importlib.resources.abc import Traversable
from importlib.resources.readers import MultiplexedPath
from pathlib import Path
from types import MappingProxyType
from typing import Any, Concatenate, overload

import apsw

from seahaven import control
from seahaven.call import Handler, Middleware
from seahaven.composition import (
    NAME,
    RESERVED_NODE_NAMES,
    AddedWorld,
    Composition,
    bump,
    epoch,
    resolve,
)
from seahaven.ctx import Ctx
from seahaven.db import SCHEMA_CHECK_CLOCK, SCHEMA_CHECK_SEED, build_blank
from seahaven.errors import WorldBug
from seahaven.fixtures import Fixture, load_all
from seahaven.instances import Instance, InstanceManager, calling
from seahaven.names import NAME_RULE, why_not_a_name
from seahaven.state import BUILTIN_FORMATS, BUILTIN_PREFIX, Formatter, check_format_name
from seahaven.tool import Tool

__all__ = [
    "CONTROL_TOOL_NAMES",
    "DDL_DOES_NOT_EXECUTE",
    "RESERVED_TOOL_NAMES",
    "Handler",
    "Middleware",
    "RegisteredStartupHook",
    "StartupHook",
    "World",
    "sql_files",
]

# `Handler` and `Middleware` are defined where the chain is built, in `call.py`,
# and re-exported here: `components/world_and_dispatch.md` §1 lists all three
# aliases with the `World` they describe, and this is where a world author looks
# for the shape its middleware has to have.
type StartupHook = Callable[..., None]

# Reserved by OpenEnv: `reset`, `step`, `state` and `close` are the environment's
# own verbs, and a tool by one of those names could not be called over the wire.
RESERVED_TOOL_NAMES = frozenset({"close", "reset", "state", "step"})

# The framework's own tool (`control.py`). It is registered on every world,
# bypasses the chain and is never listed; a world registering its name is refused
# whether or not it is registered yet.
CONTROL_TOOL_NAMES = frozenset({"controller_run_sql"})

# `reset`'s own arguments, which a startup hook therefore cannot take.
RESET_ARGUMENTS = frozenset({"fixture", "now", "seed", "state_format"})

FIXTURES_DIRNAME = "fixtures"
SQL_SUFFIX = ".sql"

# What `_prove_the_ddl_executes` says when SQLite refuses a world's schema. Named
# here because `seahaven check` has to tell that failure from every other
# `SeahavenError` an import can raise -- the first is SH104, the rest are SH501 --
# and it should read the framework's own constant rather than match a sentence it
# does not own.
DDL_DOES_NOT_EXECUTE = "has DDL that does not execute"

_POSITIONAL = (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
_WHITESPACE = re.compile(r"\s+")
# Both separators, always: `sql_files` names a directory inside a package, and a
# package is read on every platform whatever the one it was written on.
_SEPARATOR = re.compile(r"[/\\]")


@dataclass(frozen=True)
class RegisteredStartupHook:
    """A startup hook and the keyword arguments it accepts.

    Callable, so `world.startup_hooks` is a sequence of hooks rather than a
    sequence of records about them; `accepts` and `takes_var_kwargs` are what
    instance creation reads to give each hook the `reset` arguments it asked for
    and no others.
    """

    fn: StartupHook
    accepts: frozenset[str]
    takes_var_kwargs: bool

    def __call__(self, ctx: Ctx, **kwargs: Any) -> None:
        self.fn(ctx, **kwargs)


class World:
    """A world: its identity, its schema, and what is registered against it."""

    def __init__(
        self,
        name: str,
        version: str,
        schema: str,
        *,
        state_format: str | None = None,
        description: str | None = None,
        fixtures_dir: Path | str | None = None,
        work_dir: Path | str | None = None,
        untracked_tables: Sequence[str] = (),
    ) -> None:
        _check_name(name)
        # Required, and spelled with a default so that leaving it out is this
        # refusal rather than a `TypeError` naming a parameter: a world author
        # meeting it for the first time needs the built-in names, and a missing
        # argument is where they are worth saying (`functional_spec.md` §5).
        #
        # `pinned_state_format` and not `state_format`: the registration method
        # below is `world.state_format(...)`, which is the spelling a world
        # author writes (`functional_spec.md` §6), and one name cannot be both a
        # string and a decorator. (`architecture.md` §5.2 gave it to both.)
        self.pinned_state_format = _checked_state_format(name, state_format)
        self.name = name
        self.version = version
        self.schema = schema
        self.schema_hash = _schema_hash(schema)
        _prove_the_ddl_executes(name, schema)
        # The one-line description the OpenEnv metadata publishes, and nothing
        # else reads it. A free string, deliberately unvalidated: unlike `name`
        # it never becomes a path, a filename or an identifier, so there is
        # nothing for a rule to protect. `None` -- and, at publication, a string
        # that is blank -- means the fallback, `Seahaven world <name>`.
        self.description = description
        self.fixtures_dir = (
            Path(fixtures_dir)
            if fixtures_dir is not None
            # The module that called `World(...)`, which is where the world
            # package is, and the only thing the derivation has to go on.
            else _derive_fixtures_dir(sys._getframe(1).f_globals.get("__file__"))
        )
        # `None` means the default: a per-process directory under the system
        # temporary directory, resolved when the first instance is made. A
        # directory given here is the caller's and is never swept.
        self.work_dir = Path(work_dir) if work_dir is not None else None
        self.untracked_tables = tuple(untracked_tables)
        self._tools: dict[str, Tool] = {}
        # The registry inverted, for `ctx.worlds.<name>.call(fn)`. Multi-valued
        # because one function may be registered as two tools -- a factory called
        # twice with two names -- and a map that kept one of them would send a
        # call by reference to whichever the registry happened to hold last.
        self._tools_by_fn: dict[Callable[..., Any], tuple[Tool, ...]] = {}
        self._middlewares: list[Middleware] = []
        self._startup_hooks: list[RegisteredStartupHook] = []
        self._added_worlds: list[AddedWorld] = []
        self._state_formats: dict[str, Formatter] = {}
        # The registration epoch and the tree sealed at it, as one attribute so
        # that reading the pair is one load and cannot tear. `None` until
        # something asks: a world that is only imported never walks its own tree,
        # and a seal that failed is retried rather than remembered.
        self._seal: tuple[int, Composition] | None = None
        self._seal_lock = threading.Lock()
        # Made on the first instance, not here: a world that is only imported --
        # to be linted, to have its tools listed, to be scaffolded against --
        # never touches the working directory at all.
        self._manager: InstanceManager | None = None
        self._manager_lock = threading.Lock()
        # The framework's own tool, on every world and before anything the world
        # registers: `Instance.call` reaches it through the registry like any
        # tool, `Instance.tools()` filters it out of the listing, and a world
        # that registers its name is refused by `_add`.
        for tool in control.TOOLS:
            # A world nobody holds yet cannot be in anyone's tree, so registering
            # the framework's own tool on it invalidates no sealed
            # composition. Without this every `World(...)` anywhere in a process
            # would reseal every other world on its next use.
            self._add(tool, invalidates_seals=False)

    @property
    def tools(self) -> Mapping[str, Tool]:
        """The registry, in registration order. Read-only: register through `tool`."""
        return MappingProxyType(self._tools)

    @property
    def tools_by_fn(self) -> Mapping[Callable[..., Any], tuple[Tool, ...]]:
        """The registry by the function each tool was built from, in registration order.

        What `ctx.worlds.<name>.call(fn)` resolves through, over every world in
        the handle's subtree: *every* tool of this world, contributed to a host's
        surface or filtered out of it, because the allow and block lists shape
        what an agent sees and host code can call everything.

        The framework's own control tool is not in it. It is no part of a
        world's surface -- never contributed, and refused by name by both `call`
        paths -- so there is nothing for a reference to it to reach.
        """
        return MappingProxyType(self._tools_by_fn)

    @property
    def middlewares(self) -> Sequence[Middleware]:
        """The middleware, outermost first."""
        return tuple(self._middlewares)

    @property
    def startup_hooks(self) -> Sequence[RegisteredStartupHook]:
        """The startup hooks, in registration order."""
        return tuple(self._startup_hooks)

    @property
    def chain(self) -> Handler:
        """What an agent-initiated call to one of this world's own tools descends.

        The root node's chain, which for a world that adds nothing is this world's
        middlewares and `invoke` -- the chain it has always been. A contributed
        tool has its own, on the node that owns it, because the route to it runs
        through more worlds than this one.
        """
        return self.composition().root.agent_chain

    @property
    def added_worlds(self) -> Sequence[AddedWorld]:
        """The worlds this one adds, in `add_world` order. Read-only."""
        return tuple(self._added_worlds)

    @property
    def accepted_startup_kwargs(self) -> frozenset[str]:
        """Every keyword argument some startup hook names.

        A hook taking `**kwargs` accepts anything, and instance creation checks
        `takes_var_kwargs` for that; this set is the named ones only.
        """
        return frozenset().union(*(hook.accepts for hook in self._startup_hooks))

    # Three overloads, so that a registered tool keeps the type it was written
    # with: `@world.tool` hands the function back as itself and a factory's `Tool`
    # comes back parameterised, which is what `inst.call(fn, ...)` and
    # `ctx.worlds.<name>.call(fn, ...)` read their arguments and their result from
    # (architecture section 8.1). Without them every tool in every world is an
    # untyped callable and `R` is `Any` everywhere.
    # No options on this one: a factory built the tool's schema and argument model
    # from the options *it* was given, and `_register_tool` refuses any passed
    # here. Writing the overloads is where that becomes a checker error rather
    # than a `WorldBug` at import.
    @overload
    def tool[**P, R](self, obj: Tool[P, R], /) -> Tool[P, R]: ...
    @overload
    def tool[**P, R](
        self,
        obj: Callable[Concatenate[Ctx[Any], P], R],
        /,
        *,
        name: str | None = None,
        description: str | None = None,
        transaction: bool | None = None,
    ) -> Callable[Concatenate[Ctx[Any], P], R]: ...
    @overload
    def tool[**P, R](
        self,
        *,
        name: str | None = None,
        description: str | None = None,
        transaction: bool | None = None,
    ) -> Callable[
        [Callable[Concatenate[Ctx[Any], P], R]], Callable[Concatenate[Ctx[Any], P], R]
    ]: ...

    def tool(
        self,
        obj: Callable[..., Any] | Tool | None = None,
        /,
        *,
        name: str | None = None,
        description: str | None = None,
        transaction: bool | None = None,
    ) -> Any:
        """Register a tool, as a decorator or as a call.

        `transaction` defaults to `True`; it is spelled `None` here so that a
        `Tool` from a factory, which decided its own, can tell an option that was
        passed from one that was not.
        """
        if obj is None:
            return lambda fn: self._register_tool(
                fn, name=name, description=description, transaction=transaction
            )
        return self._register_tool(obj, name=name, description=description, transaction=transaction)

    def middleware(self, obj: Middleware | None = None, /) -> Any:
        """Register a middleware, as a decorator or as a call. Order is outermost first."""
        if obj is None:
            return self._register_middleware
        return self._register_middleware(obj)

    def instance_startup(self, obj: StartupHook | None = None, /) -> Any:
        """Register a hook run once per instance, before its first call."""
        if obj is None:
            return self._register_startup_hook
        return self._register_startup_hook(obj)

    def state_format(self, name: str, /) -> Callable[[Formatter], Formatter]:
        """Register a state format of this world's own, by name.

        ```python
        @world.state_format("acme.state/1")
        def acme_state(world: seahaven.World, instance: seahaven.Instance | None) -> dict:
            ...
        ```

        The function answers the value of `state` and nothing else; the framework
        writes the envelope around it, so a custom format can neither omit
        provenance nor misspell it. It is called with `None` for the instance
        before the first `reset` over OpenEnv, and one that raises on `None`
        makes the pre-reset state a `WorldBug`, which is the author's contract
        to keep.

        A format is resolved against the **root** of an instance and only the
        root, so what this registers serves this world when it is the root of a
        tree or of its own instances, and is not inherited by a world that adds
        it (`functional_spec.md` §6).

        The name is checked here, where it is written, and the registration when
        the decorator is applied. `seahaven.` is the framework's prefix and is
        refused: a world that took one of those names would collide with a
        format a later release publishes.
        """
        check_format_name(name)
        if name.startswith(BUILTIN_PREFIX):
            raise WorldBug(
                f"state format {name!r} uses the prefix {BUILTIN_PREFIX!r}, which is reserved for "
                f"Seahaven's own formats ({_builtin_names()}); name yours after your own family"
            )

        def register(fn: Formatter) -> Formatter:
            if not callable(fn):
                raise WorldBug(f"a state formatter is a function, not {type(fn).__name__}")
            if name in self._state_formats:
                raise WorldBug(f"state format {name!r} is registered twice")
            # No `bump()`, deliberately, unlike every other registration verb: a
            # format is not part of a sealed composition (composition
            # `functional_spec.md` §2), and an added world's registry is never
            # consulted, so nothing about a tree changes when one is added.
            self._state_formats[name] = fn
            return fn

        return register

    def resolve_state_format(self, name: str) -> Formatter:
        """The formatter a name answers to: a built-in, or one this world registered.

        Asked of the root of an instance and of nothing else, which is why an
        added world's registrations never answer (`functional_spec.md` §6).
        """
        formatter = BUILTIN_FORMATS.get(name) or self._state_formats.get(name)
        if formatter is None:
            registered = ", ".join(sorted(self._state_formats)) or "none"
            raise WorldBug(
                f"world {self.name!r} has no state format {name!r}; the built-in formats are "
                f"{_builtin_names()}, and this world registers {registered}"
            )
        return formatter

    def add_world(
        self,
        world: World,
        /,
        *,
        name: str | None = None,
        store: str | None = None,
        tool_prefix: str | None = None,
        tool_allow_list: Sequence[str] | None = None,
        tool_block_list: Sequence[str] | None = None,
        startup: Mapping[str, Any] | None = None,
    ) -> None:
        """Add another world to this one: its tools and its store, under a name of this world's.

        The fourth registration verb, and the only one that is call-only: there is
        nothing to decorate. `name` is this world's internal identity for the added
        world and is never agent-visible; `store` names the account scope it and
        its whole subtree belong to, `None` keeping this world's own, which is what
        shares an account. `tool_prefix`, `tool_allow_list` and `tool_block_list`
        shape what the *agent* sees; this world's own code can call every tool of
        an added world whether it is contributed or not. `startup` binds keyword
        arguments to that node's startup hooks at every instance creation.

        Only what is knowable from the two worlds in hand is checked here. Every
        whole-tree property -- a name collision after prefixing, a list naming a
        tool that does not exist, the attach bound -- belongs to the seal, and is
        raised from the first use of the tree (`composition()`).
        """
        added_name = world.name if name is None else name
        _check_added_name(world, added_name)
        if store is not None and (not isinstance(store, str) or not store.strip()):
            # `None` is "the adder's own scope" and is the only way to say it. A
            # blank string is not that: it opens a scope of its own, named with
            # nothing, and travels into a fixture's sidecar as that node's scope.
            raise WorldBug(
                f"add_world({world.name}, name={added_name!r}): store={store!r} is not a scope "
                f"name; give a name, or None to stay in this world's own scope"
            )
        if tool_allow_list is not None and tool_block_list is not None:
            raise WorldBug(
                f"add_world({world.name}, name={added_name!r}): tool_allow_list and "
                f"tool_block_list name the agent's surface two different ways; give one"
            )
        for which, listed in (
            ("tool_allow_list", tool_allow_list),
            ("tool_block_list", tool_block_list),
        ):
            # A `str` is a `Sequence[str]`, so a type checker cannot catch this
            # one: the list would be exploded into one "tool name" per character
            # and reported at the seal as six tools the added world does not have.
            if isinstance(listed, str):
                raise WorldBug(
                    f"add_world({world.name}, name={added_name!r}): {which} is a string, which "
                    f"would name one tool per character; give a list of names"
                )
        if any(existing.name == added_name for existing in self._added_worlds):
            raise WorldBug(
                f"add_world({world.name}, name={added_name!r}): {self.name!r} already adds a "
                f"world under that name; a name is one world's identity in its host"
            )
        added = AddedWorld(
            world=world,
            name=added_name,
            store=store,
            tool_prefix=tool_prefix,
            tool_allow_list=None if tool_allow_list is None else tuple(tool_allow_list),
            tool_block_list=None if tool_block_list is None else tuple(tool_block_list),
            # Copied, so a caller that keeps and mutates the mapping it passed
            # cannot change the composition after the fact.
            startup=MappingProxyType(dict(startup or {})),
        )
        _check_bound_startup(added)
        _check_for_a_cycle(self, added)
        self._added_worlds.append(added)
        # Every registration verb but `state_format` bumps the epoch (see
        # `_register_startup_hook`).
        bump()

    def composition(self) -> Composition:
        """This world's sealed tree: its nodes, their paths, and the flat tool surface.

        Sealed lazily and cached until the next registration anywhere in the
        process (`composition.bump`). A leaf world seals to one node, so every
        caller reads the tree whether or not the world adds anything.

        Every dispatched call reads this, so the steady state is one load and one
        integer compare and takes no lock at all. The lock is for the resealing,
        where two threads would otherwise each walk the tree; it is dropped again
        before anything is returned, and a thread that loses the race reseals
        rather than waits.
        """
        current = epoch()
        seal = self._seal
        if seal is not None and seal[0] == current:
            return seal[1]
        with self._seal_lock:
            seal = self._seal
            if seal is None or seal[0] != current:
                # Stored only on success: a seal that raised is retried on the next
                # use, so the author sees the error again until they fix it.
                seal = (current, resolve(self))
                self._seal = seal
            return seal[1]

    def instance(
        self,
        fixture: str | None = None,
        *,
        seed: int | None = None,
        now: str | datetime | None = None,
        state_format: str | None = None,
        **startup_kwargs: Any,
    ) -> Instance:
        """Make a live instance: a private copy of a fixture, or a blank one.

        `now` sets a blank instance's clock and is refused with a fixture, which
        carries its own. `state_format` answers in another of this world's
        formats for this instance alone, in place of the world's pin. Everything
        else keyword is passed to the startup hooks that named it. The instance
        is a context manager and leaving the block destroys it.

        Never from inside a tool call: a handler that wants another world reaches
        it through `ctx.worlds`, and a world that made its own instance would be
        writing to a store no eval can see.
        """
        if calling():
            raise WorldBug(
                "instances cannot be created from inside a tool call; reach added worlds "
                "through ctx.worlds"
            )
        return self._instances().create(
            fixture,
            seed=seed,
            now=now,
            state_format=state_format,
            startup_kwargs=startup_kwargs,
        )

    def fixtures(self) -> list[Fixture]:
        """Every fixture in the world's fixtures directory, by id.

        A world with no fixtures directory has no fixtures; that is not an error.
        """
        return sorted(load_all(self.fixtures_dir).values(), key=lambda fixture: fixture.id)

    def __copy__(self) -> World:
        """This world, to be pointed somewhere else: `copy.copy(world)`, then set an attribute.

        A world is imported once and everything in the process holds the same
        object, so moving `fixtures_dir` on it -- what a test that rebuilds the
        committed fixtures into a temporary directory wants -- moves it for every
        other caller too, for the rest of the run. A copy is how that is said
        locally.

        A copy is a *snapshot of the five registries*, taken at copy time and
        severed in both directions: the copy does not see a tool, a middleware, a
        startup hook, an added world or a state format registered on the original
        afterwards, so its `chain` is what its own snapshot seals to, and nothing
        registered on the copy reaches back. That is the one place a world stops
        being open for registration for the life of the process, so take the copy
        after import-time registration is done.

        The instance manager is deliberately *not* carried over: a manager hands
        the world it was made for to every instance it makes, and that world is
        the one an instance freezes into -- so a copy that inherited one would
        quietly freeze back into the directory the copy was taken from, which is
        the one thing a copy exists to avoid.
        """
        twin = object.__new__(type(self))
        twin.__dict__.update(self.__dict__)
        twin._tools = dict(self._tools)
        twin._tools_by_fn = dict(self._tools_by_fn)
        twin._middlewares = list(self._middlewares)
        twin._startup_hooks = list(self._startup_hooks)
        twin._added_worlds = list(self._added_worlds)
        twin._state_formats = dict(self._state_formats)
        # A copy is a distinct `World` object and therefore a distinct node, so it
        # reseals from its own snapshot on first use. Copy the *root* to relocate
        # its fixtures, never a world something else adds: a host that added the
        # original does not see the copy.
        twin._seal = None
        twin._seal_lock = threading.Lock()
        twin._manager = None
        twin._manager_lock = threading.Lock()
        return twin

    def _instances(self) -> InstanceManager:
        with self._manager_lock:
            if self._manager is None:
                self._manager = InstanceManager(self)
            return self._manager

    def _register_tool(
        self,
        obj: Callable[..., Any] | Tool,
        *,
        name: str | None,
        description: str | None,
        transaction: bool | None,
    ) -> Any:
        if isinstance(obj, Tool):
            if name is not None or description is not None or transaction is not None:
                # The factory built the schema and the argument model from the
                # options it was given; replacing one here would leave the tool
                # disagreeing with its own schema.
                raise WorldBug(
                    f"tool {obj.name!r} was built by a factory: pass name=, description= or "
                    f"transaction= to the factory, not to world.tool()"
                )
            self._add(obj)
            return obj
        if not callable(obj):
            raise WorldBug(f"a tool is a function or a Tool, not {type(obj).__name__}")
        self._add(
            Tool.from_function(
                obj,
                name=name,
                description=description,
                transaction=True if transaction is None else transaction,
            )
        )
        # The function itself, so a decorated tool stays an ordinary callable.
        return obj

    def _add(self, tool: Tool, *, invalidates_seals: bool = True) -> None:
        # OpenEnv's verbs are refused whoever is registering: they are the wire's,
        # and no flag of this framework's can reclaim them.
        if tool.name in RESERVED_TOOL_NAMES:
            raise WorldBug(
                f"tool {tool.name!r} uses a name OpenEnv reserves for the environment "
                f"({', '.join(sorted(RESERVED_TOOL_NAMES))})"
            )
        # Before the duplicate check, which every world would hit instead: the
        # control tool is registered here at construction, so a world tool by its
        # name is already taken. What is wrong with it is that the name is the
        # framework's, and that is what it is told.
        if tool.name in CONTROL_TOOL_NAMES and not tool.control:
            raise WorldBug(f"tool {tool.name!r} uses the name of a control tool")
        if tool.name in self._tools:
            raise WorldBug(f"tool {tool.name!r} is registered twice")
        # Everything that can refuse this tool has refused it by now: nothing
        # below leaves a world half-registered.
        _check_the_function_can_key_the_registry(tool)
        self._tools[tool.name] = tool
        if not tool.control:
            self._tools_by_fn[tool.fn] = (*self._tools_by_fn.get(tool.fn, ()), tool)
        if invalidates_seals:
            # Every registration verb but `state_format` bumps the epoch (see
            # `_register_startup_hook`).
            bump()

    def _register_middleware(self, obj: Middleware) -> Middleware:
        _check_middleware_shape(obj)
        self._middlewares.append(obj)
        # The chains are closures over the middleware each node had when the tree
        # was sealed, so this invalidates the seal and the next use rebuilds them.
        # Every registration verb but `state_format` bumps the epoch (see
        # `_register_startup_hook`).
        bump()
        return obj

    def _register_startup_hook(self, obj: StartupHook) -> StartupHook:
        self._startup_hooks.append(_as_startup_hook(obj))
        # Every registration verb but `state_format` bumps the epoch, and that
        # one is deliberate rather than forgotten: a format is not part of a
        # composition's seal and an added world's formats are never consulted.
        bump()
        return obj


def sql_files(package: str | None, directory: str) -> str:
    """The DDL a world is built from: every `*.sql` in a package directory, in order.

    `schema=seahaven.sql_files(__package__, "schema")` is how a world package
    states its schema. The files are read in sorted filename order -- which is
    what the `001_`, `002_` prefix convention is for -- and joined with newlines,
    so the result is one script and the order in it is the order on disk.

    Read through `importlib.resources`, not from `__file__`, so a world works
    the same installed as a wheel, from a source tree, or from a zip. That
    promise is the reason `directory` has to be an ordinary path inside the
    package, checked a segment at a time by `_check_schema_directory`: a
    filesystem and a zip disagree about every other spelling, so a world that
    used one would build from a checkout and fail once installed. Symlinks are
    refused for the same reason and in the same words -- a zip holds none, and a
    checkout on a machine without symlink support holds a text file where one
    should be.

    Everything a world author can get wrong here is a `WorldBug` that names the
    package, the directory and, where there is one, the file: a directory that is
    missing, is a file, is unreadable, or holds no `*.sql`; a `*.sql` name that is
    a directory rather than a file; a file that is not UTF-8 text. An empty schema
    builds a database with no tables perfectly happily, and any of these would
    otherwise be found much later, as a missing table -- or, for the two that
    raise `OSError` and `UnicodeDecodeError`, as a traceback out of an import with
    no world in it.

    `package` must be the name of a package, not of a module inside one:
    `__package__`, never `__name__`. A module has no directory of its own for
    `importlib.resources` to anchor to, and the answer it gives for one is an
    object whose failure names neither the world nor the file.

    It is typed to accept `None` because `__package__` -- which is what every
    world passes -- is typed `str | None`, and a world should not have to silence
    a type checker to write the one line the docs give it. `None` is refused at
    runtime, and refusing it is the point: `importlib.resources.files(None)` does
    not fail, it resolves the *caller's* package, which is this framework, and the
    world would be built out of whatever `seahaven/<directory>` happened to hold.
    """
    if not isinstance(package, str) or not package:
        raise WorldBug(
            f"sql_files needs the name of a package, not {package!r}; "
            f"pass __package__ from a module inside the world's package"
        )
    # Every dotted part a Python name, for the same reason `directory` is
    # checked a segment at a time: `import_module` raises `TypeError` and not
    # `ImportError` for a name beginning with a dot, because it reads it as a
    # relative import and there is no package here for it to be relative to --
    # so it goes straight past the handler below as a traceback with no world in
    # it. Asked as an allowlist rather than as a list of the spellings that do
    # it, because that list is the one this function has already been wrong
    # about four times.
    if not all(part.isidentifier() for part in package.split(".")):
        raise WorldBug(
            f"sql_files needs the name of a package, not {package!r}; every part of a package "
            f"name is a Python name, as it would have to be to be written as an import statement. "
            f"Pass __package__ from a module inside the world's package"
        )
    segments = _check_schema_directory(package, directory)
    try:
        anchor = importlib.import_module(package)
    except ImportError as error:
        raise WorldBug(f"cannot read schema of package {package!r}: {error}") from error
    if not hasattr(anchor, "__path__"):
        raise WorldBug(
            f"sql_files needs the name of a package, and {package!r} is a module inside one; "
            f"pass __package__ rather than __name__"
        )
    # Joined a segment at a time so that every directory on the way is checked,
    # not just the last one: `schema` may be an ordinary name and still be a
    # symlink to somewhere the package does not ship.
    root = importlib.resources.files(anchor)
    for segment in segments:
        root = root / segment
        _refuse_more_than_one_directory(root, package, directory, segment)
        _refuse_a_symlink(root, package, directory, f"directory {segment!r}")
    if not root.is_dir():
        what = "is a file, not a directory" if root.is_file() else "does not exist"
        raise WorldBug(
            f"schema directory {directory!r} of package {package!r} {what} (looked in {root})"
        )
    try:
        names = _sql_file_names(root, package, directory)
    except OSError as error:
        raise WorldBug(
            f"cannot read schema directory {directory!r} of package {package!r}: {error}"
        ) from error
    if not names:
        raise WorldBug(f"schema directory {directory!r} of package {package!r} holds no *.sql file")
    return "\n".join(_read_sql(root / name, package, directory, name) for name in names)


def _check_schema_directory(package: str, directory: str) -> list[str]:
    """`directory`'s segments, if every one of them is an ordinary name.

    An allowlist, and deliberately not a list of the ways out. The denylist this
    replaced was extended three times, each time because a spelling nobody had
    listed behaved one way on a filesystem and another in a zip -- `..`, then a
    root, then a backslash, then `.` and an empty segment -- and there would have
    been a fourth. Asking what a segment *is* ends that: an ordinary name is
    non-empty, is not `.` or `..`, and holds no `:`.

    The two families it refuses fail differently and are told apart in the
    message. `..`, a leading separator and a drive letter leave the package, and
    read files it does not ship. `.` and an empty segment (`schema/.`,
    `schema//`, `schema/`) do not leave it -- `pathlib` normalises them away and
    the world builds -- but `zipfile.Path.joinpath` is `posixpath.join`, which
    keeps them, so the same world raises from an installed wheel. Both break the
    one promise this function makes, so both are refused here.

    A `:` is refused wherever it appears -- not only in the first segment --
    because `PureWindowsPath` calls neither `"C:schema"` (a drive with no root,
    resolved against that drive's working directory) nor `"\\etc"` (a root with
    no drive) absolute, and both leave the package on Windows; and because
    joining a drive-relative segment *replaces* what came before it rather than
    appending to it, so `PureWindowsPath("schema") / "C:evil"` is `C:evil` and a
    later segment is no safer than the first. The mistake is not the platform
    the author is on.
    """
    if not isinstance(directory, str) or not directory:
        raise WorldBug(
            f"sql_files needs the name of a directory inside package {package!r}, not {directory!r}"
        )
    segments = _SEPARATOR.split(directory)
    for position, segment in enumerate(segments):
        if segment == ".." or ":" in segment or (position == 0 and not segment):
            raise WorldBug(
                f"schema directory {directory!r} of package {package!r} leaves the package at "
                f"{segment!r}; name a directory inside it. A '..', a leading separator or a drive "
                f"letter reads files the package does not ship, and cannot be read at all from a "
                f"zipped wheel"
            )
        if not segment or segment == ".":
            raise WorldBug(
                f"schema directory {directory!r} of package {package!r} is not spelled as a path "
                f"inside it: {segment!r} names nothing. A filesystem drops a '.' and a repeated or "
                f"trailing separator and a zipped wheel keeps them, so a world spelled this way "
                f"builds from a checkout and fails once installed; write the directory names alone"
            )
    return segments


def _refuse_more_than_one_directory(
    entry: Traversable, package: str, directory: str, segment: str
) -> None:
    """One schema directory, in one place, or the promise cannot be kept.

    A namespace package whose portions lie in several `sys.path` entries makes
    `importlib.resources` answer a `MultiplexedPath`: one name standing for two
    or more real directories, whose `iterdir` merges their children. No installed
    wheel can reproduce that -- the portions land in one directory under
    `site-packages` -- so a world whose schema is reached this way is already the
    failure this function exists to prevent, before anything else is asked.

    It is refused rather than merged for a second reason: a `MultiplexedPath` is
    not a `Path` and *is* on a filesystem, so `_refuse_a_symlink` below cannot
    answer for it, and one portion being a symlink out of the package would go
    unseen. Refusing it at every segment is what lets that guard stay an
    `isinstance` test rather than an open question.

    The line is not how many portions the package has. It is whether *this
    segment* resolves to more than one directory, which is what `joinpath`
    answers: a package of two portions whose `schema` exists in only one of them
    joins to a real `Path` and is read, and a package of two portions whose
    `schema` exists in both joins to another `MultiplexedPath` and is refused.
    A one-portion package always joins to a real `Path`, which is why it reads.

    The test is an `isinstance` against a class the standard library does not
    export as API. `importlib.resources.readers.MultiplexedPath` is what
    `importlib.resources` constructs and there is no public predicate for "this
    `Traversable` stands for more than one directory", so the alternative is to
    re-derive the answer from `__path__`, which is guessing at what
    `MultiplexedPath._follow` already decided. If a future Python renames it the
    import fails at import time and every test says so at once, which is the
    right way for this to break.
    """
    if isinstance(entry, MultiplexedPath):
        where = (
            f"schema directory {directory!r}"
            if segment == directory
            else f"segment {segment!r} of schema directory {directory!r}"
        )
        raise WorldBug(
            f"{where} of package {package!r} resolves to more than one directory: {package!r} is "
            f"a namespace package whose portions lie in several sys.path entries, and {segment!r} "
            f"is in more than one of them. Give the package an __init__.py, or put its portions "
            f"in one directory. An installed wheel merges those entries into a single directory, "
            f"so a world reached this way builds from a checkout and resolves differently once "
            f"installed"
        )


def _refuse_a_symlink(entry: Traversable, package: str, directory: str, what: str) -> None:
    """A symlink is the way out of a package that is left once `..` is refused.

    `is_dir` and `is_file` both follow one, so the guard above would otherwise
    hand an author the workaround as it took away the path. A zip holds no
    symlink and git on a machine without symlink support writes a text file
    where one should be, so a world whose schema is reached through one builds
    in the tree it was written in and nowhere else.

    The `isinstance` test is what makes this answerable rather than a guess. Of
    the `Traversable` kinds that reach here, `Path` is on a filesystem and is
    asked; a zip member cannot be a symlink, so leaving it alone is not a gap.
    The third kind, `MultiplexedPath`, is neither -- it is on a filesystem and is
    not a `Path` -- so it would be skipped silently and a symlinked portion would
    go unseen.

    The precondition is that no `MultiplexedPath` reaches here at all, and it is
    established differently at the two call sites. On the way down to the schema
    directory, `_refuse_more_than_one_directory` is called on the same entry
    immediately before this one, so the pair runs together. On the `*.sql`
    children, nothing is called first and nothing needs to be: the root those
    children came from is already known not to be multiplexed, and `iterdir` on
    a `Path` or a zip yields children of its own kind. That second site is sound
    only *because* of the first -- `MultiplexedPath._follow` can answer a
    multiplexed child -- so relaxing the root refusal would silently reopen the
    symlinked-child gap here, not only the one above.
    """
    if isinstance(entry, Path) and entry.is_symlink():
        raise WorldBug(
            f"schema {what} of package {package!r} in {directory!r} is a symlink, which leaves the "
            f"package; name a directory inside it. A zipped wheel holds no symlink, so a world "
            f"whose schema is reached through one builds from a checkout and fails once installed"
        )


def _sql_file_names(root: Traversable, package: str, directory: str) -> list[str]:
    """The `*.sql` files of a schema directory, sorted, and nothing else in it.

    A `*.sql` entry that is not a file -- a directory named `001_core.sql` is the
    way to make one -- is refused by name rather than skipped: skipping it would
    report "holds no *.sql file" about a directory whose listing plainly shows
    one, which sends the author looking in the wrong place.
    """
    names = []
    for child in sorted(root.iterdir(), key=lambda child: child.name):
        if not child.name.endswith(SQL_SUFFIX):
            continue
        _refuse_a_symlink(child, package, directory, f"file {child.name!r}")
        if not child.is_file():
            raise WorldBug(
                f"schema directory {directory!r} of package {package!r} holds {child.name!r}, "
                f"which is not a file; everything named *.sql in it is read as DDL"
            )
        names.append(child.name)
    return names


def _read_sql(path: Traversable, package: str, directory: str, name: str) -> str:
    """One schema file as text, or a `WorldBug` that says which file and why."""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise WorldBug(
            f"schema file {name!r} in directory {directory!r} of package {package!r} "
            f"could not be read as UTF-8 text: {error}"
        ) from error


def _check_the_function_can_key_the_registry(tool: Tool) -> None:
    """A tool is keyed by its function as well as by its name.

    `tools_by_fn` is what `ctx.worlds.<name>.call(fn)` resolves through, so a
    function that cannot be a dictionary key cannot be a tool. Said here rather
    than left as a bare `TypeError`: `from_function` accepts any callable, and an
    ordinary `@dataclass` with a `__call__` is one.

    Control tools are not in that map, and are checked all the same: they are the
    framework's own, so one that could not be is a bug here and not in a world.
    """
    try:
        hash(tool.fn)
    except TypeError as error:
        raise WorldBug(
            f"tool {tool.name!r} is built from a callable that cannot be hashed, and a tool is "
            f"keyed by the function it was built from as well as by its name"
        ) from error


def _check_added_name(world: World, name: str) -> None:
    """An added world's name is a path segment, a file name and an attached schema name.

    Identifier-like and lowercase for the first two. `__` is refused for the
    third: a node's schema name is its path with `/` replaced by `__`, so a
    segment holding `__` would let `a__b` and `a/b` name one schema. `main` and
    `temp` are SQLite's own schema names and could never be attached.
    """
    if not isinstance(name, str) or not NAME.fullmatch(name) or "__" in name:
        raise WorldBug(
            f"add_world({world.name}, name={name!r}): a name is lowercase letters, digits and "
            f"single underscores, starting with a letter. It becomes a path segment, a file name "
            f"and an attached schema name, which is why '__' is not one of them"
        )
    if name in RESERVED_NODE_NAMES:
        raise WorldBug(
            f"add_world({world.name}, name={name!r}): {name!r} is one of SQLite's own schema "
            f"names ({', '.join(sorted(RESERVED_NODE_NAMES))}), so a store could not be attached "
            f"under it"
        )


def _check_bound_startup(added: AddedWorld) -> None:
    """`startup=` binds keywords to the added world's *own* hooks, so it names them.

    Its own and not its subtree's: bound keywords are delivered to this node's
    hooks and nothing deeper. The added world's registry is complete by now -- it
    was imported before this host could name it.
    """
    if not added.startup:
        return
    hooks = added.world.startup_hooks
    if any(hook.takes_var_kwargs for hook in hooks):
        return
    accepted = added.world.accepted_startup_kwargs
    unknown = sorted(set(added.startup) - accepted)
    if unknown:
        names = ", ".join(sorted(accepted)) or "nothing"
        raise WorldBug(
            f"add_world({added.world.name}, name={added.name!r}): startup names "
            f"{', '.join(repr(keyword) for keyword in unknown)}, which no startup hook of "
            f"{added.world.name!r} accepts; its hooks accept: {names}"
        )


def _check_for_a_cycle(host: World, added: AddedWorld) -> None:
    """A world may not appear in its own subtree, directly or transitively.

    A self-add is the depth-0 case of the same walk. Without this the node graph
    would not terminate, and no reading of "one store per node" would make sense
    for a world that contains itself.
    """
    pending = [added.world]
    seen: set[World] = set()
    while pending:
        world = pending.pop()
        if world is host:
            raise WorldBug(
                f"add_world({added.world.name}, name={added.name!r}): {host.name!r} is in the "
                f"tree of {added.world.name!r}, so adding it would put the world inside itself"
            )
        if world in seen:
            continue
        seen.add(world)
        pending.extend(inner.world for inner in world.added_worlds)


def _builtin_names() -> str:
    return ", ".join(sorted(BUILTIN_FORMATS))


def _checked_state_format(name: str, state_format: str | None) -> str:
    """The format a world pins, or the refusal that says what a pin is for.

    A built-in name is checked here, at the `World(...)` line. A name outside the
    `seahaven.` prefix is not, and cannot be: a world registers its own formats
    *after* that line runs, so the earliest a custom pin can be resolved is the
    first instance (`functional_spec.md` §5).
    """
    if state_format is None:
        raise WorldBug(
            f"world {name!r} must pin a state format: World(state_format=...) is required, and it "
            f"is what keeps upgrading Seahaven from changing what a running eval saves. The "
            f"built-in formats are {_builtin_names()}"
        )
    check_format_name(state_format)
    if state_format.startswith(BUILTIN_PREFIX) and state_format not in BUILTIN_FORMATS:
        raise WorldBug(
            f"world {name!r} pins state format {state_format!r}, which Seahaven does not publish; "
            f"the built-in formats are {_builtin_names()}"
        )
    return state_format


def _check_name(name: str) -> None:
    """A world's name becomes a directory name, so it has to be one.

    The default working directory is `<root>/<pid namespace>-<pid>/<world name>/`
    and `instances._open_child` hardens the *lookup* of every component of it --
    `O_NOFOLLOW`, an owner check, a `dir_fd` -- on the understanding that what it
    is given is one component. `O_NOFOLLOW` says nothing about `..`, which is not
    a symlink, so nothing below this refuses a name that walks upwards:
    `World("..")` put an instance in the working root beside the per-process
    directories, and `World("../..")` put a live database outside the working root
    entirely, where the sweep never looks and the files stay for ever.

    The rule is `names.why_not_a_name`'s, which `fixtures.check_id` applies to a
    fixture id for the same reasons; it is written down once there. A name is also
    headed for more than a path -- a log line, a sidecar, a URL -- which is the
    other reason it is checked here, where the name is accepted, rather than where
    a directory is made from it.
    """
    reason = why_not_a_name(name)
    if reason is not None:
        raise WorldBug(
            f"not a world name: {name!r}: {reason}. World(name=...) is one directory name: "
            f"{NAME_RULE}."
        )


def _schema_hash(schema: str) -> str:
    """The DDL's identity, insensitive to how it is laid out.

    Whitespace is collapsed so that reformatting the schema does not invalidate
    every fixture frozen from it, while any change to a name, a type or a
    constraint does.
    """
    collapsed = _WHITESPACE.sub(" ", schema).strip()
    return hashlib.sha256(collapsed.encode("utf-8")).hexdigest()


def _prove_the_ddl_executes(name: str, schema: str) -> None:
    """Build the schema in memory, so a world with broken DDL cannot exist.

    The DDL *rules* -- STRICT, primary keys, no wall clock -- belong to
    `seahaven check` and are not applied here: a world under development runs
    long before it lints clean.
    """
    try:
        build_blank(":memory:", schema, clock=SCHEMA_CHECK_CLOCK, seed=SCHEMA_CHECK_SEED).close()
    except apsw.Error as error:
        raise WorldBug(f"world {name!r} {DDL_DOES_NOT_EXECUTE}: {error}") from error


def _derive_fixtures_dir(caller_file: str | None) -> Path:
    """`fixtures/` at the project root, found by walking up from the world's module.

    The project root is the nearest directory holding a `pyproject.toml`. An
    installed wheel has none above it, and `fixtures/` beside the package
    directory is the answer there. This never fails: a world that only makes
    blank instances never reads the directory, and a fixture that cannot be found
    says `World(fixtures_dir=...)` in its message.
    """
    # A `World` built somewhere with no file at all -- a REPL, an `exec` -- has
    # only the working directory to go on, and `fixtures/` under it is the answer
    # there: "beside the package" means nothing when there is no package.
    if caller_file is None:
        return _fixtures_dir_at_project_root(Path.cwd()) or Path.cwd() / FIXTURES_DIRNAME
    package_dir = Path(caller_file).resolve().parent
    return _fixtures_dir_at_project_root(package_dir) or package_dir.parent / FIXTURES_DIRNAME


def _fixtures_dir_at_project_root(start: Path) -> Path | None:
    """`fixtures/` under the nearest directory holding a `pyproject.toml`, walking up."""
    for directory in (start, *start.parents):
        if (directory / "pyproject.toml").is_file():
            return directory / FIXTURES_DIRNAME
    return None


def _check_middleware_shape(obj: Middleware) -> None:
    """A middleware is anything callable as `(ctx, call, next_)`.

    Structural, with nothing to subclass: the check is that the three arguments
    can be passed positionally, and a `*args` middleware satisfies it too.
    """
    try:
        parameters = list(inspect.signature(obj).parameters.values())
    except (TypeError, ValueError) as error:
        raise WorldBug(f"middleware must be callable as (ctx, call, next_): {obj!r}") from error
    if any(p.kind is inspect.Parameter.VAR_POSITIONAL for p in parameters):
        return
    if len([p for p in parameters if p.kind in _POSITIONAL]) < 3:
        raise WorldBug(f"middleware must be callable as (ctx, call, next_): {obj!r}")


def _as_startup_hook(obj: StartupHook) -> RegisteredStartupHook:
    """Record what `reset` arguments a hook accepts, refusing a shape that cannot work."""
    try:
        parameters = list(inspect.signature(obj).parameters.values())
    except (TypeError, ValueError) as error:
        raise WorldBug(
            f"an instance startup hook takes the context and keyword arguments: {obj!r}"
        ) from error
    positional = [p for p in parameters if p.kind in _POSITIONAL]
    variadic_positional = any(p.kind is inspect.Parameter.VAR_POSITIONAL for p in parameters)
    if len(positional) != 1 or variadic_positional:
        raise WorldBug(
            f"an instance startup hook takes the context as its only positional parameter and "
            f"everything else by keyword: {obj!r}"
        )
    for parameter in parameters[1:]:
        if parameter.name in RESET_ARGUMENTS:
            raise WorldBug(
                f"an instance startup hook cannot take {parameter.name!r}: it is reset's own "
                f"argument ({', '.join(sorted(RESET_ARGUMENTS))})"
            )
    return RegisteredStartupHook(
        fn=obj,
        accepts=frozenset(p.name for p in parameters if p.kind is inspect.Parameter.KEYWORD_ONLY),
        takes_var_kwargs=any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters),
    )
