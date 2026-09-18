# Seahaven capabilities

## Bottom Line

The vendored framework (`vendor/Seahaven`, package `0.0.1`) is real, working, and well-documented —
all 259 ProjectTracker tests pass and `seahaven check` reports zero findings once one environment
trap is worked around (see below). Five of the seven pressure points a Stripe world will apply are
already sufficient with no framework change: money (integer-cents in `INTEGER` columns matches both
`STRICT`'s type discipline and Stripe's own convention), tool count (no cap anywhere, 25 tools today
vs. 40-60 planned), composition/tool-prefixing (the most mature subsystem in the framework — sharing
one billing account across composed worlds is the *default* behavior, exactly matching this
project's `add_world` goal), deep JSON objects (no framework obstacle, just no shared helper — a
"row assembly" pattern has to be written once and reused), and fixture-fork cost (measured directly:
4.5ms median at 20,000 rows, 11.5ms median at 120,000 rows, both comfortably inside "milliseconds").
**Id generation** needs a small, self-built helper on top of `ctx.ids.random` (verified working —
Stripe-shaped `cus_...` ids are deterministic across seeds), which is fine but has no worked example
anywhere in the codebase today. **Time is the one real structural gap**: the clock is frozen for an
instance's entire life with no advance mechanism anywhere in the object model, and it's baked in as
"exactly one `now` per instance" at the fixture, state-document, and composition levels — a Stripe
`test_clock` is buildable as an ordinary synchronous tool call (the change-log machinery already
handles arbitrarily many writes per call correctly) but only if the project accepts one shared
account clock rather than per-customer independent clocks, or the framework grows a way to mutate
`ctx.clock` mid-instance.

## Key Findings

- **The environment has a live bug, not just a historical one.** Under the only Python 3.14 this
  sandbox could obtain (`3.14.0rc2` — no final release was available), the locked `pydantic==2.13.5`
  crashes `import seahaven` at the very first line. The fix (`pydantic==2.12.3`) is documented, but
  only in a benchmark results file, not in the setup path. Full repro and analysis:
  [authoring-friction.md](./authoring-friction.md) Entry 1.
- **`ctx.ids.uuid()` is fixed-format (UUIDv4 text), full stop** — no prefix, no alternate shape.
  `ctx.ids.random` (a seeded `random.Random`) is the documented, and only, escape hatch. I built and
  verified a deterministic `cus_...`-style id generator on top of it in this session
  ([scripts/id_test.py](./scripts/id_test.py)) — it works cleanly, but nothing in the repository
  demonstrates the pattern, and nothing catches a tool that reverts to a bare UUID by mistake. See
  [pressure-points.md §id generation](./pressure-points.md#id-generation).
- **There is no time-advance mechanism anywhere in the framework**, confirmed by reading `clock.py`
  end to end and grepping the whole source tree. This is the single biggest finding for
  `project_overview.md` §12.3's open question. Full analysis of what a `test_clock` primitive would
  require of the framework: [pressure-points.md §time](./pressure-points.md#time).
- **Fixture fork cost is measured, not inferred**: 4.04-7.78ms across 50 forks of a 5,000-customer/
  20,000-row/2.25MiB fixture; 10.69-29.07ms across 50 forks of a 20,000-customer/120,000-row/
  13.70MiB fixture. Script and full method: [pressure-points.md §fixture
  size](./pressure-points.md#fixture-size), reproducible via
  [scripts/fork_bench.py](./scripts/fork_bench.py).
- **Composition is the framework's most mature subsystem** and directly matches this project's
  stated `add_world` goal — sharing one payments account across composed worlds is the *default*,
  not something to configure. The one authoring constraint to design around: tool-prefixing renames
  tools but never rewrites descriptions, so cross-referencing bare tool names in a Stripe world's own
  docstrings will trip a warning lint (`SH206`) under every host that prefixes it.
- **No native decimal/money type** — but this doesn't matter, because `INTEGER`-minor-units in a
  `STRICT` column is both idiomatic Seahaven and exactly Stripe's own real convention.
- **No cap on tool count anywhere** — grepped the source for any `MAX_TOOL`-shaped limit; found
  none. The only numeric ceiling in the whole framework is composition's 125-attached-database
  SQLite limit, which bounds composed *worlds*, not one world's own tool count.

## Details

- [capability-map.md](./capability-map.md) — the full architect-facing map: package layout, all
  four registration verbs with exact signatures, every `ctx` member's precise contract, the
  declared-error mechanism and error-handler pattern, the schema/migration convention and available
  SQLite features (JSON1 confirmed, no generated-column precedent, no decimal type), the fixture
  lifecycle end to end, the change-log/state-document format family, `run_sql`/`describe_schema`,
  the pytest plugin, `seahaven check`'s 22 lint codes, and composition/tool-prefixing in full. Read
  this first if you're designing the world's package structure.
- [pressure-points.md](./pressure-points.md) — the seven pressure-point verdicts in depth, each with
  what the framework does today, whether it suffices, and options if not. The **time** section is
  the longest and most important — it lays out concretely what a `test_clock` primitive would need
  to touch in the framework (the `SQLITE_DETERMINISTIC` SQL overrides, the "one `now`" invariant in
  three different places) and what's buildable in world code without any framework change.
- [authoring-friction.md](./authoring-friction.md) — four dated, categorized entries in the exact
  shape `project_overview.md` §7 specifies for `SEAHAVEN_FINDINGS.md`: the pydantic/Python-3.14 bug
  (Bug/Docs gap), the missing id-generation template (Missing capability), the missing clock-advance
  mechanism (Missing capability/Design concern), and the missing nested-object-assembly helper
  (Ergonomics) — plus one "Good" entry recording that the framework's docs matched its code
  everywhere else this research touched.
- [scripts/fork_bench.py](./scripts/fork_bench.py), [scripts/id_test.py](./scripts/id_test.py) —
  the two runnable probes behind the measured claims above, both executed successfully in this
  session against the real framework (with the `pydantic==2.12.3` pin).

## Open Questions / Gaps

- **Whether the "final Python 3.14 fixes the pydantic incompatibility" claim in
  `bench/results/latest.md` actually holds** — I could not test it, because this sandbox could only
  obtain `3.14.0rc2`, the same build the bench note itself describes as broken. Whoever sets up the
  real project repo should verify against whatever Python 3.14 build their environment actually
  provides, and pin `pydantic==2.12.3` defensively if it doesn't.
- **Whether SQL-side `json_group_array`/`json_object` assembly is faster than Python-side
  row-merging for Stripe's multi-level nested objects** — I flagged this as worth a spike in
  [pressure-points.md §deep JSON objects](./pressure-points.md#deep-json-objects) but did not
  measure it myself; out of scope for what this subtopic needed to answer.
- **Fixture-fork cost beyond ~20,000 customers** — I measured up to 120,000 total rows / 13.7MiB and
  it extrapolates comfortably, but did not test an order of magnitude larger. Given the linear-ish
  scaling observed, I don't expect a surprise, but it's an inference, not a second measurement.
- I did not attempt to build or test a *prototype* `test_clock` mechanism inside the framework
  itself (e.g. patching `clock.py` to accept a mutable instant) — that's an architecture-phase
  decision, not a research-phase one, and project_overview.md §12.3 reserves that decision
  explicitly ("I'll decide").

## Sources

All local, under `vendor/Seahaven/` — no web access used or needed, per this subtopic's scope.

- `vendor/Seahaven/src/seahaven/docs/{index,concepts,authoring,db_schema_and_fixtures,state,
  testing,composition,extensions,projecttracker}.md` and `docs/reference/{api,cli,lints}.md` — the
  bundled framework documentation, version-matched to the installed package (`seahaven 0.0.1`).
- `vendor/Seahaven/src/seahaven/{ids,clock,tool,sandbox,fixtures}.py` and related — framework
  source, read directly wherever the docs were thin or a claim needed confirming against the actual
  implementation (id format, clock override mechanism, `run_sql`'s allowed-function list).
- `vendor/Seahaven/worlds/projecttracker/` — the reference world, read end to end: `world.py`,
  `schema/*.sql`, `errors.py`, `middleware/error_handler.py`, every module under `tools/`,
  `fixtures_src/generate.py`, `AGENTS.md`, and the `tests/` directory.
- `vendor/Seahaven/tests/worlds/{payments,ledger}/` — the framework's own small test-fixture worlds,
  checked for any existing money/id-prefix precedent (found none).
- `vendor/Seahaven/bench/results/latest.md` — the framework's own benchmark results doc, cited for
  its pydantic-version note and its (not independently re-run) recording-cost figures.
- Commands actually run in this session, against the real framework: `uv sync --extra serve`,
  `uv pip install pydantic==2.12.3`, `uv run --no-sync pytest worlds/projecttracker -q` (259
  passed), `uv run --no-sync seahaven check --world projecttracker:world` (zero findings), plus the
  two custom scripts in `scripts/`.
