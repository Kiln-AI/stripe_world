# Gap Closure Pass — 2026-09-18

This pass exists because a Tavily MCP server now reaches `docs.stripe.com` directly (`tavily_extract`/
`tavily_crawl`/`tavily_search`), which the original research pass could not do (egress to
`docs.stripe.com`/`stripe.com` was blocked outright — see the "Access method note" at the top of every
other file in this lane). Every finding below comes from a **direct, verbatim page read** via
`tavily_extract`, not a search-snippet synthesis — this is a strictly stronger source tier than most of
what the original pass could cite, and is flagged accordingly throughout. Item 1 is the reason this pass
exists and is treated first and in the most depth.

---

## 1. Proration cent-rounding rule — THE PRIORITY ITEM

**Original claim** (`proration-arithmetic.md`): round-half-up was *inferred by analogy* to Stripe's
separately-documented fee-rounding rule, explicitly flagged as unverified. Whether credit/debit lines
round independently or net-then-round was an open question.

**Verdict: REFINED to a directly-evidenced answer, with one residual gap (exact tie-break rule).**

The default (`billing_mode=classic`) prorations page contains a fully worked example with fractional
cent inputs that exposes the rounding mechanics directly — something the $10→$20-at-exactly-halfway
example (which lands on whole cents) could never do. From <https://docs.stripe.com/billing/subscriptions/prorations>,
section "Calculation logic with no prorations" (verbatim, read 2026-09-18):

> "The `billing_mode=classic` proration calculation logic creates a credit proration based on the current
> price, even though the customer never paid the 20 BRL monthly rate. The latest invoice credits a third
> of the month for 20 BRL (-6.67 BRL), even though the customer never paid for the `price_20_monthly`
> price. It also debits a third of the month for 10 BRL (3.33 BRL)."

with the accompanying JSON response:

```json
{ "id": "sub_123", "latest_invoice": { "id": "in_456", "total": -334, "currency": "usd" } }
```

**Do the math.** A third of a month on the 20 BRL price is 20/3 = 666.66̄ cents; the docs round that to
**-667** (i.e. -6.67). A third of a month on the 10 BRL price is 10/3 = 333.33̄ cents; the docs round
that to **+333** (i.e. +3.33). Sum of the two *independently-rounded* lines: -667 + 333 = **-334**,
exactly matching the invoice's `total`. If Stripe instead computed the **net first and rounded once**,
the net would be -10/3 = -333.33̄ cents, which rounds to **-333**, not -334. The actual `total` is -334.

**This directly answers the load-bearing sub-question**: credit and debit proration line items are
**rounded independently, each to the nearest cent, and then summed** — Stripe does not compute a
higher-precision net and round once. A downstream implementation that nets-then-rounds will diverge from
Stripe's real numbers by exactly the kind of off-by-one-cent error this pass was commissioned to prevent.

**What remains genuinely unconfirmed**: the specific *tie-breaking* convention for an exact `x.xx5`
boundary (round-half-up vs. round-half-even vs. round-half-down). Both cent values in the worked example
above are unambiguous under any standard convention (666.667 rounds up, 333.333 rounds down, regardless
of tie-breaking rule), so this example cannot distinguish round-half-up from round-half-even. No
`docs.stripe.com` page found in this pass (including the prorations page in full, `change-price`,
`scripts/prorations`, and `scripts/stripe-authored/proration`) states a tie-break rule explicitly for
prorations. The previously-cited fee-rounding page
(<https://support.stripe.com/questions/rounding-rules-for-stripe-fees>) describes round-half-up for
**Stripe's own processing fee**, a different computation, not proration line items — that inference is
now demoted from "the best available evidence" to "a plausible but still-untested analogy," since we now
have *direct* proration evidence for the more important sub-question (independent-vs-net) and no direct
evidence at all for the tie-break sub-question. **Recommendation for the Seahaven proration engine:**
implement independent per-line rounding to the nearest cent (confirmed), and use standard round-half-up
as the tie-break convention (still an assumption, but now a narrower and lower-risk one — it only affects
the rare exact-`.005`-cent boundary, not the general case).

**One important scope caveat found in the same read**: this worked example is explicitly under
`billing_mode=classic`. The `flexible` billing mode variant of the *same* scenario nets to exactly 0 BRL
(3.33 credit cancels 3.33 debit), which happens to not expose independent-vs-net rounding at all. I did
not find a `flexible`-mode worked example with a non-canceling fractional-cent result in this pass, so
the "independently rounded, then summed" finding is **directly confirmed for `billing_mode=classic` only**
and merely assumed (not confirmed) to also hold under `billing_mode=flexible`. Given both modes share the
same fundamental "amounts are integer cents on every object" constraint (confirmed in the original pass
via `spec3.json`), this is a low-risk assumption, but flagging it since `billing_mode` is itself a
documented axis of behavioral difference on this exact page (see also item 3 below).

A related, separate rounding mechanism was also found on the same page and is worth recording so it is
not confused with the above: when a coupon's `amount_off` is split across multiple subscription items
proportionally, Stripe does **not** round each item's share independently — it uses a floor-the-earlier-
items/remainder-to-the-last-item allocation so the shares sum exactly to the coupon total (evidenced by
the `discount_amounts` in the "Calculation logic for coupons applied to multiple subscription items"
section: a $5 coupon split 1:2 across a $10 and $20 item nets `166`/`334`, not the independently-rounded
`167`/`333`). This is **discount-amount apportionment across items**, a different code path from the
credit/debit proration-line rounding above — noted here only so a future implementer doesn't conflate the
two rounding behaviors.

Source: <https://docs.stripe.com/billing/subscriptions/prorations> (`tavily_extract`, read 2026-09-18,
full page, 657 lines). Corroborating (no rounding-rule content of its own):
<https://docs.stripe.com/billing/scripts/prorations>, <https://docs.stripe.com/billing/scripts/stripe-authored/proration>.

---

## 2. `subscription_cycle` invoice creation lead time before the period boundary

**Original claim**: gap — could not determine if the cycle invoice is created exactly at the boundary or
some N hours before.

**Verdict: CONFIRMED — created at the boundary, zero lead time.**

From <https://docs.stripe.com/billing/invoices/subscription>, section "Subscription renewal invoices"
(verbatim, read 2026-09-18):

> "When subscriptions renew, Stripe: Creates an invoice. Leaves the invoice in a `draft` status for about
> an hour. Attempts to finalize and pay the invoice with the default payment method. Changes the invoice
> status to `paid` if payment succeeds."

and, further down the same page, section "Create an invoice":

> "Stripe automatically creates an invoice for subscriptions at the end of each billing cycle. We
> finalize and send the invoice in one hour."

There is no separate lead-time window before the boundary — invoice creation *is* the period-boundary
event itself, and the previously-documented ~1-hour draft window (see `invoice-status-machine.md`)
follows *after* creation, not before it. A faithful scheduler should create the `subscription_cycle`
invoice exactly at `current_period_end`, not some hours in advance of it.

Source: <https://docs.stripe.com/billing/invoices/subscription> (`tavily_extract`, read 2026-09-18).

---

## 3. Pure quantity-change reproration semantics

**Original claim**: inferred by analogy (whole-item reproration, not delta-only) — not directly
documented.

**Verdict: REFINED — stronger direct evidence for "whole configuration reprorated," still no isolated
numeric worked example for a quantity-only change.**

Two directly-read primary sources corroborate the "whole configuration, not delta" model without fully
proving it with a dedicated number-based example:

- <https://docs.stripe.com/billing/subscriptions/prorations>, "When prorations are applied" (verbatim):
  "The prorated amount is calculated as soon as the API updates the subscription. The current billing
  period's start and end times are used to calculate **the cost of the subscription before and after the
  change**." This is stated generically for anything that triggers a proration — price changes and
  quantity changes are both listed in the same "What triggers prorations" table on this page, with no
  separate formula carved out for quantity — implying quantity changes go through the identical
  before/after-cost machinery as price changes (i.e., `old_quantity × old_price` fully credited,
  `new_quantity × new_price` fully debited for the remaining period), not a delta-only calculation.
- <https://docs.stripe.com/api/subscriptions/update> (verbatim): "We also prorate when you make quantity
  changes," stated as a direct continuation of the same $100→$200 whole-price-swap worked example and
  its formula, with no distinct mechanic described for quantity.

**What's still missing**: neither page (nor `change-price`, `quantities`, or `scripts/prorations`, all
read in full this pass) gives a worked numeric example isolating a pure `quantity: 2 → 3` change with the
same rigor as the price-change example in item 1. So this remains an inference from the generalized
mechanism description rather than a directly-proven arithmetic case — upgraded from "analogy to a
different scenario" to "directly stated to use the same mechanism," which is meaningfully stronger, but
short of a confirmed worked example. Settling it fully would still benefit from a recorded API trace.

Source: <https://docs.stripe.com/billing/subscriptions/prorations>, <https://docs.stripe.com/api/subscriptions/update>
(`tavily_extract`, read 2026-09-18).

---

## 4. The ninth hard-decline code

**Original claim**: only 8 of a claimed 9 hard-decline codes recovered (`incorrect_number`, `lost_card`,
`pickup_card`, `stolen_card`, `revocation_of_authorization`, `revocation_of_all_authorizations`,
`authentication_required`, `highest_risk_level`), sourced from third-party aggregator sites.

**Verdict: CORRECTED / CLOSED — the ninth code is `transaction_not_allowed`, and the full list is now
Stripe-primary-sourced.**

From <https://docs.stripe.com/billing/revenue-recovery/smart-retries>, section "Hard decline codes"
(verbatim, read 2026-09-18):

> "Stripe can't automatically retry a payment if the card issuer returns any of these hard decline
> codes:
> - `incorrect_number`
> - `lost_card`
> - `pickup_card`
> - `stolen_card`
> - `revocation_of_authorization`
> - `revocation_of_all_authorizations`
> - `authentication_required`
> - `highest_risk_level`
> - `transaction_not_allowed`
>
> For these failures, the scheduled retries continue but the payment only executes if you obtain a new
> payment method."

This is a **correction of the source, not of the previously-listed 8 codes** — all 8 codes the original
pass recovered from third-party sites are confirmed correct and complete except for the missing ninth,
which is `transaction_not_allowed`. The whole list should now be treated as Stripe-primary-sourced
(direct page read) rather than third-party-aggregated.

Source: <https://docs.stripe.com/billing/revenue-recovery/smart-retries> (`tavily_extract`, read
2026-09-18).

---

## 5. The `unpaid`-invoice-status contradiction (`draft` vs. "immediately closed")

**Original claim**: `dunning-and-retries.md` flagged a tension between `spec3.json`'s
`subscription.status` field description ("invoices will be created, but then immediately automatically
closed") and a search-sourced claim that invoices "stay in a draft state."

**Verdict: The contradiction is CONFIRMED to persist — and now confirmed via two independent, directly-
read `docs.stripe.com` pages on the "draft" side, which meaningfully shifts the weight of evidence. This
is loud enough to flag explicitly, per the instructions for this pass.**

Two separate, directly-fetched docs pages state "draft," not "closed":

From <https://docs.stripe.com/billing/revenue-recovery/smart-retries>, the end-of-schedule outcomes table
(verbatim):

> "Mark the subscription as unpaid | The subscription changes to an `unpaid` state after the maximum
> number of days defined in the retry schedule. **Invoices continue to be generated and stay in a draft
> state.**"

From <https://docs.stripe.com/billing/subscriptions/overview>, section "Handle unpaid subscriptions"
(verbatim):

> "If the customer doesn't pay a subscription invoice, Stripe pauses further collection attempts. **The
> subscription continues to generate invoices each billing period, which remain in `draft` status.** The
> subscription's status (`past_due` or `unpaid`) depends on your failed payment settings in the Dashboard."

Against this, `spec3.json`'s own `subscription.status` property description (quoted in full in
`subscription-status-machine.md`) still says: "when a subscription has a status of `unpaid`, no
subsequent invoices will be attempted (invoices will be created, but then immediately automatically
closed)."

**This is a genuine, unresolved inconsistency inside Stripe's own documentation corpus** — not something
a research pass can paper over by picking a side. What changed in this pass: the "draft" side is no
longer a single search-snippet claim, it is now **two independently-read, full `docs.stripe.com` pages**
saying the same thing in nearly identical language, against **one** spec-embedded description whose
word "closed" is not itself a defined `invoice.status` enum value (the actual enum is `draft, open, paid,
uncollectible, void` — "closed" isn't a literal status at all, it's loose prose). Given that, **the
practical recommendation flips from "unresolved, pick either" to "implement as `draft`-persisting,"**
since (a) two docs pages agree, (b) "closed" was never a real enum value to implement against in the
first place, and (c) `draft` is a real, correctly-typed status that composes cleanly with the rest of the
invoice state machine documented in `invoice-status-machine.md`. This is exactly the kind of correction
item 1's instructions asked to be loud about — it changes what a mock invoice-generation engine should
literally emit for `unpaid`-subscription cycle invoices, from "auto-void/uncollectible" to "draft,
untouched."

Sources: <https://docs.stripe.com/billing/revenue-recovery/smart-retries>,
<https://docs.stripe.com/billing/subscriptions/overview> (`tavily_extract`, both read 2026-09-18).

---

## 6. The `unpaid` → `active` recovery path

**Original claim**: genuinely ambiguous in `spec3.json` prose alone — unclear whether paying the invoice
auto-flips `subscription.status`, or whether an explicit subscription update call is required.

**Verdict: CONFIRMED — auto-recovers; no explicit subscription update needed.**

From <https://docs.stripe.com/billing/subscriptions/overview>, the subscription-statuses table, `unpaid`
row (verbatim, read 2026-09-18):

> "`unpaid` | The latest invoice hasn't been paid but the subscription remains in place. The latest
> invoice remains open and invoices continue to generate, but payments aren't attempted. Revoke access to
> your product when the subscription is `unpaid` because payments were already attempted and retried
> while `past_due`. **To move the subscription to `active`, pay the most recent invoice before its due
> date.**"

And the `past_due` row on the same table, corroborating the same auto-recovery pattern one status earlier
in the machine:

> "`past_due` | ... To reactivate the subscription, have your customer pay the most recent invoice. **The
> subscription status becomes `active`** regardless of whether the payment is done before or after the
> latest invoice due date."

Neither sentence mentions any separate `subscriptions.update` call or explicit status-transition action —
"pay the most recent invoice" is stated as sufficient by itself, i.e. paying the invoice is what drives
the transition (standard Stripe pattern: invoice payment success is itself the trigger, not a side effect
requiring a second API call). This closes the gap flagged in `subscription-status-machine.md`.

Source: <https://docs.stripe.com/billing/subscriptions/overview> (`tavily_extract`, read 2026-09-18).

---

## 7. `dispute.status = prevented` semantics

**Original claim**: present in the `spec3.json` enum, completely uncorroborated by any prose in the
original pass.

**Verdict: CONFIRMED (definition); fund-movement mechanics refined but not fully pinned down.**

From <https://docs.stripe.com/api/disputes/object>, the `status` enum value table (verbatim, read
2026-09-18):

> "`prevented`  A dispute that was prevented from becoming a formal chargeback."

This is the authoritative, directly-fetched definition (this is the API reference page itself, the
strongest tier of docs source, effectively the same text `spec3.json` embeds but independently
confirmed). Supporting context on the underlying mechanism, from
<https://docs.stripe.com/disputes/get-started/prevention> (verbatim): describing Visa's Compelling
Evidence 3.0 (CE 3.0) pre-dispute block: "If at least two prior transactions exist with complete product
descriptions that have matching IP addresses and at least one matching email address or customer
delivery address, the issuer must block the dispute. **As a result, the dispute is never filed** and you
don't incur any dispute fees or increases to your dispute rate." Separately, Verifi's Rapid Dispute
Resolution (RDR) product resolves would-be disputes for "a fee per dispute" without the standard "dispute
received fee."

**Refined-but-not-fully-closed**: this establishes that dispute-prevention products (CE 3.0 via Order
Insight, RDR, Ethoca Alerts) are the mechanism behind `status=prevented`, and that a *fully* prevented
dispute (CE 3.0 block) involves **no** dispute fee and (by clear implication, since "the dispute is never
filed") **no** fund withdrawal at all. I did not find an explicit statement of whether a `Dispute` API
object with `status=prevented` is actually created in every prevented case (vs. the block happening
before any Dispute object exists) — plausible reading is that it is created (since `prevented` is a
listed value of the object's own `status` field, so *something* must be able to carry that value), most
likely for the RDR/Ethoca "resolved-with-a-fee" path rather than the CE3.0 "never filed at all" path.
This last distinction (which prevention path produces a `Dispute` object with `status=prevented` vs. no
object at all) remains a genuine open question — treat `balance_transactions=[]` as the safe default
modeling choice for a `prevented` dispute given the "never filed"/"no dispute fee" language.

Sources: <https://docs.stripe.com/api/disputes/object>, <https://docs.stripe.com/disputes/get-started/prevention>
(`tavily_extract`, both read 2026-09-18).

---

## 8. `balance_transaction.type` for a dispute withdrawal

**Original claim**: inferred as `adjustment` purely because the 51-value enum had no dispute-specific
literal value — explicitly flagged as an unconfirmed inference.

**Verdict: CONFIRMED, directly and explicitly.**

<https://docs.stripe.com/reports/balance-transaction-types> is a page dedicated to exactly this question
and states, verbatim (read 2026-09-18), under the `adjustment` type's description:

> "**Disputes**. When a customer disputes a charge, Stripe deducts the disputed amount from your balance.
> The deduction is represented as a Balance transaction with the type `adjustment`, where the source
> object is a dispute.
>
> **Dispute reversals**. When you win a dispute, the disputed amount is returned to your balance. The
> returned funds are represented as a Balance transaction with the type `adjustment`, where the source
> object is a dispute."

This is an unambiguous, direct confirmation of the original pass's inference for **both** halves of the
dispute ledger lifecycle: the withdrawal (dispute opened) and the reinstatement (dispute won) are both
`balance_transaction.type=adjustment`, distinguished from each other and from other adjustment causes
(e.g. refund failures) only by the `description` field and the `source` object pointing back to the
`Dispute`. No new/hidden dispute-specific `type` literal exists — the enum-absence reasoning in the
original pass turned out to be exactly right.

Source: <https://docs.stripe.com/reports/balance-transaction-types> (`tavily_extract`, read 2026-09-18).

---

## 9. Dispute fee refundability when the merchant wins

**Original claim**: not verified this session — flagged as a clean gap, "widely known" third-party
commentary only.

**Verdict: CONFIRMED — nuanced, two-fee structure, only one of the two fees is ever refundable.**

From <https://docs.stripe.com/disputes/how-disputes-work>, section "Dispute fees" (verbatim, read
2026-09-18):

> "The fee for receiving a dispute is deducted from your account balance when a cardholder initiates a
> dispute. Dispute fees vary based on your business location:
> - For businesses outside Mexico, **the fee for receiving a dispute is non-refundable**.
> - For businesses in Mexico, the fee for receiving a dispute might be returned if you win or the
>   cardholder withdraws.
> - Businesses in the Single Euro Payments Area (SEPA) incur no fee for receiving a dispute on a card
>   payment processed on the Cartes Bancaires network.
>
> If you counter a dispute, a dispute countered fee applies, in addition to the dispute received fee...
> **Stripe returns the dispute countered fee if you win the dispute.** Unless otherwise stated in your
> Stripe contract, we never return the dispute received fee."

So there are **two distinct fees**, with **different** refundability rules:

1. **Dispute received fee** (charged the moment a dispute is opened, regardless of outcome) — **never**
   refunded (outside Mexico; Mexico is a named regional exception).
2. **Dispute countered fee** (charged only if the merchant chooses to contest/submit evidence) — **is**
   refunded if the merchant wins, never returned if they lose.

A merchant who wins a dispute they chose to contest gets the *countered* fee back but **not** the
original *received* fee — "winning" only makes you whole on the cost of fighting, not the base cost of
having been disputed at all. This directly closes the original gap and adds real nuance the original
"not verified" placeholder had no way to capture.

Source: <https://docs.stripe.com/disputes/how-disputes-work> (`tavily_extract`, read 2026-09-18).

---

## Summary of outcomes

| # | Item | Outcome |
| --- | --- | --- |
| 1 | Proration cent rounding | **Refined/closed for the core question** — lines round independently to the nearest cent, then sum (confirmed with reproducible arithmetic); exact tie-break rule (`x.xx5`) remains genuinely undocumented |
| 2 | `subscription_cycle` invoice lead time | **Confirmed** — created exactly at the period boundary, zero lead time; ~1hr draft window follows |
| 3 | Quantity-only reproration | **Refined** — directly stated to use the same whole-configuration mechanism as price changes; still no isolated numeric example |
| 4 | Ninth hard-decline code | **Closed** — `transaction_not_allowed`, now Stripe-primary-sourced |
| 5 | `unpaid`-invoice draft-vs-closed contradiction | **Confirmed as a real, persisting contradiction in Stripe's own docs** — weight of evidence (2 direct sources) now favors `draft`; flagged loudly as instructed |
| 6 | `unpaid` → `active` recovery | **Confirmed** — auto-recovers on invoice payment, no explicit update call needed |
| 7 | `dispute.status=prevented` | **Confirmed** (definition); fund/object-creation mechanics partially refined, one sub-question still open |
| 8 | Dispute `balance_transaction.type` | **Confirmed** — `adjustment`, for both withdrawal and reversal |
| 9 | Dispute fee refundability | **Confirmed** — two-fee structure; countered fee refundable on win, received fee never is (except Mexico) |

Every source cited above was fetched directly via `tavily_extract` on 2026-09-18 and is a stronger source
tier than the `WebSearch`-snippet sourcing used throughout the rest of this lane's original pass.
