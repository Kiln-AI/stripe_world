---
status: complete
---

# Phase 5: The Four API Tools

## Overview

Rebuild the four API tool signatures to match the real Stripe MCP server: operation-id addressing, context parameters (stripe_context, livemode), verbatim descriptions, and context validation with two refusals. Surface conformance goes green for all four tools.

## Steps

1. Create `tools/_descriptions.py` — one `Final[str]` per tool, copied verbatim from `tests/surface/real_tool_schemas.json`. Used by `@world.tool(description=...)`.

2. Create `tools/_context.py` — `check(ctx, stripe_context, livemode)` reads `ctx.state["account"]` and raises `SessionValidation` for mismatched account or mode. Two verbatim messages from the probe.

3. Update `errors.py`:
   - Add `SessionValidation(ToolError)` for the two context refusals.
   - Rewrite `UnknownOperation` to take `op_id: str` and emit `"Operation '{op_id}' is not available. Use stripe_api_search to find available operations."`.
   - Remove `InvalidMethod` (no longer needed — method is implicit in the operation ID) and `InvalidSearchQuery` (search no longer takes a free-text query).

4. Add `resolve_op_id(op_id) -> Route | None` to `dispatch/router.py` — a dict mapping from `op_id` to `Route`, built at import alongside `_by_key`.

5. Rewrite `tools/api.py`:
   - `stripe_api_read(ctx, stripe_api_operation_id, parameters, stripe_context, livemode)` — context check, resolve op_id to GET route, extract path params from `parameters`, build concrete path, dispatch.
   - `stripe_api_write(ctx, stripe_api_operation_id, parameters, stripe_context, livemode, human_confirmation=None)` — same but for POST/DELETE; `human_confirmation` is accepted and ignored.
   - `stripe_api_search(ctx, intent, resource, stripe_context, livemode, limit=5)` — context check, delegate to discovery with intent+resource.
   - `stripe_api_details(ctx, stripe_api_operation_id, stripe_context, livemode)` — context check, look up by op_id.
   - `call_stripe` stays unchanged.
   - All four use `@world.tool(description=_descriptions.X)`.

6. Update `discovery/index.py`:
   - `search(intent, resource, limit)` replaces `search(query)`. Combine intent+resource terms for scoring. Return list (Phase 8 wraps the envelope).
   - `details(operation_id)` replaces `details(method, path)`. Look up by operation_id.
   - Each search result adds `id` field (the operation_id).

7. Update `middleware/stripe_envelope.py`:
   - Add guidance suffix to `_render_tool_error`: read `stripe_api_operation_id` from `call.arguments`.
   - Update `_mint_request` for new argument names (no more `idempotency_key` on MCP tools).

8. Update `middleware/idempotency.py` — also fire on `call_stripe` tool calls, since `stripe_api_write` no longer carries `idempotency_key`.

9. Update `conftest.py`:
   - Add `api_read(instance, path, params)` and `api_write(instance, method, path, params)` helpers that translate (method, path) → (op_id, parameters) for tests.
   - Update `dispatch_tool()` to accept and pass `idempotency_key`.

10. Update all test files — replace old-style tool calls with the new helpers or new signatures.

11. Remove xfail from surface conformance tests: `test_ts_04`, `test_ts_05`, `test_ts_06`, `test_ts_07`, `test_ts_08`, `test_ts_24`, `test_ts_09`, `test_ts_25`.

## Tests

- `test_ts_04_read_takes_operation_id`: schema and description match the real `stripe_api_read`
- `test_ts_05_write_takes_operation_id`: schema and description match the real `stripe_api_write`
- `test_ts_06_search_takes_intent_resource`: schema and description match the real `stripe_api_search`
- `test_ts_07_details_takes_operation_id`: schema and description match the real `stripe_api_details`
- `test_ts_08_read_description_matches`: read description matches verbatim
- `test_ts_09_search_description_matches`: search description matches verbatim
- `test_ts_24_write_description_matches`: write description matches verbatim
- `test_ts_25_details_description_matches`: details description matches verbatim
- `test_ts_26_context_params_required`: context validation for stripe_context/livemode
- Context validation tests: bad stripe_context raises with verbatim message; livemode mismatch raises with verbatim message
