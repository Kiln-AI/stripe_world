# Seven pressure points: verdicts

> **Path note (added later).** This document was written while Seahaven was vendored in this
> repository at `vendor/Seahaven/`. It is now a git dependency pinned to a full commit SHA in
> `[tool.uv.sources]`, and the directory is gone. Read every `vendor/Seahaven/…` path below
> against `github.com/Kiln-AI/Seahaven` with that prefix dropped; the findings themselves are
> unchanged, recorded against the commit that was vendored at the time.

Each pressure point states what the framework does today (with citations), whether it suffices for
a faithful Stripe Billing & Payments world, and the options if not. Where I could run something
instead of inferring it, I did — every measurement below was produced in this research session, not
taken from the framework's own bench doc unless labelled as such.

Environment note, relevant to several verdicts below: this sandbox could only obtain Python
`3.14.0rc2` (not a final 3.14 release — `uv python install 3.14` resolved to `cpython-3.14.0rc2`),
and with the repository's locked `pydantic==2.13.5`, `import seahaven` crashes immediately
(`AssertionError` deep in pydantic's `_typing_extra.eval_type_backport`, triggered by
`seahaven/fixtures.py:83`'s `NodeMeta` model). Downgrading to `pydantic==2.12.3` — which is exactly
what the framework's own `bench/results/latest.md:2-8` says was necessary historically — fixed it.
All measurements below are from that working, pinned environment. See
[authoring-friction.md](./authoring-friction.md) entry 1 for the full repro; it is not this project's bug to
fix, but it is a real trap the next author (human or agent) hits on day one, so I did not soften it.

## id generation

**Question**: can `ctx.ids` produce Stripe-shaped prefixed ids (`cus_`, `pi_`, `in_`, `sub_`)
deterministically, or is its format fixed?

**What the framework does today**: `ctx.ids.uuid()` is fixed-format. Its full implementation
(`vendor/Seahaven/src/seahaven/ids.py:82-84`):
```py
def uuid(self) -> str:
    """A UUIDv4-shaped identifier drawn from the seeded stream."""
    return str(uuid.UUID(int=self.random.getrandbits(128), version=4))
```
No prefix argument, no alternate format, no way to ask it for anything but a canonical
`8-4-4-4-12` UUIDv4 string. The docs are explicit that this is deliberate: "Product-shaped keys
(`ENG-13`, a sequential invoice number) are the world's own business, built on `ctx.ids.random` or on
the world's own tables, and this is the stream they draw from" (`reference/api.md:280-281`,
identical wording in `ids.py:74-76`). I grepped the whole repository (framework source, all worlds
under `worlds/` and `tests/worlds/`) for any existing prefixed-id pattern: **there is none**. Every
tool in ProjectTracker, the `payments` test world, and the `ledger` test world calls
`ctx.ids.uuid()` verbatim for its primary key (e.g. `tests/worlds/payments/src/payments/tools/
charges.py:14`: `"id": ctx.ids.uuid()`).

**Does it suffice?** Not directly — but the seam underneath it does. `ctx.ids.random` is a
`random.Random` seeded per instance (`ids.py:79-80`: `self.random = random.Random(int.from_bytes
(seed))`), fully exposed and exactly what the docs point a world at for anything other than a bare
UUID. I wrote and ran a Stripe-shaped id generator on top of it in this session
(`/tmp/.../fork_bench/id_test.py`, full source below) and confirmed it is deterministic across two
separately-created instances at the same seed:

```py
def stripe_id(ctx: seahaven.Ctx, prefix: str) -> str:
    alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    return prefix + "".join(ctx.ids.random.choice(alphabet) for _ in range(24))
```
Output, two separate `world.instance(seed=7)` instances, each calling `make_customer` once:
```
a: cus_fXEc829wNH9fBEbcVCEhgaOl
b: cus_fXEc829wNH9fBEbcVCEhgaOl
OK: deterministic, Stripe-shaped id achievable on top of ctx.ids.random
```
This is a small, mechanical helper (a `stripe_id(ctx, prefix)` function in a shared `_ids.py`
module, exactly the shape ProjectTracker's `_types.py`/`_rows.py` convention already uses for
shared, non-tool-registering helpers). **Verdict: sufficient, but not out of the box.** Every one of
the 40-60 tools that mints an id needs to call this shared helper instead of `ctx.ids.uuid()`
directly, and it is easy to forget on one tool and get a UUID-shaped id where Stripe expects
`cus_...` — worth a project-specific lint or at minimum a code-review checklist item, since
`seahaven check` has no way to enforce "every id in this world matches pattern X." This gap —
`ctx.ids` offering exactly one fixed identifier shape and pushing every product-shaped id fully into
world code with no template, no registered "id scheme" concept, and no lint to catch a tool that
slipped back to `ctx.ids.uuid()` — is filed as a **Missing capability** finding
([authoring-friction.md](./authoring-friction.md) Entry 2).

## time

**Question**: exactly what the frozen clock does, whether an instance can advance time at all, and
what modelling a Stripe `test_clock` object would demand of the framework. Flagged in the plan as
the single biggest open design question — treated accordingly here.

**What the frozen clock does today**, read directly from `vendor/Seahaven/src/seahaven/clock.py`
(module docstring, lines 1-11) plus `concepts.md:159-177` and `reference/api.md:257-269`:

- One `Clock` per instance, constructed once, holding one instant for the instance's **entire
  life**. `Clock.now()` returns an aware UTC `datetime`; `Clock.iso()` returns the canonical text.
- Every SQLite connection the instance opens gets the *same* function-table override: SQLite's
  `current_timestamp`, `current_date`, `current_time`, and the `'now'` argument of `datetime()`,
  `date()`, `time()`, `strftime()`, `julianday()`, `unixepoch()`, `timediff()` are all rewired to
  return that one instant. The override substitutes the instant for `'now'` and evaluates the
  *original* function on a private helper connection with no overrides, so date arithmetic,
  modifiers and formatting stay exactly SQLite's own (`clock.py:1-11`).
- The overrides are registered `SQLITE_DETERMINISTIC` (`clock.py:44`) — meaning SQLite is free to
  cache and reuse one evaluation within a statement, which is correct precisely because the instant
  never changes mid-statement or mid-instance.
- I grepped the whole framework source for `advance`, `test_clock`, `set_now`, `travel` — **nothing
  matches**. There is no method, public or private, that changes an instance's clock after
  construction. `concepts.md:273-278` ("What Seahaven does not do") states it outright: Seahaven
  "does not… move a clock forward… None of these is planned."
- A blank instance's clock defaults to wall time at creation unless `now=` is given; freezing bakes
  whatever `now` the instance held into the fixture forever (`concepts.md:166-173`).

**Whether an instance can advance time at all: no, categorically, today.** Not "not yet configured" —
there is no seam anywhere in the object model for it. The clock is constructed once
(`Instance.__init__`-adjacent code path, confirmed by reading `clock.py` and the absence of any
setter), handed to every connection at open time, and never touched again.

**What a Stripe `test_clock` would demand of the framework, concretely.** Stripe's real
`test_helpers.test_clocks` API (out of scope for me to describe in depth — that's subtopic 4's job)
is fundamentally: create a clock object, attach a subscription/customer to it, advance it to a target
timestamp, and have every time-dependent object (invoices, dunning retries, trial expiry) react as if
real time had passed. Modelling *any* version of that inside Seahaven's current architecture requires
at minimum:

1. **A mutable point of truth for "now."** Today `now` is baked into the SQL connection's function
   table at connection-open time via a closure over one fixed instant (`clock.py`'s
   `register_clock_functions`-equivalent, read in this session). Advancing time would mean either
   (a) re-registering the override on every open connection when the clock moves — plausible, since
   connections are per-node and long-lived only for the length of a call, so a new connection
   opened after an advance would just pick up the new closure — or (b) indirecting through a mutable
   cell the existing closures read from, which is a smaller change but has to be done with care
   given SQLite's `SQLITE_DETERMINISTIC` flag: **a deterministic function's result may be cached and
   reused for the connection's lifetime by SQLite's own query planner**, so a naive mutable-cell
   approach risks stale reads inside one statement or one connection's lifetime unless the flag is
   dropped or the connection is torn down and reopened on every advance. This is a real engineering
   question, not a paperwork one.
2. **A second axis of provenance in the fixture/state model.** Today there is exactly **one** `now`
   per instance, everywhere in the object model: one field in the fixture sidecar
   (`db_schema_and_fixtures.md:144`), one field in the state document's envelope (`state.md:59,
   78`), and `composition.md:501`'s explicit statement for composite fixtures: "There is exactly one
   `now`, because no store has a clock of its own." A `test_clock` object that could be advanced
   independently per-customer (Stripe's real model — you attach a subset of customers to a given test
   clock, and other customers in the same account are unaffected) would break the "one instant per
   instance" invariant that the fixture sidecar, the state envelope, and the composition model all
   currently assume as a structural fact rather than a convention.
3. **Reproducibility under advance.** The seeded-id and seeded-`random()` streams are keyed off the
   instance seed and, under composition, the node's canonical path (`composition.md:475-480`) — not
   off time. So advancing the clock should not, by itself, disturb id/random reproducibility. That
   part of the architecture appears to generalize cleanly. What does *not* generalize cleanly is
   anything that models "N days of dunning retries happened, here are the resulting rows" —
   Seahaven has no scheduler, no background job concept, and explicitly disclaims one
   (`concepts.md:275`: "Seahaven does not put a time limit on a tool call... [or] mock a tool's
   response"). Every row in Seahaven is written by an explicit tool call under an explicit
   transaction. Modelling "time passed and N invoices auto-advanced" therefore requires either (a)
   the *tool* that advances the clock to also synchronously run all the world's own time-dependent
   business logic inline (e.g. `advance_test_clock` walks every subscription attached to that clock
   and finalizes/retries/dunning-transitions them in the same call, in Python, exactly the way a
   world's own code does anything else) — which is buildable entirely in world code with **no**
   framework change, since a tool can do arbitrary work in one transaction — or (b) something more
   invasive if the design wants time to advance independent of any single tool call.
4. **Change-log semantics under (a) above.** If `advance_test_clock` is one tool call that walks
   every affected subscription and writes dozens of invoice/dunning rows, the existing change-log
   machinery handles it for free — "a call that writes to the root and to an added node produces
   records for both, under the same `i`" and any number of table writes in one call already fold
   correctly under the documented rules (`state.md:375-429`). This is genuinely good news: **the
   log/fold machinery does not need to change at all** for option (a); only the clock's mutability
   does.

**Verdict**: the frozen clock, as built, is a genuine, structural gap for this project — not an
oversight, a deliberate v1 scope cut the docs state plainly. The **cheapest workable path** that
stays inside "call `advance_test_clock`, do all the work synchronously inside that one call" needs
exactly one new framework primitive: a way to mutate `ctx.clock`'s instant mid-instance (and
re-register the SQL-function overrides on the instance's live connections). Everything else — the
"what happens when time passes" business logic, the change-log accounting, the fixture/state
provenance for a *given* instant — can be built entirely in world code once that one primitive
exists, *provided* the "one `now` per instance" invariant is accepted as a real constraint rather
than worked around (i.e., a Stripe world would model one shared account clock advancing for the
whole instance, not per-customer independent clocks — which is in fact a legitimate, if narrower,
reading of Stripe's own test-clock model: Stripe test clocks are themselves scoped, and an instance
with one test clock attached to its whole cast of customers is a defensible design, not a
compromise). Filed as **Missing capability** ([authoring-friction.md](./authoring-friction.md) Entry 3) and
flagged per project_overview.md §7 as filing-worthy on the Seahaven repo, because this is exactly
the kind of finding that should reach Seahaven's own maintainers before this world is built around a
workaround.

## money

**Question**: integer minor units, and any decimal/rounding support.

**What the framework does today**: no native decimal type exists anywhere in the schema layer.
`STRICT` tables (mandatory — `SH101`) restrict every column to `INT`, `INTEGER`, `REAL`, `TEXT`,
`BLOB`, or `ANY` (`db_schema_and_fixtures.md:90-92`). `INTEGER` in SQLite is a 64-bit signed integer
— more than sufficient range for any Stripe amount in minor units (cents), which never approaches
`2^63`. I grepped the whole repository for any existing money-handling world or convention: **there
is none**. `tests/worlds/ledger/`, despite its name, has no amount column at all —
`CREATE TABLE entries (id TEXT PRIMARY KEY, memo TEXT NOT NULL, posted_at TEXT NOT NULL) STRICT;`
(read directly in this session) — and `tests/worlds/payments/` stores `amount INTEGER NOT NULL` with
no further handling, confirmed by reading its schema and `tools/charges.py`. Neither exercises
rounding, currency-aware minor-unit exceptions (JPY has no minor unit; some currencies use 3 decimal
places), or split-tender math.

The one adjacent code detail worth flagging: `decimal.Decimal` is **not** in the framework's list of
refused tool-argument annotation types (only `datetime`/`date`/`time`/`Enum` are refused,
`authoring.md:183-186`), and pydantic can build a JSON schema for `Decimal`. But a tool **result**
containing a raw `Decimal` would fail Seahaven's "JSON-serialisable data" contract for results
(`authoring.md:249-252`) at the standard-library `json.dumps` boundary, since `Decimal` is not
natively JSON-serializable — I did not test this directly (it's a straightforward inference from
`json` module behavior, not a claim I verified by running code), but it means `Decimal` is a trap to
avoid regardless.

**Does it suffice?** Yes, cleanly — and better than "sufficient," this is a case where the
framework's constraints and Stripe's own real design coincide exactly. Stripe's actual public API
represents every amount as an integer in the currency's smallest unit (cents for USD) precisely to
avoid float/decimal rounding problems — this is Stripe's own documented convention, not something
this project has to invent. `INTEGER` columns holding minor-unit amounts, with all arithmetic done
in Python `int`, is both idiomatic Seahaven (matches `STRICT`'s type discipline, matches
`payments`/`ledger`'s existing `amount INTEGER` pattern) and idiomatic Stripe. **Verdict: sufficient
as-is, no framework gap.** The only real work is the world's own proration/rounding *arithmetic*
(subtopic 3's territory, not this framework's), and ensuring every tool boundary passes `int`, never
`float` or `Decimal`.

## tool count

**Question**: any limit or lint that a 40–60 tool world would trip.

**What the framework does today**: I grepped `src/seahaven/*.py` for `MAX_TOOL`, `max_tool`, and any
tool-count-related limit — **nothing matches**. `reference/lints.md`'s full 22-code table
(`reference/lints.md:23-46`) has no rule about tool count, only about individual tool hygiene
(empty description, naming collisions, unimported modules) and composition-tree shape. The one
numeric cap anywhere in the composition machinery is **125 added *stores*** — SQLite's
attached-database limit, probed live rather than hardcoded (`composition.md:707-711`) — which bounds
how many *worlds* can be composed together, not how many tools one world registers. ProjectTracker
itself registers 25 tools (`worlds/projecttracker/tools/*.py`, counted directly:
`grep -rn "@world.tool" src/ | wc -l` → 25) plus Seahaven's two SQL helpers, so the framework has no
tested precedent anywhere near 40-60, but nothing in the registration path (`World.tool`,
`reference/api.md:115`) is shaped as an array with a fixed size, a decorator with a counter limit, or
anything else that would cap it.

**Does it suffice?** As far as the framework's own mechanics go, yes — there is no hard wall. The
practical risk at 40-60 tools is not a framework limit but an **agent-facing** one this framework
doesn't address at all: tool-list size and JSON-schema token cost for the model consuming it. That
concern belongs to subtopic 5 (agent surfaces / tool-count-vs-model-performance), not to this
framework subtopic — Seahaven "does not… filter or project tools per instance"
(`concepts.md:275-276`), so a harness that wants a smaller surface per episode has to build that
itself on top of the full `inst.tools()` listing, or a host world's `tool_allow_list`/
`tool_block_list` (composition-only, not usable for a non-composed world's own tools) if the Stripe
world is itself the host being trimmed by something above it. **Verdict: sufficient at the mechanics
level — no lint or hard cap fires — but Seahaven offers no per-episode tool-surface trimming for a
standalone (non-composed) world**, which is worth noting as a design consideration if the Stripe
world ever wants to expose a smaller tool subset for a particular eval without going through
composition's allow/block lists.

## composition

**Question**: how tool prefixing works and what a host world sees.

**What the framework does today**: fully covered in
[capability-map.md §Composition and tool-prefixing](./capability-map.md#composition-and-tool-prefixing).
Summary of the load-bearing facts for this project specifically: tool names are renamed by
`tool_prefix` but **descriptions are never rewritten** (`composition.md:182-186`, `SH206`); sharing
one payments account across two composed worlds is the *default* behavior, exactly matching this
project's own stated goal of being `add_world`-ed as a shared billing subsystem
(`composition.md:194-196`); there is **no cross-world transactional atomicity** — a host's own write
and a call into the Stripe world's tools commit independently, so a host tool that charges via the
Stripe world and then fails its own write leaves the charge standing (`composition.md:322-325`); and
order of `add_world` calls is agent-observable in the flat tool list
(`composition.md:168-171`).

**Does it suffice?** Yes for the mechanism — composition is clearly Seahaven's most mature, most
heavily documented subsystem (`composition.md` is 37KB, the single largest doc in the bundle, with
eight dedicated lint codes). **The one concrete authoring constraint this project should plan
around**: if the Stripe world's own tool docstrings cross-reference other Stripe tool names by their
bare name (plausible and arguably desirable for fidelity, since Stripe's own API docs do exactly
this — "first create a PaymentIntent with `create_payment_intent`, then confirm it with..."), every
host that prefixes this world on `add_world` will trip `SH206` warnings on every such docstring. This
is not a defect, just a fact to design the docstring style around, or accept as declared
infidelity-under-composition per the docs' own framing. **Verdict: sufficient, no gap** — this is the
one pressure point where the framework is already ahead of what a first-time author would guess it
needs.

## deep JSON objects

**Question**: how a world returns deeply nested objects, and whether the framework expects flat
rows.

**What the framework does today**: the **storage layer** (SQLite, `STRICT` tables) is unambiguously
flat — every column is a scalar (`INT`/`INTEGER`/`REAL`/`TEXT`/`BLOB`/`ANY`), so any structured
sub-object has to be represented either as (a) a separate table joined at read time, or (b) a JSON
blob in a `TEXT` column, guarded by `CHECK (json_valid(...))` since a `STRICT` table "has no JSON
storage class" (`db_schema_and_fixtures.md:84`, ProjectTracker's own comment at
`schema/001_core.sql:105-107`). Full JSON1 is available for reading these back in SQL
(`json_extract`, `->`, `->>`, `json_each`/`json_tree` as tables, etc. — verified live against
`src/seahaven/sandbox.py`'s `ALLOWED_FUNCTIONS` list in this session).

The **tool-result layer**, by contrast, has no flatness constraint at all: "A result is
JSON-serialisable data: `dict`, `list`, scalars, `None`, and pydantic models and dataclasses"
(`authoring.md:249-252`) — an arbitrarily nested `dict` is a completely ordinary return value. There
is no rule anywhere that a tool must return one flat row per call. What I found is a **convention**,
not a constraint: ProjectTracker's actual pattern for the one place it needs a one-to-many nested
shape (an issue's `label_ids`, a list attached to each issue) is to run one flat query for the parent
rows, one flat query for every child row across the whole page (`_rows.py:128-147`,
`attach_labels`), and **assemble the nested dict in Python** by merging the two in memory before
returning it:
```py
return [{**issue, "label_ids": labels[issue["id"]]} for issue in rows]
```
The docstring is explicit about why this shape and not "join per row": "Reading them one issue at a
time would be that same round trip moved inside the world; this reads the whole page's labels once
and hands each row its own" (`_rows.py:129-135`).

**Does it suffice?** Yes, but it puts real weight on world-code discipline that Stripe's actual
object shapes will stress far harder than ProjectTracker ever does. A `payment_intent` object has
several levels of real nesting Stripe's API genuinely returns — `charges: {object: "list", data:
[...], has_more, url}`, `payment_method_options: {card: {...}, ...}`,
`payment_method_details: {card: {brand, last4, ...}}` — multiple levels deep, some of it itself a
paginated sub-list. Building each of these tool results means: multiple flat `SELECT`s (or one
`SELECT` with `json_group_array`/`json_object` doing the assembly in SQL, which the ALLOWED_FUNCTIONS
list permits and could be more efficient than N round-trips per page — untested by this research,
worth a spike), or Python-side merging identical in spirit to `attach_labels` but two or three levels
deep instead of one. There is no framework helper for "assemble a nested object from N flat queries"
— it is 100% world-code responsibility, tool by tool. **Verdict: sufficient — the framework places
no obstacle in the way — but there is zero framework support for it either**, and a Stripe world
with dozens of nested-object tools will likely want its own small internal library (a "row
assembly" module, analogous to ProjectTracker's `_rows.py` but generalized) to avoid each tool
module reinventing the join-and-merge pattern. This is an **ergonomics** finding worth logging
([authoring-friction.md](./authoring-friction.md) Entry 4), not a blocker.

## fixture size

**Question**: whether a fixture with thousands of customers still forks in milliseconds. Measured,
not inferred.

**Method**: I wrote a standalone two-table Seahaven world (`customers`, `charges`, structurally
similar to what a real Stripe world's smallest slice would look like — `id TEXT PRIMARY KEY`,
scalar columns, one `metadata TEXT CHECK (json_valid(metadata))` column, one foreign key, one
index) and ran it against the actual framework in this session (script at
`/tmp/claude-0/.../scratchpad/fork_bench/fork_bench.py`, full source below). For each of two sizes I
(1) built the fixture via `inst.bulk()` + `executemany` (the documented bulk-load path,
`db_schema_and_fixtures.md:200-234`) and froze it with `inst.freeze(...)`, timing the whole
build+freeze; then (2) created 50 fresh instances from that frozen fixture, each doing
`world.instance(fixture_id)` followed by one `SELECT count(*)` through `inst.inspect()` to force the
copy to actually complete and be queryable, timing each of the 50 end-to-end.

**Results**:

| Fixture | Rows (customers + charges) | Fixture file size | Fork median | Fork p90 | Fork max | Build+freeze |
|---|---|---|---|---|---|---|
| 5,000 customers × 3 charges | 20,000 | 2.25 MiB | 4.50 ms | 5.01 ms | 7.78 ms (min 4.04 ms) | 253 ms |
| 20,000 customers × 5 charges | 120,000 | 13.70 MiB | 11.50 ms | 13.35 ms | 29.07 ms (min 10.69 ms) | 1,552 ms |

**Reading**: fork cost grows roughly with file size (copy-dominated, as the docs' own framing
predicts — "Copying that file is how a run gets its own private starting state, and it takes
milliseconds," `db_schema_and_fixtures.md:23-24`), not with row count independent of file size, and
stays in single-to-low-double-digit milliseconds through 120,000 rows / 13.7 MiB. This is a shared,
unpinned, virtualised sandbox (the same caveat the framework's own `bench/results/latest.md:20-24`
states about its own numbers applies here too — do not read these as a hardware benchmark, only as
"order of magnitude, this session, this machine"), and I did not test beyond ~20,000 customers. The
project_overview.md's large fixture spec ("a scaled account — thousands of customers, real churn,
failed payments in dunning, disputes, a mix of plans") sits comfortably inside the range I measured,
and even a 10x-larger fixture than my largest test — call it ~200,000 customers with proportionally
more child rows — would extrapolate, on this evidence, to roughly 100-150ms per fork, which is still
"a run gets its own private starting state" territory for anything short of a genuinely industrial
fixture size, though I did not verify that extrapolation directly.

**Verdict: sufficient, measured, no gap.** The one thing worth flagging for the eventual
`fixtures_src/generate.py`: a real Stripe fixture will have many more tables than my two-table probe
(customers, payment_methods, subscriptions, invoices, invoice_items, charges, refunds, disputes,
balance_transactions...), and the `inst.bulk()` API is documented as "one transaction per **node**"
(`db_schema_and_fixtures.md:203`) — meaning for a single (non-composite) world, it's still one
transaction total across every table, so table count shouldn't materially change the picture; what
matters for fork cost is total file size and total row count, both of which I directly measured
scaling linearly-ish and staying small. Not independently verified: whether SQLite's checkpoint/
vacuum step inside `freeze()` scales differently with many small tables vs. two — plausible it
doesn't matter much, but untested here.

**Reproduction**: full script committed alongside this doc at
[`scripts/fork_bench.py`](./scripts/fork_bench.py) (the id-generation determinism test from the
previous section is at [`scripts/id_test.py`](./scripts/id_test.py)). Run with:
```sh
uv run python \
    specs/projects/stripe_world/research/stripe-billing-and-payments/seahaven-capabilities/scripts/fork_bench.py
```
from the repository root, against a `pydantic==2.12.3`-pinned environment per the note at the top of
this document. (When this was first run the framework was vendored at `vendor/Seahaven/` and the
script put that `src` on `sys.path` itself; `seahaven` is an ordinary installed dependency now, so
the world's own environment is enough.)
