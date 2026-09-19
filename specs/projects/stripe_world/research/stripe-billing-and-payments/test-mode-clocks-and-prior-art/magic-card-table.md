# Stripe test-mode magic value tables — fixture-ready

**Status: primary-sourced 2026-09-18.** Fetched directly from `docs.stripe.com/testing`,
`docs.stripe.com/connect/testing`, `docs.stripe.com/api/test_clocks*`, and related pages via
`mcp__Tavily__tavily_extract`/`tavily_search` (which reach these domains; this session's `WebFetch`
still cannot). Card numbers were confirmed by cross-referencing Stripe's own page structure (row
order, error codes, decline codes — captured directly from `docs.stripe.com/testing`, whose card-
number table *cells* render client-side and came back blank through both extraction tools) against
**five independent, mutually-consistent, 2026-dated third-party mirrors** (bug0.com, growthy.com,
shopmonitor.io, getautonoma.com, rapidevelopers.com) that all explicitly claim to copy Stripe's
table verbatim, plus one number (`4242424242424241`) confirmed **directly in Stripe's own body
text**, not just a table cell ("`incorrect_number`: Use a card number that fails the Luhn check,
such as `4242424242424241`" — quoted verbatim from `docs.stripe.com/testing`). Where only one
source gave a number, that is marked. Anything not found anywhere is marked GAP, not guessed.

Source URLs are collected once at the bottom rather than repeated on every row.

---

## 1. Declined payments (the core failure-injection table)

Row order and the `code`/`decline_code` columns are **verbatim from `docs.stripe.com/testing`**,
fetched directly. Numbers are corroborated across 5 independent mirrors, all agreeing exactly.

| # | Description | Number | `code` | `decline_code` |
|---|---|---|---|---|
| 1 | Generic decline | `4000000000000002` | `card_declined` | `generic_decline` |
| 2 | Insufficient funds decline | `4000000000009995` | `card_declined` | `insufficient_funds` |
| 3 | Lost card decline | `4000000000009987` | `card_declined` | `lost_card` |
| 4 | Stolen card decline | `4000000000009979` | `card_declined` | `stolen_card` |
| 5 | Expired card decline | `4000000000000069` | `expired_card` | n/a |
| 6 | Incorrect CVC decline | `4000000000000127` | `incorrect_cvc` | n/a |
| 7 | Processing error decline | `4000000000000119` | `processing_error` | n/a |
| 8 | Incorrect number decline | `4242424242424241` | `incorrect_number` | n/a |
| 9 | Exceeding velocity limit decline | `4000000000006975` | `card_declined` | `card_velocity_exceeded` |

**"Decline after attaching"** (can't attach a declining card to a Customer directly — use this one
to test a decline on an *already-attached* payment method): `4000000000000341`. Attaching succeeds;
subsequent charge attempts fail. (5-source corroboration.)

### ⚠️ CORRECTION to the lane's prior finding

`test-mode.md` §1.1 previously flagged a conflict on **`4000 0000 0000 0069`** ("stolen vs. expired,
could not resolve"). **This is now resolved: `4000000000000069` is `expired_card`, not
`stolen_card`.** `stolen_card` is a **different number**: `4000000000009979`. All 5 independent
2026 mirrors agree on this split, it matches Stripe's own row order (Stolen card decline is row 4,
Expired card decline is a separate row 5), and the CVC/processing-error/incorrect-number rows around
it match the official page's `code`/`decline_code` text exactly. Do not use `...0069` for a
stolen-card fixture.

---

## 2. Success cards by brand

Official page confirms brand list and column headers (Brand | Number | CVC | Date) but table cells
were blank on direct fetch; numbers below are from the same 5-mirror corroboration (all identical to
each other for every row that appears in more than one mirror).

| Brand | Number | Notes |
|---|---|---|
| Visa | `4242424242424242` | The canonical always-succeeds card |
| Visa (debit) | `4000056655665556` | |
| Mastercard | `5555555555554444` | |
| Mastercard (2-series) | `2223003122003222` | |
| Mastercard (debit) | `5200828282828210` | |
| Mastercard (prepaid) | `5105105105105100` | |
| American Express | `378282246310005` | Any 4-digit CVC |
| American Express (alt) | `371449635398431` | Any 4-digit CVC |
| Discover | `6011111111111117` | |
| Discover (alt) | `6011000990139424` | single-source (growthy) |
| Discover (debit) | `6011981111111113` | corroborated separately as a Connect payout debit card too (§5) |
| Diners Club | `3056930009020004` | |
| Diners Club (14-digit) | `36227206271667` | single-source (growthy) |
| JCB | `3566002020360505` | |
| UnionPay | `6200000000000005` | |
| UnionPay (debit) | `6200000000000047` | single-source (growthy) |
| UnionPay (19-digit) | `6205500000000000004` | single-source (growthy) |

**GAP — not resolved:** BCcard/DinaCard number; Cartes Bancaires/Visa and /Mastercard co-branded
numbers; eftpos Australia/Visa and /Mastercard co-branded numbers; the full by-country success-card
list (official page confirms ~60 country rows exist — Visa cards for US, AR, BR, CA, and ~50 more —
but cell values were blank on direct fetch and mirrors only sample a handful, e.g. US
`4242424242424242`, CA `4000001240000000`, MX `4000004840008001`, BR `4000000760000002`, GB
`4000008260000000`, FR `4000002500000003`, DE `4000002760000016`, AU `4000000360000006`, IN
`4000003560000008`, JP `4000003920000003`; treat these 9 as reasonably solid single/dual-source and
the rest as unresolved). HSA/FSA card numbers also not resolved (structure confirmed, digits not
found in any mirror).

---

## 3. 3D Secure test cards

| Description | Number | Source confidence |
|---|---|---|
| Authenticate unless set up (off-session requires auth unless saved) | `4000002500003155` | 5-source |
| Always authenticate (every transaction) | `4000002760003184` | 5-source |
| Already set up for off-session use | `4000003800000446` | single-source (growthy) |
| 3DS required, then **declines** after successful auth | `4000008400001629` | corroborated (bug0/growthy, matches prior lane note) |
| 3DS2 required, succeeds | `4000000000003220` | 5-source (always triggers challenge) |
| 3DS supported but not required | `4000000000003055` | corroborated (bug0/growthy) |
| Does not support 3DS | `378282246310005` (Amex) | single-source (shopmonitor) |

**GAP — not resolved:** the official "Support and availability" 3x3 grid's IE-issued vs. US-issued
"3DS Required/OK" rows, the "3DS Required/Error" row, "3DS Supported/Error" and
"3DS Supported/Unenrolled" rows, the "Frictionless flow" row, and all 4 mobile-challenge-flow numbers
(Out of band / One time passcode / Single select / Multi select) — these are documented to exist
(confirmed via direct fetch of `docs.stripe.com/testing`, row structure captured) but no mirror
reproduces their specific digits, and they're too niche to have independent secondary coverage.

---

## 4. Disputes and dispute evidence

Card numbers (5-mirror corroboration where marked, else as noted), row order/framing confirmed
directly from `docs.stripe.com/testing`:

| Description | Number | Confidence |
|---|---|---|
| Fraudulent (3DS-protected) | `4000000000000259` | 5-source, matches prior lane data |
| Not received | `4000000000002685` | corroborated (bug0/growthy/shopmonitor) |
| Inquiry | `4000000000001976` | corroborated (growthy, matches prior lane data) |
| Warning (early fraud warning) | `4000000000005423` | corroborated (bug0/growthy) |
| Multiple disputes | `4000004040000079` | single-source (growthy) |

**GAP — not resolved:** "Discover fraudulent," Visa Compelling Evidence 3.0, Visa compliance,
Mastercard compliance, and Smart Disputes card numbers — all confirmed to exist as distinct rows on
the official page, digits not found in any source this session.

### Dispute evidence "magic strings" — RESOLVED (previously an open gap)

This is **not** a large table of canned evidence text as the lane's prior note speculated. Quoted
**verbatim, directly from `docs.stripe.com/testing`**, fetched 2026-09-18:

> To simulate winning or losing the dispute, respond with one of the evidence values from the table
> below.
> * If you respond using the API, pass the value from the table as `uncategorized_text`.
> * If you respond in the Dashboard..., enter the value from the table in the **Additional
>   information** field.

| Evidence value | Effect |
|---|---|
| `winning_evidence` | Closes the dispute as won; credits your account for the charge + fees |
| `losing_evidence` | Closes the dispute as lost, no credit. For inquiries, closes without escalation |
| `escalate_inquiry_evidence` | Escalates an inquiry to a full chargeback; debits your account |

These 3 literal strings are the entire mechanism — pass one as `uncategorized_text` on the Dispute
update call.

---

## 5. Refund failure (async refund outcomes) — RESOLVED (previously an open gap)

Row framing confirmed directly from `docs.stripe.com/testing` ("Simulate an asynchronous refund");
numbers from 1-source (growthy) matching the official behavioral description exactly:

| Description | Number | Behavior (verbatim official framing) |
|---|---|---|
| Asynchronous success | `4000000000007726` | "its status begins as `pending`. Some time later, its status transitions to `succeeded`" and sends `refund.updated` |
| Asynchronous failure | `4000000000005126` | "its status begins as `succeeded`. Some time later, its status transitions to `failed`" and sends `refund.failed` |

All other test cards refund synchronously (immediate `succeeded`, no status change after). Refund
cancellation window in test mode: 30 minutes (vs. an unspecified short window in live mode) — direct
quote from `docs.stripe.com/testing`.

---

## 6. Connect payout failure — RESOLVED (previously an open gap)

**Directly quoted from `docs.stripe.com/connect/testing`, fetched 2026-09-18.** Two separate
mechanisms: bank accounts and debit cards.

### Bank account numbers (routing `110000000` for all)

| Account number | Result |
|---|---|
| `000123456789` | Payout succeeds |
| `000111111116` | Payout fails, `no_account` |
| `000111111113` | Payout fails, `account_closed` |
| `000222222227` | Payout fails, `insufficient_funds` |
| `000333333335` | Payout fails, `debit_not_authorized` |
| `000444444440` | Payout fails, `invalid_currency` |
| `000888888883` | Payout fails only if `method` is `instant` (not eligible for Instant Payouts) |

Note: these are the **same digit patterns** as the generic ACH-failure account numbers in §7 below,
except `000888888883` (Connect/payout-specific — not in the generic ACH table) — **do not** confuse
this with the generic-ACH-test-account list's `000888888885` (deactivated tokenized account number),
which is a different, unrelated number one digit off.

### Debit card numbers (for payouts to a debit card)

| Number | Token | Result |
|---|---|---|
| `4000056655665556` | `tok_visa_debit_us_transferSuccess` | Payout succeeds |
| `4000056655665572` | `tok_visa_debit_us_transferFail` | Fails, `could_not_process` |
| `4000056755665555` | `tok_visa_debit_us_instantPayoutUnsupported` | Not eligible for Instant Payouts |
| `5200828282828210` | `tok_mastercard_debit_us_transferSuccess` | Payout succeeds |
| `6011981111111113` | `tok_discover_debit_us_transferSuccess` | Payout succeeds |

---

## 7. ACH / US bank account test values (general, non-Connect)

**Directly quoted from `docs.stripe.com/testing`.** This table is bigger than the lane's prior
2-row version — full table below, account number / token / routing / behavior:

| Account number | Token | Routing | Behavior |
|---|---|---|---|
| `000123456789` | `pm_usBankAccount_success` | `110000000` | Succeeds |
| `000111111113` | `pm_usBankAccount_accountClosed` | `110000000` | Fails — account closed |
| `000000004954` | `pm_usBankAccount_riskLevelHighest` | `110000000` | Blocked by Radar (highest risk) |
| `000111111116` | `pm_usBankAccount_noAccount` | `110000000` | Fails — no account found |
| `000222222227` | `pm_usBankAccount_insufficientFunds` | `110000000` | Fails — insufficient funds |
| `000333333335` | `pm_usBankAccount_debitNotAuthorized` | `110000000` | Fails — debits not authorized |
| `000444444440` | `pm_usBankAccount_invalidCurrency` | `110000000` | Fails — invalid currency |
| `000666666661` | `pm_usBankAccount_failMicrodeposits` | `110000000` | Fails to send microdeposits |
| `000555555559` | `pm_usBankAccount_dispute` | `110000000` | Triggers a dispute |
| `000000000009` | `pm_usBankAccount_processing` | `110000000` | Stays in `processing` indefinitely (for testing PI cancellation) |
| `000777777771` | `pm_usBankAccount_weeklyLimitExceeded` | `110000000` | Fails — exceeds weekly volume limit |
| `000888888885` | (none) | `110000000` | Fails — deactivated tokenized account number |

Microdeposit / 0.01-descriptor verification codes:

| Microdeposit values | Descriptor code | Scenario |
|---|---|---|
| `32` and `45` | `SM11AA` | Verifies the account |
| `10` and `11` | `SM33CC` | Exceeds allowed verification attempts |
| `40` and `41` | `SM44DD` | Microdeposit timeout |

Bank-debit async settlement timing (direct quote): "In test mode, transitions happen after about 3
minutes for account numbers that simulate a delay. These use a `9` prefix or suffix."

---

## 8. IBAN / SEPA test values — RESOLVED (previously an open gap)

Two generations of test IBANs exist, both confirmed directly from `docs.stripe.com`:

**Legacy Sources API** (`docs.stripe.com/sources/sepa-debit`):

| IBAN | Behavior |
|---|---|
| `AT611904300234573201` | pending → succeeded |
| `AT861904300235473202` | pending → failed |
| `AT591904300235473203` | pending → succeeded, but a dispute is immediately created |

**Current PaymentIntent/Payment Element API** (`docs.stripe.com/payments/sepa-debit/set-up-payment`,
`docs.stripe.com/billing/subscriptions/sepa-debit`):

| IBAN | Token | Behavior |
|---|---|---|
| `AT611904300234573201` | `pm_sepaDebit_success_at` | `processing` → `succeeded` |
| `AT321904300235473204` | `pm_sepaDebit_successDelayed_at` | `processing` → `succeeded`, delayed ≥3 min |
| `AT861904300235473202` | `pm_sepaDebit_failed_at` | `processing` → `requires_payment_method` |
| `AT051904300235473205` | `pm_sepaDebit_failedDelayed_at` | same, delayed ≥3 min |
| `AT511904300000066666` | (none) | PaymentMethod creation itself fails, `bank_account_unusable` |

Note the account-number reuse: `AT611904300234573201` and `AT861904300235473202` carry the same
success/fail semantics across both API generations.

---

## 9. Link one-time-passcode test values

Directly quoted from `docs.stripe.com/testing`:

| Value | Outcome |
|---|---|
| Any 6 digits not listed below | Success |
| `000001` | Error, code invalid |
| `000002` | Error, code expired |
| `000003` | Error, max attempts exceeded |

---

## 10. `pm_card_*` PaymentMethod tokens

Corroborated set (lane's prior list plus a few from growthy's mirror):

- Success: `pm_card_visa`, `pm_card_visa_debit`, `pm_card_mastercard`, `pm_card_amex`,
  `pm_card_discover`, `pm_card_diners`, `pm_card_jcb`, `pm_card_unionpay`
- Declines: `pm_card_visa_chargeDeclined`, `pm_card_visa_chargeDeclinedInsufficientFunds`,
  `pm_card_visa_chargeDeclinedLostCard`, `pm_card_visa_chargeDeclinedStolenCard`,
  `pm_card_chargeDeclinedExpiredCard`, `pm_card_chargeDeclinedIncorrectCvc`,
  `pm_card_chargeDeclinedProcessingError`, `pm_card_visa_chargeDeclinedVelocityLimitExceeded`
- 3DS: `pm_card_threeDSecure2Required`, `pm_card_threeDSecureRequiredChargeDeclined`,
  `pm_card_threeDSecureOptional`, `pm_card_authenticationRequired`
- Disputes: `pm_card_createDispute`, `pm_card_createDisputeProductNotReceived`,
  `pm_card_createDisputeInquiry`
- Fraud: `pm_card_radarBlock`, `pm_card_riskLevelHighest`, `pm_card_riskLevelElevated`,
  `pm_card_cvcCheckFail`, `pm_card_avsZipFail`

Older parallel `tok_*` tokens exist (`tok_visa`, `tok_visa_chargeDeclined`, etc.) — still not
exhaustively enumerated; low priority per the original gap note (PaymentMethods are the documented
current path).

---

## Sources

- [Test card numbers | Stripe Documentation](https://docs.stripe.com/testing) — fetched directly
  2026-09-18 via `mcp__Tavily__tavily_extract` (both `markdown` and `text` formats); table *cell*
  values render client-side and came back empty in both formats, but table row order, column
  headers, `code`/`decline_code` values, all body-text card numbers (e.g. `4242424242424241`), all
  evidence strings, all ACH/ Link/microdeposit tables, and all descriptive text were captured
  verbatim.
- [Testing Stripe Connect | Stripe Documentation](https://docs.stripe.com/connect/testing) — fetched
  directly 2026-09-18, full content including tables (this page's tables rendered as plain
  markdown/text, unlike `/testing`'s).
- [The Test Clock object | Stripe API Reference](https://docs.stripe.com/api/test_clocks/object) —
  fetched via `tavily_search`, 2026-09-18.
- [SEPA Direct Debit payments with Sources](https://docs.stripe.com/sources/sepa-debit),
  [Save SEPA Direct Debit details](https://docs.stripe.com/payments/sepa-debit/set-up-payment),
  [Set up a subscription with SEPA Direct Debit](https://docs.stripe.com/billing/subscriptions/sepa-debit)
  — fetched via `tavily_search` snippets, 2026-09-18.
- Third-party mirrors (2026-dated, all state they copy Stripe's table verbatim, used only to fill in
  card-number *digits* that Stripe's own page structure confirmed but whose cells didn't render):
  [bug0.com/blog/stripe-test-cards-2026](https://bug0.com/blog/stripe-test-cards-2026),
  [growthy.com/blog/stripe-test-cards](https://growthy.com/blog/stripe-test-cards),
  [shopmonitor.io/blog/stripe-test-cards](https://shopmonitor.io/blog/stripe-test-cards),
  [getautonoma.com/blog/stripe-test-cards](https://getautonoma.com/blog/stripe-test-cards),
  [rapidevelopers.com/stripe-guide/how-to-fix-card-declined-error-in-stripe-api](https://www.rapidevelopers.com/stripe-guide/how-to-fix-card-declined-error-in-stripe-api)
  — all fetched via `tavily_search`, 2026-09-18.
