---
status: complete
---

# Component: runtime database layer

Modules: `seahaven/db.py`, `clock.py`, `ids.py`, `sandbox.py`. The layer between APSW
and everything else: a convenience wrapper and one error type, with the raw connection public.
World code is trusted; nothing here is a barrier. Nothing below this layer knows what a tool is.

## 1. `db.py`

### 1.1 Public interface

```python
class Db:
    """The connection wrapper world code sees as ctx.db."""
    def one(self, sql: str, *params: SqlValue) -> dict[str, Any] | None: ...
    def rows(self, sql: str, *params: SqlValue) -> list[dict[str, Any]]: ...
    def execute(self, sql: str, *params: SqlValue) -> Exec: ...          # Exec(rowcount: int, last_rowid: int | None)
    def executemany(self, sql: str, rows: Iterable[Sequence[SqlValue]]) -> int: ...
    def transaction(self) -> AbstractContextManager[None]: ...            # savepoint when nested, BEGIN at top level
    @property
    def conn(self) -> apsw.Connection: ...       # the raw connection, public; see 1.2
    @property
    def in_transaction(self) -> bool: ...
    def close(self) -> None: ...

type SqlValue = None | int | float | str | bytes

def open_instance(path: Path, clock: Clock, seed: bytes) -> Db: ...
def open_inspection(path: Path, clock: Clock, seed: bytes, stream: bytes) -> Db: ...
def build_blank(path: Path | Literal[":memory:"], ddl: str) -> apsw.Connection: ...
def shadow_tables(conn: apsw.Connection) -> frozenset[str]: ...
def world_tables(conn: apsw.Connection) -> list[str]: ...
```

`params` are positional `?` bindings only. Named parameters are not offered: one style, and the
positional one is what SQLite's own docs lead with.

### 1.2 Behaviour

- **Rows as dicts.** `one`/`rows` build dicts from `cursor.get_description()` names. Iterating an
  APSW cursor yields a **tuple** per row whatever the column count, so there is nothing to
  normalise: the bare value a single-column row can arrive as belongs to `Cursor.get`, which `Db`
  never uses. Checked against `apsw>=3.53`, the floor this project pins, and held by a tripwire
  test. Duplicate column names in a `SELECT` keep the last (SQLite's own behaviour in
  `row_factory`s); the docs say to alias. *Corrected 2026-09-13 — measured in Phase 1, which
  recorded the deviation and its tripwire in `phase_plans/phase_1.md`; closes `BACKLOG.md` B2.*
- **Error wrapping.** Every method catches `apsw.Error` and raises `DbError(sqlite_message=str(e),
  sqlite_code=e.extendedresult if present, refusals=())`.
- **`transaction()`** returns `conn` itself used as a context manager (APSW semantics: BEGIN or
  SAVEPOINT depending on nesting; COMMIT/RELEASE on exit; ROLLBACK on exception). The instance
  layer's per-call transaction is the same mechanism at the outermost level.
- **The inspection connection's write denial** is internal to `open_inspection`: a `deny_writes`
  authorizer (the write action codes plus `SQLITE_ATTACH`, and `SQLITE_PRAGMA` when the pragma
  writes) installed once, for the connection's life. No other connection installs
  it and no call toggles it; the sandbox's authorizer is the only other one, it replaces rather than
  stacks, and it restores what it found.
- **`conn`** is the raw `apsw.Connection`, for anything the wrapper does not cover (incremental
  BLOB I/O, an exec trace, a virtual table). Errors raised through it are APSW's, not `DbError`;
  the error handler's catch-all maps them. The docs state the invariants: do not close it, change
  pragmas or the authorizer, or open a second connection to the instance file, because the clock
  functions, the changeset session and the per-call transaction live on this connection.

### 1.3 Connection setup

`open_instance`:

```
conn = apsw.Connection(str(path))
conn.pragma("journal_mode", "WAL"); conn.pragma("synchronous", "NORMAL")
conn.pragma("foreign_keys", "ON")
conn.config(SQLITE_DBCONFIG_DEFENSIVE, 1); conn.config(SQLITE_DBCONFIG_TRUSTED_SCHEMA, 0)
conn.enable_load_extension(False)
conn.set_busy_timeout(0)                       # one writer per instance by construction; a wait is a bug
register_random_functions(conn, seed, INSTANCE_STREAM)  # section 3
helper = register_clock_functions(conn, clock)          # section 2
```

No `sqlite3_limit` is changed from SQLite's defaults on this connection (statement length 1 GB,
expression depth 1000, compound selects 500, `LIKE` pattern 50,000, 32,766 variables). The sandbox
lowers `SQLITE_LIMIT_LENGTH` for the duration of an agent statement and restores it (section 4);
world code is trusted and gets SQLite's own defaults.

`open_inspection`: `file:<path>?mode=ro` URI, `SQLITE_OPEN_READONLY`, same hardening, clock
functions, randomness functions on the `stream` the caller names (`INSPECTION_STREAM` for the
`inspect()` handle, `CONTROL_STREAM` for the control tools' own), `deny_writes` authorizer installed
permanently.

`build_blank(path, ddl)`: refuses an existing file; `foreign_keys=ON`; executes the DDL in one
transaction; returns the open connection. The framework adds no tables of its own.

`shadow_tables(conn)`: for every `sqlite_master` row whose `sql` matches `USING fts5`
(case-insensitive), every `sqlite_master` table whose name begins with that table's name and an
underscore. Deriving them by prefix rather than from a fixed list of five is what makes the set
correct under FTS5's `content=` and `columnsize=` options, which change which shadow tables exist.
A world table whose name collides with a shadow table's is refused by the DDL check, which is the
only ambiguity the prefix rule has. `world_tables(conn)`: every `type='table'`
row not starting with `sqlite_` and not a shadow table; the FTS5 virtual table itself is included
(it is the world's) but is excluded from sessions (section 8.3 of the architecture) because the
session extension cannot track virtual tables.

## 2. `clock.py`

The design:

- `Clock(now: datetime)` requires an aware datetime; stores UTC; `now()`, `iso()`
  (`%Y-%m-%dT%H:%M:%S.mmmZ`), `from_iso(text)`, equality and hash by instant. Plus `Clock.wall()`,
  the one constructor that reads the wall clock (used only for blank instances), truncating to
  milliseconds.
- `register_clock_functions(conn, clock) -> apsw.Connection` registers overrides for `date`, `time`,
  `datetime`, `julianday`, `unixepoch`, `timediff`, `strftime` (time-value positions substituted;
  omitted time value appended) and constants for `current_timestamp`, `current_date`,
  `current_time`, all with `SQLITE_INNOCUOUS | SQLITE_DETERMINISTIC`, evaluating SQLite's real
  functions on a private `:memory:` helper connection. Returns the helper; `Db` owns and closes it.
- `'now'` matching is exact and case-insensitive, never padded, so the override is no more
  permissive than SQLite.

## 3. `ids.py`

```python
def instance_seed(source: str, caller_seed: bytes | int | None) -> bytes   # sha256(source + b"\0" + seed bytes)
class Ids:
    def __init__(self, seed: bytes) -> None
    random: random.Random                      # seeded from int.from_bytes(seed)
    def uuid(self) -> str                      # UUIDv4-shaped from random.getrandbits(128)
```

`caller_seed`: `None` → `b"default"`; `int` → 8-byte big-endian (negative refused); `bytes` as is.
Issue-key minting belongs to ProjectTracker; `Ids` is world-agnostic.

SQL's own randomness lives here too, because it is the same seed:

```python
INSTANCE_STREAM = b"instance"; INSPECTION_STREAM = b"inspection"; CONTROL_STREAM = b"control"
def register_random_functions(conn: apsw.Connection, seed: bytes, stream: bytes) -> None
```

Overrides for `random()` (0 args) and `randomblob()` (1 arg), registered `SQLITE_INNOCUOUS` and
deliberately **not** `SQLITE_DETERMINISTIC` — SQLite factors a constant deterministic call out of
the loop and evaluates it once. They draw from `random.Random(sha256(seed + b"\0" + stream))`, so
each of an instance's three connections replays a stream of its own and none of them is the stream
`ctx.ids` takes (which is the instance seed itself). SQLite's own PRNG is not an option: it is
seeded once per process from the default VFS's `xRandomness` and shared by every connection, and
per-connection seeding exists only as the test-control `SQLITE_TESTCTRL_PRNG_SEED`.

Semantics are SQLite's: `random()` is a signed 64-bit integer with `randomFunc`'s fold away from
`-2**63`; `randomblob(n)` reads `n` as `sqlite3_value_int64` does, raises `n < 1` to 1, and checks
the connection's `SQLITE_LIMIT_LENGTH` **before** drawing — the sandbox lowers that limit for the
length of an agent statement, and drawing first would allocate the value the cap exists to refuse.
The override holds a **weak** reference to the connection to read that limit: a strong one is a
cycle through the connection's own function table that the collector cannot break.

## 4. `sandbox.py`

The containment every SQL door shares, and a public module: `Authorizer`, `run_statement`,
`SqlResult` and the refusal names are part of `seahaven`'s API, and the refusal names are a
documented `Literal` and are stable, because an extension serving another dialect classifies on
them. The single-statement rule is enforced by the exec tracer rather than by counting statements
ahead of execution, and the row and byte caps are optional.

```python
class Authorizer:
    def __init__(self, tables: Iterable[str], *, read_only: bool = True,
                 functions: frozenset[str] = ALLOWED_FUNCTIONS) -> None
    refusals: tuple[str, ...]                  # what it refused since reset(), human-readable
    read_only: bool
    def reset(self) -> None
    def __call__(self, action, third, fourth, database, trigger) -> int   # SQLITE_OK or SQLITE_DENY, never IGNORE

@dataclass(frozen=True)
class SqlResult:
    columns: list[str]; rows: list[list[Any]]; truncated: bool
    row_count: int                              # len(rows)

MAX_VALUE_BYTES = 1_000_000   # the fixed cap on one SQL value

def run_statement(db: Db, sql: str, params: Sequence[SqlValue] = (), *, authorizer: Authorizer,
                  max_rows: int | None = None, max_bytes: int | None = None) -> SqlResult
```

An `Authorizer` carries mutable state, so one is constructed per call and never shared between calls
or instances. Two pieces of it, and the scope of both is the **call** and not the statement, because
both are cleared only in `reset()`, which `run_statement` calls once: `refusals`, what it turned
down, and `_wrote_a_row`, which records that this call has already been allowed a row write. The
flag is what makes the changeset session's `PRAGMA table_xinfo` question answerable without giving
an agent a pragma of its own (see below). The table allowlist and the function allowlist are
computed once, at factory time, and passed in.

Behaviour of `run_statement`:

1. `authorizer.reset()`; a fresh cursor with an `exec_trace` tracer (single statement; `is_readonly`
   check when `authorizer.read_only`; column names captured before the first step).
2. Save `conn.authorizer` and `SQLITE_LIMIT_LENGTH`; install the authorizer; set the limit to
   `MAX_VALUE_BYTES`.
3. Execute; collect rows, converting `bytes` to base64 text; stop at `max_rows` or when the
   JSON-serialised size passes `max_bytes` (`ensure_ascii=False`), setting `truncated`.
4. On `apsw.Error`: classify. Tracer abort reason → `DbError(refusals=(reason,))`; authorizer
   refusals → `DbError(refusals=authorizer.refusals)`; a value over `MAX_VALUE_BYTES` is classified as
   a refusal like any other; otherwise `DbError(sqlite_message=...)`.
5. `finally`: `cursor.close(force=True)`, restore authorizer and limit.

`MAX_VALUE_BYTES` is fixed and not configurable: no world sets it and no world can raise it. It exists
so agent SQL cannot materialise an enormous single value, which is the one failure mode measured as
worth stopping.

`ALLOWED_FUNCTIONS` allows aggregates, window, text, numeric, math and JSON functions and the clock
and randomness overrides — `random` and `randomblob` are section 3's, drawing from the instance's
seed rather than the host's entropy, so an agent may call them and the run still replays. Left out
on purpose: `load_extension`, `sqlite_version`, `changes`, `last_insert_rowid`, `total_changes`,
`sqlite_offset`. FTS5's functions are not in the
default list because shadow tables are denied, and there are **four** of them, not three: the
auxiliaries `bm25`, `snippet` and `highlight`, plus **`match`**, which is the name SQLite asks the
authorizer about when it meets the `MATCH` *operator*. A world exposing search through SQL passes
all four as `functions` and allows the shadow tables explicitly (not recommended, documented); one
that passes only the three auxiliaries is refused with `function 'match'`.

`_PRAGMAS` is the other half of that recipe, and it is the framework's rather than a world's: FTS5
reads `PRAGMA data_version` while *preparing* a `MATCH`, so `data_version` — asked as a question,
never in its assignment form — is allowed on every door, because no spelling of the published
allowlists could have allowed it and `MATCH` through `run_sql` is impossible without it. What that
buys an agent is a counter only another connection's commit moves, and an instance has one writer,
so the answer is the same number on every run. Every other pragma is an `action PRAGMA` refusal,
the introspection ones included. The single exception is `_SESSION_PRAGMA`: `PRAGMA
table_xinfo(<table>)`, which SQLite's session extension prepares inside an agent's statement the
first time the instance's changeset session sees a table change, is allowed only when this call has
already been allowed a row write to a table the door lists (`_wrote_a_row`, above). Denying it
would poison the session and lose `Instance.changes()` — the eval's score — for the life of the
instance; an agent's own `PRAGMA table_xinfo` follows no allowed write and stays a refusal.

The authorizer's table check folds ASCII case only (SQLite canonicalises differently for column
reads and bare row reads). Reads of `sqlite_master`/`sqlite_schema` are allowed when the caller
lists them (the helpers do).

*Corrected 2026-09-13 — the fourth function name and the two pragma allowances, measured in Phase 4
(`phase_plans/phase_4.md`) and pinned by `tests/test_fts5.py` and `tests/test_sandbox.py`; closes
`BACKLOG.md` B7 and B8.*

## 5. Test plan

- `test_db.py`: dict rows including single-column and duplicate-name selects; `DbError` wrapping
  with extended codes; `transaction()` nesting (savepoint rollback leaves outer intact); the
  inspection connection refuses `INSERT` and `PRAGMA journal_mode=DELETE` and allows `SELECT`;
  `conn` is the live connection (a blob written through it is visible to `rows`); `busy_timeout`
  is 0 (a second writer fails immediately, proving one-writer-by-construction); `sqlite3_limit`
  values equal SQLite's defaults.
- `test_clock.py`: every overridden function with and without an explicit time value; modifiers
  (`'+1 day'`, `'start of month'`) match SQLite's own results on the helper; `DEFAULT
  (current_timestamp)` in a `STRICT` table under `TRUSTED_SCHEMA=0` yields the clock; a trigger
  writing `datetime('now')` yields the clock; `strftime('now', ...)` with `'now'` as the format
  string is not substituted; `Clock.wall()` truncates to milliseconds; rows created before and
  after the frozen instant, with `created_at > CURRENT_TIMESTAMP` selecting exactly the later ones,
  so clock values and stored timestamps compare like with like.
- `test_ids.py`: same seed same stream; `int`/`bytes`/`None` seeds; uuid shape (version and variant
  bits).
- `test_sandbox.py`: an attack suite covering every denied action class, the
  `SQLITE_IGNORE` regression (`count(*)` on a denied table refuses), case-folded table names,
  multi-statement payloads (`SELECT 1; DROP TABLE t`), writes under `read_only`, allowed writes to
  listed tables only when `read_only=False`, function allowlist (`random()` refused,
  `load_extension` refused), `ATTACH` refused and every pragma refused but the two named in section
  4 — the `PRAGMA data_version` *question* (its assignment form refused) and the changeset
  session's `PRAGMA table_xinfo`, which is allowed only after a row write this same call was
  already allowed and is a refusal on a read-only door and a writable one alike — the fixed value
  cap turning `printf('%1000000000d')` into an immediate failure with flat memory and a refusal
  classification, row and byte truncation flags, bytes as base64, authorizer and limit restored
  after success and after failure, and the quadratic-builtin overrun pinned as a documented
  residual.

*Corrected 2026-09-13 — the pragma line of this plan, which said every pragma is refused; measured
in Phase 4 and pinned by `tests/test_sandbox.py`; closes `BACKLOG.md` B8.*
