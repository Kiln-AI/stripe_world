---
status: complete
---

# State format: ideas for discussion

This is the "start wide" step: what `state()` should return, argued from the research in
[research/state-formats-and-judges/summary.md](research/state-formats-and-judges/summary.md) and
from the code as it stands. It is not the functional spec. Each section ends with the decision it
needs from you; the sketch in §9 is one way all the recommendations fit together.

Two things the research settled that reframe the brief:

1. **Almost nobody persists environment state, and everyone who does not regrets it.** Every RL
   trainer surveyed (TRL, SkyRL, Atropos, ART, veRL) reads reward off a live object; OpenEnv persists
   a `final_state` with no version and no cap, and TRL throws it away; τ²-bench, WorkBench and BFCL
   reconstruct state by replaying the transcript and had to ship `strict=False` because recorded
   outputs drift against current tool code. The three that do persist state for offline grading
   (AppWorld's end-state databases, Inspect's `store`, verifiers v1's artifact tarball) are the ones
   whose judges can be re-run later. So a durable, versioned `state()` is not a nicety; it is the
   feature.
2. **Row-level start/end diff is the only judge substrate that scales to hundreds of cheap judges.**
   Whole-state hashing (τ-bench) says nothing about *what* differed; scoped reads (WebArena,
   OSWorld) cannot see collateral damage. The nearest published prior art to `inst.changes()` is
   Agent-Diff (Feb 2026): the same `{table, op, before, after}` diff, judged by a versioned JSON
   assertion document, with the assertion document shipped as the dataset label. We are on the
   right track, and there is a worked example to steal from and improve on.

## Decisions so far

A running log of what has been settled in discussion, so the functional spec can be written from
it. Sections below are left as argued; where a decision overrides a section, the log wins.

**Batch 1 (2026-09-15), §1 versioning:**

- The version lives in the document. Format string style is `seahaven.state/1`.
- The wire returns the whole state document as the OpenEnv state object. An earlier session
  established that a Seahaven world can return a custom JSON state object over the WebSocket state
  message (the HTTP `GET /state` route strips subclass fields; that is `BACKLOG.md` B13 and not our
  path). The plan carries a step that confirms this end to end, since nothing here works without it.
- The format is chosen at `reset(state_format=...)`, falling back to `World(state_format=...)`.
  `reset` wins. `state_format` joins `fixture`, `seed` and `now` as a reserved reset keyword that a
  startup hook may not name.
- `World(state_format=...)` is required: there is no runtime default, because a default that tracks
  the newest format would change what every eval saves on a Seahaven upgrade, and a default that
  never moves is a constant a future maintainer will be tempted to bump. A world that gives none
  fails at construction with a message naming the current format. The scaffold (`seahaven new`)
  writes the current format into the new world, so a pin is chosen once, at creation, the way the
  fixture sidecar's `format_version` is. Changing a world's pin is a change to the world: the docs
  say it should come with a `World.version` bump. Not enforceable, so recommended rather than
  checked.

**Batch 2 (2026-09-15), §2 provenance:**

- The provenance block is in: `world` (name, version), `fixture` (id, `file_sha256`), `seed`,
  `now`, `episode_id`, `seahaven_version`.
- No schema fingerprint. It would be a partial world check, covering the schema and not the tools
  or the code, so a judge would still have to trust `World.version` for everything else. If the
  version is trusted, the fingerprint adds nothing; if it is not, the fingerprint does not rescue
  it. `World.version` is the world's identity, and the docs say a schema, tool or state-format
  change bumps it. The fixture's `file_sha256` stays because it already exists and catches the one
  drift the version does not cover, a regenerated fixture under the same id.
- No `schema` block, in v1 or as a later format option. A judge author can get the world's schema
  by other means (the world package, `describe_schema`, the fixture); a state document is produced
  once per episode and is not the place to repeat it.

**Batch 3 (2026-09-15), §3 the diff, first pass:**

- The diff carries no ignore list; what is noise is the judge's decision.
- Flat versus grouped is open, and the answer has to account for composition: the grouping
  question applies at the database level as well as inside it. See §3 briefing, part 5.
- Whole rows, `changed`, `indirect`, ordering and the net-versus-log question are under
  discussion; the briefing in Appendix A is the material for it.

**Batch 4 (2026-09-15), §3 the diff, second pass** (confirmed in batch 5 except where noted):

- **No whole database.** Zero upside: the starting state is a lookup by world name, world version,
  fixture id and fixture hash, and a judge that has it can rebuild anything from it plus the log.
  Storing the fixture once beats storing it per run (one small fixture is already 1.9 MB).
- **No whole rows either, by the same logic.** Whole rows for touched rows are a partial copy of
  the starting state; a judge that needs any untouched column needs the fixture, and once it has
  the fixture it needs nothing from the row. Records carry the key and the changed columns only:
  `key` separate; on an update `before` and `after` hold exactly the changed non-key columns (which
  makes a separate `changed` list redundant); an insert's `after` and a delete's `before` are whole
  rows, as SQLite already gives them.
- **The ordered per-call change log is the authoritative data.** It strictly dominates the net
  diff (the diff is a fold of the log; the log cannot be recovered from the diff), it answers
  intermediate-value and ordering questions, and it attributes every row change to the call that
  made it, which the diff cannot. Each entry is tagged with the call ordinal `i` (batch 7 defines it).
- **The document carries the log only; no net diff beside it.** Decided 2026-09-15: one source of
  truth, no invariant between two blocks to keep for ever. The net diff is defined, not shipped:
  the format spec states the fold exactly (insert+update = insert, insert+delete = nothing,
  update+update with columns that returned to their original value dropped, delete+insert = update
  or nothing, a key rewrite = delete+insert; sorted by table and key), which is SQLite's own
  changeset semantics, and `inst.changes()` stays as the live reference implementation. A test in
  Seahaven checks `fold(log) == changes()` on every episode shape the suite exercises, so the fold
  is pinned even before a public helper exists. Two consequences the docs must carry: a judge that
  counts rows straight off the log overcounts rows touched more than once and must fold or
  deduplicate by key first; and comparing two runs for the same end state is a comparison of their
  folds, never of their logs, because equal end states can have different logs.
- **No `indirect`.** Decided 2026-09-15. It is SQLite's flag marking a change made by a trigger or
  a foreign-key action rather than by the tool's own statement. The log records state; why a row
  changed is the trace's business, not the state's. §3 is closed for the database block.
- **Composition**: every log record carries `subworld`, `null` for the root, naming the sub-world
  the table belongs to, so a composed world's document is one flat list that still materialises to
  one table. (Batch 7 later removed the `calls` block, so `subworld` appears on log records only.)
**Batch 5 (2026-09-15), §3 closed for the database block** (the `calls` block is §5's and is not
approved yet; the example in Appendix B is the agreed shape for `db.log`):

- No `indirect` field (see batch 4).
- The log is ordered by call. Within a call, records are sorted by `(subworld, table, key)`, for
  consistency rather than meaning.
- One flat list; every record carries `i`. No per-call grouping.
- No `summary` block. Counts are derivable.
- Every write after startup is in the log. Startup hooks run before the session attaches and are
  out, as today. Writes made with no call in flight (`inst.bulk()`, the authoring path that writes
  straight into an instance under one transaction, used to generate fixtures) are in, with `i`
  `null`.
- Confirmed from batch 4: no whole database, no whole rows, key separate with changed non-key
  columns only on updates, log only with the fold defined in the spec, `subworld` on every record.

**Batch 6 (2026-09-17), §4 closed:**

- Both of §4's P2 formats are gone. Per-call deltas are the v1 log; there is no
  `seahaven.state+sqlite/1` and no format that carries the database file, ever.
- Ordering questions are answered by the log. Untouched rows are answered by lookup: the fixture,
  by world name, version, fixture id and hash; for a blank instance, the world's DDL plus what the
  startup hooks wrote.
- Provenance gains `startup` (confirmed 2026-09-17): the reset keywords beyond `fixture`, `seed`,
  `now` and `state_format`, the ones the startup hooks received (`{"user_id": "u_12"}`). They are the last
  input the starting state depends on, and the principal the episode ran as is judge-relevant on
  its own. `seed` and `now` stay at the root as batch 2 has them and are not repeated under
  `startup`.
- Reproducing a startup hook's output is the world author's concern, not the format's. Nearly all
  startup SQL is DDL, most of the rest seeds enums, and a world whose hooks draw on the clock or
  randomness while also running without fixtures has opted into a situation it can handle itself.

**Batch 7 (2026-09-17), §5 closed: no `calls`, no `counters`:**

- A tool call is the harness's action, not the world's state, and the harness's trace has it
  with more context (the messages around it, the result it saw) than state could carry. Kiln
  stores the trace; OpenEnv's own episode record stores `messages` and a `tool_trace` beside the
  final state. State carries neither a call list nor counters, and `subworld` appears only on log
  records.
- `i` survives as the join key (confirmed 2026-09-17). The spec defines it: every call dispatched to the instance, in
  order from 0, including calls that errored or were refused as unknown, excluding control tools
  and tool listing. Over OpenEnv it is the harness's Nth `CallToolAction`; in-process, the Nth
  `inst.call`. A judge that asks which call changed a row joins `i` against the trace.
- `call_count` at the root (confirmed 2026-09-17): the number of calls so far, so a consumer can
  check its trace and the state agree before joining on `i`. Not OpenEnv's `step_count`, which also
  counts `list_tools` steps.
- The tool-count judge from the project overview's Example 1 runs over the trace; the
  changed-rows judge from Example 2 runs over the log.
- **Two built-in formats, locked 2026-09-17.** OpenEnv's rollout harness calls `client.state()`
  after every tool call and embeds the result in that step's metadata, so polling the full-log
  format per step is quadratic over an episode. A format is fixed per episode at `reset`, so the
  answer is a second built-in format rather than an option:
  - `seahaven.state/1`: the full document, the whole log. For a caller that reads `final_state`
    once at the end, which is Kiln and every other consumer surveyed.
  - `seahaven.state+last_step/1`: the same document with `db.log` filtered to the records of the
    most recent call (`i == call_count - 1`). Idempotent, since it is scoped by the step counter
    and not by when `state()` was last read; a per-step poller's outputs concatenate into the full
    log, a gap shows as a jump in `call_count`, and before any call the log is empty. For a
    per-step poller like the OpenEnv episode harness. Near trivial to build on the v1 formatter;
    its cost is one more frozen format kept for ever.
  - A caller whose needs differ registers a custom formatter. The interface requirement is that a
    formatter receives the instance's whole log and its `call_count`; no per-instance memory is
    needed for either built-in.
  The docs present exactly these three cases, in that order, each with its reason. `state()`
  under either built-in costs serialization only: the log in memory, appended per call, no
  database work at read time.

**Batch 8 (2026-09-17), §6 closed with no new decision:**

- The judge system is the evaluator's, outside Seahaven. The research established that the
  evaluator has several workable options over the agreed log (SQL over a materialised copy, Jinja
  in a Python-only evaluator where judges are trusted, a DSL if scale demands one), which is all
  the format design needed to know. Whether a DSL is needed is the evaluator's TBD.
- Seahaven ships no loader or helper; readers may be in any language.
- The only Seahaven-side facts are already decided in §3: a JSON document, one flat log with the
  shape in Appendix B. The functional spec states the SQLite-to-JSON value mapping as detail
  (integers and reals as numbers, text as strings, NULL as `null`, blobs as base64), noting that
  64-bit integers exceed JavaScript's exact range.

**Batch 9 (2026-09-17), §7 closed: no filtering:**

- `state()` takes no filter options and returns everything the format defines. No cap on the log.
- A consumer that wants less filters after the fact, or registers a custom formatter. Seahaven adds
  no special support for either.

**Batch 10 (2026-09-17), §8 closed: control tools deprecated:**

- `controller_run_sql` and `controller_changes` stay callable for now, unchanged in behaviour and
  still behind `--include-control-tools` over the wire, and are marked deprecated in code.
- They leave the docs entirely. Every doc that grades or inspects an episode uses `state()`:
  `serving.md`, `testing.md`, `concepts.md`, the README's grading example, and the incoming
  `openenv.md`. No alias between `controller_changes` and the formatter; it is legacy, not a second
  surface.

**Batch 11 (2026-09-17), from the architecture review:**

- The document is a framework-owned envelope (`format`, `seahaven_version`, `world`, `fixture`,
  `episode_id`, `seed`, `now`, `startup`, `call_count`) plus one key, `state`, holding the
  formatter's output. Formatters produce `state` only; the envelope is identical under every
  format and typed on the wire. Appendix B's `db.log` therefore sits at `state.db.log`.
- A formatter takes `(world, instance | None)`; before `reset` the world's pinned formatter runs
  with `None`. Custom pins resolve at first instance creation. No new lint rule (STRICT already
  refuses NULL keys). Startup keywords are refused at `reset` if not JSON-able.

- `inst.changes()`, the `Change` class and `controller_changes` are removed (locked 2026-09-17).
  One API, `state()`, and one recording mechanism, a session per call. The fold's oracle moves
  into the tests as a long-lived session the test opens itself. `controller_run_sql` stays,
  deprecated.

- **A judge helper is out of scope for this project.** Recorded as the follow-up: load a document,
  fold the log into the net diff, materialise into SQLite tables, and given the fixture file overlay
  the log to produce full before and after rows or the whole final database. The fold test above is
  the seed of it.

## 1. Versioning: producer-side formatters, pinned per world

Your proposal (`state(format="v1")`, a pluggable `StateFormatter`, a world-level default) matches
the convergent practice across CloudEvents, JSON Schema, OpenTelemetry, Stripe and Avro: a required
version identifier inside every document, a written compatibility contract, and a chain of small
per-version transformations as the escape hatch. Two refinements from the research:

- **Put the version in the document, not only in the call.** A saved `final_state` must say what it
  is without the caller remembering. Recommended: a root `format` string such as
  `"seahaven.state/1"` (family plus major, CloudEvents-style), and a compatibility rule that says
  additive fields never bump the number, removals and renames always do, and the *construction
  algorithm* of an existing field never changes within a number (AIP-180's rule, the one everyone
  forgets: `changes` silently switching from changed-columns to whole rows would be a breaking
  change even though every field name stayed).
- **Producer-side, as you proposed, because only the server has the live instance.** Stripe
  downgrades on the producer; event-sourcing upcasts on the reader. We cannot upcast: a v1 document
  saved last year cannot be re-produced, and a v2 field that needs data v1 never captured is gone.
  So new formats are written by the framework, old formats stay frozen and callable, and the world
  pins its default (`World(state_format="seahaven.state/1")`) so that upgrading Seahaven never
  changes what a running eval saves. A judge should check `format` and refuse a mismatch loudly;
  verifiers and AppWorld both make "grading stale state" an error rather than a score.

**A wire problem to solve now.** OpenEnv's WebSocket state request is `{"type": "state"}` with no
arguments, and `EnvClient.state()` takes none. `format=` cannot ride the state message. Options:

- (a) the format is chosen at `reset(state_format=...)`, defaulting to the world's; the state
  message returns that format. Fits OpenEnv without changes; a format is per episode, which is
  what a framework wants anyway.
- (b) a control tool `controller_state(format=...)`, which is the surface we are trying to retire.
- (c) an upstream change to OpenEnv. Not on our critical path.

Recommendation: (a), with in-process `inst.state(format=...)` free to take any format.

**Decision needed:** the `format` string style, and (a) for the wire.

## 2. Provenance: the block you cannot add later

Debezium's lesson is that the `source` block is the part of a change event nobody can reconstruct
retroactively. Recommended root fields, most of which `SeahavenState` already carries:

`world` (name, version), `fixture` (id, and its `file_sha256` from the sidecar), `seed`, `now`,
`episode_id`, `seahaven_version`, and a **schema fingerprint**: a hash of the world's table and
column names in canonical order (Avro's Parsing Canonical Form idea). A judge written against
`issues.status` can then detect "this world's schema is not the one I was written against" without
anyone bumping a number. Cheap to compute at `build_blank` time; impossible to recover later.

**Decision needed:** include the schema fingerprint, or a full `schema` block (tables and columns,
which also lets a judge tool type-check expressions at authoring time)? The full block is a few KB
per document; the fingerprint is 64 bytes. I lean fingerprint in v1, full schema as a P2 format
option.

## 3. The diff: keep the shape, fill in the rows, add three fields

`Change{table, op, key, before, after}` is within a rename of Agent-Diff and Debezium. Five
changes the research argues for:

1. **Whole rows on both sides of an update.** SQLite's changeset carries only the changed columns
   (plus the key) on both sides, which is what `changes.py` renders today. The consequence: a judge
   cannot read an unchanged column from the diff, so "the assignee of every issue whose status
   changed" is unwritable. Agent-Diff and Dolt both emit whole rows. Inserts and deletes already
   carry the whole row; for updates, `after` is the current row by primary key and `before` is
   that row with the old values overlaid. Cost is document size, which in a mocked world is small.
2. **A `changed` list of column names on updates.** Derivable, but an LLM-written judge gets
   `set(after) - key` wrong far more often than `"status" in changed`, and it is what makes "changed
   `status` and nothing else" a one-liner. Dolt's `--skinny` exists for the same reason.
3. **An `indirect` flag.** SQLite already marks a change made by a trigger or a foreign-key action
   rather than the statement itself. It is the only built-in handle on audit-row noise, it costs a
   boolean, and it cannot be reconstructed after the fact. (To confirm in the architecture step:
   APSW's `TableChange` exposes it.)
4. **Deterministic order, stated.** SQLite documents within-table order as *undefined*; `render()`'s
   docstring claims rowid order and the implementation emits hash-bucket order. Sort by
   `(table, key)` and say so, which also makes golden-file comparison possible.
5. **Document the two silent edges** beside the `op` field: a primary-key rewrite is a delete plus
   an insert, never an update; and a row with NULL in any key column is never recorded (worth a
   lint refusing nullable primary-key columns, which STRICT tables make cheap to check).

**What the diff should not carry: an ignore list.** Agent-Diff applies `ignore_fields` in the
judge at global, entity and assertion scope, and the diff keeps everything. Seahaven's frozen
clock makes timestamp noise unusually predictable (every row the episode wrote has one instant),
but the decision of what is noise is the judge's. One hook worth discussing: the world declaring
`volatile_columns` (`updated_at`, `sequence`) that the state's schema block names, so judge tooling
can default-ignore them without each judge author rediscovering the list.

**Flat list or grouped by table?** Agent-Diff groups by op with `__table__` on the row; Dolt groups
by table. For expression judges, `changes[?table=='issues' && op=='update']` is fine in every
candidate language, and a flat list is what materialises into a SQL table in one step (§6).
Recommendation: one flat, sorted `changes` list, plus a derived `summary` of counts per table and
op so "did anything in `comments` change" is a lookup rather than a scan.

**Decision needed:** whole rows (yes), `changed` and `indirect` (yes), flat plus summary (lean yes).

## 4. What the diff cannot say, and the escape hatch

A net diff has no sequence and cannot show untouched rows. Two judge families depend on those:
"the first write was X" and "issue ENG-7, which the agent should not have touched, still has its
original assignee" (the second is answerable only if the row *was* touched, in which case it is in
the diff; if it was not, absence is the answer, which is enough for most side-effect judges).

For the residue the research's answer is the same as verifiers v1's: ship the final database file
itself as an artifact. A P2 format `seahaven.state+sqlite/1` that carries `state.sqlite` (or a
reference to it) gives a judge full SQL over the end state, and SQLite files are small. Per-call
deltas (a log beside the net diff) are a separate P2: Dolt ships the net diff and the log as two
surfaces and does not try to make one serve both.

**Decision needed:** confirm both are P2 and out of the v1 format, with the format family designed
so they can be added as formats rather than as flags.

## 5. Trajectory: calls, counters, no results

Across eight trajectory formats the only universal fields are the tool name, the arguments and a
join id; nothing standard exists to adopt (OTel's GenAI conventions moved repos, have zero stable
attributes and a `TODO` schema URL). Recommendation, per call:

```jsonc
{"i": 3, "tool": "transition_issue", "args": {"issue_id": "…", "status": "done"},
 "ok": false, "error": {"code": "NOT_FOUND", "message": "…"}}
```

- `i` is an explicit ordinal: a saved artifact should not rely on array order surviving every
  serializer between here and a judge, and it makes "nothing happened after step N" expressible.
- `args` is the validated object, never a string; Seahaven has no malformed-JSON problem to survive.
- `error` reuses `{code, message}` and drops `details`, the one unbounded field. Framework errors
  (`unknown_tool`, invalid arguments, `internal`) should be distinguishable from a world's own
  `ToolError` codes, by a reserved prefix or a `kind` field, so a judge can tell "called a tool that
  does not exist" from "the tool said no".
- **Tool results are omitted.** In Seahaven the diff is the state and a result is a view of it; a
  judge asserting on `get_issue`'s response instead of on the row is the brittleness that makes
  action-matching judges worse than state-matching ones. Read-only tools whose output the agent
  reported to a user are the exception, and an opt-in, capped, head-tail-truncated `result` with an
  in-band `truncated` marker is the P2 answer (Inspect's 16 KiB and Letta's 2,500 chars are the two
  published budgets).
- No runtime, as you said, and no absolute timestamps: `now` is the world clock and a second clock
  in the same document is a trap.
- Control-tool and `list_tools` steps are not calls. `step_count` counts OpenEnv steps, including
  refused ones; `calls` counts `CallToolAction`s, including errored ones. Both numbers stated.

Counters, computed over the whole episode and exact even if the list is ever bounded:

```jsonc
"counters": {"steps": 14, "calls": 12, "errors": 2,
             "by_tool": {"transition_issue": 3, "search_issues": 9},
             "errors_by_tool": {"search_issues": 2}}
```

No surveyed harness keeps per-tool counts as a field (they scan the log), but `by_tool` is the one
aggregate a judge can use without iterating, and it is the shape your Example 1 needs.

**Decision needed:** results omitted in v1; the error-kind distinction; whether `by_tool` splits
reads from writes (MCP's `readOnlyHint` vocabulary) if worlds ever annotate tools that way.

## 6. The judge system: a closed DSL for the 80%, SQL for the rest, code for the 5%

The research answers "JSON and Jinja? Does that scale to DB diffs?" fairly bluntly: JSON yes,
Jinja no. Jinja's sandbox was escaped twice in fifteen months, its core has no regex test, it is
Python-only, and the same battery of five diff judges was expressible but ugly. The discriminating
capability is the cross-list join ("every issue that went open→done has a matching `issue_events`
insert"): JSONPath, base JMESPath and JSONLogic cannot express it at all, and JSONLogic returns
`false` for a true proposition without complaint. Only SQL can enumerate *which* columns changed
without naming them, which is what makes global noise suppression and "changed X and nothing else"
possible. Performance spans three orders of magnitude: 1.28 s per CEL judge in pure Python over a
2,000-row diff against 1.2 ms for SQLite over a materialised table.

The `expression + expected value + comparison` triple is the right 80% shape: OpenAI's Graders API,
promptfoo, Great Expectations and Kubernetes admission policy all converged on it independently. It
needs four amendments, each with a precedent: a third outcome, `not_applicable`, because "every
refunded order has a credit note" over zero refunds is `true` in CEL and `false` in JSONLogic and
neither is what you want reported; range and set comparators, because agents have latitude
(promptfoo argues an exact span count "would reject a correctly-batched run"); an expected value
that can be a structure or a task template (`{{task.issue_id}}`), so one judge serves many task
rows; and failures that name rows, because two hundred bare `false`s cannot be triaged.

Proposed layering, which is where every surveyed tool ended up:

1. **Named diff assertions, no expression language at all (most judges).** Agent-Diff's shape,
   extended: `{diff_type: added|removed|changed, table, where: {col: predicate}, expected_count:
   n | {min, max}, expected_changes: {col: {from, to}}, strict, ignore}` plus a `calls` assertion
   family (`{tool, count: {min, max}}`, `{tool: never}`). Closed predicate set of ~20 operators.
   JSON-encodable, validatable against a JSON Schema before anything runs, portable to any
   language, LLM-writable, and shippable as a dataset label. `strict: true` by default so an
   unnamed column change fails: side-effect checking that is a default, not a line the author
   must remember (ToolSandbox's guardrails and Agent-Diff both landed here). `expected_count: 0`
   is the idiom for "this table must not have changed".
2. **SQL over a materialised diff (the long tail).** Load `changes` and `calls` into two SQLite
   tables in memory and run one `SELECT`; the judge passes when it returns zero rows (Great
   Expectations' `UnexpectedRowsExpectation`), so the result set is the failure explanation. This
   is the only option with joins, aggregation, group-by and generic changed-column detection, it
   is the best-known query language for both humans and LLMs, and SQLite is in-process in Python
   and TypeScript. It is safe only for trusted judges, which is what a benchmark's own suite is;
   CEL is the answer if judges must ever be accepted from untrusted sources, at the cost of
   missing aggregation and a slow Python implementation.
3. **A code judge** for what neither covers.

This lives outside Seahaven, but it pins two things inside it: the state document must
materialise into flat tables in one step (§3's flat list), and the schema block of §2 is what lets
a judge tool type-check `issues.status` at authoring time rather than passing silently forever on
a typo. It also answers OpenEnv RFC 004, which rejects declarative rubrics because "the ecosystem
has converged on code-based rewards": the 80/15/5 split concedes the 5% and takes the rest.

Worth considering: Seahaven ships the JSON Schema for each state format and a tiny reference
loader (`seahaven.state.load(doc)` returning the two tables in an in-memory SQLite connection), so
every judge system starts from the same materialisation. Kiln, or anyone, builds the DSL on top.

**Decision needed:** the three tiers; SQL as the tier-2 language; whether the reference loader is
in scope for this project or a follow-up.

## 7. Filtering and size: no options in v1, bound payloads not lists

Nobody in the survey bounds the *number* of calls or changes; they bound each record's payload
and declare truncation in band. With results omitted, a call record is a few hundred bytes and a
thousand calls is under a megabyte. The diff is bounded by what the agent changed, which for a
runaway agent could be large, but a truncated diff is worse than a large one: a judge that cannot
tell "zero deletes" from "we stopped recording" silently passes.

Recommendation: `state()` takes no filter options in v1 and returns everything; counters are
always exact; a cap, if we ever add one, is a per-record payload cap on `args` with a `truncated`
marker, never sampling, and never a cap on the list without an explicit elision record. Filtering
fights stable-and-reusable, as you said, and a format flag is where variation belongs.

**Decision needed:** confirm no filtering in v1.

## 8. Control tools become the escape hatch, not the surface

Keep `controller_run_sql` and `controller_changes` for now; the docs (`serving.md`, `testing.md`,
`concepts.md`, the README's `grade(world_instance.changes())` example) move to `state()` as the
primary surface, and `controller_changes` becomes a thin alias for the v1 diff so the two cannot
disagree. OpenEnv's own precedent for "eval-only surfaces" is its simulation-mode gating of
`/state` and per-mode tool registration; `--include-control-tools` is the same idea.

## 9. A sketch, to argue with

```jsonc
{
  "format": "seahaven.state/1",
  "seahaven_version": "0.3.0",
  "world": {"name": "projecttracker", "version": "1.0.0", "schema_fingerprint": "sha256:…"},
  "fixture": {"id": "small_startup", "file_sha256": "…"},
  "episode_id": "…", "seed": 7, "now": "2026-03-04T09:00:00Z",
  "counters": {"steps": 14, "calls": 12, "errors": 2,
               "by_tool": {"transition_issue": 3, "search_issues": 9},
               "errors_by_tool": {"search_issues": 2}},
  "calls": [
    {"i": 0, "tool": "search_issues", "args": {"query": "login"}, "ok": true},
    {"i": 1, "tool": "transition_issue", "args": {"issue_id": "iss_9", "status": "done"},
     "ok": false, "error": {"kind": "tool", "code": "NOT_FOUND", "message": "issue iss_9 not found"}}
  ],
  "db": {
    "summary": {"issues": {"updates": 1}, "issue_events": {"inserts": 2}},
    "changes": [
      {"table": "issue_events", "op": "insert", "key": {"id": "ev_41"}, "indirect": false,
       "before": null, "after": {"id": "ev_41", "issue_id": "iss_3", "kind": "status", "created_at": "…"}},
      {"table": "issues", "op": "update", "key": {"id": "iss_3"}, "indirect": false,
       "changed": ["status", "updated_at"],
       "before": {"id": "iss_3", "status": "open", "assignee_id": "u_2", "updated_at": "…", "…": "…"},
       "after":  {"id": "iss_3", "status": "done", "assignee_id": "u_2", "updated_at": "…", "…": "…"}}
    ]
  }
}
```

Your two examples against it, in tier-1 form:

```jsonc
{"kind": "calls", "tool": "transition_issue", "count": {"max": 11}}
{"kind": "diff", "diff_type": "changed", "table": "issues", "expected_count": 24}
```

and one that needs tier 2, as one `SELECT` that must return no rows:

```sql
SELECT c.key FROM changes c
WHERE c.table = 'issues' AND c.op = 'update'
  AND json_extract(c.after, '$.status') = 'done'
  AND NOT EXISTS (SELECT 1 FROM changes e WHERE e.table = 'issue_events' AND e.op = 'insert'
                  AND json_extract(e.after, '$.issue_id') = json_extract(c.key, '$.id'))
```

## 10. Open items the research could not close

- The Agent-Diff paper itself was unreachable through the egress proxy; its code was read in full,
  its argument only through search summaries. Worth a read on an open network before the spec.
- No published measurement of how accurately LLMs write jq, JMESPath, CEL or SQL judges; the
  rankings are judgement. Text-to-SQL is the one well-benchmarked case, which favours tier 2 as SQL.
- Whether APSW exposes the changeset's indirect flag, and the exact cost of filling in whole rows
  for updates on a large diff. Both are architecture-step checks.
- How Prime Intellect's hub and prime-rl consume saved traces, if we want a second consumer beside
  TRL to design against.

## Appendix A: briefing for §3, the diff

### 1. What `inst.changes()` produces today

Three shapes, pinned by `tests/test_changes.py`. A table `notes(id TEXT PRIMARY KEY, body TEXT,
n INTEGER)`, fixture row `{"id": "n1", "body": "hello", "n": 3}`:

```jsonc
// INSERT INTO notes VALUES ('n2', 'new', 1)          -> whole new row
{"table": "notes", "op": "insert", "key": {"id": "n2"},
 "before": null, "after": {"id": "n2", "body": "new", "n": 1}}

// UPDATE notes SET body = 'goodbye' WHERE id = 'n1'   -> key + changed columns, both sides
{"table": "notes", "op": "update", "key": {"id": "n1"},
 "before": {"id": "n1", "body": "hello"}, "after": {"body": "goodbye"}}
//                                   ^ "n": 3 is not here, on either side

// DELETE FROM notes WHERE id = 'n1'                  -> whole old row
{"table": "notes", "op": "delete", "key": {"id": "n1"},
 "before": {"id": "n1", "body": "hello", "n": 3}, "after": null}
```

The `update` shape is SQLite's: an update carries the primary key and the columns the update
modified, nothing else, on both sides. That is the source of "a judge cannot read an unchanged
column from the diff".

### 2. It is a net diff: what a sequence of writes turns into

SQLite's session extension records, per primary key, the row *as it was the first time the
episode touched it*, and at `changes()` time compares that against the live table. So the diff is
"fixture versus now", per row, and the path between is gone. Verbatim from SQLite: "if a row is
inserted and then later deleted while a session object is active, neither the insert nor the
delete will be present in the changeset", and "if there is a row with a matching primary key in
the database, but all fields contain their original values, no change is added".

| The episode did | The diff shows |
|---|---|
| set `n` from 1 to 2, then back to 1 | nothing |
| update `body` twice | one `update`, `before` the fixture's value, `after` the final value |
| insert a row, then update it | one `insert` with the final values |
| insert a row, then delete it | nothing |
| delete a row, then insert one with the same key | one `update` (or nothing, if identical) |
| update a row to the values it already had | nothing |
| change a primary key value | a `delete` of the old key and an `insert` of the new |
| a call that raised and rolled back | nothing |
| a startup hook's rows | nothing: the session attaches after the hooks |
| a trigger or `ON DELETE CASCADE` write | included, indistinguishable from the agent's unless the `indirect` flag is surfaced |
| a write to a table in `untracked_tables`, or an FTS5 shadow table | nothing |
| a row with `NULL` in any primary-key column | nothing, silently |

This is a deliberate property, and it is what makes two runs that reach the same state agree
whatever route they took. It is also a limit: "the agent first moved the issue to `in_review`
and then to `done`" is not a question a net diff can answer. A per-call log is a different
artifact (Dolt ships both as separate tables and does not merge them), and it is the P2 format
in §4. The judge families that need it are ordering judges; everything about end state does not.

### 3. The same update in the four formats the research keeps citing

The row `issues:iss_3` goes from `status = "open"` to `"done"`, and the tool also bumps
`updated_at`. Every format below is real output, lightly trimmed.

**Seahaven today** (SQLite changeset semantics, changed columns only):

```jsonc
{"table": "issues", "op": "update", "key": {"id": "iss_3"},
 "before": {"id": "iss_3", "status": "open", "updated_at": "2026-03-01T…"},
 "after":  {"status": "done", "updated_at": "2026-03-04T…"}}
```

**Agent-Diff** (2026, the closest prior art: whole rows on both sides, table on the row,
grouped by op):

```jsonc
{"inserts": [], "deletes": [],
 "updates": [{"__table__": "issues",
              "before": {"id": "iss_3", "status": "open", "assignee_id": "u_2", "title": "…", "updated_at": "…"},
              "after":  {"id": "iss_3", "status": "done", "assignee_id": "u_2", "title": "…", "updated_at": "…"}}]}
```

Which columns changed is recomputed at judge time (`_changed_keys(before, after, ignores)`).
Their judge for this row is `{"diff_type": "changed", "entity": "issues", "where": {"id": "iss_3"},
"expected_changes": {"status": {"to": "done"}}}` with `strict: true`, which fails if any column
outside `expected_changes` and outside the ignore list changed. `updated_at` is on every shipped
suite's global ignore list.

**Debezium** (the change-data-capture envelope most of the industry copies; a log entry per
commit, not a net diff):

```jsonc
{"op": "u",
 "before": {"id": "iss_3"},                       // PK only, unless REPLICA IDENTITY FULL
 "after":  {"id": "iss_3", "status": "done", "assignee_id": "u_2", "title": "…", "updated_at": "…"},
 "source": {"connector": "postgresql", "db": "tracker", "table": "issues", "txId": 556, "lsn": 24023128},
 "ts_ms": 1465584025523}
```

The `source` block is the part nobody else has and everybody wishes they had; §2's provenance
block is our version of it, at the document root because one episode has one source.

**Dolt** (`dolt diff -r json`, a net diff between two commits, grouped by table, whole rows, NULLs
omitted, no `op` field):

```jsonc
{"tables": [{"name": "issues",
             "data_diff": [{"from_row": {"id": "iss_3", "status": "open", "assignee_id": "u_2", "title": "…"},
                            "to_row":   {"id": "iss_3", "status": "done", "assignee_id": "u_2", "title": "…"}}]}]}
```

An insert is `{"from_row": {}, "to_row": {…}}`; a delete is the reverse. As a SQL table
(`dolt_diff_issues`) the same row is `from_status = 'open', to_status = 'done', diff_type =
'modified'`, which is why "status went from A to B" is a one-line `WHERE` there.

### 4. The proposed v1 shape, on the same row

```jsonc
{"table": "issues", "op": "update", "key": {"id": "iss_3"}, "indirect": false,
 "changed": ["status", "updated_at"],
 "before": {"id": "iss_3", "status": "open", "assignee_id": "u_2", "title": "…", "updated_at": "…"},
 "after":  {"id": "iss_3", "status": "done", "assignee_id": "u_2", "title": "…", "updated_at": "…"}}
```

What each addition buys:

- **Whole rows.** "Every issue whose status changed still has an assignee" becomes writable.
  Cost: the row's width in bytes per update; nothing for inserts and deletes, which are whole
  already. How to build it: `after` is the live row by key; `before` is `after` with the
  changeset's old values overlaid. Both are available at `state()` time because the instance is
  still alive; neither is available later, which is why it has to be in the document.
- **`changed`.** Keeps the information the whole-row shape would otherwise hide, and makes
  "changed `status` and nothing else" a set comparison instead of a column-by-column diff the
  judge author has to write. Agent-Diff recomputes this at judge time; we know it exactly and
  should say it.
- **`indirect`.** SQLite's own flag: `true` when a trigger or foreign-key action made the change
  rather than the statement. A world with an `issue_events` trigger, or `ON DELETE CASCADE` on
  `issue_labels`, produces rows the agent never asked for; today they look like agent writes.
- **Order.** SQLite documents within-table order as undefined and the implementation emits hash
  order. Sorting by `(table, key)` costs nothing and makes two identical runs produce identical
  documents, which is what lets an eval diff two state files.

### 5. Flat, grouped, and composition

Three ways to arrange the same changes:

```jsonc
// (a) flat: one list, table on the record
"changes": [{"table": "issues", "op": "update", …}, {"table": "issue_events", "op": "insert", …}]

// (b) grouped by table, then op (Dolt-like)
"tables": {"issues": {"updates": [{…}]}, "issue_events": {"inserts": [{…}]}}

// (c) grouped by op, table on the record (Agent-Diff)
"updates": [{"table": "issues", …}], "inserts": [{"table": "issue_events", …}]
```

(a) materialises into one SQL table in one step and filters the same way in every expression
language. (b) reads best by eye and makes "did `comments` change at all" a key lookup. (c) is the
weakest of the three: it optimises for the op, which is the question asked least. The ideas doc
recommends (a) plus a `summary` of counts per table and op, which gives (b)'s lookup for the
common case without duplicating rows.

The composition point raised in discussion: the grouping question does not stop at the row list.
The database block is itself a group inside the state document, beside `calls` and `counters`,
and the shape has to be designed so that a document can carry more than one such group, and so
that the database group can be split further if a world ever has more than one database or more
than one namespace of tables. That is a §3 decision still to be made, and it constrains (a)
versus (b): (b) already has a natural place to put a namespace; (a) needs a field for it.

## Appendix B: the agreed `db.log` shape

Illustrative, on ProjectTracker. Three calls: a read, a transition that the tool records in
`issue_events` itself, and a delete where a foreign-key cascade removes a label row. The `calls`
block is shown for the ordinals only; its shape is §5's and not yet agreed.

```jsonc
"calls": [
  {"i": 0, "tool": "search_issues", "args": {"query": "login"}, "ok": true},
  {"i": 1, "tool": "transition_issue", "args": {"issue_id": "iss_3", "status": "done"}, "ok": true},
  {"i": 2, "tool": "delete_issue", "args": {"issue_id": "iss_7"}, "ok": true}
],
"db": {
  "log": [
    // call 0 read only: no records

    // call 1: the tool updated the issue and inserted an event row itself
    {"i": 1, "subworld": null, "table": "issue_events", "op": "insert",
     "key": {"id": "ev_41"},
     "before": null,
     "after": {"id": "ev_41", "issue_id": "iss_3", "kind": "status", "value": "done", "created_at": "2026-03-04T09:00:00Z"}},
    {"i": 1, "subworld": null, "table": "issues", "op": "update",
     "key": {"id": "iss_3"},
     "before": {"status": "open", "updated_at": "2026-03-01T14:22:10Z"},
     "after":  {"status": "done", "updated_at": "2026-03-04T09:00:00Z"}},

    // call 2: the tool deleted the issue; ON DELETE CASCADE removed its label row
    {"i": 2, "subworld": null, "table": "issue_labels", "op": "delete",
     "key": {"issue_id": "iss_7", "label_id": "lbl_2"},
     "before": {"issue_id": "iss_7", "label_id": "lbl_2"},
     "after": null},
    {"i": 2, "subworld": null, "table": "issues", "op": "delete",
     "key": {"id": "iss_7"},
     "before": {"id": "iss_7", "key": "ENG-7", "title": "Flaky login test", "status": "open", "assignee_id": null, "project_id": "prj_1", "created_at": "…", "updated_at": "…"},
     "after": null}
  ]
}
```

What it commits to:

- One flat list, one record per row per call, ordered by call, sorted within a call by
  `(subworld, table, key)`.
- `key` is separate and never repeated inside `before` or `after` on an update. An update's
  `before` and `after` hold exactly the changed non-key columns, so `updated_at` appears because the
  tool wrote it. An insert's `after` and a delete's `before` are whole rows.
- The cascade-deleted label row looks exactly like a row the tool deleted: the log records state,
  not reason.
- Values are JSON values: NULL is `null`, a blob is base64 text, everything else as SQLite stores it.
- A call that changed nothing has no records. The same row touched in two calls appears twice, once
  per call.
- A write with no call in flight carries `i: null`.
