---
status: draft
---

# Phase 14: Proration and Dunning

## Overview

The behavior spanning Phases 12 and 13: `billing/proration.py` (the pure
line engine) and `billing/dunning.py` (the retry envelope), wired into the
subscription/item update, cancel and invoice-collection paths. Conformance
scenario 1 records as cassette `01_proration_half_cent`.

Every wire shape below was probed live at `2026-08-26.dahlia`
(2026-09-21, sandbox account) before this plan was written — the three probe
trails `probe_proration`, `probe_proration_invoices` and
`probe_proration_settlement` are committed beside the cassette — and the
probes **corrected the design's one assumption**:

- **Proration lines round by FLOORING the exact rational**, not round-half-up.
  An engineered tie (fraction exactly 1/400: credit −2.5¢ → **−3**, debit
  +4.5¢ → **+4**) and the documented case (−666.67 → −667, +333.33 → +333)
  agree only under floor. `round_cents_half_up` is therefore never built.
- **Quantity-only reproration is whole-item** (recorded: quantity 1→3 at f
  credits `floor(f×1×1000) = −3` and debits `floor(f×3×1000) = +7`, a delta
  would be +5) — closing the `xfail` the design carried.
- **The pending proration item's wire body**: `parent =
  {type: subscription_details, subscription_details: {subscription,
  subscription_item}}`, `pricing.price_details` naming the OLD/NEW price (no
  mint), `unit_amount_decimal: null`, `discountable: false`,
  `frozen_fields: ["pricing", "quantity", "discounts"]`, `net_amount =
  amount`, credit quantity = old quantity, debit quantity = new quantity,
  `proration_details.credited_items = {type: "invoice_line_items",
  invoice_line_item_details: {invoice, invoice_line_items}}` on the credit
  (pointing at the invoice that billed the period), `null` on the debit.
- **Descriptions**: `Unused time on <product> after <DD Mon YYYY>` /
  `Remaining time on [N × ]<product> after <DD Mon YYYY>` — the date is the
  proration moment (recorded: an 11 Oct proration date against a 21 Oct
  period end says "after 11 Oct 2026").
- **`always_invoice`**: one `billing_reason: subscription_update` invoice,
  swept, finalized and **paid in the call** (`period_start` = the item
  period's start, `period_end` = the update moment), charge `attempt_count:
  1` when the net is collectable. A **net below the minimum chargeable
  (recorded: totals of 1¢ and 4¢)** is never charged: the invoice settles
  `paid` with `attempted: true, attempt_count: 0` and the amount **rolls
  onto `customer.balance`** as owed (next invoice's `starting_balance`).
- **A net-negative proration invoice** (the documented −334) settles
  `amount_due: 0`, `attempted: true, attempt_count: 0`,
  `ending_balance: −334`, and the balance row's type is
  **`applied_to_invoice`** with a negative amount — correcting §3.5's
  `adjustment` guess (recorded via `GET /v1/customers/{c}/balance_transactions`).
- **`proration_behavior: none`**: nothing lands (the pending list is
  byte-identical before and after).
- **`DELETE /v1/subscriptions/{id}?prorate=true`** (no `invoice_now`): one
  PENDING credit-only item, full-remainder fraction, period
  `[cancel moment, period end)`.
- **`DELETE …prorate=true&invoice_now=true`**: additionally one
  `billing_reason: subscription_cycle` invoice carrying the credit; a
  negative-total final invoice is created un-numbered and **automatically
  marked uncollectible ~5 s later** (async live; collapsed synchronous here,
  the dispute-settle precedent) — and the credit never reaches the customer
  balance.
- **`billing_cycle_anchor: "now"` on update** (recorded on the flexible-mode
  account): accepted, the item periods restart `[now, now + interval)`, and
  a stub "Remaining time" debit item lands for the sliver between the old
  and new cycle ends. The classic-mode shape this world serves is the
  machine table's ruling (truncate + prorate per `proration_behavior`),
  declared: the account cannot produce it.

## Steps

1. **`billing/_money.py`** — `floor_cents(x: Fraction) -> int` (floor of the
   exact rational; the recorded proration rule, citations in the docstring).
   `round_half_up` stays fee-only.
2. **`billing/proration.py`** (new) — `ItemConfig` (adds `product_name`,
   `product_id` over the design, for the descriptions and pricing blocks),
   `ProrationLine`, `proration_fraction` (clamped, `WorldBug` on a
   zero-length period), `proration_lines(old, new, *, period_start,
   period_end, proration_date, credited_line_ids_by_item)`: credits first
   then debits, whole-configuration, config-identical pairs skipped,
   `floor_cents` per line, lines emitted at f > 0 even when the rounded
   amount is 0 (recorded: the anchor stub item).
3. **`resources/invoiceitems.py`** — `insert_proration_item` beside
   `insert_invoice_item`: the recorded wire body verbatim (parent,
   pricing, frozen_fields, net_amount, quantity by side, credited_items
   wrapper), `invoiceitem.created` per row.
4. **`billing/invoicing.py`** — `invoice_item_line` reads proration rows
   (`proration`, parent's subscription_item, credited items, quantity);
   `compute_totals` builds the recorded proration LINE shape
   (`parent.subscription_item_details` with `invoice_item` inside, flat
   `proration_details.credited_items`, line amount = the row's amount,
   `quantity` = the row's quantity); `MINIMUM_CHARGEABLE = 50` and the roll
   branch in `pay_invoice` (0 < amount_due < 50 → balance + cbt
   `applied_to_invoice` + paid, `attempted: true, attempt_count: 0`,
   `amount_due` zeroed); `finalize_invoice`'s settlement cbt type is always
   `applied_to_invoice` (recorded); failed attempts set `next_payment_attempt`
   via `dunning` and use `next_attempt_number` (manual retries past the
   first never increment).
5. **`billing/dunning.py`** (new) — `RETRY_BLOCKING_DECLINE_CODES` (nine,
   cross-checked against `spec/enums.DECLINE_CODES`), `RetryPolicy` +
   `policy(ctx)` reading `ctx.state["account"]["dunning"]` (lazy defaults,
   the `ledger_spec` pattern), `next_attempt_number`, `will_reach_network`,
   `schedule_exhausted`, the declared even-spacing stand-in for
   `next_payment_attempt`, `record_failed_attempt` (blocked retries move
   the counter and schedule but write no charge row and emit no event), and
   `apply_end_of_schedule` (cancel / mark_unpaid / leave_past_due, the open
   invoice left open).
6. **`billing/subscription_lifecycle.py`** — `_proration_side_effects`:
   config diff → `proration_lines` → `insert_proration_item` rows →
   behavior dispatch (`create_prorations` default; `always_invoice` =
   sweep-invoice `subscription_update`, finalize, settle per the recorded
   rules, `send_invoice` left draft, a failed charge → `past_due`;
   `none` = nothing). Wired into `apply_update` (which now receives
   `proration_behavior` / `proration_date` and `billing_cycle_anchor`,
   whose `"now"` truncates, prorates, then rolls the periods and anchor),
   `_apply_items_update`, and `cancel_subscription` (`prorate` credit item;
   `invoice_now` final invoice: negative/zero total → the uncollectible
   collapse, positive → collect).
7. **`resources/subscriptions.py` + `resources/subscription_items.py`** —
   pass the proration family through instead of popping (item
   create/update/delete route into the same side effects); `SUB_UPDATE`
   gains `billing_cycle_anchor ∈ {now, unchanged}`.
8. **Record cassette `01_proration_half_cent`** (`tools_dev/scenarios/
   s01_proration_half_cent.py`) — the tie (fraction exactly 1/400 on both
   lines), the quantity-only whole-item case, the documented −667/+333/−334
   at an exact third, the minimum-chargeable roll with the balance and
   `balance_transactions` reads, `create_prorations` pending items,
   `none`, and the two DELETE shapes. The anchor reset stays in the probe
   trail only (the flexible-mode shape is not this world's).
9. **`tests/conformance/allowed_differences.py`** — the Phase 14 block:
   `**.subscription_item` id pairs, the proration-description date
   predicate, the uncollectible-collapse status pair (scoped), and the
   STRUCTURAL entries (floor-rule correction provenance, the
   minimum-chargeable constant, the uncollectible collapse, the classic
   anchor-reset cut, dunning's unrecordability on this account).
10. **Spec corrections** — `billing_engine.md` §"The one assumption" (floor
    replaces half-up; the quantity case closed; §3.5's `adjustment` →
    `applied_to_invoice`), `functional_spec.md` §7 proration bullet and the
    §15 gap table rows for the tie-break and quantity reproration (both
    now recorded).
11. **Tests** — `tests/billing/test_proration.py`, `tests/billing/
    test_dunning.py`, routed additions in `tests/test_subscriptions.py`
    and `tests/test_subscription_items.py` (pending items, always_invoice
    bodies, cancel credits, the min-roll, the counter semantics), cassette
    01 joins the replay gate by the glob.

No schema change: every column the phase needs (`invoiceitems.proration`,
`proration_details`, `parent`, `net_amount`, `frozen_fields`;
`invoices.parent_subscription_proration_date`) shipped in Phases 12–13.
`fixtures/empty` stands.

## Tests

- `tests/billing/test_proration.py` — the documented −667/+333/−334 (floor);
  the recorded tie (−2.5 → −3, +4.5 → +4) with a comment naming cassette 01;
  net-then-round still differs (−333 ≠ −334); second-precision and
  exact-`Fraction` fraction tests; `proration_date` honoured and clamped;
  zero fraction → no lines; whole-item quantity reproration (the recorded
  −3/+7, not the +5 delta); upgrade/downgrade symmetry; cancel credit-only;
  `cancel_at_period_end` no lines; `floor_cents` on negatives.
- `tests/billing/test_dunning.py` — first attempt (manual or automatic)
  sets 1; manual retries never increment (routed `/pay`); automatic
  increments; hard-decline increments without a charge row or event; the
  nine codes exactly, all in the spec's 50-value set; the three
  end-of-schedule behaviors; exhausted-cancel leaves the invoice open;
  no automatic retry fires within an instance; `RetryPolicy` is not a
  subscription field; `next_attempt_number` table.
- `tests/test_subscriptions.py` (additions) — default
  `create_prorations` writes pending items with the recorded body;
  `always_invoice` pays through the card (attempt 1) and rolls sub-minimum
  nets to the balance (`applied_to_invoice`, negative totals credit);
  `none` writes nothing; `billing_cycle_anchor=now` truncates and rolls;
  DELETE `prorate`/`invoice_now` shapes (pending credit; uncollectible
  final invoice; the credit never balances); replay of cassette 01.
