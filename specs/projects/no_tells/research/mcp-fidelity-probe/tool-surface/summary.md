# Tool Surface

## Bottom Line

The real Stripe MCP and our world are structurally incompatible at the tool-surface level.
Every one of the four shared tools has a different input schema, different parameter names,
different descriptions, and different return envelopes. The real MCP has 10 tools; our world
has 5, with only 4 names in common — and even those 4 accept different parameters, return
different shapes, and handle errors through a different mechanism (MCP-level errors vs.
normal `{status, body}` results). An agent cannot use our world's tools as drop-in replacements
for the real MCP tools, or vice versa, without code changes. This is the single largest
category of tells.

## Key Findings

- **Tool count mismatch**: Real MCP has 10 tools, our world has 5. We are missing 6 tools
  (`list_available_accounts_or_orgs`, `manage_stripe_accounts`, `stripe_analytics`,
  `search_stripe_documentation`, `stripe_implementation_planner`, `send_stripe_mcp_feedback`)
  and have 1 extra (`get_stripe_account_info`) that doesn't exist in the real MCP.
  Source: ToolSearch enumeration of all `mcp__stripe__*` tools in this session.

- **Operation IDs vs. paths**: The real MCP identifies operations by `stripe_api_operation_id`
  (e.g., `"GetCustomers"`, `"PostCustomers"`). Our world uses raw `path` strings
  (`"/v1/customers"`). This affects `stripe_api_read`, `stripe_api_write`, and
  `stripe_api_details` — three of our four tools. Source: live ToolSearch schema extraction.

- **Context parameters**: Every real MCP tool (except `list_available_accounts_or_orgs` and
  `manage_stripe_accounts`) requires `stripe_context` and `livemode`. Our tools accept
  neither. Source: live ToolSearch schema extraction.

- **Return envelope**: The real MCP returns bare Stripe objects on success and raises
  MCP-level tool errors on failure. Our world wraps everything in `{"status": <int>, "body": ...}`
  and returns errors as normal tool results. Source: live probing of `GetCustomers` (success)
  and `GetCustomersCustomer` with nonexistent ID (error).

- **Search interface**: The real `stripe_api_search` uses a semantic `intent` + `resource`
  interface (e.g., `intent="list"`, `resource="customers"`). Our world uses a single `query`
  keyword string. The return shapes differ too: real wraps results in
  `{"openapi_spec_version": "...", "data": [...]}` with `id` and optional `llm_context`
  per result; ours returns a bare list of `{method, path, summary}`. Source: live probing.

- **Details format**: The real `stripe_api_details` groups parameters as
  `{path: {}, query: {}, body: {}}` with param-name-as-key. Ours uses a flat list. The real
  output includes `tags`, `keywords`, `required_permissions`, and `openapi_spec_version`;
  ours has none of these. The real output uses `id` as the key for the operation identifier;
  ours uses `operation_id`. Source: live probing of `GetCustomers` and `PostCustomers`.

- **Path placeholders**: The real MCP uses `{id}` uniformly for all path parameters
  (`/v1/customers/{id}`, `/v1/invoices/{id}`). Our world uses resource-specific names
  (`/v1/customers/{customer}`, `/v1/invoices/{invoice}`). Source: live search results
  across customers, invoices, charges, subscriptions.

## Details

- [tool-schemas.md](./tool-schemas.md) — Side-by-side schema comparison of every tool
  with verbatim real MCP schemas. Read when you need the exact parameter types and
  descriptions.
- [return-envelopes.md](./return-envelopes.md) — Verbatim return values from every tool
  probed, showing the exact JSON shapes. Read when you need the precise structure an
  agent would parse.
- [missing-tools.md](./missing-tools.md) — Full documentation of the 6 missing tools
  and 1 extra tool, with descriptions, schemas, and example returns. Read when speccing
  stub implementations.
- [tells.md](./tells.md) — The numbered tell table (TS-01 through TS-26) with severity
  ratings and closure steps.

## Open Questions / Gaps

- **`human_confirmation` flow**: The real `stripe_api_write` has an approval-token mechanism
  for sensitive writes. I did not probe this flow (would require a write that triggers human
  confirmation). The schema is captured, but the exact error/response when confirmation is
  needed is not.
- **`stripe_analytics` return shapes**: I probed only `search_query_tables`. The five other
  intents (`execute_query_run`, `retrieve_query_run`, `retrieve_query_table`,
  `execute_query_template`, `retrieve_query_template`) were not probed; their schemas are
  captured from ToolSearch.
- **MCP annotations**: The real tools may have MCP-level annotations (e.g.,
  `readOnlyHint`, `destructiveHint`) beyond the JSON schema. These are not visible through
  ToolSearch and were not probed.
- **Error guidance text**: The real MCP appends contextual guidance to error messages
  ("Use stripe_api_details with stripe_api_operation_id: ..."). The exact set of guidance
  templates was not enumerated.

## Sources

- ToolSearch result for all 10 `mcp__stripe__*` tools — captured in this session, provides
  full JSON schema and description text.
- Live probe calls against sandbox account (15+ calls), captured in this session.
- Our world source code: `src/seahaven_stripe_world/tools/api.py`,
  `src/seahaven_stripe_world/tools/account.py`,
  `src/seahaven_stripe_world/discovery/index.py`,
  `src/seahaven_stripe_world/dispatch/routes.py`.
