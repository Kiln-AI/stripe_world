# The benchmark

Two workloads over ProjectTracker `agency`, a sweep of the concurrency gate, what a node of a
composite world costs, and what the change log costs a call. Run by hand and before a release
(`architecture.md` §10). **Never a gate:** it is not in CI, nothing fails on a number, and no
threshold is asserted anywhere.

The committed results are [`results/latest.md`](results/latest.md). Read the top of that document
before reading any number in it.

## Running it

From the repository root:

```sh
uv run python -m bench all --out bench/results/latest.md    # everything, ~10 min
uv run python -m bench baseline                             # one thread, ~1 min
uv run python -m bench sweep --progress                     # the gate sweep, ~9 min
uv run python -m bench isolation                            # the slow-call probe, ~1 min
uv run python -m bench composite                            # what a node costs, ~10 s
uv run python -m bench recording                            # what the change log costs, ~1 min
```

With no `--out` the report goes to stdout. `--progress` prints a line per point on stderr, which
is worth having on the sweep. `--quick` shrinks every count to something that finishes in seconds
and measures nothing: it is for checking that the harness runs.

Useful knobs: `--repeats`, `--calls`, `--baseline-calls`, `--gates 1 2 4 0`, `--workers 4 32`,
`--seconds`, `--readers`, `--composite-calls`, `--tree-repeats`, `--seed`, `--warm-only`.
`python -m bench --help` lists them all.

The recording probe needs a longer run than these defaults before its totals settle: at 200 calls
over 3 repeats a write probe's total moves by tens of points between runs, and comes out well
above what a longer run gives. The figures in `src/seahaven/docs/state.md` come from
`--calls 1000 --repeats 9`, which is what to run when a number is going to be quoted.

## Before you believe a number

- Run it on an otherwise idle machine, and say what that machine was. The report captures the
  interpreter, the libraries, the commit, the CPU and the filesystem for you.
- Repeats are whole passes in a shuffled order, and every cell prints its own spread. A difference
  smaller than that spread is not a difference.
- The latency columns come from a closed loop with no think time. They are saturation numbers, not
  what a session on an unsaturated server would see.
- Cold-cache runs drop the instance's pages from the *guest's* page cache with
  `posix_fadvise(DONTNEED)`. Nothing here can drop a host's cache, so a cold figure is a floor.
  The write mix's warm and cold columns also differ by more than a cache -- its warm-up writes as
  many rows as the measured pass will -- so they carry no cache conclusion at all.
- Throughput and waiting are not independent in a fixed-call-count pass: a pass ends when its
  slowest session finishes, so a configuration that starves someone also runs its tail below full
  load. And a call-bounded pass cannot show unfairness except through `worst`; that is what the
  time-bounded isolation probe is for.

## Writing up a run

`results/latest.md` is the generated report plus a hand-written `## Reading` section at the end:
what the tables mean, what was confirmed or changed, and what an operator should do. Regenerating
the file refuses to clobber that section unless you pass `--force` — and if you pass it, write the
reading again from the new tables rather than keeping the old one.

**Section 7 of the committed report was measured on its own** and pasted in above `## Reading`,
because the sections before it are an older run that this repository's environment note says is a
maintainer's call to replace. That is why the composite section carries a provenance line naming its
own command, commit and machine: a section that can be regenerated alone (`python -m bench
composite`) can end up beside tables from another run, and it has to say so. A full
`all --force` run regenerates every section together, and the provenance line then repeats what the
Environment table already says.

## Layout

| File | What it holds |
|---|---|
| `harness.py` | threads, quantiles, the page cache, the gate. Knows nothing about worlds |
| `workloads.py` | the two workloads over `agency`, and the slow statement |
| `runner.py` | one measured point: N sessions, a cache state, a fixed number of calls |
| `baseline.py` | one thread: what a call costs, and how much of it is SQLite |
| `sweep.py` | the gate sweep, and the slow-call isolation probe |
| `composite.py` | what a node costs: the `tests/worlds/` ladder stood up, and driven |
| `recording.py` | what the change log costs a call, split three ways, at one node and at four |
| `environment.py` | what the numbers were produced on |
| `report.py` | the markdown, caveats first |
| `__main__.py` | the CLI |

`tests/test_bench.py`, in the framework suite, proves the instrument still works. It asserts no
speed.
