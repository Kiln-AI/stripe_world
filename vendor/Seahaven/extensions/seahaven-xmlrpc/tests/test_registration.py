"""What the factory refuses, and what it publishes.

Everything that can be wrong with an endpoint is found at registration, which is
the rule `Tool.from_function` and `seahaven/helpers/run_sql.py` already follow: a
world author's mistake belongs at import, with a message naming it, and never on
the first call in an eval.
"""

from typing import Any

import pytest

import seahaven
import seahaven_xmlrpc
from conftest import RPC

_METHODS = {"noop": lambda ctx: True}


def test_the_tool_publishes_a_schema_an_agent_can_read() -> None:
    """One string argument, capped, required, and nothing else accepted."""
    tool = seahaven_xmlrpc.xmlrpc_call(methods=_METHODS)
    schema = tool.listing()["input_schema"]
    assert schema["required"] == ["body"]
    assert schema["properties"]["body"]["type"] == "string"
    assert schema["properties"]["body"]["minLength"] == 1
    assert schema["properties"]["body"]["maxLength"] == seahaven_xmlrpc.MAX_BODY
    assert schema["additionalProperties"] is False


def test_the_default_description_names_the_methods() -> None:
    """The endpoint's surface is its method list, and the JSON schema cannot carry it.

    The schema describes one string; every method the world serves is inside it.
    An agent that could not read the names anywhere would have to guess them.
    """
    tool = seahaven_xmlrpc.xmlrpc_call(methods={"b.two": _METHODS["noop"], "a.one": lambda ctx: 1})
    assert tool.description.endswith("Methods: a.one, b.two.")


def test_the_world_chooses_the_name_the_description_and_the_cap() -> None:
    """§21: a factory that lets the world choose them takes them as parameters."""
    tool = seahaven_xmlrpc.xmlrpc_call(
        methods=_METHODS, name="legacy_api", description="The 2009 API.", max_body=99
    )
    assert tool.name == "legacy_api"
    assert tool.description == "The 2009 API."
    assert tool.listing()["input_schema"]["properties"]["body"]["maxLength"] == 99


def test_the_method_table_is_copied_and_not_kept() -> None:
    """A method added to the world's dict after registration is not served.

    The endpoint published a description listing what it serves; a table that
    could change under it would make that description a lie.
    """
    methods: dict[str, Any] = dict(_METHODS)
    tool = seahaven_xmlrpc.xmlrpc_call(methods=methods)
    methods["late"] = lambda ctx: True
    assert "late" not in tool.description


def test_the_registered_tool_is_the_one_the_factory_built(tmp_path: Any) -> None:
    """`world.tool(tool_obj)` registers a factory's tool as it is."""
    world = seahaven.World(
        "registers",
        "1.0.0",
        seahaven.sql_files("projecttracker", "schema"),
        fixtures_dir=tmp_path / "fixtures",
        work_dir=tmp_path / "work",
        state_format="seahaven.state/1",
    )
    tool = seahaven_xmlrpc.xmlrpc_call(methods=_METHODS, name=RPC)
    assert world.tool(tool) is tool
    assert world.tools[RPC] is tool


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        pytest.param({"methods": {}}, "at least one method", id="no methods at all"),
        pytest.param({"methods": {"": _METHODS["noop"]}}, "needs a name", id="a nameless method"),
        pytest.param(
            {"methods": {"  ": _METHODS["noop"]}}, "needs a name", id="a whitespace method name"
        ),
        pytest.param(
            {"methods": {"x": "not a function"}}, "not callable", id="a handler that is not one"
        ),
        pytest.param(
            {"methods": {"x": lambda: True}},
            "takes the context as its first parameter",
            id="a handler that cannot take the context",
        ),
        pytest.param(
            {"methods": {"x": lambda *, ctx: True}},
            "takes the context as its first parameter",
            id="a handler taking the context by keyword",
        ),
        pytest.param({"methods": _METHODS, "max_body": 0}, "max_body", id="a cap of nothing"),
        pytest.param(
            {"methods": _METHODS, "transaction": False},
            "transaction=False",
            id="the switch that turns the rollback off",
        ),
    ],
)
def test_an_endpoint_that_cannot_work_is_refused_at_registration(
    kwargs: dict[str, Any], expected: str
) -> None:
    with pytest.raises(seahaven.WorldBug) as raised:
        seahaven_xmlrpc.xmlrpc_call(**kwargs)
    assert expected in str(raised.value)


def test_a_handler_taking_the_parameters_variadically_is_accepted() -> None:
    """`(ctx, *params)` is the shape a generic handler has, and it must register."""
    assert seahaven_xmlrpc.xmlrpc_call(methods={"x": lambda *args: args}) is not None


def test_the_endpoint_is_always_registered_inside_a_transaction() -> None:
    """The guarantee the whole design rests on, read off the tool it built.

    A fault is raised out of the tool so that `invoke`'s transaction rolls the
    handler's writes back. `transaction=False` is refused above; this is the other
    half of it, that the tool the factory returns says so.
    """
    assert seahaven_xmlrpc.xmlrpc_call(methods=_METHODS).transaction is True


def test_render_faults_must_name_a_tool() -> None:
    """A middleware covering nothing would be registered and silently do nothing."""
    with pytest.raises(seahaven.WorldBug) as raised:
        seahaven_xmlrpc.render_faults(tools=[])
    assert "at least one tool" in str(raised.value)
