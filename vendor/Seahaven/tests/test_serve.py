"""`serve()`: the gate it sets, and the arguments it hands uvicorn.

Every other module in this group starts a real server, because that is the only
way to prove a session is a session. This one does not: what `serve` adds over
`app` is three decisions -- the gate is sized before the app is built, the app
gets the operator's numbers, and uvicorn is given one worker and an app object
rather than an import string -- and a server that runs until the process is
stopped is not the way to read any of them. `uvicorn.run` is replaced, and the
arguments `serve` handed it are the assertion.

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

    `app` is wrapped rather than replaced, so an app `serve` builds wrongly
    still fails here, and the stand-in for `uvicorn.run` builds a real
    `uvicorn.Config` from the arguments it was given, so a set of arguments
    `Config` would refuse fails here too. `Config` takes a superset of
    `uvicorn.run`'s own keyword parameters, so that is a check on the arguments
    `Config` accepts, not on everything `uvicorn.run` would refuse.

    The arguments are read as `serve` passed them, never off the built
    `Config`. `Config.__init__` fills in a default for every one of them, so a
    `Config` answers `workers == 1` whether or not anything asked for one
    worker, and the one-worker assertion below would hold with the line that
    makes it true deleted.
    """
    call: dict[str, Any] = {}
    build = serve_module.app

    def app(world: World, **options: Any) -> Any:
        call["options"] = options
        return build(world, **options)

    def run(app: Any, **kwargs: Any) -> None:
        call["app"] = app
        call["kwargs"] = kwargs
        call["config"] = serve_module.uvicorn.Config(app, **kwargs)
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
        "console": True,
    }
    assert (DEFAULT_MAX_CONCURRENT_ENVS, DEFAULT_SESSION_TIMEOUT) == (500, 3600.0)


def test_serve_can_expose_the_control_tools(world: World, served: dict[str, Any]) -> None:
    """The flag is the operator's, and `serve` is the only thing that carries it."""
    serve(world, include_control_tools=True)
    assert served["options"]["include_control_tools"] is True


def test_serve_announces_the_console_at_an_address_a_browser_can_open(
    world: World, served: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    """The line an operator reads after `seahaven serve`.

    The default bind is `0.0.0.0`, which is not an address, so the message names
    loopback. `console_url` owns that translation and is tested against every
    spelling in `test_server.py`.

    `capsys` and not `caplog`: the line is a `print`, because until `uvicorn.run`
    configures logging the root logger has no handler and sits at WARNING, and a
    logging call in `serve` reaches no operator. A rewrite onto a logger fails
    here.
    """
    serve(world)
    assert (
        capsys.readouterr().out
        == "Starting. Web console will be available at http://127.0.0.1:8000/console\n"
    )
    serve(world, host="127.0.0.1", port=8001)
    assert (
        capsys.readouterr().out
        == "Starting. Web console will be available at http://127.0.0.1:8001/console\n"
    )


def test_serve_without_a_console_neither_serves_nor_announces_one(
    world: World, served: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    """`--no-console`: the route is not registered and nothing is printed."""
    serve(world, console=False)
    assert served["options"]["console"] is False
    assert capsys.readouterr().out == ""
