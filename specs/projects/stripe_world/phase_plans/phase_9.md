---
status: complete
---

# Phase 9: Refunds and disputes

## Overview

`refunds` and `disputes` end to end: the two tables, partial refunds with the
running `amount_refunded` / `refunded` bookkeeping on the charge, over-refund
rejection (two distinct recorded refusals), the `expand[]=refunds` inline list
on the charge body, the dispute lifecycle driven by the magic dispute cards
(creation inside the charge attempt, evidence submission with the three magic
strings, `/close`, the refund gate on `is_charge_refundable`), the four legacy
charge-scoped aliases, and the event families both slices emit. Every behavior
below was probed live at `2026-08-26.dahlia` (2026-09-20, sandbox account)
before this plan was written; the cassette is recorded as
`05_refunds_disputes`.

## Probed behavior this phase implements (recordings win)

- **Refund create** (`POST /v1/refunds` with `charge`, or `payment_intent`):
  status `succeeded` immediately on the sync cards; `amount` omitted → the
  full remaining amount; `reason` echoed (create param enum is
  `duplicate`/`fraudulent`/`requested_by_customer` only — an
  `expired_uncaptured_charge` create is refused with
  `Invalid reason: must be one of duplicate, fraudulent, or requested_by_customer`,
  param `reason`, no code); `charge` and `payment_intent` cross-fill from each
  other; `customer`, `payment_method`, `currency` derive from the charge;
  `receipt_number`/`next_action`/`instructions_email` null;
  `destination_details` = `{card: {reference_status: "pending", reference_type:
  "acquirer_reference_number", type: "refund"}, type: "card"}` on a card charge;
  `balance_transaction` is the ledger's (Phase 11 — null here, allow-listed).
- **Bookkeeping**: `charge.amount_refunded` accumulates, `refunded` flips at
  full; `refunds` stays **absent** unexpanded and expands to
  `{object: "list", data, has_more, total_count, url: "/v1/charges/{id}/refunds"}`
  (probed; `total_count` is undeclared by the pinned spec's inline schema and
  is therefore omitted here, allow-listed — the Phase 4/8 ruling that the spec
  is the authority for shape).
- **Refund refusals** (all recorded verbatim): amount > remaining → 400,
  `param: "amount"`, no code, `Refund amount ($40.00) is greater than
  unrefunded amount on charge ($30.00)` (currency symbol per charge currency —
  `$`/`€` probed); on a fully-refunded charge → 400 `charge_already_refunded`,
  `Charge ch_… has already been refunded.`; amount ≤ 0 → 400
  `parameter_invalid_integer` `This value must be greater than or equal to 1.`,
  param `amount`; neither charge nor intent → 400, no code/param, `One of the
  following params should be provided for this request: payment_intent or
  charge.`; unknown charge → 404 `resource_missing` with **`param: "id"`**;
  uncaptured charge → the recorded "You must cancel the PaymentIntent …"
  refusal; failed charge → `This PaymentIntent (pi_…) does not have a
  successful charge to refund.`; disputed charge with
  `is_charge_refundable = false` → 400 `charge_disputed`, `Charge ch_… has
  been charged back; cannot issue a refund.`
- **Refund reads/writes**: list filters `charge` / `payment_intent` / `created`;
  the scoped reads under `/v1/charges/{c}/refunds` (url is the scoped path; a
  scoped retrieve of another charge's refund is a 404 `No such refund: 're_…'`,
  param `refund`); `POST /v1/refunds/{refund}` is metadata-update only;
  `POST /v1/refunds/{refund}/cancel` on a succeeded refund → 400
  `Canceling this refund is unsupported.` (no code — and no success branch
  exists here: a pending refund requires the async refund cards, which a
  frozen clock cannot settle; declared difference).
- **The two legacy create aliases**: `POST /v1/charges/{c}/refund` (singular)
  answers with the **charge** (amount_refunded updated); `POST
  /v1/charges/{c}/refunds` (plural) answers with the refund. Both share the
  create core; the singular's differing response is why it gets its own
  two-line handler rather than literally sharing `PostRefunds`'s — the alias
  table's "share a handler" reading corrected by the recording.
- **Refund events**: `refund.created` (the refund) then `charge.refunded` (the
  charge). The live API's async `refund.updated` + `charge.refund.updated`
  pairs (acquirer-reference availability flipping) are network-settlement
  timing a frozen clock cannot model — declared structural difference.
- **Dispute ids are `du_`**, not `dp_` — every probe at the pinned version
  mints `du_…` (data_model §3.2's `dp_` row corrected in this phase).
- **Dispute creation** happens inside the charge attempt: charging a
  dispute-tagged PM succeeds, then `charge.dispute.created` +
  `charge.dispute.funds_withdrawn` fire *between* `charge.succeeded` and
  `payment_intent.succeeded` (recorded event order). The charge flips
  `disputed: true` and carries `dispute: du_…` (undeclared on charge at this
  version → omitted here, allow-listed; `disputed` stays true after
  resolution). Card flavors: `tok_visa_createDispute` → `needs_response`,
  reason `fraudulent`, `payment_method_details.card` `{brand, case_type:
  "chargeback", network, network_reason_code: "10.4"}`; the product-not-
  received variant → reason `product_not_received`, code `13.1`; the inquiry
  variant → `warning_needs_response`, `is_charge_refundable: true` (no funds
  pulled), `case_type: "inquiry"`, code `10`. **The live token spellings are
  `tok_visa_createDispute` &c.** — magic_cards' `tok_card_createDispute` is
  not a token the live API knows ("There is a part of the token that is not
  valid: 'card'."), corrected here the way Phase 8 corrected the 3DS
  spellings.
- **Dispute body**: `evidence` is the 28-field object, all null except
  `enhanced_evidence: {}` plus Stripe's automatic enrichment from the customer
  record (`customer_name` / `customer_email_address` appear without being
  submitted, timing varies by track) — enrichment is not modeled; submitted
  evidence is stored verbatim (declared difference, allow-listed).
  `evidence_details` = `{due_by: <end of UTC day, created + 8 days — the
  single-day-of-week observation this model rests on>, enhanced_eligibility:
  {}, has_evidence, past_due: false, submission_count}`. `balance_transactions`
  holds the withdrawal `adjustment` row from creation (the reversal joins on a
  win) — the ledger lands in Phase 11, so this phase emits `[]` and the
  recorded rows are allow-listed; the singular `balance_transaction` field on
  the live body is undeclared → omitted.
- **Evidence submission** (`POST /v1/disputes/{dispute}` with `evidence`
  and/or `metadata`, `submit` accepted): merges the evidence, sets
  `has_evidence`, increments `submission_count`, moves `needs_response →
  under_review` (inquiries: `warning_needs_response → warning_under_review`).
  Plain evidence stays `under_review` (probed: no async settle). The three
  magic strings in `uncategorized_text` settle **synchronously** in this
  world — live, `winning_evidence` answers `under_review` and flips to `won`
  (~5 s later, with `charge.dispute.funds_reinstated` then
  `charge.dispute.closed`); `losing_evidence` settles `lost`;
  `escalate_inquiry_evidence` escalates an inquiry to `needs_response`. A
  frozen clock cannot wait out issuer review, so the settle is collapsed into
  the submitting call — the phase's headline declared difference, scoped
  replay entries on the submit response's `status` / `is_charge_refundable`.
  A won dispute flips `is_charge_refundable: true` and reopens the refund
  path (probed end to end). Events on submit: `charge.dispute.updated` then
  `charge.updated`.
- **`POST /v1/disputes/{dispute}/close`**: `lost`, synchronously even live
  (recorded); events `charge.dispute.closed` then `charge.updated`. Any
  update or close against `won`/`lost` → 400 `This dispute is already closed`
  (no code, no param).
- **Dispute reads**: `GET /v1/disputes` filters `charge` / `payment_intent` /
  `created`; a missing dispute is 404 `No such dispute: 'du_…'`, param
  `dispute`; `GET /v1/charges/{c}/dispute` on an undisputed charge is 404
  `No dispute for charge: ch_…` (no code/param), on a bogus charge 404 `No
  such charge: 'ch_…'`, param `charge`; `POST /v1/charges/{c}/dispute` and
  `/close` are aliases of the dispute update/close; the live body's
  `customer: null` is undeclared → omitted.

## Steps

1. **`src/stripeapi/schema/002_payments.sql`** — the `refunds` (20 cols) and
   `disputes` (15 cols) tables + their five indexes, DDL verbatim from
   `components/data_model.md` §4 **except** the two
   `REFERENCES balance_transactions (id)` clauses on refunds, which land in
   Phase 11 with the ledger table (the recorded Phase 8 precedent for
   charges, same comment shape).
2. **`src/stripeapi/_ids.py`** — dispute prefix `dp_` → `du_` (probed).
   Correct data_model.md §3.2's row in the same change.
3. **`src/stripeapi/billing/magic_cards.py`** — the dispute token spellings
   corrected to the probed family (`tok_visa_createDispute`,
   `tok_createDispute`, `tok_visa_createDisputeInquiry`,
   `tok_visa_createDisputeProductNotReceived`); `pm_card_*` aliases stay.
4. **`src/stripeapi/serialize/expand.py`** — the inline-list edge: a field
   whose property is an inline list envelope (`properties.data.items.$ref`)
   becomes an `inline_list` edge, inflated from the child table scoped to the
   parent id through a registry (`register_inline_list((object, field) →
   child object, scope column, url template)`), first 10 by `x_seq DESC`,
   `has_more` beyond that, no `total_count` (undeclared). `refunds.py`
   registers `("charge", "refunds")`.
5. **`src/stripeapi/resources/refunds.py`** — FieldMap (no `livemode` — one
   of the four objects without it; constants for `transfer_reversal` /
   `source_transfer_reversal` / `customer_account`), ParamSpecs (create with
   `required_one_of` handled in-handler for the recorded message; update
   metadata-only; scoped variants), hand-written `create` (resolution,
   guards, row insert, charge bookkeeping, event pair), `cancel` refusal,
   engine-served list/retrieve/update, `lookup_for_refund` shared with the
   legacy aliases.
6. **`src/stripeapi/resources/disputes.py`** — FieldMap (derived
   `balance_transactions: []` until Phase 11), ParamSpecs (update: evidence
   text fields + `submit` + metadata; close), hand-written `update` (merge,
   counter, status move, magic-string settle, events) and `close`, the
   charge-scoped read + the two charge-scoped aliases, and
   `maybe_create_dispute(ctx, charge_row, pm_row)` called by the confirm path.
7. **`src/stripeapi/resources/payment_intents.py`** — in `_confirm`'s success
   arm, after `charge.succeeded` and before the intent event: read the PM's
   dispute tag and call `disputes.maybe_create_dispute` (writes the row,
   flips `charges.disputed`, emits the recorded event pair in order).
8. **`src/stripeapi/dispatch/routes.py`** — wire the seventeen routes (5
   `/v1/refunds*`, 5 `/v1/charges/{c}/refund(s)…`, 4 `/v1/disputes*`, 3
   `/v1/charges/{c}/dispute…`), the first four legacy aliases in the table to
   go live.
9. **`tests/conformance/allowed_differences.py`** — the refunds/disputes
   block: `**.charge` (id rule), `**.dispute` upgraded for the `du_` shape,
   `**.destination_details.card.reference{,_status}` (network timing),
   `**.refunds.total_count` (undeclared live field), `**.balance_transactions`
   (ledger is Phase 11's), `**.due_by` (clock), the two evidence-enrichment
   entries, the dispute body's `customer`/`next_action`-shaped null-vs-absent
   entries, and the scenario-05 `body.status` / `body.is_charge_refundable`
   settle-collapse predicates.
10. **Record `05_refunds_disputes`** — the flow listed in Overview, probed
    step-for-step: partial + remainder + both over-refund refusals, the
    by-intent refund, the one-of and unknown-charge refusals, update +
    cancel-refusal, the three lists, the legacy singular create, the charge
    bookkeeping reads incl. `expand[]=refunds`, the dispute card's three
    flavors, the disputed-charge refund refusal, `winning_evidence` to `won`
    (with a recorded settle pause) and the refund that reopens,
    `escalate_inquiry_evidence`, `/close` to `lost`, the already-closed
    refusals, the scoped reads' 404s.
11. **Tests** — `tests/test_refunds.py`, `tests/test_disputes.py`; additions
    to `test_tools.py` (wired surface), `test_expand.py` (inline list),
    `test_ids.py`/fixtures prefixes (`du_`), and the invariant I7's SQL over
    a refund sequence. Engine and replay tests need no new code: the autouse
    schema-conformance fixture covers every body, and cassette 05 joins the
    parametrized replay gate.
12. **Spec corrections** — data_model §3.2 (`du_`), dispatcher §3.1.3 (the
    singular refund alias answers with the charge), billing_engine's error
    table (over-refund on a partially refunded charge is the
    amount-naming refusal, not `charge_already_refunded`).

## Tests

- `test_refunds.py` — the refund body's every field (status, derived
  customer/PM/intent, destination_details, constants); partial → bookkeeping
  (`amount_refunded`, `refunded` false) → remainder → `refunded` true; both
  over-refund refusals verbatim; zero/negative amount; the one-of refusal;
  unknown charge (`param: "id"`); by-intent create; refund on uncaptured and
  failed charges; metadata update; cancel refusal; the three lists incl.
  scoped url and cross-charge scoped-retrieve 404; events
  (`refund.created` then `charge.refunded`); `expand[]=refunds` envelope
  (10-cap, `has_more`, url); the legacy singular create answers with the
  charge; the async-refund cards never leave `succeeded` (declared).
- `test_disputes.py` — `du_` ids; the three card flavors' fresh bodies
  (status, reason, `payment_method_details.card`, `is_charge_refundable`);
  creation event order (`charge.succeeded`, `charge.dispute.created`,
  `charge.dispute.funds_withdrawn`, `payment_intent.succeeded`); evidence
  merge + `under_review` + counters; the three magic strings (won +
  refund-gate reopen, lost, inquiry escalation); `/close` synchronous lost;
  already-closed refusals; refund-against-dispute refusal and success after
  won; `due_by` shape; the scoped read's two 404s; lists; `disputed` staying
  true; `balance_transactions: []`.
- `test_expand.py` — inline-list inflation on retrieve and list bodies, the
  10-cap with `has_more`, nesting under `latest_charge.refunds`.
- Replay — cassette 05 joins `test_replay_conformance.py` (parametrized).
