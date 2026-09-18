# Composing worlds

A world can **add other worlds**. Build a payments world once, a chat world once, and a company
world that adds both plus its own tables and tools. The agent sees one flat tool list and cannot
tell the world was assembled. The company world's own tools call the added worlds' tools in process.
An eval inspects every store through one SQL connection.

This page is for the author of a world that adds others. Nothing here changes a world that adds
none. A world with no added worlds is a composition of exactly one **node**, meaning one store, and
every mechanism below runs for it unchanged: one middleware chain, a `format_version: 1` fixture,
and every change-log record saying `main`. There is no second code path, which is why every rule on
every other page still holds.

| Section | What it covers |
|---|---|
| [Declaring](#declaring) | `add_world`, its parameters, and what is checked when |
| [The tool surface](#the-tool-surface) | What the agent sees, and the rules the list has to satisfy |
| [Nodes, scopes and sharing](#nodes-scopes-and-sharing) | How two worlds end up on one store |
| [Reaching an added world](#reaching-an-added-world) | `ctx.worlds`, and six rules about handles |
| [Middleware](#middleware) | Which chains a call descends |
| [Startup hooks](#startup-hooks) | Broadcasting and binding keyword arguments |
| [One instance, many stores](#one-instance-many-stores) | Files, clocks, seeds and isolation |
| [Fixtures](#fixtures) | The version-2 sidecar, and what freeze and create check |
| [What an eval sees](#what-an-eval-sees) | Inspection, the change log and the composition report |
| [Typed access](#typed-access) | Calling a tool by its function |
| [What `seahaven check` adds](#what-seahaven-check-adds) | Eight codes |
| [Limits](#limits) | The hard cap, the costs, and what is deliberately absent |

```python
import seahaven

# In a real project each of these is its own package, and the host does
# `import payments_world` and adds `payments_world.world`. One file here so the
# example runs.
payments = seahaven.World(
    name="payments",
    version="1.0.0",
    schema="CREATE TABLE charges (id TEXT PRIMARY KEY, amount INTEGER NOT NULL) STRICT;",
    state_format="seahaven.state/1",
)


@payments.tool
def create_charge(ctx: seahaven.Ctx, amount: int) -> dict[str, object]:
    """Charge the account and return the charge."""
    charge = {"id": ctx.ids.uuid(), "amount": amount}
    ctx.db.execute("INSERT INTO charges (id, amount) VALUES (?, ?)", charge["id"], charge["amount"])
    return charge


company = seahaven.World(
    name="company",
    version="0.1.0",
    schema="CREATE TABLE invoices (id TEXT PRIMARY KEY, charge_id TEXT NOT NULL) STRICT;",
    state_format="seahaven.state/1",
)
company.add_world(payments, name="payments", tool_prefix="pay_")


@company.tool
def invoice(ctx: seahaven.Ctx, amount: int) -> dict[str, object]:
    """Charge the company's payment account and file an invoice against the charge."""
    charge = ctx.worlds.payments.call(create_charge, amount=amount)
    filed = {"id": ctx.ids.uuid(), "charge_id": charge["id"]}
    ctx.db.execute(
        "INSERT INTO invoices (id, charge_id) VALUES (?, ?)", filed["id"], filed["charge_id"]
    )
    return filed


with company.instance(now="2026-06-01T09:00:00.000Z") as inst:
    # One flat list: the host's own tools, then what each added world contributes.
    assert [listed["name"] for listed in inst.tools()] == ["invoice", "pay_create_charge"]
    inst.call("invoice", amount=500)

    # Two files, one read-only connection: the added store is attached under its path.
    joined = inst.inspect().one(
        "SELECT invoices.id, charges.amount FROM invoices"
        " JOIN payments.charges AS charges ON charges.id = invoices.charge_id"
    )
    assert joined is not None and joined["amount"] == 500

    # One change log, and every record says which store it came from.
    assert {(record.world, record.table) for record in inst.change_log()} == {
        ("main", "invoices"),
        ("payments", "charges"),
    }
```

That example is the whole feature. There is one new registration verb, one new handle on `ctx`, and
everything else — the tool list, the change log, the inspection connection, the fixture — gains a
node dimension it did not have before.

## Declaring

`world.add_world(...)` is the fourth registration verb, beside `tool`, `middleware` and
`instance_startup`. It is call-only, because there is nothing to decorate, and it goes in `world.py`
beside the `World(...)`, or in the package `__init__` with the other imported registrations.

```py
import payments_world

import seahaven

world = seahaven.World(name="company", version="0.1.0", schema=..., state_format="seahaven.state/1")
world.add_world(payments_world.world, name="payments", tool_prefix="pay_")
```

| Parameter | What it does | Default |
|---|---|---|
| first positional | The added world's `World` object, normally the `world` its package exports | required |
| `name` | This host's internal identity for the added world: a path segment, a file name and a schema name. `^[a-z][a-z0-9_]*$`, no `__`, never `main` or `temp`. Never agent-visible | the added world's own `name` |
| `store` | The **account scope** this world and its whole subtree belong to. `None` keeps the adder's own scope, which is what shares an account; a string opens the named one | `None` |
| `tool_prefix` | Prepended to every contributed tool name: `"pay_"` publishes `create_charge` as `pay_create_charge` | none; names pass through |
| `tool_allow_list` | Only these tools are contributed to the agent's surface | all of them |
| `tool_block_list` | Every tool except these | none blocked |
| `startup` | Keyword arguments bound to *this node's* startup hooks at every instance creation | none |

The added world is **sealed as code**: a host cannot change its schema, its tools, its descriptions
or its middleware. Fork the package when the client's copy of the product really differs. It is
**open as state**: the host owns the instance and may read and write that store directly.

The lists and the prefix are not a projection layer. They are world code, written by the host's
author, declaring which of a dependency's tools are the client's real agent surface and under what
names. That is the same act as writing a tool list, fixed before any instance exists, and part of
what the host is faithful to. A harness that wants a different shape still reshapes the composite
list on top, as it would for any world.

### What is checked when, and why

Two things happen at the `add_world` line, and the rest waits.

**At the call**, Seahaven checks what is knowable from the two `World` objects in hand: the name's
spelling and its uniqueness on this host, `store` being a scope name rather than a blank string, the
two lists not both being given (and neither being a bare string, which would name one tool per
character), every `startup` keyword being one the added world's *own* hooks accept, and the added
world's tree not containing this one. Each raises `seahaven.WorldBug` from the line you can read it
on.

**At the first use of the tree** — `world.instance(...)`, `inst.tools()`, a call, a freeze, or
`seahaven check` — Seahaven checks everything that is a property of the *whole* tree: a list naming
a tool the added world does not contribute, two routes producing one tool name, a contributed name
that is reserved or is not a valid tool name, one `startup` key bound to two values on one node, and
more added stores than SQLite can attach.

The second group cannot be raised at the `add_world` line, and the reason is worth knowing. A host's
own tools are registered by the module imports in its `__init__.py`, which run *after* `world.py`
has executed `add_world`, so at that line the host's tool registry is usually empty. A `World` also
holds no reference to the worlds that added it, so a tool registered on a dependency later can never
be pushed back to its hosts. The tree is therefore sealed lazily, cached, and sealed again after any
registration anywhere in the process.

The practical consequence is one line in your workflow: **`seahaven check` seals as its first act**,
so every one of those failures is an `SH504` finding naming the `add_world` behind it, rather than a
traceback out of the first instance. Run it before every commit, as you would anyway.

## The tool surface

An added world contributes what *it* contributes: its own registered tools, and what the worlds it
adds contribute to it. That set is filtered by the allow or block list, then renamed by the prefix.
The lists and the prefix therefore apply to a whole subtree as one list. A name in a list is the
name the added world contributes under, with prefixes applied deeper included (`b_deep`, not
`deep`), and this host's prefix goes on top of it (`a_b_deep`). Everything else in the listing — the
description, the input schema — is **byte-identical** to the added world's. Only the name changes,
and nothing in a contributed tool reveals where it came from.

The composite list is flat and insertion-ordered: the host's own tools in registration order, then
each added world's contribution in `add_world` order, each in the order that world lists them.
`inst.tools()` and OpenEnv's tool listing both serve that list. **Order is part of what the agent
observes**, so a host that cares orders its `add_world` calls to match the client's real surface.

Three rules fall out of that:

- After filtering and prefixing, every name must be unique across the whole tree, and must not be
  one of the reserved names (`reset`, `step`, `state`, `close`, and the control tool). A
  collision is a seal error, never resolved silently.
- A prefixed name must still be a valid tool name (`^[A-Za-z0-9_-]{1,128}$`), so a `tool_prefix` of
  `"payments."` is refused rather than published.
- Control tools are never contributed. Every world has them, but a call by that name resolves on the
  root's own registry, and the root's cover every node.

**A prefix does not rewrite description text.** If an added world's descriptions cross-reference its
own tools — "call `create_customer` first" — a prefix leaves those mentions naming something the
agent cannot call. `seahaven check` warns (`SH206`), and Seahaven never edits the text, because the
description is the added world's statement about its own product. Drop the prefix, or accept the
infidelity. Those are the two answers.

## Nodes, scopes and sharing

A **node** is one store in a composite instance, identified by `(World object, scope)`: Python
object identity plus one string. The same pair anywhere in one root's tree is one store; a different
pair is a different store.

That rule matches the way a real company works. A company has *one* payments account: its
shop writes charges to it, another integration reads them, and the agent's own payments tools see
them too. Two worlds that both add the same payments package therefore land on the same node by
default, because a package's module-level `world` object is shared by everything that imports it.

A `store=` string opens a named scope for that world **and everything it adds**, recursively. Scope
names are flat and global, so two hosts that both write `store="eu"` for the same world share that
store too.

```python
import seahaven

payments = seahaven.World(
    name="payments",
    version="1.0.0",
    schema="CREATE TABLE charges (id TEXT PRIMARY KEY, amount INTEGER NOT NULL) STRICT;",
    state_format="seahaven.state/1",
)


@payments.tool
def create_charge(ctx: seahaven.Ctx, amount: int) -> dict[str, object]:
    """Charge the account and return the charge."""
    charge = {"id": ctx.ids.uuid(), "amount": amount}
    ctx.db.execute("INSERT INTO charges (id, amount) VALUES (?, ?)", charge["id"], charge["amount"])
    return charge


shop = seahaven.World(
    name="shop",
    version="1.0.0",
    schema="CREATE TABLE orders (id TEXT PRIMARY KEY, total INTEGER NOT NULL) STRICT;",
    state_format="seahaven.state/1",
)
# The shop charges through payments and hides it from the agent entirely.
shop.add_world(payments, tool_allow_list=[])


@shop.tool
def place_order(ctx: seahaven.Ctx, total: int) -> dict[str, object]:
    """Place an order and charge for it."""
    charge = ctx.worlds.payments.call(create_charge, amount=total)
    order = {"id": ctx.ids.uuid(), "total": total}
    ctx.db.execute("INSERT INTO orders (id, total) VALUES (?, ?)", order["id"], order["total"])
    return {"order_id": order["id"], "charge_id": charge["id"]}


company = seahaven.World(
    name="company",
    version="0.1.0",
    schema="CREATE TABLE staff (id TEXT PRIMARY KEY) STRICT;",
    state_format="seahaven.state/1",
)
company.add_world(payments, name="payments", tool_prefix="pay_")
company.add_world(shop, name="shop", tool_prefix="shop_")
company.add_world(payments, name="payments_eu", store="eu", tool_prefix="eu_")

with company.instance() as inst:
    nodes = {report.path: report for report in inst.composition()}
    # Four stores, not five: the shop's payments is the company's own account.
    assert sorted(nodes) == ["main", "payments", "payments_eu", "shop"]
    assert nodes["payments"].aliases == ("shop/payments",)
    assert nodes["payments"].scope is None
    assert nodes["payments_eu"].scope == "eu"

    inst.call("shop_place_order", total=500)
    inst.call("pay_create_charge", amount=20)
    inst.call("eu_create_charge", amount=7)

    view = inst.inspect()
    charges = view.rows("SELECT amount FROM payments.charges ORDER BY amount")
    assert [row["amount"] for row in charges] == [20, 500]
    assert [row["amount"] for row in view.rows("SELECT amount FROM payments_eu.charges")] == [7]
```

**Paths and aliases.** A node has one **canonical path**: the shallowest route to it, with ties
broken by registration order. The root's path is `main`, a child of the root is `<name>`, and deeper
is `<parent path>/<name>`. Every other route that reaches the node is an **alias**, recorded as a
string like `shop/payments` above. Aliases are not nodes. Nothing is created for them, and sharing
*reduces* the number of stores.

One node is one file, one connection, one attached schema, one change-log path, one id stream and
one entry in a fixture's sidecar.

Two things about scopes are worth knowing before they surprise you. **Scoping is coarse**: a
`store=` scopes a whole subtree, so "the merchant's payments but the company's chat, under one shop"
is not expressible. The only finer tool would be a host redirecting another world's children, which
is deliberately not possible. And **scope names are one namespace across the whole tree**,
dependencies' internals included: a `store="eu"` written inside a package you never read shares with
your own `store="eu"`. A world that wants an account nobody reaches by accident names its scope
after itself.

Node counts multiply with scopes. A world added under three scopes, with four worlds in its own
subtree, is twelve nodes and twelve files. That is the honest count, and it is why the attach bound
below binds sooner than a tree looks like it should.

## Reaching an added world

The reason to compose rather than to configure is that the host writes its own tools that *use* the
added worlds: one agent-visible `invoice` call that charges the payment account and files the
invoice in the company store.

`ctx.worlds.<name>` — or `ctx.worlds["name"]`, for a name held in a variable — answers a
`WorldHandle` for that child:

| Handle member | What it is |
|---|---|
| `call(tool, **arguments)` | run one tool of that node: the added world's own **unprefixed** name, or the function itself. Full validation, the owning world's chain, its transaction |
| `db` | that node's `Db` — the same wrapper you have for your own store, `conn` included |
| `state` | that node's own `ctx.state` dict |
| `worlds` | that node's own children, for reaching a grandchild explicitly |

Six rules govern handles, and each one exists for a failure that is otherwise silent.

- **Handles are call-scoped.** A handle belongs to one *activation* of the instance — the outermost
  call, or the `bulk()` block, that was running when the handle was made — and it dies the moment
  that activation ends, not when some later one begins. Using it after that raises, rather than
  quietly addressing another instance, and a handle carried to another thread is stale for the same
  reason. That is what lets Seahaven resolve a name to a different node in a different instance,
  which is how sharing works at all. A handle stays valid across a nested `handle.call`, and across
  an `inst.call(...)` made from inside a `bulk()` block, because neither opens a new activation.
- **The lists shape the agent's surface only.** Host code can call every tool of a world it adds,
  contributed or not. Wrapping primitives you have hidden from the agent is the usual pattern.
- **Errors raise.** The added world's own `ToolError` subclass, shaped by its own error handler,
  propagates into your tool, which decides what the agent sees. Nothing re-wraps it.
- **A nested call is part of the same in-flight call.** It re-enters the instance's lock and does
  not take the concurrency gate. One instance runs one call at a time, however many stores that call
  touches.
- **There is no cross-world atomicity.** The nested call's transaction is on the child's connection
  and commits when it returns, so a later failure in your tool rolls back only your store. That is
  what two real services do, and Seahaven does not paper over it. Eval authors writing state checks
  need to know it.
- **Never make an instance from inside a call.** `world.instance(...)` raises a `WorldBug` there,
  and `seahaven check` reports the line as `SH208`. The child name is the only door.

```python
from typing import Any

import seahaven

payments = seahaven.World(
    name="payments",
    version="1.0.0",
    schema="CREATE TABLE charges (id TEXT PRIMARY KEY, amount INTEGER NOT NULL) STRICT;",
    state_format="seahaven.state/1",
)
company = seahaven.World(
    name="company",
    version="0.1.0",
    schema="CREATE TABLE staff (id TEXT PRIMARY KEY) STRICT;",
    state_format="seahaven.state/1",
)
company.add_world(payments, name="payments")

kept: Any = None


@company.tool
def keep_the_handle(ctx: seahaven.Ctx) -> dict[str, str]:
    """The one thing a host tool must not do: hold a handle past its call."""
    global kept
    kept = ctx.worlds.payments
    return {"held": "yes"}


with company.instance() as inst:
    inst.call("keep_the_handle")
    try:
        kept.db.rows("SELECT * FROM charges")
    except seahaven.WorldBug as error:
        assert str(error) == "a world handle was used after the call it belongs to returned"
    else:
        raise AssertionError("a handle kept past its call must not work")
```

**Direct SQL is allowed, and it is the second-best answer.** `ctx.worlds.payments.db.execute(...)`
works, because the host owns the instance and a framework cannot predict every use. But a direct
write bypasses the added world's handlers and therefore its invariants — clock stamps, id streams,
audit rows, FTS triggers — so **prefer that world's tools wherever a tool exists**, and keep raw SQL
for what no tool covers. Outside a `db.transaction()` block, statements there autocommit one by one.

## Middleware

An agent call to a contributed tool descends every world's middleware along the canonical route from
the root to the owning node, outermost first: the host's, then any world in between, then the owning
world's, and then the tool. The host can wrap every tool it serves, with an access-control layer
gating the whole surface or a trace, and the added world's own error handler still shapes its errors
as its product would. The scaffolded handler passes `ToolError` through, so a host's outermost
handler does not re-wrap an added world's product errors.

**Each layer runs with its own world's context.** A host middleware sees the host's `db`, `state`,
`ids` and `worlds`. The owning world's middleware and the tool see the owning node's. Without that
rule, a host gate reading `ctx.state["principal"]`, set by the host's own startup hook, would find
the added world's empty state instead. `call` is the same object in every layer, and `call.node`
names the owning node's path, so a host layer knows what is being called without touching a foreign
store. If it needs that store, it has `ctx.worlds`, the same door a host tool has.

A call made from host code through a handle runs **only the owning world's** chain, because the
host's is already wrapped around the host tool making the call.

The per-call log line carries `node=<path>`, and a nested call logs its own line marked
`internal=true`, so an eval can tell agent-initiated calls from the calls a composite made on their
behalf.

## Startup hooks

Hooks run for every world in the tree, depth-first from the root: each node's own hooks, then its
added worlds in `add_world` order, each with its own node's context. Each node's hooks run **once**,
however many routes reach it. Every node's transaction is open before the first hook runs, which is
what lets a root hook write into a child's store — through `ctx.worlds.<name>.db` and
`ctx.worlds.<name>.state` — before that child's own hooks run. A hook that raises rolls all of them
back, and no instance is left behind.

`reset()` keyword arguments beyond `fixture`, `seed` and `now` are **broadcast**: every hook in the
tree that names a keyword receives it, and an unknown argument is checked against the union of names
across the whole tree, before any file is touched. Keywords bound with `add_world(startup=...)` are
**configuration**: they reach that node's hooks, and a `reset()` keyword of the same name does not
override them, because an eval must not be able to reconfigure one node of a tree by accident.

```python
import seahaven

payments = seahaven.World(
    name="payments",
    version="1.0.0",
    schema="CREATE TABLE charges (id TEXT PRIMARY KEY, amount INTEGER NOT NULL) STRICT;",
    state_format="seahaven.state/1",
)


@payments.instance_startup
def configure(ctx: seahaven.Ctx, *, region: str = "us", plan: str = "free") -> None:
    """What account this store is."""
    ctx.state["region"] = region
    ctx.state["plan"] = plan


company = seahaven.World(
    name="company",
    version="0.1.0",
    schema="CREATE TABLE staff (id TEXT PRIMARY KEY) STRICT;",
    state_format="seahaven.state/1",
)
company.add_world(payments, name="payments")
company.add_world(payments, name="payments_eu", store="eu", startup={"region": "eu"})


@company.tool
def accounts(ctx: seahaven.Ctx) -> dict[str, str]:
    """What each payments account was configured as."""
    return {
        "payments": ctx.worlds.payments.state["region"],
        "payments_eu": ctx.worlds.payments_eu.state["region"],
        "plan": ctx.worlds.payments.state["plan"],
    }


with company.instance(region="uk", plan="enterprise") as inst:
    # `region` is broadcast and reaches both hooks; the bound `region` on the EU
    # node wins there and nowhere else. `plan` is broadcast and bound nowhere.
    assert inst.call("accounts") == {
        "payments": "uk",
        "payments_eu": "eu",
        "plan": "enterprise",
    }
```

A host that wants a per-instance value on one node rather than a fixed one forwards it from its own
hook, through `ctx.worlds.<name>.state`. The child reads `ctx.state` and never learns the host's
keyword names.

## One instance, many stores

An instance of a composite is one SQLite file and one connection per node, in one instance
directory. The root's file is `state.sqlite`, as it has always been, and an added node's is
`state.<path>.sqlite` with `/` replaced by `__`: `state.payments.sqlite`,
`state.payments__tax.sqlite`. Every file carries its own world's schema and gets every per-file rule
Seahaven has.

- **One clock** for the whole instance. Every connection gets the same overrides, so every time tool
  in every added world reads the same instant.
- **One seed**, and each node draws ids from its own stream, salted with its canonical path. Adding
  or removing a node never perturbs another node's ids, and the same fixture and seed reproduce
  every node. SQL's `random()` and `randomblob()` are seeded per node from the same salt, so a
  `DEFAULT (randomblob(8))` in one added world's schema replays and is not the stream any other
  node, or any `ctx.ids`, draws from. The root's stream is the instance seed untouched, which is why
  a world that adds nothing mints exactly what it always did.
- **`inst.db`, `inst.state_path` and the `ctx` a tool of the root receives are the root's.** An
  added node's store is reached through `ctx.worlds`, never from the instance.
- **Isolation between nodes is structural.** A world's `ctx.db` is one file, so a world's own
  `run_sql` helper cannot see a sibling's tables. There is no allowlist to get wrong.

`inst.bulk()` yields the root's context with a live `ctx.worlds`, and opens one transaction per node
and commits them in sequence, so a fixture generator fills every store in one block.

## Fixtures

A composite fixture is one directory holding one frozen SQLite file per node and one `fixture.yaml`
at `format_version: 2`. It carries the version-1 fields describing the root exactly as before, plus
a `nodes` list with one entry per added node.

```yaml
format_version: 2
id: acme
world: company
world_version: 0.1.0
schema_hash: 6f1c...
now: '2026-06-01T09:00:00.000Z'
parent_id: null
file_sha256: 4a2b...
created_at: '2026-09-14T08:11:06.580Z'
description: One person, one charge.
nodes:
  - path: payments
    world: payments
    world_version: 1.0.0
    schema_hash: 92d2...
    scope: null
    file: state.payments.sqlite
    file_sha256: 0b80...
    aliases:
      - shop/payments
```

A world that adds nothing keeps writing `format_version: 1` with no `nodes` key at all, so its
fixture directory is byte for byte what it was before composition existed, and every fixture already
on disk keeps loading. There is exactly one `now`, because no store has a clock of its own. A node's
`file` is a plain file name, held to the same rule as a world's name and a fixture id but with no
length limit, since Seahaven mints it from the node's path rather than accepting it. A sidecar that
spells anything else — a separator, a `..`, a drive letter, a character outside the name charset —
is refused when it is read, because both readers join it onto the fixture directory and follow the
result, and the sidecar supplies the hash too.

- **Genesis** yields a blank file per node, each with its own schema, at one clock. Fill them
  through the host's tools, the added worlds' tools, and `inst.bulk()`. Every node of a composite
  fixture is built in one instance at one instant. There is no path that combines stores frozen at
  different times.
- **Freeze** checks *every* node against its own world's schema before it writes anything, then
  vacuums, hashes and publishes atomically. All or nothing: a drifted third node mints nothing, and
  the error names the path.
- **Create** verifies every file's hash, checks every node's schema hash, and requires the sidecar's
  set of nodes, their scopes and their alias edges to equal what the world resolves to now. A
  mismatch refuses creation with a `WorldBug` naming what changed: node added, node removed, shape
  changed, schema drifted. A node whose installed world is a *different version* with an unchanged
  schema is reported and never refused, because the schema hash is the invalidation signal, and a
  package cannot be installed at two versions in one environment anyway.
- **Fork** is unchanged: create from the composite fixture, change it, freeze that.

`seahaven check` makes the same checks before a commit. `SH402`, `SH403` and `SH405` run per node
with the path in the message. `SH401` and `SH404` stay whole-sidecar, one of them because there is
one clock per instance. `SH406` is the shape comparison, reported in the same sentence the refusal
at create uses.

**An added world's own fixtures are unreachable from a host.** `world.fixtures()` on a host lists
the host's own and nothing else, and nothing reads, copies or references a fixture belonging to a
world it adds. Letting a host load a payments file frozen in 2021 beside a chat file frozen in 2026
is the footgun this rule removes.

```python
import seahaven

payments = seahaven.World(
    name="payments",
    version="1.0.0",
    schema="CREATE TABLE charges (id TEXT PRIMARY KEY, amount INTEGER NOT NULL) STRICT;",
    state_format="seahaven.state/1",
)
company = seahaven.World(
    name="company",
    version="0.1.0",
    schema="CREATE TABLE staff (id TEXT PRIMARY KEY) STRICT;",
    fixtures_dir="fixtures",
    state_format="seahaven.state/1",
)
company.add_world(payments, name="payments")

with company.instance(now="2026-06-01T09:00:00.000Z") as inst:
    with inst.bulk() as ctx:
        ctx.db.execute("INSERT INTO staff (id) VALUES ('u1')")
        ctx.worlds.payments.db.execute("INSERT INTO charges (id, amount) VALUES ('c1', 500)")
    inst.freeze("acme", "One person, one charge.")

with company.instance("acme") as inst:
    charged = inst.inspect().one("SELECT amount FROM payments.charges")
    assert charged is not None and charged["amount"] == 500
    assert inst.composition()[1].path == "payments"
```

## What an eval sees

Nothing agent-facing says a world is composed. Everything eval-facing does.

- **`inst.inspect()`** is the read-only connection it always was, with every added node attached
  read-only under a schema named by its path, with `/` replaced by `__`: `payments.charges`,
  `payments__tax.rates`. "Was the invoice created and was the charge taken" is one statement.
  Writes, `ATTACH` and `DETACH` are denied on it, as they always were.
- **`inst.change_log()`** is one list covering every node, in call order. Each record carries
  `world`, the owning node's path, which is `main` for the root. That is what tells two tables of the
  same name in two stores apart; the records of one call are sorted by that path, then the table,
  then the key. Per-node exclusions are each world's own `untracked_tables` and FTS5 shadow tables.
- **`inst.composition()`** is what this instance is running against: one record per node with
  `path`, `world`, `world_version`, `scope`, `aliases`, `schema_hash`, and `frozen_world_version` —
  the version the fixture recorded, when that is not the version installed, and `None` otherwise. It
  describes the files on disk rather than whatever the world's seal says now.
- **`inst.state()`** is the whole document an eval grades on, and a node's path is what joins that
  document together. `composition` is keyed by path. `fixture` holds one `file_sha256` per path. A
  log record's `world` names one ([state.md](state.md)).
- **Over OpenEnv**, the `state` message carries that same document, with `composition` `null` before
  the first `reset`, so a session can say what tree it is running against. `state` is not an
  observation, and nothing agent-facing carries any of it.

## Typed access

A tool is a plain typed function, so calling it **by the function itself** gives a type checker
everything: argument completion, a wrong argument as an error, and a typed result. It works at the
instance and through a handle, and dispatch after resolution is identical to the by-name path —
validation, the chain, the transaction.

```python
import seahaven
from pydantic import BaseModel


class Charge(BaseModel):
    id: str
    amount: int


payments = seahaven.World(
    name="payments",
    version="1.0.0",
    schema="CREATE TABLE charges (id TEXT PRIMARY KEY, amount INTEGER NOT NULL) STRICT;",
    state_format="seahaven.state/1",
)


@payments.tool
def create_charge(ctx: seahaven.Ctx, amount: int) -> Charge:
    """Charge the account and return the charge."""
    charge = Charge(id=ctx.ids.uuid(), amount=amount)
    ctx.db.execute("INSERT INTO charges (id, amount) VALUES (?, ?)", charge.id, charge.amount)
    return charge


company = seahaven.World(
    name="company",
    version="0.1.0",
    schema="CREATE TABLE staff (id TEXT PRIMARY KEY) STRICT;",
    state_format="seahaven.state/1",
)
company.add_world(payments, name="payments", tool_prefix="pay_")

with company.instance() as inst:
    # `amount=` is checked by the type checker, and `charge` is a Charge.
    charge = inst.call(create_charge, amount=500)
    assert isinstance(charge, Charge) and charge.amount == 500
    # An in-process call returns the tool's own object either way; only the wire
    # serialises. Return models rather than bare dicts, and callers complete too.
    assert inst.call("pay_create_charge", amount=7).amount == 7
```

- `inst.call(fn)` resolves over the **composite surface**, so it reaches any tool the agent could
  call and nothing an allow or block list keeps off it. That one is the handle's job. A function
  whose world is a node of the tree twice — two payments accounts — does not name one store, and the
  `WorldBug` says so, naming every candidate as `<name> at <path>` and sending you to the handle
  that does.
- `ctx.worlds.<name>.call(fn)` resolves over **that handle's subtree**, which is what a handle is
  for: a handle names the account, and the owner may be a descendant. Filtering does not apply,
  because host code can call everything.
- `ctx.worlds.<name>.call("name")` uses the added world's own registry: its unprefixed, unfiltered
  names.

**Optional static child names.** `ctx.worlds.<name>` answers a handle for any name, and an
unregistered one is a `WorldBug` at run time and `SH209` in `seahaven check`. A host that wants the
names checked by a type checker too declares them once and annotates its contexts:

```py
import seahaven


class CompanyWorlds(seahaven.Worlds):
    payments: seahaven.WorldHandle
    shop: seahaven.WorldHandle


@world.tool
def invoice(ctx: seahaven.Ctx[CompanyWorlds], amount: int) -> Invoice: ...
```

The subclass is never instantiated. `ctx.worlds` is always Seahaven's own container, and the
annotation is a fiction it satisfies at run time. `seahaven check` binds the class to the
registrations: an attribute no `add_world` registered is `SH502`, and a registered name the class
forgot is `SH503`. Declaring the class is never required, and a world that skips it loses only the
static check.

## What `seahaven check` adds

Eight codes, all of them for mistakes a composite makes silently.
[reference/lints.md](reference/lints.md) has the rule, the reasoning and the fix for each.

| Code | Severity | What |
|---|---|---|
| SH206 | warning | a prefixed world's description names a sibling tool the agent cannot call |
| SH207 | warning | one tool of a shared node contributed under two names |
| SH208 | error | `.instance(` in a module under `tools/` or `middleware/` |
| SH209 | error | `ctx.worlds` naming something that is not a registered child |
| SH406 | error | a composite sidecar's `nodes` disagrees with the world's composition |
| SH502 | error | a `Worlds` subclass annotates a name no `add_world` registered |
| SH503 | warning | a registered child name no declared `Worlds` subclass annotates |
| SH504 | error | the world's composition does not seal |

## Limits

- **The hard cap is SQLite's attached-database limit**, because every node is a file the eval's
  inspection connection attaches: 125 added stores on the SQLite this ships with. Seahaven probes
  the limit at the seal rather than assuming it, and refuses there with the real number in the
  message. No lower limit is imposed. `check` does not volunteer the count, because it prints
  findings and nothing else, so the count appears where it is actionable, which is `SH504` at the
  bound. In process the count is `len(world.composition().nodes)`, and per instance
  `inst.composition()`.
- **An idle composite instance is one file and one connection per node.** A session is opened per
  node per call and closed with the call, so an idle instance holds none. Cost scales with the
  tree, and the design target — hundreds of concurrent instances with minute-long lifetimes —
  holds for a small number of nodes. The benchmark measures one node per instance and reads as a
  per-node floor.
- **Sealing costs a walk of the tree,** on the first use after any registration anywhere in the
  process. The invalidation is global, because a world cannot be told what added it. Steady state is
  one integer compare per call. Registration is expected to finish at import, so this is a cost you
  pay once.
- **Copy the root, never a dependency,** when a test points a world at another fixtures directory.
  `copy.copy(world)` is a distinct `World` object and therefore a distinct node, so a host that
  added the original does not see the copy.

Deliberately absent, and not planned: dynamic composition at `reset()`; migrations of composite
fixtures; merging an added world's schema into the host's file; cross-node SQL for the *agent*,
which exists only on the eval's inspection handle; and a host reaching into an added world's
declaration to redirect its children. Fork the package when the client's copy of the product really
differs.
