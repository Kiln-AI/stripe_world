# Eval-harness and tracing-platform records: Inspect AI, LangSmith, OpenAI Agents SDK

Fetched 2026-09-14 from `main` branches unless otherwise noted.

---

## 1. Inspect AI (`UKGovernmentBEIS/inspect_ai`) — the richest per-call record I found

Inspect is the AISI eval framework. Its saved artifact is an `EvalLog`; per-sample it carries
`messages` (the chat history) *and* `events` (a structured transcript). The two are
complementary — messages are what the model saw, events are what the harness did.

### 1.1 `ToolEvent` — the per-call record

Verbatim from [`src/inspect_ai/event/_tool.py` @ main](https://github.com/UKGovernmentBEIS/inspect_ai/blob/main/src/inspect_ai/event/_tool.py):

```python
class ToolEvent(BaseEvent):
    """Call to a tool."""
    event: Literal["tool"] = Field(default="tool")      # Event type.
    type: Literal["function"] = Field(default="function")  # Type of tool call (currently only 'function')
    id: str                                            # Unique identifier for tool call.
    function: str                                      # Function called.
    arguments: dict[str, JsonValue]                    # Arguments to function.
    view: ToolCallContent | None = None                # Custom view of tool call input.
    result: ToolResult = Field(default_factory=str)    # Function return value.
    truncated: tuple[int, int] | None = None           # Bytes truncated (from,to) if truncation occurred
    error: ToolCallError | None = None                 # Error that occurred during tool call.
    events: list[Any] = Field(default_factory=list)    # (deprecated) sub-events
    completed: UtcDatetime | None = None               # Time that tool call completed (see `timestamp` for started)
    working_time: float | None = None                  # Working time (i.e. time not spent waiting on semaphores).
    agent: str | None = None                           # Name of agent if the tool call was an agent handoff.
    agent_span_id: str | None = None                   # Span ID of the agent span, if this tool call spawned an agent.
    failed: bool | None = None                         # Did the tool call fail with a hard error?
    message_id: str | None = None                      # Id of ChatMessageTool associated with this event.
```

Inherited from `BaseEvent` ([`_base.py`](https://github.com/UKGovernmentBEIS/inspect_ai/blob/main/src/inspect_ai/event/_base.py)):

```python
class BaseEvent(BaseModel):
    uuid: str | None = None          # Unique identifer for event.
    span_id: str | None = None       # Span the event occurred within.
    timestamp: UtcDatetime           # Clock time at which event occurred.
    working_start: float             # Working time (within sample) at which the event occurred.
    metadata: dict[str, Any] | None = None
    pending: bool | None = None      # Is this event pending?
```

Things worth naming explicitly:

- **Two clocks.** `timestamp` (wall clock, start) + `completed` (wall clock, end) *and*
  `working_start` / `working_time` — "working time" excludes time spent waiting on semaphores and
  rate limits. An eval that is concurrency-limited has wall-clock durations that mean nothing; the
  working clock is the one you can compare across runs.
- **Two error channels.** `error: ToolCallError | None` (a *typed* error the model sees) and
  `failed: bool | None` ("Did the tool call fail with a hard error?"). These are different: a tool
  can return an error to the model without the harness considering the call failed.
- **Truncation is recorded in-band** as `truncated: (raw_bytes, truncated_bytes)` — the record
  carries the original size, so a consumer can tell that what it is reading is not the whole result
  *and by how much*.
- **No ordinal field.** Order is list order in `EvalSample.events`. There is a `span_id` for tree
  structure and a `uuid` for identity, but no integer step index.

### 1.2 `ToolCallError` — a closed vocabulary of error types

From [`src/inspect_ai/tool/_tool_call.py` @ main](https://github.com/UKGovernmentBEIS/inspect_ai/blob/main/src/inspect_ai/tool/_tool_call.py):

```python
@dataclass
class ToolCallError:
    """Error raised by a tool call."""
    type: Literal[
        "parsing", "timeout", "unicode_decode", "permission", "file_not_found",
        "is_a_directory", "limit", "approval", "cancelled", "sandbox_unavailable",
        "unknown",
        # Retained for backward compatibility when loading logs created with an older
        # version of inspect.
        "output_limit",
    ]
    message: str
```

Two design points transfer directly: **a small closed enum of error kinds plus a free-text
message**, and **retired enum members are kept, with a comment saying why** ("Retained for backward
compatibility when loading logs created with an older version of inspect") rather than deleted. That
is the versioning discipline a long-lived artifact needs.

### 1.3 Size handling: a hard byte cap with a default, overridable per tool

From [`src/inspect_ai/model/_call_tools.py` @ main](https://github.com/UKGovernmentBEIS/inspect_ai/blob/main/src/inspect_ai/model/_call_tools.py):

```python
def truncate_tool_output(tool_name, output, max_output) -> TruncatedToolOutput | None:
    active_max_output = max_output
    if active_max_output is None:
        active_max_output = active_generate_config().max_tool_output
        if active_max_output is None:
            active_max_output = 16 * 1024
    truncated = truncate_string_to_bytes(output, active_max_output)
    if truncated:
        truncated_output = dedent("""
            The output of your call to {tool_name} was too long to be displayed.
            Here is a truncated version:
            <START_TOOL_OUTPUT>
            {truncated_output}
            <END_TOOL_OUTPUT>
            """).format(...)
        return TruncatedToolOutput(truncated_output, truncated.original_bytes, active_max_output)
```

And the config field ([`_generate_config.py`](https://github.com/UKGovernmentBEIS/inspect_ai/blob/main/src/inspect_ai/model/_generate_config.py)):

> `max_tool_output: int | None` — "Maximum tool output (in bytes). Defaults to 16 * 1024."

Resolution order is per-tool `ToolDef.max_output` → run-level `GenerateConfig.max_tool_output` →
**16 KiB**. Note this truncation serves the model's context *and* the log at once — the truncated
string is what goes back to the model, and `truncated=(raw_bytes, cap)` is what goes in the record.

Related caps in the same file: tool-call *arguments* are middle-truncated to 16 KiB when a parse
error message must be echoed back, and `MAX_TOOL_CALL_ARGUMENTS_DEPTH = 100` caps argument nesting
depth.

### 1.4 Size handling, part two: content-addressed attachments

`EvalSample` carries ([`src/inspect_ai/log/_log.py` @ main](https://github.com/UKGovernmentBEIS/inspect_ai/blob/main/src/inspect_ai/log/_log.py)):

```python
    attachments: dict[str, str] = Field(default_factory=dict)
    """Attachments referenced from messages and events.

    Resolve attachments for a sample (replacing attachment://* references with
    attachment content) by passing `resolve_attachments=True` to log reading functions.
    """
```

The condensation rule, verbatim from
[`src/inspect_ai/log/_condense.py` @ main](https://github.com/UKGovernmentBEIS/inspect_ai/blob/main/src/inspect_ai/log/_condense.py):

```python
ATTACHMENT_PROTOCOL = "attachment://"

def events_attachment_fn(attachments, log_images=True):
    create_attachment = attachment_fn(attachments)
    # for events, we want to strip images when requested and
    # create attachments for text > 100
    def fn(text: str) -> str:
        if not log_images and is_data_uri(text):
            return BASE_64_DATA_REMOVED
        elif len(text) > 100:
            return create_attachment(text)
        else:
            return text
    return fn

def attachment_fn(attachments):
    def create_attachment(text: str) -> str:
        hash = mm3_hash(text)
        attachments[hash] = text
        return f"{ATTACHMENT_PROTOCOL}{hash}"
    return create_attachment
```

`mm3_hash` ([`_util/hash.py`](https://github.com/UKGovernmentBEIS/inspect_ai/blob/main/src/inspect_ai/_util/hash.py))
is MurmurHash3 128-bit rendered as 32 hex chars — a *non-cryptographic* content hash, chosen for
speed, since the purpose is deduplication rather than integrity.

So the working rules are:

| Rule | Threshold |
| --- | --- |
| Any string inside an **event** longer than 100 chars | replaced by `attachment://<mm3-128 hex>`; content stored once in `sample.attachments` |
| Base64 data URIs inside **messages** | become attachments (or `BASE_64_DATA_REMOVED` if `log_images=False`) |
| A tool result over `max_tool_output` bytes | truncated before it ever reaches the record; original size preserved in `truncated` |

The 100-char threshold is aggressive and deliberate: repeated system prompts, repeated file
contents, and repeated tool results collapse to one copy each. **This is the single most directly
transferable size-management design I found** — it bounds the log without losing any content, and
resolution is a pure map lookup at read time.

### 1.5 Aggregate counters beside the log

`EvalSampleSummary` (the header record that a log reader can load *without* reading events) carries:

| Field | Type |
| --- | --- |
| `message_count` | `int \| None` |
| `turn_count` | `int \| None` |
| `total_time` | `float \| None` |
| `working_time` | `float \| None` |
| `model_usage` | `dict[str, ModelUsage]` |
| `role_usage` | `dict[str, ModelUsage]` |
| `token_limit`, `token_limit_usage`, `message_limit`, `time_limit` | limits and usage against them |
| `error`, `limit`, `limit_reason`, `retries`, `completed` | outcome |
| `scores` | `dict[str, Score] \| None` |

**There is no per-tool call count and no per-tool error count in the summary.** The aggregates
Inspect keeps are *steps* (`message_count`, `turn_count`), *time* (two clocks), and *tokens*
(`model_usage` keyed by model name). Per-tool breakdowns are derived by scanning `events`.

That is a notable negative result for the brief: **I did not find any harness that maintains
per-tool counters as a first-class field.** The convention is a keyed usage map for tokens and a
scalar for steps; anything per-tool is computed from the log.

### 1.6 The summary/detail split

The existence of `EvalSampleSummary` as a separate type from `EvalSample` is itself the convention
worth copying: a **small header you can read cheaply for every sample**, and a heavy body you read
only for the sample you care about. `EvalConfig` even exposes `log_samples: bool` and
`log_realtime: bool` so a run can skip sample bodies entirely.

---

## 2. LangSmith run schema

Source: [`python/langsmith/schemas.py` @ main](https://github.com/langchain-ai/langsmith-sdk/blob/main/python/langsmith/schemas.py),
fetched 2026-09-14. LangSmith's unit is a **Run**, which its own docstring equates to a span:

> A Run is a span representing a single unit of work or operation within your LLM app. This could be
> a single call to an LLM or chain, to a prompt formatting call, to a runnable lambda invocation. If
> you are familiar with OpenTelemetry, you can think of a run as a span.

### 2.1 `RunBase` fields (verbatim docstrings)

| Field | Type | Docstring |
| --- | --- | --- |
| `id` | `UUID` | "Unique identifier for the run." |
| `name` | `str` | "Human-readable name for the run." |
| `start_time` | `datetime` | "Start time of the run." |
| `run_type` | `str` | "The type of run, such as tool, chain, llm, retriever, embedding, prompt, parser." |
| `end_time` | `datetime \| None` | "End time of the run, if applicable." |
| `extra` | `dict \| None` | "Additional metadata or settings related to the run." |
| `error` | `str \| None` | "Error message, if the run encountered any issues." |
| `serialized` | `dict \| None` | "Serialized object that executed the run for potential reuse." |
| `events` | `list[dict] \| None` | "List of events associated with the run, like start and end events." |
| `inputs` | `dict` | "Inputs used for the run." |
| `outputs` | `dict \| None` | "Outputs generated by the run, if any." |
| `reference_example_id` | `UUID \| None` | "Reference to an example that this run may be based on." |
| `parent_run_id` | `UUID \| None` | "Identifier for a parent run, if this run is a sub-run." |
| `tags` | `list[str] \| None` | |
| `attachments` | `dict[str, AttachmentInfo]` | "Each entry is a tuple of `(mime_type, bytes)`." |

Plus on `Run` (what you get back from the DB): `trace_id`, `dotted_order`, `session_id`,
`status`, `feedback_stats`, and a large token/cost block (`prompt_tokens`, `completion_tokens`,
`total_tokens`, `prompt_token_details`, `completion_token_details`, `total_cost`, `prompt_cost`,
`completion_cost`, `first_token_time`).

The key architectural point for this brief: **LangSmith has exactly one record type for every kind
of work.** A tool call is a Run with `run_type="tool"`, `inputs` = the arguments, `outputs` = the
result, `error` = a string. The same three fields carry an LLM call, a retriever call, a parser.
There is no tool-specific schema at all.

### 2.2 `dotted_order` — the ordinal convention worth stealing

```python
    dotted_order: str = Field(default="")
    """Dotted order for the run.

    This is a string composed of {time}{run-uuid}.* so that a trace can be
    sorted in the order it was executed.

    Example:
        - Parent: 20230914T223155647Z1b64098b-4ab7-43f6-afee-992304f198d8
        - Children:
        - 20230914T223155647Z1b64098b-....20230914T223155649Z809ed3a2-...
    """
```

One lexicographically-sortable string encodes both **position in time** and **position in the
tree**, with no counter to maintain and no coordination between concurrent writers. For a flat list
of tool calls this is overkill, but it is the answer to "how do you give a record a stable ordinal
when calls can be concurrent and are written out of order".

### 2.3 Size handling

- Hard batch cap in [`python/langsmith/_internal/_constants.py`](https://github.com/langchain-ai/langsmith-sdk/blob/main/python/langsmith/_internal/_constants.py):
  ```python
  _SIZE_LIMIT_BYTES = 20_971_520  # 20MB by default
  _MULTIPART_INLINE_MAX_BYTES = 20_000_000  # ~20MB
  _BLOCKSIZE_BYTES = 1024 * 1024  # 1MB
  _TRACING_QUEUE_MAX_SIZE = 10_000
  ```
  The client refuses an oversized batch: "...maximum size limit of {size_limit} bytes."
  (`client.py`). The server can also return its own `size_limit_bytes` in
  `batch_ingest_config`, which the client honors over its default.
- **Externalization is first-class in the wire type.** `RunLikeDict` includes
  `inputs_s3_urls: Optional[dict]` and `outputs_s3_urls: Optional[dict]` alongside `inputs`/`outputs`,
  plus `input_attachments` / `output_attachments`. So a run can carry *either* the payload inline or
  a pointer to object storage, in the same field set.
- `_failed_traces_max_bytes` defaults to `100 * 1024 * 1024` (100 MB) of failed-trace spooling, tunable
  via `LANGSMITH_FAILED_TRACES_MAX_MB`.

**Not verified:** I could not read LangSmith's *product* documentation on per-run size limits —
`docs.langchain.com` and `docs.smith.langchain.com` are blocked by this session's egress proxy. The
constants above are from the SDK source and are what the client enforces; the hosted service may
enforce different or additional limits.

---

## 3. OpenAI Agents SDK tracing — the minimal end of the spectrum

[`src/agents/tracing/span_data.py` @ main](https://github.com/openai/openai-agents-python/blob/main/src/agents/tracing/span_data.py):

```python
class FunctionSpanData(SpanData):
    """Represents a Function Span in the trace. Includes input, output and MCP data (if applicable)."""
    __slots__ = ("name", "input", "output", "mcp_data")
    def export(self) -> dict[str, Any]:
        return {
            "type": "function",
            "name": self.name,
            "input": self.input,
            "output": str(self.output) if self.output is not None else None,
            "mcp_data": self.mcp_data,
        }
```

Four fields. No id, no error, no timing in the span *data* (timing and error live on the enclosing
`Span`), no truncation. Note `str(self.output)` — the output is **stringified unconditionally**,
losing any structure.

Also present and relevant to counters: `TurnSpanData` carries an explicit integer `turn` plus
`agent_name` and `usage`, and `TaskSpanData` carries run-level `usage`. So this SDK *does* keep an
explicit turn ordinal, at the turn level rather than the call level.

---

## 4. OpenTelemetry SDK attribute limits (the generic size backstop)

From [`specification/common/README.md` @ main](https://github.com/open-telemetry/opentelemetry-specification/blob/main/specification/common/README.md):

> Execution of erroneous code can result in unintended attributes. If there are no limits placed on
> attribute collections, they can quickly exhaust available memory, resulting in crashes that are
> difficult to recover from safely.
>
> By default an SDK SHOULD apply truncation as per the list of configurable parameters below.

Configurable Parameters (verbatim):

> * `AttributeCountLimit` (Default=128) - Maximum allowed attribute count per record;
> * `AttributeValueLengthLimit` (Default=Infinity) - Maximum allowed attribute value length (applies to string values and byte arrays);
> * `AttributeValueDepthLimit` (Default=64) - Maximum allowed attribute value depth (applies to arrays and maps);

Truncation semantics are spelled out: strings are truncated to the limit; arrays/maps have the limit
applied to each element **recursively**; over-depth arrays/maps "MUST be replaced with an empty
value"; over-count attributes are **discarded**, and the count limit applies only to top-level
attributes.

> There MAY be a log emitted to indicate to the user that an attribute was truncated, discarded, or
> replaced due to a limit. To prevent excessive logging, the log MUST NOT be emitted more than once
> per record on which an attribute is set.

Note the gap: the default value-length limit is **Infinity**, i.e. OTel ships no default cap on the
size of a single attribute. The count limit (128) and depth limit (64) are the only defaults that
bite. Inference (mine): that is why every GenAI vocabulary above had to invent its own content-size
policy — the transport doesn't impose one.
