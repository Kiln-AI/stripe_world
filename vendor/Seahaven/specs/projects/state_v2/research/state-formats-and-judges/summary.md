# Research: State formats and judge systems for agent environments

## Bottom Line

The design Seahaven is reaching for — a persisted `state()` that hundreds of declarative judges
grade long after the episode — is **not what the RL/eval frameworks do, and is what the best
benchmarks do**. Across TRL, SkyRL, Atropos, ART and veRL, reward is universally computed from a
*live, in-process environment object*; OpenEnv specifies almost nothing about `state` (base type is
`{episode_id, step_count}` with `extra="allow"`, its schema endpoint advertises only that base, and
its RFC 004 explicitly rejects declarative graders), and the one place it persists a final state —
`artifacts["final_state"]` in `results.jsonl` — has no version, no schema and no size cap, and TRL
discards it. Meanwhile the benchmark literature has converged: **row-level diff between start and
end state** is the grading substrate that supports cheap, attributable, partial-credit judges, and
three systems prove it (AppWorld, ToolSandbox, and **Agent-Diff (2026), whose `DiffResult(inserts,
updates, deletes)` is essentially identical to Seahaven's `inst.changes()` and is graded by a
versioned JSON assertion DSL**). The two existence proofs for judging from a *saved* state are
Inspect AI's `EvalSample.store` ("state at end of sample execution", the only thing the sandbox-less
`inspect score` path can see) and verifiers' `IsolatedVerifierEnv`. So: the prior art supports the
plan, but Seahaven is ahead of its consumers, not behind them — `state()` must be designed to be
useful even to a trainer that ignores it. Three concrete constraints fall out: the **SQLite changeset
cannot answer questions about unchanged columns**, so the diff alone is not a sufficient judge
substrate; **no benchmark ships a versioned envelope around its state**, while every durable-format
community (CloudEvents, JSON Schema, OTel, Avro, Stripe) requires a version identifier inside every
document; and on the judge side, **`expression + expected + comparator` is the convergent industry
shape** but needs a `not_applicable` outcome, range/set comparators, and failures that name rows.

## Key Findings

- **Three clusters of state grading, and only one scales.** Whole-state equality (τ-bench SHA-256s
  the whole JSON DB; BFCL compares every public attribute) forces every tool to be deterministic and
  tells you nothing about *what* differed. Scoped reads (WebArena/OSWorld/WorkArena/MCPMark name a
  URL, file or SQL query) cannot see collateral damage at all. Row-level diff is where the prior art
  is. ([benchmarks](./benchmarks-with-stateful-backends/summary.md))

- **Agent-Diff is the closest published prior art to Seahaven's entire design.** Diff:
  `{inserts: [row + __table__], updates: [{__table__, before, after}], deletes: [...]}`. Judge: a
  JSON document `{version, scenario, task, ignore_fields, strict, assertions[]}` validated against a
  shipped JSON Schema, each assertion `{diff_type, entity, where: {field: predicate}, expected_count,
  expected_changes: {field: {from, to}}}` over a closed 19-operator predicate set, scored
  `{passed, total, percent}`. 224 tasks; in the published dataset the assertion spec *is* the label.
  ([benchmarks](./benchmarks-with-stateful-backends/summary.md))

- **The SQLite changeset carries only the changed columns, on both sides of an UPDATE** — verbatim
  from the session extension: unmodified non-PK fields are "undefined" in both the old and new
  records. A judge therefore **cannot read an unchanged column from the diff**, in either direction.
  It also silently drops rows with a NULL in any PK column, emits a PK rewrite as DELETE+INSERT
  rather than UPDATE, and documents within-table ordering as *undefined*. Agent-Diff, by contrast,
  carries whole `before`/`after` rows. ([diff formats](./diff-formats-and-versioned-data-contracts/summary.md))

- **Side-effect checking only works when it is the default, not a line the author remembers.**
  ToolSandbox auto-applies a guardrail similarity to every database a milestone did not constrain;
  Agent-Diff's `strict: true` fails any change whose changed-column set is not a subset of what was
  expected; WorkBench makes "harmful side effect" a reported metric class. Where it is optional
  (AppWorld, AgentDojo) or absent (WebArena, OSWorld, MCPMark), the only defence is a full reset.
  ([benchmarks](./benchmarks-with-stateful-backends/summary.md))

- **Irrelevant changes (timestamps, audit rows) are handled six ways, and declared `ignore_fields`
  wins.** The alternatives are removing all nondeterminism from tools (τ-bench), freezing the clock
  (AppWorld), a naming convention that hides fields (BFCL's `_`-prefix skip), per-column similarity
  functions (ToolSandbox), or giving up (WorkArena, in a source comment). Separately, SQLite already
  computes an **indirect-change flag** for writes caused by a trigger or FK action — the only
  built-in handle on this problem, available from `sqlite3changeset_op()`, and impossible to
  reconstruct after the fact. ([benchmarks](./benchmarks-with-stateful-backends/summary.md),
  [diff formats](./diff-formats-and-versioned-data-contracts/summary.md))

- **Replaying the transcript to recover state is documented, first-hand pain.** τ²-bench's
  `set_state` must skip hallucinated tool names and non-mutating tools, and ships a `strict=False`
  escape hatch because recorded outputs drift cosmetically against current tool code (`25` vs
  `25.0`). Persisting the state makes this class of problem disappear — which is the argument for
  `state()` in one line. ([benchmarks](./benchmarks-with-stateful-backends/summary.md))

- **Declarative config does not eliminate per-task code; it relocates it.** OSWorld's clean
  `{func, result-getter, expected-getter}` JSON is backed by **206 metric functions and 62 getters
  for 369 tasks**; Windows Agent Arena's metric histogram is a long tail of one-use functions.
  Agent-Diff avoids this by keeping the predicate set closed and small — and pays by being unable to
  express aggregations at all (its `aggregates` schema block is defined but not wired up).
  ([benchmarks](./benchmarks-with-stateful-backends/summary.md))

- **The discriminating judge is the cross-list join, and half the candidate languages cannot express
  it.** Verified against a Seahaven-shaped fixture: JSONPath (RFC 9535), base JMESPath and JSONLogic
  all fail "every ticket that went open→closed has a matching `ticket_events` insert" — JSONLogic
  *silently returns `false`*. Only CEL, jq, SQL, JMESPath-Community, simpleeval and Jinja can. And
  **only SQL can enumerate which columns changed without hardcoding column names** (SQLite
  `json_tree`), which is what makes "changed `status` and nothing else" judges possible.
  ([assertion languages](./declarative-assertion-languages-over-json/summary.md))

- **Safety and performance invert the ranking.** jq and SQL have no sandbox (a jq expression read
  this session's environment variables, verified; `[range(1e12)]` needed an external kill). CEL is
  the only join-capable language with a production cost-budget model (Kubernetes). But cel-python
  takes **1.28 s per judge over a 2,000-row diff** vs 36 ms for jq's C binding and 1.2 ms for SQLite
  against a materialised table — a three-order-of-magnitude spread, with the "safe" option slowest.
  Every candidate is O(rows) per judge, so indexing the diff once is the only structural lever.
  ([assertion languages](./declarative-assertion-languages-over-json/summary.md))

- **`expression + expected + comparator` is the convergent shape, with four amendments.** OpenAI's
  Graders API, promptfoo, Great Expectations (~60 `Expect<Subject><Metric>To<Comparator><Expected>`
  classes) and Kubernetes admission policy landed on it independently. Amendments: a **third outcome
  `not_applicable`** (CEL says `[].all(...)` is true, JSONLogic says false — a judge over zero rows
  must neither pass nor penalise), **range and set comparators** not just equality, an **expected
  value allowed to be a schema or a task template**, and a result that **names the offending rows**
  (Great Expectations' `UnexpectedRowsExpectation`). ([assertion languages](./declarative-assertion-languages-over-json/summary.md))

- **Named signals, not a verdict scalar, is what every consumer can absorb**, and judges-as-a-list
  get partial credit for free. `dict[str, Reward]` (verifiers), `dict[str, Score]` (Inspect),
  `dict[str, float]` (SkyRL, ART), one column per reward function (TRL). Inspect additionally
  distinguishes *not applicable* / *unscorable* / *scored zero* — exactly the resolution a DB-diff
  judge needs. ([OpenEnv](./openenv-and-rl-framework-consumers/summary.md),
  [assertion languages](./declarative-assertion-languages-over-json/summary.md))

- **Every durable-format community puts a required version identifier at the root of every
  document, plus a written contract for which changes are invisible, plus a chain of small pure
  per-version transformations.** CloudEvents `specversion` (major.minor only, so patches don't
  churn it); JSON Schema `$schema` on every document with non-network-addressable `$id`s blessed;
  **OpenTelemetry is the most complete published "version field plus formatter" design** —
  `schema_url` pointing at an immutable YAML file whose `versions:` map lists typed transformations
  applied one by one across a range. For a saved artifact, put the chain on the *reader* (the
  upcaster pattern); Stripe does the mirror image for pinned APIs. Avro's canonical-form fingerprint
  is a cheap way to identify a schema by content rather than a hand-maintained number.
  ([diff formats](./diff-formats-and-versioned-data-contracts/summary.md))

- **Dolt's `from_X`/`to_X` column-doubling is the most judge-friendly row-diff design found** — it
  turns a row diff into an ordinary table, so `WHERE from_status='A' AND to_status='B'` is the whole
  judge. Debezium's envelope (`op`/`before`/`after`/`source`/`ts_ms`) is the most-copied shape, and
  its **`source` provenance block is the part most row-diff formats omit and cannot add
  retroactively**. JSON Patch and Merge Patch are apply-oriented, carry no before-values, and are
  disqualified as grading substrates. ([diff formats](./diff-formats-and-versioned-data-contracts/summary.md))

- **There is no standard for a tool-call record.** Across eight formats the only universal fields are
  tool name, arguments, and an id joining call to result; errors are modelled four incompatible ways,
  an explicit ordinal appears in two of eight, timing in three. What *is* consistent is size
  discipline: the **result payload** is always the field that blows up, handled by four escalating
  patterns (don't record / record capped / record a reference / hash the whole state). Inspect AI has
  the best design — 16 KiB result cap plus `truncated: (raw_bytes, cap)`, and any string over 100
  chars replaced by `attachment://<hash>` stored once.
  ([trajectory records](./tool-call-and-trajectory-records/summary.md))

- **ATIF (Harbor RFC 0001, v1.8, April 2026) is the nearest thing to a versioned trajectory
  standard** — `schema_version`, `steps[]` with a required integer ordinal, and a `final_metrics`
  aggregate block — but it has **no error field on a tool result at all**, so it is a structural
  model, not a schema to adopt. Its cost stance is the transferable principle: record the value
  measured at run time, never the inputs needed to recompute it, because those change and make
  historical records wrong. Letta's `trajectory` has the only published concrete bounding policy
  (20,000 code points for arguments, 2,500 head-tail for results, with machine-readable
  `diagnostics` so nothing is dropped silently).
  ([trajectory records](./tool-call-and-trajectory-records/summary.md))

- **Exact argument equality is not a usable judging default** — tau2's `Action.compare_args` ("the
  arguments to check; if None, check all") and AgentEvals' `tool_args_match_overrides` (`exact` /
  `ignore` / a field list / a comparator) were invented independently to escape it. And **LLM judges
  never grade state** across all fifteen benchmarks surveyed; where state is readable, everyone reads
  it. ([trajectory records](./tool-call-and-trajectory-records/summary.md),
  [benchmarks](./benchmarks-with-stateful-backends/summary.md))

## Implications

**For what `state()` returns.** The diff is necessary but not sufficient: because a changeset UPDATE
carries only changed columns, a judge cannot ask about an unchanged column in either direction, so
`state()` should make current rows reachable beside the changes or explicitly accept that a whole
class of judge is unwritable. Seahaven's existing `{episode_id, step_count, fixture, now, world}`
already resembles Debezium's `source` provenance block, which is the field group the CDC world says
you cannot add retroactively — keep it and extend it, and stamp it with a version identifier, since
**no benchmark does this and every durable-format community says to**. Two cheap additions the
research surfaced: the SQLite indirect-change flag (available now, unrecoverable later) and an
explicit `changed` column-name list (derivable, but an LLM gets `"status" in change["changed"]`
right more often than `set(after) - {pk}`). Ordering should be promised and sorted in `render()`,
not inherited from SQLite.

**For the judge format.** The `expression + expected + comparator` triple over a closed, small
operator catalogue with a code escape hatch underneath is well-evidenced — that is both Agent-Diff's
design and the independent convergence of four eval tools. But OSWorld's
206-metric-functions-for-369-tasks outcome is the warning: if the expression language is too weak the
library grows anyway, and Agent-Diff paid for its closed operator set by being unable to aggregate.
The language choice is a real tradeoff with no free option — SQL is the only one that handles generic
changed-column detection and is 1000× faster, but has no sandbox; CEL is the only safe join-capable
option and is the slowest by three orders of magnitude.

**For the OpenEnv wire.** OpenEnv will not constrain this: `State` allows extra fields, `/state` is
simulation-mode-gated (which is itself the precedent for "controller tools are for evals only"), and
`/schema` won't advertise the real shape, so consumers must import the package or read the docs.
Note that OpenEnv's HTTP `GET /state` reads a throwaway environment, not the session — the
session-scoped read is the WebSocket `state` message. RFC 004's explicit rejection of declarative
graders is worth answering head-on in the spec, since Seahaven is proposing exactly that.

**For re-grading old episodes.** Every benchmark that survived a grading change versioned the *task
definitions* alongside results (AppWorld's `DB_VERSION` check, AgentDojo's importable suite versions,
τ²-bench's embedded tasks plus a CHANGELOG naming which domain moved which way and which git tag
reproduces the old behaviour). None versioned the state. Both halves are needed.

## Conflicts and Uncertainty

- **Declarative graders: rejected by OpenEnv, shipping everywhere else.** OpenEnv RFC 004 argues
  against a declarative grader format and keeps rubrics as server-side composable Python taking
  `(action, observation)` — never state. But verifiers ships `Criterion` rubric files as data,
  Agent-Diff ships a JSON-Schema-validated assertion DSL, and four eval tools converged on the
  triple. The evidence leans strongly toward declarative *for deterministic state assertions*; RFC
  004's argument is about LLM-judged rubrics, which is a different problem. Worth reading RFC 004's
  reasoning directly before rebutting it.

- **Whole rows vs changed columns only.** Agent-Diff — the nearest prior art — carries whole
  `before`/`after` on updates; SQLite's changeset, which Seahaven's `inst.changes()` renders, carries
  only changed columns. Dolt emits whole rows too (repeating unchanged columns on both sides). This
  is the single largest structural divergence between Seahaven's existing diff and the design it
  most resembles, and it directly determines which judges are writable.

- **Agent-Diff's own two diff producers may disagree** — the snapshot-join path yields a net diff,
  the change-journal path folds an ordered log into three buckets without coalescing, so an
  insert-then-update appears as both. No reconciliation was found in the repo and no statement of
  which is canonical. Seahaven's changeset is a net diff and corresponds to the snapshot path.

- **`render()`'s documented ordering is wrong.** The docstring says rowid order; SQLite documents
  within-table order as undefined and the implementation emits hash-bucket order over the PK. Small,
  but it is a promise the code does not keep.

- **Partial credit is nearly free one way and very expensive the other.** Judges-as-independent-
  assertions get it for free (Agent-Diff, AppWorld, TheAgentCompany's weighted checkpoints);
  single-equality judges get nothing (τ-bench, BFCL). Only ToolSandbox has smoothly *graded* partial
  credit, and it cost the most complex evaluator in the survey — per-column similarity, Hungarian
  matching, DAG topological search — for unclear benefit.

- **Library health is a live selection risk on the assertion side.** npm `jmespath` last published
  2022; the JMESPath-Community fork that can do joins installs under the module name `jmespath`,
  colliding with boto3. `jsonpath-plus` has had two RCEs from evaluating paths through `eval`/`vm`.
  Jinja's sandbox was escaped twice in 15 months and its own docs say it "is not a solution for
  perfect security". cel-python and cel-js already disagree on `[].all(...)`.

## Gaps

No subtopic failed. Every one hit the same obstacle: **this session's egress proxy blocked most
canonical documentation sites** — sqlite.org, opentelemetry.io, huggingface.co, arxiv/ACL/OpenReview,
IETF/rfc-editor, docs.dolthub.com, debezium.io, stripe.com, promptfoo.dev, cel.dev,
inspect.aisi.org.uk, docs.langchain.com and more. Agents routed around it by reading the same text
from each project's public git repo on `raw.githubusercontent.com`, which is the primary source in
almost every case. Each subtopic summary flags in place which claims rest on search summaries rather
than fetched text. Specifically:

- **The Agent-Diff paper (arXiv 2602.11224) should be re-fetched on an unblocked network.** The code
  was read first-hand, but the paper's *argument* — its "state-diff contract" framing — is the one
  piece of prose directly on-topic, and it is currently second-hand.
- **Paper-only numbers are second-hand throughout the benchmarks subtopic** (AppWorld's task splits
  and ~50-LOC-per-task figure, AgentDojo's 97/629 counts, ToolSandbox's 1,032 scenarios, MCPMark's
  127 tasks, WorkBench's 690). All code-level claims are first-hand from clones.
- **Authoring cost per task in wall-clock hours is published nowhere reachable.** Relative cost was
  inferred from LOC and metric-functions-per-task ratios. If the spec needs to argue "judges are
  cheap to write", that number does not exist in the literature.
- **No measurement of LLM accuracy at writing jq / JSONPath / JMESPath / CEL.** The writability
  rankings are reasoned judgement, explicitly labelled as such; the nearest real evidence is
  text-to-SQL benchmarking and one text-to-JQL benchmark.
- **Nobody measured how many real judges need the cross-list join.** The claim that it is the modal
  judge is reasoned from the multi-table structure of a tool surface, not from a corpus.
- **Hugging Face being blocked leaves one OpenEnv question open**: whether any hub-only environment
  exposes richer state than the 28 in-repo ones (which were censused in full — median 6 fields).
  Also uncovered: Prime Intellect's Environments Hub as a product, and `prime-rl`, the trainer that
  consumes verifiers v1 — the next repo to read if the spec needs to know how a trainer reads
  `traces.jsonl`.
- **No precedent found for bounding the *number* of tool calls.** Every format bounds per-payload
  size; none caps or samples the call list itself. Likewise there is **no convention for referencing
  externalized content** — OTel says `TODO`, OpenInference uses an uploader URI, LangSmith S3 URLs,
  MCP `resource_link`, Inspect `attachment://<hash>`. Four answers, no standard.
- **Two quick local verifications, not research questions**: whether `apsw` exposes the
  indirect-change flag and `SQLITE_SESSION_OBJCONFIG_ROWID`, and whether cel-python / cel-rust /
  cel-js can be given the *same* extension functions with the same semantics (they already diverge
  on empty-set macros).
- **DuckDB, JSONata and OPA/Rego were not researched in depth.** If CEL's missing aggregation is the
  blocker, JSONata deserves a proper look — it has variable binding, `$sum`/`$count`/`$reduce`, and
  live Python and TypeScript implementations.

## Subtopics

- [Benchmarks with stateful backends](./benchmarks-with-stateful-backends/summary.md) — fifteen
  benchmarks read at source (τ-bench, τ²-bench, AppWorld, AgentDojo, ToolSandbox, WebArena, OSWorld,
  Windows Agent Arena, BFCL, CRMArena, TheAgentCompany, WorkArena, WorkBench, MCPMark, Agent-Diff):
  what state each reads, the exact judge shape, side-effect handling and partial credit. Headline:
  row-level start/end diff is the only approach that supports many cheap attributable judges, and
  **Agent-Diff's versioned assertion DSL over a `{inserts, updates, deletes}` diff is a near-exact
  match for Seahaven's design**.
- [OpenEnv and RL-framework consumers](./openenv-and-rl-framework-consumers/summary.md) — the OpenEnv
  `State`/`Rubric`/Task-API contract, a census of all 28 in-repo environments, and how TRL, verifiers,
  SkyRL, Atropos, ART, veRL and Inspect AI treat state. Headline: **OpenEnv specifies almost nothing
  and every trainer grades from a live environment object** — Inspect's `EvalSample.store` and
  verifiers' `IsolatedVerifierEnv` are the only two precedents for grading a persisted final state.
- [Diff formats and versioned data contracts](./diff-formats-and-versioned-data-contracts/summary.md)
  — SQLite sessions/changesets/`sqldiff`, Dolt, Debezium, Datomic, JSON Patch/Merge Patch,
  jsondiffpatch, git; then CloudEvents, JSON Schema, OTel schema files, Stripe, Avro, Protobuf and
  AIP-180. Headline: **a changeset UPDATE carries only the changed columns on both sides** (plus
  three silent data-loss modes), and every durable-format community converged on a required root
  version identifier plus a chain of per-version transformations on the reader.
- [Declarative assertion languages over JSON](./declarative-assertion-languages-over-json/summary.md)
  — nine languages run hands-on against a Seahaven-shaped fixture (CEL, jq, JSONPath, JMESPath
  ±Community, JSONLogic, SQLite/DuckDB SQL, Jinja2, simpleeval, asteval), plus how promptfoo, OpenAI
  Graders, Inspect, Braintrust, DeepEval, LangSmith, Great Expectations and Kubernetes express
  assertions. Headline: **the cross-list join eliminates JSONPath, base JMESPath and JSONLogic; only
  SQL does generic changed-column detection; expression+expected+comparator is right with four
  amendments.**
- [Tool-call and trajectory records](./tool-call-and-trajectory-records/summary.md) — OTel GenAI
  semantic conventions (now split into their own repo, zero stable attributes), OpenInference,
  LangSmith runs, Inspect `ToolEvent`, MCP `tools/call`, Anthropic/OpenAI wire formats, tau-bench
  actions, ATIF and Letta's `trajectory`. Headline: **no standard exists** — name, arguments and a
  joining id are the only universal fields — but every format agrees the result payload is what
  blows up, handled by four escalating size patterns.
