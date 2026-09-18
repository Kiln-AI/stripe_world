---
status: complete
---

# Phase 11: benchmark and tuning

## Overview

`architecture.md` §10 asks for "a harness with two workloads (one-row read, write mix) over
ProjectTracker `agency`, run manually and before releases; results committed as
`bench/results/latest.md`; never a gate". §5.2 asks for one more thing: it says the concurrency
gate's default, `min(os.process_cpu_count() or 4, 16)`, "is a starting point, not a measurement",
and that "a later phase sweeps the value over ProjectTracker `agency` under a cold and a warm cache
and tunes it". This is that phase.

So the phase produces a measurement, not a feature, and the two things that make a measurement
worth committing are the two things it is judged on:

**Honesty.** The sweep either confirms the default or adjusts it, and the write-up says which,
including the case where the number does not flatter the framework. The decision rule is written
below *before* the harness runs, so the conclusion is not chosen after seeing the table.

**Provenance.** The machine this runs on is a shared, virtualised sandbox: four vCPUs of a Xeon,
no CPU pinning, neighbours we cannot see, and a filesystem nobody would deploy on. Numbers from it
are not the numbers anyone's hardware will produce. Every table therefore travels with its
environment, its method and its repeat-to-repeat spread, `latest.md` opens with what the figures
can and cannot be used for, and nothing is quoted to more precision than the noise supports.

The benchmark is never a gate: it is not in CI, nothing fails on a number, and `pyproject.toml`'s
`testpaths` keeps it out of the default pytest run. What *is* in the suite is a test of the harness
itself, at toy sizes, so a benchmark that has stopped working is noticed before someone quotes it.

### What is measured

1. **Baseline, single-threaded.** Each workload, cold and warm cache, one thread: the per-call cost
   and the per-process rate, against `functional_spec.md` §23's "about 3,000 one-row reads per
   second per process".
2. **Where the call goes.** The same one-row read timed three ways: through `Instance.call`, as
   its two SQL statements through `Db.rows`, and as those statements stepped on the APSW cursor.
   An approximate check on §23's claim that the framework layer and not SQLite is the per-call
   cost. Three legs and not two because `Db.rows` is framework code -- an error-translating
   context manager and a dict per row -- and a two-leg version would label it "SQLite" and book
   half the data layer to the database. Even the cursor leg is an upper bound on SQLite proper:
   the statements run on the inspection connection, whose authorizer is a Python callback.
3. **The gate sweep.** Gate size × cache state × offered load × workload, the sweep §5.2 asks for.
4. **Slow-call isolation.** A fifth measurement the sweep turned out to need: what a gate size
   costs when one session's call is slow. Without it "smaller is faster" is the whole story, and it
   is not.

### The decision rule

**What can be claimed about when this was written, and what cannot.** The shape of the results was
scouted with throwaway scripts before this plan existed — that is how the isolation probe came to
be in it at all — so the rule below was written by someone who already knew roughly what the sweep
would say. It was fixed before the *committed harness* ran and the numbers in `latest.md` come
from that harness, but this plan is one file in one commit: nothing in the tree distinguishes a
rule written before a run from one written after, and the rule should not be read as if something
did. Rule 3's threshold is the number this matters most for: "less than 30%" sits exactly at the
top of the range the scouting had already shown (the default runs at 70–81% of `n = 1`), and a
threshold chosen with that knowledge is a judgement, not an independent yardstick. It is stated
here rather than defended.

**`0` is not a candidate.** The sweep runs the ungated process as its control and scores it in
every table, but "ship without a gate" is `functional_spec.md` §13.1's question and not a tuning
outcome: §13.1 defines the default as a gate size and `0` as the operator's way to turn it off. So
a rule below that would elect `0` elects nothing, and says so instead.

1. A gate value is **better on throughput** than another if it is faster by more than the observed
   repeat-to-repeat spread, at the same offered load, on the same workload and cache state.
2. If one value is better on throughput and no worse on p50, p95 and worst observed wait,
   everywhere it was measured, it becomes the default.
3. If the axes disagree — one value wins throughput, another wins waiting — the default keeps the
   property a server needs: no session waits while others are served. A throughput difference of
   less than 30% does not buy a session a multi-second wait. Every candidate is scored against
   this, not only the one that won rule 1, and a candidate that has the property but cannot be the
   default (`0`, above) is reported rather than silently dropped.
4. If no value wins under 2 or 3, the default stands as it is, and the *reasons* recorded for it in
   `architecture.md` §5.2 and in `instances.default_concurrency` are corrected to what was measured.
   A default that survives for a different reason than the one written down is not a confirmed
   default until the written reason is fixed.

## Steps

### 1. `bench/`, a package at the repository root

Not under `src/`: it is not part of the distribution (`[tool.hatch.build.targets.wheel]` packages
`src/seahaven` alone), it is not importable by a world, and it depends on `projecttracker`, which
is a dev dependency of the framework. It is run from the repository root as `python -m bench`.

```
bench/
  README.md            how to run it, what it costs, what it is not
  __init__.py          what the benchmark is for; the one-paragraph warning
  environment.py       what the numbers were produced on
  harness.py           timing, statistics, cache control, gate control
  workloads.py         the two workloads and the slow statement
  runner.py            one measured point: N sessions, a cache state, a call count
  baseline.py          measurement 1 and 2
  sweep.py             measurements 3 and 4
  report.py            the markdown
  __main__.py          the CLI
  results/latest.md    committed output
```

### 2. `bench/harness.py`

The measurement primitives, and nothing about ProjectTracker.

```python
@dataclass(frozen=True)
class Run:
    """One measured pass: what it did, how long it took, and every latency in it."""
    calls: int
    seconds: float
    latencies: tuple[float, ...]        # seconds, in completion order, per call
    per_worker: tuple[int, ...]         # calls each worker completed

    @property
    def rate(self) -> float: ...        # calls / seconds

def quantile(values: Sequence[float], q: float) -> float
def summarise(runs: Sequence[Run]) -> Summary   # median rate, min/max rate, p50/p95/max latency
def significant(value: float, digits: int = 2) -> str   # "3.4k", "0.58", "12"
```

- `closed_loop(workers, warmup, measure)` runs `len(workers)` threads, each driving its own
  callable, releases them from a `threading.Barrier` and joins them. Closed loop, no think time:
  every worker issues its next call the moment the last returns. That is a saturation measurement
  and `latest.md` says so, because a saturation latency is not a service-level latency.
- Each worker runs a fixed number of calls, not a fixed duration, so a run is reproducible and a
  slow point cannot quietly measure fewer calls.
- `evicted(paths)` drops a file's pages from the page cache with
  `os.posix_fadvise(fd, 0, 0, POSIX_FADV_DONTNEED)` after an `os.fsync`. This is what "cold" can
  mean without root: the guest's page cache is dropped, the host's is not ours to touch, and the
  limits of that are stated in `latest.md` rather than papered over. Where `posix_fadvise` is
  missing, the cold measurement is skipped and the report says it was, instead of silently
  reporting a warm number under a cold heading.
- `gate_size(n)` is a context manager over `seahaven.instances.set_concurrency`, restoring
  `instances.concurrency()` on the way out — the same entry point `seahaven serve --concurrency`
  uses, so the sweep measures the gate an operator gets and not a private copy of one.

### 3. `bench/workloads.py`

Two workloads over `agency`, one factory each. A workload prepares an instance (reads the ids it
will drive, in a seeded shuffle) and hands back a callable that performs one call, so the harness
never knows what a tool is.

- **`read`** — one-row read: `get_issue(issue_id=...)` over all 600 issues of `agency` in a seeded
  shuffle, one call per issue before any repeats. Driving every row once is what makes the cold
  variant mean anything: a hot handful of rows would be warm after the first few calls.
- **`write_mix`** — a four-call cycle against the session's own copy: `create_issue`,
  `add_comment` on the new issue, `transition_issue` to `in_progress`, `assign_issue` to a user
  from the fixture. Four writes, each with its own transaction, its own audit row and its
  read-back; one of them mints a key, which is a write to the team's counter.
- **`SLOW_STATEMENT`** — the isolation probe's slow call: a `run_sql` over a self-join of `issues`
  that takes tens of milliseconds. It is a query an agent can write, not a sleep bolted into the
  framework.

Write runs are capped at a few hundred calls per instance and every repeat starts from a fresh
instance: per-call write cost drifts upward as an instance accumulates rows and changeset entries
(measured while scouting, ~10% over 3,000 writes), and a long run would report the drift as the
cost.

`runner.py` is not in the sketch this plan started from: it appeared when the baseline and the
sweep turned out to need one definition of "one measured pass" between them -- how many calls make
a pass, and what warm and cold do to an instance before the clock starts. Two copies of that would
be two benchmarks in one document.

### 4. `bench/baseline.py`

Measurements 1 and 2. One worker, both workloads, cold and warm, `--repeats` passes each. The
`share` measurement drives the same rows in the same order down three legs -- `Instance.call`,
the two statements (`SELECT ... FROM issues WHERE id = ?` and the `issue_labels` fetch) through
`Db.rows`, and the same two stepped on the APSW cursor -- and reports each as a percentage of the
first. The report labels the middle leg as the data layer and not as SQLite.

### 5. `bench/sweep.py`

Measurement 3: the cross product of

- gate ∈ (1, 2, 4, 8, 16, 0) — `0` is no gate, the control;
- cache ∈ (cold, warm);
- offered load ∈ (4, 32) worker threads, each with its own instance, which is how OpenEnv runs
  sessions (one instance per session, one thread each);
- workload ∈ (read, write_mix).

Repeats are whole passes over the point list in a seeded shuffle, so drift over a ten-minute run
spreads across the configurations instead of biasing whichever ran last. Each point reports the
median rate over repeats, the min–max of the rate, p50, p95 and the worst observed wait.

Measurement 4, the isolation probe: four reader threads on `read` and one thread looping
`SLOW_STATEMENT`, for a fixed few seconds per gate value, reporting the readers' rate, their
latency quantiles and how many calls each reader completed. A reader that completed one call in
three seconds is the finding; a p95 cannot show it and a median hides it.

### 6. `bench/report.py` and `bench/results/latest.md`

`report.py` renders the whole run as one markdown document and `__main__.py` writes it where
`--out` says. The committed document has, in this order:

1. **What this is and what it is not** — first, before a number: a shared virtualised sandbox, no
   pinning, one run of one commit; good for order-of-magnitude and for regressions against a later
   run of the same harness on the same box; not a hardware comparison, not a capacity plan, not an
   SLO, never a CI gate.
2. **Environment** — python (build, GIL, free-threaded or not), apsw and its SQLite, pydantic,
   `seahaven` and `projecttracker` versions, the commit, cpu model, `os.process_cpu_count()`,
   memory, the working directory's filesystem, and whether page-cache eviction was available.
3. **Method** — the workloads call by call, closed loop and no think time, what cold and warm mean
   here and what they cannot mean, repeats and ordering, and the exact command.
4. **Results** — baseline, share, sweep tables, isolation probe.
5. **Repeatability** — the spread of each cell and of a control configuration re-measured through
   the run, so a reader can see the noise floor and refuse to read differences smaller than it.
6. **Findings** — including the gate decision under the rule above, and the operator advice that
   follows from it.

Rates are printed to two significant figures and latencies to two, because a fourth digit on this
box is a fiction.

### 7. What the sweep's outcome changes in the tree

Whatever the sweep says, one of these happens and the write-up records which:

- **The default stands and its recorded reason is right.** Nothing changes but `latest.md`.
- **The default stands and its recorded reason is wrong.** `architecture.md` §5.2's paragraph and
  `instances.default_concurrency`'s docstring are rewritten to the measured reason, both pointing
  at `bench/results/latest.md`. Rule 4.
- **The default is wrong.** `instances.default_concurrency` changes, with its tests, and §5.2,
  §13.1 and `serve --concurrency`'s help text follow it.

Anything the sweep finds that is a defect in already-committed code rather than a tuning question
goes to `BACKLOG.md`, per `AGENTS.md`, rather than widening this diff.

**Deviation: §5.2 is not edited.** The second outcome above says to rewrite `architecture.md`
§5.2's paragraph. That artifact is `status: complete`, and no phase in this repository has edited a
completed spec artifact: `BACKLOG.md` B2 is the same situation -- a completed artifact carrying a
claim a phase measured false -- and it records the correction rather than taking the cascade to
`draft` that editing one brings, because the cascade is a maintainer's call. This phase follows
that precedent: the corrected reasoning goes into `instances.default_concurrency`'s docstring,
which is code, and into `bench/results/latest.md`, which is this phase's own artifact, and the
paragraph itself is recorded as B21.

### What the sweep found

Recorded here so the phase can be read without re-running it; `bench/results/latest.md` is the
document with the numbers and the argument.

- **Confirmed.** `functional_spec.md` §23's baseline (9.1k one-row reads a second single-threaded
  warm, 7.9k cold, 2.9k-4.3k saturated with 32 sessions, against a stated "about 3,000" -- one read
  cell of the sweep, the default at 32 sessions on a cold cache, sits just under it); §23's "the
  framework layer, not SQLite, as the per-call cost" (about 89% of a one-row read is Seahaven); and
  `architecture.md` §5.2's "SQLite is about 7% of a call" (the two statements are about 11% of the
  call on the APSW cursor, which is an upper bound on SQLite proper).
- **Not confirmed.** §5.2's "`n` near the core count is the measured throughput optimum". There is
  no optimum above 1 on a GIL build; the cpu-count default runs at 73-82% of `n = 1`.
- **The decision.** Rule 2 rejects `n = 1`, on the rows where it loses: with four sessions, and in
  the slow-call probe, it is the value that waits worst -- one reader served once in a
  three-second window while another, in that same window, is served 12,874 times. It runs 22-37%
  more calls a second than the default, and at 32 sessions it is better than the default on every
  axis including the worst wait; rule 2 asks for "no worse *everywhere*", so the four-session rows
  answer it. Rule 3 elects nothing, because *no* gate size has the property it names -- the
  default starves a reader in the same probe, in the same way, as `n = 1` does. So rule 4 applies:
  **the default stands and its stated reasons are corrected.** What is left of them is one
  measured reason (no value the sweep tried was better on every axis at once, everywhere it was
  measured) and one design intent recorded as an intent and not as a measurement (cpu affinity,
  for a free-threaded build the sweep says nothing about), with the unfairness stated alongside
  rather than papered over. The ungated control is scored in the write-up and in the report's
  derived table; it cannot be the default, because §13.1 defines `0` as the operator's way to turn
  the gate off.
- **Found on the way.** The gate starves a caller whenever it binds, at every size including this
  default: one three-second probe window served a reader once while serving another 12,874 times,
  because `BoundedSemaphore` lets a running thread barge in front of a woken waiter. It is not a
  tuning question and no value of `n` fixes it: `BACKLOG.md` B20.

### 8. Project wiring

- `pyproject.toml`: `bench` added to `[tool.ruff] src` and to `[tool.ty.src] include`. `testpaths`
  is left alone: the benchmark is not collected by pytest, and `tests/test_bench.py` is.
- No CI step. §10 says "run manually and before releases", and a benchmark in CI on a shared runner
  is a flaky test with a number in it.

## Tests

`tests/test_bench.py`, in the framework suite, at toy sizes -- 2 workers, tens of calls, one
repeat. They prove the harness is honest and still runs; they do not assert on performance, and no
test compares a rate to a threshold.

- `test_quantile_picks_an_element_that_was_measured` / `test_quantile_refuses_what_it_cannot_answer`
  -- p0/p50/p95/p100 on a known list, one and two elements, and the two refusals.
- `test_significant_prints_two_figures_and_no_exponent` -- `3421 -> "3.4k"`, `820 -> "820"`,
  `12.4 -> "12"`, `0.5831 -> "0.58"`: two figures, positional, never `8.2e+02`.
- `test_a_pass_reports_every_call_it_made` -- calls, latencies and per-worker counts all agree with
  the calls a counted stub actually received.
- `test_a_failing_call_fails_the_run` and `test_a_run_needs_a_caller` -- an exception on a worker
  thread reaches the caller instead of being swallowed; an empty run is refused.
- `test_a_timed_loop_measures_both_groups_over_one_window` -- the probe's two groups are measured
  separately over one window.
- `test_summarise_pools_latencies_and_keeps_rates_apart` -- the median rate is a pass's rate, the
  quantiles come from the pooled calls, and the spread is what the cell claims.
- `test_calls_per_worker_runs_whole_cycles_only` -- a pass of the write mix is whole cycles, never
  fewer than one.
- `test_the_read_workload_reads_one_issue_per_call` -- every call answers a different issue until
  the pool wraps.
- `test_the_write_mix_writes_an_issues_short_life` -- one cycle leaves the issue, the comment, the
  events and the team's counter in `instance.changes()`, and the issue is in the state the last two
  calls put it in.
- `test_the_share_statements_are_the_ones_the_world_runs` -- the comparison's SQL is still
  `get_issue`'s own, against `projecttracker.tools._rows`.
- `test_a_session_that_cannot_be_prepared_leaves_no_instance` and
  `test_sessions_are_destroyed_even_when_the_block_fails` -- no measurement leaks an instance,
  however it ends.
- `test_a_cold_pass_needs_the_page_cache_to_be_droppable` and `test_an_unknown_cache_state_is_refused`
  -- a cold pass without `posix_fadvise` raises rather than quietly measuring a warm one.
- `test_gate_size_restores_the_gate` -- the gate is what was asked for inside the block, including
  `0`, and what it was before afterwards, on both the normal and the exception path.
- `test_a_sweep_visits_every_point_and_puts_the_gate_back` -- one cell per (workload, cache,
  sessions, gate), the repeats kept, the gate restored.
- `test_the_isolation_probe_reports_both_sides` -- the readers and the slow caller are both
  reported, per reader as well as in aggregate.
- `test_the_report_carries_its_caveats_before_its_numbers`,
  `test_the_report_says_when_the_cold_runs_were_skipped` and
  `test_the_report_names_the_quickest_gate_and_where_the_default_sits` -- the caveats come before
  the environment and the environment before the method; a run that could not go cold says so and
  prints no cold table; the derived section names the quickest gate and where the default sits.
- `test_the_environment_answers_every_row` -- no row of the environment table is empty.
- `test_main_writes_a_report_and_leaves_the_gate_alone`,
  `test_main_refuses_to_overwrite_a_hand_written_reading` and
  `test_main_skips_the_cold_runs_when_it_cannot_drop_the_page_cache` -- the CLI writes the report,
  leaves the process as it found it, and will not clobber a hand-written reading without `--force`.

## What the code review changed

Six rounds. Every round but the last was about the *write-up* rather than the harness, which is
what a phase whose deliverable is a measurement should expect: the code that takes the numbers was
largely right by round 2, and the work was making the document say only what the numbers support.
The benchmark was re-run end to end three times over these rounds, because a change to the
instrument or to the report's template means the committed `latest.md` is no longer what the
committed harness produces.

Four of the six rounds found the same failure mode, and it is worth naming because it is the one
this kind of phase is prone to: **a claim tilted toward the conclusion the paragraph was arguing
for**, three times toward the shipped default and once against `n = 1`, which is the same
direction. None of them changed the decision. All of them would have made the document mean
something it had not measured.

### Round 1

Two Criticals, six Moderates, seven Milds.

- **The corrected reason written into shipped code was contradicted by this phase's own table.**
  `default_concurrency`'s docstring said the default "leaves room to overlap" a slow call, and rule
  4 said section 4 showed it. Section 4 showed the opposite: at gate 4, the default on this box, the
  probe starves a reader to a single call exactly as gate 1 does. The evidence rule 2 used to reject
  `n = 1` had not been applied to the default. The overlap claim is gone from both places and the
  docstring now states the unfairness.
- **"SQLite's share ~21%" counted Seahaven's own `Db` wrapper as SQLite** -- the leg was timed
  through `Db.rows`, an error-translating context manager and a dict built per row. So the benchmark
  refuted `architecture.md` §5.2's "about 7%" on the strength of a number that, measured on the APSW
  cursor, is about 11% and *supports* it. The share measurement now has three legs and labels the
  middle one as the data layer; the refutation was withdrawn from `latest.md` and from B21.
- Moderates: `--warm-only` printed "this platform has no `posix_fadvise`" on a machine that has it
  (the reason is now carried, not derived); the sweep's `per session` column was structurally
  constant and its caption promised a fairness signal a call-bounded pass cannot give; rule 3's
  first clause was never applied to the ungated control, which now has its own paragraph and two
  columns in section 6; the write mix's warm pass carries a pass worth of extra rows, which makes
  its cold/warm axis uninterpretable and is now said in three places; throughput and waiting are
  not independent in a fixed-call-count pass; and the pre-registration claim was restated as what
  the tree can actually support.

### Round 2

One Critical, four Moderates.

- **A reciprocal-base error, in the direction that flatters the default.** "17-29%" is the
  default's deficit *as a fraction of `n = 1`*; four places restated it as what `n = 1` gains over
  the default, which is the reciprocal. The docstring shipped the wrong one. Both bases are now
  derived mechanically from section 6 and the document says which is which; the correction also
  made two noise sentences true that had been false as printed.
- **The isolation probe pooled its reader counts across repeats**, so "one reader against another
  in the same window" was not what the instrument measured. `IsolationCell` now keeps per-window
  counts and reports the window in which the worst-served reader did least, which makes the claim
  true by construction.
- Also: a 10 + 70 split that contradicted the generated table three paragraphs above it; "beat the
  default on throughput and on waiting" where two of four rows were ties at the document's own
  precision; and "one slow tool call holds the entire process", a mechanism section 4's own two
  right-hand columns refute.

### Round 3

One Moderate, four Milds -- the fourth instance of the tilt, this time against `n = 1`.

- **`n = 1`'s waiting case was presented using only the rows where it loses.** The rejection is
  correct and rests on the four-session rows and section 4, but at 32 sessions `n = 1` is better
  than the default on every axis including the worst wait, in all four rows, and the document did
  not say so -- while an operator-facing superlative ("the worst setting for anyone unlucky") said
  the opposite of what those four rows show. Rule 2 now states both sides and the operator advice
  is scoped to the case it is true of.
- Milds: B20 quoted the no-gate row's number under the gate-8 row; one hand-written "75 ms" with no
  cell behind it; B21's reference to a superseded 21%; and `Summary.per_worker_min`/`max`, left dead
  by the per-window fix and inviting a future reader to re-introduce the pooling bug.

### Rounds 4 and 5

Round 4 verified the symmetry fix pairwise and took four prose Milds, including scoping "better on
every axis at once" in the docstring and the plan, where -- unlike in `latest.md` -- rule 2 is not
sitting above it to fix the scope.

Round 5 was convened to confirm a de-flaked test and found it still flaky: `share()` at eight calls
reduces each leg to a single ~150 microsecond window, and one scheduler preemption adds several
hundred microseconds to whichever leg catches it, so *any* cross-leg ordering at that size is a coin
toss on a busy machine -- measured at 11 violations in 40 probes under load. The test now asserts no
ordering at all: the pass ran the calls it reports, all three legs are positive, and
`fraction_of_call` divides by the call it came from. The ordering is `latest.md`'s finding, over
1,800 calls a leg, and the file's opening contract is that nothing in it asserts a speed.

### Round 6

Clean. Verified that no remaining assertion compares legs even transitively, and reproduced the
load condition rather than trusting a quiet machine: the removed orderings were violated 11 times in
40 direct probes under eight busy loops, while the new test passed 60 of 60 under the same load.
