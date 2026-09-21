---
status: complete
---

# Phase 12: Subscriptions

## Overview

`subscriptions` and `subscription_items` end to end plus the eight-status
machine's reachable surface: the create branches (trialing, active-with-paid-
invoice, active-with-draft-invoice, incomplete, nothing-written), immediate
cancel, `cancel_at_period_end`, the trial-end paths (active / paused / canceled
/ the recorded no-PM refusal), pause_collection, the paused/resume
pending-update machinery, and the `subscription_items` CRUD. Because a
subscription cannot be exercised without its first invoice, the phase also
lands the `invoices` and `customer_balance_transactions` **tables** and the
internal invoice path subscriptions drive (`billing/invoicing.py`:
create → finalize → pay/leave-open, item lines, invoice-scope coupon
discounts, default tax rates, customer-balance settlement). The `/v1/invoices`
and `/v1/invoiceitems` **routes**, the invoice serializer, pending-item line
assembly and `automatically_finalizes_at` on manual invoices stay Phase 13's;
proration line generation (the behavior spanning this phase and 13) and
dunning land in Phase 14, so item changes accept `proration_behavior` but
generate no lines until then — declared.

Every wire shape and refusal below was probed live at `2026-08-26.dahlia`
(2026-09-20/21, sandbox account) before this plan was written; the cassette
records as `12_subscriptions`.

## Probed behavior this phase implements (recordings win)

- **Plain create** (customer with default PM): `status: active`,
  `latest_invoice` a **paid** `subscription_create` invoice — `attempt_count:
  1`, `attempted: true`, `auto_advance: false`, `number` assigned
  (`<invoice_prefix>-0001`), `status_transitions.finalized_at`/`paid_at` set,
  `period_start == period_end ==` the creation instant, `effective_at` set;
  the line is `1 × <product> (at $20.00 / month)` with
  `parent.subscription_item_details`, `pricing.price_details`, `taxes: []`.
  The period lives on **items** (`current_period_start/end`), never on the
  subscription. `items` is an always-present nested list envelope
  (`url: /v1/subscription_items?subscription=…`, `total_count`).
- **No-PM create** → 400 `resource_missing`, message "This customer has no
  attached payment source or default payment method. Please consider adding a
  default payment method. For more information, visit
  https://stripe.com/docs/billing/subscriptions/payment-methods-setting#payment-method-priority.",
  no `param`, **nothing written**.
- **3DS PM create** (default `payment_behavior`): `status: incomplete`, the
  invoice **open** with `attempted: true, attempt_count: 0`,
  `next_payment_attempt: null`; `payment_behavior=error_if_incomplete` →
  **402** `card_error` / `subscription_payment_intent_requires_action` (long
  recorded message), nothing written.
- **`pending_if_incomplete` on create** → 400 "Setting \`payment_behavior\` to
  \`pending_if_incomplete\` has no effect when creating a subscription."
  (`param: payment_behavior`). Bad literal → the recorded choices message.
- **`send_invoice` create** → `active`; the first invoice stays **draft**
  (`auto_advance: true`, `number: null`, `due_date = now + days_until_due`,
  `attempted: false`) — corrects billing_engine §1's "created + finalized,
  left open". `days_until_due` on `charge_automatically` → the recorded
  refusal.
- **Trial**: `trial_end` future → `trialing`, `trial_start` now. Prices'
  `trial_period_days` does **not** auto-trial (probed; `trial_from_plan=true`
  is the switch). `trial_end: "now"` on update: PM present → `active` with the
  first cycle invoice paid; no PM + `missing_payment_method=create_invoice`
  → the **same 400 no-PM refusal** (corrects §1's "falls into past_due");
  `pause` → `paused` (no invoice); `cancel` → `canceled` with
  `cancellation_details.reason: "cancellation_requested"` (corrects §1's
  `payment_failed`). `trial_end` in the past → the recorded refusal.
- **Paused/resume**: `POST /resume` on non-paused → 400 "You can only resume
  a subscription if it is \`paused\`." On paused (with or without a PM —
  probed both): **200, status stays `paused`**, a `pending_setup_intent` is
  minted and `pending_update` parked (`expires_at = now + 23h`); the anchor
  resets per `billing_cycle_anchor ∈ {now, unchanged}` and a
  `subscription_cycle` invoice is created open. Activation happens when the
  SetupIntent confirms — this world wires that hook (Phase 10's confirm);
  live's minted seti reads `canceled` instantly and refuses its confirm (an
  async artifact this world declares rather than reproduces).
- **Cancel**: `DELETE` → immediate `canceled`, `cancellation_details.reason:
  "cancellation_requested"`, `canceled_at`/`ended_at` stamped; a second
  `DELETE` → 404 `No such subscription` (`param: id`). Updates on canceled:
  `metadata` (and `cancellation_details`) succeed, everything else →
  `invalid_canceled_subscription_fields` "A canceled subscription can only
  update its cancellation_details and metadata." — corrects §1's
  `status_transition_invalid`. On `incomplete`, `metadata` **and
  `description`** succeed (corrects §1's metadata/default-source-only row).
- **`cancel_at_period_end`**: sets/clears `cancel_at` to the item period end,
  status unchanged. **`pause_collection`**: `{behavior, resumes_at}` stored,
  status unchanged; `""` clears.
- **subscription_items**: create needs a **live** subscription (canceled →
  404 `No such subscription`, `param: subscription`); a new item's period is
  `[now, subscription period end)`; the list's `subscription` filter is
  required (recorded `parameter_missing`); retrieve of an unknown item →
  "Invalid subscription_item id: si_nope" (no `param`, no `code`); delete
  answers the three-key stub.
- **Create refusals** (verbatim): missing `items` (`parameter_missing`),
  unknown price (`No such price`, `param: items[0][price]`), unknown customer
  (404), duplicate price across items ("Cannot create a Subscription with
  multiple Subscription Items with the same Price: …", `param: "plan"`),
  `billing_cycle_anchor`-stub arithmetic scope-cut until Phase 14 (the
  recorded stub invoice bills a flexible-mode fraction — 116¢ recorded for a
  $30/10-day stub — that conformance scenario 1 owns).
- **Live-only echoes omitted** (spec is authority, allow-listed):
  top-level `plan`/`quantity` on the subscription body, `current_trial` on
  items, `trial_settings.end_behavior.billing_cycle_anchor`,
  `pending_update.cancel_at_period_end`. The recording account's
  `billing_mode` is `flexible` (dashboard config); this world's constant is
  `classic`.

## Steps

1. **`src/stripeapi/schema/003_billing.sql`** — `subscriptions` (43 cols),
   `subscription_items` (13 cols), `invoices` (67 cols) and
   `customer_balance_transactions` (12 cols, first written by this phase's
   finalize; Phase 15 routes it) with their indexes, DDL verbatim from
   `components/data_model.md` §5; `subscriptions.schedule` stays a bare
   column (Phase 16 joins the FK). Rebuild `fixtures/empty`.
2. **`src/stripeapi/resources/charges.py`** — `insert_charge` accepts
   `payment_intent=None` (invoice-born charges).
3. **`src/stripeapi/billing/invoicing.py`** — the subscription-driven subset:
   `add_interval`, `describe_amount` + `_line_description` ("1 × product (at
   $20.00 / month)"), `item_lines` (one line per subscription item,
   `parent.subscription_item_details`, `pricing.price_details`),
   `compute_totals` (item lines + invoice-scope coupon discounts via
   `_money.apportion` + default tax rates, exclusive/inclusive per §3.2),
   `settle_customer_balance` (§3.5 verbatim), `create_invoice` (draft,
   `automatically_finalizes_at` rule), `finalize_invoice` (number from
   `customers.invoice_prefix`/`next_invoice_sequence`, freeze, customer
   balance + the `applied_to_invoice`/`adjustment` cbt row),
   `pay_invoice` (PM resolution subscription-default → customer default;
   `_behavior_tags` success/decline/3DS; charge row + ledger bt; decline is a
   return outcome; 3DS sets `attempted: true, attempt_count: 0` +
   `invoice.payment_action_required`), `void_invoice`,
   `compute_automatically_finalizes_at`. `pay_invoice` calls
   `subscription_lifecycle.on_invoice_paid` through a function-level import.
4. **`src/stripeapi/billing/subscription_lifecycle.py`** —
   `create_subscription` (the probed branches), `apply_update`
   (canceled/incomplete guards, `cancel_at_period_end`, `pause_collection`,
   `trial_end="now"` paths, items[] quantity/price changes without proration
   lines), `cancel_subscription` (immediate; `cancellation_details`;
   `invoice_now` final invoice), `resume_subscription` (pending-update park +
   seti mint), `apply_pending_update_on_seti_success` (the confirm hook),
   `advance_cycle` + `expire_incomplete` (unrouted), `on_invoice_paid`
   (incomplete/past_due/unpaid → active).
5. **`src/stripeapi/resources/subscription_items.py`** — serializer (price
   always-inflated from `prices`, tax_rates joined, no `livemode`),
   ParamSpecs, engine-served list (required `subscription` filter) and
   retrieve (the recorded "Invalid subscription_item id" refusal),
   hand-written create/update/delete calling the lifecycle.
6. **`src/stripeapi/resources/subscriptions.py`** — FieldMap (derived
   `items` envelope and `billing_mode` object; constants per §7:
   `automatic_tax`, `default_source`, `test_clock`, `application`,
   `on_behalf_of`, `transfer_data`, `customer_account`,
   `application_fee_percent`), ParamSpecs, engine-served list/retrieve
   (filters `customer`, `price` via items, `status` incl. `all`/`ended`,
   `created`), hand-written create/update/delete/resume. `discounts[0][coupon]`
   accepted: `duration=once` applies to the first invoice only (recorded —
   `subscription.discounts` stays `[]`), repeating coupons persist as inline
   discount objects emitted as ids; expired coupon → `coupon_expired`.
7. **`src/stripeapi/resources/setup_intents.py`** — the confirm-success hook
   calls back into `subscription_lifecycle.apply_pending_update_on_seti_success`.
8. **`src/stripeapi/dispatch/routes.py`** — wire the eleven routes (six
   subscriptions incl. `/resume`, five subscription_items). The legacy
   `/v1/customers/{customer}/subscriptions*` aliases, `/migrate` and the
   discount sub-routes stay unwired (declared).
9. **Record `12_subscriptions`** — the scenario in the Overview: plain
   create/retrieve/list, `cancel_at_period_end` both ways, item CRUD + its
   404s, cancel + second-DELETE 404 + canceled-update refusals, trial →
   `trial_end=now`, 3DS incomplete + `error_if_incomplete` 402, no-PM 400,
   `pending_if_incomplete`, duplicate price, `send_invoice` + the
   `days_until_due` refusal, missing-`items`, bad price/customer, the
   items-list `parameter_missing`, resume-non-paused refusal, the
   pause → resume pending-update body, the two choices refusals
   (`payment_behavior`, `status`), unknown param, the three missing-id 404s.
10. **`tests/conformance/allowed_differences.py`** — predicated reference
    pairs (`**.latest_invoice`, `**.pending_setup_intent`,
    `**.subscription`, `**.price`-bare-id cases), int-pairs
    (`**.current_period_start`, `**.current_period_end`,
    `**.billing_cycle_anchor`), the live-only echoes
    (`body.plan`/`body.quantity` + list/bodies forms, `**.current_trial`,
    `**.trial_settings.end_behavior.billing_cycle_anchor`,
    `**.pending_update.cancel_at_period_end`), `**.billing_mode`
    (flexible-recorded vs classic-replayed), the duplicate-price message
    predicate, and STRUCTURAL entries for the resume-seti artifact, the
    unrecordable decline-card subscription, and the anchor scope cut.
11. **Spec corrections** — `billing_engine.md` §1: the five recorded
    corrections named in the Overview (send_invoice draft; resume's park;
    description-on-incomplete; trial-end no-PM refusal; cancel reason).
12. **Tests** — `tests/billing/test_subscription_machine.py` (one test per
    machine row reachable through the tools + the unrouted walkers:
    `advance_cycle`, `expire_incomplete`; terminal-stamp invariants I12),
    `tests/billing/test_invoicing.py` (line description, totals with coupon
    and tax from the recordings, `settle_customer_balance`'s three worked
    cases, number sequence), `tests/test_subscriptions.py` +
    `tests/test_subscription_items.py` (bodies, refusals verbatim, filters,
    events, the replay gate joins cassette 12; the eleven op_ids join
    `test_tools.py`'s wired list).

## Tests

- `tests/billing/test_subscription_machine.py` — create→active (paid invoice,
  attempt_count 1), create→trialing (no invoice, anchor at trial_end),
  create→incomplete (3DS: open invoice, attempted/attempt_count split), the
  402 `error_if_incomplete` leaves zero change-log records, no-PM 400,
  send_invoice→active-with-draft, trial-end: active/paused/canceled/400,
  paused→resume parks, seti-confirm applies (active + paid cycle invoice +
  `customer.subscription.resumed` + `pending_update_applied`), cancel
  (stamps, second-DELETE 404, canceled-update rules), `cancel_at_period_end`
  no status change, `pause_collection` both ways, `advance_cycle` (renewal
  invoice at boundary, `cancel_at_period_end` firing, unpaid-stays-draft),
  `expire_incomplete` (void + terminal), `on_invoice_paid` recoveries, the
  transition-table integrity (no edges out of terminal states), I12 SQL.
- `tests/billing/test_invoicing.py` — the recorded totals verbatim (plain
  2000; coupon 3000→2700 with `total_discount_amounts`; GST 3000→3150 with
  line `taxes`), customer-balance settlement worked cases, the
  `applied_to_invoice` cbt row, invoice number `<prefix>-0001` and the
  customer's sequence increment.
- `tests/test_subscriptions.py` — the created body's constants
  (invoice_settings, payment_settings, cancellation_details, trial_settings,
  billing_mode classic), items envelope, `plan`/`quantity` absent, every
  recorded refusal verbatim, list filters (`status` enum incl. `all`),
  missing-id 404s, events, `x_seq` order.
- `tests/test_subscription_items.py` — create/list/update/delete, the
  required-filter and 404 spellings, periods `[now, period_end)`, price
  inflation, sub-update `items[0][id]` quantity path.
- Replay — cassette `12_subscriptions` joins `test_replay_conformance.py`.
