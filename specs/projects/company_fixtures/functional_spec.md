---
status: complete
---

# Functional Spec: Company Fixtures

Two committed fixtures, `small_co` (Wrenfield Labs) and `big_co` (Ostrander Systems), each the live
Stripe account of one fictional company, frozen at `NOW = 2026-09-01T14:00:00.000Z` (a Tuesday).
The overview (`project_overview.md`) is the brief; this document is the contract.

## 1. What ships

| Deliverable | Where | Kind |
|---|---|---|
| The account moves into the database | schema, `startup.py` | **The only world change** (§2) |
| `small_co` builder and its hand-written cast | `fixtures_src/` | Generator code and data |
| `big_co` builder (simulation plus scripted loose ends) | `fixtures_src/` | Generator code |
| The two frozen fixtures, and `empty` rebuilt | `fixtures/` | Committed binaries |
| The fixture guide | `docs/fixtures.md` | Documentation |
| Tests for all of the above | `tests/` | Tests |
| The fixture line in `AGENTS.md` and the README | root | Documentation |

Nothing else in the world changes. The billing helpers the generator reuses (`add_interval`,
`compute_totals`, `build_item_lines`, `proration_lines`, `compute_automatically_finalizes_at`,
`next_payment_attempt_at`, the ledger's `available_on` and `stripe_fee`, dunning's `next_attempt_at`)
already take an explicit timestamp rather than reading the clock. Twelve triggers in
`005_search.sql` already keep the search index in step with every insert, so bulk-written rows are
searchable without extra work. A world bug found while building is raised with the user, not
patched in passing.

The `stripe_world` specs are not edited. They are point-in-time records and still say `small` and
`large`.

## 2. The account in the database

This is Phase 1 and lands before any fixture is built.

### 2.1 Behavior

- The account is one row in a new table. The row holds the full account object (what
  `GET /v1/account` and `get_stripe_account_info` return) and the account's settings that the world
  already reads from `ctx.state["account"]`: `ledger` (fees, settlement days, dispute fee) and
  `dunning` (retry count, window, end behavior).
- The instance startup hook reads the row and builds `ctx.state["account"]` from it, in the same
  shape it builds today. Every reader of that state (id minting's account fragment, `stripe_context`
  validation, `list_available_accounts_or_orgs`, `get_stripe_account_info`, the ledger, dunning) is
  unchanged.
- `livemode` stays an instance option (the startup keyword and `--reset-options`), not a stored
  value. The same fixture can be opened live or in test mode.
- **The schema seeds today's sandbox account** as the row's initial value. A blank instance (no
  fixture, the HTTP server's default) and `empty` therefore both see exactly today's account: the
  same id, every field the same, in both modes. Every existing test passes unchanged.
- No tool writes the row. Stripe's MCP surface has no account update, and
  `manage_stripe_accounts` only answers with a reconsent URL. A harness can still change the account
  for one episode with `setup_sql`.
- A missing row, a second row, or a row whose id is not `acct_` plus 16 alphanumerics fails
  instance creation with a `WorldBug` that says which.
- The schema change rebuilds `empty` and bumps the world version from 0.1.0 to 0.2.0.
- In a composed world, the added world's account is in its own store, like the rest of its data.

### 2.2 What a company fixture's account says

Both companies are activated live accounts: `charges_enabled`, `payouts_enabled` and
`details_submitted` all true, `requirements` with nothing due and `disabled_reason` null,
`card_payments` and `transfers` capabilities active, `business_type: company`, country `US`, default
currency `usd`, one external bank account for payouts, and a payout schedule of `daily` with
`delay_days: 2`. Each has its own fixed `acct_` id. The exact field set follows `spec3.min.json`'s
`account` schema, and what the real API returns for an account's own `GET /v1/account`.

| Field | `small_co` | `big_co` |
|---|---|---|
| `business_profile.name` | Wrenfield | Ostrander FieldOps |
| `settings.dashboard.display_name` | Wrenfield Labs | Ostrander Systems |
| `business_profile.url` | `https://wrenfield.app` | `https://ostranderfieldops.com` |
| `business_profile.support_email` | `support@wrenfield.app` | `billing@ostranderfieldops.com` |
| `business_profile.support_phone` | null | `+17205550147` |
| `business_profile.support_address` | Asheville, NC 28801 | Denver, CO 80202 |
| `business_profile.mcc` | `5734` | `5734` |
| `email` | `hello@wrenfield.app` | `finance@ostranderfieldops.com` |
| `settings.dashboard.timezone` | `America/New_York` | `America/Denver` |
| `settings.payments.statement_descriptor` | `WRENFIELD` | `OSTRANDER FIELDOPS` |
| `settings.card_payments.statement_descriptor_prefix` | `WRENFIELD` | `OSTRANDER` |
| `created` / `tos_acceptance.date` | 2025-08-25 | 2025-02-10 |
| Dunning | 8 tries within 2 weeks, then `cancel` (Stripe's default) | 8 tries within 3 weeks, then `mark_unpaid` |
| Ledger | Stripe's standard US card pricing, 2-day settlement, $15 dispute fee | the same |

## 3. Rules for both fixtures

### 3.1 Build

- Built by `fixtures_src/generate.py`, frozen from a blank instance at `NOW` on a `fixed` clock with
  a fixed seed. Rebuilding produces byte-identical files. `python fixtures_src/generate.py` with no
  arguments rebuilds `empty`, `small_co` and `big_co` in that order.
- The builder writes the company's account row first, then reloads the account state, so every id
  minted afterwards carries the company's account fragment.
- History is computed from `NOW` (timestamps are `NOW` minus an offset); the clock never moves.
  Billing arithmetic comes from the world's own helpers (§1), never a second formula.
- Bulk history is written through `inst.bulk()`. What happens at `NOW` itself, and a short tail of
  ordinary operations, goes through the real tools, so the route table, validation and
  serialization are exercised.
- After a fixture is built, the real tools carry on from it correctly: a new invoice for an existing
  customer gets that customer's next invoice number, a new object's `x_seq` follows every existing
  one, a payout draws only the balance that is actually available, and search finds bulk-written
  objects.

### 3.2 Realism

- **A live account.** Card payment methods carry a realistic brand mix (roughly Visa 55%,
  Mastercard 25%, Amex 15%, Discover 5%), random `last4`, plausible expiry dates, funding and
  fingerprint. No Stripe test-card number (`4242`, `0077`, `4444`, …) appears anywhere. Bank
  accounts use real US bank names with random `last4`.
- **Fictional people and companies.** No real person and no real company. No sample-data clichés
  (Acme, Contoso, Northwind, Initech, Globex). Names are drawn for diversity. The two company names
  and their domains get a collision check before they are committed.
- **Contact details.** Email addresses use invented domains: a business customer at its own
  invented domain, an individual at an invented personal-site domain (`okaforpiano.com`). Never a
  real webmail domain and never `example.com`. Phone numbers, where present, use the reserved
  `555-0100`–`555-0199` range. Street addresses are invented, on real city, state and ZIP
  combinations.
- **Events cover the last 30 days.** Stripe's API lists events only from the last 30 days, so each
  fixture holds an event for every state change in `[NOW − 30 days, NOW]`, each with `data.object` as
  the object stood at that moment and `previous_attributes` on updates. Older history has no
  events.
- **Only what the world supports.** USD, a US account, card and `us_bank_account`, the 24 existing
  tables.

### 3.3 Declared gaps

These are known differences from a real account, accepted by decision rather than fixed:

- **ACH fees use card pricing.** The world has one fee schedule for every payment method, so
  `big_co`'s ACH payments carry 2.9% + 30¢ rather than ACH Direct Debit's 0.8% capped at $5.
- **Retries never fire.** The clock is frozen, so `next_payment_attempt` is an honest future
  timestamp that never arrives (the world's existing contract).
- **The `555-01xx` phone range** is reserved for fiction, and an attentive reader could notice it.
- **A test-mode instance** of either fixture reads as a live account's data.

### 3.4 Consistency

Both fixtures satisfy these at build time; the build fails on the first violation:

- Every balance transaction's `net` is `amount − fee`. `/v1/balance` equals the sum of the ledger.
  No payout ever drew more than was available at its own moment.
- No charge is refunded past its amount, across refunds and credit notes combined.
- Every invoice's lines sum to its subtotal, and its totals follow from discounts and tax.
- No two invoices on one subscription have overlapping periods.
- Causality: no object is older than what it depends on (a charge after its payment intent, a
  dispute after its charge, a balance transaction after what it settles).
- Each customer's `balance` equals the sum of their customer balance transactions, and
  `next_invoice_sequence` is one past their highest invoice number.
- Every subscription's status, `latest_invoice`, period and `cancel_at_period_end` agree with its
  invoices.
- Every timestamp is at or before `NOW`, except the fields that point forward by design
  (`current_period_end`, `trial_end`, `due_date`, `next_payment_attempt`,
  `automatically_finalizes_at`, `available_on`, `arrival_date`, `evidence_details.due_by`, a schedule's
  future phase dates, a coupon's `redeem_by`).

### 3.5 Budgets

| | `small_co` | `big_co` |
|---|---|---|
| File size | Under 2 MB | 25 MB or less |
| Build time | Seconds | Under 3 minutes |
| Fork cost | Measured and recorded | Measured and recorded; the test sets its ceiling from the measurement |

If `big_co` is over 25 MB with 30 days of events, the customer count comes down before anything
else. Plain git, no LFS.

### 3.6 The fixture guide

`docs/fixtures.md` describes every fixture for whoever writes evals against it: the company's
story, its account identity, its catalog, its counts, and every loose end from §4.6 and §5.7, each
with the query that finds it and the id it has in the committed build. For `small_co` the guide also
lists the whole cast. A test runs every query in the guide against the built fixtures and checks the
documented results, so the guide cannot drift from the data.

Each fixture's `description` in `fixture.yaml` is a short paragraph of the same kind: who the
company is, what the data holds, and what it is good for.

## 4. `small_co`: Wrenfield Labs

A two-person, bootstrapped company in Asheville, North Carolina. **Wrenfield** gives independent
tutors, music teachers and coaches a booking page, automatic reminders and a simple record of who has
paid for lessons. Every customer is written by hand in a cast file, so the account reads as a story
and every loose end is exactly as specified.

### 4.1 Timeline

| Date | What happened |
|---|---|
| 2025-08-25 | Stripe account opened for a private beta. |
| 2025-09-03 to 09-08 | Six founding members buy an annual plan at a founding price of $96/year. |
| 2026-03-02 | Public launch. The first live charge is a $1.00 test by the founder, refunded the same day. |
| 2026-03 | The `LAUNCH` coupon (50% off for 3 months) brings in the first wave of customers. |
| 2026-07-14 | A teaching newsletter features Wrenfield, with promotion code `TEACHWELL25`. |
| 2026-09-01 | `NOW`. The founding members' first annual renewals fall due over the next week. |

The beta is one change from the overview's "about six months of history". Without it no annual plan
could renew near `NOW`, and "which renewals are due this week?" is a loose end worth having. Almost
all activity still falls in the six months since launch.

### 4.2 Catalog

| Product | Price | Amount | Notes |
|---|---|---|---|
| Wrenfield Solo | Solo monthly | $12/month | `lookup_key: solo_monthly` |
| | Solo annual | $120/year | `lookup_key: solo_annual` |
| | Founding member | $96/year | Archived (`active: false`) after the beta; the founding members keep it |
| Wrenfield Studio | Studio monthly | $29/month | Up to five teachers; quantity 1 |
| | Studio annual | $290/year | |
| Done-for-you setup | Setup | $49 one-time | Added as an invoice item |

Coupons: `LAUNCH`, 50% off repeating for 3 months, `redeem_by` 2026-03-31, redeemed by the ten
earliest launch customers, whose discounts have all ended. `TEACHWELL25`, a promotion code for a
coupon of 25% off once, redeemed by about five customers since 2026-07-14.

Trials are 14 days on every plan. A trial without a card on file pauses at its end
(`trial_settings.end_behavior.missing_payment_method: pause`). Cards only, charged automatically. No
tax rates. Daily payouts.

### 4.3 The cast at `NOW`

About 45 customers. The composition is fixed:

| Group | Count | Who |
|---|---|---|
| Founding members, annual $96 | 6 | All active; one set to cancel at period end |
| Solo monthly, active | 11 | Including the downgrade credit and one half of the duplicate |
| Solo annual, active | 4 | Including the partial refund |
| Studio monthly, active | 5 | Including the pending setup fee |
| Studio annual, active | 3 | Including the dispute |
| Trialing | 3 | One ends in two days with no card |
| Past due | 1 | |
| Paused | 1 | |
| Canceled | 6 | Reasons spread across `too_expensive`, `switched_service`, `unused`, `other` |
| Duplicate record (canceled trial) | 1 | The other half of the duplicate |
| Incomplete, expired | 1 | |
| No subscription | 3 | The founder's test customer, and two abandoned checkouts |

Each cast entry gives a name, an email on an invented domain, what they teach, their plan, their
signup date, and the events of their history (trial, conversion, plan change, failed payment,
refund, dispute, cancellation). Individuals make up most of the cast; a few are small studios
("Brightside Music Studio").

### 4.4 Named characters

These are the people behind the loose ends. The remaining cast is background, written to the
composition above.

| Character | Story | State at `NOW` |
|---|---|---|
| **Test (do not bill)** | The founder's own record, created launch morning; $1.00 charge, refunded in full | No subscription |
| **Daniela Ruiz**, violin | Founding member | Renews 2026-09-03 at $96 |
| **Marcus Bell**, SAT tutor | Founding member | Renews 2026-09-04 |
| **Priya Natarajan**, piano | Founding member, retiring from teaching | `cancel_at_period_end`, ends 2026-09-05 |
| **Owen Fitzgerald**, running coach | Founding member; his card expired 08/2026 | Renews 2026-09-06; the renewal will fail |
| **Hana Kobayashi**, math tutor | Founding member | Renews 2026-09-07 |
| **Tomasz Wójcik**, guitar | Founding member | Renews 2026-09-08 |
| **Grace Adeyemi**, Brightside Music Studio | Studio annual, $290 paid 2026-08-20; her bank's customer did not recognize the charge | Dispute `needs_response`, reason `unrecognized`, evidence due 2026-09-07 |
| **Leo Marchetti**, chemistry tutor | Solo monthly; renewal of 2026-08-27 declined `expired_card`, the retry of 2026-08-29 too | `past_due`, `attempt_count` 2, next attempt per the dunning policy |
| **Sofia Lindqvist**, voice coach | Trial started 2026-08-20, no card | `trialing`, ends 2026-09-03, will pause |
| **Adaeze Okafor**, piano | Trial ended 2026-08-15 with no card | `paused` |
| **Nadia Haddad**, French tutor | Downgraded Studio to Solo monthly on 2026-08-12, invoiced at once | A credit balance, to be used by her next renewal |
| **Jamal Carter**, drum teacher | Bought Studio annual 2026-07-10, needed only Solo; moved to Solo annual and refunded $170 on 2026-07-17 | Solo annual, active; one partially refunded charge |
| **Elena Petrova**, dance studio | Studio monthly; ordered setup for a second location on 2026-08-28 | A pending $49 invoice item for her 2026-09-10 renewal |
| **Chris Nguyen**, tutoring | Signed up twice with two addresses; the second trial was canceled on day three | Two customer records, one active Solo monthly |
| **Victor Alves**, swim coach | First payment declined `insufficient_funds`; never completed | `incomplete_expired` |

### 4.5 Money

About 250 charges in all. Daily payouts on a 2-day rolling schedule whenever there is an available
balance; the most recent payout is `in_transit`, every earlier one `paid`. One dispute, two refunds
(the founder's test and Jamal's partial refund), one credit balance.

### 4.6 Loose ends at `NOW`

1. The open dispute, with evidence due in six days (Grace Adeyemi).
2. A `past_due` subscription mid-dunning (Leo Marchetti).
3. A trial that will pause in two days for lack of a card (Sofia Lindqvist), and one already
   `paused` (Adaeze Okafor).
4. Five founding renewals due within a week at $96, one of them on an expired card (Owen
   Fitzgerald), and one founding member cancelling at period end (Priya Natarajan).
5. A customer credit balance (Nadia Haddad).
6. A partially refunded charge (Jamal Carter).
7. A pending one-time invoice item (Elena Petrova).
8. A duplicate customer (Chris Nguyen), and the founder's test customer.

## 5. `big_co`: Ostrander Systems

A Series B company in Denver, Colorado, with about 150 employees. **Ostrander FieldOps** is
scheduling, dispatch, quoting and a technician mobile app for HVAC, plumbing and electrical
contractors. `big_co` is generated by a simulation over a population of customers, with the loose
ends of §5.7 scripted on top so they are guaranteed.

### 5.1 Timeline

| Date | What happened |
|---|---|
| 2025-02-10 | Stripe account opened. |
| 2025-03-03 to 03-07 | Migration week: about 1,450 existing customers move from the legacy billing system. |
| 2025-03 onward | Organic and sales-led growth, rising over the period. |
| 2026-04-14 | A six-hour outage. |
| 2026-04-20 to 04-24 | SLA credit notes go out to Business and Enterprise customers. |
| 2026-09-01 | `NOW`. |

Migrated customers carry `metadata.legacy_account_id` (`OS-` plus six digits) and
`metadata.customer_since` (a date between 2019 and 2025). Their subscriptions keep their old renewal
day: each was created in migration week with its `billing_cycle_anchor` on the next old renewal date
and no proration for the gap.

### 5.2 Catalog

Seat plans are priced per technician (subscription item quantity = technicians). Annual is 10×
monthly. Every add-on has a monthly and an annual price, because a subscription's items share one
interval.

| Product | Monthly | Annual | Notes |
|---|---|---|---|
| FieldOps Starter | $39/tech | $390/tech | 1–5 techs |
| FieldOps Pro | $69/tech | $690/tech | 3–15 techs |
| FieldOps Business | $99/tech | $990/tech | 10–60 techs |
| FieldOps Enterprise | — | Per deal | 40–400 techs; a price made per contract, $70–$95/tech/month equivalent, billed annually |
| Fleet Tracking | $12/vehicle | $120/vehicle | Pro and above |
| SMS Reminders | $29 flat | $290 flat | |
| Onboarding | $499, $1,499, $4,999 one-time | | Invoice items at signup: Pro (optional), Business, Enterprise |
| Legacy Starter, Legacy Pro | $29/tech, $59/tech | | Archived prices that part of the migration cohort still pays |

About 7 active products, 13 standard prices, 2 archived legacy prices, and about 100 per-deal
Enterprise prices.

### 5.3 Customers

About 3,000 ever, about 2,100 with an active subscription at `NOW`. Every customer is a business
with an invented name ("Rivera Heating & Air", "Summit Plumbing Co."), an invented domain, a billing
contact email, a US address weighted toward states with many trades contractors, and a phone number
on about 60% of records. About 20 are tax-exempt (`tax_exempt: exempt`): school districts and
municipal facilities departments.

Plan mix at signup: Starter 40%, Pro 35%, Business 20%, Enterprise 5%. Billing interval: about 75%
monthly (mostly Starter and Pro), 25% annual. About 30% of Pro-and-above customers take Fleet
Tracking (vehicles ≈ technicians), about 40% of all customers take SMS Reminders. About 30% of the
migration cohort is on a legacy price.

### 5.4 How they pay

- Monthly plans: card, charged automatically. New self-serve signups on Starter and Pro start a
  14-day trial with a card. Business trials set up by sales have no card, and pause if none is added.
- Annual Pro and Business: about 60% ACH debit (`us_bank_account`), the rest card, charged
  automatically.
- Enterprise: `send_invoice` with 30 days to pay. About 60% of those invoices are paid by ACH, about
  40% are marked paid outside Stripe (check or wire).
- Sales tax: manual tax rates for six states where this kind of SaaS is taxed (Texas 6.25%,
  Washington 6.5%, New York 4%, Pennsylvania 6%, Ohio 5.75%, Tennessee 7%), applied by the
  customer's state as the subscription's default tax rate; state rates only, no local rates.
  Exempt customers get none.

### 5.5 What happens over time

The simulation steps through the 547 days and applies these rates. The statuses at `NOW` come out
of the rates rather than being assigned.

| Behavior | Rate |
|---|---|
| Trial to paid | ~65% |
| Monthly churn | Starter 2.5%, Pro 1.5%, Business 1% per month |
| Annual non-renewal | ~15%; Enterprise ~8% |
| Seat change | ~6% of subscriptions per month, 70% up and 30% down, prorated onto the next invoice |
| Plan upgrade | ~1% per month; immediate downgrades rare, and credited to the customer balance |
| First renewal attempt declines | ~8% of card renewals, ~2% of ACH; most recover inside the dunning window, the rest end `unpaid` |
| Refunds | ~3% of charges, partial or full |
| Disputes | ~0.3% of card charges, at every stage by age: recent ones `needs_response` or `under_review`, older ones `won`, `lost`, `warning_closed` or `warning_needs_response` |
| `SWITCH25` | Promotion code for 25% off for 3 months; about 8% of organic signups |
| Trade-association discount | 10% off forever, through one promotion code per association (`MSPG10` for the Mountain States Plumbing Guild, `FRHA10` for the Front Range HVAC Alliance, `RMEC10` for the Rocky Mountain Electrical Contractors network); about 5% of customers |
| Sales concessions | A scattering of one-off amount-off coupons |
| Enterprise ramps | About 25 subscription schedules whose seats step up quarterly |
| Scheduled downgrades | About 15 schedules that move to a lower plan at renewal |
| Credit notes | The SLA batch: every Business and Enterprise customer active on 2026-04-14 gets a credit of 10% of their latest paid subscription invoice, to customer balance, memo "Service credit: April 14 outage". Plus routine corrections on ~0.5% of invoices |
| Payouts | Daily, 2-day rolling, about 380 over the period |

### 5.6 Real-account mess

- About 25 pairs of duplicate customers: the same company under a variant name ("Rivera Heating &
  Air" and "Rivera Heating and Air LLC") with different contact emails.
- About 4% of migrated customers have no email.
- Metadata keys are inconsistent. The migration cohort uses `legacy_account_id`; sales-led signups
  use `crm_account_id`, except where reps typed `CRM ID` or `crmAccountId`.
- A few customers carry a `description` note from the customer success team ("Pays by check —
  contact AP dept").

### 5.7 Loose ends at `NOW`

Scripted on top of the simulation, so each is present on every build:

1. A dispute queue: at least four disputes in `needs_response` with evidence due within seven days.
2. An Enterprise invoice 45 days past due, for a large amount: a 60-seat annual contract.
3. An Enterprise ramp schedule that steps to its next phase within three days.
4. A Business customer with about 48 technicians on an annual plan, set to cancel at period end
   twelve days from now: a churn risk.
5. All eight subscription statuses present. `incomplete` comes from a signup three hours before
   `NOW` whose first payment needs authentication. `paused` comes from a card-less sales-led trial.
   `unpaid` comes from dunning that ran out. Each status has at least three subscriptions, except
   `incomplete`, which has at least one.
6. At least five draft renewal invoices created in the last hour, waiting on
   `automatically_finalizes_at`.
7. The most recent payout `in_transit`.

### 5.8 Expected ranges

The build asserts these, so a change in the simulation that moves them is noticed:

| Measure | Range |
|---|---|
| Customers | 2,800 – 3,200 |
| Active subscriptions | 1,900 – 2,300 |
| Disputes | 60 – 110 |
| Payouts | 350 – 400 |
| File size | 25 MB or less |

## 6. Tests

- **Determinism.** One fresh build of each fixture into a temporary directory is byte-identical to
  the committed file. One build is enough: matching the committed bytes is the proof.
- **`empty` and blank are unchanged.** The account object from `get_stripe_account_info` and
  `GET /v1/account` is identical to today's in both modes, and every existing suite passes
  unchanged.
- **Account identity.** For each company, `get_stripe_account_info`, `GET /v1/account` and
  `list_available_accounts_or_orgs` return its account, and a newly minted id carries its account
  fragment.
- **Account row validation.** A missing row, two rows, or a malformed id each fail instance
  creation with a `WorldBug`.
- **Consistency.** The §3.4 checks pass on `empty`, `small_co` and `big_co`.
- **Realism guards.** No Stripe test-card number, no real webmail domain, no `example.com`, no
  sample-data cliché name, and no timestamp past `NOW` outside the forward-pointing fields.
- **The guide is true.** Every query in `docs/fixtures.md` returns its documented result.
- **The tools carry on.** On each company fixture, through the real tools: a new invoice for an
  existing customer gets the next number; a new object's `x_seq` follows every existing one; a
  payout draws only the available balance; search finds a bulk-written customer.
- **Budgets.** `big_co`'s size, build time and expected ranges (§3.5, §5.8); fork cost measured
  for both.
- **`seahaven check`** is clean, with no `SH4xx` findings.

## 7. Decisions taken

| Decision | Choice |
|---|---|
| Business models | Two different models |
| Where the account lives | A one-row table, read at startup; seeded with today's sandbox account |
| Mode depicted | Live |
| Email domains | Invented |
| Events | The last 30 days only (recommended default; not answered explicitly) |
| `big_co` size | 25 MB or less, in plain git (recommended default; not answered explicitly) |
| ACH fees | Left on card pricing; a declared gap |
| How `small_co` is made | A hand-written cast |
| `small_co` history | A private beta from 2025-08-25 added to the six months since launch (§4.1) |
| Phone numbers | The reserved `555-01xx` range |
| `stripe_world` specs | Not edited |

## 8. Out of scope

- Writing eval tasks.
- Any world change beyond §2, including per-payment-method fees.
- A company outside the US, or a currency other than USD.
- Events older than 30 days.
