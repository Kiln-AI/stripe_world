---
status: complete
---

# Phase 4: The Envelope Transform

## Overview

Rewrite `middleware/stripe_envelope.py` so the two registered MCP tools (`stripe_api_read`,
`stripe_api_write`) return bare body on 2xx and raise a `StripeToolError` on non-2xx. The
unregistered `call_stripe` keeps its `{status, body, headers}` shape so the raw-HTTP surface
and the tests that exercise error envelope structure remain unchanged.

This closes TS-10/TS-11/EC-01 (success response shape), TS-12/EC-02 (error channel), and
EC-04 (response headers visible). The test suite adapts mechanically: success assertions drop
the `["body"]` dereference, error assertions move from status-code checks to `pytest.raises`.

## Steps

1. **Add `StripeToolError` to `errors.py`**: a `seahaven.ToolError` subclass with
   `error_type="STRIPE_API_ERROR"`, carrying `status` and `stripe_body` for test inspection.
   Message format: `Stripe API error: {message}`.

2. **Rewrite `middleware/stripe_envelope.py`**: split the tool set into `_MCP_TOOLS`
   (`stripe_api_read`, `stripe_api_write`) and `_RAW_TOOLS` (`call_stripe`). MCP tools get
   unwrap-or-raise; raw tools keep `{status, body, headers}`. Add `_render_tool_error(body)`
   that extracts `error.message` and builds the `StripeToolError`.

3. **Update `tests/schema_conformance/capture.py`**: the capture hook currently identifies
   HTTP-shaped results by `"status" in result and "body" in result`. After the change, MCP
   tools return the body directly. Update the hook to capture both shapes.

4. **Adapt every test file** that uses `stripe_api_read`/`stripe_api_write`:
   - Success: `result["body"]` becomes `result`; remove `result["status"] == 200` assertions
   - Error: `result["status"] == 4xx` becomes `pytest.raises(StripeToolError)`; check
     `exc.value.status` and `exc.value.message` where the test verifies specific error semantics

5. **Update the tells register** (`tells.md`): mark TS-10/TS-11/EC-01, TS-12/EC-02, EC-04
   as `closed`.

## Tests

- `test_mcp_success_returns_bare_body`: a 200 from `stripe_api_read` is the object directly,
  no `{status, body}` wrapper
- `test_mcp_error_raises_stripe_tool_error`: a 404 from `stripe_api_read` raises
  `StripeToolError` with the right message and status
- `test_call_stripe_keeps_status_body_headers`: `call_stripe` through a probe world still
  returns `{status, body, headers}`
- `test_a_402_decline_raises_but_keeps_the_rows`: the transaction-semantics property -- a
  402 returned by a handler commits its writes, and the middleware then raises
- Existing `test_stripe_envelope.py` tests adapted to the new contract
