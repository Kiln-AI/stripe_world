"""The two workloads, over ProjectTracker `agency`, and the slow statement.

`architecture.md` §10 names them: one-row read, write mix. Both drive the world
the way an eval does -- `world.instance("agency")`, then `instance.call(...)` --
so what is measured is the whole call: argument validation, the middleware chain,
the transaction, the tool, the change log's session and the serialiser.

`agency` and not a world of the benchmark's own, because a benchmark on a
purpose-built schema measures the benchmark. This is the world the framework
ships, at its largest fixture: twelve people, nine projects, six hundred issues
with six months of history behind them.
"""

import random
from collections.abc import Callable
from dataclasses import dataclass, field

from bench.harness import Caller
from seahaven import Instance, World

__all__ = [
    "FIXTURE",
    "SHARE_STATEMENTS",
    "SLOW_STATEMENT",
    "WORKLOADS",
    "Session",
    "Workload",
    "issue_ids",
]

# The fixture every measurement here runs on.
FIXTURE = "agency"

# The isolation probe's slow call: a self-join an agent could write through the
# world's SQL door, costing tens of milliseconds inside SQLite. A `sleep` bolted
# into a tool would measure a sleep; this measures a call that is genuinely slow
# for the reason a world's calls are genuinely slow.
SLOW_STATEMENT = "SELECT sum(length(a.description) + length(b.title)) FROM issues a, issues b"

# What `get_issue` runs underneath, for the framework-versus-SQLite comparison:
# the issue's row, then its labels. They mirror `projecttracker.tools._rows`, and
# `tests/test_bench.py` checks they still match -- the coupling belongs in a test
# rather than in an import of another package's private module.
SHARE_STATEMENTS = (
    "SELECT id, project_id, key, title, description, status, priority, assignee_id,"
    " creator_id, created_at, updated_at, due_at, archived_at FROM issues WHERE id = ?",
    "SELECT issue_id, label_id FROM issue_labels WHERE issue_id IN (?) ORDER BY issue_id, label_id",
)

# One fixed permutation of the fixture's issues, so two runs drive the same rows
# in the same order.
SHUFFLE_SEED = 20260913


@dataclass(frozen=True)
class Workload:
    """A named way to drive an instance, and how many calls make one unit of it.

    `prepare` reads whatever the workload needs from an instance -- the ids it
    will drive, the people it will assign to -- and returns the function that
    performs one call. `cycle` is how many calls make one whole unit of work: 1
    for a read, 4 for the write mix, whose four calls are one issue's short life.
    A measurement rounds its call count to a multiple of it, so no pass is cut
    halfway through a cycle and reported as the whole mix.
    """

    name: str
    description: str
    cycle: int
    prepare: Callable[[Instance], Caller] = field(repr=False)


def _read(instance: Instance) -> Caller:
    """One row, by id, through the whole call path.

    Every issue in the fixture is driven once before any is driven twice, in a
    seeded shuffle. That is what makes the cold-cache variant mean something: a
    handful of hot rows would be warm again after the first few calls, whatever
    the page cache started as.
    """
    pool = issue_ids(instance)

    def read(index: int) -> object:
        return instance.call("get_issue", issue_id=pool[index % len(pool)])

    return read


def _write_mix(instance: Instance) -> Caller:
    """An issue's short life, four writes at a time.

    File it, comment on it, start it, give it to somebody: four calls, four
    transactions, four rows on the audit trail, and a fifth write nobody asked
    for -- the key `ENG-41` is minted by incrementing the team's counter. Each
    call reads its subject back, which is what this product's tools do.
    """
    project = _column(instance, "SELECT id FROM projects WHERE state = 'active' ORDER BY id")[0]
    people = _column(instance, "SELECT id FROM users ORDER BY id")
    # The issue the rest of the cycle works on. A dict and not a `nonlocal` so the
    # four steps can stay separate functions and still share one subject.
    subject: dict[str, str] = {}

    def create(index: int) -> object:
        issue = instance.call(
            "create_issue",
            project_id=project,
            title=f"benchmark issue {index}",
            description="Filed by bench/workloads.py, over and over. " * 3,
        )
        subject["issue"] = str(issue["id"])
        return issue

    def comment(index: int) -> object:
        return instance.call(
            "add_comment", issue_id=subject["issue"], body=f"benchmark comment {index}"
        )

    def transition(_index: int) -> object:
        return instance.call("transition_issue", issue_id=subject["issue"], status="in_progress")

    def assign(index: int) -> object:
        return instance.call(
            "assign_issue", issue_id=subject["issue"], assignee_id=people[index % len(people)]
        )

    steps = (create, comment, transition, assign)

    def mix(index: int) -> object:
        return steps[index % len(steps)](index)

    return mix


WORKLOADS: dict[str, Workload] = {
    workload.name: workload
    for workload in (
        Workload(
            name="read",
            description=(
                "get_issue by id, walking the fixture's 600 issues in a seeded shuffle: a pass "
                "of 600 calls drives every issue exactly once, a shorter pass the first N of "
                "the shuffle, a longer one wraps"
            ),
            cycle=1,
            prepare=_read,
        ),
        Workload(
            name="write_mix",
            description=(
                "create_issue, add_comment, transition_issue, assign_issue, in that cycle"
            ),
            cycle=4,
            prepare=_write_mix,
        ),
    )
}


@dataclass(frozen=True)
class Session:
    """One instance and the caller that drives it: what a served session is.

    An instance per thread, because an instance serialises its own calls under its
    lock and a shared one would measure the lock rather than the framework -- and
    because one instance per session is how OpenEnv serves them.
    """

    instance: Instance
    caller: Caller = field(repr=False)

    @staticmethod
    def open(world: World, workload: Workload) -> Session:
        """A fresh instance of `agency`, prepared for one workload.

        Preparing reads the ids the workload will drive, which warms the
        instance's page cache; a cold measurement evicts afterwards, which is why
        `bench.sweep` evicts once every session is prepared and not at creation.
        """
        instance = world.instance(FIXTURE)
        try:
            return Session(instance=instance, caller=workload.prepare(instance))
        except BaseException:
            instance.destroy()
            raise


def issue_ids(instance: Instance) -> list[str]:
    """Every issue of the fixture, in the one permutation every run drives.

    Shared by the read workload and by the framework-versus-SQLite comparison,
    which has to drive the same rows in the same order to be comparing anything.
    """
    shuffled = _column(instance, "SELECT id FROM issues ORDER BY id")
    random.Random(SHUFFLE_SEED).shuffle(shuffled)
    return shuffled


def _column(instance: Instance, sql: str) -> list[str]:
    """One column of a query, read through the instance's read-only handle."""
    return [str(next(iter(row.values()))) for row in instance.inspect().rows(sql)]
