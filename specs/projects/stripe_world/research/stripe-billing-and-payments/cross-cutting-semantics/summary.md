# Cross-Cutting Semantics

## Bottom Line

The behaviors every endpoint shares — idempotency, pagination, expand, metadata, errors, versioning
— live almost entirely outside `spec3.json` (which only encodes the `limit`/`starting_after`/
`ending_before`/`expand`/`metadata` *parameter shapes* and the error envelope's *field* shape, not
the behavioral rules), so a faithful Seahaven mock has to get these from prose docs and from reading
the reference client/mock source, which is what this file set does. The single most load-bearing,
non-obvious finding: **the error envelope's wire-level `type` field has only four possible values**
(`api_error`, `card_error`, `idempotency_error`, `invalid_request_error` — confirmed both by the
schema `enum` and by a real observed 429 body), and everything people casually call
"`rate_limit_error`"/"`authentication_error`"/"`permission_error`" is a **client-side convenience
built purely from HTTP status code**, never a value Stripe actually puts in `type` for the v1 API.
The second: **stripe-python auto-generates a random idempotency key for every POST** (and v2 DELETE)
that doesn't supply one, which is why the client can safely auto-retry on 409/5xx with exponential
backoff by default (`max_network_retries = 2`). Third: `stripe-mock` implements essentially none of
this — it hard-codes `has_more: false`/exactly-one-item lists, reflects the idempotency header
without checking anything, ignores `Stripe-Version` unless a strict flag is passed (and even then
only exact-matches, no real multi-version support), and only ever emits `invalid_request_error`.

## Gap Closure (2026-09-18)

A follow-up pass with working `docs.stripe.com` access (via Tavily MCP, `mcp__Tavily__tavily_extract`)
closed most of this file's WebSearch-only gaps by reading the pages directly. Full detail, verbatim
quotes and URLs: [gap-closure-2026-09-18.md](./gap-closure-2026-09-18.md). Headline results:

- **Confirmed** (exact wording now sourced directly): idempotency-key retention ("at least 24 hours
  old"), pagination's ordering-guarantee sentence ("reverse chronological order"), the 4-level expand
  depth cap and `data.` prefix mechanism, the versioning codename/major-release model.
- **Corrected** (material): the `decline_code` table is **50 rows, not ~43** — 7 real codes were
  missing (`authentication_required`, `authentication_not_handled`, `incorrect_address`,
  `invalid_expiry_month`, `offline_pin_required`, `online_or_offline_pin_required`,
  `mobile_device_authentication_required`), 2 are marked deprecated. Also corrected: search endpoints'
  `total_count` is **opt-in via `expand[]=total_count`**, not returned by default (was described as
  something search responses "do carry").
- **Resolved** (previously open, now answered): a bad/invalid `expand[]` path is a hard 400
  `invalid_request_error` ("This property cannot be expanded (`<field>`)."), never silently ignored;
  the live API **does** echo a `Stripe-Version` response header (real header dump confirmed via a
  GitHub issue).
- **Still open even after direct docs access**: `starting_after`/`ending_before` behavior on a
  genuinely deleted object id (Stripe's own pagination page doesn't address it); exact behavior on a
  malformed/unrecognized `Stripe-Version` value (Stripe's own versioning page doesn't address it
  either — this is not a research-access gap, the prose genuinely doesn't cover it); the exact
  `param` naming rule for a bad key inside a `metadata` object specifically (bracket notation for
  arrays/nested fields is now backed by real wire-quoted examples, but this one sub-case still has no
  example).

## Key Findings

- **Idempotency: only 4 wire error types, and 409 is "retry me," not "here's a semantic error."**
  Same key + different params → 400 `idempotency_error` ("Keys for idempotent requests can only be
  used with the same parameters..."). Same key concurrently in-flight → 409, code
  `idempotency_key_in_use`, and the reference client (`stripe-python`) treats any 409 as automatically
  retryable with backoff — it isn't given a distinct exception class. Errors ARE cached/replayed under
  a key once the endpoint began executing (including 5xx); validation failures and concurrent-conflict
  responses are not cached. `request.idempotency_key` is not an echo on the original synchronous
  response — it lives on the **Event** object (`event.request.idempotency_key`, populated for events
  on/after 2017-05-23), confirmed directly from `spec3.json`. The ~24h key-retention window is now
  confirmed verbatim from Stripe's own docs: "at least 24 hours old" (a floor, not a fixed TTL).
  — [idempotency.md](./idempotency.md),
  [gap-closure-2026-09-18.md](./gap-closure-2026-09-18.md#1-idempotency-key-retention-wording-and-window)
- **Pagination: two separate mechanisms; list responses have no `total_count`, and search's
  `total_count` is opt-in, not default.** List endpoints: `limit` 1–100 (default 10),
  `starting_after`/`ending_before` are mutually exclusive object-id cursors returning objects in
  **reverse chronological order** (Stripe's own exact wording, confirmed directly), `has_more` is the
  sole client stopping signal (the reference client short-circuits to a local empty page without an
  HTTP call once `has_more` is false). Search endpoints are a different animal entirely: opaque `page`
  cursor token, and `total_count` (capped "only accurate up to 10,000") is **not returned by
  default — it must be requested via `expand[]=total_count`**, corrected from an earlier reading that
  it's carried automatically. Behavior of `starting_after` on a genuinely deleted id was checked again
  directly against Stripe's own pagination page and remains genuinely undocumented — still a real gap
  worth testing against the live API. — [pagination.md](./pagination.md),
  [gap-closure-2026-09-18.md](./gap-closure-2026-09-18.md#2-pagination-ordering-guarantee-sentence)
- **Expand: schema-encoded via `x-expandableFields`, 4-level depth cap, works on create/update too,
  and a bad path is a hard error.** Every schema's expandable fields are listed in its own
  `x-expandableFields` array (not a separate global registry) — e.g. the error envelope itself expands
  `payment_intent`/`payment_method`/`setup_intent`/`source` but not `charge`. Depth cap of 4,
  documented example `data.payment_intent.customer.default_source`. List/search responses require a
  `data.` prefix to reach into items. `expand` is a real form-encoded field on POST/create bodies too,
  not just a query param. A bad/unrecognized expand path is now confirmed to be a **hard 400
  `invalid_request_error`** ("This property cannot be expanded (`<field>`)."), never silently ignored —
  resolved via real wire-quoted GitHub issue examples. — [expand.md](./expand.md),
  [gap-closure-2026-09-18.md](./gap-closure-2026-09-18.md#4-badinvalid-expand-path--error-or-silent-ignore)
- **Metadata: 50 keys / 40-char keys / 500-char values, and the delete-all sentinel is a literal empty
  string, not an empty object.** Confirmed directly from the schema: the `metadata` request field is
  `anyOf[{object of string values}, {enum: [""]}]` — posting the literal string `""` clears all keys;
  posting an empty-string *value* for one key clears just that key; omitted keys are left alone (merge
  semantics). 83 of the ~700+ schemas in `spec3.json` carry metadata; notably `balance_transaction`,
  `event`, and `file` do **not**. — [metadata.md](./metadata.md)
- **Errors: full `code` (~215 values) and `decline_code` (50 values, corrected from ~43) enumerations
  captured, plus the authoritative HTTP-status→exception-class table reverse-engineered from the
  reference client.** The status table (400→idempotency/invalid-request, 402→card_error, 401→auth,
  403→permission, 404→invalid-request, 409→generic/retryable, 429→rate-limit, 5xx→generic/retryable)
  comes straight out of `stripe-python`'s `specific_v1_api_error`, which is about as authoritative as
  it gets short of the live API itself. `code` list is sourced from `stripe-go`'s generated `error.go`.
  **`decline_code` was re-verified directly against `docs.stripe.com/declines/codes` this pass and
  corrected: the authoritative table has 50 rows (2 marked deprecated), not ~43 — 7 codes were missing**
  (`authentication_required`, `authentication_not_handled`, `incorrect_address`, `invalid_expiry_month`,
  `offline_pin_required`, `online_or_offline_pin_required`, `mobile_device_authentication_required`).
  — [errors.md](./errors.md),
  [gap-closure-2026-09-18.md](./gap-closure-2026-09-18.md#7-is-the-decline_code-table-in-errorsmd-complete-42-43-captured-vs-claimed-44)
- **Versioning: single pinned snapshot (`2026-08-26.dahlia`), "compute at head then transform down"
  model, webhook endpoints can pin their own version independent of the account default.** Confirmed
  the spec and the Python SDK agree on the current version string; the codename/major-release model
  (monthly releases share a major codename like `dahlia` until the next breaking release) is now
  confirmed directly from Stripe's own versioning-policy prose. **The live API is now confirmed to echo
  a `Stripe-Version` response header** (a real response header dump, quoted in a GitHub issue, includes
  `'stripe-version': '2020-08-27'`) — corrected from "not confirmed." What happens on a genuinely
  malformed/unrecognized `Stripe-Version` value remains undocumented even in Stripe's own prose,
  checked directly this pass. `stripe-mock` by default ignores `Stripe-Version` entirely (single-shape
  server); its opt-in `-strict-version-check` flag does an exact string match only, with no real
  transformation engine behind it. — [versioning.md](./versioning.md),
  [gap-closure-2026-09-18.md](./gap-closure-2026-09-18.md#5-unknowninvalid-stripe-version-handling-and-whether-responses-carry-a-version-header)
- **`stripe-mock` is a shape server, not a semantics server, across every axis in this subtopic** — no
  idempotency tracking (header reflection only), no real pagination (hard-coded `has_more: false`,
  exactly one item, `total_count: 1` on every list/search call regardless of parameters), only ever
  emits `invalid_request_error`, and no version transformation. This is a precise, source-confirmed
  answer (not an assumption) to "how does the closest prior art handle these concerns," useful input
  to subtopic 4's fuller stripe-mock review. Details are folded into
  [errors.md](./errors.md#what-stripe-mock-actually-does-with-all-of-this-brief-note--full-stripe-mock-review-is-subtopic-4s-job-this-is-only-the-errorversion-handling-slice-relevant-to-cross-cutting-semantics)
  rather than a separate file, per scope (subtopic 4 owns the full review).

## Details

- [idempotency.md](./idempotency.md) — full idempotency-key behavior: retention, replay (same/
  different params, concurrent), retry policy and backoff, the auto-generated-key finding, and the
  Event.request.idempotency_key echo mechanism.
- [pagination.md](./pagination.md) — list pagination vs. search pagination as two distinct
  mechanisms, full verbatim parameter/response schemas, auto-pagination client algorithm, and the
  unresolved deleted-id-as-cursor question.
- [expand.md](./expand.md) — the `x-expandableFields` mechanism, depth limit, `data.` prefix,
  create/update expand, and the unresolved bad-path-error question.
- [metadata.md](./metadata.md) — limits, the exact deletion-semantics schema, and the full
  metadata-bearing/non-bearing resource census.
- [errors.md](./errors.md) — the complete envelope schema, the 4-value `type` enum finding and its
  corroboration, the full HTTP-status→exception mapping table, the full `code` and `decline_code`
  enumerations, rate-limit numbers, and the stripe-mock behavior notes.
- [versioning.md](./versioning.md) — version string, the compute-at-head/transform-down model,
  webhook/automated-operation version rules, and stripe-mock's strict-version-check behavior.

## Open Questions / Gaps

A 2026-09-18 follow-up pass gained working `docs.stripe.com` access (via Tavily MCP) and closed most of
the doc-access gaps this section originally listed — see
[gap-closure-2026-09-18.md](./gap-closure-2026-09-18.md) for full detail. What genuinely remains open,
**even with direct docs access**, is narrower than before:

- **`starting_after`/`ending_before` on a deleted object id** — checked again directly against
  Stripe's full `docs.stripe.com/api/pagination` prose this pass; the page simply doesn't address this
  edge case. Still recommend live test-mode verification before committing to emulation semantics; see
  `pagination.md`.
- **Exact behavior on a malformed/unrecognized `Stripe-Version` header value** — checked directly
  against `docs.stripe.com/api/versioning` and targeted searches for real quoted error bodies this
  pass; the prose is silent on this specific case (it documents version-too-old-for-a-feature errors
  and missing-required-beta-header errors, but not a garbage/nonexistent version string). This is now
  confirmed to be genuinely undocumented, not just previously unreachable. See `versioning.md`.
- **`StripeInvalidRequestError` `param` naming for a bad key *inside* a `metadata` object specifically**
  — the general bracket-notation pattern for nested/array params is now backed by real wire-quoted
  examples (not just WebSearch paraphrase), but no example of the `metadata`-key sub-case turned up
  even with direct search. See `errors.md`.
- **No official soft/hard-decline or retriable/non-retriable boolean table found** — the soft/hard
  framing in `errors.md` is practitioner consensus, not a confirmed Stripe doc table. Not re-checked
  this pass (out of the assigned gap list).
- Did not go deep on subtopic-1 territory (per-resource `x-expandableFields` full tables) or
  subtopic-3/4 territory (per-decline-code test-mode card numbers, Smart Retries schedule) — flagged
  inline where relevant rather than researched, per the plan's lane boundaries.

**Closed this pass** (previously listed here, now resolved — see Gap Closure section above and
`gap-closure-2026-09-18.md` for quotes/sources): the exact idempotency-key retention wording; the exact
pagination ordering-guarantee sentence; the bad/invalid `expand[]` path error shape; the
`decline_code` table completeness (corrected to 50 rows); whether the live API echoes a
`Stripe-Version` response header (confirmed yes).

## Sources

- `research/stripe-openapi/spec3.json` — pre-fetched OpenAPI spec, API version `2026-08-26.dahlia`;
  queried directly with Python throughout for every verbatim schema/parameter excerpt in this
  subtopic's files. Authoritative for shape, not behavior.
- `research/repos/stripe-python` (`_api_requestor.py`, `_http_client.py`, `_error.py`,
  `_error_object.py`, `_list_object.py`, `_api_version.py`, `__init__.py`) — read directly; the single
  most valuable source in this subtopic for *observed behavior* (retry policy, idempotency-key
  generation, HTTP-status→exception mapping, auto-pagination algorithm).
- `research/repos/stripe-mock` (`server/server.go`, `server/generator.go`) — read directly for exactly
  how the reference mock handles (or doesn't handle) every concern in this subtopic.
- `raw.githubusercontent.com/stripe/stripe-go/master/error.go` (WebFetch) — full `code` enumeration.
- `github.com/hideokamoto/stripe-decline-codes` (WebFetch) — `decline_code` enumeration (secondary
  source mirroring docs.stripe.com/declines/codes).
- WebSearch syntheses of: `docs.stripe.com/api/idempotent_requests`, `docs.stripe.com/api/pagination`,
  `docs.stripe.com/api/expanding_objects`, `docs.stripe.com/api/errors`, `docs.stripe.com/error-codes`,
  `docs.stripe.com/declines/codes`, `docs.stripe.com/api/versioning`, `docs.stripe.com/api/metadata`,
  `docs.stripe.com/rate-limits`, `stripe.com/blog/api-versioning` — direct WebFetch blocked this
  session for all `docs.stripe.com`/`stripe.com` URLs (see Gaps); dates/versions not independently
  confirmable beyond what's embedded in the pre-fetched spec/SDK.
- Assorted GitHub issues quoting real API error strings/behavior verbatim (`stripe/stripe-ruby#503`,
  `code-corps/stripity_stripe#564`, `tipsi/tipsi-stripe#799`, `jonasbark/flutter_stripe_payment#311`,
  `stripe/stripe-node#2368`, `stripe/stripe-php#246`, `franckverrot/terraform-provider-stripe#25`) —
  used as independent corroboration for prose-doc claims per the above.
- **2026-09-18 gap-closure pass**: `mcp__Tavily__tavily_extract` direct reads of
  `docs.stripe.com/api/idempotent_requests`, `docs.stripe.com/api/pagination` (+ search + auto-pagination
  sub-pages), `docs.stripe.com/api/expanding_objects`, `docs.stripe.com/api/errors` (+ `/errors/handling`),
  `docs.stripe.com/api/versioning` (+ `/api/enums`), `docs.stripe.com/declines/codes`,
  `docs.stripe.com/error-codes` — `WebFetch` still cannot reach these domains in this environment, but
  Tavily's extract tool can. Plus `mcp__Tavily__tavily_search` for real wire-quoted evidence
  (`stripe-node#1127`, `stripe-python#316`, `craftcms/commerce-stripe#343`, two Bubble.io forum threads,
  a Salesforce Stack Exchange thread) on questions Stripe's own prose doesn't directly answer. Full
  detail: [gap-closure-2026-09-18.md](./gap-closure-2026-09-18.md).
