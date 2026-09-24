# Discovery Tools

## Bottom Line

The real Stripe MCP's `stripe_api_search` and `stripe_api_details` are fundamentally different from
our implementation in input schema, output shape, and coverage. The search tool takes structured
`intent` + `resource` fields (not a free-text `query`), wraps results in an
`{openapi_spec_version, data}` envelope, and includes an `id` (operation ID) and optional
`llm_context` on each result. The details tool takes a single `stripe_api_operation_id` string (not
`method` + `path`), returns a 12-key document with parameters organized into `{path, query, body}`
dicts (not a flat list), and includes `tags`, `keywords`, `required_permissions`, and
`openapi_spec_version`. Path parameters are normalized to `{id}` everywhere, not the descriptive
names from the OpenAPI spec. Coverage spans the entire Stripe API (including Issuing, Connect,
Checkout, Treasury, Tax, v2 paths, and unstable paths) — not our 148-operation subset. An agent
doing even routine discovery (e.g., searching for "issuing card" or "checkout session") would
immediately see the gap. These are 24 distinct tells, 11 blatant.

## Key Findings

- **Search input schema mismatch**: Real takes `intent` + `resource` + optional `limit` (1-20,
  default 5). Our world takes a single `query` string and returns up to 10 results with no limit
  parameter. Any agent inspecting the tool schema sees this immediately. (Source: live MCP tool
  schema; [raw-responses.md](./raw-responses.md))

- **Search output envelope mismatch**: Real wraps results in
  `{"openapi_spec_version": "2026-08-26.preview", "data": [...]}`. Each result has
  `{id, method, path, summary, llm_context?}`. Our world returns a bare list of
  `{method, path, summary}`. (Source: every search probe; [raw-responses.md](./raw-responses.md))

- **Details input schema mismatch**: Real takes `stripe_api_operation_id` (e.g. `"PostCustomers"`).
  Our world takes `method` + `path`. This changes how the agent calls the tool entirely. (Source:
  live MCP tool schema)

- **Details output shape mismatch**: Real returns 12 keys including `tags`, `keywords`,
  `required_permissions`, `openapi_spec_version`, and parameters organized as
  `{path: {}, query: {}, body: {}}`. Our world returns 6 keys with a flat parameter list. Real
  includes full multi-paragraph descriptions; ours truncates to first sentence. Real nests
  parameters to arbitrary depth; ours stops at depth 1. (Source: details probes;
  [raw-responses.md](./raw-responses.md))

- **Path parameter normalization**: Real normalizes all single-resource path parameters to `{id}`
  (e.g. `/v1/customers/{id}`, `/v1/subscriptions/{id}`). Our world preserves the OpenAPI spec's
  descriptive names (`{customer}`, `{subscription_exposed_id}`, `{intent}`, etc.). Visible in both
  search results and details responses. (Source: every search and details probe)

- **Coverage: entire Stripe API vs 148 operations**: Real search surfaces operations from Issuing,
  Connect, Checkout, Treasury (v2 money_management paths), Tax, Payment Links, Billing Portal,
  Webhook Endpoints, Test Helpers, and more. Our world surfaces only the 148 routed billing-scope
  operations. A single search for "issuing card" or "checkout session" reveals the gap. (Source: 
  out-of-scope search probes; [raw-responses.md](./raw-responses.md))

- **Events are hidden on real server**: The real MCP does not expose `GetEvents` or `GetEventsId`
  through search or details. Searching for "events" returns only v2 Event Destinations. Our world
  includes events as routed, searchable operations. (Source: events search probe)

- **Some routed operations hidden on real server**: `PostInvoicesInvoicePay` returns "not available"
  on the real server. The real MCP curates which operations agents can discover, not just which exist
  in the OpenAPI spec. (Source: details probe)

## Details

- [raw-responses.md](./raw-responses.md) — Verbatim JSON responses from every search and details
  probe. Read this for the exact field names, key ordering, nesting depth, and content. ~20 search
  responses and ~10 details responses captured unabridged.

- [tells.md](./tells.md) — Numbered table of all 24 tells with severity ratings, verbatim
  comparisons, and remediation notes. Ordered by category (search, details, coverage).

## Open Questions / Gaps

- **Total operation count on real server**: I could not determine the exact number of operations the
  real server's discovery index covers. The search tool is semantic (intent + resource), not
  enumerable — there is no way to query "list all operations." A rough lower bound based on distinct
  operation IDs seen across all probes is ~80-100, but the true number is likely several hundred
  (the full Stripe API spec has 419 v1 paths alone, plus v2 paths).

- **Whether the real MCP's operation set is curated or automatic**: Some operations present in the
  OpenAPI spec are absent from the real MCP (e.g. `GetEvents`, `PostInvoicesInvoicePay`,
  `GetCustomersCustomerSources`). It is unclear whether this is an intentional curation (a blocklist
  of operations the MCP should not expose) or an artifact of how the real server indexes operations.
  This matters for our implementation: if we add coverage for out-of-scope operations, we need to
  know which operations the real server intentionally hides.

- **llm_context content**: The `llm_context` field appears on some search results and some details
  responses. I captured several examples (e.g. search endpoints link to docs.stripe.com/search,
  issuing operations disambiguate between related resources). I did not determine the complete set
  of operations that carry this field or the generation rules for its content.

- **PostCheckoutSessions details size**: The real details response for `PostCheckoutSessions` was
  156,601 characters — too large to capture in a single tool response. This demonstrates that the
  real server returns deeply nested, very large parameter documents for complex operations. Our
  depth-1 truncation would produce a much smaller response.

## Sources

- Real Stripe MCP server tools (`mcp__stripe__stripe_api_search`,
  `mcp__stripe__stripe_api_details`), probed 2026-09-22 against sandbox account
- `src/seahaven_stripe_world/discovery/index.py` — our search and details implementation as written
  today
- `src/seahaven_stripe_world/tools/api.py` — our tool registration and signatures as written today
- `specs/projects/stripe_world/components/discovery.md` — our component specification for the
  discovery layer
