"""One measured point: N sessions, a cache state, a fixed number of calls each.

The piece the baseline and the sweep share. It owns the two things that decide
what a point means -- how many calls make a pass, and what "cold" and "warm" are
done to an instance before the clock starts -- so that both measurements answer
the same question and their tables can sit in one document.
"""

from collections.abc import Iterator, Sequence
from contextlib import contextmanager

from bench.harness import Run, closed_loop, cold_cache_supported, evict
from bench.workloads import Session, Workload
from seahaven import World

__all__ = ["CACHES", "Cache", "calls_per_worker", "measure", "sessions", "whole_cycles"]

# What an instance has been through before the clock starts.
#
# `warm`: the instance has already served one full pass of this workload, so its
# connection's page cache holds what the pass touches. This is the steady state of
# a session that has been running for a while.
#
# `cold`: the instance has served nothing, and the guest's page cache has been
# told to drop its files. This is a session's first calls. What it is not is a
# cold *disk*: the host's cache is not ours to drop, so the reads it makes are
# warm somewhere below us, and the report says so.
type Cache = str
CACHES: tuple[Cache, ...] = ("warm", "cold")


def calls_per_worker(workload: Workload, calls: int) -> int:
    """`calls`, rounded down to whole cycles of the workload, at least one cycle."""
    return whole_cycles(calls, workload.cycle)


def whole_cycles(calls: int, cycle: int) -> int:
    """`calls`, rounded down to whole cycles of `cycle`, at least one cycle.

    Taken by cycle rather than by workload because `bench.recording` drives legs
    that are not `Workload`s and has to round a pass the same way, or its table
    and the sweep's would not be counting the same units of work.
    """
    return max(calls - calls % cycle, cycle)


@contextmanager
def sessions(world: World, workload: Workload, count: int) -> Iterator[list[Session]]:
    """`count` prepared sessions, destroyed together however the block ends.

    A session is an instance and a copy of the fixture, so a block that leaks one
    leaks a megabyte-and-a-half file and an open connection per repeat; over a
    sweep of a hundred points that is the benchmark measuring its own leak.
    """
    opened: list[Session] = []
    try:
        for _ in range(count):
            opened.append(Session.open(world, workload))
        yield opened
    finally:
        for open_session in opened:
            open_session.instance.destroy()


def measure(
    world: World, workload: Workload, *, workers: int, calls: int, cache: Cache = "warm"
) -> Run:
    """One pass: `workers` sessions, `calls` calls each, from `cache`.

    The warm pass runs its warm-up one session at a time and off the clock, so
    that warming is not itself a concurrency measurement. For the write mix that
    warm-up is real work -- it files as many issues as the measured pass will --
    which is why a pass is a few hundred calls and every repeat starts from a new
    instance: an instance's write cost climbs slowly as its tables and its
    change log grow, and a long pass would report that climb as the cost of a
    call.

    **That makes the write mix's warm and cold passes differ by more than a page
    cache**, and the report says so rather than pretending otherwise. A warm write
    pass runs on an instance that has already taken a pass worth of writes; a cold
    one runs on a virgin instance. The drift those extra rows cause is of the same
    order as the difference between the two columns, so the write mix's cold/warm
    axis cannot be read as a cache effect. Priming the cold pass with the same
    writes would fix the confound and destroy the measurement: the writes would
    leave the connection's own page cache warm, which is the thing "cold" means.
    """
    per_worker = calls_per_worker(workload, calls)
    with sessions(world, workload, workers) as opened:
        _prime(opened, cache, per_worker)
        return closed_loop([opened_session.caller for opened_session in opened], per_worker)


def _prime(opened: Sequence[Session], cache: Cache, calls: int) -> None:
    if cache == "warm":
        for open_session in opened:
            for index in range(calls):
                open_session.caller(index)
        return
    if cache != "cold":
        raise ValueError(f"unknown cache state: {cache!r}")
    if not cold_cache_supported():
        raise RuntimeError("cold-cache runs need posix_fadvise; ask cold_cache_supported() first")
    for open_session in opened:
        evict(open_session.instance.dir)
