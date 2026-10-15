---
status: complete
---

# Component: `big_co` (Ostrander Systems)

## Purpose and scope

Builds the `big_co` fixture: the account, the catalog, a drawn population of about 3,000 business
customers, their lives over 547 days, the scripted loose ends, daily payouts, and the checks.
Functional spec §5 is the contract; this document is the recipe.

**Not in scope:** the replay machinery (`replay.md`) and any world change.

## Public interface

```python
# fixtures_src/big_co/__init__.py
def build(inst: seahaven.Instance) -> None
EXPECTATIONS: tuple[Expectation, ...]                        # §8

# fixtures_src/big_co/shape.py
SHAPE: Shape                                                 # §1
CATALOG: tuple[ProductSpec, ...]                             # §2

# fixtures_src/big_co/population.py
def draw_population(rng: random.Random, shape: Shape) -> list[Prospect]   # pure, §3

# fixtures_src/big_co/simulate.py
class Lives:                                                 # implements replay Hooks, §4
    def __init__(self, timeline, client, subs, catalog, rng, shape) -> None
    def schedule(self, prospects: Sequence[Prospect]) -> None

# fixtures_src/big_co/loose_ends.py
def scripted(shape: Shape) -> list[Prospect]                 # §5, fixed, no randomness
```

`build`:

```python
with inst.bulk() as ctx:
    rng = random.Random(ctx.ids.random.getrandbits(64))
    install_account(ctx, accounts.OSTRANDER)
    tl = Timeline(ctx, until=NOW)
    client = Client()
    catalog = Catalog(); catalog.schedule(tl, client)        # 2025-02-11 onward, §2
    lives = Lives(tl, client, ..., rng=rng, shape=SHAPE)
    subs = Subscriptions(tl, client, lives); lives.subs = subs
    lives.schedule(draw_population(rng, SHAPE) + scripted(SHAPE))
    lives.schedule_sla_batch()                               # §4.7
    Payouts(tl, client, destination=accounts.OSTRANDER.bank_account_id).schedule(business_days())
    tl.run()
    refresh_balance_statuses(ctx, now=NOW)
    trim_events(ctx, now=NOW)
check_fixture(inst.inspect(), now=NOW, expect=EXPECTATIONS)
```

## 1. The shape (`shape.py`)

```python
@dataclass(frozen=True)
class Shape:
    migration_customers: int = 1450
    organic_customers: int = 1550
    migration_start: str = "2025-03-03T14:00:00.000Z"
    migration_end: str = "2025-03-07T22:00:00.000Z"
    organic_start: str = "2025-03-10T00:00:00.000Z"
    growth: float = 2.0                         # organic signup density at NOW ÷ at organic_start
    tier_mix: Mapping[str, float]               # starter .40, pro .35, business .20, enterprise .05
    annual_share: Mapping[str, float]           # starter .10, pro .25, business .50, enterprise 1.0
    seats: Mapping[str, tuple[int, int]]        # starter 1–5, pro 3–15, business 10–60, enterprise 40–400
    legacy_share: float = 0.30                  # of migrated starter/pro
    fleet_share: float = 0.30                   # of pro and above; vehicles ≈ seats ± 20%
    sms_share: float = 0.40
    phone_share: float = 0.60
    no_email_share: float = 0.04                # of migrated
    tax_exempt_customers: int = 20
    trial_conversion: float = 0.65
    cardless_sales_trial_share: float = 0.25    # of organic business
    auth_required_share: float = 0.01           # of non-trial organic signups: first payment needs auth
    monthly_churn: Mapping[str, float]          # starter .025, pro .015, business .010
    annual_nonrenewal: float = 0.15
    enterprise_nonrenewal: float = 0.08
    churn_at_period_end_share: float = 0.70     # the rest cancel at once
    seat_change_monthly: float = 0.06
    seat_up_share: float = 0.70
    upgrade_monthly: float = 0.01
    immediate_downgrade_monthly: float = 0.001
    decline_per_renewal: float = 0.08           # card renewals
    decline_recovers: float = 0.75
    unpaid_cancel_after_days: int = 45
    refund_per_charge: float = 0.03
    refund_partial_share: float = 0.50
    dispute_per_charge: float = 0.003
    dispute_mix: Mapping[str, float]            # fraudulent .60, not_received .25, inquiry .15
    dispute_fresh_days: int = 21
    fresh_evidence_share: float = 0.40
    settled_won_share: float = 0.45
    fraud_dispute_cancels: float = 0.50
    switch25_share: float = 0.08                # of organic
    association_share: float = 0.05
    concessions: int = 40
    scheduled_downgrades: int = 10
    credit_note_per_invoice: float = 0.005
    enterprise_card_share: float = 0.60
    duplicate_pairs: int = 25
    cs_notes: int = 30
```

The Phase 2 benchmark projects build time and file size for these numbers. If the projection breaks
the budget (functional spec §3.5), the coder stops before Phase 4 and raises it with the user with
the scaled numbers that would fit; `migration_customers` and `organic_customers` scale together.

## 2. Catalog (`shape.CATALOG`, created by `Catalog.schedule`)

| Instant | Step |
|---|---|
| 2025-02-11T16:00Z | products FieldOps Starter, Pro, Business, Enterprise, Fleet Tracking, SMS Reminders, Onboarding, Legacy Starter, Legacy Pro; prices per functional spec §5.2 (monthly and annual for each seat plan and add-on, `lookup_key` `starter_monthly`, `starter_annual`, …, `fleet_monthly`, …, `sms_annual`; onboarding `onboarding_pro` $499, `onboarding_business` $1,499, `onboarding_enterprise` $4,999; `legacy_starter_monthly` $29, `legacy_pro_monthly` $59) |
| 2025-02-11T16:30Z | tax rates: Texas 6.25, Washington 6.5, New York 4, Pennsylvania 6, Ohio 5.75, Tennessee 7 (`display_name` "Sales Tax", `jurisdiction` and `state` the state code, `country` US, `tax_type` sales_tax, `inclusive` false) |
| 2025-03-08T16:00Z | legacy prices and products archived (`active: false`); migrated subscriptions keep them |
| 2025-04-01T15:00Z | coupon `SWITCH25`: 25%, repeating 3 months, name "Competitive switch: 25% off 3 months" |
| 2025-05-15T15:00Z | coupons `MSPG10`, `FRHA10`, `RMEC10`: 10% forever, named for their associations |

Enterprise prices are made per deal (§4.4). Sales concessions are coupons made per use (§4.6).

## 3. The population (`population.py`)

`draw_population` is pure: the same `rng` state and shape give the same list. It returns prospects
sorted by `created`, then `key`.

```python
@dataclass(frozen=True)
class Prospect:
    key: str                     # "m0001"…, "o0001"…, "d0001"… (duplicates), "le_…" (loose ends)
    cohort: Literal["migration", "organic", "duplicate", "loose_end"]
    created: str
    company: str
    domain: str
    email: str | None
    phone: str | None
    address: Address
    description: str | None      # a customer-success note for `cs_notes` prospects
    metadata: Mapping[str, str]
    tax_exempt: bool
    tier: Literal["starter", "pro", "business", "enterprise"]
    interval: Literal["month", "year"]
    legacy: bool
    seats: int
    vehicles: int | None
    sms: bool
    card: Card | None
    card_exp: tuple[int, int] | None
    trial: Literal["none", "card", "cardless", "migration"]
    trial_end: str | None        # migration: the legacy next renewal date
    converts: bool               # trials only
    coupon: str | None
    onboarding: str | None       # the onboarding price key, invoiced by sales
    enterprise_rate: int | None  # cents per tech per month, enterprise only
    pays_by_card: bool           # enterprise invoices: card (else outside Stripe)
    subscribes: bool             # false for duplicates that never subscribed
    scripted: bool               # loose ends: the random life draws skip them
    script: tuple[Any, ...]      # loose ends only: their fixed beats (§5)
```

Drawing rules:

- **Migration cohort.** `created` spaced evenly through migration week's import window, so imports
  look batched. `metadata = {"legacy_account_id": "OS-" + 6 unique digits, "customer_since": a date
  2019-01-01–2025-02-28}`. `trial = "migration"`, `trial_end` = a legacy renewal date uniform over
  the next 1–30 days (monthly) or 1–365 days (annual), at the same time of day as `created`.
  Starter and Pro take a legacy price with `legacy_share`. Email omitted with `no_email_share`.
- **Organic.** `created` drawn with density rising linearly from 1 to `growth` between
  `organic_start` and `NOW − 1 day`, at a time of day between 13:00Z and 23:59Z. Starter and Pro
  are self-serve: `trial = "card"`, `converts` with `trial_conversion`. Business is sales-led:
  `cardless_sales_trial_share` take `trial = "cardless"` (converting means adding a card before the
  trial ends), the rest `trial = "none"` with `onboarding_business`. Pro takes `onboarding_pro` with
  probability 0.4. Enterprise is `trial = "none"`, `onboarding_enterprise`, `enterprise_rate` uniform
  $70–$95. Sales-led prospects get `metadata` with a CRM id under `crm_account_id` (70%), `CRM ID`
  (20%) or `crmAccountId` (10%). `coupon`: `SWITCH25` with `switch25_share`.
- **Everyone.** Company and contact from `replay.people`, the association coupon by trade (plumbing
  `MSPG10`, HVAC `FRHA10`, electrical `RMEC10`) with `association_share` when no other coupon, the
  card from `cards.pick_good` (absent for cardless trials), `tax_exempt` for exactly
  `tax_exempt_customers` prospects named as school districts or municipal facilities departments,
  `cs_notes` descriptions from a fixed list. Non-trial organic signups take
  `cards.AUTH_REQUIRED` with `auth_required_share`.
- **Duplicates.** `duplicate_pairs` prospects copy an earlier prospect's company with a variant name
  ("&" ↔ "and", an added "LLC" or "Inc.", a dropped "Co.") and a different contact email, created
  5–200 days later; 60% never subscribe, 40% start a self-serve trial that is canceled.

## 4. Lives (`simulate.py`)

`Lives.schedule` adds, for each prospect, a step at `created`:

1. `create_customer` (name = company, email, phone, address, description, metadata, `tax_exempt`).
2. The card, when the prospect has one.
3. For Enterprise, the deal price: `POST /v1/prices` on the Enterprise product, yearly, per tech,
   `unit_amount = enterprise_rate × 12`, nickname `"Enterprise — {company} {year}"`.
4. The subscription (when `subscribes`): items are the seat price (quantity `seats`), Fleet
   Tracking (quantity `vehicles`) and SMS Reminders when taken, all on the prospect's interval;
   `default_tax_rates` from the address's state when taxed and not exempt; `discounts` from
   `coupon`; trial per `trial`; Enterprise and migrated Enterprise use `collection_method:
   send_invoice`, `days_until_due: 30`. Enterprise deals signed in the last 90 days are created as a
   **ramp schedule** instead (§4.5). Then `subs.follow`.
5. Onboarding, when set: `invoice_item` then `POST /v1/invoices` (`send_invoice`,
   `days_until_due: 30`, `pending_invoice_items_behavior: include`) two hours after `created`;
   finalized automatically when due (`replay.md` §5), then paid per §4.4.
6. For a non-converting self-serve trial: `CancelNow(unused)` at a uniform instant 3–13 days in.
   For a converting cardless trial: the card is added at a uniform instant before the trial ends.
7. Loose-end prospects run their `script` instead of 4–6 (§5).

### 4.1 At each boundary (`Lives` as the driver's `Hooks`)

Scripted prospects skip every random draw. For the others, `after_boundary(ctx, sub, n)` (and the
equivalent step right after creation, as `n = 0`) draws for the **coming** period, using the
period's start and end read from the subscription:

| Draw | Probability for the period | Scheduled |
|---|---|---|
| churn | monthly: `monthly_churn[tier]`; annual: `annual_nonrenewal` (Enterprise `enterprise_nonrenewal`) | `churn_at_period_end_share`: `cancel_at_period_end` at a uniform instant in the period; else `CancelNow` at a uniform instant, feedback from (`too_expensive`, `switched_service`, `missing_features`, `unused`, `customer_service`) |
| seat change | Poisson with mean `seat_change_monthly × months in period` | each at a uniform instant: quantity ±1–3 (Starter/Pro) or ±5–20% (Business/Enterprise), up with `seat_up_share`, clamped to the tier's range; `proration_behavior: create_prorations` |
| upgrade | `upgrade_monthly × months` | price → next tier's price on the same interval, `create_prorations` |
| immediate downgrade | `immediate_downgrade_monthly × months` | price → previous tier, `always_invoice` |
| decline at the coming boundary | `decline_per_renewal`, card-paid only | a step 1–5 days before the boundary swaps the default card to `DECLINING`; with `decline_recovers`, a step 1–10 days after the boundary adds a good card |
| dispute at the coming boundary | `dispute_per_charge`, card-paid only, exclusive with decline | a step 1–3 days before the boundary swaps to a dispute card by `dispute_mix`; the step right after the boundary adds a good card back |
| credit note on this boundary's invoice | `credit_note_per_invoice`, paid invoices | `POST /v1/credit_notes` 1–20 days later, 5–100% of the total to customer balance, reason `order_change` or `duplicate` |
| refund of this boundary's charge | `refund_per_charge`, succeeded charges | 1–14 days later; partial (10–50%) with `refund_partial_share`, reason `requested_by_customer` or `duplicate` |

`on_retry` returns true when the customer's default card is not `DECLINING` (they added a good one).
`on_status_change` to `unpaid` schedules `CancelNow(customer_service, "Unpaid: closed by
customer success")` `unpaid_cancel_after_days` later.

### 4.2 Disputes after the boundary

When a renewal's charge opens a dispute, `after_boundary` decides its course from its age at `NOW`:

- **Fresh** (opened within `dispute_fresh_days` of `NOW`): left as it is (`needs_response` or
  `warning_needs_response`), or with `fresh_evidence_share` a plain evidence submission 1–5 days in
  (`under_review` / `warning_under_review`), if that day is before `NOW`.
- **Settled** (older): with `settled_won_share`, evidence with `uncategorized_text:
  "winning_evidence"` 3–10 days in (`won`); otherwise either `/close` 2–10 days in or evidence with
  `"losing_evidence"` (`lost`), half each.
- A fraudulent dispute ends the subscription with `fraud_dispute_cancels`: `CancelNow(other,
  "Disputed as fraudulent")` 0–3 days in.

### 4.3 Payment of open invoices (`on_open_invoice`)

For `send_invoice` invoices (Enterprise renewals and every onboarding invoice): card payers pay
5–28 days after finalization (`pay_invoice`); others are marked paid outside Stripe 3–35 days after
(`pay_invoice(out_of_band=True)`). Steps falling after `NOW` are dropped, leaving the invoice open.

### 4.4 Enterprise

The deal price, the subscription or ramp, and the onboarding invoice, at signup (§4). Card payers
get a card on file at signup; the rest have none.

### 4.5 Ramps and scheduled downgrades

- **Ramps.** An Enterprise deal signed within 90 days of `NOW` is created as `POST
  /v1/subscription_schedules` with `start_date` now, `end_behavior: release`, three phases (3 months
  at the signed seats, 3 months at ×1.5, then 6 months at ×2), each with the deal price, and default
  settings `send_invoice`, 30 days. The schedule creates the subscription; `subs.follow` takes it.
  Every such schedule is still in its first phase at `NOW` (functional spec §3.3).
- **Scheduled downgrades.** Exactly `scheduled_downgrades` subscriptions, chosen at their last
  boundary before `NOW` among Pro and Business monthly or annual subscriptions whose next boundary
  falls after `NOW`: a step at a uniform instant between that boundary and `NOW` makes `POST
  /v1/subscription_schedules` with `from_subscription`, then updates it with two phases (the current
  items to the period end, then the lower tier's price from the period end).

### 4.6 Concessions, mess and notes

- `concessions` active subscriptions (drawn at the 2025-06-01 and later boundaries until the count is
  reached) get a coupon made for them (`amount_off` $100–$500, `duration: once`, name "Sales
  concession: {company}") and applied by `update_subscription` with `discounts`.
- Duplicates, missing emails, metadata variants and notes come from the population (§3).

### 4.7 The SLA batch

A step at 2026-04-20T15:00Z selects every subscription whose seat price is a Business or Enterprise
price, created on or before 2026-04-14 and not ended before it (`ORDER BY created, id`), and
schedules one credit-note step per customer, spread evenly over business hours of 2026-04-20 to
2026-04-24: `POST /v1/credit_notes` on the customer's latest paid subscription invoice, amount 10% of
its total, `credit_amount` the same, memo "Service credit: April 14 outage", reason
`product_unsatisfactory`.

### 4.8 Payouts

`business_days()` yields Monday to Friday at 15:00Z from 2025-03-05 to 2026-08-31.

## 5. Loose ends (`loose_ends.py`)

Fixed prospects with `scripted=True`, invented names, good cards unless stated, and fixed beats.
Instants are UTC.

| Key | Company | Setup | State at `NOW` |
|---|---|---|---|
| `le_queue_1`…`4` | Arroyo Air & Heat, Tamarack Plumbing, Gridline Electric Co., Sandstone Mechanical | Pro monthly, created 2026-05-26T16:00Z, 05-27T18:30Z, 05-28T15:15Z, 05-29T20:45Z; a dispute card (fraudulent, not received, fraudulent, not received) swapped in a day before the August boundary | four disputes `needs_response`, due 09-03 to 09-06 |
| `le_inquiry_open`, `le_inquiry_review` | Ridgeway Heating, Larkspur Electric | Pro monthly, created 2026-06-15T16:00Z and 06-18T16:00Z; `DISPUTE_INQUIRY` swapped in a day before the August boundary; the second submits plain evidence on 08-21 | `warning_needs_response`, `warning_under_review` |
| `le_review` | Coldwater Plumbing Supply | Pro monthly, created 2026-06-12T16:00Z; `DISPUTE_FRAUDULENT` swapped in a day before the August boundary; plain evidence on 08-16 | `under_review` |
| `le_overdue` | Halvorsen Mechanical Group | Enterprise, 60 seats at $85/tech/month (annual $61,200), card payer, signed 2025-06-18T17:00Z; first invoice paid 2025-07-10; the 2026-06-18 renewal invoice is never paid | open, due 2026-07-18, 45 days past due |
| `le_ramp` | Cascade Comfort Systems | Enterprise ramp schedule created 2026-06-04T16:00Z: 80 seats to 09-04, 120 to 12-04, 160 to 2027-06-04 | first phase; steps up 2026-09-04 |
| `le_churn_risk` | Copperline Mechanical | Business annual, 48 seats, created 2025-09-13T15:00Z; `cancel_at_period_end` set 2026-08-25T19:00Z (`switched_service`) | active, ends 2026-09-13 |
| `le_incomplete` | Bristlecone Electric | Pro monthly, `AUTH_REQUIRED`, no trial, created 2026-09-01T11:00Z | `incomplete` |
| `le_expired_1`…`3` | three Starter shops | `AUTH_REQUIRED`, no trial, created 2026-07-08, 08-03, 08-21 at 15:00Z | `incomplete_expired` |
| `le_paused_1`…`3` | three Business shops | cardless sales trials created 2026-07-06, 07-27, 08-10 at 16:00Z, never converted | `paused` |
| `le_unpaid_1`…`3` | three Starter shops | monthly since 2026-04-10, 04-14, 04-18 at 15:00Z; `DECLINING` swapped in before the July boundary; never recover | `unpaid` (dunning of 8 tries over 3 weeks ends in early August; the 45-day closure falls after `NOW`) |
| `le_past_due_1`…`3` | three Pro shops | monthly since 2026-05-25, 05-27, 05-29 at 15:00Z; `DECLINING` before the August boundary | `past_due` |
| `le_draft_1`…`5` | five Business shops | created 2026-09-01T13:00Z, 13:10, 13:20, 13:30, 13:40; Business monthly with a card; onboarding invoice five minutes after | five draft onboarding invoices with `automatically_finalizes_at` after `NOW` |

## 6. Real-account mess

Per functional spec §5.6, produced by the population draw (§3).

## 7. Guide landmarks

`big_co/dispute_queue`, `big_co/dispute_stages`, `big_co/overdue_enterprise`, `big_co/next_ramp_step`,
`big_co/churn_risk`, `big_co/status_counts`, `big_co/draft_invoices`, `big_co/pending_payouts`,
`big_co/duplicate_customers` (by normalized name), `big_co/migration_cohort` (count by
`metadata.legacy_account_id` presence), `big_co/sla_credits` (count and total).

## 8. Expectations

| Name | Range |
|---|---|
| customers | 2,800 – 3,200 |
| subscriptions `active` | 1,900 – 2,300 |
| each of `trialing`, `past_due`, `canceled`, `unpaid`, `paused`, `incomplete_expired` | at least 3 |
| `incomplete` | at least 1 |
| disputes | 60 – 110, with each of the six stages of functional spec §3.2 at least once |
| payouts | 350 – 400, at least one `pending` |
| SLA credit notes | at least 100, each with memo "Service credit: April 14 outage" |
| invoices `draft` created after `NOW − 1 hour` | at least 5 |
| events | all within the last 30 days |
| file size | 25 MB or less |

## 9. Test plan (`tests/test_big_co.py`, `tests/test_population.py`)

| Test | Verifies |
|---|---|
| `test_population_is_pure` | the same seed gives an identical list; a different seed a different one |
| `test_population_shape` | cohort sizes; tier mix within ±3 points; every prospect has a domain and an address; exactly 20 exempt; duplicates reference an earlier prospect; no denied domain or cliché |
| `test_account_identity` | as `small_co`, for Ostrander Systems |
| `test_opening_live_is_refused` | as `small_co` |
| `test_expectations` | §8 through `check_fixture` |
| `test_each_loose_end` | one assertion block per row of §5, by company name |
| `test_tools_carry_on` | as `small_co`, on Halvorsen and a random active customer |
| `test_file_size` | `state.sqlite` ≤ 25 MB |
