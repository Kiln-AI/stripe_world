# Account and Statistical Tells

## Bottom Line

The two largest tells in this lane are both **blatant** and both in `_ids.py`. First, real
Stripe uses two ID suffix formats — a 14-char random suffix for `cus_`, `prod_`, and `si_`
prefixes, and a 24-char *structured* suffix (version digit + base62 timestamp + 10-char
account fragment + 8-char random) for everything else (`price_`, `pi_`, `ch_`, `sub_`,
`in_`, `txn_`, etc.). Our world as written today uses a uniform 24-char random suffix for
all prefixes; an agent can detect the difference by creating one customer and checking the
suffix length, or by comparing two structured IDs for the embedded account fragment they
would share on real Stripe. Second, the static account object in `tools/account.py` is
missing at least six top-level keys that real Stripe returns (`controller`,
`external_accounts`, `requirements`, `future_requirements`, `tos_acceptance`, and
`metadata` is present in ours but absent in real), and several field values
(`capabilities`, `charges_enabled`, `payouts_enabled`, `details_submitted`,
`business_profile.name`) are set to an internally inconsistent "completed onboarding" state
that no real sandbox matches. Beyond these, the frozen-clock model means successive creates
share timestamps, and a dozen resource-level fields present on real responses (`customer_account`,
`test_clock`, `billing_mode`, `trial_settings`, `cancellation_details`, `managed_payments`, etc.)
are absent from our serialization.

## Key Findings

- **ID suffix length varies by prefix on real Stripe** — Format A (`cus_`, `prod_`, `si_`)
  uses 14-char suffixes; Format B (all other in-scope prefixes) uses 24-char structured
  suffixes. Our world uses 24 chars for everything. An agent creating a single customer
  immediately sees the wrong length. See [id-shapes.md](./id-shapes.md) for the full
  measured table. Source: live MCP sandbox, 30+ IDs across 8 resource types.

- **Structured IDs encode the account** — every Format B ID contains the same 10-char
  fragment from the account ID at a fixed position (chars 7-16 of the suffix). Our world's
  IDs are fully random, so no shared substring exists. An agent comparing any two
  structured IDs instantly detects this. Source: live probing, confirmed across `price_`,
  `pi_`, `ch_`, `re_`, `pm_`, `sub_`, `seti_`, `txn_`, `in_`, `il_`, `setatt_` prefixes.

- **The account object is incomplete** — the real `GET /v1/accounts/{id}` response has
  20+ top-level keys; our static `_account_object()` in `tools/account.py` has ~15. Six
  keys are completely absent (`controller`, `external_accounts`, `requirements`,
  `future_requirements`, `tos_acceptance`; `metadata` is spuriously present). Several
  values are wrong for any real sandbox state: `capabilities` should be `{}` (not
  `{"card_payments": "active", "transfers": "active"}`); `charges_enabled` and
  `payouts_enabled` should be `false`; `details_submitted` should be `false`;
  `business_profile.name` should be `null`; and 5 `business_profile` sub-fields are
  missing. Source: `GetAccountsAccount` on the live sandbox.

- **The account ID is a human-readable constant** — `acct_1SWTestAccount00` contains
  `TestAccount`, which no real Stripe ID contains. Real account IDs follow the same
  structured format (version + time + identity) as Format B IDs. Source: live sandbox
  account ID shape (recorded as `acct_` + 16 alphanumeric chars).

- **Frozen clock is detectable** — three customers created in rapid succession on real
  Stripe got `created` values of 1790104589, 1790104589, 1790104590 (two shared a second,
  the third was one second later). In our frozen-clock world, all three would share the
  identical value. Creating 5-10 objects and seeing zero variance in `created` is a
  probable tell. Source: three `PostCustomers` calls made back-to-back.

- **Multiple resource fields absent from our serialization** — real responses include
  `customer_account` (null), `test_clock` (null), `billing_mode` (object),
  `trial_settings` (object), `cancellation_details` (object), `managed_payments`
  (object), `payment_details` (on PI), `destination_details` (on refund),
  `radar_options` (on charge), and others. Each is a probable or subtle tell individually;
  collectively they form a fingerprint. Source: live list/retrieve calls across all
  in-scope resource types.

- **Coupon IDs match** — real coupons use 8-char mixed-case alphanumeric IDs (`sjNdOctl`,
  `953yHBQA`, `xtdAMxIi`), exactly what `coupon_id` in `_ids.py` produces today. No tell
  here. Source: `GetCoupons` on live sandbox.

## Details

- [tells.md](./tells.md) — numbered table of all 31 tells with exact agent test, real vs.
  our world, severity, and how to close. Read this for the actionable list.
- [id-shapes.md](./id-shapes.md) — the measured ID alphabet, length, and internal structure
  per resource prefix, with comparison to `_ids.py` as written today. Read this for the
  full ID format specification that `_ids.py` needs to match.

## Open Questions / Gaps

- **Event IDs not measured.** The `GetEvents` operation ID was unavailable via the MCP tool,
  and the event-list search did not surface a v1 events endpoint. Event IDs (`evt_`) were
  not sampled. They likely follow Format B (structured 24-char), but this is inference, not
  measurement.

- **Dispute and payout IDs not measured.** No disputes or payouts existed on the sandbox
  during probing. The prefixes `du_` and `po_` are in `_ids.py` but their suffix format
  (14-char vs. 24-char structured) is not confirmed by direct observation.

- **Subscription schedule IDs not measured.** No `sub_sched_` IDs were observed.

- **`invoice_prefix` generation code not located.** The tells note (AS-11) records the real
  format (8 chars, `[A-Z0-9]`) but the generation code in our world was not found during
  this probe.

- **Data-realism tells (fixture correlations) not deeply tested.** Email domains, name
  distributions, amounts, currency mix, address realism, and sequence gaps apply to
  fixtures rather than live API behavior. The shared-account constraint makes global
  distribution measurement unreliable. Better assessed by inspecting `generate.py` once it
  exists.

- **Response latency not measured.** The MCP tool interface does not expose timing info.

## Sources

- **Live Stripe MCP sandbox** — all findings except code comparisons. Probed 2026-09-22 via
  `mcp__stripe__stripe_api_read` and `mcp__stripe__stripe_api_write`. Account shape:
  `acct_` + 16 alphanumeric chars, sandbox mode, country CA, default currency CAD.
- **`src/seahaven_stripe_world/_ids.py`** — our world's ID minting (suffix length 24, uniform
  random, per-prefix table).
- **`src/seahaven_stripe_world/_time.py`** — our world's timestamp conversion (frozen clock,
  Unix-second granularity).
- **`src/seahaven_stripe_world/tools/account.py`** — our world's static account object.
- **`specs/projects/stripe_world/components/data_model.md`** — the specified schema, field
  conventions, and ID prefix table.
- **`specs/projects/stripe_world/components/fixtures.md`** — the fixture generation design
  (frozen clock model, population knobs, timeline model).
