"""The framework's own tool: what an eval asks an instance, not what an agent does.

`controller_run_sql` is registered on every world, is never in its tool list, and
is dispatched straight from `Instance.call` -- around the middleware chain, the
world's error handler, the per-call transaction and the concurrency gate. An eval
asking what an instance holds wants the real answer and the real message, and it
must not have to wait behind the agent's queue to get one.

It is deprecated: `inst.state()` is what an eval reads now, and every call
through here warns. It is a thin wrapper -- a read-only handle on the instance is
what it reads through -- and it writes no SQL of its own. It covers a composite
instance's every node without a line here that knows it, because the handle
carries each added node attached under its own schema.

A control call takes the instance lock like any call, so it never interleaves
with a step on the same instance, and it asks the instance for what it needs with
that lock already held, which is why the lock is an `RLock`.

The handle it reads through is the instance's control handle and not the
`inspect()` one an eval holds. Same file, same read-only opener, different
connection: `sandbox.run_statement` borrows connection-level state -- the
authorizer, the value limit -- for the length of a statement, and `inspect()`
reads do not take the instance lock, so a control read on that connection would
be a second thread changing it under a cursor that is stepping. See
`Instance._control_db`.
"""

import dataclasses
import functools
import inspect
import os
import warnings
from collections.abc import Callable
from types import FunctionType
from typing import Any

import apsw

from seahaven import sandbox
from seahaven.call import serialise
from seahaven.ctx import Ctx
from seahaven.db import SqlValue
from seahaven.errors import DbError, WorldBug
from seahaven.helpers.run_sql import showing_sqlite_text, to_result
from seahaven.instances import Instance
from seahaven.sandbox import Authorizer
from seahaven.tool import Tool

__all__ = ["DEPRECATED", "TOOLS", "controller_run_sql", "dispatch"]

# The control tools whose calls warn. `functional_spec.md` §11 keeps
# `controller_run_sql` working and schedules no removal for it; `state()` is what
# replaced it.
DEPRECATED = frozenset({"controller_run_sql"})

# Every frame inside the framework, so that `warnings.warn` walks past all of
# them and reports the caller's own line. A `stacklevel` cannot do this: a
# control tool is reached through `Instance.call` in process, through the OpenEnv
# layer over the wire, and by calling `dispatch` directly, which are three
# different depths.
_FRAMEWORK = (os.path.dirname(os.path.abspath(__file__)) + os.sep,)


def controller_run_sql(
    instance: Instance, ctx: Ctx, sql: str, params: list[SqlValue] | None = None
) -> dict[str, Any]:
    """Deprecated: read inst.state() instead.

    Read the instance with one SQL statement, through its own read-only handle.

    Every table of every node: the root's store is `main` and each added node's
    file is attached under the schema its path derives, so one statement can join
    across two worlds. Every table on each of them, including the ones the
    framework knows nothing about -- FTS5's shadow tables, a world's untracked
    tables, SQLite's own -- and the introspection pragmas a read-only connection
    allows. No table allowlist, no row or byte caps, and SQLite's own error text
    as the message, because an eval grading a run wants the real one.

    A world's own `run_sql` helper runs on `ctx.db`, which is one node's
    connection with nothing attached to it, so isolation between nodes is
    structural and this door is the only one that crosses them.

    The one bound left is that connection's permanent write denial, which is
    mirrored for the length of the statement rather than replaced: a control read
    cannot become a write by going through here.
    """
    # The instance's own half of this module, private for the same reason
    # `instances._call` imports this one lazily: the two are one mechanism, and
    # the handle is no part of what an eval or a world is offered.
    db = instance._control_db()
    permanent = db.conn.authorizer
    if permanent is None:
        # The control connection is opened read-only *and* carries a denying
        # authorizer (`db.open_inspection`). If the second lock has gone, this is
        # not the door to find out through.
        raise WorldBug("the control connection has no authorizer; refusing to run control SQL")
    try:
        result = sandbox.run_statement(
            db,
            sql,
            tuple(params or ()),
            authorizer=_ControlAuthorizer(permanent),
        )
    except DbError as error:
        raise showing_sqlite_text(error) from error
    return to_result(result)


class _ControlAuthorizer(Authorizer):
    """The control connection's own denial, in the sandbox's vocabulary.

    `sandbox.run_statement` replaces the connection's authorizer for the length
    of the statement, which would leave a control statement running with the
    permanent denial off. So this one asks that denial first and adds nothing to
    it: what the connection allows, control allows.

    It is not the sandbox's containment, and is not meant to be. A control caller
    is the eval harness, in-process, and the containment that matters for it is
    that the connection is read-only.
    """

    def __init__(self, permanent: Callable[..., int]) -> None:
        # `read_only=True` is what makes `run_statement`'s tracer refuse a
        # statement SQLite calls a write before it steps, whatever the
        # authorizer said about the actions inside it.
        super().__init__((), read_only=True)
        self._permanent = permanent

    def __call__(
        self,
        action: int,
        third: str | None,
        fourth: str | None,
        database: str | None,
        trigger: str | None,
        /,
    ) -> int:
        if self._permanent(action, third, fourth, database, trigger) == apsw.SQLITE_OK:
            return apsw.SQLITE_OK
        # Denied on this connection. `Authorizer.__call__` is asked only to name
        # the refusal -- its table allowlist is empty, so it refuses whatever it
        # is given -- and the answer sent back is the denial either way.
        super().__call__(action, third, fourth, database, trigger)
        return apsw.SQLITE_DENY


def dispatch(instance: Instance, ctx: Ctx) -> Any:
    """Run the control tool `ctx.call` names: validate, run, serialise, and nothing else.

    No middleware, no error handler, no transaction (the control connection is
    read-only) and no gate. Under the instance lock like any call, because
    `Instance.call` took it before it got here.

    What it *is* is `invoke`'s own steps, in `invoke`'s own order: the validated
    arguments replace the call's, so the context a control tool reads says what
    it was really given, and the result goes through the same serialiser as every
    other call, so a control tool that answers with `bytes` or a `set` is the
    same `WorldBug` a world's tool would be rather than silently-decoded text or
    an order that changes between runs. `controller_run_sql` cannot: a BLOB
    reaches an eval as base64 text from it, as it does from `run_sql`.

    A call of a deprecated control tool warns here, before anything else: this is
    the one frame every route into a control tool passes through, and
    `skip_file_prefixes` walks out of the framework from it, so the warning is
    reported against the caller's own line. That is the caller's own code in
    process and when `dispatch` is called directly; over OpenEnv there is no such
    frame -- the eval is at the other end of a socket -- and the first frame
    outside the framework is the server's own thread runner, so what the warning
    lands on there is the server rather than the harness that sent the call.

    Python's filters then apply to that location and not to this one: the
    defaults show it when the caller is `__main__` and hide it otherwise,
    `-W default::DeprecationWarning` shows it either way, and the deduplication
    makes it once per call site rather than once per call.
    """
    call = ctx.call
    if call is None:
        raise WorldBug("a control tool is dispatched from a context bound to its call")
    if call.tool.name in DEPRECATED:
        warnings.warn(
            f"{call.tool.name} is deprecated: read inst.state() instead",
            DeprecationWarning,
            skip_file_prefixes=_FRAMEWORK,
        )
    call = dataclasses.replace(call, arguments=call.tool.validate(call.arguments))
    ctx = ctx.with_call(call)
    # The instance first: a control function is a wrapper over the instance, and
    # `Ctx` carries an `InstanceInfo`, not the instance itself. `Tool.fn` is
    # declared as a world's tool takes it -- the context, then the arguments --
    # and these two are the framework's own, built by `_control_tool` around a
    # function the field's type cannot describe.
    fn: Any = call.tool.fn
    return serialise(fn(instance, ctx, **call.arguments))


def _control_tool(fn: FunctionType) -> Tool:
    """A control tool from a control function, whose `instance` is not an argument.

    The argument model and the JSON schema come from the signature, as every
    tool's do; the `instance` parameter is bound away first, so the tool's
    arguments are exactly what a caller sends. `dispatch` supplies the real
    instance, so `fn` on the tool is the function itself and not the binding.
    """
    signature_only = functools.partial(fn, None)
    tool = Tool.from_function(
        signature_only,
        name=fn.__name__,
        description=inspect.getdoc(fn) or "",
        # Never read: `dispatch` runs no transaction. Set to what is true, so a
        # later reader of the registry is not misled.
        transaction=False,
    )
    return dataclasses.replace(tool, fn=fn, control=True)


# Registered on every `World` at construction. Built once, at import: it carries
# no world and no instance, so one tool serves every world in the process.
TOOLS: tuple[Tool, ...] = (_control_tool(controller_run_sql),)
