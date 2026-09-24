# ID Shapes — Measured from Real Stripe Sandbox (2026-09-22)

## Methodology

IDs sampled from the live Stripe MCP sandbox via `stripe_api_read` across 8+ resource types.
At least 30 IDs sampled in total. Account ID shape: `acct_` + 16 chars (recorded by shape,
not verbatim, per secrets discipline).

## Two Distinct ID Formats

Real Stripe uses **two formats**, not one. Our world (`_ids.py`) uses a single format
(prefix + 24-char uniform random from `[A-Za-z0-9]`) for every resource. That is the
single biggest statistical tell in this lane.

### Format A — Short random suffix

Used by: `cus_`, `prod_`, `si_` (subscription items).

| Prefix | Suffix length | Alphabet | Sample |
|--------|--------------|----------|--------|
| `cus_` | 14 | `[A-Za-z0-9]` | `cus_` + `VIi7Y80YXBLCzc` |
| `cus_` | 14 | `[A-Za-z0-9]` | `cus_` + `VJBYPSg1NGnQZO` |
| `cus_` | 14 | `[A-Za-z0-9]` | `cus_` + `VIi6BSrOAWmrPY` |
| `cus_` | 14 | `[A-Za-z0-9]` | `cus_` + `VIi6dNvpyA9Kkl` |
| `cus_` | 14 | `[A-Za-z0-9]` | `cus_` + `VIb0eQUxPuaBcB` |
| `cus_` | 14 | `[A-Za-z0-9]` | `cus_` + `VIZmOPu1gRy3Io` |
| `prod_` | 14 | `[A-Za-z0-9]` | `prod_` + `VIpADUByAMqWcU` |
| `prod_` | 14 | `[A-Za-z0-9]` | `prod_` + `VIp8zhzA95iDv6` |
| `prod_` | 14 | `[A-Za-z0-9]` | `prod_` + `VJBYdseyRnwG4i` |
| `prod_` | 14 | `[A-Za-z0-9]` | `prod_` + `VJBYKwS32vDjkC` |
| `si_` | 14 | `[A-Za-z0-9]` | `si_` + `VIV5acMz9nGsJc` |
| `si_` | 14 | `[A-Za-z0-9]` | `si_` + `VIa6Bd4ZqqgHhk` |
| `si_` | 14 | `[A-Za-z0-9]` | `si_` + `VIV4mGprmzxh0n` |

No observed variation in length within a prefix. Alphabet is full alphanumeric (62 chars).

### Format B — Structured suffix (account-encoding)

Used by: `price_`, `pi_`, `ch_`, `re_`, `pm_`, `sub_`, `seti_`, `txn_`, `in_`, `il_`, `setatt_`.

Structure after prefix: `V` + `TTTTT` + `AAAAAAAAAA` + `RRRRRRRR` = 24 chars total, where:

- `V` (1 char): a version/type digit, observed values `0`, `1`, or `3`
- `TTTTT` (5 chars): appears to be a base62-encoded timestamp component; objects created at
  the same second share the same 5-char group
- `AAAAAAAAAA` (10 chars): **the account's identity** — identical across every structured
  ID on the same account. Matches the last 10 characters of the account ID's own suffix.
- `RRRRRRRR` (8 chars): random, from `[A-Za-z0-9]`

| Prefix | Suffix len | V | T (5) | A (10) | R (8) | Full sample |
|--------|-----------|---|-------|--------|-------|-------------|
| `price_` | 24 | `1` | time-based | acct-frag | random | `price_1UIDVk<ACCT>a2ryArhb` |
| `pi_` | 24 | `3` | time-based | acct-frag | random | `pi_3UILv5<ACCT>1PnCufZ5` |
| `ch_` | 24 | `3` | time-based | acct-frag | random | `ch_3UILv5<ACCT>1kMdffoJ` |
| `re_` | 24 | `3` | time-based | acct-frag | random | `re_3UHt6f<ACCT>0WuBUQzo` |
| `pm_` | 24 | `1` | time-based | acct-frag | random | `pm_1UHu4g<ACCT>TNpu5jAd` |
| `sub_` | 24 | `1` | time-based | acct-frag | random | `sub_1UHywL<ACCT>eTL06X7f` |
| `seti_` | 24 | `1` | time-based | acct-frag | random | `seti_1UID3D<ACCT>3zrOOgSR` |
| `txn_` | 24 | `3` | time-based | acct-frag | random | `txn_3UILv5<ACCT>12wL0ybE` |
| `in_` | 24 | `1` | time-based | acct-frag | random | `in_1UIKyh<ACCT>JA3wGQaz` |
| `il_` | 24 | `1` | time-based | acct-frag | random | `il_1UIKya<ACCT>OfxDIe74` |
| `setatt_` | 24 | `1` | time-based | acct-frag | random | `setatt_1UID3D<ACCT>mR1bXvaZ` |

`<ACCT>` = the 10-char account fragment, scrubbed per secrets discipline.

The version digit shows a pattern:
- `1` — objects created by a direct POST call (prices, payment methods, subscriptions, invoices, etc.)
- `3` — objects created as a side-effect of another operation (charges from payment intent
  confirmation, balance transactions from charges, refunds)
- `0` — observed on some charges/PIs (e.g., `ch_3UIDW1<ACCT>0ERO2awE` has `0` in the random
  tail, but the version digit itself is `3`)

### Format C — Unprefixed (coupons only)

| Object | Length | Alphabet | Samples |
|--------|--------|----------|---------|
| coupon | 8 | `[A-Za-z0-9]` | `sjNdOctl`, `953yHBQA`, `xtdAMxIi` |

This matches our world's `coupon_id`: 8 chars, mixed case alphanumeric.

### Account ID shape

`acct_` + 16 chars. The 16-char suffix follows the same V+T+A structure of Format B:
`1` + 5-char time + 10-char identity. The 10-char identity portion is the fragment that
appears embedded in every Format B id on that account.

## Comparison with Our World (`_ids.py` as of today)

| Aspect | Real Stripe | Our World | Match? |
|--------|------------|-----------|--------|
| Format A suffix length | 14 chars | 24 chars | NO |
| Format B suffix length | 24 chars (structured) | 24 chars (random) | Length yes, structure NO |
| Format B account encoding | 10-char account fragment embedded | not encoded | NO |
| Format B version digit | `1` or `3` | absent | NO |
| Format B time component | 5-char base62 timestamp | absent | NO |
| Coupon length | 8 chars | 8 chars | YES |
| Coupon alphabet | `[A-Za-z0-9]` | `[A-Za-z0-9]` | YES |
| Uniform alphabet `[A-Za-z0-9]` | Yes for all | Yes for all | YES |
| Per-prefix length variation | Two lengths (14 vs 24) | One length (24) | NO |

## How an Agent Tests This

1. **Length check**: create a customer, read its `id`. If suffix after `cus_` is 24 chars, it is
   not real Stripe (real is 14).
2. **Account encoding check**: create a price and a charge. Extract chars 7–16 of the suffix
   (after the prefix). If they are not identical to each other and to the last 10 chars of the
   account ID suffix, it is not real Stripe.
3. **Version digit check**: compare first char of suffix on a price (should be `1`) vs a charge
   (should be `3`). If both are random, it is not real Stripe.
