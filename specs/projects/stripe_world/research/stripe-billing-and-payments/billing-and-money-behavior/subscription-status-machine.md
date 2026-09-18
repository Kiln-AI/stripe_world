# Subscription Status Machine

**Access note on this whole subtopic:** `docs.stripe.com` and `stripe.com` are blocked outright by this
session's egress proxy (`curl`/`WebFetch` to either host return `EGRESS_BLOCKED` / CONNECT 403 — this
is an organization policy block on the whole domain, confirmed by testing several docs paths and
`stripe.com/docs/...` mirrors, and even `web.archive.org`). `WebSearch` still works and returns
synthesized snippets that quote docs.stripe.com pages directly (it appears to run its own fetch,
outside this proxy). All prose-doc claims below are therefore sourced through `WebSearch` result text,
not a direct page read — flagged per-claim as "(via search)". Where the OpenAPI spec's `description`
fields duplicate docs prose verbatim (Stripe embeds long-form docs text into `spec3.json`
`components.schemas.*.properties.*.description`), that is the stronger source and is flagged
"(spec3.json, verbatim)" — treat those as primary-source quotes, not paraphrase.

## The eight statuses (ground truth: `spec3.json`)

`components.schemas.subscription.properties.status`:

```
enum: [active, canceled, incomplete, incomplete_expired, past_due, paused, trialing, unpaid]
```

The full `description` field on that property is the canonical state-machine doc, verbatim:

> Possible values are `incomplete`, `incomplete_expired`, `trialing`, `active`, `past_due`, `canceled`,
> `unpaid`, or `paused`.
>
> For `collection_method=charge_automatically` a subscription moves into `incomplete` if the initial
> payment attempt fails. A subscription in this status can only have metadata and default_source
> updated. Once the first invoice is paid, the subscription moves into an `active` status. If the first
> invoice is not paid within 23 hours, the subscription transitions to `incomplete_expired`. This is a
> terminal status, the open invoice will be voided and no further invoices will be generated.
>
> A subscription that is currently in a trial period is `trialing` and moves to `active` when the trial
> period is over.
>
> A subscription can only enter a `paused` status when a trial ends without a payment method
> ([docs.stripe.com/billing/subscriptions/trials#create-free-trials-without-payment](https://docs.stripe.com/billing/subscriptions/trials#create-free-trials-without-payment)). A `paused`
> subscription doesn't generate invoices and can be resumed after your customer adds their payment
> method. The `paused` status is different from [pausing collection](https://docs.stripe.com/billing/subscriptions/pause-payment),
> which still generates invoices and leaves the subscription's status unchanged.
>
> If subscription `collection_method=charge_automatically`, it becomes `past_due` when payment is
> required but cannot be paid (due to failed payment or awaiting additional user actions). Once Stripe
> has exhausted all payment retry attempts, the subscription will become `canceled` or `unpaid`
> (depending on your subscriptions settings).
>
> If subscription `collection_method=send_invoice` it becomes `past_due` when its invoice is not paid
> by the due date, and `canceled` or `unpaid` if it is still not paid by an additional deadline after
> that. Note that when a subscription has a status of `unpaid`, no subsequent invoices will be
> attempted (invoices will be created, but then immediately automatically closed). After receiving
> updated payment information from a customer, you may choose to reopen and pay their closed invoices.

Source: `research/stripe-openapi/spec3.json` → `components.schemas.subscription.properties.status`
(API version `2026-08-26.dahlia`). This text is also published at
<https://docs.stripe.com/api/subscriptions/object> and referenced from
<https://docs.stripe.com/billing/subscriptions/overview>.

## State machine, precisely

```
                    ┌──────────────┐
   create (no trial,│              │  first invoice pays synchronously
   payment succeeds) │   active    │◄─────────────────────────────┐
        ┌───────────►│              │                              │
        │            └──────┬───────┘                              │
        │                   │ payment fails on a renewal cycle      │
        │                   ▼                                      │
        │            ┌──────────────┐   retries exhausted,         │
        │            │  past_due    │──►settings=cancel ───► canceled (terminal)
        │            │              │──►settings=mark unpaid ──► unpaid
        │            └──────────────┘        (charge_automatically path)
        │
create (trial)  ┌──────────────┐  trial_end reached, payment method present
────────────────►  trialing    │────────────────────────► active (or incomplete/past_due
                 │              │                            if the post-trial charge fails)
                 └──────┬───────┘
                        │ trial ends, NO payment method on file
                        ▼
                 ┌──────────────┐  customer adds a payment method
                 │   paused     │────────────────────────► (resumes; goes to active or
                 │ (no invoices │                            whatever the resume flow produces)
                 │  generated)  │
                 └──────────────┘

create (payment required, payment_behavior=default_incomplete or allow_incomplete)
        │
        ▼
 ┌──────────────┐  first invoice's PaymentIntent confirmed within 23h
 │  incomplete  │─────────────────────────────────────────► active
 │ (metadata &  │
 │ default_src  │  NOT confirmed within 23h
 │ only field)  │─────────────────────────────────────────► incomplete_expired (terminal;
 └──────────────┘                                             open invoice voided, no further
                                                                invoices generated)
```

`unpaid` is **not** terminal in the same hard sense as `canceled`/`incomplete_expired`: docs state you
may "reopen and pay their closed invoices" after receiving updated payment info (spec3.json, verbatim,
above), implying a path back — but the spec text does not say the *subscription* status itself
auto-recovers to `active`; it describes reopening/paying the invoice. This is genuinely ambiguous in
the prose and worth flagging: **open question** — does resolving an `unpaid` subscription's invoices
flip `subscription.status` back to `active` automatically, or does it require a separate subscription
update call? Settling this needs either a closer read of
<https://docs.stripe.com/billing/subscriptions/overview> §"Unpaid" (blocked from direct fetch in this
session) or a recorded API trace (create sub → force decline through retries → exhaust → reopen invoice
→ observe `subscription.status`).

## `payment_behavior` (create-time), verbatim from `spec3.json`

`POST /v1/subscriptions` param `payment_behavior`, enum
`[allow_incomplete, default_incomplete, error_if_incomplete, pending_if_incomplete]`. Spec3.json's
top-level description: "Controls how Stripe handles the first invoice when payment is required and
`collection_method=charge_automatically`. Subscriptions with `collection_method=send_invoice` are
automatically activated regardless of the first Invoice status." Per-value semantics (via search,
quoting <https://docs.stripe.com/api/subscriptions/create>):

- **`allow_incomplete`** — "Creates subscriptions with `status=incomplete` if the first invoice can't
  be paid. This allows you to manage scenarios where additional customer actions are needed to pay a
  subscription's invoice, for example, SCA regulation may require 3DS authentication to complete
  payment."
- **`default_incomplete`** (Stripe's recommended default) — "Creates subscriptions with
  `status=incomplete` when the first invoice requires payment, otherwise start as active. Subscriptions
  transition to `status=active` when successfully confirming the PaymentIntent on the first invoice...
  If the PaymentIntent is not confirmed within 23 hours Subscriptions transition to
  `status=incomplete_expired`, which is a terminal state."
- **`error_if_incomplete`** — "Stripe returns an HTTP 402 status code if a subscription's first invoice
  can't be paid... this parameter doesn't create a Subscription and returns an error instead." I.e. no
  `incomplete` subscription object is ever created in this mode.
- **`pending_if_incomplete`** — "This is only used with updates and cannot be passed when creating a
  Subscription." (Applies to `POST /v1/subscriptions/{id}` only — confirmed present in that endpoint's
  enum in `spec3.json`, absent from the create endpoint's accepted values per the search result; I did
  not independently re-derive this restriction from `spec3.json`'s param-level enum, since both
  endpoints share the same schema-level enum list. **Treat "create rejects pending_if_incomplete" as
  docs-prose-sourced, not spec3.json-verified.**)

The 23-hour window is stated identically for both `incomplete`→`incomplete_expired` (status field
description) and `default_incomplete` (payment_behavior description) — consistent between two
independent locations in the same spec file, which is a strong internal-consistency signal that 23h
(not "1 hour", which is the *invoice draft* window — see `invoice-status-machine.md`) is correct for
subscription-level incomplete expiry. **Do not confuse the two timers**: invoice draft→open is ~1 hour;
subscription `incomplete`→`incomplete_expired` is 23 hours.

## `cancel_at_period_end` vs immediate cancel

- `subscriptions.update` param `cancel_at_period_end` (boolean) — spec3.json: "Indicate whether this
  subscription should cancel at the end of the current period (`current_period_end`)." Setting this
  `true` does **not** change `status` immediately; the subscription stays `active` (or whatever it was)
  until the period boundary, at which point Stripe stops renewing it and it becomes `canceled`.
- `DELETE /v1/subscriptions/{id}` (subscription cancel endpoint) performs an **immediate** cancel:
  status → `canceled` right away. (Endpoint existence and semantics from `spec3.json` paths; exact
  proration-on-cancel behavior at that moment is documented in `proration-arithmetic.md`.)
- `canceled` is a terminal status for the object as returned by the status enum's docs prose (no
  transition out of `canceled` is described anywhere in the enum text above).

## Pause/resume — two distinct mechanisms, do not conflate

There are **two** unrelated "pause" concepts on a subscription and the docs are explicit that they are
different:

1. **`status=paused`** — only reachable automatically, when a trial ends with no payment method on
   file. No invoices are generated while paused. Resumed when the customer adds a payment method.
2. **`pause_collection`** (an object param on `subscriptions.update`, independent of `status`) — spec3.json:
   "If specified, payment collection for this subscription will be paused. Note that the subscription
   status will be unchanged and will not be updated to `paused`." Takes `behavior` ∈
   `[keep_as_draft, mark_uncollectible, void]` and optional `resumes_at` (unix time). Docs prose
   (spec3.json, verbatim, from the `status` field description): "which still generates invoices and
   leaves the subscription's status unchanged." So `pause_collection` **does** generate invoices — it
   just decides what happens to them (kept as drafts / marked uncollectible / voided) rather than
   attempting collection — whereas `status=paused` generates **no** invoices at all.

## Additional status-adjacent params found in `spec3.json` worth modeling

- `trial_end` (create/update) — unix timestamp or the literal string `"now"`; "Can be at most two years
  from `billing_cycle_anchor`." Setting it updates `billing_cycle_anchor` to the `trial_end` value.
- `trial_from_plan` (boolean) — apply the price's own `trial_period_days`; mutually exclusive with
  setting `trial_end` at the same time ("Setting this flag to `true` together with `trial_end` is not
  allowed").
- `billing_cycle_anchor` on update — enum `[now, unchanged]` only (not an arbitrary timestamp on
  update — that's a create-only free timestamp param). `now` resets the anchor to the current time,
  which is also one of the three named triggers for `proration_behavior` to matter (see
  `proration-arithmetic.md`).

## Gaps / open questions

1. **Unpaid → active recovery path** — not settled from docs prose alone; see above. Would be settled
   by a recorded API trace or (if reachable in a future session) a direct read of
   <https://docs.stripe.com/billing/subscriptions/overview>.
2. **`pending_if_incomplete` create-time rejection** — sourced from a WebSearch snippet quoting docs
   prose, not independently confirmed against `spec3.json`'s per-endpoint enum restriction. Low risk
   (docs are unambiguous) but flagged since I could not open-fetch the source page to double check
   context.
3. I could not obtain the *exact* end-to-end sequence Stripe uses to decide `canceled` vs `unpaid` at
   the end of dunning ("depending on your subscription settings") beyond confirming it's a
   Dashboard/API-configurable choice — see `dunning-and-retries.md` for what was recovered on that.

## Sources

- `research/stripe-openapi/spec3.json`, `components.schemas.subscription.properties.status` and
  `.payment_behavior`/`.pause_collection`/`.cancel_at_period_end`/`.trial_end`/`.billing_cycle_anchor`
  descriptions — API version `2026-08-26.dahlia`, fetched 2026-09-18 (see repo `research/MANIFEST.md`).
  This is the strongest source in this document; the enum descriptions are Stripe's own docs prose
  embedded in the machine-readable spec.
- WebSearch synthesis quoting <https://docs.stripe.com/api/subscriptions/object> and
  <https://docs.stripe.com/api/subscriptions/create> — accessed 2026-09-18 via WebSearch (direct fetch
  blocked by session egress policy).
- <https://docs.stripe.com/billing/subscriptions/trials#create-free-trials-without-payment> and
  <https://docs.stripe.com/billing/subscriptions/pause-payment> — cited by spec3.json's own
  description text; not independently fetched.
