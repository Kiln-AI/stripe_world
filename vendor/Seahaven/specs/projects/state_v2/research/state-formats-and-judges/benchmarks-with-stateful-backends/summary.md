# Benchmarks with Stateful Backends

## Bottom Line

Fifteen agent benchmarks that run against a database, an app or an OS were read at
source. Two clusters dominate: **whole-state equality** (τ-bench hashes the entire JSON
DB with SHA-256 and compares it to a hash produced by replaying a gold action list;
BFCL compares every public attribute of the backend objects) and **scoped reads**
(WebArena/OSWorld/WorkArena/MCPMark name a URL, file, record or SQL query and look only
there). Both are dead ends for a system that wants hundreds of cheap, attributable
judges per world: whole-state equality forces every tool to be deterministic and tells
you nothing about *what* differed; scoped reads cannot see collateral damage at all.
The third cluster — **row-level diff between start and end state** — is where the good
prior art is, and there are only three serious examples: **AppWorld** (start/end SQLite
DBs loaded into a pair, with `changed_model_names` / `changed_records` /
`changed_field_names` at table/row/column granularity), **ToolSandbox** (addition,
removal, update and guardrail similarities over DataFrame snapshots), and **Agent-Diff**
(2026), whose `DiffResult(inserts, updates, deletes)` with `__table__`-tagged rows and
whole `before`/`after` on updates is essentially identical to Seahaven's
`inst.changes()`, judged by a versioned JSON assertion DSL. Only AppWorld and Agent-Diff
persist final state as an artefact that outlives the process; everyone else grades
in-process or — τ²-bench, WorkBench, BFCL — **reconstructs state by replaying the
transcript**, which τ²-bench's own code documents as fragile enough to need a
`strict=False` escape hatch for re-grading old runs.

## Key Findings

- **Agent-Diff (arXiv 2602.11224, Feb 2026) is the closest published prior art and it is
  on GitHub.** Diff format: `{inserts: [row + __table__], updates: [{__table__, before,
  after}], deletes: [...]}`, produced either by diffing snapshot tables with
  `IS DISTINCT FROM` joins on the primary key, or by folding a Postgres CDC change
  journal. Judge format: a JSON document `{version, scenario, task, ignore_fields,
  strict, assertions[]}` validated against a shipped JSON Schema, where each assertion is
  `{diff_type: added|removed|changed, entity, where: {field: predicate}, expected_count,
  expected_changes: {field: {from, to}}}` over a closed 19-operator predicate set. 224
  tasks across Box/Linear/Slack/Calendar; **in the HuggingFace dataset the assertion spec
  is literally the label**. Full read: [state-diff-dsl.md](./state-diff-dsl.md).
  ([repo](https://github.com/agent-diff-bench/agent-diff))

- **AppWorld is the benchmark that states the diff philosophy outright** — "doing a
  *diff* between the start and end database states, and asserting that all expected and
  no unexpected changes are made … so that it did not cause any 'collateral damage'" —
  and it is the only other one that writes end-state DBs to disk for offline grading.
  Its judges are ~6-12 lines of `test.case(X, "op", Y)` over the diff API, and it labels
  every test `no_op_pass` / `no_op_fail` by whether a do-nothing agent passes it, which
  is a validation harness for the judges themselves.
  ([guide](https://github.com/StonyBrookNLP/appworld/blob/main/guides/developing_new_task_generators.md))

- **Side-effect checking is only reliable when it is the default, not a line the author
  remembers.** ToolSandbox auto-applies a `guardrail_similarity` to every database the
  milestone did *not* constrain; Agent-Diff's `strict: true` fails any `changed`
  assertion where the changed-column set isn't a subset of `expected_changes`. Compare
  AppWorld/AgentDojo, where it's one optional line, and WebArena/OSWorld/MCPMark/
  TheAgentCompany, where it doesn't exist and the defence is a full environment reset.
  WorkBench goes furthest and makes it a *reported metric*: every run is classified
  correct / failed-but-harmless / **harmful side effect**, over an explicit list of
  "damaging" tables (plots excluded, "creating a plot is harmless").

- **Irrelevant changes are handled six different ways, and the declarative one wins.**
  (1) τ-bench removes all nondeterminism from the tools — verified: zero uses of
  `datetime`/`random`/`uuid` in the retail and airline tool implementations. (2) AppWorld
  freezes the clock with `freezegun` for both the run and the evaluator. (3) BFCL hides
  fields behind a naming convention — `_compare_instances` skips `_`-prefixed attributes,
  which is exactly where `GorillaFileSystem` stashes
  `self._last_modified = datetime.now()`. (4) Agent-Diff declares `ignore_fields` at
  global/entity/assertion scope; the shipped lists name timestamps, concurrency tokens
  (`etag`, `sequence_id`, `sha1`, `file_version`) and derived fields
  (`path_collection`, `html_link`, embedded `created_by`). (5) ToolSandbox picks a
  similarity function per column (ROUGE-L for `content`, exact for timestamps, always-1
  to ignore). (6) WorkArena gives up: "We don't Check if there are extra fields that
  shouldn't be there. We could have issues matching other fields since calculation rules
  may have changed through time."

- **"Expected actions" is almost always a way to *specify* an expected end state, not a
  requirement on the agent.** τ²-bench replays `evaluation_criteria.actions` on a fresh
  env to derive the target DB hash, and had to write a whole docs page because the field
  name misleads: "for many tasks, the target DB state is easier to express as 'play these
  actions on a fresh env' than to spell out by hand." Hard action-matching
  (`RewardType.ACTION`) is used in 9 of 97 banking tasks and in none of
  airline/retail/telecom. BFCL's `method_invoke_order_checker` is commented out.
  ([docs/evaluation.md](https://github.com/sierra-research/tau2-bench/blob/main/docs/evaluation.md))

- **Replaying the transcript to recover state is a real, documented source of pain.**
  τ²-bench's `Environment.set_state` must skip hallucinated tool names, skip
  non-mutating tools "to avoid re-execution and non-deterministic output comparison
  issues", and ships `strict=False` because "recorded tool outputs contain cosmetic drift
  against current tool code (e.g. numeric argument echoes rendered as `25` … but `25.0`
  after numeric-argument normalization)". Persisting the state makes all of this
  disappear.

- **Judges as a list of independent assertions get partial credit for free.**
  Agent-Diff returns `{passed, score: {passed, total, percent}}`; AppWorld tracks
  pass/fail counts under a strict TGC headline; TheAgentCompany uses weighted
  `Checkpoint(total, result)` objects with pluggable scoring strategies. Judges that are
  a single equality (τ-bench, BFCL) get nothing. Only ToolSandbox has smoothly graded
  partial credit, and it cost the most complex evaluator in the survey (per-column
  similarity, Hungarian matching, DAG topological search) for unclear benefit.

- **Declarative config does not eliminate per-task code; it relocates it.** OSWorld's
  task JSON is a clean `{func, result-getter, expected-getter, options}` block — backed
  by **206 metric functions and 62 getters for 369 tasks**. Windows Agent Arena's metric
  histogram over 154 tasks is a long tail of one- and two-use functions. The config
  language stayed too weak, so the library grew. Agent-Diff avoided this by making the
  predicate set closed and small, and pays for it by being unable to express aggregations
  (its `aggregates` schema block is defined but not wired into the evaluator).

- **SQL-as-judge already ships at scale.** OSWorld's `run_sqlite3` metric pulls a SQLite
  file off the VM and runs one SQL predicate:
  `float(cursor.execute(rules["sql"]).fetchone()[0] or 0)` — e.g. a Thunderbird task
  whose entire success criterion is a `SELECT … = SELECT …` comparison. MCPMark's
  Postgres tasks are standalone `verify.py` scripts that query the live DB, scope to the
  rows the task should have touched (`WHERE "CustomerId" > 59`), and compare as sets so
  order doesn't matter.

- **Re-grading old runs needs the task definition versioned alongside the state.**
  τ²-bench embeds task definitions in results files and offers
  `tau2 evaluate-trajs --fresh-tasks` to swap in current ones; AppWorld raises if
  `task.db_version != DB_VERSION` and writes `appworld.__version__` beside every report;
  AgentDojo keeps every suite version importable. τ²-bench's v1.0.1 CHANGELOG is the
  model for how to announce a grading change: which domain, which direction scores move,
  which git tag reproduces the old behaviour, and an explicit "scores must not be
  compared".

- **A stateful backend does not imply state-based grading.** CRMArena runs against a real
  Salesforce org but scores `exact_match`/`fuzzy_match` on the agent's answer string,
  because its tasks are retrieval. WorkBench Revisited (2026) names the converse
  limitation: outcome-centric evaluation "can't grade pure-retrieval question answering
  that leaves the sandbox unchanged". Both halves of a judge contract are needed.

- **LLM judges never grade state.** Across all fifteen, LLM judging appears only for
  free-text the agent produced (WebArena `fuzzy_match`, τ²-bench `NL_ASSERTION` — marked
  "experimental / WIP"), for qualitative process properties (CRMArena
  `privacy_rejection`), or as one checkpoint among several (TheAgentCompany, 31 of 175
  tasks). MCPMark says so explicitly. Where state is readable, everyone reads it.

## Details

- [benchmark-profiles.md](./benchmark-profiles.md) — one section per benchmark
  (τ-bench, τ²-bench, AppWorld, AgentDojo, ToolSandbox, WebArena, OSWorld, Windows Agent
  Arena, BFCL v3/v4 multi-turn, CRMArena, TheAgentCompany, WorkArena, WorkBench, MCPMark,
  Agent-Diff): what state is read, the exact judge shape with quoted code, task counts,
  authoring cost, partial credit, side-effect handling. Read this for the detail on any
  one benchmark.
- [state-diff-dsl.md](./state-diff-dsl.md) — Agent-Diff's diff format, assertion schema,
  operator set, matching semantics, scoring, ignore-field practice, and a
  what-to-copy / what-to-avoid list. Read this first if you are designing the judge
  format; it is the nearest thing to a finished answer.
- [cross-cutting-patterns.md](./cross-cutting-patterns.md) — the comparative tables:
  what final state each harness reads and whether it persists; diff vs full end state vs
  scoped read; six strategies for irrelevant changes; side-effect mechanisms ranked by
  author burden; expected-state vs expected-actions; partial credit; authoring cost per
  task; durability and re-grading. Read this for the design tradeoffs rather than the
  per-benchmark facts.

## Open Questions / Gaps

- **Paper PDFs were unreachable.** This session's egress proxy blocked arxiv.org, ar5iv,
  aclanthology.org, openreview.net, alphaxiv, appworld.dev and xlang.ai; only
  github.com / raw.githubusercontent.com and the search tool were available. All
  code-level claims are first-hand from cloned repositories; task counts and design
  rationale that exist only in papers (AppWorld's 750/105/60/168/417 splits and ~50-LOC
  solutions, AgentDojo's 97 tasks / 629 security cases, ToolSandbox's 1,032 scenarios,
  MCPMark's 127 tasks, WorkBench's 690 tasks, Agent-Diff's 224 tasks and "state-diff
  contract" framing, OSWorld-Verified's ">300 issues fixed") are **second-hand via
  web-search summaries** and are labelled as such in the docs. The Agent-Diff paper in
  particular would be worth re-fetching from an unblocked network — it is the one paper
  whose argument (not just its numbers) is directly on-topic.
- **Agent-Diff's two diff producers may disagree.** The snapshot-join path yields a net
  diff; the change-journal path folds an ordered log into three buckets without
  coalescing, so insert-then-update of the same row appears as both. I found no
  reconciliation in the repo and no statement of which is canonical for the published
  benchmark. Seahaven's SQLite changeset is a net diff and corresponds to the snapshot
  path.
- **No benchmark ships an explicit versioned envelope around the state itself.** Several
  version the *judges* or the *task data* (AgentDojo suites, AppWorld `DB_VERSION`,
  Agent-Diff's DSL `version`, τ²-bench's embedded tasks + tags), but none stamps the
  saved state with a schema version, fixture identity, or diff-producer identity. That
  gap is where the diff-formats-and-contracts subtopic should be consulted.
- **Authoring-time cost in wall-clock hours is not published anywhere I could reach.**
  I inferred relative cost from lines of code, counts of bespoke metric functions per
  task, and generator-to-task ratios. No benchmark reports annotator-hours per task.
- **AppWorld's per-task evaluator code is not in the repo** (it ships with the downloaded
  data). Everything quoted comes from the authoring guide and README examples, which are
  detailed enough to be representative but are not the shipped tasks.
- Not researched, by scope: RL/eval *frameworks* (TRL, verifiers, SkyRL, Inspect) belong
  to the OpenEnv subtopic; general assertion languages (JMESPath, CEL, jq, JSONLogic)
  belong to the assertion subtopic; row-diff formats as formats (SQLite sessions, Dolt,
  Debezium, JSON Patch) belong to the diff-formats subtopic.

## Sources

Primary (cloned and read at HEAD, September 2026):

- [sierra-research/tau-bench](https://github.com/sierra-research/tau-bench) — `tau_bench/envs/base.py` (`get_data_hash`, `calculate_reward`), `tau_bench/types.py`, `envs/{retail,airline}/tools/`, `tasks_*.py`. Authoritative for whole-DB hashing and gold-action replay.
- [sierra-research/tau2-bench](https://github.com/sierra-research/tau2-bench) — v1.0.1 (2026-07-15) plus later `main`. `src/tau2/evaluator/`, `src/tau2/data_model/tasks.py`, `src/tau2/environment/environment.py`, `docs/evaluation.md`, `CHANGELOG.md`, `data/tau2/domains/*`. Authoritative for multi-component reward bases, env assertions, replay-based re-grading, and grading-change governance.
- [StonyBrookNLP/appworld](https://github.com/StonyBrookNLP/appworld) — `src/appworld/evaluator.py`, `src/appworld/collections/models.py` (`ModelCollectionPair`), `src/appworld/apps/lib/models/orm.py`, `guides/developing_new_task_generators.md`, `README.md`. Authoritative for start/end DB diffing at table/row/column granularity, frozen clocks, and `no_op_pass`/`no_op_fail`.
- [ethz-spylab/agentdojo](https://github.com/ethz-spylab/agentdojo) — `src/agentdojo/base_tasks.py`, `task_suite/task_suite.py`, `task_suite/task_combinators.py`, `default_suites/v1*/`. Authoritative for `utility(model_output, pre_env, post_env, strict)` and suite versioning.
- [apple/ToolSandbox](https://github.com/apple/ToolSandbox) — `tool_sandbox/common/evaluation.py` (1,279 lines), `tool_sandbox/scenarios/`, `README.md`. Authoritative for milestones/minefields, snapshot-diff similarity measures, guardrails and per-column similarity defaults.
- [web-arena-x/webarena](https://github.com/web-arena-x/webarena) — `evaluation_harness/evaluators.py`, `config_files/test.raw.json` (812 tasks). Authoritative for declarative `eval_types` / `program_html` locator configs.
- [xlang-ai/OSWorld](https://github.com/xlang-ai/OSWorld) — `desktop_env/desktop_env.py` (`evaluate`), `desktop_env/evaluators/{getters,metrics}/`, `evaluation_examples/examples/` (369 tasks), `README.md` (OSWorld-Verified note, 2025-07-28). Authoritative for the getter/metric/expected config pattern, `run_sqlite3`, and `_match_value_to_rule`.
- [microsoft/WindowsAgentArena](https://github.com/microsoft/WindowsAgentArena) — `src/win-arena-container/client/desktop_env/evaluators/`, `evaluation_examples_windows/examples/` (154 tasks).
- [ShishirPatil/gorilla](https://github.com/ShishirPatil/gorilla) — `berkeley-function-call-leaderboard/bfcl_eval/eval_checker/multi_turn_eval/multi_turn_checker.py`, `func_source_code/`, `data/BFCL_v4_multi_turn_*.json` (4×200 entries), `TEST_CATEGORIES.md`, `CHANGELOG.md`. Authoritative for `state_checker` / `_compare_instances` and the `_`-prefix ignore convention.
- [SalesforceAIResearch/CRMArena](https://github.com/SalesforceAIResearch/CRMArena) — `crm_sandbox/env/env.py`. Authoritative for answer-string grading over a live Salesforce org.
- [TheAgentCompany/TheAgentCompany](https://github.com/TheAgentCompany/TheAgentCompany) — `workspaces/tasks/*/{checkpoints.md,evaluator.py}` (175 tasks), `workspaces/base_image/scoring.py`, `common.py`. Authoritative for weighted checkpoints and `evaluate_with_llm`.
- [ServiceNow/WorkArena](https://github.com/ServiceNow/WorkArena) — `src/browsergym/workarena/tasks/form.py` (`validate`, `teardown`), `README.md`. Authoritative for API read-back of a single record, the `WORKARENA_BAD_FIELD_CHANGED` tripwire, and the explicit refusal to compare full records.
- [olly-styles/WorkBench](https://github.com/olly-styles/WorkBench) — `src/evals/evaluation.py` (`_states_match`, `is_correct`, `has_side_effects`, `SIDE_EFFECT_STATE_FIELDS`, `CASE_SENSITIVE_FIELDS`). Authoritative for harmful-side-effect classification.
- [eval-sys/mcpmark](https://github.com/eval-sys/mcpmark) — `tasks/*/*/*/verify.py` (177 scripts, incl. a Postgres suite). Authoritative for the "initial state + instruction + verification script" triplet.
- [agent-diff-bench/agent-diff](https://github.com/agent-diff-bench/agent-diff) — `backend/src/platform/evaluationEngine/{core,differ,assertion,compiler,models,replication}.py`, `dsl_schema.json`, `README.md`, `docs/evaluation-dsl.md`, `examples/*/testsuites/*.json`, `datasets/agent-diff-bench/*.jsonl` (224 tasks). Authoritative for the state-diff DSL.

Second-hand (paper/blog content reached only through web-search summaries; the sources
themselves were blocked by the egress proxy):

- AppWorld, arXiv [2407.18901](https://arxiv.org/abs/2407.18901) (ACL 2024) — 750 tasks / 250 scenarios, Train 105 / Dev 60 / Test-N 168 / Test-C 417; 9 apps, 457 APIs; TGC and SGC definitions; ~1.8 apps, 9.8 API calls, ~50 LOC per task.
- AgentDojo, [NeurIPS 2024 D&B](https://proceedings.neurips.cc/paper_files/paper/2024/hash/97091a5177d8dc64b1da8bf3e1f6fb54-Abstract-Datasets_and_Benchmarks_Track.html) — 97 user tasks, 629 security test cases, "formal utility checks computed over the environment state".
- ToolSandbox, arXiv [2408.04682](https://arxiv.org/abs/2408.04682) — 1,032 scenarios, 85% in at least one hard category.
- OSWorld-Verified, [xlang.ai blog](https://xlang.ai/blog/osworld-verified) (2025-07-28) — >300 task-quality and evaluation-reliability fixes; same 369 tasks; results not comparable to the earlier version.
- MCPMark, arXiv [2509.24002](https://arxiv.org/abs/2509.24002) (ICLR 2026) — 127 tasks, "initial state + task instruction + verification script" triplet, explicit rejection of LLM-as-a-judge for state.
- WorkBench, arXiv [2405.00823](https://arxiv.org/abs/2405.00823) (COLM 2024) — 5 databases, 26 tools, 690 tasks, "outcome-centric evaluation".
- WorkBench Revisited, arXiv [2606.13715](https://arxiv.org/abs/2606.13715) (June 2026) — GPT-4 43% correct / 26% harmful (2024) → Claude Opus 4.8 89% / 2.5% (2026); the "can't grade pure-retrieval QA" limitation.
- Agent-Diff, arXiv [2602.11224](https://arxiv.org/abs/2602.11224) (Feb 2026) — 224 tasks across Box / Linear / Slack / Google Calendar; the "state-diff contract" framing.
