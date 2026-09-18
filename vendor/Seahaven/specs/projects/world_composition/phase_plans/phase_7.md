---
status: complete
---

# Phase 7: The composite benchmark

## Overview

`architecture.md` §15 says an idle composite instance is N files, N connections and N sessions, and
that the framework's benchmark "measures one node per instance and reads as a per-node floor". That
floor is an assumption: nothing has ever measured a second node. This phase measures it, over the
committed composite tree of `tests/worlds/`, and writes the numbers into `bench/results/latest.md`.

**Never a gate**, like the rest of `bench/`: not in CI, no threshold asserted anywhere,
`tests/test_bench.py` proves the instrument runs and never a speed.

Two questions, and they are separate:

1. **What does a node cost before a call is made?** The tree is stood up, held and torn down: the
   seal, `world.instance()`, the files and bytes that leaves on disk, and `destroy()`. Measured at
   one node (`payments`), two (`shop`) and four (`emporium`), which is the whole committed ladder,
   so the increment per added node is arithmetic on three points rather than an assumption.
2. **What does a node cost a call?** The same tool, driven on a leaf instance of `payments` and on
   the `payments` node of a four-node `emporium` instance: one-row read, one-row write, and
   `settle_order`, which crosses three nodes in one call. A call opens one node's transaction
   (`call.py`), so the prediction is that a call's cost is flat in N; the pair is what turns that
   reading of the code into a measurement.

What this phase does **not** do, and the report has to say so rather than let a reader assume it:

- **It does not exercise the bound phase 1 put on the seal.** That fix was about a diamond of tens
  of nodes; the largest committed tree is four nodes with one alias, and its seal is microseconds
  whether the cost is bounded or exponential. The seal column is a number to compare a future run
  against, not evidence about that bound.
- **It does not isolate phase 2's lock-free `composition()` hit path.** Every dispatch reads it, so
  it is in every cell of the per-call table, but one load and one integer compare is far below what
  this machine can resolve. The leaf-against-composite pair is the only evidence offered about it.
- **No gate sweep, no cold cache, one thread.** A per-call cost is a single-threaded number, which
  is the argument `bench/baseline.py` already makes; the composite adds no question about the gate
  that the ProjectTracker sweep does not already ask.
- **These are the smallest worlds in the repository.** A `payments` node is one table and two tools.
  That is what makes it a floor and not a typical node, and the section says so.

## Steps

1. **`bench/composite.py`** (new). The measurement, self-contained, over `harness.py`'s
   world-agnostic pieces (`closed_loop`, `summarise`, `Summary`).

   - Put `tests/worlds/{payments,shop,emporium}/src` on `sys.path` at import, guarded by
     `if src not in sys.path`, with the same reason `tests/conftest.py` gives: a host reaches a
     world it adds by importing it, and nothing installs these.
   - `Tree`: a name, the `World`, and `nodes` read from `world.composition()` rather than written
     down. `TREES` is `payments` (1), `shop` (2), `emporium` (4).
   - `TreeCost`: `tree`, `nodes`, `stores` (`*.sqlite` files in the instance directory), `files`
     (everything, `-wal` and `-shm` included), `idle_bytes`, `seal_seconds`, `open_seconds`,
     `open_min`/`open_max`, `destroy_seconds`.
   - `CallCost`: `workload`, `tree`, `nodes`, `summary`.
   - `Composite` (the whole result): `trees`, `calls`, `repeats`, `calls_per_pass`.
   - `standing_up(trees, repeats)`: per tree, a forced reseal (`composition.bump()` then
     `world.composition()`) timed `repeats` times, then `repeats` open/destroy pairs timed
     separately, with the file counts and byte total read off the first live instance.
   - The three composite callers, each a `prepare(instance) -> Caller` closure like
     `workloads.Workload.prepare`:
     - `read`: prepare writes exactly one charge, then every call lists that account — a one-row
       read through the whole call path. `list_charges` on the leaf, `pay_list_charges` on the
       composite.
     - `write`: `create_charge` / `pay_create_charge`, one row per call.
     - `settle`: `settle_order` on the composite only — one call, three nodes, two nested handle
       calls through `ctx.worlds`.
   - `per_call(pairs, calls, repeats)`: a fresh instance per repeat, one warm-up pass of the same
     size off the clock, then `closed_loop([caller], calls)`, summarised over the repeats. The same
     discipline `runner.measure` argues for, for the same reason: an instance's write cost climbs
     with the rows it already holds.
   - `composite(*, calls, repeats)`: both halves, returning `Composite`.

2. **`bench/report.py`**. `Results.composite: Composite | None`; `_composite(results)` rendered
   **last**, as `## 7. A composite tree: what a node costs`, so no existing section is renumbered
   and no cross-reference in the prose moves. The section carries its own provenance line — command,
   UTC instant, commit, interpreter, CPU — because it is pasted into a `latest.md` whose other
   tables were measured on another build, and a table whose machine is stated two sections above and
   is not its own is a table that lies. Two tables (standing up; per call) and the derived per-node
   arithmetic, plus the four limits listed in the overview.
   `render()` drops `_method` when nothing it describes was run: it is ProjectTracker's method
   section, and a composite-only run would otherwise print it above no ProjectTracker table at all.

3. **`bench/__main__.py`**. A `composite` subcommand, also run by `all`; `--composite-calls`
   (default 400) and `--tree-repeats` (default 5); both in `QUICK`.

4. **`bench/README.md`**. The `composite.py` row in the layout table, the command, and how
   `results/latest.md` came to hold a section from a different run than its other tables.

5. **Run it and write it up.** `uv run python -m bench composite` on an idle sandbox; paste the
   rendered section into `bench/results/latest.md` immediately above `## Reading`, and add a
   `### The composite tree` subsection to the hand-written reading. The ProjectTracker tables and
   every word of the existing reading stay as they are: that document's banner says re-running them
   is a maintainer's call, and this phase is not it.

## Tests

In `tests/test_bench.py`, at toy sizes, asserting no speed.

- `test_the_trees_are_the_ladder_they_claim_to_be`: `TREES` is 1, 2 and 4 nodes, and each `Tree`'s
  `nodes` is what `world.composition()` says, not a constant.
- `test_standing_up_counts_the_files_a_node_really_costs`: a `TreeCost` per tree; `stores` equals
  the node count for each; `files` is at least `stores`; `idle_bytes` and all four timings are
  positive; four nodes leave more files than one.
- `test_the_composite_read_returns_exactly_one_row`: the read caller answers a one-row list on both
  the leaf and the composite, however many times it is called.
- `test_the_composite_write_writes_one_charge_per_call`: N calls leave N rows on the node's own
  store, read back through the instance.
- `test_settle_order_writes_to_three_nodes_in_one_call`: one call, and the order, the charge and the
  owner row are each in their own node's store — `Instance.changes()` names three worlds.
- `test_the_per_call_legs_destroy_every_instance_they_open`: the world's live-instance count is what
  it was before.
- `test_the_composite_section_carries_its_own_provenance`: the rendered section names the command
  and the commit, and holds both tables.
- `test_the_method_section_is_dropped_when_no_projecttracker_run_happened`: a composite-only
  `Results` renders no `## Method`, and a full one still does.
- `test_main_writes_a_report_and_leaves_the_gate_alone` gains `## 7.` alongside `## 4.`.
