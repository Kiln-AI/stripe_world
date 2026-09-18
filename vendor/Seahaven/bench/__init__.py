"""Seahaven's benchmark: ProjectTracker workloads, a gate sweep, and a composite tree.

Run by hand and before a release (`architecture.md` §10), never in CI and never
as a gate: nothing here fails a build, and no number here is a threshold. What it
is for is the three questions the framework cares about -- what a call costs,
what the concurrency gate's size does to a process serving many sessions, and
what a second node of a composite world costs -- asked the same way twice so that
two runs on one machine can be compared.

Read the output the way the output asks to be read. A benchmark run on a shared
virtual machine measures that machine on that afternoon; `bench/results/latest.md`
opens with what its figures can and cannot be used for, and every table in it
carries the spread of its own repeats so a reader can refuse to believe a
difference smaller than the noise.

    uv run python -m bench all --out bench/results/latest.md

`bench/README.md` has the rest of the commands and what they cost in wall time.
"""
