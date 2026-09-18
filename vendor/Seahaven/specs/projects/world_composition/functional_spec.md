---
status: complete
---

# Functional Spec: World Composition

A world may add other worlds. An added world contributes its tool surface and its state store to the
host; the host owns the fixtures, the clock, the id seed, and the agent-facing surface. Nothing the
agent sees reveals that the world is composed.

This is the only design document for the project (decided 2026-09-13: overview and functional spec;
architecture is worked out in the repo with the code). It fixes behaviour and contracts and stops at
exact Python signatures, module layout and packaging. It is written against the Seahaven framework
as specified in `../seahaven_framework/` (functional spec, architecture, components). That spec does
not cover **composable world modules**, the framework's name for this feature: it keeps tool
projections and filtering out of scope (§24) and says nothing about a world adding worlds. This spec
supplies the DDL and fixture composition rules the feature needs. Where it needs the framework to
change, §13 lists the change. The overview records the session; where a sketch there
differs from this spec, this spec governs. When it lands is not part of this spec; the lead triggers
implementation.

## 0. Alignment with the framework (2026-09-13)

The first drafts of this spec were written before the framework spec was merged and assumed a
class-based world model. The framework decided Flask-style app-object registration
(framework spec §3.2): one `seahaven.World` object per package, tools as
plain functions decorated `@world.tool` taking `ctx` first, `world.middleware`,
`world.instance_startup`. This spec now builds on that, unchanged. Three allocation and shape
decisions taken in the rewrite, open to veto:

- **The mechanism is framework core; the worlds are private.** Composition touches `Instance`,
  `Ctx`, the fixture sidecar, freeze, inspection and changesets, none of which the extension
  contract (framework spec §21) can reach, so it cannot ship as an extension. The simulated
  third-party SaaS worlds (Stripe-like, Slack-like) remain the private, sold-through-access product.
  The planning record this spec was drafted from listed composable world modules as one private,
  post-V1 item; this splits it into mechanism (public, post-V1) and worlds (private).
- **`world.add_world(...)` is a fourth registration verb**, in the framework's own idiom; a
  `Worlds` class is optional typing sugar (§2.4), never required.
- **`reset()` keyword arguments are broadcast** to every startup hook in the tree that names them,
  reusing the framework's per-hook filtering (§5.3).

## 1. Terms

- **World**: a Python package exporting one `seahaven.World` object named `world` (framework spec
  §2.2). The object is code and registrations, never state.
- **Host world**: the world being defined, which adds others. When it is the world an instance is
  created from, it is the **root**. Fixtures belong to the root.
- **Added world**: a world object the host adds with `world.add_world`. Sealed as code: the host
  cannot change its DDL, handlers, tool definitions or middleware. Open as state: the host owns the
  instance and may read and write the added world's store directly (§4). Any world can be a host,
  an added world, or both; it is the same artifact either way (decided, overview Q6).
- **Name**: the host's internal identity for one added world, the `name` argument of `add_world`.
  Never agent-visible. `tool_prefix` is the thing closest to a namespace the agent sees; the name
  is bookkeeping.
- **Path**: an added world's fully qualified identity within the root, its own name under its
  host's path (`stripe`, `stripe/tax`). The root's path is `main`.
- **Scope**: an account family. The root's is the unnamed scope; `add_world(store="eu")` opens the
  scope `eu` for that world and its whole subtree; `store=None` keeps the adder's (§2.3).
- **Node**: one store in a composite instance. Identified by `(world object, scope)` (§2.3);
  named by its canonical path. Every route to a node other than the canonical one is an **alias**.
- **Composite instance**: an instance of a host that has added worlds. One store file per node, one
  clock, one id seed, one tool surface.
- **Composite fixture**: a root fixture holding one frozen file per node and one sidecar.
- **Handle**: what host code gets from `ctx.worlds.<name>`: that node's tools, store and state for
  the duration of one call (§4).

## 2. Composition model

### 2.1 Declaring

A host declares each added world with a registration call on its `World` object, in `world.py`
beside `World(...)` or in the package `__init__` with the other imported registrations
(framework spec §3.2). It works like the other three verbs: explicit, at import time, validated
immediately, failing loudly with a `WorldBug`.

```python
world.add_world(stripe_world.world, name="payments", tool_allow_list=[])
```

| Parameter | Meaning | Default |
|---|---|---|
| first positional | The added world's `World` object, normally the module-level `world` its package exports | required |
| `name` | Internal identity (§1). Identifier-like: lowercase letters, digits, underscores, starting with a letter; no `__`; not `main` or `temp` (SQLite's reserved schema names) | The added world's own `name` |
| `store` | The **account scope** this world and everything it adds belongs to. `None` keeps the adder's own scope, which is what shares an account; a string opens the named scope, for this world and its whole subtree, shared by every adder that names the same scope for the same world (§2.3) | `None` |
| `tool_prefix` | String prepended to every contributed tool name (`"stripe_"` turns `create_invoice` into `stripe_create_invoice`) | None; names pass through unchanged |
| `tool_allow_list` | Only these tools are contributed | All |
| `tool_block_list` | All tools except these are contributed | None |
| `startup` | Keyword arguments bound to this node's startup hooks: the way a host configures one node of a world differently from another (`store="eu"` with `startup={"region": "eu"}`). Delivered to that node's hooks at every instance creation, filtered by name like any startup keyword (§5.3) | None |

Rules:

- Composition is a property of the world's code. It is fixed for every fixture and instance of that
  host; it is not chosen at `reset()`. A fixture is valid only for the exact composition it was
  frozen under (§5.3).
- `tool_allow_list` and `tool_block_list` are mutually exclusive; giving both is a registration
  error. Both name tools by the added world's **own** names, before any prefix. A name that does
  not exist in the added world is a registration error (it is almost always a typo, and silently
  ignoring it would silently widen or narrow the agent's surface).
- Two `add_world` calls on one host may not share a `name`. The same world object added twice
  under two names with the same `store` is one node with two views (two prefixes or two lists over
  one store): allowed, with the duplicate-tool warning of §2.3. With different `store` keys it is
  two nodes: two Stripe accounts.
- `startup` keywords must be ones the added world's own hooks accept (its `accepted_startup_kwargs`);
  an unknown keyword is a registration error. When several routes reach one node (§2.3), their
  bound keywords are merged; the same keyword bound to two different values on two routes is a
  registration error, because a node has one configuration. A route that binds nothing has no
  opinion: a dependency's plain `add_world(stripe_world.world)` never stops a host from
  configuring the Stripe node they share.
- A world may not appear in its own subtree, directly or transitively. Registration error.
- A host may have no tools or tables of its own and be a pure aggregator. Nothing special happens.
- A world with added worlds can still be run standalone as a root, with its own fixtures. Its
  fixtures are invisible to any host that adds it (§5.4).
- Registration timing is the framework's: at import, before instances exist; registering during
  calls is unsupported (framework spec §3.2: registration is explicit and happens at import time).

**Why the lists and the prefix are not projections.** The framework keeps tool projections, groups
and filtering out of Seahaven (framework spec §24): reshaping a surface after the fact is the
harness's job. `tool_allow_list`, `tool_block_list` and `tool_prefix` are not that. They are world
code, written by the host's author, declaring which of a dependency's tools are the client's real
surface and under what names, the same act as writing the tool list, fixed before any instance
exists, and part of what the host is faithful to (§8). A harness that wants a different shape still
reshapes the composite list on top, as with any world.

### 2.2 Nesting

Decided (2026-09-13): an added world brings its own added worlds with it, recursively. Names become
paths (`stripe/tax`). Whether two routes to the same world share one store is decided by node
identity (§2.3), never by the host reaching into an added world's declaration. A world's
contribution is its own file plus its subtree's files, and its own tools plus its subtree's
already-filtered, already-prefixed tools. A host's prefix and lists apply to that whole contribution
as one list; the host neither sees nor names the grandchildren separately. Depth is bounded in
practice only by the attach limit (§6.1).

The code of a world is never duplicated, only its state. Worlds are Python packages and a world
depends on the worlds it adds as ordinary package dependencies (§7), so every route to Stripe in one
tree runs the same installed code; separate nodes differ only in their store files.

### 2.3 Sharing by identity

In reality a company has one Stripe account: its Shopify checkout writes charges to it, another tool
reads them, and the agent's own Stripe tools see them too. Two independent Stripe stores would make
a charge created through Shopify invisible to the agent's `stripe_list_charges`, which is the wrong
world to learn in. Separate stores are also sometimes right (a marketplace whose Shopify carries the
merchant's Stripe, not the company's).

Decided (2026-09-13, revised 2026-09-14): **a node is `(world object, scope)`. The same pair
anywhere in one root's tree is one store; a different pair is a different store.** This is ordinary
Python plus one string.

A **scope** is an account family. The root's scope is the unnamed one. `add_world(..., store=None)`
keeps the adder's own scope, which is what shares an account. `add_world(..., store="eu")` opens the
named scope `eu` **for that world and everything it adds**, recursively. Scope names are flat and
global: `store="eu"` means the scope `eu` wherever it is written, so two worlds that both name
`store="eu"` for the same object share that store too.

A package's module-level `world` object is therefore shared by everything that imports it and adds
it with the default store; a host that wants its own account adds the same object with `store="eu"`.
Shared is the default, and its failure mode is the benign one: an author who meant "private" and got
"shared" sees two integrations writing to one account, which is visible and usually what reality is.
The reverse default failed silently.

**Why scopes are inherited rather than read per edge.** The first version of this rule keyed a node
on `(world object, store key)`, reading the key only off that world's own `add_world` call. It did
not survive its own two motivating cases. A host with two Stripe accounts got two Stripe nodes but
**one** shared Tax node beneath them, since both routes reached `(tax, None)` — and it could not fix
that, because a leaf world's author cannot know it will be added twice. The marketplace case in the
paragraph above failed the same way: a host that adds Shopify with `store="merchant"` still found
Shopify's `payments` resolving to `(stripe, None)`, the company's own account, with no way to
separate them short of reaching into Shopify's declaration, which §12 forbids. Inheriting the scope
fixes both without a new parameter and without a host ever touching another world's declaration: the
marketplace's Shopify carries a merchant-scoped Stripe because everything under a `store="merchant"`
edge is merchant-scoped.

Two things the rule does not give, stated so they are not discovered. **Scoping is coarse.** A
`store=` scopes a whole subtree; "the merchant's Stripe but the company's Slack under one Shopify"
is not expressible, because the only finer tool would be the host redirecting Shopify's children,
which §12 rules out. **Scope names are one namespace across the tree, dependencies' internals
included.** A `store="eu"` written inside a package the host never reads shares with the host's
own `store="eu"`, and, since scopes propagate, so does everything storeless beneath both. That is
sharing-by-name working as intended; a world that wants an account nobody else can reach by
accident names its scope after itself.

- **Canonical path.** A node has one path: the shallowest route to it, ties broken by registration
  order, depth-first. Every other route is an alias. A host that wants a particular name for a
  shared node adds the object directly, with `tool_allow_list=[]` if the agent should not see it.
- One node means one file, one connection, one attached schema, one changeset key, one id stream,
  one sidecar entry with its aliases listed. Aliases are not nodes; nothing is created for them.
  Sharing reduces the attach count.
- Identity is resolved **within one root's tree**. Two roots in one process that import the same
  object each get their own stores; a `World` object carries no state, so nothing leaks across
  instances.
- Tool contribution is unchanged: each route's prefix and lists apply to its own view. If two routes
  expose the same underlying tool under two names, that is two declarations, not a collision; the
  framework warns.
- The composition-mismatch check at create (§5.3) covers sharing changes: adding or removing a
  `store` key changes the set of nodes, for that world and for its whole subtree.
- Node counts multiply with scopes: a world added under three scopes, with four worlds in its own
  subtree, is twelve nodes and twelve stores. That is the honest count rather than an inflation, but
  it makes the attach bound (§6.1) bind sooner, and a deep tree under several scopes should be
  checked against it deliberately.

**Resolution happens at world load, not at instance creation.** Load walks the tree, keys nodes by
`(world object, scope)` — propagating each edge's scope to the subtree below it — assigns each node
its canonical path, and produces the set of nodes
plus, per node, a table from child name to node. Instance creation makes one store per node and
nothing for aliases. Two rules make redirection possible and are in from the first version:
**handlers reach added worlds by name through the context on every call and never hold a store
reference** (§4), and **instance creation from inside a tool call is a `WorldBug`** (§4). The
framework's own trust model applies (framework spec §1: it guards against mistakes, not
adversaries); these are guards, and `seahaven check` backs them with a lint.

### 2.4 Typed access

Tools are plain typed functions, and the decorator returns the function unchanged (framework
component `world_and_dispatch.md` §1.2), so a tool's own signature is the type. Calling by
**function reference** gives a type checker and a language server everything: argument completion,
a wrong or mistyped argument as an error, and a typed result. PEP 612 does it without generated code:

```python
@overload
def call[**P, R](self, tool: Callable[Concatenate[Ctx, P], R], /, *args: P.args, **kwargs: P.kwargs) -> R: ...
@overload
def call(self, tool: str, /, **arguments: Any) -> Any: ...     # the dynamic door, unchanged
```

`Concatenate[Ctx, P]` strips the context parameter; `P` carries the rest; `R` is the return
annotation. This overload goes on `Instance.call` and on the handle's `call` (§4). At run time the
function object resolves to its registered `Tool` (a `fn → Tool` map the decorator fills); a
function registered on a world outside the tree is a `WorldBug`; dispatch is then exactly the
by-name path: validation, the chain, the transaction. A `Tool` built by a factory (`run_sql(...)`)
types the same way once `Tool` is generic in `P` and `R`.

Decided (2026-09-13): **typing by function reference is the model.** What it gives: argument
completion and checking, typed results, and tool discovery through ordinary module completion
(`charges.` lists that resource's tools). What it does not give: `ctx.worlds.payments.create_charge(...)`
attribute style, which needs a per-world handle class; that is the framework's post-V1 generated
per-world client and is not needed for the typing that matters.

Two consequences for the framework (§13):

- **A typed result must be the object the tool returned.** `invoke` currently returns the serialised
  form (`to_jsonable_python`), so a tool returning an `Issue` model would hand in-process callers a
  dict and `R = Issue` would lie. `invoke` serialises inside the transaction to prove the result
  serialises (keeping the framework's rule that an unserialisable result rolls the call back,
  framework architecture §4) and returns the **original object**; the OpenEnv
  layer serialises for the wire. Guidance: tools return pydantic models or TypedDicts, not bare
  dicts, so results complete too.
- **`Ctx` may be parameterised** for the optional `Worlds` sugar below; the registration check
  accepts `Ctx[X]` as the first parameter's annotation.

Two ways to reach a tool from Python, both typed, deliberately different: `get_issue(ctx, key=...)`
calls the function directly with no validation, middleware or transaction, for unit tests of the
body; `inst.call(get_issue, key=...)` and `ctx.worlds.<name>.call(get_issue, key=...)` run the
full path.

**Optional `Worlds` sugar.** `ctx.worlds.<name>` is attribute access on a handle container whose
attribute type is `WorldHandle` for any name; a misspelt child fails at run time (`WorldBug`, unknown
child) and in `seahaven check` (§9). A host that wants the child names checked statically declares
them once and annotates its context:

```python
class CompanyWorlds(seahaven.Worlds):
    stripe: seahaven.WorldHandle
    shopify: seahaven.WorldHandle

@world.tool
def stripe_add_and_assign_invoice(ctx: seahaven.Ctx[CompanyWorlds], customer_id: str, amount: int) -> Invoice: ...
```

`seahaven check` binds the class to the registrations: every annotated attribute must be a
registered `name`, and every registered name is warned about if unannotated. Never required; a
world that skips it loses nothing but the static child-name check.

`ty` is the framework's type checker and must be verified on `Concatenate` + `ParamSpec` overloads
before this section is relied on; pyright and mypy support them fully. If `ty` falls short, a second
checker on world code is the fallback, not generated code.

### 2.5 Illustration

Shapes, not signatures; the repo fixes the Python. Versions come from package metadata.

```python
# stripe_world/world.py: a leaf world, exactly as the framework scaffolds one
import seahaven
world = seahaven.World(name="stripe", version="1.4.0", schema=seahaven.sql_files(__package__, "schema"))

# stripe_world/tools/charges.py
from stripe_world.world import world
from stripe_world.models import Charge

@world.tool
def create_charge(ctx: seahaven.Ctx, amount: int, currency: str = "usd") -> Charge:
    ...
```

```python
# shopify_world/world.py: uses Stripe for checkout, hides it from the agent
import seahaven, stripe_world
world = seahaven.World(name="shopify", version="2.1.0", schema=seahaven.sql_files(__package__, "schema"))
world.add_world(stripe_world.world, name="payments", tool_allow_list=[])

# shopify_world/tools/checkout.py
from shopify_world.world import world
from shopify_world.models import Checkout
from stripe_world.tools import charges        # importing is harmless: its decorators already ran on Stripe's world

@world.tool
def complete_checkout(ctx: seahaven.Ctx, order_id: str) -> Checkout:
    order = ctx.db.one("SELECT id, total FROM orders WHERE id = ?", order_id)            # own store
    charge = ctx.worlds.payments.call(charges.create_charge, amount=order["total"])       # typed; shared or own
    ctx.db.execute("UPDATE orders SET charge_id = ? WHERE id = ?", charge.id, order_id)
    return Checkout(order_id=order_id, charge_id=charge.id)
```

```python
# my_company/world.py: the host. Two Stripe accounts: one shared with Shopify and X, one its own.
import seahaven, stripe_world, shopify_world, x_world
world = seahaven.World(name="my_company", version="0.1.0", schema=seahaven.sql_files(__package__, "schema"))
world.add_world(stripe_world.world,  name="stripe",    tool_prefix="stripe_")              # shared
world.add_world(stripe_world.world,  name="stripe_eu", tool_prefix="eu_", store="eu")      # separate
world.add_world(shopify_world.world, name="shopify",   tool_prefix="shopify_")
world.add_world(x_world.world,       name="x",         tool_prefix="x_")
# Nodes: main, stripe, stripe_eu, shopify, x.  shopify/payments and x/payments are aliases of stripe.

# my_company/tools/billing.py
from my_company.world import world
from my_company.models import Invoice
from stripe_world.tools import invoices

@world.tool
def stripe_add_and_assign_invoice(ctx: seahaven.Ctx, customer_id: str, amount: int) -> Invoice:
    invoice = ctx.worlds.stripe.call(invoices.create_invoice, customer=customer_id, amount=amount)  # hidden or not
    ctx.db.execute("INSERT INTO invoice_owners (invoice_id, owner_id) VALUES (?, ?)",
                   invoice.id, ctx.state["user_id"])
    # Direct SQL on an added world's store is allowed (§4); prefer a tool when one exists.
    ctx.worlds.stripe.db.execute("UPDATE invoices SET metadata = ? WHERE id = ?", '{"source": "agent"}', invoice.id)
    return invoice
```

```python
# in-process, as the framework's own API
with world.instance("acme-medium", seed=7) as inst:
    inst.call("shopify_complete_checkout", order_id="o_1")          # by composite name, untyped
    inst.call(checkout.complete_checkout, order_id="o_1")          # by function, typed; resolved to its node
    inst.inspect().rows("SELECT count(*) FROM stripe.charges")      # every node, schema-qualified
    inst.changes()                                                  # Change.world says which node
```

## 3. Tool surface

### 3.1 What is contributed

An added world contributes its registered tools, filtered by the allow or block list, then renamed
by the prefix. Descriptions, input schemas and everything else in the listing are byte-identical to
the added world's; only the name changes. Nothing in a contributed tool reveals its origin.

The host's own tools and every added world's contributed tools form one flat, insertion-ordered
list, the root's `world.tools`: the host's tools in registration order, then each added world's
contributed tools in `add_world` order, each in the order the added world lists them. `Instance.tools()`
and `ListToolsAction` serve that list. List order is part of what the agent observes; the host is
responsible for it matching the client's real surface, and orders its `add_world` calls
accordingly.

### 3.2 Collisions and validity

After filtering and prefixing, every name in the flat list must be unique across the host and all
added worlds, and must not be one of the framework's reserved names (`reset`, `step`, `state`,
`close`, the control tools). A collision is a registration error, never resolved silently. A
prefixed name must remain a valid tool name for OpenEnv's tool listing; otherwise a registration
error.

### 3.3 Stale descriptions

A prefix does not rewrite description text. If an added world's descriptions mention sibling tools by
name ("call `create_customer` first"), a prefix leaves those mentions stale. `seahaven check` warns
when a prefixed world's descriptions contain the unprefixed name of another contributed tool; the
framework never edits the text. The host either accepts the infidelity or drops the prefix. Decided:
warn, never rewrite.

### 3.4 Serving and dispatch

The OpenEnv environment and the in-process API serve the composite list exactly as they serve a
single world's. A call to a contributed name is routed to the owning node and runs with that node's
`Db`, the shared clock, and that node's id stream and `state`.

**Middleware nests.** An agent call to a contributed tool runs the host's chain outermost, then the
owning world's chain, then the tool: the host can wrap every tool it serves (an access-control
middleware gating the whole surface, a trace), and the added world's own error handler still shapes
its errors as its product would. The framework's scaffolded handler already passes `ToolError`
through, so a host's outermost handler does not re-wrap an added world's product errors. A call
from host code into an added world (§4) runs only the owning world's chain: the host's chain is
already around the host tool making the call.

Control tools (`controller_run_sql`, `controller_changes`) are the root's and are unchanged; their
results cover every node (§6).

## 4. Host code calling added worlds

The reason to compose rather than to configure: the host writes its own tools that use the added
worlds. A host tool `stripe_add_and_assign_invoice` calls Stripe's `create_invoice` and the company
store in one agent-visible call.

`ctx.worlds` is a new member of the framework's `Ctx` (its member list may grow, framework spec §4).
`ctx.worlds.<name>` and `ctx.worlds["name"]` return the handle for the host's child of that name:

| Handle member | What |
|---|---|
| `call(tool, **arguments)` | The tool's function (typed, §2.4) or its own unprefixed name (untyped); full path through the owning world's chain; raises the added world's `ToolError` |
| `db` | That node's `Db`, the same wrapper host code has for its own store, `conn` included |
| `state` | That node's per-instance `dict` (each node has its own `ctx.state`) |
| `worlds` | That node's own children, for a host reaching a grandchild explicitly |

- **Handlers reach added worlds by name through the context on every call and never hold a store or
  world reference.** The framework resolves the name to a node per instance, which is what makes
  sharing by identity work (§2.3). Handles are **call-scoped**: one kept past its call raises
  `WorldBug` on use instead of silently addressing another instance.
- **Instance creation from inside a tool call is a `WorldBug`.** `world.instance(...)` is the
  framework's in-process API and stays on `World`; the guard is a per-thread "in call" flag the
  dispatcher sets, so `stripe_world.world.instance(...)` from a handler fails loudly. `seahaven
  check` flags `.instance(` in `tools/` and `middleware/` (§9). There is no sanctioned way for an
  added world's author to get "their own" store; the only door is the child name.
- The block list and allow list shape the **agent's** surface only. Host code can call every tool of
  an added world, contributed or not. A host composite typically wraps primitives it has hidden from
  the agent.
- Arguments are validated exactly as if the agent had called the tool. Errors arrive as the added
  world's own `ToolError` subclasses, shaped by its own error handler; the host does not re-wrap
  them. An in-process call **raises** rather than returning an error value, so composite code can
  handle it and decide what the agent sees (decided 2026-09-13).
- **One in-flight call.** A call from host code into an added world is part of the same in-flight
  call: it re-enters the instance's `RLock` and **bypasses the concurrency gate** (the framework's
  "tools never call tools through the dispatcher" becomes "except through `ctx.worlds`, which does
  not take the gate"). One instance runs one call at a time, however many nodes that call touches.
- **Transactions are the framework's.** The per-call transaction covers the *called* tool's own
  store. A nested call into an added world is its own transaction on that node's file, committed
  when it returns. Direct SQL from host code on an added world's store autocommits per statement
  unless the host wraps it in `ctx.worlds.<name>.db.transaction()`.
- **No cross-world atomicity.** A composite that writes to Stripe, then fails writing to Slack,
  leaves the Stripe write in place. This is the real behaviour of two separate services and is not
  something the framework papers over. Eval authors writing state checks need to know this.
- The framework's per-call `INFO` log line gains the node path; a nested call logs as its own line
  marked internal, so an eval can tell agent-initiated calls from the calls a composite made.
- Added worlds are unaware of their host and of their siblings. Dependencies point one way. An added
  world cannot call the host or a sibling; if it could, it could not run standalone and "sealed"
  would mean nothing.
- **Host code has full read-write SQL access to every added world's store** (decided 2026-09-13):
  `ctx.worlds.stripe.db`. The host owns the instance; this is a framework and cannot predict every
  use, and world code is trusted (framework spec §1, trust; §4 makes `ctx.db.conn` public for the
  same reason). Direct
  writes bypass the added world's handlers and therefore its invariants (clock stamps, id streams,
  audit rows, FTS triggers); the authoring docs guide the creation agent to prefer the added world's
  tools over direct SQL wherever a tool exists. Guidance, not enforcement.

## 5. State and fixtures

### 5.1 Store

One SQLite file per node, per instance: the root's file plus one per added node, recursively. Each
file carries its own world's DDL and is subject to every framework per-file rule (schema hash,
lint, connection setup, clock UDFs, `busy_timeout=0`). The framework's "one SQLite file and one
connection per instance" becomes one per node; nothing else about a file changes.

Each node has its own connection with the same per-connection setup. A world's `run_sql` sees only
that node's file: isolation between nodes is structural, not an allowlist.

### 5.2 Fixture artifact

A root fixture is a directory holding one frozen SQLite file per node and one `fixture.yaml`. Every
framework rule applies per file: rollback journal, checkpointed, vacuumed, read-only, never opened
except to copy, hash-verified on first copy per process.

The sidecar is the framework's `FixtureMeta` at **`format_version: 2`**: the version-1 fields for
the root (`id`, `world`, `world_version`, `schema_hash`, `now`, `parent_id`, `file_sha256`,
`created_at`, `description`), plus `nodes`, one entry per added node: path, world name, world
version, scope, schema hash, file name, file SHA-256, and the alias routes that reach it. A world with no
added worlds keeps writing version 1; `load` accepts both. There is exactly one `now`; no file has
its own.

### 5.3 Lifecycle

- **Genesis.** `world.instance(None)` on a host yields a blank file for every node, each with its own
  DDL applied, at one clock (wall time or `now=`, the framework's rule). The creation agent fills them
  through the host's tools, the added worlds' tools via `ctx.worlds`, and `inst.bulk()`, whose
  yielded `ctx` reaches every node through `ctx.worlds.<name>.db`; `bulk` opens one transaction per
  node and commits them in sequence on exit. Every node in a composite fixture is built in one
  instance at one `now`; there is no path that combines data frozen at different times. This is the
  point of "added worlds always start empty" (overview).
- **Startup hooks** run for every world in the tree, tree order (root first, then each added world
  in `add_world` order, recursively), each with its own node's `ctx`. `reset()` keyword arguments
  beyond `fixture`, `seed` and `now` are **broadcast**: every hook in the tree that names a keyword
  receives it (the framework's per-hook filtering, applied across the tree); the union of accepted
  names across the tree is what an unknown argument is checked against. Keywords bound on the edge
  with `add_world(startup=...)` are delivered to that node's hooks first and are **fixed**: a
  `reset()` keyword of the same name still reaches every other hook that names it, but does not
  override the bound value on that node, because bound keywords are configuration, part of the
  composition, and an eval must not be able to change one node's configuration by accident. A host
  that wants a per-instance value on a node forwards it from its own hook (below). The changeset
  sessions are attached after all hooks have run, as the framework does for one world.
- **The root's hooks run first and see every node.** A root hook may hand a child exactly what it
  should have, renamed or transformed, through `ctx.worlds.<name>.state`, before that child's own
  hooks run; the child reads `ctx.state` and never learns the host's keyword names. This is how a
  per-instance value reaches one node without a broadcast keyword.
- **Freeze.** Every node's file is conformance-checked against its own world's DDL (each world
  checks its own), `VACUUM INTO`'d, hashed. All or nothing: if any node fails, no fixture is minted
  and the error names the path.
- **Create.** Every file's hash is verified; every node's schema hash in the sidecar is checked
  against the loaded host's composition; and the sidecar's set of paths and aliases must equal the
  host's current composition exactly. Any mismatch refuses creation with a `WorldBug` naming the
  path and what changed (hash, node added, node removed, sharing changed): the framework's "this
  fixture needs regenerating" behaviour, per node; a changed scope shows up as a changed node set. A
  world version that differs from the sidecar's
  while its schema hash matches is reported, never refused (§7).
- **Fork.** Create from a composite fixture, change, freeze. Unchanged. `parent_id` is the composite
  fixture's.
- **Destroy, sweep, working directory.** Unchanged; an instance directory holds N files.
- **`seahaven fixture freeze --run`** and the pytest plugin's `instance` fixture work on a composite
  root with no change: the callable receives the composite `Instance`.

### 5.4 Added worlds' own fixtures

Unreachable from a host. `world.fixtures()` on a host lists the host's fixtures only; there is no
path that reads, copies or references an added world's fixture from a composite instance. Decided
(overview): letting a host import a Stripe file frozen in 2021 beside a Resend file frozen in 2026
is the footgun this rule exists to remove.

### 5.5 Clock and ids

- One instance clock, shared by every node. Every connection gets the same clock UDFs; every time
  tool in every added world reads it. The frozen clock, and any later clock model, applies to the
  whole composite.
- One instance seed, derived as the framework derives it. Each node draws its ids from its own
  `Ids` seeded from the instance seed and its canonical path, with the root's derivation unchanged.
  Consequence: adding or removing a node never perturbs the ids of the root or of any other node,
  and the same fixture and seed produce identical ids in every node across runs, for worlds that use
  `ctx.ids`.

## 6. Eval-facing surfaces

### 6.1 Inspection

`inst.inspect()` returns the framework's read-only `Db`, opened on the root's file with every added
node's file `ATTACH`ed read-only under a schema named by its path with segments joined by `__`
(`stripe.charges`, `stripe__tax.rates`). Same clock UDFs, same permanent write-denying authorizer.
An eval asking "was the invoice created and was the message posted" is one SQL statement.
`controller_run_sql` runs on this connection and therefore sees every node, schema-qualified.

This is the one place files are combined, and it is safe because it is read-only and never a world
tool. A world's `run_sql` never sees a sibling node's tables (§5.1).

Bound: SQLite's attached-database limit. The SQLite that apsw bundles sets it at 125 (measured on
apsw 3.53.4, for both the runtime default and the compile-time maximum); the architecture probes the
installed value rather than assuming it. Exceeding it is a registration error when the host loads,
not a surprise at instance creation.

### 6.2 Changes

`Change` gains a `world` field: the path of the node the change belongs to, `main` for the root.
`inst.changes()` and `controller_changes` return one list covering every node, in the framework's
shape otherwise. Untracked tables and FTS5 shadow tables are excluded per node, from each world's
own declaration.

### 6.3 Composition report

The instance reports its composition (nodes with paths, world names and versions, store keys and
aliases) through the in-process API and, over OpenEnv, in `state`, so an eval can tell what it is
running against. The agent-facing surface never does.

## 7. Versioning

Worlds are Python packages. A world depends on the worlds it adds as ordinary package dependencies
with compatible version ranges; the host's lockfile resolves exactly one installed version of each
per environment. Decided 2026-09-13: the framework behaves like every other dependency tree. It has
no version check of its own and needs none, because one package cannot be installed at two versions
in one environment; every route to a world in one tree runs the same code by construction. A
bug-fix release of an added world that keeps its DDL flows through a lock update with no framework
involvement.

What the framework checks is the **schema hash**, per node, at create (§5.3), exactly as it does for
one world: a release that changes a world's schema invalidates every fixture holding that node's
store, whatever its version number says, and a release that does not keeps them valid. The sidecar
records the installed version of every node for the composition report; a version difference with an
unchanged schema is reported, never refused. No migrations of shipped fixtures, composite or
otherwise (the framework's rule).

## 8. Faithfulness

A composite has no single real product to be faithful to; the real client's agent surface is also an
assembly. Faithfulness is therefore per world: each added world is faithful to its vendor (its own
tests), and the host is faithful to the client's actual surface: its own tools, its composite tools,
and the names, filtering and order it declares for the added worlds. Prefixes and lists are the
host's statement of what the client's agent really sees; getting them wrong is the host's
infidelity, not the added world's. Prefix-stale descriptions (§3.3) are a known infidelity the host
accepts or removes.

## 9. Registration errors, check-time errors, warnings

**Registration errors** (`WorldBug` at import, like every other registration failure):

- `tool_allow_list` and `tool_block_list` both given; a list naming a tool the added world does not
  have
- Duplicate `name` on one host; an invalid or reserved `name`
- A `startup` keyword the added world's hooks do not accept; the same `startup` keyword bound to
  two different values on two routes to one node (same world, same scope)
- A tool name collision after prefixing, including the framework's reserved names
- A world appearing in its own subtree
- More nodes than the inspection connection can attach

**`seahaven check` rules** (stable codes, named fixes, in the framework's style):

- `.instance(` called in a module under `tools/` or `middleware/` (§4)
- A literal `ctx.worlds["..."]` key or `ctx.worlds.<attr>` that is not a registered child name
- A `Worlds` class whose annotated attributes do not match the registered names (§2.4); a registered
  name with no annotation, when a `Worlds` class is declared (warning)
- Every node's file hash, schema hash and the composition recorded in each fixture's sidecar
  (the framework's fixture rules, per node)

**Warnings:** prefix-stale descriptions (§3.3); one underlying tool contributed under two names
through a shared node (§2.3).

## 10. Runtime errors

- Create: file hash mismatch, schema hash mismatch, composition mismatch (paths, aliases or store
  keys). Each names the path. `WorldBug`, as today.
- Freeze: conformance failure names the path; nothing is minted.
- A handle used after its call returned; instance creation attempted from inside a tool call; a
  function passed to `call` that is registered on a world outside the tree; an unknown child name.
  All `WorldBug`.
- Tool calls: unchanged. An error from an added world reaches the agent in that world's own shape.
  A composite tool decides what the agent sees when a nested call fails, and the log shows both.

## 11. Constraints

- An idle composite instance is N files and N connections. Cost scales with the tree size; the
  design target is unchanged (hundreds of concurrent instances, minute-long lifetimes) with small N.
  The framework's benchmark measures one node per instance and reads as a per-node floor.
- No process-wide mutable state, in any world (the framework's rule). Two nodes of one world in a
  tree, and one world object reached by two roots in one process, are fully independent because a
  `World` object carries no state.
- Attach limit (§6.1).
- No cross-world atomicity (§4).

## 12. Non-goals

- Importing or referencing an added world's own fixtures (§5.4).
- Cross-node SQL for the agent. Joins across nodes exist only on the eval's inspection handle.
- Modifying an added world's code: schema, handlers, tool definitions, descriptions, middleware.
  Fork the package if the client's copy of the product differs; composite tools in the host, with
  direct store access, cover most customisation.
- Merging DDL. An added world's schema is never folded into the host's file; that is the extension
  pattern (framework spec §21, item 4) and it is for code written to be included, not for
  independently authored worlds whose SQL names its own tables.
- Merging nodes the host did not declare shared. Two store keys are two stores; the framework never
  merges them, and a host never reaches into an added world's declaration to redirect its children.
- Dynamic composition at `reset()`. Migrations of composite fixtures. Attribute-style typed handles
  (the framework's post-V1 generated client).
- Seeding helpers. A world may ship plain helper code that fills a blank instance of itself, and a
  host may call it during genesis; nothing in the framework knows about it. P3.
- Author-time vendoring (overview, option 3). Not pursued.
- Exact Python signatures, file naming inside fixture and working directories, SQLite build flags.
  The repo's, with the code.

## 13. Changes this spec asks of the framework

Each is small and additive to the framework as specified; none reopens a decision there.

| Change | Where | Why |
|---|---|---|
| `world.add_world(...)`, a fourth registration verb, with the parameters of §2.1 | `world.py` | The declaration |
| `Ctx.worlds`, the handle container; `WorldHandle` with `call`, `db`, `state`, `worlds`; per-node `Ctx` and `state` | `ctx.py`, `instances.py` | §4 |
| Per-node startup keywords bound with `add_world(startup=...)`, delivered before broadcast `reset()` keywords and not overridden by them; root hooks run first | `instances.py` | §2.1, §5.3 |
| `call` overloads on `Instance` and `WorldHandle`: function reference (PEP 612) and name; a `fn → Tool` map filled by the decorator; `Tool` generic in `P`, `R` | `instances.py`, `tool.py` | §2.4 |
| `invoke` returns the tool's original object after proving it serialises; the OpenEnv layer serialises for the wire | `call.py`, `openenv/env.py` | Typed results are honest (§2.4) |
| The registration check accepts `Ctx[X]` as the first parameter's annotation; optional `seahaven.Worlds` | `tool.py` | Optional static child names (§2.4) |
| Nested calls re-enter the `RLock` and bypass the gate; the in-call flag that makes `world.instance()` a `WorldBug` from a handler | `instances.py` | §4 |
| Nested middleware chains: host outermost, owning world inside | `call.py` | §3.4 |
| `FixtureMeta` version 2 with `nodes`; `load` accepts 1 and 2; freeze and create per node | `fixtures.py`, `instances.py` | §5 |
| `open_inspection` attaches every node read-only under its schema name | `db.py` | §6.1 |
| `Change.world` | `changes.py` | §6.2 |
| Composition in the instance report and OpenEnv `state` | `instances.py`, `openenv/env.py` | §6.3 |
| The per-call log line carries the node path; nested calls log as internal | `instances.py` | §4 |
| The `check` rules and warnings of §9 | `lint/` | §9 |
| Verify `ty` on `Concatenate` + `ParamSpec` overloads before relying on §2.4 | CI | §2.4 |

Requirements handed elsewhere:

- **Authoring docs**: prefer an added world's tools over direct SQL on its store (§4); declare
  prefixes and lists to match the client's real surface (§8); a world must not assume it is the only
  writer to a world it adds, since sharing is the default (§2.3); return models, not bare dicts, so
  typed calls complete (§2.4).
- **Allocation**: the mechanism is framework core, post-V1; the simulated third-party SaaS worlds
  are private (§0).

## 14. Proposed README section

For the framework's top-level README, once composition ships. Public voice; names nothing private.

> ### Composing worlds
>
> A world can add other worlds. Build a Stripe-like world once, a Slack-like world once, and a
> company world that adds both, plus its own tables and tools. The agent sees one tool list; the
> company world's own tools can call the added worlds' tools in-process; evals inspect every store
> with one SQL connection.
>
> ```python
> # my_company/world.py
> import seahaven, stripe_world, slack_world
>
> world = seahaven.World(name="my_company", version="0.1.0",
>                        schema=seahaven.sql_files(__package__, "schema"))
> world.add_world(stripe_world.world, name="stripe", tool_prefix="stripe_")
> world.add_world(slack_world.world,  name="slack",  tool_prefix="slack_", tool_allow_list=["post_message"])
>
> # my_company/tools/billing.py
> from my_company.world import world
> from stripe_world.tools import invoices
>
> @world.tool
> def invoice_and_notify(ctx: seahaven.Ctx, customer_id: str, amount: int) -> dict:
>     invoice = ctx.worlds.stripe.call(invoices.create_invoice, customer=customer_id, amount=amount)
>     ctx.worlds.slack.call("post_message", channel="#billing", text=f"Invoiced {invoice.id}")
>     return {"invoice_id": invoice.id}
> ```
>
> If several added worlds contain a Stripe world, they share one store by default, so a charge
> created through one is visible to the others, as it would be with one real account. Override it
> when it should not be shared:
>
> ```python
> world.add_world(stripe_world.world, name="stripe_eu", tool_prefix="eu_", store="eu")
> ```
>
> Each added world keeps its own SQLite file and its own tools; fixtures are frozen and forked at
> the top level, with every store inside.

No open questions remain as of 2026-09-13.
