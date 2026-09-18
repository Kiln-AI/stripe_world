# Cross-cutting patterns across 15 stateful-backend benchmarks

Companion to [benchmark-profiles.md](./benchmark-profiles.md), which has the per-benchmark
detail and the source citations. This doc answers the comparative questions directly.

## A. What the harness reads as "final state"

| benchmark | what is read | when | persisted? |
|---|---|---|---|
| τ-bench | whole in-memory JSON DB → SHA-256 | in-process, at episode end | no |
| τ²-bench | whole pydantic DB → hash (agent DB + user DB separately) | **reconstructed by replaying the transcript** into a fresh env | no |
| AppWorld | start + end **SQLite DB files** loaded into an ORM pair | offline, after the run | **yes** — `outputs/<exp>/tasks/<id>/dbs` |
| AgentDojo | `pre_environment` (deep copy) + `post_environment` pydantic objects | in-process | no (in-process objects) |
| ToolSandbox | a **series** of polars DataFrame snapshots, one per DB per message index | in-process over the whole trajectory | serialised in the run artefacts |
| WebArena | live page: JS `locator` expression, or a Python helper hitting the site API | in-process, browser still open | no |
| OSWorld / WAA | a **getter**: file pulled off the VM, a command's stdout, an a11y tree, a rule literal | in-process, VM still up | files are copied out |
| BFCL multi-turn | `vars(instance)` of the live backend Python objects | in-process | no |
| CRMArena | nothing — the agent's answer string | — | — |
| TheAgentCompany | service APIs (RocketChat/GitLab/ownCloud/Plane) + `/workspace` files | in-process, services still up | no |
| WorkArena | one record via the ServiceNow Table API by `sys_id`, display values | in-process | no |
| WorkBench | five pandas DataFrames, after re-executing the agent's write calls from a reset state | offline-ish (replay) | no |
| MCPMark | live Postgres/Notion/GitHub, queried by a standalone `verify.py` | after the run, backend still up | no |
| Agent-Diff | `{inserts, updates, deletes}` from snapshot-table joins or a CDC change journal | after the run | **yes** — snapshot tables + a `Diff` row |

**The split that matters for Seahaven:** only **AppWorld** and **Agent-Diff** treat the
final state as a durable artefact that outlives the process. Everyone else either grades
in-process while the environment is still alive, or — worse — reconstructs the state by
replaying the transcript.

**Replay is the failure mode to avoid.** τ²-bench, WorkBench and BFCL all re-execute
recorded calls to get the state they grade. τ²-bench's own code documents the cost: it
must skip hallucinated tool names, skip non-mutating tools "to avoid re-execution and
non-deterministic output comparison issues", and ships a `strict=False` mode because
"recorded tool outputs contain cosmetic drift against current tool code". Every one of
those is a bug class that a persisted state does not have.

## B. Diff vs. full end state

**Grades on a diff (explicitly):**

- **AppWorld** — the design statement is "doing a *diff* between the start and end
  database states, and asserting that all expected and no unexpected changes are made".
  Three granularities: `changed_model_names()` (tables), `changed_records()` →
  `(added, updated, removed)` (rows, matched by primary-key id with record-hash
  equality), `changed_field_names(model, id)` (columns).
- **Agent-Diff** — `DiffResult(inserts, updates, deletes)` is *the* interface; the judge
  language has no access to the full end state at all.
- **ToolSandbox** — `addition_similarity` / `removal_similarity` / `update_similarity`
  are diff operators relative to a reference snapshot, computed with anti-joins.
- **AgentDojo** — `utility(model_output, pre_environment, post_environment)` hands the
  judge both sides; most judges read `post_environment` but read-only tasks assert
  `pre_environment == post_environment`.
- **WorkBench** — the *side-effect* metric is a diff against the reset state; the
  correctness metric is a full-state comparison against a replayed gold state.

**Grades on full end state:**

- **τ-bench / τ²-bench** — a hash of the entire DB. Maximum strictness, zero
  attribution: you learn "different", never "different how".
- **BFCL** — all public attributes of every involved backend object vs. the gold
  instance. Same strictness, but it does report a `differences` dict per attribute.

**Grades on a scoped read, not state at all:**

- **WebArena, OSWorld, WAA, WorkArena, TheAgentCompany, MCPMark, CRMArena** — the judge
  names what to look at (a URL + locator, a file + metric, a `sys_id` + fields, a SQL
  query) and looks only there. Nothing is diffed; side effects outside the scope are
  invisible unless separately instrumented.

**The tradeoff, stated plainly.** Whole-state hashing (τ-bench) is the cheapest judge to
author — you need no judge at all, just a gold action list — and the most expensive
environment to build, because every tool must be made deterministic and every irrelevant
field must not exist. Scoped reads (WebArena/OSWorld) are the cheapest environment and
the weakest judge: nothing catches collateral damage. Diffs (AppWorld/Agent-Diff) sit in
the middle and are the only ones that let a judge say *both* "the thing I asked for
happened" and "nothing else did", per-table and per-column, with attributable failures.

## C. Irrelevant changes (timestamps, audit rows, derived fields)

Every benchmark that compares more than a scoped read hits this. Six distinct strategies
in the wild:

1. **Remove the nondeterminism from the environment.** τ-bench: no `datetime`, `random`
   or `uuid` anywhere in the retail or airline tool implementations (verified by grep);
   new ids are derived from existing data. Whole-DB hashing is only viable because of
   this.
2. **Freeze the clock.** AppWorld runs both the episode and the evaluator under
   `freezegun`'s `freeze_time(task.datetime)`, and the task records its own `datetime`.
   Timestamps are real fields, written and compared, but deterministic.
3. **Hide the field behind a naming convention.** BFCL's `_compare_instances` skips any
   attribute starting with `_`; `GorillaFileSystem` stores
   `self._last_modified = datetime.datetime.now()`. Zero cost, zero visibility, only
   works when you own the backend classes.
4. **Declarative ignore lists.** Agent-Diff's `ignore_fields` at global / entity /
   assertion scope, applied to change *detection* rather than to the stored diff. The
   shipped lists (see [state-diff-dsl.md §7](./state-diff-dsl.md)) name three classes:
   timestamps; opaque concurrency tokens (`etag`, `sequence_id`, `file_version`, `sha1`);
   derived/denormalised fields (`path_collection`, `html_link`, embedded
   `created_by`/`modified_by`/`owned_by`). AppWorld does the same at table granularity
   with a hard-coded global list (`supervisor.Task`, `admin.PaymentCard`,
   `amazon.BrowsedProduct`) plus per-call `include=`/`ignore=`.
5. **Per-column comparison functions.** ToolSandbox ships a default column→similarity
   table per database: `content` uses ROUGE-L, `creation_timestamp` uses exact match,
   `openai_tool_call_id` uses `column_one_similarity` (always 1, i.e. ignored). OSWorld
   does the coarser version with per-rule options (`ignore_case`, `approx:<threshold>`,
   `re.<FLAGS>`). WorkBench lowercases all string columns except an allowlist
   (`CASE_SENSITIVE_FIELDS = ["status", "list_name", "board"]`).
6. **Decline to compare.** WorkArena's `validate` docstring: "we check only if the
   expected fields have the right value. We don't Check if there are extra fields that
   shouldn't be there. We could have issues matching other fields since **calculation
   rules may have changed through time**." A real production backend has derived fields
   whose behaviour drifts; they gave up on total comparison and instrumented the UI
   instead (see D).

Normalisation is separate from ignoring, and two benchmarks do it explicitly:
Agent-Diff's `_normalize_for_comparison` converts `datetime`/`date` to ISO strings
recursively through containers before any predicate runs, and sanitises `bytes`/
`memoryview` to `"<binary_data>"`. AppWorld's record hash is computed over all
properties except `id`, `_db_home_path` and `record_hash` itself.

## D. Side effects the agent should not have made

Ranked by how much of the work falls on the task author:

- **Free / default-on.**
  - ToolSandbox **guardrails**: "when `guardrail_database_list` is None, guardrail is
    applied to all non-SANDBOX databases that doesn't have an associated
    SnapshotConstraint in this milestone." Anything you didn't assert about is asserted
    unchanged, with an explicit exclusion list as the opt-out.
  - Agent-Diff **`strict: true`** (the default): for a `changed` assertion, if the
    changed-key set is not a subset of `expected_changes`, the assertion fails.
  - τ-bench / τ²-bench / BFCL: total state comparison makes every stray write a failure
    automatically. Strongest guarantee, worst diagnostics.
- **One line the author must remember.**
  - AppWorld: `test.case(models.changed_model_names(), "==", {"wallet.Address"})`, and
    for read-only tasks `test.case(models.changed_model_names(), "is_falsy")`.
  - Agent-Diff: an extra assertion with `expected_count: 0`.
  - AgentDojo: `return ... and (pre_environment == post_environment or not strict)`.
- **Instrumented at the environment, not the judge.**
  - WorkArena injects JS that sets `window.gsft_main.WORKARENA_BAD_FIELD_CHANGED` when a
    field outside task scope is edited; `validate` reads the flag and returns 0.
  - WorkBench makes it a **reported metric**, not a pass/fail: `has_side_effects =
    state_changed and not correct`, evaluated over an explicit tuple
    `SIDE_EFFECT_STATE_FIELDS = ("calendar_events","emails","project_tasks","crm_data")`
    — plots are excluded because "creating a plot is harmless". Every run is classified
    correct / failed-but-harmless / harmful. That three-way split is the most useful
    output shape I saw and it costs one extra comparison.
- **Not checked at all.** WebArena, OSWorld, WAA, TheAgentCompany, MCPMark, CRMArena.
  WebArena and OSWorld rely on a full environment reset (Docker/VM snapshot) between
  tasks, which prevents *contamination* but does not *detect* damage.

**AppWorld's `no_op_pass` / `no_op_fail` labels** deserve separate mention as the
meta-check. Every test is labelled by whether a do-nothing agent already passes it:
`no_op_pass` tests are the side-effect guards, `no_op_fail` tests require real work. It
is a validation harness for the judge — a task whose tests are all `no_op_pass` tests
nothing, and a read-only task whose tests are all `no_op_fail` is probably mis-authored.

## E. Expected end state vs. expected actions

Almost everyone grades end state; the ones that grade actions say why.

- **Pure end state:** AppWorld, AgentDojo (utility over post_env), WebArena, OSWorld,
  WAA, WorkArena, MCPMark, Agent-Diff, WorkBench (correctness), τ-bench, τ²-bench's
  `DB` component.
- **Gold action list as a *means* of specifying the end state:** τ-bench, τ²-bench,
  WorkBench, BFCL. The gold calls are replayed on a fresh environment and the resulting
  state is the target. τ²-bench's docs give the rationale: "for many tasks, the target DB
  state is easier to express as 'play these actions on a fresh env' than to spell out by
  hand." This is a *judge-authoring convenience*, not a behavioural requirement — and
  τ²-bench had to write a whole documentation page because people kept misreading it.
- **Actions as a hard requirement:** τ²-bench `RewardType.ACTION`, used in 9 of 97
  `banking_knowledge` tasks and no airline/retail/telecom task, because it "promotes
  `actions` from 'one reference trajectory' to 'the only acceptable trajectory'".
  WorkBench's `is_exact_match` (a secondary metric) compares the sorted list of
  *side-effecting* calls only, deliberately ignoring read calls. BFCL's
  `method_invoke_order_checker` exists but is **commented out**.
- **Trace-based escape hatch:** AgentDojo's `utility_from_traces(..., traces)` exists
  "for tasks that do not leave a trace in the environment at the end of the execution" —
  the honest admission that end state alone cannot grade everything (a message sent and
  then deleted; a read that should have happened).
- **Neither:** CRMArena grades the answer string; TheAgentCompany grades per-checkpoint
  reads that happen to be state-ish.

## F. Partial credit

| benchmark | headline score | partial credit available? |
|---|---|---|
| τ-bench | 0/1, plus `pass^k` | no |
| τ²-bench | product of components (0/1 each) | `partial_action_reward` = m/n reference actions matched, diagnostics only |
| AppWorld | TGC (all tests pass) / SGC (all tasks in a 3-task scenario pass) | pass_count/num_tests is tracked and reported |
| AgentDojo | utility ∧ security, booleans | no |
| ToolSandbox | real-valued similarity in [0,1] | **yes, genuinely graded** — geometric mean within a milestone, arithmetic mean across milestones, best DAG-consistent assignment |
| WebArena | 0/1 (evaluators multiply) | no |
| OSWorld / WAA | float; `and` ⇒ mean of metrics, `or` ⇒ max | in principle; most metrics are 0/1 |
| BFCL | 0/1, first failure returned with a typed error | no |
| TheAgentCompany | weighted checkpoints | **yes** — `Checkpoint(total, result)`, sum by default, plus `bonus_for_completing_final` |
| WorkArena L2/L3 | per-subtask validation | yes, subtask-level |
| WorkBench | 0/1 + harmful-side-effect rate | three-way outcome classification |
| MCPMark | 0/1 (script exit) | no |
| Agent-Diff | binary `passed` | **yes, free** — `{passed, total, percent}` over assertions |

Two observations. First, **the benchmarks whose judge is a list of independent
assertions get partial credit for free** (AppWorld, TheAgentCompany, Agent-Diff);
the ones whose judge is a single equality get nothing (τ-bench, BFCL). Second, the only
benchmark with *smoothly* graded partial credit (ToolSandbox) paid for it with the most
complex evaluator in the survey — column similarity functions, Hungarian matching, DAG
topological-sort search — and it is not obvious the smoothness bought anything a
pass-count wouldn't.

## G. Authoring cost per task

Roughly cheapest to most expensive:

| model | example | cost |
|---|---|---|
| Parametric generator + one validator for N instances | WorkArena (33 validators → 19,912 L1 instances); τ²-bench telecom (a few `init_funcs`/`fix_funcs`/assertion builders → 2,285 tasks); AppWorld task generators (1 generator → N tasks sharing one `evaluation()`) | lowest per instance, high up-front |
| Gold action list only, no judge | τ-bench (115 retail + 50 airline test tasks) | very low, but requires a fully deterministic tool surface |
| Declarative JSON assertions | Agent-Diff (224 tasks; the assertion spec *is* the dataset label); WebArena (812 tasks) when an existing locator/helper fits | low — an annotator can write one |
| Declarative config over a shared metric library | OSWorld (369 tasks / **206 metric functions + 62 getters**); WAA (154 tasks, long tail of one-use metrics) | the config is cheap; the library is the real cost, ≈0.7 bespoke metric functions per task |
| Python judge with a diff API | AppWorld (~6-12 `test.case` lines per task); AgentDojo (5-20 lines per `utility`) | moderate |
| Bespoke Python program per task | TheAgentCompany (175 tasks, each a program against several service SDKs + a prose rubric); MCPMark (177 `verify.py`, 100-200 LOC each, each re-implementing set comparison) | highest |
| Python objects with polars fixtures | ToolSandbox (~12.5k lines of scenario code for 1,032 scenarios) | highest |

The lesson for a system that wants **hundreds of judges per world**: the two things that
actually reduce marginal cost are (a) a shared diff/query API so a judge is a few lines
rather than a program, and (b) making the judge *data* so it can be generated,
diffed, reviewed and stored. Agent-Diff has both. OSWorld has (a) but its "shared API"
grew into 206 functions because the config language couldn't express the checks.

## H. Durability — judging long after the episode ran

Only three benchmarks treat this as a first-class requirement:

- **AppWorld**: end-state DBs are written to disk during the run; `appworld evaluate` is
  a separate command. The task records `db_version` and `evaluate_task` **raises** if it
  doesn't match the package's `DB_VERSION`. The evaluation directory gets a
  `version.txt` containing `appworld.__version__`.
- **τ²-bench**: `tau2 evaluate-trajs [--fresh-tasks] <results.json>` re-grades saved
  trajectories. Results files **embed the task definitions** as they were at run time;
  `--fresh-tasks` reloads current ones by id and warns about ids that vanished. But the
  state itself is not saved, so re-grading means re-replaying, which is why
  `strict_replay=False` exists for historical runs.
- **AgentDojo**: suites are versioned (`get_new_version((1,2,2))`) and old versions stay
  importable, so a published score names a suite version.

And the governance practice worth copying, from the τ²-bench v1.0.1 CHANGELOG:

> **⚠️ Grading change — banking_knowledge scores are not comparable across this
> release.** … re-grading existing trajectories moves scores **only upward** … One
> task-data fix (task_074) corrects a gold refund value, so trajectories that reproduced
> the old, incorrect refund fail that task under 1.0.1. Scores produced with tau2-bench
> < 1.0.1 on `banking_knowledge` must not be compared against scores produced with
> >= 1.0.1. Old results files can be re-scored with `tau2 evaluate-trajs --fresh-tasks`.

They pin a git tag (`pre-v1.0.1`) so the old grading is reproducible, state the range of
commits over which grading is byte-identical, and say which direction scores move. That
is what "a judge is a long-lived artefact" looks like operationally. OSWorld does the
weaker version: the README tells you OSWorld-Verified results are not comparable to
pre-2025-07 results, with no re-grading path (the state was never saved).

## I. LLM judges, where they appear

Not as the primary grader anywhere that has a usable state check — which is itself the
finding.

- τ²-bench `NL_ASSERTION`: natural-language assertions judged by an LLM, labelled
  "experimental / WIP" in the enum docstring; empty lists short-circuit to 1.0.
- WebArena `fuzzy_match`: `llm_fuzzy_match(pred, ref, intent)` for free-text answers
  only — never for state.
- TheAgentCompany: `evaluate_with_llm(content, predicate)` — a natural-language
  predicate over content or an image; used by 31 of 175 tasks, always as one checkpoint
  among several.
- CRMArena `privacy_rejection`: an LLM scores the trajectory for confidentiality
  awareness.
- MCPMark explicitly rejects it: "hard-coded Python scripts to check the final state of
  the environment instead of using 'LLM-as-a-judge'" (paper, second-hand).

The pattern: LLM judging is used for *text the agent produced* and for *qualitative
process properties*, never for "did the row change". Where state is readable, everyone
reads it.
