---
status: draft
---

# Company Fixtures: `small_co` and `big_co`

Two new committed Seahaven fixtures for this world, each the Stripe account of one fictional company
with a believable past, frozen at the world's fixture instant `NOW = 2026-09-01T14:00:00.000Z` (a
Tuesday). They take over the role of the deferred `small` and `large` fixtures
(`specs/projects/stripe_world/implementation_plan.md` Phase 19), with one change of intent: each is
a specific company rather than an anonymous shape. `empty` stays as it is.

## Why

Evals and RL rollouts need starting states that look like a real business's Stripe account. An agent
asked "which customers are past due?" or "refund the duplicate charge for Maria" should be working
in an account with history, names, loose ends and a little mess, not in a blank sandbox or in rows
that are obviously generated. Two companies give two kinds of eval:

- **`small_co`**: readable end to end by a person in one sitting. The default base fixture for eval
  tasks, because whoever reviews a failing run has to be able to read the whole account.
- **`big_co`**: scale. Pagination, search, aggregation ("what was net revenue last quarter?"),
  needle-in-a-haystack lookups, and realistic distributions of churn, dunning and disputes.

The two companies use Stripe differently on purpose: a small prosumer SaaS paid by card, and a B2B
SaaS with seats, annual contracts, ACH, invoices on terms and sales tax. Between them they cover far
more of the world's 155 operations than one business model at two sizes would.

## The two companies

### `small_co`: Wrenfield Labs

A two-person, bootstrapped software company in Asheville, North Carolina. Its one product,
**Wrenfield**, gives independent tutors, music teachers and coaches a booking page, automatic
reminders and a simple record of who has paid for lessons.

| | |
|---|---|
| On Stripe since | 2026-03-02, launch day. About six months of history (183 days). |
| Customers | About 45 ever, nearly all individuals (a piano teacher, an SAT tutor, a running coach) plus a few small studios. Everything is in one table that fits on a screen. |
| Catalog | Two plans, each monthly or annual: **Solo** ($12/mo, $120/yr) and **Studio**, up to five teachers ($29/mo, $290/yr). One one-time product: **Done-for-you setup**, $49. |
| How they pay | Cards only, charged automatically. A 14-day free trial; a trial that ends with no card on file pauses rather than cancels. |
| Discounts | A `LAUNCH` coupon (50% off for 3 months) used by the founding customers, now past its `redeem_by`. A newsletter promotion code, `TEACHWELL25` (25% off the first month). |
| Tax | None collected. A company this size often doesn't collect sales tax yet, and the contrast with `big_co` is useful. |
| Money movement | Daily automatic payouts on a 2-day rolling schedule; a handful of refunds; one dispute. |

Loose ends at `NOW`, each one a ready-made eval scenario:

- One dispute in `needs_response` (reason `unrecognized`, on a Studio annual charge), with evidence
  due in about six days.
- One `past_due` subscription on its second retry after an expired card, with the next attempt in
  three days.
- One trial ending in two days with no payment method, and one subscription already `paused` for the
  same reason.
- One annual renewal due within a week.
- One subscription set to cancel at period end.
- A partially refunded charge, and a customer carrying a credit balance from a downgrade.
- A pending one-time setup invoice item that will go on the customer's next invoice.
- Leftovers a real founder leaves behind: a "Test (do not bill)" customer from launch day, and one
  customer who signed up twice with two email addresses.

### `big_co`: Ostrander Systems, Inc.

A Series B vertical SaaS company in Denver, Colorado, with about 150 employees. Its product,
**Ostrander FieldOps**, is scheduling, dispatch, quoting and a technician mobile app for trades
contractors: HVAC, plumbing and electrical businesses.

| | |
|---|---|
| On Stripe since | 2025-03-03, when Ostrander moved billing to Stripe from a legacy system. About eighteen months of history (547 days). Roughly half the customers arrived in that migration week, carrying `metadata.legacy_account_id` and `metadata.customer_since`; the rest signed up afterwards. |
| Customers | About 3,000 ever and about 2,100 active at `NOW`. All are businesses ("Rivera Heating & Air", "Summit Plumbing Co."), with a billing contact, a US address and a phone number. A few are tax-exempt (school districts, municipal facilities departments). |
| Catalog | Per-technician seat plans, each monthly or annual (annual = 10 × monthly): **Starter** $39, **Pro** $69 and **Business** $99 per tech per month. **Enterprise** is a negotiated annual contract with a price created per deal. Add-ons: **Fleet Tracking** ($12 per vehicle per month) and **SMS Reminders** ($29 per month, flat). One-time onboarding packages at $499, $1,499 and $4,999. |
| How they pay | Self-serve monthly plans by card, charged automatically. Annual Pro and Business plans are mostly paid by ACH debit (`us_bank_account`). Enterprise is invoiced on net-30 terms (`send_invoice`), and some of those invoices are paid by check or wire, so they are marked paid outside Stripe. |
| Seats over time | Seat counts change mid-cycle, which produces proration lines, upgrades, downgrades and customer-balance credits. Enterprise ramp deals are subscription schedules whose seat counts step up quarter by quarter. |
| Discounts | `SWITCH25` (25% off for 3 months, for customers moving from a competitor); a trade-association member discount (10% forever) issued through one promotion code per association; a scattering of one-off amount-off concessions from sales. |
| Tax | Manual `tax_rates` for the handful of states where this kind of SaaS is taxable, applied by the customer's state. Tax-exempt customers are marked as such. |
| Credit notes | Routine corrections, plus a batch of SLA credits issued to Business and Enterprise customers after an outage in April 2026. |
| Money movement | Daily automatic payouts on a 2-day rolling schedule. About 3% of charges partly or fully refunded. Disputes at about 0.3% of card charges, at every stage: `needs_response`, `under_review`, `won`, `lost` and the warning states. |

Loose ends at `NOW`:

- A dispute queue with several evidence deadlines falling this week.
- An Enterprise invoice 45 days overdue for a large amount.
- A ramp schedule that steps up on its next phase within days.
- A large Business customer with cancel-at-period-end set: a churn risk.
- All eight subscription statuses present, arising from the simulation rather than planted as single
  rows.
- Draft invoices waiting on `automatically_finalizes_at`, and one payout still in transit.
- Real-account mess: duplicate customer records for the same company under slightly different names,
  customers with no email, and metadata keys spelled inconsistently between the migration cohort and
  later signups.

### Side by side

| | `small_co` (Wrenfield Labs) | `big_co` (Ostrander Systems) |
|---|---|---|
| Customers | ~45, individuals | ~3,000, businesses |
| History | 6 months | 18 months, plus the migration cohort |
| Products / prices | 3 / 5 | ~7 / ~15, plus per-deal Enterprise prices |
| Payment methods | Card | Card, ACH debit, paid outside Stripe |
| Collection | Charge automatically | Charge automatically and send invoice (net 30) |
| Tax | None | Manual tax rates, some exempt customers |
| Subscription schedules | None | Enterprise ramps, scheduled downgrades |
| Credit notes | None or one | Routine plus the SLA batch |
| Disputes | 1 | ~80, at every stage |
| Role | Default base for eval tasks; human-readable | Scale, search, analytics, distributions |

## Requirements and constraints

- **No tells.** The account has to read like a real, operating business. Today the account object is
  a hard-coded fresh-sandbox constant in `startup.py`: charges and payouts disabled, an empty
  `business_profile`, and the same `acct_` id in every instance. Every minted id embeds a fragment of
  that account id. Each company therefore needs its own account identity carried in its fixture:
  business name, URL, support email and phone, statement descriptor, charges and payouts enabled,
  details submitted, a `created` date before its history, and a payout schedule of daily with a
  2-day delay. `empty` keeps today's sandbox account. How the identity is stored (most likely a small
  account table, which changes the schema and so rebuilds `empty`) is an architecture decision.
- **Fictional, and not a cliché.** No real people and no real companies. No sample-data names such as
  Acme, Contoso, Northwind or Initech, which are tells in their own right. Company and customer names
  get a collision check before they are committed. Email addresses use invented domains: each
  business customer at its own invented company domain, and each individual at an invented
  personal-site domain (a tutor's own `okaforpiano.com`). Never a real webmail domain such as
  `gmail.com`, because these fixtures are published, and never `example.com`, which is a tell.
- **A live account.** The world defaults to live mode and rows carry no `livemode` column, so the
  fixtures depict live accounts: realistic card brands and `last4` values, not `4242`. An instance
  started in test mode still works, but will read as a live account's data.
- **Only what the world supports.** USD and a US account. Card and `us_bank_account` payment methods.
  No metered prices (they need Billing Meters, which this world doesn't have, and the pinned API
  version refuses them). No Stripe Tax, Connect, Checkout Sessions or Payment Links objects. The
  stories above are written to fit inside the 24 existing tables. A gap found while building is
  reported, not filled with new world features.
- **Deterministic and committed.** Both fixtures are built by `fixtures_src/generate.py` from a fixed
  clock and the instance seed, and a rebuild produces byte-identical files. History is computed from
  `NOW` rather than by moving the clock. Billing math (period boundaries, proration, fees, ledger
  `available_on`) is reused from `billing/`, never re-derived. A deliberate tail goes through the
  real tools, and invariant checks run at the end of every build. This is the design already written
  in `specs/projects/stripe_world/components/fixtures.md`, and it is revised here rather than
  restarted.
- **Fast to fork.** `big_co` must fork in milliseconds. Events dominate its row count, so the build
  measures fork cost and trims backfilled events first if it is over budget.
- **Coherent standalone and composed.** Each fixture is a whole account, whether used on its own or as
  the billing subsystem of a world that adds this one.
- **Documented for eval writers.** Each fixture's `description` says what it contains. A short
  fixture guide lists every loose end above, with how to find it by query rather than by an id that
  changes on regeneration.

## Out of scope

- Writing the eval tasks themselves (`stripe_world` Phase 20).
- Editing the `stripe_world` specs. They are point-in-time records, and they still say `small` and
  `large`.
- New Stripe features or tables added for realism's sake.
- Any company outside the US, or any currency other than USD.
