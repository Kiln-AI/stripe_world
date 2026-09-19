# The `balance_transaction` Ledger and Payouts

See `subscription-status-machine.md` for the access-method caveat.

## What creates a `balance_transaction`

Every event that moves money into or out of the Stripe balance creates exactly one `balance_transaction`
record. The full `type` enum, taken directly from `spec3.json` (`components.schemas.
balance_transaction.properties.type`) — this is ground truth, not prose-derived:

```
adjustment, advance, advance_funding, anticipation_repayment, application_fee,
application_fee_refund, charge, climate_order_purchase, climate_order_refund,
connect_collection_transfer, contribution, fee_credit_funding, inbound_transfer,
inbound_transfer_reversal, issuing_authorization_hold, issuing_authorization_release,
issuing_dispute, issuing_transaction, obligation_outbound, obligation_reversal_inbound,
payment, payment_failure_refund, payment_network_reserve_hold, payment_network_reserve_release,
payment_refund, payment_reversal, payment_unreconciled, payout, payout_cancel, payout_failure,
payout_minimum_balance_hold, payout_minimum_balance_release, refund, refund_failure,
reserve_hold, reserve_release, reserve_transaction, reserved_funds,
stripe_balance_payment_debit, stripe_balance_payment_debit_reversal, stripe_fee, stripe_fx_fee,
tax_fee, tax_fund, topup, topup_reversal, transfer, transfer_cancel, transfer_failure,
transfer_refund
```

(51 values.) The subset directly relevant to the billing/subscriptions/invoicing slice this project
scopes (per `project_overview.md` §4) is small: **`charge`** / **`payment`** (a successful
charge/PaymentIntent capture — note both exist, likely `payment` is the newer PaymentIntent-era type and
`charge` the legacy Charges-API type; not disambiguated further in this pass), **`refund`** /
**`payment_refund`** (same legacy/newer duality pattern), **`payout`**, **`payout_cancel`**,
**`payout_failure`**, **`adjustment`** (catch-all, almost certainly what disputes use — see
`refunds-and-disputes.md` gap #2), **`stripe_fee`** (processing fee line), and **`application_fee`** /
**`application_fee_refund`** (Connect platform fees — likely out of scope per the object-graph
subtopic's Connect boundary call). Stripe also names a `reporting_category` field (spec3.json, verbatim)
as the **recommended** field for accounting classification instead of raw `type`: "To classify
transactions for accounting purposes, consider `reporting_category` instead." — not enumerated in this
pass; flag for the schema subtopic to pull its own enum list directly from `spec3.json`.

## Fee math

Three fields, all `integer` cents, all spec3.json-verbatim:

- **`amount`** — "Gross amount of this transaction (in cents...). A positive value represents funds
  charged to another party, and a negative value represents funds sent to another party."
- **`fee`** — "Fees (in cents...) paid for this transaction. Represented as a positive integer when
  assessed."
- **`net`** — "Net impact to a Stripe balance (in cents...). A positive value represents incrementing a
  Stripe balance, and a negative value decrementing a Stripe balance. You can calculate the net impact
  of a transaction on a balance by `amount` - `fee`."

So the formula is exact and given by Stripe itself: **`net = amount - fee`**. No ambiguity, no separate
rounding step described for this particular arithmetic (it's integer cents on both sides already).

**`fee_details`** — array, "Detailed breakdown of fees (in cents...) paid for this transaction." Each
entry uses the `fee` schema (spec3.json): `{ amount: integer, currency, description, type, application }`,
where `type` ∈ `[application_fee, payment_method_passthrough_fee, stripe_fee, tax, withheld_tax]`
(spec3.json, verbatim from the `fee` schema's `type` field description) — so a single balance transaction
can be composed of *multiple* fee line items (e.g., a base Stripe processing fee plus a separate
passthrough fee plus tax), all of which sum to the top-level `fee` value. The actual **percentage/fixed
fee rate itself is not in the spec** (it's an account/pricing-plan property, not a documented constant) —
I did not find Stripe's docs stating a single canonical rate as an API-discoverable value; third-party
sites quote "2.9% + 30¢" for US standard card payments but this is commercial pricing, not spec-governed,
and varies by country/card type/negotiated plan. **For a Seahaven fixture generator, this means the fee
rate should be a configurable constant the world defines, not something derivable from the API spec
itself.**

## `available` vs `pending` and `available_on`

`balance_transaction.status` (spec3.json, verbatim): "The transaction's net funds status in the Stripe
balance, which are either `available` or `pending`." Exactly two values, both named as a closed set in
prose (the field itself is typed as plain `string`, not a closed OpenAPI `enum:` — same looseness noted
for `refund.status` in `refunds-and-disputes.md`).

`available_on` (spec3.json, verbatim): "The date that the transaction's net funds become available in
the Stripe balance." Typed `integer` (unix timestamp).

Timing (via search, since the *specific delay* is a product/country policy, not a spec constant):
"The charged amount, less any Stripe fees, is initially reflected on the pending balance, and becomes
available on a 2-day rolling basis, though this timing can vary by country and account." Framed more
generally as a **T+X** model: "T" = the original payment confirmation/capture time, "X" = a
country-specific number of business days (the search result specifically names **T+2** as a commonly
cited baseline for card payments and **T+3** as another country's standard — i.e. **this is not a single
universal constant**, it is per-country/per-account). "Choosing a payout schedule doesn't change how
long it takes for your pending balance to become available — it only controls when payouts are sent."
This last sentence is important: **payout schedule and settlement delay (`available_on`) are two
independent knobs** — do not conflate "how often payouts run" with "how long funds sit pending."

**Recommendation for the Seahaven world**: treat the pending→available delay as a configurable constant
(e.g. default to T+2 business days) applied uniformly to `payment`/`charge` balance transactions'
`available_on`, rather than trying to model real per-country variation — this is exactly the kind of
fidelity-vs-scope tradeoff §4/§12 of the project overview is meant to flag, noted here for the
architecture step to decide on explicitly.

## `balance` object — the aggregate view (spec3.json)

`components.schemas.balance.properties`: `available`, `pending`, `instant_available`, `connect_reserved`,
`issuing`, `refund_and_dispute_prefunding` (each an array of per-currency amounts, via `source_types`
sub-breakdown per the field descriptions). `available`/`pending` descriptions confirm the mental model
directly: **`available`** = "funds that you can transfer or pay out"; **`pending`** = "funds that aren't
available in the balance yet." This is simply the sum, per currency, of all `balance_transaction` rows
whose `status` matches, restricted to those not yet drawn down by a payout.

## Payouts — how they draw down the balance

`payout.status` (spec3.json, verbatim): "Current status of the payout: `paid`, `pending`, `in_transit`,
`canceled` or `failed`. A payout is `pending` until it's submitted to the bank, when it becomes
`in_transit`. The status changes to `paid` if the transaction succeeds, or to `failed` or `canceled`
(within 5 business days). Some payouts that fail might initially show as `paid`, then change to
`failed`."

```
status enum (prose, not closed OpenAPI enum): pending, in_transit, paid, canceled, failed
```

Draw-down mechanics, tied directly to the ledger:

- `payout.balance_transaction` — "ID of the balance transaction that describes the impact of this
  payout on your account balance." This is the debit entry (`type=payout`) that reduces `available`.
- `payout.failure_balance_transaction` — "If the payout fails or cancels, this is the ID of the balance
  transaction that reverses the initial balance transaction and returns the funds from the failed payout
  back in your balance." I.e. a failed/canceled payout produces a **second**, reversing
  `balance_transaction` (consistent with the `payout_cancel`/`payout_failure` types in the `type` enum
  above) rather than mutating the original entry.
- `payout.automatic` (boolean) — "`true` if the payout is created by an automated payout schedule... and
  `false` if it's requested manually." So payouts are created either by a background schedule (daily/
  weekly/monthly, per account payout-schedule settings — not itself examined in this pass) or by an
  explicit `POST /v1/payouts` call.
- `payout.method` — `standard` or `instant`. Standard payouts (via search, `manual payouts` docs)
  draw only from the `available` balance; **instant** payouts (spec3.json: "instant is supported for
  payouts to debit cards and bank accounts in certain countries") draw from `instant_available`
  specifically, a separate, generally-smaller/faster-clearing sub-balance (per search: "Manual payouts
  using standard speed can only draw on the available balance... Instant manual payouts can draw on the
  `instant_available` balance").
- `payout.reconciliation_status` — enum `[completed, in_progress, not_applicable]` (spec3.json,
  verbatim closed enum this time). "If `completed`, you can use the Balance Transactions API to list all
  balance transactions that are paid out in this payout" — i.e. once reconciliation completes, every
  `balance_transaction` swept into that payout is queryable via `GET /v1/balance_transactions?payout=...`
  (standard Stripe list-filter pattern — confirm exact filter param with the schema subtopic).

## Sources

- `research/stripe-openapi/spec3.json`: `components.schemas.balance_transaction.properties.*`
  (`type` enum, `amount`, `fee`, `fee_details`, `net`, `status`, `available_on`, `reporting_category`),
  `components.schemas.fee` (fee-breakdown sub-object), `components.schemas.balance.properties.*`,
  `components.schemas.payout.properties.*` — API version `2026-08-26.dahlia`, fetched 2026-09-18.
  This document leans almost entirely on the spec itself since balance/payout mechanics are unusually
  well-documented inline in the OpenAPI descriptions.
- WebSearch synthesis quoting <https://docs.stripe.com/payouts>,
  <https://docs.stripe.com/payouts/next-day-settlement>,
  <https://support.stripe.com/questions/understanding-funding-availability-speeds-on-your-financial-account>,
  <https://docs.stripe.com/payouts/instant-payouts> — accessed 2026-09-18 (direct fetch blocked; used
  only for the settlement-delay timing narrative, which is not present in `spec3.json`).

## Gaps

1. No API-discoverable fee rate/schedule — treated as an account-config constant a Seahaven fixture
   should define itself rather than derive from Stripe's spec.
2. `charge`/`payment` and `refund`/`payment_refund` type-pair disambiguation (legacy vs. current) not
   confirmed from a direct source; inferred from naming convention only.
3. Exact settlement delay (`available_on` offset) varies by country/account and has no single documented
   constant; recommend a configurable default (e.g. T+2) for the mock rather than chasing real-world
   variability.
4. `reporting_category` enum not enumerated in this pass (recommend the schema subtopic pull it
   directly from `spec3.json` if it's needed for fidelity).
