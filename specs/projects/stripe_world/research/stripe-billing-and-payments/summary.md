# Research: Stripe Billing & Payments Core (for a Seahaven world)

## Bottom Line

The slice is buildable and the sources are good enough to spec against — with one structural
exception and one budget breach that the brief has to resolve before the functional spec is written.
`spec3.json` (API version `2026-08-26.dahlia`) turned out to be a far stronger source than expected:
Stripe embeds long-form docs prose directly into its `description` fields, so the subscription status
machine, the invoice status machine, the `balance_transaction` ledger, the refund/credit-note rules
and the entire `test_helpers/test_clocks` API are all quotable verbatim from the schema rather than
inferred from blog posts. The **table** budget (15–20) holds comfortably at ~17–19; the **tool**
budget (40–60) does not — the honest operation count for the resources §4 names is 187, and lands at
60–75 even after every legitimate cut, with the ~20 state-transition actions (`capture`, `finalize`,
`void`, `pay`, `reverse`…) being precisely the operations the project's thesis depends on and the
wrong place to cut. **Time is the one genuine framework blocker**: Seahaven has no clock-advance
mechanism anywhere, and "exactly one `now` per instance" is baked into the fixture sidecar, the state
document envelope and the composition model as a structural fact, not a convention — so a `test_clock`
needs one new framework primitive and forces a narrower, one-shared-account-clock reading of Stripe's
model. Everything else Seahaven handles already: fork cost is **measured** at 4–8ms (20k rows) and
11–29ms (120k rows), there is no tool-count cap, integer-cents matches Stripe's own convention, and
composition — the `add_world` goal — is the framework's most mature subsystem. The one prior-art
question is settled outright: `stripe-mock`'s own README says it "does not attempt to reproduce the
behavior of the real Stripe API at all," and no other open-source Stripe emulator implements test
clocks. **Deterministic-time billing against a stateful mock is unclaimed territory** — that is the
project's thesis, confirmed rather than assumed.

## Key Findings

- **The tool budget is the binding constraint, and it fails by 1.5–4.7x.** 187 raw HTTP operations
  over §4's named resources; 148 after cutting legacy aliases; ~60–75 after collapsing derived objects
  and cutting `search`. `spec3.sdk.json` independently corroborates the legacy-path cut list — its 386
  paths are an exact subset of `spec3.json`'s 419, and all 33 missing paths are exactly the ones
  recommended for cutting. ([api-surface-and-object-graph](./api-surface-and-object-graph/summary.md))
- **Stripe's own agent surface is 23 curated tools with no generic escape hatch** — flat snake_case
  (`create_customer`), read/write gated server-side by Restricted API Key scope, schemas deliberately
  flattened (the toolkit's JSON-Schema→Zod converter refuses `oneOf`/`anyOf`/`$ref`). As of v0.9.0+
  the toolkit no longer defines tools locally at all; it calls `listTools()` against `mcp.stripe.com`,
  so the authoritative list lives at Stripe, not in any OSS repo.
  ([agent-surfaces-and-licensing](./agent-surfaces-and-licensing/summary.md))
- **Seahaven has no way to advance time, categorically.** Confirmed by reading `clock.py` end to end
  and grepping the source for `advance`/`test_clock`/`set_now`/`travel` — nothing matches, and
  `concepts.md` states it outright ("does not… move a clock forward… None of these is planned"). The
  SQL function overrides are registered `SQLITE_DETERMINISTIC`, so a naive mutable-cell fix risks
  stale reads within a statement. ([seahaven-capabilities](./seahaven-capabilities/summary.md))
- **The change-log machinery already supports the expensive half of a test clock.** A single
  `advance_test_clock` tool that synchronously walks every attached subscription and writes dozens of
  invoice/dunning rows in one transaction folds correctly under the documented rules with **no**
  framework change. Only the clock's mutability is missing.
  ([seahaven-capabilities](./seahaven-capabilities/summary.md))
- **The error envelope's wire-level `type` field has only four values** (`api_error`, `card_error`,
  `idempotency_error`, `invalid_request_error`) — confirmed by both the schema enum and an observed
  real 429 body. Everything people call `rate_limit_error`/`authentication_error`/`permission_error`
  is a client-side convenience derived from HTTP status, never a value Stripe puts on the wire.
  Full `code` (~215) and `decline_code` (~43) enumerations are captured.
  ([cross-cutting-semantics](./cross-cutting-semantics/summary.md))
- **The invoice schema has drifted from every tutorial.** At `2026-08-26.dahlia` there is no top-level
  `invoice.subscription` (now `invoice.parent.subscription_details.subscription`) and no top-level
  `invoice.days_until_due`. The ~1-hour draft window is now an explicit queryable field,
  `invoice.automatically_finalizes_at`. Anything specced from memory here will be wrong.
  ([billing-and-money-behavior](./billing-and-money-behavior/summary.md))
- **Dunning has no fixed schedule to transcribe.** Smart Retries is ML-scheduled, configurable only as
  "N tries within a window" (Stripe's recommended default: 8 tries / 2 weeks) with three end-of-schedule
  outcomes. Do not build a retry-day table. One spec-verbatim precision worth keeping:
  `invoice.attempt_count` increments only on *automatic* retries, and keeps incrementing even when a
  non-retryable decline blocks the network attempt. ([billing-and-money-behavior](./billing-and-money-behavior/summary.md))
- **Fixture fork cost is measured, not assumed** — 4.04–7.78ms across 50 forks of a 5,000-customer /
  20,000-row / 2.25MiB fixture; 10.69–29.07ms at 20,000 customers / 120,000 rows / 13.7MiB. §11's
  "the large fixture must still fork in milliseconds" is satisfied with headroom.
  ([seahaven-capabilities](./seahaven-capabilities/summary.md))
- **All four Stripe sources are MIT, copyright Stripe** — `openapi`, `stripe-mock`, `stripe-python`,
  `agent-toolkit`. Committing `spec3.json` into an MIT repo is squarely permitted; the only obligation
  is carrying the copyright + permission notice (a `THIRD_PARTY_LICENSES` file satisfies it — MIT has
  no NOTICE mechanism, that is an Apache concept). Field names, enum values and `cus_`/`in_`/`pi_` id
  prefixes are functional API vocabulary that trademark law does not reach.
  ([agent-surfaces-and-licensing](./agent-surfaces-and-licensing/summary.md))

## Implications

### The three decisions §12 reserves for the owner

**§12.1 Tool surface shape — decide this *together with* the budget breach; they are one decision.**
The API lane's 60–75 honest count and the agent lane's tool-shape recommendation collide directly.
Evidence: Stripe's own production agent surface is one-tool-per-operation with no passthrough
(strong — read from the vendored repo), and the tool-count literature converges on a ~20–40-tool
accuracy elbow (weak — all WebSearch-relayed, no primary text read). The agent lane recommends
**one-tool-per-operation, erring toward 40 rather than 60, with a generic passthrough explicitly
rejected for the graded surface**, on the grounds that `stripe_request(method, path, params)` turns
tool-use grading into text-to-SQL-style path/param parsing and silently defeats the closed-set
discipline (a generic dispatcher can reach all 419 paths regardless of what the spec declares in
scope). Cost of that choice: the §4 tool budget must either move to ~60–75, or a further explicit
scope cut must be made and stated out loud. The named candidates, in order of cost-effectiveness:
cut `search` (7 ops), cut every legacy alias path (~15 ops, corroborated by `spec3.sdk.json`),
collapse `products/features` into a field (3 ops), and decide `subscription_schedules` as a unit
(6 ops + 1 table). Cutting further than that means cutting state-transition actions, which is cutting
the thesis. **A hybrid is available but should be scoped narrowly if taken** — passthrough excluded
from graded evals, logged loudly when used — rather than treated as a co-equal surface.

**§12.2 Naming — the evidence supports either, and the split is cleaner than the question implies.**
Two separable things: *field names and shapes* versus *the world's own name*. Reusing Stripe's field
names, enum values, error codes and id prefixes is unambiguously fine under MIT with attribution, and
conformance testing (§8) only makes sense against real shapes — that half is settled, strongly.
The world's *name* is the open part, and the evidence there is weaker: Stripe's Mark Usage Terms and
SSA could not be read directly (blocked), so the lane's read of them is WebSearch-relayed. The lane
recommends a **neutral world name plus a paybox-style non-affiliation line** — *"[World name] is not
affiliated with, endorsed by, or connected to Stripe, Inc. 'Stripe' is used only to describe API
compatibility."* — noting that `paybox` covers 92 Stripe endpoints without putting "Stripe" in its own
name, while `localstripe` uses the name and carries no disclaimer at all. Cost of the real name: an
asymmetric risk (trademark complaint, forced rename after ecosystem adoption) against a catchier name;
cost of the neutral name: the pretrained-familiarity benefit §12.2 identifies as "most of the point"
is diluted *only if* field names go neutral too — which the evidence says they need not. Reading the
SSA and Mark Usage Terms primary text before finalizing is worth doing and was not possible here.

**§12.3 Time — the load-bearing evidence is the Seahaven lane's, and it is strong (local source, read
directly).** There is no advance mechanism, and the one-`now`-per-instance invariant appears in three
independent places (fixture sidecar, state-document envelope, `composition.md`'s explicit "There is
exactly one `now`, because no store has a clock of its own"). Against that, Stripe's real test clocks —
documented from `spec3.json` directly, so also strong — are **per-customer-graph, not per-account**:
`frozen_time` freezes "all objects belonging to this clock," two customers on two clocks can hold
divergent `now`s simultaneously, attachment is permanent and one-way, and only `customer` and `quote`
creation accept a `test_clock` param (everything else inherits transitively). Advance is forward-only,
capped at 2× the shortest attached subscription's interval (or 2 years with none attached), and is
genuinely async (`advancing`/`ready`/`internal_failure`). What advancing *does* to attached objects is
the weaker half — WebSearch-sourced, not directly read.
The three options, with honest costs:
1. **Hold one instant, bake time-dependent state into fixtures.** Zero framework change. Cost: the
   brief's own judgment that "subscriptions, dunning and trials are substantially less interesting
   without time moving," and the headline double-charge-on-retry eval survives but the proration and
   dunning evals become state-inspection rather than behavior.
2. **Add one framework primitive** (mutate `ctx.clock`'s instant mid-instance and re-register the SQL
   overrides on live connections) and build `advance_test_clock` in world code, doing all
   time-dependent business logic synchronously in that one call. Cost: accepting **one shared account
   clock per instance**, not Stripe's per-customer clocks — a narrower but defensible reading. This is
   the cheapest path that gets time moving, and it is squarely a §7 Missing-capability finding to file
   on the Seahaven repo *before* building around a workaround.
3. **Per-customer independent clocks.** Breaks the one-`now` invariant in all three places. Not costed
   by the research; treat as out of reach for this project's timeline.
The lane deliberately did not prototype option 2 — that is an architecture-phase call, and §12.3
reserves the decision.

### What this means for the spec and architecture, beyond the three decisions

- **Build the schema-conformance harness first**, as §6.1 says. The `spec3.json` snapshot captured now
  is a **permanent pin** — `stripe/openapi` publishes one version at a time and does not archive, so
  there is no "go get an older `Stripe-Version` later" option from that source.
- **Four fields Stripe treats as enums are typed as bare `string`** — `dispute.reason`,
  `payout.status`/`method`/`source_type`, `refund.status`, `balance_transaction.status`,
  `setup_intent.usage`. A naive "read `enum` from the schema" validator will silently miss them; the
  closed value sets are recoverable only from the `description` prose, which the API lane extracted.
- **`payment_method_details` is the widest edge in the schema** — 57–61 mutually exclusive rail
  sub-objects across four parents. Model `card` and `us_bank_account` fully; stub the other ~55 as
  `{type: "<rail>"}` and promote only when an eval needs it.
- **Two Connect-shaped fields cannot simply be nulled**: `invoice.issuer` is non-nullable and required
  (model as `{type: "self"}`), and `subscription`/`invoice`'s `automatic_tax` object is required
  (model minimally as `{enabled: false}`) despite Stripe Tax being out of scope.
- **Three similarly-named ledgers must not be conflated**: `balance_transaction` (platform, in scope),
  `customer_balance_transaction` (invoicing credit/debit — a recommended *addition* to the closed set,
  needed for `invoice.starting_balance`/`ending_balance`), and `customer_cash_balance_transaction`
  (a separate product, recommended out).
- **`subscription_schedules`: conditional, scoped-down inclusion.** It is the only way to express
  "declare a future change now, apply it later," which `subscriptions.update` cannot express at all —
  but its `phases` array duplicates most of the subscription schema. Worth ~6 tools and a table; both
  the API lane and the billing lane flag it as the cleanest single cut if the budget must give.
- **Do not hard-code a proration rounding rule yet.** The formula and a worked example are confirmed
  (`credit = -fraction × old_price`, `debit = +fraction × new_price`, computed to the second; $10→$20
  at mid-period nets +$5 on two lines), but the cent-rounding rule is inferred by analogy to Stripe's
  fee-rounding rule (round-half-up), not documented. The billing lane escalates this specifically as
  the single thing most likely to be got wrong downstream. It needs a recorded API trace — see Gaps.
- **Prefixed deterministic ids work but have no worked example.** `ctx.ids.uuid()` is fixed-format;
  `ctx.ids.random` is the documented and only escape hatch, and the lane built and verified a
  deterministic `cus_...` generator on it this session. Nothing in the repo demonstrates the pattern
  and no lint catches a tool that reverts to a bare UUID — worth a helper plus a test.
- **One composition constraint to design around**: tool prefixing renames tools but never rewrites
  descriptions, so cross-referencing bare tool names in docstrings trips lint `SH206` under any host
  that prefixes this world.

### Where the research contradicts or complicates the brief

The brief asked to be told rather than quietly accommodated. Five items:

1. **§4's tool budget (40–60) does not fit the §4 resource list.** Covered above. This is the largest
   and it needs an explicit decision, not a fudge.
2. **`customer_cash_balance_transaction` appears in neither §4's "In" nor its "Out" list** — a genuine
   scope-statement gap. The API lane's ruling is "do not build," flagged rather than silently resolved.
3. **The legacy Sources/Cards/Bank Accounts API is also in neither list.** It is functionally superseded
   by `payment_methods`, which §4 *does* list as in-scope core; treating both as in-scope means two
   parallel payment-method representations. Cutting the three sub-resource path families is the single
   largest tool-budget win available that touches no fidelity-bearing object. `setup_attempt`,
   `mandate` and an unidentified preview field `smor_resource_managed_payments` are likewise unnamed;
   all ruled stub-or-null with reasons.
4. **§11's "Python 3.14+" has a live environment trap.** Under the only Python 3.14 the research
   sandbox could obtain (`3.14.0rc2`), the locked `pydantic==2.13.5` crashes `import seahaven` at the
   first line. The fix (`pydantic==2.12.3`) is documented only in a benchmark results file, not in the
   setup path. Whether a final 3.14 build fixes it could not be tested.
5. **§4's "test-mode magic card numbers as the failure-injection mechanism" rests on a table this
   research could not fully resolve.** Core decline cards, 3DS cards and the two dispute cards are
   corroborated; refund-failure and payout-failure magic values, the per-`decline_code` card numbers,
   IBAN test values and dispute-evidence magic strings are not, and `4000 0000 0000 0069` has a genuine
   source conflict (stolen vs. expired). These numbers should not be frozen into a fixtures file from
   the current sourcing.

## Conflicts and Uncertainty

- **Stripe's live MCP tool count: 23 vs 31.** The vendored DXT manifest names 23; a third-party
  catalog reports 31 for the live server. Neither was independently fetchable. Two readings are equally
  plausible (stale manifest, or a curated Claude Desktop subset). The *shape* finding —
  one-tool-per-operation, flat snake_case, no passthrough — is robust either way; the count is not.
  Note also that the manifest itself ships a truncation bug (`get_stripe_account_in`), proven against a
  sibling fixture in the same commit — do not treat it as ground truth for exact names.
- **Stripe's test clocks are per-customer-graph; Seahaven's `now` is per-instance.** This is the real
  tension under §12.3, stated above rather than here.
- **Dunning `unpaid` outcome: two sources disagree** on whether the invoices end up `draft` or
  "immediately automatically closed" (the spec's own wording). Unresolved.
- **The `unpaid` → `active` recovery path is ambiguous** — whether a subscription auto-recovers once
  its invoices are paid, or requires an explicit update, could not be settled.
- **`balance_transaction.type` for a dispute withdrawal is inferred, not confirmed.** The 51-value enum
  has no dispute-specific value; `adjustment` is the best inference.
- **One ambiguous signal against the "no passthrough" finding**: `stripe_api_search` /
  `stripe_api_details` / `stripe_api_read` appear in an OpenAI Codex submission test fixture in the
  agent-toolkit repo. It may be stale placeholder content or evidence of a genuinely more generic
  pattern on a different surface. Flagged, not resolved — it does not rise to confirmed prior art.
- **The tool-count "~20–40 elbow" is the weakest load-bearing claim in the whole research.** Several
  independent sources converge on it, but every one of them is a WebSearch summary — no primary paper,
  benchmark or engineering post was read directly.

## Gaps

**The single biggest limitation, stated plainly: this session's network egress policy blocked
`docs.stripe.com`, `stripe.com`, `api.stripe.com` and `web.archive.org` for the entire run** (403 on
CONNECT — a strict allowlist that admits GitHub and package registries but not the general web). Every
lane hit it, and one lane notes it contradicted its own dispatch prompt's premise that WebFetch was
working. GitHub-sourced evidence is fully intact — `spec3.json`, `spec3.sdk.json`, `stripe-mock`,
`agent-toolkit`, `stripe-python` and the vendored Seahaven were all read directly — and the billing
lane's discovery that `spec3.json` embeds long-form docs prose in its `description` fields partly
compensates, often producing a *stronger* source than the doc page would have been. But **any claim
whose only home is a prose doc page is sourced from WebSearch's synthesized summaries, not a direct
page read.** The lane documents label claims by source strength throughout; that distinction should be
preserved, not flattened. If the allowlist changes, the specific re-run scope is:

- *Cross-cutting*: exact idempotency-key retention wording; the exact pagination ordering-guarantee
  sentence; `starting_after`/`ending_before` behavior on a deleted object id; what error a bad
  `expand[]` path returns (or whether it is silently ignored); unknown-`Stripe-Version` handling and
  whether a version response header exists; `param` naming rules for nested/array params; confirming
  the `decline_code` table is complete (42–43 captured against a claimed 44).
- *Billing*: **the proration cent-rounding rule** (the lane escalates this one specifically), and
  whether credit/debit lines round independently or net-then-round; the `subscription_cycle` invoice
  lead time before a period boundary; pure quantity-change reproration semantics; the ninth
  hard-decline retry-gating code; the `unpaid`-invoice-status contradiction; `dispute.status=prevented`;
  the dispute-withdrawal `balance_transaction.type`; dispute fee refundability on a win.
- *Test mode*: the full magic-card table including refund-failure and payout-failure values, IBAN test
  values and dispute-evidence magic strings; the `4000 0000 0000 0069` conflict; test-clock
  `deletes_after` TTL and `internal_failure` semantics; any per-account test-clock cap; an itemized
  list of test-vs-live object-shape differences.
- *Agent surfaces*: `docs.stripe.com/mcp#tools` and `/agents` for the live tool list; Stripe's Mark
  Usage Terms and SSA primary text; the RAK scope identifiers used for server-side tool filtering.
- *API surface*: `docs.stripe.com/api/versioning` as a cross-check; the closed `event.type` list, which
  is **not** derivable from `spec3.json` (`event.type` is a bare string by design) and must come from
  `stripe-python`'s webhook constants or the docs; identifying `smor_resource_managed_payments`.

**Environment constraint on the project plan: `api.stripe.com` being blocked means §8's conformance
*recording* step cannot run in this environment at all, whatever test-mode API key is supplied.**
Replay-only CI is unaffected and remains the right design — but the initial cassette recording, and
every re-record, must happen somewhere with egress to `api.stripe.com`. That should be planned for
explicitly rather than discovered during implementation.

**Two smaller gaps not caused by egress:**

- The `seahaven-capabilities` lane could not test whether a final Python 3.14 release fixes the
  pydantic crash (only `3.14.0rc2` was obtainable — the same build the framework's own bench note
  describes as broken), did not measure fork cost beyond 120,000 rows, and did not benchmark SQL-side
  vs Python-side nested-object assembly. It also deliberately did not prototype a clock-advance patch,
  since §12.3 reserves that decision.
- The API lane did not produce a line-by-line final tool enumeration after applying every recommended
  cut — the 60–75 figure is a calculation from raw counts, and the exact list is the functional spec's
  job once §12.1 is decided.

**Harness friction worth one line, because this project will keep hitting it:** several lanes found the
`Write` tool refused the filename `summary.md` and had to write under another name and `cp` into place.
The `seahaven-capabilities` lane's summary was originally written as `bottom-line.md` for this reason;
both `bottom-line.md` and `summary.md` exist there with identical content, and `summary.md` is the one
linked below. This is reproducible, harness-level, and unrelated to the research itself.

## Subtopics

- [API surface and object graph](./api-surface-and-object-graph/summary.md) — `spec3.json` as ground
  truth: per-resource field/enum/endpoint inventory, the minimum closed set, and every `$ref` crossing
  the scope boundary ruled model/stub/null. Headline: 187 operations, 3–4.7x the tool budget; tables
  fit at ~17–19, tools do not.
- [Cross-cutting semantics](./cross-cutting-semantics/summary.md) — idempotency, pagination, expand,
  metadata, the error envelope, versioning. Headline: only four wire-level error `type` values, and
  `stripe-mock` implements essentially none of these behaviors.
- [Billing and money behavior](./billing-and-money-behavior/summary.md) — the prose-only state machines:
  subscriptions, invoices, prorations, dunning, refunds, disputes, the balance ledger, credit notes.
  Headline: most of it is quotable verbatim from `spec3.json`'s own descriptions; the proration rounding
  rule is the one real hole, and the invoice schema has drifted from every tutorial.
- [Test mode, clocks and prior art](./test-mode-clocks-and-prior-art/summary.md) — magic cards,
  sandboxes, the full test-clock API, and a line-by-line read of `stripe-mock`. Headline: stripe-mock's
  own README confirms it is stateless and behavior-free by design, and **no** open-source Stripe
  emulator implements test clocks.
- [Agent surfaces, licensing and naming](./agent-surfaces-and-licensing/summary.md) — the real Stripe
  MCP/agent-toolkit tool list, tool-shape guidance, licenses and non-affiliation language. Headline:
  23 curated one-tool-per-operation tools, no generic escape hatch, everything MIT.
- [Seahaven capabilities](./seahaven-capabilities/summary.md) — the vendored framework's capability map
  and the seven pressure points a Stripe world applies. Headline: five of seven need no framework
  change and fork cost is measured in single-digit milliseconds, but **there is no time-advance
  mechanism anywhere**, and one-`now`-per-instance is structural.
