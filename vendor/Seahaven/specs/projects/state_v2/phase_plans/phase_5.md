---
status: complete
---

# Phase 5: Documentation

## Overview

Phases 1-4 built the change log, the call log, the state document, the three formats, the world
pin and the OpenEnv surface, and removed `changes()`, `Change`, `render()`, the long-lived session
and `controller_changes`. The bundled docs still describe most of that from the outside and, in a
handful of places, still describe what was removed. This phase makes `src/seahaven/docs/` describe
what shipped.

The centre of the phase is a new page, `state.md`, written from `functional_spec.md` §3, §4, §5,
§6 and §10 in the case order §12 asks for. The rest is a sweep: `serving_and_openenv.md` gains the
state-over-the-wire section and loses its control-tool text, `concepts.md` links the two traps,
`composition.md`, `db_schema_and_fixtures.md`, `testing.md`, `authoring.md`, `reference/api.md` and
`reference/lints.md` move from the old vocabulary to the log and the document, `index.md` and the
scaffold's `AGENTS.md` list the new page, and two guards in `tests/test_docs.py` keep
`controller_` and `changes()` off every page but one line of `reference/cli.md`.

Everything is written under `AGENTS.md`'s "Docs style": plain sentences, disclosure in order, a
table of contents on a long page, prose at 100 columns, every name checked against the code, every
example executed by `tests/test_docs_examples.py`. No cost figure is quoted; that is phase 6's.

## Steps

### The new page

1. **`src/seahaven/docs/state.md`.** A table of contents at the top, then, in order:

   - **Opening.** What the document is and why it matters: one plain dict, the provenance of the
     episode and what the episode changed, saved with `json.dump` and nothing else. One executed
     example on ProjectTracker reading `inst.state()` and asserting `format`, `world`,
     `call_count` and that `json.dumps` accepts it. A line pointing at
     `serving_and_openenv.md` for the same document over the wire.
   - **The envelope and `state`.** The two halves and their owners. A `jsonc` block of the whole
     root, and a field table for every envelope field of FS §3.1, with `composition` and
     `fixture.nodes` both keyed by canonical path and `path` named as the join key across the
     document. The sentence that a formatter answers the value of `state` and nothing else.
   - **Choosing a format.** Four cases, each with its reason, in FS §12's order:
     `seahaven.state/1` for a `final_state` read once at the end (executed example: the log of a
     two-call episode); `seahaven.state+last_step/1` for a per-step reader such as an OpenEnv
     rollout harness (executed example: two reads over four calls); `seahaven.state+calls/1` for a
     harness that cannot reconcile its own trace (executed example: `calls` indexed by `i`, with
     the `error` entry); and registering your own (executed example: a self-contained `notes`
     world with a `@world.state_format` formatter), with the name rule, the `None` instance, the
     read-a-built-in-and-edit-it path, and the "runs under the lock, never in a transaction, never
     writes" rules.
   - **Where the format is chosen.** A table of `World(state_format=)`, `world.instance(
     state_format=)`, `reset(state_format=)`, `inst.state(format=)` and `inst.state_format`. The
     root decides the format of a tree, and an added world's pin and registrations are not
     consulted -- with an executed example on a two-node world built inline. `state_format` as a
     reserved reset keyword. When a bad name is caught: a `seahaven.` name at `World(...)`, a
     custom name at the first `instance`/`reset`, `EXECUTION_ERROR` over the wire. The
     recommendation to bump `World.version` with a pin change.
   - **When to read it.** Between calls, never inside one: `state()` inside `bulk()` or a tool
     call raises `WorldBug`, and why.
   - **The change log.** The record shape as a `jsonc` block and a field table, then the rules:
     net per call, not net across calls, every node in one list under one `i`, the key never
     repeated inside an update, a primary-key rewrite as delete plus insert, empty is empty, what
     is tracked, startup writes not in the log, `bulk()` writes carrying `i: null`, no cap. Then
     order: by call, and within a call by `world`, `table` and the key's rendered values, with the
     blob-key and `main`-sorts-alphabetically notes.
   - **The fold, and the two traps.** The three-step fold of FS §3.6, including the "untouched +
     any record is that record" base case; that Seahaven does not ship it. Trap one, overcounting,
     with an executed example of one row updated by two calls. Trap two, two episodes with the
     same end state and different logs.
   - **Values.** The SQLite-to-JSON table, the 2^53 note, and the infinity rule in a sentence.
   - **The state the episode started from.** `composition` and `fixture.nodes` by path as the
     lookup, with an executed example reading both on a fixture instance and `fixture is None` on
     a blank one; the blank-instance case the format does not promise to make reproducible.
   - **The compatibility contract.** The envelope's contract and the format's, the
     ignore-unknown-fields rule, and that anything else is a new major.
   - **Why nothing derivable is in the document.** No fold, no counts, no rows, no schema; that
     `state()` does no database work; and that recording a call is not free, with the sentence
     saying the measured figure is not quoted yet.

### The sweep

2. **`src/seahaven/docs/serving_and_openenv.md`.**
   - Replace the stale paragraph at the end of "Calls, results and errors" that lists the `state`
     message's fields with a pointer: the `state` message answers the document, and "Grading a
     run" has it.
   - Rewrite "Grading a run": the `state` message answers the state document; `SeahavenState` is
     the document plus OpenEnv's `step_count`, with `world`, `composition`, `fixture` and `state`
     typed as `WorldRef`, `dict[str, NodeRef]`, `FixtureRef` and `dict[str, Any]`;
     `state().model_dump(exclude={"step_count"})` is byte for byte `inst.state()`;
     `reset(state_format=...)` chooses the format for the episode, and
     `seahaven.state+last_step/1` is the one for a harness that reads after every step; `null`
     before the first `reset`; a link to `state.md`.
   - Delete the "The control tool" subsection whole, and the `--include-control-tools` row from
     the `serve` option table's description of it -- the row stays, worded without the tool's
     name, since the flag is still a flag of the command.
   - Update the wire-protocol tables' `state` rows to say the document.
   - Update the contents table's "Grading a run" row.
3. **`src/seahaven/docs/concepts.md`.** In "The change log", add the sentence linking the two traps
   on `state.md` and point the closing paragraph at `state.md` rather than only naming
   `inst.state()`.
4. **`src/seahaven/docs/composition.md`.** Drop the `controller_run_sql` sentence from "What an
   eval sees"; point the `state` bullet at `state.md` for the whole document.
5. **`src/seahaven/docs/db_schema_and_fixtures.md`.** Line ~45: "the changeset session" becomes the
   change log's per-call session, matching `db.py`'s `conn` docstring. Line ~95: a row with no key
   cannot be identified in a change-log record.
6. **`src/seahaven/docs/testing.md`.** "The changeset, for anything an eval will grade" becomes
   "The change log ..."; the closing "what not to test" line stops saying "the changeset renders".
7. **`src/seahaven/docs/authoring.md`.**
   - The refused-tool-names list stops spelling the control tool's name (the `controller_` guard);
     the refusal itself names it.
   - Add the `World(state_format=...)` paragraph: required, the root's pin governs a tree,
     `world.instance(state_format=)` and `reset(state_format=)` override it, `state_format` is a
     reserved startup-hook parameter name, and a link to `state.md`.
   - Line ~550: FTS5 shadow tables stay out of the change log.
   - Line ~628: grade on state and the change log, not on the order of an activity feed.
8. **`src/seahaven/docs/reference/api.md`.** `World(...)`'s `untracked_tables` prose and `db.conn`'s
   invariant sentence lose "changeset session"; add `Instance.state_format` and
   `World.pinned_state_format`/`World.resolve_state_format`/`World.state_format` where the page's
   member lists are, and check the `Instance`, `LogRecord` and `CallRecord` entries phase 4 wrote
   against the code rather than rewriting them.
9. **`src/seahaven/docs/reference/lints.md`.** SH101 gains one sentence: STRICT is also what keeps
   every row visible to the change log, because a STRICT table refuses `NULL` in a primary-key
   column and such a row would never be recorded.
10. **`src/seahaven/docs/index.md`.** `state.md` in the reading-order table, between `testing.md`
    and `serving_and_openenv.md`.
11. **`README.md`.** The two comments that still call the document a diff say document; the
    change-log feature bullet points at `state.md`.
12. **`src/seahaven/cli/templates/base/AGENTS.md.tmpl`.** `state.md` in the reading list.

### The guards

13. **`tests/test_docs.py`.** `state.md` joins `PAGES` after `testing.md`. Two new
    parametrised-over-`PAGES` tests:
    - no page has a line containing `controller_`, except `reference/cli.md`, which has exactly
      one;
    - no page has a line containing `changes()`.
    `reference/cli.md`'s `--include-control-tools` row is reworded to name `controller_run_sql`
    once, so that the exception is a real line and not an empty carve-out.

## Tests

- `test_docs.py::test_every_page_of_the_layout_exists[state.md]` and
  `test_every_page_has_a_heading[state.md]` -- the new page is in the layout and has a heading.
- `test_docs.py::test_the_docs_are_where_seahaven_docs_says_they_are` -- the shipped tree is
  exactly `PAGES`, so `state.md` is packaged.
- `test_docs.py::test_every_page_index_md_links_to_is_a_page_of_the_layout` -- `index.md`'s new
  link resolves.
- `test_docs.py::test_no_page_names_a_control_tool_but_the_cli_reference` -- every page, no
  `controller_`; `reference/cli.md` exactly one line.
- `test_docs.py::test_no_page_calls_the_removed_changes_method` -- every page, no `changes()`.
- `test_docs_examples.py::test_every_python_example_runs` -- every new `python` fence on
  `state.md` executes and exits 0.
- `test_docs_examples.py::test_every_python_fragment_parses` -- every new `py` fence parses.
- `test_docs_examples.py::test_every_seahaven_name_a_page_uses_exists` and
  `test_every_documented_member_exists` -- every `seahaven.*` name and every `inst.`/`world.`/
  `record.` member the new page spells resolves on a live object.
- `test_docs_examples.py::test_every_documented_command_line_parses` -- the `sh` fences still
  parse against the real CLI parser.
- `test_docs_examples.py::test_the_harness_collects_blocks_of_every_tier` -- the floors still hold
  with the new page's blocks counted.
- `test_cli_new.py` -- the scaffolded world still writes `AGENTS.md` and passes `seahaven check`.
