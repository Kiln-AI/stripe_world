"""The benchmark's own tests: that it still runs, and that it reports honestly.

`bench/` is not part of the distribution and is never a gate, so nothing here
asserts a speed -- a test that failed when the machine was busy would be a test
nobody trusts, and a threshold in CI is the thing `architecture.md` §10 says the
benchmark must not become. What these prove is that the instrument works: that a
quantile is the element it claims to be, that a pass runs every call it says it
ran, that the gate is put back the way it was found, and that a run which could
not produce a cold measurement says so rather than printing a warm one under a
cold heading.

They run at toy sizes -- a handful of calls, two sessions -- because that is
enough to exercise every path and the suite is not the place to spend minutes.
"""

import dataclasses
import os
import statistics
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, cast

import pytest
from bench import report
from bench.__main__ import QUICK, main
from bench.baseline import BaselinePoint, Share, share
from bench.composite import (
    READ,
    SETTLE,
    WRITE,
    Leg,
    composite,
    legs,
    per_call,
    standing_up,
    trees,
)
from bench.environment import capture
from bench.harness import (
    Caller,
    Run,
    Summary,
    closed_loop,
    cold_cache_supported,
    gate_size,
    quantile,
    significant,
    summarise,
    timed_loop,
)
from bench.recording import (
    LEGS,
    Recording,
    long_lived_sessions,
    probes,
    recording,
    rotated,
    seconds_per_call,
    unread_sessions,
)
from bench.runner import calls_per_worker, measure, sessions
from bench.sweep import Isolation, Point, isolation, sweep
from bench.workloads import SHARE_STATEMENTS, WORKLOADS, Session, Workload

import projecttracker
from seahaven import Instance, World, WorldBug, instances

CALLS = 8
WORKERS = 2


@pytest.fixture
def world() -> World:
    """The real reference world: the benchmark measures that one and no other."""
    return projecttracker.world


@pytest.fixture
def agency(world: World) -> Iterator[Instance]:
    with world.instance("agency") as instance:
        yield instance


def test_quantile_picks_an_element_that_was_measured() -> None:
    values = [5.0, 1.0, 4.0, 2.0, 3.0]
    assert quantile(values, 0.0) == 1.0
    assert quantile(values, 0.5) == 3.0
    assert quantile(values, 0.95) == 5.0
    assert quantile(values, 1.0) == 5.0
    assert quantile([7.0], 0.95) == 7.0
    assert quantile([1.0, 2.0], 0.5) == 1.0


def test_quantile_refuses_what_it_cannot_answer() -> None:
    with pytest.raises(ValueError, match="quantile of nothing"):
        quantile([], 0.5)
    with pytest.raises(ValueError, match=r"within 0\.\.1"):
        quantile([1.0], 1.5)


def test_significant_prints_two_figures_and_no_exponent() -> None:
    assert significant(3421) == "3.4k"
    assert significant(820) == "820"
    assert significant(12.4) == "12"
    assert significant(0.5831) == "0.58"
    assert significant(0.0001234) == "0.00012"
    assert significant(0) == "0"


def test_a_pass_reports_every_call_it_made() -> None:
    made: list[int] = []
    run = closed_loop([lambda index: made.append(index)] * WORKERS, CALLS)
    assert len(made) == CALLS * WORKERS
    assert run.calls == CALLS * WORKERS
    assert len(run.latencies) == run.calls
    assert run.per_worker == (CALLS,) * WORKERS
    assert sum(run.per_worker) == run.calls
    assert run.rate > 0


def test_a_failing_call_fails_the_run() -> None:
    def explode(_index: int) -> None:
        raise WorldBug("the workload is broken")

    with pytest.raises(WorldBug, match="the workload is broken"):
        closed_loop([explode], CALLS)


def test_a_run_needs_a_caller() -> None:
    with pytest.raises(ValueError, match="at least one caller"):
        closed_loop([], CALLS)


def test_a_timed_loop_measures_both_groups_over_one_window() -> None:
    counted: list[int] = []
    alongside: list[int] = []
    readers, others = timed_loop(
        [lambda index: counted.append(index)],
        0.05,
        alongside=[lambda index: alongside.append(index)],
    )
    assert readers.calls == len(counted) > 0
    assert others.calls == len(alongside) > 0
    assert readers.seconds == pytest.approx(others.seconds)


def test_summarise_pools_latencies_and_keeps_rates_apart() -> None:
    runs = [
        Run(calls=2, seconds=1.0, latencies=(0.1, 0.3), per_worker=(2,)),
        Run(calls=2, seconds=2.0, latencies=(0.2, 0.4), per_worker=(2,)),
        Run(calls=2, seconds=4.0, latencies=(0.5, 0.6), per_worker=(2,)),
    ]
    summary = summarise(runs)
    assert summary.runs == 3
    assert summary.calls == 6
    assert summary.rate_median == 1.0  # 2.0, 1.0 and 0.5 calls a second
    assert (summary.rate_min, summary.rate_max) == (0.5, 2.0)
    assert summary.worst == 0.6  # the pool's worst, not any one pass's
    assert summary.spread == pytest.approx(1.5)
    with pytest.raises(ValueError, match="at least one run"):
        summarise([])


def test_calls_per_worker_runs_whole_cycles_only() -> None:
    read, mix = WORKLOADS["read"], WORKLOADS["write_mix"]
    assert calls_per_worker(read, 7) == 7
    assert calls_per_worker(mix, 7) == 4
    assert calls_per_worker(mix, 8) == 8
    assert calls_per_worker(mix, 1) == 4  # never less than one cycle of the mix


def test_the_read_workload_reads_one_issue_per_call(agency: Instance) -> None:
    caller = WORKLOADS["read"].prepare(agency)
    seen = [cast(dict[str, Any], caller(index)) for index in range(CALLS)]
    keys = {issue["key"] for issue in seen}
    assert len(seen) == CALLS
    assert len(keys) == CALLS, "the pool is shuffled, so no issue repeats this early"


def test_the_write_mix_writes_an_issues_short_life(agency: Instance) -> None:
    caller = WORKLOADS["write_mix"].prepare(agency)
    for index in range(4):  # one whole cycle
        caller(index)
    issue = agency.call("list_issues", status="in_progress", limit=1)["issues"][0]
    assert issue["title"] == "benchmark issue 0"
    assert issue["assignee_id"] is not None
    comments = agency.call("list_comments", issue_id=issue["id"])["comments"]
    assert [comment["body"] for comment in comments] == ["benchmark comment 1"]
    changed = {record.table for record in agency.change_log()}
    assert {"issues", "comments", "issue_events", "teams"} <= changed


def test_the_share_statements_are_the_ones_the_world_runs(agency: Instance) -> None:
    """The comparison is only a comparison while it runs `get_issue`'s own SQL.

    Both statements, not just the first: the labels fetch is built with an
    f-string inside `attach_labels`, so it is checked against that function's
    source. If either drifts, the share number drifts with it and this fails
    rather than the report quietly reporting a different measurement.
    """
    import inspect

    from projecttracker.tools import _rows

    assert f"SELECT {_rows.ISSUE_COLUMNS} FROM issues WHERE id = ?" == SHARE_STATEMENTS[0]
    labels = inspect.getsource(_rows.attach_labels)
    for fragment in (
        "SELECT issue_id, label_id FROM issue_labels WHERE issue_id IN (",
        "ORDER BY issue_id, label_id",
    ):
        assert fragment in labels
        assert fragment in SHARE_STATEMENTS[1]
    issue_id = str(agency.inspect().rows("SELECT id FROM issues LIMIT 1")[0]["id"])
    row = agency.inspect().rows(SHARE_STATEMENTS[0], issue_id)
    assert len(row) == 1
    assert agency.call("get_issue", issue_id=issue_id)["key"] == row[0]["key"]


def test_the_share_legs_are_three_timings_of_one_pass(world: World) -> None:
    """`share` drives all three legs and reports each of them. No ordering.

    That the whole call costs more than its statements, and the statements more
    through `Db` than on the cursor, is the finding -- and it belongs to
    `bench/results/latest.md`, which measures it over 1,800 calls a leg. It cannot
    belong here. At the eight calls this test can afford, each leg is a single
    window of roughly a hundred and fifty microseconds, and one scheduler
    preemption adds several hundred to whichever leg catches it: on a machine
    running anything else, *any* cross-leg comparison at this size is a coin toss,
    the larger ratio included. A suite that fails when the machine is busy is a
    suite nobody trusts, which is this file's first paragraph.

    What is left is still worth having and is deterministic: the pass ran the
    calls it says it ran, all three legs were timed and none of them is zero, and
    `fraction_of_call` divides by the call it came from.
    """
    measured = share(world, calls=CALLS, repeats=1)
    assert measured.calls == CALLS
    legs = (measured.call_seconds, measured.db_seconds, measured.cursor_seconds)
    assert all(leg > 0 for leg in legs), legs
    for leg in legs:
        assert measured.fraction_of_call(leg) == pytest.approx(leg / measured.call_seconds)
    assert measured.fraction_of_call(measured.call_seconds) == pytest.approx(1.0)


def test_a_session_that_cannot_be_prepared_leaves_no_instance(world: World) -> None:
    broken = Workload(name="broken", description="prepares by failing", cycle=1, prepare=_refuse)
    before = len(world._instances()._instances)
    with pytest.raises(WorldBug, match="no good"):
        Session.open(world, broken)
    assert len(world._instances()._instances) == before


def _refuse(_instance: Instance) -> Caller:
    raise WorldBug("no good")


def test_sessions_are_destroyed_even_when_the_block_fails(world: World) -> None:
    opened: list[Session] = []
    with (
        pytest.raises(WorldBug, match="during the pass"),
        sessions(world, WORKLOADS["read"], WORKERS) as live,
    ):
        opened = list(live)
        raise WorldBug("during the pass")
    assert [one.instance.closed for one in opened] == [True] * WORKERS


def test_a_cold_pass_needs_the_page_cache_to_be_droppable(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delattr(os, "posix_fadvise", raising=False)
    assert not cold_cache_supported()
    with pytest.raises(RuntimeError, match="posix_fadvise"):
        measure(world, WORKLOADS["read"], workers=1, calls=CALLS, cache="cold")


def test_an_unknown_cache_state_is_refused(world: World) -> None:
    with pytest.raises(ValueError, match="unknown cache state"):
        measure(world, WORKLOADS["read"], workers=1, calls=CALLS, cache="tepid")


def test_gate_size_restores_the_gate() -> None:
    before = instances.concurrency()
    with gate_size(1):
        assert instances.concurrency() == 1
        with gate_size(0):
            assert instances.concurrency() == 0
        assert instances.concurrency() == 1
    assert instances.concurrency() == before
    with pytest.raises(WorldBug, match="inside the gate"), gate_size(2):
        raise WorldBug("inside the gate")
    assert instances.concurrency() == before


def test_a_sweep_visits_every_point_and_puts_the_gate_back(world: World) -> None:
    before = instances.concurrency()
    measured = sweep(
        world,
        gates=(1, 0),
        workers=(WORKERS,),
        caches=("warm",),
        workloads=("read",),
        repeats=2,
        calls=CALLS,
        seed=3,
    )
    assert instances.concurrency() == before
    assert {cell.point for cell in measured.cells} == {
        Point(workload="read", cache="warm", workers=WORKERS, gate=gate) for gate in (1, 0)
    }
    for cell in measured.cells:
        assert cell.summary.runs == 2
        assert len(cell.rates) == len(cell.offsets) == 2
        assert cell.summary.calls == CALLS * WORKERS * 2
    assert measured.cell(Point("read", "warm", WORKERS, 99)) is None


def test_the_isolation_probe_reports_both_sides(world: World) -> None:
    before = instances.concurrency()
    probe = isolation(world, gates=(0,), readers=WORKERS, seconds=0.2, repeats=1)
    assert instances.concurrency() == before
    (cell,) = probe.cells
    assert cell.gate == 0
    assert len(cell.windows) == 1, "one window per repeat"
    assert len(cell.worst_window) == WORKERS
    assert cell.readers.calls == sum(cell.worst_window)
    assert cell.slow.calls >= 1


def test_the_probe_quotes_one_window_and_not_a_pool(world: World) -> None:
    """The striking range has to be two readers running side by side, not two windows."""
    probe = isolation(world, gates=(0,), readers=WORKERS, seconds=0.2, repeats=2)
    (cell,) = probe.cells
    assert len(cell.windows) == 2
    assert cell.worst_window in cell.windows
    assert min(cell.worst_window) == min(min(window) for window in cell.windows)
    document = report.render(
        report.Results(
            environment=capture(),
            command="python -m bench isolation --quick",
            seconds=1.0,
            cold_skipped=None,
            isolation=probe,
        )
    )
    assert f"{min(cell.worst_window)}-{max(cell.worst_window)}" in document


def test_the_report_carries_its_caveats_before_its_numbers(world: World) -> None:
    document = report.render(_results(world))
    assert document.index("What these numbers are") < document.index("## Environment")
    assert document.index("## Environment") < document.index("## Method")
    for heading in ("## 1. Baseline", "## 3. The gate sweep", "## 5. Repeatability", "## 6."):
        assert heading in document
    assert "never a gate" in document
    assert "read`, warm cache" in document
    assert "posix_fadvise(DONTNEED)" in document


def test_the_report_says_why_the_cold_runs_were_skipped(world: World) -> None:
    """The reason is carried, not guessed: `--warm-only` is not a broken platform."""
    document = report.render(_results(world, cold_skipped="asked for with --warm-only"))
    assert "Cold-cache runs were skipped: asked for with --warm-only" in document
    assert "posix_fadvise has no" not in document
    assert "cold cache" not in document, "no table may be printed under a cold heading"


@pytest.mark.skipif(not cold_cache_supported(), reason="this platform cannot drop its page cache")
def test_the_report_prints_a_cold_table_when_the_cold_runs_happened(world: World) -> None:
    document = report.render(_results(world, caches=("warm", "cold")))
    assert "Cold-cache runs were skipped" not in document
    assert "`read`, cold cache" in document


def test_the_report_names_the_quickest_gate_and_where_the_default_sits(world: World) -> None:
    document = report.render(_results(world))
    derived = document[document.index("## 6.") :]
    assert "Quickest gate" in derived
    assert "the default" in derived


def _results(
    world: World,
    *,
    cold_skipped: str | None = None,
    caches: tuple[str, ...] = ("warm",),
) -> report.Results:
    """A whole run at toy sizes: one sweep cell per gate and cache, one baseline point."""
    measured = sweep(
        world,
        gates=(1, 0),
        workers=(WORKERS,),
        caches=caches,
        workloads=("read",),
        repeats=1,
        calls=CALLS,
        seed=1,
    )
    run = measure(world, WORKLOADS["read"], workers=1, calls=CALLS)
    return report.Results(
        environment=capture(),
        command="python -m bench all --quick",
        seconds=1.0,
        cold_skipped=cold_skipped,
        baseline=(
            BaselinePoint(
                workload="read", cache="warm", calls_per_pass=CALLS, summary=summarise([run])
            ),
        ),
        share=Share(calls=CALLS, call_seconds=0.001, db_seconds=0.0002, cursor_seconds=0.0001),
        sweep=measured,
        isolation=Isolation(cells=(), workload="read", readers=WORKERS, seconds=0.1, repeats=1),
    )


def test_the_environment_answers_every_row() -> None:
    rows = dict(capture().rows())
    assert rows["Gate default here"] == str(instances.default_concurrency())
    assert "" not in rows.values(), "a row with nothing in it is a row that lies by omission"
    assert rows["Page-cache eviction"].startswith(
        "posix_fadvise" if cold_cache_supported() else "unavailable"
    )


def test_main_writes_a_report_and_leaves_the_gate_alone(tmp_path: Path) -> None:
    out = tmp_path / "latest.md"
    before = instances.concurrency()
    assert main(["all", "--quick", "--out", str(out)]) == 0
    assert instances.concurrency() == before
    document = out.read_text()
    assert document.startswith("# Seahaven benchmark")
    assert "## 4. One slow call" in document
    assert "## 7. A composite tree" in document


def test_main_refuses_to_overwrite_a_hand_written_reading(tmp_path: Path) -> None:
    out = tmp_path / "latest.md"
    out.write_text(f"# old\n\n{report.READING}\n\nThe default was confirmed.\n")
    assert main(["baseline", "--quick", "--out", str(out)]) == 2
    assert "The default was confirmed." in out.read_text()
    assert main(["baseline", "--quick", "--force", "--out", str(out)]) == 0
    assert "The default was confirmed." not in out.read_text()


def test_main_refuses_a_partial_rotation_before_running_anything(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The recording probe refuses a partial rotation, and it runs last of six measurements."""
    out = tmp_path / "latest.md"
    assert main(["all", "--repeats", "4", "--out", str(out)]) == 2
    assert not out.exists(), "nothing was measured, so there is nothing to write"
    assert "--repeats must be a positive multiple of 3" in capsys.readouterr().err

    # And a run without the recording probe is free to repeat as it likes.
    assert main(["baseline", "--repeats", "4", "--baseline-calls", "1", "--out", str(out)]) == 0


def test_main_skips_the_cold_runs_when_it_cannot_drop_the_page_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delattr(os, "posix_fadvise", raising=False)
    out = tmp_path / "latest.md"
    assert main(["baseline", "--quick", "--out", str(out)]) == 0
    document = out.read_text()
    assert "Cold-cache runs were skipped" in document
    assert "| cold |" not in document


def test_summary_of_one_run_has_no_spread() -> None:
    run = Run(calls=1, seconds=1.0, latencies=(0.5,), per_worker=(1,))
    summary: Summary = summarise([run])
    assert summary.spread == 0.0


def test_the_ladder_is_the_committed_composite_tree() -> None:
    """What the composite section measures is the tree `tests/worlds/README.md` describes.

    Asserted here rather than assumed in the report, because the two halves of
    that document -- a `payments` leaf, and an `emporium` of four nodes with one
    alias -- are what the per-node arithmetic divides by. A change to those
    worlds that the benchmark did not notice would be a table of the same shape
    holding different measurements.
    """
    ladder = trees()
    assert [tree.name for tree in ladder] == ["payments", "shop", "emporium"]
    assert [tree.nodes for tree in ladder] == [1, 2, 4]
    assert [node.path for node in ladder[-1].world.composition().nodes] == [
        "main",
        "payments",
        "payments_eu",
        "shop",
    ]
    assert trees() is ladder, "node identity is object identity: one ladder per process"


def test_standing_up_counts_the_stores_a_node_really_costs() -> None:
    """One database per node, counted off a live instance rather than derived."""
    costs = standing_up(trees(), repeats=2)
    assert [cost.tree for cost in costs] == ["payments", "shop", "emporium"]
    for cost in costs:
        assert cost.stores == cost.nodes
        assert cost.files >= cost.stores, "each store, plus whatever SQLite keeps beside it"
        assert cost.idle_bytes > 0
        assert cost.seal_seconds > 0
        assert cost.open_min <= cost.open_seconds <= cost.open_max
        assert cost.destroy_min <= cost.destroy_seconds <= cost.destroy_max
    assert costs[-1].idle_bytes > costs[0].idle_bytes, "four nodes hold more than one"


def test_the_composite_read_returns_one_row_however_often_it_is_called() -> None:
    """A pass that read a growing list would be measuring the list and not the call."""
    for leg in _legs_named("one-row read"):
        with leg.tree.world.instance() as instance:
            caller = leg.prepare(instance)
            rows = [cast(list[Any], caller(index)) for index in range(CALLS)]
        assert [len(row) for row in rows] == [1] * CALLS, leg.tree.name


def test_the_composite_write_writes_one_row_per_call() -> None:
    (leg,) = [one for one in _legs_named("one-row write") if one.tree.name == "emporium"]
    with leg.tree.world.instance() as instance:
        caller = leg.prepare(instance)
        for index in range(CALLS):
            caller(index)
        assert len(instance.call("pay_list_charges")) == CALLS


def test_settle_order_writes_three_nodes_in_one_call() -> None:
    """The row the leaf has no equivalent of: three stores under one activation."""
    (leg,) = _legs_named("settle_order")
    with leg.tree.world.instance() as instance:
        caller = leg.prepare(instance)
        caller(0)
        assert {record.world for record in instance.change_log()} == {"main", "payments", "shop"}


def test_the_composite_measurement_destroys_every_instance_it_opens() -> None:
    """Every pass opens a fresh instance, and a leaked one is a leaked file and connection."""
    ladder = trees()
    before = [len(tree.world._instances()._instances) for tree in ladder]
    measured = composite(calls=2, repeats=1, tree_repeats=1)
    assert [len(tree.world._instances()._instances) for tree in ladder] == before
    assert len(measured.trees) == len(ladder)
    assert {(cost.workload, cost.tree) for cost in measured.calls} == {
        (leg.workload, leg.tree.name) for leg in legs(ladder)
    }


def test_a_composite_pass_reports_the_calls_it_made() -> None:
    (leg,) = [one for one in _legs_named("one-row read") if one.tree.name == "payments"]
    (cost,) = per_call((leg,), calls=CALLS, repeats=2)
    assert cost.summary.runs == 2
    assert cost.summary.calls == CALLS * 2
    assert cost.nodes == 1


def flatten(text: str) -> str:
    """One space for every run of whitespace, and no blockquote markers.

    `report._wrapped` breaks the prose it renders at 96 columns, and where the
    break lands depends on the run -- the command, the commit, the machine's CPU
    model. The provenance paragraph is quoted as well, so a break inside the
    model name leaves `> ` in the middle of it. A substring looked for across one
    of those breaks is in the document and still missed; flattening both sides
    asserts the content rather than this machine's layout.
    """
    return " ".join(" ".join(line.removeprefix(">") for line in text.splitlines()).split())


def test_the_composite_section_carries_its_own_provenance_and_both_tables() -> None:
    """It is pasted into reports whose other tables came from another run."""
    environment = capture()
    document = report.render(
        report.Results(
            environment=environment,
            command="python -m bench composite --quick",
            seconds=1.0,
            cold_skipped=None,
            composite=composite(calls=2, repeats=1, tree_repeats=1),
        )
    )
    # Whitespace-flattened on both sides, because the provenance paragraph is
    # re-wrapped at 96 columns and the break lands wherever this machine's
    # command, commit and CPU model put it -- on another host it lands mid-model
    # and an unflattened `in` misses content that is right there.
    section = flatten(document[document.index("## 7.") :])
    assert flatten("**Provenance.**") in section
    assert flatten("python -m bench composite --quick") in section
    assert flatten(environment.commit) in section
    assert flatten(environment.cpu_model) in section
    assert flatten("### Standing one up") in section
    assert flatten("### What a node costs a call") in section
    assert flatten("| `emporium` | 4 | 4 |") in section, "four nodes, four stores"
    assert flatten("settle_order") in section


def test_the_method_section_is_dropped_when_no_projecttracker_run_happened(world: World) -> None:
    """`## Method` describes `agency`, and a composite-only run drove none of it."""
    composite_only = report.render(
        report.Results(
            environment=capture(),
            command="python -m bench composite --quick",
            seconds=1.0,
            cold_skipped=None,
            composite=composite(calls=2, repeats=1, tree_repeats=1),
        )
    )
    assert "## Method" not in composite_only
    assert "## Method" in report.render(_results(world))


def _legs_named(workload: str) -> list[Leg]:
    return [leg for leg in legs(trees()) if leg.workload == workload]


def test_a_tree_is_measured_at_least_once() -> None:
    with pytest.raises(ValueError, match="at least once"):
        standing_up(trees(), repeats=0)


def test_standing_up_discards_the_first_open_of_a_tree(monkeypatch: pytest.MonkeyPatch) -> None:
    """A tree's first open in a process is slower than its steady state and is thrown away."""
    leaf = trees()[0]
    opened = 0
    real = leaf.world.instance

    def counting(*arguments: Any, **keywords: Any) -> Instance:
        nonlocal opened
        opened += 1
        return real(*arguments, **keywords)

    monkeypatch.setattr(leaf.world, "instance", counting)
    (cost,) = standing_up((leaf,), repeats=2)
    assert opened == 3, "two measured passes, and one discarded before them"
    assert cost.destroy_min <= cost.destroy_seconds <= cost.destroy_max


def test_per_call_interleaves_its_repeats_across_the_legs() -> None:
    """A repeat is a whole pass over every leg, so drift is not handed to whichever ran last."""
    leaf = trees()[0]
    order: list[str] = []

    def marking(name: str) -> Callable[[Instance], Caller]:
        def prepare(instance: Instance) -> Caller:
            order.append(name)
            return lambda _index: instance.call("list_charges")

        return prepare

    lines = (Leg("first", leaf, marking("first")), Leg("second", leaf, marking("second")))
    assert len(per_call(lines, calls=1, repeats=2)) == 2
    assert order == ["first", "second", "first", "second"]


def test_the_derived_bullets_are_tied_to_the_leg_names() -> None:
    """`report._call_deltas` finds its rows by the names `composite` gives them.

    The two bullets that compare a leaf with a composite, and the one that prices
    the cross-node call, are selected by workload name. A rename that reached only
    one of the two modules would drop them from the report in silence, and the
    table rows would still be there to make the section look complete.
    """
    assert {leg.workload for leg in legs(trees())} == {READ, WRITE, SETTLE}
    document = report.render(
        report.Results(
            environment=capture(),
            command="python -m bench composite --quick",
            seconds=1.0,
            cold_skipped=None,
            composite=composite(calls=2, repeats=2, tree_repeats=1),
        )
    )
    # Whitespace-flattened, because every bullet is re-wrapped at 96 columns and a
    # line break may land anywhere inside the sentence being looked for.
    flat = " ".join(document[document.index("## 7.") :].split())
    assert flat.count("the same tool at one node and at 4") == 2
    assert f"**{SETTLE}** is " in flat
    assert "x the cost of one call" in flat


# --- the recording probe ---------------------------------------------------


def test_every_comparison_leg_records_what_a_write_makes(agency: Instance) -> None:
    """A leg that stopped opening a session at all would still time something.

    Both of the legs `Recording` compares against exist to *have* a session and
    not read it, so the only thing that can tell a working one from a leg that
    quietly does nothing is whether its session has a changeset to give.
    """
    caller = WORKLOADS["write_mix"].prepare(agency)

    with unread_sessions(agency) as per_call_recorded:
        for index in range(WORKLOADS["write_mix"].cycle):
            caller(index)
        assert per_call_recorded()

    with long_lived_sessions(agency) as long_lived_recorded:
        assert not long_lived_recorded(), "nothing written yet inside this block"
        for index in range(WORKLOADS["write_mix"].cycle):
            caller(index)
        assert long_lived_recorded()

    # And the instance is back on its own recorder, which is the one that logs.
    before = agency.call_count
    caller(0)
    assert [record.i for record in agency.change_log() if record.i is not None][-1] == before


def test_the_probe_times_three_legs_on_one_node_and_on_four(world: World) -> None:
    measured = [recording(probe, calls=1, repeats=len(LEGS)) for probe in probes(world)]

    assert {probe.nodes for probe in measured} == {1, 4}
    assert {probe.world for probe in measured} == {"ProjectTracker agency", "emporium"}
    for probe in measured:
        legs = (probe.per_call_seconds, probe.unread_seconds, probe.long_lived_seconds)
        assert all(seconds > 0 for seconds in legs), probe
        assert probe.calls >= 1
    assert any(probe.wrote_rows for probe in measured), "the write probes record rows"


def test_a_recording_only_report_points_at_no_section_it_did_not_write(world: World) -> None:
    """Section 8 can be regenerated alone, so it carries what it needs and names nothing else."""
    only = report.render(
        report.Results(
            environment=capture(),
            command="python -m bench recording --quick",
            seconds=1.0,
            cold_skipped=None,
            recording=tuple(
                recording(probe, calls=1, repeats=len(LEGS)) for probe in probes(world)
            ),
        )
    )
    section = flatten(only[only.index("## 8.") :])

    assert flatten("**Provenance.**") in section
    assert flatten("python -m bench recording --quick") in section
    assert "## Method" not in only, "`## Method` describes columns this run has none of"
    for pointed_at in ("section 1", "section 5", "section 7", "noise floor"):
        assert pointed_at not in only, pointed_at

    # And with the sections it names present, it does name them.
    whole = report.render(
        dataclasses.replace(
            _results(world),
            recording=tuple(
                recording(probe, calls=1, repeats=len(LEGS)) for probe in probes(world)
            ),
            composite=composite(calls=2, repeats=1, tree_repeats=1),
        )
    )
    with_sections = flatten(whole[whole.index("## 8.") :])
    assert flatten("section 5's noise floor") in with_sections
    assert flatten("the same calls section 7 times") in with_sections


def test_the_leg_order_rotates_so_no_leg_always_runs_last() -> None:
    """The long-lived leg is the denominator, so a fixed order would bias both figures.

    Only Seahaven's own recorder appends to the change log, so watching the log's
    length before each call says which pass ran the real leg. Three repeats of a
    one-call pass should put that leg at a different position in each.
    """
    (probe,) = [
        found
        for found in probes(projecttracker.world)
        if found.world_name == "emporium" and found.workload == WRITE
    ]
    lengths: list[int] = []

    def watching(instance: Instance) -> Caller:
        inner = probe.prepare(instance)

        def caller(index: int) -> object:
            lengths.append(len(instance.change_log()))
            return inner(index)

        return caller

    recording(dataclasses.replace(probe, prepare=watching), calls=1, repeats=len(LEGS))

    # `lengths[k]` is the log before call `k`; call 0 is the warm-up and call
    # `p + 1` is pass `p`, so a pass that grew the log ran the real leg. The last
    # pass has no later call to be seen through, and under this rotation it is
    # not the real leg's.
    grew = [p for p in range(len(lengths) - 2) if lengths[p + 2] > lengths[p + 1]]
    assert grew == [0, 5, 7], lengths


def test_the_rotation_balances_the_mean_pass_position_and_not_the_median() -> None:
    """Why the probe reduces a leg with the mean of its passes.

    An instance's write cost climbs with its own rows, so what a leg pays for
    that climb is decided by where its passes sit in the run. Over a whole
    rotation every leg has the same mean position, and the growth cancels in a
    mean of the passes. It never cancels in a median: a median of three passes
    *is* one pass, and the three legs' middle-ranked passes are three different
    positions whatever the schedule.
    """
    for repeats in (3, 6, 9):
        positions: dict[str, list[int]] = {leg: [] for leg in LEGS}
        index = 0
        for repeat in range(repeats):
            for leg in rotated(repeat):
                positions[leg].append(index)
                index += 1

        means = {leg: statistics.fmean(where) for leg, where in positions.items()}
        assert len(set(means.values())) == 1, means

        medians = {leg: statistics.median(where) for leg, where in positions.items()}
        assert len(set(medians.values())) == len(LEGS), medians


def test_a_leg_is_reduced_with_the_mean_of_its_passes() -> None:
    """A median would pick the middle pass, which is the position bias's home."""
    passes = [
        Run(calls=10, seconds=seconds, latencies=(), per_worker=(10,))
        for seconds in (10.0, 20.0, 60.0)
    ]

    assert seconds_per_call(passes) == pytest.approx(3.0)
    assert seconds_per_call(passes) != pytest.approx(2.0), "2.0 is the median pass"


def test_a_run_that_is_not_a_whole_rotation_is_refused() -> None:
    """A partly-rotated run would print the balance the report claims without having it."""
    probe = probes(projecttracker.world)[0]
    for repeats in (0, 1, 2, 4):
        with pytest.raises(ValueError, match="positive multiple of 3"):
            recording(probe, calls=1, repeats=repeats)


def test_a_quick_run_is_a_whole_rotation() -> None:
    """`--quick` is for checking the harness runs, and a run the probe refuses is not that."""
    assert QUICK["repeats"] % len(LEGS) == 0
    assert QUICK["repeats"] >= len(LEGS)


def test_reducing_no_passes_at_all_says_so() -> None:
    with pytest.raises(ValueError, match="at least one run"):
        seconds_per_call([])


def _recorded(
    *, per_call: float, unread: float, long_lived: float, wrote_rows: bool = True
) -> Recording:
    """One probe's three legs at chosen costs, for the report's own arithmetic."""
    return Recording(
        world="ProjectTracker agency",
        workload="write_mix",
        nodes=1,
        calls=CALLS,
        per_call_seconds=per_call,
        unread_seconds=unread,
        long_lived_seconds=long_lived,
        wrote_rows=wrote_rows,
    )


def _section_eight(measured: Recording) -> str:
    document = report.render(
        report.Results(
            environment=capture(),
            command="python -m bench recording --quick",
            seconds=1.0,
            cold_skipped=None,
            recording=(measured,),
        )
    )
    return flatten(document[document.index("## 8.") :])


@pytest.mark.parametrize(
    ("per_call", "unread", "long_lived"),
    [
        pytest.param(0.4, 0.5, 0.2, id="the never-read leg is not cheaper than the full one"),
        pytest.param(0.4, 0.2, 0.3, id="the long-lived leg is not cheaper than the never-read one"),
    ],
)
def test_a_split_that_cannot_be_read_is_printed_as_noise(
    per_call: float, unread: float, long_lived: float
) -> None:
    """Each leg does strictly less than the one above it, so a share cannot be negative.

    A run that timed them in another order measured its own passes, and neither
    the table nor the finding may put a percentage on that.
    """
    inverted = _recorded(per_call=per_call, unread=unread, long_lived=long_lived)
    assert not inverted.split_reads

    section = _section_eight(inverted)

    assert flatten("| noise |") in section
    assert flatten("Its split is left unreported") in section
    assert "points are reading the changeset" not in section


@pytest.mark.parametrize("wrote_rows", [True, False], ids=["wrote rows", "wrote none"])
def test_a_total_that_cannot_be_read_is_printed_as_noise(wrote_rows: bool) -> None:
    """The total is a measured figure too, and the same order decides whether it can be read.

    The full leg does everything the long-lived leg does, on a session opened and
    attached per node per call rather than once, and reads a changeset back as
    well. A run that timed it no dearer measured its own passes, so neither the
    table nor the finding may put a percentage on the total -- including on the
    probe whose calls wrote no rows, which still has its own total to report when
    the legs do come out in order.
    """
    inverted = _recorded(per_call=0.2, unread=0.3, long_lived=0.4, wrote_rows=wrote_rows)
    assert not inverted.total_reads
    assert not inverted.split_reads

    section = _section_eight(inverted)

    assert flatten("| a session per call | 200 ms | noise |") in section
    assert flatten("Its total is this run's own noise and is left unreported") in section
    assert "-50%" not in section, "the signed total the arithmetic gives is the negative one"


def test_a_total_in_the_order_construction_forces_is_reported() -> None:
    """The other side of that guard, on the probe whose split stays unreadable.

    A pass that wrote no rows has nothing to render, so its split is noise while
    its total is a real cost: a session opened and attached per node per call,
    and an empty `changeset()` read back.
    """
    read_only = _recorded(per_call=0.3, unread=0.25, long_lived=0.2, wrote_rows=False)
    assert read_only.total_reads
    assert not read_only.split_reads

    section = _section_eight(read_only)

    assert flatten("| a session per call | 300 ms | +50% |") in section
    assert flatten("costs about +50% against the framework's previous shape") in section


def test_a_split_in_the_order_construction_forces_is_reported() -> None:
    """The other side of the guard: legs in their own order do get their shares."""
    honest = _recorded(per_call=0.4, unread=0.3, long_lived=0.2)
    assert honest.split_reads

    section = _section_eight(honest)

    assert flatten("| +50% |") in section, "the remainder, long-lived to never-read"
    assert flatten("roughly +50 points are reading the changeset") in section
    assert flatten("noise") not in section
