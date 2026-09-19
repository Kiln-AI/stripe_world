# Dunning / Smart Retries

See `subscription-status-machine.md` for the access-method caveat. This area was the least precisely
documented of everything researched here — Stripe's public docs describe the *configuration knobs* and
*end-of-schedule outcomes* precisely, but the actual day-by-day retry schedule is explicitly
**non-deterministic** (ML-driven), so there is no fixed table to transcribe.

## Two collection paths, two different dunning mechanics

Recall from `subscription-status-machine.md`: `past_due` is reached differently depending on
`collection_method`.

- **`charge_automatically`**: `past_due` when a scheduled automatic charge fails or needs additional
  customer action (e.g. 3DS). Retries are Smart Retries (below).
- **`send_invoice`**: `past_due` when the emailed invoice's `due_date` passes unpaid. "Retry" in this
  path is really just "time until an additional deadline passes" — there's no card-charge retry loop
  because Stripe isn't attempting a charge at all, the customer is expected to pay manually.

## Smart Retries — how they work (via search, not spec3.json — this is pure product-behavior docs)

Smart Retries is Stripe Billing's built-in dunning feature: "automatically re-attempts a failed
subscription charge on a day its machine learning model predicts is more likely to succeed, rather than
retrying blindly on a fixed daily schedule." Signals the model reportedly incorporates: card brand,
issuing bank, the original decline code, the customer's historical payment behavior, and
time-of-day/network-wide patterns. (Source: WebSearch synthesis of
<https://docs.stripe.com/billing/revenue-recovery/smart-retries>, plus secondary commentary sites which
described "billions of data points" / "500+ attributes" / an Auto-ML ensemble replacing an older
XGBoost model "in 2023" — **this last claim is from a third-party blog, not Stripe's own docs, and is
unverified; treat the ML-internals detail as color, not a spec to implement against.**)

### Configuration surface

- **Retry count/window**: configurable as "N tries within a time period", where the time period is one
  of **1 week, 2 weeks, 3 weeks, 1 month, or 2 months** (via search). **Stripe's recommended default is
  8 tries within 2 weeks** (stated consistently across two independent search results — "8 tries within
  2 weeks" / "8 retries within 14 days" — treat as corroborated).
- This is a **Dashboard/account-level setting** (not a per-subscription API param as far as the sources
  found describe it — I did not find an API field like `subscription.retry_schedule` in the enumerated
  `subscription` properties list gathered during this pass; retries appear to be governed by account
  billing settings rather than being an inspectable/settable field on the subscription object itself).
  **Gap**: could not confirm this from `spec3.json` directly (would need to check the account/billing
  settings API surface, which is arguably outside this subtopic's boundary — flagging for the API
  surface subtopic too).

### Decline-code gating — retries that don't actually retry

For a specific set of **hard decline codes**, Stripe will not send the retry to the card network at all,
even though the schedule keeps running and `invoice.attempt_count` keeps incrementing:

> "For nine specific decline codes, Stripe will not execute the retry at all until a new payment method
> is added. The schedule still runs and the attempt counter still climbs, but nothing reaches the
> issuer."

**Resolved 2026-09-18** — see `gap-closure-2026-09-18.md` item 4. A direct read of
<https://docs.stripe.com/billing/revenue-recovery/smart-retries> gives the complete, Stripe-primary-sourced
list of nine hard decline codes, verbatim:

```
incorrect_number, lost_card, pickup_card, stolen_card, revocation_of_authorization,
revocation_of_all_authorizations, authentication_required, highest_risk_level, transaction_not_allowed
```

The ninth code is **`transaction_not_allowed`**. The original 8 codes below (recovered via third-party
aggregator sites in the original pass) are confirmed correct and complete now that the 9th is known; the
whole list should be treated as directly docs-sourced rather than third-party-aggregated going forward.

<details><summary>Original text (superseded, kept for audit trail)</summary>

Named hard-decline codes (via search, aggregated from secondary commentary — **not cross-checked
against the `decline_code` enum in `spec3.json` in this pass; that full enum is subtopic 2's territory
("cross-cutting semantics" → error envelope) — treat this list as a good starting point, not
authoritative**):

```
incorrect_number, lost_card, pickup_card, stolen_card, revocation_of_authorization,
revocation_of_all_authorizations, authentication_required, highest_risk_level
```
(only 8 named in the source text despite it saying "nine specific decline codes" — **one is missing
from what I could retrieve; this list is incomplete**, flagged explicitly rather than guessing the
ninth.)

</details>

This distinction matters for a faithful mock: `invoice.attempt_count` incrementing does **not** imply an
actual network authorization attempt occurred when the payment method's last known decline code is one
of these hard-decline values.

### `invoice.attempt_count` semantics (spec3.json, verbatim — strong source)

> "Number of payment attempts made for this invoice, from the perspective of the payment retry
> schedule. Any payment attempt counts as the first attempt, and subsequently only automatic retries
> increment the attempt count. In other words, manual payment attempts after the first attempt do not
> affect the retry schedule. If a failure is returned with a non-retryable return code, the invoice can
> no longer be retried unless a new payment method is obtained. Retries will continue to be scheduled,
> and `attempt_count` will continue to increment, but retries will only be executed if a new payment
> method is obtained."

This is the single most implementation-precise statement found on dunning mechanics, and it's from the
spec itself, not a blog. Key implementable facts:

1. First attempt (manual or automatic) → `attempt_count = 1`.
2. Only *automatic* retries after that increment `attempt_count` further — a manual retry via API/
   Dashboard after the first attempt does not consume a slot in the schedule or bump the counter.
3. Non-retryable failure → the schedule keeps "running" (counter keeps climbing on its normal cadence)
   but no actual charge attempts happen, until a new payment method is attached.

### End of the retry schedule — three configurable outcomes (via search)

Once the configured retry window/count is exhausted, the account's dunning settings determine what
happens next, one of:

1. **Cancel the subscription** — status → `canceled`.
2. **Mark the subscription as unpaid** — status → `unpaid`. Per the `subscription.status`
   description (spec3.json, verbatim, quoted in full in `subscription-status-machine.md`): "when a
   subscription has a status of `unpaid`, no subsequent invoices will be attempted (invoices will be
   created, but then immediately automatically closed)." Also (via search, this file's retry-outcome
   description): "Invoices continue to be generated and stay in a draft state." — **these two
   statements are in mild tension** (spec says invoices are created and "immediately automatically
   closed"; the search-sourced retry-outcome text says they "stay in a draft state"). This is exactly
   the kind of docs inconsistency worth flagging rather than silently picking one: **open question** —
   does a `subscription.status=unpaid` cycle invoice end up `void`/`uncollectible` (spec3.json wording:
   "closed") or `draft` (search wording)? Settle with a test-clock trace: force a subscription through
   full dunning exhaustion with `unpaid` configured, advance past the next cycle boundary, and read the
   new invoice's `status` directly.

   **Update 2026-09-18** — see `gap-closure-2026-09-18.md` item 5. This contradiction is now confirmed
   to be **real and persisting inside Stripe's own docs corpus**, not an artifact of search-snippet
   imprecision: two separate `docs.stripe.com` pages, read directly, both say "draft" in nearly
   identical language — this same page's own source
   (<https://docs.stripe.com/billing/revenue-recovery/smart-retries>, table row verbatim: "Invoices
   continue to be generated and stay in a draft state") and
   <https://docs.stripe.com/billing/subscriptions/overview> ("The subscription continues to generate
   invoices each billing period, which remain in `draft` status."). Weight of evidence (2 independent
   direct reads) now favors **`draft`** as the correct implementation target — note also that "closed"
   was never an actual `invoice.status` enum literal (the real enum is `draft, open, paid,
   uncollectible, void`), so the spec's prose was always loose language rather than a literal status
   name. **Recommendation flips from "unresolved, pick either" to "implement as `draft`-persisting."**
3. **Leave the subscription `past_due`** — status stays `past_due` indefinitely. Per search: "Invoices
   continue to be generated and charge the customer based on retry settings" — i.e. this option keeps
   attempting future cycle invoices' own retry schedules too, it isn't a dead end.

This three-way choice is an **account-level (or possibly per-product/price, via Dashboard "subscription
settings") configuration**, not a per-API-call parameter as far as sources found in this pass show.

## Interaction with `past_due` → `active` recovery

Not explicitly re-confirmed in this pass beyond the general model: if a retry (automatic or manual)
succeeds while the subscription is `past_due`, the invoice moves to `paid` and the subscription returns
to `active`. This is implied by the overall status-machine description in
`subscription-status-machine.md` but I did not find a sentence stating it as explicitly as the other
transitions. Treat as **high-confidence inference**, not a directly-quoted fact.

## Sources

- `research/stripe-openapi/spec3.json`, `components.schemas.invoice.properties.attempt_count`
  description — API version `2026-08-26.dahlia`, fetched 2026-09-18. Strongest source in this file.
- WebSearch synthesis of <https://docs.stripe.com/billing/revenue-recovery/smart-retries> (primary page,
  direct fetch blocked this session), plus secondary commentary sites (reduxpayments.com, churnkey.co,
  churnward.com, nimbleconsults.com, recoverflow.org) cross-referenced for the retry-count/window and
  hard-decline-code claims — accessed 2026-09-18. These are third-party sites summarizing Stripe's
  behavior, not Stripe's own docs; treat with correspondingly lower confidence than spec3.json-sourced
  claims, and see inline flags above for the specific claims that are third-party-only (the ML model
  internals, the exact 8-decline-vs-9-decline-code count).

## Summary of gaps

**Update 2026-09-18**: items 1 and 2 closed/refined this pass — see `gap-closure-2026-09-18.md` items 4
and 5, and the inline "Resolved"/"Update" notes above.

1. ~~Ninth hard decline code not recovered~~ — **closed**: `transaction_not_allowed`, confirmed directly
   from <https://docs.stripe.com/billing/revenue-recovery/smart-retries>.
2. `unpaid`-outcome invoice status: `draft` (now 2 independent direct docs reads) vs. "closed"/void-like
   (spec3.json wording) — **contradiction confirmed as real and persisting**; weight of evidence now
   favors implementing as `draft`. Not "resolved" in the sense of the docs agreeing with each other, but
   resolved in the sense of having a confident implementation recommendation.
3. No confirmation of whether retry schedule is API-inspectable/settable per subscription, or purely a
   Dashboard-level account setting. **Still open** — not targeted by this pass.
4. ML-model internals (training data size, architecture) are third-party claims, not Stripe's own
   documentation, and are not load-bearing for implementation — included only as background color. **Still
   open / low priority**, not targeted by this pass.
