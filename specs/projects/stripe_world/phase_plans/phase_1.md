---
status: complete
---

# Phase 1: Skeleton

## Overview

Scaffold the Seahaven world this project lives in, with the shared modules every later
phase imports: the `World` object, the two error systems (Seahaven authoring errors and
the Stripe error envelope), the error-handler middleware, and the `_ids` / `_time` /
`_json` helpers. Ends with a green `pytest`, a clean `seahaven check`, and a committed
`empty` fixture — on a world that registers no tools and creates no tables.

## Steps

1. **Scaffold.** Render `seahaven new stripeapi` (via the vendored framework) into a
   temp directory and move its files into the repository root — this repo *is* the world
   checkout (`pyproject.toml`, `src/stripeapi/`, `tests/`, `fixtures/`, `fixtures_src/`
   at the root, beside `specs/`, `research/`, `vendor/`). Merge the scaffold's
   `.gitignore` with the existing one. Delete the scaffold's sample `tools/items.py`,
   its tests, and `schema/001_items.sql`.

2. **`pyproject.toml`.** Distribution name `seahaven-stripe-world`, module `stripeapi`
   (functional spec §1). Dependency on `seahaven~=0.0` resolved to the vendored
   checkout via `[tool.uv.sources] seahaven = { path = "vendor/Seahaven", editable = true }`.
   Pin `pydantic==2.12.3` via `[tool.uv] override-dependencies` with a comment pointing
   at `SEAHAVEN_FINDINGS.md` Entry 1 (a plain constraint conflicts with seahaven's own
   `pydantic>=2.13.5` floor, so an override is the mechanism that pins). Dev group with
   `pytest`, `ruff`, `ty`; ruff/ty configured to exclude `specs/`, `research/`,
   `vendor/`, `fixtures/`. Commit `uv.lock`.

3. **Root `AGENTS.md`.** The scaffold's framework pointers plus this world's own rules:
   check commands (`uv run ruff format --check`, `uv run ruff check`, `uv run ty check`,
   `uv run pytest`, `uv run seahaven check`), id discipline (`_ids.stripe_id`, never
   `ctx.ids.uuid()`), time discipline (`ctx.clock`, `_time.py`), the two error systems,
   JSON columns through `_json.dumps`, fixtures immutable.

4. **`src/stripeapi/world.py`.** `World(name="stripeapi", version="0.1.0", schema=
   seahaven.sql_files(__package__, "schema"), description=…, state_format=
   "seahaven.state/1")`. `untracked_tables` is added in the phase that creates
   `idempotency_keys` and `counters`, not now.

5. **`schema/000_placeholder.sql`.** `sql_files` refuses a directory with no `*.sql`,
   and Phase 1 has no tables: one comment-only file, replaced by `001_core.sql` in
   Phase 6.

6. **`errors.py`.** Authoring errors only (architecture §7): `InvalidInput` (with
   `from_violations`, the shape the error handler restates Seahaven's `ArgumentError`
   into) and `Internal`. No `NotFound` — a missing Stripe object is a Stripe
   `resource_missing`, not a Seahaven error.

7. **`stripe_errors.py`** per `components/cross_cutting.md` §2.1, minus `declined()`
   (its `decline_code` validation reads `spec/enums.py`, which Phase 2 generates;
   `declined()` lands with the phase that first needs it): `StripeErrorType` (the four
   wire values), `StripeApiError` with `status` / `pre_execution` / `envelope()`
   (omits every `None` field; `doc_url` = `https://stripe.com/docs/error-codes/<code
   with underscores hyphenated>` when `code` is set and the type is
   `invalid_request_error` or `card_error`), and the constructors `invalid_request`,
   `resource_missing`, `unknown_parameter`, `missing_parameter`, `cannot_expand`,
   `idempotency_mismatch`, `idempotency_key_in_use`, `internal` — exact messages and
   status/type/code pairings from cross_cutting §2.1/§3.5.2 and the research quotes.

8. **`middleware/error_handler.py`.** The scaffold's shape: `ArgumentError` →
   `InvalidInput.from_violations`; `DbError` → logged + `Internal`; this world's
   `ToolError`s re-raised; `WorldBug` re-raised unchanged; anything else → `Internal`.
   No SQL door tools yet.

9. **`_ids.py`** per `components/data_model.md` §2: `ID_ALPHABET` (62 chars),
   `STRIPE_ID_PREFIXES` (26 objects), stub prefixes (`ba_`, `card_`, `mandate_`,
   `setatt_`) and the `req_` request id, `stripe_id(ctx, prefix)` (24-char suffix from
   `ctx.ids.random`, `WorldBug` on an unknown prefix, never `ctx.ids.uuid()`),
   `coupon_id(ctx, supplied)` (caller-supplied or 8 uppercase alphanumerics).

10. **`_time.py`.** `ISO_PATTERN`, `to_unix(iso) -> int`, `from_unix(seconds) -> str`
    — the only ISO↔Unix-second conversion in the package.

11. **`_json.py`.** `dumps` (`sort_keys=True, separators=(",", ":"), ensure_ascii=False`)
    and a None-preserving `loads` — the only writer of every JSON `TEXT` column.

12. **`fixtures_src/generate.py`.** The scaffold's module with `NOW =
    "2026-09-01T14:00:00.000Z"` (the instant `components/fixtures.md` fixes), the
    `empty` builder, `BUILDERS`/`DESCRIPTIONS` (empty only), `build()` freezing from a
    blank instance at `NOW`, and a `main(argv)` so `python fixtures_src/generate.py`
    re-runs the committed recipe. Freeze the committed `empty` fixture.

13. **Tests** (below), then `uv sync`, `uv run pytest`, `uv run seahaven check`, ruff
    and ty, all clean.

## Tests

- `test_package.py` — importing `stripeapi` yields the `World` named `stripeapi` with
  `state_format="seahaven.state/1"` and exactly one middleware, the error handler.
- `test_error_handler.py` — ProjectTracker's probe-world pattern: this world's real
  middleware on a throwaway world with throwaway tools, driven through
  `instance.call`. `ToolError` reaches the agent unchanged; `ArgumentError` restated as
  `INVALID_INPUT`; `DbError` → `INTERNAL` (and logged); `WorldBug` re-raised; an
  unexpected exception → `INTERNAL`.
- `test_stripe_errors.py` — the `type` enum is exactly the four wire values; `envelope`
  omits `None` fields; status is carried not derived (400 and 404 both
  `invalid_request_error`); `doc_url` hyphenates the code and is absent without one;
  each constructor's status/type/code/message, including the two exact idempotency
  strings and the three `cannot_expand` forms; `pre_execution` flags.
- `test_ids.py` — `stripe_id` shape (`prefix` + 24 chars from `ID_ALPHABET`);
  determinism (same seed twice → identical ids, through a probe tool); unknown prefix
  → `WorldBug`; `coupon_id` honours a supplied id and mints 8 uppercase alphanumerics;
  every prefix in the table is unique.
- `test_time.py` — `to_unix`/`from_unix` round-trip; known instants; `ISO_PATTERN`
  matches the canonical form and rejects others.
- `test_json.py` — `dumps` output is sorted, compact, non-ASCII-passing, and
  byte-identical across runs; `loads(None) is None`.
- `test_fixtures.py` — the scaffold's recipe tests: `build("empty")` into a temp
  directory works and freezes at `NOW`; `_package_world()` is this package's world.
- `test_empty_fixture.py` — the committed `empty` fixture exists, is frozen at `NOW`,
  and an instance made from it has no tables and no rows.
