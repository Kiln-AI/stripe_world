# Agent-Diff's state-diff DSL, read in full

Repo: <https://github.com/agent-diff-bench/agent-diff> (cloned at HEAD, 2026-09).
Paper: "Agent-Diff: Benchmarking LLM Agents on Enterprise API Tasks via Code Execution
with State-Diff-Based Evaluation", arXiv 2602.11224 (Feb 2026) — the PDF was blocked by
this session's egress proxy, so everything below is read from the repository, not the
paper. Where I cite the paper (task counts, framing) it is via a web-search summary and
is labelled second-hand.

This is the single closest published prior art to a Seahaven `state()` + judge design:
a row-level diff in `{inserts, updates, deletes}` form, and a versioned JSON assertion
document evaluated against it. The whole engine is ~2.3k lines
(`backend/src/platform/evaluationEngine/`).

## 1. The diff format

`models.py` is three lines long:

```python
class DiffResult(BaseModel):
    inserts: List[dict[str, Any]]
    updates: List[dict[str, Any]]
    deletes: List[dict[str, Any]]
```

- **inserts / deletes**: the whole row as a dict, with an added `"__table__": <table>`
  key. (Table identity is carried *in the row*, not as an outer grouping.)
- **updates**: `{"__table__": t, "before": {...full row...}, "after": {...full row...}}`
  — **whole rows on both sides, not just the changed columns**. Which columns actually
  changed is recomputed at assertion time by `_changed_keys(before, after, ignores)`.

Rows are sanitised on the way out: `memoryview`/`bytes` become the literal string
`"<binary_data>"` — "Replace binary data with placeholder (not useful for evaluation)".

Values are normalised at comparison time, not at capture time:
`_normalize_for_comparison` recursively converts `datetime`/`date` to ISO strings
(through dicts, lists, tuples and sets).

## 2. Two ways to produce the diff

**(a) Snapshot tables + SQL.** `Differ.create_snapshot(suffix)` does
`CREATE TABLE IF NOT EXISTS <schema>.<t>_snapshot_<suffix> AS SELECT * FROM <schema>.<t>`
for every non-snapshot table. `get_updates` then joins the two snapshots on the table's
primary key and selects rows where any compared column differs:

```python
pk_cols = self._get_pk_columns(t)              # from the SQLAlchemy inspector
compare_cols = [c for c in cols if c not in exclude_cols]
join_conditions = " AND ".join(f"a.{q(pk)} = b.{q(pk)}" for pk in pk_cols)
cmp_expr = " OR ".join(f"a.{q(c)} IS DISTINCT FROM b.{q(c)}" for c in compare_cols)
# SELECT a.* AS after_*, b.* AS before_* FROM <after> a JOIN <before> b ON ... WHERE ...
```

Two details worth copying: rows are matched **by declared primary key** (looked up from
the schema, multi-column supported), and `IS DISTINCT FROM` is used so NULL-to-NULL
does not count as a change. `exclude_cols` (the ignore list) is applied to the
*decision of whether the row changed*, while the projected before/after still carry all
columns.

**(b) A change journal (CDC).** `compute_diff_from_journal` reads a `ChangeJournal`
table filtered by `(environment_id, run_id)`, ordered by `recorded_at, lsn`
(`lsn` = Postgres log sequence number; `replication.py` sets up logical replication),
and folds the entries into the same `DiffResult`:

```python
for entry in entries:
    if entry.operation == "insert":  row = dict(entry.after or {});  row["__table__"] = table; inserts.append(row)
    elif entry.operation == "delete": row = dict(entry.before or {}); row["__table__"] = table; deletes.append(row)
    elif entry.operation == "update": updates.append({"__table__": table, "before": entry.before or {}, "after": entry.after or {}})
```

Note the journal rows are `{table_name, operation, before, after, lsn, recorded_at}` —
a Debezium-shaped envelope — and that the fold **discards ordering**: an ordered log is
collapsed into three unordered buckets before any judge sees it. (It also does not
coalesce: an insert followed by an update of the same row will appear as both an insert
and an update, whereas the snapshot path would report only an insert. I did not find a
reconciliation between the two paths, and the repo does not document which is canonical
for the published benchmark.)

## 3. The judge document

Validated by `dsl_schema.json` ("Diff Universe DSL v0.1") through `jsonschema`, then
normalised by `DSLCompiler` (bare values become `{"eq": value}`; a bare
`expected_changes` value becomes `{"to": {"eq": value}}`).

```json
{
  "version": "0.1",
  "scenario": "at_risk_project_updates",
  "task": "Add 'At Risk' comments to at-risk projects",
  "strict": true,
  "ignore_fields": { "global": ["createdAt", "updatedAt", "id"] },
  "assertions": [
    { "diff_type": "added", "entity": "comments",
      "where": { "projectId": {"eq": 1}, "body": {"contains": "At Risk"} },
      "expected_count": 3,
      "description": "Alpha project gets 3 comments" },
    { "diff_type": "added", "entity": "comments",
      "where": { "projectId": {"eq": 2} },
      "expected_count": 0,
      "description": "Beta project unchanged" }
  ]
}
```

Fields:

| field | meaning |
|---|---|
| `version` | enum, currently only `"0.1"` |
| `scenario`, `task` | human-readable labels |
| `ignore_fields` | `{"global": [...], "<entity>": [...]}` — fields excluded from change detection |
| `strict` | default `true`; a `changed` assertion fails if any field outside `expected_changes` (and outside `ignore_fields`) changed |
| `assertions` | list, `minItems: 1` |
| `aggregates` | `{fn: sum|avg|min|max|count, field, op: eq|ne|gt|gte|lt|lte|between, value}` (defined in the schema; I did not find it consumed by `AssertionEngine.evaluate`) |

Per assertion:

| field | meaning |
|---|---|
| `diff_type` | `added` \| `removed` \| `changed` (the engine README also mentions `unchanged`; the JSON schema enum does **not** include it, and `evaluate()` raises "unknown diff_type" for it — a doc/schema mismatch) |
| `entity` | the table name, matched against each row's `__table__` |
| `where` | `{field: predicate}`, ANDed; bare scalars mean `eq`; **dot paths are supported for nested objects** (`start.timeZone`) via `_get()` |
| `expected_count` | an integer, or `{"min": n}` / `{"max": n}` / both. Omitted ⇒ "at least 1" for added/removed/changed |
| `expected_changes` | `changed` only: `{field: {"from": pred, "to": pred}}` |
| `ignore` | extra fields ignored for this assertion only, additive with global/entity |
| `description` | free text, surfaced in failure messages |

## 4. Predicate operator set

From `_matches_predicate` (multiple ops in one predicate object are ANDed):

`eq`, `ne`/`not_eq`, `in`, `not_in`, `contains`, `not_contains`, `i_contains`,
`starts_with`, `ends_with`, `i_starts_with`, `i_ends_with`, `regex`, `gt`, `gte`, `lt`,
`lte`, `exists` (boolean: field is/isn't NULL), `has_any`, `has_all` (array overlap /
containment).

That is a deliberately small, closed, JSON-encodable set — no expression language, no
eval. It covers "row exists with these properties", "this field moved from X to Y",
"exactly N rows added", and nothing else. Notably absent: cross-row joins, arithmetic,
"sum of amounts", references to the pre-state of a *different* row. The `aggregates`
block in the schema looks like an attempt at the last of these that did not ship.

## 5. Matching semantics per `diff_type`

- **added**: filter `inserts` to `__table__ == entity`, keep rows matching `where`,
  check the count.
- **removed**: same over `deletes`.
- **changed**: filter `updates` to the entity; a row is a candidate if `where` matches
  **either `after` or `before`** (so you can key on a field the task changed). Then:
  ```python
  changed = _changed_keys(before, after, ignore)      # {k : before[k] != after[k]} minus ignores
  if self.strict and not changed.issubset(expected_keys):
      strict_mismatches.append((r, [f"changed fields {sorted(changed)} not subset of expected {sorted(expected_keys)}"]))
      continue
  mismatch_reasons = _change_expectation_failures(before, after, changed, expected_changes)
  ```
  i.e. **strict mode is the per-row side-effect guard**: the agent may not touch any
  column you did not name (or explicitly ignore).

`expected_count: 0` on an `added` assertion is the table-level side-effect guard
("Beta project unchanged"), and it is used that way in the engine README's own example.

## 6. Scoring

```python
total = len(assertions_list)
failed_count = len(failed_indexes)
passed_count = max(total - failed_count, 0)
return {"passed": failed_count == 0,
        "failures": failures,          # human-readable strings, with row identities and mismatch reasons
        "score": {"passed": passed_count, "total": total,
                  "percent": float(passed_count)/total*100.0 if total else 100.0}}
```

Binary `passed` for the leaderboard, plus **assertion-level partial credit** for free.
A large amount of the 682-line `assertion.py` is failure-message formatting
(`_format_row_identity`, `_format_where_mismatch_rows`, `_best_where_details_for_update`,
`_truncate_text(value, max_len=120)`) — about half the file exists to explain *why* an
assertion failed, which is the right ratio for a judge people have to debug.

## 7. `ignore_fields` in practice

Every shipped suite sets a global ignore list, and the contents are the empirical answer
to "what irrelevant changes does a real app backend produce?":

| suite | `ignore_fields.global` | tests |
|---|---|---|
| Linear | `created_at, updated_at, updatedAt, createdAt, editedAt` | 65 |
| Slack (v1 / v2) | `created_at, updated_at` | 37 / 65 |
| GitHub | `created_at, updated_at, closed_at, merged_at` | 10 |
| Google Calendar | `created_at, updated_at, etag, html_link, ical_uid, sequence, start_datetime, end_datetime` | 65 |
| Box | `created_at, modified_at, content_created_at, content_modified_at, purged_at, trashed_at, etag, sequence_id, sha1, file_version, path_collection, created_by, modified_by, owned_by` | 65 |

Three classes show up: (a) timestamps, (b) opaque version/concurrency tokens
(`etag`, `sequence_id`, `sequence`, `file_version`, `sha1`), (c) derived/denormalised
fields (`path_collection`, `html_link`, `created_by`/`modified_by`/`owned_by` embedded
objects). Note also that both camelCase and snake_case spellings are listed for Linear —
the ignore list is matched on literal key names against whatever the row dict contains.

## 8. A real task

From `examples/linear/testsuites/linear_bench.json`:

```json
{
  "id": "test_2",
  "name": "Update issue status to In Progress",
  "prompt": "Move issue ENG-1 to 'In Progress' status",
  "type": "actionEval",
  "seed_template": "linear_expanded",
  "impersonate_user_id": "2790a7ee-fde0-4537-9588-e233aa5a68d1",
  "assertions": [
    { "diff_type": "changed", "entity": "issues",
      "where": { "identifier": {"eq": "ENG-1"} },
      "expected_changes": { "stateId": {"to": {"eq": "6963a682-5967-477a-9afc-0b8a5b70b070"}}} }
  ],
  "metadata": {
    "min_tool_calls": 2, "tools_required": ["teams", "issueCreate"],
    "task_horizon": 2, "operation_type": "search+C", "entity_scope": "single",
    "information_availability": "implicit", "prompt_ambiguity": "low"
  }
}
```

Two things to note beyond the assertion: the task names a **seed template** (the fixture
the instance starts from) and carries a `metadata` block of difficulty axes used for
slicing results. The seed + assertions + prompt is the whole task; there is no per-task
code.

## 9. The dataset ships the judge as the label

`datasets/agent-diff-bench/{train,test,all_numbered}.jsonl` — 179 / 45 / 224 lines:

```json
{"question": "In the history readings under digital humanities, there is a markdown file whose filename misspells the word 'computational' (letters swapped). Find it ... and fix the typo in the filename without changing the content.",
 "answer": "{\"assertions\":[{\"diff_type\":\"changed\",\"entity\":\"box_files\",\"where\":{\"id\":{\"eq\":\"3266469077\"}},\"expected_changes\":{\"name\":{\"to\":{\"eq\":\"computational approaches to hist research.md\"}}}}],\"ignore_fields\":{\"global\":[\"created_at\",\"modified_at\", ...]}}",
 "test_id": "box_145", "test_name": "Level 3: Typo Fix (Co..."}
```

The `answer` field *is* the assertion spec. That is a strong statement about the
contract: if the judge is data, it can be a dataset label, shipped on HuggingFace,
versioned, and — in principle — generated or verified by a model. 224 tasks across four
services (Box, Linear, Slack, Google Calendar) per the paper, which matches the JSONL
line count.

## 10. What I'd take from it, and what I'd change

Take:
- `{inserts, updates, deletes}` with `__table__` on the row and whole `before`/`after`
  on updates — it makes the judge's job trivial and the format self-describing.
- `ignore_fields` at three scopes (global / entity / assertion), applied to *change
  detection* rather than to the stored diff, so the diff keeps everything and the judge
  decides what matters.
- `strict` defaulting to true: unnamed field changes fail. Side-effect checking as a
  default rather than a line the author must remember to write (ToolSandbox's guardrails
  do the same thing at database granularity).
- `expected_count: 0` as the idiom for "this table must not have changed".
- Assertion-level partial credit alongside a binary pass — free, and it makes RL reward
  shaping and failure triage possible without a second format.
- A `version` field in the judge document, validated by a JSON Schema that ships beside
  the engine.

Change / watch out for:
- The operator set cannot express aggregations or cross-row relationships. The
  `aggregates` block is in the schema but not wired into `evaluate()`. If judges need
  "total refunded equals X across N rows", this DSL runs out.
- `diff_type: "unchanged"` is documented in the engine README but is neither in the JSON
  Schema enum nor handled by `evaluate()`. Documentation drift in a 200-line spec is a
  warning about how easily this shape rots.
- The two diff producers (snapshot-join vs change journal) do not obviously agree on
  insert-then-update coalescing. If you offer more than one way to compute the diff,
  pin down the semantics (net diff vs log) in the spec, because judges written against
  one will silently misbehave on the other. Seahaven's SQLite changeset is a *net* diff,
  which corresponds to the snapshot-join path.
- Nothing in the format records *which* diff producer, schema version, or fixture
  version the state came from. For "judge an episode months later", that provenance
  belongs in the saved state, not just in the run config.
