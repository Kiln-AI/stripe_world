---
status: complete
---

# Implementation Plan: Company Fixtures

## Phases

- [x] **Phase 1: The account table.** The only world change (architecture §1): `006_account.sql`
      with the sandbox seed and `tests/golden/sandbox_account.json`, `startup.open_account` and the
      mode rule, the version bump with `uv lock`, `empty` rebuilt, `test_empty_fixture.py`'s
      inventory, `tests/test_account_table.py`, and the doc touches (the README's `empty` example,
      table counts). Nothing else under `src/` changes.
- [ ] **Phase 2: The replay engine.** `fixtures_src/` becomes a package; `replay/` (clock,
      timeline, client, engine, cards, people, subscription and payout drivers, finish, checks) with
      `tests/replay/`; `generate.py`'s `STARTUP` plumbing. Ends with the benchmark: 200 customers
      over 12 months, its build time and file size, and the projection for `big_co` recorded in the
      phase plan. **If the projection breaks the `big_co` budget, stop and raise it with the user
      before Phase 4.**
- [ ] **Phase 3: `small_co`.** `accounts.WRENFIELD`, the catalog, the 45-member cast and its story,
      the build and the frozen fixture, `tests/test_small_co.py`, the determinism test, the guide
      tooling (`fixtures_src/guide.py`, `tests/test_guide.py`) and the `small_co` half of
      `docs/fixtures.md`. A web check that "Wrenfield" and `wrenfield.app` name no real company.
- [ ] **Phase 4: `big_co`.** `accounts.OSTRANDER`, the shape and catalog, the population draw,
      the lives, the scripted loose ends, the build and the frozen fixture, `tests/test_population.py`,
      `tests/test_big_co.py`, the fork-cost test, the `big_co` half of `docs/fixtures.md`, and the
      remaining doc lines (`AGENTS.md`'s fixture line, the README's "ready-made fixtures" line,
      `docs/technical.md`'s fixtures section). A web check that "Ostrander Systems" and
      `ostranderfieldops.com` name no real company.
