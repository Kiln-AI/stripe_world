# Envelope and Cross-Cutting: Raw Probe Results

Probed 2026-09-22 against sandbox account `acct_***` (Seahaven Sandbox) via the
real Stripe MCP server attached to this session. All calls in test mode
(`livemode: false`).

---

## 1. Return Envelope Shape (the headline question)

### Probe: successful read (list)

```
Tool: mcp__stripe__stripe_api_read
Operation: GetCustomers
Parameters: {"limit": 2}
```

**Verbatim return** (abridged to shape):

```json
{"object":"list","data":[{...},{...}],"has_more":true,"url":"/v1/customers"}
```

No wrapper. The list envelope is the tool's direct return value.

### Probe: successful write (create)

```
Tool: mcp__stripe__stripe_api_write
Operation: PostCustomers
Parameters: {"name": "Envelope Probe EC-01", "email": "envelope-probe-ec01@example.com", "metadata": {"probe_lane": "envelope"}}
```

**Verbatim return** (abridged to shape):

```json
{"id":"cus_***","object":"customer","address":null,"balance":0,"created":1790104589,...}
```

No wrapper. The API object is the tool's direct return value.

### Conclusion

The real Stripe MCP returns the **bare JSON body** on success. There is no
`{"status": ..., "body": ...}` envelope, no `headers` key, nothing. The tool
result IS the API response body.

Our world returns `{"status": 200, "body": {...}, "headers": {"Stripe-Version": "...", "Request-Id": "...", ...}}`.

---

## 2. Error Envelope Shape

### Probe: 404 (resource not found)

```
Tool: mcp__stripe__stripe_api_read
Operation: GetCustomersCustomer
Parameters: {"id": "cus_nonexistent_envelope_probe"}
```

**Verbatim return**: MCP tool error (not a JSON body):

```
Stripe API error: No such customer: 'cus_nonexistent_envelope_probe'

Use stripe_api_details with stripe_api_operation_id: "GetCustomersCustomer" to see all required and optional parameters.
```

No HTTP status code. No `{"error": {"type": "invalid_request_error", "code": "resource_missing", ...}}`.
Just a plain string with the Stripe error message, followed by the MCP server's own help hint.

### Probe: 400 (unknown parameter)

```
Tool: mcp__stripe__stripe_api_write
Operation: PostCustomers
Parameters: {"name": "...", "totally_bogus_param_ec": "test", ...}
```

**Verbatim return**: MCP tool error:

```
Stripe API error: Received unknown parameter: totally_bogus_param_ec

Use stripe_api_details with stripe_api_operation_id: "PostCustomers" to see all required and optional parameters.
```

### Probe: 400 (wrong type)

```
Tool: mcp__stripe__stripe_api_write
Operation: PostCustomers
Parameters: {"name": ["wrong", "type", "array"], ...}
```

**Verbatim return**: MCP tool error:

```
Stripe API error: Invalid string: {"0":"wrong","1":"type","2":"array"}

Use stripe_api_details with stripe_api_operation_id: "PostCustomers" to see all required and optional parameters.
```

### Probe: 400 (expand error)

```
Tool: mcp__stripe__stripe_api_write
Operation: PostCustomers
Parameters: {"name": "...", "expand": ["bogus_field_ec"], ...}
```

**Verbatim return**: MCP tool error:

```
Stripe API error: This property cannot be expanded (bogus_field_ec).

Use stripe_api_details with stripe_api_operation_id: "PostCustomers" to see all required and optional parameters.
```

### Probe: 400 (expand on list without data. prefix)

```
Tool: mcp__stripe__stripe_api_read
Operation: GetCustomers
Parameters: {"limit": 2, "expand": ["bogus_no_data_prefix"]}
```

**Verbatim return**: MCP tool error:

```
Stripe API error: This property cannot be expanded (bogus_no_data_prefix). You may want to try expanding 'data.bogus_no_data_prefix' instead.

Use stripe_api_details with stripe_api_operation_id: "GetCustomers" to see all required and optional parameters.
```

### Probe: 400 (both cursors)

```
Tool: mcp__stripe__stripe_api_read
Operation: GetCustomers
Parameters: {"starting_after": "cus_X", "ending_before": "cus_Y"}
```

**Verbatim return**: MCP tool error:

```
Stripe API error: Received both starting_after and ending_before parameters. Please pass in only one.

Use stripe_api_details with stripe_api_operation_id: "GetCustomers" to see all required and optional parameters.
```

### Conclusion

Every Stripe API error (400, 404, etc.) surfaces as an **MCP tool error** whose
text is `"Stripe API error: <message>"` plus a help suffix. The structured error
body (`type`, `code`, `param`, `doc_url`, `request_log_url`) is completely
stripped. The HTTP status code is not visible.

Our world returns `{"status": 4xx, "body": {"error": {"type": "...", "code": "...", "message": "...", ...}}, "headers": {...}}`.

---

## 3. Idempotency

### Probe: passing idempotency_key as a body parameter

```
Tool: mcp__stripe__stripe_api_write
Operation: PostCustomers
Parameters: {"name": "Idempotency Probe EC", "email": "idem-probe-ec@example.com", "metadata": {"probe_lane": "envelope"}, "idempotency_key": "ec_idem_key_001"}
```

**Verbatim return**: MCP tool error:

```
Stripe API error: Received unknown parameter: idempotency_key

Use stripe_api_details with stripe_api_operation_id: "PostCustomers" to see all required and optional parameters.
```

### Analysis

The real Stripe API accepts `Idempotency-Key` as an **HTTP header**, not a body
parameter. The MCP server has no mechanism to send HTTP headers, so idempotency
is not exposed at all. The `mcp__stripe__stripe_api_write` tool schema has no
`idempotency_key` parameter (checked: the schema carries `stripe_api_operation_id`,
`parameters`, `stripe_context`, `livemode`, `human_confirmation` only).

Our world accepts `idempotency_key` as a tool parameter and implements full
idempotency (replay, mismatch, in-flight).

---

## 4. Response Headers

### Probe: no headers visible

No probe returned any indication of HTTP response headers. There is no
`Stripe-Version`, `Request-Id`, or `Idempotency-Key` echo in any successful
response. Error responses are plain strings with no header information.

The API version `2026-08-26.preview` appears only in the `stripe_api_search`
and `stripe_api_details` results, under the key `openapi_spec_version`.

Our world returns a `headers` dict on every response:
`{"Stripe-Version": "2026-08-26.dahlia", "Request-Id": "req_...", ...}`.

---

## 5. Pagination

### Probe: limit boundary — `limit=0`

```
Tool: mcp__stripe__stripe_api_read
Operation: GetCustomers
Parameters: {"limit": 0}
```

**Result**: Returns 1 item with `has_more: true`. No error.

### Probe: limit boundary — `limit=-1`

```
Tool: mcp__stripe__stripe_api_read
Operation: GetCustomers
Parameters: {"limit": -1}
```

**Result**: Returns 1 item with `has_more: true`. No error.

### Probe: limit boundary — `limit=101`

**Result**: Returns 100 items with `has_more: true`. No error. (Response
57,310 chars, saved to file; item count confirmed with `json.load`.)

### Probe: limit boundary — `limit=200`

**Result**: Returns 100 items with `has_more: true`. No error. (57,318 chars.)

### Probe: limit boundary — `limit=1`

**Result**: Returns 1 item with `has_more: true`. Normal.

### Probe: normal pagination — `starting_after`

```
Parameters: {"limit": 2, "starting_after": "cus_VJBYxI1PZddZ7B"}
```

**Result**: Returns 2 items older than the named cursor. Cursor is exclusive
(the named customer is not in the page). `has_more: true`.

### Probe: normal pagination — `ending_before`

```
Parameters: {"limit": 2, "ending_before": "cus_VJBYxI1PZddZ7B"}
```

**Result**: Returns 2 items newer than the named cursor. Cursor is exclusive.

### Probe: nonexistent cursor

```
Parameters: {"starting_after": "cus_ZZZZZZZZZZZZZ_past_end"}
```

**Result**: MCP error: `No such customer: 'cus_ZZZZZZZZZZZZZ_past_end'`

### Probe: both cursors (one nonexistent)

```
Parameters: {"starting_after": "cus_nonexistent_ec", "ending_before": "cus_VJBYxI1PZddZ7B"}
```

**Result**: MCP error: `No such customer: 'cus_nonexistent_ec'`

The cursor is validated **before** the both-cursors check, which matches our
spec (cross_cutting.md 3.2.3 vs the `page()` implementation in `resource.py`).

### Probe: both cursors (both valid)

```
Parameters: {"starting_after": "cus_VJBYxI1PZddZ7B", "ending_before": "cus_VJBYb7kwLbICqW"}
```

**Result**: MCP error: `Received both starting_after and ending_before
parameters. Please pass in only one.`

### Pagination envelope shape

The list envelope is `{"object": "list", "data": [...], "has_more": bool, "url": "/v1/customers"}`.
Exactly four keys. No `total_count`. This matches our spec.

### Conclusion on limit clamping

Stripe and/or the MCP server **silently clamps** out-of-range limits rather
than returning a 400 error. `limit <= 0` clamps to 1; `limit > 100` clamps
to 100. Our world raises 400 `parameter_invalid_integer` for `limit < 1` or
`limit > 100`.

However, since all errors arrive as MCP error strings anyway, an agent testing
limit boundaries would see different behavior (our world: tool error mentioning
the limit; real Stripe: valid response with clamped data), making this a
probable tell.

---

## 6. Expansion

### Probe: valid single-hop expand

```
Operation: PostCustomers
Parameters: {"name": "Expand Probe EC", "expand": ["default_source"], ...}
```

**Result**: Customer object returned directly. `default_source: null` (as
expected for a new customer). No error.

### Probe: two-level expand on list

```
Operation: GetPaymentIntents
Parameters: {"limit": 2, "expand": ["data.customer"]}
```

**Result**: Payment intents returned with `customer` expanded to full customer
objects. A deleted customer expands to `{"id":"cus_***","object":"customer",
"cache_context_key":"acct_***","deleted":true}`.

Note: the expanded customer has many fields our world does not serialize
(`cache_context_key`, `invoicing`, `is_shared`, `owning_merchant`,
`owning_merchant_info`, `profiles`, `usage_metadata`, `supported_payment_methods`).
These are object-shape concerns, not envelope concerns.

### Probe: `expand[]=data` on list

```
Operation: GetCustomers
Parameters: {"limit": 2, "expand": ["data"]}
```

**Result**: Returns normally. `expand[]=data` is accepted as a no-op, matching
our spec and implementation (`expand.py` line 165).

### Probe: multiple expand paths

```
Operation: GetCustomers
Parameters: {"limit": 1, "expand": ["data.default_source", "data.discount"]}
```

**Result**: Returns normally. Both fields present (both null for this customer).

### Conclusion

Expansion behavior through MCP matches Stripe's documented behavior and our
spec. The error messages for bad expand paths match verbatim. The difference is
only in how errors are surfaced (MCP tool error string vs our structured
envelope).

---

## 7. API Version

The `openapi_spec_version` field in `stripe_api_search` and `stripe_api_details`
results is `"2026-08-26.preview"`.

Our world pins `"2026-08-26.dahlia"` (in `middleware/stripe_envelope.py`,
`API_VERSION`).

The suffix differs: `.preview` vs `.dahlia`. Neither is visible in API response
bodies through MCP — it appears only in the discovery tools' metadata.

---

## 8. MCP-Level Error Chrome

Every Stripe API error arriving through MCP carries a suffix appended by the MCP
server itself:

```
Use stripe_api_details with stripe_api_operation_id: "..." to see all required and optional parameters.
```

This is not part of Stripe's error body. It is MCP-layer guidance. Our world's
errors do not carry this suffix.

---

## 9. Path Parameter Naming

The MCP server normalizes path parameter names. `GetCustomersCustomer` takes
`id` (not `customer`). The `stripe_api_details` response confirms:

```json
"path": {"id": {"type": "string", "description": "", "required": true}}
```

Passing `{"customer": "cus_..."}` instead of `{"id": "cus_..."}` fails with
an MCP-level error ("Missing required path parameters: id"), not a Stripe API
error. Our world uses `method`/`path`/`params` directly and does not have
operation IDs.
