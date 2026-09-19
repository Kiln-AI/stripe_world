# Test mode, clocks and prior art

## Bottom Line

Test mode's magic-value surface (decline cards, `pm_card_*`/`tok_*` tokens, 3DS/dispute cards,
Sandboxes) is now **directly sourced from `docs.stripe.com`** (a 2026-09-18 gap-closure pass had
working access via `mcp__Tavily__tavily_extract`/`tavily_search`, which reach `docs.stripe.com` and
`stripe.com` even though this session's `WebFetch` still cannot). The core "declined payment" table
(9 rows: generic/insufficient-funds/lost/stolen/expired/CVC/processing/incorrect-number/velocity)
is confirmed structure-and-code-verbatim from the official page, with card-number digits
corroborated across 5 independent, mutually-consistent 2026-dated mirrors; refund-failure,
Connect-payout-failure, IBAN, and dispute-evidence magic values are now fully resolved with direct
quotes; and the one genuine conflict from the earlier pass (`4000 0000 0000 0069`: stolen vs.
expired) is settled — **it's `expired_card`, not `stolen_card`** (`stolen_card` is a different
number, `4000 0000 0000 9979`). See [magic-card-table.md](./magic-card-table.md) for the full
fixture-ready table and [gap-closure-2026-09-18.md](./gap-closure-2026-09-18.md) for the itemized
confirm/correct/refine record. Test clocks remain fully documented from primary-source `spec3.json`
as before, now supplemented with two more direct-from-docs facts: **test clocks auto-delete 30 days
after creation** (`deletes_after`'s concrete value), and `internal_failure` is effectively terminal —
Stripe's own API reference says future advance requests are expected to fail once a clock enters
that state. Most importantly for §12.3: **`stripe-mock` is confirmed, from its own README and from
reading `server.go`/`generator.go`/the fixtures file line-by-line, to be completely stateless and
behavior-free for every resource, not just billing** — advancing a test clock against stripe-mock
has zero effect on any other object, because nothing in it is wired to anything. Every other serious
open-source Stripe emulator found (`localstripe`, `stripe-stateful-mock`, `mock-stripe`) chose
statefulness as its differentiator, and **none of them implement test-clock semantics at all** —
deterministic-time subscription testing against a mock (rather than real Stripe test mode) appears
to be unclaimed territory.

## Key Findings

- **The core magic-card decline table is now primary-sourced end to end.** Row order and every
  `code`/`decline_code` value are verbatim from a direct `docs.stripe.com/testing` fetch; card-number
  digits are corroborated across 5 independent 2026 mirrors that all agree exactly, plus one number
  (`4242424242424241`, the `incorrect_number` example) confirmed directly in Stripe's own body text.
  See [magic-card-table.md §1](./magic-card-table.md#1-declined-payments-the-core-failure-injection-table).
- **CORRECTED: `4000 0000 0000 0069` is `expired_card`, not `stolen_card`.** The earlier pass
  flagged this as an unresolved conflict between two `WebSearch` syntheses. It's now settled from
  Stripe's own page structure plus 5 independently-agreeing mirrors: `stolen_card` is a different
  number, `4000000000009979`. Any fixture built on the earlier "stolen" attribution needs correcting.
  See [gap-closure-2026-09-18.md §2](./gap-closure-2026-09-18.md#2-resolve-the-4000-0000-0000-0069-conflict).
- **Refund-failure, Connect-payout-failure, IBAN/SEPA, and dispute-evidence magic values are all
  now resolved**, each with a direct quote from `docs.stripe.com`. Dispute evidence in particular
  turned out simpler than guessed: it's just 3 literal control strings (`winning_evidence`,
  `losing_evidence`, `escalate_inquiry_evidence`) passed as `uncategorized_text`, not a large table
  of canned text. See [magic-card-table.md §§4–8](./magic-card-table.md).
- **Test clock `deletes_after` = 30 days, confirmed directly**: "Simulations are automatically
  deleted 30 days after you create them" (`docs.stripe.com/billing/testing/test-clocks/api-advanced-usage`).
  Also newly confirmed: `internal_failure` is effectively terminal ("Future requests to advance time
  will fail" — Stripe's own API reference), and a previously-unknown **per-simulation cap** exists:
  up to 3 customers, 3 subscriptions per customer, and 10 unattached quotes per test clock (distinct
  from — and not a substitute for — the still-unresolved question of a cap on clocks per account).
- **stripe-mock's own README confirms the project's working assumption outright** — "stripe-mock
  does not attempt to reproduce the *behavior* of the real Stripe API at all," it is "stateless,"
  and the maintainers are "currently not planning to add statefulness." This isn't an inference
  from reading code; it's the tool's stated design boundary. See
  [prior-art.md §3.1](./prior-art.md#31-the-projects-own-stated-scope-verbatim-from-its-readme).
- **Direct proof the test-clock fixture is inert**: `fixtures3.json`'s
  `test_helpers.test_clock` entry has `status: "ready"` and identical placeholder timestamps
  hardcoded; because stripe-mock's `POST` handler only reflects request fields that structurally
  match the response schema (no cross-object side effects exist anywhere in the codebase), calling
  `advance` never changes any subscription/invoice fixture. See
  [prior-art.md §3.3](./prior-art.md#33-the-fixtures-file).
- **The test-clock API is small and its limits are explicit in `spec3.json`'s own text**: forward-only
  advancement; ≤ 2 subscription-intervals per `/advance` call when subscriptions exist, else ≤ 2
  years; every clock carries a `deletes_after` auto-expiry (now known: 30 days); a customer/quote,
  once attached, can never be detached. See [test-clocks.md §2.6](./test-clocks.md#26-documented-limits-from-the-spec-text-directly).
- **Only `customer` and `quote` creation accept a `test_clock` parameter** — subscriptions,
  invoices, invoiceitems, subscription schedules, and billing credit grants/transactions all carry
  a `test_clock` field but no create-time param of their own; they inherit it from their customer.
  See [test-clocks.md §2.3](./test-clocks.md#23-what-can-be-attached-to-a-clock-and-when).
- **Every other maintained Stripe-emulator alternative chose statefulness over stripe-mock's
  approach**, and every one of them ships an explicit "don't trust this fully" disclaimer
  (`stripe-stateful-mock` literally calls itself "half-baked"). None reproduce test clocks. See
  [prior-art.md §3.5](./prior-art.md#35-other-serious-stripe-emulator-prior-art).
- **Sandboxes are a superset of test mode, not a replacement** — up to 5 isolated
  environments per account, each with its own key pair and fully isolated settings, vs. one shared
  test-mode environment; stripe-mock's own auth check only validates a key's *shape*
  (`sk_test_*`/`rk_test_*`), it has no concept of accounts or sandboxes at all. See
  [test-mode.md §1.6](./test-mode.md#16-sandboxes-vs-a-plain-test-mode-key).
- **Test-mode vs. live-mode differences now have a direct, itemized (if not exhaustive) source**:
  Identity performs no real verification in test mode; Connect Account objects withhold sensitive
  fields in test/sandbox mode; disputes and some payment methods have simpler flows in test mode
  than live; settlement is instant in test mode vs. up to multiple days in live mode. Quoted
  directly from `docs.stripe.com/keys` and `docs.stripe.com/testing`. See
  [gap-closure-2026-09-18.md §5](./gap-closure-2026-09-18.md#5-test-mode-vs-live-mode-object-shapebehavior-differences).

## Details

- [magic-card-table.md](./magic-card-table.md) — **the fixture-ready deliverable**: every magic
  card/bank-account/IBAN number found, organized by category, with source confidence noted per row
  and explicit "GAP — not resolved" call-outs for what's still missing. Start here for anything
  going into a fixture generator.
- [gap-closure-2026-09-18.md](./gap-closure-2026-09-18.md) — itemized record of this pass: each
  original gap, its new answer, a verbatim quote + URL, and whether it confirms/corrects/refines the
  prior record. Read this for the "what changed and why" narrative, especially the `4000...0069`
  correction.
- [test-mode.md](./test-mode.md) — Part 1 (original pass): magic card table, `pm_card_*`/`tok_*`
  tokens, 3DS/dispute/subscription-failure cards, ACH test bank accounts, testmode-only vs.
  livemode, Sandboxes vs. test-mode keys. Its sourcing-caveat header is now superseded by the
  2026-09-18 gap-closure pass for the specific items listed above — cross-reference
  `gap-closure-2026-09-18.md` before treating any number here as final.
- [test-clocks.md](./test-clocks.md) — Part 2: the complete `test_helpers/test_clocks` API surface
  and `test_helpers.test_clock` schema pulled directly from `spec3.json` (verbatim field/param
  quotes), what objects can attach and how, the async `advancing`/`ready`/`internal_failure` status
  model, frozen-time semantics, and every numeric/structural limit found. This is the primary
  evidence file for the §12.3 time decision; supplement with `gap-closure-2026-09-18.md §4` for the
  TTL and `internal_failure` resolutions.
- [prior-art.md](./prior-art.md) — Part 3: a line-by-line read of `stripe-mock`'s routing, response-
  generation, and fixture code, with verbatim README quotes and direct proof (from the fixtures
  file) that test-clock advancement is a no-op in the mock. Also covers `localstripe`,
  `stripe-stateful-mock`, `mock-stripe`, and `stripe-ruby-mock` as alternative prior art, with a
  synthesized "what this teaches" section aimed at how a Seahaven Stripe world should scope its own
  honesty-about-limits.

## Open Questions / Gaps

- **A documented cap on test clocks per account/sandbox** — still not found. The 2026-09-18 pass did
  find (and add to Key Findings) a *per-clock* cap (3 customers, 3 subscriptions/customer, 10
  unattached quotes) directly from `docs.stripe.com`, but that's a different axis from "how many
  clocks/simulations can one account create," which remains genuinely undocumented anywhere found.
- Several niche magic-card rows remain unresolved after the 2026-09-18 pass (correctly deprioritized
  as non-load-bearing): 3DS mobile challenge-flow numbers, captcha cards, PIN cards, most Radar
  sub-variant cards beyond "always blocked"/"elevated risk," Discover-fraudulent and the
  Visa/Mastercard-compliance dispute cards, BCcard/DinaCard and co-branded CB/eftpos success cards,
  and the full by-country success-card list beyond the 9 samples found. See `magic-card-table.md`'s
  per-section "GAP — not resolved" notes.
- Whether `frozen_time` updates incrementally or only atomically during the `advancing` status is
  still not confirmed from any source (schema is silent; docs prose describes webhooks firing "as
  the clock advances through intervening events" but doesn't specify the object's own `frozen_time`
  field behavior mid-advance).
- **`mock-stripe` (prasanthkv) and `stripe-ruby-mock`** were surfaced but not read in depth (a raw
  README fetch 404'd for the former; the latter is architecturally out of scope as an SDK-level
  interceptor, not a server) — low-priority follow-up if more prior art is wanted.

## Sources

See the "Sources" section at the bottom of each detail file for full citations. Highest-confidence
sources used across this subtopic:

- [Test card numbers | Stripe Documentation](https://docs.stripe.com/testing) — fetched directly
  2026-09-18 via `mcp__Tavily__tavily_extract`; authoritative for the magic-card table's structure,
  codes, and body-text-embedded numbers (see `magic-card-table.md` for what did/didn't render).
- [Testing Stripe Connect | Stripe Documentation](https://docs.stripe.com/connect/testing) — fetched
  directly 2026-09-18; authoritative, full-confidence source for Connect payout-failure values.
- [API and advanced usage | Stripe Documentation](https://docs.stripe.com/billing/testing/test-clocks/api-advanced-usage) —
  fetched directly 2026-09-18; authoritative for the `deletes_after` TTL (30 days) and the
  per-simulation limits.
- [The Test Clock object | Stripe API Reference](https://docs.stripe.com/api/test_clocks/object) —
  fetched directly 2026-09-18; authoritative for `internal_failure`'s documented semantics.
- [API keys | Stripe Documentation](https://docs.stripe.com/keys) — fetched directly 2026-09-18;
  source for the test-vs-live itemized differences list.
- `/home/user/stripe_world/research/stripe-openapi/spec3.json` (`Stripe-Version: 2026-08-26.dahlia`) — primary source, read directly via Python queries; authoritative for the entire `test_helpers/test_clocks` API shape (Part 2)
- `/home/user/stripe_world/research/repos/stripe-mock` (full source tree) — primary source, read directly; authoritative for all of Part 3
- Five independently-agreeing 2026-dated mirrors used to corroborate card-number digits (full list
  in `magic-card-table.md`'s Sources section): bug0.com, growthy.com, shopmonitor.io,
  getautonoma.com, rapidevelopers.com.
- [adrienverge/localstripe](https://github.com/adrienverge/localstripe), [Giftbit/stripe-stateful-mock](https://github.com/Giftbit/stripe-stateful-mock) — READMEs read directly via `raw.githubusercontent.com`; primary source for the "other prior art" survey
