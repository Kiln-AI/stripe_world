---
status: complete
---

# Phase 7: The Refusal Model

## Overview

Add catalogue-based gating ahead of route matching so that operations the real Stripe MCP does not expose answer bucket A, and catalogued-but-unrouted operations answer bucket B (B1 product-activation or B2 permission). Register `stripe_analytics` as a tool that always answers with a B1 Sigma refusal. Land the generated refusal-conformance test.

## Steps

1. **Create `spec/catalogue.py`**: load `mcp_catalogue.jsonl` once at module import into two frozen sets (`CATALOGUED`, `ABSENT`) and a dict mapping catalogued ops to their permissions. Expose `is_catalogued(op_id) -> bool`, `is_absent(op_id) -> bool`, and `permissions(op_id) -> list[str]`.

2. **Create `spec/products.py`**: the product table mapping path prefixes to `(product_name, dashboard_url)`. Keyed on path prefix since spec3.json tags are empty. Expose `product_for_path(path) -> tuple[str, str] | None`.

3. **Add B1/B2 refusal rendering to `middleware/stripe_envelope.py`**: a `_render_bucket_b` function that builds the B1 "Your account is not set up to use {product}" message or B2 "You don't have the required permissions" message, plus the guidance suffix.

4. **Add catalogue gating to the read/write tools in `tools/api.py`**: after context validation but before route resolution, check the catalogue. If the op is absent, raise `UnknownOperation` (bucket A). If the op is catalogued but the router has no route for it, raise a new `CataloguedButUnrouted` error carrying the op_id, path, and permissions, which the envelope middleware renders as B1 or B2.

5. **Create `tools/analytics.py`**: register `stripe_analytics` with the real schema and description, always returning a B1 Sigma product-activation refusal.

6. **Update `tools/__init__.py`**: import the new analytics module.

7. **Write `tests/test_refusal_conformance.py`**: generated test from the catalogue asserting every operation lands in the right bucket.

8. **Update tells register**: close AR-01, AR-02, TS-21, DT-23, and related rows.

## Tests

- `test_ar_01_product_activation_refusal`: an Issuing operation returns the B1 product-activation error
- `test_ar_02_permission_refusal`: a Checkout operation returns the B2 permission error
- `test_bucket_a_absent_operation`: an absent operation returns the "not available" string
- `test_bucket_a_gates_routed_absent`: a routed-but-absent op (e.g. GetEvents) is gated by the catalogue before reaching the handler
- `test_ts_21_stripe_analytics_registered`: stripe_analytics is registered with the correct schema
- `test_stripe_analytics_always_refuses`: every intent gets the Sigma B1 refusal
- `test_refusal_conformance_all_catalogued`: generated sweep over the entire catalogue
