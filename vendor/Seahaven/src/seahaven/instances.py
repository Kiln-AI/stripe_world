"""A live world: a private copy of a fixture, and everything a call into it needs.

An instance is a working directory holding one file per node of its world's
composition, a connection on each, a frozen clock they share, and per node a
seeded id stream and a state dict -- plus a change log and a call log kept in
memory, and one lock over the whole of it. A world that adds nothing has one
node, so its instance is the one file it has always been. `world.instance(...)`
makes one, `inst.call(...)` runs a tool on whichever node owns it, and
`inst.destroy()` (or leaving its `with` block) takes the files away again.
Nothing is shared between two instances: two instances of one fixture are two
copies of one file.

*The activation.* Taking the lock from depth 0 opens a `Frame` (`handles.py`) and
returning to depth 0 closes it. That is the lifetime of every `ctx.worlds` handle
host code makes: the outermost call, everything nested inside it, and not one
moment more.

Three rules hold the concurrency together.

*One lock per instance.* `call`, `change_log`, `call_log`, `state`, `freeze`,
`bulk`, `destroy`, a nested call through a handle and the opens inside
`inspect()` and `_control_db()` take it, so calls into one instance serialise and
a destroy waits for the call in flight. Reads through the `inspect()` handle
afterwards do not take it: that handle is the caller's, to read from whatever
thread it likes. The lock is an `RLock` because a control tool is called with it
already held and then asks the instance for something -- its control handle --
that takes it again on the same thread.

*The gate before the lock.* The concurrency gate bounds how many tool calls run
at once across the process. It is taken before the instance lock, so a call
queued behind it holds nothing and can never delay a `destroy` or a `freeze`. A
call host code makes into an added world does not take it at all: the outermost
call is already holding it, and one instance runs one call at a time however many
nodes that call touches.

*The manager's lock is never held while an instance lock is.* The registry is
touched only in short moments that take nothing else.
"""

import atexit
import errno
import hashlib
import logging
import os
import shutil
import stat
import tempfile
import threading
import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager, ExitStack, closing, contextmanager, suppress
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Concatenate, Self, overload

from seahaven.call import Call, arguments_of, name_of, serialise
from seahaven.changes import (
    CallRecord,
    LogRecord,
    _copied_arguments,
    _sort_key,
    open_session,
    render_log,
    tracked_tables,
)
from seahaven.clock import Clock
from seahaven.composition import (
    ROOT_PATH,
    Composition,
    Node,
    NodeKey,
    NodeReport,
    canonical_tree,
)
from seahaven.ctx import Ctx, InstanceInfo
from seahaven.db import Db, build_blank, open_inspection, open_instance
from seahaven.errors import ToolError, UnknownTool, WorldBug
from seahaven.fixtures import Fixture, check_composition, check_id, freeze, load, verify
from seahaven.handles import Frame, unbound
from seahaven.ids import CONTROL_STREAM, INSPECTION_STREAM, Ids, instance_seed
from seahaven.state import Formatter, document
from seahaven.tool import Tool

if TYPE_CHECKING:  # `world.py` imports this module; the annotation is all that is needed here
    from seahaven.world import RegisteredStartupHook, World

__all__ = [
    "WORK_DIR_PREFIX",
    "Instance",
    "InstanceManager",
    "NodeRuntime",
    "calling",
    "concurrency",
    "default_concurrency",
    "gate",
    "in_call",
    "node_seed",
    "set_concurrency",
]

# The start of the working root's name, not the whole of it: the root is
# `<tempdir>/seahaven-<uid>/` and a working directory is
# `<tempdir>/seahaven-<uid>/<pid namespace>-<pid>/<world>/`. See
# `_default_work_root` for why the root is per user and `_process_dirname` for
# why the pid carries a namespace.
WORK_DIR_PREFIX = "seahaven"

# The default working root is POSIX: it is named after a user id, and it is made
# safe with `O_NOFOLLOW`, `fchmod` and `mkdirat`. Where any of that is missing
# there is no default working directory at all and `World(work_dir=...)` is the
# way to name one -- a refusal that says so, rather than an `AttributeError` or a
# `NotImplementedError` out of `world.instance()`.
_POSIX_WORK_ROOT = (
    hasattr(os, "getuid")
    and hasattr(os, "O_NOFOLLOW")
    and hasattr(os, "fchmod")
    and os.mkdir in os.supports_dir_fd
)

_log = logging.getLogger(__name__)


def default_concurrency() -> int:
    """How many tool calls run at once unless an operator says otherwise.

    `process_cpu_count` follows a container's CPU affinity rather than the host's
    core count, and is `None` on a platform that cannot say. The cap keeps a very
    large host from over-subscribing.

    **This is not the throughput optimum, and it was never measured to be.** The
    sweep in `bench/results/latest.md` found no optimum above 1 on a build with
    the GIL: most of a call is Python, so a second runnable thread buys contention
    rather than parallelism, and `n = 1` ran 22% to 37% more calls a second than
    this default on every workload, cache state and offered load measured.

    **Nor is it fair.** The same sweep found that a gate starves a waiting caller
    whenever it binds -- at this size exactly as at any other, because the cause is
    the semaphore and not the number.

    One measured reason is left: no value the sweep tried was better than this one
    on every axis at once, everywhere it was measured. (`n = 1` is better on every
    axis at 32 sessions; at four, and with one slow call in the process, it is the
    value that waits worst.) Following cpu affinity is *not* a measured advantage --
    this build has the GIL, and the sweep says nothing about what a free-threaded
    one would do -- it is the shape that keeps that door open, which is a design
    intent and is recorded here as one. An operator with a measurement of their own
    world should reach for `serve --concurrency`.
    """
    return min(os.process_cpu_count() or 4, 16)


class _Gate(threading.BoundedSemaphore):
    """The process-wide gate: a bounded semaphore that publishes its own size.

    `BoundedSemaphore` records the value it was built with and offers no way to
    read it, so `concurrency()` first kept the size in a module-level variable
    beside the gate. Two variables holding one fact is a fact that can drift:
    `set_concurrency` wrote both, and anything else that put a gate in place --
    a test substituting an instrumented one, say -- moved one and left the
    other. This class does not copy the size, it *derives* it: `size` reads the
    bound the semaphore itself is holding, so there is no second value to keep
    in step and none to assign -- a read-only property cannot go stale and
    `gate.size = 99` raises.

    The cost is one standard-library private, read in one place. That is the
    trade the class exists to make: the alternative was every caller reaching
    for `_gate._initial_value`, which is what `concurrency()` was added to stop.
    `ty` needs telling because typeshed does not declare the attribute, not
    because it is absent -- `BoundedSemaphore.__init__` has set it since 3.3 --
    and if a future CPython renames it, `concurrency()` raises `AttributeError`
    and the suite says so in nine tests rather than reporting a wrong size.
    """

    @property
    def size(self) -> int:
        return self._initial_value  # ty: ignore[unresolved-attribute]


# Enhancement: a FIFO gate hands slots out in arrival order; this one starves.
_gate: _Gate | None = _Gate(default_concurrency())


def set_concurrency(size: int) -> None:
    """Resize the process-wide gate. `0` removes it; this is `serve --concurrency`.

    Calls already running are unaffected: each releases the gate it took.
    """
    global _gate
    if size < 0:
        raise WorldBug(f"concurrency must not be negative: {size}")
    _gate = _Gate(size) if size else None


def concurrency() -> int:
    """The gate's size as it was last set, or `0` when there is no gate.

    The counterpart of `set_concurrency`, kept because there was no way to read
    the size back: a caller that wanted it had to reach for
    `instances._gate._initial_value`, which is one module's private name and one
    standard-library class's private attribute in a single expression. `_Gate`
    publishes that size as a read-only property derived from the bound the
    semaphore is holding, so this answers the gate that is actually in place
    rather than a copy of it that a resize has to remember to update.
    """
    # Read once, as `gate()` does: a `set_concurrency` between the test and the
    # attribute must not turn this into an `AttributeError` on `None`.
    current = _gate
    return current.size if current is not None else 0


@contextmanager
def gate(*, bypass: bool = False) -> Iterator[None]:
    """Hold one of the gate's slots for the block, queueing for it if need be.

    Nothing is ever rejected: the gate bounds how many calls execute at once, not
    how many are admitted.
    """
    # Read once: a `set_concurrency` between the acquire and the release must not
    # let this call release a slot on a semaphore it never took.
    semaphore = _gate
    if bypass or semaphore is None:
        yield
        return
    semaphore.acquire()
    try:
        yield
    finally:
        semaphore.release()


@dataclass
class NodeRuntime:
    """One node of one instance: its file, its connection, and what a call on it runs with.

    A leaf world's instance has exactly one of these, for the root, and it holds
    what the instance itself held before composition existed.

    `ctx` is the node's *template* context: its `db`, `ids` and `state` and the
    instance's clock, with no call and an unbound `worlds`. Every context a layer
    or a hook actually runs with is made from it by `Frame.ctx`, which is what
    binds it to one activation.
    """

    node: Node
    db: Db
    ids: Ids
    state: dict[str, Any]
    ctx: Ctx[Any]
    # The tables every per-call session on this node attaches, settled once at
    # creation: a node's schema does not change while its instance is alive.
    tracked: tuple[str, ...] = ()
    # The per-table column names, key positions and non-key positions
    # `render_log` fills as it goes, and never invalidates, for the same reason.
    # (architecture.md §2.2 names the first two; the third is a function of the
    # table as well, and belongs in the same cache rather than in the per-row
    # path.)
    columns: dict[str, tuple[list[str], list[int], list[int]]] = field(default_factory=dict)


@dataclass(frozen=True)
class _Target:
    """What a call resolved to: the name as it was asked for, and who owns it."""

    name: str
    tool: Tool
    node: Node


class Instance:
    """One live world instance. Made by `world.instance(...)`, never by hand."""

    def __init__(
        self,
        *,
        id: str,
        fixture: str | None,
        clock: Clock,
        runtime: Mapping[NodeKey, NodeRuntime],
        node_keys: frozenset[NodeKey],
        frozen_versions: Mapping[str, str],
        dir: Path,
        world: World,
        manager: InstanceManager,
        state_format: str,
        formatter: Formatter,
        episode_id: str,
        caller_seed: int | None,
        fixture_files: Mapping[str, str] | None,
        startup: Mapping[str, Any],
    ) -> None:
        self.id = id
        self.fixture = fixture
        self.clock = clock
        # In the composition's own order: breadth-first, the root first.
        self._runtime = dict(runtime)
        self._root_key = next(iter(self._runtime))
        # The derived instance seed -- what actually drove the root's `ctx.ids` --
        # and not the `seed=` the caller passed, which is one of its two inputs.
        self.seed = self.ctx.instance.seed
        # The `seed=` the caller gave, which is the one a state document reports:
        # `self.seed` above is what it was hashed into.
        self.caller_seed = caller_seed
        self.dir = dir
        self.world = world
        # Over OpenEnv the session's episode id, and in process the instance id:
        # in process an instance is an episode (`functional_spec.md` §3.1).
        self.episode_id = episode_id
        # Per node path, the `file_sha256` its fixture sidecar recorded, the root
        # included; `None` for a blank instance. With `composition()` this is a
        # reader's lookup for the state the episode started from.
        self.fixture_files = dict(fixture_files) if fixture_files is not None else None
        # The reset keywords beyond `fixture`, `seed`, `now` and `state_format`,
        # already JSON-able: serialised at creation, so a keyword no document
        # could carry is refused there rather than at `state()` time.
        self.startup = dict(startup)
        # The format this instance answers in, fixed for its life, and the
        # formatter the root resolved it to when the instance was made.
        self.state_format = state_format
        self._formatter = formatter
        # The thread running a formatter on this instance, or `None`. A thread
        # id rather than a flag because `call` asks before taking the gate: only
        # the formatting thread is refused, and a call from another thread waits
        # for the lock like any other.
        self._formatting: int | None = None
        self.closed = False
        # Re-entrant: a control tool holds this lock and then asks the instance
        # for its control handle, which takes it again, and a tool calling into an
        # added world re-enters it from the same thread.
        self.lock = threading.RLock()
        # The node set this instance was created with, and the seal it was last
        # compared against. A tool or a middleware registered later reaches this
        # instance; an `add_world` cannot, because no live instance holds the file
        # it asks for.
        self._node_keys = node_keys
        # Per node path, the world version the fixture recorded where it is not
        # the one installed. Empty for a blank instance. `composition()` is where
        # an eval reads it (architecture 11.3).
        self._frozen_versions = dict(frozen_versions)
        self._checked: Composition | None = None
        self._manager = manager
        self._inspection: Db | None = None
        self._control: Db | None = None
        # The activation: `_held` opens a `Frame` on the way from depth 0 to 1 and
        # drops it on the way back, and every handle made during it is anchored to
        # the epoch it carries.
        self._depth = 0
        self._epoch = 0
        self._frame: Frame | None = None
        # The change log, appended to in commit order as each call's recording
        # ends; the call log, appended to as each dispatched call returns or
        # raises, so `_calls[i]` is call `i`; and the ordinal counter, which is
        # `len(_calls)` once a call has finished.
        self._records: list[LogRecord] = []
        self._calls: list[CallRecord] = []
        self._call_count = 0
        # Whether a recording is already open on this instance. Only the
        # outermost one records, which is what an `inst.call(...)` made inside a
        # `bulk()` block turns on (`_recording`).
        self._recording_rows = False

    @property
    def ctx(self) -> Ctx[Any]:
        """The root node's template context: no call, and a `worlds` that is not bound."""
        return self._runtime[self._root_key].ctx

    @property
    def db(self) -> Db:
        """The root node's connection. An added node's is `ctx.worlds.<name>.db`."""
        return self._runtime[self._root_key].db

    @property
    def state_path(self) -> Path:
        """The root node's database file."""
        return self.dir / self._runtime[self._root_key].node.file_name

    @property
    def call_count(self) -> int:
        """How many calls have been dispatched to this instance.

        Every tool call that reached the world, including one that raised a
        `ToolError`; never a name the world refused, a control tool, a nested
        call through a handle, or `tools()`. The last call's ordinal is one less
        than this.
        """
        return self._call_count

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
        """Run one tool, with its arguments validated, on the calling thread.

        By the name an agent would use, or by the function itself -- which is the
        typed way in, with the arguments checked and the result the tool's own
        object rather than its rendering (architecture section 8). A function
        resolves against the composite surface, so it names a tool of this world
        or of one it adds, and unambiguously: a world that is a node of this tree
        twice does not name one store, and the answer says which handle does.

        The tool may belong to this world or to any world it adds: the composite
        surface is flat, and the call runs against the store of whichever node
        owns it. Raises the owning world's `ToolError` subclasses to the caller;
        over OpenEnv the same error is rendered onto the observation instead.
        """
        # Ahead of the gate, and ahead of everything else: a formatter reaching
        # this holds the instance lock, and queueing for a gate slot whose
        # holders may be waiting on that lock would hang instead of raising.
        self._refuse_if_formatting()
        started = time.perf_counter()
        # The exposed name once there is one, so an eval grouping the log on it
        # cannot tell the two ways in apart; the function's qualified name until
        # then, which is all a resolution failure has to name.
        name = name_of(tool)
        node: str | None = None
        try:
            target = self._target(tool)
            name, node = target.name, target.node.path
            result = self._dispatch(target, arguments_of(target.tool, tool, args, arguments))
        except BaseException as error:
            self._log_failure(name, started, error, node)
            raise
        self._log_call(name, started, "ok", node)
        return result

    def tools(self) -> list[dict[str, Any]]:
        """The tool list, with JSON schemas. Control tools are never in it.

        The composite list: this world's own tools in registration order, then
        each added world's contribution in `add_world` order. Every entry is the
        owning tool's own listing with only the name substituted, so a
        contributed tool's description and input schema are byte-identical to the
        added world's and nothing in it reveals where it came from. A world that
        adds nothing seals to one node and gets its own registry back.
        """
        return [
            entry.tool.listing() | {"name": entry.name}
            for entry in self._current_composition().tools.values()
        ]

    def inspect(self) -> Db:
        """A read-only handle on this instance, opened once and kept.

        Every table of every node: the root's store is `main` and each added
        node's file is attached under the schema its path derives, so "was the
        invoice created and was the message posted" is one statement over
        `main.invoices` and `messaging.posts`. The instance's clock, and no
        authorizer beyond the connection's permanent write denial -- which was
        installed after the attaches and therefore refuses a later `ATTACH` as
        well as every write (`db.open_inspection`).

        Reads through it do not take the instance lock: a read-only connection on
        a WAL database sees a consistent snapshot per statement. Reading through
        it concurrently with `destroy()` is the one ordering the caller owns.
        """
        with self._held():
            if self._inspection is None:
                self._inspection = open_inspection(
                    self.state_path,
                    self.clock,
                    self.ctx.instance.seed,
                    INSPECTION_STREAM,
                    self._attachments(),
                )
            return self._inspection

    def composition(self) -> tuple[NodeReport, ...]:
        """What this instance is running against: every node, root first.

        Paths, world names and versions, the scope each node resolved into and
        the alias routes that reach it. The set the instance was created with, so
        it describes the files on disk rather than whatever the world's seal says
        now. Nothing agent-facing carries any of it.

        A node whose fixture was frozen from another version of its world, with
        the schema unchanged, also carries `frozen_world_version`: that is
        reported and never refused, and this is where an eval reads it.
        """
        return tuple(
            NodeReport.of(runtime.node, self._frozen_versions.get(runtime.node.path))
            for runtime in self._runtime.values()
        )

    def change_log(self) -> list[LogRecord]:
        """Every row this instance has changed, one record per row per call, in call order.

        Across every node, one flat list: a record says which node with `world`.
        Costs no database work: each call's records were rendered when that call
        committed, and this hands back what is already in memory.

        The list is the caller's, but the records in it are the instance's: a
        `LogRecord` is frozen and its `key`, `before` and `after` dicts are the
        ones the log holds, handed out rather than copied because copying every
        record on every read would cost an episode's worth of dicts per call.
        Read them; `to_dict()` is the copy.
        """
        with self._held():
            return list(self._records)

    def call_log(self) -> list[CallRecord]:
        """Every call dispatched to this instance, in dispatch order, so `[i]` is call `i`.

        Each entry carries the tool's name as the caller gave it, a copy of the
        arguments as the call carried them, and the message of what it raised or
        `None`. Nothing is re-serialised: an in-process caller who passed
        something that is not JSON gets it back as it was.
        """
        with self._held():
            return list(self._calls)

    def state(self, format: str | None = None) -> dict[str, Any]:
        """The state document: this instance's provenance, and `state` from its format.

        A plain dict, JSON-serialisable with the standard library, so a caller
        saves an episode with `json.dump` and nothing else. It costs
        serialisation only: the change log is in memory and was rendered as each
        call committed, so this does no database work. The document is the
        caller's, all the way down -- editing it edits nothing the instance holds.

        `format` answers in another format registered on this world instead, for
        the same instance. It is in-process only, because the OpenEnv `state`
        message carries no arguments, and the instance's own format is unchanged.

        Refused inside a transaction -- `bulk()`, or a tool call -- where the
        rows written are not committed and no document could describe them.
        """
        with self._held():
            if any(runtime.db.in_transaction for runtime in self._runtime.values()):
                # The lock is an `RLock`, so a `state()` inside `bulk()` gets this
                # far and would then build a document from transactions that have
                # not committed: the log would be missing the rows the same block
                # can already read through `ctx.db`, with nothing to say so. A
                # formatter never runs in a transaction (`functional_spec.md` §6).
                # Any node: `bulk()` opens a transaction on every one of them, and
                # a call into an added world opens one on that node alone.
                raise WorldBug(
                    "state cannot run inside a transaction: a formatter reads what the instance "
                    "holds, and inside bulk() or a tool call the rows written are not committed "
                    "yet. Read it after the block or the call returns"
                )
            name = self.state_format if format is None else format
            formatter = (
                self._formatter if format is None else self.world.resolve_state_format(format)
            )
            # Saved and restored rather than set and cleared: a formatter may read
            # `instance.state(format=...)` to build a variation of a built-in
            # (`functional_spec.md` §6), and the inner read must not leave the
            # outer one unguarded for the rest of the formatter.
            formatting = self._formatting
            self._formatting = threading.get_ident()
            try:
                return document(self.world, self, name, formatter)
            finally:
                self._formatting = formatting

    def freeze(self, id: str, description: str) -> Fixture:
        """Mint a fixture from this instance's current state: every node's store, together.

        One frozen file per node and one sidecar describing all of them, at the
        one clock this instance runs on. All or nothing: a node whose schema has
        drifted mints nothing (`fixtures.freeze`).
        """
        with self._held():
            if any(runtime.db.in_transaction for runtime in self._runtime.values()):
                # The lock is an `RLock`, so a `freeze` inside `bulk()` gets this
                # far and then reaches a `VACUUM` SQLite will not run inside a
                # transaction. Said here instead, where what to do about it is
                # obvious: the rows are not committed yet, and a fixture of
                # uncommitted rows is not what the caller asked for either. Any
                # node, because `bulk()` opens a transaction on every one of them.
                raise WorldBug(
                    "freeze cannot run inside bulk(): leave the bulk() block first, so the "
                    "rows it wrote are committed and the fixture is what the instance holds"
                )
            return freeze(self, id, description, fixtures_dir=self.world.fixtures_dir)

    def bulk(self) -> AbstractContextManager[Ctx[Any]]:
        """Write straight into the instance, under its lock and one transaction per node.

        The authoring path: a tool call per row would spend its time on argument
        validation and transaction boundaries for tens of thousands of rows. What
        is yielded is the root node's context, with no call attached and a live
        `ctx.worlds`, so an added world's store is reached through
        `ctx.worlds.<name>.db`; nothing is disabled and nothing is wrapped. Every
        node's transaction is committed on the way out, and all of them are rolled
        back together if the block raised. Startup hooks do not run again.
        """
        return self._bulk()

    def destroy(self) -> None:
        """Close everything and remove the working directory. Idempotent.

        Takes the lock, so a call in flight finishes first.
        """
        # Unregistered first, and outside the instance lock: the manager's lock is
        # never taken while an instance lock is held.
        self._manager.unregister(self)
        with self.lock:
            if not self.closed:
                self.closed = True
                self._close()
                _log.info("destroyed instance %s of world %s", self.id, self.world.name)
        shutil.rmtree(self.dir, ignore_errors=True)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exception: object) -> None:
        self.destroy()

    def __repr__(self) -> str:
        return f"<Instance {self.id} of {self.world.name} from {self.fixture or 'blank'}>"

    def _target(self, tool: str | Callable[..., Any]) -> _Target:
        """Which node owns the tool a caller named, in the tree as it stands now."""
        composition = self._current_composition()
        if not isinstance(tool, str):
            # A function names the entry, and the entry names the node: the two
            # ways in meet here, and everything below this line is one path.
            entry = composition.entry_for(tool)
            return _Target(entry.name, entry.tool, entry.node)
        name = tool
        entry = composition.tools.get(name)
        if entry is not None:
            return _Target(name, entry.tool, entry.node)
        # Control tools are never contributed -- they are the framework's, not a
        # world's surface -- so the composite list cannot hold them and the root's
        # own registry is where they are. Every other name the root registers is
        # in that list already.
        registered = self.world.tools.get(name)
        if registered is None or not registered.control:
            # An agent naming a tool that does not exist reads the answer: it is
            # a tool error, not a framework one.
            raise UnknownTool(name)
        return _Target(name, registered, composition.root)

    def _dispatch(self, target: _Target, arguments: Mapping[str, Any]) -> Any:
        tool = target.tool
        # The gate first and the lock second, so a queued call holds nothing.
        with gate(bypass=tool.control), self._held() as frame:
            call = Call(target.name, arguments, tool, node=target.node.path)
            ctx = frame.ctx(target.node.key, call)
            if tool.control:
                # Imported here, not at the top: `control.py` is written against
                # `Instance`, so the dependency runs that way round and this is
                # the one place the call path needs it back.
                from seahaven import control

                return control.dispatch(self, ctx)
            # Every call that reaches the world takes an ordinal, a `ToolError`
            # and a middleware short-circuit included: the harness's Nth call is
            # this instance's Nth (functional_spec.md §7).
            i = self._next_ordinal()
            # The chain is read from the node here rather than held, so a
            # middleware registered after this instance was made applies to it.
            with self._recording(i), self._logging_call(target.name, arguments), in_call():
                return target.node.agent_chain(ctx, call)

    def _next_ordinal(self) -> int:
        """The ordinal of the call about to run, counting from 0. The lock is held."""
        self._call_count += 1
        return self._call_count - 1

    @contextmanager
    def _recording(self, i: int | None) -> Iterator[None]:
        """Log every row the block changes, on every node, as one call's worth of records.

        Every node and not only the target's: a tool on one node reaches another
        through `ctx.worlds.<name>.call(...)`, which never re-enters
        `Instance.call`, so the nested writes are recorded under the outer `i`
        because the outer call's sessions are already open there.

        A session per call rather than per-call deltas off one long-lived
        session: diffing consecutive cumulative changesets is O(everything the
        episode changed) per call, which is the cost the change log exists to
        keep off a harness. This is O(what the call changed).

        Re-entrant, and the outermost recording is the one that records. The
        nesting is `inst.call(...)` inside a `bulk()` block, which is a
        supported pattern (`_held`, and `docs/composition.md`): the call writes
        inside the block's transactions, so its rows commit or roll back with
        the block and not with the call. A nested recording that read its own
        changeset would read it while those transactions are still open --
        logging rows the block may yet roll back, and logging them a second time
        under the block's own recording. The rows therefore land under the outer
        `i`, which for a `bulk()` block is `None` (functional_spec.md §3.2).
        """
        if self._recording_rows:
            yield
            return
        self._recording_rows = True
        try:
            with ExitStack() as sessions:
                opened = [
                    (
                        runtime,
                        sessions.enter_context(
                            closing(open_session(runtime.db.conn, runtime.tracked))
                        ),
                    )
                    for runtime in self._runtime.values()
                ]
                try:
                    yield
                finally:
                    # After every node's transaction has committed or rolled back
                    # and before any other write: `changeset()` joins what a
                    # session recorded against the live table, so a rolled-back
                    # row, or one written back to its original values, contributes
                    # nothing (functional_spec.md §3.2 "net per call").
                    records: list[LogRecord] = []
                    for runtime, session in opened:
                        changeset = session.changeset()
                        if changeset:
                            records.extend(
                                render_log(
                                    changeset,
                                    runtime.db.conn,
                                    runtime.columns,
                                    i=i,
                                    world=runtime.node.path,
                                )
                            )
                    records.sort(key=_sort_key)
                    self._records.extend(records)
        finally:
            self._recording_rows = False

    @contextmanager
    def _logging_call(self, name: str, arguments: Mapping[str, Any]) -> Iterator[None]:
        """Append one call-log entry when the block ends, however it ends.

        The arguments are copied on entry, before the chain runs, so the record
        is the call as made rather than whatever a tool left in the dict it was
        handed. `error` is the message of whatever the chain raised, recorded as
        it was raised: an author reading the log in process is reading their own
        world's failure, and `CallRecord.to_dict` is where a message that was
        never written for an agent stops.
        """
        given = _copied_arguments(arguments)
        error: str | None = None
        tool_error = False
        try:
            yield
        except BaseException as raised:
            error = str(raised)
            # The world's error handler runs inside the chain, so by the time an
            # exception reaches here the world has already had its say: what
            # arrives as a `ToolError` is what the world chose to tell the agent.
            tool_error = isinstance(raised, ToolError)
            raise
        finally:
            self._calls.append(
                CallRecord(tool=name, arguments=given, error=error, tool_error=tool_error)
            )

    def _current_composition(self) -> Composition:
        """The world's tree, refusing one that has grown or lost a node since creation.

        A tool or a middleware registered after an instance exists reaches it on
        its next call, which is the framework's promise and what the seal
        delivers. An `add_world` cannot: it changes how many files an instance is
        supposed to have, and no live instance can honour that.

        Memoised on the composition's identity, so the set comparison runs once
        per seal rather than once per call.
        """
        composition = self.world.composition()
        if composition is not self._checked:
            if frozenset(composition.by_key) != self._node_keys:
                raise WorldBug(
                    f"the composition of world {self.world.name!r} changed after instance "
                    f"{self.id} was created; create a new instance"
                )
            self._checked = composition
        return composition

    def _control_db(self) -> Db:
        """A second read-only handle, opened once, for the control tool alone.

        Not the `inspect()` handle, though it is opened the same way. A control
        read runs through `sandbox.run_statement`, which sets the connection's
        authorizer and its value limit for the length of one statement; the
        `inspect()` handle is the one a caller reads through *without* taking the
        instance lock. Two threads on one connection, one of them changing its
        authorizer while the other steps a cursor, is a deadlock inside SQLite --
        and one that takes the interpreter with it, because the thread waiting on
        the connection is holding the GIL.

        Reached only from the control tool, which `Instance.call` runs with the
        instance lock held, so this connection has one statement on it at a time
        and the state `run_statement` borrows is state nobody else can see.
        """
        with self._held():
            if self._control is None:
                self._control = open_inspection(
                    self.state_path,
                    self.clock,
                    self.ctx.instance.seed,
                    CONTROL_STREAM,
                    self._attachments(),
                )
            return self._control

    def _attachments(self) -> list[tuple[str, Path]]:
        """Every node but the root, as `open_inspection` attaches them.

        Built from the instance's own runtime, which is the set of files that
        exist on disk, and not from the world's current seal. By depth rather
        than by position, so the invariant is read off the node itself.
        """
        return [
            (runtime.node.schema_name, self.dir / runtime.node.file_name)
            for runtime in self._runtime.values()
            if runtime.node.depth > 0
        ]

    def _nodes(self) -> tuple[NodeRuntime, ...]:
        """Every node's runtime, root first, in the composition's canonical order.

        Reached by `fixtures.freeze`, which writes down what they hold. Private
        for the reason `_control_db` is: a node runtime is the framework's own
        internals and no part of what a world or an eval is offered.
        """
        return tuple(self._runtime.values())

    def _refuse_if_formatting(self) -> None:
        """Refuse a write from inside a formatter.

        A formatter runs with the instance lock held, and the lock is an `RLock`,
        so a formatter calling `inst.call` or `inst.bulk` on its own instance
        would otherwise be let straight through to write.

        The test is against *this* thread: another thread's call must wait for
        the lock like any other, not be refused because the instance happens to
        be formatting somewhere else.
        """
        if self._formatting == threading.get_ident():
            raise WorldBug("a state formatter reads an instance and never writes to it")

    @contextmanager
    def _bulk(self) -> Iterator[Ctx[Any]]:
        self._refuse_if_formatting()
        # `_recording(None)`: authoring writes happen after creation and count,
        # but there is no call in flight for them to belong to. It is outside the
        # transactions, so it reads each changeset after they have all settled.
        with self._held() as frame, self._recording(None), ExitStack() as stack:
            # Every node's transaction open before the block runs and committed in
            # sequence on the way out, so a bulk write that reaches two stores
            # through `ctx.worlds` either lands in both or in neither.
            for runtime in self._runtime.values():
                stack.enter_context(runtime.db.transaction())
            yield frame.ctx(self._root_key, None)

    @contextmanager
    def _held(self) -> Iterator[Frame]:
        """Hold the lock, refusing an instance `destroy` got to first, and open the activation.

        Everything that touches the instance goes through here, so a caller
        either wins the race with `destroy` or is told the instance is gone --
        never half of each.

        The `Frame` is opened on the way from depth 0 to 1 and dropped on the way
        back, and every re-entry on the same thread yields that same frame. That
        is what makes a handle live exactly as long as the activation that made
        it: across a nested `handle.call`, after it returns, and across an
        `inst.call(...)` from inside a `bulk()` block -- but not one moment past
        the outermost block, because the epoch moves again on the way out.
        """
        with self.lock:
            if self.closed:
                raise WorldBug(f"instance {self.id} has been destroyed")
            frame = self._frame
            if frame is None:
                self._epoch += 1
                frame = Frame(self, self._epoch)
                self._frame = frame
            self._depth += 1
            try:
                yield frame
            finally:
                self._depth -= 1
                if self._depth == 0:
                    self._frame = None
                    self._epoch += 1

    def _close(self) -> None:
        """Release every handle the instance holds.

        No session outlives a call -- each one is opened and closed inside
        `_recording` -- so what is left here is a connection per node. Both
        read-only handles, the caller's from `inspect()` and the control tool's
        own, may never have been opened.
        """
        for handle in (self._inspection, self._control):
            if handle is not None:
                handle.close()
        self._inspection = None
        self._control = None
        for runtime in self._runtime.values():
            runtime.db.close()

    def _log_failure(
        self,
        name: str,
        started: float,
        error: BaseException,
        node: str | None = None,
        *,
        internal: bool = False,
    ) -> None:
        """The line for a call that raised, in the vocabulary an eval groups on.

        A `ToolError` has a code; anything else has only its class name to give,
        and `invoke` has already put the traceback on record.
        """
        outcome = error.code if isinstance(error, ToolError) else type(error).__name__
        self._log_call(name, started, outcome, node, internal=internal)

    def _log_call(
        self,
        name: str,
        started: float,
        outcome: str,
        node: str | None = None,
        *,
        internal: bool = False,
    ) -> None:
        """The one operational line per call: which instance, which tool, how long, how it went.

        The duration is wall time from entry, so a call that queued behind the
        gate reports the latency its caller saw.

        `node` is the path of the node that owns the tool, absent only when no
        node does -- an agent naming a tool that is not there. A call host code
        made through a handle marks itself internal, so an eval can tell an
        agent's calls from the ones a composite made on its behalf.
        """
        _log.info(
            "call %s on instance %s of world %s: %s in %.1f ms%s",
            name,
            self.id,
            self.world.name,
            outcome,
            (time.perf_counter() - started) * 1000,
            _where(node, internal),
        )


class InstanceManager:
    """Every live instance of one world, in this process. Created lazily by `World`."""

    def __init__(self, world: World) -> None:
        self._world = world
        self._lock = threading.Lock()
        self._instances: dict[str, Instance] = {}
        # One manager is made per world, and only when that world is about to
        # have its first instance, so this registers once: a process that exits
        # without destroying its instances still takes their files with it.
        #
        # Nothing unregisters it, and that is deliberate. The registration is
        # what makes the cleanup unconditional -- it must survive a caller
        # dropping every reference to the world while its instances are still on
        # disk, which is exactly the case that needs it -- and there is no point
        # in a manager's life at which it is known to be finished: `close()` is
        # idempotent and a closed manager can still make instances. The bound is
        # one entry per world that has ever made an instance, which is a handful
        # of objects for the life of a process.
        atexit.register(self.close)

    def create(
        self,
        fixture_id: str | None = None,
        *,
        seed: int | None = None,
        now: str | datetime | None = None,
        state_format: str | None = None,
        episode_id: str | None = None,
        startup_kwargs: Mapping[str, Any] | None = None,
    ) -> Instance:
        """Materialise an instance from a fixture, or from the world's DDL.

        One SQLite file, one connection and one `Ids` stream per node of the
        world's composition -- which for a world that adds nothing is one of each,
        in the directory it has today. A composite fixture carries one frozen file
        per node and every one of them is copied; a blank instance builds every
        node from its own world's DDL.

        `state_format` is the format this instance answers `state()` in, in place
        of the root world's pin, and `episode_id` the id every document of the
        episode reports; over OpenEnv `reset` mints one before the instance
        exists, and in process the instance id is it.
        """
        world = self._world
        kwargs = startup_kwargs or {}
        # The seal first: every whole-tree failure surfaces from the first use of
        # the tree, and this is one.
        composition = world.composition()
        # Everything else that can be refused is refused here, before a directory
        # exists: an unknown startup argument, a startup value no state document
        # could carry, a format nothing registered, an id that is not an id, a
        # fixture that is missing, modified or frozen from another schema, and
        # `now=` where the fixture already carries the clock. A creation that
        # cannot succeed copies nothing and leaves nothing behind.
        _check_startup_kwargs(composition, kwargs)
        startup = _serialised_startup(kwargs)
        # On the root, and only the root: an added world's registrations are
        # never consulted (`functional_spec.md` §6).
        format_name = state_format if state_format is not None else world.pinned_state_format
        formatter = world.resolve_state_format(format_name)
        fixture = self._fixture(composition, fixture_id, now) if fixture_id is not None else None
        _sweep_once(self)

        instance_id = str(uuid.uuid4())
        directory = self._make_instance_dir(instance_id)
        runtime: dict[NodeKey, NodeRuntime] = {}
        try:
            # The clock and the seed first, before anything is opened or built.
            # Every connection of the instance carries a stream of the seed --
            # `random()` and `randomblob()` are registered on each one from that
            # node's own seed -- and so does the connection that applies a node's
            # DDL, because a schema file that seeds reference rows is DML this
            # instance runs and has to replay like any other.
            #
            # The fixture id, or the world's name for a blank instance, so one
            # caller seed against two fixtures gives two streams.
            base = instance_seed(fixture_id if fixture_id is not None else world.name, seed)
            if fixture is None:
                clock = _clock_from(now) if now is not None else Clock.wall()
                for node in composition.nodes:
                    build_blank(
                        directory / node.file_name,
                        node.world.schema,
                        clock=clock,
                        seed=node_seed(base, node.path),
                    ).close()
            else:
                _copy_fixture(fixture, composition, directory)
                clock = Clock.from_iso(fixture.now)
            info = InstanceInfo(id=instance_id, fixture=fixture_id, seed=base)
            for node in composition.nodes:
                runtime[node.key] = _open_node(node, directory, clock, base, info)
            instance = Instance(
                id=instance_id,
                fixture=fixture_id,
                clock=clock,
                runtime=runtime,
                node_keys=frozenset(composition.by_key),
                frozen_versions=_frozen_versions(fixture, composition),
                dir=directory,
                world=world,
                manager=self,
                state_format=format_name,
                formatter=formatter,
                episode_id=episode_id or instance_id,
                caller_seed=seed,
                fixture_files=_fixture_files(fixture),
                startup=startup,
            )
            # One activation for the whole of creation, so a root hook's handles
            # stay live across every hook that runs after it.
            with instance._held() as frame:
                _run_startup_hooks(composition, frame, kwargs)
                for node_runtime in runtime.values():
                    # Asked once, here, so that the refusal of a table with no
                    # primary key is still a refusal at instance creation and
                    # the per-call sessions have nothing to work out.
                    node_runtime.tracked = tracked_tables(
                        node_runtime.db.conn, node_runtime.node.world
                    )
        except BaseException:
            for node_runtime in runtime.values():
                node_runtime.db.close()
            shutil.rmtree(directory, ignore_errors=True)
            raise
        with self._lock:
            self._instances[instance_id] = instance
        return instance

    def destroy(self, instance: Instance) -> None:
        """Destroy one instance. The same thing as `instance.destroy()`."""
        instance.destroy()

    def unregister(self, instance: Instance) -> None:
        """Forget an instance. Called by `destroy` before it closes anything."""
        with self._lock:
            self._instances.pop(instance.id, None)

    def sweep_stale_processes(self) -> int:
        """Remove the working directories of processes that are no longer running.

        A crashed process leaves its instances on disk and nothing else will ever
        clean them up. Only the default location is swept, and only sibling
        directories carrying this pid namespace's prefix and naming a pid of it
        that is not alive: a live process's directory is never touched, so two
        processes that can see each other's pids cannot sweep each other, a
        directory being built is never removed mid-creation, and a name from a
        namespace this process cannot ask about is never judged. A `work_dir` the
        caller configured is theirs and is never swept.

        The root is read through a checked descriptor (`_open_root`), so a
        symlink planted at its path is not followed and another user's directory
        at that name is not walked. An entry that is itself a symlink is not a
        directory and is never removed: `rmtree` would refuse it anyway, and the
        `lstat` here means it is not even offered. Judging and removing happen
        through that one descriptor, so the entry that was checked and the entry
        that is removed are one inode rather than one name looked up twice.

        Two processes may sweep the same root at the same moment -- each one's
        first `create` does -- so an entry can be listed and then be gone before
        it is judged. That is the other process doing this one's work, not a
        failure: the entry is stepped over, the rest of the root is still swept,
        and the count reports what this process itself removed.

        A directory from the layout before this one -- a bare `<pid>`, with no
        namespace prefix -- is not swept, and nothing else will remove it either.
        Deliberately: the condition that would make it safe to sweep is the
        cross-namespace hazard the pid prefix was added to remove.
        """
        if self._world.work_dir is not None or not _POSIX_WORK_ROOT:
            return 0
        root = _default_work_root()
        swept = 0
        try:
            with _open_root(root) as fd:
                prefix = _dirname_prefix()
                for name in os.listdir(fd):
                    pid = _pid_of(name, prefix)
                    if pid is None or _is_alive(pid):
                        continue
                    try:
                        entry = os.stat(name, dir_fd=fd, follow_symlinks=False)
                    except OSError:
                        # Removed by another sweep between the listing and here,
                        # or unanswerable. Either way it is not this process's to
                        # judge, and it is not the rest of the root's problem.
                        continue
                    if not stat.S_ISDIR(entry.st_mode):
                        continue
                    shutil.rmtree(name, dir_fd=fd, ignore_errors=True)
                    # Counted by what is gone, not by what was attempted:
                    # `rmtree` is asked to ignore errors, and a directory it
                    # could not remove has not been swept.
                    if _is_gone(name, fd):
                        swept += 1
        except OSError:
            # No root yet, or one this process must not touch. `create` raises
            # the loud refusal about the second a moment later; the sweep's job is
            # to remove what it is sure about, so it says nothing here -- and what
            # it had already removed before the refusal is still what it removed.
            pass
        if swept:
            _log.info("swept %d working directories of processes that are gone", swept)
        return swept

    def close(self) -> None:
        """Destroy every live instance. Registered with `atexit`; safe to call twice."""
        with self._lock:
            instances = list(self._instances.values())
        for instance in instances:
            instance.destroy()

    def _fixture(
        self, composition: Composition, fixture_id: str, now: str | datetime | None
    ) -> Fixture:
        """The fixture an instance is about to be copied from, and every refusal it earns.

        Found by directory name rather than by scanning: `freeze` is the only
        thing that mints a fixture and it always names the directory after the
        id, so creating an instance does not have to parse every other sidecar.
        """
        # Applied before the filesystem is touched, so an id off the wire cannot
        # become a path.
        check_id(fixture_id)
        directory = self._world.fixtures_dir / fixture_id
        if not directory.is_dir():
            raise WorldBug(
                f"world {self._world.name!r} has no fixture {fixture_id!r} in "
                f"{self._world.fixtures_dir}; freeze one, or name the directory with "
                f"World(fixtures_dir=...)"
            )
        fixture = load(directory)
        verify(fixture)
        if fixture.meta.schema_hash != self._world.schema_hash:
            raise WorldBug(
                f"fixture {fixture_id!r} was frozen from a different schema; regenerate it"
            )
        # Every node's schema and the shape of the tree itself, after the root's
        # own two checks and before anything is copied. What comes back is the
        # differences that are reported rather than refused: an added world
        # installed at another version whose schema is unchanged still loads, and
        # one package cannot be installed at two versions in one environment, so
        # there is nothing here to act on beyond saying so.
        for difference in check_composition(fixture.meta, composition):
            _log.info("fixture %s of world %s: %s", fixture_id, self._world.name, difference)
        if now is not None:
            raise WorldBug("now= applies to blank instances only: a fixture carries its own clock")
        return fixture

    def _make_instance_dir(self, instance_id: str) -> Path:
        """Make this instance's own directory, and answer where it is.

        Two paths. A `work_dir` the caller named is theirs: it is made with
        `parents=True` and nothing is checked, because a directory the caller
        chose is a directory the caller is responsible for.

        The default one is built a descriptor at a time --
        `<tempdir>/seahaven-<uid>/` , `<pid namespace>-<pid>/`, `<world>/` --
        because every one of those names sits under a directory that somebody
        else may be able to write to, and a name is a lookup while a descriptor
        is an inode. `_open_root` checks the root and `_open_child` checks each
        level below it, so a symlink planted at any of them is refused rather
        than followed, and the instance directory is created relative to the
        checked `<world>` descriptor rather than composed as a string and
        resolved again. What comes back is a path, because that is what SQLite
        and the rest of this module take; by then every component of it is an
        inode this user made or owns.

        The root is kept at `0o700` and each level is made `0o700` as well
        rather than relying on the root's mode, because the root's mode protects
        what is under it only as far as the root itself is trustworthy, and
        `_open_root` exists precisely because a root found already in place is
        not. Both are used, and neither replaces the other. `mkdir(mode=0o700)`
        is a ceiling and not a setting, because the process umask masks it, so it
        cannot be relied on to leave `0o700` behind -- which is the whole reason
        the `fchmod` exists. What it does do is make the ceiling `0o700` rather
        than `mkdir`'s default `0o777` for the window between the two calls: the
        argument that the mode is only a ceiling is a reason to follow it with an
        `fchmod`, not a reason to leave the ceiling wide, and under `umask 000`
        that is the difference between a root that is briefly world-writable and
        one that never is. The `fchmod` settles the mode, and it is on a
        descriptor and not on a path for two further reasons of its own:
        `exist_ok` says nothing about the mode of a directory that is already
        there, so the mode is re-asserted on every call and a root left by an
        earlier run is repaired; and a path can be redirected between the two
        calls, while an inode cannot.
        """
        configured = self._world.work_dir
        if configured is not None:
            directory = configured / instance_id
            try:
                directory.mkdir(parents=True)
            except OSError as error:
                # A configured `work_dir` whose parent does not exist, or a
                # filesystem that is full or read-only. A caller who named the
                # directory is the one who can do something about it.
                raise WorldBug(
                    f"cannot make the working directory {directory}: {error}. Name a directory "
                    f"this process can write with World(work_dir=...)"
                ) from error
            return directory

        root = _default_work_root()
        process = _process_dirname(os.getpid())
        try:
            root.mkdir(mode=0o700, parents=True, exist_ok=True)
            with _open_root(root) as root_fd:
                os.fchmod(root_fd, 0o700)
                # The per-process directory is one per process and not one per
                # world, so a second world in this process finds it already
                # there; the world's own is the same the second time round.
                with (
                    _open_child(root_fd, process) as process_fd,
                    _open_child(process_fd, self._world.name) as world_fd,
                ):
                    os.mkdir(instance_id, 0o700, dir_fd=world_fd)
        except OSError as error:
            # The refusals that come from outside the framework: a read-only or
            # full `<tempdir>`, a directory planted by somebody else at one of
            # these names, or a symlink standing where one of them should be.
            # They read like every other refusal here, and name the way out.
            raise WorldBug(
                f"cannot make a working directory under {root}: {error}. Name a directory this "
                f"process owns with World(work_dir=...)"
            ) from error
        return root / process / self._world.name / instance_id


def _clock_from(now: str | datetime) -> Clock:
    return Clock.from_iso(now) if isinstance(now, str) else Clock(now)


def node_seed(base: bytes, path: str) -> bytes:
    """The seed one node's id stream is drawn from.

    The root's is the instance seed itself, untouched, so a world that adds
    nothing draws exactly the identifiers it drew before composition existed. An
    added node's is a function of its own canonical path alone, so adding or
    removing a node perturbs no other node's stream -- which is why the path is
    the salt rather than a position in the tree.

    The `node` tag is domain separation against `ids._stream_seed`, which derives
    a connection's `random()` stream as `sha256(seed + b"\0" + label)` over the
    same base. A child may be named anything `^[a-z][a-z0-9_]*$` matches, `build`,
    `control`, `inspection` and `instance` included, so without the tag a node of
    that name would draw its identifiers from the very stream one of the root's
    connections hands to SQL -- and an agent's `SELECT random()` would read out
    the ids that node is about to mint. Pinned by
    `test_composite_instance.py::test_a_node_named_after_a_sql_door_does_not_draw_that_doors_stream`.
    """
    if path == ROOT_PATH:
        return base
    return hashlib.sha256(base + b"\0node\0" + path.encode("utf-8")).digest()


def _open_node(
    node: Node, directory: Path, clock: Clock, base: bytes, info: InstanceInfo
) -> NodeRuntime:
    """Open one node's file and build the context every call on it starts from.

    The connection is opened on the node's own seed, so `random()` and
    `randomblob()` in one node's SQL are a stream of that node's -- two nodes of
    one instance share neither each other's nor their own `ctx.ids`. The root's
    node seed is the instance seed itself, so a leaf world's writable connection
    draws exactly what it drew before composition existed.
    """
    seed = node_seed(base, node.path)
    db = open_instance(directory / node.file_name, clock, seed)
    ids = Ids(seed)
    state: dict[str, Any] = {}
    return NodeRuntime(
        node=node,
        db=db,
        ids=ids,
        state=state,
        # Unbound: this context belongs to the instance and to no activation, so
        # one that escapes the framework raises where it reaches for another
        # world rather than addressing whatever call happens to be running.
        ctx=Ctx(db=db, clock=clock, ids=ids, state=state, instance=info, worlds=unbound()),
    )


def _serialised_startup(kwargs: Mapping[str, Any]) -> dict[str, Any]:
    """The startup keywords as the state document reports them, one at a time.

    Rendered at creation so that a keyword a document could never carry is a
    refusal there rather than at the end of an episode, and one keyword at a
    time so that the refusal can say which one. The hooks themselves still
    receive the raw values.
    """
    startup: dict[str, Any] = {}
    for keyword, value in kwargs.items():
        try:
            startup[keyword] = serialise(value)
        except WorldBug as error:
            # `serialise` speaks about tool results, which is what it is for
            # everywhere else; this is the one caller that is not one, and it is
            # the one that can say which keyword was the problem.
            raise WorldBug(
                f"startup keyword {keyword!r} must be JSON-able data, because the state "
                f"document reports it: {error}"
            ) from error
    return startup


def _fixture_files(fixture: Fixture | None) -> dict[str, str] | None:
    """Per node path, the `file_sha256` the fixture's sidecar recorded. `None` for a blank one.

    The root under `ROOT_PATH`, because a sidecar's version-1 fields describe the
    root and `nodes` lists only what the root adds (`fixtures.py`).
    """
    if fixture is None:
        return None
    return {ROOT_PATH: fixture.meta.file_sha256} | {
        node.path: node.file_sha256 for node in fixture.meta.nodes
    }


def _frozen_versions(fixture: Fixture | None, composition: Composition) -> dict[str, str]:
    """Per node path, the world version its fixture recorded, where it is not the installed one.

    Empty for a blank instance, and empty for a fixture whose every node is at
    the version it was frozen at, so a report only carries the field when there
    is something to say. `check_composition` has already refused every node whose
    *schema* moved, so what is left here is the difference that is reported and
    never refused (architecture 11.3).

    Version only, deliberately: `NodeReport.frozen_world_version` is the field
    §12 gives the report, so a node frozen from a differently-named world with
    the same version reads as unchanged here. The other half of that difference
    is `fixtures._version_differences`, which compares world name and version
    both and puts the name in the INFO log at create.
    """
    if fixture is None:
        return {}
    recorded = {composition.root.path: fixture.meta.world_version}
    recorded |= {node.path: node.world_version for node in fixture.meta.nodes}
    return {
        node.path: recorded[node.path]
        for node in composition.nodes
        if node.path in recorded and recorded[node.path] != node.world.version
    }


def _copy_fixture(fixture: Fixture, composition: Composition, directory: Path) -> None:
    """Copy one frozen file per node into the new instance's directory.

    `copyfile` and not `copy`: the instance must not inherit the fixture's
    read-only mode, and its timestamps are its own. On Linux this is
    `copy_file_range`, so a reflink filesystem makes the copy nearly free.

    The source name is the one the sidecar recorded and the destination is the
    one this composition derives. They agree -- both come from the node's path --
    and reading the source from the sidecar is what keeps the fixture, rather
    than a rule repeated here, the description of what is in the directory.
    `check_composition` has already established that the two sets of paths match.

    Every source here is a file of the fixture's own directory rather than a link
    out of it, because `_fixture` runs `verify` first and `fixtures._verify_file`
    refuses a symlink by name. The rule is stated once, where the file is hashed,
    rather than twice.
    """
    shutil.copyfile(fixture.state_path, directory / composition.root.file_name)
    by_path = {node.path: node for node in composition.nodes}
    for node in fixture.nodes:
        shutil.copyfile(fixture.file_of(node), directory / by_path[node.path].file_name)


def _check_startup_kwargs(composition: Composition, startup_kwargs: Mapping[str, Any]) -> None:
    """Refuse a `reset` argument no startup hook in the tree asked for.

    The union across the tree, because a keyword is broadcast: every hook in the
    tree that names it receives it. A hook taking `**kwargs` anywhere accepts
    everything, which switches the check off for the whole tree; the docs say to
    spell the parameters out.
    """
    accepted = composition.accepted_startup_kwargs
    if accepted is None:
        return
    unknown = set(startup_kwargs) - accepted
    if unknown:
        raise WorldBug(f"unknown reset argument(s): {sorted(unknown)}")


def _run_startup_hooks(
    composition: Composition, frame: Frame, startup_kwargs: Mapping[str, Any]
) -> None:
    """Run every node's hooks once, root first, with every node's transaction already open.

    All the transactions before the first hook, because the root's hooks are
    specified to write into a child's store through `ctx.worlds.<name>.db` before
    that child's own hooks run, which is only coherent if the child's transaction
    is already open. A hook that raises rolls every one of them back, and creation
    removes the instance entirely.

    Depth-first preorder over the canonical tree, so a node whose hooks seed a
    child runs before it, and a node reached by two routes runs once.
    """
    runtimes = frame.instance._runtime
    with ExitStack() as stack:
        for node in composition.nodes:
            stack.enter_context(runtimes[node.key].db.transaction())
        for node in canonical_tree(composition.root):
            ctx = frame.ctx(node.key, None)
            for hook in node.world.startup_hooks:
                hook(ctx, **_hook_arguments(hook, node, startup_kwargs))


def _hook_arguments(
    hook: RegisteredStartupHook, node: Node, startup_kwargs: Mapping[str, Any]
) -> dict[str, Any]:
    """What one hook is called with: the `reset` keywords it named, then the bound ones.

    Bound last and therefore final. A bound keyword is part of the composition,
    and an eval must not be able to reconfigure one node by passing a `reset()`
    keyword that happens to share its name -- while that keyword still reaches
    every other hook in the tree that names it.
    """

    def wanted(name: str) -> bool:
        return hook.takes_var_kwargs or name in hook.accepts

    return {
        **{name: value for name, value in startup_kwargs.items() if wanted(name)},
        **{name: value for name, value in node.bound_startup.items() if wanted(name)},
    }


# The per-thread "in a call" flag. In-process calls run on the caller's thread and
# OpenEnv runs each session on its own, so a thread-local is exactly the scope
# this question has: a tool that is running must not create an instance, and a
# tool that is not is ordinary authoring code.
_in_call = threading.local()


@contextmanager
def in_call() -> Iterator[None]:
    """Mark this thread as inside a tool call for the length of the block."""
    previous = calling()
    _in_call.active = True
    try:
        yield
    finally:
        _in_call.active = previous


def calling() -> bool:
    """Is this thread inside a tool call? What `World.instance` refuses on."""
    return getattr(_in_call, "active", False)


def _where(node: str | None, internal: bool) -> str:
    """The node the call ran on, and whether host code made it, for the log line."""
    if node is None:
        return ""
    return f" (node={node}, internal=true)" if internal else f" (node={node})"


_sweep_lock = threading.Lock()
_swept = False


def _sweep_once(manager: InstanceManager) -> None:
    """The sweep is per process, not per world: the directories it removes are a process's."""
    global _swept
    if manager._world.work_dir is not None:
        # This world sweeps nothing, so it must not spend the one sweep the
        # process gets: a world with its own working directory made first would
        # otherwise leave every default-location world unswept for the run.
        return
    with _sweep_lock:
        if _swept:
            return
        _swept = True
    manager.sweep_stale_processes()


def _default_work_root() -> Path:
    """`<tempdir>/seahaven-<uid>/`: the parent of every working directory this user makes.

    Per user, and not the shared `<tempdir>/seahaven/` of
    `components/fixtures_instances.md` §2.1, because the system temporary
    directory is shared and a root inside it can be exactly one of private and
    usable by a second user. The phase plan records the reasoning. Both the
    creation path and the sweep read the root from here, so they are the same
    directory by construction.
    """
    if not _POSIX_WORK_ROOT:
        raise WorldBug(
            "there is no default working directory on this platform: it needs a user id, "
            "O_NOFOLLOW, fchmod and mkdirat. Name one with World(work_dir=...)"
        )
    return Path(tempfile.gettempdir()) / f"{WORK_DIR_PREFIX}-{os.getuid()}"


@contextmanager
def _open_root(root: Path) -> Iterator[int]:
    """The working root as a descriptor: a directory, not a link, belonging to this user.

    Everything the framework does to the root is done to this descriptor, because
    the root's *path* cannot be trusted. `<tempdir>` is world-writable with the
    sticky bit, and the sticky bit stops deleting and renaming -- not creating a
    name nobody has claimed yet -- while `seahaven-<uid>` is entirely predictable.
    So a local user can plant either of two things there before Seahaven first
    runs: a symlink, which a path-based `chmod` and a path-based `rmtree` follow
    to wherever it points, or a plain directory of their own, which a path-based
    `chmod` succeeds on whenever the victim is uid 0 -- how this suite, a CI job
    and many container harnesses run.

    `O_NOFOLLOW` refuses the first -- `ENOTDIR` on Linux, where `O_DIRECTORY` is
    judged first, `ELOOP` elsewhere -- and the owner check refuses the second
    with `EPERM`. The caller reads either as one `WorldBug` naming
    `World(work_dir=...)`.

    What this does *not* cover, so that the claim matches the code: `O_NOFOLLOW`
    is about the last component, and a symlinked `<tempdir>` above it (macOS's
    `/tmp` is one) is both legitimate and out of reach -- anyone who can redirect
    `TMPDIR` already owns the process's environment. And an entry removed through
    the root's path after it has been checked is a narrow race rather than the
    standing exposure a planted name is.
    """
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        if os.fstat(fd).st_uid != os.geteuid():
            # `geteuid`, not `getuid`: what may be written is the effective id's
            # business. A directory somebody else made at our name is theirs,
            # even when this process is root and could chmod it anyway.
            raise PermissionError(errno.EPERM, "owned by another user", str(root))
        yield fd
    finally:
        os.close(fd)


@contextmanager
def _open_child(parent: int, name: str) -> Iterator[int]:
    """`name` under an open directory, made `0o700` if it is not there, as a descriptor.

    `_open_root`'s rule one level down, and for the same reason. The names below
    the root are as predictable as the root's own -- a pid, a world name -- and
    an entry found already there is not this process's work: it is a second
    world in this process, or it is somebody else's. `mkdir` says which by
    raising `FileExistsError`, and the open that follows settles it: `O_NOFOLLOW`
    refuses a symlink planted at the name, and the owner check refuses a
    directory another user made. What comes back is an inode this user owns, so
    the names created below it cannot be redirected between the check and the
    use.
    """
    with suppress(FileExistsError):
        os.mkdir(name, 0o700, dir_fd=parent)
    fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    try:
        if os.fstat(fd).st_uid != os.geteuid():
            raise PermissionError(errno.EPERM, "owned by another user", name)
        yield fd
    finally:
        os.close(fd)


def _is_gone(name: str, parent: int) -> bool:
    """Is `name` no longer under this directory? Anything unanswerable is still there."""
    try:
        os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return False


def _pid_namespace() -> int | None:
    """The inode of this process's pid namespace, or `None` where there is no procfs."""
    try:
        return os.stat("/proc/self/ns/pid").st_ino
    except OSError:
        return None


def _dirname_prefix() -> str:
    """What every working directory name of this pid namespace starts with."""
    namespace = _pid_namespace()
    return "" if namespace is None else f"{namespace}-"


def _process_dirname(pid: int) -> str:
    """The working directory name of a process: its pid, scoped to its pid namespace.

    §2.1 keys the directory on the bare pid, and a pid means nothing outside the
    namespace that issued it. Two containers of one image with `<tempdir>`
    bind-mounted from the host share a root and have unrelated pid spaces -- and
    both are uid 0, so the per-user root does not separate them. `os.kill(pid, 0)`
    answers in the caller's namespace, so with bare pids one container reads the
    other's live pid as dead and sweeps a running eval's database away. The
    namespace's inode in the name means the sweep can tell its own pids from
    names it must not judge at all, and it ends pid reuse between namespaces too.

    Where there is no procfs to ask -- macOS, and anything else without
    `/proc/self/ns/pid` -- the name is the bare pid, which is §2.1's own layout
    and what this framework did before the prefix existed. A machine whose
    processes all share one namespace loses nothing by it; a machine that does
    not have procfs cannot be asked the question in the first place.
    """
    return f"{_dirname_prefix()}{pid}"


def _pid_of(name: str, prefix: str) -> int | None:
    """The pid a working directory name carries, or `None` if this process cannot judge it.

    A name without this namespace's prefix belongs to another namespace (or to a
    host that has no procfs), where the same number is a different process or no
    process at all. The sweep leaves those alone for ever: a directory that is
    never reclaimed costs disk, and one that is reclaimed while it is in use
    costs somebody's eval.

    Only the digits this code writes are a pid: ASCII `0`-`9`. `str.isdigit` is
    not that test. It is true of superscript digits, which `int` then refuses
    with a `ValueError` -- not an `OSError`, so neither handler in the sweep
    catches it and `world.instance()` itself fails -- and true of the Arabic-
    Indic digits, which `int` reads as an ordinary number, so a name nobody here
    wrote would be judged as some live process's pid and swept when that process
    died. Neither can be planted by a stranger, since the root is `0o700` and
    owner-checked before it is read; but a name this code did not write is not a
    pid, and `None` is the same answer it gives a foreign namespace.
    """
    if not name.startswith(prefix):
        return None
    rest = name[len(prefix) :]
    return int(rest) if rest.isascii() and rest.isdecimal() else None


def _is_alive(pid: int) -> bool:
    """Is a process with this id running?

    A pid we may not signal is alive, and so is anything else that cannot be
    answered for: the cost of being wrong is deleting a running process's
    instances. `0` is not a pid at all -- to `kill` it means this process's whole
    group -- so it is never asked about.
    """
    if pid <= 0:
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True
