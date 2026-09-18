"""The tool factory: one Seahaven tool that is a whole XML-RPC endpoint.

A world's XML-RPC surface is not a list of Seahaven tools -- it is one tool
carrying a document, with a method namespace of its own inside it. That is the
point of the example: the tool registry knows about `rpc`, and knows nothing
about the thirty methods behind it, because the protocol's namespace is the
protocol's business.

`Tool.from_function` builds the tool from the inner function's signature, exactly
as `world.tool` would for a world's own function, so the endpoint gets the
framework's argument model, its JSON schema and its strict validation for free.
Nothing here is privileged; `seahaven/helpers/run_sql.py` is the same shape.

The tool raises `XmlRpcFault` and never renders one. Rendering belongs to
`render_faults`, the middleware, and the reason is the transaction: `invoke` runs
a tool inside `ctx.db.transaction()`, so a tool that caught its own fault and
returned a document would return *normally*, and whatever the handler wrote
before it failed would commit. A faulted call must leave nothing behind.
"""

import inspect
from collections.abc import Callable, Mapping
from typing import Annotated, Any

from pydantic import Field

import seahaven
from seahaven_xmlrpc.call_log import record
from seahaven_xmlrpc.documents import parse_method_call, render_response
from seahaven_xmlrpc.faults import INVALID_PARAMS, METHOD_NOT_FOUND, XmlRpcFault

__all__ = ["MAX_BODY", "MethodHandler", "xmlrpc_call"]

type MethodHandler = Callable[..., Any]

# The default cap on the document an agent may send, in characters. Large enough
# for any real `methodCall` -- a struct of a hundred members is a few kilobytes --
# and small enough that a runaway agent cannot spend the process's memory on one.
MAX_BODY = 65_536

_POSITIONAL = (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)


def xmlrpc_call(
    *,
    methods: Mapping[str, MethodHandler],
    name: str = "xmlrpc_call",
    description: str | None = None,
    transaction: bool = True,
    allow_none: bool = False,
    max_body: int = MAX_BODY,
    log_calls: bool = False,
) -> seahaven.Tool:
    """An XML-RPC endpoint over `methods`, as a `Tool` the world registers.

    `methods` maps the name a caller sends -- `user.create`, `system.listMethods`
    -- to a handler `(ctx, *params) -> value`. The handler takes the instance
    context first, like every other function in a Seahaven world, and the
    document's parameters positionally after it.

    `name` and `description` are the world's to choose, which is what
    `functional_spec.md` §21 means by a factory that lets the world decide them.
    A world registering more than one endpoint -- a v1 and a v2 namespace, say --
    gives each its own name and names both to `render_faults`.

    `allow_none` turns on the `<nil/>` extension, for a product whose methods
    return nothing, and it governs both directions: an endpoint that does not
    speak `<nil/>` refuses one in a `methodCall` as well as declining to send one.
    `max_body` is the cap on the document, published in the
    tool's JSON schema. `log_calls` writes the `call_log` row, and requires the
    world to have concatenated `CALL_LOG_DDL` into its schema.

    `transaction` is here only to be refused. It is `Tool.from_function`'s own
    option and every other factory passes it through, so a world author will try
    it; what it would do here is switch off the guarantee the endpoint is built
    on. A fault leaves the tool as an exception precisely so that `invoke`'s
    transaction rolls the handler's writes back, and with `transaction=False`
    there is no transaction to roll back: the fault is still rendered, and the
    half-finished write still commits. A faulted RPC that leaves rows behind is
    not a configuration this example should hand anyone, so the parameter is kept
    and the value is refused, which is the only way to say why.

    Everything that can be wrong with the registration is found here, at
    registration, and never on the first call.
    """
    if not transaction:
        raise seahaven.WorldBug(
            f"tool {name!r}: an XML-RPC endpoint cannot be registered with transaction=False. A "
            f"fault is raised out of the tool so that the call's transaction rolls back what the "
            f"handler wrote before it failed; without one those writes commit and the caller is "
            f"told the method failed"
        )
    if max_body < 1:
        raise seahaven.WorldBug(f"tool {name!r}: max_body must be at least 1: {max_body}")
    handlers = _checked(name, methods)
    # Taken once, here: a signature is what every call is bound against, and
    # `inspect.signature` is far too slow to ask per call.
    signatures = {method: inspect.signature(handler) for method, handler in handlers.items()}

    def _xmlrpc_call(
        ctx: seahaven.Ctx,
        body: Annotated[
            str,
            Field(
                min_length=1,
                max_length=max_body,
                description="One XML-RPC <methodCall> document.",
            ),
        ],
    ) -> str:
        method, params = parse_method_call(body, allow_none=allow_none)
        handler = handlers.get(method)
        if handler is None:
            raise XmlRpcFault(METHOD_NOT_FOUND, f"no such method: {method}")
        try:
            # Bound rather than called-and-caught: a `TypeError` raised *inside* a
            # handler is that handler's bug, and an `except TypeError` around the
            # call would report it to the agent as bad parameters.
            signatures[method].bind(ctx, *params)
        except TypeError as error:
            raise XmlRpcFault(
                INVALID_PARAMS, f"{method} cannot be called with these parameters: {error}"
            ) from error
        if log_calls:
            record(ctx, method)
        return render_response(handler(ctx, *params), method=method, allow_none=allow_none)

    return seahaven.Tool.from_function(
        _xmlrpc_call,
        name=name,
        description=description if description is not None else _default_description(handlers),
        transaction=True,
    )


def _checked(name: str, methods: Mapping[str, MethodHandler]) -> dict[str, MethodHandler]:
    """`methods` as a dict of its own, or the `WorldBug` that says what is wrong.

    Copied rather than kept: the mapping the world passed is the world's, and an
    endpoint whose method table changed under it after registration would publish
    a description that no longer lists what it serves.
    """
    if not methods:
        raise seahaven.WorldBug(f"tool {name!r}: methods must name at least one method")
    for method, handler in methods.items():
        if not method or not method.strip():
            raise seahaven.WorldBug(f"tool {name!r}: a method needs a name: {method!r}")
        if not callable(handler):
            raise seahaven.WorldBug(
                f"tool {name!r}: the handler for {method!r} is not callable: {handler!r}"
            )
        _check_context_parameter(name, method, handler)
    return dict(methods)


def _check_context_parameter(name: str, method: str, handler: MethodHandler) -> None:
    """A handler takes the context first, and is refused now if it cannot.

    The same rule `Tool.from_function` applies to a tool's own function, applied
    a level down: a handler is called `handler(ctx, *params)`, so one that takes
    no positional parameter at all -- or takes its first by keyword only -- would
    register cleanly and fail on every call.
    """
    try:
        parameters = list(inspect.signature(handler).parameters.values())
    except (TypeError, ValueError) as error:
        raise seahaven.WorldBug(
            f"tool {name!r}: the handler for {method!r} has no signature to check: {handler!r}"
        ) from error
    if any(parameter.kind is inspect.Parameter.VAR_POSITIONAL for parameter in parameters):
        return
    if not parameters or parameters[0].kind not in _POSITIONAL:
        raise seahaven.WorldBug(
            f"tool {name!r}: the handler for {method!r} takes the context as its first "
            f"parameter, positionally, and the call's parameters after it"
        )


def _default_description(handlers: Mapping[str, MethodHandler]) -> str:
    """What an agent reads in the tool list when the world names no description.

    The method names are in it because they are the whole of the endpoint's
    surface and the JSON schema cannot carry them: the schema describes one
    string argument, and every method the world serves is inside it.
    """
    return (
        "Send one XML-RPC <methodCall> document and read the <methodResponse> that comes back; "
        "a method that fails answers with a <fault>. Methods: " + ", ".join(sorted(handlers)) + "."
    )
