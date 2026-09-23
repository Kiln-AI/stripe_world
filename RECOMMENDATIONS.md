# Recommendations for Seahaven

Distilled from `SEAHAVEN_FINDINGS.md`, which was written continuously while building this world
(Phases 1-22). This document groups the ten entries by priority, summarizes each, states its
current status, and gives a concrete recommendation. It is the second of the project's two
co-equal deliverables (`project_overview.md` section 7).

The findings log itself (`SEAHAVEN_FINDINGS.md`) has the full reproduction detail, the code
pointers and the narrative of what each finding felt like to hit. This document is the argument
about what to do about them.

---

## Priority 1: fix before this world ships

### Middleware-transaction atomicity (Entry 6)

**Finding.** A middleware's writes are not atomic with the tool call it wraps. The per-call
transaction opens inside `invoke`, beneath the middleware chain. A middleware that writes a row
and then lets the call raise sees its write survive while the call's writes roll back -- or the
reverse. For idempotency this means the stored-response row and the state change it describes can
silently diverge, which is exactly the failure mode idempotency exists to prevent.

**Workaround taken.** The idempotency middleware opens its own `ctx.db.transaction()` savepoint
around `next_`. This restores atomicity for this one case.

**Status.** The workaround is in place and tested. The underlying design is unchanged.

**Recommendation.** Either wrap the entire middleware chain (not just `invoke`) in the per-call
transaction, or document plainly in `authoring.md` that a writing middleware must open its own
savepoint. The current state is that the docs describe one call as one transaction, and the natural
reading of that claim is wrong in a way that surfaces only under failure. A framework that wants AI
authors to get this right needs to say something at the point where they would otherwise guess.

### ERROR-level tracebacks for handled exceptions (Entry 8)

**Finding.** `invoke` logs an ERROR-level traceback for every exception that is not a `ToolError`,
before the middleware chain has a chance to catch it. In this world, every ordinary Stripe 400/404
writes a full traceback at ERROR, interleaved with real failures, for the whole length of an eval
rollout. The log becomes unreadable at scale.

**Workaround taken.** None available in world code. The world's middleware catches the exception
and renders it as a normal return value, but the log line has already fired.

**Status.** Open. Every long rollout against this world will carry noise at ERROR level.

**Recommendation.** Either move the log line to the chain's outer boundary (so a middleware that
converts an exception into a result also prevents the log), or add a documented "expected exception"
marker that `invoke` checks before logging. The marker approach is less invasive: a middleware
registers the exception classes it handles, and `invoke` demotes the log to DEBUG for those.

### CallRecord lacks a result field (Entry 9)

**Finding.** `CallRecord` carries the call's tool name, arguments and error, but not its result.
This makes `instance.call_log()` unusable for after-the-fact validation of what a tool actually
returned -- the first question a conformance harness, a snapshot test, or a debug rendering asks.

**Workaround taken.** The schema-conformance hook wraps `Instance.call` from test code to capture
responses. This works but means the framework's own `call_log()` does not answer the question it
exists to answer.

**Status.** The workaround is in place. The framework API is unchanged.

**Recommendation.** Add a `result` field to `CallRecord` -- even opt-in, even excluded from the
state document's wire format. The call log's purpose is "what happened in this instance," and what
a call returned is the most basic part of that answer.

---

## Priority 2: fix for the next world author

### Product-shaped ids have no helper or worked example (Entry 2)

**Finding.** `ctx.ids.uuid()` is the only identifier primitive. Building a Stripe-shaped prefixed
id (`cus_...`, `pi_...`) required writing a helper from scratch over `ctx.ids.random`. The helper
is four lines and works immediately, but there is no example of it anywhere in the repository --
not in ProjectTracker, not in the test worlds. Worse, nothing catches a tool that accidentally
uses `ctx.ids.uuid()` instead of the project's shared helper: `seahaven check`'s `SH203` catches
the wrong entropy source (`random`/`uuid4()`), but not the right source in the wrong shape.

**Status.** This world has `_ids.stripe_id` and two project-local guards (a grep test and a
fixture-wide prefix assertion). The framework has no change.

**Recommendation.** Two things, ordered by value:
1. A documented, copyable snippet in `authoring.md` for "a product-shaped id, seeded" -- even the
   four-line function this world wrote. Every API this side of 2015 uses prefixed opaque ids.
2. Optionally, a small `ctx.ids.token(prefix, length, alphabet)` helper, since this is a recurring
   need, not a one-off.

### No mechanism to advance an instance's clock (Entry 3)

**Finding.** There is no way to advance time within an instance. The frozen clock is documented as
a deliberate v1 scope cut, and the docs are honest about it. But any world with time-dependent
state -- subscriptions, dunning, scheduled jobs, leases, SLAs -- is substantially less interesting
without time moving. This is the single highest-leverage finding the project produced.

**Status.** The design is unchanged. This world works around it by making all time-dependent
behavior callable explicitly (a dunning retry is a forceable call, not something elapsed time
triggers), so the frozen clock is not a blocker for the current feature set.

**Recommendation.** This is a framework-level design question, not a bug to fix. The minimum is
a `ctx.clock.advance(seconds)` that updates the SQL function-table overrides and the in-memory
clock, with composition semantics (one clock, one advance, all worlds see it). The hard parts are
the `SQLITE_DETERMINISTIC` binding and the fixture sidecar's "exactly one `now`" invariant. Worth
designing rather than patching.

### Distribution-name / package-name mismatch is invisible (Entry 7)

**Finding.** A world whose distribution name differs from its package name is invisible to the
CLI's project-name heuristic. The failure is a `ModuleNotFoundError` that names neither `--world`
nor the heuristic. This world resolved it by renaming the package to match, but the finding stands
for any world that wants a short package behind a namespaced distribution.

**Status.** Resolved in this project by renaming. The framework's error message is unchanged.

**Recommendation.** Two candidates, not yet decided:
1. A `[tool.seahaven] world = "pkg:world"` key in `pyproject.toml`, read by `cli.discover` before
   the project-name convention, so both the CLI and pytest inherit it from one place.
2. Making `seahaven new` scaffold the namespaced pair by default, so the convention never bends.

The minimum under either is an error message that names `--world` when the project-name heuristic
fails.

### No shared pattern for assembling nested objects from flat rows (Entry 4)

**Finding.** ProjectTracker has one bespoke instance of "attach children to parents" (`attach_labels`).
A Stripe world needs this pattern dozens of times, often two or three levels deep. Nothing in
`seahaven.helpers` addresses it, and there is no worked example beyond that one for-loop. This
world built its own serialization layer (`serialize/`), which works well but was written from
scratch.

**Status.** Not a framework defect. The right shape (SQL-side `json_group_array` vs. Python-side
merge) is product-dependent. This world's `serialize/` module is a worked example the next author
can read.

**Recommendation.** Document the pattern in `authoring.md` or the reference docs -- even just
pointing at this world's `serialize/` as the worked example. The absence of a name for the problem
is what costs time; the solution, once found, is straightforward.

### Pydantic version crash on rc interpreters (Entry 1)

**Finding.** The framework's locked `pydantic==2.13.5` crashes `import seahaven` under the
`cpython-3.14.0rc2` interpreter that some environments resolve to. The crash is an
`AssertionError` deep inside pydantic's type-evaluation internals, and the error message names
nothing actionable -- an agent hitting it cold would spend real effort debugging the wrong thing.
Downgrading to `pydantic==2.12.3` fixes it immediately.

**Status.** Resolved in practice. The pin is in `pyproject.toml` via `[tool.uv] override-dependencies`.
A later addendum confirmed the crash is an rc-interpreter interaction: on a final CPython 3.14.4,
both `pydantic==2.13.5` and `2.12.3` work. The pin is kept because it is green on both rc and
final builds.

**Recommendation.** Two things:
1. Document the pydantic-version trap in `authoring.md`'s setup path, beside the existing
   placeholder-PyPI-package warning. The fix is a one-line override, but the failure gives no hint
   that a dependency version is the cause.
2. Once a confirmed final-3.14 + compatible pydantic combination is validated, update the lock
   and remove the override. Until then the pin is the safer default.

---

## Priority 3: longer-term / design-level

### MCP server name was not separately settable (Entry 10)

**Finding.** A world that emulates a real product could set the MCP `instructions` an agent reads,
but not the `serverInfo.name` it reads them from. The name was derived from `world.name`, which
serves six other jobs (OpenEnv card, fixture sidecar, default alias under composition...), several
of which pull in the opposite direction from the agent-facing identity.

**Status.** **Resolved in Seahaven** at commit `8bfd3cb`. `World(mcp_server_name=...)` exists,
defaults to `world.name`, and is validated separately. The authoring docs were updated. This world
uses `mcp_server_name="stripe-mcp"`.

**Recommendation.** None; this is done. Recorded here as a positive resolution.

---

## What worked well

### The pytest plugin and `seahaven check` (Non-finding, Entry 5-adjacent)

Once the pydantic pin was in place, the entire toolchain worked as documented. All 259
ProjectTracker tests passed unmodified, `seahaven check` reported zero findings, and every code
example in the bundled docs matched the source. The bundled docs describe real, current behavior
accurately -- the only docs/code mismatch found was the dependency pin (Entry 1).

### The `ResourceSpec` engine

76 of 155 operations are generated from a declarative spec. The engine carries its weight: CRUD,
pagination, list filters, and the `deleted` stub shape are one declaration per resource rather
than one implementation. The split between generated and hand-written is clean -- a resource
declares what it is, and only its state transitions are code.

### The middleware chain as an error boundary

The layering -- error handler outermost, stripe envelope in the middle, idempotency innermost --
gives the exact semantics needed: a Stripe error raised inside the tool rolls back the
transaction, the envelope middleware catches it and renders the return value, and the idempotency
middleware's short-circuit path writes nothing to the change log. The Entry 6 atomicity gap is
real, but the *design* of the chain is sound.

### Determinism by construction

`ctx.clock` and `ctx.ids` make determinism free rather than a discipline. Same fixture plus same
seed produces byte-identical ids, timestamps and change logs with no effort from the world author.
The only hazard is reaching for `ctx.ids.uuid()` instead of a product-shaped helper (Entry 2),
which is a guardrail gap rather than a framework defect.

### Schema conformance from the spec

Generating validation from `spec3.min.json` catches drift automatically. Every object every test
returns is validated against the pinned spec -- field names, types, enum values, nullability,
the `object` discriminator. This caught real bugs (wrong enum values, missing fields, incorrect
nullability) within seconds of writing the handler, before any manual inspection.

---

## Summary

Ten entries in the findings log, each accounted for once:

- **3 entries worth fixing before this world ships** (Entries 6, 8, 9): middleware-transaction
  atomicity, ERROR-level noise for handled exceptions, and `CallRecord` missing the result. All
  have workarounds in place; none is a blocker today. The first two affect any world with an
  error-envelope pattern; the third affects any world with a conformance harness.
- **5 entries worth fixing for the next world author** (Entries 1, 2, 3, 4, 7): the pydantic
  version trap and its undiscoverable error message, product-shaped id helpers, clock advancement,
  nested-object assembly patterns, and distribution/package name mismatch. These are the things
  the next builder will hit, and they are all either absent documentation, absent examples, or
  absent error messages rather than broken machinery.
- **1 entry resolved in the framework** (Entry 10): `mcp_server_name`, shipped at `8bfd3cb`.
- **1 positive finding** (the non-finding): the toolchain works as documented once the pydantic
  pin is in place.

The framework's core -- the world/instance/fixture model, the pytest plugin, the CLI, the
change-log machinery, `seahaven check`, and composition -- is sound. The findings are at the
edges: middleware atomicity, logging, id helpers, time, and error messages. The next world will
be easier to build if those edges are addressed.
