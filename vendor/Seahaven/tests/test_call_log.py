"""The call log: one entry per dispatched call, the name as called, the arguments as carried.

Kept for every instance whether or not a format ever publishes it. What is
pinned here is the boundary of "dispatched": a name the world refused, a control
tool, a tool listing and a nested call through a handle are not entries, while a
call that raised is one, with the message of what it raised.
"""

import threading
from pathlib import Path

import pytest

from seahaven.call import Call, Handler
from seahaven.changes import CallRecord
from seahaven.ctx import Ctx
from seahaven.errors import ArgumentError, UnknownTool, WorldBug
from seahaven.instances import Instance
from seahaven.world import World
from tests.conftest import Boom, build_world
from tests.test_changes import add

pytestmark = pytest.mark.filterwarnings("ignore:controller_run_sql is deprecated")


def names(instance: Instance) -> list[str]:
    return [record.tool for record in instance.call_log()]


def test_the_call_log_is_empty_before_any_call(instance: Instance) -> None:
    assert instance.call_log() == []


def test_one_entry_per_dispatched_call_in_dispatch_order(instance: Instance) -> None:
    add(instance, "n1")
    instance.call("rows", sql="SELECT 1")
    add(instance, "n2")

    assert instance.call_log() == [
        CallRecord(
            tool="execute",
            arguments={"sql": "INSERT INTO notes VALUES ('n1', 'a body', 0)"},
            error=None,
        ),
        CallRecord(tool="rows", arguments={"sql": "SELECT 1"}, error=None),
        CallRecord(
            tool="execute",
            arguments={"sql": "INSERT INTO notes VALUES ('n2', 'a body', 0)"},
            error=None,
        ),
    ]


def test_the_entry_is_the_call_and_the_ordinal_is_its_index(instance: Instance) -> None:
    add(instance, "n1")
    instance.call("rows", sql="SELECT 1")
    add(instance, "n2")

    for record in instance.change_log():
        assert record.i is not None
        assert record.key["id"] in instance.call_log()[record.i].arguments["sql"]
    assert len(instance.call_log()) == instance.call_count == 3


def test_a_tool_error_is_logged_with_its_message(instance: Instance) -> None:
    with pytest.raises(Boom):
        instance.call("write_then_fail", sql="INSERT INTO notes VALUES ('n1', 'body', 0)")

    (record,) = instance.call_log()

    assert record.tool == "write_then_fail"
    assert record.error == "it did not work out"


def test_any_other_exception_is_logged_with_its_message(instance: Instance) -> None:
    with pytest.raises(ValueError):
        instance.call("crash")

    assert instance.call_log() == [
        CallRecord(tool="crash", arguments={}, error="a bug in world code")
    ]
    assert len(instance.call_log()) == instance.call_count == 1


def test_a_record_says_which_class_its_error_was(instance: Instance) -> None:
    """The class is what the published shape sorts on; the message is what an author reads."""
    with pytest.raises(Boom):
        instance.call("write_then_fail", sql="INSERT INTO notes VALUES ('n1', 'body', 0)")
    with pytest.raises(ValueError):
        instance.call("crash")

    written_for_the_agent, accident = instance.call_log()

    assert (written_for_the_agent.tool_error, written_for_the_agent.error) == (
        True,
        "it did not work out",
    )
    assert (accident.tool_error, accident.error) == (False, "a bug in world code")
    assert written_for_the_agent.to_dict()["error"] == "it did not work out"
    assert accident.to_dict()["error"] == "internal error"


def test_a_refused_name_a_control_tool_and_a_listing_are_not_entries(instance: Instance) -> None:
    with pytest.raises(UnknownTool):
        instance.call("no_such_tool")
    instance.call("controller_run_sql", sql="SELECT 1")
    instance.tools()

    assert instance.call_log() == []
    assert instance.call_count == 0


def test_a_function_the_typed_call_cannot_resolve_is_not_an_entry(instance: Instance) -> None:
    def not_a_tool(ctx: Ctx) -> None:
        """Never registered anywhere."""

    with pytest.raises(WorldBug):
        instance.call(not_a_tool)

    assert instance.call_log() == []


def test_a_typed_call_logs_its_arguments_by_name(tmp_path: Path) -> None:
    world = build_world(tmp_path)

    @world.tool
    def label(ctx: Ctx, name: str, tags: list[str]) -> dict[str, object]:
        """A tool with a positional-friendly signature."""
        return {"name": name, "tags": tags}

    with world.instance(None) as instance:
        instance.call(label, "x", tags=["a", "b"])

        assert instance.call_log() == [
            CallRecord(tool="label", arguments={"name": "x", "tags": ["a", "b"]}, error=None)
        ]


def test_the_arguments_are_what_the_caller_passed_and_not_a_rendering(tmp_path: Path) -> None:
    """In process nothing is serialised: `bytes` come back as the `bytes` they were, not base64."""
    world = build_world(tmp_path)

    @world.tool
    def store(ctx: Ctx, data: bytes) -> int:
        """Take a value that has a JSON rendering and is not one."""
        return len(data)

    with world.instance(None) as instance:
        instance.call("store", data=b"\x00\xff")

        (record,) = instance.call_log()

        assert record.arguments == {"data": b"\x00\xff"}


def test_a_call_refused_by_validation_is_logged_as_it_was_made(instance: Instance) -> None:
    """Validation runs inside the chain: the call was dispatched, with the arguments it carried."""
    with pytest.raises(ArgumentError):
        instance.call("execute", sql=42)

    (record,) = instance.call_log()

    assert record.arguments == {"sql": 42}
    assert record.error is not None
    assert instance.call_count == 1


def test_an_argument_that_cannot_be_deep_copied_is_still_recorded(tmp_path: Path) -> None:
    """The bookkeeping must not fail a call that would otherwise run, nor lose its entry.

    A parameter annotated `object` takes anything an in-process caller has,
    including values `deepcopy` refuses. The record keeps a shallow copy of the
    mapping instead, and the call runs as it always did.
    """
    world = build_world(tmp_path)
    lock = threading.Lock()

    @world.tool
    def hold(ctx: Ctx, thing: object) -> str:
        """Take a value no copy can be made of."""
        return type(thing).__name__

    with world.instance(None) as instance:
        assert instance.call("hold", thing=lock) == "lock"

        (record,) = instance.call_log()

        assert record.arguments == {"thing": lock}
        assert len(instance.call_log()) == instance.call_count == 1


def test_an_argument_the_tool_refuses_is_reported_as_an_argument_error(tmp_path: Path) -> None:
    """A bad argument is the world's `ArgumentError`, not a failure inside the framework."""
    world = build_world(tmp_path)
    lock = threading.Lock()

    @world.tool
    def counted(ctx: Ctx, n: int) -> int:
        """Take something the caller's value is not."""
        return n

    with world.instance(None) as instance:
        with pytest.raises(ArgumentError):
            instance.call("counted", n=lock)

        (record,) = instance.call_log()

        assert record.arguments == {"n": lock}
        assert len(instance.call_log()) == instance.call_count == 1


def test_a_tool_that_mutates_its_arguments_does_not_alter_the_record(tmp_path: Path) -> None:
    world = build_world(tmp_path)

    @world.tool
    def consume(ctx: Ctx, tags: list[str]) -> int:
        """Empty the list it was handed."""
        count = len(tags)
        tags.clear()
        return count

    with world.instance(None) as instance:
        given = ["a", "b"]
        instance.call("consume", tags=given)

        (record,) = instance.call_log()

        assert record.arguments == {"tags": ["a", "b"]}


def test_a_middleware_that_rewrites_the_arguments_does_not_alter_the_record(world: World) -> None:
    """The copy is taken before the chain runs: the record is the call as made."""

    @world.middleware
    def rewrite(ctx: Ctx, call: Call, next_: Handler) -> object:
        return next_(ctx, call.with_arguments(sql="SELECT 2"))

    with world.instance(None) as instance:
        assert instance.call("rows", sql="SELECT 1 AS n") == [{"2": 2}]

        assert instance.call_log() == [
            CallRecord(tool="rows", arguments={"sql": "SELECT 1 AS n"}, error=None)
        ]


def test_the_log_hands_out_a_fresh_list(instance: Instance) -> None:
    add(instance, "n1")
    instance.call_log().clear()

    assert len(instance.call_log()) == 1


def test_a_destroyed_instance_has_no_call_log(instance: Instance) -> None:
    add(instance, "n1")
    instance.destroy()

    with pytest.raises(WorldBug, match="has been destroyed"):
        instance.call_log()


# ------------------------------------------------------------------ composition


def test_the_name_is_the_root_surfaces_name_prefix_included(emporium_instance: Instance) -> None:
    live = emporium_instance
    live.call("pay_create_charge", amount=100)
    live.call("eu_create_charge", amount=200, currency="eur")

    assert live.call_log() == [
        CallRecord(tool="pay_create_charge", arguments={"amount": 100}, error=None),
        CallRecord(
            tool="eu_create_charge", arguments={"amount": 200, "currency": "eur"}, error=None
        ),
    ]


def test_a_nested_call_adds_no_entry(emporium_instance: Instance) -> None:
    """`settle_order` calls into the shop and into payments; the caller made one call."""
    live = emporium_instance
    live.call("settle_order", total=250)

    assert names(live) == ["settle_order"]
    assert live.call_count == 1
