---
status: complete
---

# Phase 5: ProjectTracker placeholder

## Overview

The framework gets its first world. Phases 1–4 built everything a world is called through — the
runtime, registration, the call path, instances and fixtures, the two SQL helpers and the control
tools — and every one of them was proved against worlds the tests built in `tmp_path`. This phase
builds the first world that is a package on disk: `worlds/projecttracker/`, in the
`functional_spec.md` §2.1 layout, with the schema's `users` table, one `ping` tool, `errors.py`, the
error handler middleware and the `empty` fixture. It is a slice of
`components/projecttracker.md`; the rest of that document is Phase 10.

Three things make it more than a directory of files:

- **It is a real, installed package.** A uv workspace member with its own `pyproject.toml`, its own
  dependency on `seahaven`, and its own pytest rootdir, so that Phase 6 can serve it and Phase 7 can
  discover, lint and scaffold against it without any of them special-casing "the world in this
  repository".
- **It is the first caller of `seahaven.sql_files`**, which did not exist: Phase 2 deferred it here
  explicitly, and writing it found a hazard in `importlib.resources` that only a world's canonical
  one-liner would have hit.
- **It is the first use of the framework by something that is not the framework's own test suite**,
  which is the whole point of a reference world. The friction that produced is recorded below and,
  where it belongs to committed code, in `BACKLOG.md` (B9, B10, B11).

Round 1 of review found no Critical and five Moderates, two of which were not code at all: they were
recorded reasons in this plan that did not hold. Both are corrected in place below rather than
deleted, and the entries say what was wrong and what the mistake was, because a corrected record is
worth more than a clean one — and because the second of the two, the one about `Handler`, is the
exact failure a reference world exists to catch.

Where each round-1 finding landed:

| # | Finding | Resolution |
|---|---|---|
| M1 | two unguarded exception paths in `sql_files` | both wrapped, and the four neighbouring cases closed with them — friction item 7 |
| M2 | `directory` could leave the package, breaking the zip promise | `_check_schema_directory`, on both separators, with seven parameters — *and still not right; see review round 2's M1/M2 below* |
| M3 | a local `Handler` alias, recorded as framework friction that is not | imported from `seahaven.world`; the rule stated; **B10** rewritten — decision above |
| M4 | `AGENTS.md` told an agent to run two commands that do not exist | head rewritten; the deferral decision corrected — decision above |
| M5 | a built wheel ships no `fixtures/` | verified, recorded as **B11** — decision above |
| m6 | `from_violations`'s "an unknown argument is one" is false | corrected in the docstring, the test and the decision; the branch is unreachable |
| m7 | "a file where the directory should be" reported as "does not exist" | the `is_dir` guard split in two, and the test with it |
| m8 | the recipe names a module that does not exist | `fixtures_src.generate`, with a test that it resolves |
| m9 | two unrecorded, non-equivalent survivors | two tests; both mutants now killed by name |
| m10 | `blank` takes an unused `tmp_path` | dropped, with a docstring saying why `probe` keeps its |
| m11 | the world's tests import `seahaven.fixtures` while the middleware refuses `seahaven.call` | one rule, stated at both imports — decision above |
| m12 | a byte-identity test reports `skipped` after passing | split in two; the skip is now the first statement of the byte test |

Round 2 of review found no Critical either, and one code change: `_check_schema_directory`, written
in round 1's fix as a denylist of the spellings that leave a package, was still wrong for spellings
nobody had thought to list. The reviewer's framing is the finding, and is worth quoting rather than
paraphrasing — *stop pattern-matching spellings of "leaves" and validate each segment positively; an
allowlist ends the sequence, an extended denylist does not*, over a function that *had now produced
six defects across two rounds, four of them adjacent to a fix*. That is this phase's own named
failure mode, the fix that closes the demonstrated case and leaves the adjacent one, caught in the
act: the denylist had been extended three times, and `./schema`, `schema/.`, `schema//` and
`schema/` would have made it four. The rewrite is in "Deviations" below.

| # | Finding | Resolution |
|---|---|---|
| M1 | `.`, `//` and a trailing `/` resolve differently on a filesystem and in a zip, and were allowed | one fix with M2: `_check_schema_directory` is now a per-segment allowlist |
| M2 | `\etc` and `C:schema` are not `is_absolute()` on `PureWindowsPath` and got through | the same fix — a segment holding `:`, or an empty first segment, is refused wherever it appears |
| m3 | a symlinked schema directory or `*.sql` file leaves the package and no zip can hold one | `_refuse_a_symlink`, applied to every segment on the way down and to every `*.sql` child |
| m4 | three wrong counts in the test paragraph | recounted from a real collection; 28 functions and 58 cases after that pass, 32 and 62 once rounds 4 and 5 added their namespace tests |
| m5 | "All six now name the package, the directory and…" is false for one of the six | corrected, with why that one names the package alone |
| m6 | **B10** describes a gap where there is a conflict | rewritten around `architecture.md:68`, quoted — decision above |
| m7 | **B11**'s first two closures do not work | corrected; no third closure invented, because "neither works" is the finding |
| m8 | the retired "annotations are lazy on 3.14" reason sits in two committed plans | `phase_2.md` and `phase_4.md` amended in place — see the note below |

The last of those is the one that mattered most, and it is the only change this phase made outside
its own artifacts. `phase_2.md` and `phase_4.md` each recorded a surviving annotation-only import as
equivalent *because annotations are lazy on Python 3.14*. Stated that generally it is false — PEP 649
defers evaluation, it does not prevent it, and `World.middleware`'s `inspect.signature` and
`Tool.from_function`'s pydantic model both evaluate at registration. Round 1 of this phase found that
out the hard way in a world's middleware module. Both plans are `complete`, so both were amended in
place rather than rewritten, each saying that the conclusion still holds — all seven of those
imports really do survive — and that it was the stated reason that was wrong. A false general rule
in a committed plan is read by a later phase as settled.

**And the first attempt at the amendment was itself a false general rule, caught by review round 3.**
It replaced "annotations are lazy" with *modules nothing registers*, and named `seahaven/control.py`
and the two `helpers/` modules as examples of such modules. All three register. `control.py:177`
registers at **import time** — `TOOLS = (_control_tool(controller_run_sql), _control_tool(
controller_changes))` runs `Tool.from_function` while the module is still executing — and the
reviewer executed the deletion the rule sanctioned: drop `control.py`'s `from typing import Any` and
`import seahaven.control` raises `WorldBug: tool 'controller_run_sql' is annotated with 'Any', which
does not exist at runtime`, taking every suite down at collection.

The rule that is true is narrower and is now what both plans say: **a name that appears only in the
annotations of functions nothing registers**. The four imports in Phase 4 qualify under it, each
for its own checked reason: `FunctionType` is in `_control_tool`'s signature (`control.py:155`), a
factory nobody registers; `Callable` is in `_ControlAuthorizer.__init__` (`control.py:104`), a
constructor that is called but never registered; and `Sequence` and `Db` are in the `run_sql` and
`describe_schema` factories and their private helpers, not in the inner functions those factories
turn into tools. `errors.py`'s `Sequence` and `Any` are in
`from_violations`, a classmethod, for the same reason.

Three tries at one sentence, in a plan whose whole subject is the fix that closes the demonstrated
case and leaves the adjacent one. The lesson is not about annotations. A rule stated at the module
level was easier to write and easier to check than the rule stated at the name level, and it was
wrong at exactly the granularity that was skipped.

Round 3 of review found no Critical, verified round 2's allowlist by execution rather than by
reading — 62 spellings driven through the public `sql_files` against a filesystem package, a zip
with explicit directory entries and a flat zip with none, in complete agreement — and found two
things. One is the seventh defect in this function, and the other is this plan's own correction
landing wrong for the second time.

| # | Finding | Resolution |
|---|---|---|
| M1 | the "annotations are lazy" amendment replaced one false general rule with another | the distinction is stated at the *name* level now, in all three plans — see below |
| m2 | a package name beginning with `.` escapes the import handler as `TypeError` | an allowlist on the package name, and the handler's width pinned in both directions |
| m3 | `world.py:115` cited for `Path(fixtures_dir)`, which is at `:117` | corrected in **B11** and in the decision above |
| m4 | "added twenty-nine more" does not tie out to its own enumeration | twenty-five new mutants, thirty-two runs |
| m5 | `key=str` in `_sql_file_names` survives and was not recorded | recorded as equivalent — but against `Path` and `zipfile.Path` only, and round 4 (M2 below) showed the third kind breaks it |
| m6 | a method note describes a two-package guard that is not what shipped | corrected to what shipped, and the lesson sharpened by *why* it had to change |
| m7 | "with nine tests" reads as a current count | dated, and pointed at the count that is current |

M1 is the one to read, because it is this phase's named failure mode landing on the sentence written
to correct that failure mode. The amendment that replaced "annotations are lazy on 3.14" replaced it
with "modules nothing registers" — and named `seahaven/control.py` and the two `helpers/` modules as
examples. All three register. `control.py:177` registers at import time. The reviewer executed the
deletion the new rule sanctioned, and `import seahaven.control` raised `WorldBug: tool
'controller_run_sql' is annotated with 'Any', which does not exist at runtime`, taking every suite
down at collection. A rule stated per module was easier to write and easier to check than the rule
stated per name, and it was wrong at exactly the granularity that was skipped. The distinction that
holds is **a name that appears only in the annotations of functions nothing registers**.

m2 is the seventh defect this one function has produced across three rounds. It was reported as a
case (`.projecttracker`), and it is fixed as a rule: every dotted part of a package name has to be a
Python name. That is deliberately wider than the report — `package.startswith(".")` would close the
demonstrated case and leave `"pkg."` and `"a-b"` — and the mutation check pins the difference: a
mutant narrowing the guard back to `startswith(".")` is killed by name. Round 4 measured what that
width actually buys, and it is less than this paragraph first claimed: only the leading-dot family
escapes the import handler at all, so the mutant dies on the *message* and not on escaping-versus-not.
The width also has a cost — `a-b` and `2pkg` are importable and are now refused. Both are recorded
in the mutation section rather than left as an implication here.

Round 4 of review upheld round 3's work — including the decision to decline round 3's narrow fix,
on evidence round 3 could not have had: the package-name allowlist refuses nothing across every
legitimate shape tried, non-ASCII identifiers and dotted subpackages included — and found two
Moderates, both the same root cause, both demonstrated by execution.

| # | Finding | Resolution |
|---|---|---|
| M1 | the symlink guard is bypassed by a `MultiplexedPath` with two portions, and reads a schema from outside the package | `_refuse_more_than_one_directory`, applied to every joined segment |
| M2 | the `key=str` equivalence record is false: a merged listing's children do not share a parent | the same fix makes it true, and the record now says *why* — see the mutation section |
| m3 | "`Callable` … in `_control_tool`'s own signature" — it is in `_ControlAuthorizer.__init__` | corrected in both plans, each name's use now cited and each confirmed by deletion |
| m4 | the refusal message names a leading dot the author may not have written | the dot clause moved to the comment, where it already was |
| m5 | "the width bought something rather than just reading better" is not what it bought | measured and corrected, and the width's real cost recorded |

**One fix closes both Moderates, and the reason they were one bug is the interesting part.**
`importlib.resources.files()` answers three kinds of `Traversable`, and this plan says so in two
places. Two claims about the third kind were written anyway, both by reasoning from the cases at
hand rather than from that list: that `MultiplexedPath` never reaches the symlink guard (true with
one portion, false with two, because `MultiplexedPath.joinpath` answers another `MultiplexedPath`),
and that every listing's children share a parent (true for a `Path` and a zip, false for a merged
listing). The first let a symlinked portion feed `sql_files` a schema from outside the package; the
second made an equivalence record that licensed reversing `001_` and `002_`.

A schema directory that resolves to a `MultiplexedPath` is split across two `sys.path` entries and
cannot be reproduced by any installed wheel — the portions merge into one directory in
`site-packages` — so it is already the failure this function exists to prevent. Refusing it is the
allowlist-shaped fix: it closes the symlink bypass, and it makes the third kind unreachable in
`_sql_file_names`, which is what makes `key=str` genuinely equivalent instead of merely untested. The
`isinstance(entry, Path)` in the symlink guard is sound *because* of that refusal, and the two are
now called as a pair with each docstring saying so.

**This is the third time in three rounds that a claim was checked against the cases in front of me
rather than against the list** — and round 5 below makes it the fourth in four. M1 in round 3 named three modules as registering nothing without
checking any of them; the `key=str` record said "both kinds" two lines from a paragraph enumerating
three; and the symlink docstring generalised from one-portion behaviour. The pattern is not
carelessness about a subject, it is verifying the reason a claim is true instead of verifying the
claim — and the fix that generalises is worth nothing if the sentence explaining it is still
reasoned from the example. Round 5 makes it four times in four: m3 below is a message that asserts
"the schema is split between them" while firing on the one case this phase added a dedicated test
for, in which the schema is *not* what is split. The fix for that shape has never been a better
sentence; it is checking the sentence against the case the test already builds.

Round 5 of review cleared the code — no Critical, no Moderate — and reproduced independently that the
per-segment placement is provably sufficient, that `key=str` is equivalent empirically as well as
analytically, and that all four sweeps rerun with working negative controls. It found eight Milds,
four of them in shipped code, and one of those is the substantive one.

| # | Finding | Resolution |
|---|---|---|
| m1 | the *permissive* half of the rule is untested: a mutant refusing any multi-portion package, byte-identical message, passes all 598 tests | `test_a_schema_directory_in_one_portion_of_a_split_package_is_read`, which is the only test that fails against that mutant |
| m2 | the docstring, the plan and the test helper all state the boundary as portion count; the code's boundary is whether *this segment* resolves to more than one directory | all three restated on the real axis — the error message was the only artifact that had it right |
| m3 | the refusal message says "the schema is split between them" in the very case a dedicated test covers, where `sql` is split and the schema is not; and "Ship the schema in one package" mis-aims the remedy | the message now names the ambiguous segment, says the *package* lies in several directories, and offers `__init__.py` or one directory |
| m4 | `_refuse_a_symlink`'s docstring says the two guards "are called as a pair"; at the `*.sql` child site it is called alone | the invariant that actually holds is stated: no `MultiplexedPath` reaches it, established by the pair on the way down and inherited by children of a non-multiplexed root |
| m5 | round 4's "a sweep must fail loudly" changed the note and not the tool | the harnesses now `raise SystemExit` on a pattern that does not match exactly once, and assert a green baseline first |
| m6 | a directory/file collision across portions is decided by `sys.path` order and never reaches the guard | recorded with its demonstration, prominently, as the adjacent case — see the mutation section |
| m7 | the import reaches into `importlib.readers`, a 3.11 compatibility shim, from a `>=3.14` project | `importlib.resources.readers`, with a sentence saying the guard turns on a class the stdlib does not export as API |
| m8 | the "last three" paragraph enumerates a different three | rewritten; the split-intermediate test is one of them and the one-portion read is not |

m1 is the one worth keeping. Every round before this found a claim that did not hold; this one found
a *test suite* that did not discriminate — the code was right, the argument for it was right, and no
test could tell it apart from a rule four times as blunt. Friction item 7's lesson was that a fix has
to be a rule rather than a patch for the reported case. The corollary, which took five rounds to
arrive, is that a narrow rule has to be pinned on both sides or the narrowness is an accident of the
implementation rather than a property of the world.

Nothing in this phase is product behaviour. There are no issues, no projects, no comments and none
of the 25 tools; where `components/projecttracker.md` specifies more, the slice follows its *shape*
— the same file layout, the same error classes and codes, the same schema conventions, the same
fixture id, instant and description — so Phase 10 extends this package rather than rewriting it.

## Steps

1. **`worlds/projecttracker/pyproject.toml`** — the world as a package: `projecttracker` 1.0.0,
   `dependencies = ["seahaven"]`, a `serve` extra, hatchling, and `[tool.pytest.ini_options]` so the
   directory is its own pytest rootdir.
2. **Root `pyproject.toml`** — `[tool.uv.workspace] members = ["worlds/*"]`, the world in the dev
   group and in `[tool.uv.sources]`, and `worlds` added to ruff's `src` and ty's `include`.
3. **`src/projecttracker/schema/001_core.sql`** — the `users` table, `STRICT`, explicit primary key,
   canonical text timestamps, no wall-clock DDL default.
4. **`src/projecttracker/world.py`** — the `World`, spelled exactly as `components/projecttracker.md`
   §2 spells it.
5. **`src/seahaven/world.py`** — `sql_files(package, directory)`, the schema loader that line calls,
   plus `SQL_SUFFIX`; exported from `seahaven/__init__.py` as `architecture.md` §1 lists it. The pass after review round 1
   split its body into three helpers — `_check_schema_directory`, `_sql_file_names` and `_read_sql`
   — so that every way a world author can get the call wrong is a `WorldBug` naming the package, the
   directory and, where there is one, the file, rather than a raw `OSError` or a path out of the
   package (friction item 7).
6. **`src/projecttracker/errors.py`** — `NotFound`, `InvalidInput` (with `from_violations`),
   `Conflict`, `Internal`: the four classes of §2, with their codes and message formats.
7. **`src/projecttracker/middleware/error_handler.py`** — the `architecture.md` §6 scaffold plus
   both of §2's product rules, keyed off two named tool sets.
8. **`src/projecttracker/tools/ping.py`** — the one tool, and the `tools/` and `middleware/`
   `__init__.py` files whose imports are what registration is.
9. **`fixtures_src/generate.py`** — the committed recipe, one function per fixture, shaped for the
   `seahaven fixture freeze <id> --run module:function` that Phase 7 writes.
10. **`fixtures/empty/`** — the frozen artifact and its sidecar, built by that recipe.
11. **`README.md`, `AGENTS.md`, `.gitignore`**, and a CI step that runs the world's suite.

## Deviations and decisions a reviewer should check

- **`sql_files` accepts `str | None` and refuses `None` at runtime, and that is not defensive
  programming — it is the only way the spec's own line type-checks.** Every world's `world.py` is
  `schema=seahaven.sql_files(__package__, "schema")`, and `__package__` is typed `str | None`. With
  the parameter typed `str`, `ty` fails on the canonical line, and a world author's first act would
  be to silence a type checker in the file the scaffold generated for them. So the parameter takes
  `str | None` and `None` is refused with a `WorldBug`.

  The refusal is not politeness about a bad argument. `importlib.resources.files(None)` does not
  fail: it resolves the *caller's* package, which is `seahaven` itself, so the world would have been
  built out of whatever `seahaven/schema/` happened to hold — today nothing, which raises "holds no
  `*.sql` file" and names the wrong package; tomorrow, if the framework ever ships a directory of
  that name, a silently wrong schema. The empty string is refused with it, for the same reason and
  because `files("")` raises something unrelated. `test_a_package_that_is_not_a_package_name_is_refused_by_name`
  covers `None`, `""` and a non-string.

  This was found by a test, not by reading: the "unimportable package" test passed for the wrong
  reason, and asking why is what surfaced it.

- **The world is a uv workspace member, and its tests are a separate pytest rootdir.** Two decisions
  in one file. The workspace is what makes `projecttracker` an ordinary installed package —
  `import projecttracker` from anywhere, `uv run --project worlds/projecttracker`, and in Phase 7
  world discovery by package name rather than by path, which is `functional_spec.md` §2.1's rule
  ("a world is a package, never a directory loaded by path"). The separate rootdir
  (`[tool.pytest.ini_options]` in the world's own `pyproject.toml`) is what lets the world's test
  modules be named the way a world's author would name them. `components/projecttracker.md` §5 asks
  for `test_fixtures.py` and `test_errors.py`; the framework suite already has modules of both
  names, and two same-named test modules in one rootdir without `__init__.py` files is an import
  error, not a failure a later phase would enjoy diagnosing. It also means the world is tested the
  way a scaffolded world is tested: `cd` into it, run `pytest`, nothing of the framework's
  configuration in scope. CI runs both suites, in two steps.

- **The error handler is written in full, including both product rules, and one of them is for a
  tool that does not exist yet.** §2 gives the handler three jobs beyond the scaffold: let a
  `DbError` from `run_sql` past with SQLite's text, turn a `DbError` from `search_issues` into
  `InvalidInput("query", ...)`, and re-raise `WorldBug`. `search_issues` is Phase 10's. The
  alternative was to write half the rule now and the other half with the tool, and that is worse
  than it looks: the branch would be written later by someone reading the same sentence again, and
  the handler is the one module in this package that every later tool depends on for its error
  vocabulary. So both rules are here, both are named by tool in `SQL_DOOR_TOOLS` and `SEARCH_TOOLS`
  rather than by string literal in a branch (a world that registers `run_sql` under another name
  edits a set), and both are tested — against real tools raising real `DbError`s, on probe worlds,
  not against stubs. The `search_issues` test registers a stand-in tool of that name over an FTS5
  table and gives it a query FTS5 rejects; when Phase 10 writes the real one, the rule it needs is
  already pinned.

- **Middleware tests run against probe worlds, not against the shipped `world`.** The handler maps
  what the tools *below* it raise, and this world has one tool, which raises nothing. Testing it as
  a plain function with a stub `next_` would prove it maps an exception, not that it maps one raised
  by a tool inside a real chain inside a real transaction. `tests/conftest.py`'s `probe` fixture
  builds a throwaway `World` carrying *this world's real `error_handler`* and the failing tools the
  test needs, and every case goes through `world.instance(...)` → `instance.call(...)`. Registering
  those tools on the shipped `world` instead would put them in the registry every other test, every
  later phase and every served instance sees, for the life of the process.

- **`InvalidInput.from_violations` puts the first violation's field in `details` and folds the rest
  into the message.** §2 says only "joins every violation into one INVALID_INPUT". `details` has to
  stay `{"field": ...}` because that is the shape every `INVALID_INPUT` this product returns has, so
  something must decide which field it names: it is the first violation's, and the message reads
  `email: must contain @; name: too long`. The first violation's field is not repeated in the
  message, because `__init__` already prefixes it — the version that did repeat it produced
  `email: email: must contain @`, which a test caught. An empty list returns
  `INVALID_INPUT arguments: invalid` rather than raising `IndexError`; nothing in the framework
  produces one, and a world author calling the classmethod by hand should get this product's error
  rather than a traceback.

  **Corrected after review round 1.** This entry used to add that a violation with no path is "pydantic
  reporting the model rather than a field — *an unknown argument is one*", and so becomes the field
  `arguments`. The parenthesis is false, and the same false sentence was in `errors.py`'s docstring
  and in the test's. `Tool.validate` builds `path` as `".".join(loc)`, and `extra_forbidden`'s `loc`
  *is* the argument name: `call("ping", mesage="typo")` produces `path='mesage'`, which is exactly
  the field an agent needs, and the branch is never taken. So the no-path branch is unreachable
  today, in the same way as the empty-list branch beside it — which this entry had honestly labelled
  as such, which is what made the inconsistency visible. All three places now say that, and say why
  the branch is kept: a `dict.get` that can return nothing should say what it does then, once,
  rather than reach an agent as `{"field": ""}`. Two tests pin the two defaults, `arguments` for a
  missing path and `invalid` for a missing message; before that pass the second was an unrecorded
  survivor.

- **`ping` is not in `components/projecttracker.md`'s tool table, and that is what the
  implementation plan asked for.** The plan names "one `ping` tool" for this phase. It is
  deliberately thin and deliberately a *read*: it returns the message it was given, `ctx.clock.iso()`
  and `SELECT count(*) FROM users`, which exercises validation, the chain, the transaction, the
  clock, the database and the serialiser without asserting anything about a product that does not
  exist yet. Because it is a read, it stays true of every fixture this world will ever have, which
  is what makes it the call for a Phase 6 transport test, a Phase 7 scaffold check or a smoke test
  to make. Phase 10 adds the 25 tools beside it; nothing about `ping` needs revisiting then.

- **The schema slice keeps §1's `users` columns exactly, including the constraints nothing yet
  reads.** `email UNIQUE`, the three-value `role` CHECK and `NOT NULL` on name and `created_at` are
  in the file although no tool writes a user. They are the DDL contract Phase 10 extends and the
  thing `schema_hash` — which is stamped into the committed fixture's sidecar — is computed from;
  writing them later would invalidate the fixture for no reason. The file is `001_core.sql` so that
  §1's `002_search.sql` lands beside it in the order `sql_files` reads.

- **The fixture is frozen at §4's instant, with §4's description, by a committed recipe.**
  `fixtures_src/generate.py` holds `NOW`, `DESCRIPTIONS` and one builder function per fixture, and
  `empty`'s builder deliberately does nothing: `empty` is made the same way its two Phase 10
  siblings will be — freeze a blank instance through this module — rather than by a hand-run
  snippet that would leave the first fixture the only one with no source. `main(argv)` is the
  by-hand path until Phase 7's CLI exists, and the module's `__main__` block puts the world's `src/`
  on `sys.path` first so the script works in a checkout where the world is not installed.

- **The rebuild test compares content always and bytes conditionally.** §4 wants byte-identical
  rebuilds asserted in CI "with the content hash as the documented fallback". `VACUUM INTO` is
  byte-reproducible on one SQLite build, not across builds, so an unconditional byte comparison
  makes an apsw upgrade look like a corrupt fixture. The test reads the `SQLITE_VERSION_NUMBER`
  SQLite stamps at offset 96 of the committed file and compares bytes only when the running SQLite
  is the one that wrote it; the schema, the rows, the sidecar's `now`, `schema_hash`, `parent_id`
  and the description are compared either way. It drives `generate.main([])` with the world's
  `fixtures_dir` redirected to `tmp_path` — through the committed recipe, not through a
  re-implementation of it, because a test that rebuilt the fixture its own way would stay green
  while the recipe rotted.

- **~~A local `Handler` alias in the middleware module.~~ Corrected after review round 1: the reason recorded
  here was false, and the alias is gone.** What this entry said was that `Handler` and `Middleware`
  live in `seahaven/call.py` and are not exported, so a world that types its middleware must
  redeclare the alias or reach into a private module — and that a redeclared copy, with the reason
  in a comment, was therefore the honest choice. The premise does not hold. `seahaven/world.py`
  re-exports both from `call.py` in its `__all__`, with a comment naming exactly the world-author
  case, and `components/world_and_dispatch.md` §1 lists all three aliases beside the `World` they
  describe. `from seahaven.world import Handler` was available the whole time.

  The mistake worth recording is not the alias, it is the move: I read `architecture.md` §1's export
  list, found `Handler` missing from it, concluded the type was private, worked around it, and filed
  the workaround as framework friction — without checking whether the framework had already solved
  it. That is the opposite of what a reference world is for, and it is the failure mode the phase
  brief names in a different costume: the fix closed the case I had looked at and left the adjacent
  one, where the adjacent one was *look first*.

  The world now imports `Handler` from `seahaven.world`, the local alias and its `__all__` entry are
  deleted, and `middleware/error_handler.py`'s docstring says which rule makes that import legal (see
  the next entry). **B10** is rewritten around what is actually true: two export surfaces, and a
  stated rule — `architecture.md:68` — that makes the wider of the two internal.

- **The rule for what a world may import: a component document's §1 is the public interface of the
  module it describes.** Round 1 caught the world contradicting itself — `middleware/error_handler.py`
  declined `seahaven.call.Handler` on the grounds that a world uses public names only, while
  `tests/test_empty_fixture.py` imported `seahaven.fixtures.load_all` and `verify` three times over.
  Both files were right about their own case and neither stated a rule, so the rule is stated now and
  both follow it: **a name a component document's §1 lists as part of a module's interface is public;
  `seahaven/__init__.py` re-exports only the subset worth a short import.** Under it,
  `world_and_dispatch.md` §1 makes `seahaven.world.Handler` public and `fixtures_instances.md` §1
  makes `seahaven.fixtures.load_all` and `verify` public, so the world imports both, each with a
  comment at the import saying so.

  The rule is written here and in the two files, not in `architecture.md` §1 where a world author
  would find it, because §1 is a `complete` artifact and stating a repository-wide rule about the
  public surface is not a placeholder slice's call to make.

  **Corrected after review round 2.** This entry and **B10** both used to call that a *gap* — "no
  stated rule saying which names a world may import". There is a stated rule, and it says the
  opposite. `architecture.md:68`, immediately after the list of what `__init__.py` re-exports, reads
  "Everything else is internal", and the two sentences after it name its exceptions individually:
  `Tool.from_function`, and `Authorizer`, `run_statement`, `SqlResult` and the refusal names in
  `seahaven.sandbox`. `Handler`, `Middleware`, `load`, `load_all`, `verify` and `freeze` are not
  among them, so the line as written makes the world's imports — and the framework's own `__all__`
  in `world.py` — internal-reaching. The rule this phase follows is the one the *code* follows, and
  it contradicts a `complete` artifact rather than filling a blank in one. **B10** is rewritten to
  say that, quoting line 68, so that whoever picks it up knows the job is an amendment and not an
  addition: the blanket sentence plus its two hand-listed exceptions has to be replaced by the
  general rule, under which those exceptions stop being exceptions. B10 also keeps the narrower
  suggestion that `Handler` and `Middleware` are strong candidates for the convenience subset: a
  typed middleware is the ordinary case, and `seahaven new`'s `middleware/` template is where every
  world author meets it.

- **~~`AGENTS.md` does not yet point at bundled docs.~~ Corrected after review round 1: the recorded reason was
  true and the file it described was not.** What this entry said — that `functional_spec.md` §2.1
  wants the world's `AGENTS.md` to point at the bundled docs, that `seahaven/docs/` is Phase 8's, and
  that the pointer is a one-line edit when the docs exist — is all correct. What it did not say is
  that the file as written opened by telling an agent to run `seahaven docs` and `seahaven check`
  before committing. Neither command exists: the CLI is Phase 7. So the plan recorded a deliberate
  omission while the file shipped the opposite defect, and a reviewer reading the two together found
  a decision that described a different file.

  `AGENTS.md`'s head is rewritten. It points at `architecture.md` and `components/projecttracker.md`,
  which do exist and are where a contributor should start today; it then says plainly that the two
  commands a finished world's `AGENTS.md` names are Phase 7's and Phase 8's and that the pointer goes
  in when they do. The decision stands — the pointer is still deferred — but the file now matches it,
  and the reason it gives is one a reader can check.

  The general lesson, since eight phases follow this one: a phase plan entry saying "X is deferred" is
  only worth something if the artifact actually defers X. Write the entry after re-reading the file,
  not from the intention that produced it.

- **A built world wheel ships no fixtures, and this phase changed nothing about that.** Verified in
  review round 1 rather than assumed: `uv build --project worlds/projecttracker` produces a wheel holding
  `projecttracker/` and nothing else. The schema travels — `schema/*.sql` is *inside* the package and
  `sql_files` reads it through `importlib.resources` — but `fixtures/` sits at the project root
  beside `src/`, which is where `freeze` writes it and where `World`'s `fixtures_dir` default finds
  it, and a wheel has no project root. Installed into a clean venv, `world.instance()` works and
  `world.fixtures()` is `[]`, so `world.instance("empty")` raises the `WorldBug` that says the
  fixture is not in `.../site-packages/fixtures`.

  Nothing here is broken as specified: the layout is `functional_spec.md` §2.1's, the error message is
  a good one and `World(fixtures_dir=...)` is the escape hatch. §2.2 even half-anticipates the case —
  `fixtures_dir` falls back "to `fixtures/` beside the package where there is none, as in an installed
  wheel" — without saying how the directory gets there. What is missing is a statement of whether a
  served world is a checkout or an install, and that is a decision Phase 6's `serve` and Phase 7's
  `--hub` image (which does `pip install .[serve]`) will otherwise make by accident. Recorded as
  **B11** with the three ways to close it — fixtures inside the package, a `force-include` in the
  world's `pyproject.toml`, or "a served world is a checkout" said out loud. Not this phase's to
  pick: it is a cross-component decision, and `architecture.md` wins on cross-component structure.

  **Corrected after review round 2.** B11 presented the first two of those three as small changes.
  Neither is. *Fixtures inside the package* is framework work, not a world's directory move:
  `World.__init__` coerces its argument with `Path(fixtures_dir)` (`src/seahaven/world.py:117` as
  this phase leaves it) and
  `seahaven.fixtures` is `Path`-typed throughout — `Fixture.dir`, `Fixture.state_path`, `load`,
  `load_all`, `verify`, `freeze` and the copy that makes an instance — while `importlib.resources`
  hands back a `Traversable` that `Path(...)` rejects for a zip member; and `freeze` *writes*, which
  a read-only `Traversable` has no answer for. *`force-include`* does build and install, but it lands
  `fixtures/` at the top of `site-packages` rather than under any package, so two installed worlds
  that each scaffolded an `empty` fixture share one `site-packages/fixtures/empty/` and the second
  install breaks both. Only the third — saying out loud that a served world is a checkout — costs
  nothing to state.

  B11 now records that, and deliberately proposes no fourth closure. Finding that neither of the
  obvious two works is the useful thing to hand Phase 6 and Phase 7; inventing a third here, without
  the cross-component decision that has to come first, would be the same mistake as picking one.

## Friction found in the framework

The implementation plan's rule is that friction found in the reference world gets fixed in the
framework rather than worked around in the world. Items 1–4 and 7 were fixed here; 6 and 8 are
recorded because they belong to code that is already committed, and 5 was withdrawn after review round 1
because it was not friction at all.

1. **`sql_files` did not exist.** Phase 2 deferred it to "the phase that first builds a world
   package (Phase 5)". Written here and exported, with nine tests in the first pass — five rounds of
   review later it has 32, and the difference is the subject of friction item 7 below.
2. **`__package__` is `str | None`.** Fixed in the framework's signature, not worked around in the
   world — see the decision above.
3. **`importlib.resources.files(None)` resolves the caller's package.** Guarded in `sql_files`,
   with the reason in its docstring, because the argument that triggers it is the one every world
   passes.
4. **The DDL loader had no story for a directory that is missing, is a file, or holds no SQL.** All
   three are `WorldBug`s naming the package, the directory and where it looked; an empty schema
   would otherwise build a database with no tables perfectly happily and surface as a missing table
   much later.
5. **~~`Handler`/`Middleware` are not exported.~~ Withdrawn after review round 1: they are.**
   `seahaven/world.py` re-exports both, and `components/world_and_dispatch.md` §1 lists them. This
   was not framework friction, it was me not looking. What remains — and what B10 now says — is that
   Seahaven has two export surfaces, the component documents' §1 interfaces and
   `seahaven/__init__.py`'s convenience subset, and `architecture.md:68` says the second is the whole
   of the public one. The code follows the first. That is a contradiction in a `complete` artifact,
   not a missing sentence.
6. **A committed fixture cannot keep its `0o444` mode through git**, which makes SH405 as specified
   fire on every fresh clone of every world that commits a fixture — B9. The mode is right where
   `freeze` writes it; git records only the executable bit, so a clone gets `0o644`. This phase's
   fixture test asserts the file's *contents* and its two-file directory, and says in a comment why
   it does not assert the mode.
7. **`sql_files`'s own error surface was thinner than its docstring claimed** — found in round 1 and
   fixed here, in the framework. Two paths raised raw: an `OSError` from listing the directory and an
   `OSError` or `UnicodeDecodeError` from reading a file, each arriving as a traceback out of an
   import with no world in it. Three more were missing outright: `directory` was never checked, so
   `".."` or `"/etc"` silently read files the package does not ship and could not be resolved in a
   zip at all — which contradicted the portability promise in the function's first paragraph; a
   `*.sql` entry that is a directory rather than a file was reported as "holds no `*.sql` file",
   which sends the author looking in the wrong place; and `__name__` where `__package__` belongs
   produced a message rendering an `importlib` adapter's `repr`. The `is_dir()` failure also said
   "does not exist" about a file. Every one of them now names the package; each also names the
   directory, and the file where there is one. The `__name__`-for-`__package__` message names the
   package alone, and correctly so: it is raised because the argument is not a package at all, which
   is decided before any directory inside it means anything. Two of them were found by mutation
   rather than by review: `read_text()` without `encoding="utf-8"` follows the locale, and splitting
   only on `/` lets a backslash traversal past — both are the same portability promise, and both are
   pinned by a test now (the first by a subprocess under `LC_ALL=C` with UTF-8 mode and C-locale
   coercion off, because the encoding is fixed when the interpreter starts).

   **The check itself was then rewritten after review round 2, and that is the interesting part.**
   What was written above as "`directory` was never checked" became a *denylist*: refuse `..`, refuse
   a leading `/`, then — after mutation — refuse a backslash too, then refuse a Windows drive.
   Extended three times, each time to close the case in front of it. Review round 2 found two more
   classes it did not cover. `.`, a doubled separator and a trailing separator are not traversals at
   all and still break the same promise, because `Path.__truediv__` on a filesystem drops them and
   `zipfile.Path.joinpath` (which is `posixpath.join`) keeps them, so `schema/.` builds from a
   checkout and fails once installed. And `\etc` and `C:schema` *are* traversals that
   `PureWindowsPath.is_absolute()` calls relative, because it wants a drive **and** a root. Four
   extensions, and a fifth waiting.

   The fix is not a fifth extension. `_check_schema_directory` now splits on either separator and
   validates **each segment positively**: a segment must be a non-empty ordinary name, so `..`, `.`,
   an empty segment and anything containing `:` are all refused wherever they appear, and the
   function returns the segment list that `sql_files` then joins one step at a time. That ends the
   sequence — there is no spelling of "leaves the package" left to think of, because nothing is
   allowed through except names. The two refusals carry different messages, because they are
   different mistakes to the author: one leaves the package, the other is not spelled as a path
   inside it. The docstring records that the denylist was extended three times and that there would
   have been a fourth, so the next person to touch it does not start the sequence again.

   The same pass closed the hole the allowlist alone leaves: a segment can be a perfectly ordinary
   name and still be a **symlink** out of the package, and no zipped wheel holds a symlink, so a
   world whose schema is reached through one builds from a checkout and fails once installed —
   exactly the promise everything else here is about. `_refuse_a_symlink` is applied to every
   directory on the way down, not just the last, and to every `*.sql` child. It is guarded on
   `isinstance(entry, Path)` because a zip `Traversable` has no `is_symlink`.

   The pin is an end-to-end test that a denylist could not have passed: eight directory spellings
   resolved through the real `sql_files` against a package on disk and against the same package
   zipped, asserting the two answers agree *and* that the agreed answer is the schema or a refusal.

   **And then a seventh defect, in review round 3, on the argument nobody had been looking at.**
   `directory` was now checked a segment at a time; `package` was checked only for being a non-empty
   string. A name beginning with a dot — `.projecttracker`, `.`, `..` — reaches
   `importlib.import_module` as a *relative* import, which raises `TypeError`, not `ImportError`, so
   it goes past the handler that exists to name a bad package and lands on the author as a traceback
   out of `importlib._bootstrap` mentioning no world, no directory and not Seahaven. Fixed the same
   way as the directory and for the same reason: every dotted part of a package name has to be a
   Python name, which is wider than the reported case and does not depend on having thought of it.
   The same pass pinned the handler's width in the other direction, which no test had constrained —
   a world whose own module body raises must fail as itself, not be relabelled a schema problem.

   Seven defects in one function across three rounds, four of them adjacent to a fix. Both arguments
   are allowlists now, which is the shape that ends it; what took three rounds was applying the same
   lesson to the second argument as to the first.

   **An eighth in round 4, and it came in through neither argument.** `importlib.resources.files()`
   answers a `MultiplexedPath` for a namespace package, and with two portions on `sys.path` its
   `joinpath` answers another one and its `iterdir` merges two real directories. The symlink guard,
   which asks `isinstance(entry, Path)`, is neither asked nor able to answer for that — a
   `MultiplexedPath` is not a `Path` and *is* on a filesystem — so one portion could be a symlink
   pointing anywhere and its files were read into the schema as if the package shipped them. The
   reviewer demonstrated it. A schema directory split across two `sys.path` entries cannot be
   reproduced by any wheel, so it is refused outright, per segment; that closes the bypass and
   restores the symlink guard's `isinstance` to a sound test rather than a silent skip.

   The lesson is not about namespace packages. Both arguments were made allowlists; the third input
   to this function is not an argument at all but the shape of what `importlib.resources` hands
   back, and nothing was asking what that could be. `sql_files` now refuses on all three.

   **Round 5 found no ninth defect, and found something else instead.** The eighth fix is narrow and
   correct — refused per segment, only when *that segment* resolves to more than one directory — but
   only its refusing half was tested. A mutant that refuses any package of more than one portion,
   with a byte-identical message, passed all 598 tests. Eight defects across four rounds say
   that a rule beats a patch; round 5 adds the other half of that, which is that an untested
   permissive boundary is not a rule the code has, it is a rule the author believes. The test that
   tells the two apart is `test_a_schema_directory_in_one_portion_of_a_split_package_is_read`, and
   it is the only test in the suite that fails against that mutant.

   Round 5 also recorded, rather than fixed, the case adjacent to the eighth: two portions where one
   holds `schema/` and the other an ordinary file named `schema` never build a `MultiplexedPath` at
   all, so the guard is never reached and `sys.path` order decides the answer. It is safe both ways
   and it is in the mutation section with its demonstration — because the single most reliable fact
   about this function is that the adjacent case comes back.
8. **A built world wheel ships no fixtures** — B11, and the decision above. Not fixed here, because
   which way it should be fixed is a cross-component decision that lands on Phase 6's `serve` and
   Phase 7's `--hub` image.

One smaller observation, recorded here rather than in `BACKLOG.md` because both artifacts state it
deliberately: the agent-facing SQL door takes `query` (`components/helpers_and_control.md` §1) and
the control tool takes `sql` (§3). Writing both in one session is where that is felt; it is a
documented difference, not a defect.

## Tests

`worlds/projecttracker/tests/` — 72 tests, in the world's own rootdir. Every one goes through
`world.instance(...)` → `instance.call(...)`; nothing calls a tool function directly.

`tests/conftest.py`: `FIXTURE_NOW`, a `BLANK_NOW` deliberately different from it (so a test
asserting one cannot pass because of the other), the `tracker` and `blank` instance fixtures, and
the `probe` world builder.

- **`test_package.py`** (20) — the world's identity and version; `projecttracker.world` is the
  `World` object and not the module of that name; the schema is exactly the `*.sql` on disk; the
  registry is `ping` plus the two control tools; the listing is `["ping"]` with a non-empty
  description; the error handler is the outermost middleware; **importing the package in a fresh
  interpreter is what registers the tool and the middleware** (a subprocess, because the test
  modules' own imports mask registration); every module under `tools/` and `middleware/` is imported
  by its package (the property SH301 lints for); the fixtures directory resolves to the one in the
  repository; and the `users` table asked about itself — the three roles accepted and a fourth
  refused, a duplicate email and a duplicate id refused, every column refused as `NULL`, and
  `STRICT` both in `pragma_table_list` and in a refused BLOB.
- **`test_ping.py`** (12) — the default message, the echo, the clock, the user count; the published
  JSON schema (default, `maxLength`, `additionalProperties: false`); an unknown argument and three
  rejected messages, each an `INVALID_INPUT` naming `message`; an unknown tool is `UnknownTool` and
  does not pass through the handler; `ping` leaves the changeset empty; two instances of one fixture
  are isolated.
- **`test_declared_errors.py`** (13) — each class's code, message and details, and the five
  `from_violations` cases (two violations, one, a violation with no path, a violation with no
  message, and an empty list). The last two pin the two documented defaults, `arguments` and
  `invalid`; before review round 1 the `invalid` one was an unrecorded survivor.
- **`test_error_handler.py`** (12) — every branch, through a real `Instance.call` on a probe world:
  `NotFound` and `Conflict` pass through untouched; an `ArgumentError` becomes one `INVALID_INPUT`
  carrying every violation, with the framework's own error — and its full violation list, which the
  restated message flattens — kept on `__cause__` (added after review round 1: the chain was an unrecorded
  survivor, while the two `DbError` branches beside it were both pinned); a `DbError` under an ordinary tool becomes
  `INTERNAL` with no SQLite text anywhere in it and the engine's message only on the chained cause;
  that suppressed error is logged at `ERROR` with its traceback; a constraint violation is
  `INTERNAL` and the call's writes are rolled back (checked through `instance.inspect()`); a
  `DbError` from `run_sql` keeps SQLite's own text; a refusal from `run_sql` keeps the framework's
  wording; an FTS5 syntax error under `search_issues` becomes `INVALID_INPUT` on `query` carrying
  FTS5's complaint; an unexpected exception becomes `INTERNAL`; a `WorldBug` is re-raised unchanged;
  a successful call is untouched.
- **`test_empty_fixture.py`** (15) — the fixture list, description, `now` and `parent_id`; `verify`
  and the sidecar's `file_sha256`; the sidecar's `schema_hash` is this world's; no `-wal`/`-shm`
  siblings and exactly two files; an instance of it starts at the frozen instant with no rows; using
  it does not touch the committed bytes; the generator still makes what is committed — content
  always, bytes in a separate test that decides whether it can compare them *before* asserting
  anything, so a run that verified everything it could is never reported as skipped; the generator
  freezes what its builder wrote; it refuses a fixture id it does not know, on stderr; run as a
  script it refuses to overwrite a committed fixture; run as a script from a checkout it builds the
  world beside it and not an installed one; it is importable as `fixtures_src.generate`, the module
  path its own docstring tells an author to freeze with; and its two signatures resolve to framework
  types.

`tests/test_world.py` (framework) gains 32 `sql_files` tests, 62 cases with the parameters: sorted
filename order (`001`, `002`, `010`), only `*.sql` read, a `World` built end to end from a package on
disk, each file's text kept intact, a nested schema directory, a package read from a zip (the
portability promise from the other side), a missing directory, a directory holding no SQL, a file
where the directory should be, a directory named like a `*.sql` file, a listing that raises
`OSError`, a file that raises `OSError`, a file that is not UTF-8 text, a file read as UTF-8 under
`LC_ALL=C` in a subprocess with UTF-8 mode and C-locale coercion off, eleven directories that leave
the package (`..` and a root, on both separators, both Windows spellings `is_absolute()` calls
relative, and a drive letter in a *later* segment), five that do not leave it and are still not a
path inside it (`./schema`, `schema/.`, `schema//`, `schema/`, `.//schema`), a schema directory that
is a symlink, one reached *through* a symlink, a `*.sql` file that is a symlink, a namespace package of one
portion (where `files()` answers a `MultiplexedPath` and joining a segment onto it answers a real
path), a namespace package of two portions whose schema is in only one of them (which reads), one
whose schema is in both (which does not), one whose *intermediate* directory alone is split, one
that uses the split to reach a symlink, a `Traversable` that implements the protocol and nothing
else, the three non-name directories, an unimportable package, a package whose own module
body raises, six package names that are not Python names, a module passed where a package belongs,
and the three non-names (`None`, `""`, `42`).

Ten of the 32 were written in the pass after review round 1, from its two reported cases and the
adjacent ones they implied. Seven more were written in the pass after review round 2, and one
of them is the test the rest of them exist for: eight directory spellings resolved through the real
`sql_files` against a package on disk and the same package zipped, asserting that the two agree *and*
that the shared answer is the schema or a refusal rather than two different failures reported alike.
That test is what a denylist could not have passed, and it is the pin on the allowlist that replaced
it. Three of the seven came from mutation rather than from the review: a drive letter in a later
segment (`schema/C:evil`, which `PureWindowsPath` joining replaces the whole path with), a namespace
package, and the bare `Traversable` — see the mutation section.

The last two came after review round 3, and they are one defect and its opposite edge. A package name
beginning with a dot reaches `importlib.import_module` as a *relative* import and raises `TypeError`,
not `ImportError`, so it went straight past the handler that turns a bad package name into a
`WorldBug` — the author got a traceback out of `importlib._bootstrap` naming no world, no directory
and not Seahaven. The other edge was unpinned in the other direction: widening that handler to
`except Exception` survived every test, so nothing stopped a future edit from swallowing a world's
own import-time exception and relabelling it as a schema problem. Both are pinned now.

The last three came after review round 4 and all three are the same fact, and a fourth came after
round 5 because the first three had only pinned one side of it.

The fact is that `files()` answers a `MultiplexedPath` however many portions a namespace package
has, and what decides everything after that is `joinpath`: a segment present in one portion resolves
to a real `Path` however many portions there are, and a segment present in two or more resolves to
another `MultiplexedPath`. A `MultiplexedPath` has no `is_symlink`, so the symlink refusal's
`isinstance(entry, Path)` test skipped it in silence and a schema from outside the package was read.
The pre-existing namespace test had a package of one portion, where the second case cannot arise, so
it could not see any of this.

The three written after round 4 are the two-portion refusal, the two-portion refusal reached through
a symlink (asserting the smuggled file's own marker text is nowhere in what comes back), and the
split *intermediate* directory — `sql` in both portions, `sql/schema` in one — which came from
mutation rather than from the review. The one-portion read was rewritten in the same pass but is not
one of the three: it was already there.

The fourth, after round 5, is the permissive side of the rule: a package of two portions whose
`schema` is in only one of them, which resolves to a real `Path` and reads. Nothing pinned that, and
the gap was not academic — a mutant widening the guard to refuse *any* package of more than one
portion, with a byte-identical message, passed all 598 tests. The implemented boundary and a much
cruder one were indistinguishable by test. That is friction item 7's lesson at one more remove: it is
not enough for the fix to be narrow, the tests have to be able to tell that it is.

## Mutation check

Every statement of the world's package and of `fixtures_src/generate.py`, and of `sql_files` in
`src/seahaven/world.py`, was replaced with `pass` in turn and both suites re-run on cleared
`__pycache__` with `PYTHONDONTWRITEBYTECODE=1`. Then forty-three mutations statement deletion cannot
express — a dropped constraint, a reordered `except`, an emptied set, a replaced expression, a
swapped import order — were made by hand (the table below folds the four `NOT NULL` mutations,
one per column that declares it, into one row).

**The pass after review round 1 added twenty-five more**, over the code that review changed: twenty-one
over the rewritten `sql_files` and its three new helpers, and four over the world's error paths. The
seven previously recorded survivors were re-run against the code as it then stood — thirty-two runs
in all, twenty-five of them new mutants. Numbers and survivors are folded into the tables below; what
the round found is set out under them.

Every number below comes from a harness that proves the mutant is the code that ran: a pytest plugin
that, in the same interpreter the tests run in, imports the packages that suite depends on at session
start and aborts unless each resolves inside the mutation tree by absolute path. The world sweep
checks `seahaven` and `projecttracker`; the framework sweep checks `seahaven` alone, because
importing the world would call `sql_files` at module level and abort the session ahead of the tests
— see the method note at the end of this plan. Both suites are run for a framework file; the world's
suite for a world file.

| File | Statement mutants | Killed |
|---|---|---|
| `projecttracker/__init__.py` | 3 | 2 |
| `projecttracker/world.py` | 2 | 2 |
| `projecttracker/errors.py` | 22 | 19 |
| `projecttracker/tools/__init__.py` | 2 | 1 |
| `projecttracker/tools/ping.py` | 9 | 7 |
| `projecttracker/middleware/__init__.py` | 2 | 1 |
| `projecttracker/middleware/error_handler.py` | 22 | 21 |
| `fixtures_src/generate.py` | 27 | 27 |
| `seahaven/world.py` (`sql_files` and helpers) | 35 | 35 |

`error_handler.py`'s row is the round-2 recount: the module lost a statement (the local `Handler`
alias) and gained one (the import that replaced it), and the `def` under the `@world.middleware`
decorator is not a runnable mutant — deleting it is a syntax error, so it is excluded rather than
scored. `seahaven/world.py`'s row is the rewritten function and its three helpers. Every other row
is unchanged: those files' code did not change in that pass, and each of their survivors was re-run.

**The sweep found four gaps in the tests, all in the fixture recipe, and every one of them is the
same mistake: a test that re-implemented what it was checking.** `build`'s `builder(inst)` call,
`main`'s two stderr lines, and `main`'s success line all survived, because the rebuild test built
the fixture its own way and only the *result* was compared. The fix was to drive `module.main([])`
with the world's `fixtures_dir` monkeypatched, assert what it prints, register a builder that
actually writes a row and look for that row in the frozen file, and assert the stderr wording of an
unknown id. That took the recipe from 21/27 to 25/27.

**The hand mutations found a second gap, in the schema.** Dropping `NOT NULL` from a column was
"caught" — by the fixture's `schema_hash`, which notices any edit to the DDL text at all. That is a
tripwire on the file, not a statement about the table, and it would have said exactly the same thing
if the constraint had never been intended. `test_the_users_table_refuses_a_row_with_a_column_missing`
now asks the table, once per column, and each of the four mutations is killed by its own parameter.
The adjacent constraint, `id TEXT PRIMARY KEY`, has `test_the_users_table_refuses_a_duplicate_id`
for the same reason, though the mutant that removes it never reaches the assertion: the framework
refuses to make an instance of a world with an untracked, key-less table at all.

The last two survivors of that file were killed rather than recorded, because both had an adjacent
case worth closing: `import seahaven` survives deletion under PEP 649 (the annotations that use it
are never evaluated), and `sys.path.insert(...)` in the `__main__` block survives because the tests
run where the world is already installed — the one environment the line does not exist for.
`test_the_generators_functions_say_in_their_types_what_a_builder_is` resolves both signatures and
kills the first; `test_the_generator_run_as_a_script_builds_the_world_it_lives_beside` copies the
package to `tmp_path`, renames the copy's version to 9.9.9, runs the script there and asserts the
sidecar says 9.9.9 — which is only true if the script's own `src/` came first. Both were then
confirmed by hand: each mutant fails that named test and no other.

Nine statement survivors remain, in three families, all recorded rather than killed (ten before the
round-2 audit moved one of them into the killed column):

| Survivor | Why it is not a test's job |
|---|---|
| `__all__` in six modules | Nothing imports from these modules with `*`, and world discovery reads the `world` attribute, not `__all__`. It is documentation of the module's surface, and the same equivalence phases 1–4 record. |
| `from collections.abc import Sequence` and `from typing import Any` in `errors.py` | Both names appear only in the annotations of `from_violations`, a classmethod nothing registers as a tool, so nothing at runtime evaluates them. Not equivalent to the gate: deleting one is `error[unresolved-reference]` from `ty`, which is checked before every commit. Confirmed by running `ty` against the mutant. **The pass after review round 1 removed `error_handler.py`'s `Any` from this row: it is not annotation-only in practice — see the correction under "After review round 1" below.** |
| `assert row is not None` in `ping` | `Db.one` returns `None` only when a query returns no row, and `count(*)` always returns one, so the assertion cannot fire. It is a statement to the type checker: deleting it is `error[not-subscriptable]` from `ty` on the next line. Confirmed the same way. |

The forty-three hand mutations and the test that kills each. Forty-two are killed; the one survivor is
equivalent and recorded below.

| Mutation | Killed by |
|---|---|
| `users.email` loses `UNIQUE` | `test_the_users_table_refuses_a_duplicate_email` (+5) |
| `users.role` loses its `CHECK` | `test_the_users_table_refuses_a_role_that_is_not_one_of_the_three` (+4) |
| any of the four columns that declare `NOT NULL` loses it (four mutations) | `test_the_users_table_refuses_a_row_with_a_column_missing[<column>]` |
| the table is no longer `STRICT` | `test_the_users_table_is_strict` (+4) |
| `users.id` loses `PRIMARY KEY` | every test that makes an instance: the framework refuses to attach a changeset session to a table with no explicit primary key (`changes._refuse_a_table_with_no_primary_key`) |
| `created_at` renamed | `test_the_users_table_refuses_a_duplicate_email`, `test_ping_counts_the_users_it_can_see` (+8) |
| `SQL_DOOR_TOOLS` emptied | `test_a_database_error_from_the_sql_door_keeps_sqlites_own_text`, `test_a_refusal_from_the_sql_door_keeps_the_frameworks_wording` |
| `SEARCH_TOOLS` emptied | `test_a_database_error_from_the_search_door_becomes_invalid_input` |
| the two sets swapped | both of the above (3 tests) |
| `ToolError` caught before `ArgumentError`/`DbError` | `test_a_database_error_under_an_ordinary_tool_becomes_internal` (+7) |
| the search error carries fixed text | `test_a_database_error_from_the_search_door_becomes_invalid_input` |
| `WorldBug` becomes `INTERNAL` | `test_a_world_bug_is_re_raised_and_never_becomes_a_product_error` |
| an unexpected exception escapes raw | `test_an_unexpected_exception_becomes_internal` |
| the suppressed `DbError` logged without its traceback | `test_a_database_error_the_agent_never_sees_is_written_to_the_log` |
| the suppressed `DbError` re-raised instead of `INTERNAL` | `test_a_database_error_under_an_ordinary_tool_becomes_internal` (+2) |
| `ping`'s default message changed | `test_ping_answers_with_the_message_the_clock_and_the_user_count` (+3) |
| `ping` echoes a constant | `test_ping_echoes_the_message_it_is_given` |
| `ping` reports a constant instant | `test_pings_time_is_the_instances_clock_and_not_the_wall_clock` |
| `ping` counts nothing | `test_the_users_table_accepts_the_three_roles` (+5) |
| `message`'s `max_length` widened | `test_ping_publishes_a_schema_an_agent_can_read`, `test_ping_refuses_a_message_its_model_does_not_accept` |
| `INVALID_INPUT` renamed | `test_invalid_input_names_the_field` (+7) |
| `NOT_FOUND` drops its details | `test_not_found_names_the_kind_and_the_key` (+1) |
| `INTERNAL`'s default message changed | `test_internal_says_nothing_about_what_went_wrong` (+2) |
| `from_violations` prefixes the first field twice | `test_from_violations_reports_every_violation_in_one_error`, `test_from_violations_on_one_violation_reads_like_a_hand_written_one` |
| a violation with no path reports an empty field | `test_from_violations_survives_a_violation_with_no_path` |
| only the first violation reported | `test_from_violations_reports_every_violation_in_one_error` |
| the world's version bumped | `test_the_world_is_named_and_versioned` (+2) |
| the world's name changed | `test_the_world_is_named_and_versioned`, `test_the_fixture_was_frozen_from_this_worlds_schema` |
| the schema read from the wrong directory | the package cannot be imported: the world's whole suite fails to collect |
| the frozen instant moved | `test_the_generator_still_makes_the_fixture_that_is_committed` (+2) |
| the fixture's description changed | `test_the_generator_still_makes_the_fixture_that_is_committed` |
| `main` ignores the ids it was given | `test_the_generator_refuses_a_fixture_it_does_not_know` |
| `sql_files` no longer sorts | `test_sql_files_reads_every_sql_file_in_filename_order` (+1) |
| `sql_files` reads every file | `test_sql_files_reads_only_sql_files`, `test_a_schema_directory_holding_no_sql_is_a_world_bug` |
| `sql_files` joins without a newline | `test_sql_files_reads_every_sql_file_in_filename_order` |
| an empty package name accepted | `test_a_package_that_is_not_a_package_name_is_refused_by_name[]` |
| a non-string package name accepted | `test_a_package_that_is_not_a_package_name_is_refused_by_name[42]` |
| a file where the directory should be accepted | `test_a_file_where_the_schema_directory_should_be_is_a_world_bug` |
| `SQL_SUFFIX` = `".SQL"` | `test_sql_files_reads_every_sql_file_in_filename_order` (+3, framework suite) |
| the package's two import lines swapped | **equivalent — see below** |

**The equivalent mutant.** `projecttracker/__init__.py` imports `middleware` and `tools` for their
registrations and then binds `world`; swapping the two lines leaves the package in exactly the same
state. Verified rather than argued: in a fresh interpreter under the swapped order,
`projecttracker.world is world` still holds and the registry still holds `ping` and the two control
tools. The reason is that Python sets a parent package's submodule attribute when the submodule is
*first* imported, and by the time `middleware` imports `projecttracker.world` that module is already
in `sys.modules`, so the attribute the `from ... import world` line bound is not overwritten. The
order is still the right one to keep — it reads as "register, then expose", and the shadowing hazard
it protects against is real enough that a test asserts the attribute is the `World` — and the
mutated order is in any case rejected by `ruff check` (`I001`), which runs before every commit.

### After review round 1

The review changed `sql_files` substantially and the world's error paths slightly, so the sweep was
re-run over both, and every equivalence record was re-run against the code as it now stands. Same
harness, same guard, cleared `__pycache__`, `PYTHONDONTWRITEBYTECODE=1`.

**`sql_files` and its three new helpers: 35 statements deleted one at a time, 35 killed, plus
twenty-one hand mutations, all twenty-one killed.** The hand mutations are the ones deletion cannot
express, and the two that mattered were both found this way and neither by review:

| Mutation | Killed by |
|---|---|
| `read_text(encoding="utf-8")` → `read_text()` | `test_a_schema_file_is_read_as_utf8_whatever_the_locale_says` |
| `re.split(r"[/\\]", directory)` → `directory.split("/")` | `test_a_schema_directory_that_leaves_the_package_is_refused[..\elsewhere]`, `[schema\..\..]` |
| the `_check_schema_directory` call dropped | `test_a_schema_directory_that_leaves_the_package_is_refused` (+7) |
| the `__path__` guard inverted, and its body dropped | `test_a_module_inside_a_package_is_refused_by_name` |
| "is a file" and "does not exist" swapped | `test_a_file_where_the_schema_directory_should_be_is_a_world_bug`, `test_a_schema_directory_that_is_not_there_is_a_world_bug` |
| the listing's `OSError` left unwrapped | `test_a_schema_directory_that_cannot_be_listed_is_a_world_bug` |
| the read's `OSError`/`UnicodeDecodeError` left unwrapped, and each caught alone | `test_a_schema_file_that_cannot_be_read_is_a_world_bug`, `test_a_schema_file_that_is_not_utf8_text_is_a_world_bug` |
| the `directory` type/empty guard dropped, and each half of it | `test_a_schema_directory_that_is_not_a_name_is_refused[None]`, `[]`, `[42]` |
| each half of the absolute-path test dropped (POSIX, Windows) | `test_a_schema_directory_that_leaves_the_package_is_refused[/etc]`, `[C:/etc]` |
| the `..` test dropped, and the whole traversal raise dropped | the same test's other five parameters |
| a non-file `*.sql` entry skipped rather than refused | `test_a_directory_named_like_a_sql_file_is_a_world_bug` |
| the listing unsorted; the `*.sql` filter dropped | `test_sql_files_reads_every_sql_file_in_filename_order`, `test_sql_files_reads_only_sql_files` |
| the sort key changed from `child.name` to `str` | **survives; equivalent only because a merged listing is refused — recorded below** |

That last row is an equivalent mutant, and the reason it is equivalent is a *consequence of another
guard* rather than a property of sorting. It is worth reading carefully, because the first version of
this record was false and review round 4 demonstrated it.

`sorted(root.iterdir(), key=str)` orders identically to `key=lambda child: child.name` whenever every
child of one listing shares a parent: `str(child)` is then that common prefix followed by the name,
and a shared prefix cannot change the comparison. That holds for `pathlib.Path`, where `str` is the
full path, and for `zipfile.Path`, where it is the archive path plus the member's `at`.

It does **not** hold for the third kind. `MultiplexedPath.iterdir()` merges the children of two or
more real directories, so they do not share a parent, and the two keys disagree: with
`path0/…/002_b.sql` and `path1/…/001_a.sql`, `key=str` runs `002` before `001` — which silently
reverses the `001_`/`002_` order this function's whole docstring is about, putting a `CREATE INDEX`
before its `CREATE TABLE`. The first version of this record said the function sees "both kinds of
entry", two lines from a paragraph of this same plan enumerating three. That was not a close call; it
was the same mistake as M1 at a different granularity — a claim checked against the cases that came to
mind rather than against the list already written down.

What makes the mutant equivalent now is that `_refuse_more_than_one_directory` refuses a
`MultiplexedPath` root before `_sql_file_names` is ever called, so a merged listing is unreachable and
the two remaining kinds do share a parent. The record therefore depends on that guard: **if the
multiplexed-root refusal is ever relaxed, this equivalence stops holding and the mutant becomes a
live defect.** `child.name` stays for that reason as much as for readability — it is correct on all
three kinds, and it says what the order is.

Two of those rows are the ones worth keeping in mind, because neither is the kind of thing a reader
finds: `read_text()` without an encoding follows the *locale*, so a world with an accented comment in
its schema builds on a developer's machine and raises `UnicodeDecodeError` under `LC_ALL=C`; and
splitting the directory on `/` alone lets `schema\..\..` past, which is a legal filename on POSIX
and a traversal on Windows. Both break the same promise — that a world builds the same everywhere —
and both are now pinned, the first by a subprocess test that turns off UTF-8 mode and C-locale
coercion, because the encoding is fixed when the interpreter starts.

**The world's error paths: four hand mutations, three killed, one recorded.**

| Mutation | Killed by |
|---|---|
| `from error` dropped on the `ArgumentError` branch | `test_an_argument_error_becomes_invalid_input_and_keeps_the_framework_error_behind_it` |
| `first.get("message", "invalid")` → `first.get("message")` | `test_from_violations_survives_a_violation_with_no_message` |
| `first.get("path") or "arguments"` → `first.get("path")` | `test_from_violations_survives_a_violation_with_no_path` |
| `from seahaven.world import Handler` → `from typing import Any as Handler` | **survives — see below** |

The first two were the review's Mild 9, both genuine and both unrecorded; the third was already
covered and is here because it is the same family. The fourth survives and is worth a record rather
than a test: `Handler` appears only in an annotation, and re-typing that annotation as `Any` changes
nothing a test can observe. Nor does any gate catch it — `ty` accepts `Any` — so what the import is
protecting against is drift, not a runtime failure, and the thing that would catch it is the reason
being written down where the import is. *Deleting* the import is a different matter, and is the
correction below.

**The audit of the equivalence records found one that does not hold.** Seven mutants were re-run
against the current code — one per recorded family, plus the new `Handler` import. Five survive and
stand: `__all__` in `errors.py` and in `error_handler.py`, the annotation-only `Sequence` and `Any`
in `errors.py`, and `assert row is not None` in `ping`. Two are killed, and both concern
`middleware/error_handler.py` — one of them a record that was wrong:

> **Corrected.** The record said that `from typing import Any` in `error_handler.py` is an
> annotation-only import, lazy under PEP 649, killed only by `ty`. It is not lazy there, and neither
> is the new `Handler` import. `World.middleware` calls `inspect.signature(obj)` to check the
> middleware's shape at registration, and in Python 3.14 that evaluates the annotations — so a
> *registered* callable's annotation names must resolve at import time. Deleting either import is a
> `NameError` inside `__annotate__` while `projecttracker` is being imported, which takes
> `tests/conftest.py` with it and fails the world's entire suite to collect. Both are killed, and
> both move out of the equivalence table.
>
> The record was wrong when it was written, not made wrong by the round-2 edits, and that is the
> point of auditing them: an equivalence record is a claim that nothing can observe the change, and
> this one had a whole suite observing it. The records that survive are the ones in `errors.py` and
> `ping.py`, where the names appear only in the annotations of functions nothing registers —
> `from_violations`, a classmethod — which is the distinction the record now states, rather than
> "annotations are lazy". (It first stated it as "a module nothing registers". That is a different
> claim and a false one; see the round-2 summary in the Overview.)

Ten statement survivors were recorded in the first pass; nine remain.

### After review round 2

The guard code that review round 2 replaced was mutated again from scratch, on a tree refreshed from
the working copy, with `__pycache__` cleared and `PYTHONDONTWRITEBYTECODE=1`, under a resolution
guard asserting from inside the test process that `seahaven.__file__` is under the mutation root.

**Statement deletion over `sql_files`, `_check_schema_directory`, `_refuse_a_symlink`, `_sql_file_names`
and `_read_sql`: 44 statements, 44 killed, no survivors** — each by a *named* test.

That last clause cost a second run and is worth recording. The first sweep reported twenty-one of the
44 as killed with `rc=3` rather than a `FAILED` line, because the resolution guard imports
`projecttracker` at session start, `projecttracker/__init__.py` calls `sql_files` at module level, and
a mutant of `sql_files` therefore aborted the session before a single test ran. That is a kill, but it
is not a *named* one, and the standing practice asks for a named test. The framework sweep does not
need `projecttracker` resolved — the suite never imports it — so it runs under a guard that checks
`seahaven` only, and the world sweep keeps the guard that checks both. Re-run that way, all 44 name a
test. A resolution guard that also aborts the run is a guard that hides what the run was measuring.

**Hand mutations over what statement deletion cannot reach: 15, all 15 killed, no survivors.** The
separator pattern losing each of its two branches; each of the three clauses of the leaving test
dropped; `:` narrowed to the first segment only; the leading-separator anchor moved off position 0;
each half of the unspellable test dropped; the loop narrowed to the first segment and to the last;
the segments joined in one step instead of one at a time; the symlink guard's `isinstance` dropped
and its condition inverted; and the `*.sql` symlink check removed.

**Three of those needed a new test, and two of the three were real holes.**

- *`:` narrowed to the first segment* survived, and should not have. `PureWindowsPath("schema") /
  "C:evil"` is `C:evil` — joining a drive-relative segment *replaces* everything before it rather
  than appending to it — so `schema/C:evil` leaves the package from a segment that is not the first.
  The code was already right; the test set was not. `schema/C:evil` is now a parameter. This is the
  phase's named failure mode one more time, at one remove: the fix was general, and the tests pinning
  it were not.
- *the symlink guard's `isinstance(entry, Path)` dropped* survived, and the first attempt to kill it
  failed for a reason worth keeping. A namespace package makes `importlib.resources.files()` answer a
  `MultiplexedPath`, which has no `is_symlink` — but joining a segment onto one yields a real `Path`,
  and the guard only ever sees joined entries, so the namespace test passed either way. Of the three
  implementations in play, `Path` and `zipfile.Path` both answer `is_symlink` and the third never
  reaches the guard. `Traversable` does not require the method, so the test that kills the mutant is
  a `Traversable` implementing the protocol and nothing else. The namespace-package test was kept
  anyway: it was written on a false assumption and turned out to cover a real third shape of
  `Traversable` that nothing else tested.
- *the symlink guard inverted* was killed by an existing test, and is listed here because it is the
  cheap direction of the same check.

No equivalence record was added in this pass, and none of the existing nine describes code this pass
changed: the rewrite is confined to `_check_schema_directory`, the new `_refuse_a_symlink`, and the
loop in `sql_files`, none of which had a survivor recorded against it.

### After review round 3

The two sweeps were re-run over the changed function on a tree refreshed from the working copy, with
cleared `__pycache__`, `PYTHONDONTWRITEBYTECODE=1`, and the resolution guard. **Statement deletion:
46 statements, 46 killed**, each by a named test — the two added are the package-name allowlist and
its raise. **The round-2 hand sweep re-run unchanged: 15, all 15 killed**, so nothing the new guard
displaced went unpinned.

**Five new hand mutations over the package-name guard and the import handler, all five killed.**

| Mutation | Killed by |
|---|---|
| the package-name allowlist dropped | `test_a_package_name_that_is_not_a_python_name_is_refused[.relative]` |
| only the first dotted part checked | the same test's `[pkg.]` |
| the allowlist narrowed to the `startswith(".")` the defect was reported as | the same test's `[pkg.]` |
| `except ImportError` widened to `except Exception` | `test_a_world_that_fails_on_import_fails_as_itself` |
| `except ImportError` widened to `except BaseException` | the same test |

The third row is the narrow fix as it was reported, run as a mutant against the general one, and it
fails a test by name.

**What that kill is and is not**, since review round 4 measured it and the first version of this
paragraph overclaimed. Only the leading-dot family — `.`, `..`, `.relative`, `.projecttracker` —
actually escapes the import handler, because only a leading dot makes `import_module` read the name
as a relative import and raise `TypeError`. Everything else the allowlist refuses (`pkg.`, `a..b`,
`a-b`, `a b`, `2pkg`) already raised `ModuleNotFoundError` and was already wrapped in a `WorldBug`.
So the mutant fails on the *message*, not on escaping-versus-not, and the width bought a better
message rather than a new class of catch.

That is still worth having, and worth being accurate about: the old message for `"pkg."` was
`cannot read schema of package 'pkg.': No module named 'pkg'`, which quotes a name the author never
wrote and sends them looking for a package that is missing rather than for the stray dot they typed.

**And the width has a real cost, which belongs in the record rather than out of it.** `a-b` and
`2pkg` are importable packages — `import_module` finds them and they have a `__path__` — so
`sql_files` read them before this guard and refuses them now. That is a new class of false refusal,
made deliberately: a package whose name is not a Python name cannot be written as an `import`
statement, so it cannot be a world's package in any way that a world author would recognise, and
`__package__` never holds one. The message says that in those terms. But it is a behaviour this
phase removed, not only a message it improved.

The fourth and fifth are the handler's width in the direction no test had constrained. `except
ImportError` is narrow on purpose: a world package whose own module body raises is not a world that
cannot be read, and relabelling its exception `WorldBug: cannot read schema of package` would take
the author's own error away and point them at the schema directory, which is the one place the bug
is not.

One equivalent mutant was added to the tables in this pass and none removed: `key=str` in
`_sql_file_names`, recorded with its reason above.

### After review round 4

Same harness, tree refreshed, bytecode cleared, resolution guard in place. **Statement deletion over
`sql_files` and its five helpers: 49 statements, 49 killed**, each by a named test — the three added
are the multiplexed-root refusal, its raise, and the call that runs it once per joined segment.

The round-3 hand sweep (5) was re-run unchanged and still kills all five. The round-2 sweep (15) was
re-run with one mutant re-expressed, and the re-expression is worth recording: H12 patched the join
loop by replacing its body verbatim, the body now has a second guard call in it, and so the patch
matched nothing and the harness *skipped* the mutant instead of running it — a mutant that cannot be
applied reports as neither killed nor survived, which is how a sweep quietly stops covering the line
it was written for. H12 is rewritten to move the *symlink* call to the last segment while leaving the
multiplexed-root call per-segment, which is the mutation it always meant and is distinct from K3
below, and it is killed by `test_a_schema_directory_reached_through_a_symlink_is_refused`. **All 15
kill.**

**Four new hand mutations over the multiplexed-root guard and the sort key.**

| Mutation | Killed by |
|---|---|
| the multiplexed-root refusal dropped | `test_a_schema_directory_split_across_two_sys_path_entries_is_refused` |
| the refusal inverted | `test_sql_files_reads_every_sql_file_in_filename_order` |
| the refusal applied to the last segment only | `test_a_schema_directory_reached_through_a_split_directory_is_refused` |
| `key=lambda child: child.name` → `key=str` | **survives, and is now equivalent — see the record above** |

The third row was a survivor at first, and the test that kills it was written because of it rather
than the other way round. Checking only the directory the schema is in accepts a package whose
*intermediate* directory is split: `sql` in both portions, `sql/schema` in one, so the join resolves
back to a single `Path` and the last-segment check sees nothing wrong. It is wrong all the same —
which `schema` the world gets depends on the order of `sys.path`, and installing both portions
merges them — so the refusal stays per-segment and the case is pinned.

The fourth row is the `key=str` mutant, which survives on purpose now. Its equivalence is conditional
on the row above it, and the record says so: relax the multiplexed-root refusal and this stops being
an equivalent mutant and becomes a live defect that reverses `001_` and `002_`.

### After review round 5

Same harness with two changes to the harness itself, described under "the sweep as a tool" below.
**Statement deletion over `sql_files` and its five helpers: 50 statements, 50 killed** — one more
than round 4, because the refusal message now chooses its own opening clause and that choice is a
statement; it is killed by `test_a_schema_directory_split_across_two_sys_path_entries_is_refused`.
The round-2 (15) and round-3 (5) sweeps re-run unchanged from round 4, all killed, every pattern
matching exactly once — which the harness now proves rather than assumes.

**One new hand mutation, and it is the one that mattered.**

| Mutation | Killed by |
|---|---|
| the guard widened to refuse every package of more than one portion, message byte-identical | `test_a_schema_directory_in_one_portion_of_a_split_package_is_read` |

Review round 5 found that this mutant **survived all 598 tests**, which is to say the narrow rule
that shipped and a much cruder one were indistinguishable by test. Only the refusing half of the
boundary was pinned; the permitting half — a package of two portions whose `schema` is in one of
them, which resolves to a real `Path` and reads — was not tested at all. The test named above is
that case, and it is the *only* test that fails against the mutant, which is the measurement that
says the gap was real rather than incidental.

**The sweep as a tool.** Round 4's method note said a sweep "must fail loudly" on a mutant whose
pattern no longer matches, but nothing in the harness changed — only how its output was read, and
the "of N run" trailer that would have caught H12 was already printed when H12 was skipped. The
harnesses now `raise SystemExit` on a pattern that does not match exactly once, so the note is true
of the tool a later round re-runs rather than of the person running it. They also assert a green
baseline before counting a single kill: review round 5's own harness produced a bogus 49/49 because
`scripts/` had not been copied into its tree and every "kill" was a collection error in
`test_licence_check.py`. A sweep that has not shown the unmutated tree passes is not measuring
mutation.

### Recorded, not fixed: a directory/file collision across portions

Two portions where one holds `schema/` and the other holds an ordinary *file* named `schema` is the
same ambiguity the guard exists to refuse, and the guard never sees it.
`MultiplexedPath._follow` builds a `MultiplexedPath` only when every colliding entry is a directory;
otherwise it falls through to `next(one_file)` and returns whichever portion comes first on
`sys.path`. Demonstrated through the public entry point, one tree, two answers:

```
portion holding the DIRECTORY first  (__path__ len=2) -> 'CREATE TABLE t (a TEXT) STRICT;'
portion holding the DIRECTORY second (__path__ len=2) -> WorldBug: schema directory 'schema' of
                                                         package 'collide' is a file, not a directory
```

It is recorded rather than fixed because both outcomes are safe — nothing outside the package is
read, and the failing order fails loudly — and because the fix would mean reaching past
`importlib.resources` into `__path__` to re-derive what `_follow` already decided, which is the kind
of guess this function has been wrong about before. But it is recorded *prominently*, because it is
the adjacent case, and the one thing this function's history establishes is that the adjacent case
comes back. The next round that touches the multiplexed guard should start here.

## Follow-up, not this phase

- **Whichever phase next touches `sql_files`' multiplexed guard** starts at "Recorded, not fixed: a
  directory/file collision across portions" in the mutation section above. It is the adjacent case to
  round 4's fix, it is demonstrated, and it is deliberately left open — not overlooked.
- **Phase 10** adds, to this package and without rewriting it: `002_search.sql` and the nine
  remaining tables of §1, `tools/_types.py`, `tools/_events.py` and the 25 tools of §3 (with the
  keyset pagination, the viewer rule and the closed-issue rule), the `run_sql`/`describe_schema`
  registrations over the nine tables, the `small_startup` and `agency` fixtures, and §5's test
  modules. The four error classes, the handler and both its rules, the fixture recipe's shape and
  the layout are settled here. The handler's `SEARCH_TOOLS` already names `search_issues`.
- **Phase 6** adds this world's `src/projecttracker/openenv_app.py`; the implementation plan says
  the placeholder needs only that file. `ping` is the call its transport tests want.
- **Phase 7**'s `seahaven new` renders this layout, and `seahaven check` runs against this package:
  SH101–SH103 over `001_core.sql`, SH301 over `tools/` and `middleware/` (there is a test here for
  the property it lints), SH401–SH405 over `fixtures/empty` — see **B9** for SH405 — and SH501 for
  discovery. The CLI's `fixture freeze --run` is what `fixtures_src/generate.py` is shaped for.
- **Phase 8** moves these tests onto the pytest plugin and gives `AGENTS.md` the bundled-docs
  pointer `functional_spec.md` §2.1 asks for.
- `BACKLOG.md` gains **B9** (a committed fixture cannot keep `0o444` through git, which SH405 as
  specified would report on every fresh clone), **B10** (two export surfaces — the component
  documents' §1 module interfaces and `seahaven/__init__.py`'s convenience subset — and
  `architecture.md:68` declaring everything outside the second internal, which the framework's own
  `__all__` lists contradict; rewritten twice, see the decision above) and
  **B11** (a built world wheel ships no `fixtures/`, so a world installed rather than checked out
  can only be run blank).
- **`fixtures_src/generate.py`'s documented entry point is `fixtures_src.generate:<id>`**, which is
  the module path Phase 7's `--run module:function` will import, and
  `test_the_generator_is_reachable_by_the_module_path_its_docstring_names` pins that it resolves.
  Round 1 found the docstring naming `projecttracker_fixtures.generate`, which exists nowhere; a
  recipe that names an entry point nobody can import is one the CLI phase would find broken.

## Method notes

- **Check whether the framework already solved it before recording friction.** Round 1's two wrong
  reasons were both this: `Handler` was exported and I did not look, and `AGENTS.md` deferred a
  pointer while shipping two commands that do not exist. Neither was a coding mistake; both were a
  recorded reason that nobody had checked against the thing it described. The reference world exists
  to find real framework friction, and a workaround filed as friction is worse than no finding —
  it puts a false entry in the backlog that a later phase will act on.
- **A phase plan entry is a claim about an artifact, so re-read the artifact when writing it.** Both
  corrected entries above would have been caught by opening the file one more time before writing
  the sentence that described it.
- **The world's suite is a second suite, and a mutation harness has to run both.** A change to
  `src/seahaven/` can be invisible to `tests/` and fatal to the world, or the reverse. The runner
  used here picks the suites a file can affect — both for a framework file, the world's for a world
  file — and a mutant is killed if either fails.
- **The guard that proves the mutant ran must import every package that suite depends on — and no
  more.** Phase 4's guard imported `seahaven` only; with two installed packages in the workspace, a
  mutant in `projecttracker` would have been reported faithfully only by luck. So this phase's guard
  imports both and checks both by absolute path, in the process the tests run in — and that turned
  out to be one package too many for the framework sweep. `projecttracker/__init__.py` calls
  `sql_files` at module level, so importing it at session start turned every `sql_files` mutant into
  an abort before a single test ran: a kill, but an unnamed one, and twenty-one of the forty-four
  were being counted that way. What shipped is two guards. The framework sweep checks `seahaven`,
  which is the only package its suite imports; the world sweep checks both, which is what its suite
  needs. The rule is *every package the suite under test imports, resolved by absolute path from
  inside the test process* — not *every package in the workspace*, which is how it was first written
  and is how it over-reached. A guard that also aborts the run hides what the run was measuring.
- **A test that rebuilds an artifact its own way is not a test of the recipe.** Four of this phase's
  six survivors were that one mistake. The rule the fixture tests now follow is that the committed
  entry point — `main`, run as `main` — is what the test calls, and only the world's `fixtures_dir`
  is redirected.
- **`ty` is part of the mutation answer, not a separate concern.** Three survivors here are killed
  by the type checker and by nothing else, and the honest record is not "equivalent" but "caught by
  a different gate". Each was verified by running `ty` against the mutant rather than assumed.
- **A hand mutant that patches by text can stop applying, so the harness must refuse to continue.**
  Round 4 re-ran the round-2 sweep and one of its fifteen mutants matched nothing, because the line
  it replaced had gained a second guard call — so the harness printed `BAD PATTERN` and moved on,
  and the 15 became a 14 that still *looked* like a clean sweep. Round 4 wrote this note and changed
  nothing in the tool, which is the part worth recording: the "of N run" trailer it pointed at was
  already being printed when H12 was skipped, so the note described a reading convention and the
  next stale mutant would have been skipped identically. The harnesses now `raise SystemExit` on a
  pattern that does not match exactly once. A note about method that leaves the tool alone is a note
  about intention.
- **A sweep must assert its baseline is green before it counts a single kill.** Review round 5's own
  harness reported a clean 49/49 while measuring nothing: `scripts/` had not been copied into its
  mutation tree, so every run failed collecting `test_licence_check.py` and every mutant "died" of
  that. It caught its own mistake by adding a baseline run, and the same assertion is now in all four
  harnesses here. The failure mode is silent and it looks exactly like success — a sweep whose
  environment is broken kills everything, including mutants that are alive.
