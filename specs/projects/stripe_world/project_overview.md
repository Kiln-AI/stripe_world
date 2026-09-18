---
status: draft
---

# Seahaven Stripe World

Build a Seahaven world that replicates Stripe's **Billing and Payments core** — a faithful,
stateful, forkable copy of the Stripe API that an agent can be run against thousands of times,
in parallel, from a known starting state, with every row it changed available afterwards.

This is a new, standalone repository. It is not part of the Seahaven framework repo.

## 1. What Seahaven is

Seahaven is a Python framework for building **synthetic worlds**: faithful, stateful mocks of the
tool surface a real company's agent works against. An agent inside a Seahaven world reads and
writes through the same tools it would have in production, against state that responds the way the
real system does, without touching the real system. It exists because RL and evals need thousands
of rollouts, in parallel, each from a known state, each inspectable afterwards, and no real system
or staging copy can do that.

The pieces to know before reading the rest of this document:

- **World** — the code for one synthetic world: a Python package holding a SQLite schema and a list
  of tools. This project builds one world.
- **Tool** — one operation the agent can call. Its Python signature is the JSON schema the agent
  sees and its docstring is the description. A world's agent-facing surface is exactly its tool list.
- **Fixture** — a named, immutable starting state. Minted by freezing an instance, hash-verified,
  never edited in place.
- **Instance** — a private, running copy of a world created from a fixture, in milliseconds. It is
  one SQLite file. Agents write to it; it is thrown away afterwards.
- **Frozen clock and seeded ids** — every instance runs on a framework clock frozen at its fixture's
  `now`, in Python *and* in SQL, with a seeded id generator. Same fixture + same seed = same run.
- **Change log** — every row the agent changed, call by call, in one versioned document. This is how
  evals grade: on state, not on transcripts.
- **Composition** — a world can `add_world` other worlds, contributing a flat tool list and a shared
  inspection connection. Other people will add this world to theirs.

**Seahaven is not published yet. A copy of the framework source lives in `vendor/seahaven` in this
repository — depend on that (a local path / uv workspace dependency), not on PyPI.** It ships its
own docs: `uv run seahaven docs` prints the path to the copy that matches the installed version.
Read those docs before writing any world code — especially `authoring.md`,
`db_schema_and_fixtures.md`, `state.md`, `testing.md` and `composition.md`. The reference world,
`worlds/projecttracker/` in the vendored copy, is the conventions worked example: read it before
writing our own, and follow its shape (one tool module per resource, a declared error set, an error
handler middleware, a committed `fixtures_src/generate.py`, and a test per tool module).

## 2. Why this project — two halves

This project has **two deliverables of equal weight**: the world, and a written account of what
building it taught us about Seahaven.

### Half one: the world

1. **Promote Seahaven.** Seahaven has exactly one example world today, and it is a fictional issue
   tracker. A serious, faithful Stripe world is the artifact that makes a reader understand what the
   framework is for. Stripe is the best-known REST API in the training corpus, money is
   unambiguously gradeable, and the failure modes — a retried call that double-charges, a refund
   against the wrong charge, a botched proration — are invisible in a transcript and obvious in the
   change log. That contrast *is* the pitch.
2. **A world we test with.** A large, real fixture set and a deep object graph to run harnesses,
   evals and optimizers against.
3. **A world other worlds are built from.** This is meant to be `add_world`-ed into larger composite
   worlds — a SaaS company, a store, a marketplace — as their billing subsystem. It should be
   designed as a component from day one: clean tool prefixing, no assumptions about the host, and
   fixtures that make sense on their own and as part of something bigger.

### Half two: kick Seahaven's tires and report back

ProjectTracker is a world the framework was designed *against*, so it exercises the paths Seahaven
already knows about. Stripe was designed by somebody else. Building it faithfully will push on parts
of the framework nothing has pushed on yet — id formats, deep object graphs, expansion, money
arithmetic, list filtering, a second notion of time — and that pressure is a primary goal, not a
side effect.

So this project is also the framework's first real user study, with an AI author as the subject.
**A deliverable describing how to improve Seahaven is as important as the world itself.** It covers
bugs, missing seams, design concerns, doc gaps and ergonomics, and it is written continuously while
the work happens, not reconstructed at the end.

A third thing falls out of both halves: this repo becomes the worked example an agent reads when
asked to build the next world. Its structure, its tests and its `AGENTS.md` should be good enough
to copy.

## 3. Seahaven native

**Build this the Seahaven way, using Seahaven's own conventions and tools, even where something else
would be quicker.** That is the whole point of half two — a world built around the framework teaches
us nothing about the framework.

Concretely:

- Scaffold with `seahaven new`. Lint with `seahaven check` and keep it clean. Freeze and fork
  fixtures with `seahaven fixture`, never by hand. Serve with `seahaven serve`.
- Use the pytest plugin and its `world` / `instance` fixtures. Use `ctx.db`, `ctx.clock`, `ctx.ids`,
  the declared-error mechanism, `run_sql` and `describe_schema`, the change log, and read-only
  inspection — rather than reaching around them.
- Register through the documented verbs (`world.tool`, `world.middleware`,
  `world.instance_startup`, `world.add_world`). Put anything genuinely new behind the extension
  seams as an ordinary package, the way an outside author would have to.
- Read the bundled docs (`seahaven docs`) as the source of truth and follow ProjectTracker's shape:
  one tool module per resource, a declared error set, an error-handler middleware, a committed
  `fixtures_src/generate.py`, a test module per tool module.
- **If a convention feels wrong, that is a finding — not a licence to route around it.** Write it
  down, then either follow the convention anyway or take the workaround *with a comment pointing at
  the finding*. An undocumented deviation destroys the signal we are running this project to get.

Where the framework genuinely cannot do something, say so loudly and stop, rather than inventing a
parallel mechanism beside it.

## 4. Scope

**In: Billing and Payments core.** Roughly the objects a B2B SaaS company's own agent would touch.

- **Core** — `customers`, `payment_methods`, `products`, `prices`, `coupons`/`promotion_codes`,
  `tax_rates` (flat rates only, not Stripe Tax)
- **Payments** — `payment_intents`, `charges`, `refunds`, `disputes`, `setup_intents`,
  `balance_transactions`, `payouts`, `balance`
- **Billing** — `subscriptions`, `subscription_items`, `invoices`, `invoice_items`, `invoice_lines`,
  `credit_notes`, `subscription_schedules` (if research says the cost is reasonable)
- **Cross-cutting, and non-negotiable for fidelity** — idempotency keys, cursor pagination
  (`limit` / `starting_after` / `ending_before` / `has_more`), `expand[]`, `metadata`, the error
  envelope (`type`, `code`, `decline_code`, `param`, `message`), list filters, the `events` object,
  and test-mode magic card numbers as the failure-injection mechanism.

**Out, explicitly.** Connect, Issuing, Terminal, Treasury, Capital, Climate, Crypto, Stripe Tax,
Radar rules, Sigma, Financial Connections, Identity, Checkout Sessions, Payment Links, Elements and
anything client-side, the Billing Portal, the Dashboard, and actual webhook *delivery*. Also out:
completeness for its own sake. This is a deep replica of a chosen slice, not a thin replica of
everything. If a research finding says an object is out of scope but three in-scope objects
reference it, say so and we will decide — don't quietly pull it in.

**Target size:** on the order of 15–20 tables and 40–60 tools. If the design comes in much bigger
than that, the scope is wrong and we should cut before building.

## 5. Planning: research phase first

Do not spec this from memory. Stripe is large, exactly specified, and changes; a wrong assumption
here poisons everything downstream.

**Download the primary sources into a research folder in this repo before speccing.** A `research/`
directory (git-ignored if large, with a committed manifest recording what was fetched, from where,
and on what date) holding at minimum:

| Resource | Where |
|---|---|
| Stripe OpenAPI spec (`spec3.json`, `spec3.sdk.json`) — the machine-readable ground truth for every field, type, and enum | https://github.com/stripe/openapi |
| `stripe-mock` — Stripe's own OpenAPI-driven mock server. The closest prior art. Read its source | https://github.com/stripe/stripe-mock |
| Stripe API reference | https://docs.stripe.com/api |
| Idempotent requests | https://docs.stripe.com/api/idempotent_requests |
| Pagination | https://docs.stripe.com/api/pagination |
| Expanding objects | https://docs.stripe.com/api/expanding_objects |
| Errors, and the error code list | https://docs.stripe.com/api/errors · https://docs.stripe.com/error-codes |
| API versioning | https://docs.stripe.com/api/versioning |
| Testing, and the magic card numbers | https://docs.stripe.com/testing |
| Test clocks | https://docs.stripe.com/billing/testing/test-clocks |
| Sandboxes | https://docs.stripe.com/sandboxes |
| Billing / subscription lifecycle | https://docs.stripe.com/billing/subscriptions/overview |
| Prorations | https://docs.stripe.com/billing/subscriptions/prorations |
| Invoice lifecycle and statuses | https://docs.stripe.com/invoicing/overview |
| Revenue recovery / smart retries (dunning) | https://docs.stripe.com/billing/revenue-recovery/smart-retries |
| Events and webhooks | https://docs.stripe.com/api/events · https://docs.stripe.com/webhooks |
| Stripe Agent Toolkit + official MCP server — **the tool surface real agents are given today** | https://github.com/stripe/agent-toolkit · https://docs.stripe.com/agents · https://mcp.stripe.com |
| `stripe-python` — reference client behavior | https://github.com/stripe/stripe-python |
| Stripe CLI | https://docs.stripe.com/stripe-cli |
| Seahaven's own bundled docs and ProjectTracker | `vendor/seahaven` |

The research phase should answer, in writing, at least:

1. **What is the minimum closed set of objects** that makes the in-scope slice coherent? Which
   `expand`-able references cross the scope boundary, and what do we do at each edge?
2. **What does the Stripe MCP server actually expose,** and how does that differ from the raw API?
   That tells us what tool surface real agents see today.
3. **Which parts of the behavior are not in the OpenAPI spec** — proration math, invoice state
   transitions, subscription status transitions, dunning retry schedules, when an invoice
   auto-finalizes, what `has_more` does at a boundary. These are the hard, high-value parts and they
   live in prose docs and observed behavior, not the schema.
4. **What Stripe's test mode is and is not.** Which behaviors are testmode-only, what the magic card
   numbers do, what a test clock does and what its API looks like.
5. **How `stripe-mock` handles all of this,** and precisely where it stops. (Expectation: it is
   OpenAPI-shaped and largely stateless — it does not model subscription lifecycles or money moving.
   That gap is our thesis. Confirm it rather than assuming it.)
6. **Licensing and naming.** What license the OpenAPI spec is under, what we may reuse, and what we
   must say about not being affiliated with Stripe.

Write findings into the research folder, cite them from the functional spec, and link the specific
doc page next to every non-obvious behavior we implement.

## 6. Testing

This world's value is entirely its fidelity, so testing is a first-class deliverable, not a chore
at the end.

**Follow Seahaven's testing conventions.** Use its pytest plugin and its `world` / `instance`
fixtures. Match ProjectTracker's test layout: a test module per tool module, plus tests for
determinism, pagination, declared errors, fixtures, and the error handler. Read
`testing.md` in the vendored docs first.

Beyond that, this world needs three kinds of test ProjectTracker does not have:

1. **Schema conformance against the OpenAPI spec.** Generated, not hand-written. Every object we
   return should validate against its `spec3.json` schema: field names, types, enum values,
   `object` discriminator, nullability. This catches a whole class of drift for free and should be
   built early, because it shapes everything after it.
2. **Behavioral conformance against the real API.** See §8.
3. **Invariant tests.** The properties that make this gradeable: the balance ledger sums correctly,
   a charge is never refunded beyond its amount, an idempotent retry produces exactly one object,
   an invoice's lines sum to its total, a subscription's periods never overlap or gap. These are the
   same queries an eval's reward function will run, so writing them is dual-use.

Deterministic-by-construction is a hard requirement: every id from `ctx.ids`, every timestamp from
`ctx.clock`, no wall-clock reads anywhere. Stripe ids are prefixed and structured (`cus_`, `pi_`,
`in_`, `sub_`) — they need to *look* right and still be seeded.

## 7. The Seahaven improvement deliverable

**Do not "get it working at any cost."** Half of this project's value is the friction report, and a
clean-looking world built on top of hidden workarounds destroys it.

Keep **`SEAHAVEN_FINDINGS.md`** in this repo, written as we go. One entry per finding, dated, with:
what we were trying to do, what we expected, what happened, a minimal reproduction where one exists,
and a category:

| Category | Example |
|---|---|
| **Bug** | The framework does the wrong thing, or the docs and the code disagree |
| **Missing capability** | Something a faithful Stripe world needs that no seam provides |
| **Design concern** | It works, but the shape will hurt the next world — or ours at scale |
| **Ergonomics** | "This took forty lines and should have taken four." Counts fully |
| **Docs gap** | The docs did not say this, said it in the wrong place, or said it wrongly |
| **Error message** | The failure did not tell an AI author what to fix |
| **Performance** | Something got slow at fixture size or instance count |
| **Good** | Something that worked notably well. Worth knowing what to protect |

Ergonomics, docs and error messages are not lesser findings. Seahaven's stated goal is to be built
for AI authors, and this project is the first honest measurement of whether that is true. If the
authoring agent had to guess, guessed wrong, or read three files to learn one thing, that is the
finding.

Rules while building:

- **A workaround must be labelled** with a comment linking to its finding, so it can be deleted
  later.
- **Never paper over a framework bug silently.**
- **Log it when it happens.** A finding reconstructed at the end has lost the thing that made it
  useful: what it actually felt like not to know.

At the end of the project, the findings log is distilled into a **written recommendations document**
— the second deliverable. Not a bug list: a prioritized argument about what Seahaven should change,
what it should document, what it got right, and what the next world author will hit. Findings worth
fixing before this world ships get filed as issues on the Seahaven repo as we go, not at the end.

## 8. Conformance against the real Stripe API

I can supply a **Stripe test-mode API key** for the implementation phase. The world should be
validated against the real thing, not against our reading of the docs.

The approach, to be designed properly in architecture:

- **Record, then replay.** Run each scenario once against real test mode, record the request /
  response pairs as cassettes, and commit the cassettes (redacted). CI then replays cassettes
  against our world and diffs. **CI must never need a Stripe key and must never make a network
  call.** Only re-recording does.
- **Diff with a declared allow-list of differences.** Our world will legitimately differ on ids,
  timestamps, `request_log_url`, livemode flags and similar. Every permitted difference is declared
  once, in one place, with a reason. Anything undeclared is a test failure. The allow-list is a
  design document in its own right — it is the precise statement of how faithful we actually are.
- **Pin the API version.** Stripe's response shape depends on `Stripe-Version`. Pick one, record it
  in the world's metadata and in every fixture, and send it on every recorded request.
- **A re-record command, and a drift job.** `just record` / `uv run ...` — one command that
  re-records everything against a fresh key. Ideally a manually-triggered job that re-records and
  reports the diff, so we can tell when Stripe has moved.
- **Secrets discipline.** The key is never committed and never printed. Cassettes are scrubbed of
  keys, real emails and account identifiers before they land in git. Nothing in this repo touches a
  live-mode key, ever.

Scenarios worth recording, at minimum: create a customer and charge them; a declined card; an
idempotent retry of a create; a subscription created, upgraded mid-cycle, and cancelled; an invoice
finalized and paid; a partial refund and then an over-refund attempt; a dispute; pagination past a
page boundary; an `expand` two levels deep; and a handful of deliberate 400s to pin the error
envelope.

## 9. Deliverables

**The two headline deliverables are co-equal:**

- **A.** The Stripe world — package, fixtures, tests, conformance harness, evals.
- **B.** `SEAHAVEN_FINDINGS.md` plus the distilled recommendations document of §7. Shipping A
  without B is a failed project.

In full:

1. The world package, with fixtures and its `AGENTS.md`.
2. Fixtures: `empty`, plus a small one (a new SaaS account, tens of customers) and a large one (a
   scaled account — thousands of customers, real churn, failed payments in dunning, disputes, a
   mix of plans). Built by a committed `fixtures_src/generate.py`, per Seahaven convention.
3. The test suite of §6 and the conformance harness of §8.
4. **A set of eval tasks with SQL reward functions** — ten to twenty, committed, each with a natural
   language task, a starting fixture, and a grader that reads final state. This is what turns the
   repo from a mock into a demonstration. The double-charge-on-retry task is the headline.
5. `SEAHAVEN_FINDINGS.md`, written continuously, and the recommendations document distilled from it.
6. Issues filed against the Seahaven repo for findings worth acting on.
7. A README that a person can read in five minutes and understand both Stripe-in-a-box and why
   Seahaven makes it possible.

## 10. Non-goals

- Full Stripe API coverage.
- Moving real money, touching card networks, or anything PCI-adjacent.
- Client-side anything: Elements, Checkout, Payment Links, the Dashboard.
- Delivering webhooks over HTTP. Model the `events` object as queryable state; delivery is the
  harness's problem, and Seahaven does not deliver.
- Being a drop-in replacement for `stripe-mock` in someone's existing test suite. Related, not the
  goal.
- Claiming any affiliation with Stripe.

## 11. Constraints

- Python 3.14+, uv, ruff, ty, pytest — matching Seahaven.
- Depend on the vendored Seahaven in `vendor/`, not on PyPI.
- MIT licensed. No copyleft in the runtime closure.
- No real customer data, ever. Every fixture is synthetic.
- The large fixture must still fork in milliseconds. If fidelity and fork cost fight, say so before
  choosing.

## 12. Open questions for me

These need answers before the functional spec is written:

1. **Tool surface shape.** One tool per API operation (`create_payment_intent`), or a single generic
   `stripe_request(method, path, params)`, or both? The official Stripe MCP server is
   one-tool-per-operation over a small subset; production agents increasingly hit the raw HTTP API.
   This changes the entire tool list and what the evals measure. Research should inform it; I'll
   decide.
2. **Naming.** Do we ship a world literally named `stripe` with Stripe's real field names and error
   codes, or a same-shape world under a neutral name? Real names give us the pretrained-familiarity
   benefit that is most of the point, and conformance testing only makes sense against real shapes.
   Seahaven's own framework repo has a constraint against mimicking a real product's names and error
   text — that constraint is about *its* reference world, and this repo is deliberately taking the
   other side. I want that decision made explicitly and stated in the README, with whatever
   non-affiliation language the research phase says we need.
3. **Time.** Stripe has test clocks; Seahaven has a frozen clock and no time advance in V1. Do we
   model a `test_clock` object and advance time within an instance, or hold everything at one
   instant and bake time-dependent state into fixtures? Subscriptions, dunning and trials are
   substantially less interesting without time moving. This is likely the biggest framework finding
   the project produces, so treat it as a research and design question, not an implementation
   detail.
