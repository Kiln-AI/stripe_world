---
status: complete
---

# Phase 5: OpenEnv and `seahaven check`

## Overview

Everything composition can go wrong about is now findable at run time and nowhere else. A host that
prefixes an added world and leaves its descriptions cross-referencing sibling tools by their
unprefixed names ships an agent surface naming tools the agent cannot call; a host that adds one
world twice through a shared node quietly publishes two names for one thing; a tool module that
makes an instance, or misspells a child name, fails inside an eval; a tree that does not seal is a
traceback out of the first `world.instance(...)`; a composite fixture's added nodes are not checked
at all, because `lint/fixtures.py` validates a version-2 sidecar and then reads only the root's
fields out of it. This phase is `seahaven check` catching every one of them before a commit.

And the eval-facing half of §12: `SeahavenState.composition`, so a session over OpenEnv can say
what tree it is running against. (`openenv/env.py` serialising the observation's result landed in
phase 3, with the `invoke` change that made it necessary.)

Eleven codes:

- **SH504 and the lazy seal (§4.3).** `check` seals as its first act, so every §4.4 failure is a
  finding with the `add_world` it names on it rather than a traceback. A world that does not seal
  still gets every rule that needs no tree.
- **SH502, SH503 (§8.3).** The `Worlds` subclass a host declares, bound to its registrations.
- **SH206, SH207 (§3.3, §2.3).** The two warnings about a tool surface that is accurate about
  itself.
- **SH208, SH209 (§4).** The two AST rules over a host's own code.
- **SH401–SH405 per node and SH406 (§11).** The gap phase 4 left: every node of a version-2 sidecar
  checked the way the root always has been, and the sidecar's shape checked against the tree.

## Steps

1. **`src/seahaven/openenv/env.py`.** `SeahavenState` gains
   `composition: list[dict[str, Any]] | None`, described like its three neighbours, and `state`
   populates it with `[asdict(report) for report in instance.composition()]`, `None` before the
   first `reset`. Nothing agent-facing changes: `state` is not an observation.

2. **`src/seahaven/lint/__init__.py`.** `Target` gains

   ```python
   @property
   def composition(self) -> Composition | None:
       """The world's sealed tree, or `None` when it does not seal (SH504 says why)."""
   ```

   so a rule that needs the tree asks for it and gets `None` rather than a `WorldBug`; `World.composition()`
   caches per epoch, so several rules asking costs an integer compare each. `run_all` runs
   `lint.world` first and concatenates its findings with the rest. The docstring table gains the
   eight new codes.

3. **`src/seahaven/lint/world.py`, new.** SH504, SH502, SH503 and SH207.

   ```python
   def run(target: Target) -> list[Finding]        # the four, in that order
   def _seal_findings(target) -> list[Finding]     # SH504: world.composition() in a try/except WorldBug
   def _worlds_class_findings(target) -> list[Finding]   # SH502 + SH503
   def _shared_contribution_findings(target) -> list[Finding]  # SH207
   ```

   - **SH504** (error): `target.world.composition()` raising `WorldBug` is the finding, carrying the
     exception's own message. Path: `<package_dir>/world.py` when it exists, else the package
     directory — `add_world` is written in `world.py` by the convention every scaffolded world
     follows.
   - **SH502** (error) / **SH503** (warning): every `Worlds` subclass declared in a module of the
     world's package, found through `target.imported` (a class used as an annotation is in a module
     the annotating module imported). `annotationlib.get_annotations(cls, format=Format.STRING)`
     reads the names without evaluating them. An annotated name that is not in
     `{added.name for added in world.added_worlds}` is SH502 on that annotation's line; a registered
     name no declared class annotates is SH503. SH503 only when a subclass is declared, which is
     what makes the declaration optional.
   - **SH207** (warning): group `composition.tools` by `(entry.node, entry.tool)`; two or more
     contributed names for one pair is one warning naming the underlying tool, the node and every
     name it is published under.

4. **`src/seahaven/lint/code.py`.** SH208, SH209 and SH206.

   - **SH208** (error): an `ast.Call` on an attribute named `instance`, in a module under `tools/`
     or `middleware/` (`coverage.REGISTERING_DIRECTORIES`, reused). `_in_middleware` generalises to
     `_inside(path, package_dir, directory)`.
   - **SH209** (error): an `ast.Attribute` or a string-literal `ast.Subscript` whose value resolves
     to exactly `ctx.worlds`, naming something that is not a registered child. Exactly `ctx.worlds`
     and not any `.worlds`, because `ctx.worlds.<child>.worlds.<grandchild>` is the sanctioned way
     to a grandchild and its names are not the root's. Names beginning `_` are skipped:
     `Worlds.__getattr__` is consulted only for what ordinary lookup does not find.
   - **SH206** (warning): over `target.composition`. For each pair of entries owned by one node
     where both are published under a name that is not the tool's own, a word-boundary occurrence
     of one tool's registered name in the other's description is a finding on the described tool's
     own source (`_source_of`, as SH205 uses).

5. **`src/seahaven/fixtures.py`.** `composition_mismatch(meta, composition) -> str | None` — the
   shape, node-set and alias comparisons `check_composition` already makes, returning the refusal's
   text instead of raising it. `check_composition` raises what it returns and is otherwise
   unchanged, so the lint's SH406 message is the refusal the author would have met at create.

6. **`src/seahaven/lint/fixtures.py`.** Per node, and SH406.

   - `_state_findings` takes a subject (`fixture 'x'` or `fixture 'x' node 'shop'`) and a path, so
     SH402 and SH405 run over every node's file with the path in the message.
   - SH403 per node against `target.composition`, which is where a node's world's schema hash is;
     skipped for a node the composition does not have, which is SH406's to report.
   - SH401 gains the two per-node sidecar-validity rules pydantic cannot state: a `path` listed
     twice, and two nodes (or a node and the root) naming one `file`.
   - SH406 (error): `fixtures.composition_mismatch` against `target.composition`, one finding
     carrying its message. Not run when the world does not seal.
   - SH404 stays whole-sidecar: there is one clock per instance and `NodeMeta` has no `now`.

7. **`src/seahaven/docs/reference/lints.md`.** The eight new codes: a table row each and a
   `## SHnnn` section each, and SH401's "carries `format_version: 1`" becomes 1 or 2. Docs are
   phase 6, but `tests/test_docs.py` fails the moment a code exists that this page does not carry,
   so the page moves with the code.

8. **`tests/worlds/`.** Three new packages and one addition, each a real package with a
   `pyproject.toml`, as `README.md` describes the others:

   - `ledger` — a leaf whose two tools' descriptions name each other, which is what a prefix makes
     stale.
   - `bazaar` — the wrong host: adds `ledger` twice under two names (one node, two surfaces), with a
     prefix on both; a tool module that calls `.instance(` and subscripts `ctx.worlds["nope"]`; a
     `Worlds` subclass annotating a name that is not registered and leaving a registered one out.
     SH206, SH207, SH208, SH209, SH502 and SH503, and nothing else.
   - `unsealed` — adds `ledger` with a `tool_allow_list` naming a tool it does not contribute: the
     §4.4 failure SH504 reports.
   - `shop` gains `worlds.py`, a correct `ShopWorlds`, which is the SH502/SH503 negative case and
     the §8.3 declaration written where a world really writes it.

   `tests/conftest.py`'s path list and `tests/worlds/README.md`'s table gain the three.

## Tests

- **`tests/test_lint_world.py`**, new:
  - `test_a_tree_that_does_not_seal_is_sh504` — `unsealed`, with the seal's own message and the
    `add_world` in it.
  - `test_a_world_that_does_not_seal_still_gets_the_rules_that_need_no_tree` — `run_all` over
    `unsealed` returns SH504 and does not raise.
  - `test_the_committed_tree_seals_clean` — `emporium` has no SH504, SH502, SH503 or SH207.
  - `test_an_annotated_name_that_is_not_a_child_is_sh502` — `bazaar`, on the annotation's line.
  - `test_a_child_no_declared_class_annotates_is_sh503` — `bazaar`, severity warning.
  - `test_a_world_that_declares_no_worlds_class_is_neither` — `emporium`.
  - `test_a_complete_declaration_is_neither` — `shop`.
  - `test_one_tool_contributed_twice_through_a_shared_node_is_sh207` — `bazaar`, naming both
    published names.
  - `test_two_nodes_of_one_world_are_not_sh207` — `emporium`'s two payments accounts are two nodes,
    so its `pay_create_charge` and `eu_create_charge` are two stores and not one tool twice.
- **`tests/test_lint_code.py`**:
  - `test_instance_in_a_tools_module_is_sh208`, `..._in_a_middleware_module_is_sh208`,
    `test_instance_outside_those_directories_is_not_sh208` — over `tmp_path` sources, as the other
    AST rules are.
  - `test_an_unregistered_child_name_is_sh209` for the attribute and the subscript spelling;
    `test_a_registered_child_name_is_not_sh209`; `test_a_grandchild_reached_through_a_handle_is_not_sh209`;
    `test_a_computed_subscript_is_not_sh209`.
  - `test_a_prefixed_worlds_stale_description_is_sh206` over `bazaar`, naming the unprefixed
    mention and the name the agent sees; `test_an_unprefixed_worlds_description_is_not_sh206` over
    `shop`, which adds `ledger`-style descriptions with no prefix.
- **`tests/test_lint_fixtures.py`**: a composite world frozen and then damaged, one damage per rule.
  - `test_a_freshly_frozen_composite_fixture_is_clean`
  - `test_a_modified_node_file_is_sh402_naming_the_node`
  - `test_a_missing_node_file_is_sh402_naming_the_node`
  - `test_a_journal_companion_beside_a_node_file_is_sh405_naming_the_node`
  - `test_a_node_schema_hash_from_another_world_is_sh403_naming_the_node`
  - `test_two_nodes_at_one_path_is_sh401`, `test_two_nodes_sharing_a_file_is_sh401`
  - `test_a_node_the_world_does_not_have_is_sh406`, `test_a_version_1_sidecar_for_a_composite_is_sh406`,
    `test_an_alias_that_moved_is_sh406`
  - `test_a_world_that_does_not_seal_reports_no_sh406`
- **`tests/test_env.py`**:
  - `test_state_before_reset_carries_no_composition`
  - `test_state_after_reset_carries_every_node` — over a composite world: paths, worlds, scopes and
    aliases, and every value JSON-serialisable.
  - The declared-description table gains `composition`.
  - A composite section: the flat tool list in declared order, control tools absent from it, a call
    to a contributed tool landing in the owning node's store, `state` carrying four nodes, and a
    second `reset` destroying every node's directory.
- **`tests/test_cli_check.py`**: `test_a_world_that_does_not_seal_is_a_line_not_a_traceback` —
  `seahaven check` in `unsealed` exits 1 with an SH504 line of the documented shape.
- **`tests/test_docs.py`** needs no change: its two lint-reference tests now cover eight more codes
  by construction.
