# Seahaven capability map

> **Path note (added later).** This document was written while Seahaven was vendored in this
> repository at `vendor/Seahaven/`. It is now a git dependency pinned to a full commit SHA in
> `[tool.uv.sources]`, and the directory is gone. Read every `vendor/Seahaven/…` path below
> against `github.com/Kiln-AI/Seahaven` with that prefix dropped; the findings themselves are
> unchanged, recorded against the commit that was vendored at the time.

An architect-facing map of what the vendored framework (`vendor/Seahaven`, package version
`0.0.1`, found at `vendor/Seahaven/pyproject.toml:9-10`) actually provides. Every claim below is
either a verbatim quote from the bundled docs (`vendor/Seahaven/src/seahaven/docs/`), a reading of
the framework source (`vendor/Seahaven/src/seahaven/`), or something I ran myself in this session —
each is labelled. No web sources; this is local-source research only, as scoped.

See also [pressure-points.md](./pressure-points.md) for the seven verdicts and
[authoring-friction.md](./authoring-friction.md) for the friction log (seed entries for `SEAHAVEN_FINDINGS.md`).

## Package layout — what `seahaven new <name>` scaffolds

Source: `vendor/Seahaven/src/seahaven/docs/authoring.md:19-76`, confirmed against the real
`worlds/projecttracker/` tree (`find worlds/projecttracker -name '*.py'`, run in this session).

```
notes/
  pyproject.toml
  README.md                    # also the OpenEnv environment's README
  AGENTS.md                    # points at the bundled docs, written once, never touched again
  src/notes/
    __init__.py                # imports world, then tools/middleware for side effects
    world.py                   # world = seahaven.World(...)
    errors.py                  # this world's error shapes (ToolError subclasses)
    openenv_app.py              # app = seahaven.openenv.app(world)
    schema/001_items.sql        # SQL, applied in filename order
    tools/__init__.py           # imports every module beside it
    tools/items.py               # one module per resource
    middleware/__init__.py
    middleware/error_handler.py
  fixtures_src/generate.py     # the script every fixture is built by; committed
  fixtures/                    # empty until you freeze one
  tests/test_items.py
  tests/test_fixtures.py
```

Three layout rules from `authoring.md:59-72`:
- A world is a **package**, never a directory loaded by path — tooling finds it by import.
- **Group tools by resource** (`tools/issues.py` with seven tools), not one file per tool.
- **A world is checked out, not installed** — `fixtures/` sits outside the package and does not
  travel in a wheel (see [Fixture lifecycle](#fixture-lifecycle-and-fork-cost) below).

ProjectTracker's real shape, confirmed by directory listing in this session:
`worlds/projecttracker/src/projecttracker/{__init__.py, world.py, errors.py, startup.py,
middleware/{__init__.py,error_handler.py}, openenv_app.py, schema/{001_core.sql,002_search.sql},
tools/{__init__.py,_events.py,_pagination.py,_rows.py,_types.py,comments.py,issues.py,labels.py,
projects.py,search.py,teams.py,users.py}}`, plus `fixtures_src/generate.py`, `fixtures/{empty,
small_startup,agency}/`, and `tests/` with one test module per resource plus
`test_determinism.py`, `test_declared_errors.py`, `test_error_handler.py`, `test_pagination.py`,
`test_fixtures.py`, `test_sql_tools.py`, `test_openenv.py`, `test_package.py`.

**Verified bug hit while scaffolding-equivalent setup (see authoring-friction.md):** the scaffold's
`seahaven~=0.0` PyPI dependency resolves to a placeholder that contains none of the framework
(`authoring.md:49-57`, `reference/cli.md:70-76`). Confirmed independently in this session: a bare
`uv sync` against the framework's own `pyproject.toml` needs Python `>=3.14`
(`vendor/Seahaven/pyproject.toml:15`), and even with the framework installed from the checkout,
`import seahaven` crashes under the only Python 3.14 this sandbox could obtain (`3.14.0rc2`) with
the locked `pydantic==2.13.5` — see [authoring-friction.md](./authoring-friction.md) Entry 1. The framework's
own `bench/results/latest.md` documents the identical incompatibility from its own history
(`bench/results/latest.md:2-8`: "a hand-patched `pydantic` 2.12.3 in place of the locked 2.13.5 —
the only way the suite ran at the time"). Downgrading to `pydantic==2.12.3` fixed it; all 259
ProjectTracker tests then pass and `seahaven check` reports zero findings (both run in this
session).

## Registration verbs

All four are call-only or decorator-or-call; each validates **immediately at import** and raises
`seahaven.WorldBug` naming the tool/parameter at fault (`concepts.md:39-41`, `authoring.md:272-306`,
`reference/api.md:113-134`).

### `world.tool`

```py
world.tool(obj=None, *, name=None, description=None, transaction=None)
```
(`reference/api.md:115`, and the decorator form throughout `authoring.md`.) Function signature:
first param `ctx: seahaven.Ctx` (positional, either annotated or unannotated — but if annotated,
must be `seahaven.Ctx`); remaining params are the tool's arguments, each type-annotated, no
positional-only, no `*args`/`**kwargs`, no mutable default. Seahaven builds one pydantic model per
tool from the signature at registration time (`authoring.md:175-176`), strict-validated: `"5"` is
not `int`, `2.0` is not `int`, `1` is not `bool` (`concepts.md:58-59`, `authoring.md:177-178`).
Result: JSON-serialisable data — `dict`, `list`, scalars, `None`, pydantic models, dataclasses.
**`bytes` and `set` are refused** (`authoring.md:249-252`). One call is one transaction by default;
`@world.tool(transaction=False)` opts out for multi-stage commits (`authoring.md:254-270`).

**Refused at registration** (`authoring.md:272-289`): duplicate tool name; name is `reset`, `step`,
`state`, `close` or the control tool name; `async def`/generator/async generator; no first param, or
one that's not positional `Ctx`-or-unannotated; an arg with no type annotation, positional-only,
`*args`/`**kwargs`; an arg annotated `datetime`, `date`, `time`, or any `Enum` subclass at any depth
(**use `str` with a `Field(pattern=...)` for timestamps, `Literal` for closed sets** —
`authoring.md:183-201`); an arg whose annotation exists only under `TYPE_CHECKING`; a mutable
default; an arg pydantic cannot give a JSON schema.

Two escapes for a wire format that isn't Python's (`authoring.md:207-241`): `Annotated[str,
Field(alias="from")]` for a Python-keyword field name (the agent sends the alias, the function
receives the Python name — an alias **replaces**, it does not add); `Annotated[int,
Field(strict=False)]` to relax one argument's strictness (accept `"5"` for `5`).

### `world.middleware`

```py
world.middleware(obj=None)
```
Shape: `(ctx, call, next_) -> result`, checked structurally at registration
(`reference/api.md:589-594`). **Order is registration order, outermost first**
(`authoring.md:467`). Can inspect/replace arguments (`call.with_arguments(**changes)`, frozen
`Call`, returns a copy), short-circuit, transform results, catch/re-raise errors, time the call.
Runs for every tool call including helpers' and an extension's, but **not** for `UnknownTool`, a
tool listing, startup hooks, or the control tool (`authoring.md:473-474`). Arguments are raw until
validation runs inside the chain; a layer wanting typed arguments calls `call.tool.validate(...)`
itself.

### `world.instance_startup`

```py
world.instance_startup(obj=None)
```
Shape: `(ctx, **kwargs) -> None` (`StartupHook` type alias, `reference/api.md:593`). Runs once per
instance, after the file is copied and the connection is set up, before the first call. Several may
be registered (an extension may bring one), run in registration order. Its keyword args are the
`reset()` arguments beyond `fixture`, `seed`, `now`, `state_format` — **spell them out explicitly**;
`**kwargs` switches off unknown-argument detection for the whole world (`authoring.md:479-523`). May
not name a parameter `fixture`, `seed`, `now` or `state_format` (those are reserved). Rows a hook
writes are **not** in the change log (no session is open yet). If setup can't succeed (e.g. a
`user_id` naming nobody), raise `WorldBug` — instance creation fails before any run starts against
bad state.

### `world.add_world`

```py
world.add_world(other, /, *, name=None, store=None, tool_prefix=None,
                 tool_allow_list=None, tool_block_list=None, startup=None) -> None
```
(`reference/api.md:73-85`, full treatment in `composition.md`.) Covered in depth in
[Composition and tool-prefixing](#composition-and-tool-prefixing) below.

## What `ctx` carries

Source: `concepts.md:143-157`, `reference/api.md:189-231`.

| Member | Contract |
|---|---|
| `ctx.db` | The `Db` wrapper: `one(sql, *params)` → dict or `None`; `rows(sql, *params)` → `list[dict]`; `execute(sql, *params)` → `Exec(rowcount, last_rowid)`; `executemany(sql, rows)`; `transaction()` → nested savepoint context manager; `conn` → raw APSW connection. Parameters are positional `?` bindings. Every SQLite failure arrives as `seahaven.DbError`. **Never close `conn`, change its pragmas/authorizer, or open a second connection to the instance file** — the clock overrides, the change-log session and the per-call transaction all depend on that one connection (`reference/api.md:252-255`). |
| `ctx.clock` | `now()` → aware UTC `datetime`; `iso()` → canonical text `2026-06-01T09:00:00.000Z`. **Static for the instance's whole life.** Every connection overrides SQLite's `current_timestamp`, `current_date`, `current_time`, and the `'now'` argument of `datetime`, `date`, `time`, `strftime`, `julianday`, `unixepoch`, `timediff`, to return that one instant — so SQL and Python agree (`concepts.md:159-177`, `reference/api.md:257-269`). There is **no method to change it mid-instance** — confirmed by reading `src/seahaven/clock.py` in this session: it registers `SQLITE_DETERMINISTIC` overrides against one frozen instant per instance and exposes nothing else. See [pressure-points.md §time](./pressure-points.md#time). |
| `ctx.ids` | `uuid()` → "a UUIDv4-shaped identifier drawn from the seeded stream" (`reference/api.md:271-278`, confirmed against `src/seahaven/ids.py:82-84`: `str(uuid.UUID(int=self.random.getrandbits(128), version=4))` — **no prefix parameter, no alternate format**); `random` → a `random.Random` seeded per instance, for anything else. "Product-shaped keys (`ENG-13`, a sequential invoice number) are the world's own business… this is the stream they draw from" (`reference/api.md:280-281`, `ids.py:74-76`). See [pressure-points.md §id generation](./pressure-points.md#id-generation) for what building Stripe-shaped ids on top of this actually looks like (I built and ran one). |
| `ctx.state` | A plain `dict` living as long as the instance. Startup hooks write to it; tools read from it. |
| `ctx.call` | Current `Call`: `name`, `arguments` (raw until validated), `tool`, `node` (owning node's canonical path, `"main"` for a non-composite world), `with_arguments(**changes)`. `None` outside a call (a startup hook, `bulk()`). |
| `ctx.instance` | `id`, `fixture` (or `None`), `seed` — read-only. |
| `ctx.worlds` | The `Worlds` container for added worlds, by name. For a world that adds none, **every name on it raises `WorldBug`** (`reference/api.md:202`). See [Composition](#composition-and-tool-prefixing). |

## Errors and the error-handler middleware

Source: `authoring.md:307-419`, `reference/api.md:424-449`.

A world declares its own error shapes in `errors.py` as ordinary classes subclassing
`seahaven.ToolError(code, message, details=None)`. Nothing about them is registered on `World` —
raise them where they happen. `error.to_dict()` is `{"code", "message", "details"}`, the same shape
in process and over the wire (in the observation's `metadata["seahaven_error"]`).

Exception hierarchy:
```
SeahavenError
├── WorldBug                  the author's mistake; never shown to an agent
└── ToolError(code, message, details=None)
    ├── ArgumentError         .violations: every problem with the arguments
    ├── DbError                .sqlite_message, .sqlite_code, .refusals
    └── UnknownTool            .details["name"]
```
Seahaven's own three codes: `invalid_arguments`, `db_error`, `unknown_tool` (lower case; a world's
own codes are its own — ProjectTracker uses `SCREAMING_SNAKE`: `NOT_FOUND`, `INVALID_INPUT`,
`CONFLICT`, `INTERNAL`, `projecttracker.md:87-98`). `ToolError.error_type` is the framework's coarse
category (`tool_not_found`, `invalid_args`, or `execution_error` for everything else, world errors
included) — a world **cannot** set its own `error_type`; a subclass that tries is refused with
`WorldBug`.

**Every world has `middleware/error_handler.py`, scaffolded, registered first (outermost)**
(`authoring.md:375-419`). Its job: "nothing reaches the agent that this world did not choose." The
canonical pattern, from the scaffold:
```py
@world.middleware
def error_handler(ctx, call, next_) -> Any:
    try:
        return next_(ctx, call)
    except seahaven.ArgumentError as error:
        raise InvalidInput.from_violations(error.violations) from error
    except seahaven.DbError as error:
        _log.error(...); raise Internal() from error
    except seahaven.ToolError:
        raise
    except seahaven.WorldBug:
        raise
    except Exception as error:
        raise Internal() from error
```
Two rules: **re-raise a `WorldBug` unchanged** (hiding it lies to the agent and hides a bug from
you); **let engine text reach an agent only where the product is a SQL door** — a tool registered
via `seahaven.helpers.run_sql` should let its `DbError` through by tool name, because a SQL
console's errors are the point; everywhere else a `DbError` is the world's own SQL being wrong.

**Look parents up before letting a foreign key refuse a write.** A missing-parent insert fails as
`DbError` → `INTERNAL` if left to SQLite; a world should `SELECT` the parent first and raise its own
`NOT_FOUND` so the agent gets an actionable sentence. Both ProjectTracker's `_rows.py` (its whole
`require_*` family) and its own docs argue this at length (`authoring.md:415-418`,
`projecttracker.md:109-112`, `_rows.py:9-16`).

## Schema, migration convention, and SQLite features available

Source: `db_schema_and_fixtures.md:52-124`, `authoring.md:568-652`, and this session's direct
inspection of `src/seahaven/sandbox.py`.

- SQL lives in `.sql` files under `schema/`, applied to a **blank** database in filename order
  (`001_`, `002_` prefixes). `seahaven.sql_files(__package__, "schema")` reads them through
  `importlib.resources`.
- **No Seahaven schema language, no migrations, ever.** `World.__init__` applies the whole schema to
  an in-memory database at construction to compute the schema hash — a schema that doesn't execute
  can't even be constructed (`db_schema_and_fixtures.md:72-75`).
- **Three rules, all enforced by `seahaven check`:**
  1. Every ordinary table is `STRICT` (FTS5 virtual tables and their shadow tables exempt). Columns
     must be `INT`, `INTEGER`, `REAL`, `TEXT`, `BLOB` or `ANY` — no `VARCHAR(64)`, no `BOOLEAN`.
  2. Every table has an explicit primary key (SQLite's implicit `rowid` does not count) — a table
     without one **cannot be tracked** in the change log at all.
  3. **No wall-clock expression anywhere** — no `CURRENT_TIMESTAMP` default, no `datetime('now')` in
     a trigger/view/generated column/partial index. World code writes timestamps from
     `ctx.clock.iso()`.
- **Timestamps are `TEXT`**, canonical `2026-06-01T09:00:00.000Z` (ISO-8601 UTC, milliseconds,
  trailing `Z`), everywhere, so text comparison sorts correctly.
- **A schema file may seed static reference rows** (currencies, plans) via plain `INSERT`s — those
  run on the instance's own clock/seed, so they replay like anything else, but the *DDL itself* still
  may not reference the wall clock. Rows belonging to one *scenario*, rather than the schema, are a
  fixture's job.
- **The schema hash costs every fixture.** Recorded in every fixture's sidecar; an instance refuses a
  fixture whose hash doesn't match the loaded world. Any column addition means regenerating **every**
  fixture (`db_schema_and_fixtures.md:647-652`, `authoring.md:293-301`) — which is why the generator
  script is committed rather than the fixture being hand-built.
- **JSON1 is fully available**, confirmed against `src/seahaven/sandbox.py` `ALLOWED_FUNCTIONS`:
  `->`, `->>`, `json`, `json_array`, `json_array_length`, `json_error_position`, `json_extract`,
  `json_group_array`, `json_group_object`, `json_insert`, `json_object`, `json_patch`, `json_pretty`,
  `json_quote`, `json_remove`, `json_replace`, `json_set`, `json_type`, `json_valid`. The table-valued
  functions `json_each` and `json_tree` are separately allowed as *tables* by `run_sql`'s default
  door (`reference/api.md:469-470`, `authoring.md`'s FTS5 note). ProjectTracker's own pattern:
  `payload TEXT NOT NULL CHECK (json_valid(payload))` — "a `STRICT` table has no JSON storage class…
  the `CHECK` is what makes the column mean JSON rather than merely hold it"
  (`schema/001_core.sql:105-107`), with values written by `json.dumps(payload, sort_keys=True,
  separators=(",", ":"), ensure_ascii=False)` in Python (`tools/_events.py:41`) for
  reproducible bytes.
- **Generated columns**: not explicitly documented as supported or unsupported. `SH103`'s rule text
  explicitly includes "a generated column" among the places a wall-clock expression is forbidden
  (`db_schema_and_fixtures.md:98-99`), which implies generated columns are an anticipated schema
  feature; nothing in the docs or code forbids one computed from other (non-time) columns. Untested
  by this research — no world in the repo uses one.
- **Money/decimal**: **no native decimal type.** `STRICT` columns are limited to `INT`/`INTEGER`/
  `REAL`/`TEXT`/`BLOB`/`ANY`. No world in the repository (ProjectTracker, `tests/worlds/payments`,
  `tests/worlds/ledger`) models money at all — `ledger`'s own schema is `id, memo, posted_at`, no
  amount column anywhere. See [pressure-points.md §money](./pressure-points.md#money) for the
  verdict (integer minor units in an `INTEGER` column is the answer, and it matches Stripe's own
  public API convention, so this is not friction).
- **Connection setup is Seahaven's, not the world's**: WAL mode, foreign keys on, SQLite's defensive
  mode, and the clock/`random()`/`randomblob()` overrides — world code never sets a pragma.

### Full-text search (FTS5)

Supported "as far as a world's own search tool needs" (`authoring.md:579-645`). A virtual table and
its sync triggers are allowed in the schema, exempt from `STRICT`/primary-key rules. FTS5's shadow
tables are known to Seahaven and stay out of the change log, the freeze comparison, and `run_sql`'s
default allowlist automatically. Nothing is inferred from a table's name in `run_sql`'s
authorization — opening `MATCH` through the SQL door means listing the virtual table **and** every
shadow table it has (`_data`, `_idx`, `_docsize`, `_config`, depending on `content=`/`columnsize=`)
plus the functions `bm25`, `snippet`, `highlight`, `match`. ProjectTracker deliberately leaves
`issues_fts` out of its SQL door, reaching search only through its own `search_issues` tool
(`projecttracker.md:146-148`).

## Fixture lifecycle and fork cost

Source: `db_schema_and_fixtures.md:126-349`, `reference/cli.md:108-155`, plus a fixture-fork-cost
probe I wrote and ran in this session (full numbers in
[pressure-points.md §fixture size](./pressure-points.md#fixture-size)).

**What a fixture is**: an immutable directory — `fixtures/<id>/state.sqlite` (checkpointed,
vacuumed, sealed `0444`) plus `fixture.yaml` (id, world, world_version, schema_hash, now, parent_id,
file_sha256, created_at, description). **Never opened, only copied** — the first process to copy a
fixture verifies `file_sha256` and refuses on mismatch.

**The only way to make one**: `inst.freeze(id, description)` — checks the instance's schema against
the world's, checkpoints, vacuums, copies the file into `fixtures/<id>/`, writes the sidecar with the
instance's clock as `now` and the source fixture as `parent_id`, seals read-only. Refuses if the
directory already exists. **There is no in-place edit** — to change a fixture, fork it (make an
instance from the parent, change it, freeze under a new id).

**Building one**: start from a **blank instance** (`world.instance()`, no fixture — built from schema
alone, clock is wall time unless `now=` is given). Fill it either through the world's own tools (when
the point is that data is reachable the way an agent would make it) or `inst.bulk()` — "yields the
root node's context, in one transaction per node, under the instance lock, with no call attached"
(`db_schema_and_fixtures.md:200-204`) — for loading thousands of rows without paying argument
validation per row. `freeze` cannot run inside a `bulk()` block (rows aren't committed yet).
ProjectTracker's own generator deliberately uses **both** paths: bulk for the bulk of the data, then
`THROUGH_THE_TOOLS = 3` of the last issues/comments through real tool calls so the fixture "exercises
the path an agent will use" and stateful counters (the per-team issue-key counter) land where the
tools would have left them (`fixtures_src/generate.py:39-44,66-70`).

**Committed CLI recipe**:
```sh
seahaven fixture freeze empty --now 2026-06-01T09:00:00.000Z \
    --run fixtures_src.generate:empty --description "..."
seahaven fixture fork empty small_startup \
    --run fixtures_src.generate:small_startup --description "..."
```
`--now` is **required on every freeze** and must be the same value every time — a blank instance
with no `--now` takes the wall clock, so omitting it silently dates the fixture from build day
(`db_schema_and_fixtures.md:261-267`). `fork` takes no `--now` — it inherits the parent's clock,
which is the whole point of forking.

**The generator is committed, always**: "A binary file with no source is one nobody can change, and
a schema change means rebuilding all of them, which has to be a command rather than a project"
(`db_schema_and_fixtures.md:239-244`). `seahaven new` scaffolds it at `fixtures_src/generate.py`: one
function per fixture, each taking a live instance and filling it.

**Fork cost, measured in this session**: creating an instance from a fixture is a private SQLite
file copy plus per-node connection setup. I built a two-table (`customers`, `charges`) fixture with
5,000 customers × 3 charges (20,000 rows, 2.25 MiB file) and separately 20,000 customers × 5 charges
(120,000 rows, 13.70 MiB file), then measured wall time to `world.instance(fixture_id)` + one
`SELECT count(*)`, 50 repeats each:

| Fixture | Rows | File size | Fork median | Fork p90 | Fork max |
|---|---|---|---|---|---|
| 5,000 customers | 20,000 | 2.25 MiB | 4.50 ms | 5.01 ms | 7.78 ms |
| 20,000 customers | 120,000 | 13.70 MiB | 11.50 ms | 13.35 ms | 29.07 ms |

Full method, script and discussion in [pressure-points.md §fixture size](./pressure-points.md#fixture-size).
**Bottom line: forking stays in single-digit-to-low-double-digit milliseconds even at tens of
thousands of rows**, comfortably inside the project's "must fork in milliseconds" constraint
(`project_overview.md` §11) for anything short of a genuinely enormous fixture.

**Where fixtures live, and the trap**: `fixtures/` sits **outside** the package deliberately — a
`World(fixtures_dir=...)` override exists but does not travel in a wheel. **A world is checked out,
not installed**, and this is the one deployment fact this research flags without re-verifying beyond
reading it: `world.instance()` (blank) works from an install; `world.instance("empty")` and every
fixture-backed instance raise `WorldBug` naming the directory and the two ways out, because
`world.fixtures()` is `[]` (`db_schema_and_fixtures.md:306-341`).

**`seahaven check`'s six fixture rules** (all errors): `SH401` sidecar doesn't validate, `SH402`
`file_sha256` mismatch (edited, or a symlink), `SH403` schema hash mismatch, `SH404` `now` not
canonical, `SH405` stray `-wal`/`-shm` companion, `SH406` (composite only) sidecar `nodes` disagrees
with the world's actual composition.

## The change log and the state document

Source: `concepts.md:232-271`, `state.md` (whole page), `reference/api.md:324-365`.

`inst.change_log()` → `list[LogRecord]`, one record per row per call, in call order:
```
record.i        # call ordinal (0-based), or None for an inst.bulk() write
record.world     # store path: "main" for the root, or the added node's path
record.table
record.op        # "insert" | "update" | "delete"
record.key        # primary-key columns as {col: value}
record.before, record.after   # row dicts, None where not applicable
```
**A record is the net of its own call**: a no-op write records nothing; insert+update-in-one-call
collapses to one insert with final values; insert+delete-in-one-call leaves no record; a rolled-back
call leaves none. **The log is never folded across calls** — a row two calls touch appears twice.
Startup-hook writes are never in the log (no session open yet). `World(untracked_tables=...)`
excludes named tables; FTS5 shadow tables are excluded automatically. A table with no explicit
primary key can't be tracked at all — refused at instance creation (`SH102`).

`inst.state(format=None)` is the versioned document an eval grades on: an **envelope** (framework-
owned, additive-only compatibility contract) around a **`state`** payload (format-owned). Required
`World(state_format=...)` pin — no default, world construction raises without it, naming the built-in
formats. Three built-ins:

| Format | `state` shape | When |
|---|---|---|
| `seahaven.state/1` | `{"db": {"log": [...]}}` — the **whole** change log | read once at episode end (the scaffold's default) |
| `seahaven.state+last_step/1` | same shape, but `log` holds only the most recent call's records | a rollout harness storing state every step (avoids the quadratic growth of storing the whole log per step) |
| `seahaven.state+calls/1` | `seahaven.state/1`'s `state` plus `calls`: `[{tool, arguments, error}]` in dispatch order | a harness that needs its own trace reconciled against the document, e.g. spanning two worlds or mixing real and synthetic tools |

A world may register its own with `@world.state_format("family/major")` (must not start with
`seahaven.`); the root's pin decides for the whole instance regardless of what an added world
registered. `inst.state(format="...")` reads any format on demand without changing the instance's
own pin. **Read between calls only** — `inst.state()` raises `WorldBug` inside `bulk()` or a tool
call, because uncommitted rows can't be described.

**The fold** (the net state difference of a whole episode) is *not* shipped by the framework — "it
is a page of code wherever you grade" (`state.md:458`). Two documented traps: counting log records
overcounts rows (two updates of one row = two records); two episodes with identical end state can
have different logs (fold them before comparing, never diff raw logs).

**Envelope fields** (`state.md:44-92`): `format`, `seahaven_version`, `world` `{name, version}`,
`composition` (per-node: world, version, scope, aliases, schema_hash, frozen_world_version),
`fixture` `{id, nodes}` or `null` for blank, `episode_id`, `seed`, `now`, `startup` (reset keywords
beyond the reserved four), `call_count`.

**Recording cost, per the framework's own bench** (`state.md:559-573`, not independently
re-measured here — this figure is the framework's own published number): a write-heavy call on
ProjectTracker's `agency` fixture costs "about 47% more than the single long-lived session per node
that Seahaven kept before this release," of which "about 33 of those 47 percentage points" is
reading and rendering the changeset. A read-only call costs less (5–18% above the old shape, on a
call under 0.1 ms).

## `run_sql`, `describe_schema`, and read-only inspection

Source: `authoring.md:589-645`, `reference/api.md:451-499`, `src/seahaven/sandbox.py` (read
directly).

```py
def run_sql(*, name="run_sql", tables: Sequence[str], read_only: bool = True,
            max_rows: int | None = None, max_bytes: int | None = None,
            functions: Sequence[str] = (), description: str | None = None) -> Tool: ...
```
One `query` string, one statement per call, SQLite dialect. Returns `{"columns", "rows",
"row_count", "truncated"}`. A SQLite `Authorizer` allows exactly the listed tables plus
`sqlite_master`, `sqlite_schema`, `json_each`, `json_tree`, and **denies everything else** — `ATTACH`,
`PRAGMA`, `load_extension`, schema changes are refused regardless of `read_only`; with
`read_only=True` every write is refused too. A statement-level check backs the authorizer, and a
fixed non-configurable cap on a single SQL value's size stops a query materialising an enormous one.
`functions=` allows SQLite functions beyond the (generous) default list — `random()`/`randomblob()`
are in the default list deliberately, because what an agent draws from them is the instance's own
seeded stream. Nothing is inferred from a table's name — an FTS5 virtual table needs its shadow
tables listed explicitly too, or `MATCH` fails with `read of table '<name>_idx'`.

```py
def describe_schema(*, name="describe_schema", tables: Sequence[str],
                     description: str | None = None) -> Tool: ...
```
No arguments; reads the **live** schema and returns `{"tables": [{"name", "columns": [{"name",
"type", "nullable", "primary_key"}], "foreign_keys": [...]}]}`. Register beside any SQL door so the
agent can read tables before querying them.

**`inst.inspect()`** is a read-only `Db` on a second connection: `one`/`rows` over every table
(including every added world's store, attached read-only under its path), the instance's clock,
opened once and kept. **Never a tool** — it's for tests and eval grading. Writes, `ATTACH`,
`DETACH` are denied on it.

`seahaven.sandbox` exposes `Authorizer`, `run_statement`, `SqlResult`, `refusal_kind`, `REFUSALS`,
`ALLOWED_FUNCTIONS`, `MAX_VALUE_BYTES` publicly, so an extension serving another SQL dialect can run
its translated statement through the same containment rather than inventing a second one.

## The pytest plugin

Source: `testing.md` (whole page), `reference/api.md:563-568`. Confirmed working: all 259
ProjectTracker tests pass in this session (`uv run pytest worlds/projecttracker`, after the pydantic
pin).

Activated automatically by installing `seahaven` — no `conftest.py` boilerplate. Two fixtures, one
marker, one option:

- **`world`** — session-scoped, the `World` object found by convention (nearest `pyproject.toml`,
  its normalised `[project] name`, the `world` attribute).
- **`instance`** — fresh per test, made from the marker's fixture, destroyed on teardown regardless
  of pass/fail.
- **`@pytest.mark.seahaven(fixture, seed=None, now=None, **startup_kwargs)`** — `fixture="agency"`
  for a fixture on disk, `fixture=None` for a blank instance (blank is a valid marker value; the
  marker's **absence** is what fails, with a message spelling out both spellings). Everything but
  `fixture` passes straight through to `world.instance(...)`. A module-level `pytestmark` is the
  usual spelling; **a marker on one test replaces the module's, never adds to it** — the plugin
  flags this explicitly as the commonest mistake. Two `seahaven` markers in one place (test, class,
  or module `pytestmark` list) are refused outright.
- **`--seahaven-world module:attr`** — the plugin's own spelling of `--world`, because pytest's
  option namespace is shared.

`testing.md:86-188` lays out what's worth testing in a world: every tool through a real
`instance.call(...)` (never the bare function — that skips argument validation, the middleware
chain and the transaction); errors by **code**, not message text; the argument model where a
`Literal`/pattern matters; state via `inst.inspect()`, not just the tool's return value; the change
log for anything an eval will grade; determinism once (same seed → same ids, needs the `world`
fixture for two instances); the fixtures' own stated invariants. Explicitly **not** worth testing:
the framework itself (that a `Literal` produces a JSON schema, that rollback rolls back).

## `seahaven check` lints

Source: `reference/lints.md` (whole page), confirmed live in this session (`seahaven check` on
ProjectTracker: zero findings, exit 0).

22 codes across five families, listed with severity and one-line rule in
`reference/lints.md:23-46`. Grouped: `SH1xx` schema (STRICT, primary key, wall-clock, executes);
`SH2xx` world-code hygiene (wall-clock calls, `random`/`uuid4`, empty descriptions, composition
description/name collisions, `.instance()` inside `tools/`/`middleware/`, unregistered
`ctx.worlds.<name>`); `SH3xx` (an unimported tool/middleware module — the one that produces
"`unknown_tool` weeks later" if missed); `SH4xx` fixture integrity (six rules, see above); `SH5xx`
package/composition-level (`world` attribute missing, `Worlds` subclass mismatches, composition
fails to seal). Runs by finding the world via the same convention as everything else; exits 1 on any
`error`-severity finding, 0 on warnings alone, so it's CI-usable as-is. Every finding names the exact
edit to make, never just describes the problem, and there is deliberately **no autofix, no
configuration file, no suppression comment**.

**No lint or hard limit anywhere caps tool count.** Grepped `src/seahaven/*.py` in this session for
`MAX_TOOL`, `max_tool`, tool-count limits: nothing. See
[pressure-points.md §tool count](./pressure-points.md#tool-count) for the full verdict.

## Composition and tool-prefixing

Source: `composition.md` (whole page, 37KB — the single deepest doc in the bundle),
`reference/api.md:136-151, 209-230`.

A world can `add_world` other worlds. A world with none added is a composition of exactly **one
node** (one store) — "There is no second code path, which is why every rule on every other page
still holds" (`composition.md:9-12`).

**`add_world` parameters** (`reference/api.md:138-146`):

| Parameter | Contract |
|---|---|
| first positional | the added world's `World` object (required) |
| `name` | this host's internal identity: path segment, file name, schema name. `^[a-z][a-z0-9_]*$`, no `__`, not `main`/`temp`. **Never agent-visible.** Defaults to the added world's own `name` |
| `store` | the account **scope** this world and its **whole subtree** belong to. `None` keeps the adder's own scope (this is what shares a store). Scope names are **global across the whole tree**, dependencies' internals included — a `store="eu"` inside a package you never read shares with your own `store="eu"` |
| `tool_prefix` | prepended to every contributed tool name (`"pay_"` → `create_charge` becomes `pay_create_charge`) |
| `tool_allow_list` | only these tools reach the agent surface |
| `tool_block_list` | every tool except these (mutually exclusive with allow list) |
| `startup` | keyword args bound to *that node's* startup hooks — **not overridable** by a `reset()` keyword of the same name |

**What the agent sees under composition**: one flat, insertion-ordered tool list — host's own tools
in registration order, then each added world's contribution in `add_world` order, in the order that
world lists them. **"Order is part of what the agent observes"** — a host that cares orders its
`add_world` calls to match the client's real surface (`composition.md:168-171`).

**Faithfulness under composition splits**, per `authoring.md:549-556`: "each added world is faithful
to its own vendor, and you are faithful to what the client's agent actually sees — your own tools,
your composite tools, and the names, the filtering and the *order* you declare for the added
worlds." Directly relevant to this project's stated goal of being `add_world`-ed as a billing
subsystem — see project_overview.md §2.3.

**A prefix renames the tool and never the description text.** If a Stripe world's own descriptions
cross-reference its own tool names ("call `create_customer` first" — which a faithful Stripe world's
docstrings plausibly will, mirroring Stripe's own docs), a host that prefixes it (`stripe_` say)
breaks that cross-reference; `seahaven check` warns (`SH206`) but **never rewrites the text** — "the
description is the added world's statement about its own product, and a host that edits it is no
longer serving that world" (`composition.md:182-186`). This is a concrete authoring constraint for
this project: either avoid cross-referencing unprefixed tool names in docstrings, or accept the
finding. Worth flagging in the functional spec.

**Sharing is the default, not opt-in**: "two worlds that both add the same payments package land on
the same store, exactly as two integrations share one real account" (`composition.md:194-196`,
matches project_overview.md's stated goal exactly: a company world and a shop world sharing one
billing account). A `store=` string opens a **new** named scope for the whole subtree.

**Reaching an added world**: `ctx.worlds.<name>` (or `ctx.worlds["name"]`) → a `WorldHandle`:
`call(tool, **kwargs)` (added world's own unprefixed name, full validation/chain/transaction),
`db` (that node's `Db`, `conn` included), `state` (that node's `ctx.state`), `worlds` (grandchildren).
Six rules, the sharpest being: **handles are call-scoped** — a handle dies at the end of the
activation (outermost call or `bulk()` block) that made it; using it after raises `WorldBug` rather
than silently addressing another instance (verbatim example and assertion in
`composition.md:329-367`). **There is no cross-world atomicity** — a nested call's transaction
commits on the child's connection when it returns; a later failure in the host tool rolls back only
the host's own store (`composition.md:322-325`) — a hard constraint for anything modelling e.g. "the
charge succeeded but the invoice write failed."

**Middleware under composition**: a call descends every world's middleware along the canonical root
→ owning-node route, outermost first — host's, then intermediate, then owning world's, then the
tool. Each layer sees **its own world's** `ctx` (db/state/ids/worlds), not a foreign one. A call made
host→child via a handle runs **only** the owning world's chain (the host's is already wrapped around
the host tool that's calling it).

**Startup hooks under composition**: depth-first from the root, each node's hooks run **once**
however many routes reach it; `reset()` keywords beyond the reserved four are **broadcast** to every
hook in the tree naming that keyword; `add_world(startup=...)` keywords are **configuration** and
cannot be overridden by a same-named `reset()` keyword.

**Fixtures under composition**: one sidecar, `format_version: 2`, one state file per node, one
shared `now` (there is exactly one clock per instance, not one per store). All nodes must be built
in one instance at one instant — "There is no path that combines stores frozen at different times"
(`composition.md:530`). Freeze is all-or-nothing across every node.

**Limits** (`composition.md:705-731`): hard cap is **SQLite's attached-database limit — 125 added
stores** on the bundled SQLite build (probed at seal time, not assumed, with the real number in the
refusal message). Node counts multiply with scopes: "A world added under three scopes, with four
worlds in its own subtree, is twelve nodes and twelve files." An idle composite instance still costs
one file/connection per node (a session opens per node per call, closes with the call). Sealing costs
a tree walk on first use after any registration anywhere in the process — steady state is one integer
compare per call. **Never** `copy.copy()` a dependency (only the root) in a test, or a host that
added the original won't see the copy.

`seahaven check`'s eight composition-specific codes: `SH206` (prefix/description mismatch), `SH207`
(one tool contributed under two names), `SH208` (`.instance()` inside `tools/`/`middleware/`),
`SH209` (unregistered `ctx.worlds.<name>`), `SH406` (composite sidecar disagrees with actual tree),
`SH502`/`SH503` (`Worlds` subclass mismatches), `SH504` (composition doesn't seal — the umbrella
finding for everything checkable only once the whole tree is known).

## Extensions (relevant if any Stripe-world plumbing should be packaged separately)

Source: `extensions.md` (whole page). An extension is an ordinary package depending on `seahaven`,
using its public API like any world would — no registry, no entry point, no discovery; the world
registers what it wants, by name, in its own `__init__`. Five things an extension may rely on: the
instance context, the middleware shape, instance startup, schema text (a `CREATE TABLE` string the
world concatenates in — **this changes the schema hash and costs every fixture a regen**), and
`seahaven.sandbox` for agent-SQL containment. Must not monkeypatch the framework, self-register, or
set the `control` flag. Not obviously relevant to this project's scope, but worth knowing if
e.g. a shared "webhook/events" or "idempotency-key" mechanism turns out to be reusable — see
project_overview.md §4's cross-cutting requirements.
