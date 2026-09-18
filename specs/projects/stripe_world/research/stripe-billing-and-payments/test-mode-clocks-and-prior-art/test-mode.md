# Part 1 — Test mode: magic values, tokens, sandboxes, and test/live shape differences

**Sourcing note (read this first):** in this session, direct `WebFetch` of every Stripe-owned
domain (`docs.stripe.com`, `stripe.com`, `stripe.dev`, `edge-docs.stripe.com`) returned
`EGRESS_BLOCKED` from the network egress proxy on every attempt — including a control fetch of
`en.wikipedia.org`, which also failed, then succeeded once redirected... actually all non-Stripe
domains I tried besides `raw.githubusercontent.com`/`api.github.com` failed too `EGRESS_BLOCKED`
is scoped per-domain and Stripe's domains specifically were on the blocklist for this run (`gh`
CLI was also unavailable). This is a deviation from the "WebFetch confirmed working" premise in
the dispatch prompt. `WebSearch`, however, works and returns a synthesized answer (built from the
search provider's own fetch of the source pages, not blocked) plus a source-link list. Every card
number, code and figure below was cross-checked across **2+ independent `WebSearch` queries** and,
where possible, against community mirrors (gists, blog posts that reproduce Stripe's table). But
none of this is a verbatim primary-source read of `docs.stripe.com/testing` — treat the table
below as **high-confidence secondary sourcing, not a verified primary quote**, and re-derive it
from the live page before hard-coding it into fixtures. This caveat applies to this entire file;
Parts 2 (test clocks) and 3 (stripe-mock) are **not** affected — those are sourced from the
pre-fetched `spec3.json` and the `stripe-mock` repo on disk, both read directly.

## 1.1 The magic test card table

Source: synthesized from `docs.stripe.com/testing` via WebSearch (multiple queries), cross-checked
against secondary reproductions (Bug0, Growthy, Finlens, PureDevKit, CardForge blogs — all of which
claim to mirror the official table) and a community gist
([rymawby/9b904feec040b25a8034](https://gist.github.com/rymawby/9b904feec040b25a8034)).

**Working / success cards** (by brand, no decline):

| Number | Brand |
|---|---|
| `4242 4242 4242 4242` | Visa — the canonical "always succeeds" card |

Any future expiry date, any 3-digit CVC (4 digits for Amex), and any postal code are accepted with
these cards.

**Generic and issuer-style decline cards** — each "returns a card error with the listed error code
and decline code" per Stripe's own framing (a card error's `type` is `card_error`, its `code` is
almost always `card_declined`, and the specific reason rides in `decline_code`):

| Card number | `code` | `decline_code` | Scenario |
|---|---|---|---|
| `4000 0000 0000 0002` | `card_declined` | `generic_decline` | Generic decline, no reason given |
| `4000 0000 0000 9995` | `card_declined` | `insufficient_funds` | Insufficient funds |
| `4000 0000 0000 9987` | `card_declined` | `lost_card` | Lost card |
| `4000 0000 0000 0069` | `card_declined` | `stolen_card` (also cited for `expired_card` in one source — see note) | See note below |
| `4000 0000 0000 0119` | `processing_error` | `processing_error` | Processing error |
| `4000 0000 0000 0127` | `incorrect_cvc` | `incorrect_cvc` | Incorrect CVC |
| `4100 0000 0000 0019` | `card_declined` | `fraudulent` | Always blocked by Radar as high-risk/fraudulent |

**Note on `4000 0000 0000 0069`:** search snippets disagreed — one synthesis attributed it to
"stolen card," another (from a different query) attributed the *same* number to "expired card." I
could not resolve this discrepancy without a primary-source read. **Do not trust this specific
number without verifying against a live `docs.stripe.com/testing` fetch** — this is exactly the
kind of load-bearing fact that needs a direct check before it goes into a fixtures file. All the
other numbers in the table above appeared consistently across independent queries.

**Known gap:** I could not get a complete, exhaustive top-to-bottom transcription of every row in
Stripe's decline table (the docs page is reported to include additional codes like
`incorrect_number`, `expired_card` as its own row with its own dedicated number,
`card_velocity_exceeded` / `velocity_limit_exceeded`, `do_not_honor`, `call_issuer`,
`pickup_card`, `restricted_card`, etc. — see `docs.stripe.com/declines/codes` for the full
*decline_code* enum, which is bigger than the *test-card* table; the test-card table only has a
dedicated magic number for a subset of these). The full `decline_code` enum itself is subtopic 2's
territory (cross-cutting semantics / error envelope) — flagging the overlap rather than
re-deriving it here.

## 1.2 Special-purpose magic cards (3DS, disputes, refunds, subscription-payment-failure)

**3D Secure:**

| Card number | Behavior |
|---|---|
| `4000 0025 0000 3155` | Requires 3DS authentication for one-time payment; once saved and reused off-session, no further authentication is required |
| `4000 0000 0000 3220` | Always triggers 3DS2 (the challenge modal) |
| `4000 0000 0000 3063` | Triggers 3DS2, then **declines after** successful authentication |
| `4000 0027 6000 3184` | Triggers a 3DS2 challenge flow that **succeeds** when completed |
| `4000 0084 0000 1629` | Also documented as a 3DS test PAN (specific behavior not resolved from search snippets) |

In test mode, Stripe's Checkout/Payment Element present a simulated authentication dialog with
buttons for "Complete authentication" and "Fail authentication" — no real card network round-trip
happens (this squares with the "no attempt to reproduce live behavior, only shape/flow" theme —
though note 3DS *flow simulation* is a special testmode UI feature layered on top of the API, not
something `stripe-mock` reproduces at all; see Part 3).

**Disputes** — cards that make a successful charge and then generate an automatic dispute:

| Card number | Dispute reason created |
|---|---|
| `4000 0000 0000 0259` | `fraudulent` |
| `4000 0000 0000 1976` | `inquiry` |

Workflow: the charge itself **succeeds**; the dispute is created asynchronously afterward (webhook
`charge.dispute.created` fires). To simulate winning/losing the dispute, you submit dispute
evidence using specific canned values from a table in the evidence-submission docs (pass the value
as `uncategorized_text` via the API, or paste it into the "Additional information" field in the
Dashboard) — the specific magic evidence strings were not resolved from search snippets (gap).

**Subscription/invoice payment failure:**

| Card number | Behavior |
|---|---|
| `4000 0000 0000 0341` | Attaching the card to a Customer **succeeds**; any subsequent attempt to **charge** it fails. The documented workflow: attach as the customer's default payment method, give the subscription a short trial (seconds/minutes) so the first charge is deferred; the subscription still goes `active` immediately; when the trial ends a draft invoice is created; after roughly the **1-hour draft→open window** (see subtopic 3 for the general invoice-finalization timing) the invoice moves to `open` and payment collection is attempted and fails. |

**Refunds:** live-mode refunds are documented as asynchronous and can "appear to succeed then
fail," or "appear pending then succeed" — Stripe ships dedicated test cards to simulate each of
those async refund outcomes in test mode, but I could not pin down the exact magic numbers from
search snippets alone (one weak signal: `4000 0000 0000 5126` was returned in one synthesis as
relevant to "simulating a purchase" in a refund-testing context, but I was not able to corroborate
what specific refund behavior it triggers — treat as unverified). **Gap: exact refund-failure
magic card numbers not resolved.**

**Payouts (Connect):** Stripe documents dedicated test bank-account/debit-card numbers (separate
from the charge-decline card table) to trigger payout success/failure scenarios, including
insufficient-funds payout failures, at `docs.stripe.com/connect/testing`. I was not able to
resolve the exact magic account numbers from search snippets — only that they exist and are
distinct from the charge-card table. **Gap: exact payout-failure magic values not resolved**; the
one number pattern that surfaced with confidence is the ACH pair below.

## 1.3 `pm_card_*` and `tok_*` magic tokens

Stripe's current guidance (per the docs' own framing, surfaced via WebSearch) is to write test code
against pre-built **PaymentMethod** tokens (`pm_card_*`) rather than raw card numbers, so you can
skip collecting card details through a client-side Element entirely. "Most test cards have an
equivalent `pm_` token."

Confirmed `pm_card_*` tokens (decline family — note the `visa` infix appears on some but not all):

- `pm_card_visa` — plain successful Visa
- `pm_card_visa_chargeDeclined` — generic decline
- `pm_card_visa_chargeDeclinedInsufficientFunds` — insufficient funds
- `pm_card_visa_chargeDeclinedLostCard` — lost card
- `pm_card_visa_chargeDeclinedStolenCard` — stolen card
- `pm_card_chargeDeclinedExpiredCard` — expired card
- `pm_card_chargeDeclinedIncorrectCvc` — incorrect CVC
- `pm_card_chargeDeclinedProcessingError` — processing error
- `pm_card_visa_chargeDeclinedVelocityLimitExceeded` — velocity limit exceeded

3DS-family `pm_card_*` tokens:

- `pm_card_threeDSecure2Required`
- `pm_card_threeDSecureRequiredChargeDeclined`
- `pm_card_threeDSecureOptional`
- `pm_card_authenticationRequired`

**Older `tok_*` tokens** (legacy Sources/Tokens API; still documented and functional but described
as no-longer-the-default path for new integrations): `tok_visa`, `tok_visa_chargeDeclined`, and by
extension a parallel `tok_*` token for most of the same decline scenarios as the `pm_card_*` table.
I was not able to enumerate the complete parallel `tok_*` list from search snippets — only that it
mirrors the `pm_card_*` set structurally. **Gap: full `tok_*` enumeration not resolved.**

## 1.4 Other magic values (bank accounts / ACH)

For manually-entered US bank accounts (ACH Direct Debit), the documented test pair is:

- **Successful** account: account number `000123456789`, routing number `110000000`
- **Failing** account: account number `000111111116`, routing number `110000000`

(Source: WebSearch synthesis of `docs.stripe.com/payments/ach-direct-debit/...` and
`docs.stripe.com/connect/testing`.) I could not resolve a corresponding table of test **IBAN**
values for SEPA testing from search snippets — **gap**.

## 1.5 What is testmode-only

From the docs (via search synthesis) and cross-referenced against the `stripe-mock` auth-check
logic (Part 3), which hard-requires a `sk_test_`/`rk_test_`-shaped key — i.e., stripe-mock itself
encodes the assumption that only test-mode-shaped keys are meaningful in a mock context:

- All of the magic card numbers, `pm_card_*`/`tok_*` tokens, and the 3DS simulated-auth dialog are
  **testmode-only constructs** — live mode has no equivalent "magic number" surface; live behavior
  depends on the real card networks/issuers.
- Test mode "doesn't affect your live data or interact with the banking networks" — no real money
  moves, and objects created in test mode are entirely separate from live-mode objects (different
  ID space, not visible to each other, `livemode: false` on every object).
- API keys are mode-scoped by prefix: `sk_test_...` / `pk_test_...` / `rk_test_...` vs
  `sk_live_...` / `pk_live_...` / `rk_live_...`. Which mode a request executes in is determined
  **solely by which key was used to authenticate** — there is no separate "mode" request
  parameter.
- Test clocks (Part 2), the entire `test_helpers.*` namespace, and (per the OpenAPI spec, confirmed
  directly — not from search) endpoints like `POST /v1/test_helpers/test_clocks` only make sense
  in test mode; nothing in the spec text says they 404 in live mode, but the ecosystem convention
  (and the fact that `test_helpers` schemas carry `livemode: false` required-true style framing) is
  that they are testmode-only in practice.

## 1.6 Sandboxes vs a plain test-mode key

Sandboxes are Stripe's newer, heavier-weight test environment concept, layered *above* "test mode"
rather than replacing it. Per WebSearch synthesis of `docs.stripe.com/sandboxes` and the Stripe Dev
blog "Avoiding test-mode tangles with Stripe Sandboxes" (direct fetch of both blocked in this
session — see the sourcing note at the top of this file):

| | Test mode (classic) | Sandbox |
|---|---|---|
| Environments per account | 1 shared test-mode environment | **Up to 5 sandboxes per account**, each isolated |
| Settings | Shared between live and test mode (can't configure independently) | Fully isolated settings per sandbox |
| API keys | One test-mode keypair | **Each sandbox issues its own publishable + secret key pair** |
| Access control | All users with account roles get the same test-mode access | Granular: only admins get sandbox access automatically; admins can invite users to specific sandboxes without granting live-mode access |
| Cost | N/A (bundled) | Free |
| Status | Legacy path, still supported | Positioned as **the current default/recommended way to test** |

Practically: a "test-mode key" (`sk_test_...`) is the authentication credential; a "Sandbox" is a
whole isolated *account-like* environment (its own settings, webhooks, Connect config, products,
etc.) that happens to also use `sk_test_...`-prefixed keys scoped to that sandbox. You cannot tell
which sandbox a `sk_test_` key belongs to from the key's shape alone — that's account-side state,
which matters for `stripe-mock` because stripe-mock's own auth check (Part 3) only validates that a
key is *shaped* like `sk_test_*`/`rk_test_*`, not which sandbox/account it nominally belongs to (it
has no concept of accounts at all).

**Gap:** I could not corroborate sandbox lifecycle details (auto-expiry, data retention, whether
sandbox data is wiped on a schedule) from search snippets — the direct fetch that would have
answered this (`docs.stripe.com/sandboxes`) was blocked. Flagging for a follow-up direct read.

## 1.7 Object-shape differences between test and live

This is the thinnest-sourced part of this file — the direct docs pages that would enumerate this
precisely were unreachable, and WebSearch's synthesis kept returning "no detailed field-level
differences found" for repeated query phrasings. What **is** confirmed:

- Every object carries a `livemode: boolean` field (confirmed structurally from `spec3.json`'s
  `test_helpers.test_clock` schema in Part 2, and it's a standard field across the Stripe schema)
  — this is the one universal, machine-checkable discriminator between test and live objects.
- ID prefixes (`cus_`, `ch_`, `pi_`, etc.) are **the same shape** in test and live — you cannot
  tell mode from an ID's prefix, only from `livemode` on the object or which key range fetched it.
- Radar / fraud-scoring fields, `receipt_url`/`receipt_number` on charges, and real
  processor/network metadata (e.g., `network_transaction_id`, AVS/CVC actual-check results from a
  real issuer) are populated with realistic-looking but non-authoritative values in test mode —
  they don't reflect a real network round-trip. I could not find a documented, itemized list of
  *which* fields specifically differ in test vs. live — **gap**, flag for a direct-fetch follow-up.
- Test mode has explicitly reduced/absent Radar risk scoring realism, no real payout/bank transfer
  timing, and no real dispute network involvement — the *behavior* differs even where the *shape*
  of the response is identical, which is consistent with Stripe's own "type-correct, not
  necessarily realistic" framing that also describes `stripe-mock` (Part 3) — test mode is a live
  server that fakes outcomes on your input; `stripe-mock` is a static server that fakes outcomes on
  the schema.

## Sources

- [Test card numbers | Stripe Documentation](https://docs.stripe.com/testing) — primary source for §1.1–1.4; **not directly fetched this session** (egress-blocked), content via WebSearch synthesis only, cross-checked across multiple queries
- [Sandboxes | Stripe Documentation](https://docs.stripe.com/sandboxes) — primary source for §1.6; not directly fetched, WebSearch synthesis only
- [Avoiding test mode tangles with Stripe Sandboxes | Stripe Dev blog](https://stripe.dev/blog/avoiding-test-mode-tangles-with-stripe-sandboxes) — corroborating source for §1.6; fetch attempt blocked (`stripe.dev` on the same egress blocklist)
- [Stripe test credit card numbers for use in development (gist)](https://gist.github.com/rymawby/9b904feec040b25a8034) — community mirror used to cross-check §1.1 numbers
- `stripe-mock` source (`/home/user/stripe_world/research/repos/stripe-mock/server/server.go`, `validateAuth`) — directly read; confirms the `sk_test_`/`rk_test_` key-shape check described in §1.5
