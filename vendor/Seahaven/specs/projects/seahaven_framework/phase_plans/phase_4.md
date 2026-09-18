---
status: draft
---

# Phase 4: helpers and control tools

## Overview

The framework gets its first tools. Phases 1–3 built the runtime, the call path and the thing a call
is made on; everything a world could offer an agent, it had to write itself. This phase adds the two
helpers every SQL-shaped world ships with (`helpers/run_sql.py`, `helpers/describe_schema.py`), the
framework's own two tools (`control.py`), and the FTS5 awareness that makes a world with a full-text
index behave like any other world — all of `components/helpers_and_control.md`.

Nothing here has containment of its own. `run_sql` is a `Tool` around `sandbox.run_statement`,
`describe_schema` is a `Tool` around two pragmas, and the control tools are wrappers over a
read-only handle on the instance and `Instance.changes()`. What this phase actually owns is five
decisions:

- **What a SQL door answers with.** One result shape (`{columns, rows, row_count, truncated}`) and
  one error policy (SQLite's own text when there is no refusal, this framework's words when there
  is), shared by `run_sql` and `controller_run_sql` through `to_result` and `showing_sqlite_text`
  so the two cannot drift.
- **What a control tool is.** An instance-first function, registered on every world at construction,
  dispatched around the chain, the transaction and the gate, and held to the same serialiser as
  every other tool. Phase 3 left the shape and the BLOB policy to this phase; both are settled
  below.
- **Which connection a control read runs on.** Not the `inspect()` handle the spec names: that
  handle is read without the instance lock, and `run_statement` borrows connection state. The
  instance opens a second read-only connection for control alone. This was the round-1 Critical and
  the reasoning is recorded in full below.
- **What a write door costs the changeset.** A door with `read_only=False` is the first thing in
  the framework that makes SQLite's session extension record a change *under an authorizer*, and
  the session asks one question of its own when it does. Denying it silently voids
  `Instance.changes()` for the life of the instance. Recorded below.
- **What a world has to say to search through SQL.** The spec's FTS5 recipe did not work as written;
  it does now, and `tests/test_fts5.py` drives the whole of it through a real world.

## Steps

1. **`src/seahaven/helpers/run_sql.py`** — the SQL door. `run_sql(...)` computes the table and
   function allowlists once, at factory time, and builds a `Tool` whose function constructs a fresh
   `Authorizer` per call (it carries that call's refusals). `to_result` and `showing_sqlite_text`
   are public in the module because `control.py` uses both.
2. **`src/seahaven/helpers/describe_schema.py`** — the companion door. `pragma_table_xinfo` for
   columns, `pragma_foreign_key_list` for keys, read from the live schema on every call.
3. **`src/seahaven/helpers/__init__.py`** and `seahaven/__init__.py` — `seahaven.helpers` is part of
   the package's surface; the two factories are what it exports.
4. **`src/seahaven/control.py`** — `controller_run_sql`, `controller_changes`, the `dispatch` that
   `Instance._call` now delegates to, `_control_tool`, and the module-level `TOOLS` pair that
   `World.__init__` registers.
5. **`src/seahaven/world.py`** — every world registers `control.TOOLS` at construction, and `_add`
   asks about control names before duplicates.
6. **`src/seahaven/instances.py`** — `_dispatch_control` (Phase 3's placeholder) is gone; `_call`
   calls `control.dispatch(self, ctx)`, and `_control_db()` is the second read-only handle control
   runs on, opened once and closed with the instance.
7. **`src/seahaven/sandbox.py`**, **`errors.py`** — the changes the doors needed: `data_version`
   for FTS5, the changeset session's `table_xinfo` for a write door, `run_statement`'s restores, and
   `DbError`'s `message=`. All recorded below.

## Deviations and decisions a reviewer should check

- **The sandbox allows one pragma the agent cannot ask for: the changeset session's
  `PRAGMA table_xinfo`.** Found in the end-to-end pass, not by a test: a world with
  `run_sql(read_only=False)`, an agent write to a table the world's own fixtures never touched, and
  then `Instance.changes()` raises `apsw.AuthError: authorization denied`. Nothing about the write
  looks wrong — the call succeeds, the row is there, `inspect()` sees it — and the damage surfaces
  later, at the changeset, which is the eval's score.

  The mechanism: the first time the instance's session records a change to a table, SQLite's
  session extension prepares `PRAGMA table_xinfo(<that table>)` to learn the table's shape. That
  happens *during* the agent's statement, so it reaches the sandbox's authorizer, which denied it
  along with every other pragma. A denied authorizer call there is not an error the statement
  reports: `sqlite3session` stores the `SQLITE_AUTH` on the session and returns it from every later
  `changeset()`. So one write poisons the instance permanently. The reason the suite was green is
  an accident of fixtures — the session already knew every table a world seeded, so the question
  was only ever asked about a table no test's agent had been the first to write.

  Two routes: allow the pragma, or keep the session from ever needing to ask. The second has no
  supported form (`sqlite3session` learns a table's shape lazily on the first preupdate, and the
  only way to warm it is to make a change, which is the thing being recorded), so the fix is the
  first, written as narrowly as the question itself. `Authorizer` carries a per-*call*
  `_wrote_a_row`, set when it *allows* a row write; the pragma is allowed only when that flag is
  up, the name folds to `table_xinfo`, and the argument names a table the door lists. The session's
  question always follows a write it was already allowed to make, so it always matches; a statement
  that is itself `PRAGMA table_xinfo(issues)` authorizes no row write first and stays an
  `action PRAGMA` refusal, on a read-only door and a writable one alike. The flag goes down in
  `reset()`, which `run_statement` calls once, at the start of a call.

  **The flag's scope is the call, and the round-2 review was right that the code's comments claimed
  the statement.** They said "this same statement"; `reset()` is called once per call, so in a
  two-statement payload — `INSERT ...; PRAGMA table_xinfo(issues)` — the second statement's *prepare*
  does reach `SQLITE_OK` in the authorizer. Containment is not broken: `_StatementTracer` refuses
  every statement after the first unconditionally, before it steps, and the refusal an agent reads
  is "statement after the first in one call". The defect was that the comment promised a stronger
  invariant than the code enforces, and a later reader would have trusted it.

  I fixed the comments, not the code, and the reason is not only the cost of another security
  review. Narrowing the flag to one statement is not available at all: instrumenting the two
  callbacks shows the order is authorizer(prepare stmt1) → tracer(stmt1) → stmt1 steps → session
  asks `table_xinfo` → authorizer(prepare stmt2) → tracer(stmt2) vetoes. The session's question
  arrives *after* stmt1's trace call and *before* stmt2 is prepared, so there is no callback at the
  boundary between the statements to put the flag down at: clearing it in the tracer (the review's
  suggestion) clears it before stmt1's own session question, which is the defect this rule exists to
  fix. So the comments in `sandbox.py` — the `_SESSION_PRAGMA` block and `_session_asking`'s
  docstring — now say *call*, and name `_StatementTracer` as the thing that makes a call one
  statement and therefore the thing that refuses the second-statement form. Pinned by
  `test_the_shape_question_is_the_tracers_to_refuse_in_the_second_statement`, which asserts the
  tracer's wording for both the listed-table and the schema-qualified folded form, the allowlist's
  wording for an unlisted table, and that the first statement's row is written (the tracer vetoes
  the second statement; it does not undo the first).

  The adjacent cases, checked rather than assumed: the pragma table-valued function
  (`SELECT ... FROM pragma_table_xinfo('salaries')`, including inside a statement that does write a
  row) is refused a step earlier, as `read of table 'pragma_table_xinfo'`, by the table allowlist; a
  trigger's write to a second listed table gets its own shape question after its own authorized
  write; a trigger's write to a table the door does not list is refused, as before; and FTS5's
  shadow tables are untracked, so the session never asks about them. A read-only door's refused
  write does not raise the flag, nor does a writable door's refused one — both are pinned.

  This widens `sandbox.py`, which is Phase 2's, by one rule. It belongs to this phase because
  `read_only=False` is this phase's: before `run_sql` there was no way for SQL under an authorizer
  to write a row.

- **A BLOB reaches an eval as base64, from every door there is.** This is Phase 3's deferred seam.
  `changes.py` had already decided base64 for a changeset; `call.serialise` refuses raw `bytes`
  outright. The choice was whether a control result should be the one place bytes travel as
  something else. It is not: `controller_run_sql` goes through `sandbox.run_statement`, whose
  `_jsonable` base64-encodes, and `controller_changes` returns `Change.to_dict()`, which is base64
  already. So the two control tools cannot produce bytes, `run_sql` and a changeset say the same
  thing about the same blob, and `serialise`'s `WorldBug` stays what it was — a backstop against a
  *world's* mistake, not the mechanism that renders control results.
  `test_a_blob_arrives_as_base64_and_null_as_null` pins it at the control door and
  `test_a_blob_comes_back_as_base64_and_a_null_as_null` at the agent's.
- **`call.serialise` is still not re-exported from `seahaven/__init__.py`.** `control.py` imports it
  from `seahaven.call`, as `world.py` does. Nothing changed about who needs it: it is the framework's
  own rendering rule, not something a world calls.
- **`DbError` grows a keyword-only `message=`, and only the two SQL doors pass it.**
  `helpers_and_control.md` §1 wants SQLite's own text as the agent-facing message for a SQL door;
  `world_and_dispatch.md` §5 says engine text reaches an agent only where a helper explicitly does
  so. Rather than change `DbError`'s default — which every other raiser in the framework relies on —
  the default is untouched and `showing_sqlite_text(error)` builds a new `DbError` carrying the
  engine text as its message, with `raise ... from error` keeping the original. A *refusal* keeps
  the refusal as its message: "not allowed: read of table 'salaries'" tells an agent more than
  SQLite's "not authorized", and it is this framework's wording rather than the engine's.
- **`run_sql` takes a `functions=` parameter the spec's signature does not have.** §4 tells a world
  that wants `MATCH` through the SQL door to "add `bm25`, `snippet`, `highlight` to the function
  allowlist via `Authorizer(functions=...)`" — but the world never constructs the `Authorizer`,
  `run_sql` does, and §1's signature has no way to say it. `functions` is additive to
  `sandbox.ALLOWED_FUNCTIONS` rather than a replacement, so a world that lists two search functions
  does not silently lose `count`.
- **`PRAGMA data_version` is allowed by `sandbox.Authorizer`, in its question form only.** This is
  the one change to already-committed containment, and it is why the documented FTS5 path works at
  all: FTS5 reads `data_version` while *preparing* a `MATCH`, so with every shadow table and every
  function allowed, the statement still died with `action PRAGMA 'data_version'` — no spelling of
  the published API could have allowed it. The authorizer cannot tell FTS5's question from the
  agent's, so an agent may now ask it and read an integer back. That integer moves only when
  *another* connection commits to the file; an instance has one writer, so it is the same number all
  run, and nothing of the world is in it. The assignment form (`PRAGMA data_version = 3`) is refused
  even though SQLite treats it as a no-op, because "reads this counter" is what is being allowed.
  The name is ASCII-folded like every other name in that module. Pinned in `test_sandbox.py`: the
  question works and does not move under this connection's own write, the uppercase spelling works,
  and the assignment form and `PRAGMA table_info` are in `DENIED`. Those three refusals all carry a
  pragma *argument*, so they say nothing about which names are in the set; `PRAGMA database_list`
  and `PRAGMA compile_options` are in `DENIED` as well, asked with no argument, which is the shape
  the allowed one has — they are refused by the name and by nothing else. The set itself is
  asserted alongside them, because no query states an upper bound: a pragma allowed here is allowed
  to every agent in every world, and the two named would hand one the instance's absolute path on
  the host and the build options.
  The artifact that misleads a world here is recorded as **B7** in `BACKLOG.md`.
- **`match` is named as the fourth function a searching world passes.** SQLite asks the authorizer
  about the `MATCH` operator under the function name `match`; §4's three-function sentence leaves a
  world with `not allowed: function 'match'`. The comment on `ALLOWED_FUNCTIONS` says so now, and
  `test_match_through_the_sql_door_is_refused_by_default` pins the refusal a world that follows the
  artifact gets.
- **Negative caps are refused at registration, and zero is a policy.** §1 says nothing about
  validation; `max_rows=-1` would otherwise truncate every result silently. It is a `WorldBug` from
  the factory, where every other tool mistake is found. `max_rows=0` is a world that truncates
  everything and is allowed —`test_a_cap_of_zero_is_a_world_that_truncates_everything`.
- **A door onto no tables is refused at registration, in both factories.** `tables=[]` is accepted
  by §1's and §2's signatures and reaches the agent as a description that trails off — "against the
  tables: ." — on a SQL door that can still read `sqlite_master` and the table-valued functions, or
  a schema door that describes nothing. It is the same class of world mistake as a negative cap and
  is found in the same place. (`test_a_door_onto_no_tables_is_refused_at_registration`,
  `test_a_schema_door_onto_no_tables_is_refused_at_registration`.)
- **`EXPLAIN` is answered, not special-cased.** It is a read like any other: SQLite prepares it
  exactly as it prepares the statement behind it, so the authorizer is asked the same questions and
  an `EXPLAIN` of an unlisted table is refused with the same refusal the plain read gets. What comes
  back is the VDBE program, which is columns and rows like anything else. A product that exposes SQL
  exposes its `EXPLAIN`, and refusing it would be this framework inventing a rule the thing it
  mimics does not have. `test_explain_is_a_read_like_any_other` pins both halves.
- **`describe_schema`'s foreign keys come back in reverse declaration order, and that is pinned
  rather than sorted.** `pragma_foreign_key_list` numbers a table's keys from the last declared, and
  the entries are ordered by that number. The order is SQLite's, it is stable, and an agent reading
  a schema does not care which way round two keys are — but nothing said so, and a reordering of the
  agent-facing list would have left the file green. `test_a_single_column_foreign_key_names_what_it_points_at`
  now asserts the whole list on a table with two keys.
- **`describe_schema` is registered `transaction=False`.** §2 says it writes nothing, and there is
  nothing for a transaction to make atomic or roll back. `run_sql` keeps §1's `transaction=True`.
- **`describe_schema` resolves a foreign key that names no parent column.** §2's pseudocode passes
  `pragma_foreign_key_list` through, where `"to"` is NULL for a bare `REFERENCES parent` — which is
  no use to an agent that has to write the join. It is resolved from the parent's primary key, by
  key position, so a parent declared `PRIMARY KEY (b, a)` answers in that order and not in column
  order. A parent with no primary key has nothing to resolve to and is left as NULL rather than
  invented; SQLite refuses that key at use, not here. Both are pinned
  (`test_an_unnamed_composite_parent_key_is_resolved_in_key_order`,
  `test_a_parent_with_no_primary_key_is_left_as_it_arrived`).
- **The parent's key is read unconditionally, not behind a fast path.** An `if all(... is not None):
  return named` guard saves one pragma read per key on a path nobody is waiting on, and its two
  sides cannot be told apart by any input — the mutation sweep found it as a survivor and it is
  gone rather than recorded.
- **A control function's first parameter is the `Instance`, not the `Ctx`.** §3 writes
  `controller_run_sql(ctx, sql, params=None)`, but `Ctx` carries an `InstanceInfo` — an id, a
  fixture and a seed — and not the instance, by `ctx.md`'s design. Both control tools are wrappers
  over instance *methods*, so the instance is the first parameter, `functools.partial(fn, None)`
  binds it away before `Tool.from_function` reads the signature (so the tool's arguments are exactly
  what a caller sends), and `dispatch` supplies the real one. Phase 3 shipped this shape already:
  its control-dispatch tests are written against it, and they now build their stand-in tools with
  `control._control_tool` so the two cannot diverge.
- **A control read runs on a connection of the instance's own, not the `inspect()` handle.** This
  is the round-1 Critical, and the decision behind it is mine to record. §3 says
  `controller_run_sql` "runs on the instance's inspection `Db` (`Instance.inspect()`, opened on
  first use)" and that both tools are "thin wrappers over `Instance.inspect()` and
  `Instance.changes()`". Written that way it deadlocks. `sandbox.run_statement` borrows
  connection-level state — it sets the connection's authorizer and its `SQLITE_LIMIT_LENGTH` for
  the length of one statement and puts them back — while `fixtures_instances.md` §2.4 promises that
  reads through the `inspect()` handle take no instance lock. An eval reading its instance on one
  thread while a control call runs on another is therefore two threads on one connection, one of
  them changing it under a cursor that is stepping. It does not resolve: the thread blocked inside
  APSW holds the GIL, so the whole interpreter stops. Reproduced here on the first control call,
  every time, reader blocked in `db.rows` and control blocked setting the authorizer.

  The two ways out are a lock covering the `inspect()` connection, or a connection control owns. I
  chose the connection, and the first reason is not about cost:

  1. **The lock treats the symptom.** What is wrong is that one function mutates a connection it
     does not own — this framework says so itself, in `Db.conn`'s docstring: "Do not close it,
     change its pragmas or its authorizer". Control now runs where those words are true, and
     `run_statement` keeps its one precondition: whoever calls it owns the connection for the
     statement. A lock would leave the precondition unstated and the next caller to break it.
  2. **The lock costs the property `inspect()` exists for.** An eval's own reads would queue behind
     the agent's calls, which is exactly what §2.4 says they do not do. That is a `complete`
     artifact; changing it needs a maintainer's decision and cascades its dependents to draft, and
     I have not touched it. This is an argument, not a veto: if the maintainer would rather have
     the lock and amend §2.4, the code change is small and I will make it.
  3. **Nothing a caller can see changes.** `Instance._control_db()` opens the same file through the
     same `open_inspection` — read-only, same permanent write denial, same clock — once, lazily,
     under the instance lock, and `_close` closes it. Every control result, refusal and message is
     what it was, and the `inspect()` handle is still lock-free, now truthfully.

  The cost is one file descriptor per instance that has taken a control call, and one sentence of
  §3 that is no longer literally true (see Follow-up). A connection per call instead would cost a
  descriptor per *call* with nothing to close them; one handle is enough, because `Instance.call`
  holds the instance lock across the whole of a control call and only one statement is ever on that
  connection. Pinned by
  `test_a_control_read_answers_while_another_thread_reads_the_inspection_handle`, which runs the
  race in a child process: the regression wedges the interpreter, so a thread and a
  `join(timeout)` inside the test runner would hang CI rather than fail it. The child carries its
  own `faulthandler` deadline — that watchdog needs no GIL, so it prints the two colliding stacks
  even from a wedged process — inside the parent's `subprocess.run(timeout=...)`. And
  `test_the_control_handle_is_its_own_connection_opened_once_and_closed_with_the_instance` says the
  handle is not the eval's, is opened once, and goes when the instance does.
- **`run_statement` gives back everything it borrowed even when a restore fails, and fails as a
  `DbError` before the statement as well as during it.** Two smaller readings of the same Critical.
  Opening the cursor and reading the connection's current authorizer and limit happen before
  anything is borrowed, which is why they sit outside the restoring `try` — but they can still
  fail (a closed connection; a `ThreadingViolationError`), and a caller is owed the same error
  shape there as anywhere else rather than a raw APSW exception through two tool layers, so they
  are now in a `try` of their own that raises through `_failure`. And the three restores in the
  `finally` are nested rather than sequential: if putting the authorizer back raises, the tighter
  value limit would otherwise stay pinned on a long-lived connection for the rest of the process
  and a half-stepped statement would be left outstanding inside the caller's transaction. The
  failure itself is not swallowed. `test_a_connection_that_cannot_even_be_asked_is_still_a_db_error`
  and `test_every_restore_runs_even_when_one_of_them_fails` — the second needs a stand-in
  connection, because APSW will not refuse to take an authorizer back and the whole risk of a
  failing restore is that it is unforeseen.
- **`_ControlAuthorizer` mirrors the connection's permanent denial rather than replacing it.**
  §3 says control runs with "no authorizer beyond the inspection connection's permanent write
  denial" — but `sandbox.run_statement` *sets* the connection's authorizer for the length of the
  statement, so passing nothing would run control SQL with that denial switched off. (The
  connection is the control one now, opened the same way and carrying the same denial.) The one passed
  asks the permanent authorizer first and adds nothing to it, and when the answer is no, words the
  refusal in the sandbox's vocabulary. `read_only=True` goes with it, which is what refuses a
  statement SQLite itself calls a write before it steps. Two tests say why each half is
  load-bearing: `test_an_attach_is_refused` (a read-only connection would run `ATTACH` happily —
  the mirrored denial is what stops it) and `test_a_vacuum_is_refused_as_a_write_before_it_runs`
  (the authorizer is asked nothing about `VACUUM`; the tracer stops it).
- **A missing authorizer on the control connection is a `WorldBug`, not a fallback.** If the
  second lock has gone, a control door is not the place to find out.
  `test_a_control_connection_with_no_authorizer_is_a_world_bug` clears it on the handle control
  actually runs on; clearing `inspect()`'s, which is what it did before the Critical was fixed,
  now correctly changes nothing.
- **`MAX_VALUE_BYTES` applies to a control read, where §3 says "no caps".** The row and byte caps of
  §1 are genuinely absent — control reads whole tables. The 1 MB single-value cap is not a policy
  `controller_run_sql` sets; it is `run_statement`'s, for a reason that does not go away for a
  trusted caller (nothing can interrupt SQLite inside one opcode, so the cap is what bounds a
  `zeroblob(1e9)` before it allocates). An eval that needs a larger value reads it through
  `instance.inspect()`, which has no cap and is in the same process — advice that was true only
  between calls while control shared that connection, and is true from any thread at any time now
  that it does not.
  `test_the_fixed_value_cap_applies_to_a_control_read` pins both halves, the refusal and the way
  round it.
- **The two control tools are registered in `World.__init__`, and `_add` asks about control names
  before duplicates.** §3 says a world registering either name "fails at registration"; with the
  pair already in the registry, the duplicate check would fire first and tell the world its own tool
  was "registered twice", which is not what is wrong with it. The reordering is behind
  `test_a_control_tool_name_is_not_a_tool_name`, and a *second* control tool by the same name is
  still a duplicate (`test_a_control_tools_name_is_taken_even_by_another_control_tool`).
- **`instances.py` imports `control` lazily, inside `_call`.** `control.py` is written against
  `Instance`, so the dependency runs that way round; the call path needs it back in exactly one
  place, and a module-level import would be a cycle. `world.py` imports it at module level, where
  there is none.
- **A control tool is built with `transaction=False`.** `dispatch` runs no transaction, so the flag
  is never read; it is set to what is true so that a later reader of the registry is not misled.
  This is a deliberate equivalent mutant and is listed as one below.

## Tests

`tests/conftest.py` gains `WAIT` and the `Caller` thread helper, moved up from `test_instances.py`
so the SQL-door and control tests can use the same re-raising thread. Every test below drives a real
world through `Instance.call`; nothing calls a tool function directly.

`tests/test_run_sql.py` (36 tests)

- result shape; a query matching nothing still names its columns; base64 blobs and `null`
- a denied table, `count(*)` on a denied table, `random()`, a second statement in one call
- read-only refuses a write; `read_only=False` commits with the call, and still refuses an unlisted
  table and a schema change
- `max_rows`, `max_bytes`, an untruncated result, a cap of zero; a negative cap and an empty table
  list at registration
- `EXPLAIN` answered on a listed table and refused on one that is not
- a write to a table the world's own writes never touched leaves the changeset readable, through
  `Instance.changes()` and `controller_changes` alike; the shape question that write needs is not
  one the agent can ask, by pragma or by pragma table-valued function
- `sqlite_master`, `json_each`, `json_tree` without being listed
- SQLite's own text for a syntax error and an unknown column; the refusal keeps this framework's
  wording
- the fixed value cap applies for the statement and is put back afterwards
- an empty query is an `ArgumentError`; the listing is one required string
- the default description names the tables and the mode; a world's own description and name win
- a refusal is not carried into the next call; every call builds its own `Authorizer`; two
  instances refusing different tables on two threads classify independently

`tests/test_describe_schema.py` (18 tests)

- table order; column shape; nullability as SQLite reports it (a STRICT table's key is NOT NULL
  without the DDL saying so); every column of a composite key marked
- generated columns (`VIRTUAL` and `STORED`) described like any other
- single, implicit and composite foreign keys, the whole list in the order an agent reads it; a
  parent key in key order; a parent with no key; a table with none
- an empty table list at registration
- a listed table that is not in the schema is a `WorldBug`
- no arguments, `transaction is False`, an argument it does not take refused
- it describes the live schema (a table made after startup) and writes nothing

`tests/test_control.py` (25 tests)

- a table the framework knows nothing about; `PRAGMA table_info`; positional params
- a write, a schema change, `ATTACH` and `VACUUM` refused; the permanent denial still in place
  afterwards; a control connection with no authorizer is a `WorldBug`
- SQLite's own text as the message; a second statement refused; a blob in and base64 out
- the fixed value cap, and `inspect()` as the way round it
- bad arguments are `ArgumentError`s; `dispatch` without a call on the context is a `WorldBug`
- `controller_changes` after two calls, and that it is exactly `Instance.changes()` rendered
- neither tool in `tools()`; control calls bypass the middleware chain; a control call from inside a
  call under the same lock; a control call while another instance holds its lock, timed
- the control handle is its own connection, opened once, closed with the instance
- a control read and an `inspect()` read racing on one instance, in a child process with a deadline
  on each side of it: the Critical's pin

`tests/test_fts5.py` (13 tests)

- a world tool searching with `MATCH`, `snippet()` and `ORDER BY rank`
- the index is in no changeset; the world conforms to itself; it freezes, forks, searches after the
  fork and starts with no changes of its own
- `MATCH` through `run_sql` refused by default (`function 'match'`), refused with the functions
  allowed but the shadow tables not (`read of table 'issues_fts_idx'`), and working when the world
  lists both
- a shadow table the world left out is refused, parametrised over the two the module asks about
- a writable door still refuses a write to the index
- `describe_schema` describes a listed FTS5 table as the world declared it
- `controller_run_sql` reads a shadow table nothing else may, and searches the index with
  nothing listed at all

`tests/test_sandbox.py`, `test_world.py`, `test_instances.py` grew with the changes above: the four
pragma refusals, the one allowed pragma and the assertion of the allowlist itself; the two
`run_statement` failure paths (a connection that cannot be asked, and a restore that raises);
`registered(world)` filtering the control pair out of the world's own list; the control-dispatch
tests building their stand-in tools with `control._control_tool`.

`tests/test_sandbox.py` gains three, for the rule above: the changeset session's shape question is
allowed only after a row write *this call* was allowed, for a table the door lists, with the names
folded and the permission going down again on `reset`; a write that was *refused* — by a read-only
door or by the table allowlist — does not open it; and, in a two-statement payload whose tail is the
pragma, the refusal an agent reads is the tracer's `statement after the first in one call`, because
the flag's scope is the call and it is `_StatementTracer` that makes a call one statement.
`PRAGMA table_xinfo(notes)` joins the `DENIED` table, which is the read-only half of the same
claim.

## Mutation check

Every statement of the three new modules, of the whole of `sandbox.py`, and of the regions of
`errors.py`, `world.py` and `instances.py` this phase touched, was replaced with `pass` in turn and
the suite re-run on cleared `__pycache__` with `PYTHONDONTWRITEBYTECODE=1`. Then fifty mutations
statement deletion cannot express — a flag, an ordering, a condition, a replaced expression — were
made by hand.

Every number below was produced by a harness that proves the mutant is the code that ran: before
each mutant it imports `seahaven.sandbox` in the same interpreter the tests use and aborts unless
`__file__` resolves inside the mutation tree. This phase produced two false-survivor runs without
that check — one here, from a workspace path passed relative so `PYTHONPATH` resolved against the
subprocess's `cwd`; one in review, from a workspace copied with its `.venv` so an installed-package
finder reached the real tree. Different causes, one check catches both, and it is now a standing
Method note in `BACKLOG.md`.

The numbers below are of the tree as it now stands, not of the tree the first round was run on. The
review's changes rewrote `control.py`'s connection, `sandbox.py`'s `run_statement`, `instances.py`'s
handles and both helpers' factories, and the changeset-session rule recorded above rewrote
`Authorizer`. Each rewrite re-ran the hand mutations that came before it in full — a rewrite strands
a mutation record as easily as it strands a test — so all thirty-nine of rounds one and two were run
again against the tree round three left, and every equivalence record below was re-read against the
code as it now stands rather than as it was when the record was written.

| File | Statement mutants | Killed |
|---|---|---|
| `helpers/run_sql.py` | 27 | 25 |
| `helpers/describe_schema.py` | 27 | 24 |
| `control.py` | 42 | 39 |
| `sandbox.py` (the whole module, not only what this phase touched) | 123 | 120 |
| `instances.py` (`_call`, `_control_db`, `_bulk`, `_held`, `_close`) | 18 | 15 |
| `errors.py` (`DbError.__init__`) | 6 | 6 |
| `world.py` (construction and `_add`) | 14 | 14 |

**The sweep found two gaps and one redundant branch, and the hand mutations a third gap.** `control.dispatch`'s "a control tool is
dispatched from a context bound to its call" guard had nothing to kill it — `dispatch` is public, so
it now has `test_dispatch_without_a_call_on_the_context_is_a_world_bug`. `describe_schema`'s
`if all(column is not None ...): return named` fast path was a branch whose two sides no input can
tell apart; it is gone. The hand mutations found two more. `run_sql`'s `transaction=True` could be flipped with the whole
suite still green — it is now pinned as a property, for the reason given below. And hoisting the
per-call `Authorizer` to factory time — the thing §1 says in bold must not happen — survived
everything, including the two-thread test written for it: `run_statement` resets the authorizer it
is handed, so a shared one is wrong only when two calls *overlap*, and a test that races for it
passes on almost every run. `test_every_call_builds_its_own_authorizer` asks the question directly
instead, and the racing test stays for what it does pin.

**Round three is the changeset-session rule, and it found no gap either.** `sandbox.py` is swept
whole this time rather than by region — 123 statements, 120 killed, the three survivors being the
two recorded families plus `_StatementTracer`'s `self.columns` initialiser, below — and `Authorizer`
alone is 37 for 37, including both places `_wrote_a_row` is put down. Eleven hand mutations then attacked the
rule where statement deletion cannot: the allowance removed entirely (the defect itself), each of
its four conditions dropped in turn, both names left unfolded, `_SESSION_PRAGMA` pointed at
`table_info`, the flag raised on a write that was refused (twice, once per way a write is refused),
`reset` leaving it up between statements, and the flag starting up. All eleven are killed, ten of
them by the two `test_sandbox.py` tests and three also by the end-to-end pair in `test_run_sql.py`.

**Round two found no new gap.** Every one of the nine mutations written for the review's changes is
killed, each by a test written for that change: the Critical restored dies in the race test in
under the timeout rather than hanging the run, the review's exact pragma widening now fails four
tests where it left the suite green, and both empty-`tables` guards and all three of
`run_statement`'s restores are pinned. The one new survivor is equivalent and recorded below. The
statement sweep's survivors are the same four families as round one — lazy annotation imports,
`__all__`, the two stated orderings, and the `None`-ing of a handle already closed — with
`instances.py`'s three (`self._inspection = None`, `self._control = None`, `self._session.close()`)
covered by `phase_3.md`'s record, re-read against the rewritten `_close` and still true of it.

The thirty-nine hand mutations and the test that kills each. Thirty-six are killed; the three
survivors are the equivalents recorded below.

| Mutation | Killed by |
|---|---|
| `run_sql`'s authorizer built once at factory time, not per call | `test_every_call_builds_its_own_authorizer` |
| the door is always read-only | `test_a_write_to_a_listed_table_commits_with_the_call` |
| `run_sql` registered outside the call's transaction | `test_the_tool_is_registered_inside_the_calls_transaction` |
| a cap of zero refused with the negative ones | `test_a_cap_of_zero_is_a_world_that_truncates_everything` |
| engine text is the message even for a refusal | `test_a_read_of_a_table_the_world_did_not_list_is_refused` |
| the always-allowed tables dropped | `test_sqlite_master_is_readable_without_being_listed` |
| a world's `functions` replace the default allowlist | `test_counting_a_denied_table_is_refused_too` |
| the default description never says "read-only" | `test_the_default_description_names_the_tables_and_the_mode` |
| generated columns dropped with the hidden ones | `test_generated_columns_are_described_like_any_other` |
| only the first key column marked `primary_key` | `test_every_column_of_a_composite_key_is_marked` |
| nullability inverted | `test_columns_carry_their_type_nullability_and_key` |
| the parent's key in column order, not key order | `test_an_unnamed_composite_parent_key_is_resolved_in_key_order` |
| every unnamed parent column resolves to the first key column | `test_an_unnamed_composite_parent_key_is_resolved_in_key_order` |
| composite key columns in the pragma's order reversed | `test_a_composite_foreign_key_is_one_entry_in_key_order` |
| `describe_schema` registered inside the transaction | `test_the_tool_takes_no_arguments_and_is_registered_outside_the_transaction` |
| the control connection's permanent denial answered with `SQLITE_OK` | `test_an_attach_is_refused` |
| control SQL not run read-only | `test_a_vacuum_is_refused_as_a_write_before_it_runs` |
| the control function gets the raw arguments, not the validated ones | `test_a_control_tool_is_called_with_exactly_the_validated_arguments` |
| a control result is not serialised | `test_a_control_tools_result_is_held_to_the_rules_every_result_is` |
| the two tools not flagged `control=True` | 90 tests, first `test_a_composite_key_is_rendered_in_key_order` |
| the instance parameter not bound away before the signature is read | the suite does not import (`WorldBug` at `control.py` import) |
| the SQL result returned as the sandbox's own object | `test_it_reads_a_table_the_framework_knows_nothing_about` |
| the missing-authorizer guard dropped | `test_a_control_connection_with_no_authorizer_is_a_world_bug` |
| no pragma allowed | `test_allowing_the_functions_is_not_enough_without_the_shadow_tables` |
| the assignment form of an allowed pragma allowed too | `test_the_sandbox_refuses[PRAGMA data_version = 3-...]` |
| the pragma name not ASCII-folded | `test_the_one_pragma_that_is_allowed_is_the_data_version_question` |
| `DbError`'s explicit `message=` ignored | `test_sqlites_own_text_is_the_message` |
| the control-name check back after the duplicate check | `test_a_control_tool_name_is_not_a_tool_name` |
| `describe_schema`'s columns in whatever order the pragma gives | **survives — equivalent, below** |
| `transaction=True` on a control tool | **survives — equivalent, below** |
| the control read back on the `inspect()` handle — the Critical itself | `test_a_control_read_answers_while_another_thread_reads_the_inspection_handle` (and `test_a_control_connection_with_no_authorizer_is_a_world_bug`) |
| the control handle reopened on every call instead of once | `test_the_control_handle_is_its_own_connection_opened_once_and_closed_with_the_instance` |
| `_close` leaving the control handle open | `test_the_control_handle_is_its_own_connection_opened_once_and_closed_with_the_instance` |
| `run_statement`'s three restores in a row, not nested | `test_every_restore_runs_even_when_one_of_them_fails` |
| `run_statement`'s cursor and its two reads unguarded again | `test_a_connection_that_cannot_even_be_asked_is_still_a_db_error` |
| the pragma allowlist widened to `database_list` and `compile_options` — the review's exact widening | 4 tests, including `test_the_sandbox_refuses[PRAGMA database_list-...]` |
| a `run_sql` door onto no tables accepted | `test_a_door_onto_no_tables_is_refused_at_registration` |
| a `describe_schema` door onto no tables accepted | `test_a_schema_door_onto_no_tables_is_refused_at_registration` |
| the foreign keys ordered `seq, id` instead of `id, seq` | **survives — equivalent, below** |

Round three's eleven, all killed:

| Mutation | Killed by |
|---|---|
| the session's pragma allowance removed — the defect itself | `test_a_write_to_a_table_the_world_never_wrote_leaves_the_changeset_readable` |
| the "a row write was already allowed" guard dropped | `test_the_shape_question_that_write_needs_is_not_one_the_agent_can_ask` (and 4 more) |
| the "a table the door lists" check dropped | `test_the_session_shape_question_is_allowed_only_after_a_write_it_follows` |
| the "asked with an argument" check dropped | `test_the_session_shape_question_is_allowed_only_after_a_write_it_follows` |
| `_SESSION_PRAGMA` pointed at `table_info` | `test_a_write_to_a_table_the_world_never_wrote_leaves_the_changeset_readable` |
| the pragma name not folded | `test_the_session_shape_question_is_allowed_only_after_a_write_it_follows` |
| the table name not folded | `test_the_session_shape_question_is_allowed_only_after_a_write_it_follows` |
| the flag raised on a write the table allowlist refused | `test_a_write_a_read_only_door_refused_does_not_open_the_shape_question` |
| the flag raised on a write a read-only door refused | `test_a_write_a_read_only_door_refused_does_not_open_the_shape_question` |
| `reset` leaving the flag up between statements | `test_the_session_shape_question_is_allowed_only_after_a_write_it_follows` |
| the flag starting up | `test_the_session_shape_question_is_allowed_only_after_a_write_it_follows` |

What survives, and why each is a mutant with no behaviour to kill:

- **`from collections.abc import Sequence`, `from types import FunctionType`,
  `from collections.abc import Callable`, `from seahaven.db import Db`** — every one of these names
  appears only in the annotations of functions that nothing registers as a tool, so nothing
  evaluates it at runtime. Deleting the import changes nothing the suite can observe. (`ty` observes
  it, and is part of the gate.)

  *(Amended twice in Phase 5, and the second amendment is the one to read.* The reason first
  recorded here was "annotations are lazy on Python 3.14". That is false as a general rule: PEP 649
  defers evaluation, it does not prevent it, and Seahaven evaluates annotations at registration in
  two places — `World.middleware` calls `inspect.signature(obj)`, and `Tool.from_function` calls
  `inspect.signature(fn, eval_str=True)` (`tool.py:150`) to build the argument model.

  The first amendment replaced that with "modules nothing registers", and named these modules as
  such. That is false too, and more dangerously, because it licenses a deletion that breaks the
  package at import. `control.py:177` registers **at import time**: `TOOLS = (_control_tool(
  controller_run_sql), _control_tool(controller_changes))` runs through `Tool.from_function` while
  the module is still being executed. `helpers/run_sql.py` and `helpers/describe_schema.py` define
  the functions worlds register. Under the "modules nothing registers" rule, `control.py`'s
  `from typing import Any` would be droppable; delete it and `import seahaven.control` raises
  `WorldBug: tool 'controller_run_sql' is annotated with 'Any', which does not exist at runtime`,
  and every suite fails to collect.

  The distinction that is actually true is narrower: **a name that appears only in the annotations of
  functions nothing registers**. These four qualify for that reason and not the other one, and here
  is where each is actually used: `FunctionType` is in `_control_tool`'s own signature
  (`control.py:155`), a factory nobody registers; `Callable` is in `_ControlAuthorizer.__init__`
  (`control.py:104`), a constructor that is called but never registered, so nothing evaluates its
  annotations; and `Sequence` and `Db` are in the `run_sql` and `describe_schema` factories and their
  private helpers, not in the annotations of the inner functions those factories turn into tools.
  Each was confirmed by deleting the import and running both suites.

  The conclusion stands unchanged through both amendments: these four imports really do survive and
  are still equivalent mutants. It was the stated reason that was wrong, twice, and each time in a
  direction a later phase could have read as licence.)
- **`__all__` in all three modules** — it steers `from module import *`, which nothing in this
  package or its tests does.
- **`ORDER BY cid` in `describe_schema`'s column query** — `pragma_table_xinfo` yields rows in `cid`
  order already, so the clause states the order rather than imposing it. Kept because the order is
  part of what the tool promises and a future SQLite is not required to keep the accident.
- **`ORDER BY id, seq` in `describe_schema`'s foreign-key query, deleted** — the sibling of the
  record above and the same caveat, recorded late: the round-2 review found it neither pinned nor
  written down. `pragma_foreign_key_list` already yields its rows grouped by `id` and ascending in
  `seq` within a group, so deleting the clause changes nothing. Probed rather than assumed, on a
  table with two single-column keys and a composite one declared between them: the two queries gave
  byte-identical rows, including SQLite's reverse-of-declaration `id` numbering. Kept for the same
  reason as `ORDER BY cid` — the grouping walk downstream depends on the order, the tool promises
  it, and no future SQLite owes us the accident. Not the same mutant as the `seq, id` *swap*
  recorded below, which the review raised separately; both are equivalent, for different reasons.
- **`transaction=False` on a control tool** — `dispatch` runs no transaction, so the flag is read by
  nothing. Deliberate, and the code says so where it is set.
- **`self.columns: list[str] = []` in `_StatementTracer.__init__`** — the tracer sets `columns`
  itself, from `get_description()`, on the one path that ever reads them. Every other path raises:
  a tracer that aborted, or a statement that failed before the tracer ran, reaches `_failure`, which
  reads `abort_reason` and nothing else. So the initialiser is reached by no test because it is
  reachable by no caller; it is there to declare the attribute and its type. `ty` does not observe
  it either, which was checked rather than assumed.
- **`ORDER BY seq, id` in `describe_schema`'s foreign-key query** — the review read this as a
  mutation that reorders the agent-facing list, and it does not; I checked rather than assumed.
  `pragma_foreign_key_list` gives every key a `seq = 0` row, so under `seq, id` the first row of
  each key still arrives in ascending `id`, which is the order the grouping walks; within a group
  both clauses give `seq` order. A probe on a schema with a composite key interleaved between two
  single-column keys produced byte-identical output under the two clauses. It is an equivalent
  mutant — but the order was genuinely unasserted, which is the real finding, so
  `test_a_single_column_foreign_key_names_what_it_points_at` now asserts the whole ordered list
  rather than one entry, and the clause states what the tool promises instead of relying on an
  accident of the pragma.
- **`with self._held():` in `Instance._control_db`** — raised by the round-2 review, which deleted
  it with the suite green. It is equivalent, and deliberately kept. `_control_db` is reached from
  nowhere but the control tools, and `Instance._call` already runs those inside
  `with gate(bypass=tool.control), self._held():` on the same thread — so the `RLock` is held and
  the destroyed check has already been made by the time the handle is opened. Nothing can observe
  either the lock or the `WorldBug`. Kept because it is the shape every handle-opening method on
  `Instance` has, and the sibling it is a copy of — `inspect()`, at the same indentation — is a
  public entry point any thread can call, where the guard is the thing that keeps a lazy open from
  racing `destroy()`. Making the private one the odd member of the pair would invite the next
  reader to conclude the guard is optional in the public one too.
- **`self._control = None` in `Instance._close`** — the same mutant as `phase_3.md`'s record for
  `self._inspection = None` and `self._session.close()`: `_close` runs once, under `_destroyed`, and
  nothing reads the attribute afterwards. That record now names one of a pair, and is true of both.
  The handle's *close* is not equivalent and is killed (`_close` leaving it open, above); only
  dropping the reference after it is closed is unobservable.

## Follow-up, not this phase

- **Two `complete` artifacts name `Instance.inspect()` as the connection a control read runs on,
  and after the Critical it is a second read-only handle of the same instance.** In
  `components/helpers_and_control.md` §3: "thin wrappers over `Instance.inspect()` and
  `Instance.changes()`", and "runs on the instance's inspection `Db` (`Instance.inspect()`, opened
  on first use)". In `components/fixtures_instances.md` §2.4, the control-dispatch bullet repeats
  the first of those, and adds "the reads it then makes through the `inspect()` handle do not [take
  the lock], which is why the lock is an `RLock`" — the conclusion still holds and the reason has
  changed: the lock is re-entrant because a control tool asks the instance for its changeset and its
  control handle with the lock already held. The same section's `destroy()` bullet lists what is
  closed ("close inspection, session, db") and there is now one more handle in that list. Everything
  else in both sections holds word for word — one statement, positional params, no authorizer beyond
  the connection's permanent write denial, no caps, `run_sql`'s result shape, SQLite's message, the
  lock-free `inspect()` reads (which are now true where they were not). Both artifacts are
  `complete`, so these are a maintainer's amendments to make and not mine; the reasoning is in the
  decision above, and I have edited neither.
- **`components/runtime_db.md` §4 describes a sandbox with no allowed pragmas**, and it now allows
  two: `data_version`, for the FTS5 recipe §4 of `helpers_and_control.md` documents, and
  `table_xinfo` under the guard above, without which a write door voids the changeset. §5's test
  plan says "`ATTACH`/`PRAGMA` refused", which is still what an agent sees for every pragma it can
  actually write — `data_version` is the exception and is itself in the attack suite. The same
  section says an `Authorizer` "carries per-statement mutable state (`refusals`)"; it now carries a
  second piece, `_wrote_a_row`, which is private and exists for the same reason `refusals` is
  per-statement. The artifact is `complete`, so the wording is a maintainer's to amend; the
  reasoning is in the decision above and I have not edited it.
- `components/helpers_and_control.md` §4's FTS5 recipe is incomplete in the artifact (the `match`
  function and the `PRAGMA data_version` read): recorded as **B7** in `BACKLOG.md`, code and tests
  closed here.
- §3's last bullet — the control tools existing over OpenEnv only with
  `seahaven serve --include-control-tools` — is Phase 6's, where `serve` and `step` are written. In
  process, `Instance.call` reaches them by name as the spec requires.
- The ProjectTracker `search_issues` pattern §4 names, and the error handler that lets a
  `run_sql` `DbError` through, are Phase 10's; the framework side both rest on is here.
