---
status: complete
---

# Phase 4: fixtures, inspection, changes, the report

## Overview

Phase 2 made an instance be the tree — N files, N connections, N sessions — and then refused
everything that has to describe more than one of them. `world.instance(<fixture>)` and
`Instance.freeze` raise on a composite; `changes()` renders the root's session alone; `inspect()`
opens the root's file alone. This phase is the other half: a fixture that carries every node, a
read-only handle that sees every node, a change list that says which node each row belongs to, and
a report saying what the instance is.

Four pieces, and the refusal in `instances._refuse_a_composite_fixture` goes away with them:

- **Fixtures (§11).** `NodeMeta`, `FixtureMeta` at `format_version` 1 or 2, per-node freeze, per-node
  hash verification, and `check_composition` at create.
- **Inspection (§9).** `open_inspection(..., attachments=)`, attaching every added node read-only
  *before* the write-denying authorizer is installed; both of the instance's read-only handles
  opened that way, which is what puts `controller_run_sql` over the whole tree.
- **Changes (§10).** `Change.world`, and `Instance.changes()` concatenating each node's changeset in
  canonical BFS order.
- **The report (§12).** `NodeReport` and `Instance.composition()`.

`SeahavenState.composition` and every lint code stay in phase 5; the docs stay in phase 6.

## Steps

1. **`db.py` — attach before the authorizer.**

   ```python
   def open_inspection(path: Path, clock: Clock, attachments: Sequence[tuple[str, Path]] = ()) -> Db
   ```

   Open the root read-only as today, harden, register the clock functions, then
   `conn.execute("ATTACH DATABASE ? AS ?", (read_only_uri(file), schema))` for each attachment, and
   only then install `_deny_writes`. The schema name is bound as a parameter, never interpolated.
   Factor the `file:...?mode=ro` spelling out of the existing call into one helper so the root and
   every attachment are opened the same way. Document the order as load-bearing: `_deny_writes`
   already denies `SQLITE_ATTACH`, so nothing can attach afterwards and nothing can write to what is
   attached.

2. **`changes.py` — a change knows its node.** `Change` gains `world: str` as its first field, the
   owning node's path (`main` for the root), and `to_dict()` carries it. `render` gains a required
   `world: str` parameter and stamps it on every record it builds. No default: every call site knows
   which node's changeset it is rendering, and a default would be a silently wrong answer for the
   one that forgot.

3. **`fixtures.py` — the version-2 sidecar.**

   ```python
   class NodeMeta(pydantic.BaseModel, frozen=True, extra="forbid"):
       path: str
       world: str
       world_version: str
       schema_hash: str
       scope: str | None
       file: str
       file_sha256: str
       aliases: tuple[str, ...] = ()

   class FixtureMeta(pydantic.BaseModel, frozen=True, extra="forbid"):
       format_version: Literal[1, 2]
       ...                                   # the version-1 fields, unchanged, describing the root
       nodes: tuple[NodeMeta, ...] = ()
   ```

   A model validator ties the two: `format_version == 2` iff `nodes` is non-empty, with a message
   naming `format_version`. `load` accepts 1 and 2 and refuses anything else, naming the file.

4. **`fixtures.py` — freeze every node.** `freeze` walks the instance's node runtimes in BFS order:
   `conformance.check` on every one of them first, all of them, before anything is written, with the
   path in the message; then `VACUUM INTO` each node's `file_name` under `.pending-<id>/`; then hash
   each, build the sidecar, `chmod 0o444` every state file, and rename. Version 1 for a one-node
   composition and version 2 otherwise, and a version-1 sidecar is dumped without a `nodes` key at
   all, so a leaf world's fixture directory is byte-identical to what it is today. Dump with
   `model_dump(mode="json")` so the nested nodes are plain YAML.

5. **`fixtures.py` — verify every node.** `verify` checks the root's file as it does today and then
   each `NodeMeta`'s, through the same per-process `(path, mtime, size, hash)` cache, naming the
   path in the refusal.

6. **`fixtures.py` — `check_composition(meta, comp)`.** Run at create, after the root's hash check
   and before any copy:

   - a version-1 sidecar against a multi-node composition, or the reverse, naming both shapes;
   - the set of `(path, scope)` pairs equals the composition's added nodes, with a difference
     reported as **node added**, **node removed** or **sharing changed**, naming the paths;
   - the alias edge sets are equal;
   - each node's `schema_hash` equals its world's, refused with the framework's "this fixture was
     frozen from a different schema; regenerate it", prefixed by the path;
   - a `world_version` difference under a matching `schema_hash` is returned rather than raised, for
     the caller to log at INFO. Never a refusal.

7. **`instances.py` — create from a composite fixture.** Delete `_refuse_a_composite_fixture` and
   both its call sites. `InstanceManager._fixture` calls `check_composition` after `verify` and the
   root's schema-hash check, and logs each reported version difference at INFO. `create` copies the
   root's file and then each added node's file, from the name the sidecar records to the name the
   composition derives.

8. **`instances.py` — both read-only handles over the tree.** `_attachments()` builds
   `[(node.schema_name, self.dir / node.file_name)]` for every node but the root, from the
   instance's own runtime — the files that exist — and `inspect()` and `_control_db()` both pass it.
   Rewrite the two docstrings: `inspect()` no longer says the root's store alone, and
   `controller_run_sql` sees every node schema-qualified in one statement.

9. **`instances.py` — changes over every node.** `Instance.changes()` renders each node's changeset
   with that node's connection and its path, concatenated in the composition's BFS order.

10. **`instances.py` — freeze over every node.** `Instance.freeze` refuses when *any* node is in a
    transaction, and hands `freeze` the whole instance as it does today.

11. **`composition.py` and `instances.py` — the report.** `NodeReport` (`path`, `world`,
    `world_version`, `scope`, `aliases`, `schema_hash`) in `composition.py`, and
    `Instance.composition() -> tuple[NodeReport, ...]` built from the instance's pinned nodes in BFS
    order.

12. **`control.py` — docstrings only.** `controller_run_sql` and `controller_changes` gain nothing;
    say in their docstrings that the connection under them now carries every node and that the
    changeset covers every node.

13. **Existing tests that change because behaviour did.** `test_fixtures.py`'s `load` case for
    `format_version: 2` becomes an unknown version; `test_lint_fixtures.py`'s damaged version does
    the same; `test_changes.py`'s constructed `Change`s carry `world="main"`.

## Tests

`tests/test_composite_fixtures.py`:

- freeze writes one state file per node plus one sidecar, and the sidecar is version 2
- a single-node world still writes version 1, with no `nodes` key on disk
- a version-1 fixture written before this phase still loads and still creates an instance
- every node's `NodeMeta` carries its path, world, version, scope, file, hash and aliases
- a conformance failure on a *non-root* node mints nothing and names that node's path
- create verifies every node's file hash, and a modified added-node file is refused by path
- node added, node removed, sharing changed, alias changed: each refused with its own message
- a node's schema hash changing is refused with the framework's regenerate message, path-prefixed
- a version-1 sidecar against a composite, and a version-2 sidecar against a leaf, each refused
- a `world_version` difference with a matching schema hash creates the instance and is reported
- fork: create from a composite fixture, write into two nodes, freeze again; `parent_id` chains and
  every node's rows survive the round trip
- an added world's own fixtures are unreachable from a host (`world.fixtures()` is the host's)
- `Instance.composition()` reports every node in BFS order with paths, scopes and aliases

`tests/test_composite_inspection.py`:

- every added node is attached under its `__` schema name, and the root is `main`
- a cross-node join in one statement through `inspect()`
- a write through `inspect()` is denied, and so is an `ATTACH` through it
- `controller_run_sql` reads an added node schema-qualified, and cannot write to one
- a world's own `run_sql` on `ctx.db` cannot see a sibling node's tables
- the inspection handle is opened once and kept, and is not the control handle
- a leaf world's inspection handle has exactly one database attached
- the attach bound: a tree over `attached_limit()` is refused at the seal with the real number

`tests/test_composite_changes.py`:

- `Change.world` is the owning node's path, `main` for the root
- one list covering every node, in canonical BFS order
- `to_dict()` carries `world`
- per-node `untracked_tables` and FTS5 shadow exclusions are each world's own
- a nested call that failed leaves no trace in either store
- `controller_changes` covers every node
