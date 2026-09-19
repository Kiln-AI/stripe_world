# Resource Inventory

Source: `research/stripe-openapi/spec3.json` (Stripe API version **`2026-08-26.dahlia`**, 419 paths,
1454 schemas), queried with short Python scripts against `components.schemas` and `paths` — see the
scripts embedded as fenced blocks below each finding for reproducibility. All field lists, `required`
sets, `nullable` flags and enum values quoted here are read directly from the spec, not from memory or
docs.stripe.com prose.

This file is the exhaustive per-resource reference. For the "what do we actually build" answer, see
[`minimum-closed-set-and-tool-budget.md`](./minimum-closed-set-and-tool-budget.md). For the ruling on
every field that points outside the declared scope, see
[`scope-boundary-edges.md`](./scope-boundary-edges.md).

## How to read each entry

- **`object`** — the literal value of the schema's `object` discriminator field (from
  `properties.object.enum`).
- **Endpoints** — every `paths` entry whose path starts with the resource's root, with its HTTP
  methods, taken verbatim from `spec3.json`.
- **Fields** — every property in `components.schemas.<name>.properties`, with type, nullability
  (`nullable: true` in the schema) and whether it's in `required`. `ref:X` means the property is
  `$ref: '#/components/schemas/X'`; `anyOf[...]` means a polymorphic reference (Stripe's convention for
  "either an expanded object or its id string, or one of several object types"). Enum values are quoted
  in full where the schema declares `enum`; where a field is realistically enum-shaped but the schema
  types it as a bare `string` (Stripe leaves many status/reason fields open-ended for forward
  compatibility), the value set from the field's `description` is quoted instead and marked
  **(doc-only enum)**.
- **List filters** — every non-pagination query parameter on the collection `GET`.

---

## Core

### `customer` — object: `customer`

Endpoints (`/v1/customers*`): `GET /v1/customers`, `POST /v1/customers`, `GET /v1/customers/search`,
`GET /v1/customers/{customer}`, `POST /v1/customers/{customer}`, `DELETE /v1/customers/{customer}`,
plus 7 sub-resource path families (`balance_transactions`, `bank_accounts`, `cards`, `cash_balance`,
`cash_balance_transactions`, `discount`, `funding_instructions`, `payment_methods`, `sources`,
`subscriptions`, `tax_ids`) — **47 operations total** under `/v1/customers` (see
[minimum-closed-set-and-tool-budget.md](./minimum-closed-set-and-tool-budget.md) for the full
breakdown; most of these sub-resources are ruled out of scope in
[scope-boundary-edges.md](./scope-boundary-edges.md)).

`required`: `created, id, livemode, object` (31 properties total). Selected fields:

| Field | Type | Nullable | Notes |
|---|---|---|---|
| `id` | string | REQ | |
| `balance` | integer | not nullable | account balance in minor units; **not** the same object as `cash_balance` — see scope-boundary-edges.md |
| `cash_balance` | `ref:cash_balance` | nullable | out of scope, see edges doc |
| `currency` | string | nullable | |
| `default_source` | `anyOf[string \| bank_account \| card \| source]` | nullable | legacy Sources API polymorphism — out of scope, see edges doc |
| `delinquent` | boolean | nullable | |
| `discount` | `ref:discount` | nullable | in scope |
| `email` | string | nullable | |
| `invoice_prefix` / `invoice_settings` / `next_invoice_sequence` | string / `ref:invoice_setting_customer_setting` / integer | mixed | |
| `metadata` | object(map) | not nullable | |
| `shipping` | `anyOf[ref:shipping]` | nullable | |
| `sources` | inline list object (4 props: `object`, `data`, `has_more`, `url`) | not nullable | legacy — see edges doc |
| `subscriptions` | inline list object | not nullable | |
| `tax` | `ref:customer_tax` | not nullable | Stripe Tax integration — out of scope |
| `tax_exempt` | `string(enum: exempt, none, reverse)` | nullable | in scope (cheap to model) |
| `tax_ids` | inline list object | not nullable | out of scope, see edges doc |
| `test_clock` | `anyOf[string \| test_helpers.test_clock]` | nullable | subtopic 4's territory |

List filters (`GET /v1/customers`): `created` (range object), `email`, `test_clock`. (Plus standard
`ending_before`, `expand`, `limit`, `starting_after` on every list endpoint — cross-cutting, not
repeated per resource here; see subtopic 2.)

### `payment_method` — object: `payment_method`

Endpoints: `GET /v1/payment_methods`, `POST /v1/payment_methods`, `GET /v1/payment_methods/{pm}`,
`POST /v1/payment_methods/{pm}`, `POST /v1/payment_methods/{pm}/attach`,
`POST /v1/payment_methods/{pm}/detach`. Also listed under
`GET /v1/customers/{customer}/payment_methods[/{payment_method}]` (read-only alias). **6 operations.**

`required`: `billing_details, created, id, livemode, object, type` (68 properties total — by far the
widest "core" schema). The bulk of the width is **57 mutually-exclusive payment-rail sub-objects**,
one per value of `type`: `acss_debit, affirm, afterpay_clearpay, alipay, alma, amazon_pay,
au_becs_debit, bacs_debit, bancontact, billie, bizum, blik, boleto, card, card_present, cashapp,
crypto, custom, customer_balance, eps, fpx, giropay, grabpay, ideal, interac_present, kakao_pay,
klarna, konbini, kr_card, link, mb_way, mobilepay, multibanco, naver_pay, nz_bank_account, oxxo, p24,
pay_by_bank, payco, paynow, paypal, payto, pix, promptpay, revolut_pay, samsung_pay, satispay,
scalapay, sepa_debit, sofort, sunbit, swish, twint, upi, us_bank_account, wechat_pay, zip` (full `type`
enum, 57 values — quoted verbatim from `payment_method.properties.type.enum`). Each rail field is
`ref:payment_method_<rail>`, a schema of its own (e.g. `payment_method_card` has 14 properties:
`brand, checks, country, display_brand, exp_month, exp_year, fingerprint, funding, generated_from,
last4, networks, regulated_status, three_d_secure_usage, wallet`).

Other top-level fields: `billing_details` (`ref:billing_details`, REQ), `customer`
(`anyOf[string|customer]`, nullable), `metadata` (nullable object map — note: nullable here, unlike
most other resources' `metadata` which is a non-nullable map defaulting to `{}`), `allow_redisplay`
(`enum: always, limited, unspecified`), `radar_options` (`ref:radar_radar_options` — Radar, out of
scope).

List filters: `allow_redisplay`, `customer`, `type` (57-value enum above).

**Recommendation:** model `card` fully (the dominant real-world rail); stub the other 56 rail sub-objects
minimally or omit — see scope-boundary-edges.md, "`payment_method_details` / payment-rail sub-objects".

### `product` — object: `product`

Endpoints: `GET/POST /v1/products`, `GET /v1/products/search`, `GET/POST/DELETE /v1/products/{id}`,
plus `/v1/products/{product}/features` (list/create) and `/{id}` (delete) — a `product_feature`
sub-resource for marketing bullet points. **10 operations** (6 core + 4 features, features are a cut
candidate).

`required`: `active, created, id, images, livemode, marketing_features, metadata, name, object,
updated` (18 properties). Fields: `active` (bool), `default_price` (`anyOf[string|price]`, nullable),
`description` (nullable string), `images` (`array<string>`, REQ), `marketing_features`
(`array<ref:product_marketing_feature>`, REQ), `metadata` (REQ map), `name` (REQ string),
`package_dimensions` (nullable), `shippable` (nullable bool), `statement_descriptor` (nullable),
`tax_code` (`anyOf[string|tax_code]`, nullable — Stripe Tax product classification, out of scope),
`unit_label` (nullable), `url` (nullable).

List filters: `active`, `created`, `ids` (array, ≤ unspecified limit, "cannot be used with
`starting_after`"), `shippable`, `url`.

### `price` — object: `price`

Endpoints: `GET/POST /v1/prices`, `GET /v1/prices/search`, `GET/POST /v1/prices/{price}`. No delete —
prices are archived via `active: false`, never deleted. **5 operations.**

`required`: `active, billing_scheme, created, currency, id, livemode, metadata, object, product, type`
(21 properties). Fields: `billing_scheme` (`enum: per_unit, tiered`), `currency` (REQ, `format:
currency`), `currency_options` (map — per-currency price variants), `custom_unit_amount` (nullable),
`lookup_key` (nullable string, unique secondary key), `nickname` (nullable), `product`
(`anyOf[string|product|deleted_product]`, REQ), `recurring` (`anyOf[ref:recurring]`, nullable — present
only for `type: recurring`; this embedded object carries `interval`, `interval_count`,
`usage_type`, `aggregate_usage`, `trial_period_days`, `meter`), `tax_behavior` (`enum: exclusive,
inclusive, unspecified`), `tiers` (`array<ref:price_tier>`, for `billing_scheme: tiered`), `tiers_mode`
(`enum: graduated, volume`), `transform_quantity` (nullable), `type` (`enum: one_time, recurring`),
`unit_amount` (nullable integer, minor units), `unit_amount_decimal` (nullable string — sub-cent
precision as a decimal string).

List filters: `active`, `created`, `currency`, `lookup_keys` (array, ≤10), `product`, `recurring`
(object filter), `type`.

### `coupon` — object: `coupon`

Endpoints: `GET/POST /v1/coupons`, `GET/POST/DELETE /v1/coupons/{coupon}`. **5 operations.**

`required`: `created, duration, id, livemode, object, times_redeemed, valid` (17 properties). Fields:
`amount_off` (nullable integer, minor units — mutually exclusive with `percent_off`), `applies_to`
(`ref:coupon_applies_to` — restricts to specific product ids), `currency` (nullable, required only when
`amount_off` set), `currency_options` (map), `duration` (`enum: forever, once, repeating`, REQ),
`duration_in_months` (nullable, only for `repeating`), `max_redemptions` (nullable), `name` (nullable),
`percent_off` (nullable number), `redeem_by` (nullable unix-time), `times_redeemed` (REQ integer),
`valid` (REQ boolean — computed: false once expired/exhausted).

List filters: `created` only (no `active`-style filter; a coupon's validity must be read off `valid`,
`redeem_by` and `times_redeemed`/`max_redemptions` client-side).

### `promotion_code` — object: `promotion_code`

Endpoints: `GET/POST /v1/promotion_codes`, `GET/POST /v1/promotion_codes/{promotion_code}`. No delete —
deactivate via `active: false`. **4 operations.**

`required`: `active, code, created, id, livemode, object, promotion, restrictions, times_redeemed` (14
properties). Fields: `code` (REQ string, the human-facing redemption code, unique per customer scope),
`customer` (`anyOf[string|customer|deleted_customer]`, nullable — restricts redemption to one
customer), `expires_at` (nullable), `max_redemptions` (nullable), `promotion`
(`ref:promotion_codes_resource_promotion` REQ — wraps the underlying `coupon`), `restrictions`
(`ref:promotion_codes_resource_restrictions` REQ — `first_time_transaction`, `minimum_amount`,
`minimum_amount_currency`).

List filters: `active`, `code`, `coupon`, `created`, `customer`.

### `tax_rate` — object: `tax_rate`

Endpoints: `GET/POST /v1/tax_rates`, `GET/POST /v1/tax_rates/{tax_rate}`. No delete — archive via
`active: false`. **4 operations.** This is the **flat-rate** tax primitive explicitly kept in scope
(project_overview.md §4); Stripe Tax's automatic-calculation product (`tax.calculation`,
`tax.transaction`, `tax.registration`, `tax.settings`) is a separate, out-of-scope resource family —
see scope-boundary-edges.md.

`required`: `active, created, display_name, id, inclusive, livemode, object, percentage` (18
properties). Fields: `country` (nullable, ISO 3166), `display_name` (REQ), `effective_percentage`
(nullable number — differs from `percentage` when `flat_amount` combines with a percentage),
`flat_amount` (`anyOf[ref:tax_rate_flat_amount]`, nullable), `inclusive` (REQ bool), `jurisdiction`
(nullable), `jurisdiction_level` (`enum: city, country, county, district, multiple, state`),
`percentage` (REQ number), `rate_type` (`enum: flat_amount, percentage`), `state` (nullable),
`tax_type` (`enum`, 16 values: `amusement_tax, communications_tax, gst, hst, igst, jct, lease_tax,
mass_transit_parking_tax, parking_tax, pst, qst, retail_delivery_fee, rst, sales_tax, service_tax,
vat`).

List filters: `active`, `created`, `inclusive`.

### `discount` (embedded resource, no top-level CRUD) — object: `discount`

Not independently listable/creatable; it's produced as a side effect of applying a coupon or
promotion code to a customer/subscription/subscription-item/invoice-item, and read/deleted via
`GET/DELETE /v1/customers/{customer}/discount` and
`GET/DELETE /v1/subscriptions/{subscription_exposed_id}/discount`. `required`: `id, object, source,
start` (13 properties). `source` is `ref:discount_source` (a coupon or a `stackable_discount`
reference). Referenced by `customer.discount`, `subscription.discounts[]`,
`subscription_item.discounts[]`, `invoiceitem.discounts[]`, `invoice.discounts[]`,
`line_item.discounts[]`.

---

## Payments

### `payment_intent` — object: `payment_intent`

Endpoints: `GET/POST /v1/payment_intents`, `GET /v1/payment_intents/search`,
`GET/POST /v1/payment_intents/{intent}`, plus 6 action sub-endpoints: `apply_customer_balance`,
`cancel`, `capture`, `confirm`, `increment_authorization`, `verify_microdeposits`, and a read-only
`amount_details_line_items`. **12 operations.**

`required`: `created, id, livemode, object, status` (45 properties). `status` enum (7 values, REQ):
`canceled, processing, requires_action, requires_capture, requires_confirmation,
requires_payment_method, succeeded`. Other fields: `amount` / `amount_capturable` / `amount_received`
(integers), `application` (`anyOf[string|application]`, nullable — Connect, out of scope),
`application_fee_amount` (nullable — Connect), `automatic_payment_methods`
(`ref:payment_flows_automatic_payment_methods_payment_intent`, nullable), `cancellation_reason`
(`enum: abandoned, automatic, duplicate, expired, failed_invoice, fraudulent,
requested_by_customer, void_invoice`), `capture_method` (`enum: automatic, automatic_async, manual`),
`client_secret` (nullable — the client-side confirmation secret), `confirmation_method` (`enum:
automatic, manual`), `currency` (`format: currency`), `customer`
(`anyOf[string|customer|deleted_customer]`, nullable), `last_payment_error`
(`anyOf[ref:api_errors]`, nullable — the error envelope embedded as a field, see subtopic 2),
`latest_charge` (`anyOf[string|charge]`, nullable), `metadata` (non-nullable map), `next_action`
(`anyOf[ref:payment_intent_next_action]`, nullable — 3DS/redirect flows, largely client-side, low
value to model deeply), `on_behalf_of` (`anyOf[string|account]`, nullable — Connect), `payment_method`
(`anyOf[string|payment_method]`, nullable), `payment_method_options`
(`anyOf[ref:payment_intent_payment_method_options]`, nullable — huge, one block per rail, mirrors
`payment_method_details`), `payment_method_types` (`array<string>`, non-nullable), `receipt_email`
(nullable), `review` (`anyOf[string|review]`, nullable — Radar, out of scope), `setup_future_usage`
(`enum: off_session, on_session`), `shipping` (`anyOf[ref:shipping]`, nullable), `transfer_data` /
`transfer_group` (Connect, out of scope).

List filters: `created`, `customer`. (Narrow — most filtering on PaymentIntents is expected to be done
via `search`, a Lucene-like query language over a separate `GET /v1/payment_intents/search` endpoint;
`search` semantics belong to subtopic 2, not documented further here.)

### `charge` — object: `charge`

Endpoints: `GET/POST /v1/charges`, `GET /v1/charges/search`, `GET/POST /v1/charges/{charge}`,
`POST /v1/charges/{charge}/capture`, plus dispute sub-paths (`GET/POST .../dispute`,
`POST .../dispute/close` — thin aliases for the `dispute` resource, not separate state) and refund
sub-paths (`POST .../refund`, `GET/POST .../refunds[/{refund}]` — aliases for the `refund` resource).
**14 operations** (many are aliases of `dispute`/`refund` endpoints reachable from the top level too).

`required`: `amount, amount_captured, amount_refunded, billing_details, captured, created, currency,
disputed, id, livemode, metadata, object, paid, refunded, status` (45 properties). `status` enum (REQ,
3 values): `failed, pending, succeeded`. Other notable fields: `application` /
`application_fee`/`application_fee_amount` (Connect, out of scope), `balance_transaction`
(`anyOf[string|balance_transaction]`, nullable — the direct link into the platform ledger),
`billing_details` (`ref:billing_details`, REQ), `calculated_statement_descriptor` (nullable),
`customer` (`anyOf[string|customer|deleted_customer]`, nullable), `disputed` (REQ bool),
`failure_balance_transaction` (nullable), `failure_code` / `failure_message` (nullable strings — the
decline reason; `failure_code` values are the same space as `decline_code` in the error envelope,
subtopic 2's territory), `fraud_details` (`anyOf[ref:charge_fraud_details]`, nullable — Radar-adjacent
but this specific field is just a merchant-set flag, cheap to keep), `on_behalf_of` (Connect),
`outcome` (`anyOf[ref:charge_outcome]`, nullable — Radar risk scoring, out of scope), `payment_intent`
(`anyOf[string|payment_intent]`, nullable), `payment_method_details`
(`anyOf[ref:payment_method_details]`, nullable — 61-key rail union, same edge as `payment_method`
above), `receipt_email` / `receipt_number` / `receipt_url` (nullable), `refunds` (inline list object),
`review` (Radar, out of scope), `shipping` (nullable), `source_transfer` / `transfer` /
`transfer_data` / `transfer_group` (Connect, out of scope).

List filters: `created`, `customer`, `payment_intent`, `transfer_group`.

### `refund` — object: `refund`

Endpoints: `GET/POST /v1/refunds`, `GET/POST /v1/refunds/{refund}`, `POST /v1/refunds/{refund}/cancel`
(cancels a refund still `pending`, e.g. an ACH refund in flight — not a general "undo"). Also reachable
under `/v1/charges/{charge}/refunds*`. **5 operations at top level.**

`required`: `amount, created, currency, id, object` (25 properties — notably thinner `required` set
than charge/dispute; most refund fields are conditionally present). `status` is typed as a bare
`string`, **doc-only enum**: `pending, requires_action, succeeded, failed, canceled` (from the field
description). `reason` (typed enum, REQ-optional): `duplicate, expired_uncaptured_charge, fraudulent,
requested_by_customer`. Other fields: `balance_transaction` (nullable), `charge`
(`anyOf[string|charge]`, nullable), `customer` (nullable), `destination_details`
(`ref:refund_destination_details` — per-rail refund routing detail, mirrors `payment_method_details`
again), `failure_balance_transaction` / `failure_reason` (nullable — populated when a refund itself
fails, e.g. destination account closed), `instructions_email` (nullable — for refund methods needing
manual customer action), `next_action` (`ref:refund_next_action`), `payment_intent`
(`anyOf[string|payment_intent]`, nullable), `payment_method` (`anyOf[string|payment_method]`,
nullable), `pending_reason` (`enum: charge_pending, insufficient_funds, processing`),
`source_transfer_reversal` / `transfer_reversal` (Connect, out of scope).

List filters: `charge`, `created`, `payment_intent`.

### `dispute` — object: `dispute`

Endpoints: `GET /v1/disputes`, `GET/POST /v1/disputes/{dispute}`, `POST /v1/disputes/{dispute}/close`.
No top-level create — disputes are created by the card network/issuing bank, never by the merchant
(only test-mode simulation, subtopic 4's territory). **4 operations.**

`required`: `amount, balance_transactions, charge, created, currency, enhanced_eligibility_types,
evidence, evidence_details, id, is_charge_refundable, livemode, metadata, object, reason, status` (17
properties). `status` enum (REQ, 8 values): `lost, needs_response, prevented, under_review,
warning_closed, warning_needs_response, warning_under_review, won`. `reason` is typed as a bare
`string`, **doc-only enum** (15 values, from description): `bank_cannot_process, check_returned,
credit_not_processed, customer_initiated, debit_not_authorized, duplicate, fraudulent, general,
incorrect_account_details, insufficient_funds, noncompliant, product_not_received,
product_unacceptable, subscription_canceled, unrecognized`. `balance_transactions`
(`array<ref:balance_transaction>`, REQ — the withdrawal-and-possible-return ledger entries, directly
relevant to subtopic 3's ledger effects). `evidence` (`ref:dispute_evidence`, REQ, 28 properties — all
the submittable evidence fields: `access_activity_log, billing_address, cancellation_policy, ...,
uncategorized_text`; every one is a nullable string or file-id string). `evidence_details`
(`ref:dispute_evidence_details`, REQ, 5 properties: `due_by, enhanced_eligibility, has_evidence,
past_due, submission_count`). `payment_method_details` (`ref:dispute_payment_method_details`, nullable
— thin, currently only a `card` variant with `case_type`, `network_reason_code`).

List filters: `charge`, `created`, `payment_intent`.

### `setup_intent` — object: `setup_intent`

Endpoints: `GET/POST /v1/setup_intents`, `GET/POST /v1/setup_intents/{intent}`,
`POST /v1/setup_intents/{intent}/cancel`, `POST .../confirm`, `POST .../verify_microdeposits`. **7
operations.**

`required`: `created, id, livemode, object, payment_method_types, status, usage` (29 properties).
`status` enum (REQ, 6 values): `canceled, processing, requires_action, requires_confirmation,
requires_payment_method, succeeded`. `usage` is typed as a bare `string`, **doc-only** default
`off_session` — the description states the allowed intent is `on_session` or `off_session` but does not
enumerate it as a closed set (forward-compatible). Other fields largely mirror `payment_intent`:
`application`, `cancellation_reason` (`enum: abandoned, duplicate, requested_by_customer`), `customer`
(nullable), `last_setup_error` (`anyOf[ref:api_errors]`), `latest_attempt`
(`anyOf[string|setup_attempt]`, nullable — `setup_attempt` is a related but separate resource, not
independently in scope, see edges doc), `mandate` / `single_use_mandate`
(`anyOf[string|mandate]`, nullable — out of scope, see edges doc), `next_action`, `on_behalf_of`
(Connect), `payment_method`, `payment_method_options`.

List filters: `attach_to_self`, `created`, `customer`, `payment_method`.

### `balance_transaction` — object: `balance_transaction` (the **platform** ledger; do not confuse with `customer_balance_transaction` below)

Endpoints: `GET /v1/balance_transactions`, `GET /v1/balance_transactions/{id}`. Read-only — every row
is created as a side effect of some other operation (a charge, refund, payout, dispute, etc.), never
directly. **2 operations.**

`required`: `amount, available_on, balance_type, created, currency, fee, fee_details, id, net, object,
reporting_category, status, type` (16 properties). `balance_type` enum (REQ, 4 values): `issuing,
payments, refund_and_dispute_prefunding, risk_reserved` — **`issuing` is out of scope** per project
scope but appears as a possible value here since it's the same ledger object type across products;
in-scope activity will only ever produce `payments`-type rows. `type` enum (REQ, **50 values**, quoted
in full — see raw dump in this directory's scratch notes, key in-scope subset: `charge, payment,
payment_refund, payment_reversal, payment_failure_refund, refund, refund_failure, payout,
payout_cancel, payout_failure, adjustment, stripe_fee, tax_fund` — the remaining ~37 values are
Connect/Issuing/Treasury/Topups/Climate-specific and will never be emitted by this world). `status` is
typed as a bare `string`, **doc-only enum**: `available, pending` (matches the two-bucket balance
model). `reporting_category` is a bare `string` with no enumerated or documented closed set in the
spec (the field description just links to a docs page); treat as an open string, not an enum, in the
schema — a real accounting-category value list would have to come from that docs page (out of scope
for this subtopic; flagged as a gap). `source` — `anyOf[string | application_fee | charge |
connect_collection_transfer | customer_cash_balance_transaction | dispute | fee_refund |
issuing.authorization | issuing.dispute | issuing.transaction | payout | refund | reserve_transaction |
tax_deducted_at_source | topup | transfer | transfer_reversal]` — a 16-way polymorphic reference; only
`charge`, `dispute`, `payout`, `refund` are in scope, the rest are Connect/Issuing/Tax/Topup objects —
see scope-boundary-edges.md. `fee_details` (`array<ref:fee>` — a `{amount, application, currency,
description, type}` breakdown; `application` here is again a Connect edge).

List filters: `created`, `currency`, `payout`, `source`, `type`.

### `payout` — object: `payout`

Endpoints: `GET/POST /v1/payouts`, `GET/POST /v1/payouts/{payout}`, `POST /v1/payouts/{payout}/cancel`,
`POST /v1/payouts/{payout}/reverse`. **6 operations.**

`required`: `amount, arrival_date, automatic, created, currency, id, livemode, method,
object, reconciliation_status, source_type, status, type` (27 properties). `method` and `status` and
`source_type` are all typed as bare `string` with **doc-only enums**: `method`: `standard, instant`;
`status`: `paid, pending, in_transit, canceled, failed`; `source_type`: `card, fpx, bank_account`.
`type` (typed enum, REQ): `bank_account, card`. `reconciliation_status` (typed enum, REQ): `completed,
in_progress, not_applicable`. `destination` — `anyOf[string | bank_account | card | deleted_bank_account
| deleted_card]` — the legacy external-account objects, out of scope (see edges doc); a faithful world
still needs *some* representation of "where the money went," so the recommendation there is to stub
this as an opaque id/string rather than model `bank_account`/`card` as full external-account resources.
`balance_transaction` / `failure_balance_transaction` (`anyOf[string|balance_transaction]`, nullable).
`original_payout` / `reversed_by` (`anyOf[string|payout]`, nullable — self-referential, needed for the
reversal flow subtopic 3 documents).

List filters: `arrival_date`, `created`, `destination`, `status`.

### `balance` — object: `balance` (singleton, no id, not independently listable)

Endpoint: `GET /v1/balance`. **1 operation** (the deprecated `GET /v1/balance/history[/{id}]` alias
just re-exposes `balance_transaction` list/retrieve and is not a separate resource — it predates
`/v1/balance_transactions` and is excluded from `spec3.sdk.json`, see api-versions doc).

`required`: `available, livemode, object, pending` (8 properties). `available` / `pending` — both
`array<ref:balance_amount>` (a `{amount, currency, source_types}` breakdown per currency) — REQ. This
is the two numbers that matter for fidelity: available vs pending balance, and the delay between them
(`available_on` on individual `balance_transaction` rows) is subtopic 3's territory.
`connect_reserved` / `instant_available` (Connect), `issuing` (`ref:balance_detail`, out of scope).

**No list filters — this is a derived/computed view, not a table.** It should be computed at read time
as a sum over `balance_transaction` rows partitioned by `available_on <= now`, not stored as its own
row. This is the first concrete instance of the "table vs. view" distinction that matters for the
15–20 table budget — see minimum-closed-set-and-tool-budget.md.

### `customer_balance_transaction` — object: `customer_balance_transaction`

**Distinct from `balance_transaction` above** — this is the ledger of changes to a single customer's
`customer.balance` integer (an invoicing credit/debit balance denominated per-customer, not the
platform's own funds), *not* the platform-wide Stripe balance. Also distinct from
`customer_cash_balance_transaction` (a third, still-different ledger for the separate "Cash Balance"
product tied to `customer.cash_balance` — out of scope, see edges doc). All three are easy to conflate
by name; only this one is in the closed set, because `invoice.starting_balance` /
`invoice.ending_balance` and credit-note-to-balance application depend on it.

Endpoints: `GET/POST /v1/customers/{customer}/balance_transactions[/{transaction}]`. **4 operations**
(nested under `customer`, no top-level path).

`required`: `amount, created, currency, customer, ending_balance, id, livemode, object, type` (15
properties). `type` enum (REQ, 11 values): `adjustment, applied_to_invoice,
checkout_session_subscription_payment, checkout_session_subscription_payment_canceled, credit_note,
initial, invoice_overpaid, invoice_too_large, invoice_too_small, migration, unapplied_from_invoice,
unspent_receiver_credit`. `credit_note` (`anyOf[string|credit_note]`, nullable), `invoice`
(`anyOf[string|invoice]`, nullable), `checkout_session` (out of scope, stub as string).

---

## Billing

### `subscription` — object: `subscription`

Endpoints: `GET/POST /v1/subscriptions`, `GET /v1/subscriptions/search`,
`GET/POST/DELETE /v1/subscriptions/{subscription_exposed_id}`,
`DELETE .../subscriptions/{id}/discount`, `POST /v1/subscriptions/{subscription}/migrate`,
`POST /v1/subscriptions/{subscription}/resume`. Also under
`/v1/customers/{customer}/subscriptions*` (a legacy alias path family, excluded from
`spec3.sdk.json`). **9 operations at top level.**

`required`: `automatic_tax, billing_cycle_anchor, billing_mode, billing_schedules,
cancel_at_period_end, collection_method, created, currency, customer, discounts, id, invoice_settings,
items, livemode, metadata, object, start_date, status` (48 properties — the widest "core billing"
object). `status` enum (REQ, 8 values, exactly the set project_overview.md §5 names): `active,
canceled, incomplete, incomplete_expired, past_due, paused, trialing, unpaid`. `collection_method`
(`enum: charge_automatically, send_invoice`, REQ). Key fields: `billing_cycle_anchor` (unix-time, REQ),
`billing_cycle_anchor_config` (nullable — day/hour/minute/month/second anchor override),
`cancel_at` / `cancel_at_period_end` / `canceled_at` / `cancellation_details` (the cancel-timing
fields subtopic 3 documents in depth), `days_until_due` (nullable, only for `send_invoice`),
`default_payment_method` (`anyOf[string|payment_method]`, nullable), `default_tax_rates`
(`array<ref:tax_rate>`, nullable), `discounts` (`array<anyOf[string|discount]>`, REQ — note: **plural**
`discounts`, superseding the older singular `discount` field which still exists on `customer` and
`invoice`), `items` (inline list object, REQ — the subscription's line items, see `subscription_item`
below), `latest_invoice` (`anyOf[string|invoice]`, nullable), `pause_collection`
(`anyOf[ref:subscriptions_resource_pause_collection]`, nullable), `pending_setup_intent`
(`anyOf[string|setup_intent]`, nullable), `pending_update`
(`anyOf[ref:subscriptions_resource_pending_update]`, nullable — a staged-but-unconfirmed change),
`schedule` (`anyOf[string|subscription_schedule]`, nullable), `test_clock`
(subtopic 4), `transfer_data` (Connect), `trial_end` / `trial_start` (nullable unix-time),
`trial_settings` (`anyOf[ref:subscriptions_resource_trial_settings_trial_settings]`, nullable —
`end_behavior.missing_payment_method`). `application` / `on_behalf_of` — Connect, out of scope.

Note: `current_period_start`/`current_period_end` are **not** top-level fields on `subscription`
itself in this API version — they live per-item on `subscription_item` (see below), reflecting
Stripe's move to multi-price-per-subscription billing periods. A world design that puts a single
current-period pair on the subscription row is drifting from the real object shape.

List filters: `automatic_tax`, `collection_method`, `created`, `current_period_end`,
`current_period_start`, `customer`, `price`, `status` (`enum: active, all, canceled, ended, incomplete,
incomplete_expired, past_due, paused, trialing, unpaid` — note `all`/`ended` are query-only values, not
possible `status` field values), `test_clock`.

### `subscription_item` — object: `subscription_item`

Endpoints: `GET/POST /v1/subscription_items`, `GET/POST/DELETE /v1/subscription_items/{item}`. **5
operations.**

`required`: `created, current_period_end, current_period_start, discounts, id, metadata, object, price,
subscription` (13 properties). `current_period_end` / `current_period_start` (unix-time, REQ — see
note above: this is where the billing period actually lives). `billed_until` (unix-time, not REQ, not
nullable per schema despite the name suggesting optionality — worth a conformance test). `discounts`
(`array<anyOf[string|discount]>`, REQ). `price` (`ref:price`, REQ — always the full object, never just
an id, on this resource specifically). `quantity` (integer, not REQ — absent/irrelevant for
metered/licensed-without-quantity prices). `tax_rates` (`array<ref:tax_rate>`, nullable).

List filters: `subscription` (**required** query param — this list endpoint cannot be called without a
subscription id; there is no global subscription-item listing).

### `invoice` — object: `invoice`

Endpoints: `GET/POST /v1/invoices`, `GET /v1/invoices/search`, `POST /v1/invoices/create_preview`,
`GET/POST/DELETE /v1/invoices/{invoice}` (delete only allowed while `status: draft`), plus 8 action
sub-endpoints: `add_lines`, `attach_payment`, `finalize`, `mark_uncollectible`, `pay`, `remove_lines`,
`send`, `void`, and line-item sub-paths `GET .../lines`, `POST .../lines/{line_item_id}`,
`POST .../update_lines`. **18 operations** — the single largest operation count of any in-scope
resource after `customer`.

`required`: `amount_due, amount_overpaid, amount_paid, amount_paid_off_stripe, amount_remaining,
amount_shipping, attempt_count, attempted, auto_advance, automatic_tax, collection_method, created,
currency, customer, default_tax_rates, discounts, id, issuer, lines, livemode, object,
payment_settings, period_end, period_start, post_payment_credit_notes_amount,
pre_payment_credit_notes_amount, starting_balance, status_transitions, subtotal, total` (**78
properties — the widest schema in the entire in-scope set**). `status` is typed as a **nullable** bare
`string`, but the description enumerates a closed set matching project_overview.md §5 exactly: `draft,
open, paid, uncollectible, void`. `billing_reason` (typed enum, 9 values):
`automatic_pending_invoice_item_invoice, manual, quote_accept, subscription, subscription_create,
subscription_cycle, subscription_threshold, subscription_update, upcoming`. `collection_method`
(`enum: charge_automatically, send_invoice`, REQ). Money fields worth calling out precisely since
subtopic 3 will build proration/dunning arithmetic on them: `amount_due`, `amount_paid`,
`amount_paid_off_stripe`, `amount_remaining`, `amount_overpaid`, `amount_shipping`, `ending_balance`
(nullable), `starting_balance` (REQ), `subtotal`, `subtotal_excluding_tax` (nullable), `total`,
`total_excluding_tax` (nullable), `total_discount_amounts` (nullable array), `total_taxes` (nullable
array), `total_pretax_credit_amounts` (nullable array), `post_payment_credit_notes_amount` /
`pre_payment_credit_notes_amount` (REQ integers). Structural fields: `auto_advance` (REQ bool),
`automatically_finalizes_at` (nullable unix-time), `confirmation_secret`
(`anyOf[ref:invoices_resource_confirmation_secret]`, nullable), `due_date` (nullable), `effective_at`
(nullable), `issuer` (`ref:connect_account_reference`, REQ — Connect-shaped even for non-Connect
invoices; degenerates to `{type: "self"}` when unused), `last_finalization_error`
(`anyOf[ref:api_errors]`, nullable), `latest_revision` (`anyOf[string|invoice]`, nullable — invoice
revisions are a distinct mechanism from credit notes), `lines` (inline list object wrapping
`line_item`, REQ), `next_payment_attempt` (nullable), `number` (nullable — assigned at finalization),
`parent` (`anyOf[ref:billing_bill_resource_invoicing_parents_invoice_parent]`, nullable — which
subscription/subscription-schedule/quote produced this invoice), `payments` (inline list object — see
`invoice_payment` in scope-boundary-edges.md), `payment_settings` (`ref:invoices_payment_settings`,
REQ), `rendering` / `hosted_invoice_url` / `invoice_pdf` (customer-facing presentation, low value to
model deeply), `status_transitions` (`ref:invoices_resource_status_transitions`, REQ — the four
timestamp fields `finalized_at`, `marked_uncollectible_at`, `paid_at`, `voided_at`; this is the
authoritative source for "when did draft become open" that subtopic 3 needs), `test_clock` (subtopic
4), `threshold_reason` (billing-thresholds invoicing, not REQ).

List filters: `collection_method`, `created`, `customer`, `due_date`, `status`, `subscription`.

### `invoiceitem` — object: `invoiceitem`

Endpoints: `GET/POST /v1/invoiceitems`, `GET/POST/DELETE /v1/invoiceitems/{invoiceitem}` (delete only
while not yet attached to a finalized invoice). **5 operations.** This is the pre-invoice "pending
line item" resource — a one-off charge line or a subscription proration line waiting to be swept into
the next invoice. Not to be confused with `line_item` (below), which is the frozen, post-finalization
line as it appears on an actual invoice.

`required`: `amount, currency, customer, date, discountable, id, livemode, object, period, proration,
quantity, quantity_decimal` (24 properties). `discountable` (REQ bool). `discounts`
(`array<anyOf[string|discount]>`, nullable). `frozen_fields` (`array<enum: discounts, pricing,
quantity>`, not REQ — marks which fields were locked at proration time). `invoice`
(`anyOf[string|invoice]`, nullable — null while still pending). `parent`
(`anyOf[ref:billing_bill_resource_invoice_item_parents_invoice_item_parent]`, nullable). `period`
(`ref:invoice_line_item_period`, REQ — a `{start, end}` pair; for a proration line this is the
unused/remaining-time window subtopic 3 needs). `pricing`
(`anyOf[ref:billing_bill_resource_invoicing_pricing_pricing]`, nullable — newer alternative to bare
`unit_amount`, wraps a `price_details` reference). `proration` (REQ bool — true for
machine-generated proration lines). `proration_details` (`ref:proration_details`, not REQ — carries
`credited_items` back-references). `quantity` / `quantity_decimal` (REQ, both — integer and decimal
string forms coexist). `tax_rates` (`array<ref:tax_rate>`, nullable).

List filters: `customer`, `created`, `invoice`, `pending` (boolean — filters to items not yet attached
to any invoice).

### `line_item` — object: `line_item` (read-only, embedded under `invoice.lines` and `GET /v1/invoices/{invoice}/lines`)

No independent create/update/delete path of its own outside the invoice line-management actions
(`add_lines`/`update_lines`/`remove_lines` on `invoice`, and
`POST /v1/invoices/{invoice}/lines/{line_item_id}` for a single-line update). This is the frozen
snapshot line as it appears on a specific invoice, produced from a `subscription_item` (recurring) or
an `invoiceitem` (one-off/proration) at finalization time.

`required`: `amount, currency, discountable, discounts, id, livemode, metadata, object, period,
subtotal` (20 properties). `discount_amounts` (`array<ref:discounts_resource_discount_amount>`,
nullable). `parent`
(`anyOf[ref:billing_bill_resource_invoicing_lines_parents_invoice_line_item_parent]`, nullable — a
tagged union back to either the originating `invoiceitem` or `subscription_item`). `period`
(`ref:invoice_line_item_period`, REQ). `pretax_credit_amounts`
(`array<ref:invoices_resource_pretax_credit_amount>`, nullable). `pricing`
(`anyOf[ref:billing_bill_resource_invoicing_pricing_pricing]`, nullable). `quantity` /
`quantity_decimal` (nullable — absent for some line types e.g. flat one-off amounts). `subscription`
(`anyOf[string|subscription]`, nullable). `taxes`
(`array<ref:billing_bill_resource_invoicing_taxes_tax>`, nullable — per-line tax-rate application,
references `tax_rate` transitively).

**Design note:** because this object is entirely derived (never independently mutated except through
the parent invoice's line-management actions) and always accessed nested under an invoice, it is a
strong candidate to store as a JSON array on the `invoice` row rather than its own SQL table — see
minimum-closed-set-and-tool-budget.md.

### `credit_note` — object: `credit_note`

Endpoints: `GET/POST /v1/credit_notes`, `GET /v1/credit_notes/preview`,
`GET /v1/credit_notes/preview/lines`, `GET /v1/credit_notes/{credit_note}/lines`,
`GET/POST /v1/credit_notes/{id}`, `POST /v1/credit_notes/{id}/void`. **8 operations.** No delete —
credit notes are immutable once issued; `void` is the only state transition and only for `status:
issued` notes of `type: post_payment` with no refund attached (subtopic 3's territory for the full
rule).

`required`: `amount, amount_shipping, created, currency, customer, discount_amount, discount_amounts,
id, invoice, lines, livemode, number, object, pdf, post_payment_amount, pre_payment_amount,
pretax_credit_amounts, refunds, status, subtotal, total, type` (34 properties). `status` (`enum:
issued, void`, REQ). `type` (`enum: mixed, post_payment, pre_payment`, REQ — whether the credit
applies to an already-paid invoice, an unpaid one, or both). `invoice` (`anyOf[string|invoice]`, REQ —
every credit note belongs to exactly one invoice). `customer_balance_transaction`
(`anyOf[string|customer_balance_transaction]`, nullable — the closure back to the customer ledger
object above, when the credit is applied as account credit rather than refunded). `out_of_band_amount`
(nullable integer — credit issued for a payment collected outside Stripe). `reason` (`enum: duplicate,
fraudulent, order_change, product_unsatisfactory`, nullable). `refunds`
(`array<ref:credit_note_refund>`, REQ — each a thin `{amount, refund}` pointer, not a full refund
object). `voided_at` (nullable unix-time).

List filters: `created`, `customer`, `invoice`.

### `credit_note_line_item` — object: `credit_note_line_item` (read-only, embedded under `credit_note.lines`)

`required`: `amount, discount_amount, discount_amounts, id, livemode, object, pretax_credit_amounts,
tax_rates, type` (16 properties). `type` (`enum: custom_line_item, invoice_line_item`, REQ —
whether this credit line mirrors an existing invoice `line_item` (`invoice_line_item`, with
`invoice_line_item` set to that id) or is a free-form custom amount). Same "derived, store as JSON on
the parent" recommendation as `line_item`.

### `subscription_schedule` — object: `subscription_schedule` (**conditional** — see budget doc)

Endpoints: `GET/POST /v1/subscription_schedules`, `GET/POST /v1/subscription_schedules/{schedule}`,
`POST .../cancel`, `POST .../release`. **6 operations.**

`required`: `billing_mode, created, customer, default_settings, end_behavior, id, livemode, object,
phases, status` (20 properties). `status` (`enum: active, canceled, completed, not_started, released`,
REQ). `end_behavior` (`enum: cancel, none, release, renew`, REQ). `phases`
(`array<ref:subscription_schedule_phase_configuration>`, REQ — each phase is itself a substantial
nested schema carrying its own `items`, `iterations`, `start_date`, `end_date`,
`trial`, `coupon`/`discounts`, `proration_behavior`, i.e. this resource embeds a large slice of
`subscription`'s create-time shape once per phase). `current_phase`
(`anyOf[ref:subscription_schedule_current_phase]`, nullable — `{start_date, end_date}` of the active
phase). `subscription` (`anyOf[string|subscription]`, nullable — the live subscription this schedule
drives, once started). `released_subscription` (nullable string).

List filters: `canceled_at`, `completed_at`, `created`, `customer`, `released_at`, `scheduled`
(boolean — not-yet-started schedules).

**Subtopic 3 owns the cost/benefit call on whether to build this** (project_overview.md §4 flags it as
conditional); the schema-level fact this subtopic contributes is that `phases[]` duplicates a large
fraction of `subscription`'s own field surface, so including it is not "one more small table" — it's
close to re-deriving subscription-creation semantics a second time inside a different object.

---

## Cross-cutting

### `event` — object: `event`

Endpoints: `GET /v1/events`, `GET /v1/events/{id}`. Read-only — every row is emitted by Stripe's own
state machine as a side effect of every other mutation. **2 operations.** project_overview.md §4
requires this object ("the `events` object") and explicitly scopes out webhook *delivery* — this world
models `event` as queryable state only.

`required`: `created, data, id, livemode, object, pending_webhooks, type` (11 properties). `type` is a
bare `string`, not an enum — the live Stripe event-type namespace (`customer.created`,
`invoice.payment_failed`, etc.) is open-ended and versioned separately from the object schema; the
closed set of event types this world will emit is a functional-spec decision, not something read off
`spec3.json` (there's no schema artifact enumerating "all event type strings" — cross-check
`stripe-python`'s constants or docs.stripe.com/api/events/types if that list is needed verbatim).
`data` (`ref:notification_event_data`, REQ — a two-key object, `object` (the full resource snapshot)
and `previous_attributes` (a partial diff, present only on `.updated` events)). `pending_webhooks`
(REQ integer — meaningless without webhook delivery; will always be `0` in this world, worth deciding
explicitly rather than leaving undefined). `request`
(`anyOf[ref:notification_event_request]`, nullable — `{id, idempotency_key}` of the API call that
triggered the event, useful for correlating an eval's actions to the events they produced). `account` /
`context` (both bare, undocumented-here strings — Connect-account attribution, out of scope).

List filters: `created`, `delivery_success` (meaningless without delivery, see above — decide whether
to expose or hardcode `true`), `type` (single string, wildcard-capable per description), `types`
(array, ≤20).

---

## Scripts used

All of the above was produced with two small scripts against `spec3.json`, kept in the scratchpad
(not committed): one that walks `components.schemas.<name>.properties` and prints
name/required/nullable/type (resolving `$ref`, `anyOf`, `enum`, `array<items>`), and one that walks
`paths.<p>.get.parameters` for list-filter query params. Both are reproducible in a few lines of
`json.load` + dict traversal; no external tooling was needed given the spec is plain OpenAPI 3.0 JSON.
