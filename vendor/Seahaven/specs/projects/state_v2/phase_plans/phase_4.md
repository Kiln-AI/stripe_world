---
status: complete
---

# Phase 4: Remove the old surface

## Overview

The change log, the call log and the state document are built and proven (phases 1-3). This phase
takes away what they replaced: `Instance.changes()`, `changes.Change`, `changes.render()`, the
long-lived `apsw.Session` every node carried for the life of its instance, and the
`controller_changes` control tool. `controller_run_sql` survives and is marked deprecated, warning
against the caller's own line.

Every caller `main` has moves per `functional_spec.md` §11's table: about fifty `.changes()` sites
across sixteen files, plus the `controller_changes` callers and the source comments that describe
the long-lived session. Docs are touched only as far as keeping them true and the docs test green;
`phase_5` rewrites them.

`bench/recording.py` lands as well: the probe `architecture.md` §16 asks for, retargeted at
`instance._runtime` because an instance is a tree of nodes here. The probe only -- no measured
numbers, which are phase 6's.

## Steps

### The framework source

1. **`src/seahaven/changes.py`.** Delete `Change` and `render()`; drop both from `__all__`. Rewrite
   the module docstring: one mechanism, a session per node per call, no second reading.

2. **`src/seahaven/instances.py`.**
   - Delete `Instance.changes()` and the module-level `_session_of`.
   - Delete `NodeRuntime.session`; the field and the comment above it go, `tracked` and `columns`
     stay.
   - In `InstanceManager.create`, drop the `open_session(...)` that followed `tracked_tables(...)`,
     and drop the session close from the failure path.
   - In `Instance._close`, drop the session close and reword the docstring: no session outlives a
     call, so what is released here are the connections and the two read-only handles.
   - Drop `Change`, `render` from the `seahaven.changes` import.
   - Reword the module docstring and the `lock` comments: the lock is re-entrant because a control
     tool asks the instance for its control handle, not for its changeset.

3. **`src/seahaven/__init__.py`.** Drop `Change` from the import and from `__all__`.

4. **`src/seahaven/control.py`.**
   - Delete `controller_changes`, its entry in `TOOLS`, and its name from `__all__`.
   - Add the deprecation:

     ```python
     DEPRECATED = frozenset({"controller_run_sql"})
     _FRAMEWORK = (os.path.dirname(os.path.abspath(__file__)) + os.sep,)
     ```

     and in `dispatch`, before validation:

     ```python
     if call.tool.name in DEPRECATED:
         warnings.warn(
             f"{call.tool.name} is deprecated: read inst.state() instead",
             DeprecationWarning,
             skip_file_prefixes=_FRAMEWORK,
         )
     ```

     `skip_file_prefixes` and not a `stacklevel`: a control tool is reached in process, over
     OpenEnv and by calling `dispatch` directly, which are three different depths.
   - `controller_run_sql`'s docstring -- its registry description -- opens with
     `"""Deprecated: read inst.state() instead.`
   - Reword the module docstring for one tool.

5. **`src/seahaven/world.py`.** `CONTROL_TOOL_NAMES = frozenset({"controller_run_sql"})`, and the
   comments that spoke of "either name" or "the two control tools".

6. **`src/seahaven/tool.py`, `src/seahaven/sandbox.py`, `src/seahaven/db.py`,
   `src/seahaven/openenv/__init__.py`.** The comments and docstrings that name `controller_changes`
   or explain the long-lived session, reworded for a session per node per call. The SQLite session
   extension keeps its own vocabulary (`changeset`) where the text is about SQLite.

### The tests

7. **`tests/test_changes.py`.** Delete the `Change` half. Four of its cases have no `LogRecord`
   equivalent and are rewritten against `change_log()` rather than dropped: a table the world lists
   as untracked, a table with no primary key made untracked instead, writes to an FTS5 table, and a
   log that survives `freeze()`. Drop the `Change` import.

8. **`tests/test_composite_changes.py`.** Every test to `change_log()`. Three are, after the move,
   the same test as one already in `tests/test_change_log.py`'s "every node" section -- the node a
   change names, two stores of one world told apart, a failed nested call -- and are dropped rather
   than duplicated. `test_the_list_runs_in_the_compositions_canonical_order` becomes the log's own
   order, which is call order across levels. `test_controller_changes_covers_every_node` becomes
   `inst.state()` covering every node.

9. **`tests/fold_oracle.py` and `tests/test_fold.py`.** Delete `from_changes` and the
   `changes()` cross-check in `test_the_fold_of_the_log_is_the_cumulative_changeset`, leaving the
   per-node oracle as the fold's only oracle. Reword the module docstring, which said the framework
   "will not" keep such a session.

10. **`tests/test_control.py`.** `controller_changes` goes from every test that calls it. New in its
    place:
    - `controller_run_sql` warns: `pytest.warns(DeprecationWarning, match="read inst.state()")`.
    - a separate test that the warning's `filename` is this test file's own -- the attribution.
    - `controller_changes` is `UnknownTool`, in process.
    - a world may now register a tool named `controller_changes`.
    Every other test in the module calls `controller_run_sql` incidentally, so the module carries
    `pytest.mark.filterwarnings("ignore:controller_run_sql is deprecated")` and the two tests above
    turn it off for themselves.

11. **Every other framework module that calls `controller_run_sql` incidentally** carries the same
    message-scoped `filterwarnings` mark: `test_change_log.py`, `test_call_log.py`,
    `test_composite_dispatch.py`, `test_composite_inspection.py`, `test_typed_call.py`,
    `test_fts5.py`, `test_env.py`, `test_server.py`.

12. **The remaining `.changes()` callers**, each by `functional_spec.md` §11's rule:
    - `tests/test_fts5.py` (4), `tests/test_run_sql.py` (2), `tests/test_describe_schema.py`,
      `tests/test_composite_instance.py`, `tests/test_composite_fixtures.py`, `tests/test_bench.py`
      (2), `tests/test_docs_examples.py`'s `change` receiver, `tests/test_env.py`'s
      `controller_changes` assertion, `tests/test_world.py`'s two control-tool tests.
    - The two that assert on the *net of several calls* -- `test_fts5.py`'s
      "a write to a table the world never wrote" and `test_run_sql.py`'s "an update the world's own
      seeding preceded" -- take `fold(instance.change_log())` from `tests/fold_support.py`.
    - `worlds/projecttracker/tests/`: `test_sql_tools.py` (2), `test_determinism.py`,
      `test_package.py`, `test_errors.py`.
    - `extensions/seahaven-xmlrpc/tests/test_faults.py`: `_user_changes` reads `change_log()` and
      returns `list[seahaven.LogRecord]`.

### The docs

13. The minimum that keeps each page true and the docs test green; `phase_5` rewrites them.
    `index.md`, `concepts.md`, `composition.md`, `testing.md`, `db_schema_and_fixtures.md`,
    `projecttracker.md`, `serving_and_openenv.md`, `authoring.md`, `reference/api.md`,
    `reference/lints.md` and `README.md`. `reference/api.md`'s `Change` section becomes `LogRecord`
    and the receiver the docs test binds is renamed `record`.

### The probe

14. **`bench/recording.py`** (new). Each workload on one instance, recorded three ways, the legs
    alternating pass by pass:

    1. a session per node per call, as Seahaven runs it;
    2. a session per node per call, opened and attached and closed with nothing read out of it --
       the gap to leg 1 is `changeset()` and `render_log`;
    3. one long-lived session per node for the whole pass, read by nobody -- the shape before this
       release; the gap to leg 2 is what a *fresh* session costs over a warmed-up one.

    Legs 2 and 3 are not configurations Seahaven offers. They swap `instance._recording` for a
    recorder of their own, opening sessions on every `instance._runtime` node; that is bench code
    and is never importable from `seahaven`.

    ```python
    type Recorder = Callable[[int | None], AbstractContextManager[None]]
    type HasRecorded = Callable[[], bool]

    @dataclass(frozen=True)
    class Probe:
        world_name: str
        workload: str
        nodes: int
        world: World
        fixture: str | None
        prepare: Callable[[Instance], Caller]

    @dataclass(frozen=True)
    class Recording:
        world: str
        workload: str
        nodes: int
        calls: int
        per_call_seconds: float
        unread_seconds: float
        long_lived_seconds: float
        wrote_rows: bool

    def probes(world: World) -> tuple[Probe, ...]
    def recording(probe: Probe, *, calls: int, repeats: int) -> Recording
    ```

    `probes` is ProjectTracker's two workloads on `agency` (one node) and `emporium`'s three legs
    from `bench.composite.legs` (four nodes), which is `architecture.md` §16's pair of cases.

15. **`bench/__main__.py`.** A `recording` subcommand, in `all` as well, driven by `--calls` and
    `--repeats`.

16. **`bench/report.py`.** Section 8, after the composite section, carrying its own provenance for
    the reason the composite section does. It states what each leg is, a table of three rows per
    probe, and a per-probe finding. A probe that wrote no rows prints `noise` for the split rather
    than a percentage. The reference to the noise floor is printed only when this run produced one.
    `_measured_projecttracker` gains `results.recording`, which drives `agency`.
    `_composite_limits`'s "one connection and one changeset session per node" is reworded: a node
    is one connection, and its sessions live for a call.

## How the probe is reduced, and why

Settled in review, and recorded here because it decides what phase 6 publishes.

An instance's write cost climbs with its own rows, so what a leg pays for that climb is decided by
where its passes sit in the run. The leg order therefore rotates by one place per repeat, which
gives every leg the same *mean* pass position over a whole rotation -- any `repeats` that is a
multiple of three, and the default is three.

**The reduction is the mean of a leg's passes, not the median.** A median of three passes is one
pass, and the three legs' middle-ranked passes are three different positions whatever the schedule
is, so a median leaves a position bias the rotation cannot reach -- a larger one, for
`rendering`, than the fixed order the rotation replaced. The mean is exactly what the rotation
balances, so the growth cancels in it.

`recording()` refuses a `repeats` that is not a positive multiple of the leg count, because the
balance holds over whole rotations only and the report's prose states it as a fact. `QUICK`'s
`repeats` therefore goes from 1 to 3, which takes `python -m bench all --quick` from about 1.6 s to
about 4 s; nothing was traded away for it, since `--quick` is for checking that the harness runs and
a few seconds is still that.

A share of the total is reported only when the run timed the three legs in the order their
construction forces, each doing strictly less than the one above it. Otherwise the run measured its
own passes, a share would come out negative, and the table and the finding both say `noise` -- the
same treatment a probe whose calls wrote no rows already got.

**Carried to phase 6.** In the unreadable-split branch the *total* is still printed as a signed
figure, and it can itself be negative when the full leg comes out no dearer than the long-lived one
-- which contradicts the sentence beside it, the way a negative share did. Found in review here and
routed forward rather than fixed, because phase 6 is the phase that reads and publishes these
numbers.

The price is the median's resistance to one slow pass. It is worth paying here: a stray pass is
noise, which averages out across runs of the probe, while a position bias is systematic and does
not, and these three figures are published as differences from each other rather than as absolute
throughputs. `seconds_per_call` is therefore pooled -- total seconds over total calls, which is the
arithmetic mean of the passes' seconds per call, since a leg's passes all run the same number of
calls -- and it is a mean of *costs* rather than of rates, because the growth being cancelled adds
time to a call rather than scaling its rate.

## Tests

- `test_a_table_the_world_lists_as_untracked_is_not_logged` — an untracked table's writes reach no
  log record, the tracked table's do.
- `test_a_table_with_no_primary_key_can_be_untracked_instead` — the instance is created and the
  write reaches no record.
- `test_writes_to_an_fts5_table_are_not_logged` — the virtual table and its shadow tables are
  absent, the ordinary table is there.
- `test_the_log_survives_a_freeze` — records from before and after a `freeze()` are both in it,
  with their own ordinals.
- `test_the_log_runs_in_call_order_across_levels` — three levels written deepest first: the log is
  `["child/grand", "child", "main"]`.
- `test_the_state_document_covers_every_node` — `settle_order`'s records reach `state()`'s
  `state.db.log` with all three paths.
- `test_a_log_record_renders_to_a_dict_carrying_its_world` (composite) — `to_dict()["world"]`.
- `test_untracked_tables_are_each_worlds_own`, `test_an_fts5_shadow_table_is_excluded_on_the_node_that_has_one`,
  `test_the_log_is_the_net_difference_per_node` — the composite exclusions, on the log.
- `test_controller_run_sql_warns_that_it_is_deprecated` — `pytest.warns(DeprecationWarning)` with
  the message naming `inst.state()`.
- `test_the_deprecation_is_reported_against_the_callers_own_line` — the recorded warning's
  `filename` is this test module's file, not any file under `seahaven/`.
- `test_controller_changes_is_an_unknown_tool` — `UnknownTool`, in process.
- `test_a_world_may_now_register_a_tool_named_controller_changes` — registration succeeds and the
  tool is callable.
- `test_every_world_carries_the_one_control_tool` (`test_world.py`, edited) — `controller_run_sql`
  alone is flagged `control`.
- `test_a_control_tool_name_is_not_a_tool_name` (`test_world.py`, edited) — parametrised over the
  one name.
- `bench`: `test_every_recording_leg_records_what_a_write_makes` — each of the two comparison legs
  reports a non-empty session after a write, so a leg that quietly stopped recording is caught.
- `bench`: `test_a_recording_only_report_points_at_no_section_it_did_not_write` — a report with
  `recording` alone names no other section number and still carries its own provenance.
- `bench`: `test_the_recording_probe_reports_three_legs_per_workload` — a `--quick`-sized run gives
  a `Recording` per probe with three positive per-call figures and the ProjectTracker and
  `emporium` probes both present.
- `bench`: `test_the_leg_order_rotates_so_no_leg_always_runs_last` — over three one-call repeats,
  the passes that grew the change log (which only Seahaven's own recorder does) are 0, 5 and 7.
- `bench`: `test_the_rotation_balances_the_mean_pass_position_and_not_the_median` — over a whole
  rotation every leg has the same mean pass position and three different median ones.
- `bench`: `test_a_leg_is_reduced_with_the_mean_of_its_passes` — passes of 1, 2 and 6 seconds per
  call reduce to 3, not to the median's 2.
- `bench`: `test_a_run_that_is_not_a_whole_rotation_is_refused` and `test_a_quick_run_is_a_whole_rotation`
  — 0, 1, 2 and 4 repeats raise, and `QUICK` asks for a whole rotation.
- `bench`: `test_a_split_that_cannot_be_read_is_printed_as_noise` and
  `test_a_split_in_the_order_construction_forces_is_reported` — either leg out of order prints
  `noise` in the row and leaves the finding's shares unreported; legs in order print them.
- `bench`: `test_reducing_no_passes_at_all_says_so` — `seconds_per_call([])` names the problem.
