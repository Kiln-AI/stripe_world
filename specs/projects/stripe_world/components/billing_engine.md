---
status: complete
---

# Component: Billing Engine

`src/stripeapi/billing/` — the behavior that spans resources. Architecture
[§8](../architecture.md); functional spec [§5](../functional_spec.md) (time) and
[§7](../functional_spec.md) (billing behavior); research lane
[`billing-and-money-behavior/`](../research/stripe-billing-and-payments/billing-and-money-behavior/),
of which [`gap-closure-2026-09-18.md`](../research/stripe-billing-and-payments/billing-and-money-behavior/gap-closure-2026-09-18.md)
is the authoritative layer wherever it disagrees with the earlier files.

This is where the project's fidelity claim actually lives. Everything below is stated to be
implemented from without a second decision.

## Purpose and Scope

### In scope

Five modules, one concern each:

| Module | Owns |
|---|---|
| `subscription_lifecycle.py` | The eight-status machine, its triggers, side effects and events |
| `invoicing.py` | The five-status invoice machine, line construction, totals arithmetic, `automatically_finalizes_at` |
| `proration.py` | `proration_lines(...)` and the cent-rounding rule |
| `dunning.py` | The retry-configuration envelope, `attempt_count`, hard-decline gating, the three end-of-schedule outcomes |
| `ledger.py` | `balance_transaction` creation, fee computation, `available_on`, `available`/`pending` derivation, payout draw-down |

The organising rule, stated once and applied throughout: **every piece of arithmetic is a pure
function of plain values.** `ctx` is passed only where a function must mint an id or read the clock,
and those functions are thin wrappers over the pure ones. `proration_lines`, `compute_totals`,
`stripe_fee`, `apportion` and every rounding helper take no `ctx` at all and are unit-testable
without a tool call, a database or an instance.

Second organising rule: **no floats anywhere.** Every intermediate value in the money path is an
`int` or a `fractions.Fraction`. A float in this component is a bug, and there is a test that greps
for one (see Test Plan).

### Not in scope

- Route matching, parameter validation, response construction, pagination, expansion — `dispatcher.md`
  and `cross_cutting.md`.
- The DDL for any table — `data_model.md`. This document names columns and states their invariants;
  it does not declare them.
- Row → API-object serialization — `data_model.md` (`serialize/`). This component produces and
  consumes **rows**, in Seahaven's storage convention (TEXT ISO timestamps, integer minor units).
  Unix-second conversion happens at the edge and never in here.
- Charge/PaymentIntent mechanics and magic-card decline selection — `resources/payment_intents.py`
  and `resources/charges.py`. The billing engine *calls* the payment path and *consumes* its outcome
  (`succeeded` / `card_error` + `decline_code`); it does not implement it.
- Dispute and refund lifecycle — `resources/disputes.py`, `resources/refunds.py`. The billing engine
  owns only their **ledger effects** (`ledger.py`).
- Credit notes and `customer_balance_transactions` as resources — `resources/credit_notes.py`. The
  billing engine owns the two points where the customer balance touches an invoice
  (`starting_balance` / `ending_balance` at finalization, and overpayment).
- Subscription schedules — `resources/subscription_schedules.py`. A schedule that advances a phase
  calls into `subscription_lifecycle.apply_update()`; the phase machine itself is not here.

### Schema drift this component is built on

Four facts about API version `2026-08-26.dahlia`, re-derived from `spec3.json` for this document,
that contradict what most Stripe documentation (and model recall) says. Getting any of them wrong
silently breaks the component:

1. **There is no `subscription.current_period_start` / `current_period_end`.** Confirmed by
   enumerating `components.schemas.subscription.properties`. The billing period lives on
   **`subscription_item`** (`current_period_start`, `current_period_end`, plus `billed_until`).
   The `cancel_at_period_end` parameter description still says "`current_period_end`", which is now
   a dangling reference in Stripe's own prose. This component therefore treats the *item* as the
   period-bearing object and derives a subscription-level period only where one is needed:

   ```
   sub_period_start = MIN(item.current_period_start) over the subscription's items
   sub_period_end   = MIN(item.current_period_end)   over the subscription's items
   ```

   `MIN` on the end, not `MAX`: the next thing that happens to the subscription is the *earliest*
   item boundary. For a single-item subscription — every case in the eval set — the two coincide.
2. **There is no top-level `invoice.subscription`.** The link is
   `invoice.parent.subscription_details.subscription`, with
   `invoice.parent.type = "subscription_details"`. Storage keeps a flat `subscription_id` column;
   the serializer builds the `parent` wrapper. There is also no top-level `invoice.days_until_due`
   (create-only param → `invoice.due_date`) and no top-level `invoice.paid` (use
   `status = 'paid'` / `amount_remaining = 0`).
3. **A line's provenance is `line_item.parent`**, a discriminated union with
   `type ∈ {invoice_item_details, subscription_item_details}`. `proration` (boolean) and
   `proration_details.credited_items` hang off **that** sub-object, not off the line.
4. **There is no top-level `discount.coupon`.** It is `discount.source`, with
   `source.type = "coupon"` and `source.coupon` holding the coupon.

## Public Interface

Types shared across the module are frozen dataclasses in `billing/_types.py`. `Money = int` (minor
units) throughout. `Rat = fractions.Fraction`.

### `billing/_money.py` — shared primitives, no `ctx`, no I/O

```python
def round_cents_half_up(x: Rat) -> int:
    """Round a non-negative exact rational to the nearest integer cent, ties away from zero.

    ASSUMPTION (the only one in the money path): Stripe's tie-break at an exact x.xx5 is
    undocumented for proration line items. See research gap-closure-2026-09-18.md item 1 and
    conformance scenario 1 (functional spec §12). Callers pass a NON-NEGATIVE magnitude and
    apply the sign themselves, so a tie always rounds away from zero.
    Raises ValueError on a negative argument, so the convention cannot be violated silently.
    """

def apportion(total: Money, weights: Sequence[Money]) -> list[Money]:
    """Split `total` across `weights` so the parts sum EXACTLY to `total`.

    Floor every share, then give the entire remainder to the LAST non-zero-weight entry.
    This is Stripe's documented coupon-across-items rule, NOT independent rounding:
    a $5 coupon split 1:2 over a $10 and a $20 item yields [166, 334], not [167, 333].
    (gap-closure-2026-09-18.md item 1, closing paragraph.) Never call round_cents_half_up here.
    """

def stripe_fee(amount: Money, schedule: FeeSchedule) -> Money:
    """percent part (round-half-up, DOCUMENTED for fees) + fixed part. Never negative."""

@dataclass(frozen=True)
class FeeSchedule:
    percent_bps: int = 290     # 2.90%
    fixed: Money = 30          # 30c
```

`round_cents_half_up` and `stripe_fee` both round half up, for **different reasons**, and the
codebase must not let the two blur: the fee rule is documented by Stripe
(`support.stripe.com/questions/rounding-rules-for-stripe-fees`), the proration tie-break is not.
Two functions, two comments, one conformance scenario attached to exactly one of them.

### `billing/proration.py`

```python
@dataclass(frozen=True)
class ItemConfig:
    subscription_item_id: str | None   # None for a brand-new item
    price_id: str
    unit_amount: Money                 # integer minor units, per unit
    quantity: int

@dataclass(frozen=True)
class ProrationLine:
    amount: Money                      # signed: negative = credit, positive = debit
    description: str                   # "Unused time on X after DD Mon YYYY" / "Remaining time on Y after ..."
    period_start: int                  # unix seconds; == proration_date
    period_end: int                    # unix seconds; == period_end
    price_id: str
    quantity: int
    subscription_item_id: str | None
    proration: bool = True
    discountable: bool = False         # spec3.json: "Always false for prorations."
    credited_line_ids: tuple[str, ...] = ()   # -> parent.proration_details.credited_items

def proration_fraction(period_start: int, period_end: int, proration_date: int) -> Rat:
    """Exact (period_end - proration_date) / (period_end - period_start), in SECONDS.

    proration_date is clamped into [period_start, period_end]. Raises WorldBug if
    period_end <= period_start (a zero-length period is an authoring error, not an agent error).
    """

def proration_lines(
    old_items: Sequence[ItemConfig],
    new_items: Sequence[ItemConfig],
    *,
    period_start: int,
    period_end: int,
    proration_date: int,
    currency: str,
    now_iso: str,
    credited_line_ids_by_item: Mapping[str, tuple[str, ...]] = {},
) -> list[ProrationLine]:
    """The whole proration algorithm. Pure: no ctx, no db, no clock, no ids.

    Returns credit lines first (matching Stripe's reverse-chronological pending-item
    ordering), then debit lines. Returns [] when the fraction is 0 or when old_items and
    new_items are configuration-identical.
    """
```

`proration_lines` is deliberately *not* given `ctx`. Its caller in `subscription_lifecycle.py`
mints the `il_` ids and turns each `ProrationLine` into an `invoiceitems` row.

### `billing/invoicing.py`

```python
# --- pure arithmetic ---------------------------------------------------------

@dataclass(frozen=True)
class LineInput:
    source: Literal["subscription_item", "invoice_item"]
    source_id: str
    price_id: str | None
    unit_amount: Money
    quantity: int
    period_start: int
    period_end: int
    description: str
    discountable: bool
    item_discount_ids: tuple[str, ...]     # item-scope discounts
    tax_rate_ids: tuple[str, ...]
    proration: bool
    credited_line_ids: tuple[str, ...]

@dataclass(frozen=True)
class DiscountSpec:      # a resolved coupon, item- or invoice-scope
    discount_id: str
    coupon_id: str
    amount_off: Money | None
    percent_off: Rat | None   # e.g. Fraction(25) for 25%
    currency: str | None

@dataclass(frozen=True)
class TaxRateSpec:
    tax_rate_id: str
    percentage: Rat
    inclusive: bool

@dataclass(frozen=True)
class Totals:
    lines: list[dict]                  # the frozen JSON written to invoices.lines
    subtotal: Money
    subtotal_excluding_tax: Money
    invoice_discount_total: Money
    total_discount_amounts: list[dict]
    tax_total: Money                   # exclusive tax only
    total_taxes: list[dict]
    total_excluding_tax: Money
    total: Money

def compute_totals(
    line_inputs: Sequence[LineInput],
    *,
    invoice_discounts: Sequence[DiscountSpec],
    discounts_by_id: Mapping[str, DiscountSpec],
    tax_rates_by_id: Mapping[str, TaxRateSpec],
    currency: str,
) -> Totals:
    """Build every line and every total. Pure. §Internal Design 3 states the arithmetic."""

def settle_customer_balance(total: Money, starting_balance: Money) -> tuple[Money, Money, Money]:
    """-> (amount_due, ending_balance, customer_balance_delta). Pure. §Internal Design 3.5."""

# --- stateful, ctx-bearing ---------------------------------------------------

def create_invoice(ctx, *, customer_id: str, subscription_id: str | None,
                   billing_reason: str, collection_method: str,
                   auto_advance: bool, days_until_due: int | None,
                   period_start: int, period_end: int) -> dict:
    """Write a draft invoice row + its pending lines. Emits invoice.created.
    Sets automatically_finalizes_at when auto_advance and collection_method allow it."""

def finalize_invoice(ctx, invoice_id: str, *, auto_advance: bool | None = None) -> dict:
    """draft -> open. Assigns invoice.number, freezes lines and every monetary field,
    applies the customer balance, clears automatically_finalizes_at, sets
    status_transitions.finalized_at and confirmation_secret. Emits invoice.finalized.
    Raises StripeApiError(400, invalid_request_error, 'invoice_not_editable') if not draft."""

def pay_invoice(ctx, invoice_id: str, *, payment_method_id: str | None = None,
                off_session: bool = True, paid_out_of_band: bool = False,
                automatic: bool = False) -> dict:
    """Attempt collection on an open invoice. `automatic=True` marks this as a retry
    driven by the dunning schedule, which is the ONLY thing that increments attempt_count
    past 1. Returns the invoice row. A decline is a return value, not an exception:
    the invoice stays `open` and invoice.payment_failed is emitted."""

def void_invoice(ctx, invoice_id: str) -> dict
def mark_uncollectible(ctx, invoice_id: str) -> dict
def delete_draft_invoice(ctx, invoice_id: str) -> dict
def compute_automatically_finalizes_at(created_iso: str, *, auto_advance: bool,
                                       collection_method: str) -> str | None
```

### `billing/subscription_lifecycle.py`

```python
def create_subscription(ctx, params: dict) -> dict
def apply_update(ctx, subscription_id: str, params: dict) -> dict
def cancel_subscription(ctx, subscription_id: str, *, prorate: bool = False,
                        invoice_now: bool = False,
                        cancellation_details: dict | None = None) -> dict
def resume_subscription(ctx, subscription_id: str, *, billing_cycle_anchor: str = "now",
                        proration_behavior: str = "create_prorations",
                        proration_date: int | None = None) -> dict
def advance_cycle(ctx, subscription_id: str) -> dict
    """Roll the items' periods forward and create the subscription_cycle invoice.
    FIXTURE-GENERATOR AND TEST ONLY: it is not routed, because nothing in a frozen-clock
    instance can reach a period boundary. See §Internal Design 7."""
def on_invoice_paid(ctx, invoice_id: str) -> dict | None
    """The single recovery hook. incomplete/past_due/unpaid -> active. Called by
    pay_invoice; there is no other path and no explicit subscriptions.update is required."""
def expire_incomplete(ctx, subscription_id: str) -> dict
    """incomplete -> incomplete_expired + void the open invoice. Fixture/test only (23h timer)."""
def transition(state: str, trigger: str, guard: Guard) -> Transition | None
    """The transition table as a pure lookup. §Internal Design 1 is this function's data."""
```

### `billing/dunning.py`

```python
RETRY_BLOCKING_DECLINE_CODES: frozenset[str] = frozenset({
    "incorrect_number", "lost_card", "pickup_card", "stolen_card",
    "revocation_of_authorization", "revocation_of_all_authorizations",
    "authentication_required", "highest_risk_level", "transaction_not_allowed",
})   # exactly nine; docs.stripe.com/billing/revenue-recovery/smart-retries

@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 8
    window: Literal["1w", "2w", "3w", "1mo", "2mo"] = "2w"
    end_behavior: Literal["cancel", "mark_unpaid", "leave_past_due"] = "cancel"

def policy(ctx) -> RetryPolicy                       # from ctx.state["account"]["dunning"]
def next_attempt_number(attempt_count: int, *, automatic: bool) -> int   # pure
def will_reach_network(last_decline_code: str | None) -> bool            # pure
def schedule_exhausted(attempt_count: int, p: RetryPolicy) -> bool       # pure
def apply_end_of_schedule(ctx, subscription_id: str) -> dict
def record_failed_attempt(ctx, invoice_id: str, *, decline_code: str | None,
                          automatic: bool) -> dict
```

### `billing/ledger.py`

```python
@dataclass(frozen=True)
class LedgerSpec:
    fees: FeeSchedule = FeeSchedule()
    settlement_business_days: int = 2       # T+2
    dispute_received_fee: Money = 1500
    dispute_countered_fee: Money = 1500

def ledger_spec(ctx) -> LedgerSpec                   # from ctx.state["account"]["ledger"]

def record(ctx, *, type_: str, amount: Money, fee: Money, currency: str,
           source_id: str, description: str,
           available_on_iso: str | None = None,
           fee_details: list[dict] | None = None,
           reporting_category: str | None = None) -> dict:
    """Mint a bt_ id and write ONE balance_transactions row. `type_` is checked against
    spec/enums.py's 51-value set; an unknown value is a WorldBug. Stores net = amount - fee
    as a column so the arithmetic is auditable in SQL; a CHECK enforces the identity."""

def available_on(created_iso: str, type_: str, spec: LedgerSpec) -> str        # pure
def net(amount: Money, fee: Money) -> Money                                   # pure: amount - fee
def bt_status(available_on_iso: str, now_iso: str) -> Literal["available", "pending"]  # pure
def read_balance(ctx) -> dict                        # the computed /v1/balance object
def available_cents(ctx, currency: str) -> Money
def create_payout(ctx, *, amount: Money, currency: str, method: str = "standard",
                  automatic: bool = False) -> dict
def fail_payout(ctx, payout_id: str, *, failure_code: str) -> dict
def cancel_payout(ctx, payout_id: str) -> dict
```

### Error conditions

Everything an agent can provoke is a `StripeApiError` return value (architecture §7), never a Python
exception. Codes are drawn from the extracted ~215-value set in
[`cross-cutting-semantics/errors.md`](../research/stripe-billing-and-payments/cross-cutting-semantics/errors.md);
nothing here invents one.

| Situation | HTTP | `type` | `code` |
|---|---|---|---|
| Finalize / edit an invoice that is not `draft` | 400 | `invalid_request_error` | `invoice_not_editable` |
| Void or mark-uncollectible an invoice that is not `open` | 400 | `invalid_request_error` | `status_transition_invalid` |
| Delete an invoice that is not `draft` | 400 | `invalid_request_error` | `invoice_not_editable` |
| Finalize a subscription invoice with no lines | 400 | `invalid_request_error` | `invoice_no_subscription_line_items` |
| Finalize a manual invoice with no lines | 400 | `invalid_request_error` | `invoice_no_customer_line_items` |
| Pay an invoice whose charge declines | 402 | `card_error` | `card_declined` (+ `decline_code`) |
| Pay an invoice needing 3DS off-session | 400 | `invalid_request_error` | `invoice_payment_intent_requires_action` |
| `payment_behavior=error_if_incomplete` and the first invoice fails | 402 | `card_error` | `card_declined` (+ `decline_code`); **no subscription row is written** |
| `payment_behavior=pending_if_incomplete` on create | 400 | `invalid_request_error` | `parameter_unknown`, `param="payment_behavior"` |
| Update a `canceled` / `incomplete_expired` subscription | 400 | `invalid_request_error` | `status_transition_invalid` |
| Update anything but `metadata` / `default_source` on an `incomplete` subscription | 400 | `invalid_request_error` | `status_transition_invalid` |
| Expired coupon applied | 400 | `invalid_request_error` | `coupon_expired` |
| Payout exceeding the available balance | 400 | `invalid_request_error` | `balance_insufficient` |
| Refund a charge with an open dispute | 400 | `invalid_request_error` | `charge_disputed` |
| Refund beyond the remaining amount | 400 | `invalid_request_error` | `charge_already_refunded` |

`WorldBug` (not a Stripe error) is reserved for authoring faults the agent cannot cause: an unknown
`balance_transaction.type`, an unknown event type, a zero-length billing period, a negative argument
to `round_cents_half_up`.

## Internal Design Approach

### 1. The subscription status machine

Eight statuses, taken from `spec3.json`
`components.schemas.subscription.properties.status.enum`, verified for this document:

```
active, canceled, incomplete, incomplete_expired, past_due, paused, trialing, unpaid
```

`transition(state, trigger, guard)` is a lookup into the table below. The **Guard** column is the
predicate evaluated at the call site; the **Side effects** column is executed inside the one
transaction Seahaven opens for the call.

#### Creation

| Trigger | Guard | → | Side effects | Events |
|---|---|---|---|---|
| `create` | `trial_end` in the future, or `trial_period_days > 0` | `trialing` | Items' periods span `[now, trial_end)`; `trial_start`/`trial_end` set; **no invoice**; `billing_cycle_anchor := trial_end` | `customer.subscription.created` |
| `create` | no trial, first invoice total ≤ 0 | `active` | `subscription_create` invoice created, finalized, `paid` with `amount_due = 0` | `customer.subscription.created`, `invoice.created`, `invoice.finalized`, `invoice.paid` |
| `create` | `collection_method = send_invoice` | `active` | Invoice created + finalized, left `open` with `due_date = now + days_until_due`. Activation does **not** depend on payment — `spec3.json` verbatim | `customer.subscription.created`, `invoice.created`, `invoice.finalized`, `invoice.sent` |
| `create` | `charge_automatically`, payment required, charge succeeds | `active` | Invoice created → finalized → `paid`; charge + `balance_transaction` | `customer.subscription.created`, `invoice.created`, `invoice.finalized`, `invoice.payment_succeeded`, `invoice.paid` |
| `create` | `payment_behavior ∈ {allow_incomplete, default_incomplete}`, charge fails | `incomplete` | Invoice stays `open`; `attempt_count = 1`; `latest_invoice` set. Only `metadata` and `default_source` are updatable from here | `customer.subscription.created`, `invoice.created`, `invoice.finalized`, `invoice.payment_failed` |
| `create` | `payment_behavior = default_incomplete`, payment required | `incomplete` | Same as above, but the PaymentIntent is created **unconfirmed** and no charge is attempted; `confirmation_secret` is the agent's handle | `customer.subscription.created`, `invoice.created`, `invoice.finalized` |
| `create` | `payment_behavior = error_if_incomplete`, charge fails | — | **Nothing is written.** 402 `card_error`. The whole call rolls back with Seahaven's per-call transaction | — |
| `create` | `payment_behavior = pending_if_incomplete` | — | 400. Update-only value | — |

#### Out of `incomplete`

| Trigger | Guard | → | Side effects | Events |
|---|---|---|---|---|
| `invoice_paid` | latest invoice reaches `paid` | `active` | Item periods set from the payment moment; `start_date` unchanged | `customer.subscription.updated`, `invoice.paid` |
| `incomplete_expiry` | 23h elapsed since creation (fixture/test only — see §7) | `incomplete_expired` | Open invoice → `void`; no further invoices ever generated. **Terminal** | `customer.subscription.updated`, `invoice.voided` |

The 23-hour window is subscription-level and is stated twice independently inside `spec3.json`
(the `status` description and the `payment_behavior` description). It must not be confused with the
~1-hour **invoice** draft window; they are different timers on different objects.

#### Out of `trialing`

Governed by `subscription.trial_settings.end_behavior.missing_payment_method`, a real closed enum in
`spec3.json`: `cancel | create_invoice | pause`. This is the API-level control that produces the
otherwise-mysterious `paused` status, and it is the reason `paused` is "only reachable automatically".

| Trigger | Guard | → | Side effects | Events |
|---|---|---|---|---|
| `trial_end` | default payment method present | `active` | `subscription_cycle` invoice for the first paid period, finalized, paid | `customer.subscription.updated`, `invoice.*` |
| `trial_end` | no PM, `missing_payment_method = create_invoice` (Stripe's default) | `active` → likely `past_due` | Invoice created and finalized; collection is attempted and fails; falls straight into the `past_due` row below | `customer.subscription.updated`, `invoice.payment_failed` |
| `trial_end` | no PM, `missing_payment_method = pause` | `paused` | **No invoice is generated at all** while paused — this is what distinguishes it from `pause_collection` | `customer.subscription.paused` |
| `trial_end` | no PM, `missing_payment_method = cancel` | `canceled` | `canceled_at = now`, `ended_at = now`, `cancellation_details.reason = payment_failed` | `customer.subscription.deleted` |
| `trial_will_end` | fixture/test only | unchanged | none | `customer.subscription.trial_will_end` |
| `update trial_end = "now"` | — | `active` (or `past_due`) | Ends the trial immediately and runs the `trial_end` path above | `customer.subscription.updated` |

#### Out of `paused`

| Trigger | Guard | → | Side effects | Events |
|---|---|---|---|---|
| `resume` (`POST /v1/subscriptions/{id}/resume`) | a default payment method now exists | `active` | `billing_cycle_anchor ∈ {now, unchanged}` (default `now`); with `now`, periods restart at `now` and **no proration is produced**; with `unchanged`, `proration_behavior` applies to the un-invoiced gap | `customer.subscription.resumed`, `customer.subscription.updated` |
| `resume` | no payment method | — | 400 `status_transition_invalid` | — |

`paused` and `pause_collection` are unrelated and must never be conflated:

| | `status = paused` | `pause_collection` |
|---|---|---|
| How you get there | only automatically, at trial end with no PM | `subscriptions.update` with a `pause_collection` object |
| `subscription.status` | becomes `paused` | **unchanged** (`spec3.json` says so explicitly) |
| Invoices | none generated | generated every cycle |
| What happens to them | n/a | `behavior ∈ {keep_as_draft, mark_uncollectible, void}` |
| How you leave | `/resume` | set `pause_collection = null`, or `resumes_at` passes |

`pause_collection.behavior` is applied by `advance_cycle`, not by the status machine: the cycle
invoice is created as usual and then immediately taken to `draft` (kept), `uncollectible`, or `void`.
`keep_as_draft` is the case that overlaps behaviorally with `unpaid` (both leave `draft` invoices)
while leaving `status` untouched.

#### Out of `active`

| Trigger | Guard | → | Side effects | Events |
|---|---|---|---|---|
| `cycle_boundary` (fixture/test) | — | `active` | Items' periods roll forward; `subscription_cycle` invoice created at the boundary with **zero lead time**; the ~1h draft window follows creation | `invoice.created` |
| `cycle_invoice_failed` | `charge_automatically`, charge declines | `past_due` | `attempt_count = 1`; invoice stays `open`; `next_payment_attempt` set | `customer.subscription.updated`, `invoice.payment_failed` |
| `due_date_passed` | `send_invoice` (fixture/test) | `past_due` | invoice stays `open` | `customer.subscription.updated`, `invoice.overdue` |
| `update` (price/quantity) | `proration_behavior = create_prorations` (default) | `active` | `proration_lines(...)` → pending `invoiceitems` rows, **no invoice** | `customer.subscription.updated` |
| `update` (price/quantity) | `proration_behavior = always_invoice` | `active` | Proration items created **and** an invoice with `billing_reason = subscription_update` created, finalized and paid immediately | `customer.subscription.updated`, `invoice.created`, `invoice.finalized`, `invoice.paid` |
| `update` (price/quantity) | `proration_behavior = none` | `active` | Items changed, **no proration lines at all** | `customer.subscription.updated` |
| `update billing_cycle_anchor = "now"` | — | `active` | Truncates the current period, prorates per `proration_behavior`, resets the anchor | `customer.subscription.updated` |
| `update cancel_at_period_end = true` | — | **unchanged** | `cancel_at_period_end = 1`, `cancel_at = sub_period_end`. **No proration, no invoice, no status change** | `customer.subscription.updated` |
| `update cancel_at_period_end = false` | before the boundary | unchanged | clears `cancel_at` | `customer.subscription.updated` |
| `cycle_boundary` | `cancel_at_period_end = 1` | `canceled` | `canceled_at = now`, `ended_at = period_end`; **no renewal invoice is generated** | `customer.subscription.deleted` |
| `DELETE /v1/subscriptions/{id}` | — | `canceled` | Immediate. `prorate=true` (default `false`) emits a credit-only proration item for the unused remainder; `invoice_now=true` additionally creates and finalizes a final invoice. `cancellation_details.reason = cancellation_requested` | `customer.subscription.deleted` (+ `invoice.*` if `invoice_now`) |

`DELETE`'s parameters were re-derived from `spec3.json` for this document, closing
`proration-arithmetic.md`'s open item 4: the endpoint takes **`prorate` (boolean, default `false`)**
and **`invoice_now` (boolean, default `false`)** — not `proration_behavior`. `prorate=true` "will
generate a proration invoice item that credits remaining unused time until the subscription period
end" (verbatim). So an immediate cancel produces a **credit line only**, with no matching debit,
because there is no new configuration to charge for.

#### Out of `past_due`

| Trigger | Guard | → | Side effects | Events |
|---|---|---|---|---|
| `invoice_paid` | latest invoice → `paid`, by any route (automatic retry, manual `/pay`, or the customer) | `active` | Recovery is unconditional on the due date: the docs state it happens "regardless of whether the payment is done before or after the latest invoice due date" | `customer.subscription.updated`, `invoice.paid` |
| `retry_failed` | `attempt_count < max_attempts` | `past_due` | `attempt_count += 1` (see §5) | `invoice.payment_failed` |
| `schedule_exhausted` | `end_behavior = cancel` | `canceled` | `canceled_at`, `ended_at`, `cancellation_details.reason = payment_failed`. The open invoice is **left `open`** — Stripe does not void it | `customer.subscription.deleted` |
| `schedule_exhausted` | `end_behavior = mark_unpaid` | `unpaid` | Open invoice left `open`; future cycles still create invoices | `customer.subscription.updated` |
| `schedule_exhausted` | `end_behavior = leave_past_due` | `past_due` | Future cycle invoices are generated and run their own retry schedules | — |

#### Out of `unpaid`

| Trigger | Guard | → | Side effects | Events |
|---|---|---|---|---|
| `invoice_paid` | the **most recent** invoice reaches `paid` | `active` | **Automatic. No `subscriptions.update` call is required or accepted as the trigger.** Implemented as the single hook `on_invoice_paid`, called from `pay_invoice` | `customer.subscription.updated`, `invoice.paid` |
| `cycle_boundary` | — | `unpaid` | A `subscription_cycle` invoice is created and **left in `draft`**: never finalized, never attempted, `auto_advance = 0`, `automatically_finalizes_at = NULL` | `invoice.created` |

The `draft` ruling is a real, live contradiction inside Stripe's own corpus and is resolved here
deliberately. `spec3.json`'s `subscription.status` prose says invoices are "immediately automatically
closed"; two directly-read docs pages say they "stay in a draft state" / "remain in `draft` status".
**`draft` wins**, for three reasons: two independent primary reads against one, "closed" was never a
member of the `invoice.status` enum (`draft, open, paid, uncollectible, void`), and `draft` composes
with the rest of the invoice machine. Recorded in `allowed_differences.py` as a declared divergence
from the spec's own prose, with the two doc URLs as its justification.

Note the asymmetry this produces, and it is intentional: the invoice that *caused* the `unpaid`
transition stays `open` (it is the one the customer must pay to recover), while every invoice
generated *after* it stays `draft`. `on_invoice_paid` therefore keys on "the subscription's
`latest_invoice`", not on "any invoice of this subscription".

#### Terminal states

`canceled` and `incomplete_expired` have no outgoing transitions. Any `subscriptions.update` against
either is `status_transition_invalid`. A test asserts the transition table has no row whose source is
one of these two.

### 2. The invoice status machine

Five statuses from `spec3.json` `components.schemas.invoice.properties.status.enum`:
`draft, open, paid, uncollectible, void`. Plus `deleted`, which is not a status — a deleted draft is
a removed row returned in Stripe's `{id, object, deleted: true}` shape.

| From | Trigger | → | Side effects | Events |
|---|---|---|---|---|
| — | `create_invoice` | `draft` | `number = NULL`; lines assembled from pending items but **not frozen**; `automatically_finalizes_at` computed; `attempt_count = 0`; `attempted = 0` | `invoice.created` |
| `draft` | line/discount/tax change | `draft` | Totals recomputed from scratch. Nothing is incremental | `invoice.updated` |
| `draft` | `POST /finalize`, or the last step of a paid-on-creation flow | `open` | Assign `number`; **freeze** `lines` JSON and every monetary field; read `customer.balance` → `starting_balance`; apply it → `amount_due`, `ending_balance`; write the `customer_balance_transactions` row; set `status_transitions.finalized_at`; set `confirmation_secret`; **set `automatically_finalizes_at = NULL`**; create the PaymentIntent for `charge_automatically` | `invoice.finalized` (+ `invoice.sent` for `send_invoice`) |
| `draft` | `DELETE /v1/invoices/{id}` | *(row gone)* | Only from `draft`. Pending invoice items are released back to unbilled | `invoice.deleted` |
| `open` | `POST /pay` succeeds, or `paid_out_of_band=true` | `paid` | `amount_paid = amount_due`, `amount_remaining = 0`, `status_transitions.paid_at`; charge + `balance_transaction`; **calls `on_invoice_paid`** | `invoice.payment_succeeded`, `invoice.paid`, `invoice_payment.paid` |
| `open` | `POST /pay` declines | `open` | `attempted = 1`; `attempt_count` per §5; `next_payment_attempt` set or cleared; `last_finalization_error` untouched (that field is for finalization, not payment) | `invoice.payment_failed` |
| `open` | payment requires 3DS off-session | `open` | `confirmation_secret` exposed | `invoice.payment_action_required` |
| `open` | overpayment (`amount_paid > amount_due`) | `paid` | `amount_overpaid` set; excess credited to `customer.balance` via a `customer_balance_transactions` row with `type = invoice_overpaid` | `invoice.overpaid`, `invoice.paid` |
| `open` | `POST /void` | `void` | `status_transitions.voided_at`; `amount_due` and `amount_remaining` left as they were; dead end | `invoice.voided` |
| `open` | `POST /mark_uncollectible` | `uncollectible` | `status_transitions.marked_uncollectible_at` | `invoice.marked_uncollectible` |
| `paid` | anything | — | `status_transition_invalid`. A paid invoice is settled only through a credit note | — |
| `void` / `uncollectible` | anything | — | `status_transition_invalid` | — |

#### `automatically_finalizes_at` under a frozen clock

This is the field the fidelity claim is most easily faked on, so it is stated bluntly.

```python
def compute_automatically_finalizes_at(created_iso, *, auto_advance, collection_method):
    if not auto_advance:
        return None                      # spec3.json: state "doesn't automatically advance"
    return iso_plus(created_iso, hours=1)
```

- The field is **populated and honest**: for any `draft` invoice with `auto_advance = 1`, it holds
  `created + 1 hour`, which is the real window Stripe documents and which `invoice.attempted`'s own
  description corroborates from inside `spec3.json` ("not attempted until 1 hour after the
  `invoice.created` webhook").
- **Nothing fires on its own.** `ctx.clock` is static for the life of the instance
  (capability map: "no method to change it mid-instance"; `clock.py` registers deterministic
  overrides against one instant and exposes nothing else). There is no scheduler, no background
  task, and no `test_helpers/*` route. A draft invoice whose `automatically_finalizes_at` is in the
  past relative to `now` **still does not finalize**. It finalizes when `POST /v1/invoices/{id}/finalize`
  is called, and only then.
- `auto_advance = 0` → the field is `NULL`, which matches Stripe exactly.
- Non-`draft` → the field is `NULL`, enforced by invariant **I11** rather than by hoping the
  transition code cleared it.
- The fixture generator produces drafts on both sides of the window, so an agent sees invoices that
  "should have" finalized and invoices that should not.

The consequence to state in `allowed_differences.py`: on the real API, a draft with
`auto_advance=true` becomes `open` after an hour whether or not anyone calls anything; here, the
draft window never elapses. This is the single largest behavioral difference in the component and it
is a direct, unavoidable consequence of the frozen clock, not an implementation shortcut.

The same reasoning applies to `next_payment_attempt` (populated, never fires) and to
`invoice.due_date` (populated, `invoice.overdue` never self-emits).

#### `billing_reason`

The full enum, from `spec3.json`:

```
automatic_pending_invoice_item_invoice, manual, quote_accept, subscription,
subscription_create, subscription_cycle, subscription_threshold, subscription_update, upcoming
```

What this world emits, and when:

| Value | Emitted by |
|---|---|
| `subscription_create` | `create_subscription` |
| `subscription_cycle` | `advance_cycle`, at the period boundary with **zero lead time** |
| `subscription_update` | `apply_update` with `proration_behavior = always_invoice` |
| `manual` | `POST /v1/invoices` with no subscription |
| `automatic_pending_invoice_item_invoice` | `POST /v1/invoices` for a customer with pending `invoiceitems` and `subscription.pending_invoice_item_interval` set |
| `subscription` | **never.** Legacy, pre-May-2018. A test asserts no row carries it |
| `upcoming` | **never stored.** Preview-only; `/create_preview` is not routed |
| `quote_accept` | **never.** Quotes are out of scope |
| `subscription_threshold` | **never.** Billing thresholds are out of scope |

A `CHECK` on the column allows all nine (the enum is the enum); three tests assert the four
never-emitted values are absent from every fixture.

### 3. Line construction and totals

Stripe's own field descriptions define the order of operations, and following them exactly is what
makes the lines sum to the total. The governing sentence, `spec3.json` on `invoice.subtotal`:
"Total of all subscriptions, invoice items, and prorations on the invoice **before any invoice level
discount or exclusive tax** is applied. **Item discounts are already incorporated**." So there are
two discount scopes with different timing, and the arithmetic must keep them apart.

#### 3.1 Where lines come from

A draft invoice's lines are the concatenation of three sources, in `spec3.json`'s documented
`invoice.lines` order:

1. **Pending invoice items**, including prorations, in **reverse chronological** order. Every row in
   `invoiceitems` with `invoice IS NULL`, `customer = this customer` and `period` inside the
   invoice's `[period_start, period_end]`. Proration lines created by `proration_lines` are exactly
   these.
2. **Subscription items**, in reverse chronological order — one line per row in
   `subscription_items` for the invoice's subscription, with
   `period = [item.current_period_start, item.current_period_end)` and
   `subtotal = price.unit_amount × quantity`.
3. **Invoice items added after invoice creation**, in **chronological** order.

Each becomes a `LineInput`; nothing else contributes. The list is assembled fresh on every draft
mutation and frozen at finalization, when it is written once into `invoices.lines` through
`_json.py`'s single canonical dump (`sort_keys=True`, tight separators) so bytes are reproducible.
`line_item` is a nested JSON object on the parent, never a table (functional spec §3.4).

#### 3.2 One line's arithmetic

For each `LineInput`, in this order:

```
subtotal          = unit_amount * quantity                       # integer * integer
item_discounts    = discount_amounts(subtotal, item_scope_discounts)
amount            = subtotal - sum(d.amount for d in item_discounts)
taxes             = tax_amounts(amount, tax_rate_ids)
```

with

```
discount_amount(base, d) = d.amount_off                       if amount_off  is not None
                         = round_half_up(base * percent_off / 100)   if percent_off is not None
```

An `amount_off` coupon is clamped to the base (`min(amount_off, base)`) so a line's `amount` can
never go negative through discounting. Multiple discounts on one line apply **sequentially** to the
running base, in the order they appear on the item, because that is the only order in which a
percentage discount after a fixed discount is well-defined.

Tax on a line, per rate:

```
exclusive rate: taxable_amount = amount
                tax.amount     = round_half_up(taxable_amount * percentage / 100)
inclusive rate: taxable_amount = amount
                tax.amount     = round_half_up(taxable_amount * percentage / (100 + percentage))
```

Each tax entry is written in `spec3.json`'s `billing_bill_resource_invoicing_taxes_tax` shape:
`{type: "tax_rate_details", tax_rate_details: {tax_rate}, amount, taxable_amount, tax_behavior:
"exclusive"|"inclusive", taxability_reason: "standard_rated"}`. **Exclusive tax is added to the
total; inclusive tax is already inside `amount` and is only reported.** That distinction is the
entire reason `subtotal_excluding_tax` and `total_excluding_tax` are separate fields.

A **proration line always has `discountable = false`** — `spec3.json` verbatim, "Always false for
prorations" — so item-scope discounts skip it. This is not an optimisation; a discount applied to a
proration credit would make the −667/+333 case irreproducible.

#### 3.3 Invoice-scope discounts

Discounts on `invoice.discounts` (inherited from `subscription.discounts` or the customer's
discount, or set directly) are computed against the post-item-discount subtotal and then
**apportioned back across the discountable lines** with `apportion`, so they appear in each line's
`discount_amounts` and aggregate correctly into `total_discount_amounts` without being
double-counted in `subtotal`.

The apportionment rule is **floor-each-then-remainder-to-the-last**, not independent rounding. This
is documented directly and is a *different* rule from proration rounding — the same page that proves
independent per-line proration rounding shows a $5 coupon split 1:2 across a $10 and a $20 item
producing `discount_amounts` of `166` and `334`, where independent rounding would have produced
`167` and `333`. Two rounding rules, two functions, and a test for each that would fail if they were
swapped.

#### 3.4 Invoice totals

```
subtotal               = Σ line.amount                       # item discounts already in
invoice_discount_total = Σ apportioned invoice-scope discount amounts
inclusive_tax_total    = Σ line.taxes where tax_behavior = 'inclusive'
exclusive_tax_total    = Σ line.taxes where tax_behavior = 'exclusive'

subtotal_excluding_tax = subtotal - inclusive_tax_total
total_excluding_tax    = subtotal - invoice_discount_total - inclusive_tax_total
total                  = subtotal - invoice_discount_total + exclusive_tax_total
tax_total              = inclusive_tax_total + exclusive_tax_total   # what total_taxes sums to
```

`total` may be **negative** — the documented −334 case is exactly that.

`total_discount_amounts` is `[{discount, amount}]` aggregated per discount across all lines;
`total_taxes` is the per-rate aggregation of every line's `taxes`. Both are stored as JSON columns;
`invoice_discount_total`, `tax_total` and `exclusive_tax_total` are additionally stored as internal
INTEGER columns so the invariants in §8 are plain SQL rather than `json_each` gymnastics.
`data_model.md` owns the DDL.

#### 3.5 Customer balance, `amount_due`, and settlement

At finalization only — `starting_balance` is "the current customer balance" while a draft, and
freezes when the invoice finalizes. Stripe's sign convention: `customer.balance` **negative means
credit available**, positive means the customer owes.

```python
def settle_customer_balance(total, starting_balance):
    if total >= 0:
        credit_available = max(0, -starting_balance)
        credit_used      = min(credit_available, total)
        debit_pending    = max(0, starting_balance)
        amount_due       = total + debit_pending - credit_used
        delta            = credit_used - debit_pending     # applied to customer.balance
    else:
        amount_due       = 0
        delta            = total                           # total < 0: grows the credit
    return amount_due, starting_balance + delta, delta
```

Worked: `starting_balance = -1000`, `total = 400` → `amount_due = 0`, `ending_balance = -600`.
`starting_balance = 500`, `total = 1000` → `amount_due = 1500`, `ending_balance = 0`.
`starting_balance = 0`, `total = -334` → `amount_due = 0`, `ending_balance = -334`.

Then `amount_remaining = amount_due - amount_paid`, maintained as an invariant (I3) rather than
written independently.

Each application writes one `customer_balance_transactions` row, with `type` from that table's real
enum (verified against `spec3.json`):

| Situation | `customer_balance_transaction.type` |
|---|---|
| Credit consumed at finalization | `applied_to_invoice` |
| Pending debit drawn onto an invoice | `adjustment` |
| A negative-`total` invoice creating credit | `adjustment` |
| Overpayment credited back | `invoice_overpaid` |
| Credit note settling to the balance | `credit_note` |
| Voiding an invoice that had consumed credit | `unapplied_from_invoice` |

**Open, declared:** the negative-`total` row is the one mapping in this table not pinned by a
primary source — Stripe's enum offers no better-named value, and `adjustment` is its documented
catch-all. Conformance scenario 5 ("a subscription created, upgraded mid-cycle, cancelled") records
a downgrade and reads the resulting `customer_balance_transaction.type`; until it does, the choice is
in `allowed_differences.py`.

### 4. `proration_lines(...)`

#### The fraction

```
fraction = (period_end − proration_date) / (period_end − period_start)
```

Both numerator and denominator are **second counts** — unix-timestamp differences, never day counts.
Stripe "prorates to the second"; a day-granularity implementation matches only by coincidence at
exact day boundaries. `proration_date` defaults to `ctx.clock`'s instant and is clamped into
`[period_start, period_end]`; a caller-supplied `proration_date` is honoured exactly, which is the
hook every conformance test pins so numbers are reproducible.

The fraction is a `fractions.Fraction`, which makes it exact. `Fraction(1, 3) * 2000` is
`Fraction(2000, 3)`, not `666.6666666666666`.

#### The algorithm

```python
def proration_lines(old_items, new_items, *, period_start, period_end,
                    proration_date, currency, now_iso, credited_line_ids_by_item={}):
    f = proration_fraction(period_start, period_end, proration_date)
    if f == 0:
        return []
    lines = []
    # 1. Credits: the WHOLE old configuration, for the remainder of the period.
    for it in old_items:
        gross = it.unit_amount * it.quantity              # int
        cents = round_cents_half_up(f * gross)            # magnitude, then sign
        if cents:
            lines.append(ProrationLine(amount=-cents, ...))
    # 2. Debits: the WHOLE new configuration, for the same remainder.
    for it in new_items:
        gross = it.unit_amount * it.quantity
        cents = round_cents_half_up(f * gross)
        if cents:
            lines.append(ProrationLine(amount=+cents, ...))
    return lines
```

Three things this encodes deliberately:

1. **Whole-configuration reproration, never a delta.** A `quantity: 2 → 3` change credits
   `2 × old_price × f` and debits `3 × new_price × f`; it does not charge for one unit. Stripe's
   prorations page states the mechanism generically — "the cost of the subscription **before and
   after the change**" — with price and quantity changes listed in the same trigger table and no
   separate formula for quantity. This is the strongest available statement short of a numeric
   example, which no docs page provides. Flagged in the Test Plan; conformance scenario 5 covers it.
2. **Independent per-line rounding, then summing.** Each credit and each debit is rounded to the
   nearest cent on its own, and the invoice total is the sum of the rounded lines. The total is
   **never** computed at higher precision and rounded once. This is settled by a worked example in
   Stripe's own documentation (see the test below), and a net-then-round implementation is off by a
   cent in exactly the cases that matter.
3. **The sign is applied after rounding a magnitude**, so a hypothetical tie rounds away from zero on
   both sides symmetrically. `round_cents_half_up` refuses a negative argument, so this cannot be
   circumvented by accident.

Lines are emitted credits-first, matching `invoice.lines`' documented reverse-chronological ordering
of pending items, and each credit line carries
`parent.subscription_item_details.proration_details.credited_items = {invoice, invoice_line_items}`
pointing at the original debit lines it reverses — a first-class structure in `spec3.json`, not an
embellishment.

#### The worked example, as a test

Reproducing the documented case exactly (`billing_mode=classic`, a 30-day period, the change landing
with one third of the period remaining, old price 20.00, new price 10.00):

```python
def test_proration_documented_667_333_334():
    period_start, period_end = 1_764_547_200, 1_764_547_200 + 30 * 86_400
    proration_date = period_end - (30 * 86_400) // 3          # exactly 1/3 remaining
    lines = proration_lines(
        old_items=[ItemConfig("si_1", "price_20_monthly", 2000, 1)],
        new_items=[ItemConfig("si_1", "price_10_monthly", 1000, 1)],
        period_start=period_start, period_end=period_end,
        proration_date=proration_date, currency="usd", now_iso=NOW,
    )
    assert [l.amount for l in lines] == [-667, 333]
    assert sum(l.amount for l in lines) == -334      # net-then-round would give -333
```

`20/3 = 666.66…¢ → −667`; `10/3 = 333.33…¢ → +333`; `−667 + 333 = −334`, matching the
`latest_invoice.total` of `-334` in the docs' own JSON response. A net-then-round implementation
computes `−10/3 = −333.33…¢ → −333` and fails this test. It is the single most load-bearing test in
the component and carries a comment saying so.

#### The one assumption, isolated

`round_cents_half_up` is the **only** assumption in this module and it is confined to one named
function with the comment quoted in the Public Interface above. Both values in the documented example
round unambiguously under every standard convention (666.667 up, 333.333 down), so the example cannot
distinguish half-up from half-even, and no Stripe page found in two research passes states a
tie-break for proration. The fee-rounding support page documents half-up for **Stripe's own
processing fee**, which is a different computation and is treated as an analogy, not evidence.

**Conformance scenario 1** (functional spec §12) closes it: a mid-cycle change engineered to land on
an exact `x.xx5`, recorded against real test mode. The same scenario also records the change under
`billing_mode=flexible`, because the documented example is explicitly `classic` and the `flexible`
variant of the same scenario happens to net to zero, exposing nothing. Until scenario 1 is recorded,
`allowed_differences.py` carries both as declared, named assumptions. Nothing else in `proration.py`
is unproven.

#### `proration_behavior`

| Value | What `apply_update` does |
|---|---|
| `create_prorations` (default) | Write the lines as pending `invoiceitems` rows with `invoice IS NULL`. **No invoice.** They ride the next invoice generated for the customer |
| `always_invoice` | The same rows, plus immediately `create_invoice(billing_reason="subscription_update")`, finalize and pay |
| `none` | No proration lines at all. The old price is kept for the period already billed; the new price takes over at the next boundary |

`cancel_at_period_end = true` produces **no** proration, ever: nothing is truncated, the period simply
is not renewed.

### 5. Dunning

Smart Retries is ML-scheduled. There is no retry-day table in Stripe's documentation, and inventing
one would be inventing behavior. This component models the **configuration envelope**, the
**counter semantics**, the **decline gating** and the **three end-of-schedule outcomes** — all four
of which are documented — and models nothing else.

#### The envelope

`RetryPolicy(max_attempts, window, end_behavior)`, read from `ctx.state["account"]["dunning"]`,
which `startup.py` populates. It is deliberately **not** a subscription column: Stripe exposes no
such field on the subscription object, and adding one would be a fidelity regression, not a feature.
`window ∈ {1w, 2w, 3w, 1mo, 2mo}` is the real, documented set of choices; Stripe's recommended
default of **8 tries within 2 weeks** is the world's default.

`end_behavior` has no documented Stripe default — it is a Dashboard setting no research pass
recovered. The world's default is `cancel`, chosen because it is the only terminal outcome and
therefore the only one that leaves no ambiguity about what a fixture's end state means. It is
declared as a world choice in `allowed_differences.py`, not presented as Stripe's default.

#### `attempt_count`

`spec3.json`'s own description is the most implementation-precise statement in the whole dunning
area, and the rules fall straight out of it:

```python
def next_attempt_number(attempt_count, *, automatic):
    if attempt_count == 0:
        return 1          # ANY first attempt, manual or automatic, is attempt 1
    return attempt_count + 1 if automatic else attempt_count
```

1. The first attempt — however it is made — sets `attempt_count = 1`.
2. After that, **only automatic retries increment**. A manual `POST /v1/invoices/{id}/pay` neither
   consumes a schedule slot nor bumps the counter.
3. **The counter keeps climbing even when nothing reaches the network.** If the last decline was one
   of the nine hard-decline codes, the schedule still runs and `attempt_count` still increments, but
   no authorization is sent to the issuer until a new payment method is attached. `attempt_count`
   incrementing is therefore **not** evidence that a network attempt happened — a distinction a
   careless mock erases.

```python
def will_reach_network(last_decline_code):
    return last_decline_code not in RETRY_BLOCKING_DECLINE_CODES
```

The nine codes, Stripe-primary-sourced from the Smart Retries page:
`incorrect_number`, `lost_card`, `pickup_card`, `stolen_card`, `revocation_of_authorization`,
`revocation_of_all_authorizations`, `authentication_required`, `highest_risk_level`,
`transaction_not_allowed`. A test asserts the set has exactly nine members and that each is present
in the extracted 50-value `decline_code` enumeration, so the two sources cannot drift apart.

`record_failed_attempt` with `will_reach_network() == False` writes **no charge row and no
PaymentIntent confirmation** — it increments the counter and moves `next_payment_attempt`, and that
is all. There is nothing in the change log because nothing was attempted, which is the observable
difference.

#### The three end-of-schedule outcomes

Covered in the subscription transition table's `past_due` block. `schedule_exhausted` is
`attempt_count >= max_attempts`.

#### What a frozen-clock world cannot model, stated plainly

The retry **schedule** does not exist at runtime, and pretending otherwise would be the dishonest
choice:

- `ctx.clock` never advances, so **no automatic retry can ever fire inside a live instance**. Within
  a rollout, `attempt_count` can only go `0 → 1`, on the first attempt; manual `/pay` calls after
  that leave it at 1, exactly as Stripe specifies.
- `next_payment_attempt` is populated with an honest future timestamp and **never arrives**. Same
  contract as `automatically_finalizes_at`.
- Everything past attempt 1 is therefore reachable only two ways: the **fixture generator**, which
  runs a virtual timeline and writes invoices at attempt counts 1 through 8 with the right decline
  codes and `next_payment_attempt` values (architecture §9), and `dunning.record_failed_attempt` /
  `apply_end_of_schedule` called directly in tests. Neither is a routed operation.
- **Stripe's actual retry days are not modelled and are not guessed.** `next_payment_attempt` in a
  fixture is drawn from the timeline simulator's own spacing, and `allowed_differences.py` declares
  that it will not match what the real API would have chosen. Modelling "N attempts within a window"
  is the honest subset of Smart Retries; modelling *which day* is not available to anyone outside
  Stripe.

What the world *can* do faithfully, and does: present a subscription in dunning at any attempt count,
with the right status, the right invoice statuses, the right `attempt_count`, the right decline code,
and correct recovery when the latest invoice is paid. That is what the "rescuing a subscription in
dunning" eval grades, and none of it needs a clock that moves.

### 6. The ledger

#### What creates a `balance_transaction`

Exactly one row per money movement. `type` is checked against the 51-value enum in `spec/enums.py`,
generated from `spec3.json`; an unknown value is a `WorldBug`.

| Event | `type` | `amount` | `fee` | `available_on` | `source` |
|---|---|---|---|---|---|
| Charge captured | `charge` | `+charge.amount` | `stripe_fee(amount)` | `created + T+2` | `ch_…` |
| Refund | `refund` | `−refund.amount` | `0` | `created` | `re_…` |
| Refund failed, funds returned | `refund_failure` | `+refund.amount` | `0` | `created` | `re_…` |
| **Dispute opened** | **`adjustment`** | `−disputed_amount` | `+dispute_received_fee` | `created` | `dp_…` |
| **Dispute won** | **`adjustment`** | `+disputed_amount` | `−dispute_countered_fee` if the merchant countered, else `0` | `created` | `dp_…` |
| Dispute lost | *(none)* | — | — | — | — |
| Payout created | `payout` | `−payout.amount` | `0` (standard) | `created` | `po_…` |
| Payout failed | `payout_failure` | `+payout.amount` | `0` | `created` | `po_…` |
| Payout canceled | `payout_cancel` | `+payout.amount` | `0` | `created` | `po_…` |

Dispute withdrawal **and** reversal are both `type = adjustment`. This is confirmed verbatim, not
inferred — `docs.stripe.com/reports/balance-transaction-types` states both halves explicitly under
the `adjustment` type, with the `source` pointing at the dispute and the `description` carrying the
distinction. The enum has no dispute-specific value and a mock that invents one is wrong.

Dispute fees follow the documented two-fee structure: the **received** fee is charged when the
dispute opens and is **never** returned; the **countered** fee is charged only if the merchant
submits evidence and **is** returned on a win. So a merchant who wins a contested dispute recovers
the disputed amount and the countered fee, but not the received fee. That is why the reversal row
carries a *negative* `fee`: `net = amount − fee = disputed_amount + countered_fee`. A `lost` dispute
writes no second row; the `dispute.balance_transactions` array is documented as holding "zero, one, or
two" entries and this table is what produces each count.

A `prevented` dispute writes **zero** rows. The docs' language for the fully-prevented path is that
"the dispute is never filed" with no fee, and whether the fee-bearing prevention products create a
`Dispute` object at all is not stated anywhere found. `balance_transactions = []` is the safe
default and is declared.

#### Fees

```python
def stripe_fee(amount, schedule):
    return round_cents_half_up(Fraction(amount * schedule.percent_bps, 10_000)) + schedule.fixed
```

The rate is **not** discoverable from `spec3.json` — it is commercial pricing, not spec. It is a
world constant in `ctx.state["account"]["ledger"]`, default 290 bps + 30¢ (US standard card).
`fee_details` is written as one entry `{type: "stripe_fee", amount: fee, currency, description:
"Stripe processing fees", application: null}`, in `spec3.json`'s `fee` shape whose `type` enum is
`[application_fee, payment_method_passthrough_fee, stripe_fee, tax, withheld_tax]`. An invariant
asserts `Σ fee_details[].amount = fee` on every row.

Unlike the proration tie-break, **this rounding rule is documented** — the fee-rounding support page
states round-half-up for the processing fee. Same helper, different epistemic status, and the two
call sites say so.

#### `net`, `available_on`, `available` and `pending`

```
net = amount − fee          # spec3.json states this identity verbatim
```

Stored as a column with `CHECK (net = amount - fee)`, so an implementation bug becomes a constraint
violation rather than a silently wrong balance.

`available_on = created + 2 business days` for `charge` rows, computed by a pure
`add_business_days(iso, n)` (Mon–Fri, no holiday calendar); `= created` for every other type, so a
refund, dispute adjustment or payout hits the available balance immediately. Real Stripe's
settlement delay varies by country and account and has no canonical constant; T+2 is a world
constant, declared. Business days rather than calendar days because a fixture with `available_on`
landing on a Sunday is visibly wrong to anyone reading it.

`balance_transaction.status` is **not a column**. It is derived at serialization time:

```python
def bt_status(available_on_iso, now_iso):
    return "available" if available_on_iso <= now_iso else "pending"
```

ISO-8601 in Seahaven's canonical form sorts correctly as text, so this is a string comparison and the
same comparison works identically in SQL. The field is typed as a bare `string` in `spec3.json` with
its two values stated only in prose, so it is one of the six fields the conformance validator checks
against an extracted set rather than against the schema (functional spec §4).

`/v1/balance` is a computed read over the ledger — no table, no counter, so it cannot drift:

```sql
SELECT currency,
       SUM(CASE WHEN available_on <= :now THEN net ELSE 0 END) AS available,
       SUM(CASE WHEN available_on >  :now THEN net ELSE 0 END) AS pending
FROM balance_transactions
WHERE balance_type = 'payments'
GROUP BY currency;
```

serialized into `balance.available[]` / `balance.pending[]` with their `source_types` breakdown.
`instant_available`, `connect_reserved`, `issuing` and `refund_and_dispute_prefunding` are returned
as empty arrays; `balance_type` is written `payments` on every row this world creates.

#### How payouts draw down

There is no separate draw-down bookkeeping, and that is the design:

1. `create_payout` reads `available_cents(ctx, currency)` — the same SQL above.
2. `amount > available` → 400 `balance_insufficient`. Nothing is written.
3. Otherwise it writes the `payouts` row (`status = pending`, `automatic = false` for an API call)
   and **one** `balance_transaction` with `type = payout`, `amount = −payout.amount`,
   `available_on = created`. Because the row is immediately available, the next balance read is
   already lower by exactly that amount. `payout.balance_transaction` points at it.
4. Status moves `pending → in_transit → paid`, or `→ failed` / `→ canceled`. A failure or
   cancellation writes a **second, reversing** row (`payout_failure` / `payout_cancel`,
   `amount = +payout.amount`) referenced by `payout.failure_balance_transaction`. The original row is
   never mutated — mutating it would make the ledger unauditable and would contradict the existence
   of the two distinct type values.
5. `reconciliation_status = completed` once paid, at which point
   `GET /v1/balance_transactions?payout=po_…` lists the rows swept into it.

Payout status never advances on its own — no clock, no scheduler. `arrival_date` is populated
honestly and never arrives; transitions happen when called, or are laid down by the fixture
generator. Same declared difference as `automatically_finalizes_at`.

Instant payouts and `instant_available` are not modelled: `method = instant` is rejected with
`invalid_request_error` / `payouts_not_allowed`, declared.

### 7. Time: what this component can and cannot do

Collected in one place so it is auditable rather than scattered.

**Unaffected by the frozen clock**, because they are computations over period boundaries rather than
over elapsed time: proration (the fraction is between three given timestamps), finalizing a draft,
paying an open invoice, refunding, retrying a failed payment manually, cancelling at period end,
applying a coupon, computing `available_on`, deriving `available` vs `pending`.

**Cannot self-fire, and say so:** draft auto-finalization (`automatically_finalizes_at`), automatic
payment retries (`next_payment_attempt`), `incomplete → incomplete_expired` (23h),
`trialing → active` at `trial_end`, cycle boundaries, `cancel_at_period_end` taking effect,
`pause_collection.resumes_at`, payout arrival, `invoice.overdue` at `due_date`. In every case the
timestamp field is **populated and honest** — it holds what the real API would hold — and the
transition is reached by an explicit call (`/finalize`, `/pay`, `/resume`, `DELETE`) or is laid down
by the fixture generator's virtual timeline. The functions that perform an otherwise-timer-driven
transition (`advance_cycle`, `expire_incomplete`) exist, are unit-tested, and are **not routed**.

Every one of these is a line in `allowed_differences.py` with this reason. None is a bug and none is
hidden.

### 8. Invariants

Written as SQL over `inst.inspect()`, because they are simultaneously the component's invariant tests
and the reward functions for the evals (functional spec §11, §13). Each returns **zero rows** when
the world is correct, so a reward function is `len(rows) == 0`. `:now` is bound from `ctx.clock`.

```sql
-- I1  Lines sum to the subtotal. (Item discounts are already inside line.amount.)
SELECT i.id, i.subtotal, SUM(json_extract(l.value, '$.amount')) AS line_sum
FROM invoices i, json_each(i.lines) l
GROUP BY i.id, i.subtotal
HAVING SUM(json_extract(l.value, '$.amount')) <> i.subtotal;

-- I2  The total identity, exactly as §3.4 states it.
SELECT id FROM invoices
WHERE total <> subtotal - invoice_discount_total + exclusive_tax_total;

-- I3  amount_remaining is derived, never independently written.
SELECT id FROM invoices WHERE amount_remaining <> amount_due - amount_paid;

-- I4  A paid invoice is fully settled and stamped; a non-paid one is not stamped.
SELECT id FROM invoices
WHERE (status =  'paid' AND (amount_remaining <> 0 OR paid_at IS NULL))
   OR (status <> 'paid' AND paid_at IS NOT NULL);

-- I5  net = amount - fee on every ledger row, and fee_details sums to fee.
SELECT b.id FROM balance_transactions b
WHERE b.net <> b.amount - b.fee
   OR b.fee <> (SELECT COALESCE(SUM(json_extract(f.value, '$.amount')), 0)
                FROM json_each(b.fee_details) f);

-- I6  The available balance is never negative: a payout can never overdraw it.
SELECT currency, SUM(CASE WHEN available_on <= :now THEN net ELSE 0 END) AS available
FROM balance_transactions WHERE balance_type = 'payments'
GROUP BY currency
HAVING SUM(CASE WHEN available_on <= :now THEN net ELSE 0 END) < 0;

-- I7  No over-refund: refunds against a charge never exceed it, and the flag agrees.
SELECT c.id FROM charges c
LEFT JOIN (SELECT charge_id, SUM(amount) AS refunded FROM refunds
           WHERE status = 'succeeded' GROUP BY charge_id) r ON r.charge_id = c.id
WHERE COALESCE(r.refunded, 0) > c.amount
   OR c.amount_refunded <> COALESCE(r.refunded, 0)
   OR c.refunded <> (COALESCE(r.refunded, 0) = c.amount);

-- I8  A subscription item's periods never overlap and never gap across its invoices.
SELECT cur.subscription_item_id, cur.period_start
FROM subscription_item_periods cur
JOIN subscription_item_periods prev
  ON prev.subscription_item_id = cur.subscription_item_id
 AND prev.seq = cur.seq - 1
WHERE cur.period_start <> prev.period_end;
-- (subscription_item_periods is a VIEW over invoice lines whose
--  parent.type = 'subscription_item_details' and proration = 0, ordered per item.)

-- I9  automatically_finalizes_at is populated exactly when it should be.
SELECT id FROM invoices
WHERE (status <> 'draft'                     AND automatically_finalizes_at IS NOT NULL)
   OR (status =  'draft' AND auto_advance = 0 AND automatically_finalizes_at IS NOT NULL)
   OR (status =  'draft' AND auto_advance = 1 AND automatically_finalizes_at IS NULL);

-- I10 attempt_count and attempted agree, and the counter is bounded by the policy.
SELECT id FROM invoices
WHERE (attempted = 1 AND attempt_count < 1)
   OR (attempted = 0 AND attempt_count > 0)
   OR attempt_count > :max_attempts;

-- I11 A subscription in `unpaid` has at most one `open` invoice (the recovery invoice);
--     every invoice generated after it stays `draft`.
SELECT s.id FROM subscriptions s
WHERE s.status = 'unpaid'
  AND (SELECT COUNT(*) FROM invoices i
       WHERE i.subscription_id = s.id AND i.status = 'open') > 1;

-- I12 Terminal statuses are stamped; non-terminal ones are not.
SELECT id FROM subscriptions
WHERE (status IN ('canceled') AND (canceled_at IS NULL OR ended_at IS NULL))
   OR (status NOT IN ('canceled', 'incomplete_expired') AND ended_at IS NOT NULL);

-- I13 Proration lines are never discountable and always carry a period inside their invoice's.
SELECT i.id FROM invoices i, json_each(i.lines) l
WHERE json_extract(l.value, '$.parent.subscription_item_details.proration') = 1
  AND (json_extract(l.value, '$.discountable') <> 0
       OR json_extract(l.value, '$.period.start') > json_extract(l.value, '$.period.end'));

-- I14 The customer balance equals its own ledger — no denormalized drift.
SELECT c.id FROM customers c
WHERE c.balance <> (SELECT COALESCE(SUM(amount), 0)
                    FROM customer_balance_transactions t WHERE t.customer_id = c.id);

-- I15 Every ledger type this world wrote is in the spec's 51-value enum.
SELECT DISTINCT type FROM balance_transactions
WHERE type NOT IN ( /* generated from spec/enums.py */ );
```

I1, I5, I6, I7 and I14 are also eval reward functions verbatim (functional spec §13: "reconciling a
balance discrepancy", "over-refunding", "a mid-cycle plan change whose proration must be correct").

## Dependencies

### This component depends on

| Dependency | For |
|---|---|
| `ctx.db` | Every read and write. One connection, never a second one |
| `ctx.clock` | `now()` / `iso()`. The only source of time in the component — nothing reads a wall clock |
| `ctx.ids` via `_ids.py::stripe_id` | `in_`, `il_`, `ii_`, `sub_`, `si_`, `txn_`, `cbtxn_`, `po_`, `evt_` prefixes. **Never `ctx.ids.uuid()`** — a test greps `billing/` for it (architecture §4.4) |
| `ctx.state["account"]` | `RetryPolicy`, `FeeSchedule`, `LedgerSpec`, settlement delay, dispute fees. Written by `startup.py` |
| `_time.py` | ISO ↔ unix seconds, `iso_plus`, `add_business_days`. Conversion lives here and nowhere else |
| `_json.py` | The single canonical dump for `invoices.lines` and every JSON column |
| `spec/enums.py` | The `balance_transaction.type`, `billing_reason`, status and `decline_code` closed sets |
| `spec/event_types.py` | Validation set for `emit_event` |
| `cross_cutting.emit_event` | Every event in the tables above |
| `stripe_errors.StripeApiError` | Every agent-visible failure |
| `resources/_lookup.py` | Parent lookups before a child write, so a missing parent is `resource_missing` and not a `DbError` |
| `resources/payment_intents`, `resources/charges` | The payment attempt behind `pay_invoice`, including magic-card decline selection |
| `fractions.Fraction` | Exact arithmetic. The only "numeric library" in the money path |

### What depends on this component

| Depends on it | How |
|---|---|
| `resources/subscriptions.py` | Every non-CRUD operation: create, update, delete, `/resume` |
| `resources/subscription_items.py` | Item create/update/delete → `apply_update` for the proration side effects |
| `resources/invoices.py` | `/finalize`, `/pay`, `/void`, `/mark_uncollectible`, `/send`, draft delete |
| `resources/invoiceitems.py` | Pending items feeding line construction |
| `resources/subscription_schedules.py` | A phase advance calls `apply_update` |
| `resources/charges.py`, `refunds.py`, `disputes.py` | `ledger.record` for their money movements |
| `resources/payouts.py`, `balance.py`, `balance_transactions.py` | `ledger.create_payout`, `read_balance` |
| `resources/credit_notes.py` | `settle_customer_balance` and the refund settlement channel |
| `fixtures_src/generate.py` | `advance_cycle`, `expire_incomplete`, `record_failed_attempt`, `apply_end_of_schedule` — the not-routed functions exist mainly for the timeline simulator |
| `tests/conformance/` | Scenarios 1, 5, 6, 7 target this component directly |
| `tests/invariants/` and `evals/` | The §8 SQL, shared verbatim |

## Test Plan

Named tests, what each verifies. Arithmetic tests call the pure functions directly; behavior tests go
through `instance.call(...)` so validation, middleware and the per-call transaction are all in play
(architecture §11). Errors are asserted by **code**, never by message text.

### Proration — `tests/billing/test_proration.py`

| Test | Verifies |
|---|---|
| `test_proration_documented_667_333_334` | **The load-bearing one.** Reproduces Stripe's documented example exactly: lines `[-667, +333]`, sum `-334`. Fails under net-then-round |
| `test_net_then_round_would_differ` | Asserts explicitly that `round(net)` is `-333` while the implementation returns `-334`, so the test's purpose survives a refactor |
| `test_fraction_is_second_precision` | A change 1 second into a 30-day period produces a fraction of `2591999/2592000`, not `29/30`. Day-granularity fails |
| `test_fraction_exact_rational_not_float` | `proration_fraction` returns a `Fraction`; a `float` anywhere in the chain fails |
| `test_proration_date_honoured_and_clamped` | An explicit `proration_date` is used verbatim; one outside the period is clamped to the boundary |
| `test_zero_fraction_returns_no_lines` | `proration_date == period_end` → `[]`, not two zero-amount lines |
| `test_quantity_change_reprorates_whole_item` | `quantity 2 → 3` credits `2 × old × f` and debits `3 × new × f`, not one unit's worth. **Marked `xfail(strict=False)` against conformance scenario 5** until a recorded trace confirms the numeric case |
| `test_upgrade_and_downgrade_are_symmetric` | The formula has no upgrade/downgrade branch; only the sign of the sum differs |
| `test_half_cent_tie_breaks_away_from_zero` | Pins the assumption so a change to it is visible. Carries a comment naming conformance scenario 1 |
| `test_round_cents_half_up_refuses_negative` | Raises, so the sign-after-magnitude convention cannot be bypassed |
| `test_apportion_is_floor_then_remainder` | `apportion(500, [1000, 2000]) == [166, 334]` — the documented coupon split, **not** `[167, 333]` |
| `test_two_rounding_rules_are_not_interchangeable` | Swapping `apportion` and `round_cents_half_up` fails both the proration and the coupon test |
| `test_cancel_immediate_prorate_credits_only` | `DELETE` with `prorate=true` yields a credit line and no debit |
| `test_cancel_at_period_end_produces_no_proration` | Zero lines |

### Totals — `tests/billing/test_invoicing_totals.py`

`test_lines_sum_to_subtotal`, `test_item_discount_folded_into_line_amount`,
`test_invoice_discount_apportioned_not_double_counted`,
`test_amount_off_clamped_to_line_base`, `test_sequential_discounts_on_one_line`,
`test_exclusive_tax_added_to_total`, `test_inclusive_tax_carved_out_of_amount`,
`test_subtotal_excluding_tax_vs_total_excluding_tax`,
`test_proration_line_is_never_discountable`, `test_negative_total_is_permitted`,
`test_total_taxes_aggregates_per_rate`, `test_line_order_matches_spec` (pending items reverse-chron,
then subscription items reverse-chron, then later invoice items chronological),
`test_lines_json_is_byte_stable` (same inputs → identical bytes through `_json.py`),
`test_no_float_in_totals_path` (introspects for `float` instances in every intermediate).

Customer balance: `test_credit_applied_reduces_amount_due`,
`test_pending_debit_increases_amount_due`, `test_negative_total_grows_credit`,
`test_overpayment_credits_customer_balance_as_invoice_overpaid`,
`test_starting_balance_frozen_at_finalization`.

### Invoice status machine — `tests/billing/test_invoice_machine.py`

`test_draft_recomputes_totals_on_every_edit`, `test_finalize_assigns_number_and_freezes_lines`,
`test_finalize_clears_automatically_finalizes_at`,
`test_automatically_finalizes_at_is_created_plus_one_hour`,
`test_automatically_finalizes_at_null_when_auto_advance_false`,
`test_past_due_finalize_time_does_not_self_fire` — **the honest-clock test**: a draft whose
`automatically_finalizes_at` is before `now`, asserted still `draft` after unrelated calls, then
`open` only after `/finalize`.
`test_finalize_non_draft_is_invoice_not_editable`, `test_void_from_paid_is_status_transition_invalid`,
`test_delete_only_from_draft`, `test_mark_uncollectible_only_from_open`,
`test_pay_failure_leaves_invoice_open`, `test_billing_reason_never_legacy_subscription`,
`test_upcoming_billing_reason_never_stored`,
`test_invoice_parent_shape_not_flat_subscription` — asserts `parent.subscription_details.subscription`
and the **absence** of a top-level `subscription`, `days_until_due` and `paid`.

### Subscription status machine — `tests/billing/test_subscription_machine.py`

One test per row of §1's tables, named `test_<from>_<trigger>_to_<to>`, plus:

`test_all_eight_statuses_reachable` — a parametrized walk proving every enum value occurs.
`test_transition_table_has_no_edges_out_of_terminal_states`.
`test_incomplete_allows_only_metadata_and_default_source`.
`test_error_if_incomplete_writes_nothing` — asserted on the **change log**, not just the response.
`test_pending_if_incomplete_rejected_on_create`.
`test_send_invoice_activates_regardless_of_payment`.
`test_trial_end_pause_produces_no_invoice` vs `test_pause_collection_still_generates_invoices` —
the two-pause distinction, the test most likely to catch a conflation.
`test_pause_collection_leaves_status_unchanged` for all three `behavior` values.
`test_resume_requires_payment_method`.
`test_resume_now_anchor_produces_no_proration`.
`test_cancel_at_period_end_does_not_change_status`.
`test_unpaid_recovers_to_active_on_latest_invoice_paid` — **no `subscriptions.update` call anywhere
in the test**; the transition must come from `pay_invoice` alone.
`test_past_due_recovers_regardless_of_due_date`.
`test_unpaid_cycle_invoices_stay_draft` — the settled `draft` ruling.
`test_period_lives_on_items_not_subscription` — asserts `current_period_end` is absent from the
serialized subscription and present on each item.

### Dunning — `tests/billing/test_dunning.py`

`test_first_attempt_sets_count_to_one_manual_or_automatic`,
`test_manual_retry_does_not_increment`, `test_automatic_retry_increments`,
`test_hard_decline_increments_without_network_attempt` — asserts the counter moved **and** that no
charge row was written, the distinction the spec's own prose draws.
`test_nine_hard_decline_codes_exactly`, `test_hard_decline_codes_are_all_real_decline_codes`
(cross-checks the extracted 50-value enumeration),
`test_end_behavior_cancel`, `test_end_behavior_mark_unpaid`, `test_end_behavior_leave_past_due`,
`test_exhausted_cancel_leaves_invoice_open`,
`test_no_automatic_retry_fires_within_an_instance` — makes an arbitrary number of unrelated calls and
asserts `attempt_count` is unchanged, pinning the frozen-clock statement as a test rather than a
comment.
`test_retry_policy_is_not_a_subscription_field` — asserts no such column and no such API parameter.

### Ledger — `tests/billing/test_ledger.py`

`test_net_equals_amount_minus_fee`, `test_fee_details_sum_to_fee`,
`test_fee_rounds_half_up`, `test_available_on_is_t_plus_two_business_days`,
`test_available_on_skips_weekend`, `test_refund_available_immediately`,
`test_status_derived_not_stored`, `test_balance_splits_available_and_pending`,
`test_payout_draws_down_available_immediately`,
`test_payout_exceeding_available_is_balance_insufficient_and_writes_nothing`,
`test_payout_failure_writes_reversing_row_not_a_mutation`,
`test_dispute_withdrawal_is_adjustment`, `test_dispute_reversal_is_adjustment`,
`test_dispute_received_fee_never_returned`, `test_dispute_countered_fee_returned_on_win`,
`test_lost_dispute_writes_one_row_only`, `test_prevented_dispute_writes_no_rows`,
`test_unknown_balance_transaction_type_is_world_bug`.

### Invariants — `tests/invariants/test_billing_invariants.py`

I1–I15 parametrized over all three fixtures, each asserting zero rows. The same module is imported by
`evals/` so a reward function and its test cannot diverge.

### Conformance — `tests/conformance/`

Scenario 1 (half-cent tie-break, plus `billing_mode=flexible`) is the only scenario that can close an
assumption in this component. Scenario 5 (create → mid-cycle upgrade → cancel; `unpaid` recovery)
covers the quantity-reproration inference and the `unpaid` → `active` path. Scenario 6 (finalize,
pay, partial refund, over-refund) covers the invoice machine and I7. Scenario 7 (a dispute to
resolution) covers both fee behaviors and the two `adjustment` rows.

**Until scenario 1 is recorded, `round_cents_half_up`'s tie-break and the `flexible`-mode assumption
are the only two unproven statements in this component.** Everything else above is either taken from
`spec3.json`, quoted from a directly-read documentation page, or a world constant declared in
`allowed_differences.py` as a choice rather than a claim.
