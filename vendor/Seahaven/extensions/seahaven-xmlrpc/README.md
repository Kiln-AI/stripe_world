# seahaven-xmlrpc

XML-RPC for Seahaven worlds, and Seahaven's worked example of the extension contract
(`functional_spec.md` §21).

A world whose real product speaks XML-RPC does not get thirty Seahaven tools. It gets one tool
carrying a `<methodCall>` document, with a method namespace of its own inside it — which is the
point of the example. The tool registry knows the endpoint; the protocol's namespace is the
protocol's business, and the framework never learns it exists.

Nothing in this package is privileged. It depends on `seahaven`, uses the public API, and Seahaven
never imports it. The dependency graph is extension → seahaven, world → both.

## Registering it

```python
import seahaven
import seahaven_xmlrpc

world = seahaven.World(
    name="myworld",
    version="1.0.0",
    schema=seahaven.sql_files(__package__, "schema") + seahaven_xmlrpc.CALL_LOG_DDL,
    state_format="seahaven.state/1",
)

world.tool(seahaven_xmlrpc.xmlrpc_call(methods=METHODS, name="rpc", log_calls=True))
world.middleware(error_handler)  # the world's own, outermost
world.middleware(seahaven_xmlrpc.render_faults(tools=["rpc"]))
world.instance_startup(seahaven_xmlrpc.remember_client)
```

A method is an ordinary function taking the instance context first and the document's parameters
after it, like every other function in a Seahaven world:

```python
def create_user(ctx: seahaven.Ctx, email: str, name: str) -> dict[str, object]:
    ctx.db.execute(
        "INSERT INTO users (id, email, name) VALUES (?, ?, ?)", ctx.ids.uuid(), email, name
    )
    ...


METHODS = {"user.create": create_user}
```

A method reports failure by raising `XmlRpcFault(code, message)` — the product's own fault code,
the way a real XML-RPC product answers. A world's ordinary `ToolError` subclass is *not* rendered
as a fault: it sails past `render_faults` and reaches the agent as a Seahaven error shape, on an
endpoint whose whole contract is that failures are fault documents.

## Two things to get right

**Register both halves, in that order.** The tool raises faults; the middleware renders them. That
split is not tidiness — a tool runs inside the call's transaction, so a tool that caught its own
fault and returned a fault document would return *normally* and commit whatever the handler wrote
before it failed. Raising past the transaction is what rolls it back. And the world's own error
handler goes on *first*, so it stays outermost: the other way round it restates the `DbError` in the
product's words before the fault layer ever sees it.

**The call log is a schema change.** `CALL_LOG_DDL` is a string this package exports and the world
concatenates into its schema. That changes the world's schema hash, so every fixture of that world
has to be regenerated — the cost `functional_spec.md` §21 point 4 names, and the reason the tests
here run against a copy of ProjectTracker rather than against the reference world itself.
`log_calls=False` (the default) needs no table at all.

## What is in it

| Module | What it is |
|---|---|
| `faults.py` | the fault codes, `XmlRpcFault`, and a fault as a document |
| `documents.py` | the wire: parse a `methodCall`, render a `methodResponse` |
| `tool.py` | `xmlrpc_call`: the endpoint, as a `Tool` the world registers |
| `middleware.py` | `render_faults`: why rendering is not in the tool |
| `call_log.py` | the DDL string and the startup hook: two more seams |

## Tests

`uv run pytest extensions/seahaven-xmlrpc` from the repository root, or `uv run pytest` from here.

They run against `tests/tracker_rpc`, a copy of ProjectTracker — the reference world's schema and
its error handler, imported rather than retyped — and they drive the endpoint with `xmlrpc.client`
on the other side of the wire. A test that built its own XML would only prove the extension agrees
with the test; driving it with the standard library's client proves the documents are XML-RPC.
