# OpenEnv: the Task API, and the one place OpenEnv persists a final state

Same source tree as the sibling doc: `meta-pytorch/OpenEnv` @
`da5929566e99c8eb376a47042b316cb13c0aae29` (2026-09-10, `0.4.3.dev0`).

---

## 1. The Task API — one environment, many tasks

Arrived in **v0.4.1 (2026-07-03)**; the guide (`docs/source/guides/task-api.md`) landed in
v0.4.2. It is the abstraction that lets a single environment carry N graded tasks.

The guide's own summary:

> The Task API has two halves:
>
> - **Server side**: your environment optionally implements five methods — `list_splits()`,
>   `list_tasks()`, `num_tasks()`, `get_task()`, `get_task_range()`. These are described by
>   the `TaskProvider` protocol.
> - **HTTP side**: when those methods exist, the environment server automatically exposes
>   them as routes under `/{env_name}/…`. You do not register anything.
>
> Task discovery is **metadata only**. It never starts an episode. Selecting a task happens
> the usual way, through `reset()`:
>
> ```python
> env.list_splits()            # ["train", "test"]
> env.num_tasks("test")        # 7595
> env.get_task("test", 12)     # {"id": "test-12", "index": 12, "split": "test"}
> env.reset(split="test", index=12)   # <- this is what starts the episode
> ```

`TaskProvider` is a structural `typing.Protocol` in
`src/openenv/core/env_server/interfaces.py` — you do not inherit from it; the server
discovers the methods by name and returns `501 Not Implemented` when they are absent.

### The task spec is deliberately untyped

> A "task spec" is deliberately untyped (`Any`). Return whatever your environment needs — a
> dict, a Pydantic model, a dataclass. The server converts it to JSON, handling Pydantic
> models, dataclasses, and plain objects. In practice a positional stub such as
> `{"id": "test-12", "index": 12, "split": "test"}` is enough, because `reset()` is what
> actually loads the row.

So OpenEnv's "task" carries **no grader**, **no expected state**, and **no schema**. It is a
selector. All grading stays in the environment's rubric (see the sibling doc), which means a
multi-task environment's per-task graders are Python objects in the env process, typically
dispatched by `RubricDict` on an observation field.

### Two constraints that shape what a task spec can be

From the guide, verbatim:

> 1. **Task methods must be side-effect-free.** They are discovery, not control. They must
>    not mutate episode state, consume a stream, or advance a cursor.
> 2. **They must work on a freshly constructed environment.** Each task route builds a
>    short-lived environment instance, calls the one method, and closes it again. Nothing
>    you set up in `reset()` is available, so read configuration in `__init__` (or lazily
>    inside the method) rather than relying on episode state.

And on scale:

> - return positional stubs rather than real rows, and cap how many you generate
>   (`list_tasks()` returning a bounded preview is fine — `num_tasks()` still reports the
>   honest total)
> ...
> Report the true count from `num_tasks()` even when `list_tasks()` is truncated. A trainer
> that shards work by `num_tasks()` needs the real denominator.

### HTTP surface

| Method | Route | Body | Response |
|--------|-------|------|----------|
| `GET`  | `/list_environments` | — | `["latex_ocr_env"]` |
| `GET`  | `/{env_name}/splits` | — | `[{"name": "train", "type": "train"}, …]` |
| `POST` | `/{env_name}/tasks` | `{"split": "test"}` | `{"tasks": [...], "env_name": "latex_ocr_env"}` |
| `POST` | `/{env_name}/num_tasks` | `{"split": "test"}` | `{"num_tasks": 7595}` |
| `POST` | `/{env_name}/task` | `{"split": "test", "index": 12}` | `{"task": {...}}` |
| `POST` | `/{env_name}/task_range` | `{"split": "test", "start": 0, "stop": 32}` | `{"tasks": [...]}` |

Error semantics: unknown `{env_name}` → 404; method absent or raising `NotImplementedError`
→ 501; `IndexError` → 400.

> These are HTTP-only. There are no WebSocket message types for task discovery — the `/ws`
> session protocol stays focused on `reset`, `step`, `state`, and `close`.

The guide says the API is "shaped to be compatible with ORS/OpenReward task and split
conventions", and that `openenv import` generates wrappers for ORS/OpenReward and Prime
Intellect Verifiers environments that implement it for free.

### The silent-drop footgun

> The server filters incoming keys against your `reset()` signature, so a key it does not
> declare is dropped silently unless the signature also has `**kwargs`. A misspelled
> parameter therefore shows up as "the environment ignored my selection" rather than an error.

Relevant if Seahaven ever selects a fixture/task through `reset()` kwargs.

## 2. The one place OpenEnv persists a final state: `EpisodeRecord.artifacts`

`src/openenv/core/harness/` (RFC 005, v0.3.0, 2026-05-11) is the rollout runtime, and
`collect.py` is the dataset writer. This is where OpenEnv actually writes an episode to
disk, and it is the only place in the project where a serialized environment state crosses
from the rollout to a consumer.

### `EpisodeRecord`

`src/openenv/core/harness/collect.py`:

```python
@dataclass
class EpisodeRecord:
    """Serializable view of one collected episode."""

    episode_id: str
    messages: list[dict[str, Any]]
    reward: float
    done: bool
    tool_trace: list[dict[str, Any]]
    metrics: dict[str, Any]
    verify_metrics: dict[str, Any]
    artifacts: dict[str, Any]
    task: Any = None
    extra: dict[str, Any] = field(default_factory=dict)
```

`src/openenv/core/harness/README.md` documents the on-disk schema, `results.jsonl`, one
JSON object per episode:

| field | type | description |
| --- | --- | --- |
| `episode_id` | string | Stable id for resume (`<prefix>-<index>`) |
| `messages` | list | Chat transcript (user/assistant/tool). TRL `SFTTrainer`-compatible |
| `reward` | float | Env reward (derived via `_resolve_env_reward`, never synthesized) |
| `done` | bool | Whether the episode terminated |
| `tool_trace` | list | Structured tool calls with args & results |
| `metrics` | struct | Harness-level metrics (turns, tool_calls) |
| `verify_metrics` | struct | Session-level metrics (step_count, …) |
| `artifacts` | struct | **Verification artifacts (final state, etc.)** |
| `task` | any | Optional task spec passed by `CollectRunner.tasks` |
| `extra` | struct | Free-form annotations supplied by the caller |

Sidecar `metadata.json` holds run-level info (env, model, n, temperature, filter flags).
`RolloutSerializer.write_episode` is a plain `json.dumps(record.to_dict(), default=str)`
appended to `results.jsonl`. **There is no version field, no `$schema`, no format
identifier anywhere in the record or the sidecar.** Resume works by re-reading
`episode_id`s out of the file.

### Where `final_state` comes from

`StepEnvSessionAdapter` (`src/openenv/core/harness/__init__.py`) calls `client.state()`
after **every** tool call and stashes the result:

```python
    def _read_state(self) -> Any:
        if hasattr(self._client, "state") and callable(self._client.state):
            return self._client.state()
        return None
```

and it puts the serialized state into **every tool result's metadata**:

```python
            metadata={
                "reward": result.reward,
                "state": _state_to_data(state),
            },
```

Then at rollout end, the default verifier writes it out as an artifact:

```python
        return VerifyResult(
            env_reward=reward,
            done=done,
            metrics=metrics,
            artifacts={
                "final_state": _state_to_data(state),
                "transcript_length": len(transcript),
            },
        )
```

The serializer is trivial:

```python
def _state_to_data(state: Any) -> Any:
    """Convert state objects to plain data for metrics and artifacts."""

    if state is None:
        return None
    if hasattr(state, "model_dump"):
        return state.model_dump()
    return state
```

So the persisted format is **`State.model_dump()`, plain JSON, inline in a JSONL row, with
no size cap, no truncation, no hashing, and no schema stamp**. Because the base `State` is
`extra="allow"`, a client that parses into the base class still round-trips the extras.

`_default_verify` also lifts `step_count` into `metrics`:

```python
        if isinstance(state, State):
            metrics["step_count"] = state.step_count
        elif isinstance(state, dict) and "step_count" in state:
            metrics["step_count"] = state["step_count"]
```

### A naming collision to be careful about

`ResourceSession.verify` is declared as:

```python
    def verify(
        self,
        transcript: list[Message],
        final_state: Any | None = None,
    ) -> VerifyResult:
        """Finalize a rollout after the harness has stopped.

        This hook may add metrics or artifacts and may forward the final reward
        already produced by the environment.
        """
```

but the `final_state` **argument** is not the environment state. Both call sites
(`build_harness_rollout_func` in `harness/__init__.py`, and `_rollout_final_state` in
`collect.py`) pass a *rollout summary*:

```python
def _rollout_final_state(rollout: HarnessRolloutResult) -> dict[str, Any]:
    """Build the structured payload passed to ``session.verify()``.

    Mirrors the shape used by ``build_harness_rollout_func`` so verifiers
    can rely on the same contract regardless of caller (TRL vs. collector).
    """
    return {
        "done": rollout.done,
        "metrics": dict(rollout.metrics),
        "events": [
            {"type": event.type, "payload": dict(event.payload)}
            for event in rollout.events
        ],
        "tool_trace": _tool_trace_to_plain(rollout),
    }
```

Meanwhile `VerifyResult.artifacts["final_state"]` *is* the environment state. Two different
things called `final_state` one call apart. Worth not copying.

### The "rewards in env" invariant is enforced, not just documented

`VerifyResult`:

```python
@dataclass
class VerifyResult:
    """Final rollout data produced after a rollout completes.

    ``env_reward`` must forward reward already produced inside the environment.
    It must not synthesize a new reward in the orchestration layer.
    """

    env_reward: float | None = None
    done: bool = False
    metrics: dict[str, Any] = field(default_factory=dict)
    artifacts: dict[str, Any] = field(default_factory=dict)
```

and `EpisodeRecord.from_rollout`'s docstring:

> Uses ``_resolve_env_reward`` so that any disagreement between the reward emitted inside
> the environment and the one forwarded by ``verify()`` raises — preserving the "rewards in
> env" invariant.

The README repeats it under Design notes: "**Rewards in env** — `EpisodeRecord.from_rollout`
reuses the runtime's `_resolve_env_reward` so any mismatch between the reward emitted by a
tool result and the one forwarded by `session.verify()` raises immediately."

## 3. `EvalHarness` (v0.4.0) is not a state consumer

`src/openenv/core/evals/` adds an Inspect/LightEval-shaped harness wrapper. The full type
surface is:

```python
class EvalConfig(BaseModel):
    harness_name: str
    harness_version: str
    library_versions: Dict[str, str]
    dataset: str
    eval_parameters: Dict[str, Any]

class EvalResult(BaseModel):
    config: EvalConfig
    scores: Dict[str, Any]
```

`EvalHarness.run(harness_version, library_versions, dataset, eval_parameters) ->
Dict[str, Any]`. No state, no trajectory, no per-task grader. It is a provenance envelope
around an existing harness's aggregate scores. Note that it *does* record
`harness_version` and `library_versions` — the only versioning discipline anywhere in
OpenEnv's output types, and it applies to the harness, not to the data.

## 4. Environment auto-validation (`openenv.validation`, RFC 008)

There is a second thing called "graders" in the tree:
`src/openenv/validation/graders/`. These are **CI checks on an environment package**, not
task graders. A `Grader` protocol takes a `Subject`:

```python
@dataclass(frozen=True)
class Subject:
    root: Path                       # Package source tree.
    manifest: NormalizedManifest
    image_ref: str | None
    running: RunningSubject | None
    outputs_dir: Path                # Where trajectory records and replay artifacts are written.
```

with `check_id` like `"semantic.oracle_max"`, registered via the entry-point group
`openenv.validation.graders`, and JSON schemas at
`src/openenv/validation/schemas/manifest.schema.json` and `report.schema.json` plus a
severity policy at `policies/severity-v1.json`. Mentioned here only so the name collision
does not confuse a later reader. The interesting transferable bit is that *this* part of
OpenEnv does version its policy file (`severity-v1.json`) and does publish JSON Schemas —
the episode format does not.

## 5. What the docs tell a trainer to do with state

Almost nothing. `docs/source/guides/rl-integration.md` (which opens with "> This page is
still being filled in") gives the generic loop:

```python
with env.sync() as client:
    for episode in range(num_episodes):
        result = client.reset()
        while not result.terminated:
            action = policy(result.observation)
            result = client.step(action)
            policy.update(result.reward)
```

No `state()` call. `docs/source/guides/concepts.md`'s step loop is the same. The only
place the docs mention consuming state is the sentence "The standard `step()`, `reset()`,
`state()` API makes it easy to use environments in training loops."

The only code in the whole project that consumes `state()` for a downstream purpose is
`StepEnvSessionAdapter` in the harness, described in §2.
