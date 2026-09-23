---
status: complete
---

# Phase 8: Discovery

## Overview

Rebuild the discovery layer so `stripe_api_search` and `stripe_api_details` serve
the full MCP catalogue (123 catalogued operations) rather than only the 155 routed
ones. The pruner gains a second output (`discovery_index.json`) holding one
precomputed details document per catalogued operation in the twelve-key shape the
real server returns. The search scoring shifts to the architecture's
`resource`/`intent`-weighted scheme over tags and keywords, and the details
document adopts `{path:{}, query:{}, body:{}}` grouped parameters with full nesting
depth. Path placeholders normalise to `{id}` in discovery output only.

## Steps

1. **Add `discovery_index.json` generation to `prune_spec.py`.** A new function
   `build_discovery_index(full_spec, catalogue)` produces a dict keyed by
   operation id, each value a twelve-key document:
   `{id, method, path, summary, description, tags, keywords, parameters, required_permissions, openapi_spec_version}`.
   Parameters grouped as `{path:{}, query:{}, body:{}}` with parameter name as
   key and nesting to full depth. Path placeholders normalised to `{id}`.
   Tags derived from the spec's `tags` array. Keywords generated as path
   segments plus verb plus tag tokens. `description` is the full text (not
   first-sentence). `openapi_spec_version` from the pinned spec.
   Committed alongside the other spec artifacts.

2. **Rewrite `discovery/index.py`.** Load `discovery_index.json` instead of
   `spec3.min.json`. The `Operation` dataclass and search scoring change:
   - `resource` terms score against `tags` and `keywords` (weight 3), path
     segments (weight 2).
   - `intent` terms score against verb class and `summary` (weight 1).
   - Ties break on operation id for stability.
   - Default limit is 5.
   - `search()` returns `{"openapi_spec_version": ..., "data": [...]}` wrapper.
   - Each search result has `id`, `method`, `path`, `summary`, optionally
     `llm_context`.
   - `details()` returns the twelve-key document directly from the index.

3. **Update `tools/api.py`.** `stripe_api_search` returns the wrapped envelope
   from discovery. `stripe_api_details` returns the details document; for
   catalogued-but-unrouted operations the discovery index now covers them so
   no `UnknownOperation` is raised for those.

4. **Update `spec/__init__.py`.** Add `discovery_index` loader function.

5. **Update existing tests.** Fix `test_discovery.py` assertions that assumed
   the old shape (flat parameter list, `operation_id` key, route-table matching).
   Add new tests for the twelve-key shape, wrapped search envelope, `{id}`
   placeholders, full-depth nesting, `required_permissions`, `tags`, `keywords`,
   `openapi_spec_version`.

6. **Write tests named for each closed tell row.**

## Tests

- `test_ts_13_dt_02_search_returns_wrapped_envelope`: search returns `{openapi_spec_version, data}`.
- `test_ts_14_dt_03_search_results_have_id_and_llm_context`: each result has `id`, optionally `llm_context`.
- `test_ts_15_dt_14_details_twelve_keys`: details has all twelve keys with `id` not `operation_id`.
- `test_ts_16_dt_15_details_parameters_grouped`: parameters is `{path, query, body}` dicts, not list.
- `test_ts_18_dt_04_path_placeholders_normalised_to_id`: paths use `{id}` everywhere.
- `test_ts_17_dt_16_details_full_description`: description is multi-paragraph, not first sentence.
- `test_dt_05_default_search_limit_is_five`: default returns at most 5 results.
- `test_dt_17_details_nests_to_full_depth`: nested parameters have `properties` to their real depth.
- `test_dt_20_required_permissions_present`: details has `required_permissions` array.
- `test_ar_05_discovery_covers_entire_catalogue`: catalogued ops are searchable and have details.
