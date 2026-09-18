---
status: complete
---

# Phase 8: the pytest plugin and the docs skeleton

## Overview

A world's tests are the only thing an authoring agent writes more often than its tools, and until
now every one of them has had to build its own instance: `with world.instance("empty") as inst:` in
a `conftest.py` fixture, once per world, written differently every time. `components/pytest_and_docs.md`
§1 puts that in the framework instead — two fixtures, one marker and one option, auto-registered by
installing `seahaven` — so a world's test reads as the marker that says which fixture it starts
from and nothing else.

The second half is the `docs/` layout. Its content is Phase 12's; what this phase owns is that the
pages exist, are shipped, and are reachable from `seahaven docs`, so `AGENTS.md` and the index can
name them without naming a file that is not there. One of the eleven pages is not a stub:
`reference/lints.md` carries the code table, because the test that keeps the docs from going stale
silently is the one that asserts every code `seahaven check` can emit appears there.

What this phase builds:

- **`src/seahaven/pytest_plugin.py`** — `pytest_addoption`, `pytest_configure`, the session-scoped
  `world` fixture and the function-scoped `instance` fixture.
- **`[project.entry-points.pytest11] seahaven = "seahaven.pytest_plugin"`** in `pyproject.toml`, so
  installing `seahaven` activates the plugin in any project.
- **`src/seahaven/docs/`** — the eleven pages of `components/pytest_and_docs.md` §2, `index.md`
  rewritten to point at them, `reference/lints.md` with the code table.
- **`src/seahaven/cli/templates/base/tests/test_items.py.tmpl`** — the scaffolded test moves to the
  marker form, which is what Phase 7's departure 1 deferred to this phase.
- **`src/seahaven/cli/templates/base/AGENTS.md.tmpl`** — one sentence: the reading order of §3, now
  that the pages it names exist.
- **`worlds/projecttracker/tests/`** — the reference world's tests move to the plugin, which is what
  the component spec means by "Seahaven's own test suite uses the plugin too".
- **`tests/test_pytest_plugin.py`** and **`tests/test_docs.py`**.

### Three notes on what is *not* being changed

1. **No build configuration for the docs.** `components/pytest_and_docs.md` §2 asks for the docs to
   ship as package data through `[tool.hatch.build] include`. They already do, and this was checked
   by building the wheel rather than assumed: the target is `packages = ["src/seahaven"]`, and
   hatchling ships every file under a packaged directory and not only the `.py` ones, so
   `seahaven/docs/*.md` is in the wheel the same way `seahaven/cli/templates/**/*.tmpl` already is,
   which nothing declares either. An `include` that restates the packaging rule would be one more
   thing to keep true.

2. **The framework's own `tests/` keep their own `world` and `instance` fixtures.** `tests/conftest.py`
   defines both names, for a throwaway world on `tmp_path` that the runtime tests need to be able to
   build per test; a conftest fixture shadows a plugin one, so nothing collides. The plugin is
   exercised the way a world exercises it: by `pytester` against worlds written into the test's own
   directory, and by ProjectTracker's suite, which is a real world with a real fixture on disk.

3. **`probe` stays in ProjectTracker's `conftest.py`.** It builds a throwaway `World` per test to
   put the error handler under a failing tool; the plugin's `world` fixture is the world the project
   *is*, and the two are not the same thing.

## Steps

1. **`src/seahaven/pytest_plugin.py`** — the whole plugin.

   ```python
   WORLD_OPTION = "--seahaven-world"
   MARKER = "seahaven"

   def pytest_addoption(parser: pytest.Parser) -> None: ...
   def pytest_configure(config: pytest.Config) -> None: ...

   @pytest.fixture(scope="session")
   def world(request: pytest.FixtureRequest) -> World: ...

   @pytest.fixture
   def instance(request: pytest.FixtureRequest, world: World) -> Iterator[Instance]: ...
   ```

   - `world` is `cli.find_world(request.config.getoption(WORLD_OPTION), start=request.config.rootpath)`,
     which is the §2.2 convention and the same `--world module:attr` override the CLI has, spelled
     `--seahaven-world` because a pytest option lives in a namespace shared with every other plugin.
     A `CliError` is turned into `pytest.fail(str(error))`: its message is one line with the fix in
     it, and a traceback out of `import_module` would bury it.
   - `instance` reads the closest `seahaven` marker. No marker at all fails with the marker line to
     add. The fixture is `marker.kwargs["fixture"]` when the key is present, else the single
     positional argument, else a failure: `fixture=None` is a blank instance and never a missing
     value, so the sentinel is "was `fixture` given at all" and never a falsy test. Every other
     keyword — `seed`, `now`, a world's own startup kwargs — passes through to `world.instance`.
   - Two spellings that would silently lose an argument are refused rather than resolved: more than
     one positional argument, and a positional argument together with `fixture=`.
   - The instance is a `with` block, so it is destroyed on teardown whether the test passed or not.

2. **`pyproject.toml`** — `[project.entry-points.pytest11] seahaven = "seahaven.pytest_plugin"`.

   An entry point lives in installed metadata, not in the source tree, so an environment that
   already has `seahaven` installed picks it up only after a reinstall (`uv sync`, or
   `uv pip install -e . --no-deps` in the environment `BACKLOG.md` B17 describes, which a plain
   `uv sync` would undo). Until that happens the plugin is inert and every test of it fails.

3. **`src/seahaven/docs/`** — the layout of `components/pytest_and_docs.md` §2: `index.md`,
   `concepts.md`, `authoring.md`, `fixtures.md`, `testing.md`, `serving.md`, `extensions.md`,
   `projecttracker.md`, `reference/api.md`, `reference/lints.md`, `reference/cli.md`.

   Each page is a heading, one sentence saying what the page will cover, and one saying that the
   prose arrives with the docs phase and where to read in the meantime. `index.md` keeps the
   commands and the reading order and links the pages rather than describing pages that do not
   exist. `reference/lints.md` carries the full code table (code, severity, rule), because step 8's
   test reads it.

4. **`src/seahaven/cli/templates/base/tests/test_items.py.tmpl`** — the marker form, which is what
   `components/cli_and_check.md` §2 specified in the first place:

   ```python
   pytestmark = pytest.mark.seahaven(fixture=None, now=NOW)

   def test_an_item_can_be_created_and_read_back(instance: seahaven.Instance) -> None: ...
   ```

   The reproducibility test needs two instances of one seed, which one `instance` fixture cannot
   give it, so it takes the `world` fixture and opens both itself. That is also what removes the
   template's `from $package import world`: a scaffolded world's test now imports nothing of the
   world's, which is the point of the plugin.

5. **`src/seahaven/cli/templates/base/AGENTS.md.tmpl`** — the docs sentence becomes the evergreen
   one of `components/pytest_and_docs.md` §3 ("Start at index.md, then authoring.md, fixtures.md,
   testing.md"). Phase 7 wrote a hedge in its place because ten of the pages did not exist; they do
   now, and `AGENTS.md` is written once and never rewritten, so the hedge would outlive its reason.

6. **`worlds/projecttracker/`** — the reference world onto the plugin.

   - `tests/conftest.py` loses `tracker` and `blank`; it keeps `FIXTURE_NOW`, `BLANK_NOW` and
     `probe`.
   - `tests/test_ping.py` takes `pytestmark = pytest.mark.seahaven(fixture="empty")` and its tests
     take `instance`; the two that need a blank instance carry
     `@pytest.mark.seahaven(fixture=None, now=BLANK_NOW)`, which is the closest marker and wins. The
     one test that needs two instances at once takes the `world` fixture, which is what lets the
     module stop importing this world's `world` object altogether.
   - `tests/test_package.py`'s table tests and `tests/test_empty_fixture.py`'s one instance test
     take the marker and the fixture.
   - `AGENTS.md` says how this world's tests are written, and its paragraph about the bundled docs
     holding one stub page becomes false with this commit and is rewritten.

7. **`tests/conftest.py`** — `pytest_plugins = ["pytester"]`, which is only honoured in a conftest at
   the root of the collected tree. The helper that writes a world into a `pytester` directory lives
   in `tests/test_pytest_plugin.py`, which is the only thing that uses it.

8. **`tests/test_docs.py`** — the layout and the lint table.

## Tests

**`tests/test_pytest_plugin.py`** — `pytester`, with `isolated_imports`, since an inner run imports
a world into this process.

- `test_a_marked_test_gets_an_instance_of_the_fixture_it_names` — the fixture's row, its id and its
  frozen clock.
- `test_a_blank_instance_is_what_fixture_none_means` — `fixture=None` passes, and the fixture's rows
  are not there.
- `test_the_fixture_may_be_positional` — `@pytest.mark.seahaven("empty")`.
- `test_the_instance_is_destroyed_when_the_test_ends` — the instance's directory is gone after the
  run, for the test that passed and for the test that failed.
- `test_two_tests_with_one_marker_do_not_share_state` — a row written by the first is not in the
  second.
- `test_an_instance_without_a_marker_fails_with_the_line_to_add` — the failure names
  `@pytest.mark.seahaven(fixture="empty")`.
- `test_a_marker_that_names_no_fixture_fails` — `@pytest.mark.seahaven()`.
- `test_a_fixture_given_twice_is_refused` and `test_more_than_one_positional_fixture_is_refused` —
  the two spellings that would silently drop an argument.
- `test_seed_passes_through` — one marker, two instances, the same minted id.
- `test_now_passes_through` — the instance's clock is the marker's `now`.
- `test_startup_kwargs_pass_through` — a world with a startup hook taking `tier=`, and the marker's
  `tier="gold"` reaching it.
- `test_a_startup_kwarg_the_world_does_not_take_is_still_the_worlds_error` — the plugin hands
  keywords on and does not swallow a world's typo detection.
- `test_the_world_fixture_is_one_object_for_the_session` — two tests in one inner run, one object.
- `test_the_world_option_overrides_the_convention` — a second world in the same directory, under an
  attribute the convention would not find, is the one `--seahaven-world` gets.
- `test_a_package_with_no_world_fails_with_the_fix_and_no_traceback` — the sentence `find_world`
  raises, and no traceback out of `import_module`.
- `test_the_marker_is_registered` — `--strict-markers` passes on a marked test, which is what
  `pytest_configure` is for.
- `test_the_plugin_does_nothing_to_a_run_that_does_not_use_it` — a directory with no world at all,
  and a test that mentions nothing of Seahaven's, passes.

**`tests/test_docs.py`**

- `test_every_page_of_the_layout_exists` — the eleven pages, by path.
- `test_every_page_has_a_heading` — no empty file in the layout.
- `test_the_docs_are_where_seahaven_docs_says_they_are` — read through `importlib.resources`, which
  is what `docs_path()` prints.
- `test_every_registered_lint_code_is_documented` — every `SHnnn` a rule module or `cli/` builds a
  finding with appears in `reference/lints.md`.
- `test_the_lint_reference_documents_no_code_that_does_not_exist` — the other direction, so a
  retired rule is not left in the reference.
- `test_the_lint_packages_own_table_agrees_with_the_reference` — `lint/__init__.py`'s docstring
  table is the third copy of the list, and this pins it to the rules themselves.

**`tests/test_cli_new.py`** — unchanged in intent; `test_pytest_passes_on_a_fresh_scaffold` is what
proves the scaffolded marker form works end to end, through the installed entry point.

**`worlds/projecttracker/tests/`** — unchanged in what they assert; the plugin's fixtures replace the
world's own. The suite passing is the plugin's integration test against a real world with a real
fixture on disk.

## What the code review changed

Round 1 found two defects, both demonstrated by mutation rather than argued, and a set of smaller
points.

- **The anti-staleness test could be defeated by a sentence.** `_DOCUMENTED` was `\bSH\d+\b` over
  the whole of `reference/lints.md`, and both that page and `lint/__init__.py`'s docstring already
  carry the sentence "a fix written against `SH203` in a world's history always means the same
  rule" — so deleting SH203's *row* from either table left all the tests passing. The one failure
  the module exists to catch was already bypassable on the day it was written. Both regexes are now
  anchored to a table row (`^\|\s*(SH\d+)\s*\|`), and the two mutations fail.
- **Two `seahaven` markers on one test dropped one silently.** The marker refuses a fixture given
  twice *inside* one marker, on the principle that nobody should have to know which spelling wins;
  two decorators on one test had the same ambiguity and the lower one won without a word.
  `_refuse_two_markers_on_one_test` counts `own_markers` — not `iter_markers`, which would refuse
  the legitimate module-`pytestmark`-plus-test-marker override — and both cases are now tested, the
  refusal and the override.

Smaller points, all taken:

- `test_the_docs_are_where_seahaven_docs_says_they_are` walked only the top level, so `reference/`
  could gain a page nothing knew about; it now compares the whole tree against the layout. A new
  test reads `index.md`'s own links and requires each to be a page of the layout, which is what the
  module docstring claimed and nothing did.
- `_REGISTERED` matches only a double-quoted literal after `code=`. That is a real constraint on how
  a future rule may spell its code, and it is now written down beside the pattern rather than left
  to be discovered.
- `docs/projecttracker.md` said "its three fixtures"; the reference world ships one today and the
  other two are functional spec §22's future. The page now says "the fixtures it ships".
- Four stub pages overran the repository's 100-column wrap on their "Until then:" line. Rewrapped.
  The three lines still over are two Markdown table rows in `index.md` and a sample finding in a
  fenced block in `reference/lints.md`, none of which wraps.
- A marker written to *add* `now=` to a module's `pytestmark` failed with the generic "needs a
  fixture". The message now also says that the closest marker replaces the module's rather than
  merging with it, which is the mistake that produced it.
- The scaffolded `AGENTS.md` sent an authoring agent from `index.md` straight to `authoring.md`,
  skipping `concepts.md`, which `index.md`'s own reading order leads with — in a file written once
  and never rewritten. Both the template and `components/pytest_and_docs.md` §3 now name
  `concepts.md`, and the component document carries a parenthesis saying the reading order was
  amended here, so the two cannot drift silently.
