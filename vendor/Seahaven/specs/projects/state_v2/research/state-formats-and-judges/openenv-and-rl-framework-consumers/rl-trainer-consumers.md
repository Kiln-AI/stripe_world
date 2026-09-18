# RL trainers as state consumers: TRL, SkyRL, Atropos, ART, veRL

All source read from shallow clones made this session. HEADs:

| project | repo | HEAD | date |
|---|---|---|---|
| TRL | `huggingface/trl` | `5354bc721fe7935178fa14d36397b348e3f271d5` | 2026-09-14 |
| SkyRL | `NovaSky-AI/SkyRL` | `8f9a3e68acbbd006b78bfdb8b7686300db293818` | 2026-09-14 |
| Atropos | `NousResearch/atropos` | `296a0bc55760e43b9c0743c052f8d6c2732606d1` | 2026-07-04 |
| ART | `OpenPipe/ART` | `2ebfc1c2a38dbeeaeefac998323b476989602942` | 2026-09-12 |
| veRL | `volcengine/verl` | `753aed3e1c286ba6825a74342b28669e72c083ea` | 2026-09-14 |

**Headline: none of these five has a concept of "environment state" as a serializable
artifact.** Every one of them computes reward either (a) inside the environment/tool object
while it is alive in process, or (b) from the text/tokens of the trajectory. The word
"state" in these codebases means either the RL sense (an observation) or a live Python
object. This is worth saying plainly because it bounds what Seahaven can expect a trainer to
do with a rich `state()`: today, nothing, unless the environment author writes glue.

---

## 1. TRL — `environment_factory`, and reward from the **live** env object

`docs/source/openenv.md` is the contract. The environment is an ordinary Python class:

> - `__init__(self)` *(optional)*: If provided, must take no arguments.
> - `reset(self, **kwargs)`: Called at the start of each episode. Receives all dataset
>   columns as keyword arguments. Return a string observation (or `None`).
> - **Tool methods**: Any public method (not starting with `_`) other than `reset` is
>   automatically exposed as a tool.

and:

> - **State for reward**: You can store any state you want on the environment instance
>   (e.g., `self.reward`, `self.done`, etc.) and access it in your reward function via the
>   `environments` parameter.

```python
def reward_func(environments, **kwargs) -> list[float]:
    return [env.reward for env in environments]
```

So the persistence between rollout and reward is **a Python attribute on a live object**.
`trl/trainer/grpo_trainer.py` adds a second, tidier path: if the env class defines
`get_reward()`, TRL registers it automatically as an extra reward column named after the
env class:

```python
                if has_reward and type(instance) not in env_reward_types:
                    env_type = type(instance)
                    ...
                        def get_reward(environments, _env_type=env_type, **kwargs):
                            return [e.get_reward() if type(e) is _env_type else None for e in environments]

                    self.reward_funcs.append(get_reward)
                    self.reward_func_names.append(env_type.__name__)
```

Note the pooling comment in the same file, which matters if Seahaven's env instances are
expensive:

> Instances are pooled and reused (reset) across batches; the probe seeds the pool so it is
> not wasted. The pool grows only when a batch needs more concurrent instances of an
> environment than have been created so far, preserving the "construct once, reset often"
> contract even when batches mix environments.

Nothing about the environment is serialized into the training log. `log_completions`
records the prompt/completion text.

### TRL's stated advice on what to grade

From the same doc, under "Tips for reward functions":

> - **Simple rewards work well.** In our experiments with Wordle and Sudoku, binary rewards
>   (1.0 for success, 0.0 otherwise) gave cleaner training signals than shaped rewards with
>   partial credit. GRPO compares completions within a group, so the relative ranking
>   matters more than the absolute values.
> - **Check the final state, not the path.** When possible, let the environment judge the
>   outcome (e.g., "did the model solve the puzzle?") rather than checking if it followed a
>   specific sequence of actions. This gives the model freedom to discover its own strategies.

"Let the environment judge the outcome" — i.e. TRL's advice is state-based grading done
*server-side*, surfaced as a scalar.

### The loop-owning (black-box) path, and the clearest statement of the split

For agents that own their own loop (opencode), TRL uses
`trl/experimental/async_grpo/openenv_harness.py` with an OpenEnv `ResourceSessionFactory`:

> 2. When the agent stops, TRL reads the proxy trace, rebuilds the per-turn training rows
>    from the recorded ids, and scores the final workspace with the session's `verify()`
>    method (a held-out verifier).

and the design note, verbatim, which is the single most useful paragraph I found on this
question anywhere:

> **Why not just use `verify()`?** `verify()` is the environment's job and answers one
> question, "how correct was the outcome," which keeps it clean and reusable for evaluation.
> The reward you train on is a separate, training-time decision (binarize the score,
> penalize degenerate behavior, drop unscorable rollouts). It also needs signals `verify()`
> never sees, since `verify()` only inspects the final workspace, while `rollout_reward_fn`
> also gets the trajectory (tool counts, `timed_out`, the trace). For example, a rollout can
> pass some tests yet never run `bash`; only `rollout_reward_fn` can see that and penalize
> it.

The trainer-side view of the rollout is:

```python
@dataclass
class HarnessRolloutOutcome:
    env_reward: float | None
    completion: list[Message]
    trace: list[TraceEntry]
    tool_call_count: int
    tool_failure_count: int
    tool_calls_by_name: dict[str, int]
    timed_out: bool
```

`env_reward` is a scalar. `tool_calls_by_name: dict[str, int]` is the per-tool counter —
note that TRL considers that worth surfacing to a reward function (relevant to Seahaven's
counters question, though the trajectory subtopic owns the shape).

**And the notable negative result:** TRL calls

```python
            verify = session.verify(completion)
            env_reward = float(verify.env_reward) if verify.env_reward is not None else None
```

— it passes no `final_state` and reads only `env_reward`. **`VerifyResult.artifacts`,
including OpenEnv's `artifacts["final_state"]`, is discarded.** The one place OpenEnv
serializes a final state, its highest-profile consumer throws away.

## 2. SkyRL — no state at all

`skyrl-gym/skyrl_gym/envs/base_text_env.py`, the entire environment contract:

```python
class BaseTextEnvStepOutput(TypedDict):
    observations: ConversationType  # OpenAI API Messages Format
    reward: float
    done: bool
    metadata: Dict[str, Any]
    postprocessed_action: Optional[str] = None


class BaseTextEnv(Env[ConversationType, str]):
    """..."""
    def step(self, action: str) -> BaseTextEnvStepOutput: ...
    def init(self, prompt: ConversationType) -> Tuple[ConversationType, Dict[str, Any]]: ...
    def close(self): ...
    def get_metrics(self) -> Dict[str, Any]:
        """Return environment-specific metrics for the episode."""
    @staticmethod
    def aggregate_metrics(metrics: List[Dict[str, Any]]) -> Dict[str, Any]: ...
```

Reward is per-step, computed inside `step()`. The only episode-level export besides reward
is `get_metrics()` → a dict, with a matching `aggregate_metrics` static method so an env
class defines its own aggregation across episodes. (That pairing — per-episode dict plus a
declared aggregator — is a small idea worth noting.)

SkyRL's own OpenEnv integration, `examples/train_integrations/openenv/env.py`, has this:

```python
        # Look at the state of the environment
        # self.env.state()
```

Commented out. `state()` is not used anywhere in SkyRL's OpenEnv path; the env's per-step
`result.reward` is forwarded to `BaseTextEnvStepOutput.reward`.

## 3. Atropos — the environment returns tokenized, scored data

`atroposlib/envs/base.py`. An Atropos environment's contract with the trainer is:

```python
class ScoredDataGroup(TypedDict):
    tokens: List[List[int]]
    masks: List[List[int]]
    scores: List[float]
    advantages: Optional[List[List[float]]]
    ref_logprobs: Optional[List[List[float]]]
    messages: Optional[List[List[Message]]]
    generation_params: Optional[Dict[str, Any]]
    inference_logprobs: Optional[List[List[float]]]
    group_overrides: Optional[Dict]
    overrides: Optional[List[Dict]]
    images: Optional[Any]
    distill_token_ids: Optional[List[List[List[int]]]]
    distill_logprobs: Optional[List[List[List[float]]]]
```

`BaseEnv.collect_trajectory(item) -> Tuple[Optional[ScoredDataItem], List[Item]]`; the
default `collect_trajectories` fans out `group_size` copies. The abstract methods an env
must implement are `get_next_item()` and `evaluate()`.

This is the extreme end of "reward in env": what crosses the process boundary is already
tokens + masks + scores. There is no state, no observation type, and no place for one. If
Seahaven were consumed by Atropos, the environment wrapper would have to do 100% of
scoring itself.

(Atropos' HEAD is 2026-07-04, ~2.5 months stale relative to the others in this table.)

## 4. ART — a trajectory with reward + metrics, scored by user code or RULER

`src/art/trajectories/__init__.py`:

```python
class Trajectory(_CompactModel):
    exchanges: TrajectoryExchanges = ...
    messages_and_choices: MessagesAndChoices = ...
    tools: Tools | None = None
    additional_histories: list[LegacyHistory] = ...
    reward: float = 0.0
    initial_policy_version: int | None = None
    final_policy_version: int | None = None
    metrics: dict[str, float | int | bool] = ...
    metadata: dict[str, MetadataValue] = ...
    logs: list[str] = ...
    start_time: _UtcDateTime = ...    # exclude=True
```

No state field. `reward` plus a flat `metrics: dict[str, float|int|bool]` and a
`metadata: dict[str, MetadataValue]`. `finish()` stamps `metrics["duration"]`;
`track_duration(name)` accumulates `f"{name}_duration"`. Scoring happens inside the
user-written async rollout function, where the environment is in scope and alive.

The built-in judge, RULER (`src/art/rewards/ruler.py`), is trajectory-only:

```python
async def ruler(
    message_lists: list[list[ChatCompletionMessageParam]],
    judge_model: str = "openai/o3",
    extra_litellm_params: dict[str, object] | None = None,
    rubric: str = DEFAULT_RUBRIC,
    tools: art.Tools | None = None,
    *,
    debug: bool = False,
) -> list[TrajectoryScore]:
```

> RULER works by:
> 1. Extracting common prefixes from trajectories to save tokens
> 2. Passing all trajectories to an LLM judge for relative scoring
> 3. Returning scores that can be used directly as rewards in GRPO
>
> The key insight is that relative scores within a group are all that matters for GRPO,
> which normalizes them anyway.

The judge sees messages and the tool schemas, nothing about the world. A useful contrast
with verifiers' agentic judge, which explicitly tells the judge *not* to trust the trace.

Also note `initial_policy_version` / `final_policy_version` on the trajectory — versioning
of the *producer*, in the same spirit as verifiers' `VersionInfo` and Inspect's
`EvalSpec.task_version`.

## 5. veRL — reward as a pure function of strings, plus per-instance tool rewards

Two reward paths.

**(a) Dataset-level reward functions** (`docs/preparation/reward_function.rst`):

> The parameters of your reward function should be ``data_source``, ``solution_str``,
> ``ground_truth``, and ``extra_info``.
>
> ```python
> def my_reward_fn(data_source, solution_str, ground_truth, extra_info=None):
>   return len(solution_str)/100
> ```

`data_source` and `ground_truth` are columns preprocessed into the parquet dataset. So
veRL's canonical reward is a pure function of *(detokenized response text, ground-truth
string)*. Anything about the world has to have been folded into `ground_truth` or
`extra_info` at dataset-build time.

Reward managers are pluggable (`docs/advance/reward_loop.rst`): subclass
`RewardManagerBase`, implement `async def run_single(self, data: DataProto) -> dict`,
register with `@register("name")`, select with `reward.reward_manager.name=...`. Async
reward functions are recommended "when reward computation need to involve external model
API calls or sandboxed execution" — so a remote judge call is an explicitly supported shape.

**(b) Tool-level rewards** (`verl/tools/base_tool.py`):

```python
    async def create(self, instance_id: Optional[str] = None, **kwargs) -> tuple[str, ToolResponse]
    async def execute(self, instance_id: str, parameters: dict[str, Any], **kwargs) -> tuple[ToolResponse, float, dict]
    async def calc_reward(self, instance_id: str, **kwargs) -> float
    async def release(self, instance_id: str, **kwargs) -> None
```

A tool holds per-rollout state keyed by `instance_id`; `calc_reward(instance_id)` reads that
**live in-process state**; `release(instance_id)` destroys it. Same pattern as TRL's
`get_reward()`, at tool granularity.

The trainer-side rollout record (`verl/experimental/agent_loop/agent_loop.py`):

```python
class AgentLoopOutput(BaseModel):
    prompt_ids: list[int]
    response_ids: list[int]
    response_mask: list[int]
    response_logprobs: Optional[list[float]] = None
    routed_experts: Optional[Any] = None
    multi_modal_data: Optional[dict[str, Any]] = None
    reward_score: Optional[float] = None
    num_turns: int = 0
    metrics: AgentLoopMetrics
    extra_fields: dict[str, Any] = {}
    mm_processor_kwargs: Optional[dict[str, Any]] = None
```

Tokens, a scalar `reward_score`, metrics, and an untyped `extra_fields` escape hatch.

---

## 6. Cross-cutting conclusions for Seahaven

1. **Live object, not serialized state, is the universal pattern.** TRL (`env.reward` /
   `get_reward()`), veRL (`calc_reward(instance_id)`), SkyRL (`step()` returns reward),
   Atropos (env returns scored tokens), ART (rollout function closes over the env) — five
   for five. OpenEnv's rubric (`forward(action, observation)`) is a sixth. A serialized
   final state judged out-of-process is *not* the ecosystem's default; verifiers'
   `IsolatedVerifierEnv` (fresh box + restored tar artifacts) and Inspect's `store` are the
   only two counterexamples I found, and both are recent.

2. **What crosses the boundary is a named scalar or a small dict of named scalars.**
   `dict[str, Reward]` (verifiers), `dict[str, float]` (SkyRL `get_metrics`, ART `metrics`),
   one reward column per reward function (TRL), `dict[str, Score]` (Inspect). If Seahaven's
   state is judged anywhere, the thing to hand back is a *named-signal dict*, not a verdict
   scalar — every consumer in this list can absorb that, and only that.

3. **Nobody versions an episode record except verifiers and Inspect.** OpenEnv's
   `results.jsonl` row has no version field. SkyRL, Atropos, ART and veRL have no on-disk
   episode contract at all — their "record" is whatever the training logger writes.
   Precedents to copy are `TRACE_VERSION`/`Trace.version` + `WireEpisode` (verifiers) and
   `EvalLog.version: int = 2` (Inspect).

4. **The TRL `verify()` note is the design argument to put in the spec.** Separating "the
   environment's answer to *was the outcome correct*" from "the trainer's reward shaping" is
   exactly the `state()`-plus-external-judge split, argued by the framework that would
   consume it.

5. **Size discipline is a real constraint and only verifiers has one.** 32 MiB per artifact
   collection, sized deliberately "for a delta, not a tree". OpenEnv's
   `artifacts["final_state"]` is an unbounded `model_dump()` inlined in a JSONL row. If
   Seahaven's `state()` returns a full changeset with `before`/`after` rows, a cap plus a
   documented truncation/overflow signal is not optional.
