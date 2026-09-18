---
status: complete
---

# Architecture: Seahaven

The technical design for Seahaven. It follows `functional_spec.md` section by section and is deep
enough that whoever implements it designs nothing significant. Where a component has enough
internal design to need its own document, this file states its responsibilities and interfaces and
points at `components/<name>.md`.

Reading order: this file, then the component documents in the order of section 8. A reference
implementation exists for most of the runtime; where this design departs from it, the departure is
stated.

## 1. The `seahaven` package

```
seahaven/
  __init__.py          # the public API: the names listed below
  world.py             # World: registration, validation at registration, the tool registry
  tool.py              # Tool (a registered tool), argument models via pydantic, schema derivation
  call.py              # Call (one invocation), the middleware chain builder
  ctx.py               # Ctx and its members' types
  db.py                # Db (the connection wrapper), connection setup
  clock.py             # Clock and the SQLite date/time overrides
  ids.py               # Ids: seeded randomness and uuid
  errors.py            # SeahavenError, WorldBug, ToolError, ArgumentError, DbError, UnknownTool
  sandbox.py           # Authorizer, run_statement: the public SQL containment every SQL door shares
  fixtures.py          # Fixture, sidecar, load, verify, freeze
  instances.py         # Instance, the per-world InstanceManager, working directory, sweep
  changes.py           # Change and changeset rendering from an APSW session
  conformance.py       # live schema versus world DDL; schema hash
  helpers/
    __init__.py        # run_sql, describe_schema
    run_sql.py
    describe_schema.py
  control.py           # controller_run_sql, controller_changes
  openenv/
    __init__.py        # app(world), SeahavenClient, the models
    env.py             # SeahavenEnv(Environment), Observation and State models
    client.py          # SeahavenClient(EnvClient): the typed client every world shares
    serve.py           # uvicorn entry used by `seahaven serve`
  lint/
    __init__.py        # Finding, run_all
    ddl.py             # STRICT, primary key, wall-clock, FTS5 awareness
    code.py            # wall-clock reads, random and uuid4 use, empty tool descriptions
    coverage.py        # import coverage of tools/ and middleware/
    fixtures.py        # sidecar schema, file hash, schema hash, now present
  cli/
    __init__.py        # argparse entry point `seahaven`
    new.py, check.py, docs.py, fixture.py, serve.py
    templates/         # files `seahaven new` renders
  pytest_plugin.py     # the `world` and `instance` fixtures, the `seahaven` marker
  docs/                # bundled Markdown, package data (later phase)
  py.typed
```

Runtime dependencies: `apsw` (SQLite), `pydantic>=2.12,<3` (argument models, sidecar model),
`pyyaml` (sidecar), `openenv` only in the `seahaven[serve]` extra (its wheel brings gradio, openai,
fastmcp and pandas, about 354 MB). `seahaven.openenv` imports it at module top; importing
`seahaven` never imports `seahaven.openenv`. Nothing else. Dev dependencies: `pytest`, `ruff`,
`ty`, `uv`.

`seahaven/__init__.py` exports exactly: `World`, `Ctx`, `Db`, `Clock`, `Ids`, `Tool`, `Call`,
`Instance`, `Fixture`, `Change`, `SeahavenError`, `WorldBug`, `ToolError`, `ArgumentError`,
`DbError`, `UnknownTool`, `sql_files`, and the `helpers` and `sandbox` subpackages.
`seahaven.openenv` is public as well but is **not** among them: it lives in the `serve` extra and
is imported by name (`from seahaven.openenv import ...`), which is what keeps importing `seahaven`
from importing `openenv`, as the paragraph above requires.

**What is public is not that list.** A name that the component document section covering a module
lists as part of that module's interface is public and stable; `seahaven/__init__` re-exports only
the subset worth a short import, and a name in neither place is internal and may change without
notice. So `Tool.from_function` (how an extension builds a tool, `components/world_and_dispatch.md`
§2) and `seahaven.sandbox`'s `Authorizer`, `run_statement`, `SqlResult` and the refusal names — a
documented `Literal`, stable, because an extension serving another dialect classifies on them,
`components/runtime_db.md` §4 — are instances of that rule rather than exceptions to it, and so are
`Handler` and `Middleware` in `seahaven.world` (`components/world_and_dispatch.md` §1) and `load`,
`load_all`, `verify` and `freeze` in `seahaven.fixtures` (`components/fixtures_instances.md` §1). A
world author annotating a middleware writes `from seahaven.world import Handler`; that is the
supported import and not a reach into a private module.

*Corrected 2026-09-13 — this paragraph read "Everything else is internal" with two hand-listed
exceptions, which the shipped `__all__`s contradict and which misled Phase 5 into declaring a local
copy of `Handler` in the reference world's `middleware/error_handler.py`; closes `BACKLOG.md` B10.
The rule is stated over the section that covers a module rather than over "§1", which B10's own
text said and which would have missed two of the four names above.*

## 2. Key types

| Type | Module | What it is |
|---|---|---|
| `World` | `world.py` | Name, version, DDL text and hash, fixtures directory, working directory, untracked tables, the tool registry (`dict[str, Tool]`, insertion ordered), the middleware list, the startup hooks, and the per-process `InstanceManager` (created lazily). Registration is allowed at any time; the tool registry and the middleware chain are read at call time. |
| `Tool` | `tool.py` | `name`, `description`, `fn`, `params` (the pydantic model class built from the signature), `schema` (JSON schema dict, computed once), `transaction`, `control` (framework control tool). Hashable by name. |
| `Call` | `call.py` | Frozen: `name`, `arguments` (validated, as a `Mapping`), `tool`. `with_arguments(**changes)` returns a copy with those arguments merged in. |
| `Ctx` | `ctx.py` | `db: Db`, `clock: Clock`, `ids: Ids`, `state: dict`, `call: Call \| None`, `instance: InstanceInfo` (`id`, `fixture`, `seed`). One object per instance; `call` is set per invocation on a shallow copy, so tools never see another call's `Call`. |
| `Db` | `db.py` | Wraps the APSW connection: `one`, `rows`, `execute`, `transaction()`, and `conn` (the raw `apsw.Connection`, public). Rows as `dict[str, Any]` built from `cursor.get_description()`. A convenience layer and one error type, not a barrier. |
| `Clock` | `clock.py` | An aware UTC instant; `now()`, `iso()`; equality by instant. |
| `Ids` | `ids.py` | `random: random.Random` seeded from the instance seed; `uuid()` (v4-shaped from the stream). |
| `Instance` | `instances.py` | Id, fixture id, seed, path, `Db`, `threading.RLock`, `Clock`, `Ids`, APSW `Session`, two read-only handles opened lazily and closed on destroy — the caller's from `inspect()` and the control tools' from `_control_db()` — `state`, `closed`. Public surface: `call`, `tools`, `inspect`, `changes`, `freeze`, `bulk`, `destroy`, `clock`, `id`, `fixture`, `seed`; context manager. |
| `Fixture` | `fixtures.py` | The sidecar as a pydantic model plus `dir`; `state_path`. |
| `Change` | `changes.py` | `table`, `op` (`"insert" \| "update" \| "delete"`), `key: dict`, `before: dict \| None`, `after: dict \| None`. |

## 3. Registration and the world object

`World.__init__(name, version, schema, *, fixtures_dir=None, work_dir=None, untracked_tables=())`:

- `schema` is the DDL text; `seahaven.sql_files(package, dir)` reads `*.sql` under a package
  directory in sorted filename order and joins them with newlines. `World` computes `schema_hash`
  (SHA-256 of whitespace-collapsed DDL) by building the DDL in an in-memory database; DDL that does
  not execute raises, with SQLite's own message. A world whose DDL does not execute cannot be
  constructed. The DDL rules are `check`'s and do not run here.
- `fixtures_dir` defaults to `fixtures/` at the project root: walk up from the file of the module
  that constructed the `World` until a directory holding `pyproject.toml` is found
  (`components/world_and_dispatch.md` §1.1). With no `pyproject.toml` above it — an installed wheel
  has none — the default is `fixtures/` beside the package directory, and a fixture that cannot be
  found names `World(fixtures_dir=...)` in its message. Given a path, it is used as is.
- `work_dir` defaults to `<tempdir>/seahaven/<pid>/<world>/`; given a path, that directory is the
  caller's and is used exactly as given.
- `untracked_tables` names tables the changeset session does not attach.
- Registration verbs are implemented once as a decorator-or-call helper: `world.tool(fn_or_tool=None,
  **options)` returns a decorator when called with only options, registers and returns the object
  when given a callable or `Tool`. Same shape for `middleware` (no options) and `instance_startup`
  (no options).

**Building a `Tool` from a function** (`tool.py`):

1. `inspect.signature(fn)`; the first parameter is the context and is skipped; every remaining
   parameter must be keyword-capable and annotated (a missing annotation is a registration error
   naming the parameter). `*args`/`**kwargs` are rejected.
2. `pydantic.create_model(f"{name}Arguments", __config__=ConfigDict(extra="forbid", strict=True),
   **fields)` where each field is `(annotation, default or ...)`. `Annotated[..., Field(...)]`
   passes through unchanged, which is how descriptions, constraints, examples and
   `Field(strict=False)` reach the model and the schema. `Field(alias=...)` is supported
   (`populate_by_name=False`) for wire names Python cannot spell.
3. `schema = model.model_json_schema(mode="validation")`, with `title` removed at the top level and
   `description` set from the tool's description. `$defs` are kept (nested models are common and the
   tool list consumers accept them).
4. The tool's description is the explicit `description=` or the whole dedented docstring
   (`inspect.getdoc`).
5. A `Tool` object passed to `world.tool(...)` (from a factory) is registered as it is, after the
   same validation of its `params` model; options must be empty, because the factory decided them
   and `schema` and `params` were built from them. A factory that lets a world choose the name or
   the description takes them as parameters, as all of Seahaven's do.

Validation at call time: `tool.params.model_validate(dict(arguments))` — strictness lives in the
model's config, so a per-field `Field(strict=False)` works; on `pydantic.ValidationError`,
raise `ArgumentError(tool=name, violations=[{path, message, type} ...])` built from `errors()`. The
tool is called as `fn(ctx, **{name: getattr(model, name) for name in fields})`, so nested pydantic
models arrive as models, not dicts.

**Registration checks** (all raise `WorldBug` at import time): duplicate tool name; a name that
collides with a control tool; a tool named `reset`, `step`, `state` or `close` (reserved by
OpenEnv); a first parameter that is not positional and annotated `Ctx` (or unannotated); an `async
def` or a generator function; a parameter annotated `datetime`, `date`, `time` or an `Enum`
subclass, with the message "parameter `x` is annotated `datetime`; use `str` with a pattern or
`Literal`"; an annotation resolvable only under `TYPE_CHECKING`, naming the symbol; a mutable
default (the rule keeps the published `default` honest and mirrors Python's own); a middleware that is not callable
with three positional parameters (`inspect.signature` check, `(ctx, call, next_)`); a startup hook
with positional parameters beyond the first or with a parameter named `fixture`, `seed` or `now`.

**Import coverage** is a lint, not a runtime check (`lint/coverage.py`, section 8.6).

## 4. The call path

One tool call, in-process (`Instance.call`) and over OpenEnv (`SeahavenEnv.step`) share this path
after transport:

```
Instance.call(name, **arguments)
  tool = world.tools.get(name) or raise UnknownTool(name)
  with gate:                                            # the concurrency gate, before the lock
      with instance.lock:                               # calls into one instance serialise
          if instance.closed: raise WorldBug("instance destroyed")
          ctx = instance.ctx.with_call(Call(name, arguments, tool))
          if tool.control: return control.dispatch(instance, ctx)
          return world.chain(ctx, ctx.call)             # the middleware chain, rebuilt when a middleware is registered
```

The innermost handler (`call.py: invoke(ctx, call)`) does the work the middleware wraps.
`Handler = Callable[[Ctx, Call], Any]`, and `invoke` matches it: nothing in the call path needs the
`Instance`.

```
def invoke(ctx, call):
    validated = call.tool.validate(call.arguments)       # ArgumentError here passes back up the chain
    call = call.with_arguments(**validated); ctx = ctx.with_call(call)
    if call.tool.transaction:
        with ctx.db.transaction():                       # APSW `with conn:` → BEGIN/COMMIT, ROLLBACK on exception
            return serialise(call.tool.fn(ctx, **call.arguments))
    return serialise(call.tool.fn(ctx, **call.arguments))
    # serialise = pydantic to_jsonable_python: models, dataclasses, datetimes
```

Serialisation is inside the transaction, so a result that cannot be serialised rolls the call back.

Errors: `ToolError` subclasses propagate to the caller (in-process) or are rendered into the
observation (OpenEnv). `apsw.Error` is wrapped as `DbError` at `Db` (section 6) before it leaves
`invoke`, so middleware only ever sees Seahaven exceptions. Any other exception is logged at `ERROR`
with `exc_info`, the tool and the instance id, and then passes through the chain unchanged, so the
standard error handler still sees it and the traceback is on record whatever the handler does with
it.

`ArgumentError` is raised inside the chain (as the first thing `invoke` does, after `Call` is built
with unvalidated arguments) so the error handler can restate it. `Call.arguments` is the raw
mapping until validation succeeds, then replaced by `call.with_arguments(**validated)` for the tool.

**Middleware chain** (`call.py: build_chain(middlewares, innermost)`): `handler = innermost; for mw
in reversed(middlewares): handler = _wrap(mw, handler)` where `_wrap(mw, nxt)` returns
`lambda ctx, call: mw(ctx if ctx.call is call else ctx.with_call(call), call, nxt)`. The
normalisation is what makes `call` always `ctx.call`, even after a middleware rewrites arguments and
passes a new `Call` on. `World.chain` is rebuilt on every `world.middleware(...)`
registration and read at call time, so a middleware registered after instances exist applies to
them too. Control tools bypass the chain: `Instance.call` dispatches them to `control.py` directly.

**Serialisation:** `pydantic_core.to_jsonable_python(result)` handles dicts, lists, scalars,
pydantic models, dataclasses and `datetime` (rendered ISO 8601; a tool that wants the canonical
`Z` format calls `ctx.clock.iso()` itself). `bytes` raise `WorldBug` in V1.

## 5. Concurrency

### 5.1 Locks and threads in-process

- **One `threading.RLock` per instance.** Every operation that touches the instance (call, the opens
  inside `inspect` and `_control_db`, changes, freeze, bulk, destroy) takes it; reads through the
  `inspect()` handle after the open do not. The lock discipline is stated once, in
  `components/fixtures_instances.md` §2. Destroy takes the lock before closing, so a call in flight
  completes first (functional spec §10). Idle instances hold no thread.
- **In-process calls run on the caller's thread.** There is no framework-owned pool in the
  in-process API; the caller (pytest, a script, the OpenEnv server) supplies the thread.
- **One `InstanceManager` lock** guards the instance registry. Framework code never blocks on it
  while holding an instance lock.
- **Registration is not locked.** Registration happens while the world module is imported and before
  instances exist, as in Flask. Registering a tool or middleware while calls are in flight is
  unsupported and its effect is undefined; Seahaven does not enforce this (no freeze) and
  does not lock the registry.
- Nothing depends on process-wide mutable state beyond the per-`World` manager, which is per-process
  by construction and safe under free-threaded CPython (its structures are under its lock).

### 5.2 OpenEnv threads and the concurrency gate

OpenEnv runs each session's `reset`/`step`/`close` on that session's own single-thread executor
(fixed; measured at about 93 kB and one thread per idle session, 500 sessions in 1.2 s). Seahaven
accepts this and owns no executor. A tool call therefore runs on the session's thread under OpenEnv
and on the caller's thread in-process.

**The gate.** `Instance.call` takes a process-wide `threading.BoundedSemaphore(n)`, **before** the
instance lock and released in `finally`, so at most `n` tool calls execute at once and the rest
queue. Taking it first means a queued call holds nothing, so it never delays a `destroy` or a
`freeze` on its instance. Default `n = min(os.process_cpu_count() or 4, 16)`, which follows a
container's CPU affinity and is `None`-safe; `seahaven serve --concurrency n` is the only override
and `0` means no gate. `reset` (instance creation), `ListToolsAction` and control tools bypass it.
The gate is not reentrant; tools do not call tools through the dispatcher, so no call ever waits on
itself.

The default is not a throughput optimum, and the cap of 16 keeps a very large host from
over-subscribing. A thread inside SQLite on a warm page cache is CPU-bound (SQLite is about 7% of a
call), and on a build with the GIL that tells against the gate rather than for it: throughput is
highest at `n = 1` in every row of Phase 11's sweep — both workloads, both cache states, both
offered loads — and the cpu-count default runs at 73–82% of it, so `n = 1` serves 22–37% more calls
a second than the value the framework computes. The default stands anyway, for the reasons
`bench/results/latest.md` records: no value the sweep tried was better than this one on every axis
at once (`n = 1` waits far worse at four sessions, and beside one slow call it starves a reader, as
every binding gate size including this default does — `latest.md` §4), and following CPU affinity
is the shape that keeps the free-threaded door open, on which this measurement says nothing. That
sweep — over ProjectTracker `agency` under a cold and a warm cache — is done;
`instances.default_concurrency`'s docstring carries the corrected reasons, unfairness included, and
the override is the operator's lever.

*Corrected 2026-09-13 — this paragraph called the core count "the measured throughput optimum" and
promised a sweep as future work; both measured in Phase 11 (`bench/results/latest.md`, §3 and §6);
closes `BACKLOG.md` B21. The "SQLite is about 7% of a call" estimate was measured too and stands:
the same run bounds it above at 11%.*

## 6. Errors

Hierarchy (`errors.py`):

```
SeahavenError(Exception)          # the root: anything Seahaven raises
  ├─ WorldBug                     # framework misuse or a bug in world code; never shown to an agent
  └─ ToolError                    # anything the agent is meant to read: code, message, details
      ├─ (the world's own subclasses, defined in its errors.py and imported where raised)
      ├─ ArgumentError            # code "invalid_arguments"; .violations
      ├─ DbError                  # code "db_error"; .sqlite_message, .sqlite_code, .refusals (from the sandbox)
      └─ UnknownTool              # code "unknown_tool"; .name
```

`ToolError(code: str, message: str, details: Any = None)`; `to_dict()` gives
`{"code", "message", "details"}`. Codes are free strings owned by the world; the framework's three
are fixed and documented. Everything the framework raises for misuse — a registration error, a call
on a destroyed instance, an unserialisable result, a missing table in `describe_schema`, a bad
fixture — is a `WorldBug`.

**A world's errors are plain classes.** `errors.py` defines `ToolError` subclasses (`NotFound`,
`InvalidInput`, `Internal`, whatever the product has), each fixing its code and message format in
`__init__`; tool modules and the error handler import them. Nothing is registered on the `World`
and `Ctx` carries nothing for errors.

**Wrapping at the boundary.** `Db` catches `apsw.Error` on every statement and raises `DbError`
carrying the SQLite message and extended code, and the sandbox's classified refusal when the
statement ran under an authorizer, so world code has one exception type to catch.

**The standard error handler** scaffolded by `seahaven new`:

```python
from <pkg>.errors import Internal, InvalidInput

@world.middleware
def error_handler(ctx, call, next_):
    try:
        return next_(ctx, call)
    except seahaven.ArgumentError as e:
        raise InvalidInput.from_violations(e.violations) from e   # scaffolded classmethod
    except seahaven.DbError as e:
        raise Internal() from e
    except seahaven.ToolError:
        raise
    except seahaven.WorldBug:
        raise                                                     # let it fail loudly
    except Exception as e:
        raise Internal() from e
```

Registered first in `__init__` (outermost).

**Wire shape over OpenEnv:** the observation has `result: Any | None` and `error: {"code",
"message", "details"} | None`; exactly one is non-null. `UnknownTool` is rendered the same way (it
does not pass through middleware).

**Logging:** stdlib `logging`, loggers under `seahaven.*`. Every call logs at `INFO` with the
instance id, the world, the tool, the duration in milliseconds and the outcome (`ok` or the error
code). Tool exceptions that were not `ToolError` log at `ERROR` with `exc_info`. `destroy` and the
sweep log at `INFO` with counts. That is the whole operational surface: no counters API, no
telemetry, no network.

## 7. Reproducibility and time

- **Seed:** `instance_seed = sha256(fixture_id_or_world_name + b"\0" + caller_seed_bytes)`;
  `seed=None` means `b"default"`; an `int` seed is encoded big-endian. `Ids.random =
  random.Random(int.from_bytes(seed))`. Instance ids themselves are `uuid4()` from the OS: identity
  is not data and must be unique across processes. SQL's `random()` and `randomblob()` are overridden
  per connection from the same instance seed, each door on its own label
  (`sha256(seed + b"\0" + stream)`), so a door replays across two runs of one seed while no two
  doors of an instance, and not `ctx.ids` either, hand out the same values.
- **Clock:** `Clock(now)` frozen; `register_clock_functions(conn, clock)` as in the reference
  implementation, including the private helper connection that evaluates SQLite's real date
  functions with `'now'` substituted, so modifiers and formats stay SQLite's. Functions registered
  with `SQLITE_INNOCUOUS | SQLITE_DETERMINISTIC`; `SQLITE_DBCONFIG_TRUSTED_SCHEMA` off so a schema
  cannot call anything not marked innocuous. Canonical text: `%Y-%m-%dT%H:%M:%S.mmmZ`.
- **One format across every door.** Values the clock functions return in SQL compare and sort
  correctly against the canonical text a world stores; a test asserts `created_at >
  CURRENT_TIMESTAMP` selects exactly the rows after the frozen instant. The simplest route is for
  the `now` outputs of the overrides to return canonical text; the implementation decides.
- **Blank instance `now`:** `datetime.now(UTC)` truncated to milliseconds at creation when `now` is
  not given; the only wall-clock read in the runtime besides the sidecar's `created_at`.
- **Lints are advisory.** The wall-clock lint (`SH201`) and the `random`/`uuid4` lint (`SH203`) are
  warnings. Reproducibility is the world's responsibility, checked by ProjectTracker's tests as the
  example.

## 8. Components

Each has a document under `components/`. Responsibilities and interfaces here; internals there.

### 8.1 `components/runtime_db.md`: `Db`, connection setup, clock functions, `Ids`, sandbox

- `open_instance(path, clock, seed) -> Db`: WAL, `synchronous=NORMAL`, `foreign_keys=ON`,
  `DEFENSIVE`, `TRUSTED_SCHEMA=0`, `busy_timeout=0`, SQLite's own `sqlite3_limit` defaults
  untouched, clock functions, randomness functions on `INSTANCE_STREAM`, `load_extension` disabled.
- `open_inspection(path, clock, seed, stream) -> Db`: `mode=ro` URI, write-denying authorizer, clock
  functions, randomness functions on the caller's stream label. The write-denying authorizer is
  internal to this connection and is installed once, permanently.
- `sandbox.Authorizer(tables, read_only)` and `sandbox.run_statement(db, sql, params, *, authorizer,
  max_rows, max_bytes) -> SqlResult`: the reference implementation's design. Default-deny;
  `ALLOWED_FUNCTIONS` allowlist; refusals recorded and classified into `DbError.refusals`; execution
  tracer for single-statement, `is_readonly` and column names; the fixed value-size cap set for the
  duration of the statement and restored after; row and byte caps only when given. This is the
  component every SQL door shares, and it is public.

### 8.2 `components/world_and_dispatch.md`: `World`, `Tool`, `Call`, `Ctx`, middleware, errors

Sections 3, 4 and 6 above, in full: the argument-model builder and its edge cases (optional
parameters, `Annotated`, enums, nested models, defaults that are mutable), the chain builder, `Ctx`
copying per call, `ctx.state` lifetime, serialisation rules, the reserved names, the `control`
flag.

### 8.3 `components/fixtures_instances.md`: fixtures, freeze, instances, working directory, changesets

- Sidecar as a pydantic model (`FixtureMeta`): fields of functional spec §9.1, `format_version`
  included; `fixture.yaml` written with `yaml.safe_dump(sort_keys=True)`.
- `freeze(instance, id, description)`: lock; conformance check (live `sqlite_master` versus a fresh
  in-memory build of the DDL, normalised; shadow tables and `sqlite_*` ignored); `VACUUM INTO
  '<fixtures>/.pending-<id>/state.sqlite'`; sidecar; chmod `0o444`; rename into place. Refuses an
  existing id.
- `verify(fixture)`: SHA-256 of the file against the sidecar, cached per process on `(path, mtime,
  size, hash)`.
- Instance creation: `shutil.copyfile` (or `os.copy_file_range`/reflink where available,
  transparently); `open_instance`; startup hooks under the lock; APSW `Session` attached after them
  to every world table (everything in `sqlite_master` of type table that is not `sqlite_*`, not an
  FTS5 virtual or shadow table, and not named in `World(untracked_tables=...)`), so seed rows a hook
  writes are not agent changes; register.
- Working directory: `World(work_dir=...)` or `<tempdir>/seahaven/<pid>/<world>/<instance-id>/`.
  With the default location, on first instance creation per process, sibling
  `<tempdir>/seahaven/<other-pid>/` directories whose pid is not alive are removed (the sweep); a
  work directory the caller named is never swept. Destroy: lock, close handles, `rmtree`.
- Changesets: `session.changeset()` rendered through `apsw.Changeset.iter` into `Change` records;
  `key` is the primary-key columns from `pragma_table_info`; `before`/`after` carry only the columns
  the change holds (`apsw.no_change` omitted), so an update shows the changed columns plus the key.
  Cumulative from creation. `start_session` raises `WorldBug` naming the table if asked to attach
  one without an explicit primary key, since it would record nothing.
- `bulk()`: lock plus one transaction for the block; yields the instance's own `Ctx` with
  `call=None`; commits on exit, rolls back on an exception. Nothing is disabled afterwards.

### 8.4 `components/helpers_and_control.md`: `run_sql`, `describe_schema`, control tools, FTS5 support

- `run_sql(...)` builds a `Tool` whose `fn(ctx, query: str)` calls `sandbox.run_statement` on
  `ctx.db` with an `Authorizer(tables + ["sqlite_master", "sqlite_schema", "json_each",
  "json_tree"], read_only)` constructed inside the call; result `{columns, rows, row_count,
  truncated}`; bytes rendered base64. `read_only=False` allows `INSERT`/`UPDATE`/`DELETE` on the
  listed tables only; the tool runs in the call's transaction either way.
- `describe_schema(...)`: `pragma_table_xinfo` with `hidden <> 1` (every declared column, generated
  columns included; only a virtual table's hidden columns are dropped) and
  `pragma_foreign_key_list`, from `ctx.db`, for the listed tables only; ordered by table then column
  position.
- Control tools (`control.py`): `controller_run_sql(sql: str, params: list[SqlValue] | None = None)`
  and `controller_changes()`, thin wrappers over `Instance.changes()` and over a **second**
  read-only handle of the instance's own (`Instance._control_db()`) — never over
  `Instance.inspect()`, whose whole promise is that a caller reads through it without the instance
  lock, and whose authorizer a control read would therefore be changing under another thread's
  cursor (`components/helpers_and_control.md` §3). Registered on every `World` with `control=True`;
  `Instance.tools()` never lists them and `Instance.call` always reaches them. `step` refuses them
  as `UnknownTool` unless `serve` was given `--include-control-tools`.

  *Corrected 2026-09-13 — this bullet named `Instance.inspect()` as the control tools' handle,
  which Phase 4 replaced after its round-1 review found the deadlock; closes `BACKLOG.md` B8, whose
  own list of affected artifacts did not reach this one.*
- FTS5 awareness lives in one function, `db.shadow_tables(conn)`: for every `CREATE VIRTUAL TABLE
  ... USING fts5`, every `sqlite_master` table whose name begins with the FTS5 table's name and an
  underscore. Used by the session, conformance, the lint and the authorizer's default deny list.

### 8.5 `components/openenv.md`: the environment class, app, serve

- `SeahavenEnv(Environment)` per world: `SUPPORTS_CONCURRENT_SESSIONS = True`; `action_cls` is
  `CallToolAction` (the server still delivers `ListToolsAction`; a union breaks it);
  `observation_cls = SeahavenObservation(tool_name, result, error, done=False, reward=None, and the
  inherited metadata)`; `reset(seed=None, episode_id=None, *, fixture: str | None = None, now: str |
  None = None, **startup_kwargs)` creates the instance (destroying a previous one on the same
  session); `step()` dispatches `ListToolsAction` to `instance.tools()` and `CallToolAction` to
  `instance.call`, rendering `ToolError` into `error` and anything that is not a `SeahavenError` into
  a fixed generic error observation after logging it, while a `WorldBug` propagates so the author's
  bug is loud; `state` returns `episode_id`, `step_count`, `fixture`, `now`,
  `world`; `close()` destroys the instance.
- `app(world, *, include_control_tools=False, max_concurrent_envs=500, session_timeout=3600.0)`
  returns the ASGI app via OpenEnv's `create_app`, with metadata from the world and the package's
  top-level `README.md`.
- One world per server process, many instances: one environment class behind `/ws`, one session
  per instance, the standard OpenEnv shape. There is no multi-world mode; a world package is one
  world and `seahaven serve` finds it by the package convention.
- Unknown `reset` keyword: `SeahavenEnv.reset` accepts `**kwargs` and raises `WorldBug` naming
  the unknown keys when no startup hook accepts them; the client receives an error frame and the
  session stays open for another `reset`.

### 8.6 `components/cli_and_check.md`: `seahaven new`, `check`, `fixture`, `serve`; the lints

- argparse, one subcommand module each; exit code 1 on any error finding, 0 otherwise; output is
  plain text, one finding per line: `<code> <path>:<line> <message>  fix: <sentence>`.
- World discovery: read `pyproject.toml` from the current directory upward; `[project] name`
  normalised (`-` to `_`); `importlib.import_module`; attribute `world`; type check.
- `check` rules, each with a stable code (`SH1xx` DDL, `SH2xx` code, `SH3xx` package, `SH4xx`
  fixtures, `SH5xx` the package's `World`): DDL rules over an in-memory build (from `World`); AST
  walk of every module in the package for `datetime.now`, `datetime.utcnow`, `date.today`,
  `time.time`, `time.monotonic`, `time.perf_counter` outside `middleware/` (warning); `random`
  module and `uuid.uuid4()` use (warning); an empty tool description (warning); import coverage
  (`pkgutil.walk_packages` under `tools` and `middleware` versus modules imported by importing the
  package); every fixture's sidecar validates, `file_sha256` matches, `schema_hash` matches the
  world, `now` parses. A `SeahavenError` raised while constructing the `World` is caught and
  reported as a finding with its message, never as a traceback line.
- `docs`: prints the directory of the bundled docs for the installed version
  (`importlib.resources.files("seahaven") / "docs"`), and nothing else.
- `new <name> [--hub]`: renders `cli/templates/` (layout of functional spec §2.1, one table,
  `get_item` and `create_item` tools, the error handler, `errors.py`, `world.py`, `openenv_app.py`,
  a `README.md`, `AGENTS.md`, `.gitignore`, one test, an empty `fixtures/`) and stops. Nothing is
  imported and no fixture is built. `--hub` adds the `openenv push` files.
- `fixture list|freeze|fork` as functional spec §18; `--run module:function` is imported and called
  with the `Instance`.
- `serve` as functional spec §18, delegating to `openenv/serve.py`.

### 8.7 `components/pytest_and_docs.md`: the pytest plugin and the bundled docs

- Entry point `pytest11`; `pytest_configure` registers the `seahaven` marker; `world` fixture
  (session scope) discovers as the CLI does, from `config.rootpath`; `instance` fixture (function
  scope) reads the closest `seahaven` marker, creates `world.instance(fixture, seed=..., **kwargs)`,
  yields, destroys; a missing marker fails with the example line.
- Docs: `seahaven/docs/*.md` shipped as package data; index, concepts, API reference, lint codes,
  the ProjectTracker walkthrough. Every page is hand-written. Written in the later phase; the layout
  is fixed now so `seahaven docs` and `AGENTS.md` can point at it.

### 8.8 `components/projecttracker.md`: the reference world

Schema, the 25 world tools with their signatures plus the two helpers, `errors.py`, the error
handler, the three fixtures and the generator script, the tests.

## 9. Technical decisions worth stating

1. **The authorizer denies and never ignores.** `SQLITE_IGNORE` leaks counts.
2. **Per-statement toggling on the shared connection** (the sandbox's authorizer and the fixed
   `SQLITE_LIMIT_LENGTH` cap) is always inside `try/finally` and restored before the lock is
   released. The sandbox owns this; nothing else touches `conn.authorizer`.
3. **A tool does not declare itself read-only.** World code is trusted, so there is no per-call
   write-denying authorizer and nothing about read-only on the wire. `run_sql(read_only=...)` is a
   sandbox mode for agent SQL and is unrelated.
4. **One transaction per call by default** through APSW's connection context manager; nested
   `db.transaction()` are savepoints. `transaction=False` skips the outer `with`.
5. **Refusals are classified by what the authorizer recorded**, never by exception type or message
   text (APSW's hierarchy is flat and messages are not an API).
6. **The clock override evaluates SQLite's own functions** on a helper connection rather than
   reimplementing date arithmetic.
7. **Freeze uses `VACUUM INTO`** so the live instance is untouched and the output is compact and
   journal-free.
8. **Changesets come from the session extension**, attached at creation to every world table, not
   from diffing files.
9. **Argument validation is pydantic and nothing else.** No second JSON-schema validator in the
   stack.
10. **The framework owns no tables of its own.** Schema hash and world version live in the
    sidecar; blob storage is not the framework's concern, and a world that needs it keeps blobs in
    its own tables.
11. **OpenEnv's reserved names** (`reset`, `step`, `state`, `close`) cannot be tool names.

## 10. Testing strategy

- **Framework unit tests** per module under `tests/`, pytest, no network. The sandbox carries an
  attack suite (`tests/test_sandbox.py`: every refusal class, the `SQLITE_IGNORE` regression,
  quadratic builtins under the value cap, multi-statement payloads, case-folded table names). Clock
  tests cover every overridden function, `DEFAULT` clauses in triggers, and that clock values
  compare correctly against stored timestamps. Fixture tests cover freeze determinism
  (byte-identical `VACUUM INTO` on one build, content hash as the fallback), hash verification,
  schema-hash refusal, fork chains. Instance tests cover lock and destroy races, sweep ordering,
  changeset rendering for insert/update/delete with composite keys.
- **Dispatch tests**: argument-model derivation for every supported annotation shape, `extra`
  rejection, `Annotated` metadata in schemas, chain order, `transaction=False`, error wrapping,
  serialisation of models and datetimes.
- **OpenEnv end-to-end**: start the app in-process (ASGI test client and a real uvicorn on a free
  port), drive it with the stock `EnvClient`: reset with fixture and startup kwargs, list tools
  (control tools absent), call tools, errors on the observation, state, reset again, close destroys;
  500 concurrent sessions smoke test with a trivial world.
- **CLI tests** via `subprocess` on a scaffolded world in `tmp_path`: `new` then `check` passes;
  each lint has a positive and a negative fixture; `fixture freeze/fork/list`.
- **ProjectTracker tests** are the integration suite: every tool, the fixtures' invariants, seeded
  determinism (same seed, same ids and changeset), the error handler's mapping.
- **Type checking** with `ty` on the framework, ProjectTracker and the tests; `ruff` format and
  lint; both in CI on 3.14.
- **Benchmark** (`bench/`): a harness with two workloads (one-row read, write mix) over
  ProjectTracker `agency`, run manually and before releases; results committed as
  `bench/results/latest.md`; never a gate.

## 11. Dependencies and versions

| Package | Pin | Why |
|---|---|---|
| `apsw` | `>=3.53` (bundled SQLite with FTS5, session, JSON, math) | the only SQLite binding |
| `pydantic` | `>=2.12,<3` | argument models, schema, sidecar model, serialisation; 2.12 is the floor with a cp314 wheel |
| `pyyaml` | `>=6` | sidecar |
| `openenv` | `>=0.4.2,<0.5` | server, models, client; pre-1.0 so the upper bound is deliberate |

Python `>=3.14`. **No copyleft anywhere Seahaven ships.** `scripts/check_licences.py` walks the
runtime closure *and* every extra the project declares, and fails on GPL, AGPL or LGPL in any
version or spelling, on an unrecognised identifier, and on an extra that is declared but not
installed. Permissive licences pass; so do MPL-2.0, whose copyleft is per file and does not cross a
link or a process boundary, and CC0-1.0, which is public-domain equivalent. CI runs the gate in the
environment it built with `uv sync --locked --extra serve`, which is the only environment in which
the extras half of the rule means anything.

*Corrected 2026-09-13 — this line read "All permissive; a CI step fails on any non-permissive
runtime dependency", and the gate it described evaluated markers with no extra, so `serve` — a
runtime extra since Phase 6 — was shipped and unchecked. The rule is now no copyleft rather than
permissive-only, by the maintainer's decision; the closure it covers grew rather than the bar
falling. Closes `BACKLOG.md` B15.*
