# Implications for Seahaven's `state()`

**This file is inference, not findings.** Every factual claim traces to
[sqlite-session-and-sqldiff.md](./sqlite-session-and-sqldiff.md) or
[row-change-formats.md](./row-change-formats.md) or
[versioned-data-contracts.md](./versioned-data-contracts.md); the recommendations are mine. I read
`src/seahaven/changes.py` in this repo to ground them.

## What the current changeset rendering gets right

- Dropping `apsw.no_change` rather than flattening it to `NULL` is correct and matches the format
  spec exactly: an UPDATE's `old.*` and `new.*` records mark unmodified non-PK columns "undefined",
  which is a third state distinct from NULL.
- Refusing to attach a table with no explicit PRIMARY KEY is correct: SQLite documents that it will
  attach such a table and then record nothing, silently.
- Treating the changeset as a net diff rather than a call log is the property the SQLite docs
  themselves highlight, and it is what makes two runs that reached the same state agree.

## Five things the format's semantics force on a judge

1. **A judge cannot read an unchanged column from the diff.** Not before, not after. "The assignee of
   every row whose status changed" requires live state as well as the diff. Whatever `state()`
   returns should make current rows reachable beside the changes, or accept that a whole class of
   judge cannot be written against it.
2. **A primary-key rewrite is a DELETE + INSERT, not an UPDATE.** Verbatim: "It is not possible for an
   UPDATE change to represent a change that modifies the values of primary key columns." Any judge
   counting updates, or diffing per key, must handle the pair. Worth documenting loudly next to the
   `op` field.
3. **Order within a table is undefined** ("The order in which the changes related to a single table
   are stored is undefined"). The current `render()` docstring claims rowid order; the implementation
   emits hash-bucket order over the PK. If determinism is a promise `state()` makes, sort in
   `render()` — by `(table, key)` — rather than inheriting SQLite's.
4. **Rows with a NULL in any PK column vanish entirely.** Silently. If worlds can have nullable PK
   parts, that is a correctness hole in grading, not a cosmetic one.
5. **Trigger- and FK-driven writes are indistinguishable from agent writes** unless the indirect flag
   is surfaced. SQLite already computes it (`sqlite3changeset_op(..., pbIndirect)`); it is the only
   built-in handle on the "audit rows and timestamps the agent didn't mean to touch" problem. Cheap
   to add to `Change`, impossible to reconstruct later.

## Shape recommendations, with the precedent each rests on

- **Keep an explicit `op` verb.** Debezium (`c`/`u`/`d`/`r`) and SQLite both name the operation; Dolt's
  JSON makes you infer it from an empty `from_row`, and its own test suite has to count string
  occurrences as a result. Explicit wins.
- **Keep `key` separate from `before`/`after`.** SQLite's UPDATE puts PK values in both records; Dolt's
  `from_*`/`to_*` puts them in both halves. Hoisting the key out once is strictly more queryable and
  is what `changes.py` already does.
- **Consider adding `changed` (the list of column names that changed).** It is derivable from
  `after.keys()` for an update, but a judge written by an LLM will get `set(after) - {pk}` wrong more
  often than it will get `"status" in change["changed"]` wrong. Dolt's `--skinny` exists for the same
  reason.
- **Sort, and say so.** Dolt's keyless-table tests and SQLite's spec both disclaim ordering. If the
  contract says sorted by `(table, key)`, golden-file comparison becomes viable for evals.
- **Add a provenance block, à la Debezium's `source`.** `fixture`, `episode_id`, `step_count`, `now`,
  world name/version — Seahaven's `state` already carries most of this. The lesson from CDC is that
  the provenance block is the part you cannot add retroactively to archived documents.
- **Aggregate counters do not belong in the diff.** They belong beside it (that is the trajectory
  subtopic's territory), but note the precedent: every format here keeps the change list pure and
  puts summaries elsewhere (`dolt diff --stat`/`--summary`, `sqldiff --summary`).

## Versioning recommendation

The convergent practice across CloudEvents, JSON Schema, OTel, Avro and event-sourcing upcasters is
narrow enough to state as a rule:

1. **A required root-level version field, written first, on every payload.** Name it plainly. Follow
   CloudEvents' example and put only the version components you are willing to break on into the
   string — if additive changes are invisible, say so in the doc so the number stops churning.
2. **A stable identifier distinct from the version** (JSON Schema's `$id` / OTel's Schema Family).
   For a single-format project the format's name is enough, but keep it separate from the number.
   JSON Schema explicitly blesses non-resolvable identifier URIs: "those identifiers are not
   necessarily network-addressable. They are just identifiers."
3. **A written compatibility contract**, in AIP-180's three flavours: which additions are allowed
   without a bump, that no field is ever removed or renamed within a version, and — the one people
   forget — that the *construction algorithm* of an existing field never changes within a version.
4. **A reader-side formatter chain when (3) is not enough**: one small pure function per version step,
   `v1 → v2 → v3`, applied in order, so the judge-side code only ever sees the newest shape. This is
   the upcaster pattern (Marten), the OTel schema-file pattern, and the mirror image of Stripe's
   response downgraders. Put the chain on the reader because saved episodes are immutable and
   numerous while readers are few and current.
5. **Freeze published versions.** OTel: "schema files are immutable once they are published." A
   version number that can mean two things is worse than no version number.

One extra idea worth considering because it is cheap: Avro's **Parsing Canonical Form + fingerprint**.
If `state()` can emit a hash of the world's schema (table and column names in canonical order), a
judge written three years ago can detect "this world's schema is not the one I was written against"
without anyone having remembered to bump a number.
