---
status: draft
---

# Component: Fixture Generation

`fixtures_src/generate.py`. One function per fixture, committed, following ProjectTracker's shape
exactly: a `Workspace`-style dataclass of population knobs, a simulation that writes rows through
`inst.bulk()`, a deliberate tail written through the real tools, and an end-of-build assertion pass
that doubles as the invariant SQL evals will grade on.

## Purpose and Scope

This component turns a schema and a set of population numbers into three committed SQLite files:
`empty`, `small`, `large`. Its job is to make each one look like an account with a real past — a
subscription mid-cycle, an invoice on its third dunning attempt, a dispute two weeks from resolution,
a balance ledger with money at several stages of settling — frozen at one instant, so that every call
an agent later makes against the fixture still sees a single `now` (functional spec §5).

**In scope:** the virtual-time model the simulation runs on; the population numbers for `small` and
`large` and the reasoning behind them; which rows are written through `inst.bulk()` and which through
`stripe_api_write`/`stripe_api_read`; the exact `seahaven fixture` commands; the invariant checks run
at the end of every build; and the fork-cost budget those population numbers have to respect.

**Out of scope:** the billing math itself (period boundaries, proration, the dunning envelope, ledger
`net` and `available_on` — all `billing/`, covered by `components/billing_engine.md`) and the table
DDL and enum sources (`components/data_model.md`). This component is a **consumer** of both: it calls
their pure functions to compute correct rows rather than re-deriving billing arithmetic a second time
for fixture purposes. Where that dependency isn't fully specified yet, it's called out under
Dependencies rather than guessed at here.

## Public Interface

```python
# fixtures_src/generate.py

NOW: str  # "2026-09-01T14:00:00.000Z" — the one instant every fixture is frozen at

def empty(inst: seahaven.Instance) -> None:
    """`empty`: schema only. Deliberately does no work."""

def small(inst: seahaven.Instance) -> None:
    """`small`: a new SaaS account, tens of customers, readable end to end."""

def large(inst: seahaven.Instance) -> None:
    """`large`: a scaled account — churn, dunning, disputes, payout history."""

BUILDERS: dict[str, Callable[[seahaven.Instance], None]]  # {"empty": empty, "small": small, "large": large}

def build(fixture_id: str, *, world: seahaven.World | None = None) -> seahaven.Fixture:
    """Fork `fixture_id` from `empty` (or freeze `empty` itself from blank) at NOW.

    Raises whatever `inst.freeze`/`inst.fork_into` raises: `WorldBug` if the directory
    already exists (freeze/fork never overwrite), `DbError` on a write failure inside
    the builder. Never leaves a partial fixture directory behind — freeze publishes by
    rename, per the framework contract.
    """

def main(argv: list[str], *, world: seahaven.World | None = None) -> int:
    """`python generate.py [fixture-id ...]`, or every fixture with no arguments."""

# Exposed so evals and tests reuse the same checks the generator runs on itself:
INVARIANTS: tuple[tuple[str, str], ...]  # (description, SQL) pairs, see "Invariant checks" below

def assert_fixture_invariants(inst: seahaven.Instance, span_days: int) -> None:
    """Runs INVARIANTS plus the timestamp-span check. Raises AssertionError with the
    failing description and offending row count on the first violation.
    """
```

`AccountShape`, the population dataclass (`small`'s and `large`'s only difference is which
`AccountShape` they read), is described under Population model below rather than repeated here.

## Internal Design Approach

### The central problem

One instance has one `ctx.clock`, fixed for its whole life, with no setter (confirmed against
`clock.py`: it registers `SQLITE_DETERMINISTIC` overrides for one instant and exposes nothing else).
A believable Stripe account is not one instant — it's two years of renewals, a customer who churned
in month four, an invoice that failed twice and paid on the third attempt eleven days later. The only
way to get that shape into a world with one clock is to **compute** the history in Python, stamping
every row with an explicit timestamp *derived from* `ctx.clock.now()`, and never advance the clock
itself. The simulation is a loop over virtual days-before-`now`; the instance's actual clock reading
never moves while that loop runs.

### Timeline model

Virtual time is `days_before_now: float`, exactly ProjectTracker's convention, rendered to a stamp by:

```python
def _stamp(ctx: seahaven.Ctx, days_before: float) -> str:
    return _shift(ctx.clock, days_before)

def _shift(clock: seahaven.Clock, days: float) -> str:
    return seahaven.Clock(clock.now() - timedelta(days=days)).iso()
```

Every historical timestamp in this fixture — `customer.created`, `subscription.start_date`,
`invoice.created`, `charge.created`, a dunning attempt's instant, `dispute.created`,
`balance_transaction.created`, `payout.arrival_date` — is `_shift(ctx.clock, some_days)`. There is
exactly one read of the wall-adjacent clock in the whole build: `ctx.clock.now()`, read fresh each
time `_shift` is called, but always returning the same frozen instant. "Past" is arithmetic on that
one instant, never a second clock or a mutated one.

Two kinds of timestamp are **not** simple subtraction and need calendar-correct arithmetic, not
`timedelta(days=30)`: a monthly subscription's `current_period_end` from a given
`billing_cycle_anchor` (day-of-month semantics — an anchor on the 31st lands on the 28th/29th in
February, exactly as Stripe's real engine computes it) and an annual one's period boundary. The
generator does **not** reimplement this. It imports and calls `billing/invoicing.py`'s period-boundary
function and `billing/proration.py`'s `proration_lines(...)` directly — both are described in the
architecture (§8) as pure functions over rows plus `ctx`, callable without going through a tool call.
Calling them from inside `inst.bulk()` for a *historical* period is the reason they need to accept an
explicit period-anchor argument rather than only ever reading "now" as the period's reference instant
— see Dependencies. Reusing them here is deliberate: if the fixture computed invoice totals or period
boundaries with a second, hand-written formula, a fixture and the live engine could quietly disagree
about the same arithmetic, and nothing would catch it until an eval graded a fixture-derived invoice
against a live-computed one and got two different answers to the same math.

A small number of fields legitimately point *forward* of the instant they're stamped at, and are the
one place `_shift` is called with a negative offset or a value smaller than the row's own age:
`invoice.automatically_finalizes_at` on a still-draft invoice, `subscription.current_period_end` on a
still-active subscription, `balance_transaction.available_on` on a charge from the last two days
(T+2 rolling — see Population model), and `invoice.due_date` on an unpaid invoice. Everything else is
asserted to fall inside `[now - span_days, now]`, checked the same way ProjectTracker's
`_assert_within_span` checks it — see Invariant checks.

**What "every call still sees one `now`" means in practice.** The bulk-written history and the
through-the-tools tail read `ctx.clock` identically — both get the same frozen `.now()`/`.iso()`. The
difference is never in the clock; it's in what the generator does with it. A bulk-written row's
timestamp is `now` minus a chosen offset, computed in Python before the `INSERT`. A through-the-tools
row's timestamp is whatever the dispatcher itself writes when the call runs, which is `now` with no
offset, because those calls represent things happening *at* the fixture's `now`, not in its past. Both
paths are reading the same one instant; only the arithmetic on top of it differs.

### Population model

`AccountShape`, mirroring `Workspace`:

```python
@dataclass(frozen=True)
class AccountShape:
    customers: int
    span_days: int
    products: tuple[str, ...]
    prices: tuple[PriceSpec, ...]       # tier, interval, unit_amount, metered: bool
    plan_weights: dict[str, float]      # tier -> share of converted subscribers
    interval_weights: dict[str, float]  # "month" | "year" -> share
    trial_share: float                  # of signups that start on a trial
    trial_conversion: float             # of trials that convert to paying
    monthly_churn_hazard: float         # rolled at every monthly renewal
    annual_churn_hazard: float          # rolled at every annual renewal
    dunning_entry_rate: float           # share of renewal invoices that decline on first attempt
    dispute_rate: float                 # share of successful charges that get disputed
    refund_rate: float                  # share of successful charges partially refunded
    payout_interval_days: int           # weekly, by default
```

**`small`** — "tens of customers... readable end to end by a human" (functional spec §9):

| Knob | Value |
|---|---|
| customers | 40 |
| span_days | 90 |
| products / prices | 2 products, 3 prices (Core monthly, Plus monthly, Plus annual) |
| plan_weights | Core 70%, Plus 30% |
| trial_share / conversion | 20% / 75% |
| churn hazard (monthly / annual) | 4% / 20% |
| dunning_entry_rate | 8% |
| dispute_rate | 1.5% (deliberately high so `small`'s handful of disputes is *guaranteed* to exist, not left to chance at low volume) |
| refund_rate | 5% |
| payout_interval_days | 7 |

Forty customers over ninety days, at these rates, lands at roughly 34 active, 3 canceled, 2 past_due
or trialing, 1 dispute, 1–2 refunds, about a dozen weekly payouts. Small enough to read the whole
`customers` table in one screen and recognize every state Stripe's status machines define, without
thousands of rows to wade through.

**`large`** — "thousands of customers, real churn, failed payments sitting in dunning, disputes at
several stages, a mix of plans, a populated balance ledger and payout history" (functional spec §9):

| Knob | Value |
|---|---|
| customers | 3,000 |
| span_days | 540 (~18 months) |
| products / prices | 4 products (Core, Plus, Pro, Enterprise), 8 prices — monthly + annual per tier, plus one metered overage price on Pro/Enterprise |
| plan_weights | Core 50%, Plus 32%, Pro 14%, Enterprise 4% |
| interval_weights | monthly 82%, annual 18% |
| trial_share / conversion | 25% / 65% |
| monthly_churn_hazard | 3.5% |
| annual_churn_hazard | 22% |
| dunning_entry_rate | 9% |
| dispute_rate | 0.35% |
| refund_rate | 4% |
| payout_interval_days | 7 |

Why these numbers rather than round guesses: 3.5% monthly churn and a 65% trial-to-paid conversion
sit inside the commonly cited SaaS benchmark ranges (mid-single-digit monthly logo churn, 50–70%
trial conversion) — plausible without being a claim about Stripe's own customers, which this fixture
is not. 0.35% is inside card-network dispute-rate norms (dispute programs generally flag accounts
above roughly 0.65–1%; a healthy account sits below that). The plan mix is long-tailed on purpose — a
few large accounts, many small ones — because a fixture that is flat across four tiers doesn't
exercise "what does our top decile of revenue look like," which is exactly the kind of question a
billing eval asks. 18 months rather than 24 keeps the simulated depth (and its event fan-out — see
Fork cost) inside budget while still producing customers who have renewed a dozen-plus times.

Applying a **hazard at every renewal**, rather than assigning each customer a status directly, is
what makes the churn distribution believable rather than authored: a customer who signed up 500 days
before `now` has had roughly 16 monthly rolls of a 3.5% die by the time the loop reaches `now`, so
older cohorts show more cancellations than recent ones — the same shape a real account has, where
this month's signups haven't had the chance to churn yet. Signup dates themselves are drawn
right-skewed toward the recent end of the span (`days_before = span * (1 - sqrt(u))`, `u ~ U(0,1)`),
so the account also looks like it's growing, not flat.

Emergent status distribution (not hand-set; this is what running the hazard produces at these
knobs, given for sizing purposes): roughly 55% `active`, 26% `canceled`, 7% `past_due`, 3% `trialing`,
2% `incomplete`, 2% `incomplete_expired`, 2% `unpaid`, 2% `paused` — all eight subscription statuses
represented, none of them a single planted row.

**Disputes** land across the stages Stripe's status machine actually has: at 0.35% of roughly 24,000
charges, about 85 disputes, aged by how recently they opened relative to `now` — disputes from the
last ~21 days (Stripe's evidence window) sit in `needs_response`/`under_review`; older ones are
resolved, split `won` / `lost` / `warning_closed` roughly 35/30/25%, with the remainder
`warning_needs_response`. **Payouts** run on a weekly automatic schedule for the whole 540-day span
(≈77 payouts), sweeping whatever's available as of each payout's instant; the most recent one or two,
whose instant falls inside T+2 of `now`, are left `in_transit` rather than `paid` — a payout that
recent genuinely would not have landed yet relative to the fixture's own frozen clock, and a fixture
where every payout is `paid` has nothing to say about a payout still in flight.

### `inst.bulk()` versus through the tools

The bulk of both fixtures — every customer, subscription, invoice, charge, balance_transaction,
dispute, payout, and their backfilled `event` rows — is written inside one `inst.bulk()` block. At
`large`'s volume, validating and dispatching a quarter-million rows one `instance.call` at a time
would dominate build time for no benefit: nothing about argument validation or middleware is being
tested here, only the shape of the resulting data.

**The deliberate tail is written through the real tools**, and for the reason ProjectTracker gives —
"a fixture should exercise the path an agent will use" — applied more literally here than in
ProjectTracker, because stripeapi's agent surface isn't 25 named tools over hand-written Python
functions; it's four generic tools over one dispatcher (functional spec §2). The through-the-tools
tail therefore calls `stripe_api_write`/`stripe_api_read` with `method`/`path`/`params` exactly the
way an agent would, which means it exercises the **actual route table, `ParamSpec` validation, and
response serialization** for whichever operations it touches — not a shortcut around them. For the
last three simulated customers in each fixture (`THROUGH_THE_TOOLS = 3`, same constant, same
reasoning as ProjectTracker):

```python
customer = inst.call("stripe_api_write", method="POST", path="/v1/customers", params={...})["body"]
pm = inst.call("stripe_api_write", method="POST",
                path=f"/v1/customers/{customer['id']}/...", params={...})["body"]
sub = inst.call("stripe_api_write", method="POST", path="/v1/subscriptions", params={...})["body"]
invoice = inst.call("stripe_api_read", method="GET", path=f"/v1/invoices/{...}")["body"]
```

**Why this is stronger here than in ProjectTracker's case.** ProjectTracker's bulk/tools seam exists
because of one stateful counter, the per-team `issue_counter`. This world has more than one such seam:

- **`invoice.number`**, Stripe's human-readable sequential invoice number. Bulk-written invoices must
  set it directly and must leave whatever counter mints it (per-account or per-customer — see
  Dependencies, this isn't nailed down yet) at the value the bulk load consumed, exactly as
  `_hand_the_counters_to_the_tools` does for `teams.issue_counter`. Skip this and the first
  through-the-tools `finalize_invoice` mints a number the bulk load already used.
- **The balance ledger itself.** `balance` is a *computed* read over `balance_transactions`
  (architecture §6.4) — there is no counter to hand off, but there is an ordering constraint just as
  real: every `balance_transaction` the bulk load writes must be `available_on`-consistent with the
  payouts it also writes, or the through-the-tools tail's first real payout could try to draw down
  money the bulk history already paid out, and `ledger.py`'s payout logic — written for the real,
  incremental case — would either double-count or refuse it.
- **Idempotency.** `idempotency_keys` is untracked infrastructure the bulk path never touches (bulk
  bypasses the dispatcher entirely, so there is nothing to reconcile), but the through-the-tools tail
  *does* go through the idempotency middleware, so it's the one place in fixture generation where an
  accidentally reused key would silently short-circuit a write. The tail therefore passes an explicit,
  distinct `idempotency_key` per call rather than relying on a default.

A ledger and a sequential invoice number both make the bulk/tools seam load-bearing in a way
ProjectTracker's single counter only foreshadows: get the seam wrong here and the fixture's *money*
doesn't add up, not just its next id.

### Exact `seahaven fixture` commands (the committed recipe)

Following the architecture's stated lineage (`empty` → fork → `small`/`large`, architecture §9) and
the capability map's committed recipe pattern exactly:

```sh
seahaven fixture freeze empty --now 2026-09-01T14:00:00.000Z \
    --run fixtures_src.generate:empty \
    --description "Schema only. Start here to write history, or to test setup flows."

seahaven fixture fork empty small \
    --run fixtures_src.generate:small \
    --description "A new SaaS account: forty customers, two products, three plans, a \
90-day history. Readable end to end."

seahaven fixture fork empty large \
    --run fixtures_src.generate:large \
    --description "A scaled account: three thousand customers, real churn, dunning, \
disputes at several stages, an eighteen-month history, a populated ledger and payout record."
```

`--now` is passed **exactly once**, on the one `freeze` call, and is never re-typed: `NOW` lives as
one constant in `generate.py` and the CLI command above is generated (and re-runnable) from
`python fixtures_src/generate.py`, which passes it for you — the same discipline ProjectTracker's
module docstring states, for the same reason (`db_schema_and_fixtures.md:261-267`: a blank instance
with no `--now` silently takes the wall clock). `fork` takes no `--now` by construction — it inherits
`empty`'s clock, which is the whole reason `small` and `large` fork from `empty` rather than each
freezing their own blank instance: forking is the cheap, correct way to guarantee all three fixtures
share the identical frozen instant without a second place that value could drift.

Rebuilding is: delete the three directories under `fixtures/`, then `python fixtures_src/generate.py`
with no arguments, which rebuilds all three in the same lineage and prints each `fixture.id`, `now`,
and directory. `freeze`/`fork` both refuse to overwrite an existing directory (there is no in-place
edit of a fixture — capability map, "Building one"), so a rebuild is always delete-then-regenerate,
never edit-then-refreeze.

### Invariant checks — the same SQL the eval reward functions run

`assert_fixture_invariants` runs at the end of every builder, against `inst.inspect()`, exactly the
way ProjectTracker's `_assert_within_span`/`_assert_nothing_predates_its_parent` do — and for the same
reason architecture §11 states: "Invariant tests are written as SQL over `inst.inspect()` so they are
literally reusable as eval reward functions." `INVARIANTS` is a public tuple so `tests/test_fixtures.py`
and the eval harness both import it rather than each keeping their own copy that can drift:

```python
INVARIANTS: tuple[tuple[str, str], ...] = (
    (
        "a balance_transaction whose net isn't amount minus fee",
        "SELECT count(*) AS n FROM balance_transactions WHERE net <> amount - fee",
    ),
    (
        "a charge refunded past its own amount",
        "SELECT count(*) AS n FROM charges WHERE amount_refunded > amount",
    ),
    (
        "an invoice whose lines don't sum to its total",
        "SELECT count(*) AS n FROM invoices WHERE total <> ("
        " SELECT COALESCE(SUM(json_extract(line.value, '$.amount')), 0)"
        " FROM json_each(json_extract(invoices.lines, '$.data')) AS line)",
    ),
    (
        "two invoices on the same subscription with overlapping periods",
        "SELECT count(*) AS n FROM invoices a JOIN invoices b"
        " ON a.subscription = b.subscription AND a.id < b.id"
        " WHERE a.period_end > b.period_start AND b.period_end > a.period_start",
    ),
    (
        "a charge older than the payment_intent it belongs to",
        "SELECT count(*) AS n FROM charges JOIN payment_intents"
        " ON payment_intents.id = charges.payment_intent"
        " WHERE charges.created < payment_intents.created",
    ),
    (
        "a balance_transaction older than the charge it settles",
        "SELECT count(*) AS n FROM balance_transactions JOIN charges"
        " ON charges.balance_transaction = balance_transactions.id"
        " WHERE balance_transactions.created < charges.created",
    ),
    (
        "a dispute older than the charge it's on",
        "SELECT count(*) AS n FROM disputes JOIN charges ON charges.id = disputes.charge"
        " WHERE disputes.created < charges.created",
    ),
    (
        "an idempotency key reused across two different stored responses",
        "SELECT count(*) AS n FROM idempotency_keys GROUP BY key HAVING count(*) > 1",
    ),
)
```

(Column names above follow the architecture's table list and Stripe's own field names; the
authoritative names are `components/data_model.md`'s — this list gets a mechanical pass once that
document's DDL lands, without changing what it checks.)

Plus the span check, exactly ProjectTracker's shape: every table/column in a `_TIMESTAMPS`-equivalent
list falls inside `[now - span_days, now]`, except the handful of forward-pointing fields named above,
whose ceiling is `now + <that field's own horizon>` instead.

This list is intentionally the same five checks architecture §11 already names as this world's
invariants (ledger sums; no over-refund; lines sum to total; periods never overlap; one object per
idempotent retry — read here as "no reused idempotency key", the fixture-time form of that same rule,
since a frozen fixture has no live retries to observe). Running them against every fixture at build
time, not just against live instances in `tests/`, means a fixture that violates its own world's rules
fails to build rather than shipping and failing an eval's reward function on the first rollout that
reads it.

### Fork cost

Measured on this framework (capability map, "Fixture lifecycle and fork cost"):

| Fixture | Rows | File size | Fork median | Fork p90 | Fork max |
|---|---|---|---|---|---|
| 5,000 customers × 3 charges | 20,000 | 2.25 MiB | 4.50 ms | 5.01 ms | 7.78 ms |
| 20,000 customers × 5 charges | 120,000 | 13.70 MiB | 11.50 ms | 13.35 ms | 29.07 ms |

Fitting a line through the two points (`cost ≈ a + b·rows`): `b ≈ (11.5 − 4.5) / (120,000 − 20,000) ≈
0.00007 ms/row`, `a ≈ 4.5 − 0.00007·20,000 ≈ 3.1 ms` fixed overhead (file copy setup, per-node
connection). That gives `cost(rows) ≈ 3.1 ms + 0.07 ms per 1,000 rows` as a first-order estimate.

**`large`'s row count, estimated per-table from the population numbers above** — this is the number
that matters, and it is dominated by one table:

| Table family | Rows (approx.) |
|---|---|
| customers, payment_methods, subscriptions, subscription_items | ~13,000 |
| invoices, invoiceitems | ~30,000 |
| charges, payment_intents, refunds | ~64,000 |
| disputes, payouts | ~200 |
| balance_transactions | ~41,000 |
| credit_notes, customer_balance_transactions, catalog | ~2,300 |
| **events** (backfilled per every earlier row's own state change — implementation plan Phase 17) | **~111,000** |
| **Total** | **~262,000** |

Plugging into the fit: `3.1 + 0.07 × 262 ≈ 21 ms` median. Scaling the observed p90/max ratios (p90
≈1.16× median at both measured sizes; max/median grows from 1.7× to 2.5× as size grows, consistent
with more variance at larger file copies) gives a rough p90 in the mid-20s ms and a max somewhere in
the 45–55 ms range. That is still comfortably inside "the large fixture must forks in milliseconds"
(project overview §11) as an order-of-magnitude statement, but it is no longer the same single-digit
regime the measured 20K-row point sits in, and events are why.

**This is worth flagging as a real tension, not designing around silently.** Events dominate row
count in this fixture by roughly 3:1 over every other table combined, because the implementation plan
(Phase 17) commits to backfilling an event for every state change "across every earlier slice," and
an 18-month, 3,000-customer simulated history produces a state change roughly every few hundred
milliseconds of simulated time. The population numbers above (3,000 customers, 540 days, not 5,000
customers over 730 days, which functional spec §9's "thousands of customers" would also satisfy) are
already a deliberate scale-down from the most literal reading of that phrase, chosen specifically to
keep total rows near double the measured 120K-row benchmark rather than five or six times it. If a
future revision needs more customers or a longer span, **events are the first thing to reduce**, not
customer count — e.g. by backfilling events only for the most recent quarter of each subscription's
history rather than its whole lifetime, since an eval reading "what changed recently" doesn't need an
`invoice.paid` event from fourteen months ago to exist as a queryable row. That's a real design choice
this document is deferring rather than making, because it belongs in `components/billing_engine.md`
and `components/data_model.md`'s event-emission design, not here — but the fork-cost budget is the
constraint that should decide it, and right now nothing in the architecture states that budget
explicitly. **Phase 19 should measure `large`'s actual fork cost against real `world.instance()` calls
before committing the fixture**, exactly as the implementation plan already schedules ("fork-cost
assertion"), and treat a result meaningfully above this estimate as a signal to trim events first.

A second reason the linear-in-rows estimate could understate cost: fork cost measured here is "private
SQLite file copy plus per-node connection setup," which is a function of **file bytes**, not row count
directly. The measured fixture's 120K rows at 13.70 MiB is ≈114 bytes/row on two narrow tables
(`customers`, `charges`). This world's rows are wider — `invoices.lines`, `credit_notes` lines,
`events.data` and embedded `discount` objects are all JSON columns (architecture §4.3) — so bytes/row
here plausibly runs 150–250 bytes rather than 114, meaning `large`'s file could land at 40–65 MiB
rather than the ~30 MiB a naive row-count scaling implies. **Row count would put the milliseconds
constraint genuinely at risk somewhere in the 700,000–1,000,000-row range** on this extrapolation
(≈50–75 ms median, plausibly 100+ ms at the tail) — which this design stays well clear of, but which a
later revision adding more history or more disputed-charge detail should treat as a hard ceiling to
check against, not just row count in isolation.

### Fixtures come late — what resource phases use in the meantime

Every column addition changes the schema hash (`SH403`), and the schema hash invalidates every
fixture (implementation plan, "Fixtures come late"). `fixtures_src/generate.py`'s `small`/`large`
builders are therefore written and populated in **Phase 19**, after the last table exists, not
incrementally alongside each resource phase.

In the meantime (Phases 6–18), resource-phase tests never import `generate.py` and never build a
committed fixture under `fixtures/`. They use Seahaven's pytest plugin's own `fixture=None` shape —
`@pytest.mark.seahaven(fixture=None, now=BLANK_NOW)` — a blank, uncommitted, in-memory-lifetime
instance, with whatever handful of rows that test needs inserted directly in the test module (a few
`inst.bulk()` rows or a couple of tool calls), exactly ProjectTracker's own test convention
("`fixture=None` with `now=BLANK_NOW` for an instance with no fixture behind it," ProjectTracker
`AGENTS.md`). `BLANK_NOW` is defined once in `tests/conftest.py` as the **same literal string** that
will later become `fixtures_src.generate.NOW`, chosen at Phase 1 alongside the `empty` fixture and
never touched again — so no rework happens at Phase 19 beyond writing the population logic itself; the
frozen instant every test already assumes is already the one `large` and `small` end up frozen at.

`empty` is the one fixture built early (Phase 1), because the pytest plugin needs *something* to
fork instances from, and because `empty` does no simulation — freezing it early costs nothing and
never needs rebuilding for a reason other than a genuine schema change, which happens to every
committed fixture equally regardless of when it was first built.

## Dependencies

**Depends on:**
- `components/billing_engine.md` — `billing/invoicing.py`'s period-boundary function and
  `billing/proration.py`'s `proration_lines(...)` must be callable with an explicit period anchor
  (not only "now") so the generator can compute historically-correct period boundaries and totals for
  bulk-written rows without re-deriving that arithmetic. **This is not yet specified** in the
  documents this design was written from (architecture §8 says these are "pure functions over rows
  plus `ctx` wherever possible" but doesn't say whether the period reference is a parameter or reads
  `ctx.clock` internally) — flagged here as a concrete requirement `billing_engine.md` needs to close
  before Phase 19 can be written against it.
- `components/billing_engine.md` / `components/data_model.md` — **where `invoice.number`'s sequence
  lives is not specified anywhere in the architecture or data model as read for this design.** Unlike
  `teams.issue_counter` in ProjectTracker, there is no named counter column or table for it in
  architecture §4. This is a genuine gap, not a detail this component can safely invent on its own,
  because whichever shape it takes (an account-level counter row, a per-customer sequence, something
  else) determines exactly what `_hand_the_counters_to_the_tools`'s equivalent here has to update.
  Flagged as a blocking dependency for Phase 19, to be resolved when `data_model.md` is written.
- `components/data_model.md` — exact table/column names for the invariant SQL above; the list's
  *checks* don't change, only their spelling.
- `components/cross_cutting.md` — the event payload shapes `emit_event` writes, so bulk-backfilled
  `event.data.object` rows are byte-identical in shape to what the live path would have produced for
  the same state change (the same principle as ProjectTracker's trail payloads: "an eval reads the
  trail without caring which of the two write paths put a row in it").
- Seahaven framework: `inst.bulk()`, `inst.freeze()`/fork via the `seahaven fixture` CLI, `ctx.clock`,
  `ctx.ids.random` (seeded per instance, so two runs of `generate.py` produce byte-identical fixtures),
  `inst.inspect()`.

**Depended on by:**
- `components/evals.md` — every eval task starts from one of these three fixtures; `INVARIANTS` is
  imported directly as (a subset of) reward functions.
- `tests/` broadly — determinism tests ("same fixture + seed → identical ids, timestamps and change
  log," architecture §11) run against these fixtures' committed bytes.
- Any host that `add_world`s this world (functional spec §10) forks one of these three as its billing
  subsystem's starting state; "fixtures must make sense standalone and as part of a composed world"
  (functional spec §9) is a constraint on this component, not on the host.

## Test Plan

- `test_fixtures_build_deterministically` — build each fixture twice into two temp directories from
  the same `ctx.ids.random` seed; assert byte-identical `state.sqlite` (matching ProjectTracker's own
  `tests/test_fixtures.py` pattern of rebuilding into a temp dir rather than moving the package's own
  `fixtures/`, so the running test process's own fixtures are never disturbed).
- `test_empty_has_no_rows` — every table in `describe_schema()` is empty in `empty`.
- `test_small_row_counts_match_shape` — table counts for `small` fall within the ranges the
  `AccountShape` at those hazard rates would produce (a range, not an exact count, since churn and
  dunning are rolled from `ctx.ids.random`).
- `test_large_row_counts_match_shape` — same, for `large`; also asserts total row count stays under
  the fork-cost budget ceiling stated above (currently ~262,000; the test's actual assertion is a
  concrete number once the generator is written, not the estimate).
- `test_all_eight_subscription_statuses_present_in_large` — every value of `subscriptions.status`
  appears at least once; same for `disputes.status` (at least five distinct stages) and
  `payouts.status` (at least one `in_transit` or `pending`).
- `test_invariants_pass_on_every_fixture` — runs `INVARIANTS` plus the span check against `empty`,
  `small`, and `large`; every count is zero.
- `test_invoice_number_sequence_continues_after_bulk_load` — the through-the-tools tail's first
  `finalize_invoice` call produces a number strictly greater than every bulk-written invoice's number
  on the same counter scope, and no collision.
- `test_ledger_never_draws_down_more_than_available` — no payout's amount exceeds the sum of
  `balance_transactions.net` available (`available_on <= created`) as of that payout's own `created`,
  checked payout by payout in chronological order (a stronger, sequential form of the static
  `INVARIANTS` ledger check, needed because a snapshot check alone can't see a running balance going
  negative mid-history and then recovering).
- `test_through_the_tools_tail_uses_distinct_idempotency_keys` — the generator's own calls never share
  an idempotency key with each other or with anything the bulk load implies (bulk never populates
  `idempotency_keys`, so this reduces to: the tail's own key set has no duplicates).
- `test_fork_cost_of_large` — `world.instance("large")` + one `SELECT count(*)` per table, 50 repeats,
  median/p90/max recorded and asserted under a stated ceiling (this is the "fork-cost assertion" the
  implementation plan schedules for Phase 19; the ceiling itself should be set from the *measured*
  number once `large` exists, not from this document's extrapolation).
- `test_rebuild_refuses_to_overwrite` — `seahaven fixture freeze empty ...` against an existing
  `fixtures/empty/` directory fails rather than silently replacing it.
- `test_seahaven_check_clean_on_fixtures` — `seahaven check` reports zero `SH4xx` findings against the
  committed fixtures directory.
