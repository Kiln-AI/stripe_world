"""`python -m bench`: run the measurements and write the report.

    uv run python -m bench all --out bench/results/latest.md

Six subcommands -- `baseline`, `sweep`, `isolation`, `composite`, `recording`,
`all` -- because a sweep takes minutes and someone changing the harness wants one
measurement back in seconds. `--quick` shrinks every count to something a test
can afford; it is not a measurement and the report it writes says the counts it
used.
"""

import argparse
import dataclasses
import sys
import time
from pathlib import Path
from typing import Any

from bench import report
from bench.baseline import baseline, share
from bench.composite import composite
from bench.environment import capture
from bench.harness import cold_cache_supported, quiet_logging
from bench.recording import LEGS, probes, recording
from bench.runner import CACHES, Cache
from bench.sweep import DEFAULT_GATES, DEFAULT_WORKERS, isolation, sweep
from bench.workloads import WORKLOADS
from seahaven import World

__all__ = ["main"]

# One pass of the read workload drives every issue in `agency` once, which is what
# makes its cold variant a cold measurement rather than a warm one after the first
# few rows.
BASELINE_CALLS = 600
SWEEP_CALLS = 200
REPEATS = 3
ISOLATION_SECONDS = 3.0
ISOLATION_READERS = 4
COMPOSITE_CALLS = 1000
TREE_REPEATS = 5
SEED = 11

# Typed loosely on purpose: these are argparse defaults of several shapes,
# written onto the parsed namespace by name.
QUICK: dict[str, Any] = {
    "baseline_calls": 8,
    "calls": 8,
    # Three and not one: `bench.recording` refuses a `repeats` that is not a
    # whole rotation of its legs, and a quick run that skipped the rotation would
    # print the balance the report claims without having done it.
    "repeats": 3,
    "seconds": 0.2,
    "readers": 2,
    "gates": (1, 0),
    "workers": (2,),
    "composite_calls": 4,
    "tree_repeats": 1,
}


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.quick:
        # After parsing rather than as defaults: `--quick` is a preset for
        # checking that the harness runs, and it overrides whatever else was
        # asked for rather than losing to it.
        for name, value in QUICK.items():
            setattr(args, name, value)
    if args.command in ("recording", "all") and (args.repeats < 1 or args.repeats % len(LEGS)):
        print(
            f"--repeats must be a positive multiple of {len(LEGS)} for the recording probe, so "
            f"that every leg gets the same mean pass position; got {args.repeats}",
            file=sys.stderr,
        )
        return 2
    if args.out is not None and not _may_write(Path(args.out), force=args.force):
        return 2
    world = _world()
    began = time.perf_counter()
    caches, cold_skipped = _caches(args)
    with quiet_logging():
        results = report.Results(
            environment=capture(),
            command=_command(argv),
            cold_skipped=cold_skipped,
            baseline=(
                tuple(
                    baseline(world, calls=args.baseline_calls, repeats=args.repeats, caches=caches)
                )
                if args.command in ("baseline", "all")
                else ()
            ),
            share=(
                share(world, calls=args.baseline_calls, repeats=args.repeats)
                if args.command in ("baseline", "all")
                else None
            ),
            sweep=(
                sweep(
                    world,
                    gates=args.gates,
                    workers=args.workers,
                    caches=caches,
                    workloads=tuple(WORKLOADS),
                    repeats=args.repeats,
                    calls=args.calls,
                    seed=args.seed,
                    progress=args.progress,
                )
                if args.command in ("sweep", "all")
                else None
            ),
            isolation=(
                isolation(
                    world,
                    gates=args.gates,
                    readers=args.readers,
                    seconds=args.seconds,
                    repeats=args.repeats,
                    progress=args.progress,
                )
                if args.command in ("isolation", "all")
                else None
            ),
            recording=(
                tuple(
                    recording(probe, calls=args.calls, repeats=args.repeats)
                    for probe in probes(world)
                )
                if args.command in ("recording", "all")
                else ()
            ),
            # Last, because a seal is timed by invalidating every cached
            # composition in the process: whatever runs after it pays for one
            # reseal of its own world.
            composite=(
                composite(
                    calls=args.composite_calls,
                    repeats=args.repeats,
                    tree_repeats=args.tree_repeats,
                )
                if args.command in ("composite", "all")
                else None
            ),
            seconds=0.0,
        )
    document = report.render(dataclasses.replace(results, seconds=time.perf_counter() - began))
    if args.out is None:
        print(document)
    else:
        Path(args.out).write_text(document, encoding="utf-8")
        print(f"wrote {args.out}", file=sys.stderr)
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m bench",
        description="Seahaven's benchmark: two workloads over ProjectTracker agency, a "
        "sweep of the concurrency gate, what a node of a composite world costs, and what the "
        "change log costs a call. Run by hand; never a gate on anything.",
    )
    parser.add_argument(
        "command",
        choices=("baseline", "sweep", "isolation", "composite", "recording", "all"),
        help="which measurements to run",
    )
    parser.add_argument("--out", default=None, help="write the report here instead of stdout")
    parser.add_argument(
        "--force",
        action="store_true",
        help="overwrite a report that carries a hand-written reading",
    )
    parser.add_argument(
        "--repeats",
        type=int,
        default=REPEATS,
        help="passes per configuration; the recording probe needs a whole rotation of its three "
        "legs, so a run including it takes a multiple of 3",
    )
    parser.add_argument(
        "--baseline-calls", type=int, default=BASELINE_CALLS, help="calls per baseline pass"
    )
    parser.add_argument(
        "--calls",
        type=int,
        default=SWEEP_CALLS,
        help="calls per session per sweep or recording pass",
    )
    parser.add_argument(
        "--gates",
        type=int,
        nargs="+",
        default=list(DEFAULT_GATES),
        help="gate sizes to sweep; 0 is no gate",
    )
    parser.add_argument(
        "--workers",
        type=int,
        nargs="+",
        default=list(DEFAULT_WORKERS),
        help="sessions calling at once",
    )
    parser.add_argument(
        "--seconds", type=float, default=ISOLATION_SECONDS, help="isolation probe window"
    )
    parser.add_argument(
        "--readers", type=int, default=ISOLATION_READERS, help="isolation probe reader sessions"
    )
    parser.add_argument(
        "--composite-calls",
        type=int,
        default=COMPOSITE_CALLS,
        help="calls per pass in the composite tables",
    )
    parser.add_argument(
        "--tree-repeats",
        type=int,
        default=TREE_REPEATS,
        help="times each composite tree is sealed, opened and destroyed",
    )
    parser.add_argument("--seed", type=int, default=SEED, help="the sweep's shuffle seed")
    parser.add_argument("--warm-only", action="store_true", help="skip the cold-cache runs")
    parser.add_argument(
        "--quick",
        action="store_true",
        help="tiny counts, for checking the harness rather than measuring anything",
    )
    parser.add_argument(
        "--progress", action="store_true", help="a line per point on stderr as it is measured"
    )
    return parser


def _caches(args: argparse.Namespace) -> tuple[tuple[Cache, ...], str | None]:
    """Warm always; cold where it was asked for and the page cache can be dropped.

    The reason travels with the answer. "Skipped because you said `--warm-only`"
    and "skipped because this platform cannot drop its page cache" are different
    facts about the run, and a report that derives the second from the absence of
    cold cells states a falsehood about the machine on every `--warm-only` run.
    """
    if args.warm_only:
        return ("warm",), "asked for with --warm-only"
    if not cold_cache_supported():
        return ("warm",), "this platform has no posix_fadvise"
    return CACHES, None


def _world() -> World:
    """ProjectTracker's world object, imported the way every tool imports a world."""
    import projecttracker

    return projecttracker.world


def _may_write(out: Path, *, force: bool) -> bool:
    if force or not out.exists() or report.READING not in out.read_text(encoding="utf-8"):
        return True
    print(
        f"refusing to overwrite {out}: it carries a hand-written `{report.READING}` section. "
        "Re-run with --force, and write the reading again from the new tables.",
        file=sys.stderr,
    )
    return False


def _command(argv: list[str] | None) -> str:
    """The command as the report should print it, so a reader can re-run it."""
    arguments = sys.argv[1:] if argv is None else argv
    return "uv run python -m bench " + " ".join(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
