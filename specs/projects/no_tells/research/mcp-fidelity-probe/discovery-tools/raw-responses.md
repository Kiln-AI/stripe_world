# Raw Responses from Real Stripe MCP Discovery Tools

Captured 2026-09-22 against sandbox account, `livemode: false`.
Account identifiers scrubbed.

## stripe_api_search

### Search: create customer

**Input:** `intent: "create", resource: "customer", limit: 5 (default)`

```json
{
  "openapi_spec_version": "2026-08-26.preview",
  "data": [
    {
      "id": "PostCustomers",
      "method": "POST",
      "path": "/v1/customers",
      "summary": "Create a customer"
    },
    {
      "id": "GetCustomersSearch",
      "method": "GET",
      "path": "/v1/customers/search",
      "summary": "Search customers",
      "llm_context": "Find customers with server-side search, filter on metadata and more. Look up https://docs.stripe.com/search to learn more about the query language."
    },
    {
      "id": "PostCustomersCustomer",
      "method": "POST",
      "path": "/v1/customers/{id}",
      "summary": "Update a customer"
    },
    {
      "id": "GetCustomers",
      "method": "GET",
      "path": "/v1/customers",
      "summary": "List all customers"
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

### Search: list subscription

**Input:** `intent: "list", resource: "subscription", limit: 5 (default)`

```json
{
  "openapi_spec_version": "2026-08-26.preview",
  "data": [
    {
      "id": "GetSubscriptions",
      "method": "GET",
      "path": "/v1/subscriptions",
      "summary": "List subscriptions"
    },
    {
      "id": "GetSubscriptionsSearch",
      "method": "GET",
      "path": "/v1/subscriptions/search",
      "summary": "Search subscriptions",
      "llm_context": "Find subscriptions with server-side search, filter on metadata and more. Look up https://docs.stripe.com/search to learn more about the query language."
    },
    {
      "id": "GetSubscriptionSchedules",
      "method": "GET",
      "path": "/v1/subscription_schedules",
      "summary": "List all schedules"
    },
    {
      "id": "GetSubscriptionsSubscriptionExposedId",
      "method": "GET",
      "path": "/v1/subscriptions/{id}",
      "summary": "Retrieve a subscription"
    },
    {
      "id": "GetSubscriptionItems",
      "method": "GET",
      "path": "/v1/subscription_items",
      "summary": "List all subscription items"
    }
  ]
}
```

### Search: create issuing card (out-of-scope)

**Input:** `intent: "create", resource: "issuing card"`

```json
{
  "openapi_spec_version": "2026-08-26.preview",
  "data": [
    {
      "id": "GetIssuingCardholders",
      "method": "GET",
      "path": "/v1/issuing/cardholders",
      "summary": "List all cardholders",
      "llm_context": "Lists issuing cardholders — the people or entities who have issuing cards, not the cards themselves. Use for \"who has cards\" or \"cardholders\". For the physical or virtual cards, use GetIssuingCards instead."
    },
    {
      "id": "GetIssuingCards",
      "method": "GET",
      "path": "/v1/issuing/cards",
      "summary": "List all cards"
    },
    {
      "id": "GetIssuingCardsCard",
      "method": "GET",
      "path": "/v1/issuing/cards/{id}",
      "summary": "Retrieve a card"
    },
    {
      "id": "GetIssuingCardholdersCardholder",
      "method": "GET",
      "path": "/v1/issuing/cardholders/{id}",
      "summary": "Retrieve a cardholder"
    },
    {
      "id": "GetIssuingTransactions",
      "method": "GET",
      "path": "/v1/issuing/transactions",
      "summary": "List all transactions",
      "llm_context": "Lists issuing card transactions — settled card spend on issued cards. Use for \"card spend\", \"card transactions\", \"issuing spend\", or \"issuing card transactions\". Distinct from GetIssuingAuthorizations (real-time approve/decline) and from GetV2MoneyManagementTransactions, which covers transactions on Treasury financial accounts, not card spend."
    }
  ]
}
```

### Search: create terminal reader (out-of-scope)

**Input:** `intent: "create", resource: "terminal reader"`

```json
{"openapi_spec_version": "2026-08-26.preview", "data": []}
```

### Search: create checkout session (out-of-scope)

**Input:** `intent: "create", resource: "checkout session"`

```json
{
  "openapi_spec_version": "2026-08-26.preview",
  "data": [
    {
      "id": "PostCheckoutSessions",
      "method": "POST",
      "path": "/v1/checkout/sessions",
      "summary": "Create a Checkout Session"
    },
    {
      "id": "GetCheckoutSessions",
      "method": "GET",
      "path": "/v1/checkout/sessions",
      "summary": "List all Checkout Sessions"
    },
    {
      "id": "PostBillingPortalSessions",
      "method": "POST",
      "path": "/v1/billing_portal/sessions",
      "summary": "Create a portal session"
    },
    {
      "id": "GetCheckoutSessionsSession",
      "method": "GET",
      "path": "/v1/checkout/sessions/{id}",
      "summary": "Retrieve a Checkout Session"
    },
    {
      "id": "GetCheckoutSessionsSessionLineItems",
      "method": "GET",
      "path": "/v1/checkout/sessions/{id}/line_items",
      "summary": "Retrieve a Checkout Session's line items"
    }
  ]
}
```

### Search: create connect account (out-of-scope)

**Input:** `intent: "create", resource: "connect account"`

```json
{
  "openapi_spec_version": "2026-08-26.preview",
  "data": [
    {
      "id": "GetAccounts",
      "method": "GET",
      "path": "/v1/accounts",
      "summary": "List all connected accounts"
    },
    {
      "id": "GetV2MoneyManagementFinancialAccounts",
      "method": "GET",
      "path": "/v2/money_management/financial_accounts",
      "summary": "List Financial Accounts",
      "llm_context": "Lists Treasury financial accounts..."
    },
    {
      "id": "EnableConnect",
      "method": "POST",
      "path": "/v1/_unstable/connect/enable",
      "summary": "Enable Connect"
    },
    {
      "id": "GetV2MoneyManagementPayoutMethodsBankAccountSpec",
      "method": "GET",
      "path": "/v2/money_management/payout_methods_bank_account_spec",
      "summary": "Retrieve Bank Account Specification by country"
    },
    {
      "id": "GetAccountsAccount",
      "method": "GET",
      "path": "/v1/accounts/{id}",
      "summary": "Retrieve account"
    }
  ]
}
```

### Search: create tax calculation (out-of-scope)

**Input:** `intent: "create", resource: "tax calculation"`

```json
{
  "openapi_spec_version": "2026-08-26.preview",
  "data": [
    {
      "id": "PostTaxCalculations",
      "method": "POST",
      "path": "/v1/tax/calculations",
      "summary": "Create a Calculation",
      "llm_context": "Creates an ephemeral tax calculation to preview rates for a transaction. Useful for validating tax is calculated as you expect.In live mode, Stripe bills a fee per calculation. Don't call this repeatedly or in a loop."
    },
    {
      "id": "GetTaxCalculationsCalculation",
      "method": "GET",
      "path": "/v1/tax/calculations/{id}",
      "summary": "Retrieve a Calculation"
    },
    {
      "id": "GetTaxCalculationsCalculationLineItems",
      "method": "GET",
      "path": "/v1/tax/calculations/{id}/line_items",
      "summary": "Retrieve a Calculation's line items"
    }
  ]
}
```

### Search: list climate order (out-of-scope)

```json
{"openapi_spec_version": "2026-08-26.preview", "data": []}
```

### Search: list radar rules (out-of-scope)

```json
{"openapi_spec_version": "2026-08-26.preview", "data": []}
```

### Search: limit=20, list payment intent

**Input:** `intent: "list", resource: "payment intent", limit: 20`

```json
{
  "openapi_spec_version": "2026-08-26.preview",
  "data": [
    {"id": "GetPaymentIntents", "method": "GET", "path": "/v1/payment_intents", "summary": "List all PaymentIntents"},
    {"id": "GetPaymentIntentsSearch", "method": "GET", "path": "/v1/payment_intents/search", "summary": "Search PaymentIntents", "llm_context": "Find payment intents with server-side search..."},
    {"id": "GetSetupIntents", "method": "GET", "path": "/v1/setup_intents", "summary": "List all SetupIntents", "llm_context": "Payment SetupIntents save payment methods for future payments without charging now..."},
    {"id": "GetPaymentIntentsIntent", "method": "GET", "path": "/v1/payment_intents/{id}", "summary": "Retrieve a PaymentIntent"},
    {"id": "GetSetupIntentsIntent", "method": "GET", "path": "/v1/setup_intents/{id}", "summary": "Retrieve a SetupIntent", "llm_context": "..."},
    {"id": "GetV2MoneyManagementOutboundSetupIntents", "method": "GET", "path": "/v2/money_management/outbound_setup_intents", "summary": "List Outbound Setup Intents"},
    {"id": "GetV2MoneyManagementOutboundSetupIntentsId", "method": "GET", "path": "/v2/money_management/outbound_setup_intents/{id}", "summary": "Retrieve an Outbound Setup Intent"},
    {"id": "GetV2CoreApprovalRequests", "method": "GET", "path": "/v2/core/approval_requests", "summary": "List Approval Requests", "llm_context": "..."}
  ]
}
```

8 results for limit=20. The server does not fill to the limit; it returns only matching results.

### Search: limit=1, list payment intent

```json
{
  "openapi_spec_version": "2026-08-26.preview",
  "data": [
    {"id": "GetPaymentIntents", "method": "GET", "path": "/v1/payment_intents", "summary": "List all PaymentIntents"}
  ]
}
```

### Search: nonsense query

**Input:** `intent: "xyzzy", resource: "nonexistent"`

```json
{"openapi_spec_version": "2026-08-26.preview", "data": []}
```

### Search: non-English (French)

**Input:** `intent: "creer", resource: "client"`

```json
{"openapi_spec_version": "2026-08-26.preview", "data": []}
```

### Search: long garbage resource

**Input:** `intent: "create", resource: "a]]]...]]]..."`

```json
{"openapi_spec_version": "2026-08-26.preview", "data": []}
```

### Search: list events

**Input:** `intent: "list", resource: "events", limit: 5`

```json
{
  "openapi_spec_version": "2026-08-26.preview",
  "data": [
    {"id": "GetV2CoreEventDestinations", "method": "GET", "path": "/v2/core/event_destinations", "summary": "List Event Destinations"},
    {"id": "GetV2CoreEventDestinationsId", "method": "GET", "path": "/v2/core/event_destinations/{id}", "summary": "Retrieve an Event Destination"},
    {"id": "PostV2CoreEventDestinations", "method": "POST", "path": "/v2/core/event_destinations", "summary": "Create an Event Destination"},
    {"id": "PostV2CoreEventDestinationsId", "method": "POST", "path": "/v2/core/event_destinations/{id}", "summary": "Update an Event Destination"},
    {"id": "GetIssuingAuthorizations", "method": "GET", "path": "/v1/issuing/authorizations", "summary": "List all authorizations", "llm_context": "..."}
  ]
}
```

**Notable: `GetEvents` and `GetEventsId` are NOT searchable. The v1 events endpoints are absent from the real MCP server's discovery surface entirely.**

### Search: list payout

```json
{
  "openapi_spec_version": "2026-08-26.preview",
  "data": [
    {"id": "GetV2MoneyManagementPayoutMethods", "method": "GET", "path": "/v2/money_management/payout_methods", "summary": "List Payout Methods", "llm_context": "..."},
    {"id": "GetPayouts", "method": "GET", "path": "/v1/payouts", "summary": "List all payouts"},
    {"id": "GetPayoutsPayout", "method": "GET", "path": "/v1/payouts/{id}", "summary": "Retrieve a payout"},
    {"id": "GetV2MoneyManagementPayoutMethodsId", "method": "GET", "path": "/v2/money_management/payout_methods/{id}", "summary": "Retrieve a Payout Method"},
    {"id": "GetV2MoneyManagementPayoutMethodsBankAccountSpec", "method": "GET", "path": "/v2/money_management/payout_methods_bank_account_spec", "summary": "Retrieve Bank Account Specification by country"}
  ]
}
```

### Search: finalize invoice

```json
{
  "openapi_spec_version": "2026-08-26.preview",
  "data": [
    {"id": "GetInvoicesSearch", "method": "GET", "path": "/v1/invoices/search", "summary": "Search invoices", "llm_context": "..."},
    {"id": "PostInvoicesInvoiceFinalize", "method": "POST", "path": "/v1/invoices/{id}/finalize", "summary": "Finalize an invoice"},
    {"id": "GetInvoices", "method": "GET", "path": "/v1/invoices", "summary": "List all invoices"},
    {"id": "PostInvoices", "method": "POST", "path": "/v1/invoices", "summary": "Create an invoice"},
    {"id": "GetInvoiceitems", "method": "GET", "path": "/v1/invoiceitems", "summary": "List all invoice items"}
  ]
}
```

### Search: pay invoice

```json
{
  "openapi_spec_version": "2026-08-26.preview",
  "data": [
    {"id": "GetInvoicesSearch", "method": "GET", "path": "/v1/invoices/search", "summary": "Search invoices", "llm_context": "..."},
    {"id": "GetInvoicePayments", "method": "GET", "path": "/v1/invoice_payments", "summary": "List all payments for an invoice"},
    {"id": "GetInvoices", "method": "GET", "path": "/v1/invoices", "summary": "List all invoices"},
    {"id": "PostInvoices", "method": "POST", "path": "/v1/invoices", "summary": "Create an invoice"},
    {"id": "GetInvoiceitems", "method": "GET", "path": "/v1/invoiceitems", "summary": "List all invoice items"}
  ]
}
```

**Notable: `PostInvoicesInvoicePay` is absent from search results. The invoice pay action is not exposed.**

---

## stripe_api_details

### Details: PostCustomers

```json
{
  "id": "PostCustomers",
  "method": "POST",
  "path": "/v1/customers",
  "summary": "Create a customer",
  "description": "Creates a new customer object.",
  "tags": ["customer"],
  "keywords": ["v1", "customers", "post", "create", "customer"],
  "parameters": {
    "path": {},
    "query": {},
    "body": {
      "additional_emails": {
        "type": "object",
        "description": "Additional email addresses of the customer.",
        "required": false,
        "properties": {
          "cc": {"type": "array", "items": {"type": "string", "description": "", "required": false}, "description": "", "required": false},
          "to": {"type": "array", "items": {"type": "string", "description": "", "required": false}, "description": "", "required": false}
        }
      },
      "address": {"type": "object", "description": "", "required": false, "properties": {"city": {"type": "string", "description": "City, district, suburb, town, or village.", "required": false}, "country": {"type": "string", "description": "A freeform text field for the country...", "required": false}, "line1": {"type": "string", "description": "Address line 1...", "required": false}, "line2": {"type": "string", "description": "Address line 2...", "required": false}, "postal_code": {"type": "string", "description": "ZIP or postal code.", "required": false}, "state": {"type": "string", "description": "State, county, province, or region...", "required": false}}},
      "balance": {"type": "integer", "description": "An integer amount in cents...", "required": false},
      "description": {"type": "string", "description": "An arbitrary string...", "required": false},
      "email": {"type": "string", "description": "Customer's email address...", "required": false},
      "expand": {"type": "array", "items": {"type": "string", "description": "", "required": false}, "description": "Specifies which fields in the response should be expanded.", "required": false},
      "metadata": {"type": "object", "description": "", "required": false},
      "name": {"type": "string", "description": "The customer's full name or business name.", "required": false},
      "phone": {"type": "string", "description": "The customer's phone number.", "required": false},
      "shipping": {"type": "object", "description": "", "required": false, "properties": {"address": {"type": "object", "description": "Customer shipping address.", "required": true, "properties": {"city": {}, "country": {}, "line1": {}, "line2": {}, "postal_code": {}, "state": {}}}, "name": {"type": "string", "description": "Customer name.", "required": true}, "phone": {"type": "string", "description": "Customer phone (including extension).", "required": false}}},
      "tax_exempt": {"type": "string", "description": "...", "required": false, "enum": ["", "exempt", "none", "reverse"]}
    }
  },
  "required_permissions": ["customer_write"],
  "openapi_spec_version": "2026-08-26.preview"
}
```

(Body truncated for readability. Full body has ~30 parameters including `automatic_card_updates`, `business_name`, `cash_balance`, `cross_border_classification`, `external_customer_reference`, `individual_name`, `invoice_prefix`, `invoice_settings`, `next_invoice_sequence`, `onboarded_at`, `pay_immediately`, `payment_method`, `preferred_locales`, `source`, `tax`, `tax_code_business_designation`, `tax_id_data`, `test_clock`, `validate`.)

### Details: GetCustomersCustomer

```json
{
  "id": "GetCustomersCustomer",
  "method": "GET",
  "path": "/v1/customers/{id}",
  "summary": "Retrieve a customer",
  "description": "Retrieves a Customer object.",
  "tags": ["customer"],
  "keywords": ["v1", "customers", "get", "customer", "retrieve"],
  "parameters": {
    "path": {
      "id": {"type": "string", "description": "", "required": true}
    },
    "query": {
      "expand": {"type": "array", "description": "Specifies which fields in the response should be expanded.", "required": false}
    },
    "body": {}
  },
  "required_permissions": ["customer_limited_read"],
  "openapi_spec_version": "2026-08-26.preview"
}
```

### Details: GetIssuingCards (out-of-scope)

```json
{
  "id": "GetIssuingCards",
  "method": "GET",
  "path": "/v1/issuing/cards",
  "summary": "List all cards",
  "description": "Returns a list of Issuing `Card` objects...",
  "tags": ["issuing.card"],
  "keywords": ["v1", "issuing", "cards", "get", "list", "issuing.card"],
  "parameters": {
    "path": {},
    "query": {
      "cardholder": {"type": "string", "description": "Only return cards belonging to the Cardholder with the provided ID.", "required": false},
      "created": {"type": "string", "description": "Only return cards that were issued during the given date interval.", "required": false},
      "ending_before": {"type": "string", "description": "A cursor for use in pagination...", "required": false},
      "exp_month": {"type": "integer", "description": "...", "required": false},
      "exp_year": {"type": "integer", "description": "...", "required": false},
      "expand": {"type": "array", "description": "Specifies which fields in the response should be expanded.", "required": false},
      "last4": {"type": "string", "description": "...", "required": false},
      "limit": {"type": "integer", "description": "A limit on the number of objects to be returned. Limit can range between 1 and 100, and the default is 10.", "required": false},
      "starting_after": {"type": "string", "description": "A cursor for use in pagination...", "required": false},
      "status": {"type": "string", "description": "...", "required": false, "enum": ["active", "canceled", "inactive", "lost", "stolen"]},
      "type": {"type": "string", "description": "...", "required": false, "enum": ["physical", "virtual"]}
    },
    "body": {}
  },
  "required_permissions": ["issuing_card_read"],
  "openapi_spec_version": "2026-08-26.preview"
}
```

### Details: NonExistentOperation

```
Error: Operation 'NonExistentOperation' is not available. Use stripe_api_search to find available operations.
```

### Details: GetCustomersCustomerSources (legacy, not available)

```
Error: Operation 'GetCustomersCustomerSources' is not available. Use stripe_api_search to find available operations.
```

### Details: GetEvents (v1 events, not available)

```
Error: Operation 'GetEvents' is not available. Use stripe_api_search to find available operations.
```

### Details: PostInvoicesInvoicePay (invoice pay, not available)

```
Error: Operation 'PostInvoicesInvoicePay' is not available. Use stripe_api_search to find available operations.
```

### Details: GetBalanceTransactions

```json
{
  "id": "GetBalanceTransactions",
  "method": "GET",
  "path": "/v1/balance_transactions",
  "summary": "List all balance transactions",
  "description": "Returns a list of transactions that have contributed to the Stripe account balance...\n\nThe previous name of this endpoint was \"Balance history,\" and it used the path `/v1/balance/history`.",
  "tags": ["balance_transaction"],
  "keywords": ["v1", "balance_transactions", "balance", "transactions", "get", "list", "balance_transaction", "transaction"],
  "parameters": {
    "path": {},
    "query": {
      "created": {"type": "string", "description": "Only return transactions that were created during the given date interval.", "required": false},
      "currency": {"type": "string", "description": "...", "required": false},
      "ending_before": {"type": "string", "description": "A cursor for use in pagination...", "required": false},
      "expand": {"type": "array", "description": "...", "required": false},
      "limit": {"type": "integer", "description": "A limit on the number of objects to be returned. Limit can range between 1 and 100, and the default is 10.", "required": false},
      "payout": {"type": "string", "description": "...", "required": false},
      "source": {"type": "string", "description": "...", "required": false},
      "starting_after": {"type": "string", "description": "A cursor for use in pagination...", "required": false},
      "type": {"type": "string", "description": "Only returns transactions of the given type. One of: `tax_fund`, `adjustment`, `advance`...", "required": false}
    },
    "body": {}
  },
  "required_permissions": ["balance_read"],
  "openapi_spec_version": "2026-08-26.preview"
}
```

### Details: PostInvoicesInvoiceVoid

```json
{
  "id": "PostInvoicesInvoiceVoid",
  "method": "POST",
  "path": "/v1/invoices/{id}/void",
  "summary": "Void an invoice",
  "description": "Mark a finalized invoice as void. This cannot be undone. Voiding an invoice is similar to [deletion](/api/invoices/delete)...\n\nConsult with local regulations to determine whether and how an invoice might be amended, canceled, or voided in the jurisdiction you're doing business in...",
  "tags": ["invoice"],
  "keywords": ["v1", "invoices", "void", "post", "invoice"],
  "parameters": {
    "path": {
      "id": {"type": "string", "description": "", "required": true}
    },
    "query": {},
    "body": {
      "expand": {"type": "array", "items": {"type": "string", "description": "", "required": false}, "description": "Specifies which fields in the response should be expanded.", "required": false},
      "voided_at": {"type": "integer", "description": "Timestamp representing the time an invoice is voided outside of Stripe.", "required": false}
    }
  },
  "required_permissions": ["invoice_write"],
  "openapi_spec_version": "2026-08-26.preview"
}
```

### Details: PostRefunds

```json
{
  "id": "PostRefunds",
  "method": "POST",
  "path": "/v1/refunds",
  "summary": "Create a refund",
  "description": "When you create a new refund, you must specify a Charge or a PaymentIntent object on which to create it.\n\n...",
  "tags": ["refund"],
  "keywords": ["v1", "refunds", "post", "create", "refund"],
  "parameters": {
    "path": {},
    "query": {},
    "body": {
      "amount": {"type": "integer", "description": "", "required": false},
      "charge": {"type": "string", "description": "The identifier of the charge to refund.", "required": false},
      "currency": {"type": "string", "description": "...", "required": false},
      "expand": {"type": "array", "items": {...}, "description": "...", "required": false},
      "metadata": {"type": "object", "description": "", "required": false},
      "payment_intent": {"type": "string", "description": "The identifier of the PaymentIntent to refund.", "required": false},
      "reason": {"type": "string", "description": "...", "required": false, "enum": ["duplicate", "fraudulent", "requested_by_customer"]},
      "refund_application_fee": {"type": "boolean", "description": "...", "required": false},
      "reverse_transfer": {"type": "boolean", "description": "...", "required": false}
    }
  },
  "required_permissions": [],
  "openapi_spec_version": "2026-08-26.preview"
}
```

(Body truncated. Full has ~20 parameters including Connect-specific ones like `destination`, `on_behalf_of`, `transfer_data`, `transfer_group`.)

### Details: PostTestHelpersTestClocks

```json
{
  "id": "PostTestHelpersTestClocks",
  "method": "POST",
  "path": "/v1/test_helpers/test_clocks",
  "summary": "Create a test clock",
  "description": "Creates a new test clock that can be attached to new customers and quotes.",
  "tags": ["test_helpers.test_clock"],
  "keywords": ["v1", "test_helpers", "test", "helpers", "test_clocks", "clocks", "post", "create", "clock", "test_helpers.test_clock", "helpers.test"],
  "parameters": {
    "path": {},
    "query": {},
    "body": {
      "customer": {"type": "string", "description": "Existing customer this test clock will be attached to...", "required": false},
      "expand": {"type": "array", "items": {...}, "description": "...", "required": false},
      "frozen_time": {"type": "integer", "description": "The initial frozen time for this test clock.", "required": true},
      "name": {"type": "string", "description": "The name for this test clock.", "required": false}
    }
  },
  "required_permissions": ["billing_clock_write"],
  "openapi_spec_version": "2026-08-26.preview"
}
```

### Details: GetInvoicesSearch

```json
{
  "id": "GetInvoicesSearch",
  "method": "GET",
  "path": "/v1/invoices/search",
  "summary": "Search invoices",
  "description": "Search for invoices you've previously created using Stripe's [Search Query Language]...",
  "tags": ["invoice"],
  "keywords": ["v1", "invoices", "search", "get", "invoice"],
  "parameters": {
    "path": {},
    "query": {
      "expand": {"type": "array", "description": "...", "required": false},
      "limit": {"type": "integer", "description": "A limit on the number of objects...", "required": false},
      "page": {"type": "string", "description": "A cursor for pagination...", "required": false},
      "query": {"type": "string", "description": "The search query string...", "required": true}
    },
    "body": {}
  },
  "required_permissions": ["invoice_read"],
  "llm_context": "Find invoices with server-side search, filter on metadata and more. Look up https://docs.stripe.com/search to learn more about the query language.",
  "openapi_spec_version": "2026-08-26.preview"
}
```

**Note: `llm_context` appears at the top level of the details response too, not only in search results.**
