---
status: complete
---

# Phase 1: the composition model and the seal

## Overview

The declaration and the tree it resolves to, and nothing that runs a call. After this phase a world
can add worlds, the whole-tree properties are validated on first use, and a composite world's flat
tool surface is what `Instance.tools()` serves. No node has a chain, no instance has more than one
store, and no call is dispatched to an added world: chains, contexts, handles and composite
instances are phase 2.

The new module is `composition.py` (architecture §2, §3.2, §4, §5). `world.py` gains the fourth
registration verb, the `fn → Tool` map, the registration epoch and the lazy seal.
`instances.py` gains one line: `Instance.tools()` reads the sealed composition instead of the
world's own registry, which for a leaf world is the same list it is today.

The single-code-path rule of architecture §1 is the shape of the whole phase: a leaf world resolves
to a composition of exactly one node, with path `main`, file `state.sqlite` and its own tools in
registration order, so every existing suite runs unchanged.

## Steps

1. **`src/seahaven/composition.py`** — new. `World` is imported under `TYPE_CHECKING` only: `world.py`
   imports this module, so the dependency runs one way. The reserved tool-name sets live in
   `world.py` and are imported inside the function that needs them, as `instances.py` already does
   for `control`.

   ```python
   type NodeKey = tuple[World, str | None]

   ROOT_PATH = "main"
   NAME = re.compile(r"^[a-z][a-z0-9_]*$")
   RESERVED_NODE_NAMES = frozenset({"main", "temp"})   # SQLite's own schema names
   TOOL_NAME = re.compile(r"^[A-Za-z0-9_-]{1,128}$")   # what OpenEnv's tool listing accepts

   @dataclass(frozen=True)
   class AddedWorld:
       world: World
       name: str
       store: str | None
       tool_prefix: str | None
       tool_allow_list: tuple[str, ...] | None
       tool_block_list: tuple[str, ...] | None
       startup: Mapping[str, Any]

   @dataclass(frozen=True, eq=False)
   class Node:
       key: NodeKey
       path: str
       world: World
       scope: str | None
       depth: int
       added: Mapping[str, Node]          # child name -> node, a live view filled after construction
       aliases: tuple[str, ...]
       file_name: str
       schema_name: str
       bound_startup: Mapping[str, Any]

   @dataclass(frozen=True, eq=False)
   class Contributed:
       name: str
       tool: Tool
       node: Node

   @dataclass(frozen=True, eq=False)
   class Composition:
       root: Node
       nodes: tuple[Node, ...]                              # BFS order, root first
       by_key: Mapping[NodeKey, Node]
       tools: Mapping[str, Contributed]                     # insertion ordered
       by_fn: Mapping[Callable[..., Any], tuple[Contributed, ...]]
       accepted_startup_kwargs: frozenset[str] | None       # None means "any"
       edges: tuple[tuple[str, str, str], ...]              # parent path, child name, child path
   ```

   `Node.added` is a `MappingProxyType` over a dict filled after every node exists, because the node
   graph is a DAG and a child may be built after its parent. `eq=False` on `Node` keeps equality
   identity-based: the graph is deep and a generated `__eq__` would walk it.

   File and schema names (§6.1, §9): the root is `state.sqlite` / `main`; an added node is
   `state.<path with '/' replaced by '__'>.sqlite` / `<path with '/' replaced by '__'>`.
   `STATE_NAME` comes from `fixtures.py`, where it already lives.

2. **`composition.resolve(root: World) -> Composition`** — architecture §4.1, in five steps, raising
   `WorldBug` for every §4.4 failure in the table's order once the walk is done.

   1. *Reachable nodes.* Work-list over node keys from `(root, None)`; a key expands to
      `[(a.world, a.store if a.store is not None else scope) for a in w.added_worlds]`, so a
      `store=` opens a scope for the whole subtree and `store=None` passes the adder's through.
      Each key expands once.
   2. *Canonical paths.* BFS from the root, children enqueued in `add_world` order; the first
      dequeue fixes the path. Root `main`, a child of the root `<name>`, deeper
      `<parent path>/<name>`.
   3. *Alias edges.* Every edge is `(parent.path, child_name, child.path)`; the route it spells is
      `child_name` under the root and `<parent path>/<child name>` deeper, and an edge whose route
      differs from the child's path is recorded on the child as an alias. Aliases are edges, never
      enumerated routes.
   4. *Bound startup.* Merge the `startup` mappings of every edge reaching a node key-wise; one key
      bound to two values by two edges is collected as a seal error naming both edges.
   5. *Contribution*, memoised per node key, bottom-up, exactly the recursion of §4.1 step 5: own
      non-control tools in registration order, then each `add_world`'s already-filtered,
      already-prefixed child contribution, filtered against the child's *contributed* names and
      prefixed as one list. The owning node travels with each entry.

   Then, in the order of the §4.4 table: an allow or block list naming a tool the child does not
   contribute; a duplicate name in the flat list; a contributed name that is reserved
   (`RESERVED_TOOL_NAMES`, `CONTROL_TOOL_NAMES`); a contributed name that fails `TOOL_NAME`; a
   bound-startup conflict; `len(nodes) - 1 > attached_limit()`. Every message names a path and the
   `add_world` behind it, in the shapes the table gives.

   `accepted_startup_kwargs` is the union of every node's world's `accepted_startup_kwargs`, or
   `None` if any hook anywhere in the tree takes `**kwargs`.

3. **`composition.attached_limit()`** — `functools.cache`d probe of
   `apsw.Connection(":memory:").limit(apsw.SQLITE_LIMIT_ATTACHED)`, so the bound in the message is
   the installed SQLite's own (measured 125 on apsw 3.53.4).

4. **`composition.bump()` / `composition.epoch()`** — a module-level counter and one `threading.Lock`
   guarding it (§4.2). `bump()` is called by every registration verb on every world; `epoch()` is
   what a cached seal is compared against. A global counter over-invalidates by design: a world
   holds no back-reference to the hosts that added it.

5. **`src/seahaven/world.py`.**

   ```python
   def add_world(self, world: World, /, *, name: str | None = None, store: str | None = None,
                 tool_prefix: str | None = None,
                 tool_allow_list: Sequence[str] | None = None,
                 tool_block_list: Sequence[str] | None = None,
                 startup: Mapping[str, Any] | None = None) -> None
   ```

   The five call-site checks of §3.2, each a `WorldBug` from the `add_world` line: the name matches
   `^[a-z][a-z0-9_]*$`, holds no `__` and is not `main` or `temp`; the two lists are not both given;
   the name is not already used on this host; every `startup` key is accepted by the added world's
   *own* hooks (any, if one takes `**kwargs`); and `self` is not in the added world's tree (a walk
   of the reachable `World` objects, which covers a self-add at depth 0). `name` defaults to
   `world.name`; `startup` is copied into a `MappingProxyType` over a plain dict.

   Also: `added_worlds` as a read-only property over a list; `_tools_by_fn: dict[Callable, Tool]`
   filled in `_add`, the one choke point every tool passes through (a factory-built `Tool` included);
   a `composition()` method returning the cached `Composition` when `_sealed_at == epoch()` and
   otherwise resolving under the world's own lock; a `bump()` in `_add`,
   `_register_middleware`, `_register_startup_hook` and `add_world`; and `__copy__` snapshotting
   `_added_worlds` and `_tools_by_fn` and dropping the seal so the copy reseals on first use.

   `World.chain` is untouched in this phase; it becomes `composition().root.agent_chain` in phase 2
   when nodes have chains.

6. **`src/seahaven/instances.py`** — `Instance.tools()` becomes
   `[c.tool.listing() | {"name": c.name} for c in self.world.composition().tools.values()]`: the
   tool's own listing with only the name substituted (§5). For a leaf world this is the list it
   returns today, because `contribute` skips control tools and keeps registration order.

7. **`tests/worlds/payments`, `tests/worlds/shop`, `tests/worlds/emporium`** — the committed
   composite world of §17, three packages in the layout `tidy` uses (a `pyproject.toml`, a
   `world.py`, `schema/`, `tools/` importing its modules), written to lint clean.

   - `payments`: a leaf with two tools, `create_charge` and `list_charges`, and a startup hook
     taking `region`, so the bound-`startup` rules have something real to bind.
   - `shop`: `world.add_world(payments.world, name="payments", tool_allow_list=[])` plus one tool of
     its own, `place_order`.
   - `emporium`: the host. `add_world(payments.world, name="payments", tool_prefix="pay_")`,
     `add_world(payments.world, name="payments_eu", tool_prefix="eu_", store="eu")`,
     `add_world(shop.world, name="shop", tool_prefix="shop_")`, and one tool of its own.
     Four nodes — `main`, `payments`, `payments_eu`, `shop` — with `shop/payments` an alias of
     `payments`.

   `tests/conftest.py` puts the three `src` directories on `sys.path` at import, so a test module
   can import them the way a host world does, and `tests/worlds/README.md` gains a row for each.

## Tests

`tests/test_add_world.py` — the declaration:

- `test_the_name_defaults_to_the_added_worlds_own_name`
- `test_a_name_that_is_not_an_identifier_is_refused` — uppercase, a leading digit, a leading
  underscore, an empty string, a `/`
- `test_a_name_holding_a_double_underscore_is_refused` — it would collide with a path's schema name
- `test_main_and_temp_are_refused` — SQLite's reserved schema names
- `test_both_lists_at_once_is_refused`
- `test_two_added_worlds_may_not_share_a_name`
- `test_a_startup_keyword_the_added_world_does_not_accept_is_refused`
- `test_a_hook_taking_var_kwargs_accepts_any_startup_keyword`
- `test_a_world_cannot_add_itself`
- `test_a_world_cannot_appear_in_its_own_subtree` — the transitive case
- `test_the_startup_mapping_is_copied` — mutating the dict passed in does not change the composition
- `test_added_worlds_are_recorded_in_registration_order`
- `test_the_same_world_under_two_names_and_one_store_is_one_node_with_two_views`
- `test_the_same_world_under_two_stores_is_two_nodes`

`tests/test_composition.py` — resolution, contribution and the seal:

- `test_a_leaf_world_is_one_node` — path `main`, `state.sqlite`, schema `main`, no aliases, no edges
- `test_a_leaf_worlds_tool_list_is_its_own_registry_in_order` — control tools absent
- `test_the_canonical_path_is_the_shallowest_route`
- `test_equal_depth_is_broken_by_registration_order`
- `test_depth_beats_registration_order` — a diamond three levels deep where a naive depth-first walk
  and BFS disagree
- `test_a_diamond_resolves_in_linear_time` — a chain deep enough that route enumeration would not
  finish; node and edge counts assert the edge representation
- `test_aliases_are_recorded_on_the_node_they_reach`
- `test_the_same_object_with_the_default_store_is_one_node`
- `test_a_named_store_is_a_second_node`
- `test_two_hosts_naming_one_store_share_it`
- `test_a_scope_propagates_to_the_whole_subtree` — a grandchild under two scoped parents is two
  nodes (§4.5)
- `test_the_marketplace_case` — a host adding a shop with `store="merchant"` gets a merchant-scoped
  payments node, distinct from its own
- `test_the_illustration_of_the_spec_resolves` — the committed `emporium` tree: four nodes, one
  alias edge, the flat tool list in declared order
- `test_a_child_is_filtered_by_the_allow_list` / `..._by_the_block_list`
- `test_a_hosts_list_is_matched_against_the_childs_contributed_names`
- `test_a_prefix_applies_to_the_whole_contribution` — a grandchild's tool carries two prefixes and
  still owns its own node
- `test_control_tools_are_never_contributed`
- `test_bound_startup_is_merged_key_wise` — a host edge and a dependency's storeless edge to one node
- `test_accepted_startup_kwargs_is_the_union_across_the_tree` and `..._is_none_for_var_kwargs`
- `test_by_fn_carries_every_entry_a_function_reaches` — two entries when a world is a node twice
- One test per §4.4 error, each asserting the message names the path and the fix:
  `test_an_allow_list_naming_an_unknown_tool_is_refused`,
  `test_a_duplicate_contributed_name_is_refused`,
  `test_a_contributed_name_that_is_reserved_is_refused`,
  `test_a_prefix_that_makes_an_invalid_tool_name_is_refused`,
  `test_one_startup_key_bound_to_two_values_is_refused`,
  `test_more_nodes_than_sqlite_can_attach_is_refused` — over `attached_limit()`, with the real number
- `test_the_attach_limit_is_probed` — the value the installed apsw reports
- `test_the_seal_is_cached_until_a_registration` — the same object twice, a new one after a tool is
  registered on a leaf, and that tool visible to the host on next use
- `test_a_copy_of_a_world_reseals` — `copy.copy` keeps the added worlds and drops the cached seal
- `test_instance_tools_serves_the_composite_list` — through a real `Instance`, in declared order
