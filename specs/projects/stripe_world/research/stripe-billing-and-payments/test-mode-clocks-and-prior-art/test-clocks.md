# Part 2 — Test clocks, in depth

This is the evidence base for §12.3 (time) — the project's biggest open design question. Unlike
Part 1, the **API shape here is taken directly from `spec3.json`** (read on disk, not from search),
so field names, types, enums and required-ness below are primary-source and can be trusted at face
value. Behavioral prose (what advancing "does") is corroborated with WebSearch synthesis of
`docs.stripe.com/billing/testing/test-clocks`, which I could not directly fetch this session (see
the sourcing note in `test-mode.md`) — those claims are flagged accordingly.

## 2.1 The full `test_helpers/test_clocks` API surface

Read directly from `/home/user/stripe_world/research/stripe-openapi/spec3.json`
(`Stripe-Version: 2026-08-26.dahlia`). Five operations across three paths:

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/v1/test_helpers/test_clocks` | Create |
| `GET` | `/v1/test_helpers/test_clocks` | List |
| `GET` | `/v1/test_helpers/test_clocks/{test_clock}` | Retrieve |
| `DELETE` | `/v1/test_helpers/test_clocks/{test_clock}` | Delete |
| `POST` | `/v1/test_helpers/test_clocks/{test_clock}/advance` | Advance |

There is **no `update` endpoint** — a test clock's `frozen_time` can only move forward via
`/advance`, never be set directly after creation, and `name` cannot be changed after creation
either (no PATCH-equivalent exists in the spec).

### Create — `POST /v1/test_helpers/test_clocks`

> "Creates a new test clock that can be attached to new customers and quotes."

Request body (`application/x-www-form-urlencoded`):

| Param | Type | Required | Description (verbatim) |
|---|---|---|---|
| `frozen_time` | integer (unix-time) | **yes** | "The initial frozen time for this test clock." |
| `customer` | string | no | "Existing customer this test clock will be attached to. Once attached, customers can't be removed from a test clock." |
| `name` | string | no | (custom label) |
| `expand` | array | no | standard expansion |

Note the asymmetry: you can attach an **existing** customer to a **new** clock at clock-creation
time (`customer` param here), or attach a **new** customer to an **existing** clock at
customer-creation time (`test_clock` param on `POST /v1/customers`, see §2.3) — but there is no way
to move an *existing* customer that already belongs to clock A onto clock B, and no way to detach a
customer from a clock at all ("once attached, customers can't be removed from a test clock" is
stated directly in the `create` description).

### List — `GET /v1/test_helpers/test_clocks`

Standard cursor pagination params: `limit` (1–100, default 10), `starting_after`, `ending_before`,
`expand`. Nothing test-clock-specific here — same pagination contract as the rest of the API
(subtopic 2's territory; not re-derived here).

### Retrieve — `GET /v1/test_helpers/test_clocks/{test_clock}`

Plain retrieve by ID, `expand` only.

### Delete — `DELETE /v1/test_helpers/test_clocks/{test_clock}`

Plain delete, no body params. Per the fixtures file (Part 3), a deleted test clock's shape is just
`{"deleted": true, "id": "clock_...", "object": "test_helpers.test_clock"}` — the standard
Stripe "deleted resource" shape, nothing test-clock-specific.

### Advance — `POST /v1/test_helpers/test_clocks/{test_clock}/advance`

> "Starts advancing a test clock to a specified time in the future. Advancement is done when status
> changes to `Ready`."

Request body:

| Param | Type | Required | Description (verbatim) |
|---|---|---|---|
| `frozen_time` | integer (unix-time) | **yes** | "The time to advance the test clock. Must be after the test clock's current frozen time. Cannot be more than two intervals in the future from the shortest subscription in this test clock. If there are no subscriptions in this test clock, it cannot be more than two years in the future." |
| `expand` | array | no | standard expansion |

Response is the `test_helpers.test_clock` object itself (200), immediately — **the advance call
returns right away with `status: "advancing"`, it does not block until the clock reaches the target
time.** You must poll `retrieve` (or listen for the relevant webhook) until `status` flips to
`ready`. This is the "asynchronous advance model" the focus paragraph asks about, and it's directly
evidenced by two things in the spec: (a) the operation summary saying "Advancement is done when
status changes to Ready" (present tense, describing a later state transition, not the response of
this call), and (b) the existence of the `status: "advancing"` enum value with a
`status_details.advancing.target_frozen_time` field that only makes sense while a clock is
mid-flight (§2.2).

## 2.2 The `test_helpers.test_clock` object — full schema

Verbatim from `spec3.json` (`components.schemas["test_helpers.test_clock"]`):

```json
{
  "description": "A test clock enables deterministic control over objects in testmode. With a test clock, you can create objects at a frozen time in the past or future, and advance to a specific future time to observe webhooks and state changes. After the clock advances, you can either validate the current state of your scenario (and test your assumptions), change the current state of your scenario (and test more complex scenarios), or keep advancing forward in time.",
  "properties": {
    "created":        { "type": "integer", "format": "unix-time" },
    "deletes_after":  { "type": "integer", "format": "unix-time", "description": "Time at which this clock is scheduled to auto delete." },
    "frozen_time":    { "type": "integer", "format": "unix-time", "description": "Time at which all objects belonging to this clock are frozen." },
    "id":             { "type": "string" },
    "livemode":       { "type": "boolean" },
    "name":           { "type": "string", "nullable": true, "description": "The custom name supplied at creation." },
    "object":         { "enum": ["test_helpers.test_clock"] },
    "status":         { "enum": ["advancing", "internal_failure", "ready"] },
    "status_details": { "$ref": "#/components/schemas/billing_clocks_resource_status_details_status_details" }
  },
  "required": ["created", "deletes_after", "frozen_time", "id", "livemode", "object", "status", "status_details"],
  "x-expandableFields": ["status_details"]
}
```

`status_details` sub-schema (`billing_clocks_resource_status_details_status_details`):

```json
{ "properties": { "advancing": { "$ref": "#/components/schemas/billing_clocks_resource_status_details_advancing_status_details" } },
  "x-expandableFields": ["advancing"] }
```

...and its nested `advancing` object:

```json
{ "properties": { "target_frozen_time": { "type": "integer", "format": "unix-time",
    "description": "The `frozen_time` that the Test Clock is advancing towards." } },
  "required": ["target_frozen_time"] }
```

### `status` values and what they mean

The enum has exactly **three** values:

- **`advancing`** — an `/advance` call is in flight; `status_details.advancing.target_frozen_time`
  tells you where it's headed. `frozen_time` on the object itself has **not** yet moved to the
  target — it presumably stays at the old value (or an intermediate value) until the transition
  completes. (The spec doesn't state whether `frozen_time` updates incrementally or only on
  completion; not resolved from the schema alone — likely answer, per WebSearch synthesis of the
  docs, is that objects effectively pause/replay through the intervening time rather than jumping,
  since invoices/webhooks fire "as if" time passed normally, but I could not confirm the precise
  mechanics from a primary source this session — **gap**.)
- **`ready`** — advancement finished (or the clock was just created); `frozen_time` now equals the
  requested value; the clock accepts a new `/advance` call.
- **`internal_failure`** — the advance failed server-side. Nothing in the schema says what happens
  to `frozen_time` in this state or whether the clock becomes unusable — **gap**, not resolved.

### `deletes_after` — the lifetime limit

Every test clock carries a `deletes_after` unix timestamp — i.e., **test clocks auto-delete on a
schedule**, not just on explicit `DELETE`. The exact TTL (how far past `created` is `deletes_after`
set) is not stated in the schema itself — it's presumably a fixed offset applied server-side at
creation, and its value is only observable per-instance, not documented as a constant in the spec.
**Gap:** the concrete TTL number was not resolved (search snippets didn't surface it; would need a
direct docs fetch or an empirical create-and-inspect).

## 2.3 What can be attached to a clock, and when

Grepped directly against `spec3.json`: every schema property literally named `test_clock`, and
every request-body param literally named `test_clock` across all paths.

**Schemas that carry a `test_clock` field** (i.e., objects that can *belong to* a clock and report
which one):

- `customer`
- `subscription`
- `subscription_schedule`
- `invoice`
- `invoiceitem`
- `quote`
- `billing.credit_grant`
- `billing.credit_balance_transaction`

**Endpoints that accept `test_clock` as a create-time parameter** — i.e., the only two places you
can *attach* something to a clock directly:

- `POST /v1/customers` — `test_clock`: "ID of the test clock to attach to the customer."
- `POST /v1/quotes` — `test_clock`: "ID of the test clock to attach to the quote."

Everything else in the first list (`subscription`, `invoice`, `invoiceitem`,
`subscription_schedule`, the billing-credit objects) has a `test_clock` field on its *response*
shape but **no `test_clock` create parameter of its own** — meaning they inherit the clock
transitively from their **customer** (or in the schedule/quote case, from the quote/customer chain
that created them). This matches the field descriptions verbatim: `subscription.test_clock` is
"ID of the test clock **this subscription belongs to**" (not "attached to"), consistent with
inheritance rather than direct attachment. The practical rule this implies: **attach the clock to
the Customer at customer-creation time; every subscription/invoice/invoiceitem/credit-grant you
then create for that customer automatically belongs to the same clock, with no per-object
opt-in.** Products, Prices, Plans, PaymentMethods, Charges, and PaymentIntents do **not** carry a
`test_clock` field at all — they're clock-agnostic, which lines up with them not being
time-driven state machines (subtopic 3's territory covers the state machines that do care about
time — subscriptions and invoices).

**Immutable attachment:** confirmed twice from spec text — "once attached, customers can't be
removed from a test clock" (create description) and there is no endpoint that unsets a customer's
or subscription's `test_clock` field. A clock's population is write-once, grow-only.

## 2.4 What advancing actually *does* — behavior (WebSearch-sourced, not directly verified)

**This subsection's claims are sourced from WebSearch synthesis of
`docs.stripe.com/billing/testing/test-clocks`, which I could not fetch directly — treat as
secondary sourcing pending a direct read.**

- Advancing a clock past a subscription's billing-cycle boundary triggers the same invoicing
  machinery that real time would: a new invoice is generated for the period, and (per subtopic 3's
  state-machine research) the normal `draft → open → paid/uncollectible` progression plays out
  against the clock's frozen time rather than wall-clock time.
- Advancing past a trial's end date ends the trial and triggers the first real charge attempt —
  this is exactly the mechanism Part 1's `4000 0000 0000 0341` card scenario depends on (give the
  subscription a short real-world trial *or* advance a test clock past a longer trial, and the
  charge-failure state kicks in either way).
- Webhooks fire as the clock advances through intervening events — i.e., advancing 3 months on a
  monthly subscription is documented to walk through and emit the three intervening months' worth
  of invoice/webhook events rather than silently teleporting and only emitting events for the final
  state. (This is the detail I could not fully confirm mechanically from the schema in §2.2 — the
  `advancing` status existing at all is consistent with this "walk forward" model, since a pure
  instant jump wouldn't need an observable in-flight state.)
- Dunning (smart retries) is clock-aware: advancing a clock through a subscription's retry schedule
  lets you observe the `past_due` → retry → `canceled`/`unpaid` progression deterministically
  instead of waiting real days between retries. (Subtopic 3 owns the retry-schedule specifics;
  flagging the overlap only.)

## 2.5 Frozen-time semantics

- `frozen_time` is described as the time "at which **all objects belonging to this clock** are
  frozen" — i.e., it's not just a per-request `Stripe-Version`-style header, it's a property that
  every attached object's `created`/`current_period_end`/etc. timestamps are computed relative to.
  Two customers on two different clocks can have divergent "now"s simultaneously within the same
  test-mode account.
- Objects **not** attached to any clock presumably run on real wall-clock time as normal — the spec
  frames `test_clock` as an optional, nullable field everywhere it appears, and there is no
  account-wide "clock mode."
- Because a customer's clock assignment is permanent and inherited by everything created under
  that customer, a whole customer graph (subscriptions, invoices, invoice items, quotes, credit
  grants) shares exactly one notion of "now," which is what makes deterministic multi-cycle
  scenario testing possible — you advance the clock once and the invoice-generation lands on every
  attached object consistently, instead of manipulating each object's timestamps individually.

## 2.6 Documented limits (from the spec text directly)

These are the load-bearing numbers for §12.3, and they come straight from the `advance` endpoint's
own parameter description in `spec3.json` — no search synthesis involved:

1. **Forward-only.** `frozen_time` on an advance "must be after the test clock's current frozen
   time" — you cannot rewind a clock. Time-travel is one-directional per clock.
2. **Bounded by the clock's own subscriptions when any exist:** "Cannot be more than **two
   intervals** in the future from the shortest subscription in this test clock." E.g. one monthly
   subscription on the clock caps a single `/advance` call at ~2 months ahead; the docs (via
   WebSearch synthesis) restate this as "if you have a monthly subscription, you can only advance
   the clock up to two months at a time." To jump further you'd issue repeated `/advance` calls.
3. **Bounded at two years when there are no subscriptions on the clock at all:** "If there are no
   subscriptions in this test clock, it cannot be more than two years in the future."
4. **Auto-deletion:** every clock carries `deletes_after` (§2.2) — clocks are not permanent
   fixtures; they expire and presumably get garbage-collected server-side. Exact TTL not resolved
   (gap, see §2.2).
5. **Customer/quote attachment is permanent and one-directional** (§2.3) — not a numeric limit, but
   a hard structural constraint worth listing alongside the numeric ones for a framework that needs
   to decide whether its own `ctx.clock` should allow detach/reattach (§12.3 in the plan explicitly
   asks whether Seahaven's clock could support a `test_clock` object; subtopic 6 owns the framework
   side of that answer, this is the Stripe-side constraint it needs to match).

**Not found / not resolved from any source this session:**
- A documented **cap on the number of test clocks per account/sandbox**. Neither the spec text nor
  WebSearch synthesis surfaced a number (unlike the "5 sandboxes" limit in Part 1, which *was*
  findable). This may simply not be publicly documented, or may be generous enough not to be called
  out. Flag as an open question rather than assuming "unlimited."
- What object types are explicitly **disallowed** from ever attaching to a clock beyond "everything
  that has no `test_clock` field in the schema" (§2.3) — the spec's silence here is itself the
  answer (Products/Prices/PaymentMethods/Charges/PaymentIntents/Accounts/etc. simply have no field
  for it), but there's no prose "here is the disallowed list" to quote.
- The precise semantics of `internal_failure` (§2.2) — recoverable vs. terminal, whether
  `frozen_time` changed, whether a fresh `/advance` retries or must be a new clock.

## Sources

- `/home/user/stripe_world/research/stripe-openapi/spec3.json` — primary source for all of §2.1–2.3
  and §2.6 items 1–3; read directly via Python queries against
  `components.schemas["test_helpers.test_clock"]`, the two supporting `billing_clocks_resource_*`
  schemas, and `paths["/v1/test_helpers/test_clocks..."]`. API version `2026-08-26.dahlia`.
- [Test your integration with test clocks | Stripe Documentation](https://docs.stripe.com/billing/testing/test-clocks) — source for §2.4 behavioral claims; **not directly fetched this session**, WebSearch synthesis only
- [Test Clocks | Stripe API Reference](https://docs.stripe.com/api/test_clocks) — corroborating source for §2.1; not directly fetched, WebSearch synthesis only
- `/home/user/stripe_world/research/repos/stripe-mock/embedded/openapi/fixtures3.json` — confirms the `deleted` shape referenced in §2.1's Delete section (directly read, see `prior-art.md` §3 for the full fixture)
