# Return Envelopes: Real Stripe MCP vs. Our World

Every verbatim example here was captured from the live MCP in this session against
`stripe_context` (scrubbed), `livemode: false`.

---

## stripe_api_read — Success

### Real MCP (verbatim)

Call: `stripe_api_read(stripe_api_operation_id="GetCustomers", parameters={"limit": 1}, ...)`

Returned **bare Stripe object** — no wrapper:
```json
{
  "object": "list",
  "data": [
    {
      "id": "cus_XXXX",
      "object": "customer",
      "address": null,
      "balance": 0,
      "created": 1790104602,
      "currency": null,
      "customer_account": null,
      "default_source": null,
      "delinquent": false,
      "description": null,
      "discount": null,
      "email": null,
      "invoice_prefix": "SJJLQEA1",
      "invoice_settings": {
        "custom_fields": null,
        "default_payment_method": null,
        "footer": null,
        "rendering_options": null
      },
      "livemode": false,
      "metadata": {"probe_lane": "envelope"},
      "name": "Expand Probe EC",
      "next_invoice_sequence": 1,
      "phone": null,
      "preferred_locales": [],
      "shipping": null,
      "tax_exempt": "none",
      "test_clock": null
    }
  ],
  "has_more": true,
  "url": "/v1/customers"
}
```

### Our world (as specified in `api.py` docstring)

Returns `{"status": <HTTP status>, "body": <response body>}`:
```json
{
  "status": 200,
  "body": {
    "object": "list",
    "data": [...],
    "has_more": true,
    "url": "/v1/customers"
  }
}
```

### Difference
The real MCP returns the **bare Stripe response body**. Our world wraps it in a
`{"status": ..., "body": ...}` envelope. An agent that parses `result["data"]`
(real) vs `result["body"]["data"]` (ours) will immediately notice the difference.

---

## stripe_api_write — Success

### Real MCP (verbatim)

Call: `stripe_api_write(stripe_api_operation_id="PostCustomers", parameters={"name": "Tool Surface Probe", "email": "probe-ts@example.com", "metadata": {"probe_lane": "tool-surface"}}, ...)`

Returned **bare Stripe object** — no wrapper:
```json
{
  "id": "cus_XXXX",
  "object": "customer",
  "address": null,
  "balance": 0,
  "created": 1790104655,
  "currency": null,
  "customer_account": null,
  "default_source": null,
  "delinquent": false,
  "description": null,
  "discount": null,
  "email": "probe-ts@example.com",
  "invoice_prefix": "JQKRR5WO",
  "invoice_settings": {
    "custom_fields": null,
    "default_payment_method": null,
    "footer": null,
    "rendering_options": null
  },
  "livemode": false,
  "metadata": {"probe_lane": "tool-surface"},
  "name": "Tool Surface Probe",
  "next_invoice_sequence": 1,
  "phone": null,
  "preferred_locales": [],
  "shipping": null,
  "tax_exempt": "none",
  "test_clock": null
}
```

### Our world
Same `{"status": 200, "body": {...}}` wrapper. Same difference.

---

## stripe_api_read — Error (nonexistent object)

### Real MCP (verbatim)

Call: `stripe_api_read(stripe_api_operation_id="GetCustomersCustomer", parameters={"id": "cus_nonexistent999"}, ...)`

Returned as an **MCP-level tool error** (not a normal tool result):
```
Stripe API error: No such customer: 'cus_nonexistent999'

Use stripe_api_details with stripe_api_operation_id: "GetCustomersCustomer" to see all required and optional parameters.
```

### Our world
Returns a **normal tool result** with the Stripe error envelope:
```json
{
  "status": 404,
  "body": {
    "error": {
      "type": "invalid_request_error",
      "message": "No such customer: 'cus_nonexistent999'",
      "param": "customer",
      "code": "resource_missing"
    }
  }
}
```

### Difference
The real MCP raises an **MCP tool error** for Stripe API errors. Our world returns them as
normal results with an HTTP status code. This is architecturally different — an LLM client
sees `isError: true` in the MCP protocol for the real server, but `isError: false` (normal
content) for ours. Additionally, the real error message includes helpful guidance text
("Use stripe_api_details with...") that the real server appends.

---

## stripe_api_read — Error (unknown operation)

### Real MCP (verbatim)

Call: `stripe_api_read(stripe_api_operation_id="GetAccount", parameters={}, ...)`

Returned as an **MCP-level tool error**:
```
Operation 'GetAccount' is not available. Use stripe_api_search to find available operations.
```

### Our world
Would return an `UnknownOperation` Seahaven error, also at the error level (this is an
authoring mistake, not a Stripe response). The error message format differs.

---

## stripe_api_search — Return shape

### Real MCP (verbatim)

Call: `stripe_api_search(intent="list", resource="customers", ...)`

```json
{
  "openapi_spec_version": "2026-08-26.preview",
  "data": [
    {
      "id": "GetCustomers",
      "method": "GET",
      "path": "/v1/customers",
      "summary": "List all customers"
    },
    {
      "id": "GetCustomersSearch",
      "method": "GET",
      "path": "/v1/customers/search",
      "summary": "Search customers",
      "llm_context": "Find customers with server-side search, filter on metadata and more. Look up https://docs.stripe.com/search to learn more about the query language."
    },
    {
      "id": "GetCustomersCustomerPaymentMethods",
      "method": "GET",
      "path": "/v1/customers/{id}/payment_methods",
      "summary": "List a Customer's PaymentMethods"
    },
    {
      "id": "GetCustomersCustomerBalanceTransactions",
      "method": "GET",
      "path": "/v1/customers/{id}/balance_transactions",
      "summary": "List customer balance transactions"
    },
    {
      "id": "GetCustomersCustomer",
      "method": "GET",
      "path": "/v1/customers/{id}",
      "summary": "Retrieve a customer"
    }
  ]
}
```

### Our world (from `index.py` line 57, `Operation.result()`)
```json
[
  {"method": "GET", "path": "/v1/customers", "summary": "List all customers"},
  {"method": "GET", "path": "/v1/customers/{customer}", "summary": "..."}
]
```

### Differences
1. Real wraps results in `{"openapi_spec_version": "...", "data": [...]}`. Ours returns a bare list.
2. Real includes `id` (operation ID) in each result. Ours does not.
3. Real may include `llm_context` field. Ours never does.
4. Real paths use `{id}` uniformly. Ours uses resource-specific placeholders (`{customer}`, `{invoice}`, etc.).

---

## stripe_api_details — Return shape

### Real MCP (verbatim, for `GetCustomers`)

```json
{
  "id": "GetCustomers",
  "method": "GET",
  "path": "/v1/customers",
  "summary": "List all customers",
  "description": "Returns a list of your customers. The customers are returned sorted by creation date, with the most recent customers appearing first.",
  "tags": ["customer"],
  "keywords": ["v1", "customers", "get", "list", "customer"],
  "parameters": {
    "path": {},
    "query": {
      "created": {"type": "string", "description": "Only return customers that were created during the given date interval.", "required": false},
      "deleted": {"type": "boolean", "description": "", "required": false},
      "email": {"type": "string", "description": "A case-sensitive filter on the list based on the customer's `email` field.", "required": false},
      "ending_before": {"type": "string", "description": "A cursor for use in pagination...", "required": false},
      "expand": {"type": "array", "description": "Specifies which fields in the response should be expanded.", "required": false},
      "limit": {"type": "integer", "description": "A limit on the number of objects to be returned. Limit can range between 1 and 100, and the default is 10.", "required": false},
      "starting_after": {"type": "string", "description": "A cursor for use in pagination...", "required": false},
      "test_clock": {"type": "string", "description": "Provides a list of customers that are associated with the specified test clock...", "required": false}
    },
    "body": {}
  },
  "required_permissions": ["customer_read"],
  "openapi_spec_version": "2026-08-26.preview"
}
```

### Our world (from `index.py` line 251-258, `details()`)
```json
{
  "method": "GET",
  "path": "/v1/customers",
  "operation_id": "GetCustomers",
  "summary": "List all customers",
  "description": "Returns a list of your customers.",
  "parameters": [
    {"name": "created", "type": "string", "required": false, "description": "Only return customers that were created during the given date interval."},
    {"name": "email", "type": "string", "required": false, "description": "A case-sensitive filter on the list based on the customer's `email` field."},
    {"name": "ending_before", "type": "string", "required": false, "description": "..."},
    {"name": "expand", "type": "array<string>", "required": false, "description": "..."},
    {"name": "limit", "type": "integer", "required": false, "description": "..."},
    {"name": "starting_after", "type": "string", "required": false, "description": "..."}
  ]
}
```

### Differences
1. Key naming: real uses `id`, ours uses `operation_id`
2. Real has `tags`, `keywords`, `required_permissions`, `openapi_spec_version`. Ours has none.
3. Parameters structure: real uses `{path: {}, query: {}, body: {}}` with param name as object key. Ours uses a flat list of `{name, type, required, description}` objects.
4. Real body params have `properties` sub-object for nested fields. Ours uses `fields` list.
5. Real includes full description text. Ours truncates to first sentence.
6. Real includes `deleted` parameter for customers. Ours may not (depends on spec filtering).

---

## list_available_accounts_or_orgs — Return shape

### Real MCP (verbatim)
```json
{
  "accounts": [
    {
      "stripe_context": "acct_XXXX",
      "livemode": false,
      "name": "Seahaven Sandbox"
    }
  ]
}
```

Not present in our world.

---

## manage_stripe_accounts — Return shape

### Real MCP (verbatim)
```json
{
  "reconsent_url": "https://access.stripe.com/mcp/oauth2/authorize/sessions/oases_XXXX"
}
```

Not present in our world.

---

## search_stripe_documentation — Return shape

### Real MCP (verbatim, truncated)
```json
{
  "results": [
    {
      "title": "Create subscriptions with Stripe Billing",
      "subtitle": "With Connect, you can create subscriptions...",
      "url": "https://docs.stripe.com/connect/subscriptions",
      "type": "docs",
      "content": "Create subscriptions to bill platform end customers..."
    },
    {
      "type": "knowledge_gem",
      "content": "The user is asking how to..."
    }
  ]
}
```

Not present in our world. Returns a mix of `docs` and `knowledge_gem` result types.

---

## stripe_analytics — Return shape

### Real MCP (verbatim, for `search_query_tables`)
```json
{
  "tooltip": "Next: call this tool with intent 'retrieve_query_table' and the table_name from these results to get column details.",
  "result": {
    "total": 10,
    "displayed": 10,
    "tables": [
      {
        "name": "charges",
        "comment": "To charge a credit or a debit card, you create a charge object."
      }
    ]
  }
}
```

Not present in our world.

---

## stripe_implementation_planner — Return shape

### Real MCP (verbatim, truncated)
```json
{
  "integration_shape": null,
  "type": "guide",
  "instructions": "Walk through each decision tree question using the merchant's context...",
  "decision_trees": [
    {
      "root_question_id": "invoice_create_owner",
      "principles": ["Dashboard is sufficient for most businesses..."],
      "title": "Decision Tree 1: Create -- How do I create an invoice?",
      "questions": [...]
    }
  ],
  "status": "draft",
  "use_cases": [...],
  "guide_id": "iguide_XXXX"
}
```

Not present in our world.

---

## send_stripe_mcp_feedback — Return shape

Not probed (would actually send feedback). Schema requires: `sentiment` (enum), `quote` (string), `context` (string), `source` (enum). Not present in our world.

---

## get_stripe_account_info — Return shape (ours only)

### Our world (from `tools/account.py`)
```json
{
  "id": "acct_1SWTestAccount00",
  "object": "account",
  "business_profile": {"mcc": null, "name": "Test Business", ...},
  "business_type": "company",
  "capabilities": {"card_payments": "active", "transfers": "active"},
  "charges_enabled": true,
  "country": "US",
  "created": 1704067200,
  "default_currency": "usd",
  "details_submitted": true,
  "email": "test@example.com",
  "metadata": {},
  "payouts_enabled": true,
  "settings": {...},
  "type": "standard"
}
```

Not present in real Stripe MCP. The closest equivalent is `list_available_accounts_or_orgs`,
which returns only `{stripe_context, livemode, name}` per account.
