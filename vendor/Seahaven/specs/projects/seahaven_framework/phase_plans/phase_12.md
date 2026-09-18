---
status: complete
---

# Phase 12: the docs

## Overview

Phase 8 built `src/seahaven/docs/` as eleven stub pages so that `seahaven docs`, `index.md` and a
scaffolded world's `AGENTS.md` could point at a layout that would not move. This phase writes the
pages. It is the authoring-experience half of the framework (`functional_spec.md` §20): "two layers:
the framework's docs stop API hallucination; the world's `AGENTS.md` notes stop architectural
drift". This phase owns the first layer, plus the repository's own `README.md`.

Three things make this phase different from the ten before it.

**The reader is an agent that has never seen Seahaven.** The docs ship inside the installed package
and are read by whatever is writing the world. That sets the register: short sentences, the exact
spelling of every name, a worked example rather than a description of one, and no advice that
depends on knowing what the framework used to do.

**An example that does not run is worse than no example**, because it will be copied. Nothing on
these pages is checked by eye. `tests/test_docs_examples.py` extracts every fenced block from every
page and checks it mechanically: a ` ```python ` block is executed as a standalone script and must
exit 0, a ` ```py ` fragment must parse, every `seahaven.<name>` either block mentions must resolve
on the real package, and every `seahaven ...` command line in a ` ```sh ` block must parse against
the real `argparse` parser. The convention is stated at the top of that test file, which is the
place a future page's author will look.

**The repository knows things that are not flattering, and the docs say them.** `BACKLOG.md` carries
five open items a docs page would otherwise walk straight into, `bench/results/latest.md` carries
real numbers with real caveats, and Phase 13 has not happened, so the package is not published.
Where a page would naturally claim something the repository knows to be untrue or untested, it says
the true thing instead, with a pointer to where the detail lives. The list is in "What the docs must
not overstate" below.

## Steps

### 1. `tests/test_docs_examples.py`, first

Written before the pages, so every example is written against a harness that is already running.

- `blocks(page)` parses fenced blocks out of a Markdown file, returning `(language, source, line)`.
- ` ```python ` → executed with `sys.executable`, cwd a `tmp_path`, `check=0`. Parametrised per
  block so a failure names the page and the line.
- ` ```py ` → `compile()` only. The fragment tier: a snippet that shows a signature, a decorator on
  a function whose world is three files away, or the inside of a module.
- Both tiers → every `seahaven.a.b` attribute chain in the source must resolve on the imported
  `seahaven` package. This is the anti-hallucination check, and it is the one that catches a page
  naming an attribute the framework does not have.
- ` ```sh ` → every line whose first word (after an optional `uv run`) is `seahaven` is parsed with
  `seahaven.cli.build_parser()`. A subcommand or an option that does not exist fails the test.
- Whole pages, prose and tables included → every `inst.x`, `ctx.y`, `world.z` chain resolved against
  a **live** object of that type. A member table is where a reference page invents a name, and no
  example can catch that.
- The reference's stub signatures → compared parameter by parameter against the real callables.
- A guard test asserting that the harness found blocks of each tier, so a refactor that silently
  stops collecting is not a green run.

The same harness covers the repository's `README.md`, whose examples are copied just as readily.

Executed examples must not import `seahaven.openenv` (the `serve` extra pulls gradio and fastmcp,
costs seconds, and is `BACKLOG.md` B17 on this interpreter). Server-side examples are fragments or
shell.

### 2. The pages

Eleven, in the layout `components/pytest_and_docs.md` §2 fixes. Each replaces its stub wholesale.

1. **`index.md`** — what Seahaven is, the reading order, the commands, the shape of a world in
   thirty lines, where to go for what. Keep every link relative and inside the layout:
   `tests/test_docs.py::test_every_page_index_md_links_to_is_a_page_of_the_layout` compares every
   `](...)` target against the page list, so an `http` link here fails a test about the layout
   (`BACKLOG.md` B18's smaller finding — not fixed here, worked within).
2. **`concepts.md`** — world, fixture, instance, tool, ctx, clock, ids, changeset, and what
   reproducibility is and is not. One diagram-free page whose job is the vocabulary every other
   page uses.
3. **`authoring.md`** — the longest page. Writing a tool (the whole docstring is the description;
   argument types and strictness; `Field(alias=...)` for a wire name Python cannot spell;
   transactions), errors and the error handler, middleware, startup hooks (spell the parameters out),
   the schema rules, and the full list of what registration refuses.
4. **`fixtures.md`** — freeze, fork, the committed generator, `bulk()`, descriptions written for
   eval authors, what a schema change costs, and where fixtures live when a world is installed
   rather than checked out.
5. **`testing.md`** — the plugin: two fixtures, one marker, one option, the failure messages, and
   what is worth testing in a world.
6. **`serving.md`** — `seahaven serve`, the session/instance mapping, the observation shape,
   `SeahavenClient`, the control tools and their flag, the operator options, hub publication.
7. **`extensions.md`** — the contract's five seams, what an extension must not do, and the XML-RPC
   example as the worked case.
8. **`projecttracker.md`** — the walkthrough: the package file by file, the schema, the error
   vocabulary, the tools by resource, the three fixtures, the conventions worth copying, and what
   to read it for.
9. **`reference/api.md`** — the public surface, name by name, with signatures taken from the code.
   Six documented names live outside `seahaven/__init__.py`; the page lists them in two groups and
   says which three it is declaring public on its own authority (the concurrency gate, which no
   component document lists), because a page that publishes more than its own header admits is the
   kind of thing a reference is not allowed to do.
10. **`reference/lints.md`** — every `SHnnn`: the rule, why it exists, the fix. The table stays (two
    tests key on it) and gains a section per code.
11. **`reference/cli.md`** — every subcommand and every option, with the exit codes and the world
    discovery rule that all of them share.

### 3. `README.md`

Its command blocks are run before they are written, like the docs' examples — the harness covers the
README's fenced Python, and the shell blocks were executed by hand from a clean checkout state. The
"Building a world" block in particular stays inside this repository's environment, because a
scaffold cannot install the framework while it is unpublished.

The repository's front page: what Seahaven is, who it is for, a thirty-second example, the state of
the project, how to run it from a checkout, and where the docs are. It replaces a stub that
describes the framework as of Phase 1 ("the runtime database layer exists today") and tells the
reader to `pip install seahaven`, which is not true: publication is Phase 13 and sign-off gated.

## What the docs must not overstate

Each of these is a place a page would naturally write something the repository knows is wrong.

- **Publication.** The framework is not published and ProjectTracker is not on a hub. The `seahaven`
  name on PyPI holds a placeholder release (0.0.1, a 1.4 KB wheel with no dependencies and no
  `serve` extra), so `pip install seahaven` succeeds and installs a stub rather than failing — which
  the README and `serving.md` both say, because a command that appears to work is worse than one
  that does not. The CLI's own missing-extra message still prints that command; it is Phase 7's code
  and is recorded as `BACKLOG.md` B22 rather than changed here.
  (`implementation_plan.md` Phase 13.)
- **Throughput.** `bench/results/latest.md` says in its own words that its numbers are not for "a
  claim in a README". No page quotes a calls-per-second figure as a property of the framework;
  `serving.md` points at the benchmark and repeats its caveat instead.
- **The concurrency gate.** `BACKLOG.md` B20: the gate is a `threading.BoundedSemaphore` and starves
  a caller whenever it binds — one benchmark window served one session once while another was served
  12,874 times. `serving.md` says this where `--concurrency` is documented, including that the
  shipped default is not exempt, and that `--concurrency 0` was the only setting measured that
  served every session evenly.
- **Ordering within an episode.** `BACKLOG.md` B23 (renumbered from a duplicate B18 in this phase):
  a frozen clock gives every row an episode writes
  the same timestamp, and the framework has no per-instance sequence. The docs do not offer an
  activity table as a pattern to copy; `authoring.md` states the limitation and what to do instead
  (grade on state and changesets), and `projecttracker.md` shows how the reference world lives with
  it.
- **The scaffold, and the hub image.** The same placeholder makes `seahaven new`'s printed
  `uv sync` and `--hub`'s `RUN uv sync --extra serve` succeed and produce a world that dies at
  import and an image that cannot start. `authoring.md`, `reference/cli.md` and `serving.md` each
  say so where a reader meets the command; the three messages themselves are Phase 7's code and are
  `BACKLOG.md` B22.
- **Installed worlds and fixtures.** `BACKLOG.md` B11: a built wheel carries the schema and not the
  fixtures. `fixtures.md` says where fixtures live and what an installed world can and cannot do.
- **The serve extra on 3.14.** `BACKLOG.md` B17: the locked closure does not import. `serving.md`
  says the extra is needed, and that this repository's own environment is patched by hand, rather
  than implying a clean `uv sync` gives a working server today.
- **SH405.** `BACKLOG.md` B9: the rule is the journal companions only; the file mode is set at freeze
  and is not something `check` can ask about. `reference/lints.md` documents the rule as
  implemented, and says why.
- **OpenEnv's HTTP endpoints.** `BACKLOG.md` B13: `GET /state` strips every field a Seahaven state
  declares and every clean disconnect logs a traceback. `serving.md` names both, because an operator
  meets them in the first hour.
- **Reproducibility.** `functional_spec.md` §1: offered, not enforced. Every page that touches it
  says what the framework promises and what it does not.

## Tests

New, in `tests/test_docs_examples.py`. It runs over every bundled page **and the repository's own
`README.md`**, whose examples are copied as readily as a docs page's.

- `test_every_python_example_runs`: parametrised over every ` ```python ` block; runs it in a
  temporary directory and asserts a zero exit, showing stdout and stderr on failure. A block that
  defines a `test_` function is run by pytest against the reference world instead of as a script,
  so a documented test module is a test module that passes.
- `test_every_python_fragment_parses`: parametrised over every ` ```py ` block; `compile()`.
- `test_every_seahaven_name_an_example_uses_exists`: over both tiers; resolves each `seahaven.a.b`
  chain against the imported package. A submodule whose own dependencies are absent (the `serve`
  extra) is skipped rather than failed — that is a fact about the environment, not the docs.
- `test_every_documented_member_exists`: every `inst.x`, `ctx.y`, `world.z`, `change.after` and the
  rest, resolved against a **live** object of that type, over the whole page rather than only its
  code. A member table is where a reference page invents a name nobody notices.
- `test_every_documented_signature_matches_the_code`: every signature the reference writes as a
  stub, compared parameter by parameter — names, order and kinds, so a misplaced `/` or `*` fails
  too — against the real callable.
- `test_every_documented_command_line_parses`: over every ` ```sh ` block; parses each `seahaven`
  command line with the real parser.
- `test_no_executed_example_imports_the_serve_extra`: keeps the executed tier from becoming a test
  of whether an optional extra is installed.
- `test_the_serve_extra_allowlist_is_exactly_what_the_page_uses`: the two `serve`-extra names the
  signature check may skip are resolved against `seahaven.openenv` like any other and skipped only
  on `ImportError`, and this keeps the allowlist from outliving the page.
- `test_the_harness_collects_blocks_of_every_tier`: the guard against a collector that quietly
  stops collecting. Its floors are the counts at the time of writing less one apiece, not round
  numbers well below them.

The signature check also covers members written as bare annotations (`row_count: int`,
`MAX_VALUE_BYTES`), resolved against the class or a live object of it — `Ids.random` is bound in
`__init__` and exists on neither the class nor its annotations. The pytest tier reads pytest's own
summary rather than only the exit code, so a documented test module that skipped everything is a
failure and not a pass.

`ruff format` reaches Python blocks inside Markdown, so every example is also formatted to the
repository's own style by the existing check, which is a second mechanical reader.

Unchanged and still passing, which is what says the layout did not drift:
`tests/test_docs.py` (every page exists, has a heading, is linked from `index.md`, and every
registered lint code is in `reference/lints.md` and nowhere stale).

## What the code review changed

Five rounds. The harness the phase built was checked by the reviewer in every one of them -- an
example was broken in each tier and all four caught it, and later rounds re-ran the mutations after
each change -- so what the rounds were mostly *about* was the prose the harness cannot read: a
claim, a name, or an instruction that no fenced block spells.

The failure mode four of the five rounds found is worth naming, because it is the one a docs phase
is prone to: **a plausible statement written from the code's shape rather than from a run.** Every
one of them was a sentence that would have survived any amount of re-reading, and none of them
survived a reproduction.

### Round 1

Two Criticals, five Moderates, seven Milds.

- **`reference/api.md` declared impossible a path the repository has a passing test for**: `MATCH`
  through `run_sql`. The allowlist is `frozenset(listed) | ALWAYS_ALLOWED_TABLES`, so a shadow table
  a world *lists* is allowed, and `components/helpers_and_control.md` §4 assigns the docs the
  opposite statement -- "the docs say to list its shadow tables too". `authoring.md` gained the
  recipe as a runnable example, and the reference now separates the unconditional refusals from the
  tables.
- **`serving.md` told an operator to `pip install "seahaven[serve]"`**, which the phase plan's own
  "must not overstate" list had already ruled out.
- Moderates: the two-marker guard stated as absolute where `BACKLOG.md` B18 says it is not; the
  concurrency gate documented as a serving feature when it is process-wide and on by default in
  every process; a `seahaven.helpers.run_sql.showing_sqlite_text` that does not resolve (the
  submodule is shadowed by the function of the same name); the signature check skipping an
  unresolved name silently -- the one case it exists for; and `World(fixtures_dir=...)` silently
  moving the hub card's README lookup, recommended three paragraphs from the sentence it breaks.

### Round 2

One Critical, three Moderates. The Critical was the same publication error in the one place round 1
had not reached: the scaffold's `seahaven~=0.0`, which the README said "nothing can resolve". It
resolves -- to the placeholder -- so `uv sync` succeeds and installs a stub. The Moderates put that
warning into the bundled docs (`authoring.md`, `reference/cli.md`, and `serving.md` for the hub
image's `RUN uv sync --extra serve`), made `reference/api.md` admit that it publishes six names
outside `__init__.py` rather than the three its header claimed, and resolved the duplicate `B18`.

A Mild was **declined with evidence**: `pytest_plugin.py`'s "only the last would be used" is
correct. `own_markers` is `[lower, upper]` and `get_closest_marker` returns the lower decorator,
which is the last one written going down the file; the finding had read list position as reading
order. The reviewer reproduced it and agreed.

### Rounds 3 and 4

Round 3's Critical was the README's "Building a world" block, which did not work when run: `cd
mytracker` leaves this repository's environment, and `uv run` there implicitly performs the `uv
sync` the paragraph below it said was safely omitted -- so the fix's load-bearing sentence was the
untrue part. The block now stays in one environment and was run verbatim before it was written.

Round 4 was the third round on one sentence, and the lesson is the phase's own: the printed next
steps' pytest failure had been attributed to `middleware/error_handler.py`'s import, which **the
test run never reaches** -- the scaffold's tests get their world from the plugin's fixtures and
never import the package. Reproducing it end to end found four distinct outcomes, not one: a pytest
from outside the new environment reports a missing `seahaven`; a pytest inside it collects fine and
errors three times with `fixture 'instance' not found`, because the placeholder ships no plugin;
`seahaven check` cannot spawn at all; and the world's own `ModuleNotFoundError: No module named
'seahaven.world'` waits for something to import the package. The README and B22 now say that, and
`authoring.md` and `reference/cli.md` were left alone, having attributed it to import all along.

### Round 5

Clean. Every outcome above reproduced end to end in a scratch clone, the placeholder's docstring
confirmed verbatim, the "Building a world" block re-run, and the tree and environment restored
byte-identically afterwards.
