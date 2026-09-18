"""One invocation, the middleware chain it descends, and the handler at the bottom.

`invoke` is that bottom handler: it validates the arguments, runs the tool inside
the call's transaction and proves that what comes back would serialise. It takes a
context and a call and nothing else, which is what keeps the instance -- its lock,
its files, its manager -- out of the call path entirely.
"""

import dataclasses
import logging
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Self

import pydantic
from pydantic_core import PydanticSerializationError, to_jsonable_python

from seahaven.errors import ToolError, WorldBug
from seahaven.tool import Tool

if TYPE_CHECKING:  # `ctx.py` is below this module; the annotation is all that is needed here
    from seahaven.ctx import Ctx

__all__ = [
    "Call",
    "Handler",
    "Middleware",
    "arguments_of",
    "build_chain",
    "invoke",
    "name_of",
    "rebind",
    "serialise",
]

# `Ctx[Any]`, not a bare `Ctx`: the parameter is a world's own declaration of the
# worlds it adds (architecture section 8.3) and `Ctx` is invariant in it, so a
# chain typed on `Ctx[Worlds]` would refuse the very context a world that made
# that declaration runs its own middleware with. Nothing in the chain reads it.
type Handler = Callable[["Ctx[Any]", "Call"], Any]
type Middleware = Callable[["Ctx[Any]", "Call", Handler], Any]

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Call:
    """A tool call: the name asked for, the arguments given, and the tool found.

    `arguments` is the raw mapping the caller sent until `invoke` validates it,
    and the validated arguments after. A middleware that wants typed arguments
    before then calls `call.tool.validate(call.arguments)` itself; the model is
    built once at registration, so that is cheap.

    `node` is the canonical path of the node that owns the tool, so a host's
    middleware anywhere on the route knows what is being called without touching
    a foreign store. A leaf world's calls all say `main`, which is what the
    default spells.
    """

    name: str
    arguments: Mapping[str, Any]
    tool: Tool
    node: str = "main"

    def with_arguments(self, **changes: Any) -> Self:
        """A copy with `changes` merged over the current arguments."""
        return dataclasses.replace(self, arguments={**self.arguments, **changes})


def build_chain(middlewares: Sequence[Middleware], innermost: Handler) -> Handler:
    """The middleware chain, outermost first, ending in `innermost`.

    The one chain builder: `composition.build_route_chain`, which is what a
    node's chains are built by, is this function over middlewares wrapped in
    the node each was paired with at the seal.
    """
    handler = innermost
    for middleware in reversed(middlewares):
        handler = _layer(middleware, handler)
    return handler


def name_of(tool: str | Callable[..., Any]) -> str:
    """What a call asked for, as one string: the name, or the function's qualified name.

    A call by function reference is logged and reported under the name it
    resolves to; this is what stands in until it has one, and what a resolution
    failure names.
    """
    return tool if isinstance(tool, str) else getattr(tool, "__qualname__", repr(tool))


def arguments_of(
    tool: Tool,
    asked: str | Callable[..., Any],
    positional: Sequence[Any],
    keywords: Mapping[str, Any],
) -> dict[str, Any]:
    """One call's arguments as validation takes them, however the caller named the tool.

    A name is the wire's own spelling, and so are the arguments beside it: they
    pass through, as they have always done. A function reference is Python's, and
    `Tool.arguments` translates -- positional arguments bind to their parameters,
    and an argument that declares an alias is sent under the alias the tool list
    publishes.
    """
    if not isinstance(asked, str):
        return tool.arguments(positional, keywords)
    if positional:
        # Only reachable by ignoring the string overload, which has no positional
        # arguments after the name: every tool takes its arguments by name.
        raise WorldBug(f"tool {asked!r} was named as a string, so its arguments are passed by name")
    return dict(keywords)


def rebind[C: Ctx](ctx: C, call: Call) -> C:
    """The context a layer runs with, bound to the call it is being handed.

    `call` is always `ctx.call` inside a layer. A middleware that rewrites
    arguments passes a new `Call` down without having to rebuild the context to
    match, and a middleware that passes the one it was given costs nothing here.

    One rule, written once: `build_chain` applies it to every layer it wraps, and
    `handles.ctx_for` applies it again on the branch where the layer's own node
    needs no context of its own -- idempotent, so a chain built by
    `composition.build_route_chain` through `build_chain` sees the same context
    either way.
    """
    return ctx if ctx.call is call else ctx.with_call(call)


def _layer(middleware: Middleware, next_: Handler) -> Handler:
    def call_middleware(ctx: Ctx, call: Call) -> Any:
        return middleware(rebind(ctx, call), call, next_)

    return call_middleware


def invoke(ctx: Ctx, call: Call) -> Any:
    """Validate and run one call, and answer with what the tool returned: the innermost handler.

    Raised inside the chain rather than before it, so a world's error handler
    sees `ArgumentError` and can restate it in the product's own words.
    """
    tool = call.tool
    # Replaced, not merged: the validated arguments are the model's fields under
    # their Python names, and a wire name that differs from its parameter's --
    # `Field(alias="from")` -- would otherwise survive the merge and reach the
    # function as an argument it has no parameter for.
    call = dataclasses.replace(call, arguments=tool.validate(call.arguments))
    ctx = ctx.with_call(call)
    try:
        if tool.transaction:
            with ctx.db.transaction():
                # Serialised inside the transaction and the rendering thrown
                # away: a result that cannot be serialised rolls the call back
                # rather than committing a write whose answer never reached the
                # caller, and what comes back is the object the tool returned, so
                # a host tool handed a `Charge` is handed a `Charge`. The wire is
                # served by `openenv/env.py`, which renders it there.
                return _proved(tool.fn(ctx, **call.arguments))
        return _proved(tool.fn(ctx, **call.arguments))
    except ToolError:
        raise
    except Exception:
        # Not a failure the agent was meant to read. It still passes through the
        # chain unchanged -- the world's handler decides what the agent sees --
        # but the traceback is on record whatever that decision is.
        # "failed", not "raised": a result this framework refuses to serialise
        # fails here too, and the tool returned normally.
        _log.error("tool %r failed on instance %s", tool.name, ctx.instance.id, exc_info=True)
        raise


def _proved(result: Any) -> Any:
    """The result, once this framework has proved it would render."""
    serialise(result)
    return result


def serialise(result: Any) -> Any:
    """A tool's return value as JSON-able data.

    `to_jsonable_python` renders models, dataclasses and datetimes, and would
    also render two things a world must not return: `bytes`, which it decodes as
    text, and a `set`, which it lists in whatever order the set iterates. Both
    are refused first, because a result that changes between two identical runs
    is the one failure this framework exists to prevent. A value it cannot render
    at all is a `WorldBug` too: the tool, not the agent, has the mistake.
    """
    _refuse_unrenderable(result, ancestors=frozenset())
    try:
        return to_jsonable_python(result)
    except PydanticSerializationError as error:
        # A value pydantic has no rendering for at all: an open file, a
        # connection, an instance of a world's own class that is not a model.
        raise WorldBug(f"a tool result must be JSON-able data: {error}") from error


def _refuse_unrenderable(value: Any, ancestors: frozenset[int]) -> None:
    if isinstance(value, bytes | bytearray):
        raise WorldBug("tool results cannot contain bytes; encode them")
    if isinstance(value, set | frozenset):
        raise WorldBug("tool results cannot contain sets; their order is undefined, so use a list")
    if isinstance(value, Iterator):
        # A generator or an iterator cannot be looked at and then serialised --
        # looking at it is what empties it -- and a tool that answers with one
        # has already lost the ability to inspect its own result. Build a list.
        raise WorldBug("tool results cannot contain a generator or an iterator; build a list")
    children = _children(value)
    if children is None:
        return
    if id(value) in ancestors:
        raise WorldBug("a tool result cannot contain itself")
    # Ancestors, not everything seen: the same dict reached twice down two
    # branches is shared, not circular, and only a loop is a problem.
    inside = ancestors | {id(value)}
    for child in children:
        _refuse_unrenderable(child, inside)


def _children(value: Any) -> Iterable[Any] | None:
    """What a container holds, or `None` for a value that holds nothing.

    Everything `to_jsonable_python` descends is descended here, or the check in
    front of it would only cover the shapes it happened to think of: a model's
    extras and computed fields are rendered as fields and are not in `__dict__`,
    and any iterable is rendered as a list.
    """
    if isinstance(value, str):  # iterable, but a leaf, and iterating it never ends
        return None
    if isinstance(value, Mapping):
        return _pairs(value)
    if isinstance(value, list | tuple):
        # The common case, and walked as it is: the generic branch below would
        # answer the same and copy every list in the result to do it.
        return value
    if isinstance(value, pydantic.BaseModel):
        return _model_values(value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return [getattr(value, field.name) for field in dataclasses.fields(value)]
    if isinstance(value, Iterable):
        # A container a model declares as a field type -- `deque[str]` and the
        # other sequences pydantic builds a serialiser for -- which the model's
        # own serialiser renders as a list. Materialised, because it is walked
        # now and rendered later. (A bare one, outside a model, `to_jsonable_python`
        # refuses, and `serialise` turns that refusal into a `WorldBug`.)
        return list(value)
    return None


def _model_values(model: pydantic.BaseModel) -> list[Any]:
    """Everything a model serialises: its fields, its extras and its computed fields."""
    return [
        *model.__dict__.values(),
        *(model.__pydantic_extra__ or {}).values(),
        *(getattr(model, name) for name in type(model).model_computed_fields),
    ]


def _pairs(mapping: Mapping[Any, Any]) -> Iterator[Any]:
    for key, item in mapping.items():
        yield key
        yield item
