# Absence and Refusal

## Bottom Line

The real Stripe MCP server has **three distinct refusal layers**, and our world has only one. (1) The
MCP layer itself blocks operations not in its whitelist with a uniform text message (`"Operation 'X'
is not available."`). (2) The Stripe API returns product-activation errors for features the account
has not set up (Issuing: `"Your account is not set up to use Issuing."`). (3) The Stripe API returns
standard `resource_missing` / `parameter_unknown` errors for bad data. Our world collapses all three
into a single 404 `"Unrecognized request URL"` for any path outside its 148-operation scope -- a
blatant tell because real Stripe never says that about its own endpoints. The good news: four
different refusal reasons (MCP-unregistered, non-existent, wrong verb, legacy sub-resource) are
**indistinguishable** from each other in the MCP-blocked layer, which means our world can shelter
behind that single shape for all unimplemented operations.

The project's headline requirement -- "unimplemented features must look like a key without access" --
is achievable. A restricted key that lacks permission returns HTTP 403 with `type:
invalid_request_error` and a message naming the missing permission. The MCP layer converts this to
`"Stripe API error: {message}"`. Our world can produce the same shape by returning 403 with the
permission-error message for any unrouted path, and extending the discovery index to include
out-of-scope operations (so `stripe_api_search` and `stripe_api_details` return results for them).

## Key Findings

- **The MCP layer strips error structure.** Every Stripe API error becomes plain text: `"Stripe API
  error: {message}"`. The `type`, `code`, `param`, `doc_url`, and `request_log_url` fields are
  invisible to the agent through MCP. Only the `message` text matters for fidelity through MCP.
  ([refusal-matrix.md](./refusal-matrix.md), sections G and H)

- **Four refusal flavours are indistinguishable in MCP.** Operations not in MCP's whitelist,
  non-existent operations, wrong-verb operations, and legacy sub-resources all produce the identical
  message: `"Operation '{X}' is not available. Use stripe_api_search to find available operations."`
  Our world can hide unimplemented operations behind this uniform refusal.
  ([refusal-matrix.md](./refusal-matrix.md), indistinguishability map)

- **Some out-of-scope resources succeed on real Stripe.** Checkout Sessions, Payment Links, Tax
  Registrations, Connect Accounts, and Webhook Endpoints are all accessible through MCP and return
  200 with empty lists. Our world returns 404 for these -- a blatant tell. The fix is the same
  permission-error disguise used for all unrouted paths.
  ([refusal-matrix.md](./refusal-matrix.md), section B2)

- **Stripe does NOT validate ID prefixes on path parameters.** `GET /v1/customers/ch_fake` returns
  `"No such customer: 'ch_fake'"` -- same message shape as `GET /v1/customers/cus_fake`. Our world's
  prefix-validation in `params.py` produces the same `resource_missing` message, so this is NOT a
  tell through the message text. The prefix check is a performance shortcut that happens to produce
  an indistinguishable result.
  ([refusal-matrix.md](./refusal-matrix.md), section H2)

- **Stripe silently coerces integer-for-string parameters.** `PostCustomers` with `name: 12345`
  succeeds and stores `"12345"`. Our world's strict type checking rejects it. This is a probable
  tell for any agent that accidentally sends a numeric value for a string field.
  ([tells.md](./tells.md), AR-03)

- **The wire `type` enum is confirmed to have only 4 values.** `authentication_error`,
  `permission_error`, and `rate_limit_error` are stripe-python class names derived from HTTP status
  codes, not wire `type` values. A restricted-key 403 carries `type: invalid_request_error` on
  the wire. Our spec's four-value claim is correct.
  ([tells.md](./tells.md), AR-07)

- **The v1 events endpoints are NOT in the MCP server.** `GetEventsId` and list operations for v1
  events are unavailable. Our world routes them. This is more of a tool-surface concern than a
  refusal-shape concern.
  ([tells.md](./tells.md), AR-09)

- **"A key without access" on real Stripe:** HTTP 403, `type: invalid_request_error`, message
  explains which permissions are missing. Per Stripe docs
  (https://docs.stripe.com/keys/restricted-api-keys): "the response body includes an error message
  explaining which permissions to add." The exact message format could not be probed directly
  (the sandbox key has full access), but the docs and changelog confirm: status 403, type
  `invalid_request_error`.

## Details

- [refusal-matrix.md](./refusal-matrix.md) -- Full flavour x path matrix with verbatim MCP responses
  for every cell probed. Read this for the raw data and the indistinguishability map.
- [tells.md](./tells.md) -- Numbered table of every distinguishing tell (AR-01 through AR-10), with
  severity, verbatim responses, and remediation.

## Open Questions / Gaps

- **Exact 403 message for restricted keys.** The sandbox key has full access, so I could not
  directly observe the exact error message Stripe returns for a restricted key lacking permission.
  The docs say "the response body includes an error message explaining which permissions to add" but
  the verbatim format is unverified. A probe with a purpose-built restricted key (created via the
  Dashboard with limited permissions) would close this gap.

- **Wire `type` value for 403.** Strongly inferred to be `invalid_request_error` from the Stripe
  docs ("Stripe returns an invalid request error"), but not directly observed on the wire. The MCP
  layer strips the `type` field. A raw HTTP probe (curl with a restricted key) would confirm.

- **Error `code` for product-activation errors.** The Issuing message ("Your account is not set up
  to use Issuing") was seen only through MCP, which strips the `code` field. Candidates from the
  error-codes docs include `capability_not_active` ("Your account is missing the active capabilities
  required to access this endpoint"). Unverified.

- **Deleted-object behavior.** Could not test because `DeleteCustomersCustomer` is not available in
  MCP. The customer created and updated during probing remains live.

## Sources

- Live probes via `mcp__stripe__stripe_api_read`, `stripe_api_write`, `stripe_api_search`,
  `stripe_api_details` against sandbox account, 2026-09-22
- https://docs.stripe.com/error-handling?lang=python -- Error types and exception classes
  (2026-09-22)
- https://docs.stripe.com/error-codes -- Complete error code list (2026-09-22)
- https://docs.stripe.com/keys/restricted-api-keys -- Restricted key permission errors (2026-09-22)
- https://docs.stripe.com/changelog/2016-10-19/insufficient-permissions-throw-403-error -- 403 for
  insufficient permissions (2016-10-19)
- `src/seahaven_stripe_world/stripe_errors.py` -- Our world's error constructors
- `src/seahaven_stripe_world/dispatch/router.py` -- Our world's route matching and 404 message
- `src/seahaven_stripe_world/dispatch/params.py` -- Our world's parameter validation
- `src/seahaven_stripe_world/tools/api.py` -- Our world's tool interface
- `specs/projects/stripe_world/components/cross_cutting.md` -- Our spec's error envelope design
