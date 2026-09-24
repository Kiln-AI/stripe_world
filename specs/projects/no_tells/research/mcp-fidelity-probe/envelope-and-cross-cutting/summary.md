# Envelope and Cross-Cutting

## Bottom Line

The real Stripe MCP returns a **bare JSON body** on success -- the API object
or list envelope directly, with no wrapper -- and raises an **MCP tool error**
(a plain string) on failure. There is no `{"status": int, "body": {...}}`
envelope, no `headers` key, no structured error object visible to the agent,
and no idempotency support. Our world wraps every response in
`{"status", "body", "headers"}` and surfaces structured error objects with
type/code/param/doc_url, which is a tell on literally every call. Four of ten
tells are blatant and all four stem from the envelope mismatch.

Pagination envelope shape, expansion behavior, cursor semantics, expand error
messages, and the both-cursors refusal message all match between the real MCP
and our spec. The only pagination tell is limit-boundary handling: real Stripe
silently clamps out-of-range limits instead of returning errors.

## Key Findings

- **The return envelope is a bare body, not {status, body, headers}** (EC-01).
  Every successful call from `stripe_api_read` or `stripe_api_write` returns
  the API object or list envelope directly as the MCP tool result. A
  `GetCustomers` returns `{"object":"list","data":[...],"has_more":true,
  "url":"/v1/customers"}`. A `PostCustomers` returns `{"id":"cus_...","object":
  "customer",...}`. Our world's three-key wrapper (`cross_cutting.md` section
  7.4, `response.py`'s `render()`) is visible on every call. Source: direct
  probes of GetCustomers, PostCustomers, GetPaymentIntents with expansion.

- **Errors are MCP tool errors, not structured JSON** (EC-02). A 404, 400, or
  any Stripe error surfaces as an MCP ToolError whose text is `"Stripe API
  error: <message>"`. The HTTP status code, `error.type`, `error.code`,
  `error.param`, `error.doc_url`, and `request_log_url` are all stripped by the
  MCP layer. Our world returns errors as successful tool results with the full
  structured body. Source: probes triggering resource_missing, unknown
  parameter, bad expand, both cursors, wrong type.

- **Idempotency is not exposed through MCP** (EC-03). The real MCP tool has no
  `idempotency_key` parameter (confirmed: the schema has only
  `stripe_api_operation_id`, `parameters`, `stripe_context`, `livemode`,
  `human_confirmation`). Passing `idempotency_key` in the `parameters` body is
  rejected as an unknown Stripe parameter. Stripe's `Idempotency-Key` is an
  HTTP header, and MCP has no header mechanism. Our world accepts it as a tool
  parameter and implements full replay/mismatch/in-flight semantics. Source:
  direct probe passing `idempotency_key` in PostCustomers parameters.

- **No response headers visible** (EC-04). No `Stripe-Version`, `Request-Id`,
  or `Idempotency-Key` echo appears anywhere in successful or failed responses.
  The API version `2026-08-26.preview` appears only in the discovery tools'
  `openapi_spec_version` field. Our world returns a `headers` dict on every
  response. Source: inspection of every probe result.

- **MCP error chrome** (EC-05). Every error carries a suffix appended by the
  MCP server: `"Use stripe_api_details with stripe_api_operation_id: \"...\""`.
  Our world does not append this. Source: all error probes.

- **Limit boundaries silently clamped** (EC-06, EC-07). `limit=0` and
  `limit=-1` return 1 item; `limit=101` and `limit=200` return 100 items. No
  error in any case. Our spec says `limit < 1` or `limit > 100` is 400
  `parameter_invalid_integer`. Source: direct probes, item counts verified
  with `json.load`.

- **API version string differs** (EC-08). The discovery tools report
  `openapi_spec_version: "2026-08-26.preview"`. Our world pins
  `"2026-08-26.dahlia"` in `stripe_envelope.py`. Source: `stripe_api_search`
  and `stripe_api_details` responses.

- **Expand behavior matches** (no tell). `expand[]=data` on a list is accepted
  as a no-op. A bare field on a list gets the `data.` hint. A bad field gets
  `"This property cannot be expanded (<field>)."`. Two-level expand
  (`data.customer`) works correctly. Multiple expand paths work. All match our
  spec. Source: expand probes on GetCustomers, PostCustomers,
  GetPaymentIntents.

- **Pagination envelope matches** (no tell). The list envelope is exactly
  `{"object":"list","data":[...],"has_more":bool,"url":"/v1/..."}` -- four
  keys, no `total_count`. Cursor exclusivity, both-cursors refusal message,
  cursor-before-pair validation order, and nonexistent cursor behavior all
  match. Source: pagination probes with starting_after, ending_before, both
  cursors, and nonexistent cursors.

- **Deleted stub has extra field** (EC-09). Expanding a deleted customer on a
  payment intent returns `{"id":"cus_...","object":"customer",
  "cache_context_key":"acct_...","deleted":true}`. Our stub is the three-key
  `{id, object, deleted}`. Source: GetPaymentIntents with
  `expand[]=data.customer`.

## Details

- [probe-results.md](./probe-results.md) -- verbatim probe transcripts for
  every test: return envelope shape, error shapes, idempotency, pagination,
  expansion, API version, path parameter naming.
- [tells.md](./tells.md) -- numbered table of all 10 tells (EC-01 through
  EC-10) with severity, exact agent action, real vs our answer, and closure
  guidance.

## Open Questions / Gaps

- **Idempotency design question**: since the real MCP does not expose
  idempotency at all, whether our world should remove it (for fidelity) or
  keep it (for eval value) is a design call, not a research question.

- **Structured error body unobservable**: the MCP layer strips `error.type`,
  `error.code`, `error.param`, and `doc_url` entirely, so I could not confirm
  whether the underlying Stripe HTTP responses carry the fields our spec says.
  An agent interacting through MCP would never see them regardless.

- **`request_log_url`**: never observed, consistent with our spec's decision to
  never emit it. But presence in the underlying HTTP response is unknown due to
  MCP stripping.

- **MCP truncation**: the MCP server truncates responses exceeding ~57k
  characters and saves them to a local file, returning a pointer. Whether our
  world should reproduce this behavior is unexamined.

- **`limit=0` clamping mechanism**: whether the clamping happens in the MCP
  layer or in the Stripe API itself is unobservable. The behavioral difference
  from our world is the same either way.

## Sources

- Real Stripe MCP server in this session, all probes in test mode against
  sandbox account (Seahaven Sandbox), 2026-09-22.
- `specs/projects/stripe_world/components/cross_cutting.md` -- our functional
  spec for cross-cutting behavior (sections 2.2, 2.3, 3.1-3.5, 7.4).
- `src/seahaven_stripe_world/dispatch/response.py` -- `render()` producing the
  `{status, body}` envelope.
- `src/seahaven_stripe_world/middleware/stripe_envelope.py` -- envelope
  middleware adding `headers` and catching `StripeApiError`.
- `src/seahaven_stripe_world/middleware/idempotency.py` -- idempotency
  middleware.
- `src/seahaven_stripe_world/dispatch/resource.py` -- pagination.
- `src/seahaven_stripe_world/serialize/expand.py` -- expansion resolver.
