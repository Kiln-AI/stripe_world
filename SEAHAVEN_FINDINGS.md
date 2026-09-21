# Seahaven Findings

The friction log for building this world, and one of the project's two co-equal deliverables
(`specs/projects/stripe_world/project_overview.md` §9). It records what building a faithful Stripe
world taught us about Seahaven — bugs, missing seams, design concerns, doc gaps, ergonomics, error
messages, performance, and things that worked notably well.

**Rules this file is kept under**, from §7 of the project overview:

- Written continuously, as things happen. A finding reconstructed at the end has lost the thing that
  made it useful: what it actually felt like not to know.
- Every workaround in the codebase carries a comment linking to its entry here, so it can be deleted
  when the finding is fixed.
- A framework bug is never papered over silently.
- Ergonomics, docs and error-message findings are not lesser findings. Seahaven's stated goal is to be
  built for AI authors, and this project is the first honest measurement of whether that is true.

Each entry gives: what we were trying to do, what we expected, what happened, a minimal reproduction
where one exists, and a category (Bug, Missing capability, Design concern, Ergonomics, Docs gap,
Error message, Performance, Good).

Framework paths below (`src/seahaven/…`, `pyproject.toml`) are paths inside Seahaven's own
repository, `github.com/Kiln-AI/Seahaven`, which this world depends on at the commit pinned in
`[tool.uv.sources]`. The framework used to be vendored at `vendor/Seahaven/`; entries written then
have been repointed, and the line numbers they cite are the ones the entry was written against.

At the end of the project this log is distilled into a prioritized recommendations document. Entries
worth acting on before this world ships are filed as issues on the Seahaven repo as we go.

## Entries

The first five entries come from the research phase, logged by the agent that read the framework and
ran it (ProjectTracker's full suite, `seahaven check`, and two purpose-written probe scripts). Their
long-form reproduction detail and the probe scripts live in
[`specs/projects/stripe_world/research/stripe-billing-and-payments/seahaven-capabilities/`](specs/projects/stripe_world/research/stripe-billing-and-payments/seahaven-capabilities/).

---
---

### Entry 1 — Locked `pydantic` version crashes `import seahaven` under the only Python 3.14 this sandbox could obtain

**Category**: Bug (docs and code disagree with the actual dependency resolution) / Docs gap
(the workaround is documented, but only in a benchmark results file, not in the setup path a new
author actually follows).

**What I was trying to do**: Set up the framework to run its own test suite and CLI, per
`project_overview.md` §3's instruction to build this project "the Seahaven way" and per
`authoring.md`'s own scaffolding instructions (`seahaven new`, then `uv sync` / install from
checkout).

**What I expected**: `uv sync` (or `uv sync --extra serve`, since I also wanted `seahaven serve`)
against the framework's own `pyproject.toml`, using whatever the lock file specifies, would produce
a working `import seahaven`.

**What happened**: Seahaven's `pyproject.toml:15` requires `requires-python = ">=3.14"`. This
sandbox had no Python 3.14 at all; `uv python install 3.14` resolved to `cpython-3.14.0rc2` (a
release candidate, not a final release — there was no way to obtain a final 3.14 in this
environment). With that interpreter and the lock file's `pydantic==2.13.5`, `uv sync --extra serve`
completed successfully, but the very first `import seahaven` (or `uv run seahaven --help`, or
running any test) crashed:
```
File ".../pydantic/_internal/_typing_extra.py", line 481, in eval_type_backport
    assert isinstance(value, typing.ForwardRef)
           ~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^
AssertionError
```
raised from `seahaven/fixtures.py:83`'s `NodeMeta` pydantic model, itself imported by
`seahaven/__init__.py:30`. `seahaven check` could not even start, because the crash happens at
import, before `seahaven.cli.main` runs a single line.

Downgrading with `uv pip install "pydantic==2.12.3"` fixed it immediately: all 259 ProjectTracker
tests then pass (`uv run --no-sync pytest worlds/projecttracker -q`), and `seahaven check` reports
zero findings.

The framework's own `bench/results/latest.md:2-8` documents an **identical, previously-known**
version of this problem: "Sections 1-6 were measured on CPython **3.14.0rc2** with a hand-patched
`pydantic` 2.12.3 in place of the locked 2.13.5 — the only way the suite ran at the time." That note
claims the incompatibility is now fixed on a final 3.14 build ("The repository now builds on a
final 3.14 against the lock, unpatched"), but I could not verify that claim, because this sandbox
could not obtain a final 3.14 interpreter to test against — only the same `3.14.0rc2` the bench note
describes as broken. So from where a new author actually starts (whatever Python 3.14 build their
environment can actually get), the failure is live and the documented "this is now fixed" claim did
not hold in this environment.

**Minimal repro**:
```sh
git clone https://github.com/Kiln-AI/Seahaven.git && cd Seahaven
uv python install 3.14   # resolves to 3.14.0rc2 in an environment with no final 3.14 available
uv sync --extra serve     # succeeds, installs pydantic==2.13.5 per the lock
uv run --no-sync seahaven --help   # crashes at import with the AssertionError above
uv pip install "pydantic==2.12.3"  # the fix
uv run --no-sync seahaven --help   # now works
```

**Why this matters for an AI author specifically**: the failure message names `NodeMeta`,
`eval_type_backport`, and a pydantic-internal assertion — nothing in it says "downgrade pydantic" or
even hints that the fix is a dependency version. An agent hitting this cold would very plausibly
spend real effort trying to fix its own world's code, or trying other Python versions, before
finding the one paragraph in `bench/results/latest.md` (a results file, not a setup doc) that names
the actual fix. This is exactly the kind of thing `authoring.md`'s "Before you run the `uv sync`..."
warning paragraph (`authoring.md:49-57`) already exists to pre-empt for the *placeholder-PyPI-package*
trap — the pydantic-version trap deserves the same treatment, in the same place, until the
underlying pin is actually fixed (which per the bench note's own framing, it should be, once a
final 3.14 + compatible pydantic combination is confirmed and the lock is updated accordingly).

**Addendum, 2026-09-19 (Phase 1).** The build environment now has a final CPython (3.14.4), and on
it `import seahaven` succeeds with the locked `pydantic==2.13.5` as well as with 2.12.3, and
ProjectTracker's full suite is green on 2.12.3 — confirming the bench note's claim that the crash is
an rc-interpreter interaction, not a version incompatibility. The pin is kept (per the plan) because
it is green on both rc and final builds and nothing yet requires 2.13.x. Mechanism worth recording:
a plain uv *constraint* cannot express this pin, because the framework's own `pydantic>=2.13.5`
floor contradicts `==2.12.3` and the resolver correctly refuses; the working spelling is
`[tool.uv] override-dependencies`. A downgrade pin against a transitive dependency's own floor is
always an override, never a constraint — the error message uv gives ("no solution found") does not
name that distinction.

---

### Entry 2 — `ctx.ids` has exactly one identifier shape (UUIDv4-text); no template or registered scheme for product-shaped ids, and nothing catches a tool that reverts to it by mistake

**Category**: Missing capability.

**What I was trying to do**: Determine whether `ctx.ids` can mint Stripe-shaped prefixed ids
(`cus_...`, `pi_...`) deterministically, per this subtopic's assignment.

**What I expected**: Given that ProjectTracker itself needs a product-shaped id (`ENG-41`) and the
docs call this out as "the world's own business" in more than one place, I expected either a small
documented helper for "a prefixed random id from the seeded stream" or at least a worked example
somewhere in the repository of a Stripe/Linear-style prefixed id being built.

**What happened**: `ctx.ids.uuid()` is the *only* identifier primitive, hardcoded to UUIDv4 text
format (`ids.py:82-84`). `ctx.ids.random` (a seeded `random.Random`) is the documented escape hatch
for "anything else," but there is no worked example anywhere in the repository — not in
ProjectTracker, not in either of the two test-fixture worlds (`tests/worlds/payments`,
`tests/worlds/ledger`) — of building a prefixed, product-shaped id on top of it. I had to write one
from scratch and verify its determinism myself (see `pressure-points.md`'s "id generation" section
and `scripts/id_test.py`). It worked immediately and cleanly once written — this is not a framework
defect, just an absent worked example plus an absent guardrail.

The guardrail gap is the sharper part: a Stripe world will have 40-60 tools, many minting ids. There
is no lint, convention-checker, or registered "this world's id scheme" concept that would catch one
tool that (by copy-paste, or because an author reached for the more familiar `ctx.ids.uuid()`
instead of the shared `stripe_id(ctx, prefix)` helper) mints a bare UUID where every other tool mints
`cus_...`. `seahaven check`'s `SH203` catches `random`/`uuid.uuid4()` misuse (reading the *wrong*
entropy source), but nothing catches minting the *right* entropy in the *wrong shape*.

**Minimal repro**: N/A — this is an absence, not a crash. See `scripts/id_test.py` for the working
alternative I built.

**Suggested fix / what Seahaven could add**: either (a) a documented, copyable snippet in
`authoring.md` or `reference/api.md` for "a product-shaped id, seeded" — even just the four-line
function I wrote — so the next author doesn't have to derive it, or (b) a small opt-in helper on
`Ids` itself, e.g. `ctx.ids.token(prefix="cus_", length=24, alphabet=...)`, since this is clearly a
recurring need (Stripe, Linear, GitHub, Slack, Notion, and most modern APIs all use prefixed opaque
ids) rather than a one-off.

---

### Entry 3 — No mechanism to advance an instance's clock; this is a structural gap for any Stripe-shaped `test_clock`

**Category**: Missing capability / Design concern.

**What I was trying to do**: Determine "whether an instance can advance time at all," per this
subtopic's assignment and per `project_overview.md` §12.3's framing of this as the single biggest
open design question for the whole project.

**What I expected**: Given the framework explicitly documents a frozen clock as a deliberate v1
choice ("Seahaven does not... move a clock forward... None of these is planned," `concepts.md:275`),
I expected to confirm this as a hard "no" rather than find a hidden escape hatch — and that is what I
found. I read `clock.py` end to end and grepped the whole framework source for `advance`,
`test_clock`, `set_now`, `travel` — no matches anywhere.

**What happened**: confirmed as expected — see the full analysis in `pressure-points.md`'s "time"
section, including what a minimal fix would need to touch (the SQL function-table overrides being
`SQLITE_DETERMINISTIC` and closure-bound to one instant at connection-open time; the "exactly one
`now` per instance" invariant baked into the fixture sidecar, the state document envelope, and —
explicitly — the composition model, `composition.md:501`: "There is exactly one `now`, because no
store has a clock of its own").

This is not a bug — the docs are honest about the scope cut — but it is the single highest-leverage
finding this research produced, because `project_overview.md` frames this exact question as the
project's biggest open design decision, and the answer is: **the framework has no partial answer to
build on here.** A Stripe world's `test_clock` tool has to be built as an ordinary world tool that
synchronously does all the time-dependent work itself in one call (viable, and the change-log
machinery handles arbitrarily many writes in one call correctly already — no framework change needed
there), *unless and until* the framework grows a way to mutate `ctx.clock` mid-instance, which it
does not have today.

**Minimal repro**: N/A — confirmed absence, not a crash.
`grep -rn "advance\|test_clock\|set_now\|travel" src/seahaven/*.py` in a Seahaven checkout returns nothing
relevant.

**Why this belongs on the Seahaven repo as an issue** (per `project_overview.md` §7's instruction to
file findings worth acting on as we go): this is exactly the kind of finding a second real-world
author would also hit, for any product with time-dependent state — subscriptions, leases, scheduled
jobs, SLAs, anything with a "this happens N days later" story. It is squarely a framework-level gap,
not a Stripe-specific one.

---

### Entry 4 — No shared pattern/helper for assembling deeply-nested tool results from flat SQL rows

**Category**: Ergonomics.

**What I was trying to do**: Determine how a world returns deeply nested objects (Stripe's
`payment_intent.charges.data[]`, `payment_intent.payment_method_details.card`, etc.) given SQLite's
flat, scalar-column storage model.

**What I expected**: Given ProjectTracker already has exactly this problem once (an issue's
`label_ids`), I expected either a documented pattern with a name, or a small reusable helper in
`seahaven.helpers` for "assemble N children onto M parents in one query pass."

**What happened**: I found the pattern (ProjectTracker's `attach_labels`, `tools/_rows.py:128-147`)
but it is bespoke, one level deep, and not extracted anywhere as a reusable shape — it is a for-loop
and a dict comprehension, written once, for one relationship. A Stripe world will need this same
pattern dozens of times, often two or three levels deep (a `payment_intent`'s nested `charges` list,
each charge's nested `payment_method_details`, each of *that*'s nested card sub-object). Nothing in
`seahaven.helpers` addresses this — `run_sql` and `describe_schema` are the only two helpers, and
neither is about result shaping. It is entirely buildable in world code (I don't believe this needs
a framework change — `json_group_array`/`json_object` in SQL, or Python-side merging identical in
spirit to `attach_labels`, both work fine within the existing model), but it will be written from
scratch, tool module by tool module, unless this project builds its own shared "row assembly"
library early — which is itself a finding worth recording in this project's own `AGENTS.md` for the
next author, separate from Seahaven's.

**Minimal repro**: N/A — this is a gap in shared tooling, not a crash. See `_rows.py:128-147` in
ProjectTracker for the one instance of the pattern that does exist.

**Suggested fix / what Seahaven could add**: not obviously a framework-level fix — this may be more
naturally "the next thing ProjectTracker or a worked example demonstrates" than a new API, since the
right shape (SQL-side `json_group_array` vs. Python-side merge) is genuinely product-dependent.
Recording it here mainly so this project's own architecture phase treats "a row-assembly module" as
a designed-in piece of the package layout from day one, rather than something 40-60 tools each
reinvent slightly differently.

---

### (Non-finding, for completeness) — `seahaven check` and the pytest plugin both worked exactly as documented once the environment was fixed

**Category**: Good.

Worth recording per `project_overview.md` §7's own instruction that positive results are "worth
knowing what to protect." Once Entry 1's pydantic pin was in place: `uv run --no-sync pytest
worlds/projecttracker -q` passed all 259 tests with no modification to anything; `uv run --no-sync
seahaven check --world projecttracker:world` reported zero findings against the shipped reference
world; and every code example quoted verbatim from the docs in this research (the `Ids`,
`Clock`, composition, and fixture-freeze examples) matched what I could independently confirm by
reading the corresponding source file. The bundled docs describe real, current behavior accurately
— nothing in this subtopic's research surfaced a docs/code mismatch beyond Entry 1's dependency
pin.

---

### Entry 6 — Middleware runs outside the per-call transaction, so a middleware that writes cannot be atomic with the call it wraps

**Category**: Design concern.

**Date**: 2026-09-19

**What we were trying to do.** Implement Stripe's idempotency keys as middleware. The middleware
stores the response a call produced, keyed by the idempotency key, and on a replay short-circuits and
returns the stored response without re-running the call. Middleware is the right seam for this
precisely because a short-circuit never reaches the tool, so a replay writes nothing and produces no
change-log records — which is exactly the property an eval grading "did this retry double-charge?"
depends on.

**What we expected.** That a middleware writing a row and the tool call it wraps would commit or roll
back together, since `authoring.md` describes one call as one transaction.

**What happened.** Reading `src/seahaven/call.py` during component design: the
per-call transaction is opened *inside* `invoke`, so it sits **beneath** the middleware chain rather
than around it. A middleware that writes is therefore not atomic with the call it wraps. For
idempotency that means the stored-response row and the state change it describes can diverge — the
call's writes commit and the key row does not, or the reverse — and the failure mode is silent and
exactly the one idempotency exists to prevent.

**Minimal reproduction.** A middleware that writes a row and then lets `next_` raise. The tool's
writes roll back with the call's own transaction; the middleware's row does not.

**Workaround taken.** The idempotency middleware opens its own `ctx.db.transaction()` savepoint
around `next_`, which restores atomicity and preserves the short-circuit-writes-nothing property.
Labelled in the code with a comment pointing at this entry.

**Why it is a finding rather than a preference.** The docs describe one call as one transaction, and
they describe middleware as the place to do cross-cutting work. Both are true, but the composition of
the two is not stated anywhere, and the natural reading is wrong in a way that only shows up under
failure. A framework that wants AI authors to get this right either wraps the chain in the
transaction, or says plainly in `authoring.md` that a writing middleware must open its own.

---

### Entry 7 — A world whose distribution name differs from its package name is invisible to the CLI's project-name heuristic

**Category**: Ergonomics (documented, mild).

**Date**: 2026-09-19 (Phase 1).

**What we were trying to do.** Name this world's distribution `seahaven-stripe-world` while its
package and world name are `stripeapi` (the functional spec fixes both), then run `seahaven check`,
`seahaven fixture freeze` and `pytest` the way the scaffold prints them.

**What we expected.** For the CLIs to find the world from the project, as `seahaven new`'s "next:"
block promises.

**What happened.** `discover`/`find_world` normalise `[project] name` to a module name and import it
(`cli/__init__.py:142`, `_package_name`), so every bare `seahaven` subcommand tried
`import seahaven_stripe_world` and failed with `ModuleNotFoundError` — a message that names neither
`--world` nor the heuristic. The override exists and is documented (`--world module:attr` for the
CLI, `--seahaven-world` for the pytest plugin, "for a layout the convention misses"), so this is a
works-as-documented note rather than a bug. The costs are real though: the override must be spelled
on every CLI invocation (it now lives in this repo's `AGENTS.md` command list) and once in
`[tool.pytest.ini_options] addopts`, and the failure mode of forgetting it is an import error that
does not point at the fix.

**Suggested fix / what Seahaven could add.** When the project-name-derived module does not exist,
look for a `[tool.uv.build-backend] module-name` (which a scaffold with this exact layout already
writes) before giving up — or name `--world` in the `ModuleNotFoundError` message the way
`_import`'s other branches do.

**Resolved here (2026-09-21) by renaming the package, not the distribution.** `src/stripeapi/`
became `src/seahaven_stripe_world/`, so the distribution keeps the descriptive, disclosing name and
the convention finds it. `--world` and `--seahaven-world` are gone from `pyproject.toml`,
`AGENTS.md` and the README.

The rename earned its keep independently, and that is the part worth carrying back. A top-level
module name is a global claim inside every venv it is installed into, and worlds compose —
`add_world` makes multi-world venvs the design rather than an edge case. A world that takes
`stripeapi` squats a short, generic, plausible name on `sys.modules` exactly as it would on PyPI,
and reads like the vendor's own library in a traceback. Namespacing both is one piece of hygiene.

**The finding still stands** for a world that wants a short package behind a namespaced
distribution, which is a reasonable thing to want: the mismatch is invisible until import time, and
the `ModuleNotFoundError` names neither `--world` nor the heuristic. Two candidate shapes, not yet
decided: a `[tool.seahaven] world = "pkg:world"` key read by `cli.discover` before the project-name
convention — backend-agnostic, names the attribute too, and both the CLI and the pytest plugin
inherit it from that one function — or making `seahaven new` scaffold the namespaced pair by
default, so the convention never has to bend. The minimum under either is an error message that
names `--world`.

---

### Entry 8 — `invoke` logs an ERROR-level traceback for every *raised* exception, including ones the world's own middleware catches and renders as ordinary results

**Category**: Design concern (ops noise).

**Date**: 2026-09-19 (Phase 3).

**What we were trying to do.** Serve Stripe errors as return values: `stripe_api_read`/`stripe_api_write` answer `{"status": 404, "body": {"error": …}}` for a missing object, a bad parameter, an unknown cursor — the ordinary, agent-expected outcomes. The world raises `StripeApiError` out of the tool body, the per-call transaction rolls back, and this world's `stripe_envelope` middleware (outside the transaction, per `authoring.md`'s own layering) catches it and renders the envelope.

**What we expected.** An exception a middleware catches and converts into a normal return is not a failure the framework needs to report; at most it is debug-level information about a handled condition.

**What happened.** `src/seahaven/call.py:162-173` (`invoke`) logs `_log.error("tool %r failed on instance %s", …, exc_info=True)` for **every** exception that is not a `ToolError` — before the middleware chain gets a chance to see it, and with no way for a world to mark an exception class as "expected, handled further out." So in this world every ordinary 400/404 — every typo'd parameter, every retrieve of a missing id, every bad `expand[]` path — writes a full Python traceback at ERROR level to the log, interleaved with real failures, for the whole length of an eval rollout. The failure mode `components/dispatcher.md` §3.8 attributes only to a *leaked* Stripe error ("would fill the log with tracebacks for ordinary card declines") in fact applies to every caught-and-rendered one too, because the log line fires at raise time, not at escape time.

**Minimal reproduction.** Any world whose middleware catches an exception subclass raised by its tools and returns a value:

```python
class Expected(Exception): ...


@world.middleware
def catcher(ctx, call, next_):
    try:
        return next_(ctx, call)
    except Expected:
        return {"handled": True}


@world.tool
def raising(ctx) -> dict:
    """Raises an exception the chain handles."""
    raise Expected()
```

`inst.call("raising")` returns `{"handled": True}`, and the log carries `tool 'raising' failed on instance …` at ERROR with a full traceback.

**Why it is a finding rather than our bug to fix.** The alternatives in world code are all worse: catching inside the tool (commits the partial writes the raise was supposed to discard — the exact hazard the boundary design exists to avoid), or demoting the log level from world code (world code cannot; the handler is `invoke`'s). What is wanted is small and framework-shaped: either a documented "expected exception" marker a middleware-handled class can carry (`invoke` skips or demotes the log for it), or the log moved to the chain's outer boundary so a middleware that converts an exception into a result also converts the log line. Until then this is a declared ops note for anyone running long rollouts against this world: ERROR-level entries with Stripe envelopes in the transcript are noise by construction, and the filter is "did the call return or raise."

**Where the code points at it.** `src/stripeapi/middleware/stripe_envelope.py`'s `except StripeApiError` branch carries a comment referencing this entry.

---

### Entry 9 — `CallRecord` logs the call but not its result, so no test hook can validate what a tool actually returned

**Category**: Missing capability (test-side).

**Date**: 2026-09-19 (Phase 4).

**What we were trying to do.** Build the schema-conformance hook the conformance component designs: after every `@pytest.mark.seahaven` test runs, walk `instance.call_log()` and validate the `body` of every `stripe_api_read` / `stripe_api_write` response in it against the pinned Stripe spec — "the resource suite *is* the corpus, automatically" (`components/conformance.md`, Public Interface).

**What we expected.** That a call log entry named "every call dispatched to this instance" would carry the call's result, the way the change log carries row images — a test-side consumer reading what actually came back is the obvious second consumer of a call log after debug rendering.

**What happened.** `seahaven.changes.CallRecord` carries `tool`, `arguments`, `error` and `tool_error` — and no result (`changes.py:96-142`). The docstring explains why: the record is shaped for the state document's wire boundary, where a result would be redundant with the observation the caller already holds. But that makes the log unusable for any after-the-fact consumer that did not intercept the call itself. Two alternatives were considered and rejected: stashing response bodies in `ctx.state` from the `stripe_envelope` middleware (the state document is a published wire boundary — every rollout's trace would carry the accumulated bodies), and a second logging list inside the world (test machinery inside the shipped package).

**Workaround taken.** The hook wraps `seahaven.Instance.call` for each test's lifetime (a `monkeypatch` in an autouse fixture, `tests/schema_conformance/capture.py`), recording `(ordinal, tool, label, body)` for the three HTTP-shaped tool names at call time and validating at teardown. Costs: the wrapper is test-side state the framework knows nothing about, and any path that produces a response without going through `Instance.call` (none exists today; a future harness could) would be invisible to the hook.

**Why it is a finding rather than our preference.** "What did this call return?" is the first question a conformance harness, a snapshot test, or a debug rendering asks, and the framework's own plugin documentation points at `inst.call_log()` as the record of what a test did. A `result` field — even opt-in, even truncated, even excluded from `to_dict` the way the workaround's records are — would make that question answerable without wrapping a framework class from test code.

**Minimal reproduction.** N/A — an absence: `grep -n "result" src/seahaven/changes.py` over the `CallRecord` block finds nothing.

**Where the code points at it.** `tests/schema_conformance/capture.py`'s module docstring and `tests/conftest.py`'s `_schema_conformance` fixture carry comments referencing this entry.

---

### Entry 10 — A world that emulates a real product can set the MCP `instructions` an agent reads, but not the server `name` it reads them from

**Category**: Missing capability (with a docs gap beside it).

**Date**: 2026-09-21 (naming pass).

**What we were trying to do.** Split this world's names by audience. The distribution, the package
and the hub card are read by humans, PyPI, a hub and a coding agent, and they should say *synthetic
Seahaven world* as loudly as possible — `seahaven-stripe-world` / `seahaven_stripe_world`. What the
tool-calling agent sees mid-rollout should feel like the product: `stripe_api_*` tool names,
`cus_…`/`pi_…` ids, Stripe's error envelopes. Two audiences, opposite goals, and no reason they
cannot both be served.

**What we expected.** That the MCP server's identity would sit on the agent-facing side of that
line, the way `mcp_server_instructions` already does. `authoring.md` makes exactly this argument
for the instruction string: *"A world that emulates a real product has this string to copy. Read
the instructions the real product's own MCP server sends, and write those… so an agent that reads
it sees the product and not Seahaven."*

**What happened.** The instructions are the author's, but the name beside them is not.
`mcp/server.py` builds `Server(name=resolved.name if resolved is not None else UNRESOLVED_NAME, …)`,
so `serverInfo.name` in the MCP handshake is `World(name=…)` — the same string that is the OpenEnv
card's name, a directory under the working root, the `world:` field in every fixture sidecar, the
seed material for a blank instance, and the default alias when another world adds this one. One
name, six jobs, two of which now pull in opposite directions: a world that names itself for
disclosure hands that disclosure to the agent it is testing, and a world that names itself for
fidelity puts the product's name on its own hub card and in its own fixture metadata.

The gap is narrow and the fix is narrow with it: `World(mcp_server_name=…)`, defaulting to
`world.name` so nothing changes for a world that does not set it. Its absence is why this repo's
world is still `World(name="stripeapi")` — renaming it to the disclosing form today would move that
string straight into the agent's handshake, which is the opposite of what the rename is for.

**Suggested fix / what Seahaven could add.** The field, and the authoring docs extended with it.
The section is already titled "Instructions for an MCP client" and already tells an emulating world
to copy the real product's instructions; it should say *name and instructions* — read the real
server's `serverInfo.name` and copy that too, for the same reason and in the same breath. A world
that emulates a product and sets only one of the two ships a handshake that half-announces itself.

**Where the code points at it.** `src/seahaven_stripe_world/world.py` carries the comment on
`name=`, and `AGENTS.md` records why the world's name has not moved yet.
