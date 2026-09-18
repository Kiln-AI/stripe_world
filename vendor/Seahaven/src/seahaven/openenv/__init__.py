"""Seahaven over OpenEnv: the ASGI app, the environment, the models, the client.

This subpackage is the only part of Seahaven that needs the `serve` extra, and
it imports `openenv` at module top: `import seahaven` never reaches here, so a
world used in-process -- in pytest, in a script, in a notebook -- never pays for
a dependency whose wheel brings gradio, openai, fastmcp and pandas with it.

```python
from projecttracker import world
import seahaven.openenv

app = seahaven.openenv.app(world)
```

That file, `openenv_app.py`, is the whole of a world's server. One world per
app, mounted at `/`, which is the shape a hub expects.
"""

import json
from contextlib import suppress
from typing import Any, NoReturn

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.routing import APIRoute, APIWebSocketRoute
from openenv.core.env_server.http_server import create_app
from openenv.core.env_server.mcp_types import (
    CallToolAction,
    JsonRpcErrorCode,
    ListToolsAction,
    ListToolsObservation,
)
from openenv.core.env_server.types import ConcurrencyConfig

from seahaven.openenv.client import SeahavenClient
from seahaven.openenv.env import (
    FileRef,
    FixtureRef,
    NodeRef,
    SeahavenEnv,
    SeahavenObservation,
    SeahavenState,
    WorldRef,
)
from seahaven.world import World

__all__ = [
    "CallToolAction",
    "FileRef",
    "FixtureRef",
    "ListToolsAction",
    "ListToolsObservation",
    "NodeRef",
    "SeahavenClient",
    "SeahavenEnv",
    "SeahavenObservation",
    "SeahavenState",
    "WorldRef",
    "app",
]

# The operator's budget, not a design limit. Seahaven refuses no session of its
# own accord; over capacity OpenEnv answers `CAPACITY_REACHED` and closes the
# connection, and `seahaven serve --max_concurrent_envs` moves the number.
DEFAULT_MAX_CONCURRENT_ENVS = 500

# A held session costs its fixture copy on disk and about a megabyte of memory,
# and a client that drops without closing holds one for ever. An hour idle is
# long enough that no live eval is reaped and short enough that a crashed
# harness does not accumulate; `--session-timeout 0` turns the reaper off.
DEFAULT_SESSION_TIMEOUT = 3600.0


# OpenEnv's three plain-HTTP episode-control routes. Every one of their handlers
# builds an `Environment` from the factory, uses it and closes it in a `finally`
# before answering -- `http_server.py` lines 691/712 (`/reset`), 726/750
# (`/step`) and 1382/1386 (`/state`) in openenv 0.5.0 -- so each request runs
# against its own throwaway environment and none of the three observes another.
# The answers are well-formed, 200, and meaningless. `/metadata` builds one the
# same way and is deliberately not in this set: metadata is the world's and not
# an episode's, so a fresh environment answers it correctly.
_HTTP_EPISODE_CONTROL_ROUTES = frozenset({"/reset", "/step", "/state"})

# 501, weighed against 410 and 400. 501 is the one that is literally true: RFC
# 9110 gives it for functionality the server does not support, and holding an
# episode over plain HTTP is exactly that -- the request is well formed and the
# resource is real, the capability is absent. 410 asserts the resource was
# served here and is permanently gone with no forwarding address; neither half
# holds, since Seahaven never served a working episode over HTTP, the path stays
# in the OpenAPI schema on purpose, and `/ws` *is* the forwarding address. 400
# blames the request, which would send a caller off editing a payload that can
# never be right. The usual objection to a 5xx -- retry storms, a red dashboard
# -- does not bite here: 501 is non-transient by definition and is in no default
# retry set (`urllib3.Retry` forces no status; `httpx` retries no status at
# all), and nothing health-checks these. OpenEnv's own push-time validator
# probes `/health`, `/metadata`, `/schema`, `/mcp` and `/openapi.json` and reads
# these three only as OpenAPI path names (`cli/_validation.py`
# `mode_endpoint_consistency`), so it never sees this response.
_HTTP_EPISODE_CONTROL_STATUS = 501

# The body is FastAPI's `{"detail": ...}`, because that is the envelope every
# other HTTP error from this app already uses and a generic client expects, and
# inside it Seahaven's own `{"code", "message", "details"}` -- the shape
# `ToolError.to_dict()` gives, so one error shape spans the framework. Nested
# rather than raised to the top level on purpose: a tool error is data an agent
# reads off an observation, and this is a transport-level refusal aimed at
# whoever pointed an HTTP client at the wrong endpoint. They should not arrive
# looking like the same thing.
_HTTP_EPISODE_CONTROL_CODE = "http_episode_control_unsupported"


def _http_episode_control_message(route: str) -> str:
    """The refusal in prose, for the person reading it at 2am.

    It is three answers in one, because at that hour all three are wanted at
    once: what just happened, whose defect it is and how to check the claim, and
    which door to use instead.
    """
    return (
        f"{route} cannot hold an episode, so Seahaven refuses it rather than answer something "
        "that looks right and is not. OpenEnv builds a brand-new environment for every plain "
        "HTTP request and closes it again before replying, so this route would have answered "
        "from an environment that has never seen your reset, never ran your steps, and is about "
        "to be thrown away -- with a 200 and a plausible body. That is an upstream defect in "
        "openenv 0.5.x, not a Seahaven limitation and not something a world can fix from its own "
        "side: every /reset, /step and /state handler in openenv/core/env_server/http_server.py "
        "builds an Environment from the factory and closes it in a finally, a regression "
        "introduced in OpenEnv commit 86a222d. Drive the episode over the WebSocket transport at "
        "/ws instead, which is genuinely session-bound -- one connection is one session holding "
        "one instance. seahaven.openenv.SeahavenClient speaks it, and so does the stock OpenEnv "
        "EnvClient."
    )


def _http_episode_control_refusal(route: str) -> dict[str, Any]:
    """The whole body these routes answer, under FastAPI's `detail` key."""
    return {
        "code": _HTTP_EPISODE_CONTROL_CODE,
        "message": _http_episode_control_message(route),
        "details": {
            "route": route,
            "use_instead": "/ws",
            "clients": ["seahaven.openenv.SeahavenClient", "openenv.EnvClient"],
            "upstream": {
                "package": "openenv >=0.5.0,<0.6 (verified against 0.5.0)",
                "file": "openenv/core/env_server/http_server.py",
                "regression": "86a222d",
                "defect": (
                    "each /reset, /step and /state handler builds an Environment from the "
                    "factory and closes it before returning, so no two requests share one"
                ),
            },
        },
    }


def _refusing_route(route: APIRoute) -> APIRoute:
    """The same route, answering the refusal instead of a throwaway environment."""
    methods = route.methods or set()
    # `HEAD` is Starlette's own addition to every `GET` route, and `OPTIONS` the
    # CORS preflight: neither is what a caller typed, so neither belongs in the
    # line naming the route back to them.
    named = f"{'/'.join(sorted(methods - {'HEAD', 'OPTIONS'}))} {route.path}"
    refusal = _http_episode_control_refusal(named)

    # No parameters, so a request with a valid body, an invalid one, or none at
    # all, all reach the refusal. A handler that still declared `StepRequest`
    # would answer 422 to a malformed step and hide the real reason it failed.
    async def refuse() -> NoReturn:
        raise HTTPException(status_code=_HTTP_EPISODE_CONTROL_STATUS, detail=refusal)

    return APIRoute(
        route.path,
        refuse,
        methods=sorted(methods),
        name=route.name,
        tags=route.tags,
        summary=f"Refused: {named} cannot hold an episode over HTTP",
        description=_http_episode_control_message(named),
        status_code=_HTTP_EPISODE_CONTROL_STATUS,
        response_model=None,
        responses={
            _HTTP_EPISODE_CONTROL_STATUS: {
                "description": "Refused: use the WebSocket transport at /ws.",
            }
        },
    )


def _refuse_http_episode_control(served: FastAPI) -> None:
    """Swap OpenEnv's three episode-control handlers for one that refuses.

    Replaced in place rather than deleted, because `openenv push` validates a
    world by reading the app's OpenAPI paths: `mode_endpoint_consistency` in
    `openenv/cli/_validation.py` calls an app with `/reset` a simulation
    environment and then requires `/step` and `/state` beside it. Deleting the
    three would silently reclassify a Seahaven world as a production
    environment, which is a different and wrong declaration about what it is.
    Keeping the paths registered keeps the declaration honest and the validation
    passing; only the behaviour changes, which is the half that was lying.
    """
    for index, route in enumerate(served.router.routes):
        if isinstance(route, APIRoute) and route.path in _HTTP_EPISODE_CONTROL_ROUTES:
            served.router.routes[index] = _refusing_route(route)


# OpenEnv's `/mcp`, on both transports: a `POST` route and a websocket route at
# the same path. Every method on both is refused. `mcp_handler`
# (`http_server.py:753` in openenv 0.5.0) dispatches exactly four --
# `openenv/session/create`, `openenv/session/close`, `tools/list` and
# `tools/call` -- and none of the four is usable here. `tools/list` answers a
# full tool list that no caller can then use; `tools/call` answers `reset first`
# whether or not it carries a session id, because a Seahaven tool needs an
# instance and this dialect has no verb that creates one; `openenv/session/create`
# hands out an id that buys nothing. With create refused, the only session ids
# left belong to `/ws` connections, so `openenv/session/close` is refused with
# the rest rather than left as a way to close a stranger's episode by guessing a
# uuid. `/ws` itself is untouched, including the `{"type": "mcp"}` frame it
# carries, which reaches the same upstream handler with a session environment
# and works correctly once the session has been reset.
_MCP_ROUTE = "/mcp"

# 200, and not the 501 the HTTP episode-control routes answer. `openenv push`
# probes this exact route: `mcp_endpoint` in `openenv/cli/_validation.py` POSTs
# `{}` to `/mcp` and passes only on HTTP 200 with a JSON body whose `jsonrpc` is
# `"2.0"`, so a 501 here would fail a push that has nothing wrong with it. The
# refusal therefore lives in the JSON-RPC envelope rather than in the HTTP
# status, which is also where a JSON-RPC caller looks for it.
_MCP_TRANSPORT_STATUS = 200

# -32601, upstream's own `METHOD_NOT_FOUND`, whose JSON-RPC definition is "Method
# does not exist / **is not available**" -- the second half of which is exactly
# this. `INTERNAL_ERROR` (-32603, what upstream answers a `tools/call` today)
# would claim the server broke, and `INVALID_REQUEST` would blame a request that
# is well formed.
_MCP_TRANSPORT_ERROR_CODE = int(JsonRpcErrorCode.METHOD_NOT_FOUND)

_MCP_TRANSPORT_CODE = "mcp_transport_unsupported"

# JSON-RPC asks for a short single sentence in `error.message` and gives `data`
# for the rest, so the sentence points at the prose rather than being it.
_MCP_TRANSPORT_SUMMARY = (
    "Method not available: this Seahaven world does not serve OpenEnv's /mcp transport. "
    "See error.data for why, and use the WebSocket transport at /ws."
)


def _mcp_transport_message() -> str:
    """The refusal in prose: what happened, whose limitation, which door instead.

    One statement for every method rather than a per-method matrix, because the
    thing being said is about the transport and not about any one call.
    """
    return (
        "/mcp is not served here, on either transport, so Seahaven refuses every method on it "
        "rather than advertise a tool list whose every entry fails when called. Two things are "
        "true of it and neither is a Seahaven limitation. First, OpenEnv's /mcp is not the MCP "
        "protocol: it dispatches exactly openenv/session/create, openenv/session/close, "
        "tools/list and tools/call, and answers 'Method not found' to the initialize an MCP "
        "client opens with, so no off-the-shelf MCP client can speak it at all. Upstream says so "
        "itself -- RFC 003 leans on MCP's custom-transports clause and lists no SSE streaming, no "
        "server-initiated messages and no session management as known gaps, with standard "
        "Streamable HTTP planned for later. Second, a Seahaven world is stateful: a tool call "
        "runs against an instance, an instance comes from a reset, and this dialect has no reset "
        "verb -- which is why every tools/call on it answers 'reset first', with or without an "
        "openenv/session/create session id. Drive the episode over the WebSocket transport at "
        "/ws instead, which is genuinely session-bound: one connection is one session holding "
        "one instance. seahaven.openenv.SeahavenClient speaks it, and so does the stock OpenEnv "
        'EnvClient. A /ws connection also carries MCP frames as {"type": "mcp"} messages, '
        "and those work correctly, because by then the session has an instance to call."
    )


def _mcp_transport_refusal(method: str | None) -> dict[str, Any]:
    """Seahaven's `{"code", "message", "details"}` triple, for the JSON-RPC `data`."""
    return {
        "code": _MCP_TRANSPORT_CODE,
        "message": _mcp_transport_message(),
        "details": {
            "route": _MCP_ROUTE,
            "method": method,
            "use_instead": "/ws",
            "clients": ["seahaven.openenv.SeahavenClient", "openenv.EnvClient"],
            "upstream": {
                "package": "openenv >=0.5.0,<0.6 (verified against 0.5.0)",
                "file": "openenv/core/env_server/http_server.py",
                "dispatches": [
                    "openenv/session/create",
                    "openenv/session/close",
                    "tools/list",
                    "tools/call",
                ],
                "missing": ["initialize", "reset"],
                "defect": (
                    "the dialect is not MCP -- there is no initialize, so no MCP client can "
                    "connect -- and it has no reset, so no call on it can reach an instance"
                ),
            },
        },
    }


def _mcp_envelope(body: bytes | str) -> tuple[str | int | None, str | None]:
    """The `id` to echo and the `method` to name back, out of whatever arrived.

    Deliberately lenient where upstream's `JsonRpcRequest` is strict: `openenv
    push` probes this route with `{}`, which that model rejects outright, and the
    refusal has to answer the validator's probe rather than argue with it.
    Anything unreadable yields a null id, which is what JSON-RPC 2.0 asks for
    when the id cannot be determined.
    """
    try:
        payload = json.loads(body)
    except ValueError:
        return None, None
    if not isinstance(payload, dict):
        return None, None
    request_id = payload.get("id")
    method = payload.get("method")
    # `bool` is a subclass of `int` in Python and is not a JSON-RPC id, so it is
    # excluded rather than echoed back as one.
    is_id = isinstance(request_id, str | int) and not isinstance(request_id, bool)
    return (request_id if is_id else None), (method if isinstance(method, str) else None)


def _mcp_refusal_frame(body: bytes | str) -> dict[str, Any]:
    """The whole JSON-RPC 2.0 response both `/mcp` transports answer."""
    request_id, method = _mcp_envelope(body)
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {
            "code": _MCP_TRANSPORT_ERROR_CODE,
            "message": _MCP_TRANSPORT_SUMMARY,
            "data": _mcp_transport_refusal(method),
        },
    }


def _refusing_mcp_route(route: APIRoute) -> APIRoute:
    """The same `POST /mcp`, answering the refusal instead of dispatching."""

    # The raw request and no declared model, for the reason the episode-control
    # refusal declares none either: a body that upstream's model rejects must
    # reach the refusal rather than a 422 about the shape of a request that was
    # never going to be served.
    async def refuse(request: Request) -> dict[str, Any]:
        return _mcp_refusal_frame(await request.body())

    return APIRoute(
        route.path,
        refuse,
        methods=sorted(route.methods or {"POST"}),
        name=route.name,
        tags=route.tags,
        summary="Refused: /mcp is not the MCP protocol and cannot hold an episode",
        description=_mcp_transport_message(),
        status_code=_MCP_TRANSPORT_STATUS,
        response_model=None,
        responses={
            _MCP_TRANSPORT_STATUS: {
                "description": (
                    "Refused: a JSON-RPC 2.0 error, code -32601. Use the WebSocket transport "
                    "at /ws."
                ),
            }
        },
    )


def _refusing_mcp_websocket(route: APIWebSocketRoute) -> APIWebSocketRoute:
    """The same `ws /mcp`, answering one refusal frame and then closing."""

    async def refuse(websocket: WebSocket) -> None:
        await websocket.accept()
        message = await websocket.receive()
        if message["type"] == "websocket.disconnect":
            return
        # Either key, because upstream's `receive_text` raises on a binary frame
        # and a caller who sent one still deserves the refusal.
        raw = message.get("text") or message.get("bytes") or b""
        # A peer that hangs up between its frame and this answer is not a server
        # error, but starlette raises on a write to a socket the peer has already
        # closed -- `WebSocketDisconnect` from `send_text` -- and an exception
        # escaping this handler costs uvicorn an `ERROR: Exception in ASGI
        # application` traceback for a connection that ended in the one way this
        # handler always ends it. Upstream guards the same two on the `ws /mcp`
        # handler this one replaces, and on the `/ws` handler it still owns --
        # `http_server.py` lines 1290 and 1767 in openenv 0.5.0.
        frame = json.dumps(_mcp_refusal_frame(raw), ensure_ascii=False)
        with suppress(WebSocketDisconnect, RuntimeError):
            await websocket.send_text(frame)
            # Closed rather than left open answering frame after frame. Every
            # method on this transport is refused, so a second frame could only
            # earn the same answer, and a socket that stays open implies a
            # negotiation that does not exist -- an MCP client would sit there
            # waiting for one. The refusal is stated once, in full, and the
            # connection ends normally.
            await websocket.close()

    return APIWebSocketRoute(route.path, refuse, name=route.name)


def _refuse_mcp_transport(served: FastAPI) -> None:
    """Swap both `/mcp` handlers for one that refuses, keeping the path registered.

    Registered and not deleted, for the reason the episode-control paths are
    kept: `openenv push` reads an app by its path names, and `mcp_endpoint` in
    `openenv/cli/_validation.py` requires a `/mcp` that answers 200 with a
    JSON-RPC body. The path and that contract are honoured; only the four
    methods behind them are refused.
    """
    for index, route in enumerate(served.router.routes):
        if getattr(route, "path", None) != _MCP_ROUTE:
            continue
        if isinstance(route, APIRoute):
            served.router.routes[index] = _refusing_mcp_route(route)
        elif isinstance(route, APIWebSocketRoute):
            served.router.routes[index] = _refusing_mcp_websocket(route)


def app(
    world: World,
    *,
    include_control_tools: bool = False,
    max_concurrent_envs: int = DEFAULT_MAX_CONCURRENT_ENVS,
    session_timeout: float | None = DEFAULT_SESSION_TIMEOUT,
) -> FastAPI:
    """The ASGI app serving one world: one session per instance, many sessions.

    `include_control_tools` makes `controller_run_sql` callable over the wire. It
    is never listed either way; the flag is for a harness that drives the world
    itself, and a server an agent talks to should not have it. The tool is
    deprecated -- the `state` message is what an eval reads now -- and a call of
    it warns in the server's process.

    OpenEnv's plain-HTTP `/reset`, `/step` and `/state` are replaced by a route
    that refuses them, because upstream cannot hold an episode over HTTP and
    answers a throwaway environment instead. Both `/mcp` routes are replaced the
    same way: that dialect is not MCP, and it has no reset, so nothing on it can
    reach an instance. The websocket transport at `/ws` is the product and is
    untouched, `{"type": "mcp"}` frames included.

    `session_timeout` is seconds of inactivity before OpenEnv reaps a session,
    or `None` for no reaper. It is passed as part of a `ConcurrencyConfig`
    because OpenEnv refuses both that and `max_concurrent_envs` together, and
    `env_name` is passed explicitly because the name OpenEnv reads off a factory
    is the factory's own `__name__`, which here is `_factory` and not the world.
    """

    # A plain `def` and never a `functools.partial`.
    # `WebInterfaceManager.__init__` builds the environment only when
    # `inspect.isclass` or `inspect.isfunction` says the factory is one
    # (`web_interface.py` lines 249-254 in openenv 0.4.2), and both answer
    # `False` for a partial, which is then stored unbuilt -- `/web/reset`,
    # `/web/step` and `/web/state` then read an environment attribute off the
    # factory object itself. `load_environment_metadata` has the same gate.
    # `ENABLE_WEB_INTERFACE=true` -- what `openenv push` writes into a Space's
    # Dockerfile -- is where it shows.
    # A plain function costs one environment at app-creation time:
    # `_validate_concurrency_safety` (`http_server.py` lines 276-305) reads
    # `SUPPORTS_CONCURRENT_SESSIONS` off the class when it can unwrap one from
    # the factory and builds and closes an environment when it cannot, so
    # `SeahavenEnv.__init__` has to stay free of side effects.
    def _factory() -> SeahavenEnv:
        return SeahavenEnv(world, include_control_tools=include_control_tools)

    served = create_app(
        _factory,
        CallToolAction,
        SeahavenObservation,
        env_name=world.name,
        # `GET /schema` answers `state_cls.model_json_schema()` and nothing else
        # publishes the state shape, so a client that is not told the class here
        # is told OpenEnv's bare `State` instead. The parameter arrived in openenv
        # 0.5.0, which is what closed huggingface/OpenEnv#1155; 0.4.2 had no way
        # to say this.
        state_cls=SeahavenState,
        concurrency_config=ConcurrencyConfig(
            max_concurrent_envs=max_concurrent_envs,
            session_timeout=session_timeout,
        ),
    )
    _refuse_http_episode_control(served)
    _refuse_mcp_transport(served)
    return served
