# Refusal Matrix: Real Stripe MCP Probes

Verbatim results for every (refusal flavour x example path) cell probed on 2026-09-22.
Account: sandbox, `livemode: false`. Probed via `mcp__stripe__stripe_api_read` / `stripe_api_write` /
`stripe_api_search` / `stripe_api_details`.

MCP error strings are quoted exactly as returned by the tool. "Stripe API error:" prefix and the
trailing hint ("Use stripe_api_details...") are MCP-layer additions, not part of the Stripe JSON body.

---

## A. Permission-restricted / product-not-activated endpoint

| Operation ID | Path | Verbatim MCP result |
|---|---|---|
| `GetIssuingCards` | `GET /v1/issuing/cards` | **MCP error**: `Stripe API error: Your account is not set up to use Issuing. Please visit https://dashboard.stripe.com/issuing/overview to get started.` |
| `GetIssuingCardsCard` | `GET /v1/issuing/cards/{id}` with `ic_nonexistent123` | **MCP error**: `Stripe API error: No such issuing card: 'ic_nonexistent123'` |

**Observation**: The list endpoint checks product activation; the retrieve endpoint skips that check
and returns a standard `resource_missing`. The `required_permissions` field in `stripe_api_details`
shows `["issuing_card_read"]` for `GetIssuingCards` and `[]` (empty) for the retrieve — suggesting
the MCP server's permission model is orthogonal to the Stripe API's product-activation checks.

---

## B. In-Stripe-but-out-of-our-scope: MCP blocks the operation

These operations exist at Stripe but the MCP server does not expose them. The error is an MCP-layer
refusal, not a Stripe API error. No "Stripe API error:" prefix.

| Operation ID attempted | Path | Verbatim MCP result |
|---|---|---|
| `GetTerminalReaders` | `GET /v1/terminal/readers` | `Operation 'GetTerminalReaders' is not available. Use stripe_api_search to find available operations.` |
| `GetTreasuryFinancialAccounts` | `GET /v1/treasury/financial_accounts` | `Operation 'GetTreasuryFinancialAccounts' is not available. Use stripe_api_search to find available operations.` |
| `GetRadarValueLists` | `GET /v1/radar/value_lists` | `Operation 'GetRadarValueLists' is not available. Use stripe_api_search to find available operations.` |
| `GetFinancialConnectionsAccounts` | `GET /v1/financial_connections/accounts` | `Operation 'GetFinancialConnectionsAccounts' is not available. Use stripe_api_search to find available operations.` |
| `GetClimateOrders` | `GET /v1/climate/orders` | `Operation 'GetClimateOrders' is not available. Use stripe_api_search to find available operations.` |
| `GetEventsId` | `GET /v1/events/{id}` | `Operation 'GetEventsId' is not available. Use stripe_api_search to find available operations.` |
| `DeleteCustomersCustomer` | `DELETE /v1/customers/{customer}` | `Operation 'DeleteCustomersCustomer' is not available. Use stripe_api_search to find available operations.` |
| `PostPaymentIntents` | `POST /v1/payment_intents` | `Operation 'PostPaymentIntents' is not available. Use stripe_api_search to find available operations.` |
| `PostIssuingCards` | `POST /v1/issuing/cards` | `Operation 'PostIssuingCards' is not available. Use stripe_api_search to find available operations.` |

---

## B2. In-Stripe-but-out-of-our-scope: MCP exposes and Stripe API succeeds

These operations exist in the MCP's operation set AND work on the sandbox account. An agent can reach
them — they return real data (empty lists in a fresh sandbox).

| Operation ID | Path | Verbatim MCP result |
|---|---|---|
| `GetCheckoutSessions` | `GET /v1/checkout/sessions` | `{"object":"list","data":[],"has_more":false,"url":"/v1/checkout/sessions"}` |
| `GetPaymentLinks` | `GET /v1/payment_links` | `{"object":"list","data":[],"has_more":false,"url":"/v1/payment_links"}` |
| `GetTaxRegistrations` | `GET /v1/tax/registrations` | `{"object":"list","data":[],"has_more":false,"url":"/v1/tax/registrations"}` |
| `GetAccounts` | `GET /v1/accounts` | `{"object":"list","data":[],"has_more":false,"url":"/v1/accounts"}` |
| `GetWebhookEndpoints` | `GET /v1/webhook_endpoints` | Available and working (confirmed via search) |

---

## C. Non-existent operation (no such path at Stripe)

| Operation ID attempted | Verbatim MCP result |
|---|---|
| `GetNonexistentFooBar` | `Operation 'GetNonexistentFooBar' is not available. Use stripe_api_search to find available operations.` |

**CRITICAL**: Indistinguishable from flavour B (MCP-blocked operations).

---

## D. Wrong verb on a real path

| Operation ID attempted | Path | Verbatim MCP result |
|---|---|---|
| `DeletePricesPrice` | `DELETE /v1/prices/{price}` | `Operation 'DeletePricesPrice' is not available. Use stripe_api_search to find available operations.` |

**CRITICAL**: Indistinguishable from flavours B and C.

---

## E. `test_helpers/*` paths

| Operation ID | Path | Verbatim MCP result |
|---|---|---|
| `PostTestHelpersTestClocks` | `POST /v1/test_helpers/test_clocks` | **Success**: `{"id":"clock_1UIZCkHIBYZYyePracvCSCfG","object":"test_helpers.test_clock","created":1790104694,...,"status":"ready"}` |

**Observation**: Test helpers are fully functional through MCP.

---

## F. Legacy sub-resources (customer sources, cards, bank accounts)

| Operation ID attempted | Verbatim MCP result |
|---|---|
| (searched via `stripe_api_search` for "customer sources", "customer cards", "customer bank accounts") | No legacy sub-resource operations found in MCP's operation set. Search returns only v2 or issuing results. |

**Observation**: Legacy sub-resources (`/v1/customers/{id}/sources`, `/v1/customers/{id}/cards`,
`/v1/customers/{id}/bank_accounts`) are not exposed through MCP at all. An agent that tries
fabricated operation IDs gets the standard "Operation not available" MCP error (indistinguishable
from flavours B/C/D).

---

## G. Unknown or malformed parameters

| Operation | What was sent | Verbatim MCP result |
|---|---|---|
| `PostCustomers` | `totally_fake_param: "value123"` | **MCP error**: `Stripe API error: Received unknown parameter: totally_fake_param` |
| `PostCustomers` | `tax_exempt: "totally_invalid_value"` | **MCP error**: `Stripe API error: Invalid tax_exempt: must be one of none, reverse, or exempt` |
| `PostCustomers` | `expand: ["totally_invalid_expand_path"]` | **MCP error**: `Stripe API error: This property cannot be expanded (totally_invalid_expand_path).` |
| `GetCustomersCustomer` | `expand: ["invalid.deep.path"]` | **MCP error**: `Stripe API error: This property cannot be expanded (invalid).` |
| `PostProducts` | (no `name` — required) | **MCP-layer error** (no "Stripe API error:" prefix): `Missing required body parameters: name` |
| `PostCustomers` | `name: 12345` (integer for string) | **Success**: Stripe accepted and coerced to `"12345"`. Customer created. |
| `PostCustomersCustomer` | `email: "not-an-email"` | **MCP error**: `Stripe API error: Invalid email address: not-an-email` |
| `PostRefunds` | (no `charge` or `payment_intent`) | **MCP error**: `Stripe API error: One of the following params should be provided for this request: payment_intent or charge.` |
| `GetSubscriptions` | `status: "totally_invalid"` | **MCP error**: `Stripe API error: Invalid status: must be one of active, past_due, unpaid, canceled, incomplete, incomplete_expired, trialing, paused, all, or ended` |
| `PostCustomers` | `idempotency_key: "test"` | **MCP error**: `Stripe API error: Received unknown parameter: idempotency_key` |

**Key observations**:
- The MCP layer has its OWN required-parameter check that fires BEFORE reaching Stripe. Its
  message format ("Missing required body parameters: name") differs from Stripe's own
  ("Missing required param: name.").
- Stripe silently coerces `name: 12345` (integer) to string. A strict world that rejects this
  is distinguishable.
- `idempotency_key` is NOT a parameter the MCP accepts in the `parameters` object. It is
  unknown to the Stripe API as a body parameter (it is an HTTP header in real Stripe).

---

## H. Non-existing or deleted object IDs

### H1. Correct prefix, never existed

| Operation | ID passed | Verbatim MCP error message (after "Stripe API error: ") |
|---|---|---|
| `GetCustomersCustomer` | `cus_nonexistent999999` | `No such customer: 'cus_nonexistent999999'` |
| `GetPaymentIntentsIntent` | `pi_nonexistent12345` | `No such payment_intent: 'pi_nonexistent12345'` |
| `GetChargesCharge` | `ch_nonexistent12345` | `No such charge: 'ch_nonexistent12345'` |
| `GetProductsId` | `prod_nonexistent12345` | `No such product: 'prod_nonexistent12345'` |
| `GetInvoicesInvoice` | `in_nonexistent12345` | `No such invoice: 'in_nonexistent12345'` |
| `GetSubscriptionsSubscriptionExposedId` | `sub_nonexistent12345` | `No such subscription: 'sub_nonexistent12345'` |
| `GetRefundsRefund` | `re_nonexistent12345` | `No such refund: 're_nonexistent12345'` |
| `GetDisputesDispute` | `dp_nonexistent12345` | `No such dispute: 'dp_nonexistent12345'` |
| `PostRefunds` (body param) | `ch_nonexistent99999` | `No such charge: 'ch_nonexistent99999'` |

### H2. Wrong prefix

| Operation | ID passed (wrong prefix) | Verbatim MCP error message |
|---|---|---|
| `GetCustomersCustomer` | `ch_nonexistent12345` | `No such customer: 'ch_nonexistent12345'` |
| `GetCustomersCustomer` | `pi_nonexistent12345` | `No such customer: 'pi_nonexistent12345'` |
| `GetCustomersCustomer` | `totally_bogus_id` | `No such customer: 'totally_bogus_id'` |

**CRITICAL**: Stripe does NOT validate ID prefixes on path parameters. It returns the same
`resource_missing` message regardless of prefix. The object name in the message comes from the
endpoint, not from the ID prefix.

### H3. Deleted object

| Operation | ID passed | Verbatim MCP result |
|---|---|---|
| `GetCustomersCustomer` | `cus_VJBZ9ui7let6R1` (created, then attempted delete — delete unavailable via MCP) | **Success**: customer returned normally (deletion could not be tested because `DeleteCustomersCustomer` is not available in MCP) |

---

## Indistinguishability Map

The following refusal flavours are **indistinguishable** from each other through MCP:

| Group | Flavours | Message shape |
|---|---|---|
| **MCP-blocked** | B (out-of-scope MCP-blocked), C (non-existent), D (wrong verb), F (legacy sub-resources) | `Operation '{X}' is not available. Use stripe_api_search to find available operations.` |
| **Stripe resource_missing** | H1 (correct prefix), H2 (wrong prefix), H3 (deleted, if testable) | `Stripe API error: No such {object}: '{id}'` |
| **Stripe unknown param** | G (unknown param) | `Stripe API error: Received unknown parameter: {name}` |

**The MCP-blocked group is the one our world must hide behind.** Four distinct "reasons" for
refusal produce the same single message. This is the shelter the project needs — unimplemented
operations can produce the same text without distinguishing them from non-existent or wrong-verb
calls.
