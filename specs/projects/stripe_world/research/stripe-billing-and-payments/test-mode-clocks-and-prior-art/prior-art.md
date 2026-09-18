# Part 3 — Prior art: `stripe-mock` in depth, and other Stripe emulators

All of §3.1–3.4 is read directly from the `stripe-mock` source at
`/home/user/stripe_world/research/repos/stripe-mock` — no web fetch involved, this is as primary as
sourcing gets. §3.5 (other emulators) is WebSearch + direct GitHub raw-file reads (GitHub's own
domains were **not** on the egress blocklist — only Stripe-owned domains were; see
`test-mode.md`'s sourcing note).

## 3.1 The project's own stated scope (verbatim, from its README)

This is worth quoting at length because it is the single most load-bearing piece of evidence for
the project's "where does prior art stop" question, and it comes from the maintainers themselves,
not an inference:

> "stripe-mock is a mock HTTP server based on the real Stripe API. It accepts the same requests and
> parameters that the Stripe API accepts, and rejects requests whose parameters are not recognized
> or have incorrect types. Its responses resemble the responses of the real Stripe API in terms of
> data type; however, **stripe-mock does not attempt to reproduce the *behavior* of the real Stripe
> API at all**. It cannot reject all invalid requests, and its responses are completely hardcoded.
> They will have a correct type, but they will not necessarily be realistic Stripe responses."

> "stripe-mock is meant for basic sanity checks. ... If you have more sophisticated testing needs,
> you shouldn't use stripe-mock. Always test changes to your Stripe integration against testmode."

Limitations, verbatim:

> - "stripe-mock is **stateless**. Data you send on a `POST` request will be validated, but it will
>   be completely ignored beyond that. It will not be reflected on the response or on any future
>   request -- unlike the real Stripe API, which stores the information you send it."
> - "For polymorphic endpoints (say one that returns either a card or a bank account), only a
>   single resource type is ever returned. There's no way to specify which one that is."
> - "It's locked to the latest version of Stripe's API and doesn't support old versions."
> - "[Testing for specific responses and errors](...) is currently not supported. It will return a
>   success response instead of the desired error response."

And on future direction — this directly answers whether statefulness is coming:

> "The scope we envision for stripe-mock has significantly narrowed since 2017... We are currently
> **not** planning to add statefulness or more sophisticated testing features to stripe-mock.
> stripe-mock will remain a tool for basic sanity checks."

**This confirms the project's working expectation outright, in the tool's own words, not just by
inference from reading the code**: stripe-mock does not model subscription lifecycles, dunning,
proration, invoice state machines, or money moving — because it doesn't model *any* behavior, full
stop, for *any* resource. It is not "billing is the gap" — it is "behavior of any kind is the gap."
§3.2–3.4 below verify this claim against the actual Go source, which fully corroborates the README.

## 3.2 OpenAPI-driven response generation — how it actually works

Source: `server/server.go`, `server/generator.go` (both read in full).

**Startup / routing (`server.go: initializeRouter`):**
- Loads `spec3.json` (embedded by default, or `-spec <path>` override) and `fixtures3.json`
  (embedded, or `-fixtures <path>` override).
- Walks every `path × verb` in the spec, compiles each OpenAPI path template
  (`/v1/charges/{charge}`) into a Go regexp (`compilePath`), and builds a `jsval` JSON-Schema
  validator for the request — from the **query-parameter list** on `GET` (there is no JSON body
  schema for reads; `BuildQuerySchema` synthesizes a pseudo-schema from `operation.Parameters`) or
  from the **request body schema** on every other verb.
- Routes are sorted so that routes with fewer path parameters win ties — e.g.
  `/v1/invoices/upcoming` is tried before `/v1/invoices/{invoice}` even though both regexes would
  match the literal string `upcoming`.
- A fixed table of "action suffixes" (`/approve`, `/capture`, `/cancel`, `/finalize`, `/pay`,
  `/refund`, `/void`, `/attach`, `/detach`, …) is used as a heuristic for "this path has a primary
  ID right before this suffix," since these RPC-style actions don't end in a `{param}`.

**Per-request handling (`server.go: HandleRequest`):**
1. Validates `Authorization` header is `Bearer`/`Basic` and the decoded key is shaped
   `{sk|rk}_test_<nonempty>` — **any** value in that shape is accepted; there is no real key
   registry (`validateAuth`, quoted in `test-mode.md` §1.5).
2. Optional `-strict-version-check`: if the client sends a `Stripe-Version` header that doesn't
   match the spec's pinned version, reject with 400 — otherwise the header is accepted but
   ignored. There is no multi-version support at all: "It's locked to the latest version of
   Stripe's API" (README) is enforced by this being the *only* version the embedded spec contains.
3. `Idempotency-Key` header, if present, is **echoed back** in the response header — and nothing
   else. No dedup, no cached-response replay, no `idempotency_key_in_use` conflict detection (the
   codebase's only two mentions of "idempot" are this echo — grepped directly, confirmed zero
   other occurrences outside `_test.go` files).
4. Routes the request; 404s with an `invalid_request_error` if nothing matches.
5. Parses query/body params (`param.ParseParams`), coerces them to the schema's expected types
   (`coercer.CoerceParams`), then validates against the `jsval` validator built at startup. Any
   failure → 400 `invalid_request_error`.
6. Extracts `expand[]` into a tree structure (`ExpansionLevel`, supporting dotted paths like
   `subscription.latest_invoice` and a `*` wildcard).
7. Calls `DataGenerator.Generate` (see below) to build the response body from the **response
   schema**, the **fixture store**, and the parsed request data.
8. Writes JSON (or pretty-printed JSON if the `User-Agent` is `curl/*`), always sets
   `Request-Id: req_123` (hardcoded, identical on every response) and `Stripe-Mock-Version`.

**Response generation (`generator.go: DataGenerator.Generate` / `generateInternal`):** this is a
recursive walk over the **response schema**, not the request:
- If the schema has `x-resourceId`, look up a **fixture** for that resource ID in the fixtures
  file and use it as the "example" to fill from.
- If the schema is a `list` or `search_result` wrapper, always synthesize exactly **one** item
  (`data: [itemData]`), `has_more: false`, `total_count: 1` — **regardless of any query params**
  (`limit`, `starting_after`, etc. are validated for type-correctness but never actually affect
  how many items come back, because there's no dataset to page over).
- If the schema has `x-expansionResources` (an expandable field), either recurse into the expanded
  full schema (if the caller asked for that expansion) or return the collapsed
  (`anyOf[0]`, i.e. typically an id-only string) form otherwise.
- For a plain object/array/scalar, recurse into `Properties`, pulling each sub-value from the
  fixture's corresponding key if present; if a key is requested via `expand` but absent from the
  fixture, or there's no fixture at all for a schema, it falls back to
  **`generateSyntheticFixture`** — a from-scratch generator that returns `""`/`0`/`false`/`[]`/
  the first `enum` value/`null` (if nullable) for whatever the schema says, walking only
  `Required` properties for object types. This is the fallback path for objects with no dedicated
  fixture (e.g. prerelease resources, or nested expansions that don't exist as top-level fixtures).
- **ID and reflection tricks that make responses look more real than they are:**
  - `maybeGeneratePrimaryID`: on a `create`-shaped request (no primary ID came from the URL path),
    generates a fresh, correctly-prefixed random ID (`randomID`) by taking the fixture's own `id`
    prefix (e.g. `ch_` from `ch_fixture123`) and appending a time-based + random suffix — so two
    consecutive `POST /v1/charges` return two different-looking `ch_...` IDs, even though every
    other field is identical, hardcoded fixture data.
  - `recordAndReplaceIDs` / `distributeReplacedIDs`: path-derived IDs (e.g. the `{charge}` in
    `GET /v1/charges/{charge}`) get substituted into the generated fixture's `id` field and into
    any nested fields that reference it (so a refund's `charge` field matches the charge ID you
    asked for in the URL, and `url` fields on nested lists get their path segment corrected too).
  - On `POST`, a separate `datareplacer` package (`generator/datareplacer/datareplacer.go`, 226
    lines) reflects **request parameters straight into the response** wherever the field name and
    type line up — this is the mechanism behind the README's claim "if a charge is created with
    `amount=123`, a charge will be returned with `"amount": 123`." It is a shallow, schema-guided
    echo, not a real create — nothing is persisted for a later `GET` to find.

**Net effect:** every response is a deep-copied, ID-patched, request-echoed version of one
**static fixture** keyed by resource type. There is exactly one canned charge, one canned
subscription, one canned invoice, etc. in the whole server, no matter what you send it or how many
times you call it.

## 3.3 The fixtures file

`embedded/openapi/fixtures3.json` — a flat JSON object:

```json
{ "resources": { "<resource-key>": <one hardcoded example object or {"deleted": true, ...}>, ... } }
```

Directly inspected: **177 resource keys**, one static example object each (plus, for many
resources, a paired `deleted_<resource>` key holding the deleted-object shape). Confirmed test-clock
entries, quoted in full:

```json
"test_helpers.test_clock": {
  "created": 1234567890,
  "deletes_after": 1234567890,
  "frozen_time": 1234567890,
  "id": "clock_1Pgc6yB7WZ01zgkWVlemIOED",
  "livemode": false,
  "name": null,
  "object": "test_helpers.test_clock",
  "status": "ready",
  "status_details": {}
}
```
```json
"deleted_test_helpers.test_clock": {
  "deleted": true,
  "id": "clock_1Pgc6yB7WZ01zgkWVlemIOED",
  "object": "test_helpers.test_clock"
}
```

**This is direct, conclusive evidence for the test-clock corner of the "where it stops" question:**
the fixture's `status` is hardcoded to `"ready"`, `frozen_time`/`created`/`deletes_after` are all
the same placeholder epoch value (`1234567890`), and — because §3.2 established that `POST`
responses only *reflect input fields that line up with matching schema properties*, and because
`test_helpers.test_clocks` is stateless like everything else — **calling `advance` against
stripe-mock has no effect whatsoever on any other object.** `POST .../advance` returns this same
static fixture (with `frozen_time` echoed from your request via `datareplacer` if you passed
`frozen_time`, since that field name/type matches), but no subscription, invoice, or dunning state
anywhere in the mock is wired to that clock, because *nothing* is wired to anything — there is no
subscription-lifecycle engine, no invoice-cycle engine, and no cross-object state at all. A caller
that creates a subscription attached to a clock and then advances the clock will see the
subscription fixture completely unchanged on the next `GET`. This confirms, with primary-source
certainty, the plan's stated expectation: **stripe-mock does not model subscription lifecycles or
money moving.**

## 3.4 What it does and doesn't do for the cross-cutting mechanics the plan asks about

(Cross-referencing subtopic 2's territory only to state what stripe-mock itself implements, per
this subtopic's mandate — not re-deriving the real API's semantics here.)

- **Pagination:** query params `limit`/`starting_after`/`ending_before` are validated for shape
  (via the `GET` pseudo-schema) but have **zero effect on the response** — every list always
  returns exactly one item and `has_more: false` (`generateListResource`, quoted structurally in
  §3.2). There is no cursor logic, no `total_count` beyond a hardcoded `1`.
- **Expand:** genuinely implemented, and one of the more complete pieces of behavior in the mock —
  `parseExpansionLevel` builds a real tree from dotted/`*`-wildcard `expand[]` values, and
  `generateInternal` recurses through `x-expansionResources` correctly, including nested
  expansions inside list `data[]` items. Requesting an expansion the schema doesn't declare
  expandable returns `errExpansionNotSupported` → mapped to a 500 in the current code path (not a
  clean 400 — worth noting as a fidelity gap if the project cares about exact error-shape parity;
  this is subtopic 2's call whether it matters).
- **Idempotency:** not implemented beyond header echo (§3.2 point 3). No key store, no replay
  detection, no conflict errors.
- **Errors:** the entire error surface is exactly two shapes:
  `invalid_request_error` (400/404, generated from a handful of hardcoded English message
  templates for auth failure, unroutable path, content-type mismatch, and schema-validation
  failure) and a generic 500 `invalid_request_error` labeled "An internal error occurred." for any
  unexpected server-side panic path. There is **no `decline_code`, no `card_error`, no
  `api_error`, no rate-limit `429`, no per-resource semantic error** (e.g. "invoice already
  paid," "can't refund more than charged") anywhere in the codebase — fully consistent with the
  README's "[t]esting for specific responses and errors is currently not supported. It will
  return a success response instead of the desired error response."
- **Validation:** real and reasonably strict at the *shape* level (JSON-Schema `jsval` validation
  against the OpenAPI request schema, plus a content-type check, plus param coercion for
  form-encoded query/body values) — this is the one area where stripe-mock does meaningfully more
  than "always succeed." It will reject wrong types, missing required fields, and unrecognized
  params. It will not reject anything that requires cross-field or business-rule knowledge (e.g.
  it cannot tell you a `proration_behavior` value is nonsensical for a plan that has no existing
  subscription, because it has no subscriptions to check against).
- **CLI surface / no hidden statefulness switch:** `main.go`'s flag set
  (`-http`, `-http-addr`, `-http-port`, `-http-unix`, `-https*`, `-port`, `-fixtures`, `-spec`,
  `-strict-version-check`, `-unix`, `-verbose`, `-version`, `-beta`) confirms there is no
  `-stateful`, no error-injection flag, no latency-simulation flag, nothing beyond swapping which
  static spec/fixtures file is loaded and which sockets to bind. The only two axes of
  configurability are "which OpenAPI version's spec+fixtures to embed" (`-beta` toggles a second
  embedded pair, `spec3.beta.sdk.json` / `fixtures3.beta.json`) and networking.

## 3.5 Other serious Stripe-emulator prior art

None of these are "official," but they're real, maintained-to-varying-degrees projects that made a
different tradeoff than stripe-mock — every one of them chose **statefulness** as the differentiator,
which is itself a signal about what the ecosystem felt stripe-mock was missing.

### `localstripe` (adrienverge/localstripe, mirrored at waglabs/localstripe)

Read directly (`raw.githubusercontent.com/adrienverge/localstripe/master/README.rst`):

- Python, runs as a **real local HTTP server** on `localhost:8420` — any language's Stripe SDK can
  point at it, same integration model as stripe-mock.
- **Stateful by design**: "objects created (customers, cards, subscriptions) persist and affect
  subsequent queries" — e.g. retrieving a customer reflects its actual current subscriptions.
- Ships JS that mocks **Stripe Elements** client-side, so the token-creation step in a browser can
  be exercised too, not just the server API — something stripe-mock has no concept of at all.
- Real **webhook delivery**: a special `/_config/webhooks` route lets you register a URL + secret;
  the server actually POSTs signed webhook events for object lifecycle changes across "Products,
  Plans, Customers, Sources, Subscriptions, and Invoices" per its own docs — i.e., it appears to
  implement at least a partial subscription/invoice lifecycle engine to have events to emit in the
  first place (this is the single biggest capability gap vs. stripe-mock: an event-driving state
  machine, however partial, actually exists here).
- Explicit non-goals: **no Stripe Connect support at all** ("only supports Stripe Payments"), only
  the latest API version "on a best-effort basis," deprecated properties may not be supported.
- Data can be wiped via `DELETE /_config/data` — i.e., state is real but resettable between test
  runs, which is the standard pattern a stateful mock needs for test isolation.

### `stripe-stateful-mock` (Giftbit, née pushplay/stripe-stateful-mock)

Read directly (GitHub raw README):

- Explicitly self-described as a **"half-baked, stateful Stripe mock server"** — the repo's own
  tagline undercuts any claim of completeness, which is itself useful signal: even the most
  state-aware alternative doesn't claim fidelity, it claims speed ("50-100x faster... than testing
  against the official server").
- Scope: charges (create/retrieve/list/update/capture), refunds, customers (+ card management),
  products, plans, prices, **subscriptions (create/retrieve/list only — no update/cancel found in
  the README's own feature list)**, tax plans, and minimal Connect-account create/delete.
- Notably claims **idempotency-key support**, which stripe-mock explicitly does not implement — a
  concrete point of divergent prior art on subtopic 2's cross-cutting-semantics territory, worth a
  pointer even though it's out of this subtopic's lane.
- Ships deliberately silly test-token extensions for edge-case testing: `tok_429` (forces a
  rate-limit response), `tok_500` (forces a server error), `tok_forget` (processes normally but
  doesn't persist), and pipe-chained tokens to script a sequence of different responses across
  repeated calls to the same endpoint — a pattern worth considering for a Seahaven world's own
  test-affordances, if it wants scriptable failure injection beyond what real Stripe's magic cards
  give you.
- Explicit disclaimer: "Correctness of this test server is not guaranteed!" and recommends
  periodically re-validating against the real Stripe test server — same "don't trust me fully"
  posture as stripe-mock's own README, just for a different reason (breadth of coverage vs. depth
  of behavioral accuracy).
- Pinned to a single old API version (`2020-08-27`) — same "locked to one version" limitation as
  stripe-mock, just a much older pin, presumably because it's less actively maintained.

### `mock-stripe` (prasanthkv/mock-stripe)

Lighter-weight, Go-based, MIT-licensed, stateful "for a few hours" (session-scoped, not
persistent-to-disk like localstripe) — positioned as a closer sibling to stripe-mock (same
sample-data-generation framing) but with a lightweight in-memory statefulness layer added on top. I
was not able to get a full feature/endpoint enumeration beyond this from search synthesis (repo's
raw README fetch 404'd on the branch names I tried) — flagging as a lead worth a closer look if the
project wants a second stateful reference implementation, but not verified in depth here.

### `stripe-ruby-mock` (stripe-ruby-mock/stripe-ruby-mock, née pearkes/stripe-ruby-mock)

A structurally different category, surfaced by search but not deeply investigated (out of scope —
it isn't an HTTP server at all): it's a Ruby gem that **monkey-patches the `stripe-ruby` SDK
in-process** to intercept calls during RSpec tests, rather than standing up a server other
languages/SDKs could hit. Worth naming only because it demonstrates a third architectural option
beyond "static OpenAPI mock" and "stateful HTTP server" — an **SDK-level interception mock**,
which is a non-option for a Seahaven world (which needs to be a real server other tools can hit)
but useful context for why HTTP-level statefulness is the pattern every *server-shaped* alternative
converged on.

### What this prior art teaches, synthesized

1. **stripe-mock's choice (static, stateless, OpenAPI-driven) is deliberate and durable** — the
   maintainers explicitly ruled out statefulness for their project, twice (README's "Future plans"
   section), so the ecosystem gap it leaves is not an oversight, it's a boundary they intentionally
   won't cross. Anyone wanting a faithful subscription/invoice lifecycle has had to go elsewhere
   for years.
2. **Every serious alternative that tried to be more faithful picked the same lever: statefulness
   plus a (partial) lifecycle/event engine**, not "more magic values" or "more error codes." The
   two most complete ones (localstripe, stripe-stateful-mock) both explicitly implement
   subscriptions with real create/update flow and, in localstripe's case, actual webhook delivery
   driven by that state.
3. **None of them claim behavioral fidelity as a selling point** — every one of them ships a
   disclaimer ("half-baked," "correctness not guaranteed," "no Connect support," "best-effort
   latest-version-only"). The pattern across the whole prior-art landscape is: pick a bounded
   subset of the API, make *that* subset stateful and roughly correct, and be honest that
   everything outside the subset is unmodeled or wrong. That is directly relevant precedent for how
   a Seahaven Stripe world should scope itself and what it should say in its own README about what
   it does and doesn't model.
4. **Nobody in this landscape reproduces test clocks' actual time-advance semantics** — none of the
   READMEs surfaced by search mention `test_helpers.test_clocks` support at all. If Seahaven wants
   determinstic-time subscription testing to actually work against a mock (rather than only against
   real Stripe test mode), that appears to be **unclaimed territory** among the prior art found
   here — a genuine gap, not just an unexplored one, as best I can tell from this search.

## Sources

- `/home/user/stripe_world/research/repos/stripe-mock/README.md` — read in full; primary source for §3.1
- `/home/user/stripe_world/research/repos/stripe-mock/server/server.go` — read in full; primary source for §3.2, §3.4
- `/home/user/stripe_world/research/repos/stripe-mock/server/generator.go` — read in full; primary source for §3.2
- `/home/user/stripe_world/research/repos/stripe-mock/embedded/openapi/fixtures3.json` — queried directly (177 resource keys enumerated, test-clock entries quoted in full); primary source for §3.3
- `/home/user/stripe_world/research/repos/stripe-mock/main.go` — grepped for CLI flags; primary source for §3.4's CLI-surface claim
- `/home/user/stripe_world/research/repos/stripe-mock/generator/datareplacer/` — file presence and size confirmed (226 lines), referenced in §3.2; not read line-by-line
- [adrienverge/localstripe](https://github.com/adrienverge/localstripe) — README read directly via `raw.githubusercontent.com`; primary source for §3.5 localstripe
- [Giftbit/stripe-stateful-mock](https://github.com/Giftbit/stripe-stateful-mock) — README read directly via `raw.githubusercontent.com`; primary source for §3.5 stripe-stateful-mock
- [prasanthkv/mock-stripe](https://github.com/prasanthkv/mock-stripe) — WebSearch synthesis only, direct README fetch failed (404 on attempted branch); light-depth source for §3.5
- [pearkes/stripe-ruby-mock / stripe-ruby-mock/stripe-ruby-mock](https://github.com/stripe-ruby-mock/stripe-ruby-mock) — WebSearch surface-level only, not investigated in depth
