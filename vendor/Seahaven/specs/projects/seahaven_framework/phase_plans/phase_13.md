---
status: complete
---

# Phase 13: OSS readiness — the contribution guide only

## Overview

> **How this phase closed.** The maintainer closed Phase 13 with the contribution guide alone.
> Publication is not part of this project — neither the package nor the hub is published from this
> repository, and the `seahaven` name on PyPI keeps the placeholder it already holds. The licence
> file was not written and remains gated by `AGENTS.md`; it is a maintainer decision rather than a
> deliverable, and nothing in the repository waits on it. The "still gated and unstarted" section at
> the bottom is kept as the record of what was considered and deliberately not done, not as a
> to-do list.

Phase 13 in `implementation_plan.md` is four things: a contribution guide, a licence file, package
publication, and hub publication of ProjectTracker. `AGENTS.md` gates the whole phase on explicit
maintainer sign-off, and **the sign-off given covers the contribution guide and nothing else**. This
plan covers that slice. The other three are untouched and are listed at the bottom with what each
one still needs.

The phase checkbox in `implementation_plan.md` was left `[ ]` while this slice was built. It is now
ticked: the maintainer closed the phase here (see the note above).

What the guide has to be is set by the state of the repository rather than by what a contribution
guide usually contains. Three facts shape it:

- **There is no licence.** A contributor cannot redistribute this code and there are no terms a
  contribution would be accepted under. That is the first thing on the page, and it comes with the
  advice that follows from it — ask before investing in a change.
- **The repository is not developed by pull request.** It is spec-driven, phase by phase, mostly by
  agents, through the `/spec` skill. A guide that described a fork-and-PR workflow would be
  describing a different project. So the guide describes what is actually here: the spec as the
  source of truth, the phase plan written before the code, the review rounds, and `BACKLOG.md`'s
  job of protecting the reviewable unit.
- **A contributor's first `uv sync --extra serve` produces a broken checkout.** `BACKLOG.md` B17 is
  open by decision, not by neglect. The guide reproduces it, quotes what the contributor will
  actually see — which names the wrong cause — and says what to do instead and what not to do.

The register is Phase 12's: honest before welcoming, and nothing stated that was not run. Every
command, count and transcript on the page came from a run in this environment, including the runs
that were made to produce a failure on purpose.

## Steps

### 1. `CONTRIBUTING.md`

New file at the repository root. Sections, in order:

1. **The state of the project.** No `LICENSE` file and no licence granted, so ask first;
   `project_overview.md` §3's "open source (MIT)" is an intent and not a grant. Nothing published,
   and `pip install seahaven` gets the placeholder. The API moves. The four things `AGENTS.md`
   sign-off gates, named so that a contributor does not propose one of them.
2. **How this repository is developed.** The spec is the source of truth and wins over code; the
   reading order; phases as the unit of work, one coding round, one review, one commit, named the
   way `git log` already names them; the phase plan written before the code, with
   `phase_plans/phase_12.md` named as the one to read; review in rounds with Critical/Moderate/Mild
   findings, reproducing claims rather than reading them, and mutation-sweeping tests.
3. **`BACKLOG.md`, and not widening a diff.** The three rules stated on their own: do not widen a
   diff under review, do not pick an item without asking, close an item by deleting it.
4. **Getting a checkout that runs.** 3.14 and uv, `uv sync` without the extra, the three suites, and
   a table of what each one printed here with and without a working `serve` extra.
5. **What is broken today.** B17, in full: the `beartype` import, the `fastmcp` message that names
   the wrong cause, the four collection errors that stop the run, why four and not five, what to do
   instead, that CI meets it too, and the two related facts (locked `pydantic` on a 3.14 release
   candidate; this repository's hand-patched environment, `--no-sync`, and uv's hard links).
6. **The checks.** The seven `uv run` lines `ci.yml` runs, in its order, plus what three of them
   cover that a reader would not guess: `ruff format` reaching Markdown, `ty` covering tests, and
   the licence gate covering the runtime closure only. Then `seahaven check`, which is not in CI,
   with the three ways of invoking it and the `SH501` the repository root correctly gives.
7. **Standards**, from `AGENTS.md`, each with what it means in practice.
8. **Making a change**, six numbered steps ending at the commit message.
9. **Things this repository will not take**, which is the list of refusals stated once, plainly.

### 2. `tests/test_docs_examples.py` — the guide joins the harness

`REPO_README` becomes `REPO_PAGES = (README.md, CONTRIBUTING.md)`, and `pages()` appends whichever
of them exists. Nothing else in the harness changes.

What this actually buys is worth stating exactly, because it is less than the harness's full
apparatus: the guide contributes four shell blocks and no Python at all, so the two executed tiers
are empty for it and the shell tier finds exactly one `seahaven` invocation to parse. The check that
earns the change is `test_every_seahaven_name_a_page_uses_exists` and the member resolution, which
run over the whole page, prose included -- this page names framework behaviour in sentences rather
than in code, and that is where it would invent something. The one parsed command line is worth
having as well: it is the only command the guide gives that the seven-check parity test below does
not cover.

### 3. `tests/test_contributing.py` — new

Three claims the harness above does not cover, and one guard:

- Every `uv run` line `.github/workflows/ci.yml` executes is one of the command lines
  `CONTRIBUTING.md` gives. This is the drift that would hurt most: a check CI runs and the guide
  does not name is a check a contributor skips, and finds out about from a red CI.
- One shell block in the guide is CI's checks in CI's order, which is what the guide's "in its
  order" claims and what nothing else checks.
- The guide is in `pages()`, so the harness is actually reaching it.
- A floor on the number of CI commands found, so a workflow this stops being able to parse makes a
  loud failure rather than a vacuous pass.

Both sides are compared as whole command lines, read out of the guide's ` ```sh ` blocks with
trailing `# ...` annotations stripped, and not by substring containment: `uv run pytest` occurs
inside `uv run pytest worlds/projecttracker`, so containment would let the framework suite disappear
from the guide entirely without a failure.

The readers return nothing when their file is absent rather than raising, because `ci_commands()`
runs at collection time to parametrise, where an exception is a collection error that takes the
whole suite down and the skip mark is never consulted. That is the same "stops rather than fails"
shape the guide's B17 section is about, and it has no business in the tests that document it.

### 4. Two pointers

- `README.md` gains a short `## Contributing` section before `## Documentation`, giving the two
  things a reader needs before they start: no licence yet, and the `serve` extra does not import.
- `AGENTS.md` gains one sentence after the `BACKLOG.md` paragraph saying what `CONTRIBUTING.md` is,
  so an agent reading its brief knows the longer document exists and what is in it.

## Tests

New, in `tests/test_contributing.py`:

- `test_every_check_ci_runs_is_in_the_contribution_guide`: parametrised over every `uv run` command
  in `ci.yml`; each must be one of the guide's command lines, whole.
- `test_the_guide_lists_the_checks_in_the_order_ci_runs_them`: one shell block in the guide is
  exactly `ci_commands()`, in order.
- `test_the_workflow_still_has_checks_to_compare_against`: a floor of seven, so the parametrisation
  cannot quietly become empty.
- `test_the_guide_is_checked_by_the_docs_example_harness`: `CONTRIBUTING.md` is in `pages()`.

All four skip rather than error when the repository's own files are absent, which is how the suite
runs against an installed wheel. Verified by moving `.github/` aside: four skips, no collection
error.

Extended, in `tests/test_docs_examples.py`: every existing parametrised test now covers
`CONTRIBUTING.md` as well — the shell tier parses its `seahaven` command lines, and the member and
name checks run over the whole page. The tier floors in
`test_the_harness_collects_blocks_of_every_tier` are floors, so a page that adds blocks does not
disturb them.

## What was run, and what it produced

Everything the guide states was produced by one of these. Interpreter: CPython 3.14.0rc2, the only
3.14 this sandbox has.

| Run | Result |
|---|---|
| `uv run --no-sync pytest` | `1101 passed` (`1085` before this phase, plus this phase's tests) |
| `uv run --no-sync pytest worlds/projecttracker` | `260 passed` |
| `uv run --no-sync pytest extensions/seahaven-xmlrpc` | `75 passed` |
| `uv run --no-sync pytest -m "not slow"` | `1100 passed, 1 deselected` |
| `uv run --no-sync ruff format --check` | `179 files already formatted` |
| `uv run --no-sync ruff check` | `All checks passed!` |
| `uv run --no-sync ty check` | `All checks passed!` |
| `uv run --no-sync python scripts/check_licences.py` | `every runtime dependency is permissively licensed` |
| `seahaven check` in `worlds/projecttracker/` | silent, exit 0 |
| `seahaven check --world projecttracker:world` from the root | silent, exit 0 |
| `seahaven check` from the root | `SH501`, exit 1 |

Every command line in the table was run with `uv run --no-sync`, inside the hand-patched `.venv`.
That is the point the review's Critical turned on: the guide's own command lines are `uv run ...`
without `--no-sync`, and on this interpreter they cannot produce these numbers. The guide now says
so above the table.

Four runs were made to produce a failure, because the guide claims failures:

- **The suite with the `serve` extra absent.** `openenv` was shadowed by a module raising
  `ModuleNotFoundError`, which is what an absent module raises and therefore what
  `pytest.importorskip` skips on: `960 passed, 5 skipped`. (Shadowing it with a plain `ImportError`
  instead gives four collection errors, because `importorskip` only swallows `ModuleNotFoundError`
  — which is the same mechanism as the next item.)
- **The suite under B17.** `beartype.typing` was shadowed by a module raising `ImportError`, which
  reproduces the real chain exactly: `import openenv` still succeeds, `import seahaven.openenv`
  fails, `fastmcp` turns the `ImportError` into "FastMCP client support is not installed", and the
  run stops with `Interrupted: 4 errors during collection` and `1 skipped, 4 errors`. The four and
  the transcript in the guide are from that run.
- **B17's root cause, on a pristine install.** `beartype==0.22.9` — the locked version — installed
  into a scratch venv with `--no-cache`, then `import beartype.typing`:
  `ImportError: cannot import name 'ByteString' from 'collections.abc'`. Separately,
  `pydantic==2.13.5` — the locked version — with `src/` on the path: `import seahaven` fails with
  `AssertionError` inside `eval_type_backport`, which is B17's recorded rc-only finding, confirmed.
- **What a contributor's checkout actually does on this machine.** `uv python list` offers
  `3.14.0rc2` and no final 3.14, so `uv python install 3.14` gives an interpreter on which the lock
  does not import, and all three suites die before collecting anything. Found by the review, which
  followed the setup section literally from a clean clone.

One more thing was counted rather than run: the modules that need 3.14 to parse. Every tracked
`.py` parsed with 3.13.12 gives **five** failures, all PEP 758 unparenthesized `except A, B:` --
`bench/environment.py`, `src/seahaven/lint/code.py`, `src/seahaven/lint/ddl.py`,
`src/seahaven/lint/fixtures.py` and `src/seahaven/openenv/env.py`. The first draft said four,
having scanned `src/` and `tests/` only.

## What this phase found and did not fix

Two things, both recorded here rather than acted on.

- **`BACKLOG.md` B17 says five test modules fail to collect; it is four now.** B17 was written in
  Phase 7, and `tests/test_cli_serve.py` has since been changed to guard on `seahaven.openenv` with
  `exc_type=ImportError`, so it skips rather than erroring. B17 is committed text and correcting it
  would widen this diff, so the guide states the true number and says why B17 differs. Worth a
  one-word edit whenever B17 is next touched.
- **A hand-patched `.venv` leaks into uv's cache.** uv links packages into a venv from its cache by
  hard link, so the in-place edit to `beartype` under `.venv/` is the same inode as the cached
  wheel: a fresh venv built from that cache is patched too. Observed here while trying to reproduce
  B17 in a scratch venv, which initially did not reproduce. It is a property of the environment
  rather than of this repository, and the guide warns about it where it tells a contributor what a
  patched environment costs.

## What the code review changed

One round, one Critical, four Moderates, five Milds.

**The Critical was the phase's own failure mode, in the section that names it.** "Getting a checkout
that runs" did not get a checkout that runs: followed literally from a clean clone, `uv sync` and
the three suites die on the first import, because `uv python install 3.14` yields rc2 here and the
locked `pydantic` does not import on it -- which the guide itself documented fifty lines further
down, in a section about the `serve` extra, where a reader setting up has not arrived yet. The table
above it compounded it by attributing counts to `uv run pytest` that were produced by
`uv run --no-sync pytest` in a patched environment. Both are fixed in place: the setup section
warns, before the commands, that a release candidate gives nothing rather than a few failures, and
the table now says in its first sentence that it was not produced by the command lines above it and
what it was produced by. The B17 section's rc bullet says that this one is not confined to the
extra.

The Moderates were two test defects and two miscounts.

- `ci_commands()` ran at collection time inside `@pytest.mark.parametrize`, so a checkout without
  `.github/` gave a `FileNotFoundError` collection error -- taking the suite down rather than
  skipping two tests, and contradicting the module's own docstring. It returns nothing when the file
  is absent now; verified by moving `.github/` aside.
- The parity check was substring containment, and the reviewer deleted **both** mentions of
  `uv run pytest` from the guide without failing it: it is a substring of
  `uv run pytest worlds/projecttracker`. Whole command lines now, read out of the guide's shell
  blocks with trailing annotations stripped, which also made the "in its order" claim checkable for
  nothing. Both mutations were re-run against the new tests and both fail, the first naming
  `[uv run pytest]`.
- "Four modules use PEP 758" is five: the scan behind it missed `bench/`, which a checkout contains
  and which the guide points at.
- "`AGENTS.md` makes all four explicitly sign-off gated" was three. `AGENTS.md` names the licence
  file, package publication and hub publication; a version bump is not there. The refusal stays and
  the attribution is now to publication rather than to `AGENTS.md`.

Of the Milds, four were taken: the elided line in the pristine traceback is restored in full,
"imports it unconditionally" is now the `if _IS_PYTHON_AT_MOST_3_16:` guard that is true on 3.14,
"each phase writes a 'What the code review changed' section" is now "recent plans" (phases 7--12
have one, `phase_1.md` does not), and `ci_commands()` is `functools.cache`d like the harness's own
readers. The fifth was taken too, in this plan: step 2 no longer lists three tiers that are empty
for this page, and says what joining the harness actually buys.

## What remains unstarted in Phase 13

None of this was touched. **The phase was subsequently closed without it**: publication is not part
of this project, and the licence file remains a maintainer decision gated by `AGENTS.md`. This list
is therefore the record of what was considered and deliberately not done — not a to-do list, and not
work that is pending inside this project. It is kept because a reader who finds the phase ticked
should be able to see exactly what "complete" did and did not cover.

- **The licence file.** No `LICENSE`, no licence text, and no licence field or classifier in
  `pyproject.toml`. `project_overview.md` §3 states the intent (MIT); the decision to grant it has
  not been taken, and `AGENTS.md` forbids the file until it is. Everything else in this list depends
  on it: a package cannot honestly be published without one.
- **Package publication.** Nothing published, no release prepared, and no version changed —
  `pyproject.toml` still reads `version = "0.0.1"`, which is what the PyPI placeholder holds.
  `BACKLOG.md` B22 lists the three places that tell a user to install from PyPI and would need to
  become true, or stay warned about, when this happens.
- **Hub publication of ProjectTracker.** Not started. `BACKLOG.md` B11 (a built world wheel ships no
  fixtures) is a prerequisite a hub publication would meet immediately.
- **The `serve` extra on 3.14.** Not part of Phase 13, but it sits across publication: the extra
  does not import against the lock, and `BACKLOG.md` B17's two questions are both maintainer calls
  on `uv.lock`.

`implementation_plan.md`'s Phase 13 checkbox is ticked on the strength of the contribution guide
alone, with the same qualification recorded beside it.
