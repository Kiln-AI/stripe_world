# Telemetry conventions: OpenTelemetry GenAI and OpenInference

All content below was fetched on **2026-09-14** from the `main` branches of the respective
repositories, unless a pinned tag is named.

---

## 1. OpenTelemetry GenAI semantic conventions

### 1.1 Status as of 2026 — they moved, and nothing is stable

The GenAI conventions **no longer live in `open-telemetry/semantic-conventions`**. That repo's
`docs/gen-ai/gen-ai-spans.md` on `main` now contains only:

> # Moved: Generative AI semantic conventions
>
> \> [!IMPORTANT]
> \>
> \> GenAI semantic conventions have moved to the
> \> [OpenTelemetry GenAI semantic conventions repository](https://github.com/open-telemetry/semantic-conventions-genai).
> \> This page has moved and is no longer maintained in this repository.

— [semantic-conventions/docs/gen-ai/gen-ai-spans.md @ main](https://github.com/open-telemetry/semantic-conventions/blob/main/docs/gen-ai/gen-ai-spans.md)

The new repo describes itself as:

> Semantic Conventions for Generative AI (GenAI), including spans, metrics, and events for GenAI
> clients, MCP (Model Context Protocol), and provider-specific conventions (OpenAI, etc.).
> ...
> ## Schema URL
>
> TODO

— [semantic-conventions-genai/README.md @ main](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/README.md)

Two facts worth carrying into a design decision:

1. **The repo has no releases and no schema URL yet.** `https://github.com/open-telemetry/semantic-conventions-genai/releases`
   renders "There aren't any releases here" (fetched 2026-09-14). The `README.md` section for
   Schema URL is literally `TODO`. So there is *no* versioned artifact to pin against, unlike the
   core semconv `v1.44.0`.
2. **Every `gen_ai.*` attribute is still `Development`.** In
   [`docs/registry/attributes/gen-ai.md` @ main](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/registry/attributes/gen-ai.md)
   I counted 118 occurrences of the `development-blue` badge and **0** of the `stable-lightgreen`
   badge. `error.type`, `server.address`, `network.transport` etc. are stable, but those are
   borrowed from core semconv, not GenAI-specific.

**Inference (mine, labelled):** treating `gen_ai.tool.*` as a *naming* reference is safe; treating it
as a stable wire contract to serialize into a durable artifact is not, because the names can still
change and there is no schema URL to record which revision you followed.

### 1.2 The `execute_tool` span

Verbatim from [`docs/gen-ai/gen-ai-spans.md` @ main](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-spans.md):

> **Status:** Development
>
> Describes tool execution span.
>
> `gen_ai.operation.name` SHOULD be `execute_tool`.
>
> **Span name** SHOULD be `execute_tool {gen_ai.tool.name}`.
>
> **Span kind** SHOULD be `INTERNAL`.
>
> **Span status** SHOULD follow the Recording Errors document.

Attribute table (verbatim field set, requirement levels preserved):

| Attribute | Req. level | Type | Notes |
| --- | --- | --- | --- |
| `gen_ai.operation.name` | Required | string | value `execute_tool` |
| `gen_ai.tool.name` | Required | string | "Name of the tool utilized by the agent." e.g. `Flights` |
| `error.type` | Conditionally Required "If the operation ended in an error." | string | low-cardinality error class, e.g. `timeout`, `500` |
| `gen_ai.agent.name` | Conditionally Required "When applicable." | string | |
| `gen_ai.tool.call.id` | Recommended "If available." | string | "The tool call identifier." e.g. `call_mszuSIzqtI65i1wAUOE8w5H4` |
| `gen_ai.tool.description` | Recommended "If available." | string | flagged "may contain sensitive information" |
| `gen_ai.tool.type` | Recommended "If available." | string | `function` \| `extension` \| `datastore` |
| `gen_ai.tool.call.arguments` | **Opt-In** | any (object) | |
| `gen_ai.tool.call.result` | **Opt-In** | any (object) | "The result returned by the tool call (if any and if execution was successful)." |

Timing is implicit in the span (start/end timestamps); ordinal is implicit in the trace ordering.
There is no explicit step index attribute.

`gen_ai.tool.type` values, verbatim:

> Extension: A tool executed on the agent-side to directly call external APIs, bridging the gap
> between the agent and real-world systems. ... Function: A tool executed on the client-side, where
> the agent generates parameters for a predefined function, and the client executes the logic. ...
> Datastore: A tool used by the agent to access and query structured or unstructured external data
> for retrieval-augmented tasks or knowledge updates.

Sampling hint (verbatim):

> The following attributes can be important for making sampling decisions and SHOULD be provided
> **at span creation time** (if provided at all):
> * `gen_ai.agent.name` * `gen_ai.operation.name` * `gen_ai.tool.name` * `gen_ai.tool.type`

### 1.3 Arguments and results: inline, opt-in, object-shaped

Verbatim note on `gen_ai.tool.call.arguments` (same wording for `.result`):

> It's expected to be an object - in case a serialized string is available to the instrumentation,
> the instrumentation SHOULD do the best effort to deserialize it to an object.
>
> Instrumentations MUST follow [JSON schema](/model/gen-ai/gen-ai-tool-call-arguments.json).
>
> When the attribute is recorded on events, it MUST be recorded in structured form. When recorded on
> spans, it MAY be recorded as a JSON string if structured format is not supported and SHOULD be
> recorded in structured form otherwise.

The referenced JSON schemas are deliberately *empty* — they only constrain the top-level type:

```json
{
    "additionalProperties": true,
    "description": "Represents object-like arguments passed to a tool call.",
    "title": "ToolCallArguments",
    "type": "object"
}
```
— [`model/gen-ai/gen-ai-tool-call-arguments.json`](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/model/gen-ai/gen-ai-tool-call-arguments.json);
`gen-ai-tool-call-result.json` is identical modulo the title/description.

**Takeaway:** OTel says "arguments and results are JSON objects, keep them structured, don't flatten
them to a string unless your transport forces you to." It imposes no schema on the contents.

### 1.4 Tool call / result inside message content

The message schema (used by `gen_ai.input.messages` / `gen_ai.output.messages`) models a tool call as
a *content part*, not a top-level message. From
[`model/gen-ai/gen-ai-input-messages.json`](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/model/gen-ai/gen-ai-input-messages.json):

```
ToolCallRequestPart  = { type: "tool_call" (const), id: string|null, name: string (required), arguments: any }
ToolCallResponsePart = { type: "tool_call_response" (const), id: string|null, response: any (required) }
```

Both have `additionalProperties: true`. Note `name` is required on the request part but *absent*
from the response part — the response is joined to the call by `id`. There are also
`ServerToolCallPart` / `ServerToolCallResponsePart` for provider-side tools (web search, code
execution), so the convention distinguishes client-executed from provider-executed tools in two
places: the part type and `gen_ai.tool.type`.

### 1.5 Size handling — this is the most transferable part

Verbatim from the "Capturing instructions, inputs, and outputs" section:

> Model instructions, user messages, and model outputs are considered sensitive and are often large
> in size.
>
> Recording large or sensitive content in telemetry may be problematic due to high storage costs,
> regulatory requirements, or the need to enforce different access models for operational and user
> data.
>
> OpenTelemetry instrumentations SHOULD NOT capture them by default, but SHOULD provide an option
> for users to opt in.
>
> Application developers should choose an appropriate usage pattern based on application needs and
> maturity:
>
> 1. [Default] Don't record instructions, inputs, or outputs.
> 2. Record instructions, inputs, and outputs on the GenAI spans using corresponding attributes ...
>    This approach is best suited for situations where telemetry volume is manageable and either
>    privacy regulations do not apply or the telemetry storage complies with them, for example, in
>    pre-production environments.
> 3. Store content externally and record references on the spans.
>
>    This pattern is recommended in production environments where telemetry volume is a concern or
>    sensitive data needs to be handled securely. Using external storage enables separate access
>    controls.

On truncation:

> It may contain media, and even in the text form, it may be larger than observability backend
> limits for telemetry envelopes or attribute values.
> ...
> Instrumentation MAY provide a configuration option allowing to truncate properties such as
> individual message contents, **preserving JSON structure**.

On external storage:

> Instrumentations MAY support user-defined in-process hooks to handle content upload. The hook
> SHOULD operate independently of the opt-in flags ... If such a hook is supported and configured,
> instrumentations SHOULD invoke it regardless of the span sampling decision ...
>
> **TODO: document a common approach to record references to externally stored content.**

That `TODO` is important and unflattering: **OTel has not standardized how to reference externalized
content.** There is no `gen_ai.*.ref` attribute, no URI convention. Anyone externalizing content
today invents their own reference format (OpenInference does — see §2.4).

Three named patterns, in escalating order — *don't record → record inline → record a reference* —
plus "truncate but keep the JSON structure" is, as far as I can find, the closest thing to an
industry consensus statement on this problem.

### 1.6 Aggregate counters beside the log: OTel GenAI metrics

From [`docs/gen-ai/gen-ai-metrics.md` @ main](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-metrics.md):

| Metric | Instrument | Unit | Attributes |
| --- | --- | --- | --- |
| `gen_ai.invoke_agent.tool_calls` | Histogram | `{tool_call}` | `gen_ai.agent.name` |
| `gen_ai.invoke_agent.inference_calls` | Histogram | `{inference_call}` | `gen_ai.agent.name` |
| `gen_ai.execute_tool.duration` | Histogram | `s` | `gen_ai.tool.name` (Required), `error.type` (CondReq), `gen_ai.agent.name`, `gen_ai.tool.type` |
| `gen_ai.client.token.usage` | Histogram | `{token}` | |
| `gen_ai.client.operation.duration` | Histogram | `s` | |

The counting rules are spelled out precisely, and they answer the "who owns a nested call" question:

> The distribution is scoped to a single agent invocation and SHOULD include only the tool calls the
> agent itself triggers **including failed ones**; calls made by sub-agents or transferred-to agents
> are recorded against those agents' own invocations so that each tool call is counted exactly once
> across the call tree.
>
> Only client-side tool calls (tools executed by the agent or framework) are counted. Tools executed
> server-side by the model provider (for example, provider built-in web search or code execution)
> are not counted here.

Recommended bucket boundaries for the two count histograms are `[1, 2, 4, 8, 16, 32, 64, 128]` —
a useful sanity check on expected trajectory lengths (OTel expects tens, not thousands, of tool
calls per agent invocation).

Note what is **absent**: there is no `gen_ai.*.tool_calls` *counter keyed by tool name*, and no
per-tool error counter. The per-tool breakdown exists only as the `gen_ai.tool.name` +
`error.type` dimensions on `gen_ai.execute_tool.duration`. So the conventional way to answer
"how many times was tool X called, and how many of those errored" is **to derive it from a
duration histogram's dimensions**, not from a dedicated counter.

### 1.7 MCP conventions (in the same repo)

[`docs/gen-ai/mcp.md` @ main](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/mcp.md)
defines client and server spans for MCP:

- **Span name**: `{mcp.method.name} {target}` — e.g. `tools/call get-weather`; fall back to
  `{mcp.method.name}` if no low-cardinality target.
- `mcp.method.name` Required (`tools/call`, `initialize`, …); `gen_ai.tool.name` Conditionally
  Required "When operation is related to a specific tool"; `jsonrpc.request.id` Conditionally
  Required; `mcp.session.id`, `mcp.protocol.version`, `network.transport` Recommended.
- `gen_ai.operation.name` "SHOULD be set to `execute_tool` when the operation describes a tool call
  and SHOULD NOT be set otherwise", with the rationale: "Populating this attribute for tool calling
  along with `mcp.method.name` allows consumers to treat MCP tool calls spans similarly with other
  tool call types."
- Arguments/results reuse the same Opt-In `gen_ai.tool.call.arguments` / `gen_ai.tool.call.result`.

The error mapping is the notable bit, because MCP has *two* error channels:

> When JSON-RPC call is successful, but an error is returned within the result payload, this
> attribute SHOULD be set to the low-cardinality string representation of the error. When
> `CallToolResult` is returned with `isError` set to `true`, this attribute SHOULD be set to
> `tool_error`.

So the convention is: a *protocol* failure gets the JSON-RPC code in `error.type`; a *tool* failure
(`isError: true`) gets the literal string `tool_error`. Two distinguishable classes of failure with
one low-cardinality field.

Metrics: `mcp.client.operation.duration`, `mcp.server.operation.duration` (both dimensioned by
`mcp.method.name`, `gen_ai.tool.name`, `error.type`), plus `mcp.client.session.duration` /
`mcp.server.session.duration`.

---

## 2. OpenInference (Arize)

Source: [`spec/semantic_conventions.md` @ main](https://github.com/Arize-ai/openinference/blob/main/spec/semantic_conventions.md)
and [`spec/configuration.md` @ main](https://github.com/Arize-ai/openinference/blob/main/spec/configuration.md),
both fetched 2026-09-14. OpenInference is an OTel-compatible attribute vocabulary, not an OTel
project; it predates and overlaps the `gen_ai.*` work.

### 2.1 Span kinds

`openinference.span.kind` is **required on all OpenInference spans**. Values:
`LLM`, `EMBEDDING`, `CHAIN`, `RETRIEVER`, `RERANKER`, `TOOL`, `AGENT`, `GUARDRAIL`, `EVALUATOR`,
`PROMPT`, `UNKNOWN`.

> `TOOL` — A span that represents a call to an external tool such as a calculator, weather API, or
> any function execution that is invoked by an LLM or agent.

### 2.2 Tool attributes

On a `TOOL` span:

| Attribute | Type | Description (verbatim) |
| --- | --- | --- |
| `tool.name` | String | "The name of the tool. On a `TOOL` span, the tool being invoked; under `llm.tools.<index>`, the name of an advertised tool definition" |
| `tool.description` | String | "Description of the tool's purpose and functionality." |
| `tool.id` | String | "The identifier for the result of the tool call (corresponding to `tool_call.id`)" |
| `tool.parameters` | JSON string | "The parameters definition for invoking the tool" |
| `tool.json_schema` | JSON String | "The json schema of a tool input" |
| `input.value` / `input.mime_type` | String | the actual call input; mime type `text/plain` or `application/json` |
| `output.value` / `output.mime_type` | String | the actual tool result |

And for calls carried inside LLM messages:

| Attribute | Type | Description |
| --- | --- | --- |
| `tool_call.id` | string | "The id of the a tool call (useful when there are more than one call at the same time)" |
| `tool_call.function.name` | String | "The name of the function being invoked by a tool call" |
| `tool_call.function.arguments` | **JSON string** | "The arguments for the function being invoked by a tool call" |
| `message.tool_call_id` | String | "Tool call result identifier corresponding to `tool_call.id`" |
| `message.name` | String | "The name of the function or tool that produced a tool/function role message." |

Note the contrast with OTel: **OpenInference stores arguments as a JSON *string*, not an object.**
That is a direct consequence of OTel's attribute model not supporting nested values (see the OTEP
referenced in the OTel doc), and OpenInference chose flattening + indexed keys rather than waiting:

> `llm.output_messages.<messageIndex>.message.tool_calls.<toolCallIndex>.tool_call.function.arguments`
> — JSON string of function arguments

The `<index>` in the key *is* the ordinal. There is no separate step-number attribute.

### 2.3 No dedicated error field

I could not find an OpenInference `tool.error` / `tool.status` attribute in
`spec/semantic_conventions.md`. Errors ride on the OTel span status and the standard
`exception.*` event. **Gap noted rather than guessed.**

### 2.4 Size handling — an explicit byte cap plus a blob uploader

From [`spec/configuration.md` @ main](https://github.com/Arize-ai/openinference/blob/main/spec/configuration.md):

| Env var | Meaning (verbatim) | Type | Default |
| --- | --- | --- | --- |
| `OPENINFERENCE_BASE64_IMAGE_MAX_LENGTH` | "Limits characters of a base64 encoding of an image" | int | **32,000** |
| `OPENINFERENCE_BLOB_UPLOADER` | "Names a `BlobUploader` registered under the `openinference_blob_uploader` entry-point group; base64 images larger than `OPENINFERENCE_BASE64_IMAGE_MAX_LENGTH` are handed to it and the span attribute records the returned URI instead of being redacted" | str | unset |
| `OPENINFERENCE_HIDE_INPUTS` / `_OUTPUTS` / `_INPUT_MESSAGES` / `_OUTPUT_MESSAGES` / `_LLM_TOOLS` / … | redaction switches | bool | False |
| `OPENINFERENCE_HIDE_EMBEDDINGS_VECTORS` | "Replaces `embedding.embeddings.*.embedding.vector` values with `\"__REDACTED__\"`" | bool | False |

And the rationale, verbatim:

> Large binary content captured as base64 data URIs can exceed span attribute and OTLP payload
> limits. Instead of redacting oversized media, an instrumentation MAY upload the decoded bytes to
> external storage at capture time and record only a reference URI in the span attribute. Today this
> applies to **images** ... Audio and video hide flags and size gates wait on a `TraceConfig`
> follow-up; shared `mask()` still only handles images.

So OpenInference implements exactly OTel's pattern 3 (externalize + reference), for images only, with
a named default threshold (32k chars) and a sentinel value (`__REDACTED__`) when the content is
suppressed rather than externalized. **Unflattering note:** it is images-only; oversized *text* tool
results are not capped by any OpenInference setting I could find.

---

## Cross-cutting observations

1. **Both vocabularies model the call and its result as one record** (one span), keyed by a
   provider-issued `tool_call.id` that also appears in the model's message stream. The id is the
   join key between "what the model asked for" and "what the harness did".
2. **Ordinal is implicit** — trace ordering in OTel, flattened index in OpenInference. Neither
   defines a durable "step number". For a *saved artifact* (as opposed to a trace queried by a
   backend), an explicit integer ordinal is an obvious addition; the conventions simply don't need
   one because their storage layer preserves ordering.
3. **Both treat the result payload as opt-in and potentially externalized.** Neither makes it a
   required field.
4. **Per-tool aggregates are dimensions on a duration histogram**, not standalone counters.
5. **Neither standardizes a hash of a large result.** OTel leaves the reference format as a TODO;
   OpenInference records an uploader-returned URI. If Seahaven wants content addressing, it is
   inventing the convention, not adopting one — but Inspect AI has a working precedent
   (see [inspect-and-langsmith.md](./inspect-and-langsmith.md)).
