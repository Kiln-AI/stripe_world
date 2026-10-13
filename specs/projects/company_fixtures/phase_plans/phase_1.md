---
status: draft
---

# Phase 1: The account table

## Overview

The account moves out of a hard-coded constant in `startup.py` and into a one-row `account` table
that also records the account's mode (architecture §1, functional spec §2). A fixture carries rows
and nothing else, so this is what lets a company fixture be a different account, in test mode.
The schema seeds today's sandbox account byte for byte, with no mode yet; the startup hook reads
the row into `ctx.state["account"]` in today's shape, stamps a blank instance's mode, and refuses a
fixture opened in the other mode. Every reader of `ctx.state["account"]` is unchanged.

This is the only world change of the project. The schema hash changes, so `empty` is rebuilt (it
comes out live), the version goes to 0.2.0, and the docs' table count goes from 24 to 25.

## Steps

1. **Capture the golden object.** Before touching `startup.py`, write
   `_json.dumps(startup.account_object())` to `tests/golden/sandbox_account.json` (no trailing
   newline, so the file is exactly the text).

2. **`src/seahaven_stripe_world/schema/006_account.sql`.** The table of architecture §1.1:

   ```sql
   CREATE TABLE account (
       id       TEXT    PRIMARY KEY,
       livemode INTEGER CHECK (livemode IS NULL OR livemode IN (0, 1)),
       object   TEXT    NOT NULL CHECK (json_valid(object) AND json_type(object) = 'object'),
       settings TEXT    NOT NULL DEFAULT '{}'
                        CHECK (json_valid(settings) AND json_type(settings) = 'object')
   ) STRICT;
   INSERT INTO account (id, livemode, object, settings)
   VALUES ('acct_0NGvb3Cz7hWRKLEp', NULL, json('<the golden object, pretty-printed>'), '{}');
   ```

   The object literal is the golden text indented for review, wrapped in SQLite's `json()`, which
   minifies it back to exactly the bytes `_json.dumps` wrote (sorted keys, no whitespace).
   `test_seed_is_todays_sandbox_account` pins the stored text against the golden file.

3. **`src/seahaven_stripe_world/startup.py`.** Delete `account_object()` and `_CREATED`; keep
   `ACCOUNT_ID` (tests import it). Add:

   ```python
   _ACCOUNT_ID = re.compile(r"acct_[A-Za-z0-9]{16}")
   _SETTINGS_KEYS = frozenset({"ledger", "dunning"})

   def open_account(ctx: seahaven.Ctx, *, livemode: bool | None) -> dict[str, Any]: ...

   @world.instance_startup
   def _startup(ctx: seahaven.Ctx, *, livemode: bool | None = None, **_kwargs: object) -> None:
       ctx.state["account"] = open_account(ctx, livemode=livemode)
   ```

   `open_account` follows architecture §1.2 in order: exactly one row (`WorldBug` naming an empty
   table or the row count); the id is `acct_` plus 16 alphanumerics (`fullmatch`); the object's
   `id` equals the row's; settings keys are within `{"dunning", "ledger"}`; then the mode rule
   (NULL → the asked mode or live, stamped with `UPDATE account SET livemode = ? WHERE id = ?`;
   set → that mode, `WorldBug` naming the account's mode and `livemode=<asked>` on a mismatch).
   Returns a new plain dict `{"id", "livemode", "name", "country", "default_currency", "object"}`
   updated with the settings. The module docstring describes the row as the source.

4. **Version 0.1.0 → 0.2.0** in `world.py`, `pyproject.toml` and `openenv.yaml`; `uv lock`.

5. **Rebuild `empty`**: `rm -rf fixtures/empty && uv run python fixtures_src/generate.py empty`.

6. **Docs.** README: the `empty` example becomes `await env.reset(fixture="empty", seed=seed)`,
   its comment no longer saying test mode; "24 tables" → 25 in `README.md`, `AGENTS.md` and
   `docs/technical.md` (whose layout line also becomes `001-006`).

## Tests

`tests/test_empty_fixture.py`:

- `test_an_instance_of_empty_has_the_schema_and_no_rows`: the inventory gains `account`.
- `test_the_account_row_is_the_live_sandbox`: one row, the sandbox id, `livemode = 1`.

`tests/test_account_table.py` (real instances; `setup_sql` shapes the row; a `copy.copy(world)`
with its own `fixtures_dir` where a freeze is needed):

- `test_seed_is_todays_sandbox_account`: a fresh schema (`seahaven.db.build_blank` in memory) seeds
  the golden object text byte for byte, `settings = '{}'`, `livemode` NULL.
- `test_blank_defaults_live_and_stamps`: no `livemode` → the golden object from
  `get_stripe_account_info`, live from `list_available_accounts_or_orgs`, row stamped `1`.
- `test_blank_opens_test_and_stamps`: `livemode=False` → test mode, row stamped `0`.
- `test_empty_opens_live`: `world.instance("empty")` is live.
- `test_empty_refuses_test_mode`: `WorldBug` naming live-mode data and `livemode=False`.
- `test_matching_mode_is_accepted`: `empty` with `livemode=True` opens live.
- `test_frozen_test_mode_reopens_in_test_mode`: a frozen blank test-mode instance reopens in test
  mode with no `livemode`, and refuses `livemode=True` naming test-mode data.
- `test_missing_row_refused`: `DELETE FROM account` → `WorldBug` naming an empty table.
- `test_second_row_refused`: a second valid row → `WorldBug` naming 2 rows.
- `test_malformed_id_refused` (parametrized: too short, too long, a non-alphanumeric), with the
  object's id moved to match → `WorldBug`.
- `test_object_id_mismatch_refused`: only `id` changed → `WorldBug` naming both ids.
- `test_unknown_settings_key_refused`: `settings = '{"fees": {}}'` → `WorldBug` naming `fees`.
- `test_settings_reach_ledger_and_dunning`: `ledger.dispute_received_fee = 2000` and
  `dunning.end_behavior = 'mark_unpaid'` reach `ledger_spec(ctx)` and `dunning.policy(ctx)` inside
  `inst.bulk()`.
- `test_ids_carry_the_rows_account_fragment`: with another valid account installed by `setup_sql`,
  a payment intent created through `stripe_api_write` carries that account's 10-character fragment.

The existing suites (`test_fixtures.py`'s byte comparison of `empty` included) run unchanged.
