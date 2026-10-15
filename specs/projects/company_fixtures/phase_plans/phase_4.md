---
status: complete
---

# Phase 4: `big_co` (Ostrander Systems)

## Overview

Builds and commits `big_co`, the test-mode account of Ostrander Systems, a Denver field-service
SaaS company, by replaying 18 months of history through the world's own code
(`components/big_co.md` is the recipe; functional spec §5 the contract). It adds the population
draw, the lives simulation, the scripted loose ends, the frozen fixture, the guide's `big_co` half
with checked landmarks, the fork-cost test and the remaining doc lines. Nothing under `src/`
changes.

A prior agent left an unverified draft in the working tree (`fixtures_src/big_co/`,
`fixtures/big_co/`, two test files). This phase keeps what matches `big_co.md` and brings the rest
to the spec: ramps for every recent Enterprise deal on `send_invoice`, scheduled downgrades
chosen at their last boundary, API steps that fail loudly, and the full test plan.

**User decision (overrides functional spec §5.3, §5.8 and `big_co.md` §1, §8 on population
size).** The Phase 2 benchmark measured about 9 KB per billing cycle, which projected 135–250 MB
for 3,000 customers; GitHub refuses files over 100 MB. The user chose about 500 customers rather
than about 3,000, with the full 18-month story kept and `fixtures/big_co/state.sqlite` at 25 MB or
less. If 500 does not fit, the customer count comes down until it does (about 385 is acceptable).
Every count-based knob (`migration_customers`, `organic_customers`, `tax_exempt_customers`,
`concessions`, `scheduled_downgrades`, `duplicate_pairs`, `cs_notes`) and every count-based
expectation scales by the same factor; every rate stays as specified.

## Deviations the user decided

1. **Population size**, above.
2. **Disputes come from one-off payments, not renewals** (overrides `big_co.md` §4.1, §4.2 and §5
   on how a dispute is made). The world opens a dispute card's dispute only when a PaymentIntent
   confirms; a renewal charged by the subscription engine opens none. The build calls no world
   function beyond `dispatch` and the four engine functions, so every dispute is on a one-off
   services payment (training day, data import, report build, app rollout) confirmed through
   `POST /v1/payment_intents` on a dispute test card the customer adds for it. The draw keeps the
   spec's rate (`dispute_per_charge` per card-paid period) and mix, and the course by age is
   unchanged. The scripted queue, inquiries and review are services payments made at the instants
   of the August renewals the spec names, so their due dates are the spec's. Ordinary services
   payments on the card on file (`services_per_period`, 3% per card-paid period) keep the disputed
   ones from being the account's only PaymentIntents. Two settled disputes are scripted (one `won`,
   one `lost`): at the scaled population the simulation draws too few disputes to promise every
   stage of §8.
3. **Expectation ranges** (overrides `big_co.md` §8 at scale): active subscriptions 190–260, because
   the specified rates leave under 60% of customers active at `NOW`, not the spec's 70% (a 2%
   unrecovered decline per renewal, plus churn); disputes from 10 to the spec's 110 at `SCALE` plus
   the nine scripted, because 0.3% of a few thousand card-paid periods is single digits.

## Steps

1. **`fixtures_src/big_co/shape.py`.** `Shape` and `CATALOG` per `big_co.md` §1–§2, with the count
   knobs scaled (migration and organic in the spec's 1,450 : 1,550 ratio) to the largest population
   whose frozen file is ≤ 25 MB. `TAX_RATES` for the six states.

2. **`fixtures_src/big_co/population.py`.** `Prospect` and `draw_population(rng, shape)`, pure,
   sorted by `(created, key)`, per `big_co.md` §3: the migration cohort spaced through migration
   week with `legacy_account_id`/`customer_since` and a legacy renewal date as `trial_end`; organic
   signups with linearly rising density; self-serve card trials, sales-led Business (cardless
   trials or onboarding), Enterprise deals with a per-deal rate; CRM metadata variants;
   `SWITCH25`, association coupons by trade; `AUTH_REQUIRED` on non-trial organic signups; exactly
   `tax_exempt_customers` school districts and municipal facilities departments on invented place
   names; `cs_notes` notes; `duplicate_pairs` variant-name duplicates 5–200 days later, 60% never
   subscribing and 40% on a lapsing trial.

3. **`fixtures_src/big_co/simulate.py`.** `Catalog` (the five instants of §2) and `Lives`, the
   subscription driver's `Hooks` (§4):
   - signup: customer, card, Enterprise deal price, subscription (`send_invoice`, 30 days for
     Enterprise), onboarding invoice two hours later, lapsing or converting trials;
   - **ramps** (§4.5): every Enterprise deal signed within 90 days of `NOW` gets a subscription on
     `send_invoice`/30 days, then `POST /v1/subscription_schedules` `from_subscription` and an
     update to three phases (3 months at the signed seats, 3 at ×1.5, 6 at ×2, month arithmetic
     from `invoicing.add_interval`), `end_behavior: release`, default settings `send_invoice`.
     The world declares no `days_until_due` on a schedule, so a schedule cannot create a
     `send_invoice` subscription itself; the two-step path reaches the same state (code comment);
   - the boundary draws of §4.1 (churn, seats, upgrade, immediate downgrade, decline, disputed
     services payment, ordinary services payment, credit note, refund), `on_retry`,
     `on_status_change` (unpaid closure after 45 days);
   - **disputes** (§4.2, as decided above): a services payment at a uniform instant in the period,
     confirmed on a dispute card by `dispute_mix`; the world opens the dispute as it confirms, and
     the step decides its course by age (evidence, acceptance, a fraud cancellation);
   - open `send_invoice` invoices paid by card 5–28 days or out of band 3–35 days after opening;
     a card payment on a card that cannot pay is an expected decline, every other API step uses
     `Client.call` so an unexpected answer stops the build;
   - concessions, the SLA batch (§4.7), and **scheduled downgrades** (§4.5): drawn at a Pro or
     Business subscription's last boundary before `NOW`, a step at a uniform instant between that
     boundary and `NOW`, capped at exactly `scheduled_downgrades`;
   - `business_days()` for payouts (§4.8).

4. **`fixtures_src/big_co/loose_ends.py`.** `scripted(shape)`: the fixed prospects of §5 with their
   beats (disputed services payments at the August renewal instants, evidence, the two settled
   disputes, the overdue Enterprise contract paid once, the ramp, the churn risk, the incomplete
   and expired signups, the paused trials, the unpaid and past-due dunning, the five draft
   onboarding invoices).

5. **`fixtures_src/big_co/__init__.py`.** `build(inst)` as `big_co.md` shows it and
   `EXPECTATIONS` (§8, counts scaled, the two ranges above; each status ≥ 3 and `incomplete` ≥ 1;
   all six dispute stages;
   payouts 350–400 with one `pending`; SLA credit notes; five drafts in the last hour; exactly the
   scheduled downgrades). `generate.DESCRIPTIONS["big_co"]` names the real customer count.

6. **The frozen fixture.** `rm -rf fixtures/big_co && uv run python fixtures_src/generate.py
   big_co`; measure build time (< 3 minutes) and size (≤ 25 MB).

7. **The guide.** `fixtures_src/guide.py` (`python -m fixtures_src.guide [--write]`, architecture
   §3.2): parse `sql landmark=<fixture>/<name>` blocks and their `json rows` blocks, run each query
   against the committed fixture, compare or rewrite. The `big_co` half of `docs/fixtures.md`: story,
   account identity, catalog, counts, how to open it, and the landmarks of `big_co.md` §7.

8. **Docs.** `AGENTS.md`'s fixture line, the README's "ready-made fixtures" line, and
   `docs/technical.md`'s fixtures section and layout line name the three fixtures.

9. **Web check** that "Ostrander Systems" and `ostranderfieldops.com` name no real company.

## Tests

- `tests/test_population.py`
  - `test_population_is_pure`: same seed, same list; different seed, different list.
  - `test_population_shape`: cohort sizes; tier mix within ±3 points (pooled over several seeds,
    so the scaled population is not a single lucky draw); every prospect has a domain and an
    address; exactly the shape's exempt count, all school districts or facilities departments;
    duplicates reference an earlier prospect's domain; migration metadata; no denied domain or
    cliché.
  - `test_loose_ends_are_fixed`: `scripted` is deterministic and keyed `le_…`.
- `tests/test_big_co.py`
  - `test_account_identity`: the tools serve Ostrander's account in test mode; a newly minted id
    carries its account fragment.
  - `test_opening_live_is_refused`.
  - `test_expectations`: §8 through `check_fixture`.
  - `test_each_loose_end`: one block per row of §5 by company name (dispute queue and due dates,
    inquiry stages, review, overdue invoice and its due date, ramp phases, churn risk, incomplete,
    expired, paused, unpaid, past due, drafts, pending payout), and the two settled disputes.
  - `test_every_dispute_is_on_a_services_payment`: each disputed charge has its PaymentIntent, a
    services description and no invoice; most services payments are not disputed.
  - `test_tools_carry_on`: on Halvorsen and a seeded random active customer, through the MCP tools:
    a new invoice gets the customer's next number, a new object's `x_seq` follows every existing
    one, a payout draws only the available balance, search finds the customer.
  - `test_file_size`: `state.sqlite` ≤ 25 MB.
- `tests/test_fixtures.py`: `big_co` rebuilt into a temporary world is byte-identical to the
  committed file.
- `tests/test_fork_cost.py`: 30 instances of each company fixture; median and max asserted under
  ceilings set from this phase's measurement.
- `tests/test_guide.py`: every landmark in `docs/fixtures.md` returns its documented rows, and
  every landmark query has an `ORDER BY`.
