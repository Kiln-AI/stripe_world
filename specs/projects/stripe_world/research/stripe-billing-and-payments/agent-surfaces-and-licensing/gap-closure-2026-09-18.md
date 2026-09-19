# Gap closure pass — 2026-09-18

**Why this file exists:** the original pass for this lane could not reach `docs.stripe.com`,
`stripe.com`, or `mcp.stripe.com` (network egress block on `WebFetch`), so the tool-surface findings
came from a vendored, dated DXT manifest plus `WebSearch`-relayed third-party catalogs, and the
licensing findings came entirely from `WebSearch` paraphrase. This session has
`mcp__Tavily__tavily_extract`/`tavily_search`, which do reach those domains (verified). This file
closes the gap list against direct reads of `docs.stripe.com/mcp`, `docs.stripe.com/agents`,
`stripe.com/legal/marks`, `stripe.com/legal/ssa`, and `docs.stripe.com/keys/restricted-api-keys`.

**Headline: item 6 below is a major correction, not a confirmation.** The lane's core §12.1
recommendation ("one-tool-per-operation, no generic passthrough tool") was built on a stale,
23-tool snapshot. The live server Stripe documents today looks nothing like that — read it in full
before treating the old tool-surface recommendation as settled.

---

## 6. The live Stripe MCP server tool list

**Original claim (`tool-surface.md` §1, `summary.md` Open Questions):** 23 tools from a vendored DXT
manifest, one-tool-per-operation (`create_customer`, `list_customers`, ...), "no generic escape
hatch," against an unresolved 23-vs-31 conflict with a third-party catalog, with an ambiguous,
unverified fragment (`stripe_api_search`/`stripe_api_details`/`stripe_api_read`) flagged as possibly
stale test-fixture noise.

**Answer: neither 23 nor 31. The live server's documented tool list is 11 tools, and its
architecture is the opposite of one-tool-per-operation — it's dominated by four generic,
API-shaped tools.** Quoted **verbatim, directly from `docs.stripe.com/mcp`**, fetched 2026-09-18:

> "## Tools
> The server exposes the following [MCP tools].
>
> | Resource | Tool | Description |
> | --- | --- | --- |
> | **API tools** | `stripe_api_search` | Search for Stripe API methods by keyword |
> | | `stripe_api_details` | Get detailed parameter information for a specific Stripe API method. |
> | | `stripe_api_read` | Read data with any Stripe API `GET` method |
> | | `stripe_api_write` | Write data with any Stripe API `POST`, `PATCH`, `PUT` and `DELETE` method |
> | **Account** | `get_stripe_account_info` | [Retrieve account] |
> | **Analytics** Private preview | `stripe_analytics` | Query metrics, run SQL against reporting
>   tables, or run pre-built templates |
> | **Treasury** Public preview | `get_balance_summary` | Displays an interactive balance summary
>   across the Stripe balance and Treasury accounts |
> | **Others** | `search_stripe_documentation` | Search the Stripe documentation for the given
>   question and language |
> | | `stripe_implementation_planner` | Guides the user through Stripe products to help users accept
>   payments, sell products online, set up billing, or build any Stripe integration |
> | | `send_stripe_mcp_feedback` | Submit feedback from user or agent about Stripe MCP server tools |
> | `stripe_report` Private preview | Search, retrieve and create reports and report runs |
>
> The Stripe MCP server exposes multiple APIs that you can call with the `stripe_api_read` and
> `stripe_api_write` tools. This access makes much of the API available through MCP without
> increasing the context window unnecessarily."

That's **11 named tools**: `stripe_api_search`, `stripe_api_details`, `stripe_api_read`,
`stripe_api_write`, `get_stripe_account_info`, `stripe_analytics`, `get_balance_summary`,
`search_stripe_documentation`, `stripe_implementation_planner`, `send_stripe_mcp_feedback`,
`stripe_report`.

### What this means for the lane's prior findings, itemized

1. **CORRECTS "no generic escape hatch."** `stripe_api_read` and `stripe_api_write` are exactly that
   escape hatch — generic tools parameterized by HTTP method and path, covering "any Stripe API `GET`
   method" / "any ... `POST`, `PATCH`, `PUT` and `DELETE` method." The lane's one piece of "ambiguous,
   unverified evidence" (a `stripe_api_search`/`stripe_api_details`/`stripe_api_read` fragment found
   in a Codex test-fixture file, which the lane explicitly could not confirm was real) **is now
   confirmed real** — it's 3 of the 4 "API tools" on the live server. It was not stale fixture noise;
   it was an early, correct signal of where the architecture was headed.
2. **CORRECTS the 23-tool count and the one-tool-per-operation naming convention as a description of
   the current live server.** The DXT manifest's `create_customer`/`list_customers`/etc. pattern is
   not what `mcp.stripe.com` exposes today. The DXT manifest may still be accurate for a legacy/local
   packaging path (not verified either way this pass), but it is **not** the answer to "what tools
   does the live Stripe MCP server expose," which is the question the dispatch prompt actually asked.
3. **RESOLVES the 23-vs-31 conflict** — not by picking a side, but by superseding both: the real
   current answer is 11, and it's a fundamentally different shape (few generic tools + a handful of
   named ones) rather than "more or fewer instances of the same one-tool-per-operation pattern."
   Neither 23 nor 31 was wrong for what it measured (historical DXT snapshot vs. some third-party
   catalog's scrape at some point in time), but neither is the live answer as of 2026-09-18.
4. **This flips the §12.1 recommendation's evidentiary basis.** The lane recommended Seahaven ship
   "one-tool-per-operation ... no default passthrough tool," reasoning from the old 23-tool shape and
   from tool-count-elbow literature. Stripe's own production MCP server — the single most relevant
   comparable — has since moved to a small number of tools **including** a generic
   read/write-by-path passthrough. This doesn't automatically mean Seahaven should copy that
   shape (Stripe's tools front a `search`/`details` discovery layer specifically to keep the
   generic tools usable without blowing the context window, which is a real design each project has
   to earn — a naive passthrough tool without that scaffolding could plausibly perform worse), but
   the recommendation can no longer be made citing "this is what Stripe itself does" as support for
   the *opposite* shape. This is a decision-relevant correction, not a footnote — flag it to whoever
   owns §12.1 before that recommendation is finalized.

### `docs.stripe.com/agents` — corroborating context, no additional tool list

Fetched directly, 2026-09-18. Confirms the MCP server, Agent plugins, and Agent skills are three
separate, bundled things (`stripe agent setup` installs all three together), and surfaces two things
not previously in the lane's notes:

> "1. Install the Stripe CLI, if not installed, with `npm install -g @stripe/cli`
> 2. Run `stripe sandbox create --help` to get working API keys without signing up.
> 3. Install the Stripe agent plugin or agent skills with the command: `stripe agent setup`"

and a list of adjacent "Build AI products with Stripe" surfaces (Agentic commerce overview, Sell
through agents, Accept payments from agents, Build an agent, Billing for LLM tokens) — these are
downstream-of-MCP agent-commerce features, not additional MCP tools; noted here for completeness but
out of this lane's core scope.

URLs: https://docs.stripe.com/mcp, https://docs.stripe.com/agents (both fetched directly, 2026-09-18)

---

## 7. Mark Usage Terms and Services Agreement primary text

**Original claim:** entirely `WebSearch`-relayed paraphrase, flagged as the weakest evidence behind
the naming/disclaimer decision.

### Mark Usage Terms — now read in full, verbatim, direct

Fetched directly from `stripe.com/legal/marks` (dated "Last updated: June 12, 2023" on the page
itself), 2026-09-18. Full relevant text:

> "As a general rule, you may use our Marks to truthfully convey information about your goods or
> services, but not in a way that will imply endorsement by us of your goods or services, or
> otherwise cause consumer confusion."

**Do:**
> "Use our Marks only on the portion of your website or application that directly relates to our
> services (such as on a checkout page using our payment processing services)."
> "Use our Stripe word mark without alteration in text to truthfully and accurately refer to us or
> our goods or services."

**Don't** (the two clauses most relevant to this project's naming question, quoted in full):
> "Misrepresent your relationship with us, or use our Marks in any way that is misleading, or that
> would imply our endorsement or sponsorship of your goods or services (or anybody else's goods or
> services)."
> "**Use or incorporate any of our Marks in your own trademark, service mark, trade dress, trade
> name, website name, domain name, corporate name, or social-media handle** (or any other
> source-identifying use), or use any trademark, service mark, trade dress, trade name, website name,
> domain name, corporate name, or social-media handle ... that is likely to be confused with any of
> our Marks."

Also relevant and previously only paraphrased:
> "Use our Marks to show Stripe or our goods or services in any disparaging or derogatory light, or
> in any way that may be damaging to our brand or to our interests in the Marks."
> "Use a ™ or ® in conjunction with our Marks."

**Verdict: CONFIRMS** the substance of the lane's prior WebSearch-relayed summary (the "disparaging
light" and "™/® symbol" points paraphrase almost identically) but **adds a materially important
clause the prior summary did not surface**: the explicit prohibition on incorporating "Stripe" into
"your own ... trade name, website name, domain name, corporate name" — i.e., a plain reading of
Stripe's own terms says **do not put "Stripe" in your product's own name**. The project owner has
already decided to keep "Stripe" in the world's name with a non-affiliation disclaimer, and this
finding does not reopen that decision — but it is exactly the kind of fact that should inform how
strong and how prominent the disclaimer needs to be, and it's worth being explicit that the current
naming choice sits in tension with a literal reading of this clause. **This is a "needs a lawyer,
not me" flag**, stated plainly per the dispatch instructions: whether "Stripe World," "the Stripe
world," or similar project naming crosses the line this clause draws (versus permissible
"describes its relationship truthfully" naming, the way "an unofficial X for Y" disclaimers try to
thread) is a real legal judgment call, not something resolved by finding the text.

URL: https://stripe.com/legal/ssa (Mark Usage Terms is at `stripe.com/legal/marks` — see above)

### Services Agreement §2.4 (reverse engineering) — now read in full, verbatim, direct

Fetched directly from `stripe.com/legal/ssa`, 2026-09-18. Full text of the relevant section
(previously only paraphrased):

> "**2.4 Modifications and Reverse Engineering.**
> Except to the extent that the following restriction is not permitted under Law, User must not (and
> User must not enable others to) decompile, reverse engineer, disassemble, attempt to derive the
> source code of, decrypt, tamper, translate, modify, or create derivative works of all or any part
> of the Stripe Technology or any services provided by Stripe. User agrees not to remove, obscure, or
> alter any proprietary notices (including trademark and copyright notices) that may be affixed to or
> contained within the Stripe Technology."

**Verdict: CONFIRMS** the lane's prior paraphrase almost word-for-word — the WebSearch-relayed
summary was accurate.

**New finding not in the prior pass at all — §2.3, immediately preceding §2.4:**

> "**2.3 Third-Party Software.**
> User acknowledges that open source software included in the Stripe Technology may grant User
> additional rights. **If there is a conflict between an open source license and this Agreement
> regarding open source code, the applicable open source license terms supersede the conflicting
> terms of this Agreement.** Portions of the Stripe Technology may utilize third-party software and
> other copyrighted material."

**Verdict: REFINES and materially strengthens** the lane's prior "plausible resolution, not legally
resolved by me" argument in `licensing-and-naming.md` §4 — that argument (an OSS-licensed
`stripe/openapi` repo is a separate, affirmative act by Stripe distinct from what the SSA's
reverse-engineering clause reaches) now has **direct textual support from the SSA itself**: the SSA
explicitly carves out open-source-licensed Stripe code and says the OSS license's terms win any
conflict. This doesn't make it a settled legal conclusion — `spec3.json` in `stripe/openapi` is
Stripe's separately-published, MIT-licensed OpenAPI description of its API, not itself literally
"software included in the Stripe Technology" in the sense §2.3 is describing (which reads more like
bundled third-party dependencies within Stripe's own SDKs/tools) — but it is meaningfully better
evidence than existed before, and it's the kind of textual hook a lawyer would want to see. **Still
flagging this as something a lawyer should confirm before leaning on it for anything consequential**,
per the dispatch instructions — the improvement here is evidentiary quality, not a legal sign-off.

Also worth surfacing (new, not in prior pass): §2's broader license grant text confirms the
restriction is scoped to "the Stripe Technology" as Stripe distributes/runs it (their SDKs, the
live API, etc.), with an explicit carve-out for anything Stripe itself marks "distributable" — again
consistent with, not contradicting, the §2.3/§12.2 argument that a separately-and-affirmatively
MIT-published spec sits outside what §2.4 is aimed at.

URL: https://stripe.com/legal/ssa (fetched directly, 2026-09-18)

---

## 8. Restricted API Key (RAK) scope identifiers

**Original claim:** "the actual scope identifiers ... are not in any file this project has access
to."

**Answer: there is no publicly documented machine-readable enum of RAK scope strings — RAK
permissions are assigned exclusively through Dashboard UI categories, not a documented API
parameter, and this is now confirmed rather than merely absent-from-what-we-checked.**

Direct quote, `docs.stripe.com/keys/restricted-api-keys`, fetched 2026-09-18:

> "When you create a RAK in the Stripe Dashboard, you select which Stripe resources the key can
> access and the permissions for each resource: **Read**, **Write**, or **None**."
> "The available permissions are grouped into categories. If you know your Stripe API usage doesn't
> include a particular category, like Stripe Billing, you can select **None** for that category."
> "Write permissions include Read permissions: if a key can write an API resource, it can also read
> that resource."

**Confirmed independently**: the public OpenAPI spec vendored in this project
(`research/stripe-openapi/spec3.json`) has **no schema for RAK permissions at all** — grepped for
any `permission`/`restricted`/`api_key`-named schema; the only hit is the unrelated
`payment_pages_checkout_session_permissions` object. This corroborates that RAK-permission
assignment genuinely isn't a public API-level construct with documented identifier strings — it's
Dashboard-only, which is why no primary-source enum exists to find.

**Best available approximation of the category list** (third-party, not Stripe's own text, but
mirrors what Stripe's Dashboard actually shows per a payments-integration partner's help doc, useful
for building a shape-compatible mock's permission model even without literal wire-format strings):
category groups include *All core resources* (Charges, Customers, Customer session, Disputes,
Events, PaymentIntents, PaymentMethods, Payment Method Domains, Payouts, Products, SetupIntents,
Tokens, Balance, Test clocks, Apple Pay Domains, Files, Funding Instructions, ...), *All Billing
resources* (Coupons, Promotion Codes, Credit notes, Customer portal, Invoices, Prices,
Subscriptions, Quote, Tax Rates, Usage Records), *All Checkout resources*, *All Connect resources*,
*All webhook resources* (Webhook Endpoints, Stripe CLI permissions, Debugging tools), *All Payment
Links resources*, *All Terminal resources* (Configurations, Locations, Readers, Connection Tokens),
*Tax* (Calculations and Transactions, Settings and Registrations), *Radar* (Reviews), *All Climate
resources* (Climate Orders). Source: Quoter Help Center's RAK-configuration guide (third-party,
quoting the Dashboard UI's own category labels), cross-checked loosely against Spreedly's
integration doc, which independently names an overlapping "All core resources" subset (Charges,
Customers, PaymentIntents, PaymentMethods, SetupIntents, Tokens, Webhook Endpoints).

**Verdict: REFINES** the gap — it's not that the identifiers exist and weren't found; it's that no
such documented identifier enum exists publicly at all (confirmed by both the docs page's own
framing and the absence of any RAK-permission schema in the public OpenAPI spec). For a
shape-compatible mock's read/write gating (the original motivating use case), the category-name list
above is the closest available approximation of what a RAK permission model should be keyed on, but
it should be treated as UI-label-shaped, not as literal API wire-format scope strings, because
Stripe evidently doesn't expose the latter publicly.

URL: https://docs.stripe.com/keys/restricted-api-keys (fetched directly, 2026-09-18); category-list
cross-reference: https://help.quoter.com/hc/en-us/articles/32085625269787,
https://support.spreedly.com/hc/en-us/articles/24531770522907

---

## Still open after this pass

- Whether the DXT manifest's 23-tool, one-tool-per-operation shape is still live *anywhere* (e.g. a
  legacy local-packaging path) — not checked this pass; the live `mcp.stripe.com` server (item 6) is
  confirmed to be the 11-tool generic-tool shape, which is what actually matters for "what does
  Stripe's production agent surface look like."
- Whether §2.3 of the SSA's OSS carve-out has ever been tested against a case resembling this
  project's (using a separately-MIT-published OpenAPI spec to build an unaffiliated mock, not
  literally "open source software included in the Stripe Technology") — flagged for a lawyer, not
  resolved here.
- The literal wire-format RAK scope strings (if Stripe has an internal name for them at all beyond
  Dashboard category labels) remain unknown — genuinely undocumented publicly, not just unfound.
