---
status: complete
---

# Phase 2: contexts, handles, composite instances and dispatch

## Overview

Phase 1 resolved the tree and sealed it; nothing ran against it. This phase makes the tree the
thing an instance *is*: N files, N connections, N sessions, one context per node, and one call path
that dispatches to whichever node owns the tool. Host code reaches an added world through
`ctx.worlds.<name>`, whose handle is valid for exactly one activation of the instance.

The single-code-path rule of architecture §1 is again the shape of the phase. A leaf world is a
composition of one node, so it gets one file called `state.sqlite`, one runtime, one chain whose
every layer pairs with the root, and a `ctx.worlds` with no children — which is byte-for-byte what
it has today. There is no `if composite:` anywhere.

Out of scope, and deliberately: fixtures over a composite (phase 4), `Instance.inspect()` over
every node (phase 4), `Change.world` (phase 4), typed `call(fn)` and `Tool[**P, R]` (phase 3),
`invoke` returning the tool's own object (phase 3). Creating an instance from a fixture, and
freezing one, are refused with a `WorldBug` on a world that resolves to more than one node, because
a version-1 sidecar describes the root's file alone and silently building the rest blank is worse
than saying so.

New module: `handles.py` (architecture §2, §7.2, §7.3, §7.6). Modified: `ctx.py`, `call.py`,
`composition.py`, `instances.py`, `world.py`, `__init__.py`.

## Steps

1. **`src/seahaven/handles.py`** — new. `Frame`, `Worlds`, `WorldHandle`, and the two module
   functions the chain reaches them through. Imports `composition` and `instances` under
   `TYPE_CHECKING` only, so `composition.py` can import this module at run time.

   ```python
   @dataclass(frozen=True, eq=False)
   class Frame:
       """One activation of an instance: everything reached during it, and nothing after."""
       instance: Instance
       epoch: int

       def ctx(self, key: NodeKey, call: Call | None) -> Ctx:
           runtime = self.instance._runtime[key]
           return runtime.ctx.with_call(call, worlds=Worlds(self, key))


   class Worlds:
       """What `ctx.worlds` is, and the base a world subclasses to declare its children."""
       def __init__(self, frame: Frame | None = None, key: NodeKey | None = None) -> None
       @classmethod
       def unbound(cls) -> Worlds          # a shared instance; every access raises
       def __getattr__(self, name: str) -> WorldHandle    # AttributeError for a '_' name
       def __getitem__(self, name: str) -> WorldHandle


   class WorldHandle:
       """One node, for one activation: `call`, `db`, `state`, `worlds`."""
       def call(self, tool: str, /, **arguments: Any) -> Any
       @property db(self) -> Db
       @property state(self) -> dict[str, Any]
       @property worlds(self) -> Worlds


   def frame_of(worlds: Worlds) -> Frame       # the liveness check, and the one door to the frame
   def ctx_for(ctx: Ctx, key: NodeKey, call: Call | None) -> Ctx
   ```

   - Both runtime attributes are `_`-prefixed and every public member of `Worlds` is a dunder,
     because a child may be named anything `^[a-z][a-z0-9_]*$` matches: a public `frame` property
     would shadow a child called `frame`. `__getattr__` refuses a `_` name with `AttributeError`
     rather than reaching for a child, so `copy`, `pickle` and pytest introspection behave.
   - Liveness is `frame.epoch == instance._epoch`, checked by `frame_of` and by every handle
     member, raising `WorldBug("a world handle was used after the call it belongs to returned")`.
     An unbound `Worlds` raises its own message naming `ctx.worlds`.
   - A child name that is not registered raises `WorldBug` naming the node's path and what it does
     add. Children are read from the **live** composition (`instance._current_composition()`), not
     from the creation-time tree, so a middleware or a tool registered after the instance exists
     reaches a nested call exactly as it reaches an agent-initiated one. `db` and `state` are read
     from the instance's runtime, which is pinned.
   - `ctx_for` is what every chain layer runs through: the ctx of the node the layer was paired
     with, keeping the caller's own ctx when the layer belongs to the node the ctx already does
     (which is every layer of a leaf world's chain, so nothing is allocated there and a middleware
     that passed a modified ctx down still has it honoured).
   - `WorldHandle.call(name)` resolves in the node's **own** registry — unprefixed, unfiltered,
     control tools excluded — takes `frame.instance._held()` (the `RLock` again, no gate), runs
     `node.internal_chain`, and logs its own line marked internal.

2. **`src/seahaven/ctx.py`** — `Ctx` becomes generic in its `worlds`.

   ```python
   @dataclass(frozen=True)
   class Ctx[W: Worlds = Worlds]:
       db: Db; clock: Clock; ids: Ids
       state: dict[str, Any]
       instance: InstanceInfo
       worlds: W
       call: Call | None = None

       def with_call(self, call: Call | None, *, worlds: Worlds | None = None) -> Ctx[Any]: ...
   ```

   `worlds` has no default: every context is built by the framework, and one built without a
   `worlds` would be a context whose `ctx.worlds` raised `AttributeError` rather than the framework's
   own refusal. `call` widens to `Call | None` because `Frame.ctx(key, None)` is how a startup hook
   and a `bulk()` block get theirs. `Worlds` is imported under `TYPE_CHECKING`.

3. **`src/seahaven/call.py`** — `Call` gains `node: str = ROOT_PATH`, so a middleware anywhere on
   the route knows which node owns the call without touching a foreign store. The default keeps
   every existing construction, and a leaf world's calls say `main`.

4. **`src/seahaven/composition.py`** — chains.

   ```python
   def build_route_chain(layers: Sequence[tuple[NodeKey, Middleware]], owner_key: NodeKey) -> Handler
   ```

   `build_chain` with each layer paired with a node key: the layer runs with `ctx_for(ctx, key,
   call)`, and the innermost handler is `invoke` paired with the owner. `Node` gains `agent_chain`
   and `internal_chain`, built in `_nodes`: the agent chain is every node's middlewares along the
   canonical route root→owner, outermost first, each paired with that node; the internal chain is
   the owner's own middlewares only. `_walk` also returns each node's canonical parent, which is
   what the route is read off.

5. **`src/seahaven/instances.py`** — the per-node runtime and the call path.

   ```python
   @dataclass
   class NodeRuntime:
       node: Node
       db: Db
       ids: Ids
       state: dict[str, Any]
       ctx: Ctx                      # the node's template: no call, unbound worlds
       session: apsw.Session | None

   def node_seed(base: bytes, path: str) -> bytes    # base for the root; sha256(base||0||path) below
   ```

   - `Instance.__init__` takes `runtime: Mapping[NodeKey, NodeRuntime]` (BFS order, root first) and
     `node_keys: frozenset[NodeKey]` in place of `ctx`, `db` and `session`. `db`, `ctx` and
     `state_path` become properties over the root's runtime, because `freeze`, `control.py` and the
     pytest plugin read them and a leaf world's instance must be what it is today.
   - `_held()` yields a `Frame`: depth 0→1 bumps `_epoch` and opens one, every nested `_held()` on
     the thread yields that same frame, and the return to depth 0 bumps `_epoch` again so a handle
     dies with its activation rather than at the start of the next one.
   - `_current_composition()` — `world.composition()` plus the pinned-node-set check, memoised on
     the composition's identity.
   - `Instance.call` resolves in `comp.tools`, falling back to the root's registry for a control
     tool (which is never contributed), then `gate(bypass=control)`, `_held()`, `in_call()`, and
     `node.agent_chain(ctx, ctx.call)`.
   - `in_call()`: a `threading.local` flag, set around agent-initiated dispatch, read by
     `World.instance`. `bulk()` does not set it.
   - `bulk()` opens one transaction per node before yielding `frame.ctx(root.key, None)`, and
     commits them in sequence on exit.
   - `InstanceManager.create` per architecture §6.2: seal first, unknown-`reset`-argument check
     against `comp.accepted_startup_kwargs`, a blank file per node named by its path, one `Ids` per
     node from `node_seed`, the `Instance`, then — inside one `_held()` — the tree's startup hooks
     and then a session per node.
   - `_run_startup_hooks`: one transaction per node opened before the first hook runs (an
     `ExitStack`), depth-first preorder over the **canonical** tree with each node's hooks run once,
     each hook handed `frame.ctx(node.key, None)` and `{**reset kwargs it accepts, **bound kwargs it
     accepts}` — bound last, so bound wins.
   - The log line gains `node=<path>` and, for a nested call, `internal=true`.

6. **`src/seahaven/world.py`** — `chain` becomes a property reading `composition().root.agent_chain`
   (so `_register_middleware` no longer rebuilds anything), and `instance()` refuses to run inside a
   call.

7. **`src/seahaven/__init__.py`** — export `Worlds` and `WorldHandle` (architecture §1).

8. **`tests/worlds/`** — the committed tree grows what dispatch needs and nothing else: a middleware
   on each of `payments`, `shop` and `emporium` that appends its own world's name to `ctx.state`, a
   `payments` tool that raises the world's own `ToolError`, and an `emporium` tool that settles an
   order through `ctx.worlds.shop` and `ctx.worlds.payments`.

## Tests

`tests/test_composite_instance.py`:

- `test_one_blank_file_per_node_named_by_its_path` — four files, the root's still `state.sqlite`.
- `test_a_leaf_worlds_directory_is_what_it_is_today` — one file, nothing else.
- `test_every_node_is_built_at_one_clock` — `now=` reaches every node's `ctx.clock`.
- `test_each_node_has_its_own_schema` — a table of one world is absent from another's file.
- `test_the_roots_id_stream_is_unchanged_by_adding_a_node` — same seed, same first uuid.
- `test_a_nodes_id_stream_does_not_move_when_a_sibling_is_added` — the salt is the path.
- `test_the_same_seed_reproduces_every_nodes_stream`.
- `test_startup_hooks_run_root_first_then_each_added_world_in_order`.
- `test_a_shared_nodes_hooks_run_once` — the alias route does not run them twice.
- `test_bound_startup_reaches_the_hook_of_its_own_node_only`.
- `test_bound_startup_is_merged_across_two_edges_that_reach_one_node`.
- `test_a_bound_keyword_is_not_overridable_by_a_reset_keyword_of_the_same_name` — and that
  keyword still reaches every other hook that names it.
- `test_an_unknown_reset_argument_is_refused_before_any_file_is_created`.
- `test_a_hook_that_raises_leaves_no_directory` — and rolls back every node's transaction.
- `test_a_root_hook_seeds_a_child_through_its_state_before_the_childs_hooks_run`.
- `test_a_root_hook_writes_into_a_childs_store_before_that_child_is_open` — the transactions are
  all open before the first hook.
- `test_an_add_world_after_an_instance_exists_makes_its_next_call_raise`.
- `test_a_tool_registered_after_an_instance_exists_is_served` — the epoch does not pin tools.
- `test_a_fixture_of_a_composite_world_is_refused_for_now` / `test_freezing_a_composite_is_too`.
- `test_bulk_opens_a_transaction_on_every_node` — a write through `ctx.worlds.<name>.db` inside the
  block is committed with the root's, and rolled back with it when the block raises.

`tests/test_composite_dispatch.py`:

- `test_a_contributed_tool_writes_to_its_own_nodes_store`.
- `test_two_accounts_of_one_world_do_not_share_rows`.
- `test_the_agent_chain_is_the_whole_canonical_route` — two-level and three-level trees, asserting
  the order of every layer.
- `test_each_layer_runs_with_its_own_worlds_ctx` — a host gate reads the principal its own hook put
  in `ctx.state`, an intermediate world's trace writes to its own table, and `invoke` runs on the
  owner's connection.
- `test_call_is_the_same_object_in_every_layer_and_names_the_owning_node`.
- `test_a_middleware_that_rewrites_arguments_is_seen_by_the_owners_layer`.
- `test_a_nested_call_runs_only_the_owning_worlds_chain`.
- `test_a_nested_call_raises_the_added_worlds_own_error`.
- `test_a_host_tool_that_fails_after_a_nested_call_leaves_that_write_in_place`.
- `test_a_nested_call_does_not_take_the_gate` — with `set_concurrency(1)`, on another thread, which
  deadlocks if it does.
- `test_a_nested_call_re_enters_the_lock` — a second thread's call waits for the whole activation.
- `test_host_code_can_call_a_tool_the_agent_cannot_see` — an empty allow list hides nothing from
  the host.
- `test_host_code_writes_directly_to_an_added_worlds_store`.
- `test_a_grandchild_is_reached_through_the_childs_own_worlds`.
- `test_an_unknown_child_name_is_a_world_bug` — attribute and item access.
- `test_a_handle_kept_past_its_call_raises`.
- `test_a_handle_made_in_a_bulk_block_survives_a_call_inside_that_block`.
- `test_a_template_contexts_worlds_raises`.
- `test_creating_an_instance_from_inside_a_call_is_refused` — and allowed again afterwards.
- `test_the_log_line_carries_the_node_and_marks_a_nested_call_internal`.
- `test_a_leaf_worlds_chain_is_what_it_was` — every layer gets the root's ctx and `call.node` is
  `main`.
