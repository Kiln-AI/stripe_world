# Trajectory container formats: ATIF, Letta `trajectory`, AgentEvals

These three are the closest thing to a *standard* for "a saved list of what an agent did", as opposed
to the per-call vocabularies in the other two docs. All fetched 2026-09-14.

---

## 1. ATIF — Agent Trajectory Interchange Format (Harbor RFC 0001)

Primary source:
[`harbor-framework/harbor/rfcs/0001-trajectory-format.md` @ main](https://github.com/harbor-framework/harbor/blob/main/rfcs/0001-trajectory-format.md).

Header, verbatim:

| Field | Value |
| :--- | :--- |
| **Status** | Active |
| **Maintainer** | Boxuan Li |
| **Date** | April 2026 |
| **Changelog** | v1.8 |

> The **Agent Trajectory Interchange Format (ATIF)** is a standardized, JSON-based specification for
> logging the complete interaction history of autonomous LLM agents. ATIF is designed to unify the
> distinct data requirements of conversational logs, explicit action sequences (MiniSweAgent), and
> replayable data structures (OpenHands), ensuring collected data is immediately usable across
> debugging, visualization, Supervised Fine-Tuning (SFT), and Reinforcement Learning (RL) pipelines.

**Corroboration that it is used outside Harbor:** NVIDIA's NeMo Agent Toolkit ships `nat.atif` —
"Pydantic models for Agent Trajectory Interchange Format (ATIF)", with `ATIF_VERSION = 'ATIF-v1.7'`
in toolkit 1.8 (`ATIF-v1.6` in toolkit 1.6) and `schema_version` accepting `ATIF-v1.0` through
`ATIF-v1.7`; the docs state the models are "derived from the Harbor reference implementation and
follow the ATIF RFC (0001-trajectory-format)". Source: NVIDIA NeMo Agent Toolkit API docs for
`nat.atif.trajectory` (1.6/1.7/1.8) — surfaced via search; `docs.nvidia.com` is blocked by this
session's egress proxy so I could not read the page directly, and I could not locate the module in
the public `NVIDIA/NeMo-Agent-Toolkit` GitHub tree at the paths I tried. **Treat the NVIDIA detail as
search-derived, not primary-verified.**

### 1.1 Root object

| Field | Type | Status | Notes (verbatim where quoted) |
| --- | --- | --- | --- |
| `schema_version` | String | **Required** | "String defining ATIF compatibility (e.g., \"ATIF-v1.7\")." |
| `session_id` | String | Optional | "Run-scoped, not document-scoped" — MAY be shared across siblings. Was Required through v1.6. |
| `trajectory_id` | String | Optional | "Canonical per-trajectory-document identifier, distinct from `session_id`." |
| `agent` | Object | **Required** | `{name, version, model_name?, tool_definitions?, extra?}` |
| `steps` | Array | **Required** | the interaction history |
| `notes` | String | Optional | "custom information, design notes, or explanations for format discrepancies" |
| `final_metrics` | Object | Optional | aggregate counters — see §1.3 |
| `continued_trajectory_ref` | String | Optional | link to a continuation file when context management split the run |
| `extra` | Object | Optional | custom root metadata |
| `subagent_trajectories` | Array | Optional | embedded, independently-valid child trajectories |

Note `agent.tool_definitions`: "Array of tool/function definitions available to the agent. Each
element follows OpenAI's function calling schema". **The tool surface is recorded with the run**, so
a judge reading the file years later knows what the agent *could* have called, not just what it did.

### 1.2 `StepObject` — the unit of the trajectory

| Field | Type | Status | Notes |
| --- | --- | --- | --- |
| `step_id` | Integer | **Required** | "Ordinal index of the turn (starting from 1)." |
| `timestamp` | String | Optional | ISO 8601 |
| `source` | String | **Required** | `"system"` \| `"user"` \| `"agent"` |
| `model_name` | String | Optional | per-step override of the agent-level model |
| `reasoning_effort` | String \| Float | Optional | |
| `message` | String \| Array | **Required** | may be empty string; array = multimodal `ContentPart`s (v1.6+) |
| `reasoning_content` | String | Optional | |
| `tool_calls` | Array | Optional | "A single LLM output may contain multiple tool calls." |
| `observation` | Object | Optional | `{results: [...]}` — see §1.4 |
| `metrics` | Object | Optional | tokens/cost/logprobs — see §1.3 |
| `extra` | Object | Optional | |
| `llm_call_count` | Integer | Optional | see below (v1.7) |
| `is_copied_context` | Boolean | Optional | (v1.7) marks steps carried across a compaction boundary |

`llm_call_count`, verbatim, because it is the cleanest statement of a counting convention I found:

> Number of LLM inferences this step represents. When `llm_call_count > 1`, the `metrics` are
> aggregated across multiple LLM calls and per-call attribution is unavailable. When `1`, the step
> represents exactly one inference. When `0` on a `source: "agent"` step, the step represents a
> deterministic (non-LLM) dispatch — a graph engine, rule-based pipeline, or eval harness that issued
> `tool_calls` without an LLM inference; `metrics` and `reasoning_content` MUST be absent on such
> steps, and SFT pipelines MUST filter them out. When null, the producer did not track this
> (backward-compatible default).

And the accompanying rule:

> **One-LLM-per-step convention:** Exporters SHOULD emit one ATIF step per LLM inference when the
> underlying framework provides per-call event boundaries (e.g., `LLM_START`/`LLM_END`). When an
> exporter cannot split calls ... it MUST set `llm_call_count` to the actual count so consumers can
> detect aggregated metrics.

Three things generalize: **a step is one model turn, not one tool call**; a counter exists
specifically so a consumer can tell *aggregated* data from *attributable* data; and `null` means
"not tracked", which is different from `0`.

### 1.3 Aggregate counters: `FinalMetricsSchema` and `MetricsSchema`

`final_metrics` (all fields Optional):

| Field | Type |
| --- | --- |
| `total_prompt_tokens` | Integer |
| `total_completion_tokens` | Integer |
| `total_cached_tokens` | Integer |
| `total_cost_usd` | Float |
| `total_steps` | Integer — "Total number of steps (can be unequal to length of steps array if explained in notes)." |
| `extra` | Object |

Per-step `metrics`: `prompt_tokens`, `completion_tokens`, `cached_tokens`, `cost_usd`,
`prompt_token_ids`, `completion_token_ids`, `logprobs`, `extra`.

The token-accounting rule is stated normatively to remove ambiguity:

> ATIF defines `prompt_tokens` as the total count of all input tokens (both cached and non-cached),
> with `cached_tokens` tracking the subset that were cache hits.

And the deliberate omission of pricing:

> Note that ATIF does not record per-token pricing information because: 1. Pricing can change over
> time, making historical trajectories inaccurate 2. Most agent frameworks don't record pricing,
> requiring a lookup table for conversion 3. Pricing varies by provider, tier, and region
>
> The `cost_usd` and `total_cost_usd` fields store the calculated cost at the time of execution,
> providing a snapshot without coupling the format to specific pricing models.

**This is the design principle to copy for any aggregate in a long-lived artifact: record the
*measured* value at run time, not the inputs needed to recompute it, because the inputs drift.**

`total_steps` being allowed to disagree with `len(steps)` — "if explained in notes" — is the same
principle applied to a count: the counter is authoritative about what happened even when the list is
incomplete.

### 1.4 `ToolCallSchema` / `ObservationResultSchema`

```
ToolCallSchema:
  tool_call_id  String  Required  "Unique identifier for this specific tool call.
                                   Used to correlate with observation results via `source_call_id`."
  function_name String  Required
  arguments     Object  Required  "Must be a valid JSON object, but can be empty ({}) if no arguments needed."
  extra         Object  Optional  "(e.g., timeout, retry count, tool version)"  [v1.7]

ObservationResultSchema:
  source_call_id          String          Optional  the tool_call_id this result answers; null for non-tool actions
  content                 String | Array  Optional
  subagent_trajectory_ref Array           Optional
  extra                   Object          Optional  "(e.g., confidence score, retrieval score, source document ID)"
```

**Unflattering finding: ATIF has no error or status field on a tool result.** There is no `ok`, no
`is_error`, no `error.type`. A failed call is indistinguishable from a successful one except by
reading `content`. Every other format surveyed (MCP, Anthropic, Inspect, tau2, Letta) has an explicit
error signal. Producers would have to put it in `extra`.

There are also **no size limits, no truncation convention, and no externalization mechanism for text**
in ATIF. Images (v1.6) and audio (v1.8) are referenced by relative path into an `images/`
subdirectory — that is the only externalization, and the RFC is explicit that streaming audio is out
of scope because "those formats are not self-describing".

### 1.5 The context-management convention (§VII) — bounding without losing auditability

ATIF v1.7 added a convention for steps that compact or prune the agent's context, declared in
`step.extra.context_management` as `{type, boundary}` with
`type ∈ {compaction, pruning, injection}` and `boundary ∈ {replace, append, truncate}`, both
extensible. The normative rule, verbatim:

> When a system step has `extra.context_management.boundary = "replace"`, the agent's effective
> context window for all subsequent steps consists of: (1) the observation content from the boundary
> step (`observation.results[].content`), and (2) any new turns (user, agent, or system) after the
> boundary step. **Steps preceding the boundary are preserved in the trajectory for auditability but
> are NOT part of the agent's context window for post-boundary steps.** Evaluation tools
> reconstructing the agent's input context for any post-boundary step MUST use the boundary's
> observation content, not the pre-boundary steps.

This separates two things that are easy to conflate: **what the agent saw** and **what the record
keeps**. The record keeps everything; a marker says where the agent's view was reset. Any bounding
scheme that drops entries should say so in-band the same way, rather than silently shortening the
list.

---

## 2. Letta `trajectory` — normalizing many harnesses into one record list

Source: [`letta-ai/trajectory` @ main](https://github.com/letta-ai/trajectory) — README, `src/types.ts`,
`src/bounds.ts`, `src/core.ts`, `schema/trajectory-v1.schema.json`. Published as
`@letta-ai/trajectory` (TS) and `agent-trajectory` (Python wrapper).

> Normalize agent transcripts from different runtimes into one validated, model-ready record format.
>
> Agent tools represent the same concepts—messages, reasoning, tool calls, and tool results—in
> incompatible native formats. `trajectory` provides one TypeScript API that turns those formats into
> deterministic, structured records for training, evaluation, analysis, and inference.

Adapters exist for `atif` (ATIF-v1.0 through ATIF-v1.7), `claude-code`, `codex`, `copilot-cli`,
`cursor`, `droid`, `gemini-cli`, `hermes`, `letta-code`, `omp`, `openclaw`, `opencode`, `openhands`,
`pi`, `deepagents`. (So ATIF is one input among fifteen, not the hub.)

### 2.1 The schema (`schema/trajectory-v1.schema.json`)

`$id: "https://letta.ai/schemas/trajectory/v1.json"`, `$schema` draft 2020-12, root is a
**non-empty array of records**, and every record has `additionalProperties: false`.

```jsonc
tool_call = { id: string(min 1), name: string(min 1), args: string }        // required: id, name, args
tool      = { role: "tool", tool_call_id: string(min 1), content: string,
              ok?: boolean, timestamp: <ISO-8601 pattern> }                 // required: role, tool_call_id, content, timestamp
assistant = { role: "assistant", content: string|null, timestamp,
              tool_calls?: [tool_call, ...] }                               // content MUST be null iff tool_calls present
meta      = { role: "meta", source: string, cwd?, git_branch?, model? }     // exactly one, leading
```

Other roles: `user`, `system` (omitted by default), `observation` ("Generic `observation` records
for environment feedback that cannot be attributed to one specific tool call, such as merged
terminal output"), `reasoning`.

Two details worth carrying:

- **`args` is a string, not an object.** It normalizes toward the OpenAI wire shape because that is
  the lossless superset — a malformed model emission survives the round trip.
- **`ok` is optional and deliberately so.** From the README:

  > Tool result records may include `ok: boolean` when the source exposes an authoritative structured
  > outcome, such as Pi/OpenClaw `isError`, Claude Code `is_error`, Letta Code `resultOk`,
  > OpenHands/Cursor `is_error`, OpenCode/Gemini terminal state, or Copilot CLI `success`. The field
  > is omitted when the source does not expose a reliable status; **result text is never interpreted
  > as success or failure.**

  Absent ≠ success. The format refuses to guess.

### 2.2 Size handling — the most concrete bounding policy I found anywhere

From [`src/types.ts`](https://github.com/letta-ai/trajectory/blob/main/src/types.ts):

```ts
export interface ToolArgumentBounds {
  /** Maximum Unicode code points in the serialized arguments object. */
  maxCharacters?: number | null;
}
export type ToolResultTruncationStrategy = "head" | "head-tail";
export interface ToolResultBounds {
  /** Maximum Unicode code points in the final stored result. */
  maxCharacters?: number | null;
  strategy?: ToolResultTruncationStrategy;
}
export type ToolResultPolicy = "include" | "omit";
export interface NormalizationFilters {
  /** Whether normalized `tool` result records are emitted. */
  toolResults?: ToolResultPolicy;
  /** Whether normalized `system` message records are emitted. Defaults to `omit`. */
  systemMessages?: SystemMessagePolicy;
}
```

Defaults ([`src/bounds.ts`](https://github.com/letta-ai/trajectory/blob/main/src/bounds.ts)):

```ts
export const DEFAULT_NORMALIZATION_BOUNDS = Object.freeze({
  toolArguments: Object.freeze({ maxCharacters: 20_000 }),
  toolResults: Object.freeze({ maxCharacters: 2_500, strategy: "head-tail" }),
});
```

**20,000 characters for arguments, 2,500 characters for results, head-tail by default.** The
asymmetry is deliberate: arguments are what the agent *decided*, results are what the world
*returned*; the decision is worth keeping in full, the return value usually is not.

The truncation implementation ([`src/core.ts`](https://github.com/letta-ai/trajectory/blob/main/src/core.ts)):

```ts
function truncationMarker(remaining: number): string {
  return `\n… [truncated, ${remaining} more chars]`;
}
```

with `truncateText` binary-searching for the largest `keep` such that
`keep + len(marker) <= limit` — i.e. **the marker is counted inside the budget, so the bound is
honest**, and head-tail splits the kept budget as `headLength = ceil(keep/2)`, tail gets the rest,
marker in the middle. Counting is in Unicode code points, not UTF-16 units or bytes.

### 2.3 Diagnostics: truncation is reported, not silent

```ts
export type DiagnosticCode =
  | "invalid_json_line" | "non_object_json_line" | "injected_context_dropped"
  | "noise_record_dropped" | "sidechain_record_dropped" | "tool_call_id_synthesized"
  | "duplicate_tool_call_id" | "orphan_tool_result" | "duplicate_tool_result"
  | "unknown_tool_name" | "tool_arguments_reshaped" | "tool_arguments_truncated"
  | "tool_result_truncated" | "timestamps_synthesized" | "timestamps_interpolated";

export interface Diagnostic { code; message; inputLine?; recordIndex?; count?; }
```

and the messages are specific, e.g.
`` `Truncated the result for tool call ${id} to at most ${resultLimit} Unicode code points using the ${strategy} strategy.` ``

`normalizeTranscript` "always" returns `diagnostics`, "empty when the transcript required no
recoverable cleanup". **A lossy transformation emits a machine-readable record of what it lost.**
That is a strictly better design than Inspect's in-band `truncated` tuple *and* complementary to it —
Letta says "something was truncated, here is the code and a count", Inspect says "this specific
field lost these bytes".

Also note `tool_call_id_synthesized` and `timestamps_synthesized` / `timestamps_interpolated`: when
the source format lacks an id or a timestamp, the normalizer **invents one and says that it did**.

---

## 3. AgentEvals — how a trajectory gets judged, and what that implies about its shape

Source: [`langchain-ai/agentevals` README @ main](https://github.com/langchain-ai/agentevals/blob/main/README.md).

> These evaluators expect you to format your agent's trajectory as a list of OpenAI format dicts or
> as a list of LangChain `BaseMessage` classes

`create_trajectory_match_evaluator` takes:

- `trajectory_match_mode ∈ {strict, unordered, subset, superset}`:
  - `strict` — "compares two trajectories and ensures that they contain the same messages" in the
    same order. "useful is if you want to ensure that tools are always called in the same order for a
    given query (e.g. a company policy lookup tool before a tool that requests vacation time)."
  - `unordered` — "contain the same tool calls in any order."
  - `superset` — "ensure that some key tools were called at some point in the trajectory, but an
    agent calling extra tools is still acceptable."
  - `subset` — "the inverse and is useful if you want to ensure that the agent did not call any tools
    beyond the expected ones."
- `tool_args_match_mode` / `tool_args_match_overrides`:

  ```python
  ToolArgsMatchMode = Literal["exact", "ignore", "subset", "superset"]
  ToolArgsMatchOverrides = dict[str, Union[ToolArgsMatchMode, list[str], Callable[[dict, dict], bool]]]
  ```

  > By default, only tool calls with the same arguments to the same tool are considered equal.
  > ... `tool_args_match_overrides` takes a dictionary whose keys are tool names and whose values are
  > either `"exact"`, `"ignore"`, **a list of fields within the tool call that must match exactly**, or
  > a comparator function.

The per-tool "list of fields that must match" is the same mechanism as tau2's `Action.compare_args`
(see [wire-formats-and-action-lists.md](./wire-formats-and-action-lists.md) §5). Two independent
projects landed on it, which is a decent signal that **exact argument equality is not a usable
default for judging** and any format meant to be judged should make partial-argument comparison easy.

What this implies for the record shape: a judgeable call list needs, at minimum, **an ordered list of
`(tool_name, arguments_object)` with stable ordering and a per-call identity**. Everything else
(timing, ids, token counts) is diagnostic, not judgeable.
