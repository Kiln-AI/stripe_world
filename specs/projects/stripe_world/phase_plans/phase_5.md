---
status: complete
---

# Phase 5: Cassette harness

## Overview

The expensive, curated half of the conformance component (`components/conformance.md`):
the cassette format, the recorder (through `stripe-python`, recording-only dependency),
the replayer, the redaction pipeline, and `allowed_differences.py` — the reviewed
statement of every permitted difference. First phase that needs a Stripe test-mode key;
CI never records and never imports `stripe`.

Four conformance scenarios are recorded now — the §12 priority-order scenarios the
currently wired slice (customers) can replay end to end: 02 (deleted-id cursor), 03
(malformed `Stripe-Version`), 08 (pagination boundary + the Phase 3 `limit` flag), 09
(error envelope). Scenarios 01 and 04–07 need resources Phases 6–14 build; each slice's
step-3 probe graduates into its `NN_slug` cassette then.

Live probes this phase settled, recordings win, world corrected in the same phase:

- `limit=0`/negative → live answers 200 with **one** item; `limit>100` → 200 with 100.
  Live clamps silently into [1, 100]; the documented 1–100 contract this world 400s on
  is corrected to clamp (functional spec §6.2 edited).
- A bogus cursor is a **400** `resource_missing` naming the cursor param (not 404), and
  cursor resolution precedes the both-cursors exclusivity check; the exclusivity error
  carries **no `code`**.
- A missing path id on a top-level customers route names `param: "id"` (nested
  sub-resource paths keep the placeholder name — probed both ways). Per-resource
  spelling, pinned per slice from recordings.
- `Invalid integer: abc` — no quotes around the offending value.
- A fresh customer emits `currency: null` and `customer_account: null`; the slice adds
  them.

## Steps

1. **`src/stripeapi/spec/__init__.py`** — `pinned_version()`, read from
   `spec3.min.json`'s `info.version`, so recorder and served version cannot drift.
2. **`tests/conformance/cassette.py`** — `Ref(step, field="id")` (dotted field paths,
   `[N]` indexing), `Step(seq, method, path, path_refs, params, idempotency_key,
   binds_as, recorded_status, recorded_body, recorded_stripe_version)`, `Cassette`;
   `load`/`dump` with canonical formatting (fixed key order, 2-space indent,
   `ensure_ascii=False`, trailing newline); `$ref` encoded in `params` and `path_refs`.
3. **`tests/conformance/redact.py`** — `scrub(step) -> (step, findings)`: API-key and
   `acct_` regex replacement (findings), `request_log_url` normalization (no finding —
   a declared allow-list field), non-reserved-email raise. `RESERVED_EMAIL_DOMAIN =
   "@conformance.stripeapi.invalid"`.
4. **`tools_dev/scenarios/_dsl.py`** — transport-agnostic `Recorder` (`Wire` in,
   `Step` out; optional scrub), `ref()`, `PINNED_VERSION` re-export. No `stripe`
   import. `tools_dev/record.py` — CLI (`--scenario/--all/--list`), key gate
   (`STRIPE_CONFORMANCE_TEST_KEY`, falling back to `.env`'s `STRIPE_SECRET_KEY`;
   `sk_test_`/`rk_test_` only), stripe-backed transport (success via
   `StripeResponse`, error via `StripeError.http_status/.json_body`), one cassette per
   scenario after zero findings, per-scenario independence.
5. **Scenario modules** — `s02_pagination_cursor_against_deleted_id` (3 same-email
   customers, delete the middle, retrieve stub, both cursors against the deleted id),
   `s03_malformed_stripe_version` (GET with `stripe_version_override`), `s08_…`
   (101 same-email customers: `limit=0`→1, `limit=101`→100, forward walk 40/40/21,
   one `ending_before` back-step), `s09_error_envelope_400s` (unknown param, nested
   unknown, non-integer limit, both-cursors with two real ids, bogus cursor). Record
   all four; commit cassettes under `tests/conformance/cassettes/`.
6. **`tests/conformance/replay.py`** — `Violation`, `ConformanceFailure` (every
   violation listed, `.replay-out` scratch bodies), recursive `Ref` resolution against
   this run's bindings, dispatch via `instance.call` on the two HTTP tools,
   scenario-script drift assertion (method+path+params mismatch is its own loud
   failure), structural tree diff through the allow-list.
7. **`tests/conformance/allowed_differences.py`** — `AllowedDifference` (path, reason,
   scenario, predicate), the path matcher (exact / `*` / `**` segments, `fnmatch`
   within a segment so `**.*_at` works), `ALLOWED_DIFFERENCES`
   (`**.id`, `**.created`, `**.*_at`, `**.invoice_prefix`, `**.request_log_url`,
   `**.livemode` predicate, scenario-03 `status`+`body`), `STRUCTURAL_DIFFERENCES`
   prose entries (idempotency retention, search freshness, no version header, no
   version negotiation, scalar stringification at the form boundary, nullable
   annotations not authoritative, `setup_intent.usage` declared reading).
8. **World corrections** (recordings win): `params.py` limit clamping + integer
   message + path-id error-param override + exclusivity moved out of bind;
   `resource.py` cursor-then-exclusivity order in `page()`/`page_embedded()`,
   `ResourceSpec.missing_path_param`, cursor `resource_missing` at 400;
   `stripe_errors.resource_missing(status=...)`; `customers.py`
   `missing_path_param="id"`, `currency` column mapping, `customer_account` null
   constant. Update `test_params.py`, `test_pagination.py`, `test_dispatch.py`.
9. **Harness tests** — cassette canonical round-trip, ref-resolution against this
   run's ids, drift failure, planted-secret redaction, committed-cassette hygiene,
   headers never stored, matcher/scoping/predicate/missing-key, replayer reports all
   violations at once, allow-listed-only pass, recorder key refusal + version pin
   (stubbed transport), registry has no orphans, probe exclusion, the parametrized
   merge gate over every committed conformance cassette.
10. **Housekeeping** — `stripe>=13` dev dependency; `.gitignore`
    `tests/conformance/.replay-out/`; functional spec §6.2 limit correction and §15
    gap-table updates for the two scenarios now recorded.

## Tests

- `test_cassette.py` — canonical byte round-trip (recorded_at masked); load/dump ref
  fidelity; `test_step_ref_resolves_against_this_runs_ids_not_recorded_ids`;
  `test_drifted_scenario_fails_loudly_not_silently`.
- `test_redaction.py` — planted `sk_test_`/`acct_` replaced with findings; non-reserved
  email raises; `request_log_url` normalized silently.
- `test_cassette_hygiene.py` — committed cassettes scan clean; no `Request-Id` /
  `Idempotency-Key` header names anywhere.
- `test_allowlist.py` — exact / `*` / `**` / `*_at` matching incl. list indices;
  scenario scoping does not leak; predicate mode rejects; undeclared missing key is a
  violation.
- `test_replay.py` — two wrong fields both reported; only-allow-listed pass; error
  envelopes diffed like any body.
- `test_recorder.py` — refuses without test-mode key (and on `sk_live_`); pins the
  version by default, override honored (stubbed transport); registry ↔ cassettes have
  no orphans; `probe_*` excluded from conformance collection.
- `test_replay_conformance.py` — the merge gate: every `NN_slug.json` replays green
  against a fresh `empty` instance, no network.
