"""`SeahavenClient`: the parsers on their own, and the whole client on a server.

The parsers are unit-tested against the frames OpenEnv actually sends, because a
parser that is only ever exercised through a happy path proves nothing about the
frame it is given. Everything below them is driven against a real server in both
of the base client's modes, because "sync and async follow the base client's
dual mode" is a claim about `_dispatch` and is not true by construction.
"""

import asyncio
from typing import Any

import pytest

from seahaven.world import World
from tests.conftest import INSTANT_ISO

# The subpackage and not `openenv`: what this module imports is
# `seahaven.openenv`, so that is what has to import for the tests below to mean
# anything. Only an `ImportError` skips -- an extra that is absent, or installed
# and unimportable. Anything else raises, and CI asserts this import separately,
# because an installed extra that skips quietly is a green run that tested none
# of this.
pytest.importorskip(
    "seahaven.openenv", exc_type=ImportError, reason="the serve extra does not import here"
)

from openenv.core.env_server.mcp_types import CallToolAction, ListToolsAction

from seahaven.openenv import SeahavenClient, SeahavenObservation, SeahavenState
from tests.serving import serving

UNCONNECTED = "http://127.0.0.1:1"


@pytest.fixture
def client() -> SeahavenClient:
    """A client that never connects: enough to drive the three parsers."""
    return SeahavenClient(base_url=UNCONNECTED)


def test_the_ping_timeout_defaults_to_two_minutes(client: SeahavenClient) -> None:
    """Wider than OpenEnv's stock `20.0`: see `SeahavenClient.__init__` for why."""
    assert client._websocket_ping_timeout_s == 120.0
    # The interval is untouched -- only the timeout gets a different default.
    assert client._websocket_ping_interval_s == 20.0


def test_an_explicit_ping_timeout_still_wins(client: SeahavenClient) -> None:
    explicit = SeahavenClient(base_url=UNCONNECTED, websocket_ping_timeout_s=5.0)
    assert explicit._websocket_ping_timeout_s == 5.0


# --- the parsers -----------------------------------------------------------


def test_step_payload_is_the_action_as_a_dict(client: SeahavenClient) -> None:
    assert client._step_payload(CallToolAction(tool_name="ping", arguments={"message": "hi"})) == {
        "type": "call_tool",
        "tool_name": "ping",
        "arguments": {"message": "hi"},
        "metadata": {},
    }
    assert client._step_payload(ListToolsAction()) == {"type": "list_tools", "metadata": {}}


def test_parse_result_answers_a_typed_observation(client: SeahavenClient) -> None:
    result = client._parse_result(
        {
            "observation": {"tool_name": "ping", "result": {"n": 1}, "error": None, "metadata": {}},
            "reward": None,
            "done": False,
        }
    )
    assert isinstance(result.observation, SeahavenObservation)
    assert (result.observation.tool_name, result.observation.result) == ("ping", {"n": 1})
    assert (result.reward, result.done, result.metadata) == (None, False, None)


def test_parse_result_carries_an_error_through(client: SeahavenClient) -> None:
    triple = {"code": "not_found", "message": "no note n9", "details": {"key": "n9"}}
    result = client._parse_result(
        {
            "observation": {
                "tool_name": "fetch",
                "error": {"error_type": "execution_error", "message": "no note n9"},
                "metadata": {"seahaven_error": triple},
            }
        }
    )
    assert isinstance(result.observation, SeahavenObservation)
    assert result.observation.error is not None
    assert result.observation.error.message == "no note n9"
    assert result.observation.seahaven_error == triple
    assert result.observation.result is None
    assert result.done is False


def test_an_observation_with_no_seahaven_error_answers_none(client: SeahavenClient) -> None:
    """`seahaven_error` reads `metadata`, which any frame may fill with anything."""
    result = client._parse_result({"observation": {"tool_name": "ping", "result": 1}})
    assert isinstance(result.observation, SeahavenObservation)
    assert result.observation.seahaven_error is None


# One whole document as a frame, so the parser is driven against the shape the
# server really sends rather than a field at a time.
DOCUMENT_FRAME: dict[str, Any] = {
    "episode_id": "ep-1",
    "step_count": 3,
    "format": "seahaven.state/1",
    "seahaven_version": "0.0.1",
    "world": {"name": "testworld", "version": "1.0.0"},
    "composition": {
        "main": {
            "world": "testworld",
            "world_version": "1.0.0",
            "scope": None,
            "aliases": [],
            "schema_hash": "b" * 64,
            "frozen_world_version": None,
        },
        "payments": {
            "world": "payments",
            "world_version": "1.4.0",
            "scope": "eu",
            "aliases": ["shop/payments"],
            "schema_hash": "c" * 64,
            "frozen_world_version": "1.3.0",
        },
    },
    "fixture": {
        "id": "start",
        "nodes": {"main": {"file_sha256": "a" * 64}, "payments": {"file_sha256": "d" * 64}},
    },
    "seed": 7,
    "now": INSTANT_ISO,
    "startup": {"tenant": "globex"},
    "call_count": 2,
    "state": {
        "db": {
            "log": [
                {
                    "i": 0,
                    "world": "main",
                    "table": "notes",
                    "op": "insert",
                    "key": {"id": "n1"},
                    "before": None,
                    "after": {"id": "n1", "body": "a body", "n": 0},
                }
            ]
        }
    },
}


def test_parse_state_answers_a_typed_state(client: SeahavenClient) -> None:
    """The whole document, nested models and the format's own `state` included."""
    state = client._parse_state(DOCUMENT_FRAME)
    assert isinstance(state, SeahavenState)
    assert (state.episode_id, state.step_count) == ("ep-1", 3)
    assert (state.format, state.seahaven_version) == ("seahaven.state/1", "0.0.1")
    assert (state.world.name, state.world.version) == ("testworld", "1.0.0")
    assert state.composition is not None
    assert list(state.composition) == ["main", "payments"]
    assert state.composition["payments"].aliases == ["shop/payments"]
    assert state.composition["payments"].frozen_world_version == "1.3.0"
    assert state.fixture is not None
    assert state.fixture.id == "start"
    assert state.fixture.nodes["payments"].file_sha256 == "d" * 64
    assert (state.seed, state.now, state.call_count) == (7, INSTANT_ISO, 2)
    assert state.startup == {"tenant": "globex"}
    # `state` is untyped on purpose: the format owns its shape, so it arrives as
    # the dict the formatter produced and nothing validates it here.
    assert state.state["db"]["log"][0]["key"] == {"id": "n1"}


def test_the_document_is_the_state_without_the_step_count(client: SeahavenClient) -> None:
    """What a harness saves as `final_state` (`functional_spec.md` §9)."""
    state = client._parse_state(DOCUMENT_FRAME)
    assert state.model_dump(exclude={"step_count"}) == {
        key: value for key, value in DOCUMENT_FRAME.items() if key != "step_count"
    }


def test_state_answers_none_for_the_fields_a_pre_reset_frame_leaves_null(
    client: SeahavenClient,
) -> None:
    """Before the first reset the document answers `null` for five envelope fields.

    They are declared with a default for that reason, so a harness that logs
    `state.fixture` every step does not fail on step zero -- and nor does a frame
    from a server that left one out entirely.
    """
    state = client._parse_state(
        {
            "episode_id": None,
            "step_count": 0,
            "format": "seahaven.state/1",
            "seahaven_version": "0.0.1",
            "world": {"name": "testworld", "version": "1.0.0"},
            "call_count": 0,
            "state": {"db": {"log": []}},
        }
    )
    assert (state.composition, state.fixture, state.seed, state.now, state.startup) == (
        None,
        None,
        None,
        None,
        None,
    )


def test_state_keeps_an_envelope_field_it_does_not_know(client: SeahavenClient) -> None:
    """`extra="allow"` is the compatibility contract: a newer server, an older client.

    A field added to the envelope is one a reader ignores (`functional_spec.md`
    §10), so parsing a frame that carries one answers the document rather than
    refusing it.
    """
    state = client._parse_state(DOCUMENT_FRAME | {"tomorrows_field": {"n": 1}})
    assert state.format == "seahaven.state/1"
    assert state.model_extra == {"tomorrows_field": {"n": 1}}


def test_state_refuses_a_frame_that_does_not_name_a_world(client: SeahavenClient) -> None:
    """`world` is envelope, and the envelope is not optional."""
    with pytest.raises(ValueError, match="world"):
        client._parse_state({key: value for key, value in DOCUMENT_FRAME.items() if key != "world"})


def test_a_reset_frame_parses_through_the_step_hook_with_only_its_metadata(
    client: SeahavenClient,
) -> None:
    """A reset frame comes through `_parse_result`, because the base client has one hook.

    The server answers a reset with a plain `Observation`, and this is what a
    typed client makes of that frame: a `SeahavenObservation` carrying the facts
    in `metadata`, with the three fields of a tool call left at their defaults.
    `reset(...).observation.result` is `None` at runtime, which is why the
    client is parameterised on `Observation` so that reading it does not
    type-check.
    """
    facts = {"fixture": "start", "now": INSTANT_ISO, "tools": 6}
    result = client._parse_result(
        {"observation": {"metadata": facts}, "reward": None, "done": False, "metadata": facts}
    )
    assert isinstance(result.observation, SeahavenObservation)
    assert result.observation.metadata == facts
    assert (result.observation.tool_name, result.observation.result) == ("", None)
    assert result.observation.error is None
    assert (result.reward, result.done, result.metadata) == (None, False, facts)


def test_the_observation_model_refuses_a_frame_it_does_not_know(client: SeahavenClient) -> None:
    """`SeahavenObservation` forbids extras, which is why `list_tools` does not use it."""
    with pytest.raises(ValueError, match="tools"):
        client._parse_result({"observation": {"tools": []}})


# --- the client, on a server -----------------------------------------------


def test_the_client_drives_a_world_synchronously(world: World) -> None:
    with serving(world) as url, SeahavenClient(base_url=url) as env:
        reset = env.reset(now=INSTANT_ISO)
        assert isinstance(reset.observation, SeahavenObservation)
        assert reset.observation.metadata["now"] == INSTANT_ISO
        assert reset.observation.result is None
        names = [tool["name"] for tool in env.list_tools()]
        assert "rows" in names and "controller_run_sql" not in names
        assert env.call("execute", sql="INSERT INTO notes VALUES ('n1', 'b', 0)").result == {
            "rowcount": 1
        }
        observation = env.call("rows", sql="SELECT id FROM notes")
        assert observation.result == [{"id": "n1"}]
        assert observation.error is None
        state = env.state()
        assert (state.world.name, state.world.version) == (world.name, world.version)
        assert (state.fixture, state.now) == (None, INSTANT_ISO)
        assert state.state["db"]["log"][0]["key"] == {"id": "n1"}


def test_the_client_drives_a_world_asynchronously(world: World) -> None:
    async def drive(url: str) -> None:
        async with SeahavenClient(base_url=url) as env:
            await env.reset(now=INSTANT_ISO)
            names = [tool["name"] for tool in await env.list_tools()]
            assert "rows" in names
            observation = await env.call("rows", sql="SELECT 1 AS n")
            assert observation.result == [{"n": 1}]
            state = await env.state()
            assert state.now == INSTANT_ISO

    with serving(world) as url:
        asyncio.run(drive(url))


def test_entering_the_client_asynchronously_also_connects(world: World) -> None:
    """`__aenter__`'s half of the same rule, which is a separate method."""

    async def drive(url: str) -> None:
        client = SeahavenClient(base_url=url)
        assert client._ws is None
        async with client as env:
            assert env is client
            assert env._ws is not None

    with serving(world) as url:
        asyncio.run(drive(url))


def test_call_answers_the_observation_and_never_raises_on_a_tool_error(world: World) -> None:
    with serving(world) as url, SeahavenClient(base_url=url) as env:
        env.reset()
        observation = env.call("no_such_tool")
        assert observation.result is None
        assert observation.seahaven_error == {
            "code": "unknown_tool",
            "message": "unknown tool: no_such_tool",
            "details": {"name": "no_such_tool"},
        }
        assert observation.error is not None
        assert observation.error.error_type.value == "tool_not_found"
        # And the session is still good: a tool error is data, not a failure.
        assert env.call("rows", sql="SELECT 1 AS n").result == [{"n": 1}]


def test_list_tools_answers_plain_dictionaries(world: World) -> None:
    with serving(world) as url, SeahavenClient(base_url=url) as env:
        env.reset()
        tools = env.list_tools()
        assert isinstance(tools, list)
        rows: dict[str, Any] = next(tool for tool in tools if tool["name"] == "rows")
        assert sorted(rows) == ["description", "input_schema", "name"]
        assert rows["input_schema"]["properties"]["sql"]["type"] == "string"


def test_the_client_is_a_context_manager_that_closes_its_session(world: World) -> None:
    with serving(world) as url:
        client = SeahavenClient(base_url=url)
        assert client._ws is None
        with client as env:
            # `__enter__` answers this client, not a bare `EnvClient`: the two
            # verbs the typed client exists for have to survive a `with`. And it
            # has connected on the way in -- the base connects lazily on the
            # first frame too, so without this the session would open on the
            # first `reset` and every timing a harness took would include it.
            assert env is client
            assert env._ws is not None
            env.reset()
        # The session is gone with the connection; a new client gets a new one.
        with SeahavenClient(base_url=url) as second:
            second.reset()
            assert second.call("rows", sql="SELECT * FROM notes").result == []
