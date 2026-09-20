---
status: complete
---

# Phase 11: Ledger and payouts

## Overview

`balance_transactions`, the computed `/v1/balance` read, and `payouts` end to
end — the phase where every money movement the earlier slices wrote gains its
ledger row. The charge, refund and dispute paths start writing
`balance_transaction` rows synchronously (charge bt at capture, refund bt at
creation, dispute withdrawal at creation/escalation and its reversal at win),
the dispute body's `balance_transactions` becomes ledger-derived, payouts draw
down the available balance with their own bt and reversal rows, and the ledger
invariant tests land. Every wire shape below was probed live at
`2026-08-26.dahlia` (2026-09-20, sandbox account) before this plan was
written; the cassette records as `11_ledger_payouts`.

**Recording constraint, probed, not assumed**: the sandbox cannot produce a
successful payout — it has no external account in any currency
(`Sorry, you don't have any external accounts in that currency (cad).`), the
recording key cannot add one (`POST /v1/accounts/{id}/external_accounts` →
403 `more_permissions_required`), and top-ups are unsupported for the
account's country (`Top-up creation is not supported for country CA and
currency USD.`). The payout success/cancel/reverse shapes are therefore
spec-derived and unit-tested, with the unrecordability declared in the
allow-list's structural section; the cassette records every account-independent
refusal verbatim.

## Probed behavior this phase implements (recordings win)

- **`GET /v1/balance`**: `{object: "balance", livemode: false, available: […],
  pending: […], refund_and_dispute_prefunding: {available: […], pending: […]}}
  — each entry `{amount, currency, source_types: {card: <amount>}}`; the
  prefunding entries carry no `source_types`; `instant_available`,
  `connect_reserved` and `issuing` are absent when empty. This world emits the
  prefunding block's keys only when a prefunding ledger row exists and the
  three optional keys never (all-omitted is spec-legal: only `available`,
  `livemode`, `object`, `pending` are required).
- **Charge bt** (cassette 04): `type: "charge"`, `reporting_category:
  "charge"`, present after manual capture in the capture response; null on the
  confirm-time body of an auto-captured charge (the async settlement window —
  later reads carry it). This world writes it synchronously at every capture;
  the confirm-body null is a declared flipped allow-list entry. The bt amount
  is the **captured** amount; the fee is the account fee schedule.
- **Refund bt** (probed + cassette 05): `type: "refund"`,
  `reporting_category: "refund"`, `amount: -<refund amount>`, `fee: 0`,
  `fee_details: []`, `description: "REFUND FOR CHARGE (<charge description>)"`
  — the parens only when the charge carries a description (unrecorded corner,
  declared) — and `status: "pending"` on a fresh row. Resolves the research
  gap: the type is `refund`, not `payment_refund`, at this version.
- **Dispute ledger rows** (cassette 05 + probes): the chargeback track writes
  one withdrawal row at creation — `type: "adjustment"`,
  `reporting_category: "dispute"`, `amount: -<dispute amount>`, `fee: 1500`,
  `fee_details: [{amount: 1500, application: null, currency,
  description: "Dispute fee", type: "stripe_fee"}]`,
  `description: "Chargeback withdrawal for <charge id>"` — and a win adds the
  reversal — `type: "adjustment"`, `reporting_category: "dispute_reversal"`,
  `amount: +<dispute amount>`, `fee: 0`, `fee_details: []`,
  `description: "Chargeback reversal for <charge id>"`. The received fee is
  **never refunded and no countered-fee row exists** — the recording corrects
  functional spec §7's two-fee reading. Inquiry (`warning_*`) disputes write
  **no** ledger rows until escalated; the escalation pulls the funds then.
- **The `source` filter quirk** (probed): `GET /v1/balance_transactions?
  source=<charge|refund>` lists the rows, but `?source=<dispute>` answers an
  empty page even though the withdrawal row's own `source` field names the
  dispute — reproduced via a new `never_prefixes` list-filter knob.
- **Reads and refusals** (probed verbatim): `No such balance transaction:
  'txn_nope'` under `param: "id"`; `No such payout: 'po_nope'` under
  `param: "payout"` on retrieve, cancel and reverse alike; unknown
  `currency` / `type` / `status` filter values answer empty pages; payout
  create answers `parameter_invalid_integer` / `This value must be greater
  than or equal to 1.` (`param: "amount"`) for 0 and negatives,
  `parameter_missing` without an amount, and the full-list
  `Invalid currency: …` refusal for a bogus currency; `GET /v1/payouts` on an
  account with none answers the empty list envelope.
- **`expand[]=source`** on a bt inflates the right polymorphic object
  (verified on a dispute-sourced row) — the resolver gains prefix-dispatched
  inflation for `balance_transaction.source`.

## Steps

1. **`src/stripeapi/schema/002_payments.sql`** — the `balance_transactions`
   (17 cols + `x_payout`) and `payouts` (22 cols) tables with their indexes,
   DDL verbatim from `components/data_model.md` §4, and the four deferred
   `REFERENCES balance_transactions (id)` clauses joined onto
   `charges.balance_transaction` / `charges.failure_balance_transaction` /
   `refunds.balance_transaction` / `refunds.failure_balance_transaction`
   (the Phase 8/9 comments' promise). Rebuild `fixtures/empty`
   (`python fixtures_src/generate.py` after removing the committed dir).
2. **`src/stripeapi/billing/_money.py`** — `FeeSchedule` (290 bps + 30¢) and
   `stripe_fee(amount, schedule)` (percent part rounded half-up via
   `Fraction`, plus fixed; never negative). No floats.
3. **`src/stripeapi/billing/ledger.py`** — `LedgerSpec`
   (`fees`, `settlement_business_days=2`, `dispute_received_fee=1500`) and
   `ledger_spec(ctx)` reading `ctx.state["account"]["ledger"]` with defaults;
   `record(...)` (mints `txn_`, checks `type_` against the 50-value set,
   stores `net = amount - fee`); `available_on(created_iso, type_, spec)`
   (midnight UTC of created's day + N settlement days; `created` itself for
   payout rows — the payout leaves the balance immediately);
   `bt_status(available_on_iso, now_iso)`; `read_balance(ctx)` (the computed
   `/v1/balance` object: available/pending summed per currency off the
   `available_on` split, `source_types: {card: amount}`, optional keys
   omitted); `available_cents(ctx, currency)`; `create_payout` /
   `cancel_payout` / `fail_payout` / `reverse_payout` / `settle_payout`
   (settle and fail are the fixture/test-only transitions a frozen clock
   cannot reach, the dispute-settle precedent). Payout create sweeps the
   currency's available unswept rows (`x_payout`) and answers
   `reconciliation_status: "completed"`; cancel/fail reverse via a second bt
   (`payout_cancel` / `payout_failure`) stored in
   `failure_balance_transaction` and unsweep; reverse (paid only) writes the
   negative-amount reversing payout with `original_payout` / `reversed_by`
   cross-set and its own `payout` bt.
4. **`src/stripeapi/dispatch/params.py`** — a `currency` Param kind: the
   probed payout-currency refusal message with its own transcribed list
   (the payout spelling differs from the map-parameter one: no
   `eurc`/`usdt`/`open_usd`), same maintenance-choice declaration.
5. **`src/stripeapi/dispatch/resource.py`** — `ListFilter.never_prefixes`:
   values starting with one of these prefixes answer an empty page (the bt
   `source`-filter dispute quirk).
6. **`src/stripeapi/resources/balance_transactions.py`** — FieldMap (no
   `livemode`: one of the four objects without it), engine-served list
   (filters `created`, `currency`, `payout` → `x_payout`, `source` with
   `never_prefixes=("du_",)`, `type`) and retrieve
   (`missing_path_param="id"`, `error_name="balance transaction"`).
7. **`src/stripeapi/resources/balance.py`** — the computed read handler.
8. **`src/stripeapi/resources/payouts.py`** — FieldMap (constants for the
   Connect nulls; `destination` a `ba_` stub minted when absent), engine
   list/retrieve/update (update is metadata-only), hand-written
   `create` (required `amount` + `currency` via the currency kind; the
   recorded `>= 1` amount refusal raised by the handler; draws on
   `available_cents`, else `balance_insufficient`; emits `payout.created`),
   `cancel` (pending only; wrong-state refusal in the recorded
   status-list family, declared unrecorded), `reverse` (paid only; same
   family), thin wrappers over `billing/ledger.py`.
9. **`src/stripeapi/serialize/expand.py`** — prefix-dispatched inflation for
   the polymorphic `balance_transaction.source`
   (`ch_`→charge, `re_`→refund, `du_`→dispute, `po_`→payout).
10. **Money-path wiring** — `charges.insert_charge` writes the charge bt when
    the attempt captures and sets the column; `payment_intents.capture`
    writes it for the hold; `refunds.create_refund` writes the refund bt on
    the synchronous path (a pending refund reserves nothing);
    `disputes.maybe_create_dispute` writes the chargeback withdrawal;
    `_apply_update` writes the escalation withdrawal and the win reversal;
    the dispute serializer derives `balance_transactions` from the ledger.
11. **`src/stripeapi/dispatch/routes.py`** — wire the nine routes (balance 1,
    balance_transactions 2, payouts 6).
12. **Record `11_ledger_payouts`** — CAD-native charges (no FX on either
    side): a confirmed charge, a manual hold + partial capture, a refund, a
    chargeback dispute through `winning_evidence`, an inquiry dispute (no
    rows), bt listings by `source` (charge, refund — and the dispute quirk
    answering `[]`), bt retrieve + `expand[]=source` on both flavors, the
    filter-empty pages, `GET /v1/balance`, the payout create refusals
    (amount 0 / negative / missing / bad currency), the three missing-id
    404s, and the empty payout list.
13. **`tests/conformance/allowed_differences.py`** — flip
    `**.balance_transaction` (the async-window declaration: recorded null or
    txn vs replayed txn, predicated to txn-shaped pairs) and
    `**.balance_transactions` (recorded FX-converted rows vs this world's
    native ones); new entries `**.available_on` / `**.arrival_date`
    (int-pairs, the settlement-schedule-plus-clock reason), `**.fee` /
    `**.net` (int-pairs — the processing fee is account pricing),
    `**.fee_details` (both lists), a `**.source` prefix-pair entry for bt
    rows, scenario-scoped `body.available` / `body.pending` /
    `body.refund_and_dispute_prefunding` (the recording account's pre-existing
    balance is account state, not scenario state); STRUCTURAL entries for the
    payout success path's unrecordability and the payout-currency
    transcription.
14. **Tests** — `tests/test_ledger.py` (the bt body, the balance read and its
    split, the invariants: `net = amount - fee` everywhere, one charge bt per
    captured charge with the captured amount, refund bt fee-0, dispute
    withdrawal/reversal pairing, every `source` resolves in exactly one
    table, no bt references a missing sweep) and `tests/test_payouts.py`
    (create draws down available and answers the spec-derived body, the
    insufficient refusal, cancel restores the funds and unsweeps, reverse on
    paid, settle/fail helpers, filters and 404s); flip the four existing
    `balance_transaction is None` assertions in the money-path suites; the
    nine op_ids join `test_tools.py`'s wired list.
15. **Spec correction** — functional_spec §7's dispute-fee sentence: the
    pinned recording shows the received fee kept on a win and no countered-fee
    row; correct the sentence in place.

## Tests

- `test_ledger.py` — the charge bt's every field (type/reporting_category/
  amount/fee/net/fee_details/description/status/currency, `livemode` absent);
  bt null on the uncaptured hold and set at partial capture with the captured
  amount; the refund bt (negative amount, fee 0, the REFUND FOR CHARGE
  description, absent on the pending-refund card); the dispute withdrawal and
  win-reversal rows incl. the inquiry-has-none rule; the escalation
  withdrawal; `/v1/balance` before and after each movement (available vs
  pending on the `available_on` split, `source_types`, empty-array shape on
  an empty ledger); bt list filters (`source` incl. the dispute quirk,
  `currency`, `type` bogus → `[]`, `payout` after a sweep); bt retrieve + 404
  spelling; `expand[]=source` on charge- and dispute-sourced rows; the
  invariants as SQL over `instance.inspect()`.
- `test_payouts.py` — the created body's spec-derived shape (status pending,
  automatic false, method standard, source_type bank_account,
  reconciliation_status completed, destination `ba_` stub, arrival_date);
  available draw-down and `balance_insufficient`; the metadata update; cancel
  (funds back, `failure_balance_transaction` set, sweep cleared, payout.canceled
  event); settle then reverse (negative reversing payout, cross-links, events);
  fail_payout; the recorded create refusals verbatim; the three 404s; the
  lists and filters.
- Replay — cassette 11 joins `test_replay_conformance.py` (parametrized).
