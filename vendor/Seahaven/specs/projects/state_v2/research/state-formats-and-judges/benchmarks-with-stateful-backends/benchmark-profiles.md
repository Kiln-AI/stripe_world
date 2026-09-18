# Benchmark profiles: what state is read, and how a judge is written

One section per benchmark. Everything here was read from primary sources in
September 2026 — mostly the benchmarks' own source trees, cloned at HEAD. Where a
number comes from a paper or a press page rather than code, it is labelled.

A research constraint to declare up front: in this session the egress proxy allowed
`github.com` / `raw.githubusercontent.com` but **blocked arxiv.org, ar5iv,
aclanthology.org, openreview.net, alphaxiv, appworld.dev and xlang.ai**. So paper
PDFs could not be fetched directly. Anything sourced from a paper is cited to a
web-search result summary and flagged as second-hand; anything sourced from code is
first-hand and quoted.

---

## 1. τ-bench (Sierra, 2024) — whole-DB SHA-256 hash vs a replayed gold action list

Repo: <https://github.com/sierra-research/tau-bench>

**What the harness reads as final state.** The environment's entire in-memory
database — a plain `dict` of JSON loaded from `data/*.json` — hashed. From
[`tau_bench/envs/base.py`](https://github.com/sierra-research/tau-bench/blob/main/tau_bench/envs/base.py):

```python
def to_hashable(item):
    if isinstance(item, dict):
        return tuple((key, to_hashable(value)) for key, value in sorted(item.items()))
    ...
def consistent_hash(value) -> str:
    return sha256(str(value).encode("utf-8")).hexdigest()

def get_data_hash(self) -> str:
    return consistent_hash(to_hashable(self.data))
```

**How success is expressed.** Per task, a `Task` pydantic model
([`tau_bench/types.py`](https://github.com/sierra-research/tau-bench/blob/main/tau_bench/types.py)):

```python
class Task(BaseModel):
    user_id: str
    actions: List[Action]     # the gold tool calls
    instruction: str
    outputs: List[str]        # substrings the agent must say
```

There is **no per-task judge code**. `calculate_reward` reloads a fresh DB, replays
`task.actions`, hashes that, and compares:

```python
self.data = self.data_load_func()
for action in self.task.actions:
    if action.name not in self.terminate_tools:
        self.step(action)
gt_data_hash = self.get_data_hash()
info = RewardActionInfo(r_actions=data_hash == gt_data_hash, gt_data_hash=gt_data_hash)
if not info.r_actions:
    reward = 0.0
```

So the comparison target is an **expected end state derived by replaying expected
actions** — not the actions themselves. Any path that lands on the same DB passes.

**Partial credit / side effects.** None and everything. Reward is 1.0 or 0.0. Because
the *whole* DB is hashed, any write the agent should not have made flips the hash and
zeroes the task. Output checks are also all-or-nothing (a single missing substring
sets `reward = 0.0`). The headline reliability metric is `pass^k` (all of k
independent trials pass).

**Irrelevant / nondeterministic changes.** Handled by construction: the tools are
written to be pure. `grep -rn "datetime\|random\|uuid\|time()"` over
`tau_bench/envs/retail/tools/*.py` and `tau_bench/envs/airline/tools/*.py` returns
**zero hits**. New payment/transaction ids are derived from existing ids, not
generated. Whole-state hashing only works because every source of nondeterminism was
removed from the tool surface first. That is the price of the design.

**Scale / authoring cost.** Counted from source: retail `tasks_test.py` 115 tasks,
`tasks_train.py` 500, `tasks_dev.py` 20; airline `tasks_test.py` 50. Authoring a task
= write an instruction + a gold action list + output substrings. Cheap per task, but
it presupposes the gold trajectory is *achievable by replay*, and it gives you no
handle on "which part failed".

---

## 2. τ²-bench (Sierra, v1.0.1, July 2026) — the same hash, plus named env assertions, plus a replay problem

Repo: <https://github.com/sierra-research/tau2-bench> (cloned at HEAD 2026-09)

τ²-bench keeps the DB-hash idea and adds four other reward components. From
[`src/tau2/data_model/tasks.py`](https://github.com/sierra-research/tau2-bench/blob/main/src/tau2/data_model/tasks.py):

```python
class RewardType(str, Enum):
    DB = "DB"                        # predicted DB hash == target DB hash
    ENV_ASSERTION = "ENV_ASSERTION"  # named assertion functions on the env
    NL_ASSERTION = "NL_ASSERTION"    # LLM judge over natural-language assertions
    ACTION = "ACTION"                # each gold action matched by a tool call
    COMMUNICATE = "COMMUNICATE"      # substrings the agent must say
```

`EvaluationCriteria.reward_basis` lists which components gate the reward; the final
reward is their **product**, so it is still effectively binary per component. Fields
that are populated but not in `reward_basis` run as diagnostics.

**The `actions` field is not a requirement.** `docs/evaluation.md` is unusually
explicit about this, and it is the clearest statement I found anywhere of
end-state-vs-expected-actions grading:

> `evaluation_criteria.actions` is **one reference trajectory** that solves the task —
> not the only correct one. It is replayed on a fresh "gold" environment to derive a
> target DB end state, which is then compared (by hash) to the predicted environment's
> DB end state. The agent is **not** required to take this specific path.

> We keep this reference trajectory in the task because, for many tasks, the target DB
> state is easier to express as "play these actions on a fresh env" than to spell out
> by hand.

`RewardType.ACTION` (the "you must take exactly these calls" mode) is used in **9 of
97** `banking_knowledge` tasks and in **no** airline/retail/telecom task.

**Env assertions — the declarative-ish judge.** An `EnvAssertion` is a named function
on the toolkit plus arguments plus an expected boolean:

```python
class EnvFunctionCall(BaseModel):
    env_type: ToolRequestor    # "user" | "assistant"
    func_name: str
    arguments: dict

class EnvAssertion(EnvFunctionCall):
    assert_value: bool = True
    message: Optional[str] = None
```

Real example from `data/tau2/domains/telecom/tasks_small.json`:

```json
"env_assertions": [
  {"env_type": "user", "func_name": "assert_mobile_data_status",
   "arguments": {"expected_status": true}, "assert_value": true},
  {"env_type": "user", "func_name": "assert_internet_speed",
   "arguments": {"expected_speed": 200, "expected_desc": "excellent"}, "assert_value": true}
]
```

The assertion functions are ordinary Python on the toolkit (`assert_internet_speed`,
`assert_line_status`, `assert_no_overdue_bill`, `assert_number_of_tasks`, …) — 17 of
them across all domains by `grep -c "def assert_"`. This is the
**"named predicate + arguments + expected value"** judge shape, encoded in JSON, with
the predicate library living in the domain code.

**The replay problem — the most important finding in this doc.** τ²-bench does **not
persist final state**. `EnvironmentEvaluator.calculate_reward` reconstructs it by
replaying the recorded message history into a fresh environment:

```python
predicted_environment.set_state(
    initialization_data=..., initialization_actions=...,
    message_history=list(full_trajectory), strict=strict_replay)
```

`Environment.set_state` re-executes every *mutating* tool call from the transcript and
compares each replayed tool output against the recorded `ToolMessage`. When they
differ it raises — unless `strict=False`. The docstring says why that escape hatch
exists:

> Lenient mode is intended for re-grading historical trajectories whose recorded tool
> outputs contain cosmetic drift against current tool code (e.g. numeric argument
> echoes rendered as `25` by the code that produced them but `25.0` after
> numeric-argument normalization); the state mutation is applied identically either
> way.

Replay also has to special-case hallucinated tool names (skipped as no-ops) and skip
non-mutating tools "to avoid re-execution and non-deterministic output comparison
issues". **A design that stores the end state directly would not need any of this.**

**Re-grading old runs.** `tau2 evaluate-trajs --fresh-tasks <results.json>` re-scores
saved trajectories. Results files embed the task definitions as they were at run time;
`--fresh-tasks` swaps in the current ones by id, warning about ids that no longer
exist. The v1.0.1 CHANGELOG is a case study in why this matters:

> **⚠️ Grading change — banking_knowledge scores are not comparable across this
> release.** … Scores produced with tau2-bench < 1.0.1 on `banking_knowledge` must not
> be compared against scores produced with >= 1.0.1. Old results files can be re-scored
> with `tau2 evaluate-trajs --fresh-tasks <results.json>`.

They also pin a git tag (`pre-v1.0.1`) so the old grading can be reproduced, and state
that grading is byte-identical back to April 2026 commits.

**Docs/data mismatch worth knowing.** `docs/evaluation.md` says the default
`reward_basis` for airline/retail/telecom is `["DB","COMMUNICATE"]`. In the shipped
data (counted at HEAD): airline 50/50 are `["DB","COMMUNICATE"]`; **retail 112/114 are
`["DB","NL_ASSERTION"]`** and 2 are `["DB"]`; banking_knowledge 87/97 `["DB"]`, 9
`["ACTION"]`, 1 `["DB","NL_ASSERTION"]`; telecom (small split) 18/20
`["ENV_ASSERTION"]`, 2 `["ENV_ASSERTION","ACTION"]`. The retail case is benign —
those tasks have `nl_assertions: null` and `NLAssertionsEvaluator` returns 1.0 for an
empty list — so retail grades DB-only in practice. But the doc and the data disagree.

**Scale.** airline 50, retail 114, banking_knowledge 97 (one JSON file per task),
telecom 2285 in `tasks.json`/`tasks_full.json` (20 in `tasks_small.json`). The telecom
2285 are **programmatically generated**: `src/tau2/domains/telecom/tasks/*.py` defines
`BaseTask(name, description, init_funcs=[...], fix_funcs=[...])` plus
`assert_*_amount(env) -> list[EnvAssertion]` builders that compute expected values from
the live env (e.g. `expected_amount = original_data_refueling_gb + 2.0`) and get crossed
with personas. That is the cheapest per-task authoring model in this survey: write a
handful of fault-injection functions and assertion builders, get thousands of tasks.

---

## 3. AppWorld (Stony Brook, ACL 2024; repo at HEAD 2026) — the canonical *database-diff* benchmark

Repo: <https://github.com/StonyBrookNLP/appworld>

**This is the one benchmark that grades explicitly on a DB diff rather than the full
end state**, and it says so:

> At the core of our evaluation function is the idea of doing a *diff* between the
> start and end database states, and asserting that all expected and no unexpected
> changes are made. This allows us to robustly test that the model did what it was
> instructed to do and also that it did not cause any "collateral damage", e.g.,
> deleting my files when I asked to attach one to an email.
> — [`guides/developing_new_task_generators.md`](https://github.com/StonyBrookNLP/appworld/blob/main/guides/developing_new_task_generators.md)

**What the harness reads.** The end-state SQLite DBs are written to
`experiments/outputs/<experiment>/tasks/<task_id>/dbs` during the run.
`evaluate_task` in `src/appworld/evaluator.py` loads both the task's start DBs and
those end DBs into a `ModelCollectionPair`:

```python
models = ModelCollectionPair(
    start_db_home_path=models_start_db_home_path, start_model_collection=models_start,
    end_db_home_path=models_end_db_home_path_in_memory, end_model_collection=models_end)
```

State is therefore **persisted to disk and re-readable after the episode** — evaluation
is a separate CLI (`appworld evaluate <experiment> <dataset>`) from the run.

**The diff API.** Three levels, all on `ModelCollectionPair`
(`src/appworld/collections/models.py`):

| method | granularity | returns |
|---|---|---|
| `changed_model_names(include=, ignore=)` | table | `set` of `"{app}.{Model}"` that changed |
| `changed_records(model_name, updated_state="end")` | row | `(added_records, updated_records, removed_records)` |
| `changed_field_names(model_name, id, ignore=)` | column | `set` of field names that differ for that row |

Mechanics: each row carries a `record_hash` (base64 SHA over all properties except
`id`, `_db_home_path`, `record_hash`); each table carries a model hash. Added/removed
are matched **by primary-key id**, updated is `id in both AND record_hash differs`, and
there is a nice wrinkle — "if a record exists in both added and deleted (matched by
hash), they will cancel out each other and be removed from both", so a delete-then-
reinsert of the identical row is not reported as a change.

**How success is expressed.** A per-task Python module with an `evaluation(...)`
method taking `(test, public_data, private_data, main_user, models, ground_truth_answer,
predicted_answer)`. Assertions use `test.case(X, "op", Y)` — a tracked `assert` whose
op is one of `== > < >= <= != in "not in" is "is not"`, plus `is_truthy` / `is_falsy`,
plus `all`/`any` modifiers (`test.case(items, "all ==", value)`) and `test.subcases([...])`.
A canonical evaluator, verbatim from the guide:

```python
# assert model changes match wallet.Address.
test.case(models.changed_model_names(), "==", {"wallet.Address"})
# assert only 1 address is updated, and 0 added/removed.
added, updated, removed = models.changed_records("wallet.Address")
test.case(len(added), "==", 0)
test.case(len(updated), "==", 1)
test.case(len(removed), "==", 0)
# assert updated address matches private_data.address_id.
test.case(updated[0].id, "==", private_data.address_id)
# assert only the country field is changed
changed_field_names = models.changed_field_names("wallet.Address", private_data.address_id)
test.case({"country"}, "==", changed_field_names)
# assert updated country is correct.
test.case(updated[0].country, "==", public_data.new_country)
```

For a pure question-answering task the whole state judge is one line:
`test.case(models.changed_model_names(), "is_falsy")`. Comments above each
`test.case` block are load-bearing: "These comments are extracted and turned into a
description for test(s) programmatically" (use `##` for a real comment).

**Irrelevant changes.** Two mechanisms.
1. **Frozen clock.** `evaluate_task` calls `set_local_date_and_time(task.datetime)`
   (a `freezegun` `freeze_time`) before running the evaluator, and the task records
   its own `datetime`. Timestamps are deterministic because time itself is pinned.
2. **Global ignore list** baked into `_changed_model_names`: `supervisor.Task`,
   `admin.PaymentCard`, and `amazon.BrowsedProduct` are always appended to `ignore`
   ("This was added after V1 and no task requires updating it, so ignoring it globally
   for now"). Per-call `include=`/`ignore=` narrows further.

**Partial credit.** Reported but not the headline. `TestTracker` counts passes and
failures; `success = pass_count == num_tests`. Metrics are **TGC** (task goal
completion: % of tasks where *all* tests passed) and **SGC** (scenario goal completion:
% of 3-task scenarios where all tasks fully passed — `min(scores)` over the scenario).

**`no_op_fail` / `no_op_pass` labels.** Every test carries a label
(`src/appworld/common/types.py`): `TestData = {"requirement": str, "label":
Literal["no_op_fail","no_op_pass"]}`. `TestTracker.prepare_test_data` builds them from
two runs: the tests a do-nothing agent already passes are `no_op_pass` (these are the
*side-effect guards* — "nothing else changed"), the rest are `no_op_fail` (these
require real work). It's a validation harness for the judge itself: a task whose tests
are all `no_op_pass` is not testing anything.

**Scale.** 9 apps, 457 APIs, 100+ tables. 750 tasks from 250 scenarios (3 tasks each),
split Train 105 / Dev 60 / Test-Normal 168 / Test-Challenge 417; tasks average 1.8
apps, 9.8 API calls, ~50 lines of solution code (paper figures, via web search —
second-hand). Task *generators* are the unit of authoring: one generator produces N
tasks by sampling supervisors and data, and carries one `evaluation()` for all of them.
`task.db_version` is checked against a package `DB_VERSION` constant and raises on
mismatch; the evaluation directory records `appworld.__version__` in a `version.txt`.

---

## 4. AgentDojo (ETH SPY Lab, NeurIPS 2024 D&B) — `utility(model_output, pre_env, post_env)`

Repo: <https://github.com/ethz-spylab/agentdojo>

**State.** The whole environment is a pydantic model loaded from `environment.yaml`.
Before the run, `pre_environment = task_environment.model_copy(deep=True)`
(`task_suite/task_suite.py`). The judge gets both objects.

**How success is expressed.** Per-task Python classes (`base_tasks.py`):

```python
class BaseUserTask(abc.ABC, Generic[Env]):
    PROMPT: str
    def ground_truth(self, pre_environment: Env) -> list[FunctionCall]: ...
    def utility(self, model_output: str, pre_environment: Env,
                post_environment: Env, strict: bool = True) -> bool: ...
    def utility_from_traces(self, model_output, pre_environment, post_environment,
                            traces: Sequence[FunctionCall]) -> bool | None: ...
class BaseInjectionTask(...):
    def security(self, model_output, pre_environment, post_environment) -> bool: ...
```

`utility_from_traces` exists explicitly "for tasks that do not leave a trace in the
environment at the end of the execution" — the acknowledgement that end state alone is
insufficient for some tasks.

**Side effects and the `strict` flag.** Read-only tasks assert nothing changed, and
that assertion is *relaxable*:

```python
return pre_environment == post_environment or not strict
```

(20+ occurrences in `default_suites/v1/workspace/user_tasks.py` alone.) The relaxation
is used by `task_combinators.py`, which composes two user tasks and evaluates the
first with `strict=False` because the second legitimately mutates state:

```python
return user_task_1.utility(model_output, pre_environment, post_environment, strict=False) \
       and user_task_2.utility(model_output, pre_environment, post_environment, strict=True)
```

This is "the whole-environment equality check is right until you compose tasks, and
then you need an off switch."

**Scale.** Paper: 97 user tasks and 629 security test cases across 4 suites (via web
search — second-hand). In the repo I counted 86 `@task_suite.register_user_task` and 27
`@task_suite.register_injection_task` in `default_suites/v1`, with later versioned
overrides in `v1_1_2`, `v1_2`, `v1_2_1`. Per-task cost: a `ground_truth` list and a
hand-written `utility` — typically 5-20 lines.

**Versioning.** Suites are versioned as tuples (`workspace_task_suite.get_new_version((1,2,2))`)
and old versions stay importable, so a published score names a suite version. This is
the cleanest "judges are data with a version" practice in the survey.

---

## 5. ToolSandbox (Apple, 2024) — milestones and minefields over a *sequence* of DB snapshots

Repo: <https://github.com/apple/ToolSandbox>

**State.** Not one end state — a **series of snapshots**, one per database per message
index. Databases are polars DataFrames in named namespaces (`SANDBOX`, `SETTING`,
`CONTACT`, `MESSAGING`, `REMINDER`); a new snapshot row-set is materialised each time a
database changes, tagged with `sandbox_message_index`.

**How success is expressed.** A `Milestone` is a list of `SnapshotConstraint`s:

```python
@define
class SnapshotConstraint:
    database_namespace: DatabaseNamespace
    snapshot_constraint: SnapshotSimilarityMeasureType
    reference_milestone_node_index: Optional[int] = None
    target_dataframe: Optional[pl.DataFrame] = None
    column_similarity_measure: Optional[Dict[str, ColumnSimilarityMeasureType]] = None
```

The similarity measures are the interesting part — they are **diff operators against a
reference snapshot**, straight from the README:

> 1. `snapshot_similarity`: How close is a database to a target database
> 2. `addition_similarity`: … if the target database was derived from adding k target rows into a reference database
> 3. `removal_similarity`: … removing k target rows from a reference database
> 4. `update_similarity`: … updating k target rows in a reference database
> 5. `tool_trace_dependant_similarity`: … includes values extracted from the tool_trace of a reference database
> 6. `guardrail_similarity`: The database should be identical to a reference database, otherwise this similarity is 0

Implementation detail worth stealing: `addition_similarity` drops
`sandbox_message_index`, fills nulls, checks the reference is fully contained in the
snapshot, then anti-joins to isolate exactly the added rows and scores *those* against
the target — i.e. a diff computed by relational algebra, then matched row-to-target by
the Hungarian algorithm (`scipy.optimize.linear_sum_assignment`) so row order and
surrogate ids don't matter.

**Column-level matching.** Per column: `column_exact_match_similarity`,
`column_close_similarity` (numeric with an `atol_dict`), `column_contains_similarity`,
`column_rouge_l_similarity`, `column_tool_trace_exact_match_similarity` (AST match on a
tool call), `column_one_similarity` (always 1 — the explicit "ignore this column"
escape). There is a shipped **default per-column similarity table**: e.g. in
`MESSAGING`, `content` defaults to ROUGE-L while `creation_timestamp` defaults to exact
match; `openai_tool_call_id` and `openai_function_name` default to
`column_one_similarity`, i.e. ignored.

**Side effects — `guardrail_similarity`.** This is the best-engineered "the agent must
not have touched anything else" mechanism I found:

> One can enforce some database should not have changed comparing to reference. These
> constraints are called guardrails. By default, when `guardrail_database_list` is
> None, guardrail is applied to all non-SANDBOX databases that doesn't have an
> associated SnapshotConstraint in this milestone.

That is: **anything you did not write an assertion about is automatically asserted
unchanged**, with `guardrail_database_exclusion_list` as the opt-out. Compare AppWorld's
explicit `changed_model_names() == {...}` line — same intent, but ToolSandbox makes it
the default rather than a line the author must remember.

**Minefields.** `class Minefield(Milestone)` — "world state that absolutely shouldn't
happen in a trajectory. … If a minefield_dag matched with non-zero similarity, the
entire trajectory is nullified." Used for the Insufficient-Information category
(the agent should refuse, not hallucinate). Scoring:
`similarity = int(minefield_similarity == 0) * milestone_similarity`.

**Partial credit.** Genuine graded partial credit — real-valued. Milestone similarity
is the **geometric** mean of its constraints (so one exact-match zero kills the whole
milestone) and the overall score is the **arithmetic** mean over milestones, maximised
over all milestone→snapshot assignments consistent with a topological sort of the
milestone DAG.

**Scale / cost.** 1032 scenarios by domain experts, 85% in at least one hard category
(paper, via web search — second-hand). Authoring is expensive: scenarios are Python
`ScenarioExtension` objects carrying polars target DataFrames; the four scenario files
total ~12.5k lines, and `single_tool_call_scenarios.py` alone has 71
`Milestone(`/`SnapshotConstraint(` constructions in 1138 lines.

---

## 6. WebArena (CMU, 2023) — declarative JSON config, state read back through the live site

Repo: <https://github.com/web-arena-x/webarena>

**State.** No dump, no diff. The evaluator drives the live browser to a URL and
extracts a value with a JS expression or a Python helper.

**How success is expressed.** One JSON object per task in
`config_files/test.raw.json` (812 tasks). The `eval` block:

```json
"eval": {
  "eval_types": ["program_html"],
  "reference_answers": null,
  "reference_url": "",
  "program_html": [
    {"url": "__GITLAB__/byteblaze/dotfiles/-/project_members",
     "locator": "func:gitlab_get_project_memeber_role(__page__, 'abisubramanya27')",
     "required_contents": {"must_include": ["Guest"]}}
  ]
}
```

Distribution of `eval_types` over the 812 tasks (counted): `string_match` 325,
`program_html` 282, `url_match + program_html` 129, `url_match` 66,
`string_match + url_match` 10.

Three evaluators (`evaluation_harness/evaluators.py`): `StringEvaluator` (exact_match /
must_include / fuzzy_match via an LLM / ua_match), `URLEvaluator`, `HTMLContentEvaluator`.
`locator` is either a JS expression evaluated in the page (`page.evaluate(f"() => {locator}")`)
or an escape hatch `func:<python_helper>(...)` into `helper_functions.py`
(`shopping_get_latest_order_url`, `gitlab_get_project_memeber_role`, …).
`EvaluatorComb` **multiplies** scores, so it's binary in practice.

**Side effects / partial credit.** Neither. There is no "nothing else changed" check
at all; the defence is a full environment reset (Docker snapshot restore) between
tasks, with a per-task `require_reset` flag. Score is 0 or 1.

**Cost.** Cheap per task *if* an existing `locator` or helper fits; otherwise you write
a Python helper that speaks the site's API. The config is genuinely declarative and an
annotator can write one; the escape hatch is where the real work goes.

---

## 7. OSWorld (XLANG, NeurIPS 2024; "OSWorld-Verified" July 2025) — `getter + metric + expected` config

Repo: <https://github.com/xlang-ai/OSWorld>

**The shape.** Each task JSON has an `evaluator` block with up to five parts:

```json
"evaluator": {
  "postconfig": [ ...setup actions run before reading state... ],
  "func": "compare_table",
  "result":   {"type": "vm_file", "path": [...], "dest": [...], "multi": true},
  "expected": {"type": "cloud_file", "path": [...gold files on HF...], "multi": true},
  "options": {"rules": [{"type": "sheet_print", "sheet_idx0": "RNSheet1",
                         "sheet_idx1": "ENSheet1", "ignore_case": true}]}
}
```

`DesktopEnv.evaluate()` resolves `result` through a **getter** (62 of them:
`vm_file`, `cloud_file`, `rule`, `vm_command_line`, `accessibility_tree`, `history`, …),
resolves `expected` through another getter, and calls the **metric** named by `func`
(206 of them across `metrics/*.py`) as `metric(result_state, expected_state, **options)`.
`func` may be a *list*, in which case `result`/`expected`/`options` are parallel lists
and `metric_conj` is `"and"` (mean, short-circuiting to 0) or `"or"` (max).

**"Expected" is a fixture.** For file-producing tasks the target is a **gold artefact**
downloaded from HuggingFace, and the metric is a tolerant comparison
(`compare_table` with per-rule `ignore_case`, sheet selection, `approx:THRESHOLD`).

**SQL as a judge.** `metrics/general.py` has:

```python
def run_sqlite3(result: str, rules: Dict[str, Any]) -> float:
    connection = sqlite3.connect(result)
    cursor = connection.execute(rules["sql"])
    return float(cursor.fetchone()[0] or 0)
```

and a real task that uses it (`examples/thunderbird/dd84e895-….json`) pulls
Thunderbird's `global-messages-db.sqlite` off the VM and grades with one SQL predicate:

```json
"result": {"type": "vm_file", "path": ".../global-messages-db.sqlite", "dest": "global-messages-db.sqlite"},
"func": "run_sqlite3",
"expected": {"type": "rule", "rules": {"sql":
  "SELECT COALESCE((SELECT COUNT(*) FROM messageAttributes WHERE attributeID = 58 AND value = 1 AND messageID IN (SELECT id FROM messages WHERE folderID = 13)), 0) = (SELECT COUNT(*) FROM messages WHERE folderID = 13);"}}
```

This is the closest thing in the survey to "expression over the final DB + expected
truthy" as a shipped, scaled judge form.

**Generic rule matcher.** `metrics/utils._match_value_to_rule(value, rule)` takes
`{"method": str, "ref": V}` where method is `eq|ne|le|lt|ge|gt`, `re.<FLAGS>` (regex
search), `approx:<threshold>` (absolute tolerance), or domain-specific ones like
`spreadsheet_range`. `check_json` takes `{"expect": [{"key": [...path], "method":..., "ref":...}], "unexpect": [...]}`.

**Side effects / partial credit.** No systematic side-effect check; the VM is reverted
to a snapshot per task. Metrics return floats and multi-metric tasks average, so partial
credit is technically possible, but most metrics return 0.0/1.0. Tasks carry a
`"possibility_of_env_change": "low"` hint used for reuse decisions. `"func": "infeasible"`
tasks score 1 only if the agent emitted `FAIL`.

**Scale / cost.** 369 tasks (counted: 369 JSON files under `evaluation_examples/examples`).
The declarative part per task is small; the **206 metric functions and 62 getters
backing 369 tasks** is the real cost — roughly 0.7 bespoke metric functions per task.
OSWorld-Verified (2025-07-28, per the repo README) "fixed several issues reported by the
community … making the benchmark signals more effective"; the linked report claims >300
task-quality and evaluation-reliability fixes (second-hand, blog blocked). The README
warns results are not comparable across the change.

---

## 8. Windows Agent Arena (Microsoft, 2024) — OSWorld's evaluator architecture, ported

Repo: <https://github.com/microsoft/WindowsAgentArena>

Same `evaluator: {func, result, expected, options}` design, same getter/metric split
(`src/win-arena-container/client/desktop_env/evaluators/{getters,metrics}`), with
Windows-flavoured getters (`fileexplorer`, `msedge`, `settings`, `windows_clock`,
`microsoftpaint`). 154 task JSONs (counted).

Metric-function distribution over those 154 tasks (counted): `exact_match` 43,
`compare_table` 24, `infeasible` 13, `check_json_settings` 10, `is_extension_installed` 7,
then a long tail of one- and two-use functions (`check_history_deleted`,
`is_cookie_deleted`, `is_vlc_recordings_folder`, `check_qt_max_volume`, …). The tail is
the story: **most metric functions are written for one or two tasks**. Example:

```json
"func": "check_history_deleted",
"result": {"type": "history", "dest": "history.sqlite"},
"expected": {"type": "rule", "rules": {"type": "keywords", "keywords": ["youtube"]}}
```

---

## 9. BFCL v3/v4 multi-turn (Berkeley/Gorilla) — compare *Python object attributes* after replaying both sides

Repo: <https://github.com/ShishirPatil/gorilla> (`berkeley-function-call-leaderboard`)

**State.** The live backend objects. Each task names `involved_classes` (e.g.
`GorillaFileSystem`, `TwitterAPI`, `TradingBot`, `TicketAPI`, `VehicleControlAPI`,
`MessageAPI`, `TravelAPI`, `MathAPI`) and an `initial_config` JSON blob that constructs
them.

**How success is expressed.** No per-task judge. The ground truth is a list of function
calls *per turn*; both the model's calls and the gold calls are executed against
separate instances built from the same `initial_config`, and after each turn:

```python
def state_checker(model_instances: dict, ground_truth_instances: dict):
    """Checks if, after executing the function calls, the model_instance has the same state
    (defined by the attributes) as the ground_truth_instance."""
    ...
def _compare_instances(model_object, ground_truth_object):
    for attr_name in vars(ground_truth_object):
        # We don't check for private attributes
        if attr_name.startswith("_"):
            continue
        if getattr(model_object, attr_name) != getattr(ground_truth_object, attr_name):
            valid = False; differences[attr_name] = {...}
```

Plus `response_checker`: the gold turn's execution results must all appear (unordered)
among the model's results so far — "We don't need to enforce the order of the responses,
because many entries have parallel operations."

**Irrelevant changes — the underscore convention.** The `_`-prefix skip in
`_compare_instances` is exactly the timestamp escape hatch.
`func_source_code/gorilla_file_system.py` has
`self._last_modified: datetime.datetime = datetime.datetime.now()` — a real wall-clock
timestamp mutated on every write, kept out of the state comparison purely by naming it
with a leading underscore. `_api_description` and `_current_dir` are excluded the same
way. **Naming convention as an ignore-list** — cheap, invisible in the task data, and
it works only because the environment authors control the backend classes.

**Partial credit / side effects.** None. The checker returns the first failure with a
typed `error_type` (`multi_turn:instance_state_mismatch`,
`multi_turn:execution_response_mismatch`, `multi_turn:empty_turn_model_response`) and a
`differences` dict — good failure attribution, binary score. No explicit "should not
have changed" check, but comparing *all* public attributes to the gold instance is an
implicit total one: any extra write fails.

**Scale.** 200 entries each in `BFCL_v4_multi_turn_{base,long_context,miss_func,miss_param}.json`
= 800 multi-turn entries (counted lines; one JSON object per line). Authoring a task =
write `initial_config` + per-turn user messages + per-turn gold call lists. The
`method_invoke_order_checker` is present but **commented out** in the checker.

---

## 10. CRMArena / CRMArena-Pro (Salesforce, 2024/2025) — stateful backend, *answer*-based grading

Repo: <https://github.com/SalesforceAIResearch/CRMArena>

The important contrast case. The backend is a real Salesforce org (tasks run SOQL
against it), but the reward comes from the agent's **final answer string**, not the org
state (`crm_sandbox/env/env.py`):

```python
if reward_metric == "exact_match":
    cleaned_proposed = proposed_answer.strip().strip('"').strip("'")
    if cleaned_proposed == gt_answer[0]: return {"parsed_answer": [...], "reward": 1}
    parsed_answers = sorted(self.parse_answers(proposed_answer, task_name))
    if parsed_answers == sorted(gt_answer): reward = 1
elif reward_metric == "fuzzy_match":
    reward = get_all_metrics(proposed_answer, gt_answer[0])
elif reward_metric == "privacy_rejection":
    reward = self.compute_privacy_confidential_awareness_score(action_trajectory)
```

Task data carries `answer` + `reward_metric`. `privacy_rejection` is an LLM judge over
the action trajectory. No diff, no end state, no side-effect check — because the tasks
are overwhelmingly retrieval/analysis. Worth remembering: a stateful backend does not
force state-based grading, and if your tasks are read-only, state grading buys nothing
(cf. WorkBench Revisited's own admission that outcome-centric evaluation "can't grade
pure-retrieval question answering that leaves the sandbox unchanged").

---

## 11. TheAgentCompany (CMU, 2024/2025) — per-task `evaluator.py` with weighted checkpoints

Repo: <https://github.com/TheAgentCompany/TheAgentCompany>

**State.** Read back through each service's API at grade time — RocketChat, GitLab,
ownCloud, Plane — plus files in `/workspace`. No snapshot, no diff.

**How success is expressed.** Per task (175 task directories, counted): a
`checkpoints.md` describing the rubric in prose and an `evaluator.py`:

```python
@grader
def grade_checkpoint_1() -> bool:
    ...  # read /workspace/ans.txt, regex a number, compare to REFERENCE_ANSWER

@grader
def grade_checkpoint_2() -> bool:
    history = get_rocketchat_personal_chat_history(rocket_client, 'Chen Xinyi')
    return any(str(REFERENCE_ANSWER) in msg for msg in history)

def grade_checkpoints(trajectory="") -> Result:
    checkpoints = [Checkpoint(1, int(grade_checkpoint_1())),
                   Checkpoint(1, int(grade_checkpoint_2()))]
    return Result(checkpoints)
```

**Partial credit — the most explicit in the survey.** `Checkpoint(total, result)` with
integer weights; `Result.final_score` sums by default, and alternative strategies exist,
notably `bonus_for_completing_final` ("If the final checkpoint is completed successfully
(full score), award full points for all previous checkpoints"). Reported metrics are a
full-completion rate and a partial-completion score.

**LLM judges.** `common.evaluate_with_llm(content, predicate, ...)` — a natural-language
predicate over arbitrary content, including images. 31 of the 175 tasks reference an
LLM-judging helper (counted by grep).

**Side effects.** Not checked. Nothing asserts "the agent didn't also delete a repo".

**Cost.** High. Each task is a bespoke Python program against several service SDKs,
plus a prose rubric, plus Docker images. The README claims you can "Add new
tasks/evaluators/subcheckpoints in minutes", which is optimistic relative to the code.

---

## 12. WorkArena / WorkArena++ (ServiceNow, 2024) — API read-back of the created record, plus a live "protected field" tripwire

Repo: <https://github.com/ServiceNow/WorkArena>

**State.** The created/edited record, pulled from the ServiceNow Table API by `sys_id`
(stashed in the browser's `localStorage` by the task's instrumentation):

```python
record = table_api_call(instance=self.instance, table=self.table_name,
                        params={"sysparm_query": f"sys_id={sys_id}", "sysparm_display_value": True},
                        wait_for_record=True, max_retries=20)["result"]
record = {f: v if not isinstance(v, dict) else v["display_value"] for f, v in record[0].items()}
for f in self.task_fields:
    if record[f] != self.template_record[f]:
        return (0, True, error_msg, {...})
```

**The explicit refusal to compare full state** — the docstring on `validate` is worth
quoting, because it is precisely the "irrelevant / derived fields" problem:

> Caveat: we check only if the expected fields have the right value. We don't Check if
> there are extra fields that shouldn't be there. We could have issues matching other
> fields since calculation rules may have changed through time.

**Side effects — a live tripwire rather than a diff.** WorkArena injects client-side
JS that sets a flag when a field outside the task's scope is modified, and validation
reads it:

```python
protected_field_changed = page.evaluate("() => window.gsft_main.WORKARENA_BAD_FIELD_CHANGED")
if protected_field_changed:
    return (0, True, "", {"message": "Some fields outside of the task scope have been changed."})
```

**Cleanup instead of reset.** `teardown()` deletes every `sys_id` the task created
(`db_delete_from_table`). The backend is a long-lived shared instance, so tasks must
tidy up after themselves rather than relying on a snapshot restore.

**Scale.** README: "WorkArena-L1 includes `19,912` unique instances drawn from `33`
tasks"; "WorkArena++ contains 682 tasks, each one sampling among thousands of potential
configurations". So 33 hand-written validators cover ~20k instances — the cheapest
per-instance authoring model here, achieved by making tasks parametric. L2/L3 are
compositional, validated subtask-by-subtask, which gives natural partial credit.

---

## 13. WorkBench (COLM 2024; revisited June 2026) — gold actions replayed, states compared as row sets, side effects a first-class metric

Repos: <https://github.com/olly-styles/WorkBench>

**State.** Five pandas DataFrames (`calendar_events`, `emails`, `project_tasks`,
`crm_data`, `plots_data`). Both the agent's write-tool calls and the gold calls are
executed from a reset state and the resulting frames compared:

```python
def _states_match(predicted: pd.DataFrame, ground_truth: pd.DataFrame) -> bool:
    """Compares two final states as an unordered set of rows.
    Additive tools such as create_plot and send_email append rows in call order,
    so a correct answer that produces the same rows in a different order must
    still be counted as a match. Row order is therefore ignored."""
    if list(predicted.columns) != list(ground_truth.columns): return False
    if len(predicted) != len(ground_truth): return False
    return predicted.sort_values(by=columns).reset_index(drop=True).equals(
           ground_truth.sort_values(by=columns).reset_index(drop=True))
```

Strings are lowercased before comparison except for an explicit allowlist
`CASE_SENSITIVE_FIELDS = ["status", "list_name", "board"]` — a tiny but instructive
normalisation policy.

**Side effects as a reported metric.** Not just a pass/fail guard:

```python
SIDE_EFFECT_STATE_FIELDS = ("calendar_events", "emails", "project_tasks", "crm_data")

def has_side_effects(predicted_actions, correct: bool) -> bool:
    """Only the states in SIDE_EFFECT_STATE_FIELDS are compared: creating a plot
    is harmless, so plots_data is excluded."""
    ...
    state_changed = any(not _states_match(predicted_states[name], original_states[name])
                        for name in SIDE_EFFECT_STATE_FIELDS)
    return state_changed and not correct
```

So every run is classified **correct / failed-but-harmless / harmful side effect**, and
the harmful rate is headlined alongside accuracy. `WorkBench Revisited` (arXiv
2606.13715, June 2026, via web search — second-hand) reports GPT-4 at 43% correct /
26% harmful in March 2024 versus Claude Opus 4.8 at 89% / 2.5% in June 2026.

**Scale.** 5 databases, 26 tools, 690 tasks (paper, second-hand).

---

## 14. MCPMark (2025, ICLR 2026) — "initial state + instruction + verification script", including a Postgres suite

Repo: <https://github.com/eval-sys/mcpmark>

**Shape.** Every task directory is `{description.md, meta.json, verify.py}` plus any
fixtures. `verify.py` is a **standalone Python program** that connects to the live
backend and exits 0/1. The Postgres tasks are the closest analogue to a Seahaven world:

```python
def verify_migrated_customers(conn, expected_customers) -> bool:
    with conn.cursor() as cur:
        cur.execute('SELECT "FirstName", ..., "SupportRepId", "Fax" FROM "Customer" WHERE "CustomerId" > 59')
        actual_customers = cur.fetchall()
        if len(actual_customers) != len(expected_customers): return False
        ...
        if actual_tuples != expected_tuples:   # set comparison: order-independent
            missing_in_actual = expected_tuples - actual_tuples
            extra_in_actual = actual_tuples - expected_tuples
            ...
```

Expected rows come from a `customer_data.pkl` fixture beside the script. Note the
pattern: **scope the query to the rows the task should have touched** (`CustomerId > 59`),
compare as sets, and report both missing and extra. That is a hand-rolled diff over a
hand-scoped subset — no framework support, so every task re-implements it.

**Scale.** Paper: 127 tasks over Notion / GitHub / Filesystem / PostgreSQL / Playwright
(second-hand). The repo at HEAD has **177 `verify.py` files** across 8 environment
families (`filesystem, github, insforge, notion, playwright, playwright_webarena,
postgres, supabase`), so it has grown. No partial credit, no side-effect checking, no
ignore mechanism — each script is ~100-200 LOC of bespoke Python.

---

## 15. Agent-Diff (2026) — a JSON **state-diff DSL** over insert/update/delete rows

Repo: <https://github.com/agent-diff-bench/agent-diff> · paper arXiv 2602.11224 (blocked)

This is the closest published prior art to what Seahaven needs, and it deserves its own
read. Details in [state-diff-dsl.md](./state-diff-dsl.md). The headline:

- The diff is `DiffResult(inserts: list[dict], updates: list[dict], deletes: list[dict])`
  where inserts/deletes are whole rows tagged `__table__`, and updates are
  `{"__table__": t, "before": {...}, "after": {...}}` — i.e. **the same shape as
  Seahaven's `inst.changes()`**.
- It can be produced two ways: SQL snapshot tables diffed with `IS DISTINCT FROM` joins
  on the primary key, or replayed from a `ChangeJournal` (Postgres logical replication,
  ordered by `lsn`).
- The judge is a versioned JSON document: `{version, scenario, task, ignore_fields,
  strict, assertions[]}` with `diff_type ∈ {added, removed, changed}`, `entity`, `where`
  predicates, `expected_count` (exact or `{min,max}`), and `expected_changes`
  (`{field: {from, to}}`).
- `ignore_fields` is global + per-entity + per-assertion, and the shipped suites use it
  for exactly the fields you'd expect: `["created_at","updated_at","updatedAt",
  "createdAt","editedAt"]` (Linear), `["created_at","modified_at","etag","sequence_id",
  "sha1","file_version","created_by","modified_by","owned_by", …]` (Box).
- `strict: true` (default) makes any *unlisted* field change fail the assertion.
- Partial credit is `passed/total` assertions, alongside a binary `passed`.
- 224 tasks across Box / Linear / Slack / Google Calendar (counted in
  `datasets/agent-diff-bench/all_numbered.jsonl`; train 179 / test 45). In the HF
  dataset the **assertion spec is literally the label**: `{"question": <prompt>,
  "answer": "<the JSON assertion spec>"}`.
