# Extensions

An extension is an ordinary Python package that depends on `seahaven` and uses its public API like
any other code. It packages something several worlds would otherwise each write: a protocol
endpoint, a set of tools for a subsystem, a piece of middleware.

**The dependency graph is extension → seahaven, and world → both.** Seahaven never imports an
extension. There is no registry, no entry point and no discovery.

Everything an extension exports is something the world passes to `world.tool(...)`,
`world.middleware(...)`, `world.instance_startup(...)` or `World(schema=...)`.

## What an extension may rely on

An extension may rely on exactly these five parts of the public API. Each one is documented, and
none of them is going to move under you.

**1. The instance context.** A tool factory returns a `seahaven.Tool` whose function takes `ctx`
first. Everything the tool needs — the database, the clock, the id stream, per-instance state — is
on it.

```py
import seahaven


def echo_tool(*, name: str = "echo") -> seahaven.Tool:
    def echo(ctx: seahaven.Ctx, message: str) -> dict[str, str]:
        """Say something back, with the instance's own timestamp on it."""
        return {"message": message, "at": ctx.clock.iso()}

    return seahaven.Tool.from_function(echo, name=name)
```

`Tool.from_function(fn, *, name=None, description=None, transaction=True)` builds the argument
model, the JSON schema and the strict validation from the signature, which is the same treatment
`@world.tool` gives a world's own function. A factory that lets the world choose the name, the
description or the transaction takes them as parameters, because `world.tool(tool_obj)` registers a
built `Tool` exactly as it is and refuses to override any of the three.

**2. The middleware shape.** `(ctx, call, next_) -> result`, checked at registration. An extension
that maps its own failures registers its middleware *inside* the world's error handler — that is,
after it, since order is outermost first — so its errors are mapped before the generic handler sees
what is left.

**3. Instance startup.** An extension may export a hook the world registers, and may read `reset()`
keyword arguments through it. Spell the parameters out; a `**kwargs` hook switches off
unknown-argument detection for the whole world.

**4. Schema text.** An extension that needs a table exports a `CREATE TABLE` string the world
concatenates into its schema:

```py
world = seahaven.World(
    name="myworld",
    version="1.0.0",
    schema=seahaven.sql_files(__package__, "schema") + seahaven_xmlrpc.CALL_LOG_DDL,
    state_format="seahaven.state/1",
)
```

This is a convention rather than a part of the API, and it has a cost: **a schema fragment changes
the world's schema hash, so every fixture of that world has to be regenerated.** That is survivable
because every fixture is committed with the script that generates it (see
[db_schema_and_fixtures.md](db_schema_and_fixtures.md)), but it is a real cost, and an extension
should make the table optional where it can.

**5. Agent SQL containment.** `seahaven.sandbox` is public — `Authorizer`, `run_statement`,
`SqlResult` and the refusal names — so an extension serving another SQL dialect runs its translated
statement through the same containment Seahaven's own `run_sql` helper uses, rather than inventing a
second one.

An extension may also subclass `seahaven.ToolError` for its own error shapes, and catch Seahaven's
errors (`WorldBug`, `ToolError`, `ArgumentError`, `DbError`, `UnknownTool`).

## What an extension must not do

- **Monkeypatch the framework.** Nothing in an extension replaces or wraps a Seahaven function.
- **Register itself.** The world registers what it wants, by name, in its own `__init__`. An
  extension that registered itself on import would make a world's tool surface depend on its import
  order.
- **Set `control`.** That flag is Seahaven's own, for its control tool, and
  `Tool.from_function` cannot set it.

## The worked example: XML-RPC

The Seahaven repository ships one extension, `extensions/seahaven-xmlrpc`. It exists to show that
the five parts above carry a protocol the framework knows nothing about.

A world whose real product speaks XML-RPC does not get thirty Seahaven tools. It gets **one** tool
carrying a `<methodCall>` document, with a method namespace of its own inside it. The tool registry
knows the endpoint; the protocol's namespace is the protocol's business.

```py
import seahaven
import seahaven_xmlrpc

from myworld.world import world


def create_user(ctx: seahaven.Ctx, email: str, name: str) -> dict[str, object]:
    """An RPC method is an ordinary function: ctx first, the document's parameters after."""
    user = {"id": ctx.ids.uuid(), "email": email, "name": name}
    ctx.db.execute("INSERT INTO users (id, email, name) VALUES (?, ?, ?)", user["id"], email, name)
    return user


METHODS = {"user.create": create_user}

world.tool(seahaven_xmlrpc.xmlrpc_call(methods=METHODS, name="rpc", log_calls=True))
world.middleware(error_handler)  # the world's own, registered first, stays outermost
world.middleware(seahaven_xmlrpc.render_faults(tools=["rpc"]))
world.instance_startup(seahaven_xmlrpc.remember_client)
```

Two things in that sketch are the reason the example exists.

**The tool raises faults, and the middleware renders them.** The split matters because a tool runs
inside the call's transaction. A tool that caught its own fault and returned a fault document would
return *normally*, and whatever the method wrote before it failed would commit. Raising past the
transaction is what rolls it back, and the layer above the transaction is where the document is
made.

**The world's own error handler is registered first.** The other way round, the handler would
restate a `DbError` in the product's words before the fault layer ever saw it, and an endpoint whose
whole contract is that failures are fault documents would answer with something else.

The package is five small modules — the fault codes, the wire format, the tool factory, the
middleware and the optional call log — with a README that walks through them. Its tests drive it
with Python's own `xmlrpc.client` against a copy of ProjectTracker. A test that built its own XML
would only prove the extension agrees with the test; driving it with the standard library's client
proves the documents are XML-RPC.

## Writing one

There is nothing framework-specific about the package itself: a `pyproject.toml` with `seahaven` as
a dependency, a `py.typed`, and modules. Four things are worth keeping in mind.

- **Export factories, not instances.** A world may want two endpoints, or a different name.
- **Take the tables you touch as a parameter.** An extension that hard-codes a table name works in
  one world.
- **Say what your schema text costs.** If you export a schema fragment, put "this changes the
  world's schema hash; regenerate the fixtures" in your README, next to the string.
- **Test against a real world.** An extension tested only against a world it made up has not been
  tested against Seahaven's rules: registration validation, the transaction, the error handler.
