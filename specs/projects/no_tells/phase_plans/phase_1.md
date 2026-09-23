---
status: complete
---

# Phase 1: Capture and Enumerate

## Overview

Build the artifacts that every later phase reads, and write the surface-conformance test
harness that holds the tool layer honest from the start. No world code changes -- this phase
only produces data files, a dev-time script, and xfailed tests.

## Steps

1. Copy `research/stripe-openapi/spec3.json` (8 MB, git-ignored) into the package at
   `src/seahaven_stripe_world/spec/spec3.json` and commit it. Update `.gitignore` if needed
   (spec3.json inside `src/` is not ignored; only `research/stripe-openapi/` is).

2. Create `tests/surface/real_tool_schemas.json` -- a JSON file holding the captured real
   schemas and descriptions for all ten real Stripe MCP tools, structured as:
   ```json
   {
     "tool_name": {
       "description": "verbatim description",
       "parameters": { "required": [...], "properties": {...} }
     }
   }
   ```
   Data is transcribed from `research/mcp-fidelity-probe/tool-surface/tool-schemas.md` and
   `tool-surface/missing-tools.md`.

3. Write `tests/surface/test_surface_conformance.py` -- the surface-conformance harness
   (architecture section 8.1). For each of the eight tools this world will register, assert:
   - the name is present in `world.tools`
   - the description is byte-identical to the captured real one
   - the input schema's `properties` key set, per-property `type`, `required` list, `default`,
     `minimum`/`maximum`, and `examples` all match

   All eight tests are `xfail(strict=True, reason="tool not yet rebuilt")` until the tools
   land in Phases 5 and 6. The two tools this world keeps but the real server lacks
   (`get_stripe_account_info`) and the three tools not built are tested for absence/presence
   as appropriate, not xfailed.

4. Write `tools_dev/enumerate_catalogue.py`:
   - Reads every `operationId` from the committed `spec3.json`.
   - For each not already in the output artifact: calls the real Stripe MCP's
     `stripe_api_details`; a document means `catalogued` (record `required_permissions`),
     the "not available" string means `absent`, anything else means `error`.
   - Output: `src/seahaven_stripe_world/spec/mcp_catalogue.jsonl`, append-and-flush per
     record, ordered by operation id.
   - `--resume` is the default (reads existing records and probes only gaps).
   - `--sample N` probes N randomly-selected uncovered operations and writes a
     `sampled: true` marker plus the uncovered remainder.
   - Also records the live server's tool list as a `{"kind": "tools", ...}` record.
   - CLI: `python -m tools_dev.enumerate_catalogue [--sample N]`.

5. Update `src/seahaven_stripe_world/spec/__init__.py` to expose a `full_spec_document()`
   loader for the committed full `spec3.json`, used by the enumeration script and later by
   the discovery index.

## Tests

- `test_surface_conformance_read` -- xfail: asserts `stripe_api_read` schema matches real
- `test_surface_conformance_write` -- xfail: asserts `stripe_api_write` schema matches real
- `test_surface_conformance_search` -- xfail: asserts `stripe_api_search` schema matches real
- `test_surface_conformance_details` -- xfail: asserts `stripe_api_details` schema matches real
- `test_surface_conformance_list_accounts` -- xfail: asserts `list_available_accounts_or_orgs` matches
- `test_surface_conformance_manage_accounts` -- xfail: asserts `manage_stripe_accounts` matches
- `test_surface_conformance_analytics` -- xfail: asserts `stripe_analytics` matches
- `test_surface_conformance_account_info` -- xfail: asserts `get_stripe_account_info` matches
- `test_full_spec_committed` -- asserts spec3.json exists and its sha256 matches MANIFEST.md
- `test_real_tool_schemas_present` -- asserts the data file loads and has 10 entries
