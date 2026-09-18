---
status: draft
---

# Phase 3: fixtures, instances, changesets

## Overview

The in-process API becomes whole. Phase 1 built the runtime a call arrives at and Phase 2 built the
path a call takes; neither could create the thing a call is made *on*. This phase adds the four
modules of `components/fixtures_instances.md` — `fixtures.py`, `instances.py`, `changes.py`,
`conformance.py` — and the two methods Phase 2 deferred on `World` (`instance`, `fixtures`), so that
after it `world.instance(...)`, `call`, `tools`, `inspect`, `changes`, `bulk`, `freeze` and
`destroy` all work against real files on disk.

The three things this phase owns that nothing before it could:

- **A fixture is a directory, not an object.** `state.sqlite` plus a signed sidecar, minted only by
  `freeze`, verified by hash before it is ever copied, and never opened in place.
- **An instance is a private copy plus a lock.** Creation materialises the file, runs the startup
  hooks in one transaction, and only then attaches the changeset session, so seed rows are starting
  state and not agent changes. Every operation that touches the instance takes its `RLock`; the
  concurrency gate is taken *before* the lock so a queued call holds nothing.
- **A changeset is the net difference from the fixture**, rendered from the APSW session extension,
  not a log of calls.

Phase 2's deferred per-call `INFO` log lands here too: `Instance.call` is the only thing that sees
both the duration and the outcome.

## Steps

1. **`src/seahaven/conformance.py`** — the live schema against the world's DDL.

   ```python
   def schema_map(conn: apsw.Connection) -> dict[str, str]
   def differences(conn: apsw.Connection, world: World) -> list[str]
   def check(conn: apsw.Connection, world: World) -> None      # raises WorldBug listing them
   ```

   `schema_map` is every `sqlite_master` row with SQL text, keyed by name, with whitespace collapsed
   — tables, indexes, triggers and views, so an extra index is a difference. Excluded from both
   sides: names beginning `sqlite_`, and FTS5 shadow tables and anything defined on one
   (`db.shadow_tables`), because the FTS5 module creates them and their text is not in the DDL.
   `differences` builds `world.schema` in a fresh in-memory database and compares the two maps,
   returning one line per name that is only in the instance, only in the schema, or textually
   different. Textual comparison, deliberately stricter than SQLite's own equivalence.

2. **`src/seahaven/changes.py`** — the changeset, and what it renders to.

   ```python
   @dataclass(frozen=True)
   class Change:
       table: str; op: Literal["insert", "update", "delete"]
       key: dict[str, Any]; before: dict[str, Any] | None; after: dict[str, Any] | None
       def to_dict(self) -> dict[str, Any]

   def start_session(conn: apsw.Connection, world: World) -> apsw.Session
   def render(changeset: bytes, conn: apsw.Connection) -> list[Change]
   ```

   `start_session` attaches every world table (`db.world_tables`) except virtual tables — the
   session extension cannot track one — and except the names in `World(untracked_tables=...)`. A
   table with no explicit primary key is refused with a `WorldBug` naming it: APSW attaches it
   happily and then records nothing for it, so the changeset would silently miss its writes.

   `render` iterates `apsw.Changeset.iter`; column names and primary-key positions come from
   `pragma_table_info` (`pk > 0`, in `pk` order); `key` is built from `new` on an insert and `old`
   otherwise; `before`/`after` carry only the columns whose value is not `apsw.no_change`; `bytes`
   are base64 text.

3. **`src/seahaven/fixtures.py`** — the artifact and the only way to mint one.

   ```python
   STATE_NAME = "state.sqlite"; SIDECAR_NAME = "fixture.yaml"; PENDING_PREFIX = ".pending-"

   class FixtureMeta(pydantic.BaseModel, frozen=True, extra="forbid"):   # §9.1's ten fields
   @dataclass(frozen=True)
   class Fixture:  meta, dir; id/now/description/parent_id properties; state_path

   def check_id(id: str) -> None                        # one path segment, no leading dot
   def load(fixture_dir: Path) -> Fixture
   def load_all(fixtures_dir: Path) -> dict[str, Fixture]
   def verify(fixture: Fixture) -> None
   def freeze(instance, id, description, *, fixtures_dir: Path) -> Fixture
   ```

   `load` raises `WorldBug` naming the file for a missing, unparseable or invalid sidecar, and for
   any `format_version` but 1. `load_all` skips dot-directories (so `.pending-*` is invisible) and
   refuses two sidecars claiming one id. `verify` hashes `state.sqlite` and compares it with
   `file_sha256`, cached per process on `(path, mtime_ns, size, expected hash)`; a mismatch names
   the fixture and says to fork it.

   `freeze` runs the five steps of §1 in order: the id rule and a target that must not exist; the
   conformance check; `VACUUM INTO` a `.pending-<id>/` sibling; hash, sidecar
   (`yaml.safe_dump(sort_keys=True)`), `chmod 0o444`; `os.rename` into place. Any failure removes
   the pending directory. `now` is the instance's clock, `parent_id` its fixture, `created_at`
   `Clock.wall().iso()`.

4. **`src/seahaven/instances.py`** — the instance, its manager, the working directory, the gate.

   ```python
   def default_concurrency() -> int                     # min(os.process_cpu_count() or 4, 16)
   def set_concurrency(n: int) -> None                  # `seahaven serve --concurrency`; 0 disables
   @contextmanager
   def gate(*, bypass: bool) -> Iterator[None]

   class Instance:                                      # id, fixture, seed, clock, world, ctx, db,
       def call(self, name: str, /, **arguments: Any) -> Any          # dir, state_path, closed
       def tools(self) -> list[dict[str, Any]]
       def inspect(self) -> Db
       def changes(self) -> list[Change]
       def freeze(self, id: str, description: str) -> Fixture
       def bulk(self) -> AbstractContextManager[Ctx]
       def destroy(self) -> None
       def __enter__/__exit__

   class InstanceManager:
       def create(self, fixture_id, *, seed, now, startup_kwargs) -> Instance
       def destroy(self, instance) -> None
       def unregister(self, instance) -> None
       def sweep_stale_processes(self) -> int
       def close(self) -> None
   ```

   Creation follows §2.1, including the order of its refusals: unknown startup kwargs first, then
   the fixture id rule, `verify`, the schema hash, and `now=` with a fixture — so nothing is copied
   for a creation that cannot succeed. The hooks run inside one transaction; the session is attached
   after them; the instance is registered last, and any failure closes what was opened and removes
   the directory.

   `call` is §2.3: `UnknownTool` for a name the world does not have, then the gate (bypassed by a
   control tool), then the lock and the closed check, then the chain — or direct control dispatch.
   It wraps the whole thing in the `INFO` log `architecture.md` §6 asks for.

   The gate is a process-wide `BoundedSemaphore`, read into a local before it is acquired so that a
   concurrent `set_concurrency` cannot make an acquire and its release disagree.

   The sweep runs once per process, only when the default working directory is in use, and removes
   `<tempdir>/seahaven-<uid>/<pid namespace>-<pid>/` siblings whose pid is not alive (the root is
   per user and the name carries a namespace; see the decisions below). It reads the root through
   the same checked descriptor the creation path uses, and judges an entry by `lstat`, so a planted
   symlink is neither followed nor swept. `os.kill(pid, 0)` is asked only about a positive integer
   name carrying this namespace's prefix: pid `0` is the caller's whole process group, and
   signalling it to ask whether it exists would be a bad way to find out.

5. **`src/seahaven/world.py`** — add the two deferred methods and the lazy manager:

   ```python
   def instance(self, fixture: str | None = None, *, seed: int | bytes | None = None,
                now: str | datetime | None = None, **startup_kwargs: Any) -> Instance
   def fixtures(self) -> list[Fixture]                  # every fixture in fixtures_dir, by id
   ```

6. **`src/seahaven/__init__.py`** — export `Instance`, `Fixture` and `Change`, which
   `architecture.md` §1's list carries and which now exist.

## Deviations and decisions a reviewer should check

- **Control dispatch stays in `instances.py` for this phase.** §2.3 writes
  `if tool.control: return control.dispatch(self, ctx0)`, and `control.py` is Phase 4
  (`implementation_plan.md`), because the two control tools it holds are wrappers over `inspect()`
  and `changes()` that need more than a `Ctx`. The *behaviour* §2.4 states is Phase 3's and is
  implemented here as `Instance._dispatch_control`: validate the arguments like any tool's, run the
  function, serialise, with no chain, no transaction, no gate, under the instance lock. Phase 4
  replaces the body with a call to `control.dispatch`. It is tested through a real `control=True`
  tool on a real world, including the re-entrancy case the `RLock` exists for. The three steps are
  `invoke`'s own, in `invoke`'s order, and share its code: `call.py`'s serialiser is now public
  (`serialise`) and is what renders a control result, so a control tool is held to the same rules
  about `bytes` and `set`s as every other tool. What a control tool may *return* — whether the blob
  a `Change` carries reaches an eval as base64 or otherwise — is Phase 4's to decide when it writes
  the two control tools; it is not decided here by the accident of a different serialiser.
- **`before`/`after` are exactly the non-`no_change` columns, and nothing is added to them.** §3
  says "an update shows the changed columns (plus the key)", which is what `old` gives for free (a
  changeset's `old` holds the primary key columns and the old values of the changed ones) but not
  what `new` does: `new` marks the key `no_change`. Rather than synthesising a key into `after` —
  putting a value in a row dict that the change does not carry — the key is in `key`, where §3 puts
  it, and `after` is the changed columns alone.
  `test_an_update_carries_the_changed_columns_and_the_key` pins both halves.
- **Primary-key positions come from `pragma_table_info`, as §3 says, not from APSW's own
  `TableChange.pk_columns`.** The pragma is what the spec names, and it gives the key columns in key
  order rather than as a set; `test_a_composite_key_is_rendered_in_key_order` uses
  `PRIMARY KEY (b, a)`, where the two disagree.
- **`start_session` skips every virtual table, not only FTS5 ones.** §3 says "FTS5 virtual and
  shadow tables"; the session extension cannot track any virtual table, so the rule is written
  against the property that matters. FTS5 is the only one a V1 world is expected to have.
- **Every refusal in creation happens before the working directory is made**, where §2.1's sketch
  makes the directory first and refuses inside the `try`. Both leave nothing behind (the `except`
  removes the directory), but hoisting them means a bad fixture id or an unknown `reset` argument
  never touches the filesystem at all, which is what the test plan's "before any file is copied"
  asks for.
- **The `Instance` object is built after its startup hooks have run**, where §2.1 builds it first
  and runs the hooks under `instance.lock`. Nothing else can hold a reference to an instance that
  has not been returned or registered, so that lock excludes nothing; building afterwards makes the
  session a plain attribute rather than an optional one that every later method would have to
  re-check. The hooks still run before the session is attached and before the instance is
  registered, which is what the ordering is for.
- **`load` does not check that the sidecar's `id` equals the directory name.** §9.1 says they are
  equal, but §1 also requires `load_all` to refuse duplicate ids, which cannot happen if `load`
  enforces the equality. The fixture lint (`lint/fixtures.py`, Phase 7) is where a hand-edited
  sidecar is called out; `load_all` is where a duplicate is.
- **`load_all` on a missing `fixtures_dir` returns `{}`.** `World.fixtures()` is the listing of a
  world that may have none, and `world_and_dispatch.md` §1.1 is explicit that construction never
  fails on the directory and that only a *named* fixture that cannot be found is an error.
- **`freeze` removes a stale `.pending-<id>` before it creates one.** Not in §1's five steps, which
  assume the sibling is free. A process that died mid-freeze would otherwise leave that id
  unfreezable for ever, with `FileExistsError` as the explanation. The pending directory is scratch
  space the rename empties, so taking it over is safe; two threads freezing *the same id* at once
  is not (they would fight over it, and one already loses the `os.rename`), and that is unsupported
  the way `architecture.md` §5.1 calls registration during calls unsupported.
- **`Instance.tools()` takes neither the gate nor the lock and does not check `closed`.** §2 lists
  what takes the lock and `tools()` is not in it: it reads the world's registry, not the instance.
- **The per-call `INFO` log measures the gate wait too.** The duration is wall time from entry to
  `Instance.call`, which is the latency a caller saw; a call that spent 40 ms queued was slow
  whether or not its tool was. The outcome is `ok`, a `ToolError`'s code, or the exception's class
  name for anything else (§6 names only the first two, and an exception that is not a `ToolError`
  has no code to log).
- **`destroy` logs at `INFO` too**, which `architecture.md` §6 asks for ("`destroy` and the sweep
  log at `INFO` with counts") and `components/fixtures_instances.md` does not mention. One line per
  destroy, and only for the first call on an instance, since `destroy` is idempotent.
- **`seed` on `Instance` is the derived instance seed**, the same bytes as `ctx.instance.seed`, not
  the `seed=` the caller passed. §2's `Instance.seed: bytes` and `ctx.py`'s `InstanceInfo.seed`
  agree on the type, and an `int` seed could not be the same field otherwise.
- **`atexit.register(manager.close)` happens when the manager is constructed**, where §2.5 puts it
  in the first `create`. A manager is constructed only by `World._instances()`, which only
  `World.instance(...)` calls, so it is the same moment — and it removes a second once-per-manager
  guard whose only effect was to hide whether the sweep's own once-per-*process* guard worked (the
  mutation sweep found exactly that: each guard kept the other's mutants alive).
- **`Instance` exposes `world`, `ctx`, `db`, `dir` and `state_path`** beyond the surface §2 lists.
  `freeze` needs the world and the connection, `bulk` yields the `ctx`, and `architecture.md` §2's
  type table names the path and the `Db` among an instance's members. `db` is the instance's own
  writable connection: using it outside the lock is the caller's to get right, as `inspect()` is.
- **`World.instance`'s `fixture` parameter defaults to `None`**, where §1 writes it without a
  default, so `world.instance()` is a blank instance and `world.instance("agency")` is the fixture
  one. Nothing is ambiguous: a startup hook may not be given a parameter named `fixture`
  (`world_and_dispatch.md` §1.2), so the name cannot collide with `**startup_kwargs`.
- **The default working root is `<tempdir>/seahaven-<uid>/`, not `<tempdir>/seahaven/`.** §2.1 and
  §2.5 both write the shared name; this is a deliberate deviation, and both the creation path and
  the sweep read it from the one `_default_work_root()`, so they move together. The system temporary
  directory is shared by every user of a machine, and a root inside it can be private *or* usable by
  a second user, not both: `0o700` means the next user's first `world.instance(...)` cannot create
  its `<pid>/`, and anything a second user can write to is world-writable. Neither price buys
  anything, because the sweep can only ever act on this user's own directories — §2.5 has it treat a
  pid it may not signal as alive, and another user's files are not ours to remove in any case, so a
  shared root gives the sweep a search space permanently larger than the set it may touch. One path
  component makes the root this user's own: it is created `0o700` and owned by them, every pid
  inside it is theirs, no second user is ever blocked, and nothing below it needs a mode of its own.
  Everything the framework does to the root *and to every level below it* is done to a checked
  descriptor rather than to a path (`_open_root`, then `_open_child` per level): a name in a
  world-writable `<tempdir>` cannot be trusted, and neither can the equally predictable names under
  it. The round-4 and round-5 findings below say what that bought. Every level is made `0o700`, and
  the root's mode is set with `os.fchmod` rather than asked of `mkdir` (whose mode argument is
  masked by the umask) and re-asserted on every call, so a root left by an earlier run is repaired;
  a root this user does not own, a directory below it that somebody else made, or a symlink standing
  where any of them should be, is refused as the `WorldBug` naming the path and
  `World(work_dir=...)`.
- **A working directory is `<pid namespace inode>-<pid>`, not `<pid>`.** §2.1 and §2.5 name the bare
  pid. A pid means nothing outside the namespace that issued it, and §2.5's rule — sweep a sibling
  whose pid is not alive — asks `os.kill(pid, 0)`, which answers in the *caller's* namespace. Two
  containers sharing a `<tempdir>` mount (a bind-mounted host `/tmp`, a shared volume, a sidecar)
  therefore read each other's directories as dead pids and delete a running process's instances;
  reproduced here with `unshare --pid --fork --mount-proc` before the fix. The namespace's inode
  (`/proc/self/ns/pid`) is the one identifier that is stable within a namespace and distinct
  between two, and prefixing it makes the rule true again: a name carrying a different prefix is
  not this namespace's to judge and is stepped over, not swept. Where there is no procfs to ask
  (macOS), the prefix is empty and the layout is §2.1's own — the one-namespace case, which is what
  every such machine is; recorded rather than silently assumed.
- **`set_concurrency` ships with the gate**, in this phase rather than with `seahaven serve`
  (Phase 6/7), because the gate is this phase's and a module-level default with no way to change it
  could not be tested at all.

## Tests

`tests/conftest.py` grows `build_world`, a `world`/`instance` pair of fixtures and a small generic
toolset (`execute`, `rows`, `write_then_fail`, `crash`, `mint`, `now`), so that every test below
drives a real world through `Instance.call` rather than reaching past it. An autouse fixture
restores the two pieces of process-wide state (the gate, the sweep's once-flag) after each test, and
`test_instances.py` runs every thread through one `Caller` helper that re-raises on `finish()`.

`pyproject.toml` turns `PytestUnhandledThreadExceptionWarning` into an error, so a thread test
cannot pass while the thread it started failed. It could, and one did.

`tests/test_conformance.py`

- a fresh instance of a world conforms to it; `check` passes and `differences` is empty
- an added table, a dropped table, an added column (with both definitions in the message), an extra
  index and an index the schema declares but the instance lost are each flagged
- a column-order change is flagged: textual, not semantic
- reformatting the DDL is not a difference
- a view is part of the schema
- `check` raises `WorldBug` listing every difference at once and naming the world
- a world with an FTS5 table conforms to itself; shadow tables and SQLite's own autoindexes are in
  no schema map

`tests/test_changes.py` (every write through a real tool call)

- insert, update and delete rendering, and `to_dict`
- an update carries the key and the old values in `before` and the new values in `after`
- a composite key is rendered in key order; an `INTEGER PRIMARY KEY` is a key
- a no-op update records nothing; insert-then-update is one insert; insert-then-delete is nothing
- a call that raised leaves no trace, in the changeset or in the database
- rows a startup hook wrote are not changes; rows `bulk()` wrote are
- a table named in `untracked_tables` is absent, and that is also how a table with no primary key is
  kept out of the session
- a table with no explicit primary key is refused at creation, naming the table
- writes to an FTS5 table and its shadow tables do not appear
- changes are cumulative across calls and survive a freeze
- a blob column comes back as base64

`tests/test_fixtures.py`

- freeze writes a directory holding exactly `state.sqlite` and `fixture.yaml`, with every §9.1 field,
  `format_version: 1`, the instance's `now`, the world's version and schema hash, a wall-clock
  `created_at` that is not the instance's clock, and the file's real hash
- the frozen file is `0o444`, rollback-journal and compacted (smaller than the churned live file)
- two freezes of identical content produce identical bytes
- an instance made from the result sees the frozen rows and the frozen clock, and the instance that
  was frozen is still usable afterwards
- freeze refuses an existing id; refuses `""`, `.`, `..`, `a/b`, `/abs`, `.hidden` and `a/../b`;
  refuses an instance that no longer holds the schema — and leaves nothing behind in every case
- a failure while writing the sidecar leaves no fixture and no pending directory
- a `.pending-<id>` left by a crash does not block a later freeze of that id
- `load` reads a sidecar without opening the state file, and refuses a missing file, unparseable
  YAML, a non-mapping, a `format_version` of 2 or none, a missing field and an unknown field —
  naming the file
- `verify` passes what freeze wrote; refuses a modified file, naming the fixture and saying to fork
  it; refuses a fixture with no state file; caches the good case on (path, mtime, size, hash) and
  re-reads when the mtime moves
- `load_all` finds every fixture by id, skips dot-directories and plain files, refuses two fixtures
  claiming one id, and is empty for a directory that does not exist; `World.fixtures()` sorts by id
- a fork chain of three keeps `parent_id` through the chain, with `None` at the root
- the fixtures directory is created by the first freeze and not before

`tests/test_instances.py`

- a blank instance's clock is the wall clock truncated to milliseconds; an explicit `now` (string or
  `datetime`) is honoured in Python and in SQL; `now` with a fixture is refused
- an instance from a fixture is a writable copy at the fixture's clock, and the fixture file stays
  read-only
- a fixture id that is not a directory name is refused before the filesystem is touched, including
  one that names a real directory outside the fixtures directory
- `freeze` inside a `bulk()` block is refused, saying to leave the block first, and works once the
  block has closed; a state file that cannot be written is a `WorldBug` naming the path
- an unknown fixture names the directory and `World(fixtures_dir=...)`; a fixture frozen from
  another schema says to regenerate it; a modified fixture says to fork it — none of them copy
- seeds: one seed and one fixture give one stream in two instances; two fixtures give two streams
  for one seed; `None`, `int` and `bytes` seeds; `inst.seed` is the derived seed
- an unknown startup argument is refused before anything is copied; a hook taking `**kwargs`
  accepts anything; hooks run in order, each with only what it accepts, inside one transaction — a
  later hook reads the earlier hook's rows, and a second connection opened from inside that hook
  cannot see them yet; a hook that raises leaves no instance and
  no directory; what a hook puts in `ctx.state` is there for every call
- `tools()` lists the world's tools with their schemas and never a control tool
- a call runs through the middleware chain; a tool registered after the instance is callable on it;
  an unknown name is `UnknownTool`; a `ToolError` reaches the caller
- every call logs one `INFO` line carrying the instance, the world, a duration and the outcome —
  `ok`, a `ToolError`'s code, an exception's class name, `unknown_tool`
- `call`, `changes`, `inspect`, `freeze` and `bulk` all refuse a destroyed instance
- `inspect()` opens one handle and keeps it, sees committed writes, does not see a write still
  inside a `bulk` block, and refuses to write
- a control tool bypasses the chain, is called with exactly its validated arguments (an aliased
  parameter under its Python name, a default it did not receive) and given a context carrying the
  same, is refused when its result holds `bytes` or a `set` like any other tool's, and may read
  through `inspect()` while the instance lock is held (the re-entrancy case)
- `bulk` commits once at the end, rolls back when the block raises, and yields the instance's own
  context with no call
- leaving the block destroys, including when the block raised; `destroy` is idempotent, takes the
  instance out of the registry, closes the connection and the inspection handle, logs one line, and
  removes its own directory and nothing above it; `InstanceManager.destroy` does the same
- destroy waits for a call in flight (thread test), and a call that starts after it is refused
- the gate bounds how many calls run at once (thread test), `0` removes it, a control call and
  `tools()` pass an exhausted gate *promptly*, a call queued behind the gate does not delay a
  `destroy` on its instance, the gate is released after a call that raised, a negative concurrency
  is refused, and the default is between 1 and 16
- the default working directory is `<tempdir>/seahaven-<uid>/<pid namespace>-<pid>/<world>/`, both
  the root and the per-process directory are `0o700` under either umask, a root left behind with a
  looser mode is repaired, and a root that cannot be made is a `WorldBug` naming the path and
  `World(work_dir=...)`; a configured working directory is used exactly as given, and one that
  cannot be made is the same `WorldBug`
- a root that is a symlink is refused and never followed, neither by the `chmod` nor by the sweep; a
  root belonging to another user is refused; opening it leaves no descriptor behind; and a platform
  without the primitives to harden a root has no default working directory at all, which
  `world.instance()` says in as many words
- the same at every level below the root: a symlink planted at `<namespace>-<pid>` or at the world's
  name is refused and never followed, and so is a directory below the root belonging to another user
- a machine with no procfs to ask about pid namespaces gets the bare-pid layout, and its sweep works
- the sweep removes what it judged: the entry checked through the root's descriptor is the entry
  removed through it, even when the root's *name* is swapped in between
- the sweep removes a dead pid's directory, leaves this process's, a non-numeric name, `0`, a pid it
  may not signal, a name from another pid namespace, and an entry that is a symlink rather than a
  directory; counts what it removed rather than what it attempted, and says so; runs on the first
  instance of the process and not again, for the process rather than for each world; a world with
  its own `work_dir` never sweeps and does not spend the process's one sweep either
- a creation that fails after the database is open leaks no file descriptors, and one that fails
  before it leaves no directory
- the manager registers exactly one `atexit` hook, and `close()` destroys every instance and is safe
  to call twice
- 200 instances of one fixture in one process, each with its own id and directory; two instances of
  one fixture cannot see each other

## Review round

A code review of the finished phase found no critical defects and judged the decisions above sound.
Two moderate findings and six smaller ones were fixed; each fix carries a named test, and each of
those tests was mutation-checked the same way as the rest (table below). A second round over those
fixes found two more, both recorded below as well: one of the fixes had overshot, and one of the
new tests pinned only half of what it claimed. Rounds three to six stayed on the working
directory: round three moved the root, round four found that the code *adopting* an existing root at
that path was exploitable — the one critical defect of the phase — round five found the same
mistake one level down, in the code that adopts an existing directory *inside* the root, and round
six found that round five's own rewrite had left the sweep discarding work it had already done, and
round six's targeted verification found a name in the same root that `int` would not read. Every
round's fixes are below, and every one of them is mutation-checked.

The shape of those three rounds is worth naming, because it is the failure mode of this code: each
fix addressed the case that had been demonstrated and left the adjacent one alone. The answer round
five settled on is not another check but a different way of naming things — the whole creation path
is now descriptor-anchored, so there is no next level down to miss.

- **The sweep's once-per-process flag was spent by a world that never sweeps.** `_sweep_once` set
  the flag and then called `sweep_stale_processes`, which returns `0` for a world with a configured
  `work_dir` — so the first such world in a process burned the sweep and every default-`work_dir`
  world afterwards inherited a dead one. That is the ordinary configuration, not a corner: the
  Phase 8 pytest plugin and every test in this suite configure `work_dir`, so in practice the sweep
  would have run for nobody. `_sweep_once` now returns without touching the flag when the world has
  its own working directory; the guard inside `sweep_stale_processes` stays, so a direct call still
  answers `0`. `test_a_world_with_its_own_working_directory_does_not_spend_the_process_sweep` builds
  the two worlds in the order that failed.
- **A control tool's result went through `to_jsonable_python` directly**, not through the serialiser
  every other result goes through, so a control tool could answer with `bytes` (decoded as text) or
  a `set` (in whatever order it iterated) where any other tool would be a `WorldBug`. `call.py`'s
  `_serialise` is now the public `serialise` and both paths call it. See the control-dispatch
  decision above for why the blob question is Phase 4's rather than this phase's to settle.
- **The control context now carries the validated arguments**, as `invoke` does — the call is
  rebuilt with them before `ctx.with_call`, so a control tool that reads `ctx.call.arguments` sees
  what it was really given, defaults and aliases included, rather than the raw mapping. *(Round 2:
  the first version of this test asserted only on `ctx.call.arguments`, and a mutant that rebuilt
  the context but still called the function with the raw wire mapping passed the whole suite. The
  test is now `test_a_control_tool_is_called_with_exactly_the_validated_arguments` and uses an
  aliased parameter, whose wire name is not a parameter of the function at all, so the call itself
  is pinned and not just the context.)*
- **`freeze` inside `bulk()` is refused where the reason is visible.** It used to reach SQLite and
  raise a raw `apsw.SQLError: cannot VACUUM from within a transaction`; a fixture of uncommitted
  rows is not what the caller asked for either, so it is a `WorldBug` that says to leave the block
  first. `fixtures.freeze` also wraps an `apsw.Error` from the `VACUUM INTO` itself (a full disk, an
  unwritable fixtures directory) as a `WorldBug` naming the path: freezing is an authoring step, and
  its refusals read in one currency.
- **The sweep counts what it removed, not what it tried.** `rmtree` is called with
  `ignore_errors=True`, so a directory that could not be removed was being counted and logged as
  swept.
- **The working root is per user (`<tempdir>/seahaven-<uid>/`), created `0o700` with an explicit
  `chmod`.** Recorded as a deviation above, with the reasoning. This took three rounds and two
  wrong answers, both of which came from reasoning about mode bits instead of running a second uid:
  - *Round 1* made `<tempdir>/seahaven/` and `<pid>/` both `0o700`. The root is not this process's
    — every user of a shared `<tempdir>` puts their `<pid>/` in it — so the first user to run
    Seahaven locked out every other one.
  - *Round 2* relaxed the root to `0o755` on the premise that the umask default had been letting a
    second user create their `<pid>/`. It had not: `r-x` is not `rwx`, so the second user was still
    refused, and the mode was still umask-derived (a ceiling, not a setting), so under `umask 077`
    the round-1 behaviour came back in full and the test asserting `0o755` failed on unmodified
    code. Both facts were confirmed here with a real second uid before the next attempt, not after.
  - *Round 3* stopped patching the symptom and moved the root. The reviewer's alternative — a
    world-writable `0o1777` shared root — was not taken: it keeps a search space the sweep can
    never act on, and it keeps the pid-reuse hazard, where `<pid>/` already exists owned by another
    user, `mkdir(exist_ok=True)` accepts it and the next write fails. A per-user root has neither,
    and costs one path component and no code.

  Verified end to end on this machine with `setpriv --reuid=65534` against a `1777` `TMPDIR`: uid 0
  and uid 65534 each get their own `0o700` root and a working instance in either order; uid 65534's
  sweep removes its own stale pid directory; uid 65534 cannot list uid 0's root; and a root
  pre-created by somebody else produces `WorldBug: cannot make a working directory under
  /tmp/foreign/seahaven-65534: [Errno 1] Operation not permitted ... Name a directory this process
  owns with World(work_dir=...)`.
- **Every failure out of the working-directory code is a `WorldBug` naming the path and
  `World(work_dir=...)`.** A read-only or full `<tempdir>`, or a root this user does not own, used
  to reach the caller as a raw `PermissionError`. It is the only refusal in this phase that comes
  from outside the framework, and it now reads like the rest of them.
- **The `atexit` registration is documented as deliberate.** It is never removed, and that is the
  point: it must survive a caller dropping every reference to a world whose instances are still on
  disk, and there is no moment at which a manager is known to be finished (`close()` is idempotent
  and a closed manager can still create). The bound is one entry per world that has ever made an
  instance.
- **The queued-call test no longer depends on timing.** It slept for a fraction of a second and
  hoped the queued thread had got somewhere; the gate-inside-the-lock mutation survived one run of
  the mutation harness for exactly that reason, having been killed in another. The gate is now a
  `BoundedSemaphore` subclass that signals on arrival, so the test waits for the queued call to
  reach the gate and then asserts that it is holding nothing — which under the swapped ordering is
  the moment its instance lock is already held.

- **The root is opened as a checked descriptor, and everything is done to the descriptor.** *(Round
  4, and the phase's only critical finding.)* The code adopted whatever it found at
  `<tempdir>/seahaven-<uid>` by path: `mkdir(parents=True, exist_ok=True)`, then `chmod` on the
  path, then a sweep that listed and `rmtree`d through the path. `<tempdir>` is world-writable and
  that name is entirely predictable, and the sticky bit stops *deleting and renaming* — not
  creating a name nobody has claimed yet. So a local user could plant either of two things there
  before Seahaven ever ran, and both were reproduced end to end through `world.instance()` against
  the unfixed tree, with a real second uid (`setpriv --reuid=65534` planting, uid 0 running):
  - a **symlink** to any directory: the `chmod` followed it and set the target to `0o700` (observed:
    a `0o755` victim came back `0o700`), and the sweep then recursively removed every child of the
    target whose name parsed as a dead pid (observed: `12345/` gone). Arbitrary `chmod` and
    arbitrary recursive delete, as whatever user runs the eval.
  - a **plain directory** of the planter's own: `exist_ok=True` accepted it, and the `chmod`
    *succeeded* — the victim here is uid 0, which is how this suite, CI and many container
    harnesses run — so the planter's directory became the working root and the planter kept a
    handle on every instance file written into it.

  The fix is `_open_root`: one `os.open(root, O_RDONLY|O_DIRECTORY|O_NOFOLLOW)`, an `os.fstat`
  owner check against `os.geteuid()`, and then `os.fchmod`, `os.mkdir(..., dir_fd=)`,
  `os.listdir(fd)` and `os.stat(..., dir_fd=, follow_symlinks=False)` on that descriptor. Both
  attacks were re-run against the fix, the same way: each is now `WorldBug: cannot make a working
  directory under … Name a directory this process owns with World(work_dir=...)`, the victim's mode
  is untouched at `0o755` and its contents are byte-for-byte what they were. Three details were
  settled rather than transcribed:
  - **`O_NOFOLLOW` and `parents=True`.** `O_NOFOLLOW` refuses a symlink at the *last* component
    only, so a symlinked `<tempdir>` above the root is still followed — and must be, since macOS's
    `/tmp` is one. That is not the exposure: an attacker who can redirect `TMPDIR` already owns the
    process's environment, whereas a planted name inside a world-writable directory needs nothing.
    `_open_root`'s docstring says exactly this, so the claim and the code agree. `parents=True`
    stays, because the parents it makes are `<tempdir>`'s own and the root is checked after.
  - **Where `mkdir` is not in `os.supports_dir_fd`.** Seahaven has *no default working directory*
    there, and `world.instance()` says so: a `WorldBug` naming `World(work_dir=...)`, not an
    `AttributeError` or a `NotImplementedError` escaping from the platform. A configured `work_dir`
    is unaffected and is the whole surface on such a platform. `_POSIX_WORK_ROOT` is the one place
    that decides, and the sweep consults it too, so a platform without the primitives sweeps
    nothing rather than sweeping unsafely.
  - **The descriptor is closed.** A leaked fd per instance would end a long eval run;
    `test_opening_the_working_root_leaves_no_descriptor_behind` counts `/proc/self/fd` across eight
    instances.
- **The sweep no longer throws away what it has already done.** *(Round 6.)* Round 5 moved
  removal inside the `with _open_root(root)` block so that judging and removing share a descriptor,
  and in doing so put it under a handler written for a block that only ever *listed*: `except
  OSError: return 0`. A single failure part-way through — most plausibly an entry removed by another
  process's sweep between the `listdir` and its `stat`, which is normal, since every process's first
  `create` sweeps the same root — therefore ended the sweep and reported that it had removed
  nothing, for a root it had in fact already been clearing. The per-entry `os.stat` now has its own
  `except OSError: continue`, so a vanished entry is stepped over; the outer handler is `pass` and
  falls through to `return swept`, so what was removed before a refusal is still what it reports.
  `test_a_directory_another_sweep_removed_first_does_not_end_this_one` stages the concurrent remover
  by having the first `rmtree` take a later entry out from under the loop, with the listing order
  fixed so that the entry that disappears always has another behind it (see the third method note
  below). The docstring says the overlap is two processes doing one job rather than a fault.
  *(The phase plan's survivor record for that handler had been left describing the pre-round-5
  shape, and asserted that the race was not stageable. Both halves are corrected under the mutation
  check below; this is the second record a rewrite has stranded, so the others were re-read against
  the code as it now stands.)*
- **A directory name is a pid only in the digits this code writes.** *(Round 6.)* `_pid_of` tested
  the name's tail with `str.isdigit`, which is true of a good deal more than `0`-`9`. Superscript
  digits are `isdigit` and `int` refuses them: a directory named `<namespace>-²` under the root
  made `world.instance()` itself die of a `ValueError`, which is not an `OSError` and so was caught
  by neither of the sweep's handlers — a crash in the phase's own entry point, not merely a sweep
  that stopped. Arabic-Indic digits are the quiet half: `int` reads them as an ordinary number, so a
  name nothing here wrote would be judged as some process's pid and swept the moment that pid died.
  Neither is reachable by a stranger — the root is `0o700` and owner-checked before it is read — but
  the predicate is now `rest.isascii() and rest.isdecimal()`, which is the actual question being
  asked, and `int` can no longer be handed anything it will refuse.
  `test_a_name_in_digits_this_code_never_writes_is_not_a_pid` plants both names and makes a real
  instance through `world.instance()`, so the crash is pinned where it would have happened rather
  than at `_pid_of`'s own boundary.
- **The whole creation path is descriptor-anchored, not just the root.** *(Round 5.)* Round 4
  anchored the root and then composed `root / <namespace>-<pid> / <world> / <uuid>` as a string
  again, creating the per-process directory with `mkdir` under `suppress(FileExistsError)` — which
  is to say: an entry already standing at that name was trusted rather than checked, exactly what
  `_open_root` exists to refuse one level up. A symlink planted at `<namespace>-<pid>` therefore
  redirected the entire instance, and the comment above the code claimed the `0o700` mkdir was
  defence-in-depth against precisely the case it did not cover.

  Reproduced on the unfixed tree with a real second uid, through `world.instance()`: uid 65534
  pre-plants symlinks at `<namespace>-<pid>` across a range of pids, uid 0 makes an instance, and
  `os.path.realpath(instance.dir)` resolves into the attacker's directory while the world is live —
  from which `head -c 16 state.sqlite` as uid 65534 answers `SQLite format 3`. Reachable only where
  the root is this user's *and* writable by the attacker: a root left permissive by something
  outside Seahaven, or the window between `root.mkdir(...)` and the first `fchmod` under a umask of
  `000`. Narrow, and the fix is not narrow, because the narrowness was an accident of the mode and
  not a property of the design.

  `_open_child` is `_open_root` one level down — `mkdir` under `suppress(FileExistsError)`, then
  `os.open(name, O_RDONLY|O_DIRECTORY|O_NOFOLLOW, dir_fd=parent)` and the same `fstat` owner check —
  and `_make_instance_dir` walks root → process → world through it, creating the instance directory
  with `os.mkdir(instance_id, 0o700, dir_fd=world_fd)`. The path it returns is composed only after
  every component of it is an inode this user made or owns. `_work_dir` is gone; the configured
  `work_dir` branch lives in the same method, where it is plainly the branch that checks nothing
  because the caller chose the directory.

  Re-run against the fix, with the same rig: a planted symlink at `<namespace>-<pid>`, a planted
  symlink at `<world>`, and a plain attacker-owned directory at `<namespace>-<pid>` are each
  `WorldBug: cannot make a working directory under … Name a directory this process owns with
  World(work_dir=...)`, with nothing written into the attacker's directory. The honest path still
  works for both uids under umask `022`, `077` and `000`, every level `0o700`, two worlds in one
  process sharing one per-process directory.
- **The sweep removes through the descriptor it judged through.** *(Round 5.)* Collection was
  `dir_fd`-anchored and removal was not: it re-derived `root / name` after the descriptor had been
  closed. `shutil.rmtree` refuses a top-level symlink, so the worst case was already blocked, but
  the two halves were one name looked up twice rather than one inode. Removal and the count that
  follows it (`_is_gone`) now happen inside the same `with _open_root(root)` block, through the same
  descriptor. `test_the_sweep_removes_through_the_descriptor_it_judged_through` stages the swap the
  race would have to win — the first removal renames the checked root away and leaves a symlink to a
  victim at its name — so the property is pinned by something that means the same thing on every
  machine.
- **A working directory carries its pid namespace, and the sweep judges only its own.** *(Round
  4.)* Recorded as a deviation above, with the reasoning and the reproduction. Two names were
  possible — put the namespace in the directory name, or narrow §2.5's claim to "the sweep is safe
  only where `<tempdir>` is not shared across pid namespaces" and document it. The name was chosen:
  the alternative leaves a correct-looking sweep that deletes a running process's data in a
  configuration nobody would think to check, and the cost of the name is one `os.stat` at startup.
  Where `/proc/self/ns/pid` cannot be read the prefix is empty, which is the spec's own layout and
  today's behaviour — a recorded degrade, not an import-time failure.
- **The per-process directory is made, `0o700`, by the code that makes the root.** *(Round 4.)*
  `create` was making it as a parent of the instance directory, so the mode assertion in the
  privacy test held only by way of the umask and the mutation that dropped it survived. It is now
  `os.mkdir(name, 0o700, dir_fd=fd)` beside the root's own `fchmod`, and `create`'s `mkdir` no
  longer has a parent to create.
- **A configured `work_dir` that cannot be made is a `WorldBug` too.** *(Round 4.)* Only the default
  path wrapped its `OSError`; a caller-named directory under a missing parent or on a read-only
  filesystem still reached the caller raw.
- **`WORK_DIRNAME` is `WORK_DIR_PREFIX`.** *(Round 4.)* It has not been a whole directory name since
  the root became per user.

| Mutation | Killed by |
|---|---|
| `_sweep_once` spends the flag for a configured `work_dir` | `test_a_world_with_its_own_working_directory_does_not_spend_the_process_sweep` |
| `sweep_stale_processes` no longer refuses a configured `work_dir` | `test_a_world_with_its_own_working_directory_never_sweeps` |
| the sweep counts what it tried | `test_the_sweep_counts_what_it_removed_and_not_what_it_tried` |
| a control result bypasses `serialise` | `test_a_control_tools_result_is_held_to_the_rules_every_result_is` |
| `freeze` no longer refuses a transaction | `test_freeze_refuses_to_run_inside_bulk` |
| the `VACUUM INTO` failure is not wrapped | `test_a_state_file_that_cannot_be_written_is_a_world_bug` |
| the root's mode is never set (the umask decides) | `test_the_default_working_directory_is_private_to_this_user` |
| the mode is asked of `mkdir` instead of set, so it is umask-derived and never repaired | `test_a_working_root_left_behind_with_the_wrong_mode_is_repaired` |
| the root is shared between users again | `test_the_default_working_directory_is_per_process_and_per_world` |
| the sweep reads a different root from the one instances are made in | `test_the_sweep_removes_a_dead_process_and_leaves_everything_else` |
| the per-process directory's mode is asked of `mkdir` instead of set | `test_the_default_working_directory_is_private_to_this_user` |
| a working root that cannot be used raises raw | `test_a_working_root_belonging_to_another_user_is_refused` |
| the root is opened by path, following a planted symlink (`O_NOFOLLOW` dropped) | `test_a_working_root_that_is_a_symlink_is_refused_and_never_followed` |
| an existing root's owner is not checked | `test_a_working_root_belonging_to_another_user_is_refused` |
| the owner check asks `getuid` instead of `geteuid` | `test_a_working_root_belonging_to_another_user_is_refused` |
| the mode is set through the path instead of the descriptor | `test_a_working_root_left_behind_with_the_wrong_mode_is_repaired` |
| the sweep reads the root by path again | `test_a_working_root_that_is_a_symlink_is_refused_and_never_followed` |
| a symlinked entry is swept like a directory (`follow_symlinks=True`) | `test_the_sweep_judges_an_entry_without_following_it` |
| the checked descriptor is never closed | `test_opening_the_working_root_leaves_no_descriptor_behind` |
| the platform check is dropped and the default root is used regardless | `test_a_platform_that_cannot_harden_the_root_has_no_default_working_directory` |
| the directory name is the bare pid again | `test_the_working_directory_of_a_process_carries_its_pid_namespace` |
| the sweep judges a name from another pid namespace | `test_the_sweep_leaves_a_directory_from_another_pid_namespace_alone` |
| the per-process directory is left to `create` to make | `test_the_default_working_directory_is_private_to_this_user` |
| a configured `work_dir` that cannot be made raises raw | `test_a_configured_working_directory_that_cannot_be_made_is_a_world_bug` |
| the per-process directory is trusted by name when it already exists | `test_a_per_process_directory_that_is_a_symlink_is_refused_and_never_followed` |
| …and the same, one level down at the world's name | `test_a_world_directory_that_is_a_symlink_is_refused_and_never_followed` |
| a level below the root is opened by path, following a planted symlink | `test_a_per_process_directory_that_is_a_symlink_is_refused_and_never_followed` |
| a level below the root does not check its owner | `test_a_directory_below_the_root_belonging_to_another_user_is_refused` |
| a level below the root asks `getuid` instead of `geteuid` | `test_a_directory_below_the_root_belonging_to_another_user_is_refused` |
| a level below the root leaks its descriptor | `test_opening_the_working_root_leaves_no_descriptor_behind` |
| the sweep removes by name instead of through the descriptor | `test_the_sweep_removes_through_the_descriptor_it_judged_through` |
| the sweep counts by name instead of through the descriptor | `test_the_sweep_removes_through_the_descriptor_it_judged_through` |
| a non-directory entry is offered to `rmtree` anyway | `test_the_sweep_judges_an_entry_without_following_it` |
| the missing pid namespace becomes a value (`0`) instead of nothing | `test_without_a_procfs_the_working_directory_is_the_bare_pid` |
| the degraded layout gets a prefix of its own | `test_without_a_procfs_the_working_directory_is_the_bare_pid` |
| the gate is taken inside the instance lock | `test_a_call_queued_behind_the_gate_does_not_delay_a_destroy` |
| the control tool is called with the raw wire mapping | `test_a_control_tool_is_called_with_exactly_the_validated_arguments` |
| the control context keeps the raw arguments | `test_a_control_tool_is_called_with_exactly_the_validated_arguments` |
| an entry gone between the listing and the stat ends the sweep (the inner `try` removed) | `test_a_directory_another_sweep_removed_first_does_not_end_this_one` |
| a vanished entry stops the loop rather than being stepped over (`continue` → `break`) | `test_a_directory_another_sweep_removed_first_does_not_end_this_one` |
| a refusal discards what was already removed (`pass` → `return 0` while the stat is unguarded) | `test_a_directory_another_sweep_removed_first_does_not_end_this_one` |
| the pid predicate is `isdigit` again (a `ValueError` out of `world.instance()`) | `test_a_name_in_digits_this_code_never_writes_is_not_a_pid` |
| the predicate keeps only `isdecimal`, so Arabic-Indic digits are read as a pid | `test_a_name_in_digits_this_code_never_writes_is_not_a_pid` |
| the predicate keeps only `isascii`, so any ASCII name is handed to `int` | `test_the_sweep_removes_a_dead_process_and_leaves_everything_else` |
| the predicate is dropped and every name is handed to `int` | `test_the_sweep_removes_a_dead_process_and_leaves_everything_else` |
| an entry the sweep cannot answer for is counted as gone (`_is_gone` → `return True`) | `test_an_entry_the_sweep_cannot_answer_for_is_not_counted_as_swept` |

Three notes on the method, each found while checking the above.

- **A mode is only asserted if it is asserted under two umasks.** `Path.mkdir(mode=...)` is masked
  by the process umask, so an assertion on a created directory's mode can be vacuous on one machine
  and red on another. The privacy test takes a fixture that sets the umask and runs under `022` and
  `077`; deleting the `chmod` is killed by the `022` half and *survives* the `077` half alone, which
  is the point of running both. The full suite passes under either umask.
- **A mutant the same length as the line it replaces can survive on stale bytecode.** CPython
  invalidates a `.pyc` on the source's size and whole-second mtime, so swapping
  `with gate(...), self._held():` for `with self._held(), gate(...):` — identical in length — inside
  the same second runs the *original* bytecode, and the mutation harness reports a survivor that is
  not one. It did, once, for the gate ordering. The harness now clears `__pycache__` and sets
  `PYTHONDONTWRITEBYTECODE=1` before every run, and every mutation in this plan was re-run under it.
- **A mutant whose survival depends on `os.listdir` order is a coin flip, not a result.** Round 6's
  `continue` → `break` mutant was killed under one umask and survived under the other, which is not
  a thing a umask can decide: the two runs had simply listed the root's four entries in different
  orders, and `break` and `continue` are the same statement when the entry they act on is the last
  one. The test now fixes the order it is given, and the difference disappeared. The rule is the
  same as the bytecode one — when a mutation result varies between runs, the harness is wrong before
  the code is.

## Mutation check

Every statement of `fixtures.py`, `instances.py`, `changes.py` and `conformance.py`, and of the
`world.py` region this phase touched, was replaced with `pass` in turn and the suite re-run (a
statement-deletion sweep; a mutant that hangs the suite counts as killed). Then sixteen mutations
statement deletion cannot express — a keyword, a condition, an ordering — were made by hand.

| File | Statement mutants | Killed |
|---|---|---|
| `changes.py` | 40 | 40 |
| `conformance.py` | 19 | 16 |
| `fixtures.py` | 84 | 81 |
| `instances.py` | 176 | 166 |
| `world.py` (lines 100-216) | 32 | 32 |

**The sweep found four real gaps and two redundant guards, all now closed.** The first pass over
`instances.py` left 28 survivors: nothing noticed a destroyed instance that stayed in the manager's
registry, a database connection that was never closed, a failed creation that leaked its file
descriptors, or the sweep's `INFO` line — and `architecture.md` §6's `destroy` log was missing
altogether. The two redundant guards were a once-per-manager flag and a once-per-process flag on the
sweep, each of which kept the other's mutants alive; the per-manager one is gone, and the remaining
guard is killed by `test_the_sweep_runs_once_per_process_and_not_once_per_world`.

The hand mutations found three more, two of them tests that could never have failed:

- **`shutil.copyfile` changed to `shutil.copy`** — which gives the instance the fixture's `0o444` —
  survived, because the test proved the copy was writable by writing to it, and the tests run as
  root here, where that proves nothing. It now asserts the file's mode.
- **`gate(bypass=tool.control)` changed to `bypass=False`** survived: the control call queued for
  five seconds and then answered, and the assertion that timed out did so on a worker thread, where
  pytest turns a failure into a warning beside a passing test. Two fixes: the control-tool test now
  times the call, and `pyproject.toml` turns `PytestUnhandledThreadExceptionWarning` into an error,
  so no thread test can pass on a thread that failed. Every thread in the suite now runs through one
  `Caller` helper that re-raises on `finish()`.
- **The gate taken inside the instance lock instead of before it** survived, which is the whole
  point of the ordering (`architecture.md` §5.2: a queued call must never delay a `destroy`).
  `test_a_call_queued_behind_the_gate_does_not_delay_a_destroy` now pins it and deadlocks on the
  swap.
- **One transaction per startup hook instead of one for all of them** survived: asserting
  `in_transaction` inside each hook is true either way. The test now opens a second connection from
  inside the second hook and asserts the first hook's row is not yet visible through it, which only
  one transaction can produce.
- **`schema_map`'s two shadow-table clauses** were redundant — a table's `tbl_name` is its own name,
  so the `name not in shadow` clause could never fire alone. It is one clause now, and the mutant
  that drops it is killed by `test_shadow_tables_are_in_no_schema_map`.

The sixteen hand mutations and the test that kills each:

| Mutation | Killed by |
|---|---|
| `shutil.copyfile` → `shutil.copy` | `test_an_instance_from_a_fixture_is_a_writable_copy_at_the_fixtures_clock` |
| the gate is never bypassed | `test_a_control_tool_passes_an_exhausted_gate` |
| the gate is taken inside the instance lock | `test_a_call_queued_behind_the_gate_does_not_delay_a_destroy` |
| the session is attached before the startup hooks | `test_rows_a_startup_hook_wrote_are_not_agent_changes` |
| one transaction per hook | `test_hooks_run_inside_one_transaction` |
| every hook gets every `reset` argument | `test_hooks_run_in_order_each_with_only_what_it_accepts` |
| the seed source is the world name even for a fixture | `test_two_fixtures_give_two_streams_for_one_seed` |
| `except OSError` before `except ProcessLookupError` | `test_the_sweep_removes_a_dead_process_and_leaves_everything_else` |
| `created_at` is the instance's clock | `test_the_sidecar_carries_every_field` |
| a leading dot is allowed in a fixture id | `test_freeze_refuses_an_id_that_is_not_a_directory_name[..]` |
| the frozen state file is left writable | `test_the_frozen_file_is_read_only_and_journal_free` |
| `verify`'s cache key drops the mtime | `test_verify_caches_a_good_fixture_and_re_reads_a_changed_one` |
| `key` comes from `old` on an insert too | `test_an_insert_is_one_change` |
| key columns in column order, not key order | `test_a_composite_key_is_rendered_in_key_order` |
| `before`/`after` carry `no_change` columns | `test_an_update_carries_the_changed_columns_and_the_key` |
| `schema_map` keeps the shadow tables | `test_shadow_tables_are_in_no_schema_map` |

`Path("..").name` is `".."` (unlike `"."`, whose name is `""`), so the leading-dot clause of
`check_id` is what refuses `..` — the one traversal that the single-path-segment rule alone lets
through. That is why it has its own mutation above.

What survives, and why each is a mutant with no behaviour to kill:

- **`if TYPE_CHECKING:` blocks and `__all__` lists**, in all four modules: annotations are lazy on
  3.14, so nothing evaluates them at runtime, and `__all__` only changes `from … import *`. `ty` is
  what catches a deleted annotation import. (Phase 2 recorded the same two.)
- **`conn.close()` in `conformance._expected`**: an in-memory database with no other reference,
  about to be collected. The line is hygiene.
- **`self._inspection = None` and `self._session.close()` in `Instance._close`**: the instance is
  closed and nothing reads either field again; APSW finalises a session with its connection. Both
  release memory sooner than the garbage collector would, which is worth doing and is not worth a
  test that reaches into the object to see it.
- **`if db is not None: db.close()` on the creation-failure path**: under CPython the only
  references to that connection are locals of the frame that is unwinding, so it is closed by
  refcounting a moment later —  which is what
  `test_a_creation_that_fails_after_the_database_is_open_leaks_nothing` measures and why it passes
  either way. The explicit close is what makes the release immediate and independent of how the
  interpreter collects.
- **`_swept = False` at module level**: the autouse fixture that restores process-wide state between
  tests assigns the same name, so deleting the definition leaves every test after the first with a
  working module attribute. The fixture is what makes the tests independent, and the trade is worth
  it.
- **`if pid <= 0: return True` in `_is_alive`**: genuinely equivalent. `os.kill(0, 0)` succeeds
  (signal `0` to one's own process group is a permission check that does nothing), so both versions
  answer "alive" for a directory named `0`, which is what `test_the_sweep_never_signals_process_zero`
  asserts. The guard is there so that the framework never sends *any* signal to a process group,
  which is a property of the code rather than of its output.
- **`return True` in the `except OSError` branch of `_is_alive`**: the function's final `return
  True` catches the fall-through, so the two are the same answer by two routes. Swapping the two
  `except` clauses, which is not equivalent, is in the hand-mutation table above.
- **Both `return False` statements in `_is_gone`**: deleting the one in the `except OSError` falls
  through to the function's final `return False`, as in `_is_alive`; deleting the final one falls
  off the end of the function, and `None` is as false as `False` to the one caller, an `if`. Two
  routes and one answer. *(Re-read in round 6 against the code as it stands: both are still there,
  and both are still equivalent. The `except` branch was also an unexecuted branch until round 6 —
  the deletions were equivalent, but nothing ran the handler, and `return True` there survived the
  whole suite, which would have counted an entry the filesystem will not answer for as swept.
  `test_an_entry_the_sweep_cannot_answer_for_is_not_counted_as_swept` stages a `stat` that is
  refused after the removal and kills it. The lesson from the `_pid_namespace` record is that
  "deletion is equivalent" is a smaller claim than it looks, and does not cover the branch.)*
- ~~**`return None` in `_pid_namespace`'s `except OSError`**~~ — **this record was wrong, and round 5
  corrected it.** Deleting the statement does fall through to `None`, so the *deletion* is
  equivalent; recording the line as equivalent is not the same claim, and it is false. Swapping the
  value — `return 0` — survived the whole suite and is not equivalent at all: `0` is not `None`, so
  `_dirname_prefix` yields `"0-"` and a machine without procfs gets a third directory layout that
  neither the spec nor this plan describes. The gap underneath it was that nothing in the suite ever
  ran the `except` branch, because CI has procfs.
  `test_without_a_procfs_the_working_directory_is_the_bare_pid` patches `os.stat` to refuse
  `/proc/self/ns/pid` and pins both halves of the degrade: the bare-pid name, and a sweep that still
  works on it. Both value mutations are killed. A survivor recorded as equivalent that is not tells
  a later reader the question is settled, which is worse than not recording it.
- **`except OSError: pass` in `sweep_stale_processes`** — **this record was wrong from round 5 until
  round 6, and round 6 rewrote both the code and the record.** What it used to say was that every
  failure the handler can be given happens before a single name has been collected, so returning `0`
  and falling through to the removal loop answer the same. That was true while the removal loop sat
  *after* the `try`; round 5 moved removal *inside* the block so that judging and removing share one
  descriptor, and this record was not moved with it. From then until round 6 the handler could
  discard completed work: a failure part-way through the loop reported `0` for a root this process
  had already swept, and the record said the question was settled. The state that tells the two
  apart is exactly the one dismissed above as unstageable — an entry gone between the `listdir` and
  its `stat` — and it is stageable, because the concurrent remover is another Seahaven process's
  first `create` and can be stood in for. Round 6 gives the per-entry `os.stat` its own `except
  OSError: continue`, so a vanished entry is stepped over rather than ending the sweep, and leaves
  the outer handler as `pass`, falling through to `return swept`.
  `test_a_directory_another_sweep_removed_first_does_not_end_this_one` stages the remover and pins
  both halves — the sweep finishes the root, and counts what it removed rather than what any sweep
  removed. What survives now is `pass` itself, which a statement sweep can only replace with `pass`,
  and `return 0` in its place. That second one is equivalent, and here is the whole of the
  reachability argument rather than a summary of it, because the first version of this clause named
  two sources and missed a third. Inside the `try`, in order: `_open_root` can fail, before anything
  is listed; `_dirname_prefix` answers its own `OSError`; `os.listdir` can fail, before anything is
  removed; `_pid_of` can no longer raise at all, which is round 6's other fix — it is a prefix test
  and an ASCII-decimal test, with nothing left for `int` to refuse; `_is_alive` answers its own
  `OSError` with "alive"; the per-entry `os.stat` has its own handler; `S_ISDIR` does no I/O;
  `rmtree` is asked to ignore its errors; and `_is_gone` answers its own `OSError` with "still
  there". That leaves exactly one thing that can reach the outer handler *after* a removal, and it
  is not in the block at all: `_open_root`'s `finally: os.close(fd)`, which runs as the `with`
  unwinds, inside this `try`. A close of a descriptor this frame opened, owns, and has not handed to
  anybody is not a failure any caller can produce, so the survivor stands — but it is the reason the
  handler is `pass` and not `return 0`, since the one path that could tell them apart is the one
  that runs last.

The working-directory code was swept again after each of rounds 4, 5 and 6, since each of them
rewrote it: statement deletion over `InstanceManager.create`, `sweep_stale_processes`,
`_make_instance_dir` and every module-level helper below them. Round 4: 116 mutants, 108 killed.
Round 5: 138 mutants, 128 killed. Round 6: 141 mutants, 131 killed, and the same sweep restricted to
`sweep_stale_processes` and the helpers it calls was run under `umask 077` too — 38 mutants, 37
killed, the survivor being the outer handler's `pass`. The full 141 were run again after the
`_pid_of` fix that round 6's verification pass asked for, with the same result and the same ten
survivors: the two new tests pin value mutations, which a statement sweep does not express.

Two survivors across those runs were not equivalent and are now killed — `os.close(fd)` in
`_open_root`, and the `S_ISDIR` guard in the sweep, which stopped being killed by the old test once
removal moved behind `rmtree`'s own symlink refusal. That guard is worth a clause of its own, for
the reader who finds it later and reads its test as circular. Since the sweep removes through a
descriptor, `rmtree` refuses a top-level symlink itself, so `S_ISDIR` no longer decides whether a
planted symlink's target survives — it decides whether the name is offered at all, and it is kept as
defence in depth behind a refusal that lives in the standard library rather than in this file.
Deleting it therefore fails only an assertion about what a call was given, which is exactly what the
docstring claims and the only thing left to assert; the test is not asserting its own mock, it is
asserting that `rmtree`'s refusal is never the last line of defence. Round 6 also retargeted the
planted symlink at a real directory, because once a vanished entry is stepped over rather than
ending the sweep, a *dangling* symlink no longer tells `follow_symlinks=False` apart from `True`.

The ten survivors that remain are all in the list above, and each was re-read in round 6 against the
code as it now stands rather than as it was when its record was written. All seven hand-mutation
harnesses were re-run under `umask 022` and `umask 077`, on clean bytecode, with every mutation
killed except the two that are expected to survive — one under `umask 077` alone, and `return 0` for
the sweep's outer handler, recorded above.

## Follow-up, not this phase

Round 4's audit found three security-hardening behaviours in `db.py` (Phase 1, already committed)
that no test proves: recorded as **B1** in `BACKLOG.md`.

Round 5 found that a working directory left by an earlier build — a bare `<pid>`, from before the
namespace prefix — is never swept, because `_pid_of` will not judge a name without this namespace's
prefix. Deliberate and safe, but it leaks disk: recorded as **B3** in `BACKLOG.md`, with a one-line
note in `sweep_stale_processes`' docstring pointing at it.

Round 6 added three more, each verified here before it was written down and none of them blocking:
a world's name reaching the filesystem as an unvalidated path component (**B4** — the validation
belongs in `world.py`, which Phase 2 committed), the default working root being created `0o777`
under a permissive umask and only then tightened by `fchmod` (**B5**), and the descriptor-anchored
final `mkdir` being claimed by a docstring that no test holds it to (**B6**). None of them is fixed
by this phase's diff.
