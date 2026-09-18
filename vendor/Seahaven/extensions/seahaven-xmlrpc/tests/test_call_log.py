"""The call log: the DDL seam and the instance-startup seam, from the outside.

Read through `instance.inspect()`, the framework's read-only handle, because that
is how an eval reads state it is grading -- not through a tool written to make
the assertion easy.
"""

import xmlrpc.client
from pathlib import Path

import pytest

import seahaven
import seahaven_xmlrpc
from conftest import NOW, rpc

pytestmark = pytest.mark.seahaven(fixture=None, now=NOW)

# `rowid`, not `called_at`: the clock is frozen, so every row of one instance
# carries the same instant and ordering by it would be resting on SQLite's
# scan order.
_LOG = f"SELECT method, client, called_at FROM {seahaven_xmlrpc.CALL_LOG_TABLE} ORDER BY rowid"


def _log(instance: seahaven.Instance) -> list[dict[str, object]]:
    return instance.inspect().rows(_LOG)


def test_a_call_is_logged_with_the_method_and_the_instances_clock(
    instance: seahaven.Instance,
) -> None:
    rpc(instance, "tracker.ping", "hello")
    rpc(instance, "user.list")
    assert [(row["method"], row["called_at"]) for row in _log(instance)] == [
        ("tracker.ping", NOW),
        ("user.list", NOW),
    ]


def test_the_client_defaults_when_reset_does_not_name_one(instance: seahaven.Instance) -> None:
    """A world that registers the hook and an eval that passes nothing still gets a row."""
    rpc(instance, "tracker.ping", "hello")
    assert _log(instance)[0]["client"] == seahaven_xmlrpc.UNKNOWN_CLIENT


@pytest.mark.seahaven(fixture=None, now=NOW, xmlrpc_client="acme-crm/2.4")
def test_the_client_comes_from_the_reset_argument(instance: seahaven.Instance) -> None:
    """§21 point 3: a `reset()` keyword argument reaching an extension's startup hook.

    The marker's keywords are `world.instance(...)`'s, which are `reset`'s over
    OpenEnv, so this is the same path an eval takes to say which client it is
    pretending to be.
    """
    rpc(instance, "tracker.ping", "hello")
    assert _log(instance)[0]["client"] == "acme-crm/2.4"


def test_a_faulted_call_leaves_no_log_row(instance: seahaven.Instance) -> None:
    """The trail is of what the server did, and a faulted call did nothing.

    The row is written *before* the handler runs, so this is a statement about the
    rollback and not about the order of the writes: the log row and the handler's
    own writes go back together.
    """
    with pytest.raises(xmlrpc.client.Fault):
        rpc(instance, "user.get", "nobody")
    assert _log(instance) == []


def test_the_log_is_off_unless_the_world_asks_for_it(tmp_path: Path) -> None:
    """`log_calls` defaults to off, so a world that wants no log needs no table.

    On a world whose schema is ProjectTracker's alone -- no `CALL_LOG_DDL` -- a
    logging endpoint would fail on every call with "no such table". This one does
    not, which is the only way to assert the default from the outside.
    """
    world = seahaven.World(
        "no_log",
        "1.0.0",
        seahaven.sql_files("projecttracker", "schema"),
        fixtures_dir=tmp_path / "fixtures",
        work_dir=tmp_path / "work",
        state_format="seahaven.state/1",
    )
    world.tool(seahaven_xmlrpc.xmlrpc_call(methods={"tracker.ping": lambda ctx, value: value}))
    with world.instance(None, now=NOW) as live:
        assert rpc(live, "tracker.ping", "hello", tool="xmlrpc_call") == "hello"


def test_asking_for_the_log_without_the_ddl_is_the_authors_mistake(tmp_path: Path) -> None:
    """And is told as one, on the first call, rather than rendered as a fault.

    Nothing can catch this at registration -- there is no schema then -- and left
    alone it is the worst shape a failure can have here: "no such table" is a
    `DbError`, `render_faults` turns a `DbError` into a plausible
    `APPLICATION_ERROR` fault, and the framework logs nothing because a `DbError`
    is a `ToolError`. Every call of the eval would answer with what looks like the
    product refusing.
    """
    world = seahaven.World(
        "log_without_a_table",
        "1.0.0",
        seahaven.sql_files("projecttracker", "schema"),
        fixtures_dir=tmp_path / "fixtures",
        work_dir=tmp_path / "work",
        state_format="seahaven.state/1",
    )
    world.middleware(seahaven_xmlrpc.render_faults(tools=["xmlrpc_call"]))
    world.tool(
        seahaven_xmlrpc.xmlrpc_call(
            methods={"tracker.ping": lambda ctx, value: value}, log_calls=True
        )
    )
    with world.instance(None, now=NOW) as live, pytest.raises(seahaven.WorldBug) as raised:
        rpc(live, "tracker.ping", "hello", tool="xmlrpc_call")
    assert seahaven_xmlrpc.CALL_LOG_TABLE in str(raised.value)
    assert "CALL_LOG_DDL" in str(raised.value)
