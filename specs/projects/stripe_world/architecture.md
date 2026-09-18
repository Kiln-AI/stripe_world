---
status: draft
---

# Architecture: StripeAPI

Technical design for the world specified in [`functional_spec.md`](functional_spec.md). Framework
facts cited here come from
[`seahaven-capabilities`](research/stripe-billing-and-payments/seahaven-capabilities/summary.md);
Stripe facts from the other five research lanes.

This is a **two-phase** architecture: this document holds the decisions and the component boundaries,
and eight component designs under [`components/`](components/) hold the detail. Nothing below leaves
a significant decision to the coding agent.

## 1. The shape of the thing

```
          stripe_api_search   stripe_api_details        ← discovery, read-only, no state
          stripe_api_read     stripe_api_write          ← the agent surface
                      │
              ┌───────┴────────┐
              │   Dispatcher    │   path → route → handler; the whole world is behind this
              └───────┬────────┘
       ┌──────────────┼──────────────┬────────────────┐
   Resource        Action          Cross-cutting     Serializer
   engine          handlers        (idempotency,     (row → API object,
   (CRUD from      (capture,        pagination,       expansion, nesting)
    a spec)         finalize,       events, errors)
                    pay, void…)
                              │
                        SQLite (STRICT tables, one connection, frozen clock)
```

Five ideas carry the design:

1. **One dispatcher, four faces.** The tools are presentation; the dispatcher is the world.
2. **Declare resources, write only the interesting handlers.** CRUD is generated from a
   `ResourceSpec`; only state transitions are hand-written.
3. **`spec3.json` is the source of truth for shapes, not for behavior.** It drives discovery and
   response conformance. Request-parameter allowlists are ours, because Stripe's own rejection of
   unknown parameters requires a closed set we control.
4. **Storage is flat and Seahaven-native; the API shape is produced at the edge.** Rows are `STRICT`
   tables with TEXT timestamps; Unix seconds and nested objects are made by the serializer.
5. **Stripe errors are return values, not exceptions.** Seahaven's error machinery is reserved for
   authoring mistakes.

## 2. Package layout

Scaffolded with `seahaven new`, following ProjectTracker's shape exactly.

```
pyproject.toml                      # distribution: seahaven-stripe-world
AGENTS.md
README.md
src/stripeapi/
  __init__.py                       # imports world, then tools/ and middleware/ for side effects
  world.py                          # world = seahaven.World(..., state_format="seahaven.state/1")
  errors.py                         # ToolError subclasses — authoring errors only (§7)
  stripe_errors.py                  # StripeApiError + the envelope — agent-visible errors (§7)
  startup.py                        # instance_startup: account config
  openenv_app.py
  schema/
    001_core.sql                    # customers, products, prices, coupons, promotion_codes, tax_rates
    002_payments.sql                # payment_intents, charges, refunds, disputes, setup_intents,
                                    #   payment_methods, balance_transactions, payouts
    003_billing.sql                 # subscriptions, subscription_items, subscription_schedules,
                                    #   invoices, invoiceitems, credit_notes,
                                    #   customer_balance_transactions
    004_infra.sql                   # events, idempotency_keys
    005_search.sql                  # FTS5 — final phase only (§3.3 of the functional spec)
  dispatch/
    __init__.py
    router.py                       # path patterns → Route
    routes.py                       # the 148-entry routing table, declarative
    resource.py                     # ResourceSpec + the generic CRUD engine
    params.py                       # per-operation parameter allowlists and coercion
    response.py                     # {status, body} construction
  resources/                        # one module per resource: its ResourceSpec + its actions
    customers.py  products.py  prices.py  coupons.py  promotion_codes.py  tax_rates.py
    payment_methods.py  payment_intents.py  charges.py  refunds.py  disputes.py
    setup_intents.py  balance.py  balance_transactions.py  payouts.py
    subscriptions.py  subscription_items.py  subscription_schedules.py
    invoices.py  invoiceitems.py  credit_notes.py  customer_balance_transactions.py  events.py
  billing/                          # behavior that spans resources
    proration.py  invoicing.py  dunning.py  ledger.py  subscription_lifecycle.py
  serialize/
    __init__.py  expand.py  fields.py
  discovery/
    __init__.py  index.py           # built from spec3.json, filtered by routes.py
  _ids.py  _json.py  _time.py       # shared helpers, ProjectTracker's `_`-prefix convention
  tools/
    __init__.py  api.py             # the four tools; account.py added at P2
  middleware/
    __init__.py  error_handler.py  idempotency.py
  spec/
    spec3.min.json                  # pruned OpenAPI subset, generated, committed (§5.2)
    expandable.py  enums.py  event_types.py   # generated, committed
fixtures_src/generate.py
fixtures/{empty,small,large}/
tools_dev/prune_spec.py             # regenerates everything under src/stripeapi/spec/
tests/
```

## 3. The dispatcher

### 3.1 Route table

`routes.py` is data, not code — one entry per operation:

```python
Route(
    method="POST",
    pattern="/v1/customers/{customer}/balance_transactions",
    op_id="PostCustomersCustomerBalanceTransactions",   # matches spec3.json operationId
    handler=customer_balance_transactions.create,
    params=CUSTOMER_BALANCE_TXN_CREATE,                  # a ParamSpec
)
```

Matching is a compiled prefix trie over `/`-split segments, with `{placeholder}` segments matching
one literal. Exact matches beat placeholders, so `/v1/customers/search` never shadows
`/v1/customers/{customer}`. A path with no match is a Stripe `404` (`invalid_request_error`,
`resource_missing`); a path that matches with a different method is Stripe's method error, not a 404.

**The route table is the single source of scope.** Discovery filters `spec3.json` through it (§5),
the conformance harness enumerates it, and a test asserts the count is exactly 148 (155 once search
lands), so scope drift shows up as a failing test rather than as a surprise.

### 3.2 Generated CRUD, hand-written actions

Most of the 148 operations are the same five shapes. Each resource declares a `ResourceSpec`:

```python
ResourceSpec(
    object="customer",            # the `object` discriminator
    table="customers",
    id_prefix="cus",
    serializer=customers.to_api,
    list_filters=("email", "created", "test_clock"),
    creatable=(...), updatable=(...),   # parameter allowlists
    deletable=True,                      # Stripe's soft `deleted: true` shape
)
```

From that, the engine provides list, create, retrieve, update and delete — roughly **95 of the 148
operations**. The remaining ~53 are state transitions and genuinely resource-specific reads
(`capture`, `confirm`, `cancel`, `finalize`, `pay`, `void`, `send`, `mark_uncollectible`, `refund`,
`reverse`, `attach`, `detach`, `resume`, `release`, and the balance/event reads). Those are ordinary
Python functions in `resources/` and `billing/`. **No operation gets a hand-written list endpoint**;
if one needs unusual filtering, it declares it, it does not reimplement pagination.

This is the decision that makes 148 operations tractable without 148 handlers, and it is why the
table budget (17–19) and the operation count (148) are not in tension.

### 3.3 Parameters

Request parameters are validated against **our** `ParamSpec`, not against `spec3.json`. Stripe
rejects unknown parameters (`Received unknown parameter: …`), which requires a closed set, and the
spec's request bodies are far larger than the subset this world implements. A `ParamSpec` declares
per-parameter: name, type, required, allowed values, and nested shape. Unknown parameter → Stripe's
own error with `param` set. Bracket-notation semantics are irrelevant here: parameters arrive as
JSON (functional spec §2.2), and **no form encoding exists in this project**.

`expand`, `limit`, `starting_after`, `ending_before` and `metadata` are handled centrally rather than
redeclared per operation.

## 4. Data model

### 4.1 Tables

19 tables plus two infrastructure tables. Per the functional spec, `balance` is computed,
`line_item` / `credit_note_line_item` / `discount` are nested JSON on their parents.

`001_core` customers, products, prices, coupons, promotion_codes, tax_rates
`002_payments` payment_methods, payment_intents, charges, refunds, disputes, setup_intents, balance_transactions, payouts
`003_billing` subscriptions, subscription_items, subscription_schedules, invoices, invoiceitems, credit_notes, customer_balance_transactions
`004_infra` events, idempotency_keys

Every table is `STRICT` with an explicit primary key (the Stripe id, TEXT). Money is `INTEGER` minor
units. Enumerated values are TEXT with a `CHECK` listing the closed set, taken from `spec/enums.py`
so the schema and the conformance validator cannot disagree.

### 4.2 Timestamps: TEXT inside, Unix seconds outside

Stored as Seahaven's canonical `2026-06-01T09:00:00.000Z` TEXT — the framework convention, sorts
correctly as text, and keeps `seahaven check`'s `SH103` satisfied. The serializer converts to Unix
seconds, which is what Stripe returns. Conversion lives in `_time.py` and nowhere else; a test
asserts no serializer emits an ISO string in a timestamp field.

Nothing reads a wall clock. Every timestamp originates from `ctx.clock.iso()`.

### 4.3 Nested JSON

`invoice.lines`, `credit_note.lines` and embedded `discount` objects are TEXT columns with
`CHECK (json_valid(col))`, written through `_json.py`'s single dump function
(`sort_keys=True, separators=(",", ":"), ensure_ascii=False`) so bytes are reproducible across runs —
ProjectTracker's established pattern. JSON1 is fully available.

### 4.4 Ids

`_ids.py` holds the only id minting function:

```python
def stripe_id(ctx: seahaven.Ctx, prefix: str) -> str:
    """A Stripe-shaped id drawn from the instance's seeded stream."""
```

Built on `ctx.ids.random`, which the framework documents as the stream product-shaped keys draw from.
`ctx.ids.uuid()` is fixed-format and unusable here.

**This is a known hazard**: nothing in `seahaven check` can enforce "every id matches pattern X", and
a tool that slips back to `ctx.ids.uuid()` produces a UUID where Stripe expects `cus_…`
(`SEAHAVEN_FINDINGS.md` Entry 2). Two project-local guards, because the framework has none:

- a test that greps `src/stripeapi/resources/` and `billing/` for `ctx.ids.uuid(` and fails on a hit;
- a fixture-wide test asserting every primary key in every table matches its resource's prefix.

### 4.5 Serialization and expansion

`serialize/` turns a row into an API object: field renaming, Unix-second conversion, nested JSON
inflation, `object` discriminator, `livemode: false`, and null-vs-absent handling.

Expansion is generic. `spec/expandable.py` is generated from the spec's `x-expandableFields` into
`{object: {field: target_resource}}`. The resolver walks requested paths, enforces Stripe's depth
limit, applies the `data.` prefix rule for lists, and raises Stripe's
`"This property cannot be expanded (<field>)"` for a bad path. Unexpanded references serialize as
the bare id string.

## 5. Discovery

### 5.1 The two tools

- `stripe_api_details(method, path)` — returns the parameter documentation for one operation.
- `stripe_api_search(query)` — keyword match over path, `operationId`, summary and description,
  returning ranked `(method, path, summary)` triples.

Both read `discovery/index.py`, which is **built from `spec3.json` filtered through `routes.py`**.
An operation that is not routed is not searchable and has no details, and the filter is derived from
the route table rather than maintained beside it, so the two cannot disagree. A test asserts that the
discovery index and the route table have identical key sets.

### 5.2 The pruned spec

`spec3.json` is 8 MB — too large to load per instance and too large to commit into the package.
`tools_dev/prune_spec.py` generates, and we commit, `src/stripeapi/spec/spec3.min.json`: only routed
operations, only referenced schemas, with descriptions retained (they are the discovery text and, per
the research, they carry Stripe's own documentation prose). It also generates `expandable.py`,
`enums.py` — including the six fields Stripe types as bare `string` but which have closed sets
recoverable only from description prose — and `event_types.py` from the committed 266-entry set.

Regenerating is a committed command, not a manual step. A test asserts the generated files match what
the pruner produces from the full spec, so a stale artifact fails CI rather than drifting. The full
`spec3.json` stays in git-ignored `research/`, with `research/MANIFEST.md` as its provenance; the
pruned subset carries Stripe's MIT copyright notice in `THIRD_PARTY_LICENSES`.

## 6. Cross-cutting behavior

### 6.1 Idempotency — middleware

`middleware/idempotency.py`, registered immediately inside the error handler. It hashes
`(path, method, canonical params)`, looks up `idempotency_keys`, and:

- miss → run the call, store the response, return it;
- hit with matching hash → **short-circuit and return the stored response**, writing nothing;
- hit with a different hash → Stripe's `idempotency_error`.

Middleware is the right seam precisely because a replay must produce **no change-log records** — a
short-circuit never reaches the tool, so nothing is written and the change log correctly shows one
charge for two calls. That property is what the headline eval grades.

`idempotency_keys` is in `World(untracked_tables=...)`: it is infrastructure, not Stripe state, and
leaving it tracked would put bookkeeping rows into every graded change log. Keys never expire, since
`now` is fixed; the real API's ≥24h floor is a declared conformance difference.

### 6.2 Pagination

One implementation in `dispatch/resource.py`: reverse-chronological ordering, `limit` 1–100 default
10, mutually exclusive `starting_after` / `ending_before`, `has_more`, and the list envelope. Ordering
is `(created DESC, id DESC)` — `created` alone is not unique under a frozen clock, and without the id
tiebreak cursor pagination would be unstable. **This is a real consequence of the frozen clock and is
designed for, not discovered later.**

### 6.3 Events

`billing/` and `resources/` call one `emit_event(ctx, type, obj)`. `type` is checked against
`spec/event_types.py` at call time; an unknown type is a `WorldBug`, because it means the author
invented an event Stripe does not have.

### 6.4 The balance

`balance` is a computed read over `balance_transactions`, splitting `available` from `pending` on
`available_on` against `ctx.clock`. No table, no denormalized counter, so it cannot drift from the
ledger — and the "ledger sums correctly" invariant test becomes a tautology rather than a check of
two copies.

## 7. Errors

Two distinct systems, deliberately not merged.

**Stripe errors are return values.** `stripe_errors.py` defines `StripeApiError(status, type, code,
decline_code=None, param=None, message=...)`, raised anywhere inside the dispatcher and caught at the
dispatcher boundary, which converts it to `{"status": …, "body": {"error": {…}}}`. Agents see
Stripe's envelope with the right HTTP status, and a declined card is an ordinary outcome rather than
an exception. `type` is constrained to the four real wire values.

**Seahaven errors are authoring errors.** `errors.py` holds `ToolError` subclasses for contract
violations — an unusable method, a malformed parameter object. `middleware/error_handler.py` follows
the scaffold: re-raise `WorldBug` unchanged, map `ArgumentError` to the world's own invalid-input
error, log and generalize `DbError`, catch everything else as internal.

**Parents are looked up before a foreign key can refuse.** Every handler that writes a child row
`SELECT`s its parent first and raises Stripe's `resource_missing` — a bare FK violation would surface
as a `DbError` and reach the agent as an internal error instead of the actionable 404 Stripe sends.
`resources/_lookup.py` holds that family, mirroring ProjectTracker's `_rows.py`.

## 8. Billing engine

`billing/` holds the behavior that spans resources, because putting it in any one resource module
would be arbitrary:

- `subscription_lifecycle.py` — the eight-status machine and its transitions.
- `invoicing.py` — the invoice status machine, line construction, totals, `automatically_finalizes_at`.
- `proration.py` — one function, `proration_lines(...)`, implementing the settled rule: credit and
  debit computed to the second, **each rounded to the nearest cent independently, then summed**. The
  half-cent tie-break is the single assumption in this module and carries a comment naming it and
  pointing at conformance scenario 1.
- `dunning.py` — the retry configuration envelope and the three end-of-schedule outcomes. No retry-day
  table: Smart Retries is ML-scheduled and inventing a schedule would be inventing behavior.
- `ledger.py` — `balance_transaction` creation, `net = amount - fee`, `available_on`, payout draw-down.

These are pure functions over rows plus `ctx` wherever possible, so they are unit-testable without
going through a tool call, and the tool-level tests then check integration rather than arithmetic.

## 9. Fixtures

`fixtures_src/generate.py`, one function per fixture, committed.

**History is simulated, then frozen.** The generator runs a virtual timeline — customers signing up,
subscriptions renewing, invoices finalizing and being paid or failing into dunning, disputes opening,
payouts settling — writing rows with explicit timestamps drawn from that timeline, and the instance is
frozen at a single `now`. Fixture data therefore looks like an account with a past, while every call
made against the instance sees one clock.

`inst.bulk()` for volume; a deliberate handful of objects created **through the real tools** at the
end, as ProjectTracker does, so the fixture exercises the path an agent uses and any stateful counter
lands where the tools would have left it.

| Fixture | Built by | Scale |
|---|---|---|
| `empty` | freeze from blank, `--now` fixed | schema only |
| `small` | fork of `empty` | tens of customers |
| `large` | fork of `empty` | thousands of customers, churn, dunning, disputes, payout history |

Measured fork cost on this framework is 4–8 ms at 20K rows and 11–29 ms at 120K rows, so `large` sits
inside the milliseconds constraint with headroom.

## 10. Conformance harness

`tests/conformance/`. Cassettes are committed JSON; CI replays only and never opens a socket.

- **Recording** (`tools_dev/record.py`) drives scenarios against real test mode **through
  `stripe-python`**, so request encoding is the SDK's problem and no form encoder exists here.
  Requires `api.stripe.com` egress, which the current environment blocks — recording happens
  elsewhere and the cassettes are committed artifacts.
- **Replay** runs the same scenario against an instance and diffs the response bodies.
- **The allow-list** (`tests/conformance/allowed_differences.py`) declares every permitted difference
  with a reason: ids, timestamps, `request_log_url`, livemode, idempotency-key retention, search
  freshness. Undeclared difference → failure. This file is the precise statement of how faithful the
  world is, and it is reviewed as a design document.

Separately and more cheaply, **schema conformance** validates every object the world returns against
`spec3.min.json`, including the six bare-`string`-but-enumerated fields. It is built first because it
constrains everything after it.

## 11. Testing strategy

Seahaven's pytest plugin throughout; `@pytest.mark.seahaven(fixture=...)` per module; every tool
exercised through `instance.call(...)`, never the bare function, so validation, middleware and the
transaction are all in play. Errors asserted by **code**, never message text. State asserted through
`inst.inspect()`.

| Layer | Tests |
|---|---|
| Router | pattern precedence, 404 vs method error, the 148-count assertion |
| Resource engine | CRUD per resource, pagination boundaries, list filters |
| Discovery | index ≡ route table; unrouted operation invisible |
| Serializer | Unix seconds, nesting, `object`, expansion depth, bad-path error |
| Billing | proration worked examples including the documented −667/+333/−334 case; status machines |
| Cross-cutting | idempotency replay writes nothing; the four error `type` values |
| Invariants | ledger sums; no over-refund; one object per idempotent retry; lines sum to total; periods never overlap |
| Determinism | same fixture + seed → identical ids, timestamps and change log |
| Conformance | schema validation; cassette replay |

Invariant tests are written as SQL over `inst.inspect()` so they are literally reusable as eval
reward functions.

## 12. Constraints and known hazards

- **Python 3.14 with `pydantic==2.12.3` pinned.** The framework's locked `2.13.5` crashes
  `import seahaven` on the 3.14 builds available (`SEAHAVEN_FINDINGS.md` Entry 1). Pin, with a
  comment pointing at the finding, and revisit.
- **One connection.** Never open a second connection to the instance file or change its pragmas.
- **Schema hash costs every fixture.** Any column addition regenerates all three — which is why the
  generator is committed and the schema should be settled before `large` is built.
- **No cross-world atomicity** under composition, which matters if a host ever wraps this world.
- **`SH206`**: tool descriptions must not cross-reference bare tool names, because a prefixing host
  renames tools without rewriting descriptions. With four tools this is easy to honour.
- **`bytes` and `set` are refused** as tool return types; everything crossing the boundary is JSON.

## 13. Component designs

| Component | Covers |
|---|---|
| `components/dispatcher.md` | Route table, trie matching, `ResourceSpec` engine, `ParamSpec`, response construction |
| `components/discovery.md` | The pruner, the index, search ranking, details rendering |
| `components/data_model.md` | Full DDL per table, id prefixes, enum sources, nesting, serializer field maps |
| `components/cross_cutting.md` | Idempotency middleware, pagination, expansion resolver, events, error envelope |
| `components/billing_engine.md` | Status machines, proration arithmetic, invoicing, dunning, ledger |
| `components/fixtures.md` | Timeline simulation, per-fixture composition, bulk vs through-the-tools |
| `components/conformance.md` | Cassette format, recorder, replayer, the allow-list |
| `components/evals.md` | The eval tasks and their SQL reward functions |

Resource slices do **not** each get a component design. They follow one recipe, documented in
[`implementation_plan.md`](implementation_plan.md), over the `ResourceSpec` engine in
`components/dispatcher.md` and the per-table DDL in `components/data_model.md`. A design doc per
resource would be twenty-odd copies of the same document.
