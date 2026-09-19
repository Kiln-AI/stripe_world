---
status: complete
---

# Phase 3: Dispatcher and the four tools

## Overview

The machinery every resource slice runs on: the router (trie over the 148 patterns),
the `ParamSpec` validation layer, the `ResourceSpec` CRUD engine with cursor pagination,
response construction with expansion, the Stripe error boundary, and the four tools
(`stripe_api_read`, `stripe_api_write`, `stripe_api_search`, `stripe_api_details`)
plus the unregistered `call_stripe` escape hatch. Discovery lands here too — there is
no later phase for it — as `discovery/index.py` over `spec3.min.json`.

Exercised against one throwaway resource: a minimal `customers` slice (5 generated
routes wired into `routes.ALL`'s existing entries, full customers DDL from
`components/data_model.md` §3). Its `ResourceSpec`, serializer field map and
ParamSpecs are provisional and are replaced when the real customers slice lands.

Step-3 probing (live sandbox, this phase) settled the expand-error mapping before
implementation: at `2026-08-26.dahlia` the "because it doesn't exist" variant of
`cannot_expand` never fires — top-level nonexistent fields and exists-but-unexpandable
fields both return the plain form — and a bad *nested* segment carries the whole
dotted path as the token (`(invoice_settings.bogus)`), not the lone segment
`components/cross_cutting.md` §3.3.5 describes. Also probed: `expand[]=data` alone on
a list is accepted (not an error), `expand[]=address` is accepted (in
`x-expandableFields`), any bare first segment on a list gets the `data.` hint form,
`Received both starting_after and ending_before …` with real ids, cursor resolution
preceding exclusivity when the cursor is bogus, `Invalid <name>: must be one of …`
for an off-list literal, `Received unknown parameter: invoice_settings[nope]` for a
nested unknown, and `No such customer: 'ch_123'` for a bad-prefix id. The live API
accepts `limit=0` and `limit=101` without a 400; this phase implements the documented
1–100 contract (`cross_cutting.md` §3.2.3, functional spec §6.2) and records the
discrepancy for the Phase 5 cassettes.

Two design conflicts between `components/dispatcher.md` and
`components/cross_cutting.md` are resolved in cross_cutting's favour, as §7 of that
document requires and as Phase 1's committed `stripe_errors.py` docstring already
promises: the Stripe error boundary is `middleware/stripe_envelope.py`, outside the
per-call transaction — `dispatch` lets `StripeApiError` propagate rather than catching
it over an inner savepoint; and no `idempotency_key` is echoed onto response bodies
(§7.3 — the echo belongs to the Event object and a headers channel that is §7.4's
flagged, undecided change).

## Steps

1. **`dispatch/params.py`** — `Kind`, `Param`, `ParamSpec`, the five central
   `Param`s (`EXPAND`, `LIMIT`, `STARTING_AFTER`, `ENDING_BEFORE`, `METADATA`),
   `MetadataUpdate` (merge/unset/clear semantics, 50/40/500 limits), strict
   coercion with bracket-notation `param` paths, `body_of(route)` (list-filter
   derived body for generated lists), and `bind(ctx, route, path_values, raw)`:
   path ids (prefix check → `resource_missing`), lift the five, allowlist,
   required, depth-first coerce, `required_one_of` / `mutually_exclusive`
   (cursors implicit on every paginated spec).

2. **`dispatch/resource.py`** — `ResourceSpec` (no `sort` field: ordering is
   `x_seq DESC` everywhere per `components/data_model.md` §3.11), `Scope`,
   `ListFilter`, `DeleteSpec`, `page()` + `page_embedded()` (cursor resolution,
   `limit+1` probe, `has_more` by direction of travel, reversal for
   `ending_before`), `ListPage.envelope`, and the five engine actions
   (`list_`, `retrieve`, `create`, `update`, `delete`) with soft-delete stubs,
   `before_create`/`before_update` normalizers, and event emission through
   `resources/events.py`.

3. **`dispatch/router.py`** — the trie (`_Node`, at most one unnamed placeholder
   child per node), exact-before-placeholder matching with backtracking, joint
   `(method, path)` match distinguishing 404 from 405, `Match`, `resolve`, import
   time `WorldBug` checks, `ROUTER: Final = Router(routes.ALL)`.

4. **`dispatch/response.py`** — `Page`, `Request`, `ApiResponse`, `Handler`,
   `build()` (normalize, expansion on 2xx only), and `dispatch()` — the entry
   point; raises `StripeApiError` through to the middleware boundary.

5. **`dispatch/routes.py`** — extend `Route` with `params`, `resource`, `action`,
   `handler`, `scope`, `alias_of` (all default `None`); a route is *wired* iff
   `params is not None`; unwired routes stay legal until their resource phase and
   dispatch on one is a `WorldBug`. Wire the five probe routes
   (`GET/POST /v1/customers`, `GET/POST/DELETE /v1/customers/{customer}`) to
   `resources/customers.SPEC`.

6. **`serialize/fields.py`** — `FieldMap` (+ a `booleans` frozenset and an `OMIT`
   sentinel, additions `components/data_model.md`'s Public Interface lacks but its
   rules 4/§7 require), `to_api` (timestamps→Unix, JSON inflation, booleans,
   null-vs-absent from spec-derived `always_present`/`omit_when_none`), `deleted_stub`.

7. **`serialize/expand.py`** — `validate_paths` + `apply` over
   `spec/expandable.py` and `spec3.min.json` property lists, with the probe-corrected
   error forms (plain form for any bad segment; whole-path token for nested
   segments; `data.` hint on lists; `data` alone accepted).

8. **`resources/`** — `customers.py` (throwaway `ResourceSpec`, FieldMap,
   create/update ParamSpecs, `before_create` defaults: `invoice_prefix`,
   `invoice_settings`, `tax_exempt='none'`, `delinquent=0`), `_lookup.py`
   (`require_row` parent lookups), `events.py` (`emit_event` per
   `cross_cutting.md` §2.7/§3.4 over the `data_model.md` §5 events table),
   `_seq` stays in `_seq.py`.

9. **Schema** — `schema/001_core.sql` with the customers DDL verbatim from
   `components/data_model.md` §3 (delete `000_placeholder.sql`);
   `schema/004_infra.sql` with the events and counters DDL verbatim from §5
   (idempotency_keys is Phase 18's). `world.py` gains
   `untracked_tables=("counters",)`.

10. **`_seq.py`** — `next_seq(ctx, table)` via `UPDATE counters … RETURNING`
    (`components/data_model.md` §3.1.2), the only reader/writer of `counters`.

11. **`middleware/stripe_envelope.py`** — the Stripe response boundary: applies to
    the two HTTP tools by name, mints the `req_` id into `ctx.state["_request"]`,
    catches `StripeApiError` (transaction already rolled back) →
    `{"status", "body"}` (two keys; the `headers` third key is
    `cross_cutting.md` §7.4's flagged, undecided change), renders `ApiResponse`
    returns, passes everything else through. Registered second.

12. **`discovery/index.py` + `discovery/__init__.py`** — load `spec3.min.json`
    once at import; `Operation`, `INDEX`, `BY_KEY`, `search()` (four fields,
    exact>substring weights, `(-score, method, path)` total order, LIMIT 10,
    `ValueError` on an empty query), `details(method, path)` with depth-1
    `ParamDoc` flattening, first-sentence descriptions, `type_of` anyOf
    collapsing.

13. **`tools/api.py`** — the four tools per `components/dispatcher.md` §2.1 with
    `stripe_api_search(ctx, query)` (no `limit` parameter —
    `components/discovery.md` §7 and the functional spec's table override the
    dispatcher sketch), translating `ValueError`/`None` into
    `errors.InvalidSearchQuery` / `errors.InvalidMethod` / `errors.UnknownOperation`
    (three new `ToolError` subclasses); `call_stripe` undecorated with the
    one-line why. `tools/__init__.py` imports it.

14. **`tools_dev/prune_spec.py`** — discovery §8a: filter each *wired* route's
    `requestBody` properties to `body_of(route)`'s names plus `expand`/`metadata`;
    unwired routes stay verbatim until their slice lands. Regenerate the
    committed artifacts (`spec3.json` is on disk and hash-verified).

15. **Fixtures** — the schema changed, so delete and regenerate `empty` via
    `python fixtures_src/generate.py`.

## Tests

- `test_router.py` — trie precedence (`credit_notes/preview` beats `{id}`,
  `invoices/create_preview` beats `{invoice}`), backtracking out of a literal dead
  end, the two shared placeholder nodes, 404 vs 405 vs placeholder-route preference,
  trailing slash / empty segment, round-trip of all 148 patterns, node-visit bound.
- `test_params.py` — unknown/nested-unknown parameters, missing required, bracket
  `param` paths for nested and indexed failures, strict types, literal choices,
  path-id prefix check before any query, cursor exclusivity, `limit` bounds and
  default, lifted-five refusal where unsupported, metadata merge/unset/clear/limits,
  range filter object-and-integer.
- `test_resource_engine.py` — CRUD round trip on the probe resource, id prefix,
  soft-delete stub then retrievable, update-absent-means-unchanged, list filters,
  reverse-chronological order under a frozen clock (x_seq), stable cursor walk,
  `has_more` on the exactly-full last page, scoped-list behavior via a test-local
  scoped route (parent 404, filtered rows, concrete `url`), `before_create`
  defaults, created/updated/deleted events land in `events`.
- `test_pagination.py` — cursor exclusivity message, unknown-cursor
  `resource_missing` naming the cursor, deleted cursor resolves and is excluded,
  walk forward/backward covers exactly once, one statement per page.
- `test_dispatch.py` — return shape through `instance.call`, a raised error rolls
  the call back (zero change-log records), a returned 402-shaped `ApiResponse`
  commits its rows, unwired route is a `WorldBug`, `expand` applied to success
  only, id prefix refusal with no row read.
- `test_expand.py` — the probe-corrected forms: plain form for nonexistent and
  for unexpandable top-level fields, whole-path token for a bad nested segment,
  `data.` hint on a list, `data` alone accepted, null reference stays null.
- `test_stripe_envelope.py` — the boundary: raised `StripeApiError` →
  `{"status", "body"}` envelope with nothing committed; `ApiResponse` returns
  rendered; search/details pass through untouched; `req_` id minted per call.
- `test_discovery.py` — index ≡ route table both directions, unrouted operation
  invisible, search determinism and tie-break, empty query and zero results,
  details parameter names equal the wired ParamSpec bodies (+ central five),
  nesting depth 1, one-sentence descriptions, size budget.
- `test_tools.py` — the four registered tools: `Literal` verb refusal is
  `INVALID_INPUT`, read tool cannot reach a POST route (405), `call_stripe`
  reaches every verb, params as JSON dict, no docstring names a sibling tool.
- Updates: `test_package.py` (two middlewares, four tools + `controller_run_sql`),
  `test_prune_spec.py` (body-filter rule on a synthetic wired route),
  `test_spec_artifacts.py` (unchanged invariants hold after regeneration).
