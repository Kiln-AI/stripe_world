---
status: complete
---

# Architecture: World Composition

The technical design for composable worlds. It follows `functional_spec.md` section by section and
is deep enough that whoever implements it designs nothing significant. It is written against the
framework as specified in `../seahaven_framework/` and **verified against the framework as
implemented** on `main` (`src/seahaven/`, all thirteen phases of its plan complete). Where the
code diverged from its spec during implementation, the code governs and is what this document
cites. None of the framework's decisions is reopened.

Four decisions were taken before drafting. Three answer questions the functional spec leaves to
"the repo, with the code" (§12); the second reopened §2.3, which is now revised.

- **Whole-tree validation is a lazy seal** (§4), not an error at the `add_world` call. Section 4.3
  explains why the spec's literal reading is not implementable.
- **Scopes are inherited** (§4.1). The functional spec's node identity was revised on 2026-09-14
  from `(World object, store key)` to `(World object, scope)`, where a `store=` opens a scope for a
  world *and its whole subtree*. The architecture drove that change: under the per-edge rule two
  Stripe accounts shared one Tax store beneath them, and the marketplace case §2.3 cites as its own
  justification was not expressible. See §4.5.
- **The node cap is SQLite's attach limit and nothing lower** (§9). Measured: apsw 3.53.4 bundles
  SQLite 3.53.4 with `SQLITE_LIMIT_ATTACHED` at 125 for both the runtime default and the
  compile-time maximum, so the bound is 126 nodes with no custom build.
- **One document**, no `components/`. Composition is one mechanism cutting across ten existing
  modules rather than a set of subsystems with independent internals.

## 1. Shape of the change

Composition is **not a second code path**. A leaf world is a composition of exactly one node, and
every mechanism below runs for it unchanged: one node, one store file named `state.sqlite`, one
chain, a `format_version: 1` sidecar. There is no `if self.added_worlds:` anywhere in the runtime.
This is the single most important structural decision in this document, because the alternative —
a fast path for simple worlds and a composite path beside it — doubles the surface every later
framework change has to be correct against.

New modules:

```
seahaven/
  composition.py       # AddedWorld, Node, Composition; resolution, the seal, all whole-tree validation
  handles.py           # Frame, Worlds, WorldHandle: the activation-scoped view host code reaches added worlds through
```

Modified: `world.py`, `ctx.py`, `tool.py`, `call.py`, `instances.py`, `fixtures.py`, `db.py`,
`changes.py`, `control.py`, `openenv/env.py`, `lint/code.py`, `lint/fixtures.py`, `lint/world.py`
(new lint module for the `Worlds`-class rules). Section 16 is the complete delta table.

`seahaven/__init__.py` gains exactly: `Worlds`, `WorldHandle`. `Ctx` becomes generic and is still
exported under the same name.

## 2. Key types

| Type | Module | What it is |
|---|---|---|
| `AddedWorld` | `composition.py` | Frozen record of one `add_world` call: `world`, `name`, `store`, `tool_prefix`, `tool_allow_list`, `tool_block_list`, `startup` (a frozen mapping). Held on the host in registration order as `World.added_worlds: tuple[AddedWorld, ...]`. Code, never state. |
| `NodeKey` | `composition.py` | `tuple[World, str \| None]` — the world **object** and the **scope** it resolved into (`None` is the unnamed root scope). Hashed by the `World`'s identity, which is what makes §2.3's "the same pair anywhere in one root's tree is one store" ordinary Python. |
| `Node` | `composition.py` | One store in a composite. `key`, `path`, `world`, `scope`, `depth`, `added: Mapping[str, Node]` (child name → node), `aliases: tuple[str, ...]`, `file_name`, `schema_name`, `bound_startup: Mapping[str, Any]`, `agent_chain: Handler`, `internal_chain: Handler`. Immutable; rebuilt by each seal. |
| `Contributed` | `composition.py` | One entry of the composite tool list: `name` (as the agent sees it), `tool`, `node`. |
| `Composition` | `composition.py` | The sealed tree: `root: Node`, `nodes: tuple[Node, ...]` (BFS order, root first), `by_key`, `tools: Mapping[str, Contributed]` (insertion ordered), `by_fn: Mapping[Callable, tuple[Contributed, ...]]`, `accepted_startup_kwargs: frozenset[str] \| None` (`None` means "any"), `edges: tuple[tuple[str, str, str], ...]` (parent path, child name, child path). |
| `Frame` | `handles.py` | One activation of an instance: `instance`, `epoch` (§7.3). `frame.ctx(key, call)` is the per-node, per-call `Ctx` every layer of a chain runs with (§7.6). Internal. |
| `Worlds` | `handles.py` | What `ctx.worlds` is: a `Frame` plus the node it belongs to. Attribute and item access to that node's children. Also the public, empty base class a world subclasses to declare child names for the type checker (§8.3). |
| `WorldHandle` | `handles.py` | `call`, `db`, `state`, `worlds` for one node, valid for one activation (§7.3). |
| `NodeRuntime` | `instances.py` | The per-instance, per-node state a `Node` describes: `db`, `ids`, `state`, `session`, and `ctx`, the node's template context with no call and an unbound `worlds`. The instance holds `dict[NodeKey, NodeRuntime]` and the frozen set of node keys it was created with (§6.2). |

## 3. Declaration

### 3.1 `World.add_world`

```python
def add_world(self, world: World, /, *, name: str | None = None, store: str | None = None,
              tool_prefix: str | None = None,
              tool_allow_list: Sequence[str] | None = None,
              tool_block_list: Sequence[str] | None = None,
              startup: Mapping[str, Any] | None = None) -> None
```

The fourth registration verb, in the framework's idiom (`components/world_and_dispatch.md` §1.2),
except that it is call-only: there is nothing to decorate, so it returns `None` rather than the
decorator-or-call shape the other three share. It appends an `AddedWorld` and bumps the
registration epoch (§4.2).

`name` defaults to `world.name`. `store` names a scope: `None` keeps the adder's scope, a string
opens that scope for the added world and everything beneath it (§4.1). `startup` is copied into a
`MappingProxyType` over a plain dict, so a caller mutating the dict it passed cannot change the
composition after the fact.

`World.__copy__` — the framework's way to point a world at another fixtures directory for a test —
snapshots the three registries and drops the instance manager. It now also snapshots
`added_worlds` and drops the sealed composition, so the copy reseals on first use. A copy is a
distinct `World` object and therefore a distinct node key (§4.1): copy the **root** to relocate its
fixtures, as the framework's tests do, never a dependency — a host that added the original does not
see the copy, and the docs say so.

### 3.2 Checks at the call

Only what is knowable from the two `World` objects in hand. Each raises `WorldBug` from the
`add_world` line, which is where the author can read it:

1. `name` matches `^[a-z][a-z0-9_]*$`, contains no `__`, and is not `main` or `temp`. The `__`
   exclusion is not cosmetic: schema names for `ATTACH` are paths with `/` replaced by `__` (§9),
   and permitting `__` inside a segment would let `a__b` and `a/b` collide.
2. `tool_allow_list` and `tool_block_list` are not both given.
3. `name` is not already used by another `add_world` on this host.
4. `startup`'s keys are accepted by the added world's **own** hooks (`world.accepted_startup_kwargs`,
   or any if a hook takes `**kwargs`) — not its subtree's, because §2.1 binds them to this node's
   hooks and nothing deeper. The added world's registry is complete at this point: it was imported
   before the host could name it.
5. The added world's tree does not contain `self`. Walk `world`'s reachable `World` objects; a hit
   is the cycle error. A self-add is the depth-0 case of the same walk.

Everything else — tool-name collisions after prefixing, allow/block names that do not exist,
the attach bound, conflicting bound `startup` on a shared node — is a whole-tree property and
belongs to the seal.

## 4. The node graph

### 4.1 Resolution

`composition.resolve(root: World) -> Composition`, pure and side-effect-free:

1. **Reachable nodes.** Work-list over `NodeKey`s from `(root, None)`; expand

   ```
   expand((w, scope)) = [(a.world, a.store if a.store is not None else scope) for a in w.added_worlds]
   ```

   Each key is expanded once. **The scope propagates down the subtree**: an edge with `store=None`
   passes the parent's scope through, an edge with `store="eu"` opens `eu` for everything below it.
   Scope names are flat and global, so `store="eu"` written anywhere means the same scope — which is
   what preserves §2.3's "two worlds that both name `store='eu'` for the same object share that
   store too".

   Termination: scopes are drawn from the finite set of declared `store` strings plus `None`, and
   §3.2 rule 5 makes the `World` graph acyclic, so the key set is bounded by |worlds| × |scopes|.

   The children of a node are a function of `(World object, scope)` — never of the route taken to
   it. That is what makes every later step linear in the node count instead of exponential in the
   route count, and it is why the memo key throughout this section is the node key, not the world.

2. **Canonical paths.** BFS from `(root, None)`, enqueueing each node's children in `add_world`
   order; the first dequeue of a key fixes its path. Root path is `main`; a child of the root has
   path `<name>`; deeper, `<parent path>/<name>`.

   This implements §2.3's "the shallowest route, ties broken by registration order, depth-first"
   exactly. Depth is BFS's primary order. Within one depth, BFS visits in
   (parent's position at depth−1, child index) order, which by induction on depth is lexicographic
   order on the sequence of registration indices along the path — and that is precisely the order a
   depth-first preorder walk visits the nodes of that depth.

3. **Aliases as edges, not paths.** Every edge is recorded as `(parent.path, child_name, child.path)`.
   An edge whose `<parent path>/<child name>` differs from `child.path` is an alias, and is recorded
   on the child as that string.

   Enumerating full alias *routes* is exponential in a diamond-shaped tree (a chain of worlds each
   adding two worlds that both add the next has 2^depth routes to the leaf). The edge set is bounded
   by nodes × children, identifies the composition just as completely, and is what the sidecar
   stores and the create-time check compares.

4. **Bound startup.** For each node, merge the `startup` mappings of every edge reaching it
   **key-wise**. The same key bound to two different values by two edges is a seal error naming both
   edges: a node has one configuration (§2.1). An edge that binds nothing, or binds other keys, has
   no opinion and conflicts with nothing. The root has none.

   The merge, rather than an equality test across edges, is what lets a host configure a node that a
   dependency also reaches: Shopify's storeless `add_world(stripe)` binds `{}`, and under an
   equality rule that would have conflicted with any host `startup` on the same node — leaving every
   shared node unconfigurable. Scope inheritance (step 1) makes such implicit routes the common
   case.

5. **Tool contribution**, memoised per node key, bottom-up:

   ```
   contribute((w, s)) -> tuple[Contributed, ...]:
       own      = [Contributed(t.name, t, node_of(w, s)) for t in w.tools.values() if not t.control]
       for a in w.added_worlds:
           inner = contribute((a.world, a.store or s))       # already filtered and prefixed below a
           kept  = filter(inner, a.tool_allow_list, a.tool_block_list)   # matched on inner names
           own  += [Contributed(prefix(a.tool_prefix, c.name), c.tool, c.node) for c in kept]
   ```

   Three things this gets right by construction. The host's lists and prefix apply to the child's
   whole already-filtered, already-prefixed contribution as one list, so the host never sees or
   names a grandchild (§2.2). Names in a list are matched against what the added world *contributes*,
   which is what "the added world's own names, before any prefix" means one level up. And the owning
   node travels with the tool, so a grandchild's tool reached through the host still runs against the
   grandchild's store.

   The memo key is the node key and not the world because a node's own tools belong to whichever
   node key the walk is under: two Stripe accounts contribute the same `Tool` objects against
   different nodes. The recursion into `a.world` propagates the scope exactly as step 1 does.

6. **Chains.** For each node, two handlers built by `composition.build_route_chain(layers,
   owner_key)`, where `layers` is a sequence of `(node_key, middleware)` pairs:

   - `agent_chain`: for each node on the canonical route root→owner, outermost first, that node's
     world's middlewares, each paired with **that node's** key; then `invoke` paired with the owner.
   - `internal_chain`: the owner's own middlewares paired with the owner; then `invoke`.

   `build_route_chain` is the framework's `build_chain` with one addition: each layer runs with the
   `Ctx` of the node it is paired with (§7.6), obtained through the frame the incoming ctx carries,
   and `call` is normalised exactly as today so that `call is ctx.call` in every layer. A leaf
   world's `agent_chain` pairs every layer with the root and is byte-for-byte the framework's
   chain. `World.chain` becomes `self.composition().root.agent_chain`.

   *Interpretation flagged:* §3.4 names two chains, "the host's chain outermost, then the owning
   world's chain". Applied recursively through §2.2's nesting, that is the whole canonical route,
   and it degenerates to the two chains §3.4 names whenever the owning node is a direct child. The
   full-route reading is the one under which an intermediate composite's error handler still shapes
   errors from the tools it contributes, which is what §3.4's "the added world's own error handler
   still shapes its errors as its product would" asks for when the added world is itself composite.

### 4.2 The seal and its invalidation

```python
_EPOCH = itertools.count()          # module-level in composition.py
_EPOCH_LOCK = threading.Lock()
def bump() -> int                   # called by every registration verb on every World
```

`World.composition()` returns the cached `Composition` when `self._sealed_at == current_epoch`, and
otherwise resolves, validates (§4.3), caches and returns. Recomputation is idempotent and cheap
(one walk of a tree bounded at 126 nodes); the lock guards only the counter and the cache slot.

A single process-wide counter, rather than per-world dirty flags, because a `World` holds no
back-reference to the hosts that added it: registering a tool on `stripe_world.world` can never
notify `my_company.world`. A global counter over-invalidates — one registration anywhere reseals
everything on next use — and that is the correct trade, since the framework's convention is that
registration finishes at import and the cost is a walk, not a copy.

Every caller that needs the tree calls `world.composition()`: `World.instance`, `World.chain`,
`Instance.call` (one integer compare per call), `Instance.tools`, `freeze`, `seahaven check`.

### 4.3 Why the seal is lazy

The functional spec (§2.1, §9) calls the whole-tree failures registration errors raised at import.
They cannot be raised at the `add_world` call:

- A host's own tools are registered by the module imports in its `__init__.py`, which run *after*
  `world.py` has executed `add_world`. At the `add_world` line the host's tool registry is usually
  empty, so no collision is detectable.
- A `World` object holds no reference to the worlds that added it, and adding one would make a
  leaf world's identity depend on its consumers — the thing §2.3 relies on not being true. So a
  tool registered on Stripe after `my_company` imported it can never be pushed to `my_company`.
- The framework does not freeze registration at all (`components/world_and_dispatch.md` §1.3):
  a tool registered after instances exist is available to them on the next call.

So the errors of §9 raise from the **first use** of the tree — `world.instance(...)`,
`world.tools`, `Instance.call`, `freeze`, or `seahaven check` — as `WorldBug`, with the same
messages. In the overwhelmingly common case (everything registered at import, first use later) this
is indistinguishable from an import-time error except in the traceback's location, and the messages
below all name the `add_world` that caused the problem so the author still knows where to look.
`seahaven check` seals as its first act, which makes the error a check finding in development, which
is where the spec's intent actually lands.

### 4.4 Seal validation

In order; the first failure raises `WorldBug`. Each message names a path and the `add_world` that
introduced it.

| Check | Message shape |
|---|---|
| `tool_allow_list`/`tool_block_list` names a tool the added world does not contribute | `add_world(stripe, name='stripe'): tool_allow_list names 'crate_charge', which 'stripe' does not contribute; it contributes: create_charge, ...` |
| Duplicate name in the composite tool list | `tool 'stripe_create_charge' is contributed by both 'stripe' and 'stripe_eu'; give one a different tool_prefix` |
| A contributed name is `reset`, `step`, `state`, `close`, or a control tool's name | reuses the framework's reserved-name message, with the path |
| A contributed name fails `^[A-Za-z0-9_-]{1,128}$` | `tool_prefix 'stripe.' produces 'stripe.create_charge', which is not a valid tool name` |
| The same `startup` key bound to two values on one node (§4.1 step 4) | `node 'stripe' has startup 'region' bound to 'us' by add_world at 'main/stripe' and to 'eu' by add_world at 'x/payments'` |
| `len(nodes) - 1 > attached_limit()` | `this world has 130 added stores; SQLite can attach 125` |

`attached_limit()` is probed once per process from a throw-away `apsw.Connection(":memory:")` via
`conn.limit(apsw.SQLITE_LIMIT_ATTACHED)` and cached. Measured at 125 on apsw 3.53.4. Probing rather
than hard-coding means a differently built SQLite fails at the seal with a number, not at the
127th `ATTACH` inside `inspect()`.

### 4.5 Why scopes are inherited

This is the one place where the architecture changed the functional spec, on 2026-09-14. It is
recorded here because the reasoning is a design argument, not a behaviour statement.

The spec originally keyed a node on `(world object, store key)`, reading the key only off that
world's own `add_world` call. Under that rule a node's children are a function of its `World` alone,
so **two accounts of one world share their children**: with `my_company` adding `stripe` and
`stripe_eu`, if `stripe_world` adds a `tax_world` with the default store, both accounts resolve to
the same `(tax_world, None)` node and share one tax store. Neither party can fix it. The host cannot,
because §12 forbids reaching into an added world's declaration to redirect its children; and
`stripe_world`'s author cannot, because a leaf world's author has no way to know it will be added
twice.

The same failure hits the marketplace case §2.3 cites as its own justification — "a marketplace
whose Shopify carries the merchant's Stripe, not the company's". A host adding Shopify with
`store="merchant"` got `(shopify, "merchant")`, but Shopify's `payments` still resolved to
`(stripe, None)`: the company's own account, with no way to separate them.

Inheriting the scope fixes both, costs no new parameter, and has a host touch no other world's
declaration — the propagation is automatic. Every example in the spec resolves identically under it:
in §2.5, `shopify` and `x` are added storeless, so they stay in the unnamed scope and their
`payments` edges still alias the root's `stripe` node.

What it costs is node count. A world added under three scopes, with four worlds in its own subtree,
is twelve nodes — twelve files, twelve connections, twelve sessions. That is the honest count rather
than an inflation, but it makes the attach bound (§4.4, §9) bind sooner than the per-edge rule would
have, and §15 records it as a constraint.

## 5. The composite tool surface

`Composition.tools` is the flat, insertion-ordered mapping of §3.1: the root's own non-control tools
in registration order, then each `add_world`'s contribution in `add_world` order. `Instance.tools()`
is `[c.tool.listing() | {"name": c.name} for c in comp.tools.values()]` — the tool's own listing with
only the name substituted, so descriptions and input schemas are byte-identical to the added world's
(§3.1) and nothing reveals the origin.

Control tools are never contributed: `contribute` skips `tool.control`. Only the root's control
tools exist, and they operate over the whole composition (§9, §10).

`Composition.by_fn` maps each tool's `fn` to the tuple of `Contributed` entries carrying it, for
typed dispatch (§8). A function reaches more than one entry exactly when its world is a node twice.

## 6. Instance creation

### 6.1 Files and connections

One SQLite file and one connection per node. File names are derived from the path: `main` →
`state.sqlite`, an added node → `state.<path with '/' replaced by '__'>.sqlite`
(`state.stripe.sqlite`, `state.stripe__tax.sqlite`). A leaf world's instance directory is therefore
byte-for-byte the layout it has today, which is what keeps `format_version: 1` fixtures loading
unchanged.

### 6.2 `InstanceManager.create`

```
comp = world.composition()                      # seals; §4.4 errors surface here
instance_id = uuid4(); dir = work_dir / instance_id; dir.mkdir(parents=True)
try:
    if fixture_id is None:
        for node in comp.nodes: build_blank(dir/node.file_name, node.world.schema).close()
        clock = Clock.from_iso(now) if now else Clock.wall(); seed_source = world.name
    else:
        fixture = world_fixture(fixture_id); verify(fixture)        # root file hash, as today
        check_composition(fixture.meta, comp)                       # §11.3
        for node in comp.nodes: verify_node(fixture, node); copy(...)
        if now is not None: raise WorldBug("now= applies to blank instances only")
        clock = Clock.from_iso(fixture.now); seed_source = fixture_id
    base = instance_seed(seed_source, seed)
    for node in comp.nodes:
        rt = NodeRuntime(db=open_instance(dir/node.file_name, clock),
                         ids=Ids(node_seed(base, node.path)), state={}, session=None)
        rt.ctx = Ctx(rt.db, clock, rt.ids, rt.state, InstanceInfo(...), worlds=Worlds.unbound())
    instance = Instance(..., node_keys=frozenset(comp.by_key)); register lazily as today
    with instance._held():                                          # depth 0→1 bumps the epoch, §7.3
        run_startup_hooks(comp, instance, startup_kwargs)           # 6.4
        for node in comp.nodes: rt.session = start_session(rt.db.conn, node.world)
except BaseException:
    close everything opened; rmtree(dir); raise
```

`Instance.__init__` takes `runtime: Mapping[NodeKey, NodeRuntime]` in place of today's single
`ctx`, `db` and `session`. `instance.db`, `instance.ctx` and `instance.state_path` stay as
properties over the **root's** runtime, because `fixtures.freeze`, `control.py` and the pytest
plugin read them and a leaf world's instance must be indistinguishable from today's.

**The instance pins its node set.** `node_keys` is captured at creation and compared against the
live seal on every call (§7.1). The framework promises that a tool or middleware registered after
instances exist reaches them on the next call, and the seal delivers that; but an `add_world` after
instances exist changes the number of files an instance is supposed to have, and no live instance
can honour it. Such a call raises `WorldBug("this world's composition changed after the instance was
created; create a new instance")` rather than dispatching against a tree the instance does not
hold.

`node_seed(base, path)` is `base` itself for the root and `sha256(base + b"\0" + path.encode())`
for an added node. The root's derivation is therefore untouched, and each added node's stream is a
function of its own canonical path alone — so adding or removing a node perturbs no other node's
ids, which is §5.5's requirement and the reason the path is the salt rather than an index.

### 6.3 Per-node context

`Ctx` gains `worlds`, and becomes generic in it:

```python
@dataclass(frozen=True)
class Ctx[W: Worlds = Worlds]:
    db: Db; clock: Clock; ids: Ids
    state: dict[str, Any]
    instance: InstanceInfo
    worlds: W
    call: Call | None = None

    @overload
    def with_call(self, call: Call | None) -> Self: ...
    @overload
    def with_call(self, call: Call | None, *, worlds: Worlds) -> Ctx[Any]: ...
```

`db`, `ids` and `state` are the node's own; `clock` and `instance` are the instance's, shared by
every node. Bare `seahaven.Ctx` keeps working as an annotation, and `Ctx[CompanyWorlds]` is the
opt-in of §2.4. `Tool.from_function`'s first-parameter check accepts `Ctx`, `Ctx[X]` (compared with
`typing.get_origin`) or no annotation, as today.

`with_call` gains the keyword because the chain, not `Ctx`, knows which node a layer belongs to and
under which activation; `Ctx` stays frozen and knows nothing about the instance. Two overloads
(phase 3), because only handing in another activation's `Worlds` widens the answer: binding a call
alone leaves the parameter alone, which is what keeps a `Ctx[CompanyWorlds]` one inside that
world's own middleware. Every `Worlds` a
running ctx carries was built by `Frame.ctx(key, call)` (§7.6); the template `worlds` on a
`NodeRuntime` is `Worlds.unbound()`, whose every access raises, so a ctx that escaped the framework
fails the same way a stale handle does.

### 6.4 Startup hooks

- **Unknown-argument check**, before any file is touched, against `comp.accepted_startup_kwargs` —
  the union across the tree, or `None` (meaning "any", switching the check off) if any hook anywhere
  takes `**kwargs`. This is §5.3's broadcast rule stated as a set.
- **Order**: depth-first preorder over the canonical tree, root first, children in `add_world`
  order. Each node's hooks run **once**, however many routes reach it.
- **One transaction per node, all opened before the first hook runs, committed in sequence after
  the last.** The root's hooks must be able to write into a child's store through
  `ctx.worlds.<name>.db` before that child's own hooks run (§5.3), which is only coherent if every
  node's transaction is already open. A hook raising rolls all of them back and the instance is
  removed. This is the same discipline `bulk()` uses (§7.5), deliberately.
- **Arguments per hook**: `effective = {**{k: v for k, v in reset_kwargs.items() if hook accepts k},
  **{k: v for k, v in node.bound_startup.items() if hook accepts k}}`. Bound wins and is not
  overridable, because a bound keyword is part of the composition and an eval must not be able to
  reconfigure one node by passing a `reset()` keyword that happens to share its name (§5.3).
- Each hook receives `frame.ctx(node.key, None)` for its own node: live `worlds`, no `call`. The
  frame is the creation activation (§7.3).

## 7. The call path

### 7.1 Agent-initiated calls

```
Instance.call(target, /, **arguments):
    comp = self._current_composition()                  # seals; refuses a changed node set (§6.2)
    entry = comp.tools.get(target) if isinstance(target, str) else resolve_fn(comp, target)   # §8
    if entry is None: raise UnknownTool(target)
    tool = entry.tool
    with gate(bypass=tool.control):
        with self._held() as frame:                     # RLock + closed check; depth 0→1 opens a Frame
            if tool.control: return control.dispatch(self, frame.ctx(comp.root.key, Call(...)))
            node = entry.node
            ctx = frame.ctx(node.key, Call(entry.name, arguments, tool, node=node.path))
            with in_call():                             # §7.4
                return node.agent_chain(ctx, ctx.call)
```

`_current_composition()` is `self.world.composition()` plus the pinned-node-set check, memoised on
the `Composition` object's identity so the set comparison runs once per seal, not per call.

Unchanged from the framework's path but for the node lookup: the gate is still taken before the
lock, control tools still bypass both the gate and the chain, and the chain is still
`build_chain`'s output read at call time.

Logging: the framework's per-call `INFO` line gains `node=<path>`. A nested call (§7.2) logs its own
line with `internal=true`, so an eval can separate agent-initiated calls from the calls a composite
made on their behalf.

### 7.2 Nested calls through a handle

```
WorldHandle.call(target, /, **arguments):
    frame = self._live_frame()                          # §7.3: raises if this activation has ended
    node, tool = resolve_in_subtree(self._node, target)
    with frame.instance._held():                        # RLock re-entry at depth ≥ 1: same frame, no gate
        ctx = frame.ctx(node.key, Call(tool.name, arguments, tool, node=node.path))
        return node.internal_chain(ctx, ctx.call)
```

- **The gate is bypassed**, per §4: the outermost call already holds it, and taking a
  non-reentrant `BoundedSemaphore` again would deadlock the instance against itself.
- **The lock is re-entered**, which is free — it is already an `RLock` for exactly this class of
  reason (`components/fixtures_instances.md` §2.4).
- **`internal_chain`**, so only the owning world's middleware runs; the host's chain is already
  wrapped around the host tool making the call (§3.4).
- **Errors raise.** The added world's `ToolError` subclass, shaped by its own handler, propagates
  into the host tool, which decides what the agent sees. The host's handler does not re-wrap it
  because the framework's scaffolded handler passes `ToolError` through.
- **Transactions.** `invoke` opens the transaction on `ctx.db`, which is the *child's* connection.
  The host's own per-call transaction is open on the host's connection and is untouched. So the
  nested call commits when it returns, and a later failure in the host rolls back only the host's
  store. That is §4's "no cross-world atomicity", and it falls out of one connection per node rather
  than being implemented.

### 7.3 Handle lifetime: handles are not references

`Instance._held()` is the one way framework code takes the instance lock. It keeps a depth counter
beside the `RLock`; the transition from depth 0 to 1 increments `_epoch` and opens a `Frame(self,
epoch)`, and every nested `_held()` on the same thread yields that same frame. The epoch bounds how
long a handle stays usable (it is unrelated to a node's *scope* in §4.1). Every `Worlds` and
`WorldHandle` holds the frame it was built from; every member access checks `frame.epoch ==
instance._epoch` first and raises `WorldBug("a world handle was used after the call it belongs to
returned")` otherwise.

Bumping at depth 0 rather than per `Instance.call` is what keeps handles valid for the whole
outermost activation: across a nested `handle.call`, after it returns, and — the case that would
otherwise bite fixture generators — across an `inst.call(...)` made from inside a `bulk()` block,
which re-enters the lock at depth 1 and therefore opens no new frame. A handle lives exactly as long
as the activation that made it, which is the lifetime §4 describes when it calls handles
call-scoped.

This is the mechanism that makes §2.3's sharing implementable: because handlers reach added worlds
by name on every call and never hold a store, the framework is free to resolve a name to a different
node in a different instance. `seahaven check` backs it with SH209 (§14), and the framework's trust
model applies — this is a guard against a mistake, not against an adversary.

### 7.4 The in-call guard

`instances.py` holds a `threading.local()` flag set by `Instance.call` around the dispatch and
cleared in `finally`. `World.instance(...)` raises
`WorldBug("instances cannot be created from inside a tool call; reach added worlds through ctx.worlds")`
when it is set. In-process calls run on the caller's thread and OpenEnv runs each session on its own
thread, so a thread-local is exactly the right scope. `bulk()` does not set it.

### 7.5 `bulk`

`_held()` at depth 0 (a new frame), one transaction per node, yields `frame.ctx(root.key, None)` —
the **root's** context with a live `worlds` and no `call` — and commits the transactions in sequence
on exit, rolling all back if the block raised. Fixture generation reaches every node through
`ctx.worlds.<name>.db` (§5.3). An `inst.call(...)` inside the block re-enters at depth 1 and keeps
the block's handles valid (§7.3).

### 7.6 Which context a middleware sees

Every layer of a chain runs with the `Ctx` of the node it was paired with at the seal (§4.1 step 6):
`frame.ctx(key, call)` is `runtime[key].ctx.with_call(call, worlds=Worlds(frame, key))`. So for an
agent call to a contributed Stripe tool through `my_company`:

- `my_company`'s middleware runs with `my_company`'s `db`, `state`, `ids` and `worlds`;
- Stripe's middleware, and then `invoke` and the tool, run with Stripe's.

`call` is the same object in every layer, and `call.node` names the owning node's path, so a host
gate or trace knows what is being called without touching a foreign store. `invoke` opens its
transaction on the ctx it was handed, which is the owner's connection.

This is the only reading under which the two examples §3.4 gives for host middleware work at all.
An access-control middleware on the host reads the principal its own startup hook put in
`ctx.state`; a trace writes to its own tables through `ctx.db`. With the owning node's ctx in every
layer, both would silently see Stripe's empty state and Stripe's tables instead. The cost is that a
host middleware cannot reach the owning node's store through `ctx.db`; if it needs to, it has
`ctx.worlds` and `call.node`, the same doors a host tool has.

A middleware that rewrites arguments and passes a new `Call` on still works: the next layer
normalises `ctx` to its own node with that `Call`, as `build_chain` does today. A middleware's own
`ctx.state` is its own world's and never the tool's, so nothing leaks downward by accident; a host
layer that deliberately wants to hand the owning node something writes to that node's `state`
through `ctx.worlds`, the same door a host tool uses. Added worlds stay unaware of their host, which
is what §4 asks.

## 8. Typed access

### 8.1 The overloads

On `Instance` and on `WorldHandle`:

```python
@overload
def call[**P, R](self, tool: Callable[Concatenate[Ctx, P], R], /, *args: P.args, **kwargs: P.kwargs) -> R: ...
@overload
def call(self, tool: str, /, **arguments: Any) -> Any: ...
```

`Tool` becomes `Tool[**P, R]` and `Tool.from_function` preserves the parameters, so a tool built by a
factory types the same way. This is annotation-only: nothing in the runtime reads `P` or `R`.

*Built 2026-09-14, phase 3.* Three things this section did not say, each needed for any of it to
reach a world author:

- **`World.tool` needs overloads too** — a `Tool` in and the same `Tool[P, R]` out, a function in
  and the same function out, and the keywords-only form returning a decorator. It returned `Any`,
  so without them every `@world.tool` function is an untyped callable and `R` resolves to `Any`
  wherever it is called.
- **`Ctx[Any]`, not a bare `Ctx`, in the `Concatenate`** here and in `Handler` and `Middleware`.
  `Ctx` is invariant in `W`, so a chain typed on `Ctx[Worlds]` refuses the very context a world
  that declared `Ctx[CompanyWorlds]` (§8.3) runs its own middleware with. Nothing in the chain
  reads the parameter.
- **A call that type-checks has to run**, and the typed path spells its arguments the way *Python*
  spells them where validation takes the way the *wire* does. `Tool.arguments` reconciles the two:
  positional arguments — which `ParamSpec` requires the signature to accept, and which therefore
  have to work — bind in the argument model's field order, the signature's order after the context;
  every name is then translated to the one the published schema carries, so an argument declaring
  `Field(alias="from")` is sent as `from` though the caller wrote `from_`. Too many positional
  arguments, or one given twice, is a `WorldBug`. A tool named as a *string* is the wire's own
  spelling on both sides and passes through untranslated, which is what `call.arguments_of`
  branches on.

`Ctx.with_call` gains two overloads for the same reason — `(call) -> Self` and
`(call, *, worlds: Worlds) -> Ctx[Any]` — because only the second answer actually widens (§6.3).

### 8.2 Resolution

`Composition.by_fn` groups the composition's `Contributed` entries by their tool's `fn` — one pass
over the flat list, since the decorator already returns the function unchanged and a `Tool` carries
the function it was built from.

*Built 2026-09-14, phase 1:* an earlier draft of this section put a `World._tools_by_fn:
dict[Callable, Tool]` on each world and had `by_fn` invert it across the tree. It shipped as the
grouping above instead, for two reasons: a single-valued map silently loses one of the two tools a
world registers from one function, while `by_fn`, which is what dispatch reads, correctly reaches
both; and inverting a per-world map buys nothing that grouping the entries does not, because the
entries already carry their tool. A per-world `fn → Tool` map is still needed for
`WorldHandle.call(fn)` below, which resolves over *every* tool of a world in the handle's subtree,
contributed or not — a set `Composition.by_fn` deliberately does not hold.

*Built 2026-09-14, phase 3:* that per-world map is `World.tools_by_fn: Mapping[Callable,
tuple[Tool, ...]]`, public and read-only beside `World.tools`, maintained by `_add` and
snapshotted by `__copy__`. Multi-valued for the reason above. The framework's own control tools are
not in it: they are no part of a world's surface, are never contributed, and are refused by name by
both `call` paths, so a reference to one resolves to nothing — which is what it is.

- `Instance.call(fn)`: `comp.by_fn[fn]`. Empty → `WorldBug("<qualname> is not a tool of this world
  or anything it adds")`. More than one entry → `WorldBug` naming the candidate paths and telling
  the caller to use `ctx.worlds.<name>.call(fn)` or the exposed name. The ambiguity is real and the
  spec does not cover it: a function belonging to a world that is a node twice (the two Stripe
  accounts) genuinely does not name one store.
- `WorldHandle.call(fn)`: the unique node **in this handle's subtree** whose world owns `fn`, found
  by walking the node and everything it adds and asking each world's `tools_by_fn`. Ambiguous or
  absent → `WorldBug`. A handle names an account, so this is unambiguous in every case the
  double-add creates. The owner may be a descendant, and the call then runs on *its* chain, its
  context and its store.
- `WorldHandle.call(name)`: the node's own world registry only — the added world's own unprefixed
  names, as §4 states. Filtering does not apply: host code can call every tool of an added world,
  contributed or not.

Dispatch after resolution is byte-identical to the by-name path: validate, chain, transaction.

### 8.3 `Worlds` as a typing declaration

`seahaven.Worlds` is the runtime container class *and* the base a world subclasses to declare its
children:

```python
class CompanyWorlds(seahaven.Worlds):
    stripe: seahaven.WorldHandle
    shopify: seahaven.WorldHandle
```

The subclass is never instantiated; `ctx.worlds` is always the framework's own `Worlds`, and the
annotation is a type-checker fiction that `__getattr__` satisfies at run time. `Worlds.__getattr__`
returns `WorldHandle` for any name and raises `WorldBug` for one that is not a registered child.
The binding of the class to the registrations is `seahaven check`'s (SH502/SH503, §14), not the
runtime's.

### 8.4 `invoke` returns the tool's object

```python
def invoke(ctx, call):
    validated = call.tool.validate(call.arguments)
    call = call.with_arguments(**validated); ctx = ctx.with_call(call)
    if call.tool.transaction:
        with ctx.db.transaction():
            result = call.tool.fn(ctx, **call.arguments)
            to_jsonable_python(result)            # prove it serialises; the value is discarded
            return result
    result = call.tool.fn(ctx, **call.arguments)
    to_jsonable_python(result)
    return result
```

The serialisation stays inside the transaction, so the framework's rule holds: a result that cannot be
serialised still rolls the call back. What changes is that the **original object** is returned, so
`R` in §8.1 is not a lie and a host tool receiving a `Charge` receives a `Charge`.

*Built 2026-09-14, phase 3:* the proof is `call.serialise`, not a bare `to_jsonable_python` as the
sketch above has it. `serialise` is where this framework's own refusals live — `bytes`, `set`, an
iterator — and running the sketch instead would have dropped all three.

`call.serialise` is already a public function of the module, so `openenv/env.py`'s `step` calls
it on what `instance.call` returns and `control.dispatch`, which already serialises its own
results, is unchanged. The change is behaviour-preserving for everything in the repository today:
every ProjectTracker tool returns `dict[str, Any]`, `str`, `list` or `None` and none returns a
model, and `to_jsonable_python` of such a value is an equal value, so the 65 call sites in the
suites and the executed docs that index a result see what they saw before.

The cost is one redundant `to_jsonable_python` per call on the server path. The alternative —
carrying the serialised form alongside the result — either changes `Handler`'s signature, which
every middleware in every world is written against, or stashes it in per-call instance state, which
nesting makes fragile. Serialising a small result twice is the cheaper mistake, and the wire encode
dominates it.

## 9. Inspection

`db.open_inspection(path, clock, attachments: Sequence[tuple[str, Path]] = ())`:

```
conn = apsw.Connection("file:<root>?mode=ro", flags=READONLY|URI)
... the existing hardening and clock functions ...
for schema, p in attachments:
    conn.execute("ATTACH DATABASE ? AS ?", (f"file:{p}?mode=ro", schema))
install the permanent deny_writes authorizer                # last: it denies SQLITE_ATTACH
```

Both properties are verified against apsw 3.53.4 rather than assumed: SQLite binds the schema name
as a parameter, so no identifier is ever interpolated into the statement; and an authorizer
installed after the attaches denies a later `ATTACH` and every write to an attached schema while
leaving cross-schema reads working.

Order is load-bearing: the write-denying authorizer already denies `SQLITE_ATTACH`, so every attach
must happen before it is installed, and none can happen after — which is exactly the property that
makes the eval's cross-node view safe. It is read-only, it is never a world tool, and it cannot grow.

Schema names are the node's path with `/` replaced by `__` (`stripe`, `stripe__tax`); the root is
SQLite's own `main`. §3.2's ban on `__` inside a name is what keeps this injective.

The instance keeps **two** read-only handles, and both are opened this way with the same
attachment list built from `comp.nodes[1:]`: `inspect()`, the caller's, read without the instance
lock; and `_control_db()`, the control tools' own, read under the lock. They are separate because
`sandbox.run_statement` swaps a connection's authorizer for the length of a statement, and a swap
on a connection another thread is stepping a cursor on deadlocks inside SQLite (`instances.py`,
`_control_db`). `controller_run_sql` therefore runs on the control handle and sees every node,
schema-qualified, in one statement (§6.1). Its `_ControlAuthorizer` defers to the permanent
`_deny_writes`, which receives the database name with every action and allows reads on an attached
schema exactly as on `main`; nothing in it changes. A world's own `run_sql` helper runs on
`ctx.db`, which is one node's connection, so isolation between nodes is structural and there is
no allowlist to get wrong.

The bound is the attach limit, checked at the seal (§4.4).

## 10. Changes

`Change` gains `world: str`, the owning node's path (`main` for the root). One `apsw.Session` per
node, attached after startup hooks as today; `Instance.changes()` renders each node's changeset with
that node's connection (for `pragma_table_info`) and concatenates in canonical BFS order.
Per-node exclusions are each world's own: its FTS5 shadow tables and its own `untracked_tables`.
`controller_changes` is unchanged and now covers every node.

## 11. Fixtures

### 11.1 The sidecar

```python
class NodeMeta(BaseModel, frozen=True, extra="forbid"):
    path: str; world: str; world_version: str; schema_hash: str
    scope: str | None
    file: str; file_sha256: str
    aliases: tuple[str, ...] = ()          # non-canonical "<parent path>/<name>" edges

class FixtureMeta(BaseModel, frozen=True, extra="forbid"):
    format_version: Literal[1, 2]
    id: str; world: str; world_version: str; schema_hash: str
    now: str; parent_id: str | None; file_sha256: str; created_at: str; description: str
    nodes: tuple[NodeMeta, ...] = ()
```

The version-1 fields describe the **root** node and keep their meaning exactly; `nodes` lists the
added nodes only. A model validator ties the two: `format_version == 1` iff `nodes` is empty.
`freeze` writes version 1 for a single-node composition and version 2 otherwise, so a leaf world's
fixtures are unchanged on disk and every existing fixture keeps loading. `load` accepts both and
rejects any other `format_version` naming the file, as today. There is exactly one `now`.

### 11.2 Freeze

Under the instance lock:

0. Refuse if any node's connection is in a transaction, in the framework's own words: `freeze`
   cannot run inside `bulk()`, and with N transactions open the check is `any(rt.db.in_transaction)`.
1. `conformance.check(rt.db.conn, node.world)` for **every** node, all of them, before anything is
   written. A failure names the path and mints nothing (§5.3).
2. `VACUUM INTO` each node's file into `.pending-<id>/<file_name>`.
3. Hash each; build `FixtureMeta`; `yaml.safe_dump(sort_keys=True)`; `chmod 0o444` on every state
   file.
4. `os.rename(pending, final)`. Any failure removes the pending directory.

Fork is unchanged: create from a composite fixture, change, freeze; `parent_id` is the composite
fixture's id.

### 11.3 Create-time verification

`check_composition(meta, comp)`, after the root's hash check and before any copy:

- A version-1 sidecar against a multi-node composition, or the reverse → refuse, naming both shapes.
- The set of `(path, scope)` pairs must equal the composition's added nodes. A difference is
  reported as **node added**, **node removed**, or **sharing changed** (a path present on both sides
  with a different scope), naming the path. Because a scope propagates, changing one `store=` on an
  edge changes every node beneath it, and the check reports the whole set that moved.
- The alias edge sets must be equal — this is what catches a redirection that changes no node's
  identity, such as a host that stops sharing a grandchild.
- Each node's `schema_hash` must equal its world's. A difference is refused with the framework's
  "this fixture was frozen from a different schema; regenerate it", prefixed by the path.
- Each node's `file_sha256` is verified as the root's is, cached per process on
  `(path, mtime, size, hash)`.
- `world_version` differing while `schema_hash` matches is **logged at INFO and reported in the
  composition report, never refused** (§7). One package cannot be installed at two versions in one
  environment, so the framework has no version check of its own to make; the schema hash is the
  real invalidation signal and it already works per node.

## 12. Composition report

```python
@dataclass(frozen=True)
class NodeReport:
    path: str; world: str; world_version: str; scope: str | None
    aliases: tuple[str, ...]; schema_hash: str
    frozen_world_version: str | None = None   # what the fixture recorded, when it is not installed

Instance.composition() -> tuple[NodeReport, ...]
```

`world_version` is the version *installed*. `frozen_world_version` is what the fixture this instance
was created from recorded for that node, and is `None` for a blank instance and wherever the two
agree — which is what makes §11.3's "reported in the composition report" something an eval can read,
rather than only a log line.

`SeahavenState` gains `composition: list[dict] | None`, populated from the same tuple, so an eval
over OpenEnv can tell what it is running against (§6.3). Nothing agent-facing carries it: `state` is
not an observation, and the tool list is untouched.

## 13. Errors

No new exception types. Everything this design raises is `WorldBug`, which is the framework's
category for misuse and world bugs, and everything an agent can see is unchanged: a contributed
tool's errors are its own world's `ToolError` subclasses, shaped by its own handler, and nothing in
them names a node.

The `WorldBug`s introduced here: the five `add_world` checks (§3.2); the six seal checks (§4.4); a
handle used outside its activation; a call on an instance whose composition changed after it was
created (§6.2); instance creation from inside a call; a function passed to `call` that no node owns
or that more than one node owns; an unknown child name; every create-time composition mismatch
(§11.3); a freeze conformance failure naming the path.

## 14. `seahaven check`

New codes. Gaps in the numbering are deliberate; retired codes are not reused.

| Code | Sev | Rule | Source |
|---|---|---|---|
| SH206 | warning | A prefixed world's tool descriptions contain the unprefixed name of another tool that world contributes | `lint/code.py`, over the sealed composition; substring match on a word boundary. Never rewrites (§3.3) |
| SH207 | warning | One underlying tool contributed under two names through a shared node | composition |
| SH208 | error | `.instance(` called in a module under `tools/` or `middleware/` | `lint/code.py`, AST |
| SH209 | error | A literal `ctx.worlds["x"]` subscript or `ctx.worlds.x` attribute that is not a registered child name | `lint/code.py`, AST, against the world's `added_worlds` |
| SH406 | error | Composite sidecar: `nodes` disagrees with the world's composition (paths, scopes or aliases) | `lint/fixtures.py` |
| SH502 | error | A `Worlds` subclass annotates an attribute that is not a registered child name | `lint/world.py` |
| SH503 | warning | A registered child name is unannotated, when the world declares a `Worlds` subclass | `lint/world.py` |
| SH504 | error | The composition does not seal: any §4.4 failure, reported with its own message and the `add_world` it names | `lint/world.py`; `check` seals before any other rule runs |

SH402, SH403 and SH405 (file hash, schema hash, journal companions) each run **per
node** on a version-2 sidecar and report the path in the message. SH401 and SH404 stay whole-sidecar: there is
one clock per instance, and a `nodes` list that repeats a `path` or a state file stops the fixture
the way an invalid sidecar does. `check` seals the composition as
its first act, so every §4.4 error is reported as SH504 rather than a traceback — which is where the
functional spec's "registration error" intent actually lands (§4.3). A `Target` whose world cannot
seal still runs every rule that needs no tree: only SH206, SH207, SH406 and the per-node half of
SH403 read `Target.composition`, so the DDL and coverage rules, SH502 and SH503, the rest of the
code rules and the rest of the fixture rules all still report.

## 15. Constraints and cost

- An idle composite instance is N stores, N connections and N sessions. The framework's benchmark
  measured one node per instance until 2026-09-14, and that figure was read as a per-node floor; it
  now stands the committed `tests/worlds/` tree up at one, two and four nodes, and the floor is a
  measurement rather than an assumption: one store per node, about 3 ms and 44 KiB of idle disk per
  added (empty) node, and no per-call cost by which a four-node tree can be told from a leaf
  (`bench/results/latest.md` §7; never a gate). The design target (hundreds of concurrent instances,
  minute-long lifetimes) holds for small N and is not re-derived here.
- Scopes multiply nodes: a world added under three scopes, with four worlds beneath it, is twelve
  nodes rather than four (§4.5). This is the correct count, but it means the attach bound is reached
  by trees that look small in source. Where an author is told the count is SH504, which reports the
  seal's own refusal with the real bound in it (§15.1).
- The hard cap is the attach limit, 125 added nodes, checked at the seal. No lower framework limit
  is imposed: the spec never asked for one, and a number invented here would be a wall in the wrong
  place. Section 4.4's error names the real bound.
- A seal is O(nodes × children + tools) and happens on the first use after any registration
  anywhere in the process. Steady state is one integer compare per call.
- No process-wide mutable state beyond the registration epoch and the per-`World` cache slot, both
  under one lock, both safe under free-threaded CPython. A `World` object still carries no state, so
  two roots in one process importing the same world get their own stores.

### 15.1 Why `check` does not report the node count

This section said, until 2026-09-14, that `seahaven check` reports the node count so an author sees
it before an instance does. Phase 5 built the check rules and did not build that, and the clause
above is rewritten rather than left standing. The reasoning, recorded here as §4.5 records its own:

`check` prints findings and nothing else (`cli/check.py`), and a clean world prints nothing at all —
the reference world's check is zero lines and exit 0. A node count is not a finding: it names no
mistake and no edit. So there were two ways to report it and both are worse than not reporting it.
An informational line changes what the command *is*, from a list of things to fix into a report with
prose in it, and every tool that reads its output — a pre-commit hook, CI, an authoring agent — has
to learn to skip a line that never means anything. A `warning`-severity finding fires on every
correct composite world, which is precisely the rule `docs/reference/lints.md` says the rule set is
kept small to avoid: one an author learns to ignore, and then ignores its neighbours with it.

What is left is that the count is only ever *actionable* at the bound, and there it is reported.
SH504 carries §4.4's refusal verbatim — `this world has 130 added stores; SQLite can attach 125` —
which names the count and the limit, on a line whose path names the world, at the moment the count
first matters and before any instance exists. That is the whole of what the struck clause promised
an author.

The count is available in process from `len(world.composition().nodes)` and per instance from
`Instance.composition()`, so nothing is unknowable; what has no home is *volunteering* it. A command
whose job is to describe a world rather than to find fault with one — `seahaven world info`, say —
would be that home, and this design does not propose one.

## 16. The framework delta

Every change this design makes to `../seahaven_framework/`. Each is additive; none reopens a
decision there.

| Module | Change | Section |
|---|---|---|
| `composition.py` | **New.** `AddedWorld`, `Node`, `Contributed`, `Composition`; resolution, the seal, the epoch, all whole-tree validation, `attached_limit()`; `Composition.entry_for(fn)` | 3, 4, 8.2 |
| `handles.py` | **New.** `Frame`, `Worlds`, `WorldHandle`; the subtree walk `WorldHandle.call(fn)` resolves over, which lives here because `composition.py` imports this module | 7.3, 7.6, 8.2, 8.3 |
| `world.py` | `add_world`; `added_worlds`; `composition()`; `chain` reads `composition().root.agent_chain`; every verb bumps the epoch; `__copy__` snapshots `added_worlds` and the inverted registry and drops the seal; `tools_by_fn`, the per-world multi-valued `fn → Tool` map `WorldHandle.call(fn)` resolves through, and the refusal of a tool function that cannot key it; `tool` overloaded so a registered tool keeps its own type | 3, 4.2, 8.1, 8.2 |
| `ctx.py` | `Ctx` generic in `W: Worlds`; `worlds` field; `with_call(..., worlds=)` | 6.3 |
| `tool.py` | `Tool[**P, R]`; first-parameter check accepts `Ctx[X]`; `Tool.arguments` binds positional arguments and translates parameter names to the wire names the schema publishes | 8.1, 6.3 |
| `call.py` | `Call.node: str = "main"`; `invoke` returns the original object after proving it serialises; `Handler` and `Middleware` take `Ctx[Any]`, since `Ctx` is invariant in `W`; `name_of` and `arguments_of`, the two "how did the caller spell it" rules; `build_chain` gains the per-layer node pairing used by `composition.build_route_chain` | 7.6, 8.1, 8.4 |
| `instances.py` | `NodeRuntime` per node; N stores, connections, sessions, `Ids`, `state`; `node_seed`; the pinned node set; `db`/`ctx`/`state_path` as the root's; tree startup hooks with merged bound kwargs and N transactions; node dispatch; `_held()` with the depth counter, `_epoch` and the `Frame`; the thread-local in-call flag; `bulk` over N transactions; `freeze` refusing inside `bulk` per node; both read-only handles opened with attachments; `composition()`; the node path and `internal` marker in the log line | 6, 7, 9, 12 |
| `fixtures.py` | `NodeMeta`; `FixtureMeta` `format_version` 1 or 2 with `nodes`; per-node freeze, hash and verify; `check_composition` | 11 |
| `db.py` | `open_inspection(..., attachments=)`, attaching before the authorizer is installed | 9 |
| `changes.py` | `Change.world`; render per node | 10 |
| `control.py` | `controller_run_sql` and `controller_changes` over the composition (no signature change) | 5, 9, 10 |
| `openenv/env.py` | `step` calls `call.serialise` on the result; `SeahavenState.composition` | 8.4, 12 |
| `lint/` | SH206–SH209, SH406, SH502–SH504; SH401–SH405 per node; `lint/world.py` is new | 14 |
| CI | Verify `ty` handles the `Concatenate` + `ParamSpec` overloads of §8.1 before world code relies on them; if it does not, a second checker on world code is the fallback, never generated code | 8.1 |

Handed elsewhere, unchanged from the functional spec: the authoring-docs requirements (prefer an
added world's tools over direct SQL on its store; declare prefixes and lists to match the client's
real surface; a world must not assume it is the only writer to a world it adds; return models, not
bare dicts) and the allocation note that the mechanism is framework core, post-V1, while the
simulated third-party worlds are private.

## 17. Testing strategy

Framework tests, pytest, no network, as `../seahaven_framework/architecture.md` §10. New files:

- **`test_composition.py`** — resolution and the seal. Canonical path selection: shallowest wins;
  equal depth broken by registration order; the induction case (a diamond three levels deep where
  BFS and a naive DFS disagree unless depth is the primary key). Aliases recorded as edges, and a
  diamond that would produce 2^depth routes resolves in linear time. `(world, scope)` identity and
  scope propagation: the same object with the default store is one node from two hosts; a named
  store is two nodes; two hosts naming the same store share; a grandchild under two scoped parents
  is **two** nodes (the §4.5 case, asserted directly); the marketplace case — a host adding Shopify
  with `store="merchant"` gets a merchant-scoped Stripe, distinct from its own — asserted as the
  regression test for the rule's motivation; §2.5's illustration resolves to exactly the five nodes
  and two aliases it lists. Contribution: filtering and prefixing per edge,
  a host's list matched against a child's *contributed* names, a grandchild's owner preserved
  through two prefixes, control tools never contributed, flat list order. Every §3.2 and §4.4 error,
  each with its message. Seal caching: no recompute without a registration; a tool registered on a
  leaf after a host sealed is visible to the host on next use.
- **`test_add_world.py`** — the five call-site checks; `name` defaults to the added world's name;
  `startup` is copied; a world added twice under two names with one store is one node with two
  views; the same world at two stores is two nodes.
- **`test_composite_instance.py`** — N files created and named by path; blank genesis at one clock;
  per-node `Ids` (the root's stream identical with and without added nodes; a node's stream
  unchanged when a sibling is added or removed; same fixture and seed reproduce every node);
  hook order and the once-per-shared-node rule; bound `startup` merged key-wise across a host edge
  and a dependency's storeless edge to the same node, and refused with both edges named when one key
  has two values; bound `startup` not overridable by a `reset()` keyword of the same name while that
  keyword still reaches every other hook; an `add_world` after an instance exists makes that
  instance's next call raise while a tool registered after it exists is served; a root hook seeding a
  child through `ctx.worlds.<name>.state` before the child's hooks run; unknown `reset` argument
  refused before any file is created; a hook raising leaves no directory.
- **`test_composite_dispatch.py`** — a contributed tool runs against its own node's store; middleware
  nesting order for a two-level and a three-level tree, asserting the full canonical route; **each
  layer sees its own world's ctx**: a host gate reading `ctx.state["principal"]` set by the host's
  hook passes for a contributed tool, an intermediate world's trace writes to its own table, and
  `invoke` runs on the owner's connection, with `call` identical in every layer and `call.node`
  naming the owner; a middleware rewriting arguments in the host layer is seen by the owner's layer;
  `ctx.worlds.<name>.call` runs only the owning chain; an added world's `ToolError` reaches the host
  tool as itself; a host tool writing to two stores where the second fails leaves the first write in
  place (no cross-world atomicity, asserted, because evals depend on it); nested calls do not take
  the gate (a thread test that would deadlock if they did); the `RLock` re-entry; direct
  `ctx.worlds.<name>.db` writes; a handle kept past its call raises; a handle made in a `bulk()`
  block survives an `inst.call(...)` inside that block; a template ctx's `worlds` raises; `world.instance()` from inside a
  tool raises; the log line carries the path and marks nested calls internal.
- **`test_typed_call.py`** — `by_fn` resolution by function reference at the instance and through a
  handle; ambiguity when a world is a node twice, with the message; a function from a world outside
  the tree; `invoke` returns the model, not a dict; an unserialisable result still rolls back; the
  OpenEnv layer serialises. Plus a `ty` run over a fixture world that calls tools by reference,
  asserting the checker resolves `R` — the §8.1 CI gate, as a test.
- **`test_composite_fixtures.py`** — freeze writes one file per node and a version-2 sidecar; a
  single-node world still writes version 1 and its directory is byte-identical to today's; a version-1
  fixture loads into a leaf world unchanged; conformance failure on any node mints nothing and names
  the path; create verifies every hash; each of node-added, node-removed, sharing-changed,
  alias-changed and schema-hash-changed refused with its own message; a version difference with a
  matching schema hash is accepted and reported; fork chain preserves `parent_id`; an added world's
  own fixtures are unreachable from a host.
- **`test_composite_inspection.py`** — every node attached read-only under its `__` schema name; a
  cross-node join in one statement; a write through the inspection handle denied; `ATTACH` from
  agent SQL denied; a world's own `run_sql` cannot see a sibling's tables; the attach bound probed
  and a tree over it refused at the seal with the real number.
- **`test_composite_changes.py`** — `Change.world` per node; one list covering every node in BFS
  order; per-node `untracked_tables` and FTS5 exclusions; a failed nested call leaves no trace in
  either store.
- **Lint tests** — one positive and one negative fixture per new code, under `tests/worlds/`.
- **OpenEnv end-to-end** — a composite world served: the flat tool list in declared order, control
  tools absent, a call to a contributed tool, `state` carrying the composition, a session reset
  destroying every node's directory.

The reference world stays single-node. A second, deliberately small composite fixture world lives
under `tests/worlds/` beside `tidy`, `messy`, `broken_ddl` and `no_world` — a two-tool "payments"
leaf, a "shop" world that adds it with an empty allow list, and a host that adds both plus a second
payments account — which is the tree every test above is written against and is cheap enough to
build blank in each test. `tests/test_docs_examples.py` executes every example in the docs, so the
composition page's examples run against that world too.

## 18. Technical decisions worth stating

1. **One code path.** A leaf world is a composition of one node. No branch in the runtime asks
   whether a world is composite.
2. **Node identity is `(World object, scope)`, and a scope propagates to the subtree** — Python
   identity plus one string, with no registry and no names to keep unique across packages. The
   architecture drove this change to the functional spec; §4.5 is the argument.
3. **The seal is lazy and globally invalidated** because a world cannot observe its consumers
   (§4.3). A global epoch over-invalidates by design; the recompute is a walk, not a copy.
4. **Aliases are edges, not routes.** Route enumeration is exponential; the edge set identifies the
   composition exactly and bounds the sidecar.
5. **BFS in registration order implements the spec's ordering rule**, with the induction in §4.1
   step 2 as the proof; there is no separate tie-breaking pass.
6. **The path, not an index, salts a node's id stream**, so adding or removing a node perturbs no
   other node.
7. **Startup transactions are opened for every node before the first hook runs**, because the root's
   hooks are specified to write into children.
8. **The inspection connection attaches before its authorizer is installed**, which is what makes
   the eval's cross-node view both possible and closed.
9. **`invoke` returns the tool's object and serialises twice on the server path.** The alternative
   changes `Handler`, which every middleware in every world is written against.
10. **Nested calls bypass the gate and re-enter the lock.** One instance runs one call at a time,
    however many nodes that call touches.
11. **A middleware layer runs with its own world's context**, paired at the seal, with `call`
    shared. The owning node's context reaches only the owner's own layers and the tool. Without
    this the host middleware examples in §3.4 cannot be written.
12. **An instance pins its node set.** Late tools and middleware reach it; a late `add_world` does
    not, loudly.
13. **Bound `startup` is merged key-wise across routes.** An equality rule would have left every
    node a dependency also reaches unconfigurable.
14. **No new exception type.** Composition adds no vocabulary an agent can observe.
15. **The attach bound is probed, not hard-coded.** Measured at 125 on apsw 3.53.4; a different
    build fails at the seal with its own number.
