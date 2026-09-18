# Seahaven

Seahaven is a Python framework for building synthetic worlds: faithful, stateful mocks of a
company's tool surface, on SQLite, that agents work against in evals.

The bundled docs in `src/seahaven/docs/` are the written documentation, and their examples are
executed by the test suite. Where nothing is written down, the code is the answer.

We develop with the `/spec` skill: https://github.com/scosman/vibe-crafting

`CONTRIBUTING.md` is the short guide for outside contributors.

## Specs

The specs in `specs/` are point-in-time records of past projects: what was designed and decided at
the time that project was built. They are history, not a live description of the system.

**You do not need to update prior specs when you change the code.** They are expected to go out of
date, and drift between a completed spec and the current code is not a defect to fix. Do not spend a
review round on it, and do not open follow-up work for it.

Where the code and a spec disagree, the code is the answer. The bundled docs in `src/seahaven/docs/`
are the documentation that must stay current; specs are not.

## Docs style

The bundled docs in `src/seahaven/docs/` are written in plain, direct English, close to ASD-STE100
Simplified Technical English. Being friendly and explaining why something exists is welcome; a
literary register is not. Check a docs change against this list.

- **Plain sentences.** One idea each, short, active, present tense for facts and imperative for
  instructions. One term per thing, defined before it is used ("schema", not "DDL"). Name the thing
  again rather than writing "it" or "this" across a clause boundary.
- **Disclose in order:** what it is and why it matters, then the commands or usage, then technical
  depth, then edge cases and reference. The same order applies to the page set in `index.md`. Never
  open a page by telling the reader what they do not need.
- **Long page, table of contents** with anchor links at the top. A short page does not need one.
- **Do not write these**, because they read as machine-written: aphoristic openers and closers
  ("Nine words carry the whole framework"); the words "load bearing", "seam", "the whole point",
  "surface" as a verb, and "lives in" for where code is; "X isn't just Y, it's Z"; sentence
  fragments for emphasis; rule-of-three list sentences; strings of em-dash asides.
- **Check every command, flag, path and API name against the code** before writing it down, and wrap
  prose at 100 columns.
- **Examples are tested.** `tests/test_docs_examples.py` runs a `python` fence and parses a `py`
  fence, so write a runnable example as the first and a fragment as the second. `tests/test_docs.py`
  holds the page list: adding or renaming a page means updating that list and every link to the old
  name.

## Environment

This project runs on a final release of CPython 3.14 or newer, never a release candidate: two locked
dependencies break on the 3.14 rcs, and uv will sync onto one without complaint. Check before
anything else:

```sh
uv run python -V        # must print 3.14.0 or higher, with no "rc" in it
```

If it is missing or prints a release candidate:

```sh
uv self update
uv python install 3.14
uv sync --extra serve
```

## Automated checks

These are what CI runs. All of them must be clean before any commit:

```sh
uv run python -c "import seahaven.openenv"
uv run ruff format --check
uv run ruff check
uv run ty check
uv run pytest                              # the framework
uv run pytest worlds/projecttracker        # the reference world
uv run pytest extensions/seahaven-xmlrpc   # the example extension
uv run python scripts/check_licences.py
```

The three suites are separate because a world and an extension are separate packages with their own
pytest rootdir. `ruff format` also formats Python blocks inside Markdown, and
`tests/test_docs_examples.py` executes every example in the docs, `README.md` and `CONTRIBUTING.md`,
so an example that is added anywhere it reaches will be run.

## Rules

- Python 3.14+, fully typed, tests included. `ty` is the checker.
- Tests are written with the code and catch real breakage; a test that still passes when the line
  under it is deleted is not a test.
- Cover the real entry point with a test. This project has a history of defects that passed a unit
  test and failed on the first real call. When a change touches behaviour a caller can observe, add
  a test that drives it through `world.instance(...)` as well as the unit test, not instead of it.
- Comments carry external constraints, not a description of the code beneath them.
- No `LICENSE` file, no package publication, no version bump, no hub publication of the reference
  world without explicit maintainer sign-off.
- No real customer data, ever. The reference world is fictional: no real product's names, schema or
  error text.
- No copyleft in anything Seahaven ships. Runtime dependencies and every extra must be permissively
  licensed; `scripts/check_licences.py` is the rule and CI runs it. The bar for adding a runtime
  dependency at all is high, because Seahaven is vendored into other people's products.
