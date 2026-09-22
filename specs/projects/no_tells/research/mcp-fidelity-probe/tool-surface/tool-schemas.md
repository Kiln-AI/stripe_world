# Tool Schema Comparison: Real Stripe MCP vs. Our World

## Tool Inventory

### Real Stripe MCP (10 tools)
1. `stripe_api_read` — Read data via GET
2. `stripe_api_write` — Write data via POST/PATCH/PUT/DELETE
3. `stripe_api_search` — Search for API operations by intent+resource
4. `stripe_api_details` — Get parameter details for an operation
5. `list_available_accounts_or_orgs` — List connected accounts with context
6. `manage_stripe_accounts` — Returns URL for account management
7. `stripe_analytics` — SQL-based reporting and analytics
8. `search_stripe_documentation` — Search Stripe docs
9. `stripe_implementation_planner` — Decision-tree payment integration planning
10. `send_stripe_mcp_feedback` — Submit feedback about MCP tools

### Our World (5 tools, as written in `src/seahaven_stripe_world/tools/api.py` and `tools/account.py`)
1. `stripe_api_read`
2. `stripe_api_write`
3. `stripe_api_search`
4. `stripe_api_details`
5. `get_stripe_account_info` **(not present in real MCP)**

---

## Per-Tool Schema Comparison

### 1. `stripe_api_read`

**Real MCP schema (verbatim from ToolSearch):**
```json
{
  "required": ["stripe_api_operation_id", "parameters", "stripe_context", "livemode"],
  "properties": {
    "stripe_api_operation_id": {"type": "string", "description": "The operation ID to execute", "examples": ["PostCustomers", "GetPaymentIntents"]},
    "parameters": {"type": "object", "description": "Parameters for the API call. Include path parameters (e.g. 'customer' for /v1/customers/{customer}), query parameters, and body parameters. Array fields (e.g. line_items) must be passed as a JSON array value, not as a plain string."},
    "stripe_context": {"type": "string", "description": "The account to target for this request. Use the `stripe_context` value returned by list_available_accounts_or_orgs."},
    "livemode": {"type": "boolean", "description": "Whether to operate in livemode (true) or test mode/ sandbox (false). Must match the livemode of the stripe_context account."}
  }
}
```

**Real description:** "Read data from any Stripe API GET operation:\n1. Use stripe_api_search to find the operation ID.\n2. Use stripe_api_details to understand its parameters (required for operations with nested object fields like address, metadata, or restrictions).\n3. Call this tool with the stripe_api_operation_id and a parameters object containing path and query parameters. For mutations (POST/PATCH/PUT/DELETE), use stripe_api_write instead.\nMonetary values in responses are in the smallest currency unit (e.g. 1000 = $10.00 USD for most currencies)."

**Our schema (from `api.py`):**
```python
def stripe_api_read(
    ctx: seahaven.Ctx,
    path: str,
    params: dict[str, Any] | None = None,
) -> Any:
```
- Parameters: `path` (str, required), `params` (dict|None, optional)
- No `stripe_api_operation_id`, no `stripe_context`, no `livemode`

**Our description:** "Read data from the Stripe API with a GET method.\n\n`path` is a Stripe API path — a pattern with placeholders as the API reference spells them (`/v1/customers/{customer}`) or a concrete one (`/v1/customers/cus_123`). `params` carries the endpoint's own parameters as a JSON object: filters such as `limit` and `starting_after`, and `expand` as an array of paths.\n\nReturns `{\"status\": <HTTP status>, \"body\": <response body>}`. A non-2xx status is an ordinary outcome, not an error: its body is Stripe's error envelope."

**Differences:**
- Real uses operation IDs (`GetCustomers`), ours uses raw paths (`/v1/customers`)
- Real requires `stripe_context` and `livemode`, ours has neither
- Real param name is `parameters`, ours is `params`
- Real `parameters` is always required (object), ours `params` defaults to `None`
- Real description references other tools by name, ours doesn't
- Real description doesn't mention return shape; ours explicitly says `{"status": ..., "body": ...}`

---

### 2. `stripe_api_write`

**Real MCP schema (verbatim from ToolSearch):**
```json
{
  "required": ["stripe_api_operation_id", "parameters", "stripe_context", "livemode"],
  "properties": {
    "stripe_api_operation_id": {"type": "string", "description": "The operation ID to execute", "examples": ["PostCustomers", "GetPaymentIntents"]},
    "parameters": {"type": "object", "description": "Parameters for the API call..."},
    "stripe_context": {"type": "string"},
    "livemode": {"type": "boolean"},
    "human_confirmation": {
      "type": "object",
      "properties": {
        "approval_token": {"type": "string", "description": "The ID of the approval token for the request..."}
      },
      "description": "This tool might require human confirmation..."
    }
  }
}
```

**Our schema (from `api.py`):**
```python
def stripe_api_write(
    ctx: seahaven.Ctx,
    method: Literal["POST", "DELETE"],
    path: str,
    params: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> Any:
```

**Differences:**
- Real uses operation IDs, ours uses `method` + `path`
- Real has `human_confirmation` (approval flow), ours doesn't
- Ours has `method` (Literal["POST", "DELETE"]), real infers verb from operation ID
- Ours has `idempotency_key` as explicit param, real doesn't expose it
- Real requires `stripe_context` and `livemode`, ours has neither

---

### 3. `stripe_api_search`

**Real MCP schema (verbatim from ToolSearch):**
```json
{
  "required": ["intent", "resource", "stripe_context", "livemode"],
  "properties": {
    "intent": {"type": "string", "description": "The intent of the operation", "examples": ["create", "list", "refund"]},
    "resource": {"type": "string", "description": "Target resource the operation is looking to manipulate", "examples": ["customer", "payout methods", "issuing card transactions"]},
    "limit": {"type": "integer", "default": 5, "minimum": 1, "maximum": 20, "description": "Maximum number of results to return"},
    "stripe_context": {"type": "string"},
    "livemode": {"type": "boolean"}
  }
}
```

**Real description:** "Search for Stripe API operations by providing an intent and a resource to operate on.\n\nFor the resource, use a specific, descriptive phrase (e.g. \"issuing card transactions\", \"payout methods\", \"outbound payments\") rather than a single generic noun — Stripe has many similar resources, and the extra qualifiers help disambiguate between them rather than causing a miss. Only fall back to the bare core noun (e.g. \"transactions\") if a specific multi-word search returns no results.\n\nReturns matching operations with their HTTP method, path, summary, and top-level parameter names with types. Does not include parameter descriptions, enum values, or nested object fields — use stripe_api_details to get those before calling the relevant Stripe API execution tool."

**Our schema (from `api.py`):**
```python
def stripe_api_search(ctx: seahaven.Ctx, query: str) -> list[dict[str, Any]]:
```
- Single `query` parameter (string, required)

**Our description:** "Find Stripe API methods by keyword.\n\nMatches the routed operations' paths, operation ids, summaries and descriptions — exact word matches ranked above substring matches — and returns up to ten `{method, path, summary}` results, best first. This searches the API method catalogue, not Stripe objects. A query that matches nothing returns an empty list."

**Differences:**
- Completely different interface: real uses `intent` + `resource` (semantic), ours uses single `query` (keyword)
- Real has configurable `limit` (1-20, default 5), ours is hard-coded to 10
- Real requires `stripe_context` and `livemode`, ours has neither
- Real description mentions "top-level parameter names with types" in results — ours doesn't
- Different return shapes (see return-envelopes.md)

---

### 4. `stripe_api_details`

**Real MCP schema (verbatim from ToolSearch):**
```json
{
  "required": ["stripe_api_operation_id", "stripe_context", "livemode"],
  "properties": {
    "stripe_api_operation_id": {"type": "string", "description": "The operation ID to get details for (e.g. 'PostCustomers', 'GetPaymentIntents')"},
    "stripe_context": {"type": "string"},
    "livemode": {"type": "boolean"}
  }
}
```

**Real description:** "Get detailed parameter information for a specific Stripe API operation.\nProvide the stripe_api_operation_id from stripe_api_search results to see all path, query, and body parameters with their types, descriptions, and whether they are required.\n\nUse before calling the relevant Stripe API execution tool for operations with nested object fields.\n\nThe response may include an \"LLM Context\" section with usage guidance specific to the operation — follow any instructions there."

**Our schema (from `api.py`):**
```python
def stripe_api_details(
    ctx: seahaven.Ctx,
    method: Literal["GET", "POST", "DELETE"],
    path: str,
) -> dict[str, Any]:
```

**Differences:**
- Real uses single `stripe_api_operation_id`, ours uses `method` + `path`
- Real requires `stripe_context` and `livemode`, ours has neither
- Real mentions "LLM Context" section in response, ours doesn't

---

### 5. `get_stripe_account_info` (ours only)

Present in our world, absent from the real MCP entirely. Returns a static account object with billing-relevant fields.

### 6-10. Tools present in real MCP, absent from our world

See `missing-tools.md` for full schemas and return envelopes.
