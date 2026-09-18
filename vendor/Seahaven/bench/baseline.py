"""What one call costs on one thread, and where inside the call it goes.

Two measurements, both single-threaded, because a per-call cost is a
single-threaded number: the moment two threads are running, what is being
measured is the interpreter's scheduling as much as the framework's code, and
that is the sweep's question rather than this one's.

1. **Baseline.** Each workload, cold and warm, one thread. The number
   `functional_spec.md` §23 commits to -- "about 3,000 one-row reads per second
   per process" -- is this one.
2. **Share, in three legs.** The same one-row read, driving the same rows in the
   same order, timed three ways: through `Instance.call`; as the two statements
   the tool runs through `Db.rows`, the data layer world code is handed; and as
   those same two statements stepped straight on the APSW cursor.

   Three and not two because the middle leg is *not* SQLite. `Db.rows` is
   framework code -- an error-translating context manager and a dict built per
   row from the cursor description -- and reporting it as "SQLite's share" would
   book half of the data layer to SQLite and make this benchmark refute a claim
   it corroborates. The third leg is the closest an honest measurement here gets
   to SQLite proper, and it is still an upper bound on it: the statements are
   stepped on the inspection connection, which carries the read-only authorizer,
   so a Python callback runs while SQLite prepares them.

   All three legs are approximate by construction: only the first has an argument
   model, a middleware chain, a transaction and a serialiser, which is the whole
   of what the comparison is for.
"""

from dataclasses import dataclass

from bench.harness import Run, Summary, closed_loop, summarise
from bench.runner import Cache, calls_per_worker, measure, sessions
from bench.workloads import SHARE_STATEMENTS, WORKLOADS, issue_ids
from seahaven import World

__all__ = ["BaselinePoint", "Share", "baseline", "share"]


@dataclass(frozen=True)
class BaselinePoint:
    """One workload from one cache state, over every repeat."""

    workload: str
    cache: Cache
    calls_per_pass: int
    summary: Summary


@dataclass(frozen=True)
class Share:
    """One read, as the whole call, as its statements through `Db`, and on the cursor."""

    calls: int
    call_seconds: float
    db_seconds: float
    cursor_seconds: float

    def fraction_of_call(self, seconds: float) -> float:
        """What one leg is worth as a share of the whole call, between 0 and 1."""
        return seconds / self.call_seconds if self.call_seconds else 0.0


def baseline(
    world: World, *, calls: int, repeats: int, caches: tuple[Cache, ...]
) -> list[BaselinePoint]:
    """Every workload from every cache state, one thread, `repeats` passes each."""
    points: list[BaselinePoint] = []
    for workload in WORKLOADS.values():
        for cache in caches:
            runs = [
                measure(world, workload, workers=1, calls=calls, cache=cache)
                for _ in range(repeats)
            ]
            points.append(
                BaselinePoint(
                    workload=workload.name,
                    cache=cache,
                    calls_per_pass=calls_per_worker(workload, calls),
                    summary=summarise(runs),
                )
            )
    return points


def share(world: World, *, calls: int, repeats: int) -> Share:
    """Time the read three ways, warm, on one instance, driving the same rows.

    One instance and one order for all three legs, so the only difference between
    the numbers is how much of the stack each of them goes through. The statements
    run on the instance's read-only handle rather than its writable one: reaching
    for the connection a call would use means reaching past `Instance`, and a
    second connection on the same file reads the same pages.
    """
    read = WORKLOADS["read"]
    with sessions(world, read, 1) as (only,):
        pool = issue_ids(only.instance)
        db = only.instance.inspect()
        conn = db.conn

        def through_db(index: int) -> object:
            issue_id = pool[index % len(pool)]
            db.rows(SHARE_STATEMENTS[0], issue_id)
            return db.rows(SHARE_STATEMENTS[1], issue_id)

        def on_the_cursor(index: int) -> object:
            # What `Db.rows` does with the cursor and nothing else: step it to
            # exhaustion. No dict per row, no error translation, no wrapper.
            issue_id = pool[index % len(pool)]
            for statement in SHARE_STATEMENTS:
                for _row in conn.execute(statement, (issue_id,)):
                    pass
            return None

        for index in range(calls):  # warm all three paths before any is timed
            only.caller(index)
            through_db(index)
            on_the_cursor(index)
        by_call = [closed_loop([only.caller], calls) for _ in range(repeats)]
        by_db = [closed_loop([through_db], calls) for _ in range(repeats)]
        by_cursor = [closed_loop([on_the_cursor], calls) for _ in range(repeats)]
    return Share(
        calls=calls * repeats,
        call_seconds=_median_seconds_per_call(by_call),
        db_seconds=_median_seconds_per_call(by_db),
        cursor_seconds=_median_seconds_per_call(by_cursor),
    )


def _median_seconds_per_call(runs: list[Run]) -> float:
    return 1 / summarise(runs).rate_median
