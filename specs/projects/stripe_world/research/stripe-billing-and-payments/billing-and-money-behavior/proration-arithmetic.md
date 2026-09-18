# Proration Arithmetic

This is the highest-value file in the subtopic — get this wrong and every mid-cycle upgrade/downgrade
fixture will be off. See `subscription-status-machine.md` for the access-method caveat (docs.stripe.com
blocked for direct fetch this session; findings below come from WebSearch synthesis of docs pages,
cross-checked against `spec3.json` where the spec carries relevant fields/param descriptions).

## The formula

Stripe computes a **proration factor** per subscription item whose price, quantity, or billing period
changes mid-cycle:

```
proration_factor = time_remaining_in_period / total_period_duration
```

both measured in **seconds** (see "Second-level precision" below), where `time_remaining_in_period` is
measured from the effective change moment (`proration_date`, defaulting to "now") to the end of the
current billing period, and `total_period_duration` is the full length of that billing period.

Two line items are generated per changed item:

```
credit_line_item = -1 × proration_factor × old_price_amount   (unused time on the OLD price)
debit_line_item  = +1 × proration_factor × new_price_amount   (remaining time on the NEW price)
```

Net proration adjustment on the invoice = `credit_line_item + debit_line_item` (algebraically; the
credit is negative). This nets to a **charge** if the new price is higher (upgrade) and a **net credit**
if the new price is lower (downgrade), assuming a mid-period change with no quantity change.

### Worked example (from Stripe's own docs, quoted via WebSearch synthesis)

Customer on a **$10/month** plan switches to a **$20/month** plan exactly **halfway through** the
current billing period (`proration_factor = 0.5`):

```
Unused time on original 10 USD plan (credit): -5 USD    (= -0.5 × 10)
Remaining time on new 20 USD plan (debit):    +10 USD    (= +0.5 × 20)
-----------------------------------------------------------------------
Net proration adjustment:                      +5 USD
```

This is presented on the invoice as two separate line items (both tagged as prorations — see
`invoice-status-machine.md` and `proration` field below), not pre-netted into one line, "for accounting
purposes, as it reopens a period you have already billed and restates it" (WebSearch synthesis,
paraphrasing docs commentary on why Stripe keeps credit/debit separate rather than emitting one net
line).

Source for this exact example: WebSearch synthesis of
<https://docs.stripe.com/billing/subscriptions/prorations> and
<https://docs.stripe.com/billing/subscriptions/change-price>, accessed 2026-09-18. I could not
independently re-fetch the page to confirm the numbers verbatim character-for-character (egress
blocked), but the same $10/$20/halfway example was returned consistently across two independently
phrased search queries, which is a meaningful corroboration signal for a search-synthesized answer.

### Quantity changes

"Stripe also prorates when you make quantity changes" (via search) — same mechanics apply: the old
`quantity × old effective price` for the unused portion is credited, the new `quantity × price` for the
remaining portion is debited. I did not find a source spelling out whether a *quantity-only* change
(same price ID, `quantity: 2 → 3`) produces one net line item covering just the added unit, or the same
full credit/debit pair covering the entire item's total amount. **Gap** — likely the latter (Stripe
reprorates the whole item, not a delta), by analogy with the price-change case and the general
"credit old configuration for the remainder, debit new configuration for the remainder" model, but this
is inference, not a confirmed doc statement. Settle with a recorded API trace (`subscriptions.update`
changing only `items[0].quantity` mid-cycle, inspect the resulting invoice's line items).

## Second-level precision

"By default Stripe calculates all of this 'down to the second'" — confirmed independently by two
separate WebSearch queries quoting <https://docs.stripe.com/billing/subscriptions/prorations>:
"Stripe prorates to the second. This means prorated amounts can change between the time they're
previewed and the time the update is made." This means `time_remaining_in_period` and
`total_period_duration` in the formula above are both **second counts** (unix-timestamp differences),
not day counts — a naive day-granularity implementation will not match Stripe's numbers except by
coincidence at exact day boundaries.

### Customizing granularity

Stripe publishes an official customization ("Customize subscription proration calculations",
<https://docs.stripe.com/billing/scripts/prorations>, via search) that lets an integration round the
start/end timestamps used in the calculation to a coarser interval — `hour`, `day`, `week`, or `month`
— before computing the fraction, for accounts that want less granular (more human-legible) proration
line items. This is opt-in scripting on top of the default second-level behavior, not a different
built-in mode. `proration_date` (see below) is the general-purpose hook this customization is built on.

## Rounding rule — NOT FULLY SETTLED, flagged explicitly

I could **not** find an explicit, docs-stated rounding rule for the final proration line-item amount
(i.e., after multiplying the fractional-second proration factor by the price, which sub-cent value is
produced, and how is it rounded to the integer minor-unit amount Stripe actually stores/bills?).

What I *did* find, which is adjacent but not the same thing: Stripe's documented rounding rule for **fee
amounts** (not proration): "Stripe rounds the Stripe fee to the nearest unit (e.g., cents). For example,
if the fee is 0.025, Stripe will round up to 0.03. If the fee is 0.024, Stripe will round down to 0.02."
(via search, <https://support.stripe.com/questions/rounding-rules-for-stripe-fees>). That is
round-half-up to the nearest minor unit. It is plausible (and would be consistent with Stripe's general
practice of storing all amounts as integer minor units) that the **same round-half-up-to-nearest-cent
rule applies to proration line items**, since every `amount` field on every line item is typed as
integer cents in the API (confirmed: `spec3.json` `line_item.amount` is `type: integer`) — a fractional
cent cannot be represented on the object at all, so *some* rounding must occur, and round-half-up is
Stripe's only documented rounding convention found in this research pass. **Treat this as a plausible
inference, not a confirmed fact** — recommend verifying with a recorded API call before hard-coding
round-half-up into the Seahaven proration engine: pick an old/new price and a `proration_date` that
forces an exact `x.xx5` cent boundary and compare the actual returned line-item amount.

Also unconfirmed: whether the credit and debit line items are rounded **independently** (each to its own
nearest cent, meaning the net could be off by up to 1 cent from the "ideal" net value) or whether Stripe
computes the net in higher precision and rounds once. The example above ($10→$20 at exactly 0.5) doesn't
expose this because 0.5 × any whole-dollar amount lands on an exact cent already. **Open question**,
same recommended settling method (a boundary-case recorded trace).

## `proration_behavior` — full enum (spec3.json, verbatim)

`POST/POST /v1/subscriptions{,/{id}}` param `proration_behavior`, enum `[always_invoice,
create_prorations, none]`, default `create_prorations`. Top-level description (spec3.json, verbatim):
"Determines how to handle prorations when the billing cycle changes (e.g., when switching plans,
resetting `billing_cycle_anchor=now`, or starting a trial), or if an item's `quantity` changes."

Per-value behavior (via search, cross-referenced against the above):

- **`create_prorations`** (default) — "the prorations are created but not automatically invoiced. If
  you want to bill the customer for the prorations before the subscription's renewal date, you need to
  manually invoice the customer." I.e. the credit/debit lines are added as pending invoice items and
  will ride along on the *next* invoice Stripe generates (whether that's the next renewal cycle's
  invoice, or an earlier manual/threshold invoice) — they do not themselves trigger an invoice.
- **`always_invoice`** — "create prorations, automatically invoice the customer for those proration
  adjustments, and attempt to collect payment" immediately. This is the mode that produces an invoice
  with `billing_reason=subscription_update` right away (see `invoice-status-machine.md`), rather than
  waiting for the next cycle.
- **`none`** — no prorations at all. Documented example (via search): with `none`, changing a $100/mo
  plan to $200/mo mid-cycle results in "the customer is billed 100 USD on May 1 and 200 USD on June 1"
  — i.e. the old price is simply charged in full for the period already invoiced, and the new price
  takes over cleanly at the *next* period boundary with no true-up for the switch itself.

The three named triggers for proration behavior to matter, per the description field: (1) switching
plans/prices, (2) resetting `billing_cycle_anchor=now`, (3) starting a trial [presumably trial→paid
transitions where a partial period is involved], and separately (4) item `quantity` changes.

## `proration_date`

`spec3.json`, verbatim: "If set, prorations will be calculated as though the subscription was updated
at the given time. This can be used to apply exactly the same prorations that were previewed with the
create preview endpoint (`POST /v1/invoices/create_preview` — the successor to the older "upcoming
invoice preview" endpoint). `proration_date` can also be used to implement custom proration logic, such
as prorating by day instead of by second, by providing the time that you wish to use for proration
calculations." Defaults to the actual time of the update call if unset. This is the field a faithful
mock world needs to honor exactly — it is the deterministic input to the whole formula above, and any
conformance test that wants reproducible proration numbers should pin it explicitly.

## `billing_cycle_anchor` interaction

On `subscriptions.update`, `billing_cycle_anchor` only accepts the literal values `now` or `unchanged`
(spec3.json, verbatim) — **not** an arbitrary timestamp on update (that free-timestamp form is
create-only). Setting `now` resets the anchor to the current moment, which restarts the periodic cycle
from now and is one of the three named `proration_behavior` triggers above — i.e. it forces a
proration computation for the truncated "old" period that just ended early.

## Upgrades vs. downgrades — no behavioral asymmetry in the formula itself

The formula above is symmetric: an upgrade produces a net debit (customer owes more), a downgrade
produces a net credit (customer is owed money back, applied as account/customer balance credit toward
future invoices — not a cash refund; see `credit-notes-and-subscription-schedules.md` and
`balance-ledger-and-payouts.md` for how customer-balance credits differ from `balance_transaction`
refund entries). Whether the *invoice* it lands on is emitted immediately or deferred is entirely a
function of `proration_behavior`, not of upgrade-vs-downgrade direction.

## Cancellation and its proration

Canceling a subscription immediately (`DELETE /v1/subscriptions/{id}`) mid-period is, structurally, a
"switch to nothing" and is governed by the same `proration_behavior` machinery on that endpoint (the
delete endpoint also accepts `prorate`/`proration_behavior`-style params per typical Stripe API shape —
I did not independently re-verify the exact param name on the delete endpoint in `spec3.json` in this
pass; **gap**, low risk, confirm against `spec3.json` `paths./v1/subscriptions/{subscription_exposed_id}.delete`
directly if it matters for the schema subtopic). `cancel_at_period_end=true`, by contrast, produces
**no** proration at all — the subscription simply is not renewed; the customer keeps what they already
paid for through the period they're in.

## Sources

- `research/stripe-openapi/spec3.json`: `paths./v1/subscriptions/{subscription_exposed_id}.post`
  request schema properties `proration_behavior`, `proration_date`, `billing_cycle_anchor`; and
  `components.schemas.line_item.properties.amount` (integer-cents confirmation) — API version
  `2026-08-26.dahlia`, fetched 2026-09-18.
- WebSearch synthesis quoting <https://docs.stripe.com/billing/subscriptions/prorations> (the primary
  page for this whole file — direct fetch blocked, accessed via search snippets only, 2026-09-18),
  <https://docs.stripe.com/billing/subscriptions/change-price>,
  <https://docs.stripe.com/billing/scripts/prorations>,
  <https://docs.stripe.com/billing/scripts/stripe-authored/proration>, and
  <https://support.stripe.com/questions/rounding-rules-for-stripe-fees> (fee rounding, used only as an
  adjacent data point for the proration rounding inference, explicitly flagged as such above).

## Open questions requiring a recorded API trace to settle

1. Exact rounding rule for proration line-item amounts (round-half-up inferred from fee rounding, not
   confirmed for prorations specifically).
2. Whether credit/debit lines round independently or net-then-round.
3. Exact behavior of a pure quantity change (whole-item reproration vs. delta-only).
4. Exact `proration_behavior`/param name and semantics on the subscription **delete** (immediate cancel)
   endpoint specifically.
