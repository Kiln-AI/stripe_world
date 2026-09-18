"""The response layer: the middleware that turns a fault into a fault document.

XML-RPC answers a failed method with a response, not with an error, so something
has to stand between the endpoint's exception and the caller's result. It is a
middleware and not part of the tool for one reason, and the reason is the
transaction:

    invoke  ->  with ctx.db.transaction():  ->  the tool  ->  a handler writes,
                                                              then fails

A tool that caught the failure and returned a fault document would return
normally, and the transaction would commit the half-finished write. Raising past
the transaction is what rolls it back, and this middleware -- which runs outside
it -- is the first place a fault can become a document without committing
anything.

That also settles what it does *not* catch. A `WorldBug`, an `ArgumentError`, a
`ZeroDivisionError` in a handler: none of those is an outcome XML-RPC has, and
the world's own error handler -- registered outside this one -- is what decides
what an agent sees when the world breaks. `functional_spec.md` §21 names `DbError`
for this middleware because that is the failure that arrives *through* the
protocol rather than beside it: a `UNIQUE` violation under a handler is how an
XML-RPC method usually fails, and it has to roll back before it is rendered.

**Order matters.** Register the world's error handler first and this middleware
second, so the product's handler is outermost:

    world.middleware(error_handler)                    # outermost
    world.middleware(render_faults(tools=["rpc"]))     # inside it

The other way round, the product's handler sees the `DbError` first and restates
it in the product's words, and the fault document is never built.
"""

from collections.abc import Sequence
from typing import Any

import seahaven
from seahaven.world import Handler, Middleware
from seahaven_xmlrpc.faults import APPLICATION_ERROR, XmlRpcFault, fault_document

__all__ = ["render_faults"]


def render_faults(
    *,
    tools: Sequence[str],
    db_fault: int = APPLICATION_ERROR,
    db_message: str | None = None,
) -> Middleware:
    """Render the faults of the named tools as `<methodResponse>` documents.

    `tools` names the endpoints this applies to -- the names the world gave
    `xmlrpc_call`. Nothing else in the world is touched: a `DbError` under a tool
    that is not an XML-RPC endpoint is the world's own failure, and turning it
    into a fault document would answer a caller who never spoke XML-RPC.

    `db_fault` is the fault code a `DbError` becomes, and `db_message` the string
    it carries. The default message is the `DbError`'s own -- "database error",
    or "not allowed: write" from the sandbox -- and never SQLite's text: engine
    text reaches an agent only where a world decides it should, which is what
    passing `db_message` is for.
    """
    covered = frozenset(tools)
    if not covered:
        raise seahaven.WorldBug("render_faults must name at least one tool")

    # Named, not a lambda and not `middleware`: a world reads its chain back by
    # `__name__` and a traceback prints it, and "middleware" would say nothing.
    def render_xmlrpc_faults(ctx: seahaven.Ctx, call: seahaven.Call, next_: Handler) -> Any:
        if call.name not in covered:
            return next_(ctx, call)
        try:
            return next_(ctx, call)
        except XmlRpcFault as fault:
            return fault.document()
        except seahaven.DbError as error:
            return fault_document(db_fault, db_message if db_message is not None else error.message)

    return render_xmlrpc_faults
