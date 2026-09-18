"""What the middleware is for: a failure becoming a response, and rolling back first.

The two halves of the contract this file pins are that a fault is a document the
caller reads, and that a call which faulted wrote nothing. The second is the
reason rendering is a middleware and not part of the tool, so it is asserted from
both sides: the rows, and the change log an eval is graded on.
"""

import xmlrpc.client
from collections.abc import Callable
from typing import Any

import pytest

import seahaven
import seahaven_xmlrpc
from conftest import NOW, RPC, method_call, rpc
from tracker_rpc.methods import NOT_FOUND

pytestmark = pytest.mark.seahaven(fixture=None, now=NOW)

_ADA = ("ada@tracker.invalid", "Ada", "admin")


def _user_records(instance: seahaven.Instance) -> list[seahaven.LogRecord]:
    return [record for record in instance.change_log() if record.table == "users"]


def test_a_fault_a_handler_raises_carries_the_worlds_own_code(
    instance: seahaven.Instance,
) -> None:
    """A product's fault codes are the product's vocabulary, not the protocol's."""
    with pytest.raises(xmlrpc.client.Fault) as raised:
        rpc(instance, "user.get", "nobody")
    assert raised.value.faultCode == NOT_FOUND
    assert raised.value.faultString == "no such user: nobody"


@pytest.mark.parametrize(
    "params",
    [
        pytest.param(_ADA, id="an email the UNIQUE constraint refuses"),
        pytest.param(("bob@tracker.invalid", "Bob", "wizard"), id="a role the CHECK refuses"),
    ],
)
def test_a_db_error_under_a_handler_comes_back_as_an_application_error_fault(
    instance: seahaven.Instance, params: tuple[str, ...]
) -> None:
    """The failure `functional_spec.md` §21 names for this middleware, both ways it happens."""
    rpc(instance, "user.create", *_ADA)
    with pytest.raises(xmlrpc.client.Fault) as raised:
        rpc(instance, "user.create", *params)
    assert raised.value.faultCode == seahaven_xmlrpc.APPLICATION_ERROR
    # The `DbError`'s own message, never SQLite's text: engine text reaches an
    # agent only where a world decides it should.
    assert raised.value.faultString == "database error"


def test_a_faulted_call_writes_nothing(instance: seahaven.Instance) -> None:
    """Why rendering is outside the transaction, asserted on the state.

    `user.create` inserts and only then meets the `UNIQUE` violation, so a fault
    rendered *inside* the call would return normally and commit the row it had
    already written. There would be two Adas.

    The log is read for the `users` table alone, because the reading itself is a
    call and this world logs every call: what is asserted is that the faulted
    call left nothing in the world's own tables. The call log's side of the same
    rollback is `test_call_log.py`'s.
    """
    rpc(instance, "user.create", *_ADA)
    before = _user_records(instance)
    with pytest.raises(xmlrpc.client.Fault):
        rpc(instance, "user.create", "ada@tracker.invalid", "Ada Again", "member")
    assert [user["name"] for user in rpc(instance, "user.list")] == ["Ada"]
    assert _user_records(instance) == before


def test_a_handler_that_returns_what_xml_rpc_cannot_carry_is_a_world_bug(
    probe: Callable[..., seahaven.World],
) -> None:
    """The author's mistake, not the agent's, so it is not a fault and not `INTERNAL`.

    ProjectTracker's error handler re-raises `WorldBug` untouched, which is what
    makes this assertion a statement about both layers: the extension raises one,
    and the world's handler is right to let it past.
    """

    def returns_a_set(ctx: seahaven.Ctx) -> Any:
        return {1, 2}

    def returns_a_number_too_big_for_an_int(ctx: seahaven.Ctx) -> Any:
        # XML-RPC's `<int>` is 32 bits, which a rowid or a millisecond timestamp
        # outgrows: the same mistake as a type XML-RPC has never heard of.
        return 2**40

    world = probe({"a set": returns_a_set, "a big int": returns_a_number_too_big_for_an_int})
    with world.instance(None, now=NOW) as live:
        for method in ("a set", "a big int"):
            with pytest.raises(seahaven.WorldBug) as raised:
                live.call(RPC, body=method_call(method))
            assert method in str(raised.value)


def test_an_unexpected_exception_reaches_the_worlds_error_handler(
    probe: Callable[..., seahaven.World],
) -> None:
    """An XML-RPC fault is a protocol outcome; a bug in world code is not one."""

    def divides_by_zero(ctx: seahaven.Ctx) -> Any:
        return 1 // 0

    world = probe({"boom": divides_by_zero})
    with world.instance(None, now=NOW) as live, pytest.raises(seahaven.ToolError) as raised:
        live.call(RPC, body=method_call("boom"))
    assert raised.value.code == "INTERNAL"


def test_the_middleware_leaves_every_other_tool_alone(
    probe: Callable[..., seahaven.World],
) -> None:
    """A `DbError` under a tool that never spoke XML-RPC is still the world's own failure."""

    def counts_a_table_that_is_not_there(ctx: seahaven.Ctx) -> int:
        row = ctx.db.one("SELECT count(*) AS n FROM sprints")
        return 0 if row is None else int(row["n"])

    world = probe({"noop": lambda ctx: True}, tools=(counts_a_table_that_is_not_there,))
    with world.instance(None, now=NOW) as live, pytest.raises(seahaven.ToolError) as raised:
        live.call("counts_a_table_that_is_not_there")
    assert raised.value.code == "INTERNAL"


def test_without_the_middleware_a_fault_is_raised_rather_than_rendered(
    probe: Callable[..., seahaven.World],
) -> None:
    """Half the pair registered, and what the world gets for it.

    Not nothing -- `XmlRpcFault` is a `ToolError`, so the agent is still told
    something with a code -- but not XML-RPC either, which is the documented
    consequence of registering the tool without `render_faults`.
    """
    world = probe({"noop": lambda ctx: True}, render_faults=False)
    with world.instance(None, now=NOW) as live, pytest.raises(seahaven.ToolError) as raised:
        live.call(RPC, body=method_call("absent"))
    assert raised.value.code == seahaven_xmlrpc.TOOL_ERROR_CODE
    assert raised.value.details == {
        "faultCode": seahaven_xmlrpc.METHOD_NOT_FOUND,
        "faultString": "no such method: absent",
    }


def test_the_products_error_handler_stays_outermost(
    world: seahaven.World, instance: seahaven.Instance
) -> None:
    """Registration order is chain order, and this world registered them in it.

    Were `render_faults` outermost, ProjectTracker's handler would meet the
    `DbError` first and this call would come back as `INTERNAL` rather than as a
    fault document. The assertion is the order, read off the world.
    """
    names = [getattr(middleware, "__name__", "") for middleware in world.middlewares]
    assert names == ["error_handler", "render_xmlrpc_faults"]
    rpc(instance, "user.create", *_ADA)
    with pytest.raises(xmlrpc.client.Fault):
        rpc(instance, "user.create", *_ADA)
