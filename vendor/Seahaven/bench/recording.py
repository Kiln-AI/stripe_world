"""What the change log costs a call, against the long-lived session it replaced.

`architecture.md` §16 asks for this number. Until this release an instance kept a
single `apsw.Session` per node for its whole life, and `Instance.changes()` read
it out when an eval asked; now every call opens a session on every node, attaches
each tracked table to it, reads its changeset when the call's transactions are
done, and renders that into the log records the state document carries.

The alternative that was rejected -- keeping the long-lived session and diffing
it per call -- is O(everything the episode changed) per call rather than O(what
the call changed), which is the cost the change log exists to keep off a harness.
What this measures is what the shape that was chosen costs instead.

Three legs per probe, on one instance, because two would say how much and not
where:

1. **A session per node per call**, as Seahaven runs it.
2. **A session per node per call, never read**, opened and attached and closed
   with nothing taken out of it. The difference from the leg above is
   `changeset()` and `render_log`: the work that moved from an eval's `changes()`
   call, once an episode, to every call.
3. **One long-lived session per node**, opened once for the whole pass and never
   read: the shape the framework had before this release. The difference from the
   leg above is everything a *fresh* session does that a warmed-up one does not
   -- opening it, attaching its tables, its first sighting of each table it
   records (the `PRAGMA table_xinfo` `sandbox.py`'s authorizer has to allow), and
   freeing a populated change buffer at `close()`. Which of those dominates
   depends on how much the call wrote, so the probes answer differently. This leg
   is the noisiest of the three and should not be read to a point.

Legs 2 and 3 are not configurations Seahaven offers. They exist to split the
number in leg 1, and they reach `instance._runtime` to do it; that is bench code,
never importable from `seahaven`.

Two worlds, because a session is per node and `architecture.md` §16 accepts a
cost linear in node count rather than assuming it: ProjectTracker `agency` is one
node, and `emporium` is four. Within each, a workload that writes and one that
does not, because a read changes no rows and so has nothing to render and nothing
to free.
"""

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, ExitStack, closing, contextmanager
from dataclasses import dataclass

import apsw

from bench.composite import legs, trees
from bench.harness import Caller, Run, closed_loop
from bench.runner import whole_cycles
from bench.workloads import FIXTURE, WORKLOADS
from seahaven import Instance, World
from seahaven.changes import open_session

__all__ = [
    "LEGS",
    "LONG_LIVED",
    "PER_CALL",
    "UNREAD",
    "HasRecorded",
    "Probe",
    "Recorder",
    "Recording",
    "long_lived_sessions",
    "probes",
    "recording",
    "rotated",
    "seconds_per_call",
    "unread_sessions",
]

# The three legs, in the order the report reads them. Named rather than
# positional because a pass is run by name, out of order, once the rotation
# below starts moving them.
PER_CALL = "per_call"
UNREAD = "unread"
LONG_LIVED = "long_lived"
LEGS: tuple[str, ...] = (PER_CALL, UNREAD, LONG_LIVED)

# What `Instance._recording` is: a context manager over one call's writes on every
# node, taking that call's ordinal.
type Recorder = Callable[[int | None], AbstractContextManager[None]]

# What a comparison leg reports to a test: whether any of its sessions has a
# changeset to give. `sqlite3session_isempty` and not the changeset's size, which
# SQLite only tracks when a session is configured to -- configuring one would put
# work into the leg that the leg is there to leave out. Read in tens of
# nanoseconds, against a call of hundreds of microseconds.
type HasRecorded = Callable[[], bool]


@dataclass(frozen=True)
class Probe:
    """One line of the table: a world, a fixture and a way to drive an instance."""

    world_name: str
    workload: str
    nodes: int
    world: World
    fixture: str | None
    # How many calls make one whole unit of work, so a pass is never cut halfway
    # through a cycle -- `runner.whole_cycles`, which the sweep rounds by too.
    cycle: int
    prepare: Callable[[Instance], Caller]


@dataclass(frozen=True)
class Recording:
    """One probe, one instance, recorded three ways. Seconds are per call."""

    world: str
    workload: str
    nodes: int
    calls: int
    per_call_seconds: float
    unread_seconds: float
    long_lived_seconds: float
    # Whether any call of the pass gave a session something to record, asked of
    # the sessions rather than assumed from the workload's name. A workload that
    # writes nothing has nothing to render and nothing to free, so the split
    # between its legs is noise and the report says so instead of reading it.
    wrote_rows: bool

    @property
    def overhead(self) -> float:
        """What the change log adds to a call, against one long-lived session per node."""
        return self._share(self.per_call_seconds - self.long_lived_seconds)

    @property
    def rendering(self) -> float:
        """The part of that which is `changeset()` and `render_log`."""
        return self._share(self.per_call_seconds - self.unread_seconds)

    @property
    def total_reads(self) -> bool:
        """Whether this run's total can be read as a cost at all.

        The full leg does everything the long-lived leg does and more: it opens
        and attaches a session on every node per call where the long-lived leg
        opens each once, and it reads a changeset back besides. So a run that
        timed the full leg no dearer than the long-lived one has measured its own
        passes, and the total taken from it comes out negative -- a cost that
        cannot exist, whether or not the pass wrote rows.

        `split_reads` is this same reading taken one leg finer, and implies this
        one.
        """
        return self.per_call_seconds > self.long_lived_seconds

    @property
    def split_reads(self) -> bool:
        """Whether this run's legs can be read as shares of the total at all.

        Each leg does strictly less than the one above it: the full leg does
        everything the never-read leg does and then reads and renders the
        changeset, and the never-read leg opens and attaches a session on every
        node per call where the long-lived leg opens each once. So a run that did
        not time them in that order has measured its own passes, and a share
        taken from it would be negative -- a cost that cannot exist. A probe
        whose calls wrote no rows has nothing to render either way.

        The report prints `noise` in place of every share in those cases, rather
        than a percentage that invites being read.
        """
        return (
            self.wrote_rows
            and self.per_call_seconds > self.unread_seconds > self.long_lived_seconds
        )

    def _share(self, seconds: float) -> float:
        """One leg's difference from the long-lived leg, as a fraction of that leg."""
        if not self.long_lived_seconds:
            return 0.0
        return seconds / self.long_lived_seconds


def probes(world: World) -> tuple[Probe, ...]:
    """ProjectTracker's two workloads at one node, and `emporium`'s three at four.

    `emporium`'s legs are `bench.composite`'s own, so the calls timed here are
    the calls section 7 times and the two tables can be read together. `shop` and
    the `payments` leaf are not here: what this probe asks is what a *tree* of
    nodes costs a call that records, and the leaf is the one-node case
    ProjectTracker already answers on a real schema.
    """
    composite = [leg for leg in legs(trees()) if leg.tree.name == "emporium"]
    return (
        *(
            Probe(
                world_name="ProjectTracker agency",
                workload=workload.name,
                nodes=1,
                world=world,
                fixture=FIXTURE,
                cycle=workload.cycle,
                prepare=workload.prepare,
            )
            for workload in WORKLOADS.values()
        ),
        *(
            Probe(
                world_name=leg.tree.name,
                workload=leg.workload,
                nodes=leg.tree.nodes,
                world=leg.tree.world,
                fixture=None,
                # Every composite leg is one call, as `bench.composite` drives them.
                cycle=1,
                prepare=leg.prepare,
            )
            for leg in composite
        ),
    )


def recording(probe: Probe, *, calls: int, repeats: int) -> Recording:
    """Time one probe down all three legs, on one instance.

    **The leg order rotates between repeats**, so that each leg runs first,
    second and last in turn. An instance's write cost climbs slowly as its tables
    grow, and a fixed order hands the climb to whichever leg runs last -- which
    here is the long-lived leg, the denominator of both figures this reports, so
    a fixed order would understate the change log's overhead every time. Running
    the legs as three blocks is the same bias, larger.

    The rotation gives every leg the same *mean* pass position, and that is why
    `seconds_per_call` reduces a leg with the mean and not with the median. A
    median of three passes is one pass, and the three legs' middle-ranked passes
    are three different positions whatever the schedule, so a median would leave
    a bias the rotation cannot reach. The mean costs the median's resistance to
    one slow pass; a stray pass is noise that averages out over runs, while a
    position bias is systematic and does not, and these three figures are
    published as differences from each other.

    The balance holds over *whole* rotations only, so `repeats` has to be a
    positive multiple of the leg count and a run that asks for anything else is
    refused here. A report that printed a partly-rotated run would claim a
    balance its numbers do not have, and the figures are published.
    """
    if repeats < 1 or repeats % len(LEGS):
        raise ValueError(
            f"repeats must be a positive multiple of {len(LEGS)}, the number of legs, so that "
            f"every leg gets the same mean pass position; got {repeats}"
        )
    per_pass = whole_cycles(calls, probe.cycle)
    runs: dict[str, list[Run]] = {leg: [] for leg in LEGS}
    wrote_rows = False
    with _live(probe) as (instance, caller):
        for index in range(per_pass):  # warm the path before any leg is timed
            caller(index)
        for repeat in range(repeats):
            for leg in rotated(repeat):
                with _recorded_for(leg, instance) as has_recorded:
                    runs[leg].append(closed_loop([caller], per_pass))
                    # After the pass, so reading it is no part of what was timed.
                    wrote_rows = wrote_rows or has_recorded()
    return Recording(
        world=probe.world_name,
        workload=probe.workload,
        nodes=probe.nodes,
        calls=per_pass * repeats,
        per_call_seconds=seconds_per_call(runs[PER_CALL]),
        unread_seconds=seconds_per_call(runs[UNREAD]),
        long_lived_seconds=seconds_per_call(runs[LONG_LIVED]),
        wrote_rows=wrote_rows,
    )


def rotated(repeat: int) -> tuple[str, ...]:
    """`LEGS`, turned by one place per repeat."""
    turn = repeat % len(LEGS)
    return LEGS[turn:] + LEGS[:turn]


@contextmanager
def _recorded_for(leg: str, instance: Instance) -> Iterator[HasRecorded]:
    """Put the instance on one leg's recorder for the length of a pass.

    Every leg answers the same question about itself -- did anything it was
    recording actually see a row -- through the mechanism that leg uses, so a leg
    that quietly stopped recording is caught wherever the rotation puts it.
    """
    # Against the constants and not against string literals, which a `match`
    # would bind rather than compare.
    if leg == PER_CALL:
        # Seahaven's own recorder, untouched; the log is what it leaves. Its
        # length before the pass, because the log is cumulative: the warm-up and
        # a workload's own preparation have already written to it, so what is
        # asked is whether *this pass* added to it.
        before = len(instance.change_log())
        yield lambda: len(instance.change_log()) > before
    elif leg == UNREAD:
        with unread_sessions(instance) as has_recorded:
            yield has_recorded
    elif leg == LONG_LIVED:
        with long_lived_sessions(instance) as has_recorded:
            yield has_recorded
    else:
        raise ValueError(f"unknown leg: {leg!r}")


@contextmanager
def unread_sessions(instance: Instance) -> Iterator[HasRecorded]:
    """A session per node per call, opened and attached and closed, with nothing read.

    Yields whether any call's sessions have had anything to give, asked of each
    just before it was closed: what the leg records has to be observable, or a
    leg that quietly stopped opening a session at all would still measure
    something and still look right.
    """
    recorded = [False]

    @contextmanager
    def opened(_i: int | None) -> Iterator[None]:
        with ExitStack() as stack:
            sessions = _attached(instance, stack)
            try:
                yield
            finally:
                recorded[0] = recorded[0] or any(not session.is_empty for session in sessions)

    with _recorded_by(instance, opened):
        yield lambda: recorded[0]


@contextmanager
def long_lived_sessions(instance: Instance) -> Iterator[HasRecorded]:
    """Record this instance's writes the way Seahaven did before the change log.

    One session per node for the length of the block, attached to the tables
    every per-call session attaches, and nothing read out of them -- which is
    also how the long-lived sessions behaved between calls: they were rendered
    when an eval asked, not once per call. Yields whether they have anything to
    give, for the reason above.
    """
    with ExitStack() as stack:
        sessions = _attached(instance, stack)
        with _recorded_by(instance, _nothing):
            yield lambda: any(not session.is_empty for session in sessions)


def _attached(instance: Instance, stack: ExitStack) -> list[apsw.Session]:
    """One session on every node of the instance, closed when `stack` unwinds."""
    return [
        stack.enter_context(closing(open_session(runtime.db.conn, runtime.tracked)))
        for runtime in instance._runtime.values()
    ]


@contextmanager
def _recorded_by(instance: Instance, recorder: Recorder) -> Iterator[None]:
    """Run this instance's calls through another recorder for the length of the block.

    Shadowed on the instance and not on the class, so the measurement cannot
    escape into another instance, and deleted afterwards so the method the class
    defines is what answers again.
    """
    instance._recording = recorder  # ty: ignore[invalid-assignment]
    try:
        yield
    finally:
        del instance._recording


@contextmanager
def _nothing(_i: int | None) -> Iterator[None]:
    yield


@contextmanager
def _live(probe: Probe) -> Iterator[tuple[Instance, Caller]]:
    """One instance of the probe's world, prepared for its workload and destroyed after."""
    instance = probe.world.instance(probe.fixture)
    try:
        yield instance, probe.prepare(instance)
    finally:
        instance.destroy()


def seconds_per_call(runs: list[Run]) -> float:
    """Several passes as a single cost, pooled: total seconds over total calls.

    Deliberately not `harness.summarise`'s median rate. It is a mean of costs and
    not of rates, because the growth `recording`'s rotation cancels adds time to
    a call rather than scaling its rate.

    Pooling weights each pass by its own call count. Equal-sized passes are
    `recording`'s precondition rather than this function's, and under it the
    pooled figure is the plain arithmetic mean of the passes' seconds per call,
    which is what the rotation balances.
    """
    if not runs:
        raise ValueError("seconds_per_call needs at least one run")
    return sum(run.seconds for run in runs) / sum(run.calls for run in runs)
