# The Error Envelope, `type`, `code`, `decline_code`, and HTTP Status Mapping

## The envelope shape — verbatim from `spec3.json`

Every error response body is `{"error": <APIErrors object>}`. Confirmed directly:

```json
// components.schemas.error
{
  "description": "An error response from the Stripe API",
  "properties": { "error": { "$ref": "#/components/schemas/api_errors" } },
  "required": ["error"],
  "type": "object"
}
```

The full `api_errors` (title `APIErrors`) schema, **every field, verbatim**:

```json
{
  "properties": {
    "advice_code": {
      "description": "For card errors resulting from a card issuer decline, a short string indicating how to proceed with an error if they provide one.",
      "maxLength": 5000, "type": "string"
    },
    "charge": {
      "description": "For card errors, the ID of the failed charge.",
      "maxLength": 5000, "type": "string"
    },
    "code": {
      "description": "For some errors that could be handled programmatically, a short string indicating the error code reported.",
      "maxLength": 5000, "type": "string"
    },
    "decline_code": {
      "description": "For card errors resulting from a card issuer decline, a short string indicating the card issuer's reason for the decline if they provide one.",
      "maxLength": 5000, "type": "string"
    },
    "doc_url": {
      "description": "A URL to more information about the error code reported.",
      "maxLength": 5000, "type": "string"
    },
    "message": {
      "description": "A human-readable message providing more details about the error. For card errors, these messages can be shown to your users.",
      "maxLength": 40000, "type": "string"
    },
    "network_advice_code": {
      "description": "For card errors resulting from a card issuer decline, a 2 digit code which indicates the advice given to merchant by the card network on how to proceed with an error.",
      "maxLength": 5000, "type": "string"
    },
    "network_decline_code": {
      "description": "For payments declined by the network, an alphanumeric code which indicates the reason the payment failed.",
      "maxLength": 5000, "type": "string"
    },
    "param": {
      "description": "If the error is parameter-specific, the parameter related to the error. For example, you can use this to display a message near the correct form field.",
      "maxLength": 5000, "type": "string"
    },
    "payment_intent": { "$ref": "#/components/schemas/payment_intent" },
    "payment_method": { "$ref": "#/components/schemas/payment_method" },
    "payment_method_type": {
      "description": "If the error is specific to the type of payment method, the payment method type that had a problem. This field is only populated for invoice-related errors.",
      "maxLength": 5000, "type": "string"
    },
    "request_log_url": {
      "description": "A URL to the request log entry in your dashboard.",
      "maxLength": 5000, "type": "string"
    },
    "setup_intent": { "$ref": "#/components/schemas/setup_intent" },
    "source": {
      "anyOf": [
        { "$ref": "#/components/schemas/bank_account" },
        { "$ref": "#/components/schemas/card" },
        { "$ref": "#/components/schemas/source" }
      ],
      "description": "The source object for errors returned on a request involving a source.",
      "x-stripeBypassValidation": true
    },
    "type": {
      "description": "The type of error returned. One of `api_error`, `card_error`, `idempotency_error`, or `invalid_request_error`",
      "enum": ["api_error", "card_error", "idempotency_error", "invalid_request_error"],
      "type": "string"
    }
  },
  "required": ["type"],
  "title": "APIErrors",
  "x-expandableFields": ["payment_intent", "payment_method", "setup_intent", "source"]
}
```

Notes on fields **not** requested explicitly in the focus paragraph but present and worth carrying
forward: `advice_code`, `network_advice_code`, `network_decline_code`, `payment_method`,
`payment_method_type` — these are recent additions (network-level decline detail, added as card
networks started supplying richer decline metadata) and are good signal that the error envelope has
grown over time; a Seahaven mock targeting `2026-08-26.dahlia` should include them even though
older blog posts describing "the Stripe error object" predate them. `charge` is a plain string
(not expandable — see `expand.md`), while `payment_intent`/`payment_method`/`setup_intent`/`source`
are full expandable sub-objects.

**Only `type` is `required`.** Every other field is optional/nullable-by-omission, confirmed also by
the `stripe-python` `ErrorObject._refresh_from` defaults block (`_error_object.py`), which explicitly
sets all 17 non-`type` fields to `None` as defaults "because the API will omit attributes in error
objects when they have a null value."

## The `type` enum — only 4 wire values (important, corrects a common misconception)

**The wire-level `type` field has exactly four possible values as of API version `2026-08-26.dahlia`:
`api_error`, `card_error`, `idempotency_error`, `invalid_request_error`.** This is directly from the
schema `enum` above, and it is *not* a documentation artifact — it's corroborated end-to-end by two
independent sources:

1. **A real observed 429 response body**, quoted verbatim in a `franckverrot/terraform-provider-stripe`
   GitHub issue (via WebSearch):
   ```json
   {"code":"rate_limit","status":429,"message":"Testmode request rate limit exceeded, the rate limits in testmode are lower than livemode. You can learn more about rate limits here https://stripe.com/docs/rate-limits.","type":"invalid_request_error"}
   ```
   Note `type` is `"invalid_request_error"`, **not** `"rate_limit_error"` — rate limiting is
   distinguished by HTTP status (429) and `code: "rate_limit"`, not by a dedicated wire `type`.

2. **`stripe-python`'s own exception hierarchy** (`_error.py`) defines many more exception *classes*
   than the wire has `type` values — `AuthenticationError`, `PermissionError`, `RateLimitError`,
   `APIConnectionError`, `SignatureVerificationError` all exist as Python classes, but a comment in
   the file marks only one class (`TemporarySessionExpiredError`) as genuinely
   "generated from our OpenAPI spec" for v2's `type` switch — the v1 classes above are constructed
   **purely from HTTP status code**, never by switching on a wire `type` value (see mapping table
   below). This means "authentication_error" / "permission_error" / "rate_limit_error" are **client-
   side conveniences layered on top of HTTP status codes**, not values that ever appear in the
   `error.type` JSON field itself for v1. (v2 errors *do* get a few more type-like discriminators —
   `rate_limit` and `temporary_session_expired` — via `specific_v2_api_error`'s explicit `elif type
   == "rate_limit":` / `elif type == "temporary_session_expired":` branches, so the v2 API surface is
   slightly richer here than v1; that's a v1/v2 asymmetry worth remembering.)

## HTTP status → client exception mapping — the authoritative, client-observed table

Directly from `stripe-python`'s `specific_v1_api_error` (`_api_requestor.py` lines 430-482),
reproduced in full because this is exactly the kind of precise, load-bearing logic a faithful mock
needs to reproduce:

```python
def specific_v1_api_error(self, rbody, rcode, resp, rheaders, error_data):
    # Rate limits were previously coded as 400's with code 'rate_limit'
    if rcode == 429 or (rcode == 400 and error_data.get("code") == "rate_limit"):
        return error.RateLimitError(...)
    elif rcode in [400, 404]:
        if error_data.get("type") == "idempotency_error":
            return error.IdempotencyError(...)
        else:
            return error.InvalidRequestError(...)   # includes param + code
    elif rcode == 401:
        return error.AuthenticationError(...)
    elif rcode == 402:
        return error.CardError(...)                  # includes param + code
    elif rcode == 403:
        return error.PermissionError(...)
    else:
        return error.APIError(...)                   # covers 409, 5xx, anything else
```

Reading this as a status→meaning table:

| HTTP status | Wire `type` seen | Client exception | Notes |
|---|---|---|---|
| 400 | `idempotency_error` | `IdempotencyError` | e.g. same key, different params |
| 400 | `invalid_request_error` (or `code=="rate_limit"` legacy path) | `InvalidRequestError` or `RateLimitError` | legacy rate-limit-as-400 still special-cased |
| 402 | `card_error` | `CardError` | carries `param` + `code`; this is where `decline_code` lives |
| 401 | (any) | `AuthenticationError` | bad/missing API key |
| 403 | (any) | `PermissionError` | key valid but lacks permission (e.g. restricted key) |
| 404 | `invalid_request_error` (usually) | `InvalidRequestError` | "No such \<resource\>" |
| 409 | (any) | `APIError` (generic) | **not specially modeled** — falls to the catch-all; this is also the idempotency **in-flight-conflict** status (`idempotency_key_in_use`, see `idempotency.md`) — client treats it as a retryable network condition (see `_should_retry`), not a distinct semantic error class |
| 429 | `invalid_request_error` (legacy) or v2 `rate_limit` | `RateLimitError` | see the real observed body above |
| 5xx | (any) | `APIError` | retried automatically by the client (`status_code >= 500` in `_should_retry`) |

This table is the single most load-bearing artifact in this subtopic for a Seahaven mock: it is the
actual decision tree an official client uses, reverse-engineered from source rather than paraphrased
from a blog post.

## Rate limits (numeric)
Via WebSearch synthesis (docs.stripe.com/rate-limits, not independently re-fetched this session):
**100 requests/second in live mode, 25 requests/second in test/sandbox mode** as the basic global
limiter; individual endpoints can have their own (generally lower, e.g. ~25 req/s) per-resource caps
that count against the global figure; the Meter Event ingestion endpoint is called out as having a
much higher limit (~1,000/s live). Treat the exact numbers as corroborated-but-not-independently-
reverified against Stripe's own current page.

## `StripeInvalidRequestError` `param` naming for nested/array params
Via WebSearch synthesis (no direct docs.stripe.com fetch): the `param` field uses PHP-style bracket
notation for nested/array parameters, e.g. `items[0][price]` or `line_items[0][currency]` — i.e. the
same `deepObject`/bracket encoding the OpenAPI spec itself uses for `style: deepObject` parameters
(as seen on `expand[]` and the `created` range-filter object in `pagination.md`). I did not find a
single authoritative doc excerpt pinning down every rule (e.g. how a bad key *inside* a `metadata`
object is named) — flagged as a partial gap; the bracket-notation pattern itself is well corroborated
across multiple independent GitHub issues quoting real error payloads.

## Full `code` enumeration (~215 values)

Sourced from `stripe-go`'s `error.go` (`github.com/stripe/stripe-go`, fetched via WebFetch of the
raw file), which is itself generated from the same Stripe-internal error-code registry that produces
`docs.stripe.com/error-codes` (the OpenAPI `code` field description explicitly points there). I could
not fetch `docs.stripe.com/error-codes` directly this session to pull per-code *descriptions*, so
this list gives the **exact code strings** (high confidence — generated/machine-sourced) without
per-code prose; treat prose meanings as inferable from the name and cross-check against
docs.stripe.com/error-codes before writing a mock's user-facing messages.

```
acss_debit_session_incomplete, api_key_expired, account_closed, account_country_invalid_address,
account_error_country_change_requires_additional_steps, account_information_mismatch,
account_invalid, account_number_invalid, account_token_required_for_v2_account, action_blocked,
alipay_upgrade_required, amount_too_large, amount_too_small, anomalous_money_movement_request,
application_fees_not_allowed, approval_required, authentication_failure, authentication_required,
balance_insufficient, balance_invalid_parameter, bank_account_bad_routing_numbers,
bank_account_declined, bank_account_exists, bank_account_restricted, bank_account_unusable,
bank_account_unverified, bank_account_verification_failed, billing_invalid_mandate,
bitcoin_upgrade_required, capability_not_active, capture_charge_authorization_expired,
capture_unauthorized_payment, card_decline_rate_limit_exceeded, card_declined,
cardholder_phone_number_required, charge_already_captured, charge_already_refunded,
charge_disputed, charge_exceeds_source_limit, charge_exceeds_transaction_limit,
charge_expired_for_capture, charge_invalid_parameter, charge_not_refundable,
clearing_code_unsupported, country_code_invalid, country_unsupported, coupon_expired,
customer_max_payment_methods, customer_max_subscriptions, customer_session_expired,
customer_tax_location_invalid, debit_not_authorized, email_invalid, expired_card,
expired_payment_method, failed_tax_calculation,
financial_account_balance_does_not_support_currency, financial_account_capability_not_enabled,
financial_account_capability_restricted, financial_connections_account_inactive,
financial_connections_account_pending_account_numbers,
financial_connections_account_unavailable_account_numbers,
financial_connections_no_successful_transaction_refresh, forwarding_api_inactive,
forwarding_api_invalid_parameter, forwarding_api_retryable_upstream_error,
forwarding_api_upstream_connection_error, forwarding_api_upstream_connection_timeout,
forwarding_api_upstream_error, idempotency_key_in_use, incorrect_address, incorrect_cvc,
incorrect_number, incorrect_postal_code, incorrect_zip,
india_recurring_payment_mandate_canceled, instant_payouts_config_disabled,
instant_payouts_currency_disabled, instant_payouts_limit_exceeded,
instant_payouts_unsupported, insufficient_funds, intent_invalid_state,
intent_verification_method_missing, invalid_cvc, invalid_canceled_subscription_fields,
invalid_card_type, invalid_characters, invalid_charge_amount, invalid_expiry_month,
invalid_expiry_year, invalid_mandate_reference_prefix_format, invalid_number,
invalid_source_usage, invalid_tax_location, invoice_no_customer_line_items,
invoice_no_payment_method_types, invoice_no_subscription_line_items, invoice_not_editable,
invoice_on_behalf_of_not_editable, invoice_payment_intent_requires_action,
invoice_upcoming_none, livemode_mismatch, lock_timeout, missing, no_account,
not_allowed_on_standard_account, out_of_inventory, ownership_declaration_not_allowed,
parameter_invalid_empty, parameter_invalid_integer, parameter_invalid_string_blank,
parameter_invalid_string_empty, parameter_missing, parameter_unknown, parameters_exclusive,
payment_intent_action_required, payment_intent_authentication_failure,
payment_intent_incompatible_payment_method, payment_intent_invalid_parameter,
payment_intent_konbini_rejected_confirmation_number, payment_intent_mandate_invalid,
payment_intent_payment_attempt_expired, payment_intent_payment_attempt_failed,
payment_intent_rate_limit_exceeded, payment_intent_unexpected_state,
payment_method_bank_account_already_verified, payment_method_bank_account_blocked,
payment_method_billing_details_address_missing, payment_method_configuration_failures,
payment_method_currency_mismatch, payment_method_customer_decline,
payment_method_invalid_parameter, payment_method_invalid_parameter_testmode,
payment_method_microdeposit_failed, payment_method_microdeposit_processing_error,
payment_method_microdeposit_verification_amounts_invalid,
payment_method_microdeposit_verification_amounts_mismatch,
payment_method_microdeposit_verification_attempts_exceeded,
payment_method_microdeposit_verification_descriptor_code_mismatch,
payment_method_microdeposit_verification_timeout, payment_method_not_available,
payment_method_provider_decline, payment_method_provider_timeout,
payment_method_restricted, payment_method_unactivated, payment_method_unexpected_state,
payment_method_unsupported_type, payout_reconciliation_not_ready, payouts_limit_exceeded,
payouts_not_allowed, platform_api_key_expired, platform_account_required,
postal_code_invalid, processing_error, product_inactive,
progressive_onboarding_limit_exceeded, rate_limit, refer_to_customer,
refund_disputed_payment, request_blocked, resource_already_exists, resource_missing,
return_intent_already_processed, routing_number_invalid, sepa_unsupported_account,
sku_inactive, secret_key_required,
service_period_coupon_with_metered_tiered_item_unsupported, setup_attempt_failed,
setup_intent_authentication_failure, setup_intent_invalid_parameter,
setup_intent_mandate_invalid, setup_intent_mobile_wallet_unsupported,
setup_intent_setup_attempt_expired, setup_intent_unexpected_state,
shipping_address_invalid, shipping_calculation_failed, siret_invalid, state_unsupported,
status_transition_invalid, storer_capability_missing, storer_capability_not_active,
stripe_tax_inactive, tls_version_unsupported, tax_id_invalid, tax_id_prohibited,
taxes_calculation_failed, terminal_location_country_unsupported, terminal_reader_busy,
terminal_reader_hardware_fault, terminal_reader_invalid_location_for_activation,
terminal_reader_invalid_location_for_payment, terminal_reader_offline,
terminal_reader_timeout, testmode_charges_only, token_already_used,
token_card_network_invalid, token_in_use, transfer_source_balance_parameters_mismatch,
transfers_not_allowed, url_invalid
```

(Also note `idempotency_key_in_use` and `rate_limit` appear as `code` values in this same list —
confirming the `code` field, not a dedicated `type`, is where those two conditions are actually
tagged on the wire, consistent with the 4-value `type` enum finding above.)

## Full `decline_code` enumeration (43 values, from card-issuer declines)

Sourced from the `hideokamoto/stripe-decline-codes` GitHub repository, a third-party project that
maintains a machine-readable mirror of Stripe's own `docs.stripe.com/declines/codes` table — **this
is a secondary source, not Stripe's own docs directly** (direct WebFetch to docs.stripe.com was
blocked this session; see summary.md Gaps). Corroborated for the handful of codes I cross-checked
(`generic_decline`, `do_not_honor`, `fraudulent`, `insufficient_funds`) against independent WebSearch
summaries of the same page, which agreed.

| Code | Description |
|---|---|
| `approve_with_id` | Payment cannot be authorized |
| `call_issuer` | Card declined for unknown reason |
| `card_not_supported` | Card doesn't support this purchase type |
| `card_velocity_exceeded` | Balance or credit limit exceeded |
| `currency_not_supported` | Card doesn't support the currency |
| `do_not_honor` | Card declined for unknown reason |
| `do_not_try_again` | Card declined for unknown reason |
| `duplicate_transaction` | Identical transaction submitted recently |
| `expired_card` | Card has expired |
| `fraudulent` | Payment suspected to be fraudulent |
| `generic_decline` | Card declined for unknown reason |
| `incorrect_number` | Card number is incorrect |
| `incorrect_cvc` | CVC number is incorrect |
| `incorrect_pin` | PIN is incorrect |
| `incorrect_zip` | ZIP/postal code is incorrect |
| `insufficient_funds` | Insufficient funds |
| `invalid_account` | Card or account is invalid |
| `invalid_amount` | Payment amount is invalid or too large |
| `invalid_cvc` | CVC number is incorrect |
| `invalid_expiry_year` | Expiration year is invalid |
| `invalid_number` | Card number is incorrect |
| `invalid_pin` | PIN is incorrect |
| `issuer_not_available` | Card issuer could not be reached |
| `lost_card` | Card reported lost |
| `merchant_blacklist` | Payment matches merchant blocklist |
| `new_account_information_available` | Card or account is invalid |
| `no_action_taken` | Card declined for unknown reason |
| `not_permitted` | Payment is not permitted |
| `pickup_card` | Card cannot be used for this payment |
| `pin_try_exceeded` | PIN attempts exceeded |
| `processing_error` | Error processing the card |
| `reenter_transaction` | Payment could not be processed |
| `restricted_card` | Card cannot be used for this payment |
| `revocation_of_all_authorizations` | Card declined |
| `revocation_of_authorization` | Card declined |
| `security_violation` | Card declined |
| `service_not_allowed` | Card declined |
| `stolen_card` | Card reported stolen |
| `stop_payment_order` | Card declined |
| `testmode_decline` | Stripe test card used |
| `transaction_not_allowed` | Card declined |
| `try_again_later` | Card declined, try again later |
| `withdrawal_count_limit_exceeded` | Credit limit exceeded |

That's 42 rows captured here (the source claimed 44 total with one row missing from the fetched
excerpt) — **treat this table as very likely complete but not 100%-certain-complete**, and cross-
check against docs.stripe.com/declines/codes directly before treating it as the final word (see
Gaps). Test-mode-specific decline behavior (which card numbers trigger which decline codes) is
subtopic 4's ("Test mode, clocks and prior art") territory, not duplicated here.

### Soft vs. hard declines (retriability)
Per WebSearch synthesis, common practitioner framing (not a verbatim Stripe doc table I could
confirm the existence of this session) splits declines into:
- **Soft declines** (issuer says "try later/different amount") — e.g. `insufficient_funds`,
  `generic_decline`, `try_again_later` — often recoverable by Smart Retries (subtopic 3's territory).
- **Hard declines** (permanent issue with the card/account) — e.g. `lost_card`, `stolen_card`,
  `expired_card`, `revocation_of_authorization`, `pickup_card` — should never be retried; the
  customer needs new payment details. Stripe's own guidance (reported secondhand) is to **not**
  surface `lost_card`/`stolen_card` specifically to the end customer and instead present them as a
  generic decline, for fraud-disclosure reasons.

I did not find a definitive, official per-code retriable/non-retriable boolean table from Stripe
itself this session — flagged as a gap; a Seahaven mock wanting to simulate Smart Retries faithfully
(subtopic 3) should treat the soft/hard split above as a reasonable starting taxonomy pending direct
confirmation.

## What `stripe-mock` actually does with all of this (brief note — full stripe-mock review is
subtopic 4's job; this is only the error/version-handling slice relevant to cross-cutting semantics)

From `research/repos/stripe-mock/server/server.go`, directly read:
- **Every synthetic error stripe-mock generates uses `type: "invalid_request_error"`** — there is
  exactly one error-type constant used across the file (`typeInvalidRequestError =
  "invalid_request_error"`, line 597; every `createStripeError(...)` call in the file passes this
  same constant). **stripe-mock does not simulate `card_error`, `decline_code`, or realistic
  card-decline behavior at all** through this generic path — it's a schema-shape server, not a
  payment-state-machine simulator (consistent with the research plan's expectation that stripe-mock
  "does not model subscription lifecycles or money moving").
- **Idempotency**: stripe-mock does not implement any idempotency semantics. It just reflects the
  `Idempotency-Key` request header back as a response header, verbatim (lines 263-268):
  ```go
  // We don't do anything with the idempotency key for now, but reflect it
  // back into response headers like the Stripe API does.
  idempotencyKey := r.Header.Get("Idempotency-Key")
  if idempotencyKey != "" {
      w.Header().Set("Idempotency-Key", idempotencyKey)
  }
  ```
  No replay-detection, no 409-on-conflicting-params, no caching.
- **Versioning**: by default stripe-mock ignores `Stripe-Version` entirely and always serves the
  single OpenAPI-spec-pinned version's shapes. It has an **opt-in `-strict-version-check` flag**
  that, when set, hard-rejects (400 `invalid_request_error`) any request whose `Stripe-Version`
  header doesn't exactly match the embedded spec's version:
  ```go
  if s.strictVersionCheck {
      stripeVersion := r.Header.Get("Stripe-Version")
      if stripeVersion != "" && stripeVersion != s.spec.Info.Version {
          message := fmt.Sprintf(invalidStripeVersion, stripeVersion, s.spec.Info.Version)
          stripeError := createStripeError(typeInvalidRequestError, message)
          writeResponse(w, r, start, http.StatusBadRequest, stripeError)
          return
      }
  }
  ```
  There is no version-*transformation* logic at all (unlike the real API's documented internal
  "always compute at current version, then transform down to the requested version" model — see
  `versioning.md`) — stripe-mock has only one shape, period.
- **Pagination**: `generateListResource`/`generateSearchResultResource` in `server/generator.go`
  **hard-code `has_more: false` and `total_count: 1`, and always synthesize exactly one item** in
  `data`, regardless of `limit`/`starting_after`/`ending_before`/`page`. There is no real filtering,
  no real cursor tracking, and no multi-page behavior — confirmed directly reading lines 490-565 of
  `generator.go`. This is a concrete, precise answer to "how does prior art handle pagination": it
  doesn't, beyond shape-conformance.

## Sources
- `research/stripe-openapi/spec3.json` — authoritative for the full `api_errors`/`error` schema, the
  4-value `type` enum, and expandable-field annotations (queried directly).
- `research/repos/stripe-python/stripe/_error.py`, `_error_object.py`, `_api_requestor.py` — authoritative for
  the client-observed HTTP-status → exception mapping and the v1/v2 `type`-switch asymmetry (read
  directly).
- `research/repos/stripe-mock/server/server.go`, `server/generator.go` — authoritative for exactly how
  the reference mock handles errors/idempotency/versioning/pagination (read directly).
- `raw.githubusercontent.com/stripe/stripe-go/master/error.go` (WebFetch) — source of the ~215-entry
  `code` enumeration.
- `hideokamoto/stripe-decline-codes` GitHub repo (WebFetch) — source of the decline-code table
  (secondary source, mirrors docs.stripe.com/declines/codes).
- WebSearch synthesis of docs.stripe.com/api/errors, docs.stripe.com/error-codes,
  docs.stripe.com/declines/codes, docs.stripe.com/rate-limits — direct WebFetch to all docs.stripe.com
  / stripe.com URLs was blocked by this session's egress policy (`EGRESS_BLOCKED`); see summary.md
  Gaps for what that means for confidence levels above.
- A real observed 429 response body quoted in `franckverrot/terraform-provider-stripe` GitHub issue
  — corroborates the 4-value `type` enum finding.
