---
status: complete
---

# Phase 7: the CLI and `seahaven check`

## Overview

Phases 1–6 built a framework a Python caller can drive: `world.instance(...)`, `instance.call(...)`,
and an OpenEnv server in front of both. None of it can be reached from a shell, and none of the
rules the specs state about a *world* — STRICT tables, no wall-clock reads, every tool module
imported, fixtures that match their sidecars — is checked anywhere. This phase adds the authoring
surface: the `seahaven` command and the lints behind `seahaven check`.

The two halves are deliberately separate packages:

- **`seahaven/lint/`** is a library. `Finding` is a dataclass, each rule module is a pure function
  from a linted world to a list of findings, and nothing in it prints or exits. That is what lets
  the rules be tested one at a time and lets a future consumer (the docs' lint table, an editor
  integration) use them without a subprocess.
- **`seahaven/cli/`** is argument parsing, world discovery and output. Every subcommand module is a
  parser plus a `run(args) -> int`; the work is done by code that already exists (`World.instance`,
  `Instance.freeze`, `openenv.serve`) or by `seahaven/lint/`.

What this phase builds:

- **`cli/__init__.py`** — `main`, the parser, `CliError`, and `find_world` / `discover`: the
  §2.2 convention (nearest `pyproject.toml`, `[project] name` normalised, attribute `world`) with
  `--world module:attr` as the override, plus the `sys.path` handling that makes an uninstalled
  world resolve.
- **`cli/new.py` and `cli/templates/`** — the scaffold of functional spec §2.1, rendered with
  `string.Template`, and the five extra files `--hub` adds.
- **`cli/check.py`**, **`cli/docs.py`**, **`cli/fixture.py`**, **`cli/serve.py`** — the other four
  subcommands.
- **`lint/__init__.py`, `lint/ddl.py`, `lint/code.py`, `lint/coverage.py`, `lint/fixtures.py`** —
  `Finding`, `Target`, `run_all`, and the thirteen rules of `components/cli_and_check.md` §3.
- **`src/seahaven/docs/index.md`** — one stub page, so `seahaven docs` prints a directory that
  exists.
- **`[project.scripts] seahaven = "seahaven.cli:main"`** in `pyproject.toml`.

### Four deliberate departures from the component spec, and why

1. **The scaffolded test does not use the pytest plugin yet.**
   `components/cli_and_check.md` §2 gives `tests/test_items.py` as
   `@pytest.mark.seahaven(fixture=None, now=...)` over an `instance` fixture. That marker and that
   fixture are `seahaven/pytest_plugin.py`, which is Phase 8. The implementation plan's acceptance
   for *this* phase is "a scaffolded world passes `check` and its own tests", so the scaffolded test
   has to pass now, with what exists now: it drives `world.instance(None, now=...)` directly, which
   is exactly what the plugin's `instance` fixture wraps. Phase 8 changes the one template file to
   the marker form when the fixture it needs exists. Building the plugin here instead would move a
   whole component out of the phase that owns it.

2. **SH405 is the journal companions, and not the file mode.**
   `components/cli_and_check.md` §3 gives SH405 as "state file not read-only or has `-wal`/`-shm`
   companions". `freeze` does seal the file at `0444` and still does. But git records only the
   executable bit, so every *committed* fixture — which is every fixture, since a fixture is
   committed with the script that generates it — comes back from a clone at `0644`. Running the
   rule as written against ProjectTracker on this checkout produced exactly that: one SH405 on
   `fixtures/empty/state.sqlite`, on a fixture nothing had touched. A lint that fires on every
   fixture of every world after every clone is one an author learns to ignore, and it would take
   the twelve rules beside it down with it. What the seal guards against is the file changing, and
   that is SH402, over a hash version control does preserve. The mode half is dropped, with the
   reasoning in `lint/fixtures.py`'s module docstring and a test named after it; the companion half
   is unchanged. Worth a maintainer's confirmation, because it is a rule the spec states and this
   phase does not implement.

3. **`seahaven/docs/` gets `index.md` and nothing else.**
   The `docs/` layout with its stub pages is Phase 8's deliverable and its content is Phase 12's.
   But this phase's own test plan requires that the path `seahaven docs` prints exists and holds
   `index.md`, and a command that prints a directory that is not there is not a command. One stub
   page is the smallest thing that makes `seahaven docs` true; the other eleven pages arrive with
   the phase that owns the layout.

4. **An exception out of a fixture generator is a traceback, not a one-line error.**
   `components/cli_and_check.md` §5 says a failure inside the `--run` callable destroys the instance
   and leaves no fixture directory, and this phase's own test list said "exit 1 with the generator's
   message". Both halves about the *instance* hold and are tested. What is not done is turning the
   exception into a CLI error: a generator is the author's own code, the traceback names the line
   that failed, and a one-line "something went wrong" would be strictly less than
   `python generate.py` already gives them. Every *other* failure of `seahaven fixture` — a
   malformed `--run`, a module that does not import, a target that is not callable, a `now` that is
   not a timestamp, a fixture id that already exists — is one line and exit 1 as specified. The
   exception is stated in `cli/fixture.py`'s module docstring and in `cli/__init__.py`'s "Failure"
   paragraph, so the two do not contradict each other.

### What it does not build

The pytest plugin and the rest of `docs/` (Phase 8). No lint for typing: functional spec §2.0 is
explicit that `seahaven check` has no typing rule. No autofix, no `--format json`, no config file —
the rule set is fixed in code and deliberately small.

## Steps

1. **`pyproject.toml`** — add `[project.scripts] seahaven = "seahaven.cli:main"`.

2. **`src/seahaven/docs/index.md`** — a stub page: a heading, one paragraph saying the pages arrive
   with the docs phase, and the list of pages the layout will hold.

3. **`src/seahaven/world.py`** — extract the message fragment `_prove_the_ddl_executes` raises into
   a module constant, `DDL_DOES_NOT_EXECUTE = "has DDL that does not execute"`, so `cli/check.py`
   can tell SH104 (a `WorldBug` about the DDL) from SH501 (any other `SeahavenError` raised while
   the `World` is constructed) without matching a literal it does not own. One constant, one call
   site changed, and a test that pins the two together.

4. **`src/seahaven/lint/__init__.py`** — the library's surface.

   ```python
   Severity = Literal["error", "warning"]

   @dataclass(frozen=True)
   class Finding:
       code: str
       severity: Severity
       path: Path
       message: str
       fix: str
       line: int | None = None
       def render(self, root: Path | None = None) -> str: ...
       @property
       def sort_key(self) -> tuple[str, str, int]: ...

   @dataclass(frozen=True)
   class Target:
       world: World
       package: ModuleType
       imported: frozenset[str]      # sys.modules as it stood right after the import
       @property
       def package_dir(self) -> Path: ...

   def run_all(target: Target) -> list[Finding]: ...
   ```

   `render` is `f"{code} {severity} {path}:{line}  {message}  fix: {fix}"`, the path made relative to
   `root` where it can be, and the `:{line}` dropped where there is no line (a fixture's sidecar has
   no useful one). `run_all` concatenates the four rule modules and sorts by `sort_key`, which is
   code then path then line, as §3 asks.

   `imported` is a snapshot rather than a live read of `sys.modules` because `coverage.py` walks with
   `pkgutil.walk_packages`, which imports the packages it recurses into: comparing against a live
   `sys.modules` would let the walk answer its own question.

5. **`src/seahaven/lint/ddl.py`** — SH101–SH104 over `build_blank(":memory:", world.schema)`.
   `PRAGMA table_list` gives type and `strict` per table; `PRAGMA table_info` gives the primary key;
   virtual tables (FTS5) and `shadow_tables(conn)` are skipped for both rules, and `sqlite_%` is
   never the world's. SH103 is a regex (`CURRENT_TIMESTAMP|CURRENT_DATE|CURRENT_TIME|'now'`, case
   insensitive) over every row of `sqlite_master.sql` the world owns, which catches a wall clock in
   a trigger, a view, a generated column or an index as well as in a `DEFAULT`.

   Line numbers come from the schema files on disk (`package_dir.rglob("*.sql")`, sorted): a
   `CREATE ...` regex finds the line the named object is defined on. Where no source matches — an
   installed world with no `.sql` shipped, a schema built by hand — the finding carries the package
   directory and no line.

   `ddl_execution_finding(message, path)` builds SH104 and is called from two places: here, if the
   in-memory build somehow fails, and `cli/check.py`, for the import that never got as far as a
   `World`.

6. **`src/seahaven/lint/code.py`** — SH201, SH203 and SH205 over `ast.parse` of every `*.py` under
   the package.

   Both call rules resolve a dotted expression (`ast.Attribute` chain down to an `ast.Name`) and
   then expand its root through an alias map built from the module's own `import` and
   `from ... import` statements, so `from datetime import datetime as dt; dt.now()` and
   `from time import time; time()` are the same finding as `datetime.now()`. This is what keeps
   `ctx.ids.random.random()` and `ctx.clock.now()` clean: their roots are `ctx`, which no import
   binds.

   - SH201 (warning): a call whose resolved name ends in `datetime.now`, `datetime.utcnow`,
     `date.today`, `time.time`, `time.monotonic` or `time.perf_counter`, in a file that is not under
     `middleware/`.
   - SH203 (warning): `import random` / `from random import ...`; a call whose resolved name starts
     with `random.`; a call resolving to `uuid.uuid4` or `uuid.uuid1`. `import uuid` alone is not a
     finding — `uuid.UUID` is how `ctx.ids.uuid()`'s output is parsed.
   - SH205 (warning): a registered tool whose description is blank, control tools excluded, located
     through `inspect.getsourcefile` / `getsourcelines` on the function.

7. **`src/seahaven/lint/coverage.py`** — SH301. For each of `tools/` and `middleware/` that exists
   under the package: the subpackage itself plus every module `pkgutil.walk_packages` finds under
   it, each checked against `target.imported`.

8. **`src/seahaven/lint/fixtures.py`** — SH401–SH405 over `world.fixtures_dir`, one fixture
   directory at a time, dot-directories skipped (`.pending-<id>` is a freeze in flight). SH401 is
   the sidecar: unreadable, not YAML, not a mapping, or pydantic's own errors from
   `FixtureMeta.model_validate`, which is where a `format_version` that is not `1` is reported.
   A fixture whose sidecar does not validate is not checked further — the other four rules read
   fields it does not have. Then SH404 (`Clock.from_iso(now).iso() == now`, which is the whole of
   "canonical"), SH403 (`schema_hash` against `world.schema_hash`), SH402 (`sha256` of
   `state.sqlite`, and a missing state file is reported here) and SH405 (a `-wal` or `-shm` beside
   the state file; see departure 2 for the half that is not implemented).

9. **`src/seahaven/cli/__init__.py`** — `main(argv=None) -> int`, the argparse tree, `CliError`,
   `find_world` and `discover`.

   ```python
   class CliError(Exception):
       def __init__(self, message: str, *, code: str | None = None) -> None: ...

   @dataclass(frozen=True)
   class Discovery:
       world: World
       package: ModuleType
       imported: frozenset[str]
       root: Path

   def discover(explicit: str | None, start: Path | None = None) -> Discovery: ...
   def find_world(explicit: str | None, start: Path | None = None) -> World: ...
   ```

   `find_world` is the name `components/pytest_and_docs.md` §1 already writes into the pytest
   plugin, so it keeps that signature and returns a `World`; `discover` is what `check` needs, which
   is the same work plus the module, the project root and the `sys.modules` snapshot.

   `code` on `CliError` is how a failure that `check` must render as a finding (SH104, SH501) is
   told from one that is a plain user error (no `pyproject.toml`, a malformed `--world`). `main`
   prints any `CliError` or `SeahavenError` to stderr as one line and returns 1; argparse's own exit
   code 2 for a usage error is left alone.

   Discovery puts the project root and its `src/` on `sys.path` when they exist, so a world that has
   not been installed still imports.

10. **`src/seahaven/cli/new.py` and `src/seahaven/cli/templates/`** — `seahaven new <name>
    [--dir <path>] [--hub]`. Every template file is named `<real name>.tmpl` so that ruff, ty and
    pytest never see a `.py` full of `$package`; `new` strips the suffix as it renders. The package
    directory is spelled `PACKAGE` in the template tree and renamed on the way out. Substitutions
    are `$name`, `$package` and `$seahaven_requirement` (`seahaven~=<major>.<minor>`, read from the
    installed version). Refuses an existing target directory; refuses a name that does not normalise
    to a Python identifier; prints the four next steps.

11. **`src/seahaven/cli/check.py`** — `discover`, then `lint.run_all`, then print every finding
    sorted, and return 1 if any is an error. A `CliError` carrying a code becomes that one finding
    instead; a `CliError` without one propagates to `main`.

12. **`src/seahaven/cli/docs.py`** — print `importlib.resources.files("seahaven") / "docs"`, return
    0. Nothing else, so `cat "$(seahaven docs)/index.md"` composes.

13. **`src/seahaven/cli/fixture.py`** — `list`, `freeze` and `fork`. `--run module:function` is
    imported and called with the live `Instance`; `freeze` starts from a blank instance (`--now`, or
    the wall clock), `fork` from the parent fixture. The instance is a `with` block, so a generator
    that raises destroys it, and `Instance.freeze` publishes by rename, so nothing partial is left
    behind. On success the sidecar is printed verbatim.

14. **`src/seahaven/cli/serve.py`** — the options of functional spec §18, `--session-timeout 0`
    translated to `session_timeout=None`, `--concurrency` left as `None` when not given so
    `openenv.serve` applies its own default, and `ImportError` on `seahaven.openenv` turned into the
    one-line message naming the extra.

15. **`tests/worlds/`** — four small worlds on disk, each a real package with a `pyproject.toml` so
    that discovery works from inside it: `tidy` (lint-clean: a STRICT table with a primary key, one
    described tool, an error handler, every module imported), `messy` (a wall clock in a tool and
    another in the middleware that must *not* fire, `random`, `uuid.uuid4`, an undescribed tool and
    an orphan module under `tools/`), `broken_ddl` (DDL that does not execute) and `no_world` (a
    package with no `world` attribute). The DDL rules that need neither an import nor a package
    (SH101–SH103) are driven with an inline `World` and an inline schema source instead, which is
    what keeps the committed worlds down to four.

## Tests

`tests/conftest.py` gains one shared fixture, `isolated_imports`, which restores `sys.path` and
removes modules imported during a test; every test module below that imports a world declares it
with `pytestmark`.

**`tests/test_find_world.py`**

- `test_discovery_finds_the_world_from_the_project_root` — `tidy/` resolves with no `--world`.
- `test_discovery_finds_the_world_from_a_subdirectory` — the same from `tidy/src/tidy/tools`.
- `test_the_package_name_is_the_project_name_normalised` — a project named `my-world` resolves the
  package `my_world`.
- `test_explicit_world_overrides_the_convention` — `--world tidy.world:world` from an unrelated cwd.
- `test_explicit_world_with_no_colon_is_a_user_error`, and a module that does not import.
- `test_a_package_with_no_world_attribute_names_the_fix` — the message holds
  `must export world = seahaven.World(...)` and `--world module:attr`.
- `test_an_attribute_that_is_not_a_world_is_refused`.
- `test_no_pyproject_anywhere_is_a_user_error_not_a_finding` — `CliError` with no code.

**`tests/test_lint_ddl.py`** — a positive and a negative case per code:

- `test_a_strict_table_is_clean` / `test_a_table_that_is_not_strict_is_sh101`, including that the
  finding's line is the `CREATE TABLE` line in the source file.
- `test_a_table_with_a_primary_key_is_clean` / `test_a_table_with_no_primary_key_is_sh102`.
- `test_an_integer_primary_key_counts_as_explicit`.
- `test_ddl_with_no_wall_clock_is_clean` / `test_a_current_timestamp_default_is_sh103`,
  `test_a_wall_clock_in_a_trigger_is_sh103`, `test_datetime_now_in_a_view_is_sh103`.
- `test_an_fts5_table_is_exempt_from_strict_and_primary_key` — the virtual table and its shadow
  tables produce nothing.
- `test_a_finding_with_no_source_file_carries_no_line`.
- `test_ddl_that_does_not_execute_is_sh104_with_sqlites_message`.

**`tests/test_lint_code.py`**

- `test_a_clean_world_has_no_code_findings` — `tidy`.
- `test_a_wall_clock_call_in_a_tool_is_sh201`, one case per name in the six, and
  `test_a_wall_clock_call_in_middleware_is_not_reported`.
- `test_an_alias_for_datetime_is_still_sh201` and `test_from_time_import_time_is_still_sh201`.
- `test_ctx_clock_and_ctx_ids_are_not_wall_clock_reads` — the endorsed spellings stay clean.
- `test_importing_random_is_sh203`, `test_a_random_call_is_sh203`, `test_uuid4_is_sh203`,
  `test_uuid1_is_sh203`, `test_importing_uuid_alone_is_not_sh203`.
- `test_a_tool_with_no_description_is_sh205` / `test_a_described_tool_is_clean`.
- `test_the_control_tools_are_not_sh205`.

**`tests/test_lint_coverage.py`**

- `test_a_world_that_imports_every_module_is_clean` — `tidy`.
- `test_a_module_under_tools_that_is_not_imported_is_sh301` — `messy`'s orphan, named in the
  message, with the fix naming the package `__init__`.
- `test_a_module_under_middleware_that_is_not_imported_is_sh301`.
- `test_the_walk_does_not_answer_its_own_question` — a nested package under `tools/` that is not
  imported is still reported after `pkgutil.walk_packages` has imported it.

**`tests/test_lint_fixtures.py`** — over a world frozen into `tmp_path`:

- `test_a_freshly_frozen_fixture_is_clean`.
- `test_a_sidecar_that_is_not_yaml_is_sh401`, `test_a_sidecar_missing_a_field_is_sh401` (the
  pydantic error is in the message), `test_a_format_version_that_is_not_one_is_sh401`.
- `test_a_modified_state_file_is_sh402` and `test_a_missing_state_file_is_sh402`.
- `test_a_schema_hash_from_another_world_is_sh403` — the fix names `seahaven fixture`.
- `test_a_now_that_is_not_canonical_is_sh404`.
- `test_a_journal_companion_beside_the_state_file_is_sh405`, once per companion.
- `test_a_writable_state_file_is_not_a_finding` — departure 2, with its reasoning in the test.
- `test_a_pending_directory_is_not_a_fixture` — `.pending-x` is skipped.
- `test_a_broken_sidecar_stops_at_sh401` — no SH402/403/404 piled on top of it.

**`tests/test_cli_check.py`**

- `test_a_clean_world_prints_nothing_and_exits_zero`.
- `test_the_finding_line_has_the_documented_shape` — code, severity, path, line, message, `fix:`.
- `test_findings_are_sorted_by_code_then_path`.
- `test_warnings_alone_exit_zero` and `test_any_error_exits_one`.
- `test_ddl_that_does_not_execute_is_sh104_with_no_traceback` — `broken_ddl`; SQLite's message is in
  the line and the word `Traceback` is not.
- `test_a_package_with_no_world_is_sh501`.
- `test_an_import_error_is_sh501_with_the_last_traceback_line`.
- `test_paths_are_printed_relative_to_the_project`.
- `test_the_reference_world_passes_check` — ProjectTracker, with its fixture, through the real
  command. The four worlds under `tests/worlds/` are written to make rules fire; this is the one
  that asks whether the rules are right about a world built to be a world, and it is what found
  departure 2.

**`tests/test_cli_new.py`**

- `test_the_scaffold_has_every_file_the_layout_names`.
- `test_check_passes_on_a_fresh_scaffold` — zero findings, exit 0.
- `test_pytest_passes_on_a_fresh_scaffold` — subprocess, current interpreter, the scaffold's `src`
  on `PYTHONPATH`.
- `test_the_scaffold_has_no_fixture_directory` — `fixtures/` exists and holds only `.gitkeep`.
- `test_hub_adds_five_files_and_nothing_else` — the set difference against a plain scaffold is
  exactly `openenv.yaml`, `Dockerfile`, `__init__.py`, `client.py`, `models.py`.
- `test_the_hub_client_and_models_re_export_the_framework` — the files name `SeahavenClient` and
  the models, and the Dockerfile names `<pkg>.openenv_app:app`.
- `test_an_existing_directory_is_refused`.
- `test_the_package_name_is_normalised` — `my-world` gives `src/my_world/` and
  `[project] name = "my-world"`.
- `test_a_name_that_is_not_a_package_name_is_refused`.
- `test_nothing_is_imported_and_no_fixture_is_built` — the world package is not in `sys.modules`
  afterwards.
- `test_the_next_steps_are_printed`.

**`tests/test_cli_fixture.py`** — on a scaffold in `tmp_path`:

- `test_freeze_writes_a_fixture_and_prints_its_sidecar`.
- `test_freeze_uses_the_now_it_is_given` / `test_freeze_defaults_to_the_wall_clock`.
- `test_fork_records_its_parent`.
- `test_a_generator_that_raises_leaves_nothing_behind` — no fixture directory and no `.pending-`;
  the exception itself reaches the caller (departure 4).
- `test_a_run_target_that_does_not_import_is_a_user_error`, and one that is not callable.
- `test_list_prints_id_parent_now_and_description`.
- `test_list_on_a_world_with_no_fixtures_prints_nothing`.
- `test_freezing_over_an_existing_id_is_refused`.

**`tests/test_cli_docs.py`**

- `test_docs_prints_a_directory_that_exists_and_holds_index_md`.
- `test_docs_prints_the_path_and_nothing_else` — one line, exit 0.

**`tests/test_cli_serve.py`**

- `test_serve_passes_every_option_through` — `seahaven.openenv.serve.serve` monkeypatched.
- `test_session_timeout_zero_disables_the_reaper` — `session_timeout=None`.
- `test_concurrency_is_left_to_serve_when_it_is_not_given` — `concurrency=None`.
- `test_a_missing_serve_extra_names_the_extra` — the import blocked, exit 1, the message is the
  documented sentence.

**`tests/test_cli.py`**

- `test_the_entry_point_is_declared` — `pyproject.toml` maps `seahaven` to `seahaven.cli:main`.
- `test_an_unknown_subcommand_is_exit_two` and `test_no_subcommand_is_exit_two`.
- `test_a_user_error_prints_one_line_to_stderr_and_exits_one`.
- `test_ddl_does_not_execute_message_is_pinned` — the constant of step 3 is what `World` raises.

## Two things this phase touches outside its own files

- **`worlds/projecttracker/AGENTS.md` and `fixtures_src/generate.py`** each carry a paragraph saying
  that the CLI does not exist yet and naming this phase. They become false with this commit, so they
  are rewritten to name the commands instead. Nothing else in the reference world changes.
- **`BACKLOG.md` B17** records that `import seahaven.openenv` fails on Python 3.14 through
  `beartype`, which takes `tests/test_client.py`, `test_env.py`, `test_serve.py`, `test_server.py`
  and this phase's `test_cli_serve.py` with it. It is a lockfile and environment problem in
  already-committed dependencies, found while writing the serve subcommand's tests, and fixing it
  means moving `uv.lock` — which is not this phase's diff.

## What the code review changed

Round 1 endorsed the three departures above and found five defects and a set of smaller points. The
departures are unchanged; departure 4 was written in this round, for behaviour that was already
shipped and argued for in a docstring but recorded nowhere a reviewer would look.

- **SH103 read SQL comments.** SQLite keeps a statement's comments in `sqlite_master.sql`, so a
  schema that *documents* the rule — "no `CURRENT_TIMESTAMP` default here; use `ctx.clock.iso()`",
  which is the style the scaffold's own DDL uses — failed `check` on the sentence telling its reader
  not to do the thing, as an error, with nothing to suppress it. `_without_comments` blanks line and
  block comments before the search, leaving string literals alone so `'--'` is still a value and
  `DEFAULT 'now'` is still a finding. Three cases.
- **`pkgutil.walk_packages` had no `onerror`,** so anything but an `ImportError` out of a
  subpackage's `__init__` came out of `seahaven check` as a traceback — the one thing the command
  promises never to do. The subpackage is yielded before it is imported, so swallowing the failure
  loses nothing and the module is still reported as unimported.
- **A world that is one module crashed with `AttributeError`.** Refusing is right (functional spec
  §2.1), but it has to be a sentence. Discovery now climbs from a module to the package around it —
  which also makes `--world mypkg.world:world`, the natural spelling of the override, work — and
  refuses only a world with no package to climb to. `Target.package_dir` raises `WorldBug` for the
  same case, for anything building a `Target` without the CLI.
- **Findings rendered relative to the shell, not the project.** `collect` had the project root from
  `discover` and threw it away, so a check run from `src/<pkg>/middleware/` printed every path
  absolute. It now returns a `Report(root, findings)`; the plan's
  `test_paths_are_printed_relative_to_the_project` is back under that name, with a second case from
  a subdirectory.
- **`test_a_seahaven_error_is_reported_the_same_way` tested nothing.** `--run` is resolved before
  the instance is made, so the bad `--run` in the test raised `CliError` and the nonexistent parent
  fixture was never reached: the `except SeahavenError` arm of `main` had no test at all. It now
  forks a bad fixture id with a generator that imports.
- **`tests/test_cli_serve.py` guarded on `openenv`, not on `seahaven.openenv`,** so it was a
  collection error rather than a skip on the environment of B17. It guards on the subpackage that
  has to work.

Smaller points taken: the scaffolded `AGENTS.md` no longer names three doc pages that do not exist
yet; both `generate.py` docstrings are raw, so the `\` continuations survive into `__doc__`;
SH203 ignores a relative `from .random import …`, which is the world's own module; SH201 matches the
whole resolved dotted name rather than its last two segments, so `self.time.time()` is clean; SH501's
`fix` no longer restates its `message`; `isolated_imports` purges only modules loaded from
`tests/worlds/` or a temporary directory.

One point declined. `except OSError, UnicodeDecodeError:` (PEP 758) is what `ruff format` produces
for this project's `requires-python = ">=3.14"` — parenthesising it is rewritten on the next format
run — so it is the formatter's call rather than this phase's, and changing it would need a
`target-version` decision in `pyproject.toml`.

Round 2 confirmed those and found one more defect and four smaller points.

- **The reference world's freeze recipe dropped `--now`.** Round 1's rewrite of
  `worlds/projecttracker/fixtures_src/generate.py` presented a `seahaven fixture freeze` command as
  "exactly the shape the CLI calls" and then, twelve lines later, stated that every fixture is
  frozen at `NOW`. `freeze` defaults `--now` to the wall clock, so the documented command mints a
  fixture dated today, and nothing catches it: SH404 asks whether `now` is canonical, and a
  wall-clock instant is. The paragraph it replaced ("the CLI is Phase 7") could not be wrong; this
  one could. Both recipes now pass `--now` and say why, and say that `fork` does not need it. The
  command as documented was run against a copy of the world with `fixtures/empty` removed: it
  reproduces the committed fixture's `now`, `file_sha256` and `schema_hash` exactly.
- `_locate` scanned raw file text, so a block-commented draft of a statement won the line lookup
  against the live one below it. `_without_comments` now blanks a comment character for character
  with newlines kept, so the blanked text has the file's offsets and line numbers, and `_sources`
  hands `_locate` that text. Two cases.
- `_modules` named `__init__.py` as a subpackage's own path even for a namespace `tools/`, which
  has none. It points at the directory when the file is not there.
- The scaffold's `README.md` bound an `Instance` to a name called `world`, which is the one name in
  the framework that already means something else. It is `instance` now, as everywhere else.
- The reference world's `AGENTS.md` said the docs are Phase 12; the `docs/` layout and its stubs are
  Phase 8 and the prose is Phase 12.

