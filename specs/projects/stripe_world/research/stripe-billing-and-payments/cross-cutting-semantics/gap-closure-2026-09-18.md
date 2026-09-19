# Gap Closure Pass — 2026-09-18

This file closes the specific gaps left open in this lane's `summary.md` after direct `docs.stripe.com`
access became available via a Tavily MCP server (verified working — `mcp__Tavily__tavily_extract` reads
`docs.stripe.com` pages directly). Every item below was previously sourced from **WebSearch's synthesized
summaries** (no direct fetch); this pass re-derives each from a direct page read, quotes the exact
sentence, and states explicitly whether it **confirms**, **corrects**, or **refines** the prior claim.

Method note: `WebFetch` still cannot reach `docs.stripe.com` in this environment (confirmed not used);
all reads below are `mcp__Tavily__tavily_extract` against the literal URL, or `mcp__Tavily__tavily_search`
where a single page didn't cover the question and a targeted secondary source (a real observed API
response, quoted verbatim in a GitHub issue) was the best available evidence.

---

## 1. Idempotency-key retention wording and window

**Question:** What is the exact retention window and wording for idempotency keys?

**Answer:** **Confirms** the prior "~24 hours" claim, and **refines** it with the exact wording — the
real rule is "at least 24 hours," i.e. a floor, not a fixed TTL; keys are pruned automatically no sooner
than 24h but the doc does not promise exactly-24h expiry.

**Verbatim quote** (from `docs.stripe.com/api/idempotent_requests`, fetched directly):

> "You can remove keys from the system automatically after they're at least 24 hours old. We generate a
> new request if a key is reused after the original is pruned. The idempotency layer compares incoming
> parameters to those of the original request and errors if they're not the same to prevent accidental
> misuse."

Also confirms, verbatim, the v1 POST-only claim in `idempotency.md`:

> "All `POST` requests accept idempotency keys. Don't send idempotency keys in `GET` and `DELETE`
> requests because it has no effect. These requests are idempotent by definition."

And confirms the cache-only-after-execution-begins carve-out verbatim:

> "We save results only after the execution of an endpoint begins. If incoming parameters fail
> validation, or the request conflicts with another request that's executing concurrently, we don't save
> the idempotent result because no API endpoint initiates the execution. You can retry these requests."

**URL:** https://docs.stripe.com/api/idempotent_requests

**Disposition:** Prior claim (`idempotency.md`, "Retention period" section) **confirmed and refined**
with exact wording. Update `idempotency.md`'s "Open items" — the retention figure is now directly
sourced, not WebSearch-synthesized.

---

## 2. Pagination ordering-guarantee sentence

**Question:** What does Stripe's own prose say about list-pagination ordering?

**Answer:** **Confirms** the lane's "newest first" / reverse-chronological framing, and gives the exact
citable sentence the prior pass could not fetch.

**Verbatim quote** (from `docs.stripe.com/api/pagination`, fetched directly):

> "Stripe's list API methods use cursor-based pagination through the `starting_after` and `ending_before`
> parameters. Both parameters accept an existing object ID value (see below) and return objects in
> reverse chronological order. The `ending_before` parameter returns objects listed before the named
> object. The `starting_after` parameter returns objects listed after the named object. These parameters
> are mutually exclusive."

**URL:** https://docs.stripe.com/api/pagination

**Bonus correction found on the same page, not one of the seven items but load-bearing:** the lane's
`pagination.md`/`summary.md` describe search endpoints as endpoints that "**do** carry `total_count`."
The docs page **corrects** this — `total_count` is **not returned by default** on search results at all;
it must be explicitly requested via `expand`:

> "`total_count` — optional positive integer or zero — The total number of objects that match the query,
> only accurate up to 10,000. **This field isn't included by default. To include it in the response,
> [expand](/api/expanding_objects) the `total_count` field.**"

This is a real correction: `pagination.md`'s Section 2 schema excerpt (from `spec3.json`) is technically
accurate (the field exists in the schema, optional/not-required) but the prose framing "search endpoints
... **do** carry `total_count`" overstates it — it is opt-in via `expand[]=total_count`, same as any other
expandable field, not a value that ships by default the way `has_more` does.

**Disposition:** Ordering-guarantee sentence **confirmed** verbatim. `total_count`-on-search
**corrected**: opt-in via `expand`, not default-included. Update `pagination.md` and `summary.md`.

---

## 3. `starting_after` / `ending_before` on a deleted object's id

**Question:** What happens when the cursor id refers to a genuinely deleted object?

**Answer:** **Still open.** Direct reads of `docs.stripe.com/api/pagination` and
`docs.stripe.com/api/pagination/search` (both fetched in full this pass) do not address this case at
all — the prose only defines cursor semantics in terms of "an existing object ID value" and says nothing
about a since-deleted one. This is not a page-access failure this time (the page was read in full); the
prose genuinely doesn't cover this edge case. The `stripe-node#2368` corroboration already in
`pagination.md` (filter-interaction case, `resource_missing` when auto-paginating past a state change)
remains the best available indirect evidence, and the general cursor-pagination-design inference (id-based
cursors conventionally still work as positional markers after the referenced object is gone) still stands
as inference, not confirmation.

**Disposition:** **No change** — genuinely unresolved even with direct docs access. Recommend live
test-mode verification as before. Leave `pagination.md`'s gap note in place, but note in `summary.md`
that this was re-checked against the full prose page this pass and remains unanswered by Stripe's own
docs (upgrade the gap's provenance note from "couldn't fetch the page" to "fetched the page, it doesn't
say").

---

## 4. Bad/invalid `expand[]` path — error or silent ignore?

**Question:** What happens when `expand[]` names a field that doesn't exist or isn't expandable?

**Answer:** **Resolved — hard error, not silent ignore.** `docs.stripe.com/api/expanding_objects` itself
(fetched directly, in full) does not state the failure-mode explicitly, but a real, currently-open
`stripe-python` GitHub issue quotes the exact wire error text verbatim, and it's corroborated by two
independent secondary reports of the same error family:

**Verbatim quotes** (real API responses, quoted in GitHub issues / forums, not synthesized):

> `stripe/stripe-python#316`: "InvalidRequestError ... **This property cannot be expanded (application)**
> ... You may want to try expanding **'data.application'** instead" — this was on a `list` call missing
> the `data.` prefix; the error message itself suggests the fix.

> `craftcms/commerce-stripe#343`: "**This property cannot be expanded because it doesn't exist:
> payment_intent.**" — a variant wording for a field that isn't a real property at all (vs. one that
> exists but isn't the flavor of a valid expand target for that endpoint).

> Salesforce Stack Exchange (quoting a raw Stripe API error body): `{"error": {"message": "This property
> cannot be expanded (data).", "type": "invalid_request_error"}}`.

So: an invalid/unrecognized `expand[]` path is a **hard 400, `type: "invalid_request_error"`**, with a
`message` of the form **"This property cannot be expanded (`<field>`)."** — or **"This property cannot
be expanded because it doesn't exist: `<field>`."** for a field that isn't a real property at all — never
silently ignored. On list endpoints missing the required `data.` prefix, the message additionally
suggests the correct path.

**URLs:** https://docs.stripe.com/api/expanding_objects (confirms depth-4 cap and `data.` prefix
mechanism, but not the error text); https://github.com/stripe/stripe-python/issues/316;
https://github.com/craftcms/commerce-stripe/issues/343;
https://salesforce.stackexchange.com/questions/341312 (secondary, quoting a raw error body).

**Disposition:** Prior "not confirmed this session" in `expand.md` is now **resolved and corrected** —
it is a hard error, with a knowable message template, not an open question. Update `expand.md` and move
this out of Open Questions.

---

## 5. Unknown/invalid `Stripe-Version` handling, and whether responses carry a version header

**Question (two parts):** (a) What happens on an unrecognized/malformed `Stripe-Version` value? (b) Does
the live API echo a version-related response header?

**Answer to (b): Resolved — yes, confirmed.** A real API response's raw headers, quoted verbatim in a
`stripe-node` GitHub issue (`#1127`), include a literal `stripe-version` response header reflecting the
version the response was actually rendered at:

> `headers: {server: 'nginx', date: '...', 'content-type': 'application/json', ..., 'request-id':
> 'req_ilaWI3cmDeGzoc', '`**`stripe-version`**`': '`**`2020-08-27`**`', 'x-stripe-c-cost': '0', ...}`

This directly contradicts `versioning.md`'s "Not confirmed this session" — the live API **does** set a
`Stripe-Version` response header (lowercased in this dump per Node's header normalization) equal to the
version the request was actually served at.

**URL:** https://github.com/stripe/stripe-node/issues/1127 (real API response headers, quoted in full by
the issue reporter).

**Answer to (a): Still open, but now searched directly against the authoritative source rather than
inferred.** `docs.stripe.com/api/versioning` (fetched directly, full page) says only:

> "Each major release, such as [Acacia](/changelog/acacia), includes changes that aren't
> [backward-compatible] with previous releases. Upgrading to a new major release can require updates to
> existing code. Each monthly release includes only backward-compatible changes, and uses the same name
> as the last major release. You can safely upgrade to a new monthly release without breaking any
> existing code. The current version is 2026-08-26.dahlia."

— nothing about malformed/unrecognized version strings. Targeted searches for the specific behavior
(`site:docs.stripe.com "Stripe-Version" invalid`, etc.) surfaced only: (1) a documented case of
requesting a version that's simply **too old for a given feature**, which produces a functional error at
the endpoint level, not a header-parsing error — e.g. a real quoted error: *"Search is not supported on
api version 2020-03-02. Update your API version, or set the API Version of this request to 2020-08-27 or
greater."* — and (2) beta/preview version suffixes (e.g. `2025-03-31.basil;fx_quote_preview=v1`) being
*required* for certain endpoints, with a real quoted error for a missing one: *"Unrecognized request URL
... Hint: In order to access this beta feature, you must explicitly specify which version of the beta
you want, by passing an HTTP header..."* Neither of these is the "garbage/non-existent date string in
`Stripe-Version`" case originally asked about. **No source (prose doc or real quoted error) answers this
precisely** — treat as a genuinely undocumented implementation detail, not a research gap that more
searching would close.

**URLs:** https://docs.stripe.com/api/versioning;
https://forum.bubble.io/t/strange-stripe-error-message/307212 (real quoted "Search is not supported on
api version..." error); https://forum.bubble.io/t/stripe-fx-api-help/367164 (real quoted beta-header-hint
error).

**Disposition:** (b) **corrected** — response version header confirmed to exist (was "not confirmed").
(a) **unchanged**, but upgrade its provenance note the same way as item 3: directly checked this pass,
Stripe's own docs are silent on it. Update `versioning.md` and `summary.md`.

---

## 6. `param` naming rules for nested/array parameters in `invalid_request_error` bodies

**Question:** How does Stripe name the `param` field for nested-object and array parameters?

**Answer:** **Refined** with real verbatim wire examples (previously WebSearch-synthesized only, no
direct quotes). `docs.stripe.com/api/errors` itself (fetched directly, full page) does not document the
naming grammar — it only defines `param` as "the parameter related to the error" — so the grammar itself
remains inferred from real observed error bodies, but those are now genuine wire-quoted examples rather
than a paraphrase:

**Verbatim real API error bodies** (quoted in GitHub issues / forum threads reporting live requests):

> `stripe-node#1127`: `{code: 'parameter_missing', message: 'Missing required param:
> line_items[currency].', param: 'line_items[currency]', type: 'invalid_request_error'}` — bracket
> notation for a field nested inside an array-of-objects parameter, **without a numeric index** when the
> error is about the array-item shape generically (Checkout Session `line_items`).

> `stripe-node#1127` (same issue, different case): `"You passed an empty string for 'line_items[price]'.
> ... 'line_items[price]' cannot be unset."` — same bracket pattern, no index, for `price`.

> Bubble.io forum (quoting a real 400 body): `{"message": "No such price: '...'; ...", "param":
> "line_items[price]", "type": "invalid_request_error"}` and, from the same thread, a separate error
> whose `param` is the bare top-level array name with no brackets at all: `{"message": "Invalid array",
> "param": "payment_method_types", "type": "invalid_request_error"}` — confirming the bracket suffix is
> only added when the error is about a sub-field, not the array parameter itself.

> A second Bubble.io thread independently reports an **indexed** array-item case in the same family:
> `Line_items [0][...` — i.e. when Stripe *can* pin the error to a specific array element, the `param`
> does carry a numeric index (`line_items[0][price]`-shaped), consistent with the deepObject/bracket
> encoding the OpenAPI spec itself uses for `expand[]` and range-filter objects (already documented in
> `pagination.md`/`expand.md`).

**Synthesis:** The naming rule is: dot-free, PHP-style bracket nesting (`parent[child]`,
`parent[N][child]` for array items when an index is resolvable, bare `parent` for whole-array-level
errors) — this **confirms** the bracket-notation pattern `errors.md` already asserted, and **adds** the
first real, wire-quoted examples of each of the three shapes (bare array, unindexed nested-in-array
field, indexed nested-in-array field) that the prior pass could only describe generically. It does **not**
resolve the one specific sub-question `errors.md` flagged — exactly how a bad key *inside* a `metadata`
object is named — no example of that specific shape turned up in this pass either.

**URLs:** https://docs.stripe.com/api/errors (confirms `param`'s definition, not its naming grammar);
https://github.com/stripe/stripe-node/issues/1127 (two real error-body quotes);
https://forum.bubble.io/t/whats-wrong-with-this-stripe-api-call/117918 (two real error-body quotes);
https://forum.bubble.io/t/help-setting-up-the-stripe-api-with-bubble (indexed-array `param` mention).

**Disposition:** `errors.md`'s "partial gap" note **refined**, not fully closed — now backed by real
wire-quoted examples instead of a generic WebSearch paraphrase, but the `metadata`-key-naming
sub-question remains genuinely open. Update `errors.md`'s wording to cite the real examples; keep the
`metadata`-key sub-question flagged.

---

## 7. Is the `decline_code` table in `errors.md` complete? (42-43 captured vs. claimed 44)

**Question:** Get the authoritative decline-code list from Stripe and diff it against what's captured.

**Answer: No — this is a real, material correction.** The authoritative table at
`docs.stripe.com/declines/codes` (fetched directly, in full) has **50 card decline codes** (2 marked
`deprecated`: `do_not_try_again`, `try_again_later`), not 44 as the prior pass's secondary source
(`hideokamoto/stripe-decline-codes`) implied, and not the 43 `errors.md` actually captured. **Seven
codes are missing entirely** from `errors.md`'s table:

- `authentication_required` — "The card was declined because the transaction requires authentication
  such as 3D Secure."
- `authentication_not_handled` — "Related to `authentication_required`. You tried to proceed without
  performing the required authentication, so the issuer declined again."
- `incorrect_address` — "The address entered by the customer is incorrect."
- `invalid_expiry_month` — "The expiration month is invalid."
- `offline_pin_required` — "The card was declined because it requires a PIN."
- `online_or_offline_pin_required` — "The card was declined as it requires a PIN."
- `mobile_device_authentication_required` — "The card was declined because the transaction requires
  authentication." (listed separately from `authentication_required` on the current page, tap-to-pay
  specific)

**Full authoritative table** (verbatim descriptions, `docs.stripe.com/declines/codes`, "Card decline
codes" section — this is the complete, current, 50-row replacement for `errors.md`'s table):

| Decline code | Description |
|---|---|
| `authentication_required` | The card was declined because the transaction requires authentication such as 3D Secure. |
| `authentication_not_handled` | Related to `authentication_required`. You tried to proceed without performing the required authentication, so the issuer declined again. |
| `approve_with_id` | The payment can't be authorized. |
| `call_issuer` | The card was declined for an unknown reason. |
| `card_not_supported` | The card doesn't support this type of purchase. |
| `card_velocity_exceeded` | The customer has exceeded the balance, credit limit, or transaction amount limit available on their card. |
| `currency_not_supported` | The card doesn't support the specified currency. |
| `do_not_honor` | The card was declined for an unknown reason. |
| `do_not_try_again` (deprecated) | The card was declined for an unknown reason. |
| `duplicate_transaction` | A transaction with identical amount and credit card information was submitted very recently. |
| `expired_card` | The card has expired. |
| `fraudulent` | The payment was declined because Stripe suspects that it's fraudulent. |
| `generic_decline` | The card was declined for an unknown reason or Stripe Radar or Adaptive Acceptance blocked the payment. |
| `incorrect_address` | The address entered by the customer is incorrect. |
| `incorrect_cvc` | The CVC number is incorrect. |
| `incorrect_number` | The card number is incorrect. |
| `incorrect_pin` | The PIN entered is incorrect. This decline code only applies to payments made with a card reader. |
| `incorrect_zip` | The postal code is incorrect. |
| `insufficient_funds` | The card has insufficient funds to complete the purchase. |
| `invalid_account` | The card, or account the card is connected to, is invalid. |
| `invalid_amount` | The payment amount is invalid, or exceeds the amount that's allowed. |
| `invalid_cvc` | The CVC number is incorrect. |
| `invalid_expiry_month` | The expiration month is invalid. |
| `invalid_expiry_year` | The expiration year is invalid. |
| `invalid_number` | The card number is incorrect. |
| `invalid_pin` | The PIN entered is incorrect. |
| `issuer_not_available` | The card issuer couldn't be reached, so the payment couldn't be authorized. |
| `lost_card` | The payment was declined because the card is reported lost. |
| `merchant_blacklist` | The payment was declined because it matches a value on the Stripe user's block list. |
| `new_account_information_available` | The card, or account the card is connected to, is invalid. |
| `no_action_taken` | The card was declined for an unknown reason. |
| `not_permitted` | The payment isn't permitted. |
| `offline_pin_required` | The card was declined because it requires a PIN. |
| `online_or_offline_pin_required` | The card was declined as it requires a PIN. |
| `pickup_card` | The customer can't use this card to make this payment (it's possible it was reported lost or stolen). |
| `pin_try_exceeded` | The allowable number of PIN tries was exceeded. |
| `processing_error` | An error occurred while processing the card. |
| `reenter_transaction` | The payment couldn't be processed by the issuer for an unknown reason. |
| `restricted_card` | The customer can't use this card to make this payment (it's possible it was reported lost or stolen). |
| `revocation_of_all_authorizations` | The card was declined for an unknown reason. |
| `revocation_of_authorization` | The card was declined for an unknown reason. |
| `security_violation` | The card was declined for an unknown reason. |
| `service_not_allowed` | The card was declined for an unknown reason. |
| `stolen_card` | The payment was declined because the card is reported stolen. |
| `stop_payment_order` | The card was declined for an unknown reason. |
| `testmode_decline` | A Stripe test card number was used. |
| `transaction_not_allowed` | The card was declined for an unknown reason. |
| `try_again_later` (deprecated) | The card was declined for an unknown reason. |
| `withdrawal_count_limit_exceeded` | The customer has exceeded the balance or credit limit available on their card. |
| `mobile_device_authentication_required` | The card was declined because the transaction requires authentication. |

Note: the docs page also has a **separate, non-overlapping "Local payment method decline codes" table**
(`partner_generic_decline`, `invalid_customer_account`, `payment_limit_exceeded`, etc. — ~23 more codes)
for LPM-specific declines, which is a genuinely distinct set from the card `decline_code` values above
and was not in scope of the original 44-count question; flagging its existence since a faithful mock of
non-card payment methods would need it too, but not folding it into the "card decline codes" count above.

**URL:** https://docs.stripe.com/declines/codes

**Disposition:** **Material correction.** `errors.md`'s decline-code table underrepresents the real set
by 7 entries (43 vs. 50) and its "deprecated" status for 2 codes was not previously noted. Replace
`errors.md`'s decline-code table with the 50-row table above and update `summary.md`'s Key Findings
line (currently says "~43 values") accordingly. Also worth a follow-up note in `errors.md` about the
separate LPM decline-code table if a Seahaven mock ever needs non-card rails.

---

## Sources used this pass

- `mcp__Tavily__tavily_extract` (direct page reads, full content): `docs.stripe.com/api/idempotent_requests`,
  `docs.stripe.com/api/pagination` (list + search + auto-pagination sub-pages, single combined fetch),
  `docs.stripe.com/api/expanding_objects`, `docs.stripe.com/api/errors` (+ `/errors/handling`),
  `docs.stripe.com/api/versioning` (+ `/api/enums`), `docs.stripe.com/declines/codes`,
  `docs.stripe.com/error-codes` (skimmed, not needed for the 7 assigned items).
- `mcp__Tavily__tavily_search`: targeted queries for `Stripe-Version` invalid-value handling, response
  version headers, bad-`expand[]` error text, and `param` bracket-notation examples — each surfaced real,
  verbatim-quoted API responses/error bodies from GitHub issues and forum threads (`stripe-node#1127`,
  `stripe-python#316`, `craftcms/commerce-stripe#343`, two Bubble.io forum threads, a Salesforce Stack
  Exchange thread), used as primary evidence where Stripe's own prose docs are silent on the specific
  wire-level detail (items 4, 5(a), 6).
