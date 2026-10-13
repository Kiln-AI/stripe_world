---
status: complete
---

# Architecture: Company Fixtures

Two kinds of work, kept apart on purpose:

1. **One world change**: the account moves into a one-row table that also records the account's
   mode (§1). It is the only edit under `src/`.
2. **Fixture generation**: code under `fixtures_src/` that uses the world as a client. It calls the
   world's API dispatcher and the engine functions the world provides for fixture generators, at
   historical instants, inside one `inst.bulk()` transaction (§2). It changes no world code.

Verification (§3) runs the same checks at build time and in the test suite.

| Document | Covers |
|---|---|
| this file | the account table, the generator's layout and contracts, verification, testing, risks |
| [`components/replay.md`](components/replay.md) | the replay engine shared by both companies: clock, timeline, client, subscription and payout drivers, finishing steps, checks |
| [`components/small_co.md`](components/small_co.md) | Wrenfield Labs: the cast file, its beat vocabulary, the full cast |
| [`components/big_co.md`](components/big_co.md) | Ostrander Systems: the shape, the population draw, the lifecycle simulation, the scripted loose ends |

## 1. The account table (Phase 1)

### 1.1 Schema: `src/seahaven_stripe_world/schema/006_account.sql`

```sql
-- The instance's Stripe account: one row (company_fixtures functional spec §2).
-- `livemode` is NULL until the account is created: a blank instance's startup hook
-- stamps the mode it was opened in, and freezing keeps it. A fixture's data (its
-- cards, and the livemode inside stored JSON) was made in that mode.
-- `object` is the account object as `get_stripe_account_info` returns it, a wire
-- snapshot like events.data (Unix-second `created`). `settings` holds the
-- `ledger` and `dunning` overrides the world already reads from ctx.state.
CREATE TABLE account (
    id       TEXT    PRIMARY KEY,
    livemode INTEGER CHECK (livemode IS NULL OR livemode IN (0, 1)),
    object   TEXT    NOT NULL CHECK (json_valid(object) AND json_type(object) = 'object'),
    settings TEXT    NOT NULL DEFAULT '{}'
                     CHECK (json_valid(settings) AND json_type(settings) = 'object')
) STRICT;

INSERT INTO account (id, livemode, object, settings)
VALUES ('acct_0NGvb3Cz7hWRKLEp', NULL, '<the sandbox object>', '{}');
```

- `<the sandbox object>` is exactly `_json.dumps(startup.account_object())` as the code stands
  before this change, captured once. The same text is committed as
  `tests/golden/sandbox_account.json`, so the test of §1.4 proves the seeded account is
  byte-for-byte today's. After capture, `account_object()` and `_CREATED` are deleted from
  `startup.py`: the SQL is the one source.
- The table carries no `x_seq` and no counter: it is not listable, and no route reads or writes it.
- `account` is a tracked table, but nothing in a session writes it, so it never appears in a change
  log. `setup_sql` can still change it per episode.
- Adding the file changes the schema hash, so `empty` is rebuilt and committed in the same phase.

### 1.2 `startup.py`

```python
ACCOUNT_ID = "acct_0NGvb3Cz7hWRKLEp"   # the sandbox account 006_account.sql seeds
_ACCOUNT_ID = re.compile(r"acct_[A-Za-z0-9]{16}")
_SETTINGS_KEYS = frozenset({"ledger", "dunning"})


def open_account(ctx: seahaven.Ctx, *, livemode: bool | None) -> dict[str, Any]:
    """Read the account row into the `ctx.state["account"]` shape, settling its mode."""


@world.instance_startup
def _startup(ctx: seahaven.Ctx, *, livemode: bool | None = None, **_kwargs: object) -> None:
    ctx.state["account"] = open_account(ctx, livemode=livemode)
```

`open_account`, in order:

1. `rows = ctx.db.rows("SELECT id, livemode, object, settings FROM account")`. Zero rows raises
   `WorldBug("the account table is empty: an instance needs exactly one account row")`; more than
   one raises `WorldBug(f"the account table holds {n} rows: an instance is exactly one account")`.
2. The id must match `_ACCOUNT_ID`, else `WorldBug(f"account id {id!r} is not acct_ plus 16
   alphanumerics")`. The loaded object's `id` must equal the row's, else `WorldBug(f"account row
   {id!r} holds the object of {obj_id!r}")`. Settings keys outside `_SETTINGS_KEYS` raise
   `WorldBug(f"account settings key {key!r} is not one of ['dunning', 'ledger']")`.
3. The mode, per functional spec §2.1:
   - stored `NULL`: `mode = livemode if livemode is not None else True`, then
     `UPDATE account SET livemode = ? WHERE id = ?`;
   - stored `0`/`1`: `mode = bool(stored)`; if `livemode is not None and livemode != mode`, raise
     `WorldBug(f"account {id} holds {'live' if mode else 'test'}-mode data and cannot be opened with
     livemode={livemode}; omit livemode, or open a blank instance")`.
4. Return a **new, mutable** dict, the shape the hook builds today plus the settings:
   `{"id", "livemode": mode, "name": object["settings"]["dashboard"]["display_name"], "country",
   "default_currency", "object"}`, then `state.update(settings)` (so `ledger` and `dunning` appear
   only when the row carries them). Tests mutate `ctx.state["account"]["dunning"]`, so it must stay
   a plain dict.

`open_account` is public because the generator calls it after installing a company's row (§2.3).
`**_kwargs` stays as it is today; changing it is out of scope.

### 1.3 What else Phase 1 touches

- **Version 0.1.0 → 0.2.0** in `world.py`, `pyproject.toml` and `openenv.yaml`, then `uv lock`
  (CI syncs with `--locked`).
- **`empty` rebuilt**: delete `fixtures/empty/`, run `python fixtures_src/generate.py empty`, commit.
  Its row is stamped live by the default startup, so `empty` is a live account.
- **`tests/test_empty_fixture.py`**: the table inventory gains `account`; `account` is excluded from
  the "tracked tables are empty" loop and asserted to hold one row with `livemode = 1` and the
  sandbox id.
- **Docs**: the README's `empty` example becomes `await env.reset(fixture="empty", seed=seed)` with
  its comment no longer saying test mode; the table count goes from 24 to 25 in `AGENTS.md`, the
  README and `docs/technical.md`.
- Nothing else under `src/` changes. `customers.ACCOUNT_DEFAULT_CURRENCY`, the invoice serializer's
  account fallbacks and every other reader stay as they are.

### 1.4 Tests: `tests/test_account_table.py`

All through real instances of the real world (`world.instance(...)`), using `setup_sql` to shape the
row and a temporary `copy.copy(world)` with its own `fixtures_dir` where a freeze is needed.

| Test | Asserts |
|---|---|
| `test_seed_is_todays_sandbox_account` | the seeded `object` text equals `tests/golden/sandbox_account.json` byte for byte; `settings` is `{}`; `livemode` is NULL in a fresh schema |
| `test_blank_defaults_live_and_stamps` | a blank instance with no `livemode` serves the golden object, `livemode` true, and its row reads `livemode = 1` |
| `test_blank_opens_test_and_stamps` | `startup={"livemode": False}` serves `livemode` false and stamps `0` |
| `test_empty_opens_live` | `world.instance("empty")` is live |
| `test_empty_refuses_test_mode` | `world.instance("empty", startup={"livemode": False})` raises `WorldBug` naming live and `livemode=False` |
| `test_matching_mode_is_accepted` | `world.instance("empty", startup={"livemode": True})` works |
| `test_frozen_test_mode_reopens_in_test_mode` | freeze a blank test-mode instance, reopen with no `livemode`: test mode; reopen with `True`: `WorldBug` |
| `test_missing_row_refused` | `setup_sql="DELETE FROM account"` → `WorldBug` naming an empty table |
| `test_second_row_refused` | `setup_sql` inserting a second row → `WorldBug` naming 2 rows |
| `test_malformed_id_refused` | `setup_sql` setting `id = 'acct_short'` (and the object's id to match) → `WorldBug` |
| `test_object_id_mismatch_refused` | `setup_sql` changing only `id` → `WorldBug` |
| `test_unknown_settings_key_refused` | `settings = '{"fees": {}}'` → `WorldBug` |
| `test_settings_reach_ledger_and_dunning` | `settings` with `ledger.dispute_received_fee = 2000` and `dunning.end_behavior = 'mark_unpaid'` → `ledger_spec(ctx)` and `dunning.policy(ctx)` read them (inside `inst.bulk()`) |
| `test_ids_carry_the_rows_account_fragment` | after `setup_sql` installs another valid account, a minted `pi_` id carries that account's fragment |

The existing suites run unchanged apart from `test_empty_fixture.py`.

## 2. The generator

### 2.1 Layout

```
fixtures_src/
  __init__.py
  generate.py           NOW, BUILDERS, STARTUP, DESCRIPTIONS, build(), main()
  accounts.py           the two company account rows: id, object, settings
  replay/               the replay engine (components/replay.md)
    __init__.py
    clock.py            at(ctx, iso): a context reading a historical instant
    timeline.py         Timeline, Step, FixtureBuildError
    client.py           Client: the API dispatcher at an instant, and its helpers
    engine.py           renew, expire, fail_retry, arrive: the world's engine functions
    cards.py            the test cards the fixtures use, by role
    people.py           deterministic names, invented domains, addresses, phones
    subscriptions.py    the renewal / trial / dunning / expiry driver
    payouts.py          the payout cadence driver
    finish.py           refresh_balance_statuses, trim_events
    checks.py           invariants, realism guards, expectations
  small_co/             Wrenfield Labs (components/small_co.md)
    __init__.py         build(inst)
    catalog.py          products, prices, coupons
    cast.py             the hand-written cast
    story.py            beats → timeline steps
  big_co/               Ostrander Systems (components/big_co.md)
    __init__.py         build(inst)
    shape.py            the population knobs and the catalog
    population.py       draw_population(rng, shape) -> list[Prospect], pure
    simulate.py         Prospect → timeline steps
    loose_ends.py       the scripted guarantees of functional spec §5.7
  guide.py              python -m fixtures_src.guide [--write]
```

`fixtures_src` becomes a regular package (it is on `pythonpath` through pytest's `pythonpath = ["."]`
and through the project root for `python -m`). `ruff` and `ty` already include it.

### 2.2 `generate.py`

```python
NOW = "2026-09-01T14:00:00.000Z"            # unchanged

BUILDERS = {"empty": empty, "small_co": small_co.build, "big_co": big_co.build}
STARTUP = {"empty": {}, "small_co": {"livemode": False}, "big_co": {"livemode": False}}
DESCRIPTIONS = {"empty": ..., "small_co": ..., "big_co": ...}   # the paragraphs of spec §3.6


def build(fixture_id: str, *, world: seahaven.World | None = None) -> seahaven.Fixture:
    into = world if world is not None else _package_world()
    with into.instance(None, now=NOW, clock_mode="fixed", startup=STARTUP[fixture_id]) as inst:
        BUILDERS[fixture_id](inst)
        return inst.freeze(fixture_id, DESCRIPTIONS[fixture_id])
```

`main()` keeps its contract; its default order is `empty`, `small_co`, `big_co`. The module
docstring stops recommending `seahaven fixture freeze --run` for the company fixtures: that path runs
without the test-mode startup and without a fixed clock, so it cannot reproduce them. The rebuild is
`rm -rf fixtures/<id> && uv run python fixtures_src/generate.py <id>`.

### 2.3 Installing a company's account

`fixtures_src/accounts.py` holds two `CompanyAccount(id, object, settings)` values (the field values
of functional spec §2.2). `object` is the sandbox golden object with those fields changed: the
external account list holds one test-mode bank account (`ba_` id derived from the account id,
`bank_name` "STRIPE TEST BANK", `last4` "6789", `routing_number` "110000000"); `settings.payouts.schedule`
is `{"delay_days": 2, "interval": "manual"}`.

The builder's first act inside `inst.bulk()`:

```python
def install_account(ctx: seahaven.Ctx, account: CompanyAccount) -> None:
    ctx.db.execute(
        "UPDATE account SET id = ?, object = ?, settings = ?",
        account.id, _json.dumps(account.object), _json.dumps(account.settings),
    )
    ctx.state["account"] = startup.open_account(ctx, livemode=None)
```

`livemode` is left as startup stamped it (`0`, because `STARTUP` opens the instance in test mode).
This is the one row the builder writes directly: it is the fixture's definition, not history.

### 2.4 How history is made

Detailed in `components/replay.md`; the contract is:

- **One clock trick.** `replay.clock.at(ctx, iso)` is `dataclasses.replace(ctx,
  clock=seahaven.Clock.from_iso(iso))`: the same database, id stream and state, with a fixed clock
  reading `iso`. Seahaven builds contexts the same way (`Ctx.with_call`). No world code and no SQL in
  the world reads time any other way.
- **Two kinds of step.** An API step calls `dispatch(ctx_at, method, path, params,
  idempotency_key=None)`, after `stripe_envelope.mint_request(ctx_at, None)` gives it a request id,
  as the tool and HTTP faces do. An engine step calls one of `advance_cycle`, `expire_incomplete`,
  `dunning.record_failed_attempt` or `ledger.settle_payout`, with `ctx.state["_request"]` set to
  `{"id": None, "idempotency_key": None}`: Stripe's own automatic actions carry no request.
- **Nothing else writes**, except `install_account` and the two finishing steps
  (`refresh_balance_statuses`, `trim_events`).
- **Time order.** Steps run in instant order, ties in the order they were scheduled, so `x_seq`
  follows `created` everywhere. A step scheduled after `NOW` is dropped: it has not happened yet.
- **Reads** (to decide the next step) go through `ctx.db` with deterministic `ORDER BY`, or through
  the API.

### 2.5 Determinism

- Builds run in a blank instance opened with `now=NOW, clock_mode="fixed"` and no `seed`, so the
  instance seed is the world's own derived seed on every build.
- Each builder draws `rng = random.Random(ctx.ids.random.getrandbits(64))` once, first thing, and
  uses only `rng` for its own choices. Ids keep drawing from `ctx.ids`, as world code does.
- No iteration over a `set` or a `dict` built from unordered input; no `hash()`; every SQL read that
  feeds a decision has an `ORDER BY` ending in `id` or `x_seq`.
- No wall clock anywhere. Every instant is computed from fixed literals and the world's
  `invoicing.add_interval`.
- JSON the builder writes (the account row only) goes through `_json.dumps`.
- The proof is the determinism test: one fresh build equals the committed bytes.

### 2.6 Error handling

- Any exception inside a step is re-raised as `FixtureBuildError(f"{step.label} at {step.at}: {cause}")`
  with the cause chained. A `StripeApiError`, or an API response whose status the step did not
  expect, is an error. A 402 is expected only where a step says so (a declining card's charge).
- An error aborts the `bulk()` transaction, so nothing is written, and `freeze` never runs, so no
  fixture directory is left behind.
- `checks.check_fixture` runs after `bulk()` and before `freeze`; a failed check raises
  `AssertionError` naming the check and up to five offending rows.
- If a step reveals that the world does not do what a story needs, the build stops; the story is
  changed to do without it (functional spec §3.3), or the gap is raised with the user. The world is
  never patched from here.

## 3. Verification

### 3.1 Checks (`replay/checks.py`)

One module, imported by the builders and by the tests, so build-time and test-time checks cannot
drift. `check_fixture(db, *, now: str, expect: Expectations)` runs, against a read-only handle
(`inst.inspect()`):

- the invariants of functional spec §3.4 (SQL listed in `components/replay.md` §7);
- the realism guards of functional spec §3.2: every card payment method's
  `json_extract(rail, '$.fingerprint')` is the fingerprint of a number in `replay.cards.ALL`
  (computed with `magic_cards.fingerprint`); no customer email ends in a denied domain (`gmail.com`,
  `googlemail.com`, `yahoo.com`, `outlook.com`, `hotmail.com`, `live.com`, `icloud.com`, `me.com`,
  `aol.com`, `proton.me`, `example.com`, `example.org`, `example.net`); no customer or product name
  contains a denied cliché (`acme`, `contoso`, `northwind`, `initech`, `globex`, `hooli`,
  `umbrella corp`, `wayne enterprises`, `stark industries`), case-insensitive;
- the fixture's own `Expectations`: exact counts for `small_co`, ranges for `big_co`
  (functional spec §4.3, §5.8), and the presence of every loose end.

### 3.2 The guide (`docs/fixtures.md`, `fixtures_src/guide.py`)

The guide is prose plus **landmark blocks**: a fenced SQL block whose info string is
`sql landmark=<fixture>/<name>`, followed immediately by a fenced block with info string
`json rows` holding the rows the query returns, as a JSON array of objects.

````markdown
```sql landmark=small_co/open_dispute
SELECT d.id, d.status, d.reason, c.customer
FROM disputes d JOIN charges c ON c.id = d.charge
WHERE d.status = 'needs_response'
ORDER BY d.id
```

```json rows
[{"id": "du_…", "status": "needs_response", "reason": "fraudulent", "customer": "cus_…"}]
```
````

- `python -m fixtures_src.guide` checks every landmark against the committed fixtures and exits 1
  on a difference; `--write` rewrites the `json rows` blocks in place from the fixtures (run after a
  rebuild, then review the diff).
- `tests/test_guide.py` runs the check. Every query must have an `ORDER BY`.
- Landmarks are required for every loose end of functional spec §4.6 and §5.7, and for the
  `small_co` cast list.

### 3.3 Tests

| File | Phase | What |
|---|---|---|
| `tests/test_account_table.py` | 1 | §1.4 |
| `tests/test_empty_fixture.py` | 1 | the inventory change |
| `tests/test_fixtures.py` | 1, 3, 4 | the recipe: `empty`, then `small_co` and `big_co`, each rebuilt into a temporary world and compared with the committed `state.sqlite` by sha256; `main()` order and unknown-id handling |
| `tests/replay/test_clock.py` | 2 | `at()` reads the instant; ids, `created` columns and events written through it carry it; writes land in the bulk transaction |
| `tests/replay/test_timeline.py` | 2 | order, ties, dropping steps after `until`, refusing to schedule into the past, error wrapping, request context per step kind |
| `tests/replay/test_drivers.py` | 2 | on a blank test-mode instance: a monthly subscription renews to `NOW`; a trial ends; a declining card enters dunning and the retries walk to the end-of-schedule outcome; an incomplete signup expires; payouts settle only when arrived |
| `tests/replay/test_finish.py` | 2 | balance statuses refreshed; events before the cutoff deleted, none after |
| `tests/replay/test_checks.py` | 2 | each invariant and guard catches a planted violation (in a throwaway instance, via `setup_sql`) |
| `tests/test_small_co.py` | 3 | the account identity through the MCP tools in test mode; the exact cast counts; each named character's state; checks clean; "the tools carry on" (spec §6) |
| `tests/test_big_co.py` | 4 | identity; the expected ranges; all eight statuses; each loose end; checks clean; "the tools carry on"; file size |
| `tests/test_fork_cost.py` | 4 | 30 forks of each company fixture, median and max recorded; asserted under ceilings set from the Phase 4 measurement |
| `tests/test_guide.py` | 3, 4 | every landmark matches |

The conftest helpers `api_read`, `api_write`, `api_search` and `api_details` gain keyword
`stripe_context` and `livemode` parameters defaulting to today's values, so company-fixture tests
can address their account. Company-fixture tests use
`@pytest.mark.seahaven(fixture="small_co")` (and `big_co`) with no startup: the row's mode applies.

**CI cost.** The determinism tests rebuild both fixtures on every run. `small_co` takes seconds;
`big_co` is budgeted at under 3 minutes (functional spec §3.5).

## 4. Risks and how each is contained

| Risk | Containment |
|---|---|
| The clock trick misbehaves somewhere in world code | Phase 2 opens with `test_clock.py` and the driver tests on a blank instance before any company exists. If it fails, the fallback is a day-granular chain (freeze, reopen with a later `now=`), raised with the user first because it is slower and coarser |
| `big_co` builds too slowly | Phase 2 measures a 200-customer, 12-month benchmark and projects `big_co`; Phase 4 measures the real build. Over budget: the population comes down, or the user decides |
| `big_co` is too large | Only 30 days of events are kept; Phase 4 measures the file; over 25 MB: the population comes down |
| A story needs something the world does not do | The build stops (§2.6); the story does without it, or the user decides. No world patch |
| A test card behaves differently than a story assumes | Each story's state at `NOW` is asserted in `test_small_co.py` / `test_big_co.py` and by the guide |
