---
status: complete
---

# Functional Spec: Company Fixtures

Two committed fixtures, `small_co` (Wrenfield Labs) and `big_co` (Ostrander Systems), each the
test-mode Stripe account of one fictional company, frozen at `NOW = 2026-09-01T14:00:00.000Z` (a
Tuesday). The overview (`project_overview.md`) is the brief; this document is the contract.

## 1. What ships

| Deliverable | Where | Kind |
|---|---|---|
| The account moves into the database | `schema/`, `startup.py` | **The only world change** (§2) |
| `small_co` builder and its hand-written cast | `fixtures_src/` | Generator code and data |
| `big_co` builder (simulation plus scripted loose ends) | `fixtures_src/` | Generator code |
| The two frozen fixtures, and `empty` rebuilt | `fixtures/` | Committed binaries |
| The fixture guide | `docs/fixtures.md` | Documentation |
| Tests for all of the above | `tests/` | Tests |
| Fixture lines and table counts in `AGENTS.md`, the README and `docs/technical.md`; the README's `empty` example | root, `docs/` | Documentation |

Nothing else in the world changes: no routes, no handlers, no billing, resource, dispatcher or
middleware code. The generator uses the world as a client (§3.1). The `stripe_world` specs are not
edited; they are point-in-time records and still say `small` and `large`.

## 2. The account in the database

Phase 1. It lands before any fixture is built.

### 2.1 Behavior

- The account is one row in a new `account` table. The row holds the account's id, its **mode**, the
  full account object (what `get_stripe_account_info` returns), and the account settings the world
  already reads from `ctx.state["account"]`: `ledger` (fees, settlement days, dispute fee) and
  `dunning` (retry count, window, end behavior).
- The instance startup hook reads the row and builds `ctx.state["account"]` from it, in the same
  shape it builds today. Every reader of that state (id minting's account fragment, `stripe_context`
  validation, `list_available_accounts_or_orgs`, `get_stripe_account_info`, serialization's
  `livemode`, the invoice's account fields, the ledger, dunning) is unchanged.
- **The mode is set when the account is created, and kept.** A fixture's data is coupled to the mode
  it was made in: its cards, and the `livemode` stored inside invoice lines, credit note lines and
  event snapshots.

  | Account row's mode | `livemode` passed at startup | Result |
  |---|---|---|
  | none yet (a blank instance) | omitted | live; the row is stamped live |
  | none yet | `true` / `false` | that mode; the row is stamped with it |
  | set (any fixture) | omitted | the row's mode |
  | set | the same | that mode |
  | set | the other | `WorldBug` at instance creation, naming the account's mode and the one asked for |

  The stamp is a startup write, so it is not in the change log, and freezing an instance saves it.
- **The schema seeds today's sandbox account**, with no mode yet. A blank instance (no fixture, the
  HTTP server's default) therefore sees exactly today's account, in whichever mode it is opened.
  `empty` is rebuilt from a blank instance opened with the default, so it is a live account: the same
  id and every field the same as today. Opening `empty` in test mode is refused; a blank instance is
  the same empty account and opens in either mode. The README's `empty` example drops its
  `startup={"livemode": False}`.
- No tool writes the row. A harness can still change the account for one episode with `setup_sql`.
- A missing row, a second row, a row whose id is not `acct_` plus 16 alphanumerics, or an object
  whose `id` differs from the row's, fails instance creation with a `WorldBug` that says which.
- The world version goes from 0.1.0 to 0.2.0. Existing tests pass, changed only where they
  enumerate tables (`test_empty_fixture.py`'s inventory gains `account`, with its one row).
- In a composed world, the added world's account is in its own store, like the rest of its data.

### 2.2 What a company fixture's account says

Both companies are activated accounts opened in test mode: `charges_enabled`, `payouts_enabled` and
`details_submitted` all true, `requirements` with nothing due and `disabled_reason` null,
`card_payments` and `transfers` capabilities active, `business_type: company`, country `US`, default
currency `usd`, one external bank account for payouts, and a **manual** payout schedule
(`interval: manual`, `delay_days: 2`), because this world's payouts are made by API call. Each has its
own fixed `acct_` id. The field set follows `spec3.min.json`'s `account` schema and the shape of
today's sandbox object.

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
| Mode | test | test |
| Dunning | 8 tries within 2 weeks, then `cancel` (Stripe's default) | 8 tries within 3 weeks, then `mark_unpaid` |
| Ledger | the world's defaults: 2.9% + 30¢, 2-day settlement, $15 dispute fee | the same |

## 3. Rules for both fixtures

### 3.1 Build

- Built by `fixtures_src/generate.py` from a blank instance opened in test mode at `NOW` on a `fixed`
  clock. Rebuilding produces byte-identical files. `python fixtures_src/generate.py` with no
  arguments rebuilds `empty`, `small_co` and `big_co` in that order.
- The builder writes the company's account row first, then reloads the account state, so every id
  minted afterwards carries the company's account fragment.
- **History is replayed, not hand-written.** Inside `inst.bulk()`, the builder walks a timeline from
  the company's first day to `NOW`. At each step it calls one of two things, with the context's
  clock reading that step's instant:
  - the world's API dispatcher (`dispatch`), the same function the MCP tools and the HTTP API call:
    customers, payment methods, subscriptions, invoices, refunds, disputes' evidence, credit notes,
    payouts, coupons, tax rates;
  - the engine functions the world already provides for transitions a frozen clock cannot fire
    (`stripe_world` `billing_engine.md` §7): `advance_cycle` (renewals, trial ends, cancel at period
    end), `expire_incomplete`, `dunning.record_failed_attempt` (which applies the end-of-schedule
    outcome itself) and `ledger.settle_payout` (a payout's arrival).

  Every row, id, invoice number, ledger entry and event is therefore written by world code, at the
  right time. The instance's own clock never moves.
- **The builder never edits a row the world wrote**, with two finishing steps as the only
  exceptions, both applying the world's own rules at `NOW`:
  - each balance transaction's stored `status` is refreshed with the ledger's `bt_status` rule, since
    the world stores it at write time (`pending`) and a frozen clock never revisits it;
  - events older than 30 days are deleted (§3.2).
- Where the world cannot express something, the fixtures go without it (§3.3).

### 3.2 Realism

- **Test cards only.** Every card is one of Stripe's documented test cards that this world
  recognizes (`billing/magic_cards.py`), created through the API. Good payments use the brand mix
  Visa, Visa debit, Mastercard, Mastercard debit, Amex and Discover (`4242424242424242`,
  `4000056655665556`, `5555555555554444`, `5200828282828210`, `378282246310005`,
  `6011111111111117`), weighted roughly 45/10/20/5/15/5. A card that attaches and then declines
  every charge is `4000000000000341`. Disputes come from the dispute cards (`4000000000000259`
  fraudulent, `4000000000002685` product not received, `4000000000001976` inquiry). Required
  authentication comes from `4000002760003184`. Expiry dates vary across plausible months and years.
- **Dispute outcomes** come from Stripe's test evidence: `uncategorized_text` of `winning_evidence`
  wins, `losing_evidence` loses, `escalate_inquiry_evidence` escalates an inquiry; any other evidence
  leaves the dispute under review. Accepting a dispute (`/close`) loses it.
- **Fictional people and companies.** No real person and no real company. No sample-data clichés
  (Acme, Contoso, Northwind, Initech, Globex, Hooli). Names are drawn for diversity. The two company
  names and their domains get a collision check before they are committed.
- **Contact details.** Email addresses use invented domains: a business customer at its own
  invented domain, an individual at an invented personal-site domain (`okaforpiano.com`). Never a
  real webmail domain and never `example.com`. Phone numbers, where present, use the reserved
  `555-0100`–`555-0199` range. Street addresses are invented, on real city, state and ZIP
  combinations.
- **Events cover the last 30 days.** Stripe's API lists events only from the last 30 days. The
  replay emits every event as it happens, and the finishing step deletes those older than
  `NOW − 30 days`.

### 3.3 Declared gaps

Known differences from a real account, accepted rather than worked around:

- **Payouts are manual.** The world creates payouts only by API call (`automatic: false`), so both
  accounts use a manual payout schedule.
- **No ACH.** The world's `us_bank_account` payment methods always name "STRIPE TEST BANK" and are
  priced as cards, so neither company uses ACH. Enterprise invoices are paid by card or outside
  Stripe.
- **Disputes open with their charge.** Stripe's dispute test cards open the dispute at the charge's
  instant, as they do in real test mode.
- **No `warning_closed` disputes.** No test evidence reaches it.
- **No promotion codes.** This world's subscriptions take a coupon, not a promotion code, so a
  promotion code could never show a redemption. Discount codes are coupons whose ids are the codes
  (`LAUNCH`, `TEACHWELL25`, `SWITCH25`, `MSPG10`, …).
- **Subscription schedules never advance.** The world has no phase-transition walker, so every
  schedule in the fixtures is still in its first phase at `NOW`: recent ramp deals, and downgrades
  set for the next renewal.
- **Renewals finalize and charge at the boundary.** A renewal's `finalized_at` equals its `created`,
  not an hour later.
- **Retries write no charge.** The world's dunning walker records a failed automatic retry on the
  invoice without a failed charge row.
- **Test mode only.** Opening either fixture live is refused (§2.1).

### 3.4 Consistency

Both fixtures satisfy these at build time; the build fails on the first violation:

- Every balance transaction's `net` is `amount − fee`, and its `status` agrees with `bt_status` at
  `NOW`. No payout ever drew more than was available at its own moment.
- No charge is refunded past its captured amount.
- Every invoice's lines sum to its subtotal.
- No two invoice lines share an id.
- No invoice carries a discount past its `end`: the instant the invoice bills (where its period
  closes) is before the end of every discount it applies.
- Every coupon's stored `valid` agrees with the world's rule at `NOW`, and no subscription stores a
  discount whose `end` has passed.
- No two cycle invoices on one subscription have overlapping periods.
- No subscription item is billed twice for one span: no two of its non-proration invoice lines
  with a non-zero amount, on invoices not voided, have overlapping periods. A $0 line, such as a
  trial's free line, bills nothing.
- Causality: no object is older than what it depends on (a charge after its payment intent, a
  dispute or refund after its charge, a balance transaction after what it settles).
- Each customer's `balance` equals the ending balance of their latest customer balance transaction
  (zero if none), and `next_invoice_sequence` is one past their highest invoice number.
- Every subscription's status agrees with its latest invoice: `past_due` and `unpaid` have an unpaid
  latest invoice, `trialing` has a `trial_end` after `NOW`, `canceled` and `incomplete_expired` have
  `ended_at`.
- Every timestamp is at or before `NOW`, except the fields that point forward by design
  (`current_period_end`, `trial_end`, `due_date`, `next_payment_attempt`,
  `automatically_finalizes_at`, `available_on`, `arrival_date`, `evidence_details.due_by`, a schedule's
  future phase dates, a coupon's `redeem_by`).
- No event is older than `NOW − 30 days`.

### 3.5 Budgets

| | `small_co` | `big_co` |
|---|---|---|
| File size | Under 2 MB | 25 MB or less |
| Build time | Seconds | Under 3 minutes |
| Fork cost | Measured and recorded | Measured and recorded; the test sets its ceiling from the measurement |

If `big_co` is over 25 MB, the customer count comes down. Plain git, no LFS. If it cannot build in
under 3 minutes, the population comes down or the user decides otherwise.

### 3.6 The fixture guide

`docs/fixtures.md` describes every fixture for whoever writes evals against it: the company's story,
its account identity, its catalog, its counts, how to open it (test mode), and every loose end from
§4.6 and §5.7, each with the query that finds it and the rows it returns in the committed build. For
`small_co` the guide also lists the whole cast. A test runs every query in the guide against the
committed fixtures and compares the documented rows, so the guide cannot drift from the data.

Each fixture's `description` in `fixture.yaml` is a short paragraph of the same kind: who the company
is, that it is a test-mode account, what the data holds, and what it is good for.

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
| 2026-03-02 | Public launch. The first charge is a $1.00 test by the founder, refunded the same day. |
| 2026-03 | The `LAUNCH` coupon (50% off for 3 months) brings in the first wave of customers. |
| 2026-07-14 | A teaching newsletter features Wrenfield, with the code `TEACHWELL25`. |
| 2026-09-01 | `NOW`. The founding members' first annual renewals fall due over the next week. |

### 4.2 Catalog

| Product | Price | Amount | Notes |
|---|---|---|---|
| Wrenfield Solo | Solo monthly | $12/month | `lookup_key: solo_monthly` |
| | Solo annual | $120/year | `lookup_key: solo_annual` |
| | Founding member | $96/year | Archived (`active: false`) at launch; the founding members keep it |
| Wrenfield Studio | Studio monthly | $29/month | `lookup_key: studio_monthly`; up to five teachers, quantity 1 |
| | Studio annual | $290/year | `lookup_key: studio_annual` |
| Done-for-you setup | Setup | $49 one-time | Added as an invoice item |

Coupons: `LAUNCH`, 50% off repeating for 3 months, `redeem_by` 2026-03-31, redeemed by the ten
earliest launch customers, whose discounts have all ended. `TEACHWELL25`, 25% off once, redeemed
by about five customers since 2026-07-14. Both are coupons whose ids are the codes (§3.3).

Trials are 14 days on every plan. A trial without a card on file pauses at its end
(`trial_settings.end_behavior.missing_payment_method: pause`). Cards, charged automatically. No tax
rates. A manual payout every Monday of whatever balance is available.

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

Each cast entry gives a stable key, a name, an email on an invented domain, what they teach, their
plan, their signup instant, their card, and the beats of their history (trial, conversion, plan
change, card change, refund, dispute, cancellation). Individuals make up most of the cast; a few are
small studios ("Brightside Music Studio").

### 4.4 Named characters

These are the people behind the loose ends. The remaining cast is background, written to the
composition above.

| Character | Story | State at `NOW` |
|---|---|---|
| **Test (do not bill)** | The founder's own record, created launch morning; a $1.00 payment with `4242`, refunded in full | No subscription |
| **Daniela Ruiz**, violin | Founding member | Renews 2026-09-03 at $96 |
| **Marcus Bell**, SAT tutor | Founding member | Renews 2026-09-04 |
| **Priya Natarajan**, piano | Founding member, retiring from teaching | `cancel_at_period_end`, ends 2026-09-05 |
| **Owen Fitzgerald**, running coach | Founding member; replaced his card on 2026-08-18 with one that declines (`…0341`) | Renews 2026-09-06; the renewal will decline |
| **Hana Kobayashi**, math tutor | Founding member | Renews 2026-09-07 |
| **Tomasz Wójcik**, guitar | Founding member | Renews 2026-09-08 |
| **Grace Adeyemi**, Brightside Music Studio | Bought Studio annual on 2026-08-30 with a dispute test card (`…0259`) | Dispute `needs_response`, reason `fraudulent`, evidence due end of day 2026-09-07 UTC |
| **Leo Marchetti**, chemistry tutor | Solo monthly since 2026-04-30; replaced his card on 2026-08-20 with `…0341`; the renewal of 2026-08-30 declined and one automatic retry failed | `past_due`, `attempt_count` 2, next attempt after `NOW` |
| **Sofia Lindqvist**, voice coach | Trial started 2026-08-20, no card | `trialing`, ends 2026-09-03, will pause |
| **Adaeze Okafor**, piano | Trial started 2026-08-01, ended 2026-08-15 with no card | `paused` |
| **Nadia Haddad**, French tutor | Studio monthly since 2026-05-12; downgraded to Solo monthly on 2026-08-20, invoiced at once | A credit balance, to be used by her next renewal |
| **Jamal Carter**, drum teacher | Bought Studio annual 2026-07-10, needed only Solo; moved to Solo annual and refunded $170 on 2026-07-17 | Solo annual, active; one partially refunded charge |
| **Elena Petrova**, dance studio | Studio monthly since 2026-04-10; ordered setup for a second location on 2026-08-28 | A pending $49 invoice item for her 2026-09-10 renewal |
| **Chris Nguyen**, tutoring | Signed up twice with two addresses; the second trial was canceled on day three | Two customer records, one active Solo monthly |
| **Victor Alves**, swim coach | Signed up 2026-06-22 with `…0341`; the first payment declined and was never completed | `incomplete_expired` |

### 4.5 Money

About 100 charges in all. A manual payout each Monday of the available balance; payouts whose
arrival date is still ahead of `NOW` are `pending`, every earlier one `paid`. One dispute, two refunds
(the founder's test and Jamal's partial refund), one credit balance.

### 4.6 Loose ends at `NOW`

1. The open dispute, with evidence due in six days (Grace Adeyemi).
2. A `past_due` subscription mid-dunning (Leo Marchetti).
3. A trial that will pause in two days for lack of a card (Sofia Lindqvist), and one already
   `paused` (Adaeze Okafor).
4. Five founding renewals due within a week at $96, one on a card that will decline (Owen
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
day: each was created in migration week with `trial_end` set to the next old renewal date, the
method Stripe's migration guidance gives for carrying a billing date over, so nothing is billed
twice for the period the legacy system already charged.

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
| Onboarding | $499, $1,499, $4,999 one-time | | Invoiced at signup: Pro (optional), Business, Enterprise |
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

- Self-serve plans, monthly and annual: card, charged automatically. New self-serve signups on
  Starter and Pro start a 14-day trial with a card. Business trials set up by sales have no card, and
  pause if none is added.
- Enterprise: `send_invoice` with 30 days to pay. About 60% of those invoices are paid by card on the
  invoice, about 40% are marked paid outside Stripe (check or wire).
- Sales tax: manual tax rates for six states where this kind of SaaS is taxed (Texas 6.25%,
  Washington 6.5%, New York 4%, Pennsylvania 6%, Ohio 5.75%, Tennessee 7%), applied by the
  customer's state as the subscription's default tax rate; state rates only, no local rates.
  Exempt customers get none.

### 5.5 What happens over time

The simulation draws each customer's life from these rates. The statuses at `NOW` come out of the
rates rather than being assigned.

| Behavior | Rate | How it is made |
|---|---|---|
| Trial to paid | ~65% | Unconverted trials are canceled before their end |
| Monthly churn | Starter 2.5%, Pro 1.5%, Business 1% per month | Cancel at period end, or cancel at once with a reason |
| Annual non-renewal | ~15%; Enterprise ~8% | Cancel at period end |
| Seat change | ~6% of subscriptions per month, 70% up and 30% down | Item quantity update, prorated onto the next invoice |
| Plan upgrade | ~1% per month | Price change; rare immediate downgrades are invoiced at once and credit the balance |
| A renewal declines | ~8% of card renewals | The customer's card is swapped for `…0341` beforehand; most customers swap in a good card and the invoice is paid inside the dunning window; the rest end `unpaid` |
| Refunds | ~3% of charges, partial or full | Refund through the API |
| Disputes | ~0.3% of card charges | The customer's card is swapped for a dispute card for one renewal, then back. By age at `NOW`: within 21 days left in `needs_response` (or `warning_needs_response` for inquiries), some with plain evidence submitted (`under_review`, `warning_under_review`); older ones settled with test evidence, about 45% won and 55% lost (accepted or losing evidence) |
| `SWITCH25` | ~8% of organic signups | Coupon for 25% off for 3 months |
| Trade-association discount | ~5% of customers | 10% off forever, through one coupon per association (`MSPG10` for the Mountain States Plumbing Guild, `FRHA10` for the Front Range HVAC Alliance, `RMEC10` for the Rocky Mountain Electrical Contractors network) |
| Sales concessions | a scattering | One-off amount-off coupons |
| Enterprise ramps | the Enterprise deals signed in the last 90 days | Subscription schedules with quarterly seat steps, still in their first phase |
| Scheduled downgrades | ~10 | Schedules created during the current period that move to a lower plan at renewal |
| Credit notes | The SLA batch, plus ~0.5% of invoices | SLA: every Business and Enterprise customer active on 2026-04-14 gets a credit of 10% of their latest paid subscription invoice, to customer balance, memo "Service credit: April 14 outage" |
| Payouts | every business day | A manual payout of the available balance |

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
6. At least five draft invoices created in the last hour, waiting on `automatically_finalizes_at`:
   onboarding packages that sales invoiced this morning.
7. The most recent payouts not yet arrived (`pending`).

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
  the committed file.
- **`empty` and blank.** A blank instance serves today's account object in either mode, and stamps
  its mode. `empty` opens live by default and refuses test mode. The existing suites pass with only
  the inventory change §2.1 names.
- **The account row.** Each row of the mode table in §2.1, through real instances. A missing row,
  two rows, a malformed id and a mismatched object id each fail instance creation with a
  `WorldBug`. Settings in the row reach the ledger and dunning. A test-mode instance frozen and
  reopened comes back in test mode.
- **Account identity.** For each company, `get_stripe_account_info` and
  `list_available_accounts_or_orgs` return its account, in test mode, and a newly minted id carries
  its account fragment.
- **Consistency.** The §3.4 checks pass on `empty`, `small_co` and `big_co`.
- **Realism guards.** Every card is a known test card; no real webmail domain, no `example.com`, no
  sample-data cliché name.
- **The guide is true.** Every query in `docs/fixtures.md` returns its documented rows.
- **The tools carry on.** On each company fixture, through the real MCP tools: a new invoice for an
  existing customer gets that customer's next number; a new object's `x_seq` follows every existing
  one; a payout draws only the available balance; search finds a customer the build created.
- **Budgets.** `big_co`'s size and expected ranges (§3.5, §5.8); fork cost measured for both.
- **`seahaven check`** is clean, with no `SH4xx` findings.

## 7. Decisions taken

| Decision | Choice |
|---|---|
| Business models | Two different models |
| World changes | The account table only; no `GET /v1/account` |
| Where the account lives | A one-row table, read at startup, holding the mode the account was created in |
| Mode | Test, with Stripe's test cards; a fixture refuses the other mode |
| `empty` | A live account; the README example drops its test-mode option |
| Email domains | Invented |
| Events | The last 30 days only |
| `big_co` size | 25 MB or less, in plain git |
| Where the world can't express something | Go without it (§3.3) |
| How `small_co` is made | A hand-written cast |
| `small_co` history | A private beta from 2025-08-25, then six months since launch |
| Phone numbers | The reserved `555-01xx` range |
| `stripe_world` specs | Not edited |

## 8. Out of scope

- Writing eval tasks.
- Any world change beyond §2.
- A company outside the US, or a currency other than USD.
- Events older than 30 days.
