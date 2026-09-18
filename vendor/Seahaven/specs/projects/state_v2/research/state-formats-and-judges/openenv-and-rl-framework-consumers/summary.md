# OpenEnv and RL-framework consumers

## Bottom Line

OpenEnv specifies almost nothing about `state`: the base `State` is `{episode_id,
step_count}` with `extra="allow"`, the `/schema` endpoint advertises only that base shape
(there is no `state_cls` in `create_app`), `/state` exists only in simulation mode, and the
HTTP `GET /state` handler reads a throwaway environment rather than your session — the
session-scoped read is the WebSocket `{"type":"state"}` message. Rewards are explicitly the
environment's job ("The OpenEnv contract is that reward computation stays on the server
side, inside `Environment.step`"), and the `Rubric` that computes them receives
`(action, observation)` — **never state**. A census of all 28 in-repo environments shows a
median of 6 state fields, mostly episode bookkeeping and *identifiers for a live backend*
(`sandbox_id`, `session_id`, `workspace_path`); no shipped environment grades from state.
The one place OpenEnv does persist a final state is the harness collector, which writes
`artifacts["final_state"] = State.model_dump()` into a `results.jsonl` row with no version
field and no size cap — and TRL, its most prominent consumer, calls `session.verify(...)`
and reads only `env_reward`, discarding those artifacts. Across TRL, SkyRL, Atropos, ART
and veRL the universal pattern is that reward is read off a **live, in-process environment
object** (`env.reward`, `get_reward()`, `calc_reward(instance_id)`), and nothing serializes
world state. Only two counterexamples exist and both are recent and instructive: verifiers
v1's `IsolatedVerifierEnv` (tar the declared final-state delta, destroy the box, restore it
into a *fresh* container, run the graders there) and Inspect AI's `EvalSample.store` —
"State at end of sample execution" — which is the only thing a post-hoc `inspect score`
can see, because the re-scoring path rebuilds `TaskState` from the log with **no sandbox**.

## Key Findings

- **OpenEnv's `State` is two fields plus `extra="allow"`, and its schema endpoint lies about
  the rest.** `create_app()` takes `action_cls` and `observation_cls` but no `state_cls`, and
  `/schema` returns `State.model_json_schema()` — the base class. An external consumer can
  fetch the action and observation schemas but must import the environment's Python package
  to learn the real state shape. [details](./openenv-state-and-rewards.md#3-state-is-a-simulation-mode-only-route-and-the-http-one-is-not-session-scoped)

- **`GET /state` is not your session.** `get_state_handler` does
  `_env = self._env_factory(); return _env.state; finally: _env.close()`. The live read is
  the WebSocket `state` message, which is what `EnvClient.state()` uses. [details](./openenv-state-and-rewards.md#3-state-is-a-simulation-mode-only-route-and-the-http-one-is-not-session-scoped)

- **`/state` is gated on simulation mode**, alongside `/reset` and `/step`; production mode
  exposes only the MCP tool surface. `MCPEnvironment` also supports per-mode tool
  registration (`@self.tool(mode="simulation")`). This is OpenEnv's own precedent for
  Seahaven's "controller tools are for evals only" concern. [details](./openenv-state-and-rewards.md#3-state-is-a-simulation-mode-only-route-and-the-http-one-is-not-session-scoped)

- **Rubrics take `(action, observation)`, live inside the env, and compose like `nn.Module`.**
  RFC 004: "Rubrics are **server-side only**." Containers: `Sequential`, `Gate`,
  `WeightedSum`, `RubricList`, `RubricDict`, `LLMJudge`; hooks + `named_rubrics()` for
  trainer introspection; `TrajectoryRubric` for delayed rewards with CPU-only accumulation.
  **The RFC explicitly rejects a declarative grader format** — worth answering head-on in the
  Seahaven spec. [details](./openenv-state-and-rewards.md#4-rewards-live-inside-the-environment-and-rubrics-see-action-observation--not-state)

- **OpenEnv's answer to "one env, many graded tasks" is the Task API (v0.4.1, 2026-07-03) +
  `RubricDict`.** Five optional methods (`list_splits`, `list_tasks`, `num_tasks`,
  `get_task`, `get_task_range`) auto-exposed as `/{env_name}/…` routes, discovery-only,
  side-effect-free, must work on a fresh instance; a task is selected via
  `reset(split=..., index=...)`. **The task spec is deliberately untyped (`Any`) and carries
  no grader and no expected state.** [details](./openenv-tasks-and-episode-persistence.md#1-the-task-api--one-environment-many-tasks)

- **The only serialized OpenEnv final state is `EpisodeRecord.artifacts["final_state"]`.**
  `StepEnvSessionAdapter` calls `client.state()` after every tool call, stamps
  `_state_to_data(state)` into every tool result's metadata, and on verify writes
  `artifacts={"final_state": _state_to_data(state), "transcript_length": ...}`. The
  serializer is `state.model_dump()`; the container is a line in `results.jsonl`. **No
  version field, no schema, no size cap, no truncation.** Beware a naming collision:
  `session.verify(transcript, final_state=...)`'s `final_state` argument is a *rollout*
  summary, not env state. [details](./openenv-tasks-and-episode-persistence.md#2-the-one-place-openenv-persists-a-final-state-episoderecordartifacts)

- **Every RL trainer computes reward from a live object.** TRL: `reward_func(environments)`
  reads `env.reward`, or the env defines `get_reward()` and TRL auto-registers it as a
  reward column. veRL: `BaseTool.calc_reward(instance_id)` then `release(instance_id)`;
  dataset rewards are `compute_score(data_source, solution_str, ground_truth, extra_info)`.
  SkyRL: reward is a field of `BaseTextEnvStepOutput`; its OpenEnv example has
  `# self.env.state()` commented out. Atropos: the env returns `ScoredDataGroup` — tokens,
  masks, scores — so it does 100% of grading. ART: `Trajectory` has `reward`, `metrics`,
  `metadata` and no state; RULER judges messages only. [details](./rl-trainer-consumers.md)

- **TRL states the `state()`-vs-reward split better than anyone**: "`verify()` is the
  environment's job and answers one question, 'how correct was the outcome' ... The reward
  you train on is a separate, training-time decision ... `verify()` only inspects the final
  workspace, while `rollout_reward_fn` also gets the trajectory." That is the argument for
  Seahaven's design, made by the framework that would consume it. [details](./rl-trainer-consumers.md#1-trl--environment_factory-and-reward-from-the-live-env-object)

- **`verifiers` has been rewritten; the v0 `Rubric`/`State`-dict stack is gone.** In v1
  (HEAD 2026-09-14), `State` is a Pydantic model whose only base field is
  `artifacts: dict[str, bytes | None]`, and on the trace it is declared
  `state: StateT = Field(..., exclude=True)` — "**excluded from serialization**". What
  persists is `rewards: dict[str, Reward]` (score × weight) and `metrics: dict[str, float]`.
  [details](./verifiers-and-prime-intellect.md#2-state-is-a-tiny-artifact-bag--and-it-is-explicitly-not-serialized)

- **verifiers makes "needs a live box" a first-class, introspected property of a reward
  function.** `Task.score()` injects by parameter name from `{"task", "trace", "runtime"}`;
  a reward declaring `runtime: Runtime` with no default is *skipped with a log line* when
  scoring offline. Rewards/metrics are `@vf.reward(weight=...)` / `@vf.metric` methods, and
  run config can plug or override them by name. [details](./verifiers-and-prime-intellect.md#4-how-rewards-are-declared-decorated-methods-with-dependency-injection)

- **`IsolatedVerifierEnv` is the closest published design to Seahaven's goal** — and it
  chose *filesystem artifacts restored into a fresh container* over a serialized JSON state.
  Declared paths + a `/logs/artifacts` convention dir are tarred out (`MAX_ARTIFACT_BYTES =
  32 MiB`, "sized for a delta, not a tree"), the solver box is destroyed, a fresh box is
  provisioned, artifacts restored, `stage_verifier()` plants held-out graders, and the
  task's ordinary rewards run there onto the solver's trace. **A missing declared artifact is
  a hard error** because "grading a partial state scores the rollout wrong rather than
  failing it." [details](./verifiers-and-prime-intellect.md#6-isolatedverifierenv-grade-the-final-state-in-a-fresh-box)

- **verifiers also ships a declarative rubric-as-data format**, contra OpenEnv RFC 004:
  `Criterion{name, text, weight, choices}` loaded from a `.toml`/`.json` file, `choices`
  ordered worst→best and rank-normalized to [0,1], weights overridable per-name from run
  config, each criterion reported as `<judge>/<name>`. [details](./verifiers-and-prime-intellect.md#8-rubrics-as-data-criterion)

- **Inspect AI is the existence proof for judging from a saved state.**
  `EvalSample.store: dict[str, Any]` is documented as "State at end of sample execution",
  written through namespaced `StoreModel` Pydantic views (`ClassName:field` keys, defaults
  on every field). Scorers normally run *inside* the live `sandboxenv_context`, but
  `inspect score` rebuilds `TaskState` from the log with messages, output, metadata, store,
  events — **and no sandbox**. So: store-reading scorers are re-runnable forever;
  sandbox-reading scorers run once. [details](./inspect-ai-state-and-scorers.md#5-and-the-re-scoring-path-gets-no-sandbox-this-is-the-crux)

- **Three episode-versioning precedents worth copying.** verifiers: `TRACE_VERSION = 1` +
  `Trace.version` + `verifiers: VersionInfo` (producer build) + a permissive
  `WireEpisode = Episode[WireTaskData, State, WireAgentConfig]` whose unknown fields land in
  `model_extra`, plus a de-tensored `to_record()` projection. Inspect: `EvalLog.version:
  int = 2` with a header comment forbidding field reordering without a bump,
  `EvalSpec.task_version`, and `@model_validator(mode="before")` upgraders that lift legacy
  keys into new fields while leaving the old key in place so round trips are byte-stable.
  OpenEnv: none — `results.jsonl` rows carry no version at all.
  [details](./verifiers-and-prime-intellect.md#3-what-is-persisted-the-trace--episode-record-and-its-versioning), [details](./inspect-ai-state-and-scorers.md#6-log-versioning-and-forward-migration)

- **The stateful-backend environments that exist grade server-side and fail loudly.**
  OpenEnv's `tbench2_env` runs Terminal-Bench 2's canonical verifier in the container,
  reads the verdict from `/logs/verifier/reward.txt`, treats a missing verdict as an error
  (`reward=None` + `observation.error`, not 0.0), and withholds verifier assets from the
  agent as "reward-hacking bait". verifiers' Harbor taskset and agentic judge repeat the
  pattern ("Raises rather than scoring stale state"). [details](./openenv-state-and-rewards.md#5-how-a-stateful-openenv-environment-actually-grades-today-run-the-verifier-in-the-box)

- **Named signals, not a verdict scalar, is what every consumer can absorb.**
  `dict[str, Reward]` (verifiers), `dict[str, Score]` (Inspect, incl. one scorer emitting a
  dict of sub-scores with per-key metrics), `dict[str, float]` (SkyRL `get_metrics` + a
  paired `aggregate_metrics` static method, ART `metrics`), one column per reward function
  (TRL). Inspect additionally distinguishes three outcomes — *not applicable* (`return
  None`), *unscorable* (`Score.unscored()`, NaN, excluded from metrics), and *scored zero* —
  which is exactly the resolution a DB-diff judge needs.

## Details

- [OpenEnv: the `State` type, the `/state` surface, and where rewards live](./openenv-state-and-rewards.md)
  — release timeline by git tag; the verbatim `State`/`Observation`/`Environment`
  definitions; a field census of all 28 in-repo environment `State` subclasses; the three
  `/state` surprises (simulation-only, factory-scoped, base-class schema); RFC 004 rubrics
  in full including the rejected-declarative-format argument, `RubricDict` multi-task
  dispatch, and `TrajectoryRubric`; how `tbench2_env` actually grades; the `openenv.yaml`
  manifest and what it does *not* declare. Read this first if you want the OpenEnv contract.

- [OpenEnv: the Task API, and the one place OpenEnv persists a final state](./openenv-tasks-and-episode-persistence.md)
  — the full Task API (protocol, HTTP routes, error semantics, the two easy-to-miss rules,
  the silent-kwarg-drop footgun); `EpisodeRecord` / `results.jsonl` schema; exactly where
  `artifacts["final_state"]` comes from and how it is serialized; the `final_state` naming
  collision; the enforced "rewards in env" invariant; `EvalHarness`; the unrelated
  `openenv.validation.graders`. Read this for the persistence format and the many-tasks
  abstraction.

- [`verifiers` / Prime Intellect: rubrics, state, and the isolated verifier](./verifiers-and-prime-intellect.md)
  — the v0→v1 rewrite warning; `State`'s artifact bag and `exclude=True`; the full `Trace`
  record and its four versioning mechanisms; `@vf.reward`/`@vf.metric` dependency injection
  and the offline/live split; the artifact collect/restore protocol with its size cap and
  fail-loud rule; `IsolatedVerifierEnv` and `AgenticJudgeEnv` in full; `Criterion` rubric
  files; both directions of the verifiers↔OpenEnv bridge. **Read this one if you read only
  one.**

- [RL trainers as state consumers: TRL, SkyRL, Atropos, ART, veRL](./rl-trainer-consumers.md)
  — each framework's environment/reward interface verbatim, what it persists, and the
  cross-cutting conclusions. Includes TRL's `environment_factory` contract, the
  `HarnessRolloutOutcome` fields, the `verify()` design note, and proof that TRL discards
  OpenEnv's `artifacts`.

- [Inspect AI: `TaskState`, the `Store`, sandbox-backed scorers, and re-scoring a log](./inspect-ai-state-and-scorers.md)
  — `TaskState` and `StoreModel` namespacing; `EvalSample.store` as "State at end of sample
  execution"; attachment de-duplication and the `summary()` projection for size; the `Score`
  record and the not-applicable / unscorable / zero trichotomy; source-level proof that
  scorers run inside the sandbox context and that `inspect score` does not; log versioning
  and the `mode="before"` migration pattern.

## Open Questions / Gaps

- **Hugging Face is egress-blocked in this session.** `huggingface.co` returns 403 from the
  proxy for both WebFetch and curl, so I could not inspect published environment Spaces,
  the Environments Hub listings, `huggingface.co/docs/openenv`, or the
  `openenv-agentic-rl` blog post directly. Mitigation: the Spaces are built from the
  `envs/<name>/` trees in the OpenEnv repo, which I read in full (28 environments censused),
  and the rendered docs are built from `docs/source/`, which I also read in full. I judge
  the hub-side claims well-covered, but the *specific* question "does any hub-only
  environment expose richer state than the in-repo ones?" is unverified.
- **I could not read the GitHub REST API or clone via codeload** (the session proxy gates
  `api.github.com` and `codeload.github.com` to allow-listed repos). Release *notes* text
  therefore comes from one WebFetch of the rendered releases page, whose dates were wrong
  (it reported 2024 for 2026 tags); I replaced all dates with `git log` on the tags. I did
  not try to recover the prose of individual release notes beyond feature-arrival-by-tag,
  which I established by diffing file presence across tags.
- **Prime Intellect's Environments Hub as a *product*** (how envs are published, whether the
  hub stores episode records, what its API returns) is not covered — `app.primeintellect.ai`
  was not fetched. Everything here about verifiers is from the open-source library.
- **`verifiers` version number** — the clone is shallow and the package uses hatch-vcs, so I
  could not read the released version string. I have dated it by HEAD (2026-09-14) and by
  the presence of the `v1`-only layout.
- **prime-rl** (the trainer that consumes verifiers v1) was not examined; I inferred the
  trainer-side consumption from `verifiers/v1/episode.py`'s `TrainRunInfo`/`PolicySpan` and
  `docs/v1/evaluation.md`. If the spec needs to know how a trainer reads `traces.jsonl`, that
  is the next repo to read.
- **No evidence either way on whether anyone has built a declarative assertion language over
  an OpenEnv `state()`.** I looked; I found RFC 004 arguing against declarative rubrics and
  verifiers' `Criterion` files as the only rubric-as-data precedent, and that is LLM
  criteria rather than deterministic assertions. The assertion-language subtopic owns the
  positive case.

## Sources

Primary sources are local clones made 2026-09-14; HEAD SHAs are recorded so claims are
reproducible.

- [meta-pytorch/OpenEnv](https://github.com/meta-pytorch/OpenEnv) — `da59295`, 2026-09-10,
  `version = "0.4.3.dev0"`. Authoritative for the `State`/`Action`/`Observation` types, the
  HTTP/WS server surface, `rfcs/004-rubrics.md`, `rfcs/005-agentic-harnesses`, the Task API,
  `core/harness/collect.py`, and all 28 `envs/`. Tags v0.2.1 (2026-02-04) → v0.4.2 (2026-09-09).
- [PrimeIntellect-ai/verifiers](https://github.com/PrimeIntellect-ai/verifiers) — `f9121b8`,
  2026-09-14. Authoritative for the v1 `State`/`Trace`/`Episode`/`Task`/`Judge` model, the
  artifact protocol, `IsolatedVerifierEnv`, `AgenticJudgeEnv`, and `Criterion`. **Note the
  v0 stack has been removed**, so older writing about verifiers does not apply.
- [huggingface/trl](https://github.com/huggingface/trl) — `5354bc7`, 2026-09-14.
  `docs/source/openenv.md` is authoritative for the `environment_factory` contract and the
  `verify()`-vs-`rollout_reward_fn` design note; `trl/trainer/grpo_trainer.py` and
  `trl/experimental/async_grpo/openenv_harness.py` for the implementation.
- [UKGovernmentBEIS/inspect_ai](https://github.com/UKGovernmentBEIS/inspect_ai) — `9525af8`,
  2026-09-14. Authoritative for `TaskState`, `Store`/`StoreModel`, `EvalSample.store`,
  `EvalLog.version = 2`, scorer-inside-sandbox, and the sandbox-less `inspect score` path.
- [NovaSky-AI/SkyRL](https://github.com/NovaSky-AI/SkyRL) — `8f9a3e6`, 2026-09-14.
  `skyrl-gym/skyrl_gym/envs/base_text_env.py` and `examples/train_integrations/openenv/env.py`.
- [NousResearch/atropos](https://github.com/NousResearch/atropos) — `296a0bc`, 2026-07-04
  (noticeably staler than the rest). `atroposlib/envs/base.py`.
- [OpenPipe/ART](https://github.com/OpenPipe/ART) — `2ebfc1c`, 2026-09-12.
  `src/art/trajectories/__init__.py`, `src/art/rewards/ruler.py`.
- [volcengine/verl](https://github.com/volcengine/verl) — `753aed3`, 2026-09-14.
  `verl/tools/base_tool.py`, `verl/experimental/agent_loop/agent_loop.py`,
  `docs/preparation/reward_function.rst`, `docs/advance/reward_loop.rst`.
- [OpenEnv releases page](https://github.com/meta-pytorch/OpenEnv/releases) — fetched for
  release-note prose; **its dates as summarized were wrong** (2024 for 2026 tags), so all
  dates above come from `git log` on the tags instead.
