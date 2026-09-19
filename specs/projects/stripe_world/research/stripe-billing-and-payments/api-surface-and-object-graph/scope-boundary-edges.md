# Scope Boundary Edges

Every `$ref` or expandable field, found while compiling `resource-inventory.md`, that points from an
in-scope object to something outside project_overview.md §4's declared scope (Connect, Issuing,
Terminal, Treasury, Capital, Climate, Crypto, Stripe Tax, Radar, Sigma, Financial Connections, Identity,
Checkout Sessions, Payment Links, Elements, the Billing Portal, the Dashboard) — plus a few edges that
cross into *adjacent in-scope territory but the wrong subsystem* (legacy Sources API, test clocks) and
therefore still need an explicit ruling. For each: the edge, one of **model / stub / null**, and why.

**Definitions used below:**
- **model** — build the real object (its own table or embedded schema), at least a useful subset of its
  fields.
- **stub** — keep the field, but represent it as an opaque id-string only (or a thin passthrough of
  whatever the caller sent); never resolve, expand, or validate it against a real object.
- **null** — drop the field from the world's object shape entirely, or always return `null`/`[]`/the
  schema's zero-value for it. (A conformance-diff allow-list entry, per project_overview.md §8.)

---

## Connect

Connect is explicitly out of scope (§4). It shows up as a field on nearly every in-scope payments
object because Stripe's object model doesn't distinguish "platform" fields from "Connect" fields at
the schema level — they're just optional/nullable fields that are `null` outside a Connect context.
**This is good news: on a non-Connect account, every one of these fields is already `null` in the real
API**, so nulling them here is not a divergence from Stripe's own behavior on a directly-owned account
— it's the correct behavior for the account type this world models.

| Edge | Found on | Ruling | Reason |
|---|---|---|---|
| `application` (`anyOf[string\|ref:application]`) | `payment_intent`, `setup_intent`, `charge`, `subscription`, `invoice`, `subscription_schedule` | **null** | Always `null` on a direct (non-Connect-platform) account already; matches real behavior, zero cost. |
| `on_behalf_of` (`anyOf[string\|ref:account]`) | `payment_intent`, `setup_intent`, `charge`, `subscription`, `invoice` | **null** | Same — Connect-only field, `null` by default. |
| `application_fee_amount`, `application_fee` | `payment_intent`, `charge`, `payout` | **null** | Connect application-fee mechanics; meaningless without Connect. |
| `transfer_data`, `transfer_group`, `transfer`, `source_transfer`, `source_transfer_reversal` | `payment_intent`, `charge`, `refund`, `subscription` | **null** | Connect's destination-charge / transfer mechanism. |
| `connect_collection_transfer` (as a `balance_transaction.source` variant) | `balance_transaction` | **null** (this variant never occurs) | Connect-specific ledger source type; will never appear in in-scope activity. |
| `reserve_transaction` (as a `balance_transaction.source` variant) | `balance_transaction` | **null** (this variant never occurs) | Connect reserve mechanism. |
| `account` (top-level resource, referenced everywhere as `anyOf[string\|ref:account]`) | `payment_method.customer`... no — `on_behalf_of`, `issuer` (invoice) | **stub as id-only where a reference must round-trip; null where it's Connect-only** | `invoice.issuer` (`ref:connect_account_reference`, REQUIRED on `invoice`) is the one field in this whole category that **cannot simply be nulled** — it's non-nullable and required. Model it as its degenerate self-owned form: always emit `{"type": "self"}` (the documented shape when the invoice isn't issued on behalf of a connected account). This is a concrete, load-bearing exception to "just null it" — flag it for the functional spec so nobody hits a required-field conformance failure. |
| `issuing.authorization` / `issuing.dispute` / `issuing.transaction` (as `balance_transaction.source` variants) | `balance_transaction` | **null** (variant never occurs) | Issuing is out of scope entirely; these `source` union members will never be produced. |

## Stripe Tax (the automatic-calculation product — distinct from flat `tax_rate`/`tax_id`)

§4 is explicit: `tax_rates` stays in scope ("flat rates only, not Stripe Tax"). The schema draws this
same line cleanly — `tax_rate` and `tax_id` are plain data records with no dependency on the
`tax.*`-namespaced schemas (`tax.calculation`, `tax.transaction`, `tax.registration`, `tax.settings`,
`tax.association`, and the ~30 `tax_product_*` supporting schemas). The only place the line gets blurry
is `automatic_tax` (embedded on `subscription` and `invoice`) and `customer.tax`.

| Edge | Found on | Ruling | Reason |
|---|---|---|---|
| `automatic_tax` (`ref:subscription_automatic_tax` / `ref:automatic_tax`) | `subscription` (REQUIRED), `invoice` (REQUIRED) | **model minimally as `{enabled: false}`** | Non-nullable required object on both. Its real shape has `enabled`, `disabled_reason`, `liability`, `status` — model just `enabled` (always `false` in this world, since Stripe Tax is out of scope) and drop the rest, rather than nulling a required field. |
| `customer.tax` (`ref:customer_tax`) | `customer` | **null / omit** | Not required (`opt`, not nullable but has a schema default), carries `automatic_tax` enablement + `ip_address`/`location` for tax jurisdiction inference — purely Stripe-Tax machinery. |
| `product.tax_code` (`anyOf[string\|ref:tax_code]`) | `product` | **stub as id-only string, or null** | `tax_code` is a Stripe-maintained taxonomy id (e.g. `txcd_99999999`) used only to feed Stripe Tax calculation. Since automatic tax stays off, this field has no effect in this world; stub it as an accepted-but-unused string field for schema-conformance, don't build the taxonomy. |
| `billing_bill_resource_invoicing_taxes_tax` (`invoice.total_taxes[]`, `line_item.taxes[]`, `credit_note_line_item.taxes[]`) | `invoice`, `line_item`, `credit_note_line_item` | **model** | This is *not* Stripe Tax — it's the per-line application of a flat `tax_rate` (references `tax_rate_details` → `tax_rate.id`), which is exactly the in-scope flat-rate mechanism. Keep it; it's how `default_tax_rates`/`tax_rates` on subscriptions and invoice items actually manifest as invoice totals. |

## Radar (fraud/risk product)

| Edge | Found on | Ruling | Reason |
|---|---|---|---|
| `review` (`anyOf[string\|ref:review]`) | `payment_intent`, `charge` | **null** | Radar review is a distinct out-of-scope product; the field is nullable and `null` whenever Radar review isn't triggered — which, with Radar out of scope, is always. |
| `radar_options` (`ref:radar_radar_options`) | `charge`, `payment_method` | **null / omit** | A single-field object (`session` id from Radar.js) with no effect absent Radar. |
| `outcome` (`ref:charge_outcome`) | `charge` | **null**, with one exception | `outcome` carries Radar's risk scoring (`risk_level`, `risk_score`, `rule`) *and* the plain decline/success reasoning (`network_status`, `reason`, `seller_message`, `type`). Recommend modeling the non-Radar subset (`network_status`, `reason`, `type`, `seller_message`) since declines are core to the project's failure-injection thesis (test-mode magic cards, subtopic 4), and nulling the Radar-specific fields (`risk_level`, `risk_score`, `rule`) within the same object rather than dropping `outcome` wholesale. |
| `fraud_details` (`ref:charge_fraud_details`) | `charge` | **model (cheap)** | Not actually Radar — it's a merchant-settable flag (`user_report`/`stripe_report`, both plain strings) with no dependency on the Radar product; trivial to keep for completeness. |

## Checkout Sessions / Payment Links (client-side / hosted-page products, explicitly out per §4)

| Edge | Found on | Ruling | Reason |
|---|---|---|---|
| `checkout_session` (`anyOf[string\|ref:checkout.session]`) | `customer_balance_transaction`, `discount.checkout_session` | **stub as id-only string** | These fields exist so a balance transaction or discount can be traced back to the Checkout Session that produced it. Since this world's `customer_balance_transaction` and `discount` rows are produced by direct API calls, not Checkout, the field will simply always be `null` here — no stubbing needed in practice, but if a future feature synthesizes one, treat the value as an opaque string, never a real `checkout.session` object. |
| `checkout_session_subscription_payment` / `checkout_session_subscription_payment_canceled` (as `customer_balance_transaction.type` enum values) | `customer_balance_transaction` | **null** (these enum values never occur) | Checkout-specific ledger event types; this world's `customer_balance_transaction.type` values will only ever be the subset produced by direct invoicing/credit-note flows. |

## Financial Connections / Issuing / Terminal / Treasury / Capital / Climate / Crypto

None of these appear as a *direct* `$ref` from any in-scope object's top-level fields in the schema
survey behind `resource-inventory.md` — they only appear as (a) far-outer members of the
`balance_transaction.source` 16-way union (`issuing.authorization`, `issuing.dispute`,
`issuing.transaction`, already covered under Connect above since they share the mechanism), (b)
deep inside `payment_method_details.us_bank_account`'s optional `financial_connections_account`
sub-field (see payment-method-details section below), and (c) as enum members in wide type lists
(`balance_transaction.type` includes `climate_order_purchase`/`climate_order_refund`;
`payment_method.type` doesn't include Crypto's underlying network details beyond the `crypto` rail
stub). **Ruling: null across the board** — these products have no other point of contact with the
in-scope object graph, so there is nothing to model or stub; the relevant enum members and nested
fields simply never occur in this world's data.

## Legacy Sources / Cards / Bank Accounts API (`source`, `card`, `bank_account`, and their `deleted_*` variants)

This predates `payment_method` (introduced 2019) and is still live in the spec for backward
compatibility. It is *not* one of §4's "Out" categories by name, but it is functionally superseded by
`payment_method`, which §4 does list as in-scope core. Treating both as in-scope would mean building
two parallel, largely-redundant payment-method representations.

| Edge | Found on | Ruling | Reason |
|---|---|---|---|
| `customer.default_source` (`anyOf[string\|bank_account\|card\|source]`) | `customer` | **stub as id-only string, or repoint at `payment_method`** | Recommend the world simply doesn't populate this field (always `null`) and treats `invoice_settings.default_payment_method` / `subscription.default_payment_method` as the one source of truth — matches modern integration guidance from Stripe itself, which steers new integrations away from `default_source`. |
| `invoice.default_source`, `subscription.default_source` | `invoice`, `subscription` | **null** | Same reasoning. |
| `payout.destination` (`anyOf[string\|bank_account\|card\|deleted_bank_account\|deleted_card]`) | `payout` | **stub as id-only string** | A payout has to point *somewhere*, and "where the money went" is worth keeping for fidelity, but modeling a full external-account resource (with its own bank-routing / card-brand fields, verification states, etc.) is exactly the kind of second parallel payment-method system this cut is meant to avoid. An opaque `ba_...`/`card_...`-shaped string id is enough to make payout fixtures look right without building the object. |
| `customer.sources`, `customer.cards` sub-resources, `/v1/customers/{customer}/bank_accounts*`, `/v1/customers/{customer}/cards*`, `/v1/customers/{customer}/sources*` (whole path families) | `customer` | **null / cut entirely** | 3 sub-resource path families, ~15 operations combined (see minimum-closed-set-and-tool-budget.md) that only exist for `source`/`card`/`bank_account` objects. Cutting them is the single largest tool-budget win available without touching a fidelity-bearing object. |

## `mandate` and `setup_attempt`

| Edge | Found on | Ruling | Reason |
|---|---|---|---|
| `mandate`, `single_use_mandate` (`anyOf[string\|ref:mandate]`) | `setup_intent`, (also appears on `payment_intent.payment_method_options.*.mandate_options` deep inside rail-specific options) | **stub as id-only string** | `mandate` is the authorization record for recurring/future-off-session charges on mandate-based rails (SEPA, ACSS, etc.) — real and important for those specific rails, but a full model requires per-rail mandate-acceptance semantics that's disproportionate for a slice whose dominant rail (per the `payment_method_card`-detailed treatment elsewhere) is card. Keep the field so `setup_intent`'s shape is complete, but never resolve it to a real `mandate` object — just echo back a synthesized id. |
| `latest_attempt` (`anyOf[string\|ref:setup_attempt]`) | `setup_intent` | **stub as id-only string** | `setup_attempt` is effectively "the `charge`/PaymentIntent-confirmation-attempt equivalent for SetupIntents" — a real, separate resource in the full API with its own list endpoint, not named in §4's core/payments list. Modeling `setup_intent.status` transitions (in scope, subtopic 3's territory) does not require materializing every attempt as its own resource; stub the pointer. |

## `payment_method_details` / payment-rail sub-objects (the biggest single-field edge by field count)

Not a scope-boundary crossing in the Connect/Tax/Radar sense — every rail is nominally "core payments"
— but it's the edge project_overview.md's focus paragraph calls out by name, and it's the single
widest field in the entire schema survey: **`payment_method.{57 rail fields}`,
`payment_method_details.{61 rail fields}` (charge), `payment_intent_payment_method_options.{~50 rail
fields}`, `refund_destination_details.{~20 rail fields}`** — four separate 20-to-61-key polymorphic
unions, one member type per payment rail, each member its own multi-field schema (e.g.
`payment_method_card` alone has 14 properties: `brand, checks, country, display_brand, exp_month,
exp_year, fingerprint, funding, generated_from, last4, networks, regulated_status,
three_d_secure_usage, wallet`).

**Ruling: model `card` (and, cheaply, `us_bank_account` for ACH coverage) fully; stub every other rail
as `{type: "<rail>"}` with no further fields, or null the field entirely for rails never exercised by
fixtures.** Reasons:

- `card` is the dominant real-world rail and the one every "declined card" / dunning / 3DS scenario in
  project_overview.md §8's conformance scenario list depends on. It needs real fidelity: `brand`,
  `last4`, `exp_month`/`exp_year`, `funding`, `checks` (CVC/postal/address check results — this is
  where declined-card magic-number behavior surfaces, subtopic 4's territory), `wallet` (nullable,
  Apple Pay/Google Pay markers).
  - `us_bank_account` (ACH) is worth a second full model because it's the other rail with materially
    different timing behavior (`pending` status window, microdeposit verification) that a faithful
    payments world plausibly wants to exercise.
  - The remaining ~55 rails (`afterpay_clearpay`, `klarna`, `alipay`, `wechat_pay`, `paypal`,
    `sepa_debit`, `crypto`, `mb_way`, `naver_pay`, ... down to genuinely obscure ones like `satispay`,
    `payto`, `sunbit`) are individually thin (`payment_method_card`'s 14 properties are unusually
    rich; most rails are 2–6 properties, mostly IDs and country codes) and each one modeled fully buys
    almost nothing for the eval scenarios this project is built around. Stub them.
- This decision should be revisited only if a specific eval task needs a specific alternate rail (e.g.
  a "handle a SEPA mandate revocation" task) — at which point that one rail gets promoted from stub to
  model, deliberately, not as a blanket policy change.

## `smor_resource_managed_payments`

Found on `payment_intent.managed_payments` and `setup_intent.managed_payments`. This is a
single-property wrapper schema in `spec3.json` with no further documented shape visible at the survey
depth used here.

> **2026-09-18 update:** "SMOR" is now identified — **Stripe Merchant Of Record** — tied to Stripe's
> **Managed Payments** product (`docs.stripe.com/payments/managed-payments/how-it-works`, fetched
> directly), where Stripe's acquiring affiliates become the merchant of record for a seller's digital-
> goods transactions, handling global tax compliance. It does not appear in project_overview.md's
> in/out lists, so it is still out of this project's declared scope. See
> [gap-closure-2026-09-18.md](./gap-closure-2026-09-18.md#10-smor_resource_managed_payments--identified-low-stakes-timeboxed-as-instructed)
> item 10.

**Ruling: null** (unchanged). It's nullable, not required, and appears to be an emerging/preview
feature (its narrow 1-property shape and lack of presence in `stripe-mock`'s older fixtures — see
subtopic 4 — both suggest this); nulling it costs nothing, and while its product meaning is now known,
Managed Payments itself is not in this project's declared scope, so there's still no basis to model it
further.

## `customer.cash_balance` / `customer_cash_balance_transaction` (the Cash Balance product — distinct from `customer.balance` / `customer_balance_transaction`)

Not named in §4's in/out lists at all — a genuine gap in the scope statement this subtopic flags rather
than silently deciding. Cash Balance is a separate Stripe product (let customers fund a balance via
bank transfer, then draw down against it) with its own state machine (`cash_balance.available`/
`settings.reconciliation_mode`) and its own transaction ledger (`customer_cash_balance_transaction`,
16 properties, **not the same schema** as the in-scope `customer_balance_transaction`).

**Ruling: null `customer.cash_balance`; do not build `customer_cash_balance_transaction` or its 2
endpoints.** Reason: §4's Payments list names `balance_transactions`/`balance`/`payouts` (the platform
ledger) and Core names `customers` without qualification; Cash Balance is closer to a Connect-adjacent
funding mechanism than to either "core customer data" or "payments," and building it means a second,
easily-confused ledger next to the one that *is* in scope (see resource-inventory.md's warning about
the three similarly-named ledgers). If a future scenario needs "customer pays via bank transfer to a
balance," that's a deliberate scope addition to make explicitly, not something to grow by leaving the
field un-nulled.

## `test_helpers.test_clock`

Found on `customer`, `subscription`, `invoice`, `invoiceitem`, `subscription_schedule` — every
resource whose lifecycle time matters. **Not this subtopic's ruling to make** — project_overview.md
§12.3 and subtopic 4's plan own the test-clock design decision in full. Noted here only as a
schema-level fact: the field is a simple nullable `anyOf[string|ref:test_helpers.test_clock]` on each
of those five resources, so whatever subtopic 4/the architecture phase decides about test clocks, the
field itself is cheap to wire up structurally (it's just an id pointer) — the hard part is what
advancing a clock does to the attached resources' state, which is explicitly out of this subtopic's
lane.

## `event.data.object` — the whole-API polymorphism note

Not a single edge but a structural one worth flagging: `event.data.object` (`ref:notification_event_data`
→ effectively "any Stripe object") is, by construction, a hole through every scope boundary at once —
any event this world emits for an in-scope mutation will naturally only ever carry an in-scope object
snapshot, so this isn't a live risk, but it means **`event` cannot be schema-validated generically**
the way project_overview.md §6 wants for the other objects ("every object we return should validate
against its `spec3.json` schema") — its `data.object` validates against whatever the specific event
`type` implies, which is a per-type lookup, not a fixed schema. Flag for the testing/architecture phase.

---

## Summary table (per project_overview.md's requested format)

| Edge | Ruling |
|---|---|
| Connect (`account`, `application`, `application_fee*`, `transfer*`, `on_behalf_of`, `connect_collection_transfer`, `reserve_transaction`) | null (matches real non-Connect-account behavior) |
| `invoice.issuer` (Connect-shaped but required) | model minimally as `{type: "self"}` |
| Stripe Tax (`tax.*`, `tax_product_*`, `customer.tax`, `product.tax_code`) | null / stub id |
| `automatic_tax` (required on subscription+invoice) | model minimally as `{enabled: false}` |
| flat-rate tax application (`billing_bill_resource_invoicing_taxes_tax`) | model (in scope) |
| Radar (`review`, `radar_options`, `outcome.risk_*`) | null |
| `charge.outcome` non-Radar subset, `charge.fraud_details` | model |
| Checkout Sessions / Payment Links (`checkout.session`, related enum values) | null |
| Financial Connections / Issuing / Terminal / Treasury / Capital / Climate / Crypto | null |
| Legacy Sources/Cards/BankAccounts (`source`, `card`, `bank_account`, `default_source`, sub-resource paths) | null / cut paths entirely |
| `payout.destination` | stub as id-only string |
| `mandate`, `single_use_mandate`, `setup_attempt` | stub as id-only string |
| `payment_method_details`/`payment_method`/`payment_method_options`/`refund_destination_details` rail unions | model `card` (+ `us_bank_account`) fully; stub all other ~55 rails as `{type}` only |
| `smor_resource_managed_payments` | null (unidentifiable preview feature) |
| `customer.cash_balance`, `customer_cash_balance_transaction` | null / don't build (flagged scope gap) |
| `test_helpers.test_clock` pointers | structurally cheap; behavior deferred to subtopic 4 |
| `event.data.object` polymorphism | structural note for testing/architecture phase, not a per-edge ruling |
