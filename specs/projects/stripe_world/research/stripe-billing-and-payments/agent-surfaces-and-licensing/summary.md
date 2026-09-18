# Agent Surfaces, Licensing and Naming

## Bottom Line

Stripe's real agent tool surface is small (23 named tools in the repo's own DXT manifest, possibly
more live — see gaps), curated (not 1:1 with the API's 419 raw endpoints), flat-snake_case-named
(`create_customer`, not `customers.create`), read/write-gated entirely server-side by Restricted API
Key scopes, and has **no generic "call any endpoint" tool** — recommend this project do the same:
one-tool-per-operation, sized at or under the ~20–40-tool accuracy elbow multiple independent sources
report for LLM tool selection, with no default passthrough tool. Separately, `stripe/openapi`,
`stripe-mock`, `stripe-python`, and `agent-toolkit` are all MIT-licensed by Stripe, which plainly
permits committing `spec3.json` (or a derived subset, with slightly more legal nuance) into this
project's repo as long as Stripe's copyright+permission notice travels with it — no Apache-style
NOTICE file is legally required, though a `THIRD_PARTY_LICENSES` file is good practice. Trademark and
API-terms questions could not be verified against Stripe's own primary-source text in this
environment (network access to `stripe.com`/`docs.stripe.com` was blocked); based on secondary
(WebSearch-relayed) evidence and on how comparable real projects (`paybox`, `localstripe`) name and
disclaim themselves, recommend shipping under a **neutral world name** with an explicit non-affiliation
disclaimer, while still reusing real Stripe field names/error codes/object shapes under the MIT grant.

## Key Findings

- **The agent-toolkit repo no longer defines tools locally at all (v0.9.0+).** The TypeScript/Python
  SDK wrappers are thin clients that call `listTools()` against `https://mcp.stripe.com` at runtime;
  the "local" CLI/Docker MCP server is a stdio↔HTTP relay to the same hosted endpoint, not a real
  local implementation. Source: `research/repos/agent-toolkit/tools/typescript/src/shared/mcp-client.ts`,
  `.../MIGRATION.md`. This means the authoritative tool list lives at Stripe, not in any OSS repo.
- **Exact tool list (23), from the repo's own DXT manifest** —
  `research/repos/agent-toolkit/tools/modelcontextprotocol/manifest.json`: `search_documentation`,
  `get_stripe_account_in[fo]` (truncated in the file — confirmed real name via a sibling test-fixture
  file), `create_customer`, `list_customers`, `create_product`, `list_products`, `create_price`,
  `list_prices`, `create_payment_link`, `create_invoice`, `list_invoices`, `create_invoice_item`,
  `finalize_invoice`, `retrieve_balance`, `create_refund`, `list_payment_intents`,
  `list_subscriptions`, `cancel_subscription`, `update_subscription`, `list_coupons`,
  `create_coupon`, `update_dispute`, `list_disputes`. A third-party MCP catalog (Speakeasy, via
  WebSearch) reports 31 tools for the live server — the static manifest may be stale; not resolvable
  from this environment (see Gaps).
- **Naming convention: `<verb>_<resource>`, snake_case, flat** — confirmed via `MIGRATION.md`'s own
  before/after table (`createCustomer` → `create_customer`). No `resource.verb` namespacing anywhere.
- **No generic escape hatch.** All 23 tools are specific named operations; no `stripe_request(method,
  path, params)` exists in the manifest, client code, or CLI. One piece of ambiguous, unverified
  evidence (`stripe_api_search`/`stripe_api_details`/`stripe_api_read` in a ChatGPT-submission test
  fixture) hints at a different, more generic pattern elsewhere but could not be confirmed as real —
  see `tool-surface.md` §4.
- **Read/write gating is entirely server-side via Restricted API Key (RAK) scopes** — not a
  client-side allow-list. The old `configuration.actions` client-side gating mechanism was removed in
  v0.9.0 with no replacement; per the code's own comment, "the server filters tools based on RAK
  permissions." Source: `toolkit-core.ts`, `MIGRATION.md`, `tools/modelcontextprotocol/README.md`.
- **Schema shape is deliberately flattened.** The toolkit's JSON-Schema→Zod converter explicitly does
  not support `oneOf`/`anyOf`/`allOf`/`$ref`/conditional schemas — only primitives, arrays, simple
  objects, and enums. This is independent structural evidence that the MCP tool schemas are simpler
  than the raw OpenAPI spec's schemas (which use `$ref` and nested unions throughout).
- **Coverage: ~23 tools vs. 419 raw API paths / 1454 schemas** (per `research/MANIFEST.md`'s spec
  numbers) — a curated, read-heavy, ~95%-reduced subset, not an API-parity mapping. Several resources
  are list-only (Payment Intents have no `create`; Subscriptions have no `create`).
- **Tool-count/schema-size guidance converges on a ~20–40-tool "elbow"** across several independently
  found sources (Anthropic's own engineering guidance reporting a 79.5%→88.1% accuracy jump from
  switching off "load every tool at once"; a RAG-MCP paper reporting ~90%→~14% accuracy degradation
  at scale; a HumanMCP benchmark reporting 87.4%→65% from 500 to 2,000 tools; several blog-level
  analyses converging on a 20–35-tool elbow). **Caveat: all of this is relayed through `WebSearch`
  summaries, not independently fetched/read primary text** — this environment's `WebFetch` could
  reach only `github.com`/`raw.githubusercontent.com`; every other domain (including
  `docs.stripe.com`, `stripe.com`, `arxiv.org`, `anthropic.com`) returned `EGRESS_BLOCKED`.
- **All four sources are MIT-licensed, copyright Stripe** (confirmed by reading LICENSE files on disk
  for `stripe-mock`/`stripe-python`/`agent-toolkit`, and by fetching `stripe/openapi`'s LICENSE
  directly from `github.com`). MIT permits committing `spec3.json` or a derived subset into this
  project's repo; the only condition is carrying Stripe's copyright+permission notice with the copy.
  No NOTICE-file mechanism exists in MIT (that's an Apache-2.0 concept) — a `THIRD_PARTY_LICENSES`
  file is recommended practice, not a legal requirement.
- **Trademark/API-terms text could not be verified against primary sources** in this environment
  (network policy blocked `stripe.com`/`docs.stripe.com`). Secondary (WebSearch-relayed) evidence
  describes a Mark Usage Terms page governing Stripe's name/logo (not API field names, which
  trademark law doesn't reach) and an SSA reverse-engineering clause whose scope (Stripe's live
  Services vs. the separately/affirmatively MIT-published OpenAPI spec) is a real nuance worth a
  lawyer's read, not something I resolved.
- **Three real, verbatim non-affiliation disclaimer examples gathered**, including the closest direct
  comparable found: `dsasante1/paybox` (a multi-provider payment emulator covering "Stripe (92
  endpoints)") — *"Not affiliated with, endorsed by, or connected to Paystack, Stripe, Flutterwave,
  Kora or Quid Payments. Provider names are used only to describe API compatibility."* Also
  `yfinance` and a generic auto-generated-SDK template. Notably, `adrienverge/localstripe` — the
  other close comparable (a third-party stateful Stripe mock server) — carries **no** such disclaimer
  at all in its README, which is worth knowing as a counter-example, not just supporting evidence.

## Details

- [tool-surface.md](./tool-surface.md) — full Part 1 write-up: the architecture shift to
  server-fetched tools, the complete 23-tool list with resource grouping, naming convention evidence,
  RAK-based read/write gating, schema-flattening evidence, raw-HTTP-API contrast, the tool-count
  literature (with sourcing caveats spelled out), and the full §12.1 recommendation with the honest
  case for and against each of the three tool-surface shapes.
- [licensing-and-naming.md](./licensing-and-naming.md) — full Part 2 write-up: license text and
  copyright lines for all four sources, a clause-by-clause read of what MIT permits and requires, the
  NOTICE-file question, Stripe trademark/SSA evidence (with access-constraint caveats), all
  verbatim disclaimer examples (including ones I looked for and didn't find), and the full §12.2
  naming recommendation.

## Open Questions / Gaps

- **Could not reach `docs.stripe.com/mcp#tools` or `docs.stripe.com/agents`** — the pages the
  research plan explicitly asks this subtopic to read — because this environment's network egress
  policy blocks `docs.stripe.com` (and `stripe.com`, `api.stripe.com`) outright for `WebFetch`; the
  proxy status log shows explicit `403`/`connect_rejected` entries for these hosts. I substituted the
  repo's own DXT manifest (a static, dated snapshot) plus third-party MCP catalog pages surfaced via
  `WebSearch`, which is a real degradation in source quality for the "exact tool list" claim — a
  follow-up with working access to `docs.stripe.com` should re-verify the live tool count/list/
  descriptions directly, since third-party catalogs disagree (23 vs. 31) and neither was independently
  fetchable to check its own sourcing/freshness.
- **Could not fetch Stripe's Mark Usage Terms, SSA, or any arxiv/blog primary source directly** — same
  network constraint. All quotes in `licensing-and-naming.md` §4 and `tool-surface.md` §6 attributed
  to WebSearch are secondary paraphrases relayed through that tool's own summarization, not
  independently read primary text. This is flagged inline everywhere it applies, but is worth
  restating here as the single biggest evidentiary weakness in this subtopic's findings.
- **Live `mcp.stripe.com` tool count/descriptions are unverified** — nothing in this offline
  environment could call `listTools()` against the real server (no live API key, and the domain is
  blocked anyway). The 23-tool list is a known-good historical snapshot from the repo at commit
  `da4991b0a0b9299d423ae2d5856e6d7e2b31b031` (2026-09-17), not a live-confirmed current list.
- **The exact RAK scope names Stripe uses server-side for MCP tool filtering are unknown** — the repo
  confirms *that* filtering happens by RAK scope, but the actual scope identifiers (e.g. whatever
  Stripe's dashboard UI calls "customers: write") are not in any file this project has access to.
- **The `stripe_api_search`/`stripe_api_details`/`stripe_api_read` fragment in the Codex submission
  test fixtures is unresolved** — it may be stale placeholder content or evidence of a genuinely
  different, more generic tool-naming pattern used on a different surface (OpenAI Apps SDK). Flagged,
  not resolved, in `tool-surface.md` §4.

## Sources

- `research/repos/agent-toolkit/` (local, commit `da4991b0a0b9299d423ae2d5856e6d7e2b31b031`, fetched
  2026-09-17) — primary source for the whole tool-surface analysis: `tools/typescript/MIGRATION.md`,
  `tools/typescript/src/shared/{mcp-client,toolkit-core,configuration,schema-utils}.ts`,
  `tools/python/stripe_agent_toolkit/shared/mcp_client.py`,
  `tools/modelcontextprotocol/{manifest.json,server.json,src/cli.ts,README.md}`,
  `providers/codex/plugin/{README.md,test-cases.json}`, `skills/stripe-pay/SKILL.md`,
  `skills/stripe-best-practices/SKILL.md`, `LICENSE`.
- `research/repos/stripe-mock/LICENSE`, `research/repos/stripe-python/LICENSE` (local) — license text
  and copyright lines.
- [github.com/stripe/openapi](https://github.com/stripe/openapi) (fetched live via `WebFetch`,
  2026-09-18) — repo description, README summary, and LICENSE text (MIT, copyright 2011– Stripe,
  Inc.), since this repo isn't vendored in full on disk.
- `research/MANIFEST.md` (local) — spec version (`2026-08-26.dahlia`), path/schema counts (419/1454)
  used for the raw-API-size contrast.
- [github.com/dsasante1/paybox](https://github.com/dsasante1/paybox) — verbatim non-affiliation
  disclaimer and safety-guarantee text, fetched via `raw.githubusercontent.com`, 2026-09-18.
- [github.com/adrienverge/localstripe](https://github.com/adrienverge/localstripe) — comparable
  third-party Stripe mock; confirmed absence of disclaimer language, fetched 2026-09-18.
- [github.com/ranaroussi/yfinance](https://github.com/ranaroussi/yfinance) — verbatim non-affiliation
  disclaimer, fetched via `raw.githubusercontent.com`, 2026-09-18.
- [github.com/voxgig-sdk/mixpanel-gdpr-sdk](https://github.com/voxgig-sdk/mixpanel-gdpr-sdk) —
  verbatim generic-template disclaimer, fetched 2026-09-18.
- `WebSearch` result summaries (2026-09-18, not independently fetched — see Gaps) for: Stripe MCP
  third-party catalog listings (Speakeasy, Portkey, PolicyLayer, LobeHub, remote-mcp.com, Stacklok),
  Stripe's Mark Usage Terms and Services Agreement content, and the tool-count/schema-size literature
  (Anthropic engineering guidance, a RAG-MCP paper, a HumanMCP benchmark, Archestra's MCP-tool-count
  analysis, and several independent blog-level tool-count-degradation write-ups).
