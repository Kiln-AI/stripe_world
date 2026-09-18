---
status: complete
---

# Component: fixtures and instances

Modules: `seahaven/fixtures.py`, `instances.py`, `changes.py`, `conformance.py`. This document
states their interface and behaviour.

## 1. `fixtures.py`

```python
class FixtureMeta(pydantic.BaseModel, frozen=True, extra="forbid"):
    format_version: Literal[1]    # every YAML Seahaven writes carries it
    id: str; world: str; world_version: str; schema_hash: str
    now: str                      # canonical timestamp text
    parent_id: str | None
    file_sha256: str
    created_at: str               # canonical timestamp text, wall clock
    description: str

@dataclass(frozen=True)
class Fixture:
    meta: FixtureMeta; dir: Path
    id, now, description, parent_id (properties over meta)
    state_path: Path

STATE_NAME = "state.sqlite"; SIDECAR_NAME = "fixture.yaml"
def load(fixture_dir: Path) -> Fixture                 # raises WorldBug on a malformed sidecar, and on
                                                       # any format_version but 1, naming the file
def load_all(fixtures_dir: Path) -> dict[str, Fixture] # skips dot-directories; duplicate ids are an error
def verify(fixture: Fixture) -> None                   # SHA-256 check, cached per process on (path, mtime, size, hash)
def freeze(instance: Instance, id: str, description: str, *, fixtures_dir: Path) -> Fixture
```

`freeze` (called under the instance lock by `Instance.freeze`):

1. `id` must be a single path segment (`id == Path(id).name`, no leading dot); the target directory
   must not exist.
2. `conformance.check(instance_conn, world)` (section 4); raises with every difference.
3. `VACUUM INTO '<fixtures_dir>/.pending-<id>/state.sqlite'`. `VACUUM INTO` produces a
   rollback-journal, compacted file, reads the WAL correctly, and leaves the live database
   untouched.
4. Hash the file; write `fixture.yaml` (`yaml.safe_dump(meta.model_dump(), sort_keys=True)`);
   `chmod 0o444` on the state file. The seal is what stops a live instance writing a fixture in
   place and is not optional; it is also not something `seahaven check` can verify, because git
   records only the executable bit — SH405 is the journal companions alone
   (`components/cli_and_check.md` §3). *Second sentence added 2026-09-13 with SH405's correction;
   closes `BACKLOG.md` B9.*
5. `os.rename(pending, final)`. A failure at any step removes the pending directory.

The sidecar's `now` is `instance.clock.iso()`; `parent_id` is `instance.fixture` (None for a blank
instance); `world_version` and `schema_hash` from the world; `created_at` is `Clock.wall().iso()`.

## 2. `instances.py`

```python
class Instance:
    id: str; fixture: str | None; seed: bytes; clock: Clock
    def call(self, name: str, /, **arguments: Any) -> Any             # raises ToolError subclasses
    def tools(self) -> list[dict[str, Any]]                            # control tools are never listed
    def inspect(self) -> Db                                            # read-only handle; one per instance, cached
    def changes(self) -> list[Change]
    def freeze(self, id: str, description: str) -> Fixture
    def bulk(self) -> AbstractContextManager[Ctx]
    def destroy(self) -> None
    def __enter__/__exit__                                             # exit destroys
    closed: bool

class InstanceManager:                      # one per World, created lazily; internal
    def create(self, fixture_id, *, seed, now, startup_kwargs) -> Instance
    def destroy(self, instance) -> None
    def sweep_stale_processes(self) -> int
    def close(self) -> None                 # destroy everything; registered with atexit
```

**Locking.** One `threading.RLock` per instance. `call`, `changes`, `freeze`, `bulk`, `destroy` and
the opens inside `inspect()` and `_control_db()` take it; reads through the `inspect()` handle after
that open do not — that handle is the caller's, to read from whatever thread it likes. It is
re-entrant because a control tool is called with the lock already held and then asks the instance
for something — its changeset, its control handle — that takes it again on the same thread.
Framework code never blocks on the `InstanceManager` lock while holding an instance lock. The
caller owns one ordering the framework cannot: do not read through the `inspect()` handle
concurrently with `destroy()`.

*Corrected 2026-09-13 — this paragraph gave the `RLock`'s reason as the control tools reading
through `inspect()`, which Phase 4 replaced with a handle of their own; see §2.4. Closes
`BACKLOG.md` B8.*

### 2.1 Creation (`InstanceManager.create`)

```
instance_id = str(uuid.uuid4())
dir = work_dir / instance_id; dir.mkdir(parents=True)
try:
    if fixture_id is None:
        build_blank(dir/STATE, world.schema).close()
        clock = Clock.from_iso(now) if now else Clock.wall()
        seed_source = world.name
    else:
        check_id(fixture_id)                              # freeze's rule: one path segment, no leading dot
        fixture = world_fixture(fixture_id)               # by directory name; no scan
        verify(fixture)
        if fixture.meta.schema_hash != world.schema_hash: raise WorldBug("fixture 'x' was frozen from a different schema; regenerate it")
        if now is not None: raise WorldBug("now= applies to blank instances only")   # reset(now=) is post-V1
        copy(fixture.state_path, dir/STATE)               # shutil.copyfile; os.copy_file_range on Linux, transparently
        clock = Clock.from_iso(fixture.now); seed_source = fixture_id
    seed_bytes = instance_seed(seed_source, seed)     # before the open: the connection carries it too
    db = open_instance(dir/STATE, clock, seed_bytes)  # its random() and randomblob() draw from it
    ctx = Ctx(db, clock, Ids(seed_bytes), {}, InstanceInfo(...))
    instance = Instance(..., world=world)                 # the chain and registry are read from the world at call time
    with instance.lock:
        run_startup_hooks(world, ctx, startup_kwargs)     # 2.2
        session = start_session(db.conn, world)           # section 3; after the hooks, so seed rows are not agent changes
    register(instance)
except BaseException:
    close everything opened; rmtree(dir); raise
```

`world_fixture(fixture_id)` applies `freeze`'s id rule (a single path segment, no leading dot)
before it touches the filesystem, so an id off the wire cannot become a path.

The working directory: `World(work_dir=Path)` sets it, and such a directory is the caller's — used
exactly as given and never swept. Otherwise it is
`Path(tempfile.gettempdir()) / "seahaven" / str(os.getpid()) / world.name`, created on first use.

### 2.2 Startup hooks and `reset` kwargs

Before anything is copied: `unknown = set(startup_kwargs) - world.accepted_startup_kwargs`; if
non-empty and no hook takes `**kwargs`, raise `WorldBug(f"unknown reset argument(s): {sorted(unknown)}")`.
Hooks run in registration order, each called as `hook(ctx, **{k: v for k, v in startup_kwargs.items()
if k in hook.accepts or hook.takes_var_kwargs})`, inside one transaction (a hook that writes seed
rows is atomic with the others). A hook that raises aborts creation and the instance is removed.
The changeset session is attached after they have all run, so rows a hook writes are part of the
starting state and not agent changes.

### 2.3 Calls, control tools, tools list

`Instance.call(name, **arguments)`:

```
tool = world.tools.get(name)
if tool is None: raise UnknownTool(name)
with gate(bypass=tool.control):                           # the concurrency gate, taken before the lock
    with self._held():                                    # lock + closed check
        ctx0 = self.ctx.with_call(Call(name, arguments, tool))
        if tool.control: return control.dispatch(self, ctx0)  # bypasses the chain
        return world.chain(ctx0, ctx0.call)
```

The gate is a process-wide `threading.BoundedSemaphore(n)`, `n` defaulting to
`min(os.process_cpu_count() or 4, 16)` and set by `seahaven serve --concurrency n` (`0` disables
it). It is taken **before** the instance lock, so a queued call holds nothing and never delays a
`destroy` or a `freeze`. Instance creation, `tools()` and the control tools bypass it.

`tools()`: `[t.listing() for t in world.tools.values() if not t.control]`. Control tools are never
listed, in-process or over OpenEnv; `Instance.call` always reaches them, and it is `step` that
refuses them when `serve` was started without `--include-control-tools`.

### 2.4 `inspect`, `_control_db`, `changes`, `bulk`, `freeze`, `destroy`

- `inspect()`: opens `open_inspection(path, clock, seed, INSPECTION_STREAM)` once and caches it;
  returns the `Db`. The handle is closed on destroy. Under the lock for the open; reads afterwards
  are the caller's and do not take the instance lock (a read-only connection on a WAL database sees
  a consistent snapshot per statement). Reading through the handle concurrently with `destroy()` is
  the caller's ordering to get right.
- `changes()`: under the lock, `session.changeset()` rendered (section 3).
- `bulk()`: under the lock and one transaction, yields the instance's own `Ctx` (with `call=None`);
  on exit it commits, or rolls back if the block raised. Nothing is disabled and nothing is wrapped.
  Startup hooks are not re-run.
- `freeze(id, description)`: under the lock; delegates to `fixtures.freeze`.
- `destroy()`: `manager.unregister(self)` first, then `with lock: closed = True; close both
  read-only handles (the caller's from `inspect()` and the control tools' from `_control_db()`,
  either of which may never have been opened), session, db`, then `rmtree(dir,
  ignore_errors=True)`. Idempotent.
- `_control_db()`: a **second** read-only handle, opened once under the lock by the same
  `open_inspection(path, clock, seed, CONTROL_STREAM)` and closed on destroy, reached only by the
  control tools. The stream label is what stops this door and `inspect()`'s handing out the same
  `random()` values from the one instance seed. Not the `inspect()` handle: a control read goes
  through `sandbox.run_statement`, which sets the connection's authorizer and its value limit for
  the length of one statement, and `inspect()` is the handle a caller reads through *without* the
  instance lock. Two threads on one connection, one changing its authorizer while the other steps a
  cursor, wedge inside SQLite and take the interpreter with them, because the thread waiting on the
  connection holds the GIL.
- Control dispatch: `control.dispatch(instance, ctx)` validates the arguments like any tool's, runs
  the control function and serialises the result. `controller_run_sql` and `controller_changes` are
  thin wrappers over `_control_db()` and `changes()` and own no SQL or rendering of their own. A
  control call takes the instance lock like any call, and then asks the instance for what it needs —
  its changeset, its control handle — with that lock already held, which is why the lock is an
  `RLock`: the re-entry is same-thread, not a claim about lock-free reads.

*Corrected 2026-09-13 — the control tools' connection, the `RLock`'s reason and `destroy()`'s fourth
handle, measured in Phase 4 (whose round-1 review found the deadlock and whose plan records the
fix); closes `BACKLOG.md` B8.*

### 2.5 Sweep

On the first `create` in a process, and only when the default working directory is in use: list
`<tempdir>/seahaven/*/`; for each entry whose name is an integer pid that is not alive
(`os.kill(pid, 0)` raising `ProcessLookupError`; on permission errors assume alive), `rmtree`. Also
registers `atexit(manager.close)`. A live process's directory is never touched, so two processes on
one machine cannot sweep each other and a directory being built is never removed mid-creation. A
`work_dir` the caller configured is never swept: it is theirs.

## 3. `changes.py`

```python
@dataclass(frozen=True)
class Change:
    table: str; op: Literal["insert", "update", "delete"]
    key: dict[str, Any]; before: dict[str, Any] | None; after: dict[str, Any] | None
    def to_dict(self) -> dict[str, Any]

def start_session(conn: apsw.Connection, world: World) -> apsw.Session
# attaches every world table except FTS5 virtual and shadow tables and the world's untracked_tables
def render(changeset: bytes, conn: apsw.Connection) -> list[Change]
```

`start_session` raises `WorldBug` naming the table if asked to attach one with no explicit primary
key: the session would record nothing for it and the changeset would silently miss its writes.
A world lists a table in `World(untracked_tables=...)` to keep it out of the session deliberately;
two lines here skip those names.

`render`: iterate `apsw.Changeset.iter(changeset)`; for each `TableChange`: column names from
`pragma_table_info(table)`; primary-key column indexes from the same pragma (`pk > 0`); `key` is
built from `new` on insert, `old` otherwise; `before`/`after` include only columns whose value is
not `apsw.no_change`, so an update shows the changed columns (plus the key). Order is the
changeset's own (by table, then rowid), which is deterministic for a given sequence of writes.
Blob values are base64 text.

What a changeset means, stated once because evals grade on it: it is the net difference between the
fixture and the current state, not a log of calls. A write that leaves a value unchanged records
nothing; an insert followed by an update of the same row is one insert; a call that rolled back
leaves no trace.

## 4. `conformance.py`

```python
def schema_map(conn) -> dict[str, str]           # name -> whitespace-normalised sql, excluding sqlite_* and FTS5 shadow tables
def differences(conn, world) -> list[str]        # against a fresh in-memory build of world.schema
def check(conn, world) -> None                   # raises WorldBug listing differences
```

Textual comparison over `sqlite_master.sql`, deliberately stricter than SQLite's own equivalence.
FTS5 shadow tables are excluded from both sides (they are created by the module and their SQL text
is not in the DDL).

## 5. Test plan

- `test_fixtures.py`: freeze produces a rollback-journal, read-only, compacted file with a valid
  sidecar; byte-identical output for identical content on one build (with content hash as the
  documented fallback); freeze refuses an existing id, a non-conforming schema (extra table,
  altered column), a bad id; `verify` refuses a modified file and caches the good case; `load_all`
  refuses duplicate ids and skips `.pending-*`; fork chain of three preserves `parent_id`.
- `test_instances.py`: blank instance `now` default is wall time truncated to ms, explicit `now`
  honoured, `now` with a fixture refused; a fixture id with a path separator or a leading dot
  refused before the filesystem is touched; schema-hash mismatch refused with the regeneration
  message; seeds: same seed same ids across two instances, different fixtures different streams;
  unknown startup kwarg refused before any file is copied (directory absent afterwards); hooks run
  in order and atomically; `tools()` never lists control tools; `call` on a destroyed instance
  raises `WorldBug`; destroy during an in-flight call waits for it (thread test); a control call
  that reads through `inspect()` under the
  instance lock completes (the re-entrancy case); `inspect` sees committed writes and not
  uncommitted ones; `bulk` writes are committed once; context manager destroys; working directory
  layout; sweep removes a fake dead-pid directory, leaves a live one, and never touches a
  configured `work_dir`; 200 instances from one fixture in one process.
- `test_changes.py`: insert/update/delete rendering with single and composite keys; update carries
  only changed columns plus key; a failed call leaves no trace; a no-op update records nothing; an
  insert then an update of the same row collapses to one insert; rows written by a startup hook are
  absent; a table listed in `untracked_tables` is absent; a table without an explicit primary key is
  refused at `start_session`; FTS5 table writes do not appear; cumulative across calls; blob column
  as base64.
- `test_conformance.py`: identical schema passes; column order change is flagged; extra index is
  flagged; shadow tables ignored.
