"""What a second node costs: the ladder `architecture.md` §15 assumes a floor from.

§15 says an idle composite instance is N stores, N connections and N sessions,
and that until this module the benchmark measured one node per instance, a
figure read as a per-node floor. Nothing had ever measured the second node.
This module measures it, in two halves that answer different questions:

1. **Standing a tree up.** The seal, `world.instance()`, what that leaves on
   disk, and `destroy()` -- at one node, two and four, which is the whole
   committed ladder. The increment per added node is then arithmetic on three
   points rather than an assumption.
2. **Driving it.** The same tool on a leaf instance of `payments` and on the
   `payments` node of a four-node `emporium` instance. A call opens one node's
   transaction (`call.py`), so the prediction is that a call costs what it costs
   whatever the tree around it; the pair is what turns reading the code into
   measuring it.

`tests/worlds/{payments,shop,emporium}` and not a tree of the benchmark's own,
for the reason `workloads.py` gives about `agency`. It costs honesty about what
these worlds are: they are the smallest in the repository -- a `payments` node is
one table and two tools -- so a node here is close to an empty node, which is the
right shape for a floor and the wrong shape for a typical store. The report says
so beside the tables.

One thread, warm, no gate sweep. A per-call cost is a single-threaded number, for
the reason `baseline.py` gives, and the composite asks no question about the gate
that the ProjectTracker sweep does not already ask.
"""

import functools
import statistics
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from bench.harness import Caller, Run, Summary, closed_loop, summarise
from seahaven import Instance, World
from seahaven.composition import bump

__all__ = [
    "READ",
    "SETTLE",
    "TREE_PACKAGES",
    "WRITE",
    "CallCost",
    "Composite",
    "Leg",
    "Tree",
    "TreeCost",
    "composite",
    "legs",
    "per_call",
    "standing_up",
    "trees",
]

# The committed composite of `tests/worlds/README.md`, shallowest first. Nothing
# installs these packages -- they are fixtures of the framework's own suite -- so
# their source directories go on the path the way `tests/conftest.py` puts them
# there, because a host reaches a world it adds by importing it.
WORLDS = Path(__file__).resolve().parent.parent / "tests" / "worlds"
TREE_PACKAGES = ("payments", "shop", "emporium")

# What a line of the per-call table is called. Named here rather than written
# into `legs()` as literals, because `report._call_deltas` pairs the two trees of
# one workload and singles out the cross-node call by these names: a rename that
# reached only one of the two files would drop the derived bullets in silence.
READ = "one-row read"
WRITE = "one-row write"
SETTLE = "settle_order"


@dataclass(frozen=True)
class Tree:
    """One rung of the ladder: a world, and how many nodes it seals to."""

    name: str
    world: World

    @property
    def nodes(self) -> int:
        """Read from the seal rather than written down, so the table cannot drift."""
        return len(self.world.composition().nodes)


@dataclass(frozen=True)
class TreeCost:
    """What one tree costs before a call is made: on disk, and in time.

    `stores` is the databases -- one per node -- and `files` is everything the
    directory holds, which is each store plus the `-wal` and `-shm` SQLite keeps
    beside it. Both are counted off a live instance rather than derived from the
    node count, because "N files" is the claim under test.
    """

    tree: str
    nodes: int
    stores: int
    files: int
    idle_bytes: int
    seal_seconds: float
    open_seconds: float
    open_min: float
    open_max: float
    destroy_seconds: float
    destroy_min: float
    destroy_max: float


@dataclass(frozen=True)
class CallCost:
    """One workload on one tree, over every repeat."""

    workload: str
    tree: str
    nodes: int
    summary: Summary


@dataclass(frozen=True)
class Leg:
    """One line of the per-call table: a workload, on one tree, by that tree's names.

    `prepare` is `workloads.Workload.prepare`'s shape -- read what the workload
    needs from a fresh instance, return the function that performs one call --
    because the harness that drives it is the same one.
    """

    workload: str
    tree: Tree
    prepare: Callable[[Instance], Caller]


@dataclass(frozen=True)
class Composite:
    """Both halves of the measurement, and the counts that produced them."""

    trees: tuple[TreeCost, ...]
    calls: tuple[CallCost, ...]
    calls_per_pass: int
    repeats: int
    tree_repeats: int


@functools.cache
def trees() -> tuple[Tree, ...]:
    """The ladder, imported the way a host imports a world it adds.

    Cached, and the imports are inside the function rather than at the top of the
    module, for the same reason `bench.__main__._world()` imports ProjectTracker
    inside a function: a world is reached by importing its package, and this one
    needs `sys.path` prepared first. Node identity is object identity, so every
    caller must get the same `World` objects -- which is what the cache is for.
    """
    for package in TREE_PACKAGES:
        source = str(WORLDS / package / "src")
        if source not in sys.path:
            sys.path.insert(0, source)
    import emporium
    import payments
    import shop

    return (
        Tree(name="payments", world=payments.world),
        Tree(name="shop", world=shop.world),
        Tree(name="emporium", world=emporium.world),
    )


def standing_up(ladder: tuple[Tree, ...], *, repeats: int) -> list[TreeCost]:
    """Seal, open, measure and destroy each tree `repeats` times."""
    return [_tree_cost(tree, repeats=repeats) for tree in ladder]


def _tree_cost(tree: Tree, *, repeats: int) -> TreeCost:
    """One tree's standing-up cost, after a discarded pair.

    The discarded open and destroy are the warm-up the per-call half gets from
    `runner.measure`'s rule, and this half needs it for the same reason. What was
    observed is a penalty on the *process's* first open rather than on each
    tree's: the first tree measured opened several milliseconds slower than its
    own steady state, while the trees after it showed no first-pass outlier this
    machine could tell from their spread -- consistent with the work root not
    existing yet, the once-per-process sweep of abandoned directories not having
    run, and nothing on the path being warm. Discarding a pair per tree is the
    uniform rule rather than three special cases, and timing the process's first
    open would put a number in the min-max columns that is not the pass-to-pass
    spread those columns are read as.
    """
    if repeats < 1:
        raise ValueError("a tree is measured at least once")
    seals = [_one_seal(tree.world) for _ in range(repeats)]
    footprint, _discarded, _pair = _open_and_destroy(tree, inspect=True)
    assert footprint is not None  # asked for with `inspect=True`
    measured = [_open_and_destroy(tree) for _ in range(repeats)]
    opens = [opening for _footprint, opening, _destroying in measured]
    destroys = [destroying for _footprint, _opening, destroying in measured]
    return TreeCost(
        tree=tree.name,
        nodes=tree.nodes,
        stores=footprint.stores,
        files=footprint.files,
        idle_bytes=footprint.idle_bytes,
        seal_seconds=statistics.median(seals),
        open_seconds=statistics.median(opens),
        open_min=min(opens),
        open_max=max(opens),
        destroy_seconds=statistics.median(destroys),
        destroy_min=min(destroys),
        destroy_max=max(destroys),
    )


def _open_and_destroy(
    tree: Tree, *, inspect: bool = False
) -> tuple[_Footprint | None, float, float]:
    """One instance opened and destroyed, both halves timed.

    The directory is read only where it is asked for, which is the discarded
    warm-up pair: walking twelve files between the two timers would be work that
    grows with the node count, sitting in the middle of the measurement of how
    much a node costs.
    """
    began = time.perf_counter()
    instance = tree.world.instance()
    opening = time.perf_counter() - began
    try:
        footprint = _footprint(instance.dir) if inspect else None
    finally:
        began = time.perf_counter()
        instance.destroy()
        destroying = time.perf_counter() - began
    return footprint, opening, destroying


def _one_seal(world: World) -> float:
    """How long one whole seal of this tree takes, from cold.

    `bump()` is what every registration verb calls, and it invalidates every
    cached seal in the process -- so the next `composition()` is a full walk and
    not a cache hit. The cost of that over-invalidation is one reseal per world
    that is used again afterwards, which is why this runs last in a full report.
    """
    bump()
    began = time.perf_counter()
    world.composition()
    return time.perf_counter() - began


@dataclass(frozen=True)
class _Footprint:
    stores: int
    files: int
    idle_bytes: int


def _footprint(directory: Path) -> _Footprint:
    """What an instance that has served nothing holds on disk."""
    files = [path for path in sorted(directory.iterdir()) if path.is_file()]
    return _Footprint(
        stores=sum(1 for path in files if path.suffix == ".sqlite"),
        files=len(files),
        idle_bytes=sum(path.stat().st_size for path in files),
    )


def legs(ladder: tuple[Tree, ...]) -> tuple[Leg, ...]:
    """The per-call table's lines: the same two tools at one node and at four.

    `shop` is not here. It adds `payments` with an empty allow list, so no
    payments tool is on its surface and there is no call it and the leaf both
    answer; its rung of the ladder is the standing-up table, where the question
    is the tree and not the call.
    """
    by_name = {tree.name: tree for tree in ladder}
    leaf, host = by_name["payments"], by_name["emporium"]
    return (
        Leg(READ, leaf, _reading("list_charges", "create_charge")),
        Leg(READ, host, _reading("pay_list_charges", "pay_create_charge")),
        Leg(WRITE, leaf, _writing("create_charge")),
        Leg(WRITE, host, _writing("pay_create_charge")),
        Leg(SETTLE, host, _settling),
    )


def _reading(list_tool: str, write_tool: str) -> Callable[[Instance], Caller]:
    """Read an account holding exactly one charge.

    One charge, written once in preparation, so every call of the pass returns
    one row: `list_charges` answers the whole account, and a pass that wrote as
    it read would measure a list growing rather than a row being read.
    """

    def prepare(instance: Instance) -> Caller:
        instance.call(write_tool, amount=1)

        def read(_index: int) -> object:
            return instance.call(list_tool)

        return read

    return prepare


def _writing(write_tool: str) -> Callable[[Instance], Caller]:
    """One row into the node's own store per call."""

    def prepare(instance: Instance) -> Caller:
        def write(index: int) -> object:
            return instance.call(write_tool, amount=index + 1)

        return write

    return prepare


def _settling(instance: Instance) -> Caller:
    """One call over three nodes: the shop's order, the account's charge, the host's own row.

    What the composite adds and a leaf has no equivalent of -- two nested
    `handle.call`s through `ctx.worlds`, three stores written under one lock --
    so this row is read against the two `pay_` rows above it rather than against
    another tree.
    """

    def settle(index: int) -> object:
        return instance.call(SETTLE, total=index + 1)

    return settle


def per_call(lines: tuple[Leg, ...], *, calls: int, repeats: int) -> list[CallCost]:
    """Every leg, `repeats` passes of `calls` calls, each pass on a fresh instance.

    A repeat is a whole pass over every leg, as `sweep.py` makes a repeat a whole
    pass over every point and for the same reason: what this table exists to show
    is a difference between a leaf and a composite smaller than the machine's own
    noise, and running one leg's repeats back to back would hand any drift during
    the run to whichever leg was running when it happened. The order within a
    repeat is the declared one rather than a shuffle -- there are five legs and
    not a hundred points, and a fixed order is one less thing that differs
    between two runs.
    """
    runs: list[list[Run]] = [[] for _ in lines]
    for _ in range(repeats):
        for index, leg in enumerate(lines):
            runs[index].append(_one_pass(leg, calls))
    return [
        CallCost(
            workload=leg.workload,
            tree=leg.tree.name,
            nodes=leg.tree.nodes,
            summary=summarise(passes),
        )
        for leg, passes in zip(lines, runs, strict=True)
    ]


def _one_pass(leg: Leg, calls: int) -> Run:
    """One warm pass on an instance that has served exactly one identical pass.

    The warm-up is `runner.measure`'s, and so is the fresh instance per repeat and
    the reason for both: a write pass leaves rows and changeset entries behind, an
    instance's write cost climbs with them, and a long-lived instance would report
    that climb as the cost of a call. Both trees are treated identically, which is
    what makes the two rows a comparison.
    """
    with _live(leg.tree) as instance:
        caller = leg.prepare(instance)
        for index in range(calls):
            caller(index)
        return closed_loop([caller], calls)


@contextmanager
def _live(tree: Tree) -> Iterator[Instance]:
    """A blank instance of one tree, destroyed however the block ends."""
    instance = tree.world.instance()
    try:
        yield instance
    finally:
        instance.destroy()


def composite(*, calls: int, repeats: int, tree_repeats: int) -> Composite:
    """The whole section: what the trees cost to stand up, and what they cost a call."""
    ladder = trees()
    return Composite(
        trees=tuple(standing_up(ladder, repeats=tree_repeats)),
        calls=tuple(per_call(legs(ladder), calls=calls, repeats=repeats)),
        calls_per_pass=calls,
        repeats=repeats,
        tree_repeats=tree_repeats,
    )
