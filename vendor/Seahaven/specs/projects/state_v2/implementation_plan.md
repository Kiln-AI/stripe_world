---
status: complete
---

# Implementation Plan: State Format (v2)

Ordered so that every phase leaves CI green on `main`: the new machinery lands beside the old,
the callers move, then the old is removed, then the docs, then the measurement. Details are in
`functional_spec.md` (FS) and `architecture.md` (ARCH); this is the checklist.

The first attempt is on branch `claude/happy-allen-inkfcc` (tip `07fc86a`). Each phase below says
what it takes from there and what it does not. "Reference" means read it for the shape and the
review-settled details, then write it against `main`; nothing is cherry-picked or merged, and
anything on that branch that assumes an instance is one store is wrong here.

## Phases

- [x] **Phase 1: The change log and the call log, per node.** `LogRecord` with `world`,
  `CallRecord`, `render_log` with the node path and the rendered-value sort, `tracked_tables`,
  `open_session`, the infinity mapping (ARCH §2.1, §3.4, §3.5); `NodeRuntime.tracked` and
  `.columns`, one session per node per call in `_dispatch` and per `bulk()`, `_logging_call`, the
  ordinal as `main`'s dispatch already gives it, `change_log()`, `call_log()`, `call_count`
  (ARCH §2.2, §3, §4); `seed` narrowed to `int | None` (ARCH §7). `changes()` and the long-lived
  sessions stay for now: the fold test's oracle is the per-node session of its own, and on a
  one-node world `changes()` is a second, independent cross-check until phase 4 removes it.
  Composite cases on `tests/worlds/emporium`. Tests: `test_changes.py` additions,
  `test_change_log.py`, `test_call_log.py`, `test_fold.py` with `tests/fold_support.py` and
  `tests/fold_oracle.py`, `test_ids.py`, the pytest-marker seed test, the "no database work in
  `state()`" precursor on `change_log()`.
  *Reference:* the old branch's phase 1 and its review rounds for `render_log`, the blob sort
  order, `_recording`'s transaction ordering and the test shapes. *Not reference:* its
  `_recording` (one session on one connection) and its `_call` restructure (`main`'s dispatch
  differs and already gives FS §7).

- [x] **Phase 2: Formats and the world pin.** `seahaven/state.py` with `envelope` (composition
  and `fixture.nodes` keyed by path), `document`, the three built-ins and name validation
  (ARCH §5); `World(state_format=)` required and stored as `pinned_state_format`,
  `RESET_ARGUMENTS`, `world.state_format()` without `bump()`, `resolve_state_format`, `copy()`
  (ARCH §5.2); `Instance.state()` with the thread-keyed formatting guard and the transaction
  refusal (ARCH §5.3); `InstanceManager.create` with `state_format`, `episode_id`, per-keyword
  startup serialisation and `fixture_files` (ARCH §6); the scaffold template; the sweep over every
  `World(...)` and every world-building helper on `main`, the six `tests/worlds/` worlds included
  (ARCH §10). Tests: `test_state.py` including the root-decides cases on `emporium`,
  `test_world.py` additions, scaffold tests, `tests/state_v1.schema.json`.
  *Reference:* the old branch's phase 2 whole -- `state.py`, the guards, the registration verb,
  the aliasing fixes and their tests. *Not reference:* its envelope (single world, single file)
  and its sweep list (`main` added twelve sites and four helpers since).

- [x] **Phase 3: OpenEnv.** `reset(state_format=, episode_id)` through the manager, the `state`
  property before and after `reset`, `SeahavenState` typed over the envelope with `WorldRef`,
  `NodeRef`, `FixtureRef` and `FileRef`, `_composition()` and `_episode_id` removed, `main`'s
  `step()` and `_listing()` kept, client docstring (ARCH §9). Tests: `test_env.py` state tests
  rewritten, `test_client.py`, and the WebSocket confirmation in `test_server.py` (FS §9). **This
  phase is the gate:** if the document does not arrive whole over the wire, to the typed client
  and to the stock one, stop and report before anything else is built on it.
  *Reference:* the old branch's phase 3 whole; the transport did not change and its gate tests
  carry over with the new fields added. *Not reference:* its `SeahavenState` field list.

- [x] **Phase 4: Remove the old surface.** Delete `Instance.changes()`, `Change`, `render()`,
  `start_session`, `NodeRuntime.session` and `controller_changes`; deprecate `controller_run_sql`
  with `skip_file_prefixes` (ARCH §1, §2.1, §8, §13); drop the fold test's `changes()`
  cross-check, leaving the per-node oracle; move every caller `main` has per FS §11's table --
  fifty `.changes()` sites across sixteen files, `tests/test_composite_changes.py` first -- and
  reword the source comments that describe the session; `bench/recording.py` retargeted at
  `instance._runtime` with its two tests (ARCH §14), the probe only, no numbers yet. Docs are
  touched only as far as keeping the docs test green requires. Every suite green: framework,
  `worlds/projecttracker`,
  `extensions/seahaven-xmlrpc`.
  *Reference:* the old branch's phase 4 for the deprecation's attribution and its test, the
  `DEPRECATED` set, and the probe's leg design and report prose rules. *Not reference:* its caller
  list (`main`'s is different and larger), its `BACKLOG.md` edits (the file is gone), and its
  measured numbers.

- [x] **Phase 5: Documentation.** `state.md` under `AGENTS.md`'s "Docs style" with a table of
  contents, in FS §12's case order; the state section in `serving_and_openenv.md`;
  `composition.md`, `db_schema_and_fixtures.md`, `testing.md`, `concepts.md`, `authoring.md`,
  `index.md`, `reference/api.md`, `reference/cli.md`'s one line, `reference/lints.md`'s SH101
  sentence, the README example, the scaffold's `AGENTS.md.tmpl` reading list, `PAGES` and the two
  guards (FS §12, ARCH §12). Every example executes under the docs test; no doc mentions
  `controller_` except `cli.md`'s one line, and none mentions `changes()`. No cost figure is
  quoted yet.
  *Reference:* the old branch's `state.md` for its structure and its executed examples, and its
  review rounds for the sentences that were wrong. *Not reference:* its page set, its style, or
  anything it wrote into `serving.md` or `BACKLOG.md`.

- [x] **Phase 6: The cost, measured.** Second priority, deliberately last. Run
  `bench/recording.py` on ProjectTracker's write mix and read workloads and on `emporium`; record
  the numbers in the phase plan and in `state.md` as approximate, with the exact command and its
  flags, and no figure the probe does not produce (ARCH §16). If a number is surprising, report
  it; do not tune the design in this phase.
  *Reference:* the old branch's phase 4 plan for how the previous numbers were taken and what
  went wrong in reporting them three times.
