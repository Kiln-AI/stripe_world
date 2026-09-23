# Consolidated Tell Register

100 raw tells from five lanes collapsed into **70 distinct tells** after cross-lane
de-duplication (30 raw tells merged into 17 rows that cite multiple IDs). Two entries
were confirmed non-tells (AR-07, DT-24) and are excluded.

**Severity:** 28 blatant, 27 probable, 15 subtle.

Ordered by severity (blatant first), then cost to close within each severity band.
Merged rows cite all original lane IDs so the deep docs stay reachable.

## Dispositions

**This file is the project's living checklist, not a frozen research artifact.** Functional spec §3
requires every row to end in one of three dispositions, and the `Disposition` column is where that
is recorded. There is deliberately no second copy of this list anywhere — two hand-maintained lists
that are supposed to agree will not.

| Value | Meaning |
|---|---|
| `open` | Not yet resolved. The default, and the only value that represents outstanding work |
| `closed` | The world now matches the real server, and a test named for this row's id holds it closed |
| `declared` | We will not match it. Recorded in functional spec §13 with a reason and an owner |
| `not-a-tell` | Probing showed we already match, or the premise was wrong |

Each implementation phase updates the rows it touches (implementation plan, "How a phase is done").
A row left `open` at the end of a phase that touched it is unfinished work, not an oversight to
discover later.

## Blatant (28)

| ID | Tell | Lane(s) | Cost | Disposition |
|---|---|---|---|---|
| EC-04 | Response headers (`Stripe-Version`, `Request-Id`) visible in our world; real MCP surfaces none | envelope | trivial | open |
| TS-13, DT-02 | Search output missing `{openapi_spec_version, data}` wrapper; ours returns bare list | tool-surface, discovery | trivial | open |
| TS-14, DT-03 | Search results missing `id` (operation ID) and optional `llm_context` fields | tool-surface, discovery | trivial | open |
| TS-08 | `stripe_api_read` description text doesn't match (references paths, not operation IDs) | tool-surface | trivial | open |
| TS-09 | `stripe_api_search` description text doesn't match (keyword search vs intent+resource) | tool-surface | trivial | open |
| TS-24 | `stripe_api_write` description text doesn't match | tool-surface | trivial | open |
| TS-25 | `stripe_api_details` description text doesn't match | tool-surface | trivial | open |
| AS-01 | Customer ID suffix is 24 chars; real is 14. `prod_` and `si_` also 14 (Format A) | account-stats | trivial | open |
| AS-03 | Account object missing `controller`, `external_accounts`, `requirements`, `future_requirements`, `tos_acceptance` | account-stats | trivial | open |
| AS-04 | Account `capabilities` should be `{}` on a fresh sandbox, not `{"card_payments":"active",...}` | account-stats | trivial | open |
| AS-05 | Account `charges_enabled`/`payouts_enabled` should be `false`; ours are `true` | account-stats | trivial | open |
| AS-06 | `business_profile` missing 5 null sub-fields; `name` is `"Test Business"` but should be `null` | account-stats | trivial | open |
| AS-08 | Account ID `acct_1SWTestAccount00` is a human-readable constant; real IDs are 16 random alphanumerics | account-stats | trivial | open |
| TS-10, TS-11, EC-01 | Success response is bare JSON body; ours wraps in `{status, body, headers}` | tool-surface, envelope | moderate | open |
| TS-12, EC-02 | Errors are MCP tool errors (plain text); ours returns structured `{status, body}` with `error` object | tool-surface, envelope | moderate | open |
| TS-15, DT-14 | Details response has 12 keys (`id`, `tags`, `keywords`, `required_permissions`, ...); ours has 6 with `operation_id` not `id` | tool-surface, discovery | moderate | open |
| TS-16, DT-15 | Details parameters grouped as `{path:{}, query:{}, body:{}}` dicts; ours is a flat list | tool-surface, discovery | moderate | open |
| TS-18, DT-04 | Path placeholders normalized to `{id}` everywhere; ours uses `{customer}`, `{invoice}`, etc. | tool-surface, discovery | moderate | open |
| TS-06, DT-01 | Search tool takes `intent` + `resource` + `limit`; ours takes free-text `query` | tool-surface, discovery | moderate | open |
| TS-02 | `get_stripe_account_info` does not exist on real MCP; real uses `list_available_accounts_or_orgs` | tool-surface | moderate | open |
| TS-03 | `list_available_accounts_or_orgs` missing; it gates every other tool (provides `stripe_context`/`livemode`) | tool-surface | moderate | open |
| AR-01 | Out-of-scope paths with product-activation errors (Issuing, etc.) get 404 "Unrecognized"; should be permission-style error | absence-refusal | moderate | open |
| AR-02 | Out-of-scope paths that succeed on real Stripe (Checkout, Payment Links, etc.) get 404; should be permission error | absence-refusal | moderate | open |
| AS-02 | Structured IDs (Format B) embed a 10-char account fragment at a fixed position; ours are fully random | account-stats | moderate | open |
| TS-04, TS-05, TS-07, TS-26, DT-13 | Read/write/details tools use `stripe_api_operation_id` + `stripe_context` + `livemode`; ours use `method` + `path` | tool-surface, discovery | hard | open |
| TS-01 | Tool count: real MCP has 10 tools; ours has 5 (6 missing, 1 extra) | tool-surface | hard | not-a-tell |
| AR-05, AR-06, DT-06, DT-07, DT-08, DT-09, DT-10, DT-11, DT-12, DT-19, DT-25 | Discovery covers entire Stripe API (Issuing, Connect, Checkout, Treasury, Tax, v2, ...); ours covers only 148 ops | absence-refusal, discovery | hard | open |
| EC-03, AR-08 | `idempotency_key` not exposed through MCP (it's an HTTP header); ours accepts it as tool parameter | envelope, absence-refusal | decision | closed |

## Probable (27)

| ID | Tell | Lane(s) | Cost | Disposition |
|---|---|---|---|---|
| TS-17, DT-16 | Details returns full multi-paragraph description; ours truncates to first sentence | tool-surface, discovery | trivial | open |
| EC-06, EC-07 | Out-of-range `limit` (0, -1, 101, 200) silently clamped to 1..100; ours returns 400 | envelope | trivial | open |
| EC-08, DT-21 | `openapi_spec_version` is `"2026-08-26.preview"`; ours says `"2026-08-26.dahlia"` | envelope, discovery | trivial | declared |
| AR-03 | Integer-for-string params (e.g. `name: 12345`) silently coerced; ours rejects with 400 | absence-refusal | trivial | open |
| DT-05 | Default search result count is 5 (configurable 1-20); ours is fixed at 10 | discovery | trivial | open |
| AS-07 | Account object has `metadata: {}`; real account response has no `metadata` key | account-stats | trivial | open |
| AS-09 | Account `created` is `1704067200` (midnight 2024-01-01); conspicuously round | account-stats | trivial | open |
| AS-11 | `customer.invoice_prefix` should be 8 chars from `[A-Z0-9]`; format unverified in our code | account-stats | trivial | open |
| AS-12 | `customer.customer_account` field missing (always `null` on standard accounts) | account-stats | trivial | open |
| AS-13 | `customer.test_clock` and `subscription.test_clock` fields missing (always `null`) | account-stats | trivial | open |
| AS-14 | `product.attributes` (`[]`) and `product.type` (`"service"`) fields missing | account-stats | trivial | declared |
| AS-22 | `subscription.billing_mode` field missing (`{"type":"flexible",...}` or `{"type":"classic"}`) | account-stats | trivial | open |
| AS-23 | `subscription.trial_settings` field missing | account-stats | trivial | open |
| AS-24 | `subscription.cancellation_details` field missing (null sub-fields) | account-stats | trivial | open |
| AS-25 | `subscription.managed_payments` and `payment_intent.managed_payments` missing (`{"enabled":false}`) | account-stats | trivial | open |
| AS-27 | Structured IDs encode creation time; ours have no time correlation | account-stats | trivial | open |
| EC-05, DT-18 | Error messages carry MCP guidance suffix ("Use stripe_api_details..."); ours don't | envelope, discovery | moderate | open |
| TS-19 | `manage_stripe_accounts` tool missing (returns reconsent URL) | tool-surface | moderate | open |
| TS-20 | `search_stripe_documentation` tool missing (returns docs results) | tool-surface | moderate | open |
| TS-21 | `stripe_analytics` tool missing (Sigma query interface) | tool-surface | moderate | open |
| TS-22 | `stripe_implementation_planner` tool missing (decision tree + guide_id) | tool-surface | moderate | open |
| TS-23 | `send_stripe_mcp_feedback` tool missing (accepts feedback) | tool-surface | moderate | open |
| DT-17 | Real details nests parameters to arbitrary depth (156K chars for CheckoutSessions); ours stops at depth 1 | discovery | moderate | open |
| DT-20 | `required_permissions` array missing from details response | discovery | moderate | open |
| DT-23 | Some routed ops hidden on real server (`PostInvoicesInvoicePay`); real MCP curates its whitelist | discovery | moderate | open |
| AR-09, DT-22 | v1 events endpoints hidden in real MCP (only v2 event destinations); ours routes them | absence-refusal, discovery | decision | closed |
| AR-10, AS-10 | Successive creates share identical `created` timestamp (frozen clock); real timestamps advance | absence-refusal, account-stats | decision | declared |

## Subtle (15)

| ID | Tell | Lane(s) | Cost | Disposition |
|---|---|---|---|---|
| AR-04 | 404 message missing trailing help text ("If you are trying to list objects...") | absence-refusal | trivial | open |
| EC-09 | Deleted customer stub missing `cache_context_key` field | envelope | trivial | open |
| EC-10 | JSON field ordering may differ between our serialization and real API | envelope | trivial | open |
| AS-15 | `product.tax_details` field missing (always `null`) | account-stats | trivial | declared |
| AS-16 | `balance_transaction.fee_details` structure: must include all 5 sub-fields exactly | account-stats | trivial | open |
| AS-17 | `charge.outcome.risk_score` varies per charge on real Stripe (0-99); ours may be fixed | account-stats | trivial | open |
| AS-18 | `charge.payment_method_details.card` has 20+ sub-fields; ours has fewer | account-stats | trivial | open |
| AS-19 | `charge.receipt_url` absent or differs from real `pay.stripe.com/receipts/...` URL | account-stats | trivial | open |
| AS-20 | `account.settings.payouts.schedule.delay_days` is 7 (CA sandbox); ours is 2 | account-stats | trivial | open |
| AS-21 | `invoice.account_country`/`account_name` must be derived from account object, not hardcoded | account-stats | trivial | open |
| AS-26 | `payment_intent.payment_details` field missing on subscription-created PIs | account-stats | trivial | open |
| AS-28 | `charge.radar_options` field missing (empty `{}`) | account-stats | trivial | open |
| AS-29 | `refund.customer_account` field missing (always `null`) | account-stats | trivial | open |
| AS-30 | `refund.destination_details` card structure missing | account-stats | trivial | open |
| AS-31 | `setup_intent.allowed_payment_method_types` and `excluded_payment_method_types` missing | account-stats | trivial | open |
