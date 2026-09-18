---
status: complete
---

# Phase 1: skeleton and the runtime database layer

## Overview

The repository becomes a real, checked Python package, and the bottom layer of the runtime — the
one that knows about SQLite and nothing about tools — is built and tested.

Two things ship together: the project skeleton (`pyproject.toml` with the `serve` extra, uv, ruff,
ty, pytest on 3.14 in CI, the dependency licence check) and the modules of
`components/runtime_db.md`: `db.py`, `clock.py`, `ids.py`, `sandbox.py`, plus the framework error
hierarchy of `components/world_and_dispatch.md` §5, which those modules raise. The sandbox ships
with the attack suite of `architecture.md` §10.

Nothing in this phase knows what a tool, a world, a fixture or an instance is; every module here is
importable and testable on its own. `seahaven/__init__.py` exports only the names that exist after
this phase (`Db`, `Clock`, `Ids`, the five error types and the `sandbox` module); the rest of the
export list of `architecture.md` §1 arrives with the phases that build those names.

No `LICENSE` file (sign-off gated, Phase 13).

## Steps

1. **`pyproject.toml`.** `requires-python = ">=3.14"`. Runtime dependencies `apsw>=3.53`,
   `pydantic>=2.12,<3`, `pyyaml>=6`; `[project.optional-dependencies] serve = ["openenv>=0.4.2,<0.5"]`;
   `[dependency-groups] dev = ["pytest", "ruff", "ty"]`. Ruff (line length 100, `E,F,I,UP,B,SIM,RUF`),
   ty (`src`, `tests`, `scripts`) and pytest configuration. Hatchling builds `src/seahaven`.

2. **`src/seahaven/errors.py`** — the hierarchy of `world_and_dispatch.md` §5, verbatim:
   `SeahavenError`, `WorldBug`, `ToolError(code, message, details=None)` with `to_dict()` and a
   `__repr__` showing all three fields, and the three framework subclasses `ArgumentError`,
   `DbError`, `UnknownTool`, each fixing its code and building its message. `DbError`'s default
   message is `"database error"`; raw SQLite text reaches an agent only through
   `sqlite_message`/`refusals`, never through `message`.

3. **`src/seahaven/clock.py`** — `Clock` (aware UTC instant; `now()`, `iso()`, `from_iso()`,
   `wall()`, equality and hash by instant) and
   `register_clock_functions(conn, clock) -> apsw.Connection`, which overrides `date`, `time`,
   `datetime`, `julianday`, `unixepoch`, `timediff`, `strftime` and the three `current_*` constants
   with `SQLITE_INNOCUOUS | SQLITE_DETERMINISTIC` functions that substitute the instant for a
   `'now'` time value and evaluate SQLite's own function on a private `:memory:` helper connection.
   The helper is returned; `Db` owns and closes it.

   One deviation from the reference implementation, required by `architecture.md` §7 ("one format
   across every door"): the overrides that render a full timestamp — `datetime(...)` and
   `current_timestamp` — return the canonical text `%Y-%m-%dT%H:%M:%S.mmmZ` rather than SQLite's
   space-separated form, by evaluating `strftime('%Y-%m-%dT%H:%M:%fZ', ...)` on the helper. SQLite
   still does all the parsing and the arithmetic. Without it, `created_at > CURRENT_TIMESTAMP`
   compares `'…T12:00:00.000Z'` against `'… 12:00:00'` and is true for every row of the same day.
   `current_timestamp` keeps the instant's milliseconds (so a row a tool just wrote with
   `ctx.clock.iso()` is *not* after "now"). `date` and `time` are already unambiguous and are left
   as SQLite renders them; `julianday`, `unixepoch` and `timediff` are numeric or duration values
   and are untouched. The canonical rendering says more than it knows in exactly one place, the
   `'localtime'` modifier, which stamps `Z` on a host-dependent local time; a test pins it and the
   lint phase is where a world using it should be told. `Clock` truncates its instant to
   milliseconds at construction, the precision `iso()` renders and SQL therefore sees, so two
   instants nothing in the framework can tell apart are equal and hash alike.

4. **`src/seahaven/ids.py`** — `instance_seed(source, caller_seed)`
   (`sha256(source + b"\0" + seed)`; `None` → `b"default"`, `int` → 8 bytes big-endian, negative
   refused with `WorldBug`) and `Ids(seed)` with `random: random.Random` and
   `uuid() -> str` (UUIDv4-shaped, drawn from the seeded stream).

5. **`src/seahaven/db.py`** — `SqlValue`, `Exec`, `Db`, `open_instance`, `open_inspection`,
   `build_blank`, `shadow_tables`, `world_tables`, per `components/runtime_db.md` §1:

   ```python
   class Db:
       def one(self, sql: str, *params: SqlValue) -> dict[str, Any] | None
       def rows(self, sql: str, *params: SqlValue) -> list[dict[str, Any]]
       def execute(self, sql: str, *params: SqlValue) -> Exec
       def executemany(self, sql: str, rows: Iterable[Sequence[SqlValue]]) -> int
       def transaction(self) -> AbstractContextManager[None]
       @property
       def conn(self) -> apsw.Connection
       @property
       def in_transaction(self) -> bool
       def close(self) -> None
   ```

   Rows are dicts built from `cursor.get_description()`; duplicate names keep the last. Every
   method wraps `apsw.Error` as `DbError(sqlite_message=str(e), sqlite_code=e.extendedresult)`.
   `transaction()` is APSW's connection context manager (BEGIN at the top level, SAVEPOINT when
   nested); only the BEGIN and the COMMIT are wrapped, so an exception from the body — including an
   `apsw.Error` raised through the public raw `conn` — reaches the caller unchanged.
   `open_instance` sets WAL, `synchronous=NORMAL`, `foreign_keys=ON`, `DEFENSIVE`,
   `TRUSTED_SCHEMA=0`, `busy_timeout=0`, load-extension off, clock functions, and leaves every
   `sqlite3_limit` at SQLite's default. `open_inspection` opens `file:…?mode=ro`, the same
   hardening and clock functions, and installs a permanent `deny_writes` authorizer (the write
   action codes, `SQLITE_ATTACH`, and `SQLITE_PRAGMA` when the pragma is a setter).
   `shadow_tables` derives FTS5 shadow tables by prefix; `world_tables` is every non-`sqlite_`,
   non-shadow table, the FTS5 virtual table included.

6. **`src/seahaven/sandbox.py`** — the public containment: `ALLOWED_FUNCTIONS`, `MAX_VALUE_BYTES`,
   `Authorizer(tables, *, read_only=True, functions=ALLOWED_FUNCTIONS)` (default-deny, ASCII-only
   case folding, refusals recorded, never `SQLITE_IGNORE`), frozen `SqlResult`, and
   `run_statement(db, sql, params=(), *, authorizer, max_rows=None, max_bytes=None)`: an exec
   tracer for the single-statement rule, `is_readonly` and the column names; the authorizer and
   `SQLITE_LIMIT_LENGTH` saved, set and restored first in `finally`; rows collected with `bytes` as
   base64 and optional row and byte caps (`max_bytes` counts UTF-8 bytes) setting `truncated`;
   failures classified into `DbError` — tracer abort, then the authorizer's refusals, then a value
   over `MAX_VALUE_BYTES` (`apsw.TooBigError`, a result code rather than message text), then a
   plain SQLite error.

   Refusal names are stable API: every refusal string begins with a `Refusal`, and `refusal_kind`
   is the seam an extension classifies on. A row write is refused as a `write` whether the table
   was unlisted or the whole statement was; DDL, which SQLite asks about as a row write to
   `sqlite_master`, stays an `action`.

7. **`src/seahaven/__init__.py`** — export `Db`, `Clock`, `Ids`, `SeahavenError`, `WorldBug`,
   `ToolError`, `ArgumentError`, `DbError`, `UnknownTool` and the `sandbox` module, with `py.typed`
   beside them.

8. **`scripts/check_licences.py`** — resolves the runtime dependency closure from installed
   metadata (extras and dev groups excluded), reads `License-Expression`, the licence classifiers
   and legacy `License`, and exits non-zero for anything outside the permissive allowlist
   (MIT, Apache-2.0, BSD-class, ISC, PSF, Zlib, Unlicense, 0BSD).

9. **`.github/workflows/ci.yml`** — uv with Python 3.14: `ruff format --check`, `ruff check`,
   `ty check`, `pytest`, and the licence check.

## Deviations from `components/runtime_db.md`

Two places where the component spec and the code differ, and why. Everything else follows it.

- **§1.2, "APSW returns a bare value for a single-column row, which is normalised to a tuple
  first".** Not true of `apsw>=3.53`, the floor this project pins: iterating a cursor yields a
  tuple per row whatever the column count, and the bare value belongs to `Cursor.get`, which
  nothing in `Db` uses. The normalisation would be dead code, so `_dicts` does not carry it and
  says what it checked instead. `test_a_single_column_row_is_still_a_dict` is what would catch the
  assumption breaking.

- **§1.1, `Exec(rowcount: int, last_rowid: int | None)` is narrowed to `last_rowid: int`.**
  `sqlite3_last_insert_rowid` always answers: `0` before a connection's first insert, and
  otherwise the last rowid inserted *on the connection*. SQLite offers no "this statement did not
  insert", and `0` is a legal rowid, so mapping `0` to `None` would report a real row as no row —
  leaving no value `None` could stand for. Declaring an optional that cannot occur would put an
  unreachable `assert` or `or 0` in front of every use of the field in world code, under a `ty`
  gate that is not optional. `test_rowid_zero_is_a_rowid` pins both the value and the type.

## Tests

`tests/test_errors.py`

- `to_dict` shape for a bare `ToolError` and for each framework subclass
- `str(e)` is the message; `repr(e)` shows class, code, message and details
- `ArgumentError` builds `"invalid arguments: <path>: <message>; …"` and keeps `tool`/`violations`
- `DbError` message is `"database error"` with no refusals and `"not allowed: <first>"` with them,
  and never leaks `sqlite_message` into `message`
- `UnknownTool` message and code
- a world-defined subclass fixes its own code and is caught as `ToolError`
- `WorldBug` and `ToolError` are both `SeahavenError` and neither is the other

`tests/test_clock.py`

- every overridden function with and without an explicit time value matches SQLite's own result
  computed on a plain connection with the instant substituted
- modifiers (`'+1 day'`, `'start of month'`, `'weekday 0'`) match SQLite's
- `strftime('now', …)` — `'now'` as the *format* — is not substituted
- `'NOW'` folds, `' now '` does not (no more permissive than SQLite)
- `DEFAULT (current_timestamp)` in a `STRICT` table under `TRUSTED_SCHEMA=0` yields the instant
- a trigger writing `datetime('now')` yields the instant
- `current_timestamp` equals `clock.iso()` and `datetime('now')` renders canonical text
- rows written before and after the instant: `created_at > CURRENT_TIMESTAMP` selects exactly the
  later ones, and a row written at the instant is not selected
- `Clock.wall()` truncates to milliseconds; naive datetimes refused; equality and hash by instant,
  including two instants that differ only below a millisecond; `from_iso`/`iso` round-trip
- `datetime('now', 'localtime')` is pinned as the one value the canonical rendering mislabels

`tests/test_ids.py`

- same seed, same stream and same uuids; different caller seeds diverge
- `None`, `int` and `bytes` caller seeds; negative `int` refused
- the same caller seed under two sources gives different streams
- uuid shape: parses as a `UUID`, version 4, RFC 4122 variant

`tests/test_db.py`

- dict rows: multi-column, single-column, no rows, duplicate column names keep the last
- `one` returns `None` for an empty result and leaves no statement open (a following write works)
- `execute` reports `rowcount` and `last_rowid`, and rowid `0` is a rowid; `executemany` returns
  the number of changed rows
- `build_blank` runs every statement of a DDL file, including what follows a row-returning one
- `DbError` wrapping carries the SQLite message and an extended code
- `transaction()` nesting: an inner savepoint rolls back and the outer transaction survives; a
  top-level rollback undoes everything; `in_transaction` tracks it
- an `apsw.Error` raised through the raw `conn` inside `transaction()` stays an `apsw.Error`
- `conn` is the live connection: a write through it is visible to `rows`
- the inspection connection allows `SELECT` and the report-only pragmas whatever their case,
  refuses `INSERT`, `PRAGMA journal_mode=DELETE`, `PRAGMA optimize` and `ATTACH`, and sees the
  clock
- `busy_timeout` is 0: a second writer fails immediately rather than waiting
- `sqlite3_limit` values on an instance connection equal SQLite's defaults
- `open_instance` sets WAL, `foreign_keys`, `DEFENSIVE`, `TRUSTED_SCHEMA=0`, and load-extension off
- `build_blank` refuses an existing file, runs the DDL and adds no tables of its own
- `shadow_tables`/`world_tables` over a schema with an FTS5 table (shadow tables excluded, the
  virtual table kept), including an FTS5 table with `content=`

`tests/test_sandbox.py` (the attack suite)

- every denied action class: `INSERT`/`UPDATE`/`DELETE` under `read_only`, `CREATE TABLE`,
  `DROP TABLE`, `ALTER TABLE`, `CREATE TRIGGER`, `CREATE VIEW`, `ATTACH`, `DETACH`, `PRAGMA`,
  `SAVEPOINT`/transaction control, `REINDEX`, `ANALYZE`
- a read of an unlisted table is refused, and the `SQLITE_IGNORE` regression: `count(*)` on an
  unlisted table refuses rather than returning a count
- case-folded table names: `SELECT id FROM ISSUES` and `SELECT count(*) FROM ISSUES` both allowed;
  a Unicode near-fold (`ıssues`) is not
- multi-statement payloads (`SELECT 1; DROP TABLE t`) abort with the single-statement refusal,
  and a cap reached by the first statement truncates instead, with nothing after it run
- `read_only=False` allows writes to listed tables only, and still refuses DDL
- the function allowlist: `random()`, `load_extension()` and an unlisted function refused; the
  clock overrides and JSON functions allowed; a caller-supplied `functions` set replaces the
  default and folds like SQLite
- shadow tables are refused unless explicitly listed
- the fixed value cap: `printf('%1000000000d', 1)` fails immediately, classified as a refusal
- `max_rows` and `max_bytes` truncate and set `truncated`; unset means the whole result; a
  non-ASCII row proves `max_bytes` counts bytes; a negative cap is a `WorldBug`
- a refused row write classifies as `write` and refused DDL as `action`; `refusal_kind` raises a
  `WorldBug` on text that is not a refusal
- `bytes` come back base64; `params` bind positionally
- the authorizer and `SQLITE_LIMIT_LENGTH` are restored after success, after a refusal and after
  truncation, and the connection is usable afterwards
- refusals reset between statements on one authorizer
- the quadratic-builtin residual (`instr`/`replace` under the value cap) is pinned as documented

`tests/test_licence_check.py`

- the allowlist accepts the metadata of the real runtime closure (the check passes on this project)
- the closure is the runtime one: extras and the dev group are outside it
- a fabricated distribution with a copyleft expression is rejected, as is a choice of licences and
  an undeclared one
- a requirement that is not installed is reported rather than raising
