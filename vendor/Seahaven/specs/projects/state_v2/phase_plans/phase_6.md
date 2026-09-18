---
status: complete
---

# Phase 6: The cost, measured

## Overview

The change log is built, the old surface is gone and the docs are written. What is left is the
number: what a call pays for a session per node per call, against the one long-lived session per
node the framework kept before this release. `bench/recording.py` and the report's section 8 landed
in phase 4; this phase runs them, publishes what they produced, and fixes the one finding phase 4's
review routed forward.

Three deliverables, in this order:

1. The carried finding in `bench/report.py`: in the unreadable-split branch the *total* is still
   printed as a signed measured figure, and it can itself be negative when the full leg comes out
   no dearer than the long-lived one. The full leg opens and attaches a session on every node per
   call where the long-lived leg opens each once, and reads a changeset back besides, so a negative
   total is the run measuring its own passes and not a cost. It gets the `noise` treatment the
   split already has, in the table cell and in the finding sentence, pinned by tests built from
   synthetic `Recording` values rather than from this machine's timings.
2. The run itself, with its exact command and flags.
3. The numbers, recorded here and in `src/seahaven/docs/state.md` as approximate.

`architecture.md` §16 asks for the measurement and says not to tune the design to it. Nothing in
`src/seahaven/` changes in this phase; a surprising number is reported, not designed around.

## Steps

1. **`bench/recording.py`.** `Recording` gains a property beside `split_reads`:

   ```python
   @property
   def total_reads(self) -> bool:
       """Whether this run's total can be read as a cost at all."""
       return self.per_call_seconds > self.long_lived_seconds
   ```

   `split_reads` requires `per_call > unread > long_lived` and so implies `total_reads`: the finer
   reading of the same order. Add it to the docstring of `split_reads` so the two are read
   together, and leave `overhead` and `rendering` alone -- they are arithmetic, and it is the
   report that decides what may be printed.

2. **`bench/report.py`, `_recording_rows`.** The total cell takes the same treatment the middle
   cell has:

   ```python
   total_cell = f"{total:+d}%" if probe.total_reads else "noise"
   ```

   and the first row prints `total_cell`. The third row's `--` is unchanged: it is the denominator,
   not a measured share.

3. **`bench/report.py`, `_recording_finding`.** All three branches open with the same sentence
   today, so lift it into a helper and let it say `noise`:

   ```python
   def _recording_cost(probe: Recording) -> str:
       """A finding's opening sentence: the measured total, or why there is none."""
   ```

   When `total_reads` is false the sentence states that the full leg timed no dearer than the
   long-lived one, which its construction does not allow, and that the total is this run's own
   noise and is left unreported. The `wrote_rows` and `split_reads` branches then continue from
   that opening as they do now, and the unreadable-split branch does not explain the leg order
   twice when the opening has already given it.

4. **Run the probe.** `uv run python -m bench recording --calls 200 --repeats 3`, printed to
   stdout rather than written to `bench/results/latest.md`, which `functional_spec.md` §11 keeps as
   a dated record of an earlier measurement and does not rewrite. Record in "The numbers" below:
   the command with its flags, the environment the run happened in, the table the probe produced,
   and anything surprising in it.

5. **`src/seahaven/docs/state.md`.** The closing paragraph ends with the sentence phase 5 left for
   this phase. Give it the measured figures as approximate, with the command that produced them,
   and no figure the probe does not itself produce. Under `AGENTS.md`'s docs style, wrapped at 100
   columns, and its example fences unchanged.

## The numbers

**The command.** `uv run python -m bench recording --calls 1000 --repeats 9`, run three times; the
table below is the third run. `--repeats 9` and not the default 3 because the environment is noisy
(below) and 9 is three whole rotations of the probe's three legs; `--calls 1000` and not the
default 200 for the same reason. The whole run takes about 32 s.

**The environment.** A shared, virtualised container: 4 vCPUs of an Intel Xeon at 2.10 GHz, load
average 0.89 at the start, CPython 3.14.7, apsw 3.53.4.0, SQLite 3.53.4, on commit `5d70ad3` with
this phase's working tree. Nothing was pinned and nothing else on the host was under our control,
so absolute timings are worth less here than the ratios between the legs, which are measured
against each other in the same pass. The probe prints its own machine in the section's provenance
line, so the published report carries this without depending on this file.

**The table**, from the third run:

| World | Nodes | Probe | Leg | Per call | Against one session |
|---|---:|---|---|---:|---:|
| `ProjectTracker agency` | 1 | `read` | a session per call | 0.096 ms | +18% |
| `ProjectTracker agency` | 1 | `read` | a session per call, never read | 0.091 ms | noise |
| `ProjectTracker agency` | 1 | `read` | one long-lived session | 0.082 ms | -- |
| `ProjectTracker agency` | 1 | `write_mix` | a session per call | 0.62 ms | +47% |
| `ProjectTracker agency` | 1 | `write_mix` | a session per call, never read | 0.48 ms | +14% |
| `ProjectTracker agency` | 1 | `write_mix` | one long-lived session | 0.43 ms | -- |
| `emporium` | 4 | `one-row read` | a session per call | 0.062 ms | +30% |
| `emporium` | 4 | `one-row read` | a session per call, never read | 0.060 ms | noise |
| `emporium` | 4 | `one-row read` | one long-lived session | 0.048 ms | -- |
| `emporium` | 4 | `one-row write` | a session per call | 0.16 ms | +55% |
| `emporium` | 4 | `one-row write` | a session per call, never read | 0.13 ms | +25% |
| `emporium` | 4 | `one-row write` | one long-lived session | 0.10 ms | -- |
| `emporium` | 4 | `settle_order` | a session per call | 0.43 ms | +50% |
| `emporium` | 4 | `settle_order` | a session per call, never read | 0.32 ms | +12% |
| `emporium` | 4 | `settle_order` | one long-lived session | 0.29 ms | -- |

**The spread over the three runs**, as the totals the probe printed for each:

| Probe | Run 1 | Run 2 | Run 3 |
|---|---:|---:|---:|
| `read` on `ProjectTracker agency` | +5% | +12% | +18% |
| `write_mix` on `ProjectTracker agency` | +48% | +47% | +47% |
| `one-row read` on `emporium` | +26% | +37% | +30% |
| `one-row write` on `emporium` | +49% | +67% | +55% |
| `settle_order` on `emporium` | +53% | +45% | +50% |

**The reading.** A write-heavy call costs about +47% against the one long-lived session per node
the framework kept before this release, and roughly two thirds of that is reading the changeset and
rendering the records -- +33 of the +47 points in the run above. That part is inherent to a
per-call record: it is the work an eval's `changes()` call used to do once an episode. The
remaining +14 points are what a fresh session per call costs over a warmed-up one.

A read-only call is the harder figure. It records nothing, so both comparison legs differ from each
other by an empty `changeset()` alone and the probe leaves their split unreported; its total moved
between +5% and +18% over the three runs, on a call of under 0.1 ms, where a few microseconds of
this machine's noise is several points. It is stated as a range rather than as a point.

Four nodes cost about what one does, as a share: `emporium`'s write probes came out at +45% to
+67%, in the same range as the one-node case, although each of its calls opens four sessions rather
than one. The absolute cost per call does rise with node count, which is what
`architecture.md` §16 accepts; what does not rise is the share, because a call that touches four
nodes is doing more of everything else as well.

**Against the first attempt.** `architecture.md` §16 recorded about +40% on a write-heavy call and
about +10% on a read-only one, measured on the abandoned branch and carried in the spec as a figure
to re-measure rather than to trust. The write figure here is higher (+47%) and the read figure is
higher and much less certain (+5% to +18%). Nothing was tuned to either: this phase measures, and
the spec's own instruction is to report a surprising number rather than design around it.

**What is surprising, reported and not acted on.** At the probe's default `--calls 200
--repeats 3`, the same command on the same tree gave +73%, +128% and +143% on the three write
probes -- roughly double the figures above, and not stable between runs. The rotation balances a
leg's mean pass position, not the variance of a short pass, and 200 calls over three repeats is
short enough for one disturbed pass to move a leg's mean by tens of points. The figures published
here therefore come from a run ten times longer with three whole rotations, and the noisier default
is left alone: changing the probe's defaults is a change to the probe, and this phase's job is to
read it.

## Tests

- `test_a_total_that_cannot_be_read_is_printed_as_noise` — a synthetic `Recording` whose full leg
  is no dearer than its long-lived one prints `noise` in the total cell and leaves the finding
  without a signed total, both when the pass wrote rows and when it wrote none.
- `test_a_split_in_the_order_construction_forces_is_reported` (existing) — the positive side: legs
  in their construction order still print both figures and the word `noise` appears nowhere in the
  section.
- `test_a_split_that_cannot_be_read_is_printed_as_noise` (existing) — unchanged, and its two cases
  keep a readable total, so the split's guard and the total's are pinned apart.
