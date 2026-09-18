"""The gate sweep, and what a gate size costs when one call is slow.

`architecture.md` §5.2 records the concurrency gate's default,
`min(os.process_cpu_count() or 4, 16)`, as "a starting point, not a measurement",
and asks a later phase to sweep the value "under a cold and a warm cache" and
tune it. This is the sweep: gate size against cache state against offered load
against workload, with the ungated process as the control.

**Repeats are whole passes in a shuffled order.** A sweep takes minutes and the
machine under it drifts over minutes. Running every point once, then every point
again in a different order, spreads that drift across the configurations instead
of handing it to whichever one ran last, and the per-repeat rates are kept so the
report can show how far apart the passes were.

**The isolation probe is the other half of the answer.** A sweep of short calls
says smaller gates are faster, and stops there. The probe asks what a gate size
does to four sessions when a fifth is running a slow statement, which is the
question a serving framework actually has to answer, and it reports how many
calls each reader completed rather than only their quantiles: a session that was
served once in three seconds is a finding no percentile can show.
"""

import random
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass

from bench.harness import Caller, Run, Summary, gate_size, summarise, timed_loop
from bench.runner import Cache, measure, sessions
from bench.workloads import SLOW_STATEMENT, WORKLOADS, Session
from seahaven import World

__all__ = [
    "DEFAULT_GATES",
    "DEFAULT_WORKERS",
    "PROBE_WARMUP",
    "READ",
    "Cell",
    "Isolation",
    "IsolationCell",
    "Point",
    "Sweep",
    "isolation",
    "sweep",
]

# `0` is no gate at all, and it is in the list as the control: every other value
# is read against the process that was not gated.
DEFAULT_GATES: tuple[int, ...] = (1, 2, 4, 8, 16, 0)

# Offered load, in sessions calling at once: one at the scale of the machine's
# cpus, one well past it. A gate only does anything when more calls are offered
# than it admits, so a sweep at one offered load cannot tell a gate that binds
# from one that does not.
DEFAULT_WORKERS: tuple[int, ...] = (4, 32)

# Calls each probe session makes before the window opens. Enough to have the rows
# it will drive in its page cache, and short enough that five sessions are warm in
# well under a second.
PROBE_WARMUP = 100

# The probe's readers run the one-row read: the ordinary call whose service the
# slow one is interfering with.
READ = "read"


@dataclass(frozen=True)
class Point:
    """The four axes of one cell. Hashable, so it can index the runs it collected."""

    workload: str
    cache: Cache
    workers: int
    gate: int


@dataclass(frozen=True)
class Cell:
    """One point, its repeats reduced, and its repeats kept."""

    point: Point
    summary: Summary
    # Per repeat, in the order they ran, with how far into the sweep each began.
    # The report's repeatability section is these two: a cell whose passes
    # disagree, and a machine whose behaviour moved while the sweep was running,
    # look identical in a median and different here.
    rates: tuple[float, ...]
    offsets: tuple[float, ...]


@dataclass(frozen=True)
class Sweep:
    """Every cell, and the configuration that produced them."""

    cells: tuple[Cell, ...]
    gates: tuple[int, ...]
    workers: tuple[int, ...]
    caches: tuple[Cache, ...]
    workloads: tuple[str, ...]
    repeats: int
    calls: int
    seed: int
    seconds: float

    def cell(self, point: Point) -> Cell | None:
        return next((cell for cell in self.cells if cell.point == point), None)


@dataclass(frozen=True)
class IsolationCell:
    """One gate size, with the readers and one slow caller sharing the process."""

    gate: int
    readers: Summary
    slow: Summary
    # What each reader completed, one tuple per window. Per window and not pooled
    # across the repeats, because the finding is a claim about one window -- this
    # reader got a single call while that one got thousands, *at the same time* --
    # and a range pooled over three windows could be two facts about two of them.
    windows: tuple[tuple[int, ...], ...]

    @property
    def worst_window(self) -> tuple[int, ...]:
        """The window in which the worst-served reader did least.

        The one to quote: it is a single three-second window, so its smallest and
        largest counts are two readers that really were running side by side.
        """
        return min(self.windows, key=min)


@dataclass(frozen=True)
class Isolation:
    """Every gate size probed, and the shape of the probe that produced them."""

    cells: tuple[IsolationCell, ...]
    workload: str
    readers: int
    seconds: float
    repeats: int


def sweep(
    world: World,
    *,
    gates: Sequence[int],
    workers: Sequence[int],
    caches: Sequence[Cache],
    workloads: Sequence[str],
    repeats: int,
    calls: int,
    seed: int,
    progress: bool = False,
) -> Sweep:
    """Measure every (workload, cache, offered load, gate), `repeats` times over."""
    points = [
        Point(workload=workload, cache=cache, workers=count, gate=gate)
        for workload in workloads
        for cache in caches
        for count in workers
        for gate in gates
    ]
    collected: dict[Point, list[Run]] = {point: [] for point in points}
    offsets: dict[Point, list[float]] = {point: [] for point in points}
    began = time.perf_counter()
    for repeat in range(repeats):
        order = list(points)
        random.Random(seed + repeat).shuffle(order)
        for point in order:
            offsets[point].append(time.perf_counter() - began)
            with gate_size(point.gate):
                run = measure(
                    world,
                    WORKLOADS[point.workload],
                    workers=point.workers,
                    calls=calls,
                    cache=point.cache,
                )
            collected[point].append(run)
            _say(progress, f"  {_label(point)}: {run.rate:.0f} calls/s")
    return Sweep(
        cells=tuple(
            Cell(
                point=point,
                summary=summarise(collected[point]),
                rates=tuple(run.rate for run in collected[point]),
                offsets=tuple(offsets[point]),
            )
            for point in points
        ),
        gates=tuple(gates),
        workers=tuple(workers),
        caches=tuple(caches),
        workloads=tuple(workloads),
        repeats=repeats,
        calls=calls,
        seed=seed,
        seconds=time.perf_counter() - began,
    )


def isolation(
    world: World,
    *,
    gates: Sequence[int],
    readers: int,
    seconds: float,
    repeats: int,
    progress: bool = False,
) -> Isolation:
    """`readers` sessions reading one row at a time, beside one running slow SQL.

    Duration-bounded rather than call-bounded, which is the one place in this
    benchmark that is: the question is what each session got through in a fixed
    window, and a call-bounded run would wait however long the worst-served
    session needed and report nothing about the waiting.
    """
    cells: list[IsolationCell] = []
    for gate in gates:
        reader_runs: list[Run] = []
        slow_runs: list[Run] = []
        for _ in range(repeats):
            with gate_size(gate):
                reader_run, slow_run = _probe(world, readers=readers, seconds=seconds)
            reader_runs.append(reader_run)
            slow_runs.append(slow_run)
        cells.append(
            IsolationCell(
                gate=gate,
                readers=summarise(reader_runs),
                slow=summarise(slow_runs),
                windows=tuple(run.per_worker for run in reader_runs),
            )
        )
        _say(progress, f"  gate={gate}: readers {cells[-1].readers.rate_median:.0f} calls/s")
    return Isolation(
        cells=tuple(cells),
        workload=READ,
        readers=readers,
        seconds=seconds,
        repeats=repeats,
    )


def _probe(world: World, *, readers: int, seconds: float) -> tuple[Run, Run]:
    read = WORKLOADS[READ]
    with sessions(world, read, readers + 1) as opened:
        for open_session in opened:
            for index in range(PROBE_WARMUP):  # off the clock
                open_session.caller(index)
        slow = _slow_caller(opened[-1])
        slow(0)
        return timed_loop(
            [open_session.caller for open_session in opened[:readers]],
            seconds,
            alongside=[slow],
        )


def _slow_caller(open_session: Session) -> Caller:
    def slow(_index: int) -> object:
        return open_session.instance.call("run_sql", query=SLOW_STATEMENT)

    return slow


def _label(point: Point) -> str:
    return (
        f"{point.workload} {point.cache} workers={point.workers} "
        f"gate={point.gate if point.gate else 'none'}"
    )


def _say(progress: bool, line: str) -> None:
    if progress:
        print(line, file=sys.stderr, flush=True)
