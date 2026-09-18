---
status: complete
---

# Phase 6: Docs

## Overview

Five phases built composition and nothing written down says it exists. A world author reading the
bundled docs today finds no `add_world`, no `ctx.worlds`, no composite fixture, and an `api.md`
whose `Change` block is missing a field the code now writes on every record. This phase is the
documentation, and its only hard rule is that it describes **the code**, not the spec: several
things the functional spec and the architecture describe are not what shipped, and each phase's
commit message says which.

The deviations the pages have to be written against, rather than around:

- `World._tools_by_fn` does not exist. What a world publishes is `tools_by_fn`, multi-valued, and
  the composition's own `by_fn` groups contributed entries (phase 1, phase 3).
- Contribution folds repeated names, so a node reached twice through one host contributes one entry
  per name (phase 1).
- The activation epoch moves out of depth 0 as well as in, so a handle dies when its call returns
  rather than when the next one starts (phase 2).
- Control tools resolve on the root's own registry: no entry can carry one, because nothing
  contributes them (phase 2).
- `NodeReport` carries `frozen_world_version` (phase 4).
- `NodeMeta.file` is refused unless it is a plain file name (phase 4).
- Architecture §15's promise that `check` reports the node count was withdrawn; §15.1 records why,
  and SH504 is where a count is ever said (phase 5).

`reference/lints.md` was authored in phase 5, because `tests/test_docs.py` fails the moment a
`code="SHnnn"` literal exists that the page does not carry. This phase reviews those eight sections
rather than writing them.

Everything in `src/seahaven/docs/`, `README.md` and `CONTRIBUTING.md` is executed by
`tests/test_docs_examples.py`, and `ruff format` formats Python blocks inside Markdown, so every
example here is real code that runs.

## Steps

1. **`src/seahaven/docs/composition.md`**, new — the page. Sections, in order:
   - What composition is, and the one-screen example: two worlds, `add_world`, a host tool calling
     the added world through `ctx.worlds`, a cross-node read through `inst.inspect()`, and
     `Change.world`.
   - **Declaring** — `add_world`'s parameter table (the added world's object, `name`, `store`,
     `tool_prefix`, `tool_allow_list`, `tool_block_list`, `startup`), what is checked at the call
     and what waits for the seal, and why the seal is lazy.
   - **The tool surface** — one flat list in declaration order, byte-identical listings with only
     the name substituted, collisions are errors, control tools are never contributed, stale
     descriptions are SH206 and are never rewritten.
   - **Nodes, scopes and sharing** — a node is `(World object, scope)`, `store=` opens a scope for a
     world *and its whole subtree*, canonical path is the shallowest route with ties broken by
     registration order, every other route is an alias, one node is one file.
   - **Reaching an added world from host code** — the `WorldHandle` members, by name and by function
     reference, handles are call-scoped, direct SQL is allowed and second-best, no cross-world
     atomicity, `world.instance(...)` from inside a call is a `WorldBug`.
   - **Middleware** — the host's chain outermost along the whole canonical route, each layer with
     its own world's context, `call.node`, and a nested call running only the owning chain.
   - **Startup hooks** — tree order, broadcast `reset()` keywords, `startup=` bound and not
     overridable, the root's hooks seeing every node.
   - **One instance, N stores** — file naming, one clock, per-node `Ids` salted by path.
   - **Fixtures** — a `format_version: 2` sidecar with `nodes`, a version-1 sidecar for a world that
     adds nothing, freeze all-or-nothing, what create refuses, an added world's own fixtures being
     unreachable.
   - **What an eval sees** — `inst.inspect()` with every node attached under its `__` schema name,
     `Change.world`, `inst.composition()` and its `frozen_world_version`, and `state` over OpenEnv.
   - **Typed access** — calling by function reference at the instance and through a handle, the
     ambiguity a world that is a node twice creates, and the optional `Worlds` subclass with
     `Ctx[CompanyWorlds]`.
   - **What `seahaven check` adds** — the eight codes, one line each, pointing at
     `reference/lints.md`.
   - **Limits** — the attach bound, node counts under scopes, and the non-goals of functional
     spec §12.

   Executed examples are self-contained scripts: `tests/test_docs_examples.py` runs each in a
   subprocess with no `tests/worlds/` on the path, so a page example builds the worlds it needs
   inline rather than importing the committed composite tree.

2. **`tests/test_docs.py`** — `composition.md` joins `PAGES`, after `authoring.md`, because
   `test_the_docs_are_where_seahaven_docs_says_they_are` compares the tuple with the directory.

3. **`tests/test_docs_examples.py`** — the member-chain walk stops when it reaches a
   `seahaven.Worlds`. Everything after `ctx.worlds` is a child name the page's own author chose, and
   no live object can answer it: `Worlds.__getattr__` raises `WorldBug` rather than `AttributeError`
   for an unregistered name, so the existing `hasattr` walk would error the test on the first
   `ctx.worlds.payments` any page writes. The same break the harness already makes at a callable.

4. **`src/seahaven/docs/index.md`** — `composition.md` in the reading-order table.

5. **`src/seahaven/docs/concepts.md`** — the changeset section names `world` among a change's
   fields, and a short "Composition" entry pointing at the new page, since this is the page that
   defines the vocabulary every other page uses.

6. **`src/seahaven/docs/authoring.md`** — a "Adding another world" section carrying the four
   authoring requirements functional spec §13 hands here: prefer an added world's tools over direct
   SQL on its store; declare prefixes and lists to match the client's real surface; never assume you
   are the only writer to a world you add; return models rather than bare dicts, so a typed call
   completes. Plus the registration refusals `add_world` adds, beside the existing list.

7. **`src/seahaven/docs/reference/api.md`** — `add_world` in the `World` stub with its parameter
   table; `world.added_worlds`, `world.composition()` and `world.tools_by_fn` in the member table;
   `ctx.worlds` in the `Ctx` table and the `Ctx[X]` annotation; `inst.composition()` and the typed
   `call` in the `Instance` table; a `Worlds` and `WorldHandle` section; `change.world` in the
   `Change` block; `seahaven.Worlds` and `seahaven.WorldHandle` in the exported-names block.

8. **`src/seahaven/docs/serving.md`** — the `state` message answers `composition` too. One sentence,
   stale since phase 5.

9. **`src/seahaven/cli/templates/base/AGENTS.md.tmpl`** — the reading order names `composition.md`
   for a world that adds worlds, and the rules gain the one rule an authoring agent gets wrong
   silently: reach an added world through `ctx.worlds.<name>`, never by making an instance of it.

10. **`README.md`** — the "Composing worlds" section of functional spec §14, in the framework's own
    voice, with its example executed rather than quoted; and `composition.md` in the documentation
    list.

11. **`specs/projects/seahaven_framework/components/pytest_and_docs.md`** — §2's layout gains
    `composition.md`, dated, in the style of that section's existing correction note, because
    `tests/test_docs.py` cites it as the source of `PAGES`.

12. **`reference/lints.md`** — review only. Check the eight new sections against the rules they
    describe and against their neighbours' shape (Rule / Why / Fix, the fix in the code's own
    words).

## Tests

- `tests/test_docs.py` — every page of the layout exists and has a heading, and the directory is
  exactly the layout: `composition.md` is covered by the existing parametrised tests once it is in
  `PAGES`.
- `tests/test_docs_examples.py` — every `python` block on the new page is executed and must exit 0;
  every `py` fragment parses; every `seahaven` name any page spells resolves; every `inst.`, `ctx.`
  and `change.` chain resolves on a live object; `reference/api.md`'s `add_world` stub is compared
  parameter by parameter against `World.add_world`.
- `test_the_harness_collects_blocks_of_every_tier` — unchanged floors; the page only adds blocks.
- `test_every_registered_lint_code_is_documented` and its two siblings — unchanged, and the
  guard that the lints page review does not remove a row.
