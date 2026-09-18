---
status: draft
---

# Functional Spec: StripeAPI — a Seahaven world

## 1. What this is

**StripeAPI** is a Seahaven world: a faithful, stateful, forkable replica of Stripe's Billing and
Payments core. An agent inside it makes Stripe API calls against SQLite-backed state that responds
the way Stripe does — objects with real ids, money that moves through a ledger, subscriptions with
real statuses, invoices that finalize and get paid — without touching Stripe.

Its purpose is rollouts: thousands of them, in parallel, each forked from a known fixture in
milliseconds, each with every changed row recoverable afterwards. Evals grade on final state, not on
transcripts, which is what makes a double-charge or a misapplied refund visible at all.

- **World name / package:** `stripeapi` (displayed as *StripeAPI*), matching Seahaven's convention of
  a lowercase single-token package, as `projecttracker` does. The package is deliberately not named
  `stripe`, which would shadow the real `stripe` PyPI distribution in any environment that installs
  both.
- **Pinned API version:** `2026-08-26.dahlia` — the `info.version` of the `spec3.json` snapshot in
  `research/` (see `research/MANIFEST.md`). Every object shape, every fixture and every conformance
  cassette is that version. There is no version negotiation.
- **Not affiliated with Stripe.** The README carries an explicit non-affiliation line. Stripe's field
  names, enum values, error codes and id prefixes are reused as functional API vocabulary under the
  MIT licence the source material carries (see
  [`agent-surfaces-and-licensing`](research/stripe-billing-and-payments/agent-surfaces-and-licensing/summary.md)).

All non-obvious behavior below is sourced from the research phase. The cross-subtopic summary is
[`research/stripe-billing-and-payments/summary.md`](research/stripe-billing-and-payments/summary.md);
per-behavior citations point into its lane documents. Claims sourced from web search rather than a
primary document are labelled there, and that labelling is load-bearing — see §12.

## 2. The agent surface: one tool

The world exposes **exactly one tool**. Everything an agent can do, it does through:

```python
call_stripe(
    method: str,                      # "GET" | "POST" | "DELETE"
    path: str,                        # "/v1/customers", "/v1/charges/ch_.../refund", with query string
    body: dict | str | None = None,   # JSON object, or a raw form-encoded string
    idempotency_key: str | None = None,
    stripe_version: str | None = None,
) -> dict                             # {"status": int, "body": {...}}
```

### 2.1 Why one tool

Stripe is the best-known REST API in the training corpus. An agent already knows that customers live
at `/v1/customers` and that you refund a charge by posting to `/v1/refunds`. A one-tool-per-operation
surface would replace that pretrained knowledge with a bespoke tool list the agent has to be taught,
and would make the world's realism depend on our naming rather than Stripe's.

It is also what Stripe itself ships. The **live** Stripe MCP server exposes 11 tools, four of which
are generic — `stripe_api_search`, `stripe_api_details`, `stripe_api_read`, `stripe_api_write`. An
earlier reading of a vendored DXT manifest suggested 23 curated one-tool-per-operation tools with no
generic escape hatch; that manifest was stale, and the correction is recorded in
[`agent-surfaces-and-licensing/gap-closure-2026-09-18.md`](research/stripe-billing-and-payments/agent-surfaces-and-licensing/gap-closure-2026-09-18.md).
A single generic entry point is therefore closer to the surface production agents are given today,
not a departure from it.

The practical consequence is that endpoint *discovery* is not a tool-schema problem. The tool's
docstring carries a compact index of every routable path (§3), and the world answers an unrecognised
path exactly as Stripe does — a 404 with `invalid_request_error` / `resource_missing` — so an agent
that guesses wrong gets a realistic correction rather than a framework error.

### 2.2 Parameters

**`method`** — `GET`, `POST` or `DELETE`. Stripe v1 uses no others. Any other verb is a `400`
`invalid_request_error`.

**`path`** — the request path, with an optional query string. `GET` list filters and `expand[]` may be
supplied in the query string, in `body`, or both; Stripe accepts both and so do we. The leading `/v1`
is required; a path without it is a 404, as on the real API.

**`body`** — accepted in **either** of two forms, detected by type:

| Form | Example | Notes |
|---|---|---|
| JSON object | `{"amount": 2000, "currency": "usd", "metadata": {"order": "6735"}}` | Encoded internally to Stripe's form syntax before dispatch, including `metadata[order]`, `expand[]`, `items[0][price]` |
| Raw form string | `"amount=2000&currency=usd&metadata[order]=6735"` | Parsed with Stripe's bracket rules, exactly as the wire carries it |

Both are first-class. The JSON form is the ergonomic path; the raw form is the faithful one and lets a
scenario test whether an agent can construct a real Stripe request body. Encoding and decoding go
through one shared implementation so the two forms cannot drift apart, and a malformed raw body
produces the same `invalid_request_error` the real API would. Which form a call used is recorded in
the change log so evals and cassette diffs can tell them apart.

**`idempotency_key`** — the `Idempotency-Key` header, promoted to a named parameter so it is visible
in the tool schema. Idempotency is a headline eval, and an agent that cannot see the parameter cannot
be graded on using it. Semantics in §6.1.

**`stripe_version`** — the `Stripe-Version` header. Accepted for fidelity and echoed back, but the
world serves exactly one version; §6.5 defines what happens when a different one is sent.

No generic `headers` argument. The two headers that carry behavior are named; the rest
(`Authorization`, `Content-Type`, `User-Agent`) carry none here, and a generic dict would invite
agents to set headers that silently do nothing.

### 2.3 Return value

```python
{"status": 402, "body": {"error": {"type": "card_error", "code": "card_declined", ...}}}
```

The HTTP status is returned alongside the body rather than raised, because Stripe's status codes
carry meaning an agent is supposed to react to — `402` card decline is a different situation from
`400` bad request, and both are ordinary outcomes an agent must handle rather than exceptions. Errors
come back in Stripe's error envelope (§6.4) with the matching status.

Seahaven's declared-error mechanism is still used, for the class of failures that are *not* Stripe
responses: a malformed `method`, an unparseable body, a call that violates the tool's own contract.
Those are framework-level authoring errors and should look like framework errors, not like Stripe
outages. The error-handler middleware maps everything else to the Stripe envelope.

## 3. Scope: the routing table

The world routes **148 operations**. That number is exactly `spec3.json`'s 187 operations under the
resource roots in `project_overview.md` §4, minus the 39 legacy, sub-resource and `search` operations
enumerated in
[`minimum-closed-set-and-tool-budget.md`](research/stripe-billing-and-payments/api-surface-and-object-graph/minimum-closed-set-and-tool-budget.md).

### 3.1 Routed resources

`customers`, `customer_balance_transactions`, `payment_methods`, `products`, `prices`, `coupons`,
`promotion_codes`, `tax_rates`, `payment_intents`, `charges`, `refunds`, `disputes`, `setup_intents`,
`balance`, `balance_transactions`, `payouts`, `subscriptions`, `subscription_items`,
`subscription_schedules`, `invoices`, `invoiceitems`, `credit_notes`, `events`.

### 3.2 Explicitly not routed

Everything the overview's §4 "Out" list names, plus four rulings the research surfaced because the
overview's scope statement did not cover them:

| Not routed | Why |
|---|---|
| Legacy Sources / Cards / Bank Accounts sub-resources | Functionally superseded by `payment_methods`, which is in scope. Routing both would mean two parallel payment-method representations. Largest cut available that touches no fidelity-bearing object. |
| `customer_cash_balance_transactions`, `cash_balance`, `funding_instructions` | A separate Stripe product. Named in neither the overview's In nor Out list; ruled out. |
| `tax_ids` | Adjacent to Stripe Tax, which is out. |
| `products/features` | Entitlements, not billing core. |
| All four `search` endpoints, `balance/history` | Query-language surface, not billing behavior. |
| `test_helpers/*`, including test clocks | See §5. |

`customer_balance_transactions` is an **addition** to the overview's literal resource list:
`invoice.starting_balance` and `invoice.ending_balance` have no meaning without the ledger behind
`customer.balance`, and "apply a credit note to the customer's balance" has nowhere to write.

`subscription_schedules` is **in, scoped down** — routed, with a `phases` array modelling the fields
the lifecycle needs rather than full parity with the subscription schema. It is the only way to
express "declare a future change now", which `subscriptions.update` cannot express at all.

### 3.3 Objects, and how they are stored

Roughly 17–19 tables. Four objects in the closed set are deliberately *not* tables:

- **`balance`** — no id, not listable, derivable from `balance_transaction` rows. A computed read.
- **`line_item`**, **`credit_note_line_item`** — written once by the transaction that writes their
  parent and then frozen. Stored as JSON on the parent, which is also how the API already nests them.
- **`discount`** — always embedded in an owner, never independently created.

`spec3.json` models these as nested rather than flat, so storing them nested follows the source of
truth rather than fighting it.

## 4. Fidelity rules for every object

These apply to every routed resource and are the substance of "faithful".

- **Ids are Stripe-shaped and deterministic.** `cus_`, `pi_`, `ch_`, `in_`, `sub_`, `re_`, `evt_` …
  with the right prefix and a plausible-looking suffix, generated from `ctx.ids` so the same fixture
  and seed produce the same ids. No wall-clock, no `uuid4()`, no autoincrement leaking into an id.
- **Every timestamp comes from `ctx.clock`.** Unix seconds, as Stripe returns them.
- **Money is integer minor units**, matching Stripe's own convention and Seahaven's integer columns.
  No floats anywhere in the money path.
- **`object` discriminator on every object**, and `livemode: false` everywhere — this is a test-mode
  replica and says so.
- **Nullability and enums follow `spec3.json`.** Including the trap the research found: `dispute.reason`,
  `payout.status`/`method`/`source_type`, `refund.status`, `balance_transaction.status` and
  `setup_intent.usage` are typed as bare `string` in the spec but have closed value sets recoverable
  only from the description prose. The conformance harness must check those against the extracted
  sets, not against the schema's (absent) `enum`.
- **Schema drift is respected, not "corrected".** At `2026-08-26.dahlia` there is no top-level
  `invoice.subscription` (it is `invoice.parent.subscription_details.subscription`) and no top-level
  `invoice.days_until_due`. The widely-documented older shape is wrong for this version. Anything
  specced from memory here will be wrong; the schema is the authority.
- **References that cross the scope boundary** are modelled, stubbed as an id-only reference, or
  nulled, per the per-edge rulings in
  [`scope-boundary-edges.md`](research/stripe-billing-and-payments/api-surface-and-object-graph/scope-boundary-edges.md).
  Two cannot simply be nulled: `invoice.issuer` is required and Connect-shaped (modelled as
  `{"type": "self"}`), and `automatic_tax` is required on subscriptions and invoices (modelled as
  `{"enabled": false}`).
- **`payment_method_details`** is the widest edge in the schema — 57–61 mutually exclusive rail
  sub-objects. `card` and `us_bank_account` are modelled in full; the rest are stubbed as
  `{"type": "<rail>"}` and promoted only when an eval needs one.
- **`metadata`** on every object that carries it in the spec, with Stripe's key/value limits and
  deletion semantics.

## 5. Time: one instant

**The world holds a single frozen `now`. There are no test clocks and time does not advance.**

Seahaven has no clock-advance mechanism, and "exactly one `now` per instance" is structural — it
appears in the fixture sidecar, the state-document envelope and the composition model, not as a
convention but as an invariant. Building a Stripe-shaped `test_clock` would require a new framework
primitive. That is out of scope for this project and is logged as a Missing-capability finding
(`SEAHAVEN_FINDINGS.md`, Entry 3) rather than worked around.

What this means concretely:

- `POST /v1/test_helpers/test_clocks` and every other `test_helpers` path is **not routed** and returns
  the standard 404. The `AGENTS.md` and README state the one-instant model plainly so no one mistakes
  it for a bug.
- **Time-dependent *computation* still works and is where the interest lives.** Proration on a
  mid-cycle upgrade is computed from period boundaries relative to the frozen `now` and is fully
  exercised. So are: finalizing a draft invoice, paying an open one, refunding, retrying a failed
  payment, cancelling at period end, applying a coupon, issuing a credit note.
- **Time-dependent *progression* is baked into fixtures instead.** The generator simulates months of
  virtual history and freezes at `now`, so a fixture contains subscriptions in every status, invoices
  at several dunning attempt counts, disputes at several stages, and a populated balance ledger. The
  richness that would have come from advancing a clock comes from the starting state.
- **What is genuinely lost:** watching a renewal happen, or a dunning schedule play out, inside a
  single rollout. Nothing auto-advances; no background job fires. An eval that needs "and then two
  weeks pass" is not expressible in this world today.

## 6. Cross-cutting behavior

Non-negotiable for fidelity, and mostly not in the OpenAPI schema. Details and citations in
[`cross-cutting-semantics`](research/stripe-billing-and-payments/cross-cutting-semantics/summary.md).

### 6.1 Idempotency

- A key is scoped to the endpoint and stored with the response it produced.
- A replay with the **same key and same parameters** returns the stored response — the same object,
  the same id, no second write. This is the headline eval: a retried charge must not double-charge.
- A replay with the **same key and different parameters** is an `idempotency_error`.
- A replay while the first request is still in flight is `idempotency_key_in_use`. In a synchronous
  single-threaded world this is unreachable in normal operation but is modelled so the error exists.
- Errors are cached the way Stripe caches them.
- `request.idempotency_key` is echoed on responses.
- Retention on the real API is a **floor, not a fixed TTL** — the documented wording is that keys are
  evicted once "at least 24 hours old". With one frozen instant, keys never expire within a rollout;
  the retention window is recorded in the conformance allow-list as a declared difference.

### 6.2 Pagination

Cursor pagination on every list: `limit`, `starting_after`, `ending_before`, `has_more`, and the
`{"object": "list", "data": [...], "url": ...}` envelope. `limit` defaults to 10 and ranges 1–100.
Lists **return objects in reverse chronological order**, and `starting_after` / `ending_before` are
mutually exclusive — supplying both is an error.

`total_count` is **not** returned by default; it is opt-in via `expand[]=total_count` on the search
endpoints, which are not routed here in any case (§3.2).

`starting_after` / `ending_before` against the id of a **deleted** object remains unspecified — and
now known to be a genuine silence in Stripe's documentation rather than a gap in our reading. It is a
conformance scenario (§12), and until a recorded trace settles it the world's behavior is stated in
the allow-list rather than assumed correct.

### 6.3 Expansion

`expand[]` on retrieve, list, create and update, with Stripe's depth limit, the `data.` prefix for
list responses, and the spec's `x-expandableFields` as the authority on what is expandable. An
expandable reference is stored as an id and inflated on read; unexpanded, it serialises as the bare
id string.

A bad or non-expandable path is a **hard `400` `invalid_request_error`, never silently ignored** —
`"This property cannot be expanded (<field>)."` for a non-expandable field, or
`"...because it doesn't exist: <field>."` for one that is not there at all.

### 6.4 The error envelope

```json
{"error": {"type": "...", "code": "...", "decline_code": "...", "param": "...", "message": "...", "doc_url": "..."}}
```

The wire-level `type` has **exactly four values**: `api_error`, `card_error`, `idempotency_error`,
`invalid_request_error`. `rate_limit_error`, `authentication_error` and `permission_error` are
client-side conveniences derived from the HTTP status and are never emitted on the wire — a detail
model recall reliably gets wrong, and one the conformance tests will catch.

The full `code` (~215 values) and `decline_code` (**50 values**, two of them deprecated) enumerations
extracted during research are the source for what this world may emit. The first research pass
captured only ~43 decline codes and was missing seven, including `authentication_required` and three
PIN/address variants; the corrected table is authoritative. `param` names the offending parameter,
using bracket notation for nested and array parameters.

### 6.5 Versioning

One version is served: `2026-08-26.dahlia`. The real API does return a `stripe-version` response
header, so this world does too. A request carrying a *different* `stripe_version` is answered in the
served version with the mismatch recorded in the change log — the world does not transform shapes
across versions and does not pretend to. An eval that cares can assert on the record.

Stripe's codenames cover a run of monthly backward-compatible releases until the next breaking major
release, so `dahlia` names a series rather than a single day's shape. Handling of a *malformed*
version string is undocumented even in Stripe's own prose; ours is declared in the allow-list.

### 6.6 Events

Every state change that Stripe raises an event for writes an `event` row: `evt_`-prefixed, with
`type`, `data.object` holding the object as of the change, and `created`. Events are listable and
retrievable at `/v1/events`.

**No delivery.** Events are queryable state; there is no HTTP webhook dispatch, no signing, no retry
queue. Delivery is the harness's problem.

The closed `event.type` list is not derivable from `spec3.json` (`event.type` is a bare string by
design). The authoritative set is the committed
[`event-types-closed-set.txt`](research/stripe-billing-and-payments/api-surface-and-object-graph/event-types-closed-set.txt)
— 266 entries, the union of Stripe's published list and `stripe-python`'s generated enum. Only the
subset belonging to routed resources is ever emitted; the file is the validation set.

## 7. Billing and payments behavior

The parts that live in prose and observed behavior rather than the schema. Full detail and
per-behavior citations in
[`billing-and-money-behavior`](research/stripe-billing-and-payments/billing-and-money-behavior/summary.md).

- **Subscription status machine** — all eight statuses and every transition, including
  `payment_behavior`, incomplete expiry, trials, `cancel_at_period_end` versus immediate cancel, and
  the two distinct pause mechanisms.
- **Invoice status machine** — `draft` → `open` → `paid` / `void` / `uncollectible`, with
  `auto_advance`, `collection_method`, the full `billing_reason` enum, and
  `invoice.automatically_finalizes_at` as the explicit, queryable draft window. Because time does not
  advance, automatic finalization does not fire on its own; the field is populated and honest, and
  finalization happens when called. A `subscription_cycle` invoice is created **exactly at the period
  boundary with zero lead time** — the ~1-hour draft window follows that creation, it does not
  precede it.
- **Proration** — `credit = -fraction × old_price`, `debit = +fraction × new_price`, the fraction
  computed to the second, producing the familiar two `unused_time` / `remaining_time` lines.
  **Credit and debit lines each round to the nearest cent independently, and the rounded lines are
  then summed** — the total is not computed at higher precision and rounded once. This is settled by
  a worked example in Stripe's own documentation whose arithmetic only reconciles under
  independent-per-line rounding (a 20/3 credit rounding to −667 and a 10/3 debit to +333, totalling
  −334; net-then-round would give −333). It was previously an assumption by analogy and is now a
  documented fact.

  One narrow assumption remains: the tie-break at an exact half-cent (`x.xx5`) is genuinely
  undocumented, so round-half-up stands there. It lives behind a single named function with the
  assumption stated at the call site. The documented example is `billing_mode=classic`; whether
  `flexible` rounds identically is unproven, because its analogous example nets to zero. Both go to
  conformance (§12).
- **Dunning** — Smart Retries is ML-scheduled; there is no retry-day table to transcribe, and building
  one would be inventing behavior. The world models the configuration envelope (N attempts within a
  window) and the three end-of-schedule outcomes. `invoice.attempt_count` increments only on automatic
  retries and keeps incrementing even when a non-retryable decline blocks the network attempt. Nine
  hard-decline codes gate further retries, the ninth being `transaction_not_allowed`.

  At the end of the schedule, the `unpaid` outcome leaves invoices **`draft`**. Two Stripe
  documentation pages say `draft`; `spec3.json`'s own prose says "closed", which was never a real
  `invoice.status` value. The docs win, and the discrepancy is a declared difference. A subscription
  in `unpaid` **auto-recovers to `active` when its most recent invoice is paid** — no explicit
  subscription update is required.
- **Refunds** — partial refunds, over-refund rejection, refunds against disputed charges, and correct
  `refunded` / `amount_refunded` bookkeeping.
- **Disputes** — status machine, evidence submission, and the ledger effects of funds withdrawn and
  returned. Both the dispute withdrawal and its reversal are `balance_transaction.type = adjustment`;
  the enum has no dispute-specific value, and this is confirmed rather than inferred. There are **two
  distinct fees**: the "dispute received" fee is never refunded, while a separate "dispute countered"
  fee — charged only if the merchant contests — is refunded on a win. `dispute.status = prevented`
  means a dispute stopped before becoming a formal chargeback.
- **Balance ledger** — a `balance_transaction` for every money movement, the 51-value `type` enum,
  `net = amount - fee`, `available` versus `pending` with `available_on`, and payouts drawing down
  the available balance.
- **Credit notes** — the three-channel settlement model (refund, customer balance, out-of-band).

## 8. Failure injection: magic cards

Test-mode magic card numbers are the failure-injection mechanism, per the overview's §4. A payment
method created from a magic number carries the behavior that number implies, and charging it produces
the corresponding decline, dispute or 3DS outcome with the right `code` and `decline_code`.

The table is now **fixture-ready**, read from Stripe's own testing documentation:
[`magic-card-table.md`](research/stripe-billing-and-payments/test-mode-clocks-and-prior-art/magic-card-table.md).
It covers the decline cards with their `code` and `decline_code`, the 3DS cards, the dispute cards,
refund-failure and payout-failure values, IBAN/SEPA values, and the three dispute-evidence strings
(`winning_evidence`, `losing_evidence`, `escalate_inquiry_evidence`).

One correction worth stating because it would have been baked into a fixture: **`4000 0000 0000 0069`
is `expired_card`, not `stolen_card`** — `stolen_card` is `4000 0000 0000 9979`. The first research
pass flagged these as conflicting; the official table settles it.

A tail of niche rows remains unresolved — mobile 3DS challenge flows, captcha/PIN cards, most Radar
sub-variants, and the full by-country list. None is needed for the eval set, and each is added only
when something needs it.

## 9. Fixtures

Three, all synthetic, all built by a committed `fixtures_src/generate.py` and frozen with
`seahaven fixture`:

| Fixture | Shape |
|---|---|
| `empty` | Schema only, no rows. The composition and unit-test baseline. |
| `small` | A new SaaS account: tens of customers, a couple of products, a few plans, a short history. Readable end to end by a human. |
| `large` | A scaled account: thousands of customers, real churn, failed payments sitting in dunning, disputes at several stages, a mix of plans, a populated balance ledger and payout history. |

Generation simulates virtual history and freezes at the fixture's `now` (§5), so time-dependent state
is real rather than hand-placed. Fork cost is a hard requirement and is already measured on this
framework at 4–8 ms for 20K rows and 11–29 ms for 120K rows, so the overview's §11 constraint holds
with headroom.

Fixtures must make sense **standalone and as part of a composed world** — a host adding this as its
billing subsystem should find a coherent account, not a fragment.

## 10. Composition

This world is designed to be `add_world`-ed from day one:

- Tool prefixing is the host's to apply; nothing here assumes an unprefixed name. Because prefixing
  renames tools without rewriting descriptions, the tool docstring must not refer to the tool by a
  bare name — doing so trips lint `SH206` under any prefixing host.
- No assumptions about the host's schema, clock or ids beyond what Seahaven guarantees.
- Sharing one payments account across composed worlds is Seahaven's default behavior, which is
  exactly the intended use.

## 11. Testing

Testing is a deliverable, not a chore. Seahaven's pytest plugin and its `world` / `instance` fixtures
throughout, with ProjectTracker's layout as the model: a test module per tool module, plus tests for
determinism, pagination, declared errors, fixtures and the error handler.

Three kinds ProjectTracker does not have:

1. **Schema conformance, generated from `spec3.json`.** Every object the world returns validates
   against its schema — field names, types, enum values, `object` discriminator, nullability — plus
   the six bare-`string`-but-actually-enum fields from §4. Built early, because it shapes everything
   after it. The committed `spec3.json` snapshot is a **permanent pin**: `stripe/openapi` publishes
   one version at a time and does not archive, so there is no retrieving this version later.
2. **Behavioral conformance against the real API** (§12).
3. **Invariant tests** — the balance ledger sums correctly; a charge is never refunded beyond its
   amount; an idempotent retry produces exactly one object; an invoice's lines sum to its total; a
   subscription's periods never overlap or gap. These are the same queries an eval reward function
   runs, so they are dual-use.

Determinism is by construction and is itself tested: same fixture plus same seed produces byte-identical
ids, timestamps and change logs.

## 12. Conformance against the real Stripe API

Record against real test mode, replay in CI, diff against a declared allow-list.

- **CI never needs a key and never makes a network call.** Only re-recording does.
- **Cassettes are committed, redacted** — no keys, no real emails, no account identifiers.
- **Every permitted difference is declared once, with a reason** — ids, timestamps,
  `request_log_url`, livemode flags, idempotency-key retention, the served-version echo. Anything
  undeclared fails. The allow-list is the precise statement of how faithful this world actually is.
- **`Stripe-Version: 2026-08-26.dahlia` on every recorded request.**
- **One command re-records everything**, plus a manually-triggered drift job.

**Environment constraint, known now rather than discovered later: recording cannot run in the
current session's environment.** Its egress policy blocks `api.stripe.com` outright, so no API key
will make recording work here. Replay-only CI is unaffected. Recording and every re-record need a host
with egress to `api.stripe.com`.

**Scenarios to record**, in priority order. The first three exist to settle rules the implementation
currently assumes; the rest are fidelity coverage.

1. **Proration half-cent tie-break** — a mid-cycle change whose line lands on an exact `x.xx5`, which
   is the one part of proration rounding still undocumented (§7). Also record the same change under
   `billing_mode=flexible`, which was never separately proven.
2. **`starting_after` / `ending_before` against a deleted object id** — a genuine documentation
   silence, not a gap in our reading.
3. **A malformed `Stripe-Version`** — also undocumented.
4. Create a customer and charge them; a declined card; an idempotent retry of a create.
5. A subscription created, upgraded mid-cycle, cancelled; and a subscription recovering from `unpaid`.
6. An invoice finalized and paid; a partial refund, then an over-refund attempt.
7. A dispute through to resolution, to confirm both fee behaviors and the `adjustment` ledger rows.
8. Pagination past a page boundary; an `expand` two levels deep; a bad `expand[]` path.
9. Deliberate 400s to pin the error envelope, including a nested `param` name.

## 13. Evals

Ten to twenty committed eval tasks, each with a natural-language task, a starting fixture, and a SQL
reward function that reads final state. Because grading is on state rather than tool calls, the
single-tool surface costs nothing here.

The headline is **double-charge on retry**: the agent is asked to complete a payment, the first
attempt appears to fail ambiguously, and the grader asserts exactly one charge exists against the
customer. It is invisible in a transcript and unmissable in the change log — which is the whole
argument for the framework.

Others draw from: refunding the wrong charge; over-refunding; a mid-cycle plan change whose proration
must be correct; rescuing a subscription in dunning; applying a credit note to the right settlement
channel; reconciling a balance discrepancy; and a pagination task where the answer is only correct if
the agent walked past the first page.

## 14. Out of scope

Everything the overview's §10 lists, plus:

- Test clocks and any advancing of time (§5).
- Webhook delivery over HTTP. Events are queryable state only (§6.6).
- Multi-version response shapes (§6.5).
- The endpoints in §3.2.
- Being a drop-in replacement for `stripe-mock`.

## 15. Known gaps carried into implementation

Stated rather than papered over. Each has a defined way to close it.

Most of the original list closed on 2026-09-18, when a Tavily MCP server made `docs.stripe.com`
reachable and a targeted pass re-read every previously-blocked page. What survives:

| Gap | Status | How it closes |
|---|---|---|
| Proration half-cent tie-break | Round-half-up assumed; the independent-per-line rule is now documented fact | Conformance scenario 1 |
| Proration under `billing_mode=flexible` | Unproven — the documented example nets to zero | Conformance scenario 1 |
| `starting_after` / `ending_before` on a deleted id | Genuine documentation silence | Conformance scenario 2 |
| Malformed `Stripe-Version` handling | Undocumented | Conformance scenario 3 |
| Quantity-only reproration | Mechanism confirmed; no isolated worked example found | Conformance scenario 5 |
| Dunning `unpaid` invoice outcome | Implemented as `draft`; `spec3.json` prose says "closed", which was never a real status value | Declared difference |
| Niche magic-card rows (mobile 3DS, captcha/PIN, Radar sub-variants, by-country) | Not needed by the eval set | Added when something needs one |
| Whether every dispute-prevention path creates a Dispute object | Open | Conformance scenario 7 |
| Tool-count / schema-size literature | Still web-search-only | Low priority; no decision now rests on it |

Closed since the first pass, and no longer assumptions: the proration independent-rounding rule, the
magic-card table including the `4000…0069` conflict, the `decline_code` set (50, not 43), the
`expand[]` error shape, the `stripe-version` response header, the `event.type` closed set, the
`unpaid` → `active` recovery path, the dispute-withdrawal `balance_transaction.type`, dispute fee
refundability, the `subscription_cycle` lead time, and the ninth hard-decline code.

The source-strength labelling in the research documents must be preserved when those documents are
cited here. A claim that was a search summary does not become a primary source by being written into
a spec — and where the second pass **corrected** a first-pass claim, the correction is recorded in
that lane's `gap-closure-2026-09-18.md` rather than silently overwritten.
