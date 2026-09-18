# Credit Notes and Subscription Schedules

See `subscription-status-machine.md` for the access-method caveat.

## Credit Notes

### Status/type/reason (spec3.json, verbatim — strong source)

- `credit_note.status` — `enum: [issued, void]`. "Status of this credit note, one of `issued` or
  `void`." Two states only — a credit note is not itself draft/finalized like an invoice; it's created
  already-effective (`issued`) or later voided.
- `credit_note.type` — `enum: [mixed, post_payment, pre_payment]`. Note the description text and the
  enum values are slightly inconsistent in the spec itself: the description says "one of `pre_payment`
  or `post_payment`" but the actual enum has a **third** value, `mixed`, undescribed in prose. "A
  `pre_payment` credit note means it was issued when the invoice was open. A `post_payment` credit note
  means it was issued when the invoice was paid." `mixed` is presumably for a credit note spanning both
  a while-open portion and a while-paid portion (e.g. if the invoice's payment status changed between
  when credit-note line items were being assembled) — **inferred, not documented**; flagged as a
  spec-prose gap worth reporting (the description field is stale relative to the enum).
- `credit_note.reason` — `enum: [duplicate, fraudulent, order_change, product_unsatisfactory]`, closed
  enum, matches the create-endpoint param exactly (cross-checked both locations in `spec3.json`).

### Effect on the invoice — three distinct settlement channels

A credit note's `create` params require **at least one** of `amount` / `lines` / `shipping_cost`, and
independently can specify **any combination** of:

- **`refund_amount`** (spec3.json, verbatim): "If set, a refund will be created for the charge
  associated with the invoice." — this is real money leaving via the Refunds machinery (see
  `refunds-and-disputes.md`), i.e. it produces an actual `refund` object and its own
  `balance_transaction`.
- **`credit_amount`** (spec3.json, verbatim): "the amount to credit the customer's balance, which will
  be automatically applied to their next invoice." — this does **not** touch `balance_transaction` /
  real money at all; it's a `customer.balance` adjustment (a promise against future invoices), separate
  from the Stripe account balance ledger entirely. This is an important distinction for the ledger doc:
  **customer balance credit is not the same ledger as the account's `balance_transaction` stream.**
- **`out_of_band_amount`** (spec3.json, verbatim): "the amount that is credited outside of Stripe." —
  pure bookkeeping, no Stripe-side money movement or refund object at all; exists purely so the credit
  note's total reconciles when the merchant settled part of it by other means (check, cash, etc.).

Via search, on how these interact with a paid invoice: "When you issue a credit note for an open
invoice, it decreases the amount due on the invoice. When you issue a credit note for a paid invoice,
you credit the customer's account balance or give them a refund outside of Stripe" (i.e. for a
**paid** invoice — which can no longer have its own `amount_due` reduced since it's already fully paid
— the credit note *must* resolve via one of `refund_amount`/`credit_amount`/`out_of_band_amount`,
whereas for an **open** invoice a credit note can simply reduce what's still owed without any of those
three needing to be set). Also via search: "For a paid invoice, the sum of the refund, credit, and
out-of-band payment amounts must equal the credit note total" — a hard reconciliation invariant worth
enforcing in a mock's validation layer.

### Interaction with the Refunds API directly

Via search: "For invoices that also have refunds created through the Refund API, the credit note API
subtracts those refund amounts from the maximum creditable amount. This prevents the combined credit
notes and refunds from exceeding the invoice amount." — i.e. credit notes and direct
`refunds.create` calls share a single ceiling (the invoice/charge total), tracked jointly, not
independently. A faithful mock needs a single running "amount already returned to the customer by any
means" counter per invoice, not two separate ones.

### Credit note line items

`lines` param: each entry is `type` ∈ `[custom_line_item, invoice_line_item]`
(`credit_note_line_item_params`, spec3.json) with `amount`/`unit_amount`/`quantity`/`tax_amounts`/
`tax_rates`, and — for `type=invoice_line_item` — a reference back to `invoice_line_item` (the specific
original line being credited). This is the schema-level tie between a credit note and the
`proration_details.credited_items` structure noted in `proration-arithmetic.md`'s companion invoice
doc — i.e. crediting a specific proration debit line is a first-class, spec-modeled operation, not
something bolted on.

## Subscription Schedules

### What it is (spec3.json fields)

`subscription_schedule` top-level fields: `id`, `status`, `customer`, `subscription` (the underlying
subscription it drives, once one exists), `current_phase`, `phases` (array), `default_settings`,
`end_behavior`, `released_at`/`released_subscription`, `canceled_at`/`completed_at`, `billing_mode`,
`test_clock`.

`status` — `enum: [active, canceled, completed, not_started, released]` (spec3.json, verbatim closed
enum): "The present status of the subscription schedule. Possible values are `not_started`, `active`,
`completed`, `released`, and `canceled`."

`end_behavior` — `enum: [cancel, none, release, renew]`, default described as `release`: "`release` will
end the subscription schedule and keep the underlying subscription running. `cancel` will end the
subscription schedule and cancel the underlying subscription." (`none`/`renew` present in the enum but
undescribed in the property text found — **gap**, not corroborated further this pass.)

Each **phase** (`subscription_schedule_phase_configuration`) carries its own: `items`, `currency`,
`collection_method`, `billing_cycle_anchor`, `billing_thresholds`, `proration_behavior`, `start_date`/
`end_date`, `trial`/`trial_end`, `add_invoice_items`, `automatic_tax`, `default_payment_method`,
`default_tax_rates`, `discounts`, `invoice_settings`, `application_fee_percent`, `transfer_data`,
`on_behalf_of`, `description`, `metadata` — i.e. essentially the full surface of a subscription's
billing configuration, snapshotted per time-block.

### Endpoints (spec3.json `paths`)

```
POST   /v1/subscription_schedules                    (create)
GET    /v1/subscription_schedules                     (list)
GET    /v1/subscription_schedules/{schedule}           (retrieve)
POST   /v1/subscription_schedules/{schedule}           (update)
POST   /v1/subscription_schedules/{schedule}/cancel     (cancel)
POST   /v1/subscription_schedules/{schedule}/release    (release — detach the schedule, subscription
                                                           keeps running on its own from here)
```

Six operations total: create/list/retrieve/update/cancel/release.

### Why you'd use it instead of direct `subscriptions.update` — the actual value proposition

Via search, synthesizing docs + third-party commentary consistently: "Use subscription schedules to
automate changes to subscriptions over time. Some changes you might want to schedule include starting a
subscription on a future date, backdating a subscription to a past date, and upgrading or downgrading a
subscription [**at a future, predetermined time**]." The core distinction from a direct
`subscriptions.update` call: a direct update is **immediate** — "The default Stripe subscription update
is designed for immediate changes" and computes proration synchronously against the current cycle right
then. A schedule instead lets you **pre-declare a sequence of phases** (e.g. "3 months at price A, then
switch to price B") so the transition happens automatically at the phase boundary, without a
synchronous mutation call at that future moment, and "decouples the customer's decision from the billing
change" — e.g. a salesperson can set up a future downgrade today that only takes effect at renewal,
without needing a scheduled job of their own to call the API later.

### Explicit cost/benefit verdict: **is `subscription_schedules` worth including?**

**Recommendation: model it, but as a thin layer, not full-fidelity — and only if the project's use cases
actually need "declare now, take effect later" semantics.** Reasoning:

**Cost side:**
- It is a genuinely separate object with its own status machine, its own six endpoints, and — critically
  — a `phases` array whose *elements* replicate nearly the entire subscription-configuration surface
  (price, quantity, proration behavior, trial, tax, discounts, collection method, transfer data...).
  Modeling it faithfully means either (a) duplicating a large fraction of the subscription-item/price
  schema inside a `subscription_schedule_phase` table, or (b) accepting a much shallower phase model that
  only captures the fields this project's scenarios actually exercise. Either way it is **not a cheap
  add** relative to its own footprint — it's arguably as complex as the subscription object itself, just
  time-sliced.
- Its main *behavioral* payoff — phases executing automatically at a future boundary with no explicit
  API call at that moment — only matters if the Seahaven world's scenarios need to advance a test clock
  *across* a scheduled phase transition and observe the subscription mutate itself without a tool call in
  between. If the project's conformance scenarios only ever call `subscriptions.update` directly to
  change price/quantity, the schedule object adds surface area with no scenario exercising its
  distinguishing feature.

**Benefit side:**
- If the project's goal (per the research plan) includes agent-driven scenarios like "set up a
  downgrade for next month" or "start this subscription in 30 days," a `subscriptions.update` alone
  **cannot express that** — there is no "effective_at" on a plain update; you'd need either a
  schedule or an out-of-band scheduled job the mock doesn't otherwise have modeled. In that case,
  subscription_schedules is the *only* way to represent "declare a future change" state faithfully, and
  skipping it would push that scenario class out of scope entirely.
- It's the mechanism Stripe itself recommends for "cleaner invoicing and simpler customer experiences"
  around planned changes (via search) — so if the eval/tool-surface work (subtopic 5) finds that real
  agent tool-lists (Stripe's own MCP server / agent-toolkit) expose subscription-schedule tools, that
  would tip the recommendation toward inclusion regardless of the modeling cost, since fidelity to real
  agent surfaces is a stated goal.

**Bottom line for the architecture step:** treat `subscription_schedules` as **conditional and
scoped-down** — include the object and its status machine, `default_settings`, and a phases array with
only the fields the project's actual `project_overview.md` §4 scenarios need (most likely: `items`,
`start_date`/`end_date`, `proration_behavior`, and maybe `trial_end`), explicitly deferring `transfer_data`/
`application_fee_percent`/`on_behalf_of` (Connect-flavored fields, out of scope per the object-graph
subtopic's Connect boundary anyway) and `add_invoice_items`/`automatic_tax`/`discounts` unless a
concrete scenario needs them. Do **not** attempt full parity with the live API's phase configuration
surface — the cost is disproportionate to the likely scenario coverage, per the §4 budget note in the
research plan (project budgets 15–20 tables / 40–60 tools total; a full-fidelity subscription_schedule
alone could plausibly consume 5-10% of that budget on its own given the phase-object's breadth).

## Sources

- `research/stripe-openapi/spec3.json`: `components.schemas.credit_note.properties.*`,
  `paths./v1/credit_notes.post` request schema (`refund_amount`, `credit_amount`, `out_of_band_amount`,
  `reason`, `lines`), `components.schemas.subscription_schedule.properties.*`,
  `components.schemas.subscription_schedule_phase_configuration.properties.*`,
  `paths` entries under `/v1/subscription_schedules*` — API version `2026-08-26.dahlia`, fetched
  2026-09-18.
- WebSearch synthesis quoting <https://docs.stripe.com/billing/invoicing/credit-notes>,
  <https://docs.stripe.com/invoicing/dashboard/credit-notes>,
  <https://support.stripe.com/questions/using-credit-notes-with-invoices-in-stripe-billing>,
  <https://docs.stripe.com/billing/subscriptions/subscription-schedules>,
  <https://docs.stripe.com/billing/subscriptions/change> — accessed 2026-09-18 (direct fetch blocked).

## Gaps

1. `credit_note.type=mixed` — enum value present in `spec3.json` but undocumented in the field's own
   description text (which only mentions `pre_payment`/`post_payment`); inferred meaning only.
2. `subscription_schedule.end_behavior` values `none` and `renew` — present in the closed enum, not
   described in the property text captured this pass.
3. Whether Stripe's own MCP server / agent-toolkit exposes subscription-schedule tools was **not**
   checked here — that's subtopic 5's ("Agent surfaces, licensing and naming") territory, but the
   finding would directly affect the cost/benefit verdict above; flagged as a cross-subtopic dependency
   worth the synthesis step reconciling.
