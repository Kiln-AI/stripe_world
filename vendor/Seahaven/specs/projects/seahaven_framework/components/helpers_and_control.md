---
status: complete
---

# Component: helpers, control tools, FTS5 support

Modules: `seahaven/helpers/run_sql.py`, `helpers/describe_schema.py`, `control.py`, plus the FTS5
awareness that lives in `db.shadow_tables`. Everything here is a `Tool` built on the sandbox
(`components/runtime_db.md` §4), over `ctx.db` for the helpers and over the instance's own
read-only control handle for the control tools; nothing here has its own containment.

## 1. `run_sql`

`run_sql(read_only=...)` is the sandbox mode for the SQL an agent writes; it has nothing to do with
how the tool itself is registered.

```python
def run_sql(*, name: str = "run_sql", tables: Sequence[str], read_only: bool = True,
            max_rows: int | None = None, max_bytes: int | None = None,
            description: str | None = None) -> Tool
```

Builds a `Tool` whose function is:

```python
def _run_sql(ctx: Ctx, query: Annotated[str, Field(min_length=1, description="One SQL statement in the SQLite dialect.")]) -> dict:
    result = sandbox.run_statement(ctx.db, query, authorizer=Authorizer(allowed_tables, read_only=read_only),
                                   max_rows=max_rows, max_bytes=max_bytes)
    return {"columns": result.columns, "rows": result.rows, "row_count": result.row_count,
            "truncated": result.truncated}
```

- `allowed_tables = set(tables) | {"sqlite_master", "sqlite_schema", "json_each", "json_tree"}` and
  the function allowlist are computed once, at factory time. The `Authorizer` itself is constructed
  **per call**, inside the tool function: it carries the call's refusals, so a shared one would let
  one instance's denied table name appear in another instance's error.
- The `Tool` is registered with `transaction=True`. With `read_only=False`, writes to the listed
  tables commit with the call; schema changes, `ATTACH`, `PRAGMA` are still denied by the
  authorizer.
- Default description: "Run one read-only SQL statement (SQLite dialect) against the tables:
  a, b, c. Returns columns and rows." with "read-only" dropped when writes are allowed. The world
  overrides it to match the product.
- Errors: `DbError` from the sandbox. The default `DbError.message` for a helper-raised error is
  SQLite's own text when `refusals` is empty and "not allowed: <refusal>" otherwise (this is the one
  place `sqlite_message` is the default message, because the mimicked product is a SQLite door).
  The standard error handler passes `DbError` from `run_sql` through unchanged only if the world
  says so; the scaffold maps every `DbError` to `internal`, and the ProjectTracker handler shows the
  pattern of letting `run_sql`'s through (`if call.name == "run_sql": raise`).
- Bytes in results are base64 text; `NULL` is `null`.

## 2. `describe_schema`

```python
def describe_schema(*, name: str = "describe_schema", tables: Sequence[str],
                    description: str | None = None) -> Tool
```

Function (no arguments; it writes nothing):

```
for table in tables (in the given order):
    columns = ctx.db.rows("SELECT name, type, \"notnull\", pk FROM pragma_table_xinfo(?) WHERE hidden <> 1 ORDER BY cid", table)
    fks = ctx.db.rows("SELECT \"from\", \"table\", \"to\", id, seq FROM pragma_foreign_key_list(?) ORDER BY id, seq", table)
```

Result: `{"tables": [{"name", "columns": [{"name", "type", "nullable": not notnull, "primary_key":
pk > 0}], "foreign_keys": [{"columns": [...], "references_table", "references_columns": [...]}]}]}`
with composite foreign keys grouped by `id`. `hidden <> 1` keeps every column the world declared and
drops only a virtual table's hidden columns: `hidden` is `2` and `3` for `GENERATED ALWAYS AS ...
VIRTUAL` and `... STORED`, and a generated column is declared, selectable and must be described like
any other. A listed table that does not exist in the instance is
a world bug: `WorldBug`, which the scaffolded handler re-raises rather than hiding.

## 3. Control tools (`control.py`)

Registered on every `World` at construction with `control=True`, bypassing the middleware chain and
the concurrency gate, dispatched by `Instance.call` directly:

```python
# `instance` is bound away by `_control_tool`, so a caller sends only the rest
def controller_run_sql(instance: Instance, ctx: Ctx, sql: str, params: list[SqlValue] | None = None) -> dict
def controller_changes(instance: Instance, ctx: Ctx) -> list[dict]
```

`control.dispatch(instance, ctx)` is the whole dispatch: `args = tool.validate(call.arguments)`,
which raises `ArgumentError` and is rendered like any tool error; run the control function; return
`to_jsonable_python(result)`. No middleware, no transaction (the control connection is read-only)
and no gate.

- `controller_run_sql` and `controller_changes` are thin wrappers over the instance's own
  read-only control handle and `Instance.changes()`; the control module owns no SQL or rendering
  of its own.
- `controller_run_sql` runs on the instance's **control** `Db` (`Instance._control_db()`, opened on
  first use and closed with the instance) and **never** on the `inspect()` handle an eval holds.
  Same file and same read-only opener, a second connection: `sandbox.run_statement` borrows
  connection-level state — the authorizer and the value limit — for the length of a statement,
  while `inspect()` reads are taken without the instance lock, so running control SQL on the
  `inspect()` connection puts one thread changing that state under another thread's stepping
  cursor. That wedges inside SQLite and takes the interpreter with it, because the thread waiting
  on the connection holds the GIL. One statement, positional params, no authorizer beyond that
  connection's permanent write denial — which is mirrored for the length of the statement rather
  than replaced, so a control read cannot become a write — no caps. Result shape as `run_sql`.
  Errors are raised as `DbError` with SQLite's message as the message (an eval wants the real text)
  and are rendered into the observation like any tool error.
- `controller_changes` returns `[c.to_dict() for c in instance.changes()]`.
- Neither is ever in `Instance.tools()`. Their names are reserved: a world registering either name
  fails at registration.
- A control call takes the instance lock like any call, so it never interleaves with a step on the
  same instance; it then asks the instance for what it needs — its changeset, its control handle —
  with that lock already held, which is why the lock is a `threading.RLock`. The re-entry is
  same-thread; it is not a claim about lock-free reads.
- Over OpenEnv they exist only when `seahaven serve` was given `--include-control-tools`. Without
  it, `step` raises `UnknownTool(name)` before dispatch, exactly as for an unregistered name.

*Corrected 2026-09-13 — the control tools' connection and the `RLock`'s reason, measured in Phase 4
(whose round-1 review found the deadlock and whose plan records the fix); closes `BACKLOG.md` B8.*

## 4. FTS5 support

The only FTS5-specific code in the framework is `db.shadow_tables(conn)`. Its consumers:

| Consumer | Behaviour |
|---|---|
| `changes.start_session` | Neither the FTS5 virtual table nor its shadow tables are attached to the session |
| `conformance.schema_map` | Shadow tables excluded from both sides of the comparison |
| `lint.ddl` | Virtual tables exempt from STRICT and primary-key rules; shadow tables never listed |
| `sandbox.Authorizer` | Shadow tables are denied unless explicitly listed (they are not world tables); the FTS5 virtual table itself can be listed by a world that wants `MATCH` through `run_sql`, in which case the docs say to list its shadow tables too and to add **four** names to the function allowlist via `Authorizer(functions=...)`: `bm25`, `snippet`, `highlight` and **`match`** |
| `describe_schema` | A listed FTS5 table is described from `pragma_table_xinfo` like any table (its declared columns) |

`match` is the fourth name because SQLite asks the authorizer about the `MATCH` **operator** under
that function name: a world that lists the shadow tables and only the three auxiliaries gets
the refusal `function 'match'`. There is no fifth thing for a world to do. FTS5 also reads
`PRAGMA data_version` while preparing a `MATCH`, and no spelling of the published allowlists could
have allowed that — `sandbox._PRAGMAS` allows the question form on every door for this reason
(`components/runtime_db.md` §4), so it is the framework's to get right and not the world's.

ProjectTracker's `search_issues` is the reference pattern: `MATCH ? ORDER BY rank, issues.id LIMIT ?`
with `bm25()` and `snippet()`, and FTS5's syntax errors (which arrive as `DbError`) mapped by the
error handler to the world's validation shape when the call is `search_issues`.

*Corrected 2026-09-13 — the function allowlist was three names and the pragma was unstated; measured
in Phase 4, which closed both in code, and pinned by `tests/test_fts5.py`; closes `BACKLOG.md` B7.*

## 5. Test plan

- `test_run_sql.py`: allowed table reads; denied table read (`refusals`); `count(*)` on a denied
  table refuses; write under `read_only=True` refused, allowed under `read_only=False` to listed
  tables only, still no `CREATE TABLE`; `max_rows`/`max_bytes` truncation flags; `sqlite_master`
  readable; `json_each` usable; `random()` refused; result shape and base64 bytes; SQLite syntax
  error text is the message; the fixed value cap applies for the statement only; two threads
  refusing different tables on two instances classify independently.
- `test_describe_schema.py`: columns, nullability, primary keys (single and composite), foreign
  keys (single and composite), order; listed missing table is a `WorldBug`.
- `test_control.py`: `controller_run_sql` sees every table including framework-unknown ones and
  runs `PRAGMA table_info` (allowed on a read-only connection); a write attempt is refused; a
  control call made while the instance lock is held completes rather than deadlocking, and the
  control handle is a different connection from `inspect()` and is opened once; a bad
  argument is an `ArgumentError`; `controller_changes` after two calls; both absent from `tools()`;
  registration of a world tool named `controller_changes` fails; control calls bypass a middleware
  that would otherwise record them.
- `test_fts5.py`: a world with an FTS5 table freezes, forks and diffs cleanly (no shadow tables in
  changesets or conformance); `search` via `MATCH` works through a world tool; `MATCH` through
  `run_sql` is refused by default and works when the world lists the shadow tables and functions.
