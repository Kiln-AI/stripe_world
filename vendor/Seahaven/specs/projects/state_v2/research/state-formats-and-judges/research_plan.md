# Research Plan: State formats and judge systems for agent environments

## Goal

Inform the functional spec of the `state_format` project (`specs/projects/state_format/`): what
Seahaven's `state()` should return so that an external eval or RL framework can save it as
`final_state` and judge an episode against it, possibly with hundreds of judges per world and
possibly long after the episode ran. The spec is blocked on knowing (a) how existing agent
environments and benchmarks expose final state and write per-task judges, (b) what row-level diff
formats and versioned data contracts have proven durable, (c) which declarative assertion languages
over JSON are a good fit for a judge, and (d) what a tool-call record conventionally looks like.
Research feeds Step 2 (functional spec) of `/spec new_project`.

Seahaven context the sub-agents should know: a world is a SQLite-backed mock of a company's tool
surface; an instance is a private copy of a fixture; `inst.changes()` already renders the SQLite
session extension's changeset as `[{table, op: insert|update|delete, key, before, after}]` (net
diff since instance creation, not a log of calls); over the wire the environment is OpenEnv 0.4.x
with `CallToolAction` steps, and `state` today answers `{episode_id, step_count, fixture, now,
world}`. Two control tools (`controller_run_sql`, `controller_changes`) exist for evals and are to
be superseded by `state()`.

## Run

- Model: Opus (stepped down from the Fable tier per the skill's cost rule; covers the subtopic and
  summary agents)
- Web tools: `WebSearch`, `WebFetch`

## Subtopics

- [x] Benchmarks with stateful backends — how tau-bench, AppWorld, AgentDojo, ToolSandbox,
  WorkArena, OSWorld and peers expose final state and write per-task judges
- [x] OpenEnv and RL-framework consumers — what OpenEnv and the RL/eval frameworks that consume
  environments expect from `state`, rewards and saved episodes
- [x] Diff formats and versioned data contracts — row-level change representations and how
  long-lived structured outputs are versioned
- [x] Declarative assertion languages over JSON — Jinja2, JMESPath, jq, JSONPath, CEL, JSONLogic,
  SQL-over-JSON and how eval tools express assertions
- [x] Tool-call and trajectory records — conventional shapes for a tool call record and a
  trajectory, and how they cope with size

## Focus Details

### Benchmarks with stateful backends

Survey agent benchmarks whose tasks run against a stateful backend (a database, an app, an OS) and
answer, for each: what "final state" the harness reads after an episode (a DB hash, a diff, raw
tables, API getters), how a task's success is expressed (code per task, expected-state fixtures,
declarative config, LLM judge, milestones), how many tasks they have and how expensive authoring
one is, whether they compare against expected end state or expected actions, and how they handle
partial credit and side effects the agent should not have made. Cover at least: tau-bench and
tau2-bench (Sierra), AppWorld, AgentDojo, ToolSandbox (Apple), WorkArena, WebArena, OSWorld and
Windows Agent Arena (their getter/metric evaluator configs), BFCL v3/v4 multi-turn state checks,
CRMArena, TheAgentCompany, and any 2025-2026 successors. Note explicitly any benchmark that grades
on a *database diff* rather than the full end state, and any that has to deal with irrelevant
changes (e.g. timestamps, audit rows). Prior art in RL/eval *frameworks* (TRL, verifiers, SkyRL,
Inspect) belongs to the OpenEnv subtopic; assertion languages belong to the assertion subtopic.

### OpenEnv and RL-framework consumers

Establish what OpenEnv (Meta/PyTorch `openenv`, 0.4.x and later) specifies or conventionally does
for `state`, `reward`, graders and episode persistence: the `State` type, whether environments on
the Hugging Face hub expose richer state, how rewards or graders are attached (in-env vs external),
any "task"/"goal" abstraction that lets one environment carry many graded tasks, and how the
OpenEnv docs and examples say a trainer should consume state. Then look at how the RL and eval
frameworks that consume such environments treat environment state: TRL's environment/reward
interfaces, `verifiers` / Prime Intellect Environments Hub (rubrics, multi-reward functions), SkyRL,
Atropos, ART, veRL agent loops, and Inspect AI's sandbox/state model. Key questions: is final state
persisted between the rollout and the reward computation (and in what format), are rewards
computed from a serialized state or from a live environment, and how do these frameworks version
or schema their saved episodes. Benchmarks with their own backends belong to the benchmarks
subtopic; trajectory record shapes belong to the trajectory subtopic.

### Diff formats and versioned data contracts

Two halves. First, row-level change representations: SQLite's session extension and its changeset
and patchset forms (and `sqldiff`), Dolt's `dolt diff` and `dolt_diff_*` system tables and JSON
output, Debezium and other CDC change-event envelopes (`before`/`after`/`op`/`source`), Datomic's
transaction log, JSON Patch (RFC 6902) and JSON Merge Patch (RFC 7386), jsondiffpatch, and
git-style textual diffs. For each: what a change to one row looks like, how updates to a subset of
columns are represented (only changed columns vs whole rows), whether the format is a net diff or
an ordered log, how it identifies rows (primary key vs rowid), and how easy it is to query from a
generic language (JSON-friendly?). Second, versioning of long-lived structured outputs that
consumers save and re-read years later: JSON Schema `$schema`/`$id` conventions, CloudEvents
`specversion`, OpenTelemetry's `schema_url` and semantic-convention versioning, Stripe's API
version pinning, Avro and Protobuf compatibility rules, and any published guidance on "version
field plus formatter" designs. Assertion languages and trajectory shapes belong to other
subtopics.

### Declarative assertion languages over JSON

Compare candidate languages for writing hundreds of small judges over a JSON document: Jinja2
expressions and sandboxed Jinja, JMESPath, jq, JSONPath (RFC 9535), CEL (Common Expression
Language), JSONLogic, SQL over JSON (SQLite `json_each`/`json_tree`, DuckDB), and restricted Python
expression evaluators (`simpleeval`, `asteval`). For each: expressiveness for filtering, counting,
aggregating and joining lists (e.g. "number of changed rows in table X where status went from A to
B"), safety when the expression is untrusted or LLM-written, cross-language availability (Python
and TypeScript at least), readability by a non-programmer, and whether an LLM writes it reliably.
Then survey how eval tooling expresses assertions today: promptfoo assertion types, Inspect AI
scorers, OpenAI Evals YAML (`match`, `includes`, model-graded), Braintrust and DeepEval scorers,
LangSmith evaluators, and the `expected value + comparison operator` pattern generally. Answer
whether "expression + expected value + comparator" is a good judge shape, and what the expression
language should be able to do for a DB diff to be judgeable at scale. Benchmark-specific judges
belong to the benchmarks subtopic.

### Tool-call and trajectory records

Establish the conventional shape of a tool-call record and of a trajectory: OpenTelemetry GenAI
semantic conventions (tool spans, `gen_ai.tool.*` attributes, and the status of those conventions
as of 2026), OpenInference, LangSmith run schema, Inspect AI `EvalLog` transcripts, tau-bench's
action lists, the Anthropic and OpenAI message formats for `tool_use`/`tool_result`, and MCP's
`tools/call` request and result. For each: what fields a call carries (name, arguments, result,
error, ordinal, timing, ids), whether results are stored inline or referenced, and how size is
handled (truncation, sampling, size caps, hashing of large results). Also collect conventions for
aggregate counters beside the log (calls per tool, errors per tool, steps). The goal is a
recommendation for what Seahaven's per-call record and counters should contain and how to keep
the list bounded. Frameworks' reward interfaces belong to the OpenEnv subtopic.
