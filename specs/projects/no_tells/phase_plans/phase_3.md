---
status: complete
---

# Phase 3: Ids

## Overview

Replace the uniform 24-char random suffix with the two measured formats from real Stripe
(id-shapes.md). Format A (14 random chars) applies to `cus_`, `prod_`, `si_`; Format B
(V + T(5) + A(10) + R(8) = 24 structured chars) applies to everything else. The account
fragment in Format B comes from `ctx.state["account"]`, and the timestamp group comes from
the caller's stamped time rather than "now".

## Steps

1. Add a `FORMAT_A_PREFIXES` frozenset in `_ids.py` listing `{"cus_", "prod_", "si_"}`.
2. Add a `_account_fragment(ctx)` helper that reads `ctx.state["account"]["id"]`, extracts
   the last 10 chars of the suffix (chars after `acct_`), and raises `WorldBug` if startup
   has not run.
3. Add a `_base62_timestamp(iso_timestamp: str) -> str` helper that encodes a Unix timestamp
   into 5 base62 characters using the `ID_ALPHABET` (digits + uppercase + lowercase, i.e.,
   `0-9A-Za-z` in the standard base62 ordering).
4. Rewrite `stripe_id` to accept optional `timestamp: str | None` (ISO) and
   `version_digit: str` (default `"1"`). For Format A prefixes: mint 14 random chars.
   For Format B prefixes: mint `version_digit + base62_timestamp + account_fragment + 8 random`.
5. Update every caller of `stripe_id` across the source tree to pass `timestamp=` where the
   creation timestamp is available (typically `ctx.clock.iso()` or the local `now`/`created`
   variable). Callers that create side-effect objects (charges from PI confirmation,
   balance_transactions, refunds) pass `version_digit="3"`.
6. Update `test_stub_and_request_prefixes_mint_too` — stub prefixes (`ba_`, `card_`,
   `mandate_`, `setatt_`) and `req_` use Format B and now mint 24-char structured suffixes.
7. Replace the blanket "prefix + 24 alphanumerics" assertion with per-format tests.
8. Update the register dispositions for AS-01, AS-02, AS-27.

## Tests

- `test_format_a_customer_id_is_14_chars`: create a `cus_` id, assert suffix length is 14
- `test_format_a_product_id_is_14_chars`: `prod_` suffix is 14
- `test_format_a_subscription_item_id_is_14_chars`: `si_` suffix is 14
- `test_format_b_price_id_is_24_structured`: `price_` suffix is 24, has version digit +
  timestamp group + account fragment + random
- `test_format_b_account_fragment_matches`: account fragment in Format B ids matches last
  10 chars of account id suffix
- `test_format_b_account_fragment_consistent_across_prefixes`: two different Format B ids
  share the same account fragment
- `test_format_b_timestamp_group_from_stamped_time`: ids created at the same timestamp share
  the same 5-char timestamp group
- `test_format_b_version_digit_default_is_1`: default version digit is "1"
- `test_format_b_version_digit_3_for_side_effects`: passing version_digit="3" places "3" at
  position 0 of the suffix
- `test_as_01_format_a_suffix_length`: register AS-01
- `test_as_02_format_b_account_encoding`: register AS-02
- `test_as_27_format_b_time_encoding`: register AS-27
- `test_no_uuid_calls_in_source`: existing guard stays
- `test_determinism_same_seed`: existing determinism property preserved
