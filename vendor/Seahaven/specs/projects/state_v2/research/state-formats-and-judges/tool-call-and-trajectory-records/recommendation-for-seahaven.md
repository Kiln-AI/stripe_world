# Recommendation: Seahaven's per-call record, counters, and bounding

This is my synthesis, not a source. Every load-bearing claim links back to the other three docs in
this directory, which carry the citations. Seahaven context I read in the repo on 2026-09-14:
`SeahavenState` is `{episode_id, step_count, fixture, now, world}`
(`src/seahaven/openenv/env.py`); a tool error is `{code, message, details}` (same file,
`SeahavenObservation.error`); `Change` is `{table, op, key, before, after}`
(`src/seahaven/changes.py`); steps arrive as OpenEnv `CallToolAction`.

---

## 1. What the survey actually establishes

**There is no standard for a tool-call record.** Eight sources, eight field sets
([wire-formats-and-action-lists.md §6](./wire-formats-and-action-lists.md)). The only universal
fields are **tool name**, **arguments**, and **an id that joins the call to its result**. Errors are
modelled four incompatible ways; an explicit ordinal appears in two of eight; timing in three.

**But there is a rough consensus on the *shape*:** a record per call, carrying name + arguments +
result + an error signal, joined by an id, with the result treated as the field that will blow up
your storage and therefore as the one that gets capped, dropped, externalized, or hashed.

The nearest thing to a durable, versioned trajectory *standard* is **ATIF**
([trajectory-container-formats.md §1](./trajectory-container-formats.md)) — a `schema_version`
string, a `steps` array, and an optional `final_metrics` object of aggregate counters. Its notable
hole is that it has **no error field on a tool result at all**, so it is a structural model to copy,
not a schema to adopt wholesale.

---

## 2. Recommended per-call record

```jsonc
{
  "i": 3,                            // ordinal, 0- or 1-based, monotonic within the episode
  "tool": "create_ticket",           // the tool name as advertised
  "args": { "title": "…", "assignee_id": 7 },   // parsed object; see §2.2
  "ok": true,                        // or absent — see §2.3
  "error": null,                     // {code, message} on failure; Seahaven already has this shape
  "ms": 12,                          // duration, integer milliseconds
  "at": "2026-09-14T14:02:11.318Z"   // optional; only if a judge could want ordering across sources
}
```

with the **result deliberately absent by default** — see §3.

### 2.1 Ordinal: include it, explicitly

Neither Anthropic, OpenAI, nor MCP carries one; ATIF (`step_id`, "Ordinal index of the turn (starting
from 1)"), tau2 (`turn_idx`), and the OpenAI Agents SDK (`TurnSpanData.turn`) all do. The difference
is that the protocols rely on their transport to preserve order, while the **saved artifacts** add an
explicit index. Seahaven's output is a saved artifact that will be read by JMESPath/jq/CEL-style
expressions years later, and `state.tool_calls[3]` being the fourth call is a property you want
written down, not inferred from array order surviving every serializer in between. It also makes
judges like "the first write was X" and "nothing happened after step N" expressible.

It should agree with `step_count`: `step_count == len(tool_calls)` if every step is a call, and the
spec should say which it is, because ATIF explicitly allows `total_steps != len(steps)`
"if explained in notes" and that ambiguity costs a reader a round of confusion.

### 2.2 Arguments: store the parsed object, not a JSON string

OTel says instrumentations "SHOULD do the best effort to deserialize it to an object"; Anthropic's
wire type is already `Dict[str, object]`; Inspect's is `dict[str, JsonValue]`. Letta and OpenAI keep
a string because they must survive a model emitting malformed JSON
([wire-formats-and-action-lists.md §2.2](./wire-formats-and-action-lists.md) — the OpenAI SDK's own
docstring warns "the model does not always generate valid JSON").

**Seahaven does not have that problem.** By the time a `CallToolAction` reaches the environment the
arguments are already a validated dict; a malformed call never becomes a Seahaven tool call. So
store the object. A judge writing `changes[?table=='tickets']` should not have to `json.loads` a
sibling field.

Cap the serialized size (§3) but preserve JSON structure when you do — OTel's phrasing: "truncate
properties such as individual message contents, **preserving JSON structure**".

### 2.3 Error: reuse `{code, message}`, and do not invent a second vocabulary

Seahaven already has `{code, message, details}` on the observation, and it already made the right
call that a tool error is data rather than a transport failure. Carry the same shape into the record,
minus `details` (which is per-tool free-form and is exactly the field that grows without bound).

`code` is the low-cardinality field — the same role as OTel's `error.type` and Inspect's
`ToolCallError.type` — and it is what a per-tool error counter should be keyed by. Two rules worth
writing into the spec, both borrowed:

- **Retired codes stay in the enum, annotated.** Inspect keeps `"output_limit"` with the comment
  "Retained for backward compatibility when loading logs created with an older version of inspect."
- **Absent ≠ success.** Letta: "The field is omitted when the source does not expose a reliable
  status; result text is never interpreted as success or failure." Seahaven *does* have an
  authoritative signal, so `ok`/`error` should always be present — but say so, so a future reader
  knows absence would be a bug rather than a shrug.

Consider separating **tool error** from **harness failure** the way Inspect does (`error` vs
`failed`) and MCP does (`isError` vs a JSON-RPC error): Seahaven's `INTERNAL_ERROR_CODE` /
`UnknownTool` path is a different category from a world's own `ToolError`, and a judge that
distinguishes "the agent called a tool that doesn't exist" from "the tool said no" will want them
apart. One extra boolean, or a reserved code prefix, is enough.

### 2.4 Timing: include duration, be careful with wall clock

Inspect keeps **two clocks** — `timestamp`/`completed` (wall) and `working_start`/`working_time`
(excluding time spent waiting on semaphores). Seahaven has a concurrency gate
(`src/seahaven/instances.py`), which is precisely the situation that makes wall-clock durations
incomparable across runs.

If timing is included at all, `ms` (duration) is the useful field and it should be measured inside
the gate, not around it. An absolute `at` timestamp is only worth its bytes if a judge might need to
correlate with something outside the episode — Seahaven's `now` is already the instance's simulated
clock, and having two different notions of time in one document is a trap. **Default position: ship
`ms`, skip `at`, and note in the spec that `now` is the world clock and is not the record's clock.**

### 2.5 Fields to *not* include

- **A call id.** Its only job in the protocols is joining a call to its result across two messages.
  Seahaven's record has both sides in one object; there is nothing to join. (Add one only if calls
  can ever be concurrent within an episode — they cannot today, one instance per session on one
  thread.)
- **Tool description / schema per call.** ATIF puts `tool_definitions` once at the root
  (`agent.tool_definitions`) rather than per step. If the tool surface is worth recording, record it
  once. It probably belongs in the world/fixture identity block rather than the trajectory.
- **The agent's message text.** That is the harness's business, not the environment's, and it is
  where the size goes.

---

## 3. The result field: the whole size problem lives here

Four industry patterns, in escalating order of effort:

| Pattern | Who does it | Cost |
| --- | --- | --- |
| **Don't record it** | OTel default ("[Default] Don't record instructions, inputs, or outputs"); Letta `filters.toolResults: "omit"` | free; a judge can't see results |
| **Record, capped** | Inspect 16 KiB default; Letta 2,500 chars head-tail; OpenInference 32,000 chars for base64 | cheap; lossy, and the loss must be declared |
| **Record a reference** | OTel pattern 3 (reference format still `TODO`); OpenInference `BLOB_UPLOADER` URI; LangSmith `outputs_s3_urls`; MCP `resource_link`; Inspect `attachment://<hash>` | needs a second store, or a hash map in the same doc |
| **Record a hash of the whole state** | tau-bench `consistent_hash(to_hashable(data))` | 64 chars; all-or-nothing, no diagnostics |

**My recommendation: omit tool results from the record by default.**

The reasoning is specific to Seahaven rather than general. Seahaven's answer to "what happened" is
`changes()` — a net row-level diff of the database. A tool result is a *view* of state that the
world computed for the agent; the diff is the state itself. A judge that wants to know whether the
ticket was created reads the diff, not the `create_ticket` response. Recording results would
duplicate the authoritative signal with a derived one, at 100% of the size cost, and invite judges
to assert against the derived one — which is exactly the brittleness that makes action-matching
judges worse than state-matching judges.

Where a result genuinely matters — read-only tools whose output the agent *reported* to the user, and
which therefore leave no trace in the diff — the escape hatch should be **opt-in and bounded**, i.e.
a world or a caller can ask for results, and when it does:

- cap it (a documented byte or code-point budget; Letta's 2,500 chars is a sane starting point for a
  judgeable record, Inspect's 16 KiB for a debuggable one);
- use **head-tail** rather than head — Letta's default, and the right one, because tool output
  commonly puts the summary at the end;
- **count the marker inside the budget** so the cap is honest (Letta binary-searches `keep` such that
  `keep + len(marker) <= limit`);
- **say that you truncated, in band.** Inspect's `truncated: (raw_bytes, cap)` and Letta's
  `tool_result_truncated` diagnostic with a `count` are two good answers; the minimum is a boolean
  plus the original size, so a judge can distinguish "empty result" from "result we dropped".

If results *are* recorded and duplication becomes the problem (the same 40 KB file read twenty
times), **Inspect's attachment scheme is the design to copy**: any string over a threshold becomes
`attachment://<hash>` and the content lives once in a map beside the log
([eval-harness-records.md §1.4](./eval-harness-records.md)). It bounds the document without losing
anything, resolution is a map lookup, and it costs one hash function. Inspect's threshold is 100
characters and its hash is non-cryptographic MurmurHash3-128 — chosen for dedup speed, not integrity.
If Seahaven ever wants integrity (an eval proving the artifact wasn't edited), use a cryptographic
hash instead and say which one in the document.

**Do not adopt tau-bench's whole-state hash as the primary signal.** It collapses everything to 64
hex chars and tells a failing judge nothing about *why*; and its canonicalization
(`str()` of nested Python tuples) is not a portable serialization. A content hash beside a diff is
useful as a cheap equality check; it is not a substitute for the diff.

---

## 4. Counters

### 4.1 What the field actually does

**No harness surveyed keeps per-tool call counts or per-tool error counts as a first-class field.**
Not Inspect, not tau2, not ATIF, not LangSmith. Inspect's sample summary keeps `message_count`,
`turn_count`, two clocks and `model_usage` (a map keyed by model name); ATIF's `final_metrics` keeps
four token totals, a cost, and `total_steps`; tau2's run keeps two costs, a duration, a termination
reason, a trial and a seed. Per-tool breakdowns are **derived by scanning the log**.

OTel is the one exception, and it is instructive: the per-tool breakdown exists only as the
`gen_ai.tool.name` + `error.type` **dimensions on the `gen_ai.execute_tool.duration` histogram**
([otel-genai-and-openinference.md §1.6](./otel-genai-and-openinference.md)). There is no counter
named "calls per tool". The recommended bucket boundaries for `gen_ai.invoke_agent.tool_calls` are
`[1, 2, 4, 8, 16, 32, 64, 128]`, i.e. the conventions assume tens of calls per episode, not
thousands.

### 4.2 Recommendation

Ship counters as a small, flat, **derived-and-therefore-cheap** block, and be explicit that it is
derived:

```jsonc
"counters": {
  "steps": 14,                       // == step_count
  "calls": 14,                       // tool calls attempted
  "errors": 2,                       // calls that returned an error
  "by_tool":  { "create_ticket": 3, "search": 9, "close_ticket": 2 },
  "errors_by_tool": { "search": 2 }  // or omit tools with zero
}
```

Reasons to include `by_tool` even though nobody else does:

1. **It is the one aggregate a judge can use without iterating.** The whole point of the brief is
   hundreds of small judges; `counters.by_tool.create_ticket == 1` is a one-line judge in every
   expression language under consideration, where the equivalent over a list is a filter + length in
   each of them with different syntax.
2. **It survives bounding.** If the call list is ever capped (§5), the counters are still exact —
   this is ATIF's `total_steps != len(steps)` principle, and it is the strongest argument for having
   counters at all.
3. It is O(calls) to compute once and O(1) to read forever.

Two rules to write down:

- **A counter records what happened, not what is in the list.** Say explicitly that counters are
  computed over the full episode even if the list is truncated. This is exactly ATIF's stance on
  `total_steps` and on `cost_usd` ("store the calculated cost at the time of execution ... without
  coupling the format to specific pricing models").
- **Count failures too, and say so.** OTel is unusually careful here: the tool-call histogram
  "SHOULD include only the tool calls the agent itself triggers **including failed ones**". Whether
  `calls` includes errored calls is the kind of thing that is obvious to the author and ambiguous to
  every reader.

Optionally add a **write/read split** using MCP's `ToolAnnotations` vocabulary (`readOnlyHint`,
`destructiveHint`, `idempotentHint`) if Seahaven's worlds declare anything equivalent — "the agent
made N mutating calls" pairs naturally with a row-diff judge. Note MCP's defaults are pessimistic
(unannotated ⇒ mutating, destructive, non-idempotent).

Do **not** add token counts, cost, or model names. Those are the harness's, not the environment's;
every framework that records them (ATIF, Inspect, tau2, LangSmith) is on the harness side of the
line. Seahaven doesn't call a model.

---

## 5. Keeping the list bounded

Nobody in the survey bounds the *number* of calls — they bound the *size of each call's payload*.
That is the first answer: **with results omitted and arguments capped, a per-call record is on the
order of 100–300 bytes, and 1,000 calls is ~0.3 MB.** OTel's histogram buckets top out at 128 calls;
tau2 and Inspect both impose step/turn/message limits at the harness level, not in the log. So the
list is very likely self-bounding in practice and a cap is insurance, not a core mechanism.

If a cap is wanted anyway, the ranked options:

1. **Cap each record's payload; leave the list unbounded.** Simplest, exact, and what everyone else
   effectively does. Preferred.
2. **Head-tail on the *list*, mirroring head-tail on a string** — keep the first N and last M calls
   and one explicit elision marker record in between, e.g.
   `{"elided": 412, "from": 50, "to": 461}`. This preserves the two regions judges actually use (how
   it started, how it ended), keeps the counters exact, and — critically — **is declared in band**,
   which is ATIF's rule for its context-management boundary: "Steps preceding the boundary are
   preserved in the trajectory for auditability but are NOT part of the agent's context window" —
   the record always says what it dropped.
3. **Never silently drop.** Whatever the mechanism, the artifact must carry a machine-readable
   statement that truncation occurred and how much was lost. Letta's diagnostics list (a code, a
   message, and a `count`) and Inspect's `truncated: (from, to)` tuple are the two working
   precedents. A judge that can't tell "zero calls to `delete_row`" from "we stopped recording" is a
   judge that will silently pass.

Avoid **sampling** (random or reservoir). I found no precedent for it in any agent trajectory format,
and it is incompatible with the judges this feature exists to serve: "the agent never called
`delete_customer`" is unanswerable over a sample.

---

## 6. Versioning the record itself

Out of scope for this subtopic (it belongs to the diff-formats-and-versioned-contracts subtopic), but
two data points from mine are worth handing over:

- **ATIF's `schema_version` is a Required root-level string** with values like `"ATIF-v1.7"`, and
  implementations accept a *range* (NeMo Agent Toolkit 1.8 accepts `ATIF-v1.0` through `ATIF-v1.7`).
  Its RFC carries a per-version changelog naming which fields were added and which change was
  breaking.
- **OpenTelemetry's GenAI conventions have no schema URL at all** — the section in the new repo's
  README is literally `TODO` — and every `gen_ai.*` attribute is still `Development`. That is the
  cautionary case: a widely-cited vocabulary that a durable artifact cannot pin to.

---

## 7. Summary of the recommendation

| Question | Answer |
| --- | --- |
| Per-call fields | `i` (ordinal), `tool`, `args` (object), `ok`/`error{code,message}`, `ms` |
| Result | **omitted by default**; opt-in, capped, head-tail, with truncation declared in band |
| Call id | not needed — call and result are one record and calls are serial |
| Timing | duration in ms, measured inside the concurrency gate; skip absolute timestamps |
| Error vocabulary | reuse Seahaven's `{code, message}`; keep `code` low-cardinality; retire codes by annotation, never deletion; distinguish tool error from harness failure |
| Counters | `steps`, `calls`, `errors`, `by_tool`, `errors_by_tool` — exact over the whole episode even if the list is bounded |
| Bounding | bound the payload per record first; if the list must be bounded, head-tail with an explicit elision record; never sample |
| Don't include | token counts, cost, model names, agent messages, per-call tool schemas |
