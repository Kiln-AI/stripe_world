# Research Plan: Stripe Billing & Payments Core (for a Seahaven world)

## Goal

Ground the functional spec and architecture for the Seahaven Stripe world in primary sources rather
than recall. Stripe is large, exactly specified, and moves; a wrong assumption here poisons the
schema, the tool list, the conformance harness and every eval built on top. This research feeds
`/spec new_project` Step 2 (functional spec) and Step 4 (architecture) for project
`specs/projects/stripe_world`, and must answer the six questions in `project_overview.md` §5 plus
supply evidence for the three open questions in §12 (tool surface shape, naming, time).

## Run

- Model: **Sonnet** for all 6 subtopic agents (user decision, 2026-09-18 — research fan-out is the
  token-hungriest work in the skill and these lanes are mostly faithful extraction from primary
  sources). The cross-subtopic summary agent runs on Opus 5: it reads only the six subtopic
  summaries, so it is cheap, and it is the synthesis the functional spec is written from.
- 6 subtopics, one sub-agent each, dispatched in parallel.
- Subtopics 1–5 are web research (billable). Subtopic 6 is local-source research over
  `vendor/Seahaven` — no web spend.

## Primary Sources Pre-Fetched

Before fan-out, the raw primary sources are downloaded to `research/` at the repo root (git-ignored
where large, with a committed `research/MANIFEST.md` recording URL, commit/etag and fetch date), so
subtopic agents read from disk instead of re-fetching multi-megabyte artifacts:

- `stripe/openapi` — `spec3.json`, `spec3.sdk.json`, version metadata
- `stripe/stripe-mock` — full source
- `stripe/agent-toolkit` — full source
- `stripe/stripe-python` — full source

Prose docs (docs.stripe.com) are fetched per-page by the subtopic agents and cited by URL.

## Subtopics

- [x] API surface and object graph — the OpenAPI spec as ground truth; the minimum closed object set and every reference that crosses the scope boundary
- [x] Cross-cutting semantics — idempotency, pagination, expand, metadata, the error envelope, versioning
- [x] Billing and money behavior — the prose-only state machines: subscriptions, invoices, prorations, dunning, refunds, disputes, the balance ledger
- [x] Test mode, clocks and prior art — magic cards, test clocks, sandboxes, and exactly where `stripe-mock` stops
- [x] Agent surfaces, licensing and naming — the Stripe MCP server and agent toolkit tool lists; spec licensing and non-affiliation language
- [x] Seahaven capabilities — what the vendored framework actually offers and where a faithful Stripe world will push on it (local sources, no web)

## Focus Details

### API surface and object graph

Own the machine-readable ground truth. Working from the pre-fetched `spec3.json`, produce a written
inventory of the in-scope resources (`project_overview.md` §4): for each, its `object` discriminator,
its endpoints and HTTP shapes, its full field list with types, nullability and enum values, and its
list-filter parameters. Then answer §5 Q1 precisely: what is the **minimum closed set** of objects
that makes the slice coherent, and for every `$ref` or expandable field that points outside the
declared scope (Connect accounts, Checkout Sessions, Stripe Tax, Radar, Financial Connections,
`payment_method_details` sub-objects, `source`, `on_behalf_of`, `transfer_data`, `application`,
`review`, `mandate`, etc.) name the edge and propose one of: model it, stub it as an id-only
reference, or null it — with a reason per edge. Produce a table sized for the §4 budget (15–20 tables,
40–60 tools) and flag where the honest count exceeds it. Also report: how many distinct API versions
the repo publishes and how the spec is keyed to `Stripe-Version`, and what `spec3.sdk.json` adds over
`spec3.json`. Do **not** cover idempotency/pagination/expand *semantics* (subtopic 2), lifecycle or
proration math (subtopic 3), or licensing (subtopic 5) — only the schema-level facts and the scope
boundary decisions.

### Cross-cutting semantics

Own the behaviors that apply to every endpoint, because they are non-negotiable for fidelity and they
are mostly *not* in the OpenAPI schema. Answer, with citations and observed-behavior detail:
idempotency keys — how long a key is retained, what happens on a replayed key with identical params,
with *different* params, on a concurrent in-flight replay, which methods honour keys, whether errors
are cached, and the exact `idempotency_key_in_use` / `idempotent_request_conflict` shapes plus the
`request.idempotency_key` echo on responses. Pagination — default and max `limit`, the ordering
guarantee, exactly how `starting_after` and `ending_before` behave (exclusive? on a deleted id? both
at once?), what `has_more` reports at a page boundary and on the last page, `total_count` availability,
and auto-pagination in the clients. Expansion — the depth limit, which fields are expandable, the
`data.` prefix for lists, expansion inside subscriptions/invoices, `expand` on create/update, and what
error a bad path returns. Metadata — key/value limits, deletion semantics, which objects carry it.
The error envelope — every field (`type`, `code`, `decline_code`, `param`, `message`,
`doc_url`, `request_log_url`, `payment_intent`, `setup_intent`, `charge`), the full `type` enum, the
full `code` list and the full `decline_code` list, and which HTTP status pairs with which type,
including rate limiting and `StripeInvalidRequestError` parameter naming for nested/array params.
Versioning — what `Stripe-Version` changes, how unknown versions are handled, and what the response
headers carry. Leave money/lifecycle behavior to subtopic 3 and test-mode specifics to subtopic 4.

### Billing and money behavior

Own §5 Q3: the high-value behavior that lives in prose docs and observed behavior rather than the
schema, written precisely enough to implement from. Cover: the `subscription` status machine
(`incomplete`, `incomplete_expired`, `trialing`, `active`, `past_due`, `canceled`, `unpaid`, `paused`)
with every transition and its trigger, including `payment_behavior`, incomplete expiry timing, trials,
`cancel_at_period_end` vs immediate cancel, and pause/resume. The `invoice` status machine (`draft`,
`open`, `paid`, `uncollectible`, `void`) — what finalizes a draft and *when* (the ~1-hour draft window,
`auto_advance`, `collection_method`, `days_until_due`), what `billing_reason` values exist, and how
subscription cycles create invoices. Proration math to the arithmetic level: how credit and debit
proration line items are computed, `proration_behavior` options, `proration_date`, upgrades vs
downgrades mid-cycle, quantity changes, `billing_cycle_anchor` and what `unused_time` /
`remaining_time` lines look like — including the exact rounding rule. Dunning / smart retries: the
default retry schedule, what happens at the end of it, `past_due` vs `unpaid` vs cancel. Refunds:
partial, over-refund rejection, refund of a disputed charge, `refunded`/`amount_refunded` bookkeeping.
Disputes: status machine, funds-withdrawn-and-returned ledger effects, evidence submission. The
balance ledger: what a `balance_transaction` is created for, its `type` enum, fee computation,
`available`/`pending` balance movement and the `available_on` delay, and how payouts draw down.
Credit notes and subscription schedules: cover both, and give an explicit cost/benefit call on whether
`subscription_schedules` is worth the tables and tools (`project_overview.md` §4 flags it as
conditional). Cite the specific doc page next to every behavior.

### Test mode, clocks and prior art

Own §5 Q4 and Q5, and gather the evidence for open question §12.3 (time). Part one — test mode: what
is testmode-only, the complete magic card number table and exactly which error/`decline_code` each
produces, magic values for other flows (disputes, payouts, refunds, 3DS-required cards, specific
`payment_method` test tokens like `pm_card_*` and `tok_*`), what a Sandbox is versus a test-mode key,
and what differs in object shape between test and live. Part two — **test clocks**, in depth, because
this is the biggest open design question: the full `test_helpers/test_clocks` API (create, advance,
list, delete), what objects can be attached to a clock and when, what advancing actually does to
subscriptions/invoices/dunning, the `status` values and the async advance model, the frozen-time
semantics, and the documented limits (how far you can advance, how many clocks, what cannot be
attached). Part three — prior art: read the `stripe-mock` source on disk and report precisely how it
works (OpenAPI-driven response generation, the fixtures file, what state it does and does not keep,
how it handles pagination/expand/idempotency/errors) and **exactly where it stops** — confirm or
refute the expectation that it does not model subscription lifecycles or money moving. Also note any
other serious Stripe emulator prior art you find and what it teaches. Leave licensing to subtopic 5.

### Agent surfaces, licensing and naming

Own §5 Q2 and Q6, and the evidence for open questions §12.1 and §12.2. Part one — what tool surface
real agents are given today: read the pre-fetched `stripe/agent-toolkit` source and the docs for the
official Stripe MCP server (`mcp.stripe.com`, `docs.stripe.com/agents`) and report the **exact tool
list** — names, parameters, granularity, which API subset they cover, whether there is a generic
"call any endpoint" escape hatch, how they name things (`create_customer` vs `customers.create`), what
they deliberately omit, and how read vs write is gated. Contrast with agents that hit raw HTTP, and
report any published guidance or measurement on which shape works better for LLM agents (tool-count
limits, schema size, etc.). Write a clear recommendation for §12.1: one-tool-per-operation, a single
generic `stripe_request(method, path, params)`, or both — with the argument on each side and what each
choice does to the eval surface. Part two — licensing and naming: the exact license on
`stripe/openapi`, on `stripe-mock`, on `stripe-python` and on the agent toolkit; what that permits us
to redistribute (can we commit `spec3.json` into an MIT repo? must we carry a NOTICE?); Stripe's
trademark policy and any published API-terms clause that bears on building a compatible mock; and what
comparable open-source compatible-API projects put in their READMEs as non-affiliation language —
gather two or three real examples verbatim. Conclude with a recommendation for §12.2.

### Seahaven capabilities

Own the framework side. Local sources only — `vendor/Seahaven` — no web access needed or wanted.
Read the bundled docs (`src/seahaven/docs/`: `index.md`, `concepts.md`, `authoring.md`,
`db_schema_and_fixtures.md`, `state.md`, `testing.md`, `composition.md`, `extensions.md`,
`serving_and_openenv.md`, `reference/api.md`, `reference/cli.md`, `reference/lints.md`), the
`worlds/projecttracker/` reference world end to end, and the framework source where the docs are thin.
Produce a written capability map an architect can design against: how a world package is laid out and
what `seahaven new` scaffolds; the registration verbs (`world.tool`, `world.middleware`,
`world.instance_startup`, `world.add_world`) and their exact signatures; what `ctx` carries
(`ctx.db`, `ctx.clock`, `ctx.ids`) and the precise contract of each; how the declared-error mechanism
and the error-handler middleware work; the schema/migration convention and what SQLite features are
available (JSON1? generated columns? decimal handling?); the fixture lifecycle (`seahaven fixture`,
`fixtures_src/generate.py`, freezing, hashing, forking) and what fork cost looks like; the change log
document and its versioned shape; `run_sql` / `describe_schema` / read-only inspection; the pytest
plugin's `world` and `instance` fixtures; `seahaven check` lints; and composition/tool-prefixing rules
that matter for being `add_world`-ed into a host. Then, specifically, assess the pressure points a
faithful Stripe world will apply, with a verdict on each: (a) **id generation** — can `ctx.ids`
produce Stripe-shaped prefixed ids (`cus_`, `pi_`, `in_`) deterministically, or is its format fixed?
(b) **time** — exactly what the frozen clock does, whether an instance can advance time at all, and
what a `test_clock` object would require of the framework; this is the §12.3 decision. (c) **money**
— integer minor units, any decimal/rounding support. (d) **tool count** — any limit or lint that 40–60
tools would trip. (e) **composition** — how tool prefixing works and what a host world sees.
(f) **deep JSON objects** — how a world returns nested objects, and whether Seahaven expects flat rows.
(g) **fixture size** — whether a thousands-of-customers fixture forks in milliseconds. Note any doc
gap, unclear error message or ergonomic friction encountered while reading — these are the first
entries in `SEAHAVEN_FINDINGS.md`, so record them with the same detail the findings log wants.
Do not research Stripe; that is the other five subtopics' job.
