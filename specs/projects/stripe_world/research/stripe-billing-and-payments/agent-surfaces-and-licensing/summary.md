# Agent Surfaces, Licensing and Naming

## Bottom Line

**Major correction from a 2026-09-18 gap-closure pass, which had working direct access to
`docs.stripe.com`/`stripe.com` via `mcp__Tavily__tavily_extract`/`tavily_search` (this session's
`WebFetch` still cannot reach those domains):** the live Stripe MCP server's tool list, read
directly from `docs.stripe.com/mcp`, is **11 tools, not 23 or 31**, and its architecture is the
**opposite** of one-tool-per-operation — it's dominated by four generic tools
(`stripe_api_search`, `stripe_api_details`, `stripe_api_read`, `stripe_api_write`) that expose
"much of the API" through a search/read/write-by-path pattern, plus 7 named tools
(`get_stripe_account_info`, `stripe_analytics`, `get_balance_summary`,
`search_stripe_documentation`, `stripe_implementation_planner`, `send_stripe_mcp_feedback`,
`stripe_report`). The lane's original 23-tool, one-tool-per-operation picture (from a vendored DXT
manifest) is a stale/legacy-packaging snapshot, not what the live server exposes today, and the
lane's "no generic escape hatch" finding is now known to be wrong for the live server — see
[gap-closure-2026-09-18.md §6](./gap-closure-2026-09-18.md#6-the-live-stripe-mcp-server-tool-list)
before relying on the original §12.1 recommendation, which was built on the old shape. Separately,
Stripe's Mark Usage Terms and Services Agreement §2.4 have now been read in full, verbatim, direct
(previously entirely `WebSearch`-paraphrased): the Mark Usage Terms confirm the prior summary's
substance but add a clause not previously surfaced — a literal prohibition on incorporating "Stripe"
into "your own ... trade name ... corporate name" — worth flagging to whoever owns the disclaimer
wording, though the naming decision itself isn't being reopened here. The SSA's reverse-engineering
clause (§2.4) reads exactly as previously paraphrased, and a newly-read adjacent clause (§2.3) gives
direct textual support — not previously found — for the argument that Stripe's own OSS-license
carve-out favors treating the separately-MIT-published OpenAPI spec differently from the SSA's
"Stripe Technology" restriction. RAK scope identifiers are now confirmed to have **no public
machine-readable enum at all** (Dashboard-UI category labels only) — not merely unfound, but
genuinely undocumented, confirmed by both the docs page's own framing and the absence of any
RAK-permission schema in the public OpenAPI spec. Everything else from the original pass — the
MIT-licensing analysis, the disclaimer-language survey, the tool-count/schema-size literature caveat
— stands unchanged.

## Key Findings

- **CORRECTED — the live Stripe MCP server has 11 tools, not 23 (DXT manifest) or 31 (third-party
  catalog), and 4 of them (`stripe_api_search`, `stripe_api_details`, `stripe_api_read`,
  `stripe_api_write`) are a generic search/read/write-by-path passthrough layer — exactly the
  "generic escape hatch" the original pass said didn't exist.** Read directly from
  `docs.stripe.com/mcp`, 2026-09-18. The lane's one piece of "ambiguous, unverified evidence" (a
  `stripe_api_search`/`stripe_api_details`/`stripe_api_read` fragment found in a Codex test-fixture
  file, flagged as possibly stale) is now confirmed real — it was an early, correct signal, not
  noise. This flips the evidentiary basis for the lane's §12.1 recommendation ("one-tool-per-
  operation ... no default passthrough tool") — that recommendation can no longer cite "this is what
  Stripe itself does" as support, since Stripe's own production server has since moved to the
  opposite shape. See
  [gap-closure-2026-09-18.md §6](./gap-closure-2026-09-18.md#6-the-live-stripe-mcp-server-tool-list)
  for the full quoted tool table and the itemized implications.
- **Exact tool list (23), from the repo's own DXT manifest — now known to be a stale/legacy
  snapshot, not the live server's shape** —
  `research/repos/agent-toolkit/tools/modelcontextprotocol/manifest.json`: `search_documentation`,
  `get_stripe_account_in[fo]`, `create_customer`, `list_customers`, `create_product`,
  `list_products`, `create_price`, `list_prices`, `create_payment_link`, `create_invoice`,
  `list_invoices`, `create_invoice_item`, `finalize_invoice`, `retrieve_balance`, `create_refund`,
  `list_payment_intents`, `list_subscriptions`, `cancel_subscription`, `update_subscription`,
  `list_coupons`, `create_coupon`, `update_dispute`, `list_disputes`. Kept here for historical
  record; do not treat this as current.
- **Naming convention: `<verb>_<resource>`, snake_case, flat** — confirmed via `MIGRATION.md`'s own
  before/after table (`createCustomer` → `create_customer`), and still true of the live server's
  named tools (`get_stripe_account_info`, `get_balance_summary`, etc.) even though the live surface
  is now dominated by the 4 generic tools rather than one-per-operation named tools.
- **Read/write gating is entirely server-side via Restricted API Key (RAK) scopes** — not a
  client-side allow-list. Confirmed again directly: `docs.stripe.com/keys/restricted-api-keys`
  states RAK permissions are Dashboard-assigned per resource category (Read/Write/None), and the
  public OpenAPI spec has no RAK-permission schema at all, confirming this is genuinely not a
  documented API-level construct with identifier strings — see
  [gap-closure-2026-09-18.md §8](./gap-closure-2026-09-18.md#8-restricted-api-key-rak-scope-identifiers).
- **Schema shape is deliberately flattened** in the (now-superseded) toolkit SDK wrapper layer — the
  JSON-Schema→Zod converter explicitly does not support `oneOf`/`anyOf`/`allOf`/`$ref`/conditional
  schemas. Independent structural evidence the MCP tool schemas are simpler than the raw OpenAPI
  spec's. (Whether this still holds for the live server's 4 generic tools, whose parameters are
  necessarily more free-form — path/method/params rather than a fixed per-operation schema — wasn't
  re-verified this pass.)
- **Tool-count/schema-size guidance converges on a ~20–40-tool "elbow"** across several independently
  found sources — still entirely `WebSearch`-relayed, not independently re-verified this pass (lower
  priority than the MCP tool-list and licensing items, which the dispatch prompt named explicitly).
- **All four sources are MIT-licensed, copyright Stripe** — unchanged from the original pass,
  confirmed by reading LICENSE files on disk and fetching `stripe/openapi`'s LICENSE live. MIT
  permits committing `spec3.json` or a derived subset into this project's repo; the only condition is
  carrying Stripe's copyright+permission notice with the copy.
- **Mark Usage Terms — now read in full, verbatim, direct** (`stripe.com/legal/marks`). Confirms the
  prior WebSearch-relayed summary's substance, and surfaces a clause not previously found: "Use or
  incorporate any of our Marks in your own trademark, service mark, trade dress, trade name, website
  name, domain name, corporate name, or social-media handle" is listed under "Don't." A literal
  reading says don't put "Stripe" in your own product's name — worth informing the disclaimer's
  wording/prominence even though the naming decision itself isn't being reopened. See
  [gap-closure-2026-09-18.md §7](./gap-closure-2026-09-18.md#7-mark-usage-terms-and-services-agreement-primary-text).
- **SSA §2.4 (reverse engineering) confirmed verbatim, matching the prior paraphrase closely.** New
  find: **SSA §2.3 (Third-Party Software)** — immediately preceding §2.4 — states "If there is a
  conflict between an open source license and this Agreement regarding open source code, the
  applicable open source license terms supersede the conflicting terms of this Agreement." This
  gives direct textual support (not a legal conclusion, but real evidentiary support) for the prior
  pass's "plausible resolution" argument that the separately-and-affirmatively MIT-published OpenAPI
  spec sits outside what the SSA's reverse-engineering clause is aimed at. Still flagged as something
  a lawyer should confirm, per the dispatch instructions — this pass improves the evidence, not the
  legal conclusion.
- **Three real, verbatim non-affiliation disclaimer examples gathered** (unchanged from the original
  pass), including the closest direct comparable found: `dsasante1/paybox` — *"Not affiliated with,
  endorsed by, or connected to Paystack, Stripe, Flutterwave, Kora or Quid Payments. Provider names
  are used only to describe API compatibility."* Also `yfinance` and a generic auto-generated-SDK
  template. `adrienverge/localstripe` carries **no** such disclaimer at all.

## Details

- [gap-closure-2026-09-18.md](./gap-closure-2026-09-18.md) — itemized record of this pass: each
  original gap, its new answer, a verbatim quote + URL, and whether it confirms/corrects/refines the
  prior record. **Read this first** — item 6 (the live MCP tool list) materially changes the picture
  the rest of this lane's findings were built on.
- [tool-surface.md](./tool-surface.md) — full Part 1 write-up from the original pass: the
  architecture shift to server-fetched tools, the 23-tool DXT-manifest list (now known stale),
  naming convention evidence, RAK-based read/write gating, schema-flattening evidence, raw-HTTP-API
  contrast, the tool-count literature, and the full §12.1 recommendation — **read alongside
  `gap-closure-2026-09-18.md §6` before treating the recommendation as settled**, since its
  Stripe-precedent argument no longer holds as originally stated.
- [licensing-and-naming.md](./licensing-and-naming.md) — full Part 2 write-up: license text and
  copyright lines for all four sources, a clause-by-clause read of what MIT permits and requires, the
  NOTICE-file question, the original (WebSearch-relayed) Stripe trademark/SSA evidence, all verbatim
  disclaimer examples, and the full §12.2 naming recommendation. Supplement §4 with
  `gap-closure-2026-09-18.md §7` for the now-verbatim primary text.

## Open Questions / Gaps

- Whether the DXT manifest's 23-tool, one-tool-per-operation shape is still live *anywhere* (e.g. a
  legacy local-packaging path distinct from `mcp.stripe.com`) — not checked this pass; not material
  to §12.1 since the live production server (now confirmed) is what matters for precedent.
- Whether SSA §2.3's OSS carve-out has ever been tested against a case resembling this project's
  (using a separately-MIT-published OpenAPI spec to build an unaffiliated mock, rather than literally
  "open source software included in the Stripe Technology") — flagged for a lawyer, not resolved.
- The literal wire-format RAK scope strings (if any exist beyond Dashboard category labels) remain
  unknown — now confirmed to be genuinely undocumented publicly, not just unfound. See
  `gap-closure-2026-09-18.md §8` for the best available category-name approximation.
- The `stripe_api_search`/`stripe_api_details`/`stripe_api_read` fragment in the Codex submission
  test fixtures, previously flagged as unresolved, **is now resolved** — it matches 3 of the 4 live
  "API tools" exactly. Removed from open questions.
- Tool-count/schema-size literature (Anthropic engineering guidance, RAG-MCP paper, HumanMCP
  benchmark, etc.) remains entirely `WebSearch`-relayed, not independently re-verified this pass —
  lower priority than the items the dispatch prompt named explicitly, left open for a future pass if
  needed.

## Sources

- [Model Context Protocol (MCP) | Stripe Documentation](https://docs.stripe.com/mcp) — fetched
  directly 2026-09-18 via `mcp__Tavily__tavily_extract`; authoritative, primary-source list of the
  live server's 11 tools.
- [Agents and AI on Stripe | Stripe Documentation](https://docs.stripe.com/agents) — fetched
  directly 2026-09-18; corroborating context, no additional tool list.
- [Stripe's Mark Usage Terms](https://stripe.com/legal/marks) — fetched directly 2026-09-18; full
  verbatim primary text (page dated "Last updated: June 12, 2023").
- [Stripe Services Agreement](https://stripe.com/legal/ssa) — fetched directly 2026-09-18; full
  verbatim primary text of §2.3 and §2.4.
- [Restricted API keys | Stripe Documentation](https://docs.stripe.com/keys/restricted-api-keys) —
  fetched directly 2026-09-18; confirms RAK permissions are Dashboard-category-based, not a
  documented API-level enum.
- `research/repos/agent-toolkit/` (local, commit `da4991b0a0b9299d423ae2d5856e6d7e2b31b031`, fetched
  2026-09-17) — primary source for the (now-superseded-for-tool-list-purposes) architecture analysis:
  `tools/typescript/MIGRATION.md`, `tools/typescript/src/shared/{mcp-client,toolkit-core,configuration,schema-utils}.ts`,
  `tools/python/stripe_agent_toolkit/shared/mcp_client.py`,
  `tools/modelcontextprotocol/{manifest.json,server.json,src/cli.ts,README.md}`,
  `providers/codex/plugin/{README.md,test-cases.json}`, `skills/stripe-pay/SKILL.md`,
  `skills/stripe-best-practices/SKILL.md`, `LICENSE`.
- `research/repos/stripe-mock/LICENSE`, `research/repos/stripe-python/LICENSE` (local) — license text
  and copyright lines.
- [github.com/stripe/openapi](https://github.com/stripe/openapi) — repo description, README
  summary, and LICENSE text (MIT, copyright 2011– Stripe, Inc.).
- `research/MANIFEST.md` (local) — spec version (`2026-08-26.dahlia`), path/schema counts (419/1454).
- [github.com/dsasante1/paybox](https://github.com/dsasante1/paybox),
  [github.com/adrienverge/localstripe](https://github.com/adrienverge/localstripe),
  [github.com/ranaroussi/yfinance](https://github.com/ranaroussi/yfinance),
  [github.com/voxgig-sdk/mixpanel-gdpr-sdk](https://github.com/voxgig-sdk/mixpanel-gdpr-sdk) —
  disclaimer-language survey, fetched via `raw.githubusercontent.com`, 2026-09-18.
- `WebSearch`/`WebFetch`-relayed sources from the original pass (Stripe MCP third-party catalogs,
  tool-count/schema-size literature) — see `tool-surface.md` and `licensing-and-naming.md` for the
  full list; still not independently re-verified except where superseded above.
