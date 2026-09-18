# `verifiers` / Prime Intellect Environments Hub: rubrics, state, and the isolated verifier

**Source:** `PrimeIntellect-ai/verifiers` @ `f9121b8e4ef8ec284c07961b499f646bcf92fac6`
(2026-09-14 — i.e. today's HEAD), cloned from <https://github.com/PrimeIntellect-ai/verifiers>.
Version is derived from git tags via hatch-vcs; the clone is shallow so I could not read the
tag, but the package layout dates it precisely — see §1.

> **Read this first if you know the old verifiers.** Almost every blog post, tutorial and
> secondhand description of `verifiers` describes the v0 stack: `vf.Environment`,
> `vf.MultiTurnEnv`, `vf.Rubric(funcs=[...], weights=[...])`, and a `State` that was a plain
> `dict` you mutated during the rollout and that reward functions received as a `state=`
> kwarg. **That stack has been removed.** `verifiers/__init__.py`, verbatim:
>
> ```
> """The verifiers package root.
>
> The v1 stack lives in `verifiers.v1` (`import verifiers.v1 as vf`). The
> classic v0 stack (`verifiers.legacy`, which also answered at its historical
> top-level paths — `verifiers.envs`, `verifiers.types`, ...) has been removed.
> """
> ```
>
> Everything below describes v1, which is what the Environments Hub and prime-rl run now.

---

## 1. The v1 object model

| concept | v1 type | file |
|---|---|---|
| the immutable row | `TaskData` (frozen Pydantic) | `verifiers/v1/task.py` |
| behavior for a row | `Task[DataT, StateT, ConfigT]` | `verifiers/v1/task.py` |
| a dataset of tasks | `Taskset` | `verifiers/v1/taskset.py` |
| the multi-agent shape | `Env[EnvConfigT]` with `run()` / `finalize()` | `verifiers/v1/env.py` |
| one agent's run | `Trace[DataT, StateT, AgentConfigT]` | `verifiers/v1/trace.py` |
| the whole artifact | `Episode[DataT, StateT, AgentConfigT]` | `verifiers/v1/episode.py` |
| live rollout state | `State` | `verifiers/v1/state.py` |
| LLM graders | `Judge`, `RubricJudge`, `Criterion`, `ReferenceJudge` | `verifiers/v1/judge.py`, `judges/` |

## 2. `State` is a tiny artifact bag — and it is explicitly **not serialized**

Whole file, `verifiers/v1/state.py`:

```python
"""Mutable rollout-level state shared across tool server + host."""

from pydantic import BaseModel, ConfigDict, Field
from typing_extensions import TypeVar

from verifiers.v1.utils.generic import concrete_type


class State(BaseModel):
    model_config = ConfigDict(ser_json_inf_nan="constants")

    artifacts: dict[str, bytes | None] = Field(default_factory=dict)


StateT = TypeVar("StateT", bound=State, default=State)


def state_cls(cls: type) -> type[State]:
    """Resolve a class's `State` specialization through its MRO, else `State`."""
    return concrete_type(cls, State) or State
```

One base field: `artifacts`, a dict of **tar archives keyed by source path**. A task
subclasses `State` to add its own fields.

And on the trace (`verifiers/v1/trace.py`, line ~428):

```python
    state: StateT = Field(default_factory=State, exclude=True)
    """Runtime (possibly, non-serializable) state shared across runtimes; excluded from serialization."""
```

**`exclude=True`.** This is the single most load-bearing fact in this doc for Seahaven's
question. In verifiers v1:

- state is a **live, in-process object** shared between the rollout, the tool servers and
  the scoring functions;
- state is **never written to `traces.jsonl`**;
- what *is* persisted is the scoring output — `rewards`, `metrics`, and `info` — plus the
  message graph.

The consequence is that a verifiers episode record cannot be re-graded later against the
world it ran in. If you want a signal, you compute it while the rollout's state (and
usually its runtime) is still alive, and you record the *number*, not the state.

## 3. What is persisted: the `Trace` / `Episode` record, and its versioning

`Trace` fields that survive to disk (`verifiers/v1/trace.py`):

```python
TRACE_VERSION = 1
"""Current version of the trace schema."""

class Trace(BaseModel, Generic[DataT, StateT, AgentConfigT]):
    version: int = TRACE_VERSION
    """The trace schema this trace serializes as."""
    id: str = ...
    verifiers: VersionInfo = Field(default_factory=_current_build)
    """The verifiers version that produced this trace."""
    task: TraceTask[DataT]
    agent: AgentInfo[AgentConfigT]
    tools: list[Tool] = ...
    nodes: list[MessageNode] = ...          # the message graph
    calls: list[ModelCall] = ...            # every model call
    rewards: dict[str, Reward | None] = ...
    """Named, weighted rewards; `None` means scoring didn't run (e.g. because of a
    preceding error)."""
    metrics: dict[str, float | None] = ...
    """Unweighted, named metrics; `None` as in `rewards`."""
    info: dict[str, Any] = ...
    """Scratch space for task-specific metadata."""
    state: StateT = Field(default_factory=State, exclude=True)
    is_completed: bool = False
    ok: bool = False
    stop_condition: str | None = None
    errors: list[Error] = ...
    timing: Timing = ...
```

with

```python
class Reward(BaseModel):
    score: float
    weight: float = 1.0

    @property
    def value(self) -> float:
        return self.score * self.weight
```

and `Trace.reward` = `sum(r.value for r in self.rewards.values() if r is not None)`.

**Three versioning mechanisms worth stealing:**

1. `version: int = TRACE_VERSION` — an explicit schema version stamped on every record.
2. `verifiers: VersionInfo` — the *producer's* version, recorded alongside.
3. A **wire form** that parses an old record without the producing packages:

```python
WireEpisode = Episode[WireTaskData, State, WireAgentConfig]
"""Record loader for consumers without the run's packages: unknown task fields
survive in `task.model_extra`, agent configs parse loose (`WireAgentConfig`)."""
```

`WireTaskData(TaskData)` is simply `model_config = ConfigDict(extra="allow")`. That is the
whole trick: a strict typed model for producers, a permissive twin for the archive reader,
with the unknown fields parked in `model_extra`.

4. A **de-tensored JSON projection** distinct from the wire form:

```python
    def to_record(
        self, float_decimals: int | None = RECORD_FLOAT_DECIMALS
    ) -> dict[str, Any]:
        """JSON record without raw trace tensors, which remain on the msgpack wire.
        Per-token float streams are rounded to `float_decimals` (`None` keeps every digit)."""
```

Excluded on disk but kept on the msgpack wire: `multi_modal_data`, `routed_experts`,
`sampling_mask` (the `EXCLUDE_FIELDS` dict). So the project runs two serializations of the
same model: a lossless internal wire and a lossy, human-readable archive record.

**On-disk layout** (`docs/v1/evaluation.md`):

> The output from evaluations are written into `outputs/<env>--<model>--<harness>/<uuid>/`
> by default ... The folder contains the used `config.toml`, all the episodes in
> `traces.jsonl`, as well as logs of the run and workers in `logs/attempt_<n>/eval.log`

`--resume <output-dir>` re-runs only the missing/errored rollouts and appends to the same
`traces.jsonl`, reloading the saved `config.toml` verbatim.

## 4. How rewards are declared: decorated methods with dependency injection

Reward and metric functions are **methods on the `Task`**, marked with decorators
(`verifiers/v1/utils/decorators.py`):

```python
def reward(func=None, weight: float = 1.0, priority: int = 0):
    """Mark a weighted `Task` reward returning a float or keyed scores — per-trace
    judgement over the trace's own run. Cross-agent judgement is an
    `Env`'s `finalize()`, imperatively."""

def metric(func=None, priority: int = 0):
    """Mark a `Task`/`Harness` metric `(self, trace) -> float` (recorded, not
    summed) — per-trace judgement; it declares what it needs by name (`task`,
    `trace`, `runtime`). Cross-agent judgement is an `Env`'s `finalize()`,
    imperatively."""
```

`Task.score()` collects them and injects by parameter name:

```python
        judges = self.plugged_judges()
        available = {"task": self.data, "trace": trace}
        if runtime is not None:
            available["runtime"] = runtime
```

A reward returning a `Mapping` fans out into several named rewards; a scalar is recorded
under the function's own name. Weight comes off the decorator (`fn._vf_weight`). Functions
are sorted by `(-priority, name)`. Config can *plug* extra reward functions by name
(`TaskConfig.rewards`), and a plugged function with the same name replaces the decorated
method — so rubric composition is partly declarative (TOML config) over code.

### Offline vs live is an explicit, first-class distinction

This is the direct answer to "are rewards computed from a serialized state or from a live
environment": **verifiers supports both and detects which by introspection.**

```python
        def requires_runtime(fn) -> bool:
            param = inspect.signature(fn).parameters.get("runtime")
            # A defaulted runtime parameter can still be called offline with None.
            return param is not None and param.default is inspect.Parameter.empty
        ...
            if runtime is None:
                skipped = [
                    fn.__name__ for fn in (*metrics, *rewards) if requires_runtime(fn)
                ] + [
                    judge.reward_name
                    for judge in judges
                    if requires_runtime(judge.score)
                ]
                if skipped:
                    logger.info(
                        "score: no runtime — skipped runtime-dependent signals: %s",
                        skipped,
                    )
```

A reward function that *requires* a live sandbox declares `runtime: Runtime` with no
default, and is **skipped with a log line** when scoring offline. A reward that can work
from the trace alone is called either way. Note what this implies: verifiers does not try
to give an offline grader a snapshot of the world; it just does not run that grader.

Real example — the Harbor (Terminal-Bench-style) taskset
(`verifiers/v1/tasksets/harbor/taskset.py`):

```python
    @reward(weight=1.0)
    async def solved(self, runtime: Runtime, trace: Trace) -> float | dict[str, float]:
```

Live runtime, required.

## 5. The artifact mechanism: how a final state *does* cross a runtime boundary

`TaskData.artifacts: list[Artifact]`:

> Paths collected from one runtime and restored at the same locations in another, on top of
> the implicitly collected `/logs/artifacts/` convention dir. Declare runtime outputs that
> must cross that boundary. A declared path that is missing at collection time fails the
> rollout.

`verifiers/v1/utils/artifacts.py`:

```python
ARTIFACTS_DIR = "/logs/artifacts"
"""Implicit artifact directory; tasks that write here need no declaration."""

MAX_ARTIFACT_BYTES = 32 * 1024 * 1024
"""Ceiling per collection. Sized for a delta, not a tree: the grading box boots from the
agent's image, so the repo is already there and only its output has to travel."""


class Artifact(BaseModel):
    """One path to restore at the same location in another runtime."""

    source: str
    exclude: list[str] = Field(default_factory=list)
    """`tar --exclude` patterns, applied when `source` is a directory."""
    required: bool = True
```

`collect(runtime, artifacts) -> dict[str, bytes | None]`:

> Tar the convention dir and every declared path out of `runtime`.
>
> Keyed by source path; the values are tar archives. Insertion order is the order they were
> declared, and a path cannot be collected twice.
>
> A declared source that is missing raises: it was declared because grading needs it, and
> **grading a partial state scores the rollout wrong rather than failing it.** The implicit
> convention sweep is exempt — most tasks never write there.

**Design points worth transplanting, whatever Seahaven's serialization is:**

- The final state that travels is a **declared, bounded delta**, not "everything". The
  comment says why: the grading box boots from the same image, so only the output has to
  move.
- A **hard size ceiling** (32 MiB) on the whole collection.
- A **missing declared path is a hard error**, on the grounds that a stale/partial state
  produces a *wrong score*, which is worse than a failed rollout. The same argument appears
  three more times in the codebase (`docs/v1/harbor.md`: "a silently absent file makes the
  verifier score a stale state"; `stage_tests`: "Raises rather than scoring stale state").

## 6. `IsolatedVerifierEnv`: grade the final state in a fresh box

`verifiers/v1/envs/isolated_verifier/env.py`, module docstring, verbatim:

> Deterministic task verification in a fresh runtime.
>
> One solver agent runs the task. Its task scoring is deferred, declared artifacts are
> collected after normal task finalization, and the solver runtime is destroyed. The task is
> then set up with a fresh controller in a fresh runtime, its artifacts are restored, and
> its ordinary metrics and rewards run there onto the solver's trace.

The lifecycle:

```python
    async def run(self, task: vf.Task, agents: vf.Agents) -> None:
        if task.config.judges:
            raise ValueError(
                "isolated-verifier runs deterministic task metrics and rewards; "
                "model-backed task judges are not supported"
            )
        self.verifier_config(task)  # Refuse an impossible verifier before solving.
        await agents.agent.run(task.defer_scoring(), collect_artifacts=True)

    async def finalize(self, task: vf.Task, episode: vf.Episode) -> None:
        solution = episode.traces[0]
        if solution.ok:
            graded = await self.grade(self.verifier_config(task), task, solution)
            episode.traces[0] = graded[1]

    async def stage_verifier(self, task, solution, runtime) -> None:
        artifacts = dict(solution.state.artifacts)
        async with boundary(TaskError, "verifier task setup"):
            await invoke(task.setup, {"trace": solution, "runtime": runtime})
        await vf.restore(runtime, artifacts)
        async with boundary(TaskError, "verifier staging"):
            await invoke(task.stage_verifier, {"trace": solution, "runtime": runtime})

    async def verify(self, task, solution, runtime) -> Any:
        await task.score(solution, runtime)
```

`Task.defer_scoring()`:

> An independent copy whose task signals are deferred.
>
> Lifecycle hooks still run normally: in particular, ``finalize`` can prepare state before
> declared artifacts are collected and the solver runtime is destroyed. Only task metrics,
> rewards, and judges are skipped; harness metrics remain attached to the solver trace.

`Task.stage_verifier(trace, runtime)`: "Prepare trusted verifier-only inputs after artifacts
are restored." — the hook for planting graders/tests that the agent must not have seen.
`Task.validate(runtime)`: "Check the ground truth, or return None when no model-free check
exists."

Hardening details worth noting: the env refuses a `subprocess` runtime ("isolated-verifier
requires a container runtime so artifacts can be restored safely"), refuses relative
artifact paths across mismatched workdirs, and has `retries: int = 2` "Extra fresh-runtime
attempts after setup, restoration, staging, or scoring failures."

**This is the closest published design to what Seahaven's `state()` + external judge wants
to be**, and it is instructive that Prime Intellect chose *filesystem artifacts restored
into a fresh container* over *a serialized JSON state read by a pure function*.

## 7. `AgenticJudgeEnv`: the same trick with an LLM judge

`verifiers/v1/envs/agentic_judge/env.py` docstring, verbatim:

> Agentic judging: a solver plays the task, then a judge verifies the work.
>
> Two reusable envs share the grading protocol. `--env.id agentic-judge` provisions a fresh
> box from the solver's runtime policy and restores only the task's collected artifacts;
> `--env.id shared-agentic-judge` explicitly runs the judge in the solver's box. The judge
> grades rubric criteria (`[env.task]`: policy prompt, criteria file) and writes its
> verdicts to `/tmp/verdict.json`, with the solver's observable trace record uploaded at
> `/tmp/trace.json`. Hidden reasoning and opaque provider state are omitted by default and
> may be explicitly included through the judge task config. `finalize()` validates the
> verdicts strictly onto the solver's trace — `judge/<name>` metrics plus a weighted-mean
> `judge` reward, composed with the taskset's own rewards via `[env.score]` (judge-only by
> default).
>
> The environment id selects the runtime boundary; there is no mode boolean whose value can
> disagree with the environment's security and artifact semantics.

Its grading prompt:

```
You are grading another agent's attempt at a task. Verify the work EMPIRICALLY:
reconstruct what the agent did from its trace and test it with real execution
in your sandbox — never take the trace's word for an outcome you can check.
```

The judge gets **both** the trace record (as a JSON file) and the restored final state (as
a sandbox), and is told to prefer the state. The `finalize()` scrape is strict:

> Scrape the verdict off the box while it's alive. A judge that wrote no file (or garbage)
> fails HERE — on the judge's own trace, the retryable unit — never silently.

## 8. Rubrics as data: `Criterion`

This is verifiers' declarative-judge format, and a direct counterexample to OpenEnv RFC
004's "declarative formats don't fit" argument. `verifiers/v1/judges/rubric.py`:

```python
class Criterion(BaseModel):
    name: str
    """Key for the criterion's metric (`<judge name>/<name>`) and its `weights` override."""
    text: str
    weight: CriterionWeight = 1.0
    """The criterion's share of the reward (overridable per name via `weights` in config)."""
    choices: list[str] = Field(default_factory=lambda: ["no", "yes"], min_length=2)
    """Allowed answers, ordered **worst → best**: the first scores 0.0, the last 1.0, the rest
    evenly spaced by rank. Default `["no", "yes"]` is a binary check. Needs >= 2, no duplicates."""
```

```python
def normalize_choice(choice: str, choices: list[str]) -> float:
    return choices.index(choice) / (len(choices) - 1)
```

`load_criteria(path, weights)` reads a `.toml` or `.json` file with a `criteria` list,
applies per-name weight overrides from run config, and validates: no duplicate names, no
unknown names in the overrides, finite and positive total weight. `RubricJudgeConfig` points
at `path: Path` — "Relative paths resolve against the evaluation's working directory."

So: **a file of N named, weighted, ordinal criteria; scores normalized to [0,1] by rank;
weights overridable at run time without editing the file; each criterion reported separately
as `<judge name>/<criterion name>`.** That is a shipped, production shape for "hundreds of
small judges as data" — though these are *LLM* criteria, not deterministic assertions over a
state document.

The judge's JSON contract also forces a written reason *before* the verdict:

> Each criterion carries a one-sentence `reason` written *before* the verdict
> (chain-of-thought), so the verdict follows from it and the reasoning is auditable in
> `trace.info["judge_calls"]`.

## 9. The `verifiers` ↔ OpenEnv bridges, in both directions

**verifiers consuming OpenEnv** — `verifiers/v1/tasksets/openenv/taskset.py` (154 lines).
Module docstring: "OpenEnv's per-step rewards are summed onto the seat's trace
(`openenv_reward`)." The whole reward path is:

```python
                    # OpenEnv reports per-step rewards; v1 scores their total.
                    total += result.reward or 0.0
...
        trace.record_reward("openenv_reward", total)
```

**It never calls `env.state()`.**

**OpenEnv consuming verifiers** — `src/openenv/cli/importers/verifiers.py` +
`templates/verifiers_environment.py.tpl` in the OpenEnv tree. The generated env's scoring
path builds a `state: dict`, optionally upgrades it via the verifiers `State` class
(`maybe_state_cls.for_task(task)` — note this is the *v0* API, so the importer targets the
legacy stack), calls `harness.score_group([task], [state])` or
`env.rubric.score_rollout(state)`, and returns:

```python
        return {
            "reward": reward,
            "metrics": _dump(state.get("metrics") or {}),
            "state": _dump(state),
        }
```

…which becomes the *observation's* `result` field, not the OpenEnv `State`. The generated
OpenEnv `State` is still the bare base class with `episode_id` and `step_count`.

So even the bridge between the two ecosystems does not route state through OpenEnv's
`state()`.
