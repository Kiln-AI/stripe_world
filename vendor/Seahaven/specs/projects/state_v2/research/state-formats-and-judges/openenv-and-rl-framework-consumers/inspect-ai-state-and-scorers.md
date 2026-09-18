# Inspect AI: `TaskState`, the `Store`, sandbox-backed scorers, and re-scoring a log

**Source:** `UKGovernmentBEIS/inspect_ai` @ `9525af80b47f56a012a7ffccfa7525eb694c2936`
(2026-09-14), cloned this session. Docs read from `docs/*.qmd` in that tree (these are the
sources for the published site).

Inspect is the eval framework in this survey that comes closest to Seahaven's shape:
a stateful sandbox, per-task scorers, a durable versioned log, and a documented post-hoc
re-scoring path. It is also the one that draws the sharpest line between "state you kept"
and "state you can get back later".

---

## 1. `TaskState` — conversation + a typed key-value store

`src/inspect_ai/solver/_task_state.py`:

```python
class TaskState:
    """
    The `TaskState` represents the internal state of the `Task` being run for a single `Sample`.

    The `TaskState` is passed to and returned from each solver during a sample's
    evaluation. It allows us to maintain the manipulated message history, the tools
    available to the model, the final output of the model, and whether the task
    is completed or has hit a limit.
    """

    def __init__(
        self,
        model: ModelName,
        sample_id: int | str,
        epoch: int,
        input: str | list[ChatMessage],
        messages: list[ChatMessage],
        target: Target = Target(""),
        choices: list[str] | None = None,
        output: ModelOutput | None = None,
        message_limit: int | None = None,
        token_limit: int | None = None,
        token_limit_type: str = "all",
        cost_limit: float | None = None,
        completed: bool = False,
        metadata: dict[str, Any] | None = None,
        store: dict[str, Any] | None = None,
        scores: dict[str, Score] | None = None,
        sample_uuid: str | None = None,
    ) -> None:
```

The two general-purpose buckets are:

- **`metadata`** — comes *in* from the `Sample` (task-authored, per-row). Read-mostly.
- **`store`** — a `Store` (dict-like) the solvers and tools write *during* the run.

The sandbox is **not** on `TaskState`. It is ambient: `from inspect_ai.util import sandbox`
and `await sandbox().exec(...)` / `.read_file(...)` reach the container bound to the current
sample.

### `StoreModel`: typed, namespaced access to the store

From `docs/_store_typing.md`:

> If you prefer a typesafe interface to the sample store, you can define a Pydantic model
> which reads and writes values into the store. There are several benefits to using Pydantic
> models for store access:
>
> 1. You can provide type annotations and validation rules for all fields.
> 2. Default values for all fields are declared using standard Pydantic syntax.
> 3. Store names are automatically namespaced (to prevent conflicts between multiple store
>    accessors).

```python
class Activity(StoreModel):
    active: bool = Field(default=False)
    tries: int = Field(default=0)
    actions: list[str] = Field(default_factory=list)

activity = state.store_as(Activity)
activity.active = True
```

> Note that we define defaults for all fields. This is generally required so that you can
> initialise your Pydantic model from an empty store.

Namespacing is literal key prefixing — `EvalSample.store_as` un-prefixes on read:

```python
        data = {
            k.replace(f"{model_cls.__name__}:", "", 1): v for k, v in self.store.items()
        }
```

so the persisted store is a **flat `dict[str, Any]` with `ClassName:field` keys**, and
several independent typed views can coexist in one sample's store without colliding. The
`instance` parameter allows multiple instances of the same model in one sample.

This is a directly transplantable idea for a Seahaven state document that several graders
and tools want to contribute to: one flat namespaced bag, N typed Pydantic views, defaults
everywhere so an older/newer reader still parses.

## 2. The store IS persisted, and it is described as "state at end of sample"

`src/inspect_ai/log/_log.py`:

```python
class EvalSample(BaseModel):
    """Sample from evaluation task."""

    id: int | str
    epoch: int
    input: str | list[ChatMessage]
    choices: list[str] | None = Field(default=None)
    target: str | list[str]
    sandbox: SandboxEnvironmentSpec | None = Field(default=None)
    """Sandbox environment type and optional config file."""
    files: list[str] | None = Field(default=None)
    """Files that go along with the sample (copied to SandboxEnvironment)"""
    setup: str | None = Field(default=None)
    messages: list[ChatMessage] = Field(default_factory=list)
    output: ModelOutput = Field(default_factory=ModelOutput)
    scores: dict[str, Score] | None = Field(default=None)
    metadata: dict[str, Any] = Field(default_factory=dict)
    ...
    store: dict[str, Any] = Field(default_factory=dict)
    """State at end of sample execution."""
```

**`store: dict[str, Any]` — "State at end of sample execution."** That is Inspect's
`final_state`, and its format is: *plain JSON, flat, namespaced keys, inlined in the sample
record*. No separate envelope, no schema pointer on the payload.

Note also `sandbox: SandboxEnvironmentSpec` and `files: list[str]` on the sample record —
the log preserves *how to rebuild* the environment (the spec and the input files), but not
its end contents.

### Size handling: attachments

`docs/eval-logs.qmd`:

> Sample logs often include large pieces of content that are duplicated in multiple places
> in the log file (input, message history, events, etc.). To keep the size of log files
> manageable, images and other large blocks of content are de-duplicated and stored as
> attachments.

`EvalSample.attachments: dict[str, str]` — "Attachments referenced from messages and
events. Resolve attachments for a sample (replacing `attachment://*` references with
attachment content) by passing `resolve_attachments=True` to log reading functions." Plus
`events_data: EventsData | None` — "Pooled dedup data for condensed events (messages and
calls)."

And a cheap-read projection: `EvalSample.summary()` →

> The summary excludes potentially large fields like messages, output, events, **store**,
> and metadata so that it is always fast to load.

So Inspect has content-addressed de-duplication with `attachment://` references for big
blobs, and a summary projection that drops the store. Both are relevant if Seahaven's state
can get large: reference-not-inline, and a cheap header.

## 3. `Score` — the per-judge record

`src/inspect_ai/scorer/_metric.py`:

```python
class Score(BaseModel):
    """Score generated by a scorer."""

    model_config = ConfigDict(ser_json_inf_nan="constants")

    value: Value
    answer: str | None = Field(default=None)
    """Answer extracted from model output (optional)"""
    explanation: str | None = Field(default=None)
    """Explanation of score (optional)."""
    reason: ScoreReason | str | None = Field(default=None)
    """Machine-readable reason for an abnormal score (optional)."""
    metadata: dict[str, Any] | None = Field(default=None)
    history: list[ScoreEdit] = Field(default_factory=list)
    """Edit history - users can access intermediate states."""
```

Scorer signature: `async def score(state: TaskState, target: Target) -> Score`. `Value` may
be a string (`"C"` / `"I"` / `"P"` / `"N"`), a number, a bool, a list, or a **dict of named
sub-scores**.

Three ideas here worth stealing for a judge record:

1. **`answer` + `explanation` alongside `value`.** From `docs/custom-scorers.qmd`: "If you
   are extracting an answer from within a completion ... you should strive to *always*
   return an `answer` as part of your `Score`, as this makes it much easier to understand
   the details of scoring when viewing the eval log file."
2. **`Score.unscored()` is distinct from a zero.** "Return `None` when a sample is outside
   the scorer's scope... Use `Score.unscored()` when a judgment is expected but could not be
   obtained, such as an unparsable grader verdict... This creates a score with a `NaN` value
   and preserves the supplied reason, answer, and explanation. Metrics exclude the value."
   Three distinct outcomes — *not applicable*, *unscorable*, *scored zero* — is more
   resolution than most judge designs carry, and is exactly the distinction a DB-diff judge
   needs (table absent vs. judge errored vs. condition false).
3. **A documented error policy.** "Return a scored verdict for an incorrect answer or a
   failure to follow the task's required format. Raise an exception for execution failures
   or invalid scorer inputs so Inspect can apply the run's error-handling settings."

### Many judges per task

`docs/multiple-scorers.qmd` gives three shapes:

> 1. You can provide a list of scorers in a `Task` definition (this is the best option when
>    scorers are entirely independent)
> 2. You can yield multiple scores from a `Scorer` (this is the best option when scores
>    share code and/or expensive computations).
> 3. You can use multiple scorers and then aggregate them into a single scorer (e.g.
>    majority voting).

with per-key metrics declared on the decorator:

```python
@scorer(metrics={"correct": [mean(), stderr()], "concise": [mean()]})
def answer_quality(max_words: int = 50):
    async def score(state, target) -> Score:
        ...
        return Score(value={"correct": correct, "concise": concise}, answer=completion)
```

Option 2 — one scorer emitting a dict of named values — is the shape that scales to "many
small checks over one expensive-to-compute state", which is Seahaven's case.

## 4. Scorers run with the sandbox alive

Confirmed from source, not just docs. In `src/inspect_ai/_eval/task/run.py` the per-sample
body is wrapped in `sandboxenv_context(task_name, sandbox, max_sandboxes, sandbox_cleanup,
sample)`, and the scorer loop runs **inside** that context:

```python
                                    async with span(name="scorers"):
                                        for scorer_idx, scorer in enumerate(scorers or []):
                                            ...
                                                score_result = await scorer(
                                                    state, Target(sample.target)
                                                )
```

`docs/multiple-scorers.qmd`:

> The contents of the sandbox for the Sample are available to the scorer; simply call
> `await sandbox().read_file()` (or `.exec()`).

```python
@scorer(metrics=[accuracy()])
def check_file_exists():
    async def score(state: TaskState, target: Target):
        try:
            _ = await sandbox().read_file(target.text)
            exists = True
        except FileNotFoundError:
            exists = False
        return Score(value=1 if exists else 0)
```

So the default Inspect answer to "is the reward computed from a serialized state or a live
environment?" is: **a live environment, in the same process, in the same sample scope, just
before teardown.** Exactly the OS-benchmark pattern.

## 5. …and the re-scoring path gets no sandbox. This is the crux.

`docs/scoring-workflow.qmd`:

> By default, model output in evaluations is automatically scored. However, you can defer
> scoring by using the `--no-score` option.
> ...
> You can score an evaluation previously run this way using the `inspect score` command
> ...
> This will use the scorers and metrics that were declared when the evaluation was run,
> applying them to score each sample and generate metrics for the evaluation.

You can also point a *different* scorer at an existing log
(`docs/extensions-components.qmd`):

```bash
inspect score logs/2025-01-01-mytask.eval --scorer evals/my_scorer
inspect score logs/2025-01-01-mytask.eval --scorer evals/my_scorer -S threshold=0.8
```

with `--action append|overwrite` and `--overwrite` controlling whether the scored log
replaces the original or lands as a `-scored` sibling.

What that path actually reconstructs (`src/inspect_ai/_eval/score.py`):

```python
    state = TaskState(
        model=ModelName(model_name),
        sample_id=resolved_sample.id,
        epoch=resolved_sample.epoch,
        input=resolved_sample.input,
        target=target,
        choices=resolved_sample.choices,
        messages=resolved_sample.messages,
        output=resolved_sample.output,
        completed=True,
        metadata=resolved_sample.metadata,
        store=resolved_sample.store,
        scores=dict(resolved_sample.scores or {}) if append_scores else {},
        sample_uuid=resolved_sample.uuid,
    )

    # initialize active model and store
    init_task_context(model, model_roles)
    init_subtask_store(state.store)

    # load a copy of the current sample events into the transcript
    init_transcript(
        Transcript([*resolved_sample.events], log_model_api=False, bounded=False)
    )
```

Messages, output, metadata, **store**, events, timelines. **No sandbox.** The word does not
appear anywhere in `score.py`.

**The consequence, stated plainly, because it is the single most transferable lesson in this
whole subtopic:**

> In Inspect, a scorer that reads `state.store` can be re-run against an old log years
> later. A scorer that reads `sandbox()` can only ever run once, while the container is up.
> Whatever a future judge will need must have been written into the store before the sample
> ended.

That is precisely the bet Seahaven is making with `state()`, and Inspect is the existence
proof that the bet is realistic — and that the store has to be *chosen* up front, because
nothing recovers it afterwards.

(`--no-sandbox-cleanup` exists as a debugging option to keep containers alive after a run,
but that is a manual, same-machine affordance, not a re-scoring mechanism.)

## 6. Log versioning and forward migration

```python
class EvalLog(BaseModel):
    """Evaluation log."""

    # WARNING: The order of these fields is important for the log file format.
    # Do not change the order of these fields without incrementing the version number,
    # updating the log file read/write functionality (such as read_eval_log),
    # and updating the tests.
    version: int = Field(default=2)
    """Eval log file format version."""
```

plus `EvalSpec.task_version: int | str = Field(default=0)` — the *task's* own version,
author-controlled, so a task that changes its grading can be distinguished in old logs.

Two migration mechanisms live on the models themselves:

```python
    @model_validator(mode="before")
    @classmethod
    def migrate_deprecated(cls: Type["EvalSample"], values: Any) -> Any:
```

and, on `Score`:

```python
    @model_validator(mode="before")
    @classmethod
    def _lift_unscored_reason(cls, data: Any) -> Any:
        """Lift legacy ``metadata["unscored_reason"]`` into ``reason``.

        Logs written before ``reason`` existed (#4048, 0.3.245+) record the
        grade-parse failure mode in metadata. Read it into the field so new
        readers see one channel; the metadata key is left in place so that
        round-tripped logs don't differ.
        """
```

This is a clean worked example of the "version field plus formatter" pattern: the schema
version is coarse (an int on the log root), and fine-grained field moves are handled by
`mode="before"` validators that upgrade old payloads on read while leaving the old key in
place so a round trip is byte-stable. Worth copying if Seahaven's state document is meant
to be re-read years later.

## 7. Small extras worth noting

- **`EvalSampleLimit`** on the sample record ("The limit that halted the sample") — the
  record says *why* it stopped, not just that it did.
- **Interim scoring** (`docs/control-channel.qmd`): "Completed samples are folded in, never
  re-scored: already-scored samples contribute their final scores to the interim metrics...
  writing scores into a mid-run log isn't safe, so `inspect score` after the run remains the
  way to score them."
- **Score edit history** (`Score.history: list[ScoreEdit]`) — a judge verdict is an
  append-only record with human edits tracked, not a value that gets silently overwritten.
