---
status: draft
---

# Implementation Plan: StripeAPI

Two kinds of phase. **Infrastructure phases** build the machinery every resource uses. **Resource
phases** are vertical slices that each follow the same four-step recipe, so they are repetitive by
design and can be worked one at a time without re-deciding anything.

## The resource phase recipe

Every resource phase does the same four things, in this order:

1. **Schema** — the tables, `STRICT`, explicit primary key, enum `CHECK`s from `spec/enums.py`.
2. **Routes and handlers** — the `ResourceSpec` (which generates CRUD) plus the slice's hand-written
   state transitions, registered in `routes.py`.
3. **Probe the real API** — drive the scenarios for this slice against real test mode, record
   cassettes, and turn what actually came back into tests. **This step is how the tests get written**;
   they are not written from our reading of the docs. See §Probing below — it has an environment
   dependency that must be solved before Phase 5.
4. **Make it pass** — implement until the recorded behavior and the schema-conformance checks are
   green, and add the slice's invariant tests.

A slice is not done until steps 3 and 4 are both green and `seahaven check` is clean.

## Phases

### Infrastructure

- [ ] **Phase 1: Skeleton.** `seahaven new`, package layout, `pyproject.toml` (pinning
      `pydantic==2.12.3`, `SEAHAVEN_FINDINGS.md` Entry 1), root `AGENTS.md` with the check commands,
      `world.py`, `errors.py`, `stripe_errors.py`, the error-handler middleware, `_ids.py`, `_time.py`,
      `_json.py`, and `empty` fixture. Ends with a green `pytest` and a clean `seahaven check` on a
      world that does nothing.
- [ ] **Phase 2: The spec pipeline.** `tools_dev/prune_spec.py` and the committed artifacts it
      generates — `spec3.min.json`, `expandable.py`, `enums.py`, `event_types.py` — plus the drift
      test. Built early because the schema's enum `CHECK`s and every later conformance test read from
      it.
- [ ] **Phase 3: Dispatcher and the four tools.** Router, `ResourceSpec` engine, `ParamSpec`,
      response construction, pagination, the Stripe error envelope, and the four tools. Exercised
      against one throwaway resource, replaced in Phase 5.
- [ ] **Phase 4: Schema conformance harness.** Generated validation of every returned object against
      `spec3.min.json`, including the six bare-`string`-but-enumerated fields. Before any real
      resource, because it constrains all of them.
- [ ] **Phase 5: Cassette harness.** Recorder (via `stripe-python`), replayer, and
      `allowed_differences.py`. **Gated on solving the probing dependency below.**

### Resource slices

Grouped rather than strictly per-table: a charge with no payment intent, or an invoice line with no
invoice, cannot be exercised end to end, and a phase that ships untestable code defeats the recipe.
Each still touches only its own tables.

- [ ] **Phase 6: Customers and payment methods.** `customers`, `payment_methods`. The first real
      slice; establishes the pattern every later one copies. Includes magic-card behavior on
      payment-method creation.
- [ ] **Phase 7: Catalog.** `products`, `prices`, `coupons`, `promotion_codes`, `tax_rates`. Mostly
      generated CRUD — the slice that proves the `ResourceSpec` engine carries its weight.
- [ ] **Phase 8: The money path.** `payment_intents`, `charges`. Confirm, capture, cancel. The
      headline idempotency eval becomes runnable here.
- [ ] **Phase 9: Refunds and disputes.** Partial refunds, over-refund rejection, dispute lifecycle.
- [ ] **Phase 10: Setup intents.**
- [ ] **Phase 11: Ledger and payouts.** `balance_transactions`, computed `balance`, `payouts`. The
      ledger invariants land here.
- [ ] **Phase 12: Subscriptions.** `subscriptions`, `subscription_items`, the eight-status machine.
- [ ] **Phase 13: Invoices.** `invoices`, `invoiceitems`, nested lines, the status machine,
      `automatically_finalizes_at`.
- [ ] **Phase 14: Proration and dunning.** The behavior spanning Phases 12 and 13. Proration's
      documented −667/+333/−334 case is a test here; the half-cent tie-break is the one assumption and
      is probed against the real API in this phase's step 3.
- [ ] **Phase 15: Credit notes and customer balance.** `credit_notes`,
      `customer_balance_transactions`, the three settlement channels.
- [ ] **Phase 16: Subscription schedules.** Scoped-down `phases`.
- [ ] **Phase 17: Events.** Emission across every earlier slice, backfilled, plus `/v1/events`.
      Late on purpose: it needs every mutation that exists.

### Completion

- [ ] **Phase 18: Idempotency, expansion and cross-cutting sweep.** The middleware and the expansion
      resolver against the whole surface, not one slice. Some of this lands opportunistically earlier;
      this phase is where it is made uniform and the cross-cutting tests become exhaustive.
- [ ] **Phase 19: Fixtures.** `small` and `large`, timeline simulation, fork-cost assertion.
      Deliberately late — see §Fixtures below.
- [ ] **Phase 20: Evals.** Ten to twenty tasks with SQL reward functions, headline first.
- [ ] **Phase 21: `get_stripe_account_info`.** P2. Cuttable.
- [ ] **Phase 22: STOP — confirm before starting search.** An explicit gate, per the functional
      spec §3.3.
- [ ] **Phase 23: Search.** The seven endpoints, the query-language parser, seven field allowlists,
      FTS5, the `page`/`next_page` paginator.
- [ ] **Phase 24: Documentation and findings.** README, world `AGENTS.md`, and the recommendations
      document distilled from `SEAHAVEN_FINDINGS.md`. The findings log itself is written continuously
      from Phase 1, never reconstructed here.

## Probing: an unsolved environment dependency

Step 3 of every resource phase talks to `api.stripe.com`, which **this environment's egress policy
blocks**. That was a footnote when recording was one late harness task; under this plan it is on the
critical path of every slice from Phase 6 onward.

Three ways to solve it, to be decided before Phase 5:

1. Allowlist `api.stripe.com` in the environment's network policy — then the recipe works as written.
2. Record in a different environment and commit cassettes — the loop still works, with a handoff per
   slice.
3. Batch the probing: record every scenario once, up front, in one session elsewhere. Cheapest on
   handoffs, but it front-loads deciding what to probe before the slices have taught us what is
   interesting.

Until one is chosen, resource phases can complete steps 1, 2 and 4 against schema conformance and
hand-written tests, with step 3 outstanding — but a slice in that state is **not done**, and the plan
should not pretend otherwise.

## Fixtures come late

Every column addition changes the schema hash, and the schema hash invalidates **every** fixture.
Building `large` before the schema settles means rebuilding it after every resource phase. Phase 19
therefore builds the real fixtures once, after the last table exists; resource phases use small
throwaway fixtures built by their own tests.

`empty` is the exception and is built in Phase 1, because the pytest plugin needs something to make
instances from.
