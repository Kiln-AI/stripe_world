# Test mode, clocks and prior art

## Bottom Line

Test mode's magic-value surface (decline cards, `pm_card_*`/`tok_*` tokens, 3DS/dispute cards,
Sandboxes) is well-documented but this session could **only reach it through `WebSearch`
synthesis** — every direct fetch of a Stripe-owned domain (`docs.stripe.com`, `stripe.com`,
`stripe.dev`) was blocked by the network egress proxy, so treat Part 1 as high-confidence secondary
sourcing, not verified primary quotes, and re-derive load-bearing numbers before hard-coding them.
Test clocks, by contrast, are fully documented here from **primary-source `spec3.json`**: a
5-endpoint API (create/list/retrieve/delete/advance), a 3-value `status` enum
(`advancing`/`ready`/`internal_failure`) with a genuinely async advance model, permanent one-way
attachment via `customer`/`quote` creation params only (everything else — subscriptions, invoices,
invoiceitems, schedules, credit grants — inherits the clock transitively and can never be
detached), and two hard numeric limits baked into the API text itself (advance ≤ 2× the shortest
attached subscription's interval, or ≤ 2 years with no subscriptions attached). Most importantly
for §12.3: **`stripe-mock` is confirmed, from its own README and from reading `server.go` /
`generator.go` / the fixtures file line-by-line, to be completely stateless and behavior-free for
every resource, not just billing** — advancing a test clock against stripe-mock has zero effect on
any other object, because nothing in it is wired to anything. Every other serious open-source
Stripe emulator found (`localstripe`, `stripe-stateful-mock`, `mock-stripe`) chose statefulness as
its differentiator, and **none of them implement test-clock semantics at all** — deterministic-time
subscription testing against a mock (rather than real Stripe test mode) appears to be unclaimed
territory.

## Key Findings

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
  years; every clock carries a `deletes_after` auto-expiry; a customer/quote, once attached, can
  never be detached. See [test-clocks.md §2.6](./test-clocks.md#26-documented-limits-from-the-spec-text-directly).
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
- **The magic-card table is not fully resolved** — core decline cards (generic, insufficient
  funds, lost, stolen, processing error, incorrect CVC, fraudulent/Radar-blocked), 3DS cards, and
  the two dispute cards are corroborated across multiple independent searches, but the exact
  refund-failure and Connect-payout-failure magic values, the full `decline_code` enum's dedicated
  card numbers, and IBAN test values were not resolved. One number (`4000 0000 0000 0069`) had a
  genuine conflict between sources (stolen vs. expired) that needs a direct-fetch resolution. See
  [test-mode.md §1.1–§1.4](./test-mode.md#11-the-magic-test-card-table).

## Details

- [test-mode.md](./test-mode.md) — Part 1: magic card table, `pm_card_*`/`tok_*` tokens, 3DS/dispute/subscription-failure cards, ACH test bank accounts, testmode-only vs. livemode, Sandboxes vs. test-mode keys, and what's known/unknown about test-vs-live object shape differences. Read this for anything fixture-table-shaped; also read its sourcing-caveat header first.
- [test-clocks.md](./test-clocks.md) — Part 2: the complete `test_helpers/test_clocks` API surface and `test_helpers.test_clock` schema pulled directly from `spec3.json` (verbatim field/param quotes), what objects can attach and how, the async `advancing`/`ready`/`internal_failure` status model, frozen-time semantics, and every numeric/structural limit found. This is the primary evidence file for the §12.3 time decision.
- [prior-art.md](./prior-art.md) — Part 3: a line-by-line read of `stripe-mock`'s routing, response-generation, and fixture code, with verbatim README quotes and direct proof (from the fixtures file) that test-clock advancement is a no-op in the mock. Also covers `localstripe`, `stripe-stateful-mock`, `mock-stripe`, and `stripe-ruby-mock` as alternative prior art, with a synthesized "what this teaches" section aimed at how a Seahaven Stripe world should scope its own honesty-about-limits.

## Open Questions / Gaps

- **WebFetch was blocked for every Stripe-owned domain this session** (`docs.stripe.com`,
  `stripe.com`, `stripe.dev`, `edge-docs.stripe.com` all returned `EGRESS_BLOCKED`; a control fetch
  of `en.wikipedia.org` also failed, while GitHub's raw-content and API domains worked fine) — this
  contradicts the dispatch prompt's premise that WebFetch was "confirmed working." All of Part 1
  and the behavioral half of Part 2 (§2.4) rest on `WebSearch`'s synthesized answers instead of a
  direct page read. Recommend a follow-up pass with working `WebFetch` against
  `docs.stripe.com/testing`, `/billing/testing/test-clocks`, `/sandboxes`, and `/connect/testing`
  before any of these specific numbers are frozen into a fixtures file.
- **Refund-failure and payout-failure magic values** (cards/bank accounts) were not resolved beyond
  confirming they exist and are documented at `docs.stripe.com/testing` and
  `docs.stripe.com/connect/testing` respectively.
- **The `4000 0000 0000 0069` card's actual decline_code is unresolved** — conflicting synthesis
  (stolen vs. expired) across two independent search queries.
- **Dispute evidence "magic strings"** (the specific `uncategorized_text` values that force a
  dispute win/loss) were not found.
- **Test clock `deletes_after` TTL** (how long after creation a clock auto-deletes) and the
  **`internal_failure` status's precise semantics** (recoverable? does `frozen_time` change?) are
  not documented in `spec3.json`'s prose and were not resolved from search either.
- **No documented cap on test clocks per account/sandbox was found** — unlike the "5 sandboxes"
  limit, which was findable, this number (if it exists) stayed out of reach in this session.
- **Object-shape differences between test and live** (§1.7) is the weakest-sourced section in this
  subtopic — beyond the universal `livemode` boolean and unchanged ID-prefix shape, I could not
  find an itemized list of which fields differ.
- **`mock-stripe` (prasanthkv) and `stripe-ruby-mock`** were surfaced but not read in depth (a raw
  README fetch 404'd for the former; the latter is architecturally out of scope as an SDK-level
  interceptor, not a server) — low-priority follow-up if more prior art is wanted.

## Sources

See the "Sources" section at the bottom of each detail file for full citations. Highest-confidence
sources used across this subtopic:

- `/home/user/stripe_world/research/stripe-openapi/spec3.json` (`Stripe-Version: 2026-08-26.dahlia`) — primary source, read directly via Python queries; authoritative for the entire `test_helpers/test_clocks` API shape (Part 2)
- `/home/user/stripe_world/research/repos/stripe-mock` (full source tree) — primary source, read directly; authoritative for all of Part 3
- [Test card numbers | Stripe Documentation](https://docs.stripe.com/testing) — authoritative for Part 1's magic-value tables in principle; **not directly fetched this session**, see caveat above
- [Test your integration with test clocks | Stripe Documentation](https://docs.stripe.com/billing/testing/test-clocks) — authoritative for Part 2 §2.4's behavioral claims; not directly fetched, WebSearch synthesis only
- [Sandboxes | Stripe Documentation](https://docs.stripe.com/sandboxes) — authoritative for §1.6; not directly fetched
- [adrienverge/localstripe](https://github.com/adrienverge/localstripe), [Giftbit/stripe-stateful-mock](https://github.com/Giftbit/stripe-stateful-mock) — READMEs read directly via `raw.githubusercontent.com`; primary source for the "other prior art" survey
