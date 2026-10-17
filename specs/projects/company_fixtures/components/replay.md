---
status: complete
---

# Component: The Replay Engine (`fixtures_src/replay/`)

## Purpose and scope

Turns "this happened to this customer at this instant" into world writes, by calling the world's own
code at that instant. Shared by `small_co` and `big_co`.

**In scope:** the historical clock, the timeline, the API client and its helpers, the engine-step
wrappers, the subscription and payout drivers, the test-card and people catalogues, the two
finishing steps, and the checks.

**Out of scope:** what happens to whom and when (the company components decide), and any change to
world code. Everything here calls public world entry points (`dispatch`, `startup.open_account`,
`stripe_envelope.mint_request`) or the engine functions listed in `architecture.md` §2.4. It reads the
database freely and writes it only in `finish.py`.

## 1. `clock.py`

```python
def at(ctx: seahaven.Ctx, iso: str) -> seahaven.Ctx:
    """`ctx` with a fixed clock reading `iso`: same db, ids, state, instance and worlds.

    World code reads time only through `ctx.clock` (no world SQL calls a date function),
    so every row, id time-group and event written through the result carries `iso`.
    Seahaven derives call contexts the same way (`Ctx.with_call` is a dataclasses.replace).
    """
    return dataclasses.replace(ctx, clock=seahaven.Clock.from_iso(iso))


def plus(iso: str, *, days: float = 0, hours: float = 0, minutes: float = 0, seconds: float = 0) -> str:
    """`iso` shifted, rendered canonically (seahaven.Clock(...).iso())."""
```

Calendar months and years always come from the world's `invoicing.add_interval`, never from `plus`.

## 2. `timeline.py`

```python
class FixtureBuildError(Exception):
    """A step failed; the message names the step and its instant."""


@dataclass(frozen=True)
class Step:
    at: str
    seq: int
    label: str
    run: Callable[[seahaven.Ctx], None]


class Timeline:
    def __init__(self, base: seahaven.Ctx, *, until: str) -> None: ...

    @property
    def now(self) -> str:
        """The running step's instant; before run(), the earliest instant ever scheduled."""

    def add(self, at: str, label: str, run: Callable[[seahaven.Ctx], None]) -> bool:
        """Schedule `run` at `at`. Returns False (and drops it) when `at` is after `until`.
        Raises FixtureBuildError when `at` is before `now` while running: history only moves forward."""

    def run(self) -> None:
        """Pop steps in (at, seq) order and call step.run(at(base, step.at)) until none remain."""
```

- The heap holds `(at, seq, step)`; `seq` is a counter incremented per `add`, so ties run in
  scheduling order and steps never compare.
- Instants are canonical strings, so string order is time order.
- An exception from `step.run` is re-raised as `FixtureBuildError(f"{label} at {at}: {exc}")` with
  `__cause__` set.

## 3. `client.py` and `engine.py`

```python
class Client:
    def call(self, ctx: seahaven.Ctx, method: str, path: str,
             params: Mapping[str, Any] | None = None, *, automatic: bool = False) -> dict[str, Any]:
        """mint_request(ctx, None), then dispatch(ctx, method, path, params, idempotency_key=None).
        With `automatic=True` the request context is null instead (engine.automatic): Stripe acting
        on its own through the same handler, as when it finalizes a draft whose
        automatically_finalizes_at has come. Returns the body on 200; anything else (a
        StripeApiError, a non-200 response) raises FixtureBuildError with the status and the error
        body."""

    def attempt(self, ctx: seahaven.Ctx, method: str, path: str,
                params: Mapping[str, Any] | None = None) -> ApiResponse:
        """The same, but returns the response whatever its status, for steps whose expected
        outcome is a decline. The caller asserts the status it expects."""
```

`params` are typed Python values (ints, bools, nested dicts and lists), the shape the MCP tools pass,
not form strings. Parameter names follow each route's `Param`s in `resources/*.py`; when a story
needs a parameter the route does not declare, the story changes (`architecture.md` §2.6).

Helpers in `client.py`, each a small composition of `call`:

| Helper | Calls |
|---|---|
| `create_customer(ctx, *, name, email, phone=None, address=None, description=None, metadata=None, tax_exempt=None) -> str` | `POST /v1/customers` |
| `add_card(ctx, customer, card: Card, *, exp: tuple[int, int], default=True) -> str` | `POST /v1/payment_methods` (`type: card`, `card: {number, exp_month, exp_year, cvc: "123"}`, `billing_details.name`), `POST /v1/payment_methods/{pm}/attach`, then `POST /v1/customers/{c}` with `invoice_settings.default_payment_method` when `default` |
| `subscribe(ctx, customer, items, *, trial_days=None, trial_end=None, coupon=None, collection_method="charge_automatically", days_until_due=None, tax_rates=(), metadata=None) -> dict` | `POST /v1/subscriptions`; a trial carries `trial_settings.end_behavior.missing_payment_method: pause`; returns the body |
| `update_subscription(ctx, sub, params) -> dict` | `POST /v1/subscriptions/{sub}` |
| `cancel_subscription(ctx, sub, *, feedback, comment=None) -> dict` | `DELETE /v1/subscriptions/{sub}` with `cancellation_details` |
| `refund(ctx, charge, *, amount=None, reason="requested_by_customer") -> dict` | `POST /v1/refunds` |
| `invoice_item(ctx, customer, price) -> dict` | `POST /v1/invoiceitems` |
| `pay_invoice(ctx, invoice, *, out_of_band=False) -> dict` | `POST /v1/invoices/{in}/pay` |
| `latest_charge(ctx, customer) -> dict` | read: the customer's most recent succeeded charge, `ORDER BY x_seq DESC` |

```python
# engine.py: the transitions a frozen clock cannot fire (stripe_world billing_engine.md §7)
def automatic(ctx) -> None:          # ctx.state["_request"] = {"id": None, "idempotency_key": None}
def renew(ctx, sub_id) -> dict:      # automatic(ctx); subscription_lifecycle.advance_cycle(ctx, sub_id)
def expire(ctx, sub_id) -> dict:     # automatic(ctx); subscription_lifecycle.expire_incomplete(ctx, sub_id)
def fail_retry(ctx, invoice_id, decline_code) -> dict:
    # automatic(ctx); dunning.record_failed_attempt(ctx, invoice_id, decline_code=decline_code,
    #                                               automatic=True, at_iso=ctx.clock.iso())
def arrive(ctx, payout_id) -> dict:  # automatic(ctx); ledger.settle_payout(ctx, payout_id)
```

## 4. `cards.py` and `people.py`

```python
@dataclass(frozen=True)
class Card:
    number: str
    brand: str
    weight: int = 0          # share among GOOD cards

GOOD = (Card("4242424242424242", "visa", 45), Card("4000056655665556", "visa", 10),
        Card("5555555555554444", "mastercard", 20), Card("5200828282828210", "mastercard", 5),
        Card("378282246310005", "amex", 15), Card("6011111111111117", "discover", 5))
DECLINING = Card("4000000000000341", "visa")          # attaches; every charge declines
DECLINING_CODE = "generic_decline"
DISPUTE_FRAUDULENT = Card("4000000000000259", "visa")
DISPUTE_NOT_RECEIVED = Card("4000000000002685", "visa")
DISPUTE_INQUIRY = Card("4000000000001976", "visa")
AUTH_REQUIRED = Card("4000002760003184", "visa")
ALL = GOOD + (DECLINING, DISPUTE_FRAUDULENT, DISPUTE_NOT_RECEIVED, DISPUTE_INQUIRY, AUTH_REQUIRED)

def pick_good(rng) -> Card                     # weighted by `weight`
def expiry(rng, from_iso: str) -> tuple[int, int]   # a month 8–48 months after from_iso
```

`test_cards_match_the_world` asserts each number's behavior through `magic_cards.card_for`: `GOOD`
succeed with the stated brand; `DECLINING` attaches and declines with `DECLINING_CODE`; the dispute
cards carry the stated reason and track; `AUTH_REQUIRED` requires authentication.

`people.py` holds the data and the deterministic draws `big_co` needs (`small_co`'s cast is written
by hand):

- `FIRST_NAMES`, `LAST_NAMES`: about 200 each, mixed across origins, no famous full names.
- `TRADE_WORDS`, `BUSINESS_PATTERNS`: e.g. `"{last} Heating & Air"`, `"{place} Plumbing Co."`,
  `"{last} & Sons Electric"`, `"{place} Mechanical"`; `PLACE_WORDS` are generic geography
  ("Summit", "Front Range", "Blue Mesa", "Cedar Creek").
- `CITIES`: about 150 real `(city, state, zip)` triples, each with a real area code, weighted by
  state toward large trades markets (TX, CA, FL, CO, AZ, GA, NC, OH, PA, NY, WA, IL, TN, …).
- `STREET_NAMES`: invented-generic names ("Larkin Ave", "Old Mill Rd").
- `business_name(rng)`, `domain_for(name, rng)` (slug of the name without its suffix word,
  `.com` 80%, `.net` 10%, `.co` 5%, `.us` 5%), `contact_email(first, last, domain, rng)`
  (`billing@`, `office@`, `{first}@`, `{first}.{last}@`), `address(rng, state=None)`,
  `phone(rng, area_code)` (`+1{area}5550{100–199}`).

## 5. `subscriptions.py`: the driver

Keeps every subscription moving from its creation to `NOW`, asking the company's hooks at each
decision point.

```python
class Hooks(Protocol):
    def before_boundary(self, ctx: seahaven.Ctx, sub_id: str, n: int) -> None: ...
    def after_boundary(self, ctx: seahaven.Ctx, sub_id: str, n: int) -> None: ...
    def on_open_invoice(self, ctx: seahaven.Ctx, sub_id: str, invoice_id: str) -> None: ...
    def on_retry(self, ctx: seahaven.Ctx, sub_id: str, invoice_id: str, attempt: int) -> bool: ...
    # True: the hook made the payment possible (e.g. added a good card) and the driver pays;
    # False: the retry fails.
    def on_status_change(self, ctx: seahaven.Ctx, sub_id: str, old: str, new: str) -> None: ...


class Subscriptions:
    def __init__(self, timeline: Timeline, client: Client, hooks: Hooks) -> None: ...
    def follow(self, ctx: seahaven.Ctx, sub_id: str) -> None:
        """Call right after a subscription is created. Schedules whatever happens to it next."""
```

`follow` reads the subscription and its latest invoice and schedules, by status:

| Status after the last step | Next step scheduled |
|---|---|
| `active`, `trialing`, `past_due`, `unpaid` | a **boundary** at the subscription's period end (`MIN(subscription_items.current_period_end)`) |
| `incomplete` | an **expiry** at `created + 23 hours` |
| `past_due` with an open latest invoice whose `next_payment_attempt` is set | also a **retry** at `next_payment_attempt` |
| `canceled`, `incomplete_expired`, `paused` | nothing; the driver stops following |
| any, with a `draft` latest invoice that has `auto_advance` and an `automatically_finalizes_at` | also a **finalization** at that instant |

The steps:

- **Boundary** (`n` counts boundaries since creation, starting at 1): `hooks.before_boundary`, then
  `engine.renew`, then `hooks.after_boundary`. If the renewal left a `send_invoice` invoice open,
  `hooks.on_open_invoice`. Then `follow` again.
- **Retry**: if the invoice is no longer open (paid meanwhile), nothing. Else if
  `hooks.on_retry(...)` is true, `client.pay_invoice` (the world's `on_invoice_paid` returns the
  subscription to `active`); else `engine.fail_retry(ctx, invoice, DECLINING_CODE)`, which may apply
  the end-of-schedule outcome. Then `follow` again.
- **Expiry**: `engine.expire` if still `incomplete`.
- **Finalization**: if still `draft`, `client.call(ctx, "POST", f"/v1/invoices/{id}/finalize",
  automatic=True)`; then, if it is a `send_invoice` invoice now open, `hooks.on_open_invoice`.

After every step the driver compares the subscription's status with the last one it saw and calls
`hooks.on_status_change` when it moved.

`finalize_when_due(timeline, client, invoice: dict, on_open: Callable[[Ctx, str], None])` schedules
the same finalization for an invoice made outside a subscription (an onboarding invoice) and calls
`on_open` once it is open.

A retry and a boundary can coexist for a `past_due` subscription; whichever instant comes first runs
first. The driver keeps `n` per subscription in a dict keyed by id, iterated only by key lookup.

## 6. `payouts.py` and `finish.py`

```python
class Payouts:
    def __init__(self, timeline: Timeline, client: Client, *, destination: str) -> None: ...
    def schedule(self, instants: Iterable[str]) -> None:
        """At each instant: GET /v1/balance; when the available usd amount is positive,
        POST /v1/payouts {amount, currency: "usd", destination}; then schedule
        engine.arrive at the payout's arrival_date (dropped if after NOW, leaving it pending)."""
```

```python
def refresh_balance_statuses(ctx: seahaven.Ctx, *, now: str) -> int:
    """For every balance transaction stored `pending`, set status = ledger.bt_status(available_on, now)
    where that differs. Returns the number changed."""

def refresh_coupon_validity(ctx: seahaven.Ctx, *, now: str) -> int:
    """For every coupon, set valid = coupons.is_valid(row, now) where that differs (a redeem_by
    that passed with no write since). Returns the number changed."""

def prune_ended_discounts(ctx: seahaven.Ctx, *, now: str) -> int:
    """For every subscription, drop the stored discounts subscription_lifecycle.discount_ended
    says have ended by now (no invoice has billed since to prune them). Returns the number of
    subscriptions changed."""

def trim_events(ctx: seahaven.Ctx, *, now: str) -> int:
    """DELETE FROM events WHERE created < now − 30 days (computed with plus()). Returns the count."""
```

All four run inside the builder's `bulk()` after `timeline.run()`. They are the only writes this
package makes to rows the world wrote. The coupon refresh and the discount prune were added with
the fix for issue #13: the API answers a coupon's `valid` and a subscription's `discounts` at read
time, and these make the stored columns agree with it at `NOW`.

## 7. `checks.py`

```python
@dataclass(frozen=True)
class Expectation:
    name: str
    sql: str        # SELECT count(*) AS n ...
    low: int
    high: int       # equal to low for an exact count

def check_fixture(db: seahaven.Db, *, now: str, expect: Sequence[Expectation]) -> None  # db = inst.inspect()
```

Invariants, each a query returning offending rows (the check fails if any row comes back). Column
names are this schema's; where an invariant reads JSON, the coder confirms the stored shape in the
writing module first (`billing/invoicing.py` for invoice lines).

| Invariant | Query (abridged) |
|---|---|
| net is amount − fee | `SELECT id FROM balance_transactions WHERE net <> amount - fee` |
| status agrees with `bt_status` at `NOW` | Python over `SELECT id, available_on, status FROM balance_transactions` |
| coupon `valid` agrees with `coupons.is_valid` at `NOW` | Python over `SELECT * FROM coupons` (added with the fix for issue #13) |
| no subscription carries a discount past its end at `NOW` | Python over `SELECT id, discounts FROM subscriptions`, by `subscription_lifecycle.discount_ended` (added with the fix for issue #13) |
| no payout overdrew | for each payout bt in `x_seq` order: `SELECT SUM(net) FROM balance_transactions WHERE balance_type = 'payments' AND x_seq <= :seq AND available_on <= :created` must be ≥ 0 |
| no over-refund | `SELECT c.id FROM charges c WHERE (SELECT COALESCE(SUM(amount), 0) FROM refunds r WHERE r.charge = c.id AND r.status = 'succeeded') > c.amount_captured` |
| lines sum to subtotal | `SELECT id FROM invoices WHERE subtotal <> (SELECT COALESCE(SUM(json_extract(l.value, '$.amount')), 0) FROM json_each(invoices.lines) l)` |
| invoice discounts are inside their windows | over every `invoices i, json_each(i.discounts) d` with a non-null `end`: `unixepoch(i.period_end) >= end` (added with the fix for issue #13: a repeating coupon never ended) |
| line ids are unique | over every `invoices i, json_each(i.lines)`: a line `id` that appears more than once (added with the fix for issue #14: a draft rebuild keyed a proration line by its item and period start, so a credit/debit pair shared one id) |
| no overlapping cycle periods | `invoices a JOIN invoices b ON a.parent_subscription = b.parent_subscription AND a.id < b.id WHERE both billing_reason = 'subscription_cycle' AND neither void AND a.period_start < b.period_end AND b.period_start < a.period_end` |
| no overlapping item line periods | over `invoices i, json_each(i.lines)` on invoices not void: two lines with the same `parent.subscription_item_details.subscription_item`, neither a proration (a missing `proration` flag counts as false) and neither `amount = 0`, whose `period`s overlap. A $0 line bills nothing, so a trial's `Free trial` line may overlap the first paid period of a trial ended early (added with the fix for issue #12, which the invoice-level check could not see) |
| causality | charge before its payment intent; refund or dispute before its charge; balance transaction before the charge, refund, dispute or payout in its `source` |
| customer balance | `customers.balance <> COALESCE((latest cbt by x_seq).ending_balance, 0)` |
| invoice sequence | per customer, `next_invoice_sequence <> 1 + MAX(CAST(substr(number, instr(number, '-') + 1) AS INTEGER))` over numbered invoices (or `<> 1` with none) |
| status agrees with latest invoice | `past_due`/`unpaid` with a latest invoice not `open`/`draft`; `trialing` with `trial_end <= NOW`; `canceled`/`incomplete_expired` with `ended_at IS NULL` |
| nothing after `NOW` | every `created` column, and `canceled_at`, `ended_at`, `voided_at`, `effective_at`, `released_at`, `completed_at`, `invoiceitems.date`, `> NOW` |
| events window | `SELECT id FROM events WHERE created < NOW − 30 days` |

Realism guards: as `architecture.md` §3.1.

## 8. Test plan

| Test | Verifies |
|---|---|
| `test_at_reads_the_instant` | `at(ctx, t).clock.iso() == t`; the base context's clock is untouched |
| `test_rows_ids_and_events_carry_the_instant` | a customer and a payment intent created through `Client.call` at `t` have `created = t`; the `pi_` id's time group encodes `t`; its events have `created = t` and a `req_` request id |
| `test_engine_steps_carry_no_request` | an event from `engine.renew` has a null request id |
| `test_writes_land_in_the_bulk_transaction` | rows written through `at()` are visible to `inst.inspect()` after `bulk()` exits, and none if the block raises |
| `test_timeline_order_and_ties` | steps run by instant, ties by scheduling order |
| `test_timeline_drops_the_future` | `add` after `until` returns False and never runs |
| `test_timeline_refuses_the_past` | scheduling before `now` while running raises |
| `test_step_errors_name_the_step` | a failing step raises `FixtureBuildError` naming label and instant |
| `test_monthly_subscription_renews_to_now` | created 100 days before `NOW`: three paid cycle invoices, periods contiguous, the next boundary after `NOW` |
| `test_trial_without_card_pauses` | `paused` at the trial end |
| `test_declining_card_walks_dunning` | swap to `DECLINING` before a boundary: `past_due`, retries every `window / max_attempts`, end-of-schedule outcome per the account's dunning settings |
| `test_retry_recovers_when_hook_pays` | `on_retry` true after a card swap: invoice paid, subscription `active` |
| `test_incomplete_expires` | `AUTH_REQUIRED` signup: `incomplete`, then `incomplete_expired` 23 hours later |
| `test_payouts_arrive_or_stay_pending` | a payout whose arrival is before `NOW` is `paid`, after is `pending`; amounts never exceed the available balance |
| `test_refresh_and_trim` | the finishing steps change exactly the stale statuses and old events |
| `test_refresh_coupon_validity` | a coupon whose `redeem_by` passed is stored invalid at `NOW`, once; one still redeemable is untouched |
| `test_prune_ended_discounts` | a canceled subscription's ended discount is dropped at `NOW`, once; its `forever` discount stays |
| `test_cards_match_the_world` | §4 |
| `test_each_check_catches_its_violation` | each invariant and guard, against a throwaway instance with one planted violation |
| `test_benchmark` | Phase 2's measurement (not an assertion): 200 customers over 12 months; records build seconds and file bytes, and writes the projection for `big_co` into the phase plan |
