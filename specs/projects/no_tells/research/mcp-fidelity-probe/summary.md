# Research: Real Stripe MCP -- fidelity probe

## Bottom Line

Our world and the real Stripe MCP server are incompatible at every layer of the tool
interface. The real MCP addresses operations by `stripe_api_operation_id` (not `path`),
requires `stripe_context` and `livemode` on every call, returns bare JSON bodies on success
(not `{status, body, headers}`), raises MCP tool errors on failure (not structured error
objects), exposes 10 tools (not 5), and has no idempotency support. Discovery covers the
entire Stripe API (not 148 operations) and uses a structured `intent` + `resource` search
(not free-text `query`). These are not edge-case differences: an agent trained on one
surface cannot operate on the other without code changes.

The 100 raw tells across five lanes collapse to **70 distinct tells** after cross-lane
de-duplication: 28 blatant, 27 probable, 15 subtle. They cluster into six themes. Three
require architectural decisions; the rest are mechanical edits of varying size. The good
news: pagination, expansion, cursor semantics, and the error-type enum all match, and the
MCP layer's habit of stripping HTTP structure (headers, status codes, error fields) means
the fidelity bar is lower than a raw-HTTP replica would demand.

## Key Findings

- **The entire tool interface is structurally wrong.** All four API tools accept different
  parameter names, return different shapes, and handle errors through a different mechanism.
  The addressing key (`stripe_api_operation_id` vs `path`) and the return envelope (bare
  body vs `{status, body}`) are the two deepest changes; everything else is downstream of
  them. ([tool-surface](./tool-surface/summary.md), [envelope](./envelope-and-cross-cutting/summary.md))

- **The tool list is wrong.** Real MCP has 10 tools; ours has 5 with only 4 names in common.
  `get_stripe_account_info` does not exist; `list_available_accounts_or_orgs` (which gates
  every other tool by providing `stripe_context` and `livemode`) is missing. Five more tools
  (`stripe_analytics`, `search_stripe_documentation`, `stripe_implementation_planner`,
  `manage_stripe_accounts`, `send_stripe_mcp_feedback`) are absent.
  ([tool-surface](./tool-surface/summary.md))

- **Discovery covers the full Stripe API, not 148 operations.** The real MCP's search and
  details return results for Issuing, Connect, Checkout, Treasury, Tax, Payment Links, v2
  paths, and more. An agent searching for "checkout session" or "issuing card" in our world
  gets zero results -- a blatant tell. The fix is to expand the discovery index (not the
  router) so that out-of-scope operations are discoverable but return permission errors when
  called. ([discovery](./discovery-tools/summary.md), [absence-refusal](./absence-and-refusal/summary.md))

- **Idempotency is not exposed through MCP.** The real MCP tool schema has no
  `idempotency_key` parameter. `Idempotency-Key` is an HTTP header, and MCP has no header
  mechanism. Our world accepts it as a named tool parameter and implements full
  replay/mismatch semantics. The headline eval grades an agent on using it; fidelity and
  the eval are in direct conflict.
  ([envelope](./envelope-and-cross-cutting/summary.md))

- **The refusal shape is fixable.** Four different refusal reasons (MCP-unregistered,
  nonexistent path, wrong verb, legacy sub-resource) produce the identical MCP message
  `"Operation 'X' is not available."` Our world can shelter behind this for all unimplemented
  operations. Out-of-scope paths that currently return 404 "Unrecognized request URL" need to
  return a permission-style 403 instead -- real Stripe never says "Unrecognized" for its own
  endpoints. ([absence-refusal](./absence-and-refusal/summary.md))

- **ID shapes and the account object are wrong but cheaply fixable.** Customer, product, and
  subscription-item IDs use 14-char suffixes (not 24), and all other IDs embed a 10-char
  account fragment at a fixed position. The static account object is missing keys, has wrong
  values for a fresh sandbox, and uses a human-readable constant as its ID. Each is a
  one-function edit. ([account-stats](./account-and-statistical-tells/summary.md))

## Corrections to `stripe_world`

The functional spec at `specs/projects/stripe_world/functional_spec.md` was written before
this probe. The following assertions in it contradict what the live server shows. That
project is still being implemented, so these are corrections to carry forward, not claims
about what the implementation does or does not do.

| Spec section | The spec says | The live server does |
|---|---|---|
| SS2, tool table | 5 tools: `stripe_api_search(query)`, `stripe_api_details(method, path)`, `stripe_api_read(path, params)`, `stripe_api_write(method, path, params, idempotency_key)`, `get_stripe_account_info()` | 10 tools, all using `stripe_api_operation_id` + `stripe_context` + `livemode`. No `get_stripe_account_info`. No `idempotency_key`. No `method`. |
| SS2, dropped tools | Lists `stripe_analytics`, `get_balance_summary`, `stripe_report`, `search_stripe_documentation`, `stripe_implementation_planner`, `send_stripe_mcp_feedback` as dropped | Live server has `stripe_analytics`, `search_stripe_documentation`, `stripe_implementation_planner`, `send_stripe_mcp_feedback`, `manage_stripe_accounts`, `list_available_accounts_or_orgs`. Does NOT have `get_balance_summary` or `stripe_report`. |
| SS2.2 | `idempotency_key` is a named parameter on `stripe_api_write`, promoted for visibility | The real MCP has no `idempotency_key` parameter. It is an HTTP header with no MCP equivalent. |
| SS2.3 | Every tool returns `{"status": int, "body": {...}}` | Success: bare JSON body. Failure: MCP tool error (plain text string). No wrapper. |
| SS2.6 | `get_stripe_account_info()` returns the account object | This tool does not exist on the real MCP. `list_available_accounts_or_orgs` returns `stripe_context` + `livemode` per account. The account object is available via `stripe_api_read` with `GetAccount`. |
| SS2.7 | Discovery is "keyword matching over path, operationId, summary and description" with single `query` | Discovery uses `intent` + `resource` (semantic, not keyword). Results wrapped in `{openapi_spec_version, data}` envelope with `id` and `llm_context` fields. |
| SS2.7, last para | "Discovery serves exactly the operations this world actually implements" | Real discovery covers the entire Stripe API, including operations the key cannot call. |
| SS6.1 | Full idempotency behavior: key scoping, replay, mismatch, in-flight, caching | The MCP layer strips all idempotency. An agent interacting through MCP cannot use idempotency keys. |
| SS6.5 | Pinned API version: `2026-08-26.dahlia` | Discovery tools report `openapi_spec_version: "2026-08-26.preview"`. Whether `dahlia` and `preview` are different versions or different labels for the same version is unresolved. |
| SS6.6 | Events listable and retrievable at `/v1/events` | The real MCP does not expose `GetEvents` or `GetEventsId`. Only v2 Event Destinations appear in search. |

## Decisions Needing a Human

1. **Idempotency: fidelity vs eval value.** The headline eval grades an agent on using
   `idempotency_key`, but the real MCP does not expose it. Keeping it makes the eval work but
   breaks fidelity on every write call. Removing it preserves fidelity but eliminates the
   eval that justified the project. Options: (a) remove from the MCP-shaped surface but keep
   it on the `call_stripe(method, path, body)` raw surface from SS2.5; (b) document it as a
   deliberate extension; (c) redesign the eval to test idempotency through the raw surface.

2. **Discovery coverage scope.** The real MCP's discovery covers the full Stripe API. Should
   our discovery index expand to include out-of-scope operations (discoverable but not
   executable, returning permission errors)? This is moderate engineering work (a broader
   pruned spec, out-of-scope operation stubs in the index) but closes 11+ tells at once.

3. **Events visibility.** The real MCP hides v1 events from search and details; calling
   `GetEvents`/`GetEventsId` returns "not available." Our world routes and surfaces them.
   Events are useful for evals that check state. Keep them accessible through the tool
   interface but hide from MCP discovery? Or match the real MCP and force evals to use a
   different read path?

4. **Account object state.** The static account mixes completed-onboarding values
   (`charges_enabled: true`, `capabilities: {card_payments: active}`) with incomplete values
   (`business_profile.name: "Test Business"` but real fresh sandboxes have `null`). Pick one
   consistent state: either a fresh sandbox (charges disabled, no capabilities, no name) or a
   fully onboarded account (charges enabled, active capabilities, real name). A fresh sandbox
   is simpler and matches the probe data.

5. **Structured ID complexity.** Real IDs (Format B) embed a version digit, base62-encoded
   timestamp, and a 10-char account fragment -- enabling cross-ID correlation. Implementing
   this closes 2 blatant and 1 probable tell. Is the engineering cost justified, or are
   correct-length random suffixes with per-prefix length tables sufficient?

6. **Frozen clock.** The frozen-clock model (all timestamps identical within a session) is a
   probable tell: an agent creating 5+ objects sees zero variance. Advancing the clock by +1
   second per write is trivial to implement but changes a design assumption stated in the
   functional spec (SS5: "Every call within an instance sees the same `now`"). Decide whether
   fidelity or determinism wins.

## Conflicts and Uncertainty

- **API version codename vs label.** The functional spec pins `2026-08-26.dahlia`; the live
  server's discovery tools report `2026-08-26.preview`. These may be different labels for the
  same version (a codename vs a preview flag), or genuinely different versions. The spec's
  `spec3.json` has `info.version: "2026-08-26.dahlia"`. The live server may be running a newer
  build. This is unresolvable without Stripe-internal knowledge; pin to whatever the OpenAPI
  spec file says and declare the difference.

- **MCP operation curation.** Some operations present in the OpenAPI spec are absent from
  the real MCP (`GetEvents`, `PostInvoicesInvoicePay`, `GetCustomersCustomerSources`). It is
  unclear whether this is an intentional curation (a blocklist) or an artifact of how the
  MCP server indexes operations. This matters if we expand discovery coverage: we need to
  know which operations to include.

- **The 403 permission-error message format is inferred, not observed.** The sandbox key has
  full access, so no probe could trigger a real restricted-key 403. The wire `type` is
  strongly inferred as `invalid_request_error` from Stripe docs, and the message format is
  documented as naming the missing permission -- but the verbatim text is unverified. A
  purpose-built restricted key would close this gap.

## Gaps

- **Exact restricted-key 403 message.** Needs a probe with a key that actually lacks a
  permission. The sandbox key has full access.
- **Total operation count on real server.** The search tool is semantic, not enumerable.
  A lower bound of ~80-100 distinct operation IDs was observed; the true count is likely
  several hundred.
- **Event ID format.** `evt_` IDs were not sampled (events not exposed via MCP).
  Likely structured (Format B) but unconfirmed.
- **`human_confirmation` flow.** The real `stripe_api_write` has an approval-token mechanism
  for sensitive writes. Not probed; schema captured.
- **Concurrent-probe contamination.** All five agents probed the same sandbox simultaneously.
  Object counts, list pages, and any aggregate statistics are contaminated.
- **Response latency.** The MCP tool interface does not expose timing info.

## Themes

The 70 distinct tells group into six themes. The first two are architectural; the rest are
field-level or configuration edits.

1. **Tool interface shape** (15 blatant, 2 probable) -- addressing scheme, input schemas,
   descriptions, return envelope, error handling, stripe_context/livemode. This is the
   largest cluster and the hardest to close. It requires rewriting every tool's signature,
   removing the `{status, body}` wrapper, raising MCP errors instead of returning them, and
   adopting operation-ID-based dispatch.

2. **Discovery coverage and output** (6 blatant, 9 probable) -- the discovery index covers
   only 148 operations; the output shape is missing keys, uses the wrong parameter structure,
   and truncates descriptions. The coverage gap is one decision; the output shape is
   mechanical once the index is expanded.

3. **Refusal and error fidelity** (2 blatant, 3 probable, 1 subtle) -- out-of-scope paths
   return the wrong error; the error guidance suffix is missing; type coercion and limit
   clamping differ. All moderate or trivial once the refusal strategy is decided.

4. **ID and timestamp fidelity** (3 blatant, 2 probable) -- suffix lengths, account-embedded
   structure, and frozen-clock timestamps. Format A (14-char) is trivial; Format B (structured)
   is moderate; the clock is a design decision.

5. **Account object fidelity** (5 blatant, 2 probable) -- missing keys, wrong values,
   fake ID, spurious metadata. All trivial static edits in `_account_object()`.

6. **Serialization completeness** (0 blatant, 9 probable, 13 subtle) -- missing fields on
   customers, products, subscriptions, charges, refunds, setup intents, payment intents, and
   balance transactions. Each is a trivial constant addition; collectively they form a
   fingerprint. Closing all 22 is a day of work.

## Subtopics

- [Tool Surface](./tool-surface/summary.md) -- the MCP tool list, schemas, descriptions,
  and return shapes. Headline: every shared tool has a different schema and return envelope;
  the tool list itself is wrong. 26 tells (19 blatant, 7 probable).
- [Absence and Refusal](./absence-and-refusal/summary.md) -- how the real server says "no."
  Headline: four refusal flavours are indistinguishable in MCP, so a single permission-error
  response hides all unimplemented operations. 9 tells + 1 non-tell.
- [Discovery Tools](./discovery-tools/summary.md) -- `stripe_api_search` and
  `stripe_api_details` contracts. Headline: structured input, broader output, full-API
  coverage. 24 tells (12 blatant, 11 probable, 1 subtle).
- [Envelope and Cross-Cutting](./envelope-and-cross-cutting/summary.md) -- idempotency,
  pagination, expansion, version, the envelope. Headline: bare body on success, MCP error on
  failure, no idempotency, no headers. Pagination and expansion match. 10 tells.
- [Account and Statistical Tells](./account-and-statistical-tells/summary.md) -- IDs,
  timestamps, account object, resource field completeness. Headline: ID shapes and the
  account object have 7 blatant tells, plus ~24 missing resource fields. 31 tells.
