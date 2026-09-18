---
status: complete
---

# Architecture: State Format

How `functional_spec.md` is built into the code as it stands on `main`, after world composition.
Single document: the change is wide (it touches the instance, the world, the OpenEnv layer, the
CLI scaffold and the docs) but every piece is small, and no component has enough internal
complexity for a document of its own. Section numbers in `functional_spec.md` are cited as
"FS §n"; the composition project's specs as "composition FS §n" and "composition ARCH §n".

The first attempt's code is on branch `claude/happy-allen-inkfcc` (tip `07fc86a`). Where a
section below says "as the first attempt did", the coding agent may read that branch for the
shape and the reasoning, then write it against `main`. Nothing is merged from it.

## 1. Module map

| Module | Change |
|---|---|
| `seahaven/changes.py` | `LogRecord` and `render_log()` replace `Change` and `render()`; `CallRecord`; `tracked_tables(conn, world)` and `open_session(conn, tracked)` replace `start_session()`; the JSON value mapping (`_jsonable` handles infinities); the cross-type rank for sorting. |
| `seahaven/state.py` | **New.** The formatter protocol, the three built-in formatters (each handling the no-instance case), format-name validation, the envelope. |
| `seahaven/instances.py` | `NodeRuntime` loses `session` and gains `tracked` and `columns`; `Instance` loses `changes()`; gains the record store, the call store, the call counter, `state()`, `change_log()`, `call_log()`, `call_count`, `episode_id`, `caller_seed`, `fixture_files`, `startup`, `state_format`; per-call and per-bulk sessions on every node; the formatting guard. `InstanceManager.create` gains `state_format` and `episode_id`, resolves the formatter on the root, serialises the startup keywords, computes each node's tracked tables. |
| `seahaven/world.py` | `World(..., state_format: str)` required, stored as `pinned_state_format`; `RESET_ARGUMENTS` gains `state_format`; `world.state_format()` registration; `world.resolve_state_format()`; `copy()` carries the registry; `CONTROL_TOOL_NAMES` shrinks to one. |
| `seahaven/composition.py`, `seahaven/handles.py`, `seahaven/fixtures.py` | **Read, not changed.** `NodeReport` and `ROOT_PATH` feed the envelope; `FixtureMeta.nodes` feeds `fixture.nodes`; `WorldHandle.call` is why recording is per node (§3). |
| `seahaven/ids.py` | `_seed_bytes` and `instance_seed` narrow to `int | None`. |
| `seahaven/pytest_plugin.py` | The marker's `seed` narrows to `int | None`; `state_format` binds to `world.instance`'s keyword. |
| `seahaven/openenv/env.py` | `reset(state_format=...)`; `state` answers the document; `SeahavenState` types the envelope and carries `state` as a dict; `_composition()` goes. |
| `seahaven/openenv/client.py` | Docstring example only. |
| `seahaven/control.py` | `controller_changes` removed; `DeprecationWarning` in `dispatch` for `controller_run_sql`, attributed with `skip_file_prefixes`; docstring names `state()`. |
| `seahaven/cli/templates/base/src/PACKAGE/world.py.tmpl` | `state_format="seahaven.state/1"`. |
| `seahaven/__init__.py` | Exports `LogRecord` and `CallRecord`; `Change` is gone. |
| Every `World(...)` in the repo | Gains `state_format=`, the helpers that build worlds included (§10). |
| `bench/recording.py` | **New.** The probe for §16, run in the last phase. |
| Docs | FS §12. |

## 2. Data model

### 2.1 `LogRecord` and `CallRecord` (`changes.py`)

```python
@dataclass(frozen=True)
class LogRecord:
    i: int | None
    world: str                      # the node's canonical path; ROOT_PATH for the root
    table: str
    op: Literal["insert", "update", "delete"]
    key: dict[str, Any]
    before: dict[str, Any] | None
    after: dict[str, Any] | None

    def to_dict(self) -> dict[str, Any]   # FS §3.2 field order: i, world, table, op, key, before, after
                                          # key/before/after copied; the document is the caller's

@dataclass(frozen=True)
class CallRecord:
    tool: str                       # the name as called: the root surface's name, prefix included
    arguments: dict[str, Any]       # as the call carried them, deep-copied at capture, never re-serialised
    error: str | None

    def to_dict(self) -> dict[str, Any]   # FS §4.2 field order; arguments copied
```

`Change` and `render()` are deleted with `Instance.changes()`; `render_log` is the one renderer,
and `_row`, `_columns` and `_jsonable` move under it. `world` is `Change.world`'s definition
(composition FS §6.2) under the log's name for it; the value comes from `runtime.node.path`.

### 2.2 The stores (`Instance`)

- `self._records: list[LogRecord]`, appended in commit order, read under the instance lock.
- `self._calls: list[CallRecord]`, appended as each dispatched call returns or raises, so that
  `self._calls[i]` is call `i`.
- `self._call_count: int`, incremented per dispatched call (§4); always `len(self._calls)` once a
  call has finished.
- Per node, on `NodeRuntime`: `tracked: tuple[str, ...]`, the tables every per-call session on
  that node attaches, computed once at creation by `changes.tracked_tables(conn, node.world)`
  (today's `start_session` loop, factored out), including the refusal of a table with no explicit
  primary key; and `columns: dict[str, tuple[list[str], list[int]]]`, the node's per-table column
  names and key positions, filled lazily by `render_log` and never invalidated, since a node's
  schema does not change for the life of an instance. `NodeRuntime.session` is gone.

### 2.3 The state document

A `dict[str, Any]`: the envelope, written by `state.envelope(world, instance, format)`, plus
`state`, the formatter's output (§5). Never a model in process; the OpenEnv layer types it (§9).

## 3. Capturing the change log

### 3.1 One SQLite session per node per call

There is no long-lived session any more: `Instance.changes()` is removed, and with it the
cumulative session each node carried. **Every call opens a session on every node**, and that is
the only recording in production. `main`'s dispatch is `call` → `_target` → `_dispatch`; the
recording wraps the chain inside `_dispatch`, and nothing about `_target` moves:

```python
def call(self, tool, /, *args, **arguments):
    self._refuse_if_formatting()                   # ahead of the gate: never queue while formatting
    target = self._target(tool)                    # UnknownTool / WorldBug raised here take no ordinal
    return self._dispatch(target, arguments)

def _dispatch(self, target, arguments):
    tool = target.tool
    with gate(bypass=tool.control), self._held() as frame:
        call = Call(target.name, arguments, tool, node=target.node.path)
        ctx = frame.ctx(target.node.key, call)
        if tool.control:
            return control.dispatch(self, ctx)      # not a call: no ordinal, no session, no entry
        i = self._next_ordinal()
        with self._recording(i), self._logging_call(target.name, arguments):
            with in_call():
                return target.node.agent_chain(ctx, call)
```

```python
@contextmanager
def _recording(self, i: int | None) -> Iterator[None]:
    sessions = [(runtime, open_session(runtime.db.conn, runtime.tracked))
                for runtime in self._runtime.values()]
    try:
        yield
    finally:
        # After every node's transaction has committed or rolled back and before
        # any other write: `changeset()` joins what a session recorded against the
        # live table, so a rolled-back row, or one written back to its original
        # values, contributes nothing (FS §3.2 "net per call").
        records: list[LogRecord] = []
        for runtime, session in sessions:
            changeset = session.changeset()
            session.close()
            if changeset:
                records.extend(render_log(changeset, runtime.db.conn, runtime.columns,
                                          i=i, world=runtime.node.path))
        records.sort(key=_sort_key)                 # FS §3.3: (world, table, key) across nodes
        self._records.extend(records)
```

Why every node and not only the target's: a tool on one node reaches another through
`ctx.worlds.<name>.call(...)`, which `handles.py` dispatches through the owner's `internal_chain`
inside the outer call's frame, never re-entering `Instance.call`. The outer call's sessions are
already open on every node when that happens, so the nested writes are recorded under the outer
`i` with no hook in the handle path (FS §7). Opening N sessions per call instead of one is the
cost of that, bounded by node count; §16 measures it and the number is reported, not asserted.

Why not one long session per node with per-call deltas: diffing consecutive cumulative changesets
is O(total changes) per call, quadratic inside Seahaven, which is the cost FS §4.1 exists to avoid
on the harness side. A session per call is O(changes in the call).

The `finally` runs on a `ToolError` too, and on any exception: a failed call still consumed its
ordinal and still gets its (empty) recording. If `session.changeset()` itself raises, the exception
propagates as a `WorldBug`-class failure of the instance; nothing is appended.

### 3.2 `bulk()`

`main`'s `_bulk` opens every node's transaction on an `ExitStack` before the block runs, so a
bulk write that reaches two stores lands in both or in neither. `_recording(None)` wraps that
stack from outside, so authoring writes land in the log with `i: null` (FS §3.2), on every node
the block touched, appended when the block exits, and a block that raised leaves nothing on any
node. The transactions are inner: the recording must read each changeset after they have all
committed or rolled back. Startup hooks run before any session exists and stay out.

`_recording` is re-entrant per instance, and the outermost one records: `_dispatch`'s
`_recording(i)` inside an open `bulk()` recording yields and does nothing else. That is the one
nesting there is, `inst.call(...)` inside a `bulk()` block, which `Instance._held` and
`docs/composition.md` both support. A nested recording of its own would read its changeset while
the block's transactions are still open -- logging rows the block may yet roll back, and logging
them again under the block's own recording when it commits, which is one row and two records. The
call's rows therefore land under the outer `i`, which for `bulk()` is `None` (FS §3.2).

### 3.3 The call log

`_logging_call(name, arguments)` takes `copy.deepcopy(arguments)` on entry, before the chain
runs, and appends `CallRecord(name, that copy, error)` in its `finally`, where `error` is
`str(exc)` for whatever the chain raised, or `None`. Nothing is serialised: over OpenEnv the
mapping is the JSON that arrived in the `CallToolAction`, and re-rendering it would only be a
chance for drift; in process it is what the caller passed. The copy is what makes the record the
call as made rather than whatever a tool left in the dict it was handed. `to_dict()` copies again
on the way out. The name is `target.name`, which under composition is the root surface's name for
the
tool, prefix included. Nested calls through a `WorldHandle` never reach `_dispatch` and add no
entry.

### 3.4 Rendering (`render_log`)

```python
def render_log(changeset: bytes, conn, columns: dict[...], *, i: int | None, world: str) -> list[LogRecord]
```

For each `TableChange`: `key` from the key positions (as today's renderer does); `before`/`after`:

| op | before | after |
|---|---|---|
| insert | `None` | every column (whole row) |
| update | changed non-key columns, old values | changed non-key columns, new values |
| delete | every column (whole row) | `None` |

"Changed" is a column whose `new` value is not `apsw.no_change`; key columns are excluded from
both sides of an update. Records of one call, across nodes, are **sorted** by `(world, table,
key)` with `_sort_key(record)` returning `(record.world, record.table, tuple(_sqlite_rank(v) for
v in record.key.values()))` over the **rendered** values, where `_sqlite_rank` maps `None` to
`(0, None)`, a number to `(1, number)` and anything else to `(2, str)`. Rendered, not raw: FS §3.3
publishes the within-call order as part of the format and FS §3.6 has a consumer re-sort by it,
and a consumer holds a blob's base64 and never its bytes, so the rendered order is the only one a
reader can reproduce (decided in the first attempt's review). STRICT guarantees one type per
column, so in practice the rank never varies.

### 3.5 Values (`_jsonable`)

Extended from today's bytes→base64: a `float` that is infinite becomes `None`. One-line comment at
the conversion: `# JSON has no infinities; SQLite already stores NaN as NULL, so this is its rule
one step further (functional_spec.md §3.4).` Integers and finite floats pass through; text
passes through; `None` stays `None`.

## 4. The call ordinal and `call_count`

`_next_ordinal()` increments and returns `self._call_count - 1`, under the lock, in `_dispatch`
before the chain, so a `ToolError` and a middleware short-circuit both count. `main` resolves the
target in `Instance.call` outside the gate and the lock, and `_target` raises `UnknownTool` (a
name) or `WorldBug` (a function `call()` cannot resolve) from there; neither reaches
`_next_ordinal`, which is FS §7's rule with no code to add. A control tool never reaches it. A
nested call through a `WorldHandle` never reaches `_dispatch`. `inst.tools()` is not a call.
`call_count` is a read-only property over `_call_count`.

## 5. Formats and formatters (`state.py`)

```python
type Formatter = Callable[["World", "Instance | None"], dict[str, Any]]

SEAHAVEN_STATE_V1 = "seahaven.state/1"
SEAHAVEN_STATE_LAST_STEP_V1 = "seahaven.state+last_step/1"
SEAHAVEN_STATE_CALLS_V1 = "seahaven.state+calls/1"
BUILTIN_FORMATS: Mapping[str, Formatter]        # the three names above
BUILTIN_PREFIX = "seahaven."
_NAME = re.compile(r"^[A-Za-z0-9_.+-]+/[1-9][0-9]*$")

def check_format_name(name: str) -> None       # syntax; WorldBug naming the rule
def envelope(world: World, instance: Instance | None, format: str) -> dict[str, Any]
def document(world: World, instance: Instance | None, format: str, formatter: Formatter) -> dict[str, Any]
```

`check_format_name` is deliberately its own rule and not `names.why_not_a_name`: a format name is
never a path, and `/` is the one character it must contain. A formatter takes the world and an
optional instance and returns **the value of `state` only**. `envelope` writes FS §3.1's
provenance in field order, with the instance-dependent fields `None` and `call_count` 0 when there
is no instance. `document` runs the formatter, raises `WorldBug` if it returns anything but a
dict, and joins the two; it is the one place they are joined, used by `Instance.state` and by the
environment before `reset`. `None` is the state before the first `reset` over OpenEnv (FS §3.5):
the world's pinned formatter runs with it; no default. In process an instance always exists.

### 5.1 The envelope and the built-ins

```python
def envelope(world, inst, format):
    return {"format": format,
            "seahaven_version": seahaven.__version__,
            "world": {"name": world.name, "version": world.version},
            "composition": _composition(inst) if inst else None,
            "fixture": {"id": inst.fixture, "nodes": {p: {"file_sha256": h} for p, h in inst.fixture_files.items()}}
                       if inst and inst.fixture else None,
            "episode_id": inst.episode_id if inst else None,
            "seed": inst.caller_seed if inst else None,
            "now": inst.clock.iso() if inst else None,
            "startup": copy.deepcopy(inst.startup) if inst else None,
            "call_count": inst.call_count if inst else 0}

def _composition(inst):               # NodeReport per node, keyed by path, root first
    return {report.path: {"world": report.world, "world_version": report.world_version,
                          "scope": report.scope, "aliases": list(report.aliases),
                          "schema_hash": report.schema_hash,
                          "frozen_world_version": report.frozen_world_version}
            for report in inst.composition()}

def state_v1(world, inst):            # the value of `state`
    return {"db": {"log": [r.to_dict() for r in inst.change_log()] if inst else []}}

def state_last_step_v1(world, inst):  # the same, filtered to the last call
    if inst is None:
        return {"db": {"log": []}}
    last = inst.call_count - 1
    return {"db": {"log": [r.to_dict() for r in inst.change_log() if r.i == last]}}

def state_calls_v1(world, inst):      # state_v1 plus the call log
    doc = state_v1(world, inst)
    doc["calls"] = [c.to_dict() for c in inst.call_log()] if inst else []
    return doc
```

`i: None` records never pass the last-step filter (FS §4.1). `startup` is deep-copied and
`to_dict()` copies, so the document aliases nothing the instance holds (FS §8); every value below
the copies is a JSON scalar or a fresh container, so that is the whole of it. `Instance.
composition()` answers root first, and a dict keeps insertion order, so the keys are written root
first; a reader is told not to rely on it.

### 5.2 Registration and resolution (`world.py`)

- `World.__init__(..., *, state_format: str | None = None, ...)`: spelled optional so that a
  missing value is a `WorldBug` naming the built-ins rather than a `TypeError` (FS §5, §13);
  `None` is refused. `check_format_name`; if the name begins with `seahaven.` it must be in
  `BUILTIN_FORMATS`, else `WorldBug` listing the built-ins. A name outside the prefix is a custom
  format and is resolved at instance creation (§6), because it is registered after the
  `World(...)` line runs. Stored as `self.pinned_state_format`: one name cannot be both the pin
  and the registration verb, and the verb keeps the spelling FS §6 gives a world author.
- `world.state_format(name)` is a decorator/call like `world.tool`: `check_format_name`; refuse
  the `seahaven.` prefix; refuse a duplicate; require a callable; store in
  `self._state_formats: dict[str, Formatter]`. Open for the life of the world. It does **not**
  call `composition.bump()`: formats are not part of a seal, and a comment beside the other verbs'
  `bump()` calls says so, so nobody adds one later.
- `world.resolve_state_format(name) -> Formatter`: built-ins first, then this world's own; unknown
  is `WorldBug` naming the built-ins and the world's registered names. Only ever called on the
  root of an instance (§6, §9): an added world's registry is never consulted (FS §6).
- `copy()` copies `_state_formats` beside `_tools_by_fn`, `_added_worlds` and the seal.
- `RESET_ARGUMENTS = frozenset({"fixture", "now", "seed", "state_format"})`: the existing hook
  check refuses a parameter of that name with the existing message.

### 5.3 Running a formatter (`Instance.state`)

```python
def state(self, format: str | None = None) -> dict[str, Any]:
    with self._held():
        if any(runtime.db.in_transaction for runtime in self._runtime.values()):
            raise WorldBug("state cannot run inside a transaction: ...")     # FS §8; freeze() has the same guard
        name = self.state_format if format is None else format
        formatter = self._formatter if format is None else self.world.resolve_state_format(format)
        outer, self._formatting = self._formatting, threading.get_ident()
        try:
            return state.document(self.world, self, name, formatter)
        finally:
            self._formatting = outer
```

The framework writes the envelope and `format`, so nothing about the formatter's output is
checked beyond its being a dict. The guard is saved and restored, not set and cleared, because
FS §6 tells a formatter to read `instance.state(format=...)` and edit the result; clearing would
disarm the outer read. It holds the formatting *thread's* ident, and `_refuse_if_formatting()` in
`Instance.call` and `_bulk` compares against the caller's thread ahead of the gate: a formatter
that calls a tool holds the instance lock, and queueing for a gate slot whose holders may be
waiting on that lock would hang instead of raising, while a call from another thread must wait
for the lock rather than be refused. The error is `WorldBug("a state formatter reads an instance
and never writes to it")`. Reads through `inspect()`, `change_log()`, `call_log()`,
`composition()` and the attributes are what a formatter is for.

## 6. Instance creation (`InstanceManager.create`)

`main`'s signature is `create(fixture_id, *, seed, now, startup_kwargs)`. It gains
`state_format: str | None = None, episode_id: str | None = None`; `seed` narrows to `int | None`.
Order of refusals, all before a directory exists:

1. `_check_startup_kwargs(composition, kwargs)` (`main`'s, unchanged).
2. `startup[k] = serialise(v)` per keyword, so the refusal names the keyword: the document's
   `startup` object, JSON-able or a `WorldBug` now rather than at `state()` time. Hooks still
   receive the raw values.
3. `formatter = world.resolve_state_format(state_format or world.pinned_state_format)`, on the
   root.
4. The fixture checks (unchanged). `fixture_files = {ROOT_PATH: meta.file_sha256} |
   {n.path: n.file_sha256 for n in meta.nodes}` is kept for the envelope; `None` for a blank
   instance.

Per node, after the stores exist: `runtime.tracked = tracked_tables(runtime.db.conn,
runtime.node.world)`, which performs the refusal `start_session` performs today. No session is
opened; `_close` no longer closes one.

`Instance.__init__` gains `state_format: str`, `formatter: Formatter`, `episode_id: str`
(`episode_id or id`), `caller_seed: int | None`, `fixture_files: dict[str, str] | None`,
`startup: dict[str, Any]`. `world.instance(...)` gains `state_format` and passes it through;
`episode_id` is not on `world.instance` (in process the instance id is the episode id, FS §3.1),
so the OpenEnv layer goes to the manager (§9).

## 7. Seed narrowing (`ids.py`)

`instance_seed(source, caller_seed: int | None)`; `_seed_bytes` drops the `bytes` arm, so a
`bytes` value falls into the existing `case _` and raises the existing `WorldBug` with the message
updated to "an int or None". `World.instance`, `InstanceManager.create`, `InstanceInfo` docs and
the pytest marker follow. `node_seed(base, path)` and `ctx.instance.seed` (the derived bytes) are
unchanged.

## 8. Control tools (`control.py`) and the callers of `changes()`

`controller_changes` is deleted: its function, its `Tool`, its entry in `control.TOOLS`, its name
in `world.CONTROL_TOOL_NAMES`, and its tests; `CONTROL_TOOL_NAMES` holds one name, and the
comments that spoke of "either name" follow. `dispatch` gates on a module-level `DEPRECATED =
frozenset({"controller_run_sql"})` rather than warning unconditionally, so a control tool added
later does not inherit the deprecation, and issues
`warnings.warn("controller_run_sql is deprecated: read inst.state() instead", DeprecationWarning,
skip_file_prefixes=(<the seahaven package directory>,))`: the warning lands on the first frame
outside the framework, the caller's own line, whichever of the three depths (in process, over
OpenEnv, `dispatch` called directly) it came by. Python's filters then apply to that location:
shown from `__main__` under the defaults, hidden from an imported module until
`-W default::DeprecationWarning`, once per call site either way. The tool's docstring, which is
its registry description, opens with the replacement.

Every caller of `changes()`, `Change`, `render()` and `controller_changes` that `main` has --
fifty `.changes()` sites across sixteen files, `tests/test_composite_changes.py` the largest --
moves per FS §11's table. The removal is its own phase, after the log is proven, so every test
that moves has the thing it moves to.

## 9. OpenEnv (`openenv/env.py`)

- `reset(self, seed=None, episode_id=None, *, fixture=None, now=None, state_format=None,
  **startup_kwargs)`: computes `episode_id or str(uuid.uuid4())` first and calls
  `self.world._instances().create(fixture, seed=..., now=..., state_format=..., episode_id=...,
  startup_kwargs=...)` -- the manager `world.instance` itself delegates to, because `episode_id`
  is deliberately not a parameter of `world.instance` (§6). `SeahavenEnv._episode_id` is removed:
  the instance is the one answer to which episode this is.
- `state` property: `SeahavenState(step_count=self._steps, **instance.state())` with an instance;
  without one, `SeahavenState(step_count=self._steps, **state.document(self.world, None, pin,
  self.world.resolve_state_format(pin)))` with `pin = self.world.pinned_state_format`.
  `_composition()` is deleted; the envelope carries it.
- `SeahavenState(State)`: every envelope field typed, in FS §3.1 order, each with a description:
  `format: str`, `seahaven_version: str`, `world: WorldRef` (`name`, `version`),
  `composition: dict[str, NodeRef] | None` (`world`, `world_version`, `scope`, `aliases`,
  `schema_hash`, `frozen_world_version`), `fixture: FixtureRef | None` (`id`,
  `nodes: dict[str, FileRef]` with `file_sha256`), `seed: int | None`, `now: str | None`,
  `startup: dict[str, Any] | None`, `call_count: int`, and `state: dict[str, Any]`, untyped
  because its shape is the format's. `episode_id` and `step_count` are the base class's;
  `extra="allow"` is inherited and kept. `WorldRef`, `NodeRef`, `FixtureRef` and `FileRef` are
  exported from `seahaven.openenv`. `main`'s `composition: list[dict]` field is replaced. The
  built-in `state` shapes are validated by tests against a JSON Schema kept in `tests/`, not by the
  model.
- `step()`'s `serialise(...)` wrapper and the composition-aware `_listing()` are `main`'s and are
  kept as they are.
- `_parse_state` is unchanged. `SeahavenClient.state().state` is the formatter's output;
  `.model_dump(exclude={"step_count"})` is the document, and the gate test asserts it equals
  `inst.state()`.

## 10. Scaffold and sweep

- `world.py.tmpl`: `state_format="seahaven.state/1",` with a two-line comment: pinned at creation;
  changing it changes what every eval of this world saves, so bump `version` with it.
- The sweep adds `state_format="seahaven.state/1"` to every `World(...)` in `worlds/`,
  `extensions/`, `tests/` (the six worlds under `tests/worlds/` included), `bench/`, `README.md`
  and `src/seahaven/docs/`, and one `options.setdefault("state_format", "seahaven.state/1")` to
  each helper that builds worlds: `tests/conftest.py`'s `build_world` and `composable_world`, and
  the `make_world`/`rowed` helpers in `tests/test_add_world.py`, `tests/test_composition.py` and
  `tests/test_typed_call.py`. A paren-matching scan, not a grep, finds them; the docs test executes
  every example, so a missed one there fails CI, and `python -m bench composite` imports the six
  worlds, so a missed one there fails at import.

## 11. Lint

No new rule. **Measured on SQLite 3.45.1: a STRICT table refuses `NULL` in any primary-key column
with `NOT NULL constraint failed`, single and composite keys alike, and only non-STRICT tables
accept it.** SH101 already requires STRICT, so the hazard cannot reach a linted world. SH101's
`lints.md` entry gains one sentence saying STRICT is also what keeps every row visible to the
change log. SH208 (no `.instance(` under `tools/` or `middleware/`) and SH209 (`ctx.worlds`
naming a registered child) are `main`'s and nothing here trips them.

## 12. Documentation

Per FS §12, against `main`'s page set and `AGENTS.md`'s "Docs style". The coding agent for the
docs phase writes `state.md` from FS §3, §4, §5, §6 and §10 in the case order FS §12 gives, with
a table of contents; adds the state section to `serving_and_openenv.md` and removes its control-tool
text; moves `composition.md`, `db_schema_and_fixtures.md`, `testing.md` and `concepts.md` from
`changes()` to the log and the document; updates the README example; removes every `controller_`
mention except `cli.md`'s deprecation line and every `changes()` mention; adds `state.md` to
`index.md`, to `tests/test_docs.py`'s `PAGES` and to the scaffold's `AGENTS.md.tmpl` reading list;
adds `state_format` and the root-decides rule to `authoring.md`; updates `reference/api.md` for
the new members and models and removes the old ones. Two guards in `tests/test_docs.py` hold the
`controller_` and `changes()` constraints.

## 13. Error handling

| Condition | Outcome |
|---|---|
| `World(...)` without `state_format`, or with an invalid name, or a `seahaven.` name that is not built in | `WorldBug` at construction, naming the built-ins |
| `reset`/`instance` with an unknown format, or a custom name registered only on an added world | `WorldBug` before any directory exists; over OpenEnv the reset's `EXECUTION_ERROR`, session stays open |
| A hook parameter named `state_format` | `WorldBug` at registration (existing check, extended set) |
| A startup keyword that is not JSON-able | `WorldBug` at creation, naming the keyword |
| A formatter that returns anything but a dict | `WorldBug` from `state()` |
| A formatter that calls `inst.call` or `inst.bulk` | `WorldBug` from the guard, ahead of the gate |
| `state()` inside `bulk()` or a tool call | `WorldBug`, as `freeze()` inside `bulk()` |
| `state()` on a destroyed instance | `WorldBug`, as every other method |
| `seed` that is `bytes` | `WorldBug` from `_seed_bytes` |
| A `controller_run_sql` call | `DeprecationWarning` on the caller's line, then the normal result |
| A `controller_changes` call | `UnknownTool`, as for any unregistered name |

Nothing new is logged at `INFO`; the per-call log line is unchanged.

## 14. Testing strategy

Unit tests live beside the module they pin; every FS §13 item maps to one below. Composite cases
use `tests/worlds/emporium`, `payments` and `shop`, which `tests/conftest.py` already puts on
`sys.path`. Support code is flat under `tests/`, as `tests/serving.py` and
`tests/typed_calls_fixture.py` are.

- `tests/test_changes.py` gains the `LogRecord`/`render_log` cases: the three ops, update with
  one and several changed columns and a column set to `NULL`, composite key order, integer key,
  blob, infinity → `null`, key columns absent from an update's sides, the sort including a blob
  key by its base64 and a mixed-type key on a column of type ANY, `to_dict` field order and
  copying; `CallRecord.to_dict`. The `Change` cases go in the removal phase, each with a
  `LogRecord` equivalent already in place.
- `tests/test_change_log.py` (new): net per call and not across calls, case by case, driven
  through multi-statement `execute` calls rather than `bulk()` where the name says "call";
  ordering across calls; `i: null` for `bulk()`, on one node and on two; no records for a
  rolled-back call, a rolled-back `bulk()`, a read-only call, and startup rows; byte-identical
  `json.dumps` of two identical episodes; ordinals for `ToolError` and none for a refused name, a
  function `call()` cannot resolve, a control tool or `tools()`; on `emporium`: one call writing
  to two nodes, a nested call carrying the outer `i`, the two `payments` stores told apart;
  `state()` does no database work (a statement counter with a positive control); `state()` after
  `destroy()`.
- `tests/test_call_log.py` (new): one entry per dispatched call with the surface name (prefixed on
  `emporium`), the arguments as carried, `error` null and set; none for a refused name, a control
  tool, a nested call; `len(call_log()) == call_count`; a wire call's JSON round-trips through
  `state()` unchanged (`test_server.py`); a tool that mutates its arguments does not alter the
  record.
- `tests/test_fold.py` (new) with `tests/fold_support.py` and `tests/fold_oracle.py`: the FS §3.6
  fold as test code, and an oracle that opens its own `apsw.Session` on every node's connection
  for the whole episode (attached to each `runtime.tracked`, after creation) and renders each with
  the node's path to the net-diff shape. `fold(log) == oracle()` on every `test_change_log.py`
  episode plus a primary-key rewrite, single-node and on `emporium`; a test that the oracle saw
  rows; a test that the log and the fold sort a blob key the same way. Neither support module is
  importable from `seahaven`.
- `tests/test_state.py` (new): the envelope field by field against a fixture instance and a blank
  one, one-node and on `emporium` (`composition` keyed by path with `NodeReport`'s fields,
  `fixture.nodes` from the sidecar); `startup` given and empty; each built-in with and without an
  instance; `state_last_step_v1` per step, concatenation equals the full log, empty before any
  call; `state_calls_v1` with `calls[i]` matching each record's `i`; a custom formatter registered,
  selected by `instance(state_format=)`, refused for a `seahaven.` name, a duplicate, a
  non-callable, a bad name; its output lands under `state` with the envelope untouched, and a
  non-dict output is a `WorldBug`; editing a document does not edit the instance (startup, nested
  startup, a record's key and `after`); `World(...)` without the argument; an unknown `seahaven.`
  name at `World`, an unregistered custom name at `instance`; the root's pin governing `emporium`
  and a leaf's ignored; a formatter on `payments` not resolvable from `emporium`;
  `payments.instance()` using its own pin; the guards (formatter calling a tool refused, another
  thread waits, `state()` in a transaction refused); the built-in documents validate against
  `tests/state_v1.schema.json` with a negative case.
- `tests/test_world.py`: `RESET_ARGUMENTS` refusal for `state_format`; `copy()` carries formats;
  registering a format does not change the seal epoch.
- `tests/test_ids.py`: `bytes` seed refused; the pytest marker test in `tests/test_pytest_plugin.py`
  follows.
- `tests/test_env.py`: `state` before `reset` is the world's pinned formatter with `None`; after
  `reset` the document with `step_count`; `reset(state_format=)` selects; a second `reset` starts
  a new log; `close` discards it; `DECLARED_DESCRIPTIONS` covers the new models.
- `tests/test_server.py`: over the WebSocket, `env.state().model_dump(exclude={"step_count"})`
  equals `inst.state()` with every FS §3.1 field given a non-default value (this is FS §9's
  confirmation step); `reset(state_format="seahaven.state+last_step/1")` selects it; the stock
  `GenericEnvClient` sees the identical dict; an unknown format is an error frame and the session
  survives.
- `tests/test_control.py`: `controller_run_sql` warns (`pytest.warns(DeprecationWarning)`), and a
  separate test that the warning's `filename` is the test's own file; `controller_changes` is
  `UnknownTool`, in process and over the wire with the flag on; a world may register a tool named
  `controller_changes`. The sites that call `controller_run_sql` incidentally carry
  `filterwarnings("ignore:controller_run_sql is deprecated")`, message-scoped.
- `tests/test_composite_changes.py`, `tests/test_composite_instance.py`,
  `tests/test_composite_fixtures.py`, `tests/test_bench.py`, ProjectTracker's and the xmlrpc
  extension's suites: moved per FS §11's table in the removal phase.
- `tests/test_cli_new.py` (existing scaffold tests): the generated `world.py` carries the pin and
  `seahaven check` passes on it.
- `tests/test_docs.py`: `state.md` in `PAGES`; the two guards, each proven to fail on a planted
  violation.
- `bench/`: the probe for §16, with a test that each comparison leg records (its session is
  non-empty after a write) and a test that a recording-only report points at no section it did
  not write.

## 15. Determinism

Two identical episodes produce byte-identical `json.dumps(inst.state())`: the log is sorted
within a call, dict field order is fixed by `to_dict`, `composition` and `fixture.nodes` are
written root first in composition order, `seahaven_version` and the fixture hashes are constants
for a build, and `episode_id` is the instance id, which is a UUID. The test therefore compares
documents with `episode_id` masked; over OpenEnv a caller who passes `episode_id` to `reset` gets
full byte identity.

## 16. Performance

- `state()` does no database work: the log is in memory; `render_log` runs at call time. Pinned by
  a test that installs a statement counter on every node's connection during `state()` and asserts
  zero statements, with a positive control.
- A call's cost with a session per node per call, against one long-lived session per node, is
  measured by `bench/recording.py` on ProjectTracker's write mix and read workloads (one node) and
  on `emporium` (four nodes), in the last phase and as a second priority: composition is rare and
  a cost linear in node count is accepted. The first attempt measured the one-node case at about
  +40% on a write-heavy call and about +10% on a read-only one, and found that most of it was
  `changeset()` plus `render_log` running once per call rather than once per episode, which is
  inherent to a per-call record; the number is re-measured here rather than carried over. The
  report and the docs state the numbers as approximate, with the exact command and its flags, and
  no figure the probe does not itself produce. The probe reaches `instance._runtime` to swap the
  recorder; that is bench code, never importable from `seahaven`.
- `columns` is filled once per table per node per instance; `tracked` once per node per instance.

## 17. Open items

1-7 are the first attempt's, settled and standing. 8-15 are the restart's, settled 2026-09-17;
FS §14 carries the same numbering.

1. No lint rule (§11).
2. Built-in names checked at `World(...)`, custom names at first instance creation (§5.2).
3. A formatter takes `(world, instance | None)`; before `reset` the world's pinned formatter runs
   with `None`, after `reset` the instance's runs; no framework-built document and no default (§5,
   §9).
4. The document is a framework-owned envelope plus `state`; `SeahavenState` types the envelope
   and leaves `state` a dict (§5, §9).
5. Startup keywords are serialised at creation, per keyword (§6).
6. The deprecation warning is tested in process, and its attribution is tested (§8, §14).
7. `changes()` removed; one session per call and no long-lived one (§3, §8).
8. One session per node per call, opened in `_dispatch` and in `_bulk`, read after the
   transactions settle; nested calls need no hook (§3.1).
9. `LogRecord.world` from `runtime.node.path`; sort across nodes on the rendered values (§2.1,
   §3.4).
10. The envelope builds `composition` from `Instance.composition()` and `fixture.nodes` from
    `FixtureMeta`, both keyed by path; `world` stays (§5.1).
11. `pinned_state_format` on every `World`; resolution on the root only; no `bump()` (§5.2).
12. `changes()`, `Change`, `render()`, `controller_changes` deleted in their own phase; FS §11's
    table moves `main`'s callers (§8).
13. `main`'s two-stage dispatch already gives FS §7's ordinals; nothing moves (§4).
14. `CallRecord`, `_logging_call`, `state_calls_v1`; arguments deep-copied as given, never
    re-serialised (§2.1, §3.3, §5.1). `composition` is `None` before `reset` (§5.1).
15. `skip_file_prefixes` for the deprecation; flat test support; the probe last (§8, §14, §16).
