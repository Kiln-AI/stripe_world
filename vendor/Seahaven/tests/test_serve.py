"""`serve()`: the gate it sets, and the arguments it hands uvicorn.

Every other module in this group starts a real server, because that is the only
way to prove a session is a session. This one does not: what `serve` adds over
`app` is three decisions -- the gate is sized before the app is built, the app
gets the operator's numbers, and uvicorn is given one worker and an app object
rather than an import string -- and a server that runs until the process is
stopped is not the way to read any of them. `uvicorn.run` is replaced, and the
call it would have made is the assertion.

The one-worker rule is the reason this file exists at all. A second worker
process answers a session's second frame with an environment that has never seen
its first, and nothing in a single-process test suite would ever notice.
"""

from typing import Any

import pytest

from seahaven import instances
from seahaven.world import World

# The subpackage and not `openenv`: what this module imports is
# `seahaven.openenv`, so that is what has to import for the tests below to mean
# anything. Only an `ImportError` skips -- an extra that is absent, or installed
# and unimportable. Anything else raises, and CI asserts this import separately,
# because an installed extra that skips quietly is a green run that tested none
# of this.
pytest.importorskip(
    "seahaven.openenv", exc_type=ImportError, reason="the serve extra does not import here"
)

from seahaven.openenv import DEFAULT_MAX_CONCURRENT_ENVS, DEFAULT_SESSION_TIMEOUT
from seahaven.openenv import serve as serve_module
from seahaven.openenv.serve import DEFAULT_HOST, DEFAULT_PORT, serve


@pytest.fixture
def served(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Record what `serve` does instead of letting it serve for ever.

    `uvicorn.run` is replaced outright. `app` is wrapped rather than replaced:
    the real one is still built from the real arguments -- so a call `app` would
    refuse still fails here -- and the arguments it was handed are recorded on
    the way past, which is the claim `serve` is making.
    """
    call: dict[str, Any] = {}
    build = serve_module.app

    def app(world: World, **options: Any) -> Any:
        call["options"] = options
        return build(world, **options)

    def run(app: Any, **kwargs: Any) -> None:
        call["app"] = app
        call["kwargs"] = kwargs
        # The gate's size as it stands when uvicorn would start: the ordering
        # claim. `instances.concurrency()` rather than `_gate._initial_value`,
        # which reached into two libraries' privates to read one number.
        call["concurrency"] = instances.concurrency()

    monkeypatch.setattr(serve_module, "app", app)
    monkeypatch.setattr(serve_module.uvicorn, "run", run)
    return call


def test_serve_runs_one_worker_on_an_app_object(world: World, served: dict[str, Any]) -> None:
    """An app object and `workers=1`, which is the only spelling uvicorn honours."""
    serve(world)
    assert served["kwargs"]["workers"] == 1
    assert not isinstance(served["app"], str), "an import string would let uvicorn fork workers"
    assert callable(served["app"])
    # The literals, not the constants: comparing a default against itself would
    # pass whatever the default became, and "which interface" is the decision.
    assert (served["kwargs"]["host"], served["kwargs"]["port"]) == ("0.0.0.0", 8000)
    assert (DEFAULT_HOST, DEFAULT_PORT) == ("0.0.0.0", 8000)
    # An operator watching a training run reads uvicorn's request log; a quieter
    # default would be a decision, and it is not the one that was made.
    assert served["kwargs"]["log_level"] == "info"


def test_serve_binds_where_it_is_told(world: World, served: dict[str, Any]) -> None:
    serve(world, host="127.0.0.1", port=9123)
    assert (served["kwargs"]["host"], served["kwargs"]["port"]) == ("127.0.0.1", 9123)


def test_serve_sizes_the_gate_before_the_server_starts(
    world: World, served: dict[str, Any]
) -> None:
    """`--concurrency 3`: three slots, in force before the first frame arrives."""
    serve(world, concurrency=3)
    assert served["concurrency"] == 3


def test_serve_without_a_concurrency_uses_the_frameworks_default(
    world: World, served: dict[str, Any]
) -> None:
    instances.set_concurrency(1)
    serve(world)
    assert served["concurrency"] == instances.default_concurrency()


def test_serve_with_a_concurrency_of_zero_removes_the_gate(
    world: World, served: dict[str, Any]
) -> None:
    serve(world, concurrency=0)
    # `0` is how `set_concurrency` spells "no gate"; that it really stops gating
    # is `test_concurrency_zero_removes_the_gate` in the instances suite.
    assert served["concurrency"] == 0


def test_serve_passes_the_operators_session_numbers_to_the_app(
    world: World, served: dict[str, Any]
) -> None:
    """The app is built from `serve`'s arguments, not from the module defaults."""
    serve(world, max_concurrent_envs=7, session_timeout=None)
    assert served["options"]["max_concurrent_envs"] == 7
    assert served["options"]["session_timeout"] is None


def test_serve_passes_the_module_defaults_when_it_is_told_nothing(
    world: World, served: dict[str, Any]
) -> None:
    serve(world)
    assert served["options"] == {
        "include_control_tools": False,
        "max_concurrent_envs": 500,
        "session_timeout": 3600.0,
    }
    assert (DEFAULT_MAX_CONCURRENT_ENVS, DEFAULT_SESSION_TIMEOUT) == (500, 3600.0)


def test_serve_can_expose_the_control_tools(world: World, served: dict[str, Any]) -> None:
    """The flag is the operator's, and `serve` is the only thing that carries it."""
    serve(world, include_control_tools=True)
    assert served["options"]["include_control_tools"] is True
