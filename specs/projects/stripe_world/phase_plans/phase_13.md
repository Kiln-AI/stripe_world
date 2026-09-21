---
status: complete
---

# Phase 13: Invoices

## Overview

`/v1/invoices` (16 routed operations) and `/v1/invoiceitems` (5) end to end,
plus the invoice serializer that Phase 12 deferred: the nested `lines` list
envelope, `parent` reconstruction, the account echoes, and the `invoice.*`
events every invoice-writing flow starts emitting now that a serialized
invoice exists. The status machine's transitions land as routed handlers
(finalize, pay, void, mark_uncollectible, send, delete, the three
line-editing endpoints), `automatically_finalizes_at` and
`next_payment_attempt` ship as the recorded draft-window fields, and
`effective_at` moves to finalization (recorded: null on every draft,
including subscription drafts — a Phase 12 write that this phase corrects).
The `invoiceitems` table joins the schema (the one DDL addition; the
`invoices` and `customer_balance_transactions` tables shipped in Phase 12),
and `pending` items feed line construction — `billing/invoicing.py`'s
`compute_totals` completion for `invoice_item`-sourced lines, which also
learns the recorded `pretax_credit_amounts` discount echo.

Every wire shape and refusal below was probed live at `2026-08-26.dahlia`
(2026-09-21, sandbox account) before this plan was written; the cassette
records as `13_invoices` (81 steps).

## Probed behavior this phase implements (recordings win)

- **Manual create** (`POST /v1/invoices`): draft, `billing_reason: manual`,
  `auto_advance` **false by default** (spec's own "Defaults to false"),
  `number`/`effective_at`/`ending_balance` null, `period_start ==
  period_end == created`, `attempted: false`, `attempt_count: 0`.
- **`pending_invoice_items_behavior`**: absent → **exclude** (the create
  answers an empty-lines draft even with pending items — corrects any
  include-by-default reading); `include` sweeps **all** pending items for
  the customer, newest-first, regardless of period (the invoice's own
  period is zero-width; billing_engine §3.1.1's period-bounded sweep is
  wrong for manual invoices). Choices `include|exclude`; the bad-literal
  refusal carries no `code`.
- **The draft window**: `automatically_finalizes_at = created + 3601s` iff
  `auto_advance` (both collection methods — the +1s is Stripe's own
  ceil-vs-floor artifact, recorded twice); `next_payment_attempt = created
  + 3600s` additionally iff `charge_automatically`. Clearing `auto_advance`
  nulls both; re-setting recomputes from `created`. `due_date = created +
  days_until_due × 86400` exactly.
- **The send_invoice create refusals**: no due date → "If invoice collection
  method is 'send_invoice', you must specify 'due_date' or
  'days_until_due'."; `days_until_due` under `charge_automatically` → "You
  can only specify 'due_date' or 'days_until_due' if invoice collection
  method is 'send_invoice'." (both no `code`, no `param`).
- **`/pay` on a draft succeeds**: finalize + collect inside the one call
  (`finalized_at == paid_at == effective_at`, attempt 1/1 on the card,
  `amount_paid = amount_due`). Re-pay → 400 "Invoice is already paid".
  `paid_out_of_band: true` on open → paid with `attempted` **still false**.
- **`/finalize`**: assigns `number` (per-customer sequence, shared by every
  finalization path including `/send` and $0), `effective_at`,
  `hosted_invoice_url`/`invoice_pdf`, the balance settlement; **no
  collection attempt** (open with `attempted: false` even with a default
  card); clears both window fields; a $0-`amount_due` result pays
  immediately (`attempted: true, attempt_count: 0`). Re-finalize → 400
  "This invoice is already finalized, you can't re-finalize a non-draft
  invoice."
- **`/void`**: open → void (amounts left as-is). Draft or uncollectible →
  "You can only pass in open invoices. This invoice isn't open."; paid →
  "Invoices with \`paid\` payments cannot be voided." (all no `code`).
- **`/mark_uncollectible`**: open → uncollectible; the same isn't-open
  refusal otherwise; re-mark → "This invoice has already been marked
  uncollectible."
- **`/send`**: on a draft → finalizes (and a $0 result pays); on an open
  `send_invoice` → 200, the body unchanged; on paid → 400 "This invoice
  cannot be sent right now. Please contact us via
  https://support.stripe.com/contact with details, so we can help."
- **DELETE**: draft → the three-key stub; the swept items stay attached to
  the dead invoice — **not released** (corrects billing_engine §2's
  "released back to unbilled"; their later DELETE refuses and their UPDATE
  answers "This invoice item has been deleted."). Non-draft → 400 "You can
  only delete draft invoices."
- **Update on non-draft**: `metadata` succeeds (recorded on paid);
  `description` → 400 "Finalized invoices can't be updated in this way"
  (`param: description`, no `code`).
- **Line editing**: `add_lines` lines[] take `amount` XOR `quantity`
  ("You may only specify one of these parameters: amount, quantity.",
  `param: lines[0][amount]`); the added line lands **first** (newest
  pending item) and mints an invoice item + a one-off price.
  `lines/{line_item_id}` updates one line and answers the **line**, not the
  invoice (an amount change mints a new one-off price). `update_lines`
  answers the invoice. `remove_lines` lines[] require `id` **and**
  `behavior ∈ {delete, unassign}` (`parameter_missing` on
  `lines[0][behavior]`). All draft-only; non-draft `add_lines` → 400
  `invoice_not_editable` "Invalid invoice: This invoice is no longer
  editable".
- **invoiceitems**: `amount`+`currency` create mints a one-off price and
  product behind `pricing.price_details` (the recorded shape); `date` is
  the creation stamp, `period = [date, date]`; list is newest-first with
  `customer`/`pending`/`invoice`/`created`(→`date`) filters; DELETE of a
  pending item hard-deletes (stub; retrieve after → 404 "No such Invoice
  Item: '…'(livemode=false)", `param: id`).
- **The 404 `param` spellings, per endpoint** (recorded, wildly
  inconsistent): GET/DELETE//send//add_lines name `invoice`;
  POST-update//pay//finalize//void//mark_uncollectible name `id`.
- **List filters**: `status` message "Invalid status: must be one of draft,
  open, void, paid, or uncollectible" (that order, no `code`); `customer`,
  `collection_method`, `created`, `due_date` (range), `subscription`.
- **Body echoes** (allow-listed): `rendering` null on subscription
  invoices and an account-default object on manual ones (we emit null);
  `account_country`/`account_name` account echoes; `webhooks_delivered_at`
  = `created`; `lines.total_count` (the spec's nested envelope omits it);
  the invoiceitem's `invoicing_rules` (spec-undeclared).

## Scope cuts, declared in `allowed_differences.py`

`/v1/invoices/create_preview` (never-stored preview surface; route stays
unwired), `/v1/invoices/{invoice}/attach_payment` (unprobed; unwired),
`subscription`/`from_invoice`/`custom_fields`/`rendering`/`shipping_*`/
`issuer`/`transfer_data`-family create/update parameters (cut at the
parameter layer), invoiceitem `price_data`/`pricing`/`discounts`/
`tax_code`/`tax_behavior`/`unit_amount_decimal`/`quantity_decimal`/
`subscription` parameters, `/pay`'s `mandate`/`off_session`/`source`/
`payment_method` (our pay resolves the recorded default-method chain), and
the `invoice_payment.paid` event (the `invoice_payment` object is out of
scope; `invoice.payment_succeeded` + `invoice.paid` carry the payment).

## Steps

1. **`src/stripeapi/schema/003_billing.sql`** — the `invoiceitems` table
   (24 cols) and its four indexes, DDL verbatim from `components/
   data_model.md` §5. Rebuild `fixtures/empty`.
2. **`src/stripeapi/billing/invoicing.py`** — the completion: an
   `InvoiceItemLine` path in `compute_totals` (invoice_item-sourced lines:
   `parent.invoice_item_details` naming the `ii_`, `pricing.price_details`
   naming the one-off price, period from the item) with the recorded
   `pretax_credit_amounts` discount echo and the 0-amount entry on
   non-discountable lines; `pending_item_lines(ctx, …)` (all pending items
   for the customer, newest-first); `recompute_invoice(ctx, invoice_id)`
   (rebuild lines + totals on every draft mutation); `create_invoice`
   stops writing `effective_at`; `finalize_invoice` writes it; the
   `$0-settles` helper `finalize_and_settle`; `delete_draft_invoice`
   (no release); `emit` wiring for `invoice.*` events on every transition;
   `next_payment_attempt = created + 3600s` beside the window field.
3. **`src/stripeapi/resources/invoices.py`** — the FieldMap (columns,
   `parent` reconstruction with `metadata: {}` snapshot, `lines` list
   envelope, `default_tax_rates` inflation, derived
   `hosted_invoice_url`/`invoice_pdf` (post-finalization only),
   `webhooks_delivered_at = created`, constants per §7 plus
   `account_country`/`account_name` from `ctx.state["account"]`), the
   eleven ParamSpecs (create/update with the recorded refusals, pay,
   finalize, void, mark_uncollectible, send, add_lines, update_lines,
   remove_lines, the line update), and the eleven hand-written handlers +
   engine-served list/retrieve/lines.
4. **`src/stripeapi/resources/invoiceitems.py`** — FieldMap (period
   derived, `tax_rates` inflated, `pricing` verbatim), ParamSpecs, the
   one-off price/product minter, engine-served list/retrieve, hand-written
   create/update/delete with the recorded refusal families.
5. **`src/stripeapi/dispatch/routes.py`** — wire the 21 routes (16
   invoices minus `create_preview` and `attach_payment`, 5 invoiceitems),
   `missing_path_param` per the recorded per-endpoint spellings.
6. **`tests/conformance/allowed_differences.py`** — the Phase 13 block:
   `**.rendering`, `**.account_country`/`**.account_name`,
   `**.webhooks_delivered_at`, `**.hosted_invoice_url`/`**.invoice_pdf`
   (placeholder predicates), `**.lines.total_count`,
   `**.invoicing_rules`, `**.date`/`**.due_date`/`**.period_start`/
   `**.period_end`/`**.next_payment_attempt`/`**.automatically_finalizes_at`
   int pairs, `**.pricing.price_details.price`/`-product`,
   `**.invoice_item`, `**.discounts`/`**.discount_amounts[*].discount`/
   `**.pretax_credit_amounts[*].discount`/`**.total_discount_amounts[*].discount`
   (di_ pairs), the "No such Invoice Item …(livemode=false)" message form,
   and the STRUCTURAL entries for the scope cuts and the corrected
   wrong-state refusal codes.
7. **Spec corrections** — `billing_engine.md`: §2's error table (the
   recorded no-code spellings replace `invoice_not_editable`/
   `status_transition_invalid` except add_lines'), the pay-a-draft row,
   the $0-finalize row, delete-draft's no-release, the draft-window
   +3601s/+3600s pair, §3.1.1's unbounded sweep.
8. **Tests** — `tests/billing/test_invoice_machine.py` (one per recorded
   transition + refusal, I1–I4/I9/I10 invariants),
   `tests/test_invoices.py` (create/list/filters/404 spellings, the window
   fields, line editing, events), `tests/test_invoiceitems.py` (CRUD,
   pending filter, the deleted-item refusals, the one-off price mint),
   cassette 13 joins `test_replay_conformance.py`; `tests/test_routes.py`
   op-count and `tests/test_tools.py` wired-list updates.

## Tests

- `tests/billing/test_invoice_machine.py` — create→draft (both sweep
  modes), the window fields on both `auto_advance` values and both
  collection methods, clear/re-set recompute, finalize (number sequence
  across /pay-on-draft, /finalize, /send; $0 settle; no attempt;
  effective_at; hosted URLs), pay-a-draft (card: attempt 1/1;
  out-of-band: attempted false), re-pay/void-paid/mark-paid/re-finalize/
  delete-paid refusals verbatim, void/mark_uncollectible success shapes,
  /send's three outcomes, delete-draft (stub + no release + the dead-item
  refusals), metadata-on-paid succeeds, I9 (affa populated exactly when
  draft+auto_advance) and the attempt invariants.
- `tests/test_invoices.py` — the created body's constants and echoes
  (`issuer`, `automatic_tax`, `rendering: null`, `customer_tax_ids: []`,
  `payments` absent), list filters incl. the recorded status message,
  `GET …/lines` pagination, add_lines XOR refusal, update-line answers the
  line, remove_lines behavior requirement, the per-endpoint 404 `param`
  spellings, events (`invoice.created/finalized/paid/…`), `x_seq` order.
- `tests/test_invoiceitems.py` — create/retrieve/list/update/delete, the
  pending filter after a sweep and after a draft delete, the minted
  price/product behind `pricing`, the "(livemode=false)" 404, dead-item
  refusals, subscription-invoice lines now naming `invoice_item_details`.
- Replay — cassette `13_invoices` joins `test_replay_conformance.py`;
  cassette 12 must stay green (the `effective_at` change is invisible
  there — no invoice bodies recorded).
