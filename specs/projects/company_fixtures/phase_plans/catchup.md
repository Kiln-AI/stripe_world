---
status: complete
---

# Catch-up: review fixes for Phases 1–3 (and two Phase 4 items)

## Overview

Phases 1–3 were committed without code review. Three reviews have since been run on them, and
the manager triaged their findings with the user. This round does every item that triage marked
DO. Nothing under `src/` changes except `startup.py`, which is part of the account-table change.
Four world bugs the reviews found were filed as issues instead of being fixed:
[#12](https://github.com/Kiln-AI/stripe_world/issues/12),
[#13](https://github.com/Kiln-AI/stripe_world/issues/13),
[#14](https://github.com/Kiln-AI/stripe_world/issues/14) and
[#15](https://github.com/Kiln-AI/stripe_world/issues/15). The fixtures avoid each bug where they
can, and the guide declares the rest.

The web check found a live product named "Wrenfield" at `wrenfield.app`, the domain `small_co`
uses. The user kept the name (decision 5 below).

## Deviations from the specs

### The user's decisions

1. **World bugs are declared or avoided, not fixed** (functional spec §2.6 and §8 keep the world
   unchanged).
   - **#12, renewal lines bill the period that just ended.** A trial-end invoice is stamped with
     the new period, and the renewal after it has that same span. Functional spec §3.4 says no
     two cycle invoices on a subscription overlap. The check now allows exactly one exception:
     an identical span between a trial-end invoice and the cycle invoice right after it. Any
     other overlap fails. The gap is declared in the guide and in a comment at the exclusion.
     **Fixed since:** a renewal now rolls the item periods before it invoices, so its lines bill
     the period that starts at the boundary, and a natural trial end's invoice period looks back
     over the trial. Both fixtures were rebuilt, the exclusion is gone (no cycle overlap passes),
     a line-level check fails any two overlapping non-proration, non-zero lines of one item, and
     the guide no longer lists the gap.
   - **#13, repeating coupons never end and `times_redeemed` stays 0.** No coupon uses
     `duration: repeating`.
     - `LAUNCH` is 50% off once: the first invoice. The spec said 50% off for 3 months.
     - `SWITCH25` is 25% off once. The spec said 25% off for 3 months.
     - Neither the guide nor the tests claim a redemption count (the spec said `LAUNCH` 10 and
       `TEACHWELL25` 5). They also make no claim about discounts having ended.
     **Fixed since:** a repeating discount ends `duration_in_months` after it starts and bills
     only invoices before its end, leaving the subscription once its end passes; each
     discount minted counts a redemption, and `valid` reflects `redeem_by` and
     `max_redemptions` at read time. `LAUNCH` and `SWITCH25` are 50% and 25% off for 3 months
     again, both fixtures were rebuilt (`LAUNCH` redeemed 10 times, `TEACHWELL25` 5), the guide
     shows the counts and no longer lists the gap.
   - **#14, pending invoice items are never swept.**
     - Anika's setup fee is billed on its own invoice right after she subscribes (the
       `BillPending` beat, which is not in small_co.md §3's beat vocabulary).
     - Elena's $49 item stays pending. The spec said "for her 2026-09-10 renewal"; the guide no
       longer says it will be swept there, and declares the gap.
     **Fixed since:** a subscription's invoices (create, trial end, renewal, resume, conversion
     and the `always_invoice` proration invoice) sweep the customer's pending items that are
     untagged or tagged to that subscription. The `BillPending` beat is gone: Anika's setup item
     is created a minute before she subscribes, as small_co.md row 13 has it, and her first
     invoice sweeps it (`LAUNCH` takes half off it too). Elena's item is still pending at `NOW`,
     and the guide says her 2026-09-10 renewal will sweep it. Both fixtures were rebuilt, and the
     guide no longer lists the gap.
   - **#15, `canceled_at` is null on a cancel-at-period-end request.** Declared in the guide.
     **Fixed since:** the world now stamps `canceled_at` at the request, both fixtures were
     rebuilt, and the guide no longer lists the gap.
2. **Disputes come only from one-off payments** confirmed through `POST /v1/payment_intents` on
   a dispute test card. `big_co` already worked this way.
   - small_co.md row 45 had Grace buy Studio annual on the dispute card. Now she buys it with a
     good card. At 15:25 the same day she pays $49 for "Done-for-you setup" as a one-off payment
     on a `DISPUTE_FRAUDULENT` card added for it. That payment is the one disputed.
   - `OneOffPayment` gains an optional `card`: a card added for that payment, not made the
     default.
   - The evidence is still due 2026-09-07T23:59:59Z.
   - No engine function beyond the spec's four is called.
3. **Accepted as they stand:** `big_co` at about 380 customers and 25 MB or less, and its widened
   expectation ranges (both in `phase_4.md`).
4. **Dropped from this round:**
   - `test_benchmark`. Phase 2 has no plan recording a projection.
   - Renaming the replay test files to architecture §3.3's names. The tests live in
     `tests/replay/test_clock_and_timeline.py`, `test_drivers.py` and `test_finish_and_checks.py`.
   - Rewriting small_co.md's tables (corrections below).
   - The three "Done-for-you setup" products.
   - In `big_co`, the `_card_step`/`_swap` duplication, and the refund and credit-note draws.
     big_co.md §4.1 makes those two draws independent; `_settle_draws` makes them exclusive.

5. **The `small_co` name stays, and the collision is accepted.** Functional spec §3.2 asks for
   no real company and a collision check on the two names and domains. The check (2026-10-15)
   found:
   - `wrenfield.app` is live: "Wrenfield", a collection of cozy puzzle and word games, "coming
     soon to Google Play". The fixture's account URL and its `support@`, `hello@` and
     `test+launch@` addresses are on that domain.
   - Wrenfield Finance (wrenfieldfinance.com, a loan consultancy), Wrenfield Capital
     (wrenfieldcap.com, Zurich) and Wrenfield Media (wrenfieldmedia.net, by search results an
     AI lead-capture agency for local service businesses) all exist.
   - No company called "Wrenfield Labs" was found.

   The user kept Wrenfield Labs, `wrenfield.app`, the emails and the account id
   `acct_1Pq7WrnFd3kT9mZa` unchanged.

### Corrections to `components/small_co.md`, recorded here instead of in the spec

- **Row 13 (Anika):** card at 21:29, subscribe at 21:30, setup item at 21:31, billed on its own
  invoice at 21:32 (#14). **Fixed since:** the row stands as written (setup item at 21:29,
  subscribe at 21:30).
- **Row 34 (Victor):** expires at 2026-06-23T15:40Z. That is 23 hours after 16:40 the day before,
  not 16:40.
- **Row 45 (Grace):** as decision 2 above.
- **§6:** 5 products and 8 prices, not 3 and 6. Each setup invoice item mints its own one-off
  price and product, as Stripe does. The 3 catalog products and 6 catalog prices are checked
  separately.
- **§6:** payouts 15–40, not 20–40. A quiet Monday with nothing newly available makes no payout.

### Engineering deviations

- **`CompanyAccount`** (architecture §2.3 had `CompanyAccount(id, object, settings)` holding the
  whole object): it now holds only the company's fields and settings. `install_account` reads the
  seeded row's object, sets the fields over it and writes it back, so the SQL seed is the only
  copy of the sandbox object. It refuses a row that is not the seeded sandbox.
- **"Nothing after NOW"** (functional spec §3.4, replay.md §7):
  - It covers every timestamp-shaped column: TEXT values in canonical ISO form, and integer
    timestamps inside JSON whose key names a time.
  - The fields that point forward are listed once in `_FORWARD`. Beyond the spec's list, it adds
    `billing_cycle_anchor`, `cancel_at`, period ends (`period_end`, a line's or discount's `end`),
    `end_date`, and every date inside a schedule's `phases`.
  - Offenders are reported by `id`.
  - It skips only the FTS virtual tables and their shadow tables.
- **Retries** (replay.md §5): a scheduled retry is tied to its invoice. If that invoice is still
  open but is no longer its subscription's latest, the build raises `FixtureBuildError` rather
  than dropping the remaining retries.
- **`n`** (replay.md §5) counts only boundaries that ran: a boundary abandoned after
  `before_boundary` does not use up a number.
- **`open_account`** raises a `WorldBug` naming the missing field when the stored object lacks
  `settings.dashboard.display_name`, `country` or `default_currency`, instead of a bare
  `KeyError`.
- **`empty`'s description** says what it holds: the schema plus the sandbox account, in live mode.
- **`big_co`'s trades:**
  - `TRADE_WORDS` keeps HVAC, plumbing, electrical and close kin (drain and sewer, mechanical),
    matching functional spec §5.
  - `_trade_coupon` matches whole words, so "Prairie" no longer reads as "air".
  - CRM ids are `006` plus 12 digits everywhere (scripted loose ends used 10).

## Steps

1. **`startup.py`:** `open_account` reads `settings.dashboard.display_name`, `country` and
   `default_currency` through one helper, which raises
   `WorldBug(f"account {id} object has no {path}")`.
2. **`generate.py`:**
   - `empty`'s description becomes the schema plus the sandbox account, live mode, and `empty`
     is rebuilt.
   - Fix the stale lineage-order docstrings.
3. **`accounts.py`:**
   - `CompanyAccount(id, fields, settings, bank_suffix)`, with `bank_account_id` derived from the
     id and the suffix.
   - `object_over(sandbox)` sets the fields over a sandbox object, refusing a field the sandbox
     does not have.
   - `install_account` reads the row's object (it must be the seeded sandbox), sets the fields,
     writes it with `_json.dumps`, then reloads the state.
   - Delete `_SANDBOX`.
4. **`replay/checks.py`:**
   - Rewrite `_nothing_after_now` as described above.
   - Narrow the overlap exclusion. The overlap query compares by `x_seq`, and allows a pair only
     when the earlier invoice was created at the subscription's `trial_end`, the later is the
     first live cycle invoice after it, and both spans are identical.
   - The email guard also matches subdomains of a denied domain.
5. **`replay/subscriptions.py`:**
   - Use `_time.from_unix`.
   - Key retries by invoice id: an open invoice that is no longer its subscription's latest
     raises `FixtureBuildError`.
   - Store `n` only after `engine.renew` runs.
6. **`replay/people.py`:**
   - Grow `FIRST_NAMES` and `LAST_NAMES` to about 200 each, and `CITIES` to about 150 real
     (city, state, ZIP, area code) rows.
   - Weight states per replay.md §4: TX, CA, FL, CO, AZ, GA, NC, OH, PA, NY, WA, IL and TN.
   - Restrict `TRADE_WORDS`.
7. **`big_co/population.py`:** a whole-word `_trade_coupon`. **`big_co/loose_ends.py`:** 15-character
   CRM ids. **`big_co/simulate.py`:** `SWITCH25` becomes 25% off once.
8. **`small_co/`:**
   - `LAUNCH` becomes 50% off once.
   - `redeem_by` comes from `_time.to_unix` (quick fix E).
   - `OneOffPayment(card=...)`, and Grace's beats restated.
   - Delete `_open_dispute` and its `disputes` import.
   - `_Story.create` uses `client.create_customer`, and `Member.email` becomes `str`.
9. **Rebuild** `empty`, `small_co` and `big_co` (`big_co` must stay at 25 MB or less).
10. **`docs/fixtures.md`:**
    - The `small_co` section gains `sql landmark=small_co/...` blocks: `cast` plus one per loose
      end of small_co.md §7.
    - Add a "Known gaps" list linking #12–#15.
    - Correct the coupon text in both sections and big_co's trade names, then run
      `python -m fixtures_src.guide --write`.
11. **Docs:** update `AGENTS.md`, the README and `docs/technical.md` wherever a fact changed (the
    layout comment for `startup.py`, sizes, the coupons).

## Tests

- `tests/test_account_table.py`:
  - P1's quick fixes: the test-mode blank serves the golden object, `encoding="utf-8"`, and
    `ensure_ascii=False`.
  - `test_an_object_missing_a_field_the_state_reads_is_refused`, parametrized over the missing
    fields, each naming the missing path.
- `tests/replay/test_finish_and_checks.py`:
  - `test_each_check_catches_its_violation`, parametrized with one plant per invariant and guard.
    The set of failing checks must be exactly the planted one.
  - Its plants cover: a payout that overdrew; lines against subtotal; overlap; a charge before
    its intent; a refund and a dispute before their charge; a balance transaction before its
    source; customer balance; invoice sequence; status against latest invoice; a TEXT and a JSON
    timestamp after `NOW`; the events window; an unknown card; a webmail subdomain; a cliché
    name.
  - `test_the_schema_refuses_what_two_checks_guard`: `net` against amount minus fee, and
    refunded against captured.
  - `test_nothing_after_now_spares_the_forward_fields`, `test_after_now_names_each_row_by_id`,
    `test_a_trial_end_invoice_may_share_its_span_with_the_next_renewal_only`.
  - `test_cards_match_the_world`, as the review wrote it.
- `tests/replay/test_accounts.py` (new): a company differs from the seeded sandbox only in its own
  fields; a field the sandbox lacks is refused; installing starts from the seeded sandbox.
- `tests/replay/test_clock_and_timeline.py`: `test_payment_intent_ids_and_events_carry_the_instant`.
- `tests/replay/test_drivers.py`:
  - No payout overdraws.
  - `test_automatic_finalization_carries_no_request`, `test_trial_without_card_pauses_at_its_end`,
    `test_declining_card_walks_dunning_to_mark_unpaid`.
  - `test_a_retry_on_a_superseded_invoice_stops_the_build`,
    `test_an_abandoned_boundary_uses_no_number`.
- `tests/test_small_co.py`:
  - Use `pytest.mark.seahaven(fixture="small_co")` with no startup, and conftest's `api_read`.
  - Make `test_account_identity` exact.
  - Add `test_opening_live_is_refused`, `test_new_ids_carry_wrenfield` and
    `test_tools_carry_on`.
  - Replace `test_named_character_states` with one test per character of functional spec §4.4
    (the founder test, the founding renewals, Owen, Priya, Grace, Leo, Sofia and Adaeze, Nadia,
    Jamal, Elena, Chris and Victor).
- `tests/test_guide.py`: `test_every_small_co_loose_end_has_a_landmark`.
- `tests/test_fixtures.py`: quick fix D (the imported world holds exactly its three fixtures).
- `tests/test_population.py`: whole-word trade coupons; one CRM id format; only the market's
  trades.
