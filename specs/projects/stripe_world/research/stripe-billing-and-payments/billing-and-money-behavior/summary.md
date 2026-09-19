# Billing and Money Behavior

## Access method note (read this before the rest)

`docs.stripe.com` and `stripe.com` were **blocked outright** by this session's egress proxy for the
entire research pass (confirmed via both `WebFetch` and direct `curl`, every path tried, including
`web.archive.org` as a fallback mirror — also blocked). `WebSearch` remained available and returns
content that quotes/synthesizes the actual docs pages (it appears to fetch independently of the blocked
proxy), so it was the primary tool used for prose-doc claims. Wherever `spec3.json`'s own
`description` fields happened to carry the same prose verbatim (Stripe embeds long-form docs text
directly into the OpenAPI spec for many enums), that was used as the stronger, directly-quoted source
and is flagged as such throughout. Every claim below is labeled with its source strength; treat
WebSearch-only claims as corroborated-but-not-directly-verified, and flag anything genuinely
load-bearing for a follow-up recorded-API-trace pass once test-mode access exists (subtopic 4's
territory).

**Update 2026-09-18 — gap-closure pass.** A Tavily MCP server was connected that *does* reach
`docs.stripe.com`/`stripe.com` directly (`tavily_extract`/`tavily_crawl`/`tavily_search`, verified
working). A follow-up pass used it to close nine specific, itemized gaps left by the original pass —
including, as the top priority, the proration cent-rounding rule. Full detail, verbatim quotes, and URLs
for every item closed are in **[gap-closure-2026-09-18.md](./gap-closure-2026-09-18.md)**, and the
relevant sections of each detail file below now carry inline "Resolved 2026-09-18" / "Update 2026-09-18"
notes pointing back to it. Claims now marked that way are directly-quoted `docs.stripe.com` reads, a
stronger source tier than the original pass's `WebSearch` snippets.

## Bottom Line

The subscription and invoice status machines, the `balance_transaction` ledger, and refund/credit-note
mechanics are documented precisely enough to implement from directly — much of that precision comes
straight out of `spec3.json`'s own field descriptions, which are stronger sources than any blog post.
Proration arithmetic has a confirmed formula (`credit = -fraction × old_price`, `debit = +fraction ×
new_price`, fraction computed to the second) and a confirmed worked example ($10→$20 mid-period nets
+$5). **The rounding rule — the single biggest open risk from the original pass — is now resolved for
its load-bearing question.** A worked example in Stripe's own docs with fractional-cent inputs proves,
with reproducible arithmetic, that **credit and debit proration line items are rounded independently to
the nearest cent and then summed — not netted at higher precision and rounded once** (a $10/3-and-$20/3
-scale example: independently-rounded lines sum to -334 cents; a net-then-round approach would instead
give -333 cents, which is not what Stripe's own example shows). Only the exact tie-break convention for
an `x.xx5` cent boundary remains undocumented — round-half-up is still an assumption there, but now a
narrow, low-risk one rather than the load-bearing assumption it was before. See
`proration-arithmetic.md` and `gap-closure-2026-09-18.md` item 1 for the full arithmetic proof. Smart
Retries/dunning is deliberately non-deterministic (ML-scheduled, not a fixed day table) with only the
configuration envelope documented (8 tries / 2 weeks default, three end-of-schedule outcomes); do not
build a fixed retry-day table into the mock, model it as "N attempts within a window, outcome per account
setting." All nine hard-decline codes that gate retries are now confirmed directly from Stripe's own docs
(the ninth, missing from the original pass, is `transaction_not_allowed`). A real, persisting
contradiction was confirmed inside Stripe's own docs corpus over what happens to invoices for an
`unpaid`-outcome subscription (`spec3.json`'s prose says "closed", two independently-read
`docs.stripe.com` pages say "draft") — the practical recommendation is to implement `draft`-persisting,
since "closed" was never a real `invoice.status` enum value in the first place and the docs-page
evidence now outweighs the spec's loose prose 2-to-1.
The invoice object's schema has drifted substantially from what most existing tutorials describe
(`invoice.subscription` and `invoice.days_until_due` no longer exist as flat fields at API version
`2026-08-26.dahlia`) — this is a load-bearing finding for the schema subtopic, not just this one.
`subscription_schedules` is recommended as **conditional, scoped-down inclusion**: real value for
"declare a future change" scenarios, but its `phases` array duplicates most of the subscription schema
and is not worth full-fidelity modeling against the project's 40-60-tool budget.

## Key Findings

- **Subscription status machine is fully specified in `spec3.json` itself, verbatim.** The `status`
  enum's own description field is the canonical state-machine doc (8 states, every named transition and
  trigger) — see `subscription-status-machine.md`. **The `unpaid`→`active` recovery question is now
  closed**: a subscription auto-recovers to `active` simply by paying its most recent invoice before the
  due date — no separate subscription update call is needed or described (confirmed via a direct read of
  <https://docs.stripe.com/billing/subscriptions/overview>; see `gap-closure-2026-09-18.md` item 6).
- **The invoice schema has moved on from the "classic" shape.** At API version `2026-08-26.dahlia` there
  is no top-level `invoice.subscription` (now `invoice.parent.subscription_details.subscription`) and no
  top-level `invoice.days_until_due` (create-only param; the computed value is `invoice.due_date`). The
  ~1-hour draft-finalization window is now an explicit, queryable field:
  `invoice.automatically_finalizes_at`. See `invoice-status-machine.md`. **The `subscription_cycle`
  invoice's creation timing is now confirmed**: it's created exactly at the period boundary (zero lead
  time before it), with the ~1-hour draft window following creation, not preceding it.
- **Proration formula, worked example, and rounding rule are all confirmed**: `proration_factor = seconds
  remaining in period / total period seconds`; credit = `-factor × old_price`; debit =
  `+factor × new_price`; both computed to the second. Worked example: $10→$20 exactly halfway through
  the period nets to +$5 on two separate line items (-$5 credit, +$10 debit). **Rounding**: each line
  item rounds independently to the nearest cent, then the lines are summed — confirmed via a fractional
  BRL example in Stripe's own docs with reproducible arithmetic (see `proration-arithmetic.md` and
  `gap-closure-2026-09-18.md` item 1); only the exact tie-break rule for an `x.xx5` boundary remains
  unconfirmed. A pure quantity-only change is now directly confirmed to use the identical
  before/after-cost mechanism as a price change (whole-configuration reproration, not delta-only),
  though still without an isolated numeric worked example.
- **`proration_behavior` is a 3-value enum** (`create_prorations` default / `always_invoice` /
  `none`), each with precise, distinct effects on whether and when an invoice is generated for the
  adjustment — not just whether proration is computed.
- **Dunning has no fixed schedule to transcribe** — Smart Retries is ML-scheduled, configurable only as
  "N tries within [1wk|2wk|3wk|1mo|2mo]" (Stripe's recommended default: 8 tries / 2 weeks), with three
  configurable end-of-schedule outcomes (cancel / mark unpaid / leave past_due). One spec3.json-verbatim
  fact is precise and important: `invoice.attempt_count` only increments on *automatic* retries, not
  manual ones, and keeps incrementing even when a non-retryable decline blocks actual network attempts.
  **All nine hard-decline codes that gate retries are now confirmed** directly from
  <https://docs.stripe.com/billing/revenue-recovery/smart-retries>: `incorrect_number`, `lost_card`,
  `pickup_card`, `stolen_card`, `revocation_of_authorization`, `revocation_of_all_authorizations`,
  `authentication_required`, `highest_risk_level`, and (the one the original pass was missing)
  `transaction_not_allowed`. See `dunning-and-retries.md`.
- **The `unpaid`-outcome invoice-status contradiction is confirmed real, not a search-snippet artifact**
  — and the practical recommendation now leans firmly toward `draft`. Two independently-read
  `docs.stripe.com` pages (`smart-retries` and `subscriptions/overview`) both say cycle invoices
  generated while a subscription is `unpaid` "stay in a draft state" / "remain in `draft` status", against
  `spec3.json`'s looser prose ("immediately automatically closed" — note "closed" isn't even a real
  `invoice.status` enum value). See `dunning-and-retries.md` and `gap-closure-2026-09-18.md` item 5.
- **Refunds cannot touch a charge with an open dispute** — confirmed both in prose ("You can't issue a
  refund on a disputed charge until your customer's card issuer decides in your favor") and in the spec
  itself via `dispute.is_charge_refundable`. Over-refunding is a hard server-side-enforced ceiling, not a
  clamped amount.
- **Dispute lifecycle has two parallel tracks**: full chargebacks (`needs_response` →
  `under_review` → `won`/`lost`) and softer inquiries (`warning_needs_response` → `warning_under_review`
  → `warning_closed`, the last being a 120-day timeout, not a merchant win/loss). At most two
  `balance_transaction` rows per dispute (withdrawal + optional reinstatement); **the
  `balance_transaction.type` for both halves is now directly confirmed as `adjustment`** (not just
  inferred from the enum's lack of a dispute-specific value) — see
  <https://docs.stripe.com/reports/balance-transaction-types> and `gap-closure-2026-09-18.md` item 8.
  **`dispute.status=prevented`** is now confirmed to mean "a dispute that was prevented from becoming a
  formal chargeback" (Stripe's own API reference definition), tied to Verifi/Ethoca/CE3.0
  dispute-prevention products; whether every prevention path creates a `Dispute` object remains a minor
  open question (see `refunds-and-disputes.md`). **Dispute fee refundability on a win is now confirmed
  nuanced**: the base "dispute received" fee is never refunded (outside Mexico), but a separate "dispute
  countered" fee (charged only if the merchant contests) is refunded on a win — see
  `gap-closure-2026-09-18.md` item 9.
- **The `balance_transaction` ledger's fee math is exact and spec-stated**: `net = amount - fee`, no
  ambiguity. `available`/`pending` and `available_on` are real fields but the *settlement delay itself*
  (commonly T+2, varies by country) is a product/account policy, not a spec constant — recommend the
  Seahaven world treat it as a configurable constant.
- **Payouts draw down `available` (standard) or `instant_available` (instant) specifically**, produce a
  `balance_transaction` of `type=payout`, and a failed/canceled payout produces a **second**, reversing
  transaction rather than mutating the first.
- **Credit notes have three independent settlement channels** — `refund_amount` (real refund + real
  `balance_transaction`), `credit_amount` (customer-balance credit, a *different* ledger entirely, no
  `balance_transaction`), `out_of_band_amount` (pure bookkeeping, no money movement at all) — and share a
  single ceiling with the Refunds API against the invoice/charge total.
- **`subscription_schedules`: recommend conditional, scoped-down inclusion**, not full parity. Its
  `phases` array duplicates most of the subscription/price configuration surface, which is expensive
  relative to the tool/table budget; but it's the *only* way to represent "declare a future change now,
  have it apply automatically later" scenarios, which a plain `subscriptions.update` cannot express at
  all. Full reasoning and a concrete scoped-field recommendation in
  `credit-notes-and-subscription-schedules.md`.

## Details

- [gap-closure-2026-09-18.md](./gap-closure-2026-09-18.md) — **read this first for anything touching
  proration rounding, retry decline codes, the unpaid-invoice contradiction, unpaid recovery, dispute
  `prevented`/fee/ledger-type semantics, or subscription_cycle invoice timing.** Nine itemized gaps from
  the original pass, each closed/refined/confirmed via a direct `docs.stripe.com` read (Tavily), with
  verbatim quotes and URLs.
- [subscription-status-machine.md](./subscription-status-machine.md) — full 8-state machine, every
  transition/trigger, `payment_behavior` (4 values), `cancel_at_period_end` vs. immediate cancel, the
  two distinct "pause" mechanisms (`status=paused` vs `pause_collection`), and the (now closed)
  `unpaid`-recovery question.
- [invoice-status-machine.md](./invoice-status-machine.md) — 5-state machine, the invoice-schema-drift
  finding (no flat `subscription`/`days_until_due`/`paid`), the draft→open mechanism and its 1-hour
  window, the full 9-value `billing_reason` enum with definitions, and how subscription cycles create
  invoices (lead-time question now closed: zero lead time, created at the boundary).
- [proration-arithmetic.md](./proration-arithmetic.md) — the formula, the worked $10→$20 example,
  second-level precision, the now-resolved rounding rule (independent-per-line, nearest-cent),
  `proration_behavior`'s 3 values, `proration_date`, `billing_cycle_anchor` interaction, and
  immediate-cancel proration.
- [dunning-and-retries.md](./dunning-and-retries.md) — Smart Retries configuration envelope, hard
  decline-code gating (now all 9 of 9 codes confirmed), `attempt_count` semantics (spec-verbatim), the
  three end-of-schedule outcomes and the (now-confirmed-real) contradiction between two Stripe docs
  sources on what happens to invoices in the `unpaid` outcome (draft vs. effectively-closed) —
  recommendation now leans `draft`.
- [refunds-and-disputes.md](./refunds-and-disputes.md) — refund status/reason/failure_reason enums
  (spec-verbatim), over-refund rejection, refund-blocked-during-dispute rule, the full dispute status
  machine (chargebacks vs. inquiries, including the now-defined `prevented` status), the
  `subscription_canceled` dispute reason, the `balance_transactions` (≤2 per dispute) ledger-effect field
  with its `type` now confirmed as `adjustment`, and dispute fee refundability (now confirmed nuanced).
- [balance-ledger-and-payouts.md](./balance-ledger-and-payouts.md) — full 51-value `balance_transaction.type`
  enum, the exact `net = amount - fee` formula, `fee_details` sub-breakdown, `available`/`pending`/
  `available_on` semantics, the `balance` aggregate object, and payout draw-down mechanics including the
  standard-vs-instant balance split and the failure-reversal transaction pattern.
- [credit-notes-and-subscription-schedules.md](./credit-notes-and-subscription-schedules.md) — credit
  note status/type/reason enums, the three settlement channels and their reconciliation invariant, the
  shared ceiling with the Refunds API, the full subscription_schedule status machine and phase-object
  field list, and the explicit cost/benefit verdict on including `subscription_schedules`.

## Open Questions / Gaps

**Update 2026-09-18**: nine items from this list (the `unpaid`→`active` recovery question, the
`subscription_cycle` lead time, the proration rounding rule's core independent-vs-net question, the
ninth hard-decline code, the `unpaid`-outcome draft-vs-closed contradiction, `dispute.status=prevented`'s
definition, the dispute `balance_transaction.type`, and dispute fee refundability) were closed or
substantially refined by a follow-up pass using a newly-connected Tavily MCP server that reaches
`docs.stripe.com` directly. Full detail in **[gap-closure-2026-09-18.md](./gap-closure-2026-09-18.md)**.
They have been removed from the list below; what remains genuinely open:

- Exact tie-break rounding convention for proration line-item amounts at an exact `x.xx5` cent boundary
  (round-half-up is a low-risk assumption; the general nearest-cent, independent-per-line rule itself is
  now confirmed — see `gap-closure-2026-09-18.md` item 1). Also unconfirmed: whether the
  independently-rounded-then-summed behavior holds identically under `billing_mode=flexible` (directly
  confirmed only for `billing_mode=classic`).
- Exact behavior of a pure quantity-only change lacks an isolated numeric worked example (the underlying
  mechanism — whole-configuration reproration, not delta-only — is now directly confirmed to be the same
  as for price changes; see `gap-closure-2026-09-18.md` item 3).
- Exact `proration_behavior`/param semantics on the subscription **delete** (immediate cancel) endpoint
  specifically — not targeted by either pass; low risk, confirm against `spec3.json` paths directly if
  it matters for the schema subtopic.
- Whether an RDR/Ethoca-resolved dispute (as opposed to a fully CE3.0-blocked one) creates a `Dispute`
  API object with `status=prevented` and a corresponding `balance_transaction`, or no object at all —
  `gap-closure-2026-09-18.md` item 7 narrowed this considerably but didn't fully close it. Recommend
  modeling a `prevented` dispute with `balance_transactions=[]` as the safe default.
- No confirmation of whether the dunning retry schedule (count/window) is API-inspectable/settable per
  subscription, or purely a Dashboard-level account setting.
- Real-world settlement delay (`available_on` offset) varies by country; no single spec constant exists.
- `credit_note.type=mixed` and `subscription_schedule.end_behavior` values `none`/`renew` — present in
  closed enums but undocumented in the property text found this pass.
- Whether Stripe's own MCP server/agent-toolkit exposes subscription-schedule tools (cross-subtopic
  dependency with subtopic 5, would strengthen the inclusion case if true).

None of these remaining gaps block writing a functional spec — each has either a stated best inference or
an explicit "treat as configurable/undecided until a trace confirms it" recommendation. The proration
rounding rule — previously the single gap most worth escalating, since the research plan called it out as
"the single thing most likely to be got wrong downstream" — is now settled for its load-bearing question
(independent-per-line, nearest-cent, not net-then-round); only a narrow tie-break-convention detail
remains genuinely undocumented.

## Sources

All per-claim sources are cited inline in the detail files, including the new
`gap-closure-2026-09-18.md`. At the aggregate level:

- `research/stripe-openapi/spec3.json` (API version `2026-08-26.dahlia`, fetched 2026-09-18) — used
  directly via short Python scripts for every enum and every field `description` quoted as
  "spec3.json, verbatim" throughout. This was the single strongest and most-used source in this
  subtopic's original pass, more so than initially expected, because Stripe embeds substantial docs
  prose directly into the OpenAPI spec's `description` fields.
- **`tavily_extract` (Tavily MCP), 2026-09-18** — used in the gap-closure pass to directly fetch and read
  `docs.stripe.com` pages in full, now that this domain is reachable through this tool (it was blocked
  for the entire original pass). This is the strongest prose-doc source tier available to this lane —
  full verbatim page content, not a search snippet. Pages read directly: `billing/subscriptions/prorations`,
  `billing/subscriptions/change-price`, `billing/subscriptions/quantities`,
  `billing/scripts/prorations`, `billing/scripts/stripe-authored/proration`,
  `billing/revenue-recovery/smart-retries`, `billing/invoices/subscription`,
  `billing/subscriptions/overview`, `disputes/how-disputes-work`, `disputes/get-started/prevention`,
  `disputes`, `api/disputes/object`, `disputes/responding`, `reports/balance-transaction-types`. See
  `gap-closure-2026-09-18.md` for exact quotes attributed to each.
- `WebSearch` tool results, 2026-09-18 (original pass) — used for everything not present in the spec and
  not re-verified in the gap-closure pass (e.g. general dunning/ML-model commentary, some
  payout/settlement-timing narrative). Direct fetch of `docs.stripe.com`/`stripe.com` was blocked for the
  entire original-pass session (organization egress policy) — confirmed via `WebFetch` errors and direct
  `curl` CONNECT-403s against multiple paths and mirrors, documented at the top of every detail file.
- No repository source code (`stripe-python`, `stripe-mock`) was needed for this subtopic beyond
  confirming it existed on disk; the money-behavior questions here are prose/spec questions, not
  SDK-shape questions (that's subtopic 1's territory).
