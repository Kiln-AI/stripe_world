# API Surface and Object Graph

## Bottom Line

`spec3.json` (Stripe API version `2026-08-26.dahlia`, 419 paths, 1454 schemas) is a single-version
snapshot — the `stripe/openapi` repo does not archive historical versions, so whatever is captured now
(recorded in `research/MANIFEST.md`) is this project's permanent pin. Taking project_overview.md §4's
named resources at face value, the honest operation count is **187 HTTP operations**, 3–4.7x the 40–60
tool budget; even after cutting every legacy-alias and sub-resource path it only drops to 148. The
**table** budget (15–20) is achievable — a recommended closed set of ~21 candidate objects collapses to
roughly **17–19 real tables** once derived/frozen objects (`balance`, `line_item`,
`credit_note_line_item`, `discount`) are built as computed views or JSON, not separate tables — but the
**tool** budget is not achievable without either tool-grouping/a generic dispatcher (subtopic 5's call)
or a further explicit scope cut. Every `$ref` from an in-scope object that crosses into Connect,
Stripe Tax, Radar, Checkout, Financial Connections/Issuing/Treasury/etc., the legacy Sources API,
`mandate`/`setup_attempt`, or the 61-key `payment_method_details` rail union has been named and ruled
model/stub/null with a reason in `scope-boundary-edges.md` — the two load-bearing exceptions to "just
null Connect fields" are `invoice.issuer` (Connect-shaped but non-nullable and required — model as
`{type: "self"}`) and `subscription`/`invoice`'s required `automatic_tax` object (model minimally as
`{enabled: false}`).

## Key Findings

- **The named-resource operation count blows the tool budget by 3–4.7x before any cutting.** 187 raw
  operations across the 21 resource prefixes §4 names; `customers` alone is 47 because of 7 legacy/
  adjacent sub-resource families (`bank_accounts`, `cards`, `sources`, `cash_balance*`, `tax_ids`,
  `funding_instructions`, plus a `payment_methods` read-only alias). See
  [minimum-closed-set-and-tool-budget.md](./minimum-closed-set-and-tool-budget.md).
- **The table budget is fine; the tool budget is the real constraint.** ~17–19 tables fits comfortably
  in 15–20 once `balance` (derived), `line_item`/`credit_note_line_item` (frozen, nested, never
  independently written), and `discount` (always embedded, no independent CRUD) are built as views/JSON
  rather than tables. Tool count, even after cutting `search` (7 ops) and every legacy alias path (33
  paths, independently corroborated by `spec3.sdk.json` excluding exactly this same set — see below),
  still lands near 60–75 by this subtopic's estimate, because ~20 fidelity-bearing state-transition
  actions (`capture`, `confirm`, `finalize`, `void`, `pay`, `close`, `release`, `reverse`...) are the
  operations the project's whole thesis depends on and are the wrong place to cut further.
- **`spec3.sdk.json` independently corroborates the legacy-path cut list.** It has 386 paths (fewer
  than `spec3.json`'s 419) but 281 *more* schemas (codegen annotation types). Its path set is an exact
  subset of `spec3.json`'s; all 33 missing paths are precisely the legacy/back-compat paths this
  subtopic recommends cutting on independent grounds (bank_accounts/cards/sources sub-resources, the
  singular `/refund` alias, `/balance/history`, legacy nested `customer.subscriptions`). Stripe's own
  SDK generator agrees these aren't "real" current operations. See
  [api-versions-and-spec-diff.md](./api-versions-and-spec-diff.md).
- **The repo publishes exactly one API version at a time, not an archive.** `info.version` is a single
  string (`2026-08-26.dahlia`); the `stripe/openapi` GitHub repo's release tags (`v2500`, `v2499`...)
  are sequential release-numbers, not `Stripe-Version` identifiers, and each release just re-publishes
  whatever the current GA version is. There is no `Stripe-Version` header parameter anywhere in the
  OpenAPI document — versioning is entirely out-of-band HTTP-header behavior the schema doesn't
  describe. Practical consequence: **what's captured in `research/stripe-openapi/` right now is the
  only version obtainable from this source; pin it, there's no "go get v2025-01-01 later" option from
  this repo.**
- **Three similarly-named ledgers are easy to conflate and only one is in scope.** `balance_transaction`
  (platform funds), `customer_balance_transaction` (a customer's invoicing credit/debit balance —
  recommended addition to the closed set, needed for `invoice.starting_balance`/`ending_balance` and
  credit-note-to-account-balance flows), and `customer_cash_balance_transaction` (the separate Cash
  Balance product, not named anywhere in §4, recommended **not** to build — a genuine scope-statement
  gap, flagged rather than silently resolved). See resource-inventory.md's `customer_balance_transaction`
  entry and scope-boundary-edges.md's Cash Balance section.
- **`payment_method_details` and its three sibling rail-unions are the widest single edge in the whole
  schema** — 57–61 mutually exclusive payment-rail sub-objects on `payment_method`, `charge`,
  `payment_intent`, and `refund`. Recommendation: model `card` (14 properties) and `us_bank_account`
  fully; stub the other ~55 rails as `{type: "<rail>"}` only. Promote a specific rail from stub to full
  model only when a specific eval task needs it.
- **Several fields Stripe clearly treats as enums are typed as bare `string` in the schema, not
  `enum`** — `dispute.reason`, `payout.status`/`method`/`source_type`, `refund.status`,
  `balance_transaction.status`, `setup_intent.usage`. The closed value set is only recoverable from the
  field's prose `description`, which this subtopic extracted and quoted per-field in
  resource-inventory.md. This is deliberate on Stripe's part (forward compatibility) but means a naive
  "read `enum` from the schema" approach to building validation will silently miss real, documented
  enum constraints on these specific fields.

## Details

- [resource-inventory.md](./resource-inventory.md) — the exhaustive per-resource reference: `object`
  discriminator, every endpoint, every field with type/nullability/required/enum, list filters, for all
  21 in-scope resources (Core, Payments, Billing, cross-cutting `event`). Read this when building the
  schema/DB design or a conformance validator.
- [minimum-closed-set-and-tool-budget.md](./minimum-closed-set-and-tool-budget.md) — the §5 Q1 answer:
  the raw 187-operation count broken down per resource, the table-vs-view collapse that gets tables
  into budget, and the concrete list of cuts (search endpoints, legacy alias paths, `features`
  sub-resource) that get tools from 187 toward budget without reaching it. Read this before scoping the
  tool list.
- [scope-boundary-edges.md](./scope-boundary-edges.md) — every `$ref`/expandable field crossing the
  declared scope boundary, with a model/stub/null ruling and reason per edge, organized by the
  out-of-scope product it touches (Connect, Stripe Tax, Radar, Checkout, Financial
  Connections/Issuing/etc., legacy Sources API, `mandate`/`setup_attempt`, the payment-method-details
  rail unions, an unidentified `smor_resource_managed_payments` preview field, Cash Balance, test
  clocks). Ends with a single summary table. Read this when deciding exactly what a given field returns.
- [api-versions-and-spec-diff.md](./api-versions-and-spec-diff.md) — how many API versions the repo
  publishes (one), how the spec is (not) keyed to `Stripe-Version` at the schema level, and the full
  `spec3.json` vs `spec3.sdk.json` diff (path-set subset relationship, the four SDK-only annotation
  keys, what the 281 extra schemas are for).

## Open Questions / Gaps

- **`docs.stripe.com` was unreachable from this session** (`WebFetch` returned `EGRESS_BLOCKED` for
  `docs.stripe.com/api/versioning`) — this subtopic's versioning findings are sourced from the
  `stripe/openapi` repo's own README/releases and `stripe-mock`'s README instead, which corroborate
  each other but weren't cross-checked against Stripe's own versioning-policy prose. If another
  subtopic's session has working `docs.stripe.com` access, it's worth a quick cross-check.
- **`smor_resource_managed_payments`** (on `payment_intent`/`setup_intent`) could not be identified —
  no expansion of "SMOR" was findable from the spec alone, and it wasn't chased further on the web
  since it was ruled `null` regardless (single optional field, no observable effect on in-scope
  behavior). If it turns out to be something load-bearing, revisit.
- **Whether `stripe/openapi`'s git history goes back far enough to reconstruct an older pinned
  `Stripe-Version`** was not checked — only the currently-fetched snapshot was examined. Not needed
  for this project (project_overview.md §8 just needs *a* pinned version, and one is already captured),
  but worth knowing if a later need arises to compare against an older version's shape.
- **The full "all Stripe event `type` strings" list** is not derivable from `spec3.json` — `event.type`
  is a bare `string`, not an enum, by design. If the functional spec needs a closed event-type list for
  this world to emit, it should come from `stripe-python`'s webhook-type constants or
  docs.stripe.com/api/events/types, not from the OpenAPI spec.
- Did not verify the exact honest tool count after full application of every cut recommended in
  `minimum-closed-set-and-tool-budget.md` — the ~60–75 estimate there is this subtopic's calculation
  from the raw counts, not a line-by-line enumerated final tool list (that's the functional spec's job,
  informed by subtopic 5's tool-shape recommendation).

## Sources

- [`research/stripe-openapi/spec3.json`](../../../../../../research/stripe-openapi/spec3.json) — Stripe
  OpenAPI spec, API version `2026-08-26.dahlia`, fetched 2026-09-18 (sha256 in `research/MANIFEST.md`).
  Primary source for every field/endpoint/enum claim in this subtopic's docs; queried directly via
  Python, this session.
- [`research/stripe-openapi/spec3.sdk.json`](../../../../../../research/stripe-openapi/spec3.sdk.json) —
  companion SDK-annotated spec, same API version, fetched 2026-09-18. Diffed against `spec3.json`
  directly, this session.
- [`research/repos/stripe-mock/README.md`](../../../../../../research/repos/stripe-mock/README.md) —
  read directly, this session; authoritative for stripe-mock's own stated single-version limitation
  (quoted in api-versions-and-spec-diff.md).
- [stripe/openapi](https://github.com/stripe/openapi) — root README, fetched via `WebFetch`,
  2026-09-18; authoritative for the `/latest`/`/preview`/`/openapi` track structure and the
  `spec3.json` vs `spec3.sdk.json` description.
- [stripe/openapi releases](https://github.com/stripe/openapi/releases) — fetched via `WebFetch`,
  2026-09-18; authoritative for the release-tag numbering scheme and cadence observed (`v2500` down to
  `v2496`, all same-day, 2026-09-18).
