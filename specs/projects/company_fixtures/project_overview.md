---
status: complete
---

# Company Fixtures: `small_co` and `big_co`

Two new committed Seahaven fixtures for this world, each the Stripe account of one fictional company
with a believable past, frozen at the world's fixture instant `NOW = 2026-09-01T14:00:00.000Z` (a
Tuesday). They take over the role of the deferred `small` and `large` fixtures
(`specs/projects/stripe_world/implementation_plan.md` Phase 19), with one change of intent: each is
a specific company rather than an anonymous shape. `empty` stays as it is.

**This project builds fixtures and nothing else.** The one world change is the account table the
fixtures need (below). Everything else is fixture generation code that uses the world as a client.

## Why

Evals and RL rollouts need starting states that look like a real business's Stripe account. An agent
asked "which customers are past due?" or "refund the duplicate charge for Maria" should be working
in an account with history, names, loose ends and a little mess, not in a blank sandbox. Two
companies give two kinds of eval:

- **`small_co`**: readable end to end by a person in one sitting. The default base fixture for eval
  tasks, because whoever reviews a failing run has to be able to read the whole account.
- **`big_co`**: scale. Pagination, search, aggregation ("what was net revenue last quarter?"),
  needle-in-a-haystack lookups, and realistic distributions of churn, dunning and disputes.

The two companies use Stripe differently on purpose: a small prosumer SaaS, and a B2B SaaS with
seats, annual contracts, invoices on terms and sales tax.

Both are **test-mode accounts**, built with Stripe's test cards. Declines, disputes and their
outcomes all come from the test cards and test evidence that Stripe documents, through the API.

## The two companies

### `small_co`: Wrenfield Labs

A two-person, bootstrapped software company in Asheville, North Carolina. Its one product,
**Wrenfield**, gives independent tutors, music teachers and coaches a booking page, automatic
reminders and a simple record of who has paid for lessons.

| | |
|---|---|
| On Stripe since | 2025-08-25, for a private beta with six founding members on an annual plan. Public launch 2026-03-02. Almost all activity is in the six months since launch. |
| Customers | About 45 ever, nearly all individuals (a piano teacher, an SAT tutor, a running coach) plus a few small studios. Everything is in one table that fits on a screen. |
| Catalog | Two plans, each monthly or annual: **Solo** ($12/mo, $120/yr) and **Studio**, up to five teachers ($29/mo, $290/yr). An archived founding-member price ($96/yr). One one-time product: **Done-for-you setup**, $49. |
| How they pay | Cards, charged automatically. A 14-day free trial; a trial that ends with no card on file pauses rather than cancels. |
| Discounts | A `LAUNCH` coupon (50% off for 3 months) used by the first launch customers, now past its `redeem_by`. A newsletter promotion code, `TEACHWELL25` (25% off the first month). |
| Tax | None collected. A company this size often doesn't collect sales tax yet, and the contrast with `big_co` is useful. |
| Money movement | Manual payouts, once a week; a handful of refunds; one dispute. |

Loose ends at `NOW`, each one a ready-made eval scenario:

- One dispute in `needs_response`, with evidence due in about six days.
- One `past_due` subscription on its second failed attempt.
- One trial ending in two days with no payment method, and one subscription already `paused` for the
  same reason.
- Five founding-member renewals due within a week, one on a card that will decline.
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
| How they pay | Self-serve plans by card, charged automatically. Enterprise is invoiced on net-30 terms (`send_invoice`); those invoices are paid by card or by check or wire, marked paid outside Stripe. |
| Seats over time | Seat counts change mid-cycle, which produces proration lines, upgrades, downgrades and customer-balance credits. Recent Enterprise deals carry ramp schedules whose seat counts step up quarter by quarter, and some customers have a downgrade scheduled for their next renewal. |
| Discounts | `SWITCH25` (25% off for 3 months, for customers moving from a competitor); a trade-association member discount (10% forever) issued through one promotion code per association; a scattering of one-off amount-off concessions from sales. |
| Tax | Manual `tax_rates` for the handful of states where this kind of SaaS is taxable, applied by the customer's state. Tax-exempt customers are marked as such. |
| Credit notes | Routine corrections, plus a batch of SLA credits issued to Business and Enterprise customers after an outage in April 2026. |
| Money movement | Manual payouts every business day. About 3% of charges partly or fully refunded. Disputes at about 0.3% of card charges, at the stages Stripe's test cards and test evidence reach: `needs_response`, `under_review`, `won`, `lost`, `warning_needs_response` and `warning_under_review`. |

Loose ends at `NOW`:

- A dispute queue with several evidence deadlines falling this week.
- An Enterprise invoice 45 days overdue for a large amount.
- A ramp schedule that steps up on its next phase within days.
- A large Business customer with cancel-at-period-end set: a churn risk.
- All eight subscription statuses present.
- Draft invoices waiting on `automatically_finalizes_at`, and payouts not yet arrived.
- Real-account mess: duplicate customer records for the same company under slightly different names,
  customers with no email, and metadata keys spelled inconsistently between the migration cohort and
  later signups.

### Side by side

| | `small_co` (Wrenfield Labs) | `big_co` (Ostrander Systems) |
|---|---|---|
| Customers | ~45, individuals | ~3,000, businesses |
| History | A small beta, then 6 months | 18 months, plus the migration cohort |
| Products / prices | 3 / 6 | ~7 / ~15, plus per-deal Enterprise prices |
| Payment methods | Test cards | Test cards, and invoices paid outside Stripe |
| Collection | Charge automatically | Charge automatically and send invoice (net 30) |
| Tax | None | Manual tax rates, some exempt customers |
| Subscription schedules | None | Recent Enterprise ramps, scheduled downgrades |
| Credit notes | None | Routine plus the SLA batch |
| Disputes | 1 | ~80, at six stages |
| Role | Default base for eval tasks; human-readable | Scale, search, analytics, distributions |

## Requirements and constraints

- **The account lives in the database; the only world change.** Today the account is a hard-coded
  sandbox constant in `startup.py`, and every minted id embeds a fragment of its id. A fixture
  carries rows and nothing else, so the account becomes a one-row table that the startup hook reads
  into `ctx.state`. The row also stores the account's **mode**, set when the account is created,
  because a fixture's data (its cards, and the `livemode` inside stored JSON) is coupled to the mode
  it was made in. Opening a fixture in the other mode is refused. Nothing else in the world changes:
  no new routes, no billing, resource or dispatcher changes. **This is the first phase of the plan.**
- **`empty` keeps working.** It holds today's sandbox account, created live. A blank instance (no
  fixture, the HTTP server's default) still opens in either mode. The README's `empty` example drops
  its test-mode option.
- **A real business identity.** Each company's account carries its name, URL, support email and
  phone, statement descriptor, charges and payouts enabled, details submitted, and a `created` date
  before its history.
- **Fictional, and not a cliché.** No real people and no real companies. No sample-data names such as
  Acme, Contoso, Northwind or Initech. Company and customer names get a collision check before they
  are committed. Email addresses use invented domains: each business customer at its own invented
  company domain, and each individual at an invented personal-site domain (a tutor's own
  `okaforpiano.com`). Never a real webmail domain such as `gmail.com`, because these fixtures are
  published, and never `example.com`.
- **Test mode, test cards.** Every card is one of Stripe's documented test cards that this world
  recognizes. Declines, disputes and dispute outcomes come from those cards and Stripe's test
  evidence strings.
- **Only what the world already does.** USD, a US account, cards, the existing tables. Where the
  world can't express something (automatic payouts, ACH bank names, a card network's dispute ruling,
  a schedule moving to its next phase), the fixtures do without it rather than work around it. No
  metered prices, Stripe Tax, Connect, Checkout Sessions or Payment Links.
- **Deterministic and committed.** Both fixtures are built by `fixtures_src/generate.py` from a fixed
  clock and the instance seed, and a rebuild produces byte-identical files. History is replayed
  through the world's own API and the engine functions the world provides for fixture generators,
  at historical instants, so every row is written by world code. Invariant checks run at the end of
  every build. This revises the design in `specs/projects/stripe_world/components/fixtures.md`, which
  planned to hand-write the rows.
- **Fast to fork.** `big_co` must fork in milliseconds. Events dominate its row count, so only the
  last 30 days of events are kept, and the build measures fork cost.
- **Coherent standalone and composed.** Each fixture is a whole account, whether used on its own or as
  the billing subsystem of a world that adds this one.
- **Documented for eval writers.** Each fixture's `description` says what it contains. A fixture guide
  lists every loose end above, with the query that finds it.

## Out of scope

- Any world change beyond the account table, including `GET /v1/account`.
- Writing the eval tasks themselves (`stripe_world` Phase 20).
- Editing the `stripe_world` specs. They are point-in-time records, and they still say `small` and
  `large`.
- New Stripe features or tables added for realism's sake.
- Any company outside the US, or any currency other than USD.
