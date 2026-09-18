"""The measuring instrument: threads, a clock, quantiles, a page cache and the gate.

Nothing here knows what a tool is. A *caller* is a function that performs one
call when handed its index, the harness runs a fixed number of them on each of a
fixed number of threads, and what comes back is a `Run`: how many calls, how long
in total, and every call's latency.

Three deliberate choices, each of which shapes what the numbers mean:

*A closed loop with no think time.* Every thread issues its next call the instant
the last one returns, so the process is saturated for the whole measurement. That
is the right shape for a throughput number and the wrong shape for a latency
promise: what a saturated closed loop reports as p95 is the queueing of the
benchmark's own offered load, not what a session would see.

*A fixed number of calls, not a fixed duration.* Two runs of one configuration do
the same work, so their wall times can be compared. A duration-bounded run
measures whatever it got through, which makes a slow point look like a short one.
The price is that a pass ends when its slowest thread finishes: a configuration
that leaves one thread waiting runs the tail of its pass below full offered load,
so its rate and its worst wait are two views of one thing rather than two
independent measurements.

*One instance per thread.* An instance serialises its own calls under its lock,
so threads sharing one would be measuring the lock. OpenEnv gives every session
its own environment and its own thread, and that is what this copies.
"""

import logging
import math
import os
import statistics
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from seahaven import instances

__all__ = [
    "Caller",
    "Run",
    "Summary",
    "closed_loop",
    "cold_cache_supported",
    "evict",
    "gate_size",
    "quantile",
    "quiet_logging",
    "significant",
    "summarise",
    "timed_loop",
]

# One call, given the index of that call on its own thread. The index is what
# lets a workload drive a different row each time without holding state of its
# own; a workload that needs state keeps it in the closure it returns.
type Caller = Callable[[int], object]


@dataclass(frozen=True)
class Run:
    """One measured pass: what it did, how long it took, and every latency in it."""

    calls: int
    seconds: float
    # Seconds per call, unsorted and pooled across the threads. Kept whole rather
    # than reduced to quantiles here because repeats are pooled before they are
    # reduced, and a quantile of quantiles is not a quantile.
    latencies: tuple[float, ...]
    # What each thread completed. Two threads with wildly different counts are the
    # finding a rate and a median both hide: somebody was not being served.
    per_worker: tuple[int, ...]

    @property
    def rate(self) -> float:
        """Calls per second over the whole pass, threads included."""
        return self.calls / self.seconds if self.seconds > 0 else math.inf


@dataclass(frozen=True)
class Summary:
    """Several runs of one configuration, reduced for a table cell."""

    runs: int
    calls: int
    rate_median: float
    rate_min: float
    rate_max: float
    p50: float  # seconds
    p95: float
    worst: float

    @property
    def spread(self) -> float:
        """(max - min) / median of the rate: this cell's own noise, as a fraction."""
        return (self.rate_max - self.rate_min) / self.rate_median if self.rate_median else 0.0


def summarise(runs: Sequence[Run]) -> Summary:
    """Reduce repeats of one configuration. Latencies pool; rates do not.

    The rate is a property of a whole pass, so the repeats give three of them and
    the median is the middle pass. Latencies are per call, so pooling them and
    taking the quantile of the pool answers "of every call this configuration
    made, what did the slowest twentieth wait" -- which is the question -- rather
    than averaging three p95s, which answers nothing.

    Per-worker counts are deliberately *not* summarised here. Pooling them across
    repeats is how a range that reads as "one session against another" becomes a
    range over several windows, which is a different and weaker claim; a caller
    that wants them reads `Run.per_worker` one window at a time, as
    `sweep.IsolationCell` does.
    """
    if not runs:
        raise ValueError("summarise needs at least one run")
    rates = [run.rate for run in runs]
    latencies = [latency for run in runs for latency in run.latencies]
    return Summary(
        runs=len(runs),
        calls=sum(run.calls for run in runs),
        rate_median=statistics.median(rates),
        rate_min=min(rates),
        rate_max=max(rates),
        p50=quantile(latencies, 0.50),
        p95=quantile(latencies, 0.95),
        worst=max(latencies),
    )


def quantile(values: Sequence[float], q: float) -> float:
    """The value at `q` of the sorted sample, by nearest rank.

    Nearest rank and not interpolation: every number here is a latency that some
    call really waited, and an interpolated p95 is a number no call ever saw.
    """
    if not values:
        raise ValueError("quantile of nothing")
    if not 0.0 <= q <= 1.0:
        raise ValueError(f"quantile must be within 0..1: {q}")
    ordered = sorted(values)
    rank = math.ceil(q * len(ordered))
    return ordered[max(rank - 1, 0)]


def significant(value: float, digits: int = 2) -> str:
    """A number at two significant figures, thousands as `k`.

    The machine this benchmark runs on moves by more than a percent between one
    pass and the next, so a third digit would be decoration and a fourth would be
    a lie. Everything printed goes through here.

    Positional notation rather than `%g`, which turns 820 into `8.2e+02` at two
    significant figures: a table of rates is read by eye, and an exponent in one
    cell of it is a cell nobody reads.
    """
    if value == 0:
        return "0"
    if abs(value) >= 1000:
        return f"{significant(value / 1000, digits)}k"
    decimals = max(digits - 1 - math.floor(math.log10(abs(value))), 0)
    return f"{value:.{decimals}f}"


class _Worker(threading.Thread):
    """A thread of calls whose failure is the whole run's failure.

    An exception on a bare `Thread` is a traceback on stderr and a benchmark that
    reports a number anyway. This one keeps it, and `closed_loop` re-raises it.
    """

    def __init__(
        self,
        caller: Caller,
        *,
        start_line: threading.Barrier,
        calls: int | None = None,
        stop: threading.Event | None = None,
    ) -> None:
        super().__init__(daemon=True)
        self._caller = caller
        self._start_line = start_line
        self._calls = calls
        self._stop = stop
        self.latencies: list[float] = []
        self.failure: BaseException | None = None

    def run(self) -> None:
        try:
            self._start_line.wait()
            index = 0
            while self._keep_going(index):
                began = time.perf_counter()
                self._caller(index)
                self.latencies.append(time.perf_counter() - began)
                index += 1
        except BaseException as error:
            # Carried, not swallowed: `_collect` re-raises it, so a benchmark
            # whose workload broke reports the breakage instead of a number.
            self.failure = error

    def _keep_going(self, index: int) -> bool:
        if self._calls is not None:
            return index < self._calls
        assert self._stop is not None
        return not self._stop.is_set()


def closed_loop(callers: Sequence[Caller], calls: int) -> Run:
    """Run `calls` calls on each caller's own thread, all released together.

    The wall time starts when the last thread reaches the start line and ends when
    the last one finishes, so it covers exactly the window in which every thread
    was working -- not the thread creation before it.
    """
    workers, wall = _drive(
        [_WorkerSpec(caller, calls=calls) for caller in callers],
        run=lambda: None,
    )
    return _collect(workers, wall)


def timed_loop(
    callers: Sequence[Caller], seconds: float, *, alongside: Sequence[Caller] = ()
) -> tuple[Run, Run]:
    """Loop every caller for `seconds`, and report the two groups separately.

    For the questions a fixed call count cannot ask: what the callers in
    `alongside` do to the ones in `callers` while both are running. Both groups
    are measured over the same window, and a call already in flight when the
    window closes is still counted -- a call that waited out the whole
    measurement is the single most interesting number a gate sweep produces, and
    dropping it would be dropping the finding.

    The wall time both groups are divided by therefore includes the drain after
    the window closes: a caller blocked on the gate at that moment still has to
    take a slot and run before it can stop. That inflates the denominator by a few
    percent, most at the gate sizes that make callers wait, so the rate column
    here is a slight under-estimate exactly where the waiting is worst. The counts
    per caller, which is what this probe is for, are unaffected.
    """
    stop = threading.Event()
    specs = [_WorkerSpec(caller, stop=stop) for caller in (*callers, *alongside)]

    def measure() -> None:
        # The stop is set in a `finally` so that a Ctrl-C during the window stops
        # the workers too: `_drive` joins them whatever happens here, and a
        # worker whose stop was never set would never be joined.
        try:
            time.sleep(seconds)
        finally:
            stop.set()

    workers, wall = _drive(specs, run=measure)
    split = len(callers)
    return _collect(workers[:split], wall), _collect(workers[split:], wall)


@dataclass(frozen=True)
class _WorkerSpec:
    caller: Caller
    calls: int | None = None
    stop: threading.Event | None = None


def _drive(specs: Sequence[_WorkerSpec], *, run: Callable[[], None]) -> tuple[list[_Worker], float]:
    """Start every worker, release them together, run `run`, and join them."""
    if not specs:
        raise ValueError("a run needs at least one caller")
    start_line = threading.Barrier(len(specs) + 1)
    workers = [
        _Worker(spec.caller, start_line=start_line, calls=spec.calls, stop=spec.stop)
        for spec in specs
    ]
    for worker in workers:
        worker.start()
    start_line.wait()
    began = time.perf_counter()
    try:
        run()
    finally:
        for worker in workers:
            worker.join()
    return workers, time.perf_counter() - began


def _collect(workers: Sequence[_Worker], seconds: float) -> Run:
    for worker in workers:
        if worker.failure is not None:
            raise worker.failure
    latencies = [latency for worker in workers for latency in worker.latencies]
    return Run(
        calls=len(latencies),
        seconds=seconds,
        latencies=tuple(latencies),
        per_worker=tuple(len(worker.latencies) for worker in workers),
    )


def cold_cache_supported() -> bool:
    """Whether this platform lets an unprivileged process drop a file's pages.

    `POSIX_FADV_DONTNEED` is the whole of what a benchmark without root can do
    about the page cache. Where it is missing there is no cold measurement to be
    had, and the report says the runs were skipped rather than printing a warm
    number under a cold heading.
    """
    return hasattr(os, "posix_fadvise") and hasattr(os, "POSIX_FADV_DONTNEED")


def evict(directory: Path) -> None:
    """Drop every page of every file in `directory` from this guest's page cache.

    Flushed first: `DONTNEED` drops clean pages and leaves dirty ones, so without
    the `fsync` a freshly written database would stay resident and "cold" would be
    the word for a warm file. What this cannot do is reach the hypervisor's cache
    or the host's, which is why `latest.md` states the limit instead of claiming a
    cold disk.
    """
    if not cold_cache_supported():
        raise RuntimeError("this platform cannot drop page cache: posix_fadvise is missing")
    for path in sorted(directory.iterdir()):
        if not path.is_file():
            continue
        handle = os.open(path, os.O_RDONLY)
        try:
            os.fsync(handle)
            os.posix_fadvise(handle, 0, 0, os.POSIX_FADV_DONTNEED)
        finally:
            os.close(handle)


@contextmanager
def gate_size(size: int) -> Iterator[None]:
    """Hold the process-wide concurrency gate at `size`, restoring it afterwards.

    Through `instances.set_concurrency`, which is what `seahaven serve
    --concurrency` calls: the sweep measures the gate an operator would get and
    not a private semaphore that happens to resemble it.
    """
    before = instances.concurrency()
    instances.set_concurrency(size)
    try:
        yield
    finally:
        instances.set_concurrency(before)


@contextmanager
def quiet_logging() -> Iterator[None]:
    """Pin `seahaven`'s logger to WARNING for the run, and say so in the report.

    Every call logs one INFO line. With no handler configured, the line costs a
    level check and is dropped -- but a level check that depends on how the
    process happens to be configured is a measurement that moves for a reason
    that has nothing to do with the framework. Pinned here so two runs agree;
    `serve` under uvicorn *does* configure INFO handlers, so a served call pays
    for formatting and writing that line and this benchmark does not measure it.
    """
    logger = logging.getLogger("seahaven")
    before = logger.level
    logger.setLevel(logging.WARNING)
    try:
        yield
    finally:
        logger.setLevel(before)
