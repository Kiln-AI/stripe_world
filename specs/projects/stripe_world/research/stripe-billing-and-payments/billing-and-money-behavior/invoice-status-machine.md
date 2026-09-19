# Invoice Status Machine

See `subscription-status-machine.md` for the access-method caveat (docs.stripe.com blocked for direct
fetch this session; WebSearch synthesis used instead, flagged per claim; `spec3.json` description
fields used as primary source where available).

## The five statuses (ground truth: `spec3.json`)

`components.schemas.invoice.properties.status`:

```
enum: [draft, open, paid, uncollectible, void]
```

description (spec3.json, verbatim): "The status of the invoice, one of `draft`, `open`, `paid`,
`uncollectible`, or `void`." with a pointer to
<https://docs.stripe.com/billing/invoices/workflow#workflow-overview> (not independently fetched — blocked).

## IMPORTANT: the invoice object shape has changed substantially from older docs/blog posts

At API version **`2026-08-26.dahlia`** (the version in the pre-fetched `spec3.json`), the `invoice`
object does **not** have the flat fields that most existing tutorials, Stack Overflow answers, and even
some current-looking blog posts describe. Confirmed by listing `components.schemas.invoice.properties`
directly:

- There is **no top-level `invoice.subscription`** field. The subscription link now lives at
  `invoice.parent.subscription_details.subscription`, where `invoice.parent` is a
  `billing_bill_resource_invoicing_parents_invoice_parent` object with fields `type`, `subscription_details`
  (a `billing_bill_resource_invoicing_parents_invoice_subscription_parent`), and `quote_details`. This
  is a Connect/multi-parent generalization — an invoice can apparently be parented by a subscription or
  a quote, hence the wrapper.
- There is **no top-level `invoice.days_until_due`** field. `days_until_due` is a **create-only
  parameter** (`POST /v1/invoices`, "The number of days from when the invoice is created until it is
  due. Valid only for invoices where `collection_method=send_invoice`."); the resulting computed
  due date is exposed as `invoice.due_date` (a unix timestamp) on the object.
- There is **no top-level `invoice.paid` boolean** any more (older docs mention it). Use
  `invoice.status === 'paid'`, or the newer `amount_remaining === 0` / `amount_paid` fields.
- The **~1-hour draft-finalization window now has an explicit field**: `invoice.automatically_finalizes_at`
  (spec3.json, verbatim: "The time when this invoice is currently scheduled to be automatically
  finalized. The field will be `null` if the invoice is not scheduled to finalize in the future. If the
  invoice is not in the draft state, this field will always be `null` — see `finalized_at` for the time
  when an already-finalized invoice was finalized.") This is a stronger, queryable replacement for the
  old "just wait ~1 hour and poll `status`" mental model.
- New fields not in older docs: `amount_overpaid` ("Amount that was overpaid on the invoice. The amount
  overpaid is credited to the customer's credit balance."), `amount_paid_off_stripe` ("Amount... paid on
  the invoice outside of Stripe."), `confirmation_secret` ("Currently, this contains the client_secret
  of the PaymentIntent that Stripe creates during invoice finalization."), `payments` (a list — "Payments
  for this invoice. Use invoice payment to get more details." — implying an invoice can now have
  *multiple* payment attempts represented as first-class sub-objects, not just a single
  `charge`/`payment_intent` pointer), and `from_invoice`/`latest_revision` (invoice **revisions** —
  "Details of the invoice that was cloned" — a versioning mechanism not present in the classic model).

**Implication for the Seahaven world:** if the project's mental model was built from older
documentation or from memory, the invoice schema needs re-deriving from `spec3.json` directly (the
"API surface and object graph" subtopic owns this in full) — this doc only flags the specific fields
that affect *status-machine* behavior. Treat any pre-existing assumption of flat `invoice.subscription`
/ `invoice.days_until_due` / `invoice.paid` as **stale**.

## Status transition fields

`invoice.status_transitions` (schema `invoices_resource_status_transitions`), all four fields present
regardless of whether that transition has happened (null until it does):

- `finalized_at` — "The time that the invoice draft was finalized."
- `marked_uncollectible_at` — "The time that the invoice was marked uncollectible."
- `paid_at` — "The time that the invoice was paid."
- `voided_at` — "The time that the invoice was voided."

## `draft` → `open`: when and why, precisely

Two independent controls:

1. **`auto_advance`** (boolean, on the invoice) — spec3.json, verbatim: "Controls whether Stripe
   performs automatic collection of the invoice. If `false`, the invoice's state doesn't automatically
   advance without an explicit action." Docs link:
   <https://docs.stripe.com/invoicing/integration/automatic-advancement-collection> (not independently
   fetched).
2. **The ~1 hour draft window** (via search, corroborating and dating the mechanism): "When you turn on
   automatic collection, Stripe automatically finalizes draft invoices after one hour... You must turn
   on automatic collection by setting the `auto_advance` property on the invoice to `true`." This is
   also indirectly confirmed by `invoice.attempted`'s description (spec3.json, verbatim): "Whether an
   attempt has been made to pay the invoice. An invoice is not attempted until 1 hour after the
   `invoice.created` webhook, for example, so you might not want to display that invoice as unpaid to
   your users." — i.e. the 1-hour delay is real and independently corroborated inside the spec itself,
   not just in blog commentary.
3. **Manual finalize**: `POST /v1/invoices/{id}/finalize` finalizes a draft immediately regardless of
   the timer (via search, <https://docs.stripe.com/api/invoices/finalize>: "Stripe automatically
   finalizes drafts before sending and attempting payment on invoices, but if you'd like to finalize a
   draft invoice manually, you can do so using the finalize method.")
4. Once finalized, the invoice number is assigned and (per search, quoting docs) "Once an invoice is
   finalized, monetary values, as well as `collection_method`, become uneditable." — finalization is the
   line items/amounts freeze point.

**Note the potential inconsistency worth flagging explicitly**: the 1-hour figure appears only in
WebSearch-sourced prose, not verified verbatim against a currently-fetchable docs page in this session.
The `attempted` field's 1-hour language *is* spec3.json-verbatim and independently corroborates it, so
confidence is high, but I could not confirm whether `automatically_finalizes_at` is computed as exactly
`invoice.created + 1h` in all cases (e.g., does `days_until_due`/`collection_method=send_invoice`
change this?). **Open question** — settle by creating a draft invoice against a real/mocked account and
reading `automatically_finalizes_at` directly, or by a direct fetch of
<https://docs.stripe.com/invoicing/integration/automatic-advancement-collection> once egress allows it.

## `open` → `paid` / `uncollectible` / `void`

- **`paid`**: full amount collected (via `amount_remaining` reaching 0). Sets
  `status_transitions.paid_at`.
- **`uncollectible`**: explicit dashboard/API action marking the invoice as bad debt (does not attempt
  further collection). Sets `status_transitions.marked_uncollectible_at`. This is also the fate of
  invoices generated for a subscription in `status=unpaid` (subscription doc, verbatim, previous file):
  "invoices will be created, but then immediately automatically closed" — "closed" here maps to
  `uncollectible` or `void` depending on account dunning settings (not fully disambiguated by the
  sources gathered — see gap below).
- **`void`**: cancels the invoice, e.g. an `incomplete_expired` subscription's open invoice is voided
  per the subscription-status description ("the open invoice will be voided"). Sets
  `status_transitions.voided_at`. A voided invoice is a dead end (no further payment attempts).

`draft` invoices can be deleted outright (they don't need voiding since they were never finalized) —
this is standard Stripe behavior but I did not find a spec3.json field or corroborating search result
confirming the exact endpoint name in this session; flagging as **unconfirmed, low priority**.

## `billing_reason` — full enum with definitions (spec3.json, verbatim)

```
enum: [automatic_pending_invoice_item_invoice, manual, quote_accept, subscription,
       subscription_create, subscription_cycle, subscription_threshold, subscription_update, upcoming]
```

- `manual` — "Unrelated to a subscription, for example, created via the invoice editor."
- `subscription` — "**No longer in use.** Applies to subscriptions from before May 2018 where no
  distinction was made between updates, cycles, and thresholds." (Legacy value; new invoices should
  never carry this — a Seahaven mock generating fresh fixtures should never emit it.)
- `subscription_create` — "A new subscription was created."
- `subscription_cycle` — "A subscription advanced into a new period." — this is the recurring-renewal
  invoice, the one dunning schedules apply to (see `dunning-and-retries.md`).
- `subscription_threshold` — "A subscription reached a billing threshold." (usage-based billing
  threshold trigger — out of scope detail for this subtopic, belongs to metered-billing behavior.)
- `subscription_update` — "A subscription was updated." — this is the proration-invoice case for
  `proration_behavior=always_invoice` (immediate separate invoice on a mid-cycle change) — see
  `proration-arithmetic.md`.
- `upcoming` — "Reserved for upcoming invoices created through the Create Preview Invoice API or when
  an `invoice.upcoming` event is generated for an upcoming invoice on a subscription." Not present on
  invoices, only lives ephemeral in-preview.
- `automatic_pending_invoice_item_invoice` — not documented with further prose in spec3.json beyond the
  enum name; not covered by the WebSearch queries in this session. Likely fires when standalone pending
  invoice items accumulate and get auto-invoiced outside a subscription cycle. **Gap**: could not
  corroborate the exact trigger condition; would need
  <https://docs.stripe.com/api/invoices/object> read directly or a recorded trace showing an invoice
  with this `billing_reason`.
- `quote_accept` — fires when a Quote is accepted and generates an invoice (Quotes feature; out of this
  subtopic's scope per the research plan's object-graph boundary, noted here only because it's in the
  enum).

## How subscription cycles create invoices

Confirmed mechanism (via search + subscription status description cross-reference): at the end of a
subscription's current billing period, Stripe generates a new invoice with `billing_reason=
subscription_cycle`, covering the upcoming period's charges (plus any pending proration/invoice items).
For `collection_method=charge_automatically`, Stripe attempts to pay it immediately using the default
payment method; on success the subscription (if it was `past_due`) returns to `active`, on failure it
moves to `past_due` and dunning begins (see `dunning-and-retries.md`). For `collection_method=
send_invoice`, the invoice is emailed with `due_date` = creation time + `days_until_due`, and the
subscription becomes `past_due` only once that due date passes unpaid.

**Resolved 2026-09-18** — see `gap-closure-2026-09-18.md` item 2. A direct read of
<https://docs.stripe.com/billing/invoices/subscription> (unreachable in the original pass, reachable now)
confirms: "Stripe automatically creates an invoice for subscriptions at the end of each billing cycle. We
finalize and send the invoice in one hour." There is **no lead time before** the boundary — invoice
creation *is* the period-boundary event; the ~1-hour draft window (documented above) comes *after*
creation, not before it.

## `collection_method` and `due_date`/`days_until_due` mechanics

- `collection_method` ∈ `[charge_automatically, send_invoice]`, same enum shape on both `subscription`
  and `invoice` (spec3.json, both verbatim, quoted in `subscription-status-machine.md`). It is set on
  the subscription and inherited by invoices it generates; also settable directly on manually-created
  invoices.
- `days_until_due` — create-only param, "Valid only for invoices where `collection_method=
  send_invoice`" (spec3.json, verbatim). Determines the computed `due_date`.
- Once finalized, `collection_method` becomes uneditable (WebSearch, quoting docs prose above).

## Sources

- `research/stripe-openapi/spec3.json`, `components.schemas.invoice.properties.*` (full property list
  enumerated directly, `status`, `billing_reason`, `auto_advance`, `attempted`, `automatically_finalizes_at`,
  `collection_method`, `parent`, `status_transitions`), `components.schemas.invoices_resource_status_transitions`,
  and the `POST /v1/invoices` request schema for `days_until_due` — API version `2026-08-26.dahlia`,
  fetched 2026-09-18.
- WebSearch synthesis quoting <https://docs.stripe.com/api/invoices/finalize>,
  <https://docs.stripe.com/invoicing/integration/automatic-advancement-collection>,
  <https://docs.stripe.com/invoicing/integration/workflow-transitions>,
  <https://docs.stripe.com/billing/invoices/subscription> — accessed 2026-09-18 (direct fetch blocked).
