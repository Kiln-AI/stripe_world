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

**What I was trying to do**: Set up the vendored framework to run its own test suite and CLI, per
`project_overview.md` §3's instruction to build this project "the Seahaven way" and per
`authoring.md`'s own scaffolding instructions (`seahaven new`, then `uv sync` / install from
checkout).

**What I expected**: `uv sync` (or `uv sync --extra serve`, since I also wanted `seahaven serve`)
against the framework's own `pyproject.toml`, using whatever the lock file specifies, would produce
a working `import seahaven`.

**What happened**: `vendor/Seahaven/pyproject.toml:15` requires `requires-python = ">=3.14"`. This
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
cd vendor/Seahaven
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
`grep -rn "advance\|test_clock\|set_now\|travel" vendor/Seahaven/src/seahaven/*.py` returns nothing
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
