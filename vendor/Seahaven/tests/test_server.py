"""The whole thing over a socket: uvicorn, websockets, sessions, the gate.

`test_env.py` proves the environment class and `test_client.py` proves the
client. This module proves the two of them together through the transport that
sits between, because that is where this project's defects have lived: a frame
that will not serialise, a session that is not a session, a lock that is held on
the wrong thread. Every test here starts a real server on a real port.

The stock `GenericEnvClient` is driven beside the typed one throughout. A world
that can only be reached by Seahaven's own client is not on the OpenEnv wire,
and the claim that it is is worth a test rather than a paragraph.
"""

import asyncio
import copy
import json
import logging
import re
import time
import urllib.error
import urllib.request
from collections.abc import Iterator, MutableMapping
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any

import emporium
import jsonschema
import pytest

import seahaven
from seahaven import instances
from seahaven.ctx import Ctx
from seahaven.errors import WorldBug
from seahaven.world import World
from tests.conftest import INSTANT_ISO, build_world

# The subpackage and not `openenv`: what this module imports is
# `seahaven.openenv`, so that is what has to import for the tests below to mean
# anything. Only an `ImportError` skips -- an extra that is absent, or installed
# and unimportable. Anything else raises, and CI asserts this import separately,
# because an installed extra that skips quietly is a green run that tested none
# of this.
pytest.importorskip(
    "seahaven.openenv", exc_type=ImportError, reason="the serve extra does not import here"
)

from fastapi import WebSocket
from fastapi.routing import APIWebSocketRoute
from openenv import GenericEnvClient
from openenv.core.env_server.mcp_types import (
    CallToolAction,
    CallToolObservation,
    ListToolsAction,
    ToolErrorType,
)
from openenv.core.mcp_client import MCPToolClient
from openenv.core.utils import convert_to_ws_url
from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import ConnectionClosedOK

from seahaven.openenv import _MCP_ROUTE, SeahavenClient, _refusing_mcp_websocket
from seahaven.openenv.env import SeahavenObservation, SeahavenState
from tests.serving import serving

pytestmark = pytest.mark.filterwarnings("ignore:controller_run_sql is deprecated")

# Long enough that two threads genuinely overlap inside SQLite on a warm cache,
# short enough that the gate tests stay in the same order of magnitude as the
# rest of the suite. The assertion is about overlap, never about duration.
SLOW_ROWS = 300_000

SESSIONS = 500

# How long a connection that has a slot is given to prove it is not being
# refused. A refusal arrives immediately; this only bounds the quiet case.
UNSOLICITED_TIMEOUT = 2.0

# How long the server is given to write its answer into a peer that has already
# gone. The write is on the server's own task, so the client returning proves
# nothing about it; this only bounds the quiet case.
WRITE_INTO_A_DEAD_PEER = 1.0

# The idle timeout the reaper test serves with, and how long it waits for the
# sweep. OpenEnv checks every `max(timeout / 4, 5.0)` seconds, so the wait is
# floored at that 5 seconds however small the timeout is.
REAPED_AFTER = 1.0
REAP_DEADLINE = 30.0

# OpenEnv's episode-control routes over plain HTTP, and the verb each answers.
HTTP_EPISODE_CONTROL = (("POST", "/reset"), ("POST", "/step"), ("GET", "/state"))

# Every method OpenEnv's `/mcp` dispatches -- `mcp_handler` in
# `openenv/core/env_server/http_server.py` handles these four and nothing
# else. All four are refused, so all four are asserted.
MCP_METHODS = ("tools/list", "tools/call", "openenv/session/create", "openenv/session/close")


def insert(id: str) -> str:
    return f"INSERT INTO notes VALUES ('{id}', 'a body', 0)"


def ids(observation: Any) -> list[str]:
    return [row["id"] for row in observation.result]


@pytest.fixture
def slow_world(tmp_path: Path) -> World:
    """A world with a tool that spends real time inside SQLite, for the gate."""
    world = build_world(tmp_path)

    @world.tool
    def slow(ctx: Ctx) -> dict[str, float]:
        """Burn time in the engine and report the wall clock either side of it."""
        started = time.perf_counter()
        ctx.db.one(
            "WITH RECURSIVE counter(x) AS ("
            "  SELECT 1 UNION ALL SELECT x + 1 FROM counter WHERE x < ?"
            ") SELECT count(*) AS n FROM counter",
            SLOW_ROWS,
        )
        return {"started": started, "ended": time.perf_counter()}

    return world


@pytest.fixture
def trivial_world(tmp_path: Path) -> Iterator[World]:
    """The smallest real world there is, for the sessions that are counted in hundreds."""
    world = World(
        "smoke",
        "1.0.0",
        "CREATE TABLE notes (id TEXT PRIMARY KEY) STRICT;",
        fixtures_dir=tmp_path / "fixtures",
        work_dir=tmp_path / "work",
        state_format="seahaven.state/1",
    )

    @world.tool
    def echo(ctx: Ctx, message: str = "pong") -> dict[str, str]:
        """Answer with what it was given."""
        return {"message": message}

    yield world


# --- the section 7 flow, both clients --------------------------------------


def test_the_typed_client_drives_a_session_end_to_end(world: World) -> None:
    fixture_id = _freeze(world)
    with serving(world) as url, SeahavenClient(base_url=url) as env:
        reset = env.reset(fixture=fixture_id, seed=7)
        assert isinstance(reset.observation, SeahavenObservation)
        assert reset.observation.result is None
        assert reset.observation.metadata == {
            "fixture": fixture_id,
            "now": INSTANT_ISO,
            "tools": 6,
        }
        assert [tool["name"] for tool in env.list_tools()] == [
            "execute",
            "rows",
            "write_then_fail",
            "crash",
            "mint",
            "now",
        ]
        assert ids(env.call("rows", sql="SELECT id FROM notes")) == ["n0"]
        env.call("execute", sql=insert("n1"))
        assert ids(env.call("rows", sql="SELECT id FROM notes ORDER BY id")) == ["n0", "n1"]
        state = env.state()
        assert (state.world.name, state.world.version) == (world.name, world.version)
        assert state.fixture is not None and state.fixture.id == fixture_id
        assert (state.now, state.step_count, state.call_count) == (INSTANT_ISO, 4, 3)


def test_the_stock_client_drives_the_same_session(world: World) -> None:
    """No Seahaven on the client side at all: dictionaries in, dictionaries out."""
    fixture_id = _freeze(world)
    with serving(world) as url, GenericEnvClient(base_url=url) as env:
        reset = env.reset(fixture=fixture_id)
        assert reset.observation["metadata"]["fixture"] == fixture_id
        assert reset.metadata == reset.observation["metadata"]
        listed = env.step(ListToolsAction().model_dump()).observation
        assert [tool["name"] for tool in listed["tools"]] == [
            "execute",
            "rows",
            "write_then_fail",
            "crash",
            "mint",
            "now",
        ]
        observation = env.step(
            CallToolAction(tool_name="rows", arguments={"sql": "SELECT id FROM notes"}).model_dump()
        ).observation
        assert observation == {
            "tool_name": "rows",
            "result": [{"id": "n0"}],
            "error": None,
            "metadata": {},
        }
        state = env.state()
        assert state["world"] == {"name": world.name, "version": world.version}
        assert state["fixture"]["id"] == fixture_id


def test_a_reset_frame_carries_its_facts_at_the_top_level_of_the_envelope(world: World) -> None:
    """The raw wire, with no client at all: `metadata` is a sibling of `observation`.

    OpenEnv's `serialize_observation` copies a non-empty `metadata` to the top
    level of the envelope so a client that knows nothing of an environment's
    observation class still finds it, and nothing does that for `result`. That
    hoist is the reason a reset answers its facts in `metadata`, so it is
    asserted here off the JSON itself rather than off either client's parse.
    """
    fixture_id = _freeze(world)
    with serving(world) as url:
        frame = asyncio.run(_raw_reset(url, fixture=fixture_id))
    assert frame["type"] == "observation"
    facts = {"fixture": fixture_id, "now": INSTANT_ISO, "tools": 6}
    assert frame["data"] == {
        "observation": {"metadata": facts},
        "reward": None,
        "done": False,
        "metadata": facts,
    }


async def _raw_reset(url: str, **arguments: Any) -> dict[str, Any]:
    """Reset a `/ws` session and answer the reply frame as the JSON it arrived as."""
    async with ws_connect(convert_to_ws_url(url) + "/ws", proxy=None) as sock:
        await sock.send(json.dumps({"type": "reset", "data": arguments}))
        return dict(json.loads(await sock.recv()))


def test_a_tool_error_reaches_the_stock_client_as_data(world: World) -> None:
    with serving(world) as url, GenericEnvClient(base_url=url) as env:
        env.reset()
        result = env.step(CallToolAction(tool_name="no_such_tool").model_dump())
        assert result.observation["error"] == {
            "error_type": "tool_not_found",
            "message": "unknown tool: no_such_tool",
        }
        assert result.observation["metadata"]["seahaven_error"]["code"] == "unknown_tool"
        assert result.done is False
        assert result.reward is None


# --- the shape of an error, against upstream's own models -------------------

# One call of each kind this server can answer, in one session: a result, a name
# the world does not have, arguments that do not validate, a world's own error,
# and a Python exception nobody planned for. Every assertion below is driven
# through this list, so a class that is added here is a class every one of them
# covers.
ERROR_CLASSES: list[CallToolAction] = [
    CallToolAction(tool_name="rows", arguments={"sql": "SELECT 1 AS n"}),
    CallToolAction(tool_name="no_such_tool"),
    CallToolAction(tool_name="rows", arguments={"sql": 7}),
    CallToolAction(tool_name="write_then_fail", arguments={"sql": "SELECT 1"}),
    CallToolAction(tool_name="crash"),
]

EXPECTED_TYPES = [None, "tool_not_found", "invalid_args", "execution_error", "execution_error"]


async def _raw_steps(url: str, actions: list[CallToolAction]) -> list[dict[str, Any]]:
    """One session: reset, then every action, answering each reply as it arrived."""
    async with ws_connect(convert_to_ws_url(url) + "/ws", proxy=None) as sock:
        await sock.send(json.dumps({"type": "reset", "data": {}}))
        await sock.recv()
        frames = []
        for action in actions:
            await sock.send(json.dumps({"type": "step", "data": action.model_dump()}))
            frames.append(dict(json.loads(await sock.recv())))
        return frames


def test_upstreams_own_observation_model_parses_every_frame(world: World) -> None:
    """`CallToolObservation` forbids extra keys, so this is the conformance gate.

    It is asserted off the raw JSON rather than off a client's parse, because the
    frame is what upstream's model is handed.
    """
    with serving(world) as url:
        frames = asyncio.run(_raw_steps(url, ERROR_CLASSES))

    assert "a bug in world code" not in json.dumps(frames, ensure_ascii=False)
    parsed = [CallToolObservation.model_validate(frame["data"]["observation"]) for frame in frames]
    assert [None if o.error is None else o.error.error_type.value for o in parsed] == EXPECTED_TYPES
    # The triple is on the frame all the same, in the one place `Observation`
    # leaves free, and OpenEnv's serializer hoists it beside the observation too.
    codes = [frame["data"]["observation"]["metadata"].get("seahaven_error") for frame in frames]
    assert [None if code is None else code["code"] for code in codes] == [
        None,
        "unknown_tool",
        "invalid_arguments",
        "boom",
        "internal",
    ]
    assert [frame["data"].get("metadata") for frame in frames[1:]] == [
        frame["data"]["observation"]["metadata"] for frame in frames[1:]
    ]


def test_upstreams_tool_client_reads_an_error_as_data_and_raises_legibly(world: World) -> None:
    """The client Seahaven used to break: `step` parses, `call_tool` raises in words.

    `MCPToolClient.call_tool` raises on any non-null `error`, conformant or not
    (`mcp_client.py` lines 538-542 in openenv 0.5.0). What the shape fixes is
    which failure: a `RuntimeError` naming the message and the type, rather than
    a pydantic `ValidationError` three fields deep in the parse. That parse is
    `MCPClientBase._parse_result`, whose `ToolError(**obs_data["error"])` is the
    line the old shape broke on.

    `use_production_mode` is turned off because this client otherwise opens its
    session over `/mcp`, which Seahaven refuses on purpose (see
    `seahaven/openenv/__init__.py`). Off, it drives `/ws` like every other
    client here and parses replies through the same MCP code path.
    """

    async def drive(url: str) -> None:
        client = MCPToolClient(base_url=url)
        client.use_production_mode = False
        # `client`, not what `async with` answers: the base client's `__aenter__`
        # is typed on `EnvClient`, which has neither of the two verbs under test.
        async with client:
            await client.reset()
            result = await client.step(CallToolAction(tool_name="no_such_tool"))
            observation = result.observation
            assert isinstance(observation, CallToolObservation)
            assert observation.error is not None
            assert observation.error.error_type is ToolErrorType.TOOL_NOT_FOUND
            assert observation.metadata["seahaven_error"]["details"] == {"name": "no_such_tool"}
            with pytest.raises(RuntimeError) as raised:
                await client.call_tool("no_such_tool")
            assert "unknown tool: no_such_tool" in str(raised.value)
            assert "tool_not_found" in str(raised.value)
            # And the session is still good: a tool error is data, not a failure.
            assert await client.call_tool("rows", sql="SELECT 1 AS n") == [{"n": 1}]

    with serving(world) as url:
        asyncio.run(drive(url))


def test_a_world_bug_reaches_the_client_as_an_error_frame_saying_nothing(tmp_path: Path) -> None:
    """The author's bug is loud on the wire, and says nothing on it.

    OpenEnv renders a raised exception as `WSErrorResponse(data={"message":
    str(e)})`, so the frame is the boundary: the call still hard-fails, and what
    the client is told is "internal error" and a correlation id. The session
    survives, as it did before.
    """
    world = build_world(tmp_path)

    @world.tool
    def misuse(ctx: Ctx) -> None:
        """Fail the way a broken world fails."""
        raise WorldBug("the world is wrong")

    with serving(world) as url:
        frames = asyncio.run(_raw_steps(url, [CallToolAction(tool_name="misuse")]))
        with SeahavenClient(base_url=url) as env:
            env.reset()
            with pytest.raises(RuntimeError) as raised:
                env.call("misuse")
            assert env.call("rows", sql="SELECT 1 AS n").result == [{"n": 1}]

    assert "the world is wrong" not in json.dumps(frames, ensure_ascii=False)
    assert frames[0]["type"] == "error"
    assert re.fullmatch(r"internal error \([0-9a-f]+\)", frames[0]["data"]["message"])
    assert "the world is wrong" not in str(raised.value)


def test_the_state_document_over_the_wire_carries_no_internals(tmp_path: Path) -> None:
    """The third boundary, driven end to end with one call of each class in one episode.

    `seahaven.state+calls/1` is the format that publishes the call log, and a
    harness reading a trace reads this document. What a world wrote for the agent
    is in it; a `WorldBug`'s wording and a Python exception's are not.
    """
    world = build_world(tmp_path)

    @world.tool
    def misuse(ctx: Ctx) -> None:
        """Fail the way a broken world fails."""
        raise WorldBug("the world is wrong")

    with serving(world) as url, SeahavenClient(base_url=url) as env:
        env.reset(state_format="seahaven.state+calls/1")
        env.call("write_then_fail", sql=insert("n1"))
        with pytest.raises(RuntimeError):
            env.call("misuse")
        env.call("crash")
        document = env.state()

    assert [entry["error"] for entry in document.state["calls"]] == [
        "it did not work out",
        "internal error",
        "internal error",
    ]
    rendered = json.dumps(document.model_dump(), default=str, ensure_ascii=False)
    assert "the world is wrong" not in rendered
    assert "a bug in world code" not in rendered


# --- the state document, over the wire: the gate ----------------------------

# `functional_spec.md` §9's confirmation step, and the gate this project's
# implementation plan put at this phase. Subclass fields travel over the
# websocket state message, and nothing above `SeahavenState` works unless that
# holds for a document carrying nested models, a composition keyed by node path
# and an arbitrarily deep `state`. So it is driven over a real socket, to the
# typed client and to the stock one, rather than assumed.

SCHEMA = json.loads((Path(__file__).parent / "state_v1.schema.json").read_text())

# The one episode both clients drive: a fixture, a seed, a named episode, a
# startup keyword and one write, so no envelope field below is left at its
# default.
EPISODE: dict[str, Any] = {"seed": 7, "episode_id": "ep-1", "tenant": "globex"}
WRITE = CallToolAction(tool_name="execute", arguments={"sql": insert("n1")})


@pytest.fixture
def document_world(tmp_path: Path) -> World:
    """The notes world with a startup keyword, so no envelope field is empty."""
    world = build_world(tmp_path)

    @world.instance_startup
    def tenant(ctx: Ctx, *, tenant: str = "acme") -> None:
        """Accept the keyword; the document reports it whatever the hook does."""

    return world


def expected_document(world: World, fixture_id: str) -> dict[str, Any]:
    """What `EPISODE` plus `WRITE` leaves behind, field by field, as §3.1 defines it."""
    fixture = {found.id: found for found in world.fixtures()}[fixture_id]
    return {
        "format": "seahaven.state/1",
        "seahaven_version": seahaven.__version__,
        "world": {"name": world.name, "version": world.version},
        "composition": {
            "main": {
                "world": world.name,
                "world_version": world.version,
                "scope": None,
                "aliases": [],
                "schema_hash": world.schema_hash,
                "frozen_world_version": None,
            }
        },
        "fixture": {"id": fixture_id, "nodes": {"main": {"file_sha256": fixture.meta.file_sha256}}},
        "episode_id": "ep-1",
        "seed": 7,
        "now": INSTANT_ISO,
        "startup": {"tenant": "globex"},
        "call_count": 1,
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


def test_the_whole_document_arrives_over_the_websocket(document_world: World) -> None:
    """Every field of `functional_spec.md` §3.1, with the value it has in process."""
    fixture_id = _freeze(document_world)
    with serving(document_world) as url, SeahavenClient(base_url=url) as env:
        env.reset(fixture=fixture_id, **EPISODE)
        env.call("execute", sql=insert("n1"))
        state = env.state()
    assert state.model_dump(exclude={"step_count"}) == expected_document(document_world, fixture_id)
    assert state.step_count == 1


def test_the_stock_client_sees_the_same_document(document_world: World) -> None:
    """No Seahaven on the client side at all: the document is plain JSON.

    The same episode driven twice against the same server answers the same
    document down to the last field, which is `functional_spec.md` §3.3's
    determinism read over the wire: the episode id is the one both were given,
    and everything else is the world's or the episode's.
    """
    fixture_id = _freeze(document_world)
    with serving(document_world) as url:
        with GenericEnvClient(base_url=url) as generic:
            generic.reset(fixture=fixture_id, **EPISODE)
            generic.step(WRITE.model_dump())
            stock = generic.state()
        with SeahavenClient(base_url=url) as typed:
            typed.reset(fixture=fixture_id, **EPISODE)
            typed.call("execute", sql=insert("n1"))
            document = typed.state().model_dump()
    assert stock == expected_document(document_world, fixture_id) | {"step_count": 1}
    assert stock == document


def test_the_document_over_the_wire_validates_against_the_published_schema(
    document_world: World,
) -> None:
    """What the stock client holds is a `seahaven.state/1` document and nothing else."""
    fixture_id = _freeze(document_world)
    with serving(document_world) as url, GenericEnvClient(base_url=url) as generic:
        generic.reset(fixture=fixture_id, **EPISODE)
        generic.step(WRITE.model_dump())
        stock = generic.state()
    jsonschema.validate({key: value for key, value in stock.items() if key != "step_count"}, SCHEMA)


def test_the_document_of_a_composite_arrives_whole(tmp_path: Path) -> None:
    """Four nodes over the wire, and a record from an added node's store in the log.

    The nested `composition` and the per-node `world` on a record are what a leaf
    world cannot prove: a document that flattened either would still pass every
    assertion above it.
    """
    world = copy.copy(emporium.world)
    world.work_dir = tmp_path / "work"
    with serving(world) as url, SeahavenClient(base_url=url) as env:
        env.reset(now=INSTANT_ISO)
        env.call("pay_create_charge", amount=250)
        state = env.state()
    assert state.composition is not None
    assert list(state.composition) == ["main", "payments", "payments_eu", "shop"]
    assert state.composition["payments"].aliases == ["shop/payments"]
    assert state.composition["payments_eu"].scope == "eu"
    assert state.fixture is None
    assert [record["world"] for record in state.state["db"]["log"]] == ["payments"]


def test_reset_selects_a_state_format_over_the_wire(world: World) -> None:
    """The state message carries no arguments, so the episode's format is the only one."""
    with serving(world) as url, SeahavenClient(base_url=url) as env:
        env.reset(state_format="seahaven.state+last_step/1")
        env.call("execute", sql=insert("n1"))
        env.call("execute", sql=insert("n2"))
        state = env.state()
    assert state.format == "seahaven.state+last_step/1"
    assert [record["key"]["id"] for record in state.state["db"]["log"]] == ["n2"]


def test_an_unknown_state_format_is_an_error_frame_and_the_session_survives(world: World) -> None:
    with serving(world) as url, SeahavenClient(base_url=url) as env:
        with pytest.raises(RuntimeError, match="state format"):
            env.reset(state_format="acme.state/1")
        env.reset()
        assert env.call("rows", sql="SELECT 1 AS n").result == [{"n": 1}]


# --- sessions --------------------------------------------------------------


def test_two_sessions_are_independent(world: World) -> None:
    with (
        serving(world) as url,
        SeahavenClient(base_url=url) as first,
        SeahavenClient(base_url=url) as second,
    ):
        first.reset()
        second.reset()
        first.call("execute", sql=insert("only-in-first"))
        assert ids(first.call("rows", sql="SELECT id FROM notes")) == ["only-in-first"]
        assert ids(second.call("rows", sql="SELECT id FROM notes")) == []
        assert first.state().episode_id != second.state().episode_id


def test_an_unknown_reset_kwarg_is_an_error_frame_and_the_session_survives(world: World) -> None:
    with serving(world) as url, SeahavenClient(base_url=url) as env:
        with pytest.raises(RuntimeError, match=r"unknown reset argument\(s\)"):
            env.reset(nonsense=1)
        reset = env.reset(now=INSTANT_ISO)
        assert reset.observation.metadata["now"] == INSTANT_ISO
        assert env.call("rows", sql="SELECT 1 AS n").result == [{"n": 1}]


def test_a_second_reset_over_the_wire_starts_from_the_fixture_again(world: World) -> None:
    fixture_id = _freeze(world)
    with serving(world) as url, SeahavenClient(base_url=url) as env:
        env.reset(fixture=fixture_id)
        env.call("execute", sql=insert("n1"))
        assert len(ids(env.call("rows", sql="SELECT id FROM notes"))) == 2
        env.reset(fixture=fixture_id)
        assert ids(env.call("rows", sql="SELECT id FROM notes")) == ["n0"]


# --- the control tool ------------------------------------------------------


def test_the_control_tool_is_callable_with_the_flag_and_never_listed(world: World) -> None:
    with (
        serving(world, include_control_tools=True) as url,
        SeahavenClient(base_url=url) as env,
    ):
        env.reset()
        assert "controller_run_sql" not in [tool["name"] for tool in env.list_tools()]
        env.call("execute", sql=insert("n1"))
        assert env.call("controller_run_sql", sql="SELECT id FROM notes").result == {
            "columns": ["id"],
            "rows": [["n1"]],
            "row_count": 1,
            "truncated": False,
        }
        logged = env.state().state["db"]["log"]
        assert [(record["table"], record["op"]) for record in logged] == [("notes", "insert")]


def test_the_control_tool_is_unknown_without_the_flag(world: World) -> None:
    with serving(world) as url, SeahavenClient(base_url=url) as env:
        env.reset()
        assert "controller_run_sql" not in [tool["name"] for tool in env.list_tools()]
        observation = env.call("controller_run_sql", sql="SELECT 1")
        assert observation.seahaven_error == {
            "code": "unknown_tool",
            "message": "unknown tool: controller_run_sql",
            "details": {"name": "controller_run_sql"},
        }


# --- what a world is allowed to call its arguments -------------------------


def test_a_tool_argument_called_tool_is_callable_through_the_client(tmp_path: Path) -> None:
    """`Instance.call` made the tool name positional-only; review round 1 found
    the client had not.

    A world may declare a tool argument called `tool`, or `self`, or anything
    else -- the name is the world author's to choose and the framework validates
    it against their signature. With the name a keyword parameter of
    `SeahavenClient.call`, such a tool listed, worked through
    `step(CallToolAction(...))` and raised `TypeError: got multiple values for
    argument 'tool'` through the documented convenience: a tool nobody could
    call from a harness.
    """
    world = build_world(tmp_path)

    @world.tool
    def describe(ctx: Ctx, tool: str, self: str = "unset") -> dict[str, str]:
        """Two argument names that collide with a method's own parameters."""
        return {"tool": tool, "self": self}

    with serving(world) as url, SeahavenClient(base_url=url) as env:
        env.reset()
        assert env.call("describe", tool="rows").result == {"tool": "rows", "self": "unset"}
        assert env.call("describe", tool="rows", self="mine").result == {
            "tool": "rows",
            "self": "mine",
        }


# --- the refused HTTP episode-control routes -------------------------------


def test_the_http_episode_routes_refuse_rather_than_answer_a_throwaway_environment(
    world: World,
) -> None:
    """The three routes OpenEnv cannot serve, refused here in words.

    Upstream builds a fresh environment inside each of the `/reset`, `/step` and
    `/state` handlers and closes it before replying, so the three never observe
    one another and every answer is a plausible 200 about nothing. Seahaven
    replaces the handlers. What is asserted is what a reader at 2am needs: the
    status, the machine-readable code, which route they hit, where to go
    instead, and enough of the upstream citation to check the claim.
    """
    with serving(world) as url:
        for verb, path in HTTP_EPISODE_CONTROL:
            status, body = _request(url + path, method=verb)
            assert status == 501, (verb, path, body)
            refusal = body["detail"]
            assert refusal["code"] == "http_episode_control_unsupported"
            assert refusal["details"]["route"] == f"{verb} {path}"
            assert refusal["details"]["use_instead"] == "/ws"
            assert refusal["details"]["upstream"]["regression"] == "86a222d"
            assert refusal["details"]["upstream"]["package"] == (
                "openenv >=0.5.0,<0.6 (verified against 0.5.0)"
            )
            assert "/ws" in refusal["message"]
            assert "SeahavenClient" in refusal["message"]


def test_a_well_formed_step_is_refused_in_the_same_words_as_a_malformed_one(world: World) -> None:
    """The refusal answers a good request, not only a bad one.

    A replacement handler that still declared OpenEnv's `StepRequest` would
    answer 422 to a body that does not parse, and the caller would go off fixing
    a payload that can never work. Both bodies have to reach the same 501.
    """
    step = json.dumps(
        {"action": CallToolAction(tool_name="rows", arguments={"sql": "SELECT 1"}).model_dump()}
    ).encode()
    with serving(world) as url:
        well_formed = _request(url + "/step", method="POST", data=step)
        nonsense = _request(url + "/step", method="POST", data=b"not json at all")
    assert well_formed[0] == 501
    assert nonsense == well_formed


def test_the_refused_routes_are_still_published_as_openapi_paths(world: World) -> None:
    """Refused and not removed, because `openenv push` reads the paths.

    `mode_endpoint_consistency` in `openenv/cli/_validation.py` calls an app that
    publishes `/reset` a simulation environment and then requires `/step` and
    `/state` beside it. It never calls the three, only names them, so deleting
    them would not fail that criterion -- it would quietly reclassify a Seahaven
    world as a *production* environment, which is a wrong declaration about what
    the world is. The paths stay, and what the schema now promises at each of
    them is the refusal and nothing else.
    """
    with serving(world) as url:
        status, document = _request(url + "/openapi.json")
    assert status == 200
    paths = document["paths"]
    for verb, path in HTTP_EPISODE_CONTROL:
        assert path in paths, sorted(paths)
        assert sorted(paths[path][verb.lower()]["responses"]) == ["501"]


def test_refusing_the_episode_routes_leaves_the_rest_of_the_http_surface_alone(
    world: World,
) -> None:
    """Three routes, and the neighbours they sit between are untouched.

    `/metadata` builds a throwaway environment exactly as the refused three do,
    and is deliberately still served: metadata is the world's and not an
    episode's, so a fresh environment answers it correctly. This is the test
    that fails if the refusal is ever widened to a path that did not need it.
    """
    with serving(world) as url:
        assert _request(url + "/health") == (200, {"status": "healthy"})
        metadata = _request(url + "/metadata")
        assert metadata[0] == 200
        assert metadata[1]["name"] == world.name
        schema = _request(url + "/schema")
        assert schema[0] == 200
        assert sorted(schema[1]) == ["action", "observation", "state"]


def test_a_websocket_session_is_untouched_by_the_refusal(world: World) -> None:
    """The transport that is the product, on the same server at the same moment.

    The `state` frame in particular: its HTTP namesake now answers 501, and the
    session's own state has to keep arriving over the wire, whole and real.
    """
    with serving(world) as url, SeahavenClient(base_url=url) as env:
        env.reset()
        assert _request(url + "/state")[0] == 501
        env.call("execute", sql=insert("n1"))
        state = env.state()
        assert (state.world.name, state.step_count) == (world.name, 1)
        # Whole and real: the write this session just made is in the document.
        assert [record["key"]["id"] for record in state.state["db"]["log"]] == ["n1"]
        assert ids(env.call("rows", sql="SELECT id FROM notes")) == ["n1"]


# --- the refused /mcp transport --------------------------------------------


def _mcp_request(method: str, request_id: int = 1) -> dict[str, Any]:
    """A well-formed JSON-RPC request for one of the four methods upstream dispatches."""
    params: dict[str, Any] = {}
    if method == "tools/call":
        params = {"name": "rows", "arguments": {"sql": "SELECT 1 AS n"}}
    elif method == "openenv/session/close":
        params = {"session_id": "00000000-0000-0000-0000-000000000000"}
    return {"jsonrpc": "2.0", "method": method, "params": params, "id": request_id}


def _assert_refused(frame: dict[str, Any], *, method: str | None, request_id: Any) -> None:
    """One refusal frame, asserted the way a JSON-RPC caller reads one.

    The id is echoed because that is how a caller matches a response to its
    request; `result` is absent because JSON-RPC 2.0 forbids both halves in one
    response and a client that checks for `result` first must not find one.
    """
    assert frame["jsonrpc"] == "2.0"
    assert frame["id"] == request_id
    assert "result" not in frame
    error = frame["error"]
    assert error["code"] == -32601, error
    assert "/ws" in error["message"]
    refusal = error["data"]
    assert refusal["code"] == "mcp_transport_unsupported"
    assert sorted(refusal) == ["code", "details", "message"]
    assert refusal["details"]["method"] == method
    assert refusal["details"]["route"] == "/mcp"
    assert refusal["details"]["use_instead"] == "/ws"
    assert refusal["details"]["upstream"]["missing"] == ["initialize", "reset"]
    # The three claims the prose has to keep making: it is not MCP, a world
    # needs a reset this dialect cannot ask for, and here is the door instead.
    assert "initialize" in refusal["message"]
    assert "reset" in refusal["message"]
    assert "SeahavenClient" in refusal["message"]


def test_every_mcp_method_is_refused_over_http_as_a_json_rpc_error(world: World) -> None:
    """The four methods OpenEnv's `/mcp` dispatches, all refused in one voice.

    `tools/list` is the one that makes this worth doing: upstream answers it
    happily, with every tool the world has, and then every `tools/call` behind
    it fails `reset first` -- a well-formed answer about nothing, which is the
    same thing `POST /reset` was refused for. `openenv/session/create` is refused
    with them because the id it hands out buys nothing, and `openenv/session/close`
    because with create gone the only ids left belong to `/ws` sessions.
    """
    with serving(world) as url:
        for request_id, method in enumerate(MCP_METHODS, start=1):
            request = json.dumps(_mcp_request(method, request_id=request_id)).encode()
            status, frame = _request(url + "/mcp", method="POST", data=request)
            assert status == 200, (method, frame)
            _assert_refused(frame, method=method, request_id=request_id)


def test_a_real_mcp_clients_handshake_is_refused_in_the_same_words(world: World) -> None:
    """`initialize` is not one of the four, and earns no different answer.

    It is the frame an actual MCP client opens with, so it is the one most
    likely to arrive here from somebody who believed the path name. Upstream
    answers it a bare `-32601 Method not found: initialize`, which reads like a
    typo; the refusal has to say why no MCP client can speak this at all.
    """
    request = json.dumps({"jsonrpc": "2.0", "method": "initialize", "id": "handshake"}).encode()
    with serving(world) as url:
        status, frame = _request(url + "/mcp", method="POST", data=request)
    assert status == 200
    _assert_refused(frame, method="initialize", request_id="handshake")


def test_the_push_validators_probe_of_mcp_still_answers_a_json_rpc_payload(world: World) -> None:
    """The hard constraint: `openenv push` must still pass.

    `mcp_endpoint` in `openenv/cli/_validation.py` POSTs `{}` to `/mcp` and
    passes only on HTTP 200 with a JSON body whose `jsonrpc` is `"2.0"`. That is
    why this refusal is a JSON-RPC error inside a 200 and not the 501 the HTTP
    episode-control routes answer. A body that is not JSON at all takes the same
    path, with the null id JSON-RPC asks for when the id cannot be determined.
    """
    with serving(world) as url:
        probe = _request(url + "/mcp", method="POST", data=b"{}")
        nonsense = _request(url + "/mcp", method="POST", data=b"not json at all")
    assert probe[0] == 200
    assert probe[1]["jsonrpc"] == "2.0"
    _assert_refused(probe[1], method=None, request_id=None)
    assert nonsense == probe


def test_the_mcp_websocket_refuses_the_same_way_and_then_closes(world: World) -> None:
    """The second `/mcp` door, which is exactly as dead as the first.

    `ws /mcp` reaches the same upstream handler with a session environment that
    has never been reset, so `tools/call` on it answers `reset first` too. It
    answers the same refusal frame and then closes: every method here is
    refused, so a second frame could only earn the same answer, and a socket
    left open implies a negotiation that does not exist. The close is normal
    (1000) because nothing failed -- the transport is simply not served.
    """
    with serving(world) as url:
        frame, close_code = asyncio.run(
            _refused_over_the_mcp_websocket(url, _mcp_request("tools/call", request_id=9))
        )
    _assert_refused(frame, method="tools/call", request_id=9)
    assert close_code == 1000


def test_mcp_frames_over_a_websocket_session_still_work(world: World) -> None:
    """The door that stays open, pinned because one careless route swap closes it.

    `{"type": "mcp"}` on a `/ws` connection routes into the same upstream MCP
    handler, but with the session's own environment -- so after a real `reset`
    it has an instance and the call works. That is the whole difference between
    this and `/mcp`, and it must keep working.
    """
    with serving(world) as url:
        frame = asyncio.run(_mcp_frame_over_a_session(url))
    assert frame["type"] == "mcp"
    assert frame["data"]["id"] == 11
    assert frame["data"]["result"]["result"] == [{"n": 1}]
    assert frame["data"]["result"]["error"] is None


def test_mcp_is_still_published_as_an_openapi_path(world: World) -> None:
    """Refused and not removed, because `openenv push` reads path names.

    The validator probes `/mcp` directly rather than reading it off the schema,
    but the schema is what a reader and a generated client see, and a path that
    answers has to be in it. What it promises there is the refusal: one 200,
    described as one.
    """
    with serving(world) as url:
        status, document = _request(url + "/openapi.json")
    assert status == 200
    assert "/mcp" in document["paths"], sorted(document["paths"])
    assert sorted(document["paths"]["/mcp"]["post"]["responses"]) == ["200"]


async def _refused_over_the_mcp_websocket(
    url: str, request: dict[str, Any]
) -> tuple[dict[str, Any], int | None]:
    """One frame to `ws /mcp`: the reply, and the code the server closed with."""
    async with ws_connect(convert_to_ws_url(url) + "/mcp", proxy=None) as sock:
        await sock.send(json.dumps(request))
        reply = json.loads(await sock.recv())
        with pytest.raises(ConnectionClosedOK):
            await sock.recv()
        return reply, sock.close_code


async def _mcp_frame_over_a_session(url: str) -> dict[str, Any]:
    """Reset a `/ws` session and call a tool through its MCP frame, raw."""
    async with ws_connect(convert_to_ws_url(url) + "/ws", proxy=None) as sock:
        await sock.send(json.dumps({"type": "reset", "data": {}}))
        await sock.recv()
        await sock.send(
            json.dumps({"type": "mcp", "data": _mcp_request("tools/call", request_id=11)})
        )
        return json.loads(await sock.recv())


# --- the Gradio web interface a pushed Space serves ------------------------


def test_the_web_interface_holds_an_episode_and_names_the_world(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`ENABLE_WEB_INTERFACE=true`: reset, step and metadata off one environment.

    This is the mode every pushed world runs in. `openenv push` writes
    `ENV ENABLE_WEB_INTERFACE=true` into the Dockerfile it generates and
    `base_path: /web` into the Space README, and `GET /` there redirects to the
    page these routes serve.

    `WebInterfaceManager.__init__` builds the environment only when
    `inspect.isclass` or `inspect.isfunction` says the factory is one
    (`web_interface.py` lines 249-254 in openenv 0.4.2), and neither is true of
    a `functools.partial`. A factory it does not recognise is kept unbuilt, and
    every `/web` handler then reads an environment attribute off the factory
    object itself.
    """
    world = build_world(tmp_path, description="Notes, and the tools that touch them.")
    monkeypatch.setenv("ENABLE_WEB_INTERFACE", "true")
    step = json.dumps(
        {"action": {"tool_name": "execute", "arguments": {"sql": insert("n0")}}}
    ).encode()
    with serving(world) as url:
        reset = _request(url + "/web/reset", method="POST")
        stepped = _request(url + "/web/step", method="POST", data=step)
        metadata = _request(url + "/web/metadata")
        published = _request(url + "/metadata")
    assert reset[0] == 200, reset
    # A reset answers a plain `Observation` carrying its facts in `metadata`,
    # and OpenEnv's serializer (`core/env_server/serialization.py:137-175` in
    # openenv 0.4.2) hoists a non-empty `metadata` to the top level of
    # the envelope beside `observation`, which is why the same three facts stand
    # twice. `result` belongs to a tool call and a reset calls no tool.
    facts = reset[1]["metadata"]
    assert reset[1] == {
        "observation": {"metadata": facts},
        "reward": None,
        "done": False,
        "metadata": facts,
    }
    assert sorted(facts) == ["fixture", "now", "tools"]
    assert facts["fixture"] is None
    assert facts["tools"] == 6
    # The step runs against the instance the reset made: a second environment,
    # or none, answers `reset first` instead of a rowcount.
    assert stepped[0] == 200, stepped
    assert stepped[1]["observation"]["result"] == {"rowcount": 1}
    assert stepped[1]["observation"]["error"] is None
    assert metadata[0] == 200, metadata
    assert metadata[1]["name"] == world.name
    # Upstream's card, not the world's: `load_environment_metadata` calls
    # `get_metadata()` only on an environment *instance*, and a factory of any
    # kind -- class, function or partial -- gets `f"{name} environment"`
    # instead. `env_name` is what reaches it, so the card names the world and
    # carries no description of it. With a partial the name is unreadable too
    # and this line reads `partial environment`. Plain `GET /metadata` builds an
    # environment and answers `world.description`; the two routes disagree, and
    # that half is upstream's to fix.
    assert metadata[1]["description"] == f"{world.name} environment"
    assert published[0] == 200, published
    assert published[1]["description"] == world.description


def test_the_refusals_survive_the_web_interfaces_extra_routes_and_mount(
    monkeypatch: pytest.MonkeyPatch, world: World
) -> None:
    """The HTTP refusals again, in the one configuration every pushed world runs in.

    `_refuse_http_episode_control` and `_refuse_mcp_transport` swap handlers by
    walking `served.router.routes` after `create_app` has returned and replacing
    entries in place, matched on the route's class and its path. With
    `ENABLE_WEB_INTERFACE=true` that app is a different app: seven more routes
    and a Gradio sub-app mounted at `/web`. The swaps still find the three HTTP
    episode-control routes and the `POST /mcp` route -- a mount is a `Mount` and
    not an `APIRoute` -- but a swap that stopped matching would not raise.
    `/reset`, `/step` and `/state` would go back to answering 200 from a
    throwaway environment, and `POST /mcp` to advertising a tool list whose every
    entry fails when called, which are the silent wrong answers the refusals
    exist to prevent.

    The websocket `/mcp` refusal is covered by
    `test_the_mcp_websocket_refuses_the_same_way_and_then_closes`, with the web
    interface off, and is not asserted here.
    """
    monkeypatch.setenv("ENABLE_WEB_INTERFACE", "true")
    with serving(world) as url:
        refused = [
            (verb, path, _request(url + path, method=verb)) for verb, path in HTTP_EPISODE_CONTROL
        ]
        listing = json.dumps(_mcp_request("tools/list")).encode()
        mcp = _request(url + "/mcp", method="POST", data=listing)
        health = _request(url + "/health")
        web = _request(url + "/web/metadata")
    # The premise, asserted rather than assumed: `/web/metadata` is served only
    # by the web-interface app, so this fails if `ENABLE_WEB_INTERFACE` ever
    # stops reaching `create_app` -- a renamed variable, a narrowed set of
    # accepted values (`http_server.py:1755` takes only `true`, `1` and `yes`),
    # a `serving()` that no longer builds the app eagerly -- and the rest of
    # this test quietly becomes a duplicate of the refusal tests above.
    assert web[0] == 200, web
    for verb, path, (status, body) in refused:
        assert status == 501, (verb, path, body)
        refusal = body["detail"]
        assert refusal["code"] == "http_episode_control_unsupported"
        assert refusal["details"]["route"] == f"{verb} {path}"
        assert refusal["details"]["use_instead"] == "/ws"
    assert mcp[0] == 200, mcp
    _assert_refused(mcp[1], method="tools/list", request_id=1)
    # The neighbour that is deliberately not refused, so a swap that widened to
    # everything it walked past fails here rather than passing quietly.
    assert health == (200, {"status": "healthy"})


# --- the published schema --------------------------------------------------


def test_the_served_schema_publishes_the_observation_with_its_descriptions(
    world: World,
) -> None:
    """`GET /schema` is part of the app's surface, and nothing was reading it.

    Review round 1 found `SeahavenObservation.result`'s redeclaration dropping
    `CallToolObservation`'s inherited field description from this endpoint,
    which the phase plan had recorded as changing nothing; round 2 found the fix
    closing exactly that field and leaving `tool_name` and `error` published
    with no description at all. This is the half of the rule that needs a
    server: the text really does reach a client. The other half -- that *every*
    field either model declares has a description in the first place -- is
    `test_every_declared_field_publishes_a_description`, in process.

    The state half is here because it only started being true with openenv
    0.5.0: `create_app` takes a `state_cls` now, so the route answers the real
    state model rather than OpenEnv's bare `State`
    (huggingface/OpenEnv#1155). A bump that dropped the argument would publish a
    state document that no Seahaven world ever answers, and nothing else reads
    this endpoint.
    """
    with serving(world) as url, urllib.request.urlopen(url + "/schema") as response:
        schema = json.loads(response.read())
    assert schema["action"]["title"] == "CallToolAction"
    state = schema["state"]
    assert state["title"] == "SeahavenState"
    assert set(SeahavenState.model_fields) <= set(state["properties"])
    assert {
        name: state["properties"][name].get("description") for name in SeahavenState.__annotations__
    } == {
        name: SeahavenState.model_fields[name].description for name in SeahavenState.__annotations__
    }
    observation = schema["observation"]
    assert observation["title"] == "SeahavenObservation"
    properties = observation["properties"]
    assert sorted(properties) == ["done", "error", "metadata", "result", "reward", "tool_name"]
    # What the model says is what the wire publishes, for every field the class
    # declares itself. The texts themselves are pinned in `test_env.py`, which
    # is also where the rule that each declared field *has* one lives; asserting
    # them again here would duplicate a literal rather than test a second thing.
    declared = set(SeahavenObservation.__annotations__)
    assert declared == {"tool_name", "result", "error"}
    assert {name: properties[name]["description"] for name in declared} == {
        name: SeahavenObservation.model_fields[name].description for name in declared
    }


# --- the web interface -----------------------------------------------------


def test_the_world_is_whole_with_the_web_interface_on(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`ENABLE_WEB_INTERFACE=true` is the mode every pushed world runs in.

    `openenv push` writes `ENV ENABLE_WEB_INTERFACE=true` into the Dockerfile it
    builds, so a hub world is served by `create_app_with_web_interface` and never
    by the plain `create_app` every other test here exercises. It is a different
    branch: it mounts Gradio at `/web`, and the arguments `app()` passes have to
    reach the server through it. Nothing else in this suite runs it.
    """
    monkeypatch.setenv("ENABLE_WEB_INTERFACE", "true")
    with serving(world) as url, SeahavenClient(base_url=url) as env:
        assert _request(url + "/web/metadata")[0] == 200
        # The refusals are added to the app `app()` answers, whichever branch
        # built it, and the published state class has to survive the same trip.
        assert _request(url + "/state")[0] == 501
        assert _request(url + "/schema")[1]["state"]["title"] == "SeahavenState"
        env.reset()
        assert env.call("rows", sql="SELECT 1 AS n").result == [{"n": 1}]


# --- the idle reaper -------------------------------------------------------


def test_an_idle_session_is_reaped_and_its_instance_destroyed(world: World, tmp_path: Path) -> None:
    """`session_timeout` was pinned as a number passed through, never as behaviour.

    It is the only thing standing between a long-lived server and a disk full of
    abandoned instances, and OpenEnv's cleanup swallows every exception
    `env.close()` raises -- so an `Instance.destroy()` that started failing would
    leak a fixture copy and an APSW connection per session with nothing red
    anywhere. This test asks the filesystem instead.

    The reaper wakes every `max(timeout / 4, 5.0)` seconds, so the deadline is
    generous and the assertion is only that the directory goes.
    """
    work = tmp_path / "work"
    with serving(world, session_timeout=REAPED_AFTER) as url, SeahavenClient(base_url=url) as env:
        env.reset()
        instances_on_disk = list(work.iterdir())
        assert len(instances_on_disk) == 1, instances_on_disk
        directory = instances_on_disk[0]
        deadline = time.monotonic() + REAP_DEADLINE
        while directory.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        assert not directory.exists(), "the idle session's instance was not destroyed"
        assert list(work.iterdir()) == []


# --- the gate --------------------------------------------------------------


def test_the_gate_serialises_two_sessions(slow_world: World) -> None:
    """With one slot, two calls on two sessions cannot be inside SQLite together."""
    instances.set_concurrency(1)
    first, second = _two_slow_calls(slow_world)
    assert not _overlap(first, second), f"the calls overlapped: {first} and {second}"


def test_without_the_gate_two_sessions_run_together(slow_world: World) -> None:
    """The permissive half of the same rule: two slots, and the calls do overlap.

    Without this, a gate that serialised everything -- or a server that ran every
    session on one thread -- would pass the test above and nothing would notice.
    """
    instances.set_concurrency(2)
    first, second = _two_slow_calls(slow_world)
    assert _overlap(first, second), f"the calls did not overlap: {first} and {second}"


def _two_slow_calls(world: World) -> tuple[dict[str, float], dict[str, float]]:
    """One slow call on each of two sessions, started as close together as possible."""

    async def drive(url: str) -> tuple[dict[str, float], dict[str, float]]:
        async with (
            SeahavenClient(base_url=url) as first,
            SeahavenClient(base_url=url) as second,
        ):
            await first.reset()
            await second.reset()
            both = await asyncio.gather(first.call("slow"), second.call("slow"))
            return both[0].result, both[1].result

    with serving(world) as url:
        return asyncio.run(drive(url))


def _overlap(first: dict[str, float], second: dict[str, float]) -> bool:
    return first["started"] < second["ended"] and second["started"] < first["ended"]


# --- capacity --------------------------------------------------------------


def test_over_capacity_the_server_refuses_rather_than_queueing(world: World) -> None:
    """The budget is a refusal, not a queue: the connection over it is told so.

    Read straight off the socket rather than through a client. The server sends
    the refusal and then closes, so whether a client sees the frame or the close
    first is a race, and a test that asserted on the client's exception would be
    asserting on who won it.
    """
    with serving(world, max_concurrent_envs=1) as url, SeahavenClient(base_url=url) as env:
        env.reset()
        refusal = asyncio.run(_first_frame(url))
        assert refusal is not None, "the connection over the budget was served"
        assert refusal["type"] == "error"
        assert refusal["data"]["code"] == "CAPACITY_REACHED"
        assert (refusal["data"]["active_sessions"], refusal["data"]["max_sessions"]) == (1, 1)
        # The session that has capacity is untouched by the one that did not.
        assert env.call("rows", sql="SELECT 1 AS n").result == [{"n": 1}]


def test_under_capacity_a_second_connection_is_served(world: World) -> None:
    """The permissive half: the same second connection, with a slot for it."""
    with serving(world, max_concurrent_envs=2) as url, SeahavenClient(base_url=url) as env:
        env.reset()
        assert asyncio.run(_first_frame(url)) is None


async def _first_frame(url: str) -> dict[str, Any] | None:
    """Connect and answer the frame the server volunteers, or `None` if it waits."""
    async with ws_connect(convert_to_ws_url(url) + "/ws", proxy=None) as websocket:
        try:
            return dict(json.loads(await asyncio.wait_for(websocket.recv(), UNSOLICITED_TIMEOUT)))
        except TimeoutError:
            return None


@pytest.mark.slow
def test_five_hundred_sessions_reset_and_call_with_no_errors(trivial_world: World) -> None:
    """The smoke test: as many sessions as the default budget, all at once."""

    async def drive(url: str) -> list[dict[str, str]]:
        clients = [SeahavenClient(base_url=url) for _ in range(SESSIONS)]
        try:
            await asyncio.gather(*(client.connect() for client in clients))
            await asyncio.gather(*(client.reset() for client in clients))
            results = await asyncio.gather(
                *(client.call("echo", message=str(number)) for number, client in enumerate(clients))
            )
            return [observation.result for observation in results]
        finally:
            await asyncio.gather(*(client.close() for client in clients))

    with serving(trivial_world) as url:
        answers = asyncio.run(drive(url))
    assert answers == [{"message": str(number)} for number in range(SESSIONS)]


# --- disconnects -----------------------------------------------------------


class _Kept(logging.Handler):
    """A handler that keeps every record it is handed."""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@contextmanager
def _watched(world: World, **options: Any) -> Iterator[tuple[str, list[logging.LogRecord]]]:
    """Serve a world, and collect what uvicorn logs on its own error channel.

    `caplog` cannot see this: uvicorn's loggers set `propagate=False`, so nothing
    uvicorn logs ever reaches the handler pytest puts on the root logger. The
    handler has to go on *after* the server starts, because uvicorn configures
    logging with `dictConfig` as it boots and that drops every handler already on
    `uvicorn.error`, and it has to come off *after* the server stops, or a
    traceback logged while the last connection is torn down would be missed.
    `ExitStack` unwinds in reverse, so registering the removal first buys that
    order.
    """
    logger = logging.getLogger("uvicorn.error")
    kept = _Kept()
    with ExitStack() as stack:
        stack.callback(logger.removeHandler, kept)
        url = stack.enter_context(serving(world, **options))
        logger.addHandler(kept)
        yield url, kept.records


def _errors(records: list[logging.LogRecord]) -> list[str]:
    return [record.getMessage() for record in records if record.levelno >= logging.ERROR]


def test_a_session_that_ends_normally_leaves_nothing_in_the_error_log(world: World) -> None:
    """A clean disconnect is silent, on the log an operator greps.

    A session that ended normally must not look like a failure. Seahaven carried
    a client-side override and an ASGI middleware to buy this under openenv
    0.4.2, whose `/ws` handler closed the connection in a `finally` guarded by
    `except RuntimeError` and so logged `ERROR: Exception in ASGI application`
    for every normal close. openenv 0.5.0 catches `WebSocketDisconnect` there as
    well (`http_server.py` lines 1290 and 1767), both workarounds are gone, and
    this test is what says upstream really does hold the line now.
    """
    with _watched(world) as (url, records), SeahavenClient(base_url=url) as env:
        env.reset()
        assert env.call("rows", sql="SELECT 1 AS n").result == [{"n": 1}]
    assert _errors(records) == []


def test_a_peer_that_vanishes_is_not_logged_as_a_server_error(world: World) -> None:
    """The half no client-side fix can reach: a socket that simply stops.

    A harness that dies mid-session, a stock client, anything that hangs up
    without waiting -- OpenEnv is then closing a connection that is already gone.
    That is the peer's business and never the server's, and it must not reach the
    error log either.
    """

    async def connect_and_vanish(url: str) -> None:
        async with ws_connect(convert_to_ws_url(url) + "/ws", proxy=None):
            pass

    with _watched(world) as (url, records):
        asyncio.run(connect_and_vanish(url))
    assert _errors(records) == []


def test_a_peer_that_leaves_mid_refusal_is_not_logged_as_a_server_error(world: World) -> None:
    """`ws /mcp` is Seahaven's own handler, so its own close has to be guarded.

    The refusal writes one frame and closes. A peer that hangs up between its
    request and that answer leaves starlette raising `WebSocketDisconnect` out of
    `send_text`, which is the peer's business and not the server's -- unguarded
    it costs an `ERROR: Exception in ASGI application` traceback per abandoned
    connection. Upstream's guard on its own `/ws` handler does not reach this
    one, because this one replaced upstream's.
    """

    async def ask_and_vanish(url: str) -> None:
        sock = await ws_connect(convert_to_ws_url(url) + "/mcp", proxy=None)
        await sock.send(json.dumps(_mcp_request("tools/list")))
        # Hung up without reading the answer, which is what a harness that dies
        # and a client that gave up both look like from here.
        await sock.close()

    with _watched(world) as (url, records):
        asyncio.run(ask_and_vanish(url))
        time.sleep(WRITE_INTO_A_DEAD_PEER)
    assert _errors(records) == []


def test_the_mcp_refusal_absorbs_a_dead_peer_and_nothing_else() -> None:
    """The guard itself, without the race the served test depends on.

    `test_a_peer_that_leaves_mid_refusal_is_not_logged_as_a_server_error` needs
    the peer's socket to be gone before the handler writes, which is a race it
    wins today and cannot be made to win for ever. This drives the same handler
    with a send that fails on purpose, so the guard is asserted rather than
    raced for -- and asserted to be narrow: a real failure still escapes.
    """

    async def never_called(websocket: WebSocket) -> None:
        raise AssertionError("the replaced handler is never called")

    refuse = _refusing_mcp_websocket(
        APIWebSocketRoute(_MCP_ROUTE, never_called, name="mcp")
    ).endpoint

    def dead_peer(failure: Exception) -> WebSocket:
        frames = iter(
            [
                {"type": "websocket.connect"},
                {"type": "websocket.receive", "text": json.dumps(_mcp_request("tools/list"))},
            ]
        )

        async def receive() -> MutableMapping[str, Any]:
            return next(frames)

        async def send(message: MutableMapping[str, Any]) -> None:
            if message["type"] == "websocket.accept":
                return
            raise failure

        return WebSocket({"type": "websocket", "path": _MCP_ROUTE}, receive, send)

    # An `OSError` on the write is what starlette turns into the
    # `WebSocketDisconnect` a vanished peer raises.
    asyncio.run(refuse(dead_peer(OSError("the peer went away"))))
    with pytest.raises(ValueError, match="the world is on fire"):
        asyncio.run(refuse(dead_peer(ValueError("the world is on fire"))))


def _request(url: str, *, method: str = "GET", data: bytes | None = None) -> tuple[int, Any]:
    """Answer the status and decoded body, for the statuses urllib calls errors.

    `urlopen` raises on anything from 400 up, and the body of a refusal is the
    whole point of these tests, so both halves are read the same way.
    """
    request = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as refused:
        return refused.code, json.loads(refused.read())


def _freeze(world: World, fixture_id: str = "start") -> str:
    with world.instance(None, now=INSTANT_ISO) as instance:
        instance.call("execute", sql=insert("n0"))
        instance.freeze(fixture_id, "One note.")
    return fixture_id
