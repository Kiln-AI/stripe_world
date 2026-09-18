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

## Bottom Line

The subscription and invoice status machines, the `balance_transaction` ledger, and refund/credit-note
mechanics are documented precisely enough to implement from directly — much of that precision comes
straight out of `spec3.json`'s own field descriptions, which are stronger sources than any blog post.
Proration arithmetic has a confirmed formula (`credit = -fraction × old_price`, `debit = +fraction ×
new_price`, fraction computed to the second) and a confirmed worked example ($10→$20 mid-period nets
+$5), but the exact **rounding rule** for the final line-item amount is not directly documented — only
inferable by analogy to Stripe's separately-documented fee-rounding rule (round-half-up to the nearest
cent) — and should be confirmed with a recorded API trace before being hard-coded. Smart Retries/dunning
is deliberately non-deterministic (ML-scheduled, not a fixed day table) with only the configuration
envelope documented (8 tries / 2 weeks default, three end-of-schedule outcomes); do not build a fixed
retry-day table into the mock, model it as "N attempts within a window, outcome per account setting."
The invoice object's schema has drifted substantially from what most existing tutorials describe
(`invoice.subscription` and `invoice.days_until_due` no longer exist as flat fields at API version
`2026-08-26.dahlia`) — this is a load-bearing finding for the schema subtopic, not just this one.
`subscription_schedules` is recommended as **conditional, scoped-down inclusion**: real value for
"declare a future change" scenarios, but its `phases` array duplicates most of the subscription schema
and is not worth full-fidelity modeling against the project's 40-60-tool budget.

## Key Findings

- **Subscription status machine is fully specified in `spec3.json` itself, verbatim.** The `status`
  enum's own description field is the canonical state-machine doc (8 states, every named transition and
  trigger) — see `subscription-status-machine.md`. One genuine ambiguity remains: whether an `unpaid`
  subscription auto-recovers to `active` once its invoices are paid, or requires an explicit update.
- **The invoice schema has moved on from the "classic" shape.** At API version `2026-08-26.dahlia` there
  is no top-level `invoice.subscription` (now `invoice.parent.subscription_details.subscription`) and no
  top-level `invoice.days_until_due` (create-only param; the computed value is `invoice.due_date`). The
  ~1-hour draft-finalization window is now an explicit, queryable field:
  `invoice.automatically_finalizes_at`. See `invoice-status-machine.md`.
- **Proration formula and one fully-worked example are confirmed**: `proration_factor = seconds
  remaining in period / total period seconds`; credit = `-factor × old_price`; debit =
  `+factor × new_price`; both computed to the second. Worked example: $10→$20 exactly halfway through
  the period nets to +$5 on two separate line items (-$5 credit, +$10 debit). The **rounding rule for
  the final cent amount is not directly documented** — treat round-half-up as a plausible-but-unverified
  inference from Stripe's separately-documented fee-rounding rule. See `proration-arithmetic.md`.
- **`proration_behavior` is a 3-value enum** (`create_prorations` default / `always_invoice` /
  `none`), each with precise, distinct effects on whether and when an invoice is generated for the
  adjustment — not just whether proration is computed.
- **Dunning has no fixed schedule to transcribe** — Smart Retries is ML-scheduled, configurable only as
  "N tries within [1wk|2wk|3wk|1mo|2mo]" (Stripe's recommended default: 8 tries / 2 weeks), with three
  configurable end-of-schedule outcomes (cancel / mark unpaid / leave past_due). One spec3.json-verbatim
  fact is precise and important: `invoice.attempt_count` only increments on *automatic* retries, not
  manual ones, and keeps incrementing even when a non-retryable decline blocks actual network attempts.
  See `dunning-and-retries.md`.
- **Refunds cannot touch a charge with an open dispute** — confirmed both in prose ("You can't issue a
  refund on a disputed charge until your customer's card issuer decides in your favor") and in the spec
  itself via `dispute.is_charge_refundable`. Over-refunding is a hard server-side-enforced ceiling, not a
  clamped amount.
- **Dispute lifecycle has two parallel tracks**: full chargebacks (`needs_response` →
  `under_review` → `won`/`lost`) and softer inquiries (`warning_needs_response` → `warning_under_review`
  → `warning_closed`, the last being a 120-day timeout, not a merchant win/loss). At most two
  `balance_transaction` rows per dispute (withdrawal + optional reinstatement); the exact
  `balance_transaction.type` used for a dispute withdrawal was not confirmed (`adjustment` is the best
  inference from the 51-value type enum, which has no dispute-specific value).
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

- [subscription-status-machine.md](./subscription-status-machine.md) — full 8-state machine, every
  transition/trigger, `payment_behavior` (4 values), `cancel_at_period_end` vs. immediate cancel, the
  two distinct "pause" mechanisms (`status=paused` vs `pause_collection`), and the `unpaid`-recovery gap.
- [invoice-status-machine.md](./invoice-status-machine.md) — 5-state machine, the invoice-schema-drift
  finding (no flat `subscription`/`days_until_due`/`paid`), the draft→open mechanism and its 1-hour
  window, the full 9-value `billing_reason` enum with definitions, and how subscription cycles create
  invoices (with an open question on lead time before the period boundary).
- [proration-arithmetic.md](./proration-arithmetic.md) — the formula, the worked $10→$20 example,
  second-level precision, the unresolved rounding-rule question, `proration_behavior`'s 3 values,
  `proration_date`, `billing_cycle_anchor` interaction, and immediate-cancel proration.
- [dunning-and-retries.md](./dunning-and-retries.md) — Smart Retries configuration envelope, hard
  decline-code gating (8 of a claimed 9 codes recovered), `attempt_count` semantics (spec-verbatim), the
  three end-of-schedule outcomes and a flagged contradiction between two sources on what happens to
  invoices in the `unpaid` outcome (draft vs. effectively-closed).
- [refunds-and-disputes.md](./refunds-and-disputes.md) — refund status/reason/failure_reason enums
  (spec-verbatim), over-refund rejection, refund-blocked-during-dispute rule, the full dispute status
  machine (chargebacks vs. inquiries), the `subscription_canceled` dispute reason, and the
  `balance_transactions` (≤2 per dispute) ledger-effect field.
- [balance-ledger-and-payouts.md](./balance-ledger-and-payouts.md) — full 51-value `balance_transaction.type`
  enum, the exact `net = amount - fee` formula, `fee_details` sub-breakdown, `available`/`pending`/
  `available_on` semantics, the `balance` aggregate object, and payout draw-down mechanics including the
  standard-vs-instant balance split and the failure-reversal transaction pattern.
- [credit-notes-and-subscription-schedules.md](./credit-notes-and-subscription-schedules.md) — credit
  note status/type/reason enums, the three settlement channels and their reconciliation invariant, the
  shared ceiling with the Refunds API, the full subscription_schedule status machine and phase-object
  field list, and the explicit cost/benefit verdict on including `subscription_schedules`.

## Open Questions / Gaps

Aggregated from the per-file gap lists (see each file's "Gaps"/"Open Questions" section for full detail
and recommended settling method — mostly "read the blocked docs page directly once egress allows it" or
"a recorded API trace against test mode"):

- Does `subscription.status=unpaid` recover to `active` automatically once invoices are paid, or does it
  require an explicit call?
- Exact lead time before a subscription's period boundary at which the `subscription_cycle` invoice is
  created.
- Exact rounding rule for proration line-item cent amounts (round-half-up inferred, not confirmed), and
  whether credit/debit lines round independently or net-then-round.
- Exact behavior of a pure quantity-only change (whole-item reproration vs. delta-only) — inferred by
  analogy, not directly documented.
- The ninth hard-decline retry-gating code (only 8 of 9 recovered).
- Contradiction between two sources on `unpaid`-outcome invoice status (`draft` vs. spec's "immediately
  automatically closed" wording).
- `dispute.status=prevented` — trigger and fund-movement semantics not corroborated at all this pass.
- Exact `balance_transaction.type` used for dispute withdrawal/reinstatement (inferred as `adjustment`).
- Dispute fee amount/refundability on a win — not verified this session.
- Real-world settlement delay (`available_on` offset) varies by country; no single spec constant exists.
- `credit_note.type=mixed` and `subscription_schedule.end_behavior` values `none`/`renew` — present in
  closed enums but undocumented in the property text found this pass.
- Whether Stripe's own MCP server/agent-toolkit exposes subscription-schedule tools (cross-subtopic
  dependency with subtopic 5, would strengthen the inclusion case if true).

None of these gaps block writing a functional spec — each has either a stated best inference or an
explicit "treat as configurable/undecided until a trace confirms it" recommendation. The one gap worth
escalating to the manager/synthesis step specifically is the **proration rounding rule**, since the
research plan calls it out as "the single thing most likely to be got wrong downstream" and it genuinely
could not be pinned to a directly-quoted source in this session.

## Sources

All per-claim sources are cited inline in the six detail files. At the aggregate level:

- `research/stripe-openapi/spec3.json` (API version `2026-08-26.dahlia`, fetched 2026-09-18) — used
  directly via short Python scripts for every enum and every field `description` quoted as
  "spec3.json, verbatim" throughout. This was the single strongest and most-used source in this
  subtopic, more so than initially expected, because Stripe embeds substantial docs prose directly into
  the OpenAPI spec's `description` fields.
- `WebSearch` tool results, 2026-09-18 — used for everything not present in the spec (proration worked
  examples, dunning configuration, dispute-lifecycle narrative, payout/settlement timing, credit-note
  and subscription-schedule usage guidance). Direct fetch of `docs.stripe.com`/`stripe.com` was blocked
  for the entire session (organization egress policy) — confirmed via `WebFetch` errors and direct
  `curl` CONNECT-403s against multiple paths and mirrors, documented at the top of every detail file.
- No repository source code (`stripe-python`, `stripe-mock`) was needed for this subtopic beyond
  confirming it existed on disk; the money-behavior questions here are prose/spec questions, not
  SDK-shape questions (that's subtopic 1's territory).
