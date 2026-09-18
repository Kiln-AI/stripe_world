"""The typed client: one client for every Seahaven world.

Every world speaks the same wire shape -- `CallToolAction` in, an observation
carrying `result` or `error` out -- so there is one client rather than one per
world. That is why a world's `client.py` in a hub push is a single re-export
(`components/openenv.md` §6) and not generated code.

`SeahavenClient` is OpenEnv's `EnvClient` with the three parsers filled in and
two conveniences on top. Both conveniences go through `_dispatch`, which is how
the base client answers a result in synchronous code and an awaitable in
asynchronous code from the same method; writing them in terms of the public
`step()` would have answered `step`'s dual-mode wrapper instead of the
observation, so the sketch in the component document is spelled out here rather
than copied.
"""

from typing import Any, Self

from openenv.core.client_types import StepResult
from openenv.core.env_client import EnvClient
from openenv.core.env_server.mcp_types import CallToolAction, ListToolsAction
from openenv.core.env_server.types import Observation

from seahaven.openenv.env import SeahavenObservation, SeahavenState

__all__ = ["SeahavenClient"]


class SeahavenClient(EnvClient[CallToolAction | ListToolsAction, Observation, SeahavenState]):
    """A connected session on a `seahaven serve` server.

    ```python
    with SeahavenClient(base_url="http://127.0.0.1:8000") as env:
        env.reset(fixture="empty", seed=7).observation.metadata  # fixture, now, tools
        tools = env.list_tools()
        obs = env.call("ping", message="hello")
        obs.result, obs.error, obs.seahaven_error
        state = env.state()
        state.state["db"]["log"]  # the format's output; here, the change log
        final_state = state.model_dump(exclude={"step_count"})  # the document
    ```

    The observation parameter is OpenEnv's base `Observation`, because a reset
    and a tool call are not the same shape. The server answers a reset with a
    plain `Observation` whose `metadata` carries `fixture`, `now` and `tools`,
    and typing the parameter this way is what keeps `.result` off a reset:
    `_parse_result` below says what a typed client makes of that frame. `call`,
    and `step` on a `CallToolAction`, answer a `SeahavenObservation`, the shape
    of a tool call.

    `state()` answers the whole state document (`functional_spec.md` §3.1) plus
    OpenEnv's `step_count`: `.state` is the formatter's output, everything else
    but `step_count` is the framework's envelope, and
    `.model_dump(exclude={"step_count"})` is the document a harness saves as
    `final_state` -- byte for byte what `inst.state()` answers in process.
    `reset(state_format=...)` chooses the format for the episode.

    A tool error arrives on the observation and never raises, and exactly one of
    `obs.result` and `obs.error` is set. `obs.error` is OpenEnv's own
    `{error_type, message}`, and `obs.seahaven_error` is the
    `{"code", "message", "details"}` the world produced. Only a framework or
    protocol failure -- a `WorldBug` out of a world's own code, a malformed
    frame, a closed connection -- raises, as `RuntimeError`, which is the stock
    client's behaviour too. A `WorldBug` raises with the fixed message the server
    sends for one, `internal error (<id>)`; the author's own wording and its
    traceback are in the server's log under that id.
    """

    def __init__(
        self,
        base_url: str | None = None,
        *args: Any,
        websocket_ping_timeout_s: float | None = 120.0,
        **kwargs: Any,
    ) -> None:
        """Default the websocket pong timeout to two minutes, not OpenEnv's twenty seconds.

        A session is one websocket connection holding one instance, and there is
        no resume: any disconnect destroys the instance and the episode, and
        reconnecting mints a fresh environment. So if the server goes
        unresponsive at the event-loop level for longer than the ping timeout,
        every connected client gives up at once and every one of those episodes
        is lost -- one stall, many episodes, unrecoverable. Widening the timeout
        widens the window a server can stall through without taking the episodes
        with it.

        The honest cost: a server that is genuinely dead, or a network that is
        genuinely severed, now takes up to two minutes to notice instead of
        twenty seconds. That is the right trade for a long eval or RL rollout
        and a worse one for a short interactive session -- which is why this is
        a default and not a fixed value; a caller who passes
        `websocket_ping_timeout_s` explicitly still gets exactly that value.
        `websocket_ping_interval_s` is untouched, still OpenEnv's `20.0`.
        """
        super().__init__(
            base_url, *args, websocket_ping_timeout_s=websocket_ping_timeout_s, **kwargs
        )

    def __enter__(self) -> Self:
        """The base client's `__enter__`, narrowed to this client's own type.

        `EnvClient.__enter__` is annotated as returning `EnvClient`, so `with
        SeahavenClient(...) as env` would hand back something with no `call` and
        no `list_tools` as far as a type checker is concerned -- and a world
        author would be told the two verbs this client exists for do not exist.
        """
        super().__enter__()
        return self

    async def __aenter__(self) -> Self:
        """The same narrowing for `async with`."""
        await super().__aenter__()
        return self

    def _step_payload(self, action: CallToolAction | ListToolsAction) -> dict[str, Any]:
        return action.model_dump()

    def _parse_result(self, payload: dict[str, Any]) -> StepResult[Observation]:
        """A reply frame as a `StepResult` carrying a `SeahavenObservation`.

        The base client parses the reply to a `step` and the reply to a `reset`
        through this one hook, so both arrive as a `SeahavenObservation`. A tool
        call fills `tool_name` and one of `result` and `error`, and an error also
        fills `metadata["seahaven_error"]`, which `obs.seahaven_error` reads. A
        reset fills only the inherited `metadata`, because the server answers a
        reset with a plain `Observation` and this model's own three fields all
        have defaults.

        Reading `result` off a reset is therefore `None` at runtime rather than
        an error, which is why this client is parameterised on `Observation`
        instead: `reset(...).observation.result` does not type-check, and
        `metadata` is how a reset is read.

        A `ListToolsAction` answers a `ListToolsObservation`, which has a
        `tools` list and no `result`; it does not come through here, and
        `list_tools` below says why.
        """
        return StepResult(
            observation=SeahavenObservation.model_validate(payload.get("observation", {})),
            reward=payload.get("reward"),
            done=payload.get("done", False),
            metadata=payload.get("metadata"),
        )

    def _parse_state(self, payload: dict[str, Any]) -> SeahavenState:
        return SeahavenState.model_validate(payload)

    def call(self, tool: str, /, **arguments: Any) -> Any:
        """Call a tool and answer the observation. Awaitable in asynchronous code.

        The observation, not the `StepResult` around it: a Seahaven step is never
        done and never rewarded, so the wrapper carries nothing a caller of this
        client wants. Read `.result`, or `.error` and `.seahaven_error`.

        The tool name is positional-only, as `Instance.call`'s is, and for the
        same reason: a world is free to declare a tool argument called `tool` --
        or `self` -- and `**arguments` must be able to carry it. Without the `/`
        such a tool lists, works through `step(CallToolAction(...))`, and cannot
        be called through this client at all.
        """
        return self._dispatch(lambda: self._call_async(tool, **arguments))

    def list_tools(self) -> Any:
        """The world's tool list, as OpenEnv spells it. Awaitable in asynchronous code.

        Each entry is `{"name", "description", "input_schema"}`. Control tools are
        never listed, whatever the server was started with.

        This is the one verb that reads the frame itself instead of parsing it
        into `SeahavenObservation`: that model forbids extra fields and has no
        `tools`, because it is the shape of a tool *call*. The list is read
        straight off the step payload as `list[dict]`.
        """
        return self._dispatch(self._list_tools_async)

    async def _call_async(self, tool: str, /, **arguments: Any) -> SeahavenObservation:
        # Read off the frame rather than through `_step_async`, whose result is
        # typed on the base `Observation` this client is parameterised with; a
        # tool call's observation is the `SeahavenObservation` and this is the
        # verb that promises one.
        action = CallToolAction(tool_name=tool, arguments=arguments)
        response = await self._send_and_receive(
            {"type": "step", "data": self._step_payload(action)}
        )
        return SeahavenObservation.model_validate(response.get("data", {}).get("observation", {}))

    async def _list_tools_async(self) -> list[dict[str, Any]]:
        response = await self._send_and_receive(
            {"type": "step", "data": ListToolsAction().model_dump()}
        )
        observation = response.get("data", {}).get("observation", {})
        return list(observation.get("tools", []))
