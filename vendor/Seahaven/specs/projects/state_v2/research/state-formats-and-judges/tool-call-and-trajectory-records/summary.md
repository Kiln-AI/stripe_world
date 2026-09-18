# Tool-call and trajectory records

## Bottom Line

There is no standard for a tool-call record. Across eight surveyed formats the only universal fields
are **tool name, arguments, and an id joining the call to its result**; errors are modelled four
incompatible ways, an explicit ordinal appears in two of eight, and timing in three. What *is*
consistent is the size discipline: every format treats the **result payload** as the field that will
blow up storage, and handles it with one of four escalating patterns — don't record it, record it
capped, record a reference to it, or hash the whole state. The nearest thing to a versioned
trajectory standard is **ATIF** (Harbor RFC 0001, v1.8, April 2026; implemented by NVIDIA's NeMo
Agent Toolkit), a root object with `schema_version`, a `steps` array, and a `final_metrics` block of
aggregate counters — but ATIF has **no error field on a tool result at all**, so it is a structural
model, not a schema to adopt. For Seahaven the concrete recommendation is a ~6-field per-call record
(`i`, `tool`, `args` as a parsed object, `ok`/`error{code,message}`, `ms`) with **tool results omitted
by default** because `changes()` is the authoritative signal and a result would duplicate it, plus a
small derived `counters` block (`steps`, `calls`, `errors`, `by_tool`, `errors_by_tool`) that stays
exact even if the call list is ever bounded.

## Key Findings

- **OpenTelemetry's GenAI conventions moved out of the main semconv repo and are still entirely
  unstable.** `open-telemetry/semantic-conventions/docs/gen-ai/*` now reads "Moved: ... no longer
  maintained in this repository", pointing at
  [`semantic-conventions-genai`](https://github.com/open-telemetry/semantic-conventions-genai). That repo has
  **no releases**, its README's Schema URL section is literally `TODO`, and I counted 118
  `Development` badges and **0** `Stable` badges in its `gen_ai` attribute registry (fetched
  2026-09-14). Safe as a naming reference; not pinnable as a durable contract.
- **The `execute_tool` span is the canonical OTel shape**: `gen_ai.operation.name` = `execute_tool`
  and `gen_ai.tool.name` Required; `error.type`, `gen_ai.tool.call.id`, `gen_ai.tool.type`
  (`function`/`extension`/`datastore`) Recommended; **`gen_ai.tool.call.arguments` and
  `gen_ai.tool.call.result` are Opt-In** and "expected to be an object" —
  [gen-ai-spans.md](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-spans.md).
- **OTel names the three content-size patterns explicitly** — "[Default] Don't record ... / Record ...
  on the GenAI spans ... / Store content externally and record references on the spans" — and then
  admits it has not standardized the third: *"TODO: document a common approach to record references
  to externally stored content."* Truncation guidance is "truncate properties ... **preserving JSON
  structure**". ([same doc](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-spans.md))
- **Per-tool aggregates are conventionally histogram *dimensions*, not counters.** OTel has
  `gen_ai.invoke_agent.tool_calls` (a Histogram, buckets `[1,2,4,8,16,32,64,128]`) and
  `gen_ai.execute_tool.duration` dimensioned by `gen_ai.tool.name` + `error.type` — there is no
  "calls per tool" counter. Counting rules are precise: count "only the tool calls the agent itself
  triggers **including failed ones**", sub-agent calls against their own invocation, provider
  server-side tools not at all.
  ([gen-ai-metrics.md](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-metrics.md))
- **No surveyed eval harness keeps per-tool call or error counts as a field.** Inspect's sample
  summary keeps `message_count`, `turn_count`, two clocks and `model_usage`; ATIF's `final_metrics`
  keeps four token totals, a cost and `total_steps`; tau2's run keeps two costs, duration,
  termination reason, trial, seed. Per-tool breakdowns are derived by scanning the log.
- **Inspect AI has the richest per-call record and the best size design.** `ToolEvent` carries
  `id`, `function`, `arguments` (object), `result`, **`truncated: (raw_bytes, cap)`**,
  `error: ToolCallError{type ∈ 11-value enum, message}`, `failed: bool`, `timestamp`, `completed`,
  `working_time`, `agent`, `span_id`, `uuid`. Results are capped at **16 KiB by default**
  (`max_tool_output`, overridable per tool), and *any string over 100 chars inside an event* is
  replaced by `attachment://<murmur3-128 hex>` with content stored once in `sample.attachments`.
  ([_tool.py](https://github.com/UKGovernmentBEIS/inspect_ai/blob/main/src/inspect_ai/event/_tool.py),
  [_call_tools.py](https://github.com/UKGovernmentBEIS/inspect_ai/blob/main/src/inspect_ai/model/_call_tools.py),
  [_condense.py](https://github.com/UKGovernmentBEIS/inspect_ai/blob/main/src/inspect_ai/log/_condense.py))
- **LangSmith has exactly one record type for everything.** A tool call is a `Run` with
  `run_type="tool"`, `inputs`, `outputs`, `error: str`. Its `dotted_order` — `{time}{run-uuid}`
  joined by `.` — is a single lexicographically sortable string encoding both time order and tree
  position. Size: a 20 MB batch cap (`_SIZE_LIMIT_BYTES = 20_971_520`), and externalization is
  first-class in the wire type via `inputs_s3_urls` / `outputs_s3_urls`.
  ([schemas.py](https://github.com/langchain-ai/langsmith-sdk/blob/main/python/langsmith/schemas.py))
- **MCP 2026-07-28 returns *two* representations of every result** — `content: ContentBlock[]` for the
  model and `structuredContent?: unknown` validated against `Tool.outputSchema` for programs — plus
  `isError?: boolean`, with the normative rule that tool errors go in the result (so the LLM "would
  ... see that an error occurred and self-correct") while errors *finding* the tool go to JSON-RPC.
  Large results are externalized via a `resource_link` content block; **there are no size caps,
  truncation rules or pagination on `tools/call`.**
  ([schema.ts](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/schema/2026-07-28/schema.ts))
- **Provider wire formats carry no timing and no ordinal.** Anthropic: `tool_use{id, name, input:
  dict}` / `tool_result{tool_use_id, content, is_error: bool}`. OpenAI Responses:
  `function_call{id, call_id, name, arguments: str, status}` / `function_call_output{call_id, output,
  status, name}` — arguments are a **string**, and the SDK's own docstring warns "the model does not
  always generate valid JSON". That single fact is why every downstream format must decide between
  storing the raw string and the parsed object.
- **ATIF is the versioned trajectory contract.** Root: `schema_version` (Required, e.g. `"ATIF-v1.7"`),
  `agent{name, version, tool_definitions?}`, `steps[]`, `final_metrics?`, `subagent_trajectories?`.
  `StepObject` has a Required integer `step_id` ("Ordinal index of the turn (starting from 1)"),
  `tool_calls[]`, `observation.results[]`, and a v1.7 `llm_call_count` whose `0`/`1`/`>1`/`null`
  semantics distinguish deterministic dispatch, attributable metrics, aggregated metrics, and
  untracked. Its cost stance is the principle to copy for any aggregate in a long-lived artifact:
  record the value measured at run time, never the pricing needed to recompute it, because "pricing
  can change over time, making historical trajectories inaccurate".
  ([RFC 0001](https://github.com/harbor-framework/harbor/blob/main/rfcs/0001-trajectory-format.md))
- **Letta's `trajectory` package has the most concrete bounding policy published anywhere:** defaults
  of **20,000 code points for arguments** and **2,500 code points for results with a `"head-tail"`
  strategy**, a marker `\n… [truncated, N more chars]` that is **counted inside the budget**, and a
  machine-readable `diagnostics` list (`tool_result_truncated`, `tool_arguments_truncated`,
  `tool_call_id_synthesized`, `orphan_tool_result`, …) so nothing is dropped silently. Its `ok:
  boolean` is deliberately optional: "result text is never interpreted as success or failure".
  ([letta-ai/trajectory](https://github.com/letta-ai/trajectory))
- **Two independent projects invented per-tool partial-argument matching**, which says exact argument
  equality is not a usable judging default: tau2's `Action.compare_args` ("The arguments to check in
  tool call. If None, will check all the arguments") and AgentEvals'
  `tool_args_match_overrides`, whose values may be `"exact"`, `"ignore"`, **a list of fields that
  must match**, or a comparator function. AgentEvals also names the four trajectory comparison modes:
  `strict` / `unordered` / `subset` / `superset`.
- **tau-bench's action record is two fields** — `Action{name, kwargs}` — and its grader compares a
  canonicalized SHA-256 of the whole database against the hash produced by replaying the ground-truth
  action list. Cheap and portable-looking, but `consistent_hash` is `sha256(str(tuple))`, which is not
  a stable serialization outside the producing process, and a mismatch yields no diagnostic.

## Details

- [otel-genai-and-openinference.md](./otel-genai-and-openinference.md) — the OTel GenAI conventions'
  2026 status and repo split, the full `execute_tool` attribute table, the content-capture patterns
  and their `TODO`, the GenAI/MCP metrics, and OpenInference's flattened `tool_call.*` vocabulary with
  its 32,000-char base64 cap and blob-uploader. Read when you need the exact attribute names or the
  official stance on recording payloads.
- [eval-harness-records.md](./eval-harness-records.md) — Inspect AI's `ToolEvent`, `ToolCallError`
  enum, 16 KiB truncation and `attachment://` content-addressing; LangSmith's `RunBase`/`Run` field
  list, `dotted_order`, and size constants; the OpenAI Agents SDK's minimal `FunctionSpanData`; and
  OTel's generic SDK attribute limits (128 attributes, depth 64, **value length default Infinity**).
  Read when designing the record's fields or its size policy.
- [wire-formats-and-action-lists.md](./wire-formats-and-action-lists.md) — verbatim type definitions
  for Anthropic `tool_use`/`tool_result`, OpenAI Responses and Chat Completions, MCP `tools/call`
  (2026-07-28) including `ToolAnnotations`, and tau-bench / tau2 action and message models. Ends with
  an eight-source side-by-side table of every field. Read when you need to know what a specific
  protocol actually puts on the wire.
- [trajectory-container-formats.md](./trajectory-container-formats.md) — ATIF in full (root,
  `StepObject`, `FinalMetricsSchema`, `ToolCallSchema`, the context-management boundary rule), Letta's
  `trajectory` v1 JSON schema and bounds/diagnostics implementation, and AgentEvals' trajectory and
  tool-arg match modes. Read when designing the container around the calls, or the versioning and
  bounding story.
- [recommendation-for-seahaven.md](./recommendation-for-seahaven.md) — the synthesis: proposed record
  and counter shapes, why results should be omitted by default, why an explicit ordinal is worth its
  bytes, why no call id is needed, and a ranked list of bounding options. Read this first if you only
  read one.

## Open Questions / Gaps

- **LangSmith's *product* limits are unverified.** `docs.langchain.com` and `docs.smith.langchain.com`
  are blocked by this session's egress proxy. The 20 MB batch cap and the S3-URL fields come from the
  SDK source, which is what the *client* enforces; the hosted service may enforce more.
- **`inspect.aisi.org.uk` and `opentelemetry.io` are also blocked.** Everything attributed to Inspect
  AI and to OTel here was read from source and spec markdown on GitHub instead, which is the primary
  source anyway, but I could not cross-check against the rendered docs.
- **The NVIDIA NeMo ATIF implementation is search-derived, not primary-verified.** `docs.nvidia.com`
  is blocked and I could not find `src/nat/atif/` at the paths I tried in the public
  `NVIDIA/NeMo-Agent-Toolkit` GitHub tree. The claim "NeMo Agent Toolkit 1.8 sets
  `ATIF_VERSION = 'ATIF-v1.7'` and accepts v1.0–v1.7" rests on search-result excerpts of the API docs.
- **I could not date the OTel GenAI repo split.** The GitHub API rate-limited my metadata requests and
  the GitHub MCP tools are restricted to `kiln-ai/seahaven` in this session. I can confirm the split
  has happened as of 2026-09-14 and that the new repo has zero releases; I cannot say when.
- **No precedent found for bounding the *number* of calls.** Every format bounds per-payload size;
  none I found caps or samples the call list itself. The head-tail-on-the-list suggestion in the
  recommendation is my construction by analogy to Letta's head-tail string truncation and ATIF's
  in-band context boundary, not an observed convention.
- **No standard reference format for externalized content.** OTel says `TODO`; OpenInference records
  an uploader-returned URI; LangSmith uses S3 URLs; MCP uses `resource_link`; Inspect uses
  `attachment://<hash>`. Four answers, no convention.

## Sources

- [semantic-conventions-genai @ main](https://github.com/open-telemetry/semantic-conventions-genai) —
  authoritative for `gen_ai.*` span/metric/attribute names, the `execute_tool` span, and the
  content-capture patterns. No releases; all GenAI attributes `Development` as of 2026-09-14.
  (`docs/gen-ai/gen-ai-spans.md`, `gen-ai-metrics.md`, `mcp.md`, `docs/registry/attributes/gen-ai.md`,
  `model/gen-ai/*.json`)
- [semantic-conventions/docs/gen-ai/gen-ai-spans.md @ main](https://github.com/open-telemetry/semantic-conventions/blob/main/docs/gen-ai/gen-ai-spans.md)
  — the "Moved" notice confirming the split.
- [opentelemetry-specification/specification/common/README.md @ main](https://github.com/open-telemetry/opentelemetry-specification/blob/main/specification/common/README.md)
  — authoritative for SDK attribute limits and truncation semantics.
- [Arize-ai/openinference `spec/semantic_conventions.md` + `spec/configuration.md` @ main](https://github.com/Arize-ai/openinference)
  — authoritative for OpenInference span kinds, `tool.*` / `tool_call.*` attributes, and the
  `OPENINFERENCE_*` redaction/size env vars.
- [UKGovernmentBEIS/inspect_ai @ main](https://github.com/UKGovernmentBEIS/inspect_ai) — authoritative
  for `ToolEvent`, `ToolCallError`, `EvalSample`/`EvalSampleSummary`, `max_tool_output`, and the
  `attachment://` condensation scheme.
- [langchain-ai/langsmith-sdk @ main](https://github.com/langchain-ai/langsmith-sdk) — authoritative
  for the `Run`/`RunBase` schema, `dotted_order`, and client-side size constants.
- [modelcontextprotocol `schema/2026-07-28/schema.ts`](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/schema/2026-07-28/schema.ts)
  — authoritative for `CallToolRequest`/`CallToolResult`, `ContentBlock`, `ResourceLink`, `Tool`,
  `ToolAnnotations`. Current stable revision per
  [blog.modelcontextprotocol.io/posts/2026-07-28](https://blog.modelcontextprotocol.io/posts/2026-07-28/).
- [anthropics/anthropic-sdk-python @ main](https://github.com/anthropics/anthropic-sdk-python) —
  `types/tool_use_block.py`, `types/tool_result_block_param.py`. Supplemented by the bundled
  `claude-api` skill (v2.1.270) for usage guidance on `is_error` and parallel tool use.
- [openai/openai-python @ main](https://github.com/openai/openai-python) — `types/responses/*` and
  `types/chat/*`; generated from OpenAI's OpenAPI spec.
- [openai/openai-agents-python @ main](https://github.com/openai/openai-agents-python) —
  `tracing/span_data.py` (`FunctionSpanData`, `TurnSpanData`, `TaskSpanData`).
- [sierra-research/tau-bench @ main](https://github.com/sierra-research/tau-bench) — `tau_bench/types.py`,
  `tau_bench/envs/base.py` (`Action`, `EnvRunResult`, `consistent_hash`).
- [sierra-research/tau2-bench @ main](https://github.com/sierra-research/tau2-bench) —
  `src/tau2/data_model/{message,tasks,simulation}.py` (`ToolCall`, `ToolMessage`, `Action.compare_args`,
  `SimulationRun`).
- [harbor-framework/harbor `rfcs/0001-trajectory-format.md` @ main](https://github.com/harbor-framework/harbor/blob/main/rfcs/0001-trajectory-format.md)
  — the ATIF specification. Status Active, maintainer Boxuan Li, dated April 2026, changelog v1.8.
- [letta-ai/trajectory @ main](https://github.com/letta-ai/trajectory) — README,
  `schema/trajectory-v1.schema.json`, `src/types.ts`, `src/bounds.ts`, `src/core.ts`. Authoritative
  for the normalized record shape and the default bounds / truncation strategy.
- [langchain-ai/agentevals @ main](https://github.com/langchain-ai/agentevals) — README; authoritative
  for `trajectory_match_mode` and `tool_args_match_mode` / `tool_args_match_overrides`.
