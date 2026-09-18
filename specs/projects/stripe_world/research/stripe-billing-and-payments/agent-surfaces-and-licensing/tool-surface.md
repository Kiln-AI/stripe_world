# Part 1 — What Tool Surface Real Agents Get Today

Scope: `project_overview.md` §5 Q2 and open question §12.1 (tool-surface shape). Sources: the
pre-fetched `stripe/agent-toolkit` repo on disk (commit `da4991b0a0b9299d423ae2d5856e6d7e2b31b031`,
fetched 2026-09-17, package version `0.9.1` for the TypeScript toolkit / `0.7.0` for the Python
toolkit), plus `docs.stripe.com` content recovered only indirectly (see **Access constraints** below).

## 0. The single biggest finding: there is no local tool list any more

As of the toolkit's current architecture (v0.9.0+, per
`research/repos/agent-toolkit/tools/typescript/MIGRATION.md`), **the LangChain / OpenAI / Vercel
AI SDK / MCP wrappers in this repo do not define Stripe tools themselves.** They are thin clients
that connect to the hosted server at `https://mcp.stripe.com` over `StreamableHTTPClientTransport`,
call `listTools()`, and hand back whatever that call returns:

```ts
// research/repos/agent-toolkit/tools/typescript/src/shared/mcp-client.ts
await this.client.connect(this.transport);
const result = await this.client.listTools();
this.tools = result.tools as McpTool[];
```

```ts
// research/repos/agent-toolkit/tools/typescript/src/shared/toolkit-core.ts
async initialize(): Promise<void> {
  await this._initializer.initialize(async () => {
    await this.mcpClient.connect();
    const remoteTools = this.mcpClient.getTools();
    this._tools = this.convertTools(remoteTools);
  });
}
```

The Python toolkit (`tools/python/stripe_agent_toolkit/shared/mcp_client.py`) is the same shape:
same `McpTool`/`McpToolInputSchema` types, same "fetch from `mcp.stripe.com`, no fallback" design.
The `tools/modelcontextprotocol` package's local CLI (`npx -y @stripe/mcp`) is **not a local MCP
server implementation** — `src/cli.ts` shows it is a stdio↔HTTP relay that forwards every message to
`https://mcp.stripe.com` and pipes the response back over stdio (see `MCP_SERVER_URL` constant and
the `stdioTransport.onmessage` handler). So "run the Stripe MCP server locally" really means "run a
local process that proxies to Stripe's servers" — there is no offline/local tool implementation
anywhere in this repo.

**Consequence for this research:** the authoritative tool list, tool descriptions, and JSON Schemas
are defined server-side by Stripe and are not static content vendored anywhere in the OSS repos. The
closest thing to a static tool list in the repo is a Desktop Extension (DXT) manifest used for
Claude Desktop packaging (see §1). Everything else has to come from what `listTools()` returns live,
or from Stripe's own docs page, which this environment could not fetch (§4).

The migration itself is dated by the repo's `MIGRATION.md`, which frames it as "v0.8.x (direct
API-based toolkit) → v0.9.0+ (MCP-based architecture)" and lists these breaking changes verbatim:

- Async initialization now required (`await createStripeAgentToolkit()` instead of
  `new StripeAgentToolkit()`)
- "Tools are fetched from `mcp.stripe.com`. If the server is unreachable, initialization fails with
  no fallback."
- Tool names changed to snake_case (see §2)
- `@modelcontextprotocol/sdk` moved from peer to direct dependency
- **`configuration.actions` removed** — this was the old mechanism for an integrator to locally
  allow-list which resources/operations a toolkit instance exposed (e.g.
  `actions: {customers: {create: true, read: true}, invoices: {create: true}}`). It has no
  replacement in the SDK; see §3.
- The AI SDK metered-billing `middleware()` was removed

## 1. The exact tool list (as vendored in the repo)

`research/repos/agent-toolkit/tools/modelcontextprotocol/manifest.json` is the DXT (Desktop
Extension) manifest bundled for the Claude Desktop one-click install. It is the only place in the
repo with a static, enumerated tool list, and it names **23 tools** verbatim:

```json
"tools": [
  {"name": "search_documentation"},
  {"name": "get_stripe_account_in"},
  {"name": "create_customer"},
  {"name": "list_customers"},
  {"name": "create_product"},
  {"name": "list_products"},
  {"name": "create_price"},
  {"name": "list_prices"},
  {"name": "create_payment_link"},
  {"name": "create_invoice"},
  {"name": "list_invoices"},
  {"name": "create_invoice_item"},
  {"name": "finalize_invoice"},
  {"name": "retrieve_balance"},
  {"name": "create_refund"},
  {"name": "list_payment_intents"},
  {"name": "list_subscriptions"},
  {"name": "cancel_subscription"},
  {"name": "update_subscription"},
  {"name": "list_coupons"},
  {"name": "create_coupon"},
  {"name": "update_dispute"},
  {"name": "list_disputes"}
]
```

**Unflattering detail, confirmed, not guessed:** `"get_stripe_account_in"` is truncated in the repo's
own manifest — it should read `get_stripe_account_info`. Proof: `get_stripe_account_info` (full,
correct spelling) appears as a `tools_triggered` value in
`research/repos/agent-toolkit/providers/codex/plugin/test-cases.json` in the same repo, at the same
commit. This is a copy-paste/truncation bug shipped in `manifest.json`, not a real 22-character tool
name — worth knowing before anyone treats this file as ground truth for exact tool names.

Third-party MCP directories corroborate a tool count in the low-to-mid twenties as of their most
recent scrape (all via `WebSearch`, not independently fetched — see §4 caveats): one aggregator
(Speakeasy's MCP gateway catalog) lists **31 tools** for the same server, which is higher than the
23 in this repo's DXT manifest. Two readings are both plausible and I cannot distinguish them from
here: (a) the DXT manifest is stale/hand-maintained and the live server has grown past 23, or (b) the
DXT manifest deliberately lists a curated subset for the Claude Desktop packaging while the full
`listTools()` response (what the SDKs actually consume) is larger. Given the whole architecture now
fetches tools live from `mcp.stripe.com`, **treat 23 as a known-good historical snapshot, not a
live-verified current count** — this needs a live `listTools()` call (or a fetch of
`docs.stripe.com/mcp#tools`, which this environment could not reach) to pin down precisely.

Grouping the 23 by resource (my own categorization, not Stripe's):

| Resource | Tools |
|---|---|
| Account / meta | `get_stripe_account_in[fo]`, `search_documentation` |
| Customers | `create_customer`, `list_customers` |
| Products & prices | `create_product`, `list_products`, `create_price`, `list_prices` |
| Payment Links | `create_payment_link` |
| Invoices | `create_invoice`, `list_invoices`, `create_invoice_item`, `finalize_invoice` |
| Balance | `retrieve_balance` |
| Refunds | `create_refund` |
| Payment Intents | `list_payment_intents` (list-only — no `create_payment_intent` or
  `capture_payment_intent` in this list) |
| Subscriptions | `list_subscriptions`, `cancel_subscription`, `update_subscription` (no
  `create_subscription`) |
| Coupons | `list_coupons`, `create_coupon` |
| Disputes | `update_dispute`, `list_disputes` (no `create_dispute` — disputes are created by
  Stripe/the cardholder's bank, not the merchant, so this is a correct omission, not a gap) |

## 2. Naming convention

Confirmed from `MIGRATION.md`'s own before/after table (verbatim):

| Old (v0.8.x, camelCase) | New (v0.9.0+, snake_case) |
|---|---|
| `createCustomer` | `create_customer` |
| `listCustomers` | `list_customers` |
| `createPaymentLink` | `create_payment_link` |

So the convention is **`<verb>_<resource>`, snake_case, flat (no dots, no namespacing)** —
`create_customer` not `customers.create`, not `stripe.customers.create`. This is a deliberate
break from the old camelCase convention the toolkit itself used through v0.8.x. It also matches the
convention visible in the manifest's other names (`finalize_invoice`, `retrieve_balance`,
`cancel_subscription`). There is no resource-prefix/namespace segment in any observed tool name.

## 3. Read vs write gating

Gating is **entirely server-side, via Restricted API Key (RAK) scopes** — this is stated in three
independent places in the repo, all consistent:

- The MCP README: *"Tool permissions are controlled by your Restricted API Key (RAK). Create a RAK
  with the desired permissions at https://dashboard.stripe.com/apikeys"*
  (`tools/modelcontextprotocol/README.md`)
- The core client code comment: *"The server filters tools based on RAK permissions."*
  (`tools/typescript/src/shared/toolkit-core.ts`, on `initialize()`)
- `MIGRATION.md`, describing the removed `configuration.actions` option: *"Tool permissions are now
  controlled entirely by your Restricted API Key (RAK) on the server side"* — before v0.9.0, an
  integrator could additionally scope things client-side with a JS/Python object
  (`actions: {customers: {create: true, read: true}}`); that mechanism is gone with no replacement.

Practically: a caller authenticates to `mcp.stripe.com` with `Authorization: Bearer <key>` where the
key must start with `sk_` (full secret key — the client code warns against this) or `rk_` (restricted
key — recommended). `StripeMcpClient`'s own `_validate_key`/`validateKey` rejects any other prefix
before a connection is even attempted. The server is presumed to return only the subset of the 23 (or
however many) tools that the presented key's scopes permit — i.e., **tool visibility itself is the
read/write gate**, not a runtime "can I call this" check surfaced back to the agent. This is inferred
from the three quotes above; this repo does not contain the server-side filtering logic itself (that
lives at `mcp.stripe.com`, not in an open-source repo this project has access to), so the exact scope
names Stripe uses (e.g. is it `customers:write`, `Customer.Write`, or something else) could not be
verified from here — flagged as an open question in the summary.

Two connection modes both funnel into the same server:

1. **Hosted remote MCP** (`https://mcp.stripe.com`) reached directly by MCP-native clients
   (Cursor, VS Code, Claude Code, ChatGPT) — the README describes this as OAuth-secured:
   *"This allows secure MCP client access via OAuth."*
2. **Local proxy** (`npx -y @stripe/mcp --api-key=...` or the `mcp/stripe` Docker image) — a stdio
   process that authenticates to the same `mcp.stripe.com` endpoint with a static API key via
   `Authorization: Bearer`, for MCP clients that only speak stdio.

Both terminate at the same backend, so both are presumably gated by the same RAK-scope filtering.

## 4. Granularity, parameters, and API-subset coverage

**Granularity:** coarse, curated, per-operation — not 1:1 with the raw HTTP API. Contrast:

- Raw Stripe HTTP API (per the pre-fetched `spec3.json`, `info.version`
  `2026-08-26.dahlia`, per `research/MANIFEST.md`): **419 paths, 1454 component schemas.**
- MCP tool list vendored in this repo: **23 tools**, covering roughly 8 resources out of Stripe's
  full object graph, and for most of those 8 resources only 1–4 operations (e.g. Payment Intents get
  only `list`, not `create`/`retrieve`/`capture`/`cancel`; Subscriptions get `list`/`cancel`/`update`
  but not `create`). This is a ~95% reduction from the raw endpoint count, and it is intentional
  curation, not an artifact of a scan that only sampled some resources — the manifest's own selection
  (list-heavy, write-light) reads like a "safe defaults for an assistant" design rather than API
  parity.

**Parameters/schema shape:** `schema-utils.ts`'s `jsonSchemaToZodShape()` converts each tool's
`inputSchema` (JSON Schema) into a Zod shape for local validation before dispatch, and the conversion
is explicitly partial. `MIGRATION.md`, verbatim:

> **Not Supported:** `oneOf`, `anyOf`, `allOf`, `$ref`, conditional schemas
> **Supported:** Primitives, arrays, simple objects, enums, required/optional fields

This means the tool schemas Stripe actually serves for MCP are (or are constrained by the client to
be) flat: string/number/boolean/array-of-primitive/enum fields, no polymorphic unions and no internal
`$ref`s — a materially simpler shape than the raw OpenAPI spec's schemas, which do use `$ref`,
`anyOf`, and deeply nested objects throughout (see the API-surface subtopic's inventory). This is
independent, structural evidence for "the MCP layer deliberately flattens/simplifies Stripe's schema
before handing it to a model," not just a naming-convention change.

**Generic "call any endpoint" escape hatch:** **not present** in the enumerated tool list. All 23
names are specific verbs on specific resources; there is no `stripe_request(method, path, params)` or
similar catch-all in `manifest.json`, in the TypeScript/Python client code, or in the CLI. One piece
of **ambiguous, second-hand evidence** points the other way and is worth flagging rather than
resolving: `providers/codex/plugin/test-cases.json` (the OpenAI Apps SDK / ChatGPT submission file,
same repo) records test cases whose `tools_triggered` field names **`stripe_api_search`,
`stripe_api_details`, `stripe_api_read`**, and a bare **`search`** — generic-sounding names quite
unlike the 23 named MCP tools. The same file's own README
(`providers/codex/plugin/README.md`) explains the actual submission flow: *"Click **Scan Tools** and
OAuth into `acct_...` to import the tool list from `mcp.stripe.com`"* — i.e., the real tool list for
this surface is imported live from the same MCP server at submission time, and `test-cases.json`'s
`tools_triggered` values look like placeholder/example text written before that import, or possibly
leftover names from an internal search-based prototype. I could not confirm which. **Do not treat
`stripe_api_search`/`stripe_api_details`/`stripe_api_read` as a documented, real generic tool** — it
is unverified and possibly stale test fixture content, not a confirmed second tool-surface design.

**Documentation search as a tool:** `search_documentation` is itself worth noting — it is not an API
operation at all, it is RAG-over-docs exposed as a tool, i.e. the MCP server also functions as a
docs-search endpoint for the agent, separate from the CRUD surface.

## 5. Contrast: agents hitting the raw HTTP API directly

The pre-fetched `stripe-python` SDK (`research/repos/stripe-python`) is the baseline for "an agent
just uses the normal client library": one method per operation, full parameter fidelity (every field
in `spec3.json`, including the ones the MCP tool list omits), full pagination/expand support, and no
RAK-driven tool filtering — whatever the key can do, the code can call, with permission errors
surfacing as normal HTTP 403s at call time rather than as an invisible/omitted tool. This is a
materially different reliability posture than the MCP surface:

- **Raw SDK:** agent (or its harness/framework) must choose from potentially hundreds of SDK methods
  with full-fidelity, deeply-nested parameter schemas (`$ref`, `oneOf` unions, nested objects) —
  exactly the shape the toolkit's own schema converter says it *can't* support losslessly.
- **MCP tool list:** agent chooses from ~23 pre-flattened, pre-curated, snake_case-named tools, most
  of them read-only, with permission errors avoided ahead of time by simply not listing tools the key
  can't use.

This is the practical form of the "coarse curated tools vs. fine-grained 1:1 API mapping" tradeoff
this subtopic is asked to make a call on (§6).

## 6. Published guidance/measurement on tool-count and schema-size effects

**Access constraint up front:** this environment's `WebFetch` tool could reach only `github.com` and
`raw.githubusercontent.com` — every other domain tried (`docs.stripe.com`, `stripe.com`,
`arxiv.org`, `anthropic.com`, `modelcontextprotocol.io`, `archestra.ai`, `speakeasy.com`,
`portkey.ai`, `policylayer.com`, `news.ycombinator.com`, `en.wikipedia.org`, and others) returned
`EGRESS_BLOCKED` from the session's network policy (confirmed via
`curl "$HTTPS_PROXY/__agentproxy/status"`, which logs each as a `connect_rejected`/403 from the
gateway). `WebSearch` still worked and returned real result links with AI-generated summaries of
their content, so everything in this section is **sourced from WebSearch snippets/summaries, not from
an independently fetched and read primary document.** Treat the specific percentages below as
"reported by a secondary summarization pass over these sources," not as personally verified figures —
this is a real gap; a follow-up pass with working `WebFetch` access to these domains should confirm
them against the primary text.

With that caveat, several independent sources converge on the same qualitative and rough quantitative
picture:

- **Anthropic's own engineering guidance** (`anthropic.com/engineering/writing-tools-for-agents`, via
  WebSearch summary): a working range of **5–15 tools** active at once "for clean selection and cheap
  context," with **no measurable degradation up to roughly 20 tools**, and a reported before/after
  number for Claude specifically — tool-picking accuracy **79.5% → 88.1%** after switching from
  loading every tool into context at once to loading tools on demand.
- **RAG-MCP** (an academic paper on retrieval-augmented tool selection for MCP, found via WebSearch,
  not independently read): reported baseline tool-selection accuracy dropping from **>90% on a small
  tool set to ~14% at scale**, with one specific instrumented number of **13.62%** baseline accuracy
  on a large tool set, recovering to **43%** with retrieval-augmented selection (a **3.2×**
  improvement figure is also quoted).
- **A "HumanMCP" benchmark** (via WebSearch summary): Gemini 2.0 Flash accuracy **87.4% at 500 tools
  → 65% at 2,000 tools**.
- **Archestra's "How many MCP tools is too many?"** analysis (via WebSearch summary): describes the
  degradation curve as flat through roughly 5–15 tools, an "elbow" in the **20–35 tool** band for
  frontier models specifically, and slow continued degradation past 40.
- Multiple independent blog-level sources converge on the same **~20–40 tool** range as "where
  accuracy visibly starts to bend," even though none of them agree on an exact number, and all
  emphasize that the real threshold is model- and task-dependent and should be measured, not assumed.

**Why this matters for this project specifically:** the API-surface subtopic's own budget (from the
research plan) targets **40–60 tools**, i.e. right at or past the "elbow" every source above
describes — and Stripe's own officially curated MCP surface sits at roughly half that (23, possibly
up to ~31), covering a small, read-heavy slice of its ~419-endpoint API. That is independent
real-world evidence that Stripe itself chose *not* to expose anywhere near a 1:1 tool-per-endpoint
surface, consistent with (not proof of, given the sourcing caveat above) the published guidance that
large flat tool lists measurably hurt tool selection.

## 7. Recommendation (§12.1)

**Recommend one-tool-per-operation for the curated 40–60 in scope, with no default generic escape
hatch.** Reasoning, with the honest case for each option:

**One-tool-per-operation (e.g. `create_customer`, `list_invoices`, `cancel_subscription`)**
- *For:* Matches what Stripe itself actually ships (23 named, flat, snake_case tools — see §1–2) and
  is directly gradeable: an eval can assert "the agent called `finalize_invoice` with
  `invoice=in_123`," a crisp, unambiguous signal. Per-tool JSON Schemas (small, flat, per §4) are
  exactly the shape the tool-count/schema literature above says keeps selection accuracy high, and
  40–60 tools is a defensible number only if each tool's schema stays small and flat — this format
  forces that discipline naturally, one schema per operation.
- *Against:* Someone has to hand-author and hand-maintain 40–60 schemas as the closed object set
  evolves; anything outside the chosen set is simply unreachable by the agent, full stop (which is
  also the point, per subtopic 1's closed-set framing).

**Single generic `stripe_request(method, path, params)`**
- *For:* Total coverage of the object graph with one schema to write, trivially matches the tool
  count guidance (n=1 tool can never hit a "too many tools" ceiling), and sidesteps needing a curated
  40–60 list at all.
- *Against:* This is the worse choice for an eval-driven project specifically. It pushes correctness
  entirely into free-text `path`/`params` construction, which is much harder to grade than "right
  tool, right args" — you'd need to parse and validate the emitted path/params against the spec to
  know if the call was even well-formed, closer to a text-to-SQL eval than a tool-use eval. It also
  quietly defeats the "minimum closed set" discipline the API-surface subtopic is building: a generic
  tool can technically reach every one of the 419 raw paths, whether or not that path is in the
  declared 40–60, so "in scope" stops being enforced by the tool surface and has to be enforced some
  other way (e.g. an allow-list inside the world's dispatcher) if it's to mean anything.
- Also notably **absent from Stripe's own real-world design** — nothing in the DXT manifest, the
  TypeScript/Python client code, or the CLI defines or forwards a raw passthrough tool (the one piece
  of ambiguous `stripe_api_*` evidence in §4 does not rise to confirmed prior art for this pattern).

**Both (curated named tools + one passthrough for the long tail)**
- *For:* Keeps the common-path tools gradeable while giving a pressure-release valve for anything the
  40–60 budget can't fit, without redoing the tool list every time a gap is found.
- *Against:* Reintroduces exactly the grading ambiguity above for whatever traffic goes through the
  passthrough, and — unless the eval harness deliberately never exercises it — makes "which tools did
  the eval actually cover" an open question again. If adopted at all, it should be scoped narrowly
  (e.g. excluded from graded evals, logged loudly when used, or reserved for a clearly-separate
  "advanced/ungraded" track) rather than treated as a first-class alternative to the curated set.

Net: pick **one-tool-per-operation**, size the tool list at or under the accuracy elbow every source
in §6 describes (the existing 40–60 target is already at the edge of that; err toward 40, not 60, if
the eval design wants headroom), and treat a generic passthrough as something to explicitly reject
for the graded surface rather than add "just in case."
