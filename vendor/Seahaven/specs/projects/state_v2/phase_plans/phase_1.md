---
status: complete
---

# Phase 1: The change log and the call log, per node

## Overview

The data every later phase formats, serves and documents lands first and alone: one
`LogRecord` per row per call across every node, one `CallRecord` per dispatched call, and the
ordinal that joins them. Recording is one SQLite session per node per call, opened in
`_dispatch` and in `_bulk`, read after the transactions settle, rendered at call time and kept
in memory. Nothing is removed: `changes()`, `Change`, `render()` and the long-lived per-node
sessions stay until phase 4, so the fold test can cross-check against them on a one-node world
while its real oracle is a per-node session of its own. `seed` narrows to `int | None`.

## Steps

1. `src/seahaven/changes.py`
   - Add `LogRecord(i, world, table, op, key, before, after)`, frozen, with `to_dict()` in FS §3.2
     field order and `key`/`before`/`after` copied on the way out.
   - Add `CallRecord(tool, arguments, error)`, frozen, `to_dict()` in FS §4.2 order with
     `arguments` deep-copied.
   - Factor `start_session(conn, world)` into `tracked_tables(conn, world) -> tuple[str, ...]`
     (the loop and the no-primary-key refusal) and `open_session(conn, tracked) -> apsw.Session`
     (a `Session` plus one `attach` per table); `start_session` stays as the composition of the
     two for the long-lived session phase 4 removes.
   - Add `render_log(changeset, conn, columns, *, i, world) -> list[LogRecord]`: whole row on
     insert (`after`) and delete (`before`); on update only the changed non-key columns on both
     sides. `columns` is the caller's per-table cache.
   - Add `sort_key(record)` returning `(world, table, tuple(_sqlite_rank(v) for v in key.values()))`
     over the rendered values; `_sqlite_rank`: `None -> (0, None)`, number `-> (1, n)`, anything
     else `-> (2, value)`.
   - `_jsonable`: an infinite float becomes `None`, with the one-line comment FS §3.4 asks for.
   - Module docstring rewritten for the two readings; `__all__` extended.

2. `src/seahaven/instances.py`
   - `NodeRuntime` gains `tracked: tuple[str, ...] = ()` and
     `columns: dict[str, tuple[list[str], list[int]]]` (default factory); `session` stays.
   - `Instance.__init__` gains `self._records: list[LogRecord]`, `self._calls: list[CallRecord]`,
     `self._call_count = 0`.
   - `call_count` property; `change_log() -> list[LogRecord]` and `call_log() -> list[CallRecord]`
     under `_held()`, each a fresh list over the instance's own records.
   - `_next_ordinal()`: increments under the lock, returns the ordinal.
   - `_recording(i)`: opens `open_session(runtime.db.conn, runtime.tracked)` on every node; in
     `finally`, reads each changeset, renders with `render_log(..., i=i, world=runtime.node.path)`
     using `runtime.columns`, sorts the call's records with `sort_key`, extends `_records`;
     sessions closed whatever happens.
   - `_logging_call(name, arguments)`: `copy.deepcopy(dict(arguments))` on entry; appends
     `CallRecord(name, copy, error)` in `finally`, `error = str(exc)` or `None`.
   - `_dispatch`: after the control branch, `i = self._next_ordinal()`, then
     `with self._recording(i), self._logging_call(target.name, arguments), in_call(): ...`.
   - `_bulk`: `with self._held() as frame, self._recording(None), ExitStack() as stack:` so the
     transactions are inner and the recording reads settled changesets.
   - `InstanceManager.create`: `seed: int | None`; after the hooks, per node
     `runtime.tracked = tracked_tables(conn, node.world)` then
     `runtime.session = open_session(conn, runtime.tracked)`.
   - Module docstring and the lock rule mention the log.

3. `src/seahaven/ids.py`: `instance_seed(source, caller_seed: int | None)`; `_seed_bytes` drops
   the `bytes` arm; message becomes "a seed must be an int or None, not ...".

4. `src/seahaven/world.py`: `World.instance(..., seed: int | None = None, ...)`.

5. `src/seahaven/pytest_plugin.py`: no code change needed (the marker passes `seed=` through);
   confirmed by the new pytester test.

6. `src/seahaven/__init__.py`: export `LogRecord` and `CallRecord` beside `Change`.

7. Test support, flat under `tests/`:
   - `tests/fold_support.py`: FS §3.6's fold as code over `LogRecord`, producing `NetChange`
     records keyed by `(world, table, key)`, sorted per FS §3.3 with its own rank function. Not
     importable from `seahaven`.
   - `tests/fold_oracle.py`: `recording(instance)` opens `open_session` on every node's
     connection over `runtime.tracked` (reaching `instance._runtime`), for the whole episode;
     `net_diff()` renders each with the node's path through `render_log` and sorts with the
     fold's own order. `from_changes(instance.changes())` maps `Change` to `NetChange` (dropping
     key columns from an update's `before`) as the one-node second cross-check phase 4 removes.

## Decisions taken during implementation

**A call made inside `bulk()` records under the block, not under the call** (found in review;
FS §3.2 and ARCH §3.2 now say so). `inst.call(...)` inside a `bulk()` block is a supported
pattern, and both `_bulk` and `_dispatch` opened a recording, so one write produced two records
and the inner recording -- reading its changeset while the block's transactions were still open --
logged rows a rolled-back block then discarded. Three options were open: make the recording
re-entrant so the outermost owns the rows; refuse `inst.call(...)` inside `bulk()`; or have the
outer recording drop rows an inner one claimed. The first is the one taken. It is the only one
that keeps both of FS §3.2's guarantees -- one record per row per call, and nothing in the log
that rolled back -- because the only recording that reads a changeset is the one outside the
transactions. The second breaks shipped documentation; the third leaves the phantom, since what
an inner recording claims may still roll back. The cost is that such a call's rows carry
`i: null` rather than its own ordinal, which is what those rows are: they commit with the block.

## Tests

- `tests/test_changes.py` (additions, the `Change` cases untouched):
  - `test_an_insert_logs_the_whole_row`, `test_a_delete_logs_the_whole_row`
  - `test_an_update_logs_only_the_columns_it_changed` (key absent from both sides)
  - `test_an_update_of_several_columns_logs_them_all`, `test_a_column_set_to_null_is_a_change`
  - `test_a_composite_key_is_logged_in_key_order`, `test_an_integer_primary_key_is_logged_as_a_key`
  - `test_a_blob_is_logged_as_base64`, `test_an_infinite_float_is_logged_as_null`
  - `test_a_log_record_renders_to_a_dict` (field order) and
    `test_to_dict_copies_the_records_dicts`
  - `test_records_of_one_call_are_sorted_by_table_then_key`
  - `test_a_mixed_type_key_sorts_by_type_and_then_by_its_published_value` (ANY column)
  - `test_a_blob_key_sorts_by_its_base64_and_not_by_its_bytes`
  - `test_a_call_record_renders_to_a_dict` (field order, arguments copied)
  - `test_tracked_tables_lists_what_a_session_attaches`, `test_open_session_attaches_only_those`
- `tests/test_change_log.py` (new): empty before any call; read-only call, no-op call,
  rolled-back call, rolled-back `bulk()`, startup rows all log nothing; insert+update in one
  `execute` call is one insert; insert+delete in one call is nothing; update back in one call is
  nothing and across two calls is two records; same row in two calls is two records; records
  ordered by call; `bulk()` writes carry `i: None` between calls; `ToolError` consumes an
  ordinal; a refused name, a function `call()` cannot resolve, a control tool and `tools()` do
  not; two identical episodes give byte-identical `json.dumps`; `change_log()` does no database
  work (statement counter on every node, positive control); `change_log()` after `destroy()`
  raises. On `emporium`: one `settle_order` call writes three nodes under one `i` (nested calls
  carry the outer ordinal); `pay_`/`eu_` charges are told apart by `world`; a `bulk()` reaching
  two nodes carries `i: None` on both; a nested call that rolls back leaves nothing on either
  node; within a call `child` sorts before `main`.
- `tests/test_call_log.py` (new): one entry per dispatched call with the surface name, prefixed
  on `emporium`; arguments as carried (typed call binds positionals by name); `error` `None`
  on success, the message on a `ToolError` and on a plain exception; no entry for a refused
  name, a control tool or `tools()`; a nested call adds no entry; `len(call_log()) ==
  call_count`; a tool that mutates its arguments does not alter the record; `call_log()` after
  `destroy()` raises.
- `tests/test_fold.py` (new): `fold(log) == oracle.net_diff()` parametrised over every episode
  shape (insert, insert+update, insert+delete, update+update, update back, update+delete,
  delete+insert other, delete+insert same, primary-key rewrite, calls around bulk, everything)
  on a one-node world, where `from_changes(changes())` is the second cross-check; the same fold
  against the oracle on `emporium` episodes; the oracle saw rows (non-vacuity); the fold drops
  what cancelled out; the log and the fold sort a blob key the same way.
- `tests/test_ids.py`: `bytes` seed refused with "an int or None"; the `bytes` arms of the
  existing cases removed; `tests/test_instances.py` parametrisation follows.
- `tests/test_pytest_plugin.py`: `test_a_bytes_seed_in_the_marker_is_refused`.
