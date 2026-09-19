---
status: complete
---

# Phase 2: The spec pipeline

## Overview

`tools_dev/prune_spec.py` and the four committed artifacts it generates under
`src/stripeapi/spec/` — `spec3.min.json`, `expandable.py`, `enums.py`,
`event_types.py` — plus the drift tests that pin them. Built before any resource
because the schema's enum `CHECK`s and every later conformance test read from
these artifacts (implementation plan, Phase 2; architecture.md §5.2;
components/discovery.md).

The pruned spec is generated from the pinned, git-ignored
`research/stripe-openapi/spec3.json` (fetched per `research/MANIFEST.md`,
sha256 verified `f0e0fc8f…`, `info.version` `2026-08-26.dahlia`), scoped to the
routed operation set, and trimmed by the two passes discovery.md §2–§3 specify:
the scope-boundary stoplist and the payment-rail fan-out trim.

Phase-ordering note: discovery.md makes `dispatch/routes.py` the pruner's first
input, and that table is declared here, as pure data — 148 `Route(method,
pattern, op_id)` values bootstrapped mechanically from the spec. Phase 3 attaches
handlers and ParamSpecs to it. Request-body filtering to `ParamSpec` allowlists
(discovery.md §8a) is therefore deferred to Phase 3: Phase 2's `spec3.min.json`
carries request bodies verbatim (trimmed keys, HTML stripped), and the drift
test pins that shape until params.py exists.

## Steps

1. **Fetch and pin the source spec.** `research/stripe-openapi/spec3.json`
   downloaded per `research/MANIFEST.md`'s re-fetch command; sha256 matches the
   manifest (`f0e0fc8f…`, 8,028,700 B, 419 paths, 1454 schemas).

2. **`src/stripeapi/dispatch/` package.** `__init__.py` (docstring only) and
   `routes.py`: a frozen `Route(method: str, pattern: str, op_id: str)`
   dataclass and `ALL: Final[tuple[Route, ...]]` — the 148-entry table grouped
   by resource root in path order. Phase 3 extends `Route` with
   `handler`/`params`/`alias_of`; here it is data only, with no framework
   imports, so `tools_dev` and tests can import it standalone.

3. **`tools_dev/prune_spec.py`** — the generator. Pure core, thin CLI:

   - `derive_operations(full_spec) -> tuple[Route, ...]` — the 22 resource-root
     prefixes minus the 39 cuts (`bank_accounts` 6, `cards` 5, `sources` 6,
     `cash_balance*` 4, `tax_ids` 4, `funding_instructions` 1,
     `products/features` 4, `search` 7, `balance/history` 2 — dispatcher.md
     §1.3); asserts the result is exactly 148. Legacy subscription aliases,
     `customers/{c}/payment_methods`, `customers/{c}/discount` and
     `charges/{charge}/refund` stay routed (dispatcher.md §3.1.3).
   - `build_artifacts(full_spec, routes, event_types) -> Artifacts` — pure; no
     filesystem. Every route must match a `(method, path)` in the spec or
     generation fails (discovery.md §1). The schema closure walk applies, in
     order, the scope-boundary stoplist (union members whose `$ref` target is
     stoplisted are dropped; a union left empty becomes `{"type": "string"}`; a
     direct `$ref` to a stoplisted schema becomes `{"type": "null"}`; an
     `x-expansionResources` union left empty is removed) and then the rail trim
     (any property named for a `payment_method.type` rail — read off the enum,
     57 values — that is not in `KEEP_RAILS = {card, us_bank_account, link}` is
     dropped from `properties`, `required` and `x-expandableFields`). Every
     `summary`/`description` string, in paths and schemas, is HTML-stripped
     (tags dropped, entities unescaped) at generation time.
   - `main(argv)` — `python -m tools_dev.prune_spec` writes the four artifacts
     plus the `spec3.min.json.sha256` provenance sidecar; `--check` regenerates
     in memory and exits non-zero on any byte difference; `--bootstrap-routes`
     regenerates `routes.py`'s table (a Phase 2 bootstrap command — Phase 3's
     hand-maintained handlers make it non-round-trippable thereafter). All
     modes require the spec on disk and name the path when it is absent.
   - The stoplist carries `topup` beyond discovery.md §2's representative table
     (`balance_transaction.source` member; the Connect/Tax stragglers' exact
     situation — the residual-schema test is the backstop).

4. **`THIRD_PARTY_LICENSES.md`** — Stripe's MIT text and copyright line for
     `stripe/openapi`, per licensing-and-naming.md §2's conservative reading;
     referenced from the generated files' headers.

5. **Committed artifacts under `src/stripeapi/spec/`** (generated, then
   committed): `spec3.min.json` (`{openapi, info, paths, components.schemas}`,
   canonical compact dump — 353 schemas, ~1.29 MB), the sha256 sidecar,
   `expandable.py` (`EXPANDABLE_FIELDS`, non-empty `x-expandableFields` per
   closure schema — 185 entries), `enums.py` (`DOC_ONLY_ENUMS`, the six
   hand-transcribed doc-only sets from resource-inventory.md),
   `event_types.py` (`EVENT_TYPES`, the 266 committed lines verbatim), and a
   package `__init__.py` re-exporting the three constants.

6. **Tests** (below), then `ruff format`, `ruff check`, `ty check`, `pytest`,
   `seahaven check` — all clean.

## Tests

- `test_routes.py` — `len(ALL) == 148` (the scope tripwire); `(method,
  pattern)` unique; every `op_id` unique; patterns are `/v1/…` with
  `{placeholder}` segments only; the alias families and `payment_methods`
  read-only alias are present; `GET /v1/customers/search` absent. When the
  source spec is on disk: `derive_operations` output ≡ `ALL`, both directions.
- `test_prune_spec.py` — against a small synthetic spec so every rule runs in
  CI: unmatched route raises; a cut operation (search, `balance/history`) never
  reaches the output; stoplisted union members dropped (empty union → string,
  direct ref → null, stale `x-expansionResources` removed); rail properties
  outside `KEEP_RAILS` dropped from properties/required/x-expandableFields;
  HTML stripped; `Artifacts` byte-identical across two runs; event types
  transcribed verbatim; the six enum sets carried through.
- `test_spec_artifacts.py` — the drift tests, tiered per discovery.md §4.
  Tier 1 (always): `spec3.min.json` path set ≡ `routes.ALL`, both directions;
  no stoplisted schema name or `$ref` anywhere in the artifact; rail fan-out
  schemas carry only the three kept rails; no `<`/`>` in any
  summary/description; size budget (< 2.5 MB, < 500 schemas);
  `info.version == "2026-08-26.dahlia"`; sidecar sha256 == `research/MANIFEST.md`'s
  pinned hash; `EVENT_TYPES` ≡ the committed 266-line txt; `DOC_ONLY_ENUMS`
  has exactly the six keys with values pinned in the test; `EXPANDABLE_FIELDS`
  spot-checks (`customer`, `subscription`, `invoice`). Tier 2 (skipped with a
  clear reason when the spec is absent): `--check` mode's in-memory artifacts
  byte-match every committed file.
