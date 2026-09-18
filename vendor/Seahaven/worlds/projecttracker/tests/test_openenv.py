"""`projecttracker.openenv_app:app` on a real port, driven by two real clients.

The rest of this suite drives the world in process. This module drives the
object a container runs -- the module-level `app` an ASGI import string points
at -- over a websocket, because "this world is an OpenEnv environment" is a
claim about that object and about the wire, and nothing in process can check it.

Both clients are driven on purpose. `SeahavenClient` is Seahaven's own and knows
this world's frames; `GenericEnvClient` is OpenEnv's stock client and knows
nothing at all. A world that only the first can reach is not on the OpenEnv wire
whatever its README says, and the stock client is the only thing that proves it.

The serve extra is optional (`pip install "projecttracker[serve]"`), so the
whole module skips without it, and `conftest.py` stays clear of `openenv` so the
tests that do not need it keep running.
"""

import json
import re
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from conftest import FIXTURE_NOW, SMALL_STARTUP

# The subpackage and not `openenv`: what this module imports is
# `seahaven.openenv`, so that is what has to import for the tests below to mean
# anything. Only an `ImportError` skips -- an extra that is absent, or installed
# and unimportable. Anything else raises, and CI asserts this import separately,
# because an installed extra that skips quietly is a green run that tested none
# of this.
pytest.importorskip(
    "seahaven.openenv", exc_type=ImportError, reason="the serve extra does not import here"
)

import uvicorn
from openenv import GenericEnvClient
from openenv.core.env_server.mcp_types import CallToolAction, ListToolsAction

from projecttracker.openenv_app import app
from seahaven.openenv import SeahavenClient

# How long `serving` waits for uvicorn to bind, and for it to stop again.
START_TIMEOUT = 30.0
STOP_TIMEOUT = 30.0


@contextmanager
def serving() -> Iterator[str]:
    """Serve this world's app on a free port for the block, and answer its URL.

    A copy of `tests/serving.py` in the framework's own suite, and deliberately
    so: this is a world author's suite, it runs with this directory as its
    rootdir, and it cannot import the framework's tests. What it serves is not a
    copy -- it is the `app` object the package publishes, imported by name.

    Port `0` rather than a scan for a free one: asking the kernel is the only
    way that does not race with whatever takes the port between scan and bind.
    """
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))
    thread = threading.Thread(target=server.run, name="test-uvicorn", daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{_port_of(server)}"
    finally:
        server.should_exit = True
        thread.join(STOP_TIMEOUT)
        assert not thread.is_alive(), "the server did not stop"


def _port_of(server: uvicorn.Server) -> int:
    """Wait for the bind and read back the port the kernel gave it."""
    deadline = time.monotonic() + START_TIMEOUT
    while time.monotonic() < deadline:
        if server.started:
            return int(server.servers[0].sockets[0].getsockname()[1])
        time.sleep(0.01)
    raise AssertionError("the server never started")


def test_the_typed_client_lists_and_calls_this_worlds_tools() -> None:
    """The flow an eval runs: connect, reset onto a fixture, list, call, read state."""
    with serving() as url, SeahavenClient(base_url=url) as env:
        reset = env.reset(fixture="empty")
        assert reset.observation.metadata == {"fixture": "empty", "now": FIXTURE_NOW, "tools": 27}
        names = {tool["name"] for tool in env.list_tools()}
        assert {"create_issue", "search_issues", "run_sql"} <= names
        observation = env.call("create_user", email="ada@tracker.invalid", name="Ada")
        assert observation.error is None
        assert observation.result["email"] == "ada@tracker.invalid"
        assert observation.result["created_at"] == FIXTURE_NOW
        state = env.state()
        assert state.world.name == "projecttracker"
        assert state.fixture is not None and state.fixture.id == "empty"
        assert state.now == FIXTURE_NOW
        # The document, not a placeholder: the user this session created is in
        # the change log the state message carried back.
        assert [record["table"] for record in state.state["db"]["log"]] == ["users"]


def test_reset_names_the_person_the_session_drives_the_tracker_as() -> None:
    """`user_id` is this world's startup keyword, and it reaches the hook over the wire.

    The property `functional_spec.md` §21 point 3 is about, on this world's own
    hook: what an eval passes to `reset` becomes the actor of every write that
    names none.
    """
    with serving() as url, SeahavenClient(base_url=url) as env:
        env.reset(fixture=SMALL_STARTUP)
        admin = env.call("list_users", role="admin").result["users"][0]
        member = env.call("list_users", role="member").result["users"][0]
        project = env.call("list_projects").result["projects"][0]
        # No `user_id`: the hook fell back to the workspace's first admin.
        assert (
            env.call("create_issue", project_id=project["id"], title="A").result["creator_id"]
            == admin["id"]
        )

    with serving() as url, SeahavenClient(base_url=url) as env:
        env.reset(fixture=SMALL_STARTUP, user_id=member["id"])
        assert (
            env.call("create_issue", project_id=project["id"], title="B").result["creator_id"]
            == member["id"]
        )


def test_the_stock_client_reaches_the_same_world_with_no_seahaven_on_its_side() -> None:
    """Dictionaries in, dictionaries out: OpenEnv's own client, unmodified."""
    with serving() as url, GenericEnvClient(base_url=url) as env:
        env.reset(fixture="empty")
        listed = env.step(ListToolsAction().model_dump()).observation
        tools = {tool["name"]: tool for tool in listed["tools"]}
        assert len(tools) == 27
        assert "full-text" in tools["search_issues"]["description"]
        assert tools["list_issues"]["input_schema"]["properties"]["limit"]["default"] == 50
        result = env.step(CallToolAction(tool_name="list_issues", arguments={}).model_dump())
        assert result.observation == {
            "tool_name": "list_issues",
            "result": {"issues": [], "next_cursor": None, "has_next": False},
            "error": None,
            "metadata": {},
        }
        assert (result.done, result.reward) == (False, None)


def test_this_worlds_error_words_survive_the_wire() -> None:
    """The error handler's `INVALID_INPUT`, as JSON, to a client that never imports it."""
    with serving() as url, GenericEnvClient(base_url=url) as env:
        env.reset(fixture="empty")
        result = env.step(
            CallToolAction(
                tool_name="create_user", arguments={"email": "nope", "name": "Ada"}
            ).model_dump()
        )
        assert result.observation["result"] is None
        # Upstream's two-key shape carries the text; this world's own vocabulary
        # -- the code it chose and the field it names -- rides beside it.
        assert result.observation["error"] == {
            "error_type": "execution_error",
            "message": "email: 'nope' is not an email address",
        }
        seahaven_error = result.observation["metadata"]["seahaven_error"]
        assert seahaven_error["code"] == "INVALID_INPUT"
        assert seahaven_error["details"] == {"field": "email"}


def test_control_tools_are_not_served_by_this_worlds_app() -> None:
    """The app is built with the default, which is that an agent cannot reach them."""
    with serving() as url, SeahavenClient(base_url=url) as env:
        env.reset(fixture="empty")
        assert "controller_run_sql" not in {tool["name"] for tool in env.list_tools()}
        assert env.call("controller_run_sql", sql="SELECT 1").seahaven_error == {
            "code": "unknown_tool",
            "message": "unknown tool: controller_run_sql",
            "details": {"name": "controller_run_sql"},
        }


def test_two_sessions_of_this_world_do_not_see_each_other() -> None:
    """One session is one instance: a write in one is invisible in the other."""
    with (
        serving() as url,
        SeahavenClient(base_url=url) as first,
        SeahavenClient(base_url=url) as second,
    ):
        first.reset(fixture="empty")
        second.reset(fixture="empty")
        assert first.state().episode_id != second.state().episode_id
        first.call("create_user", email="ada@tracker.invalid", name="Ada")
        assert len(first.call("list_users").result["users"]) == 1
        assert second.call("list_users").result["users"] == []


def test_seahaven_serve_really_serves_this_world() -> None:
    """The production entry point, in a process of its own, on a real port.

    Every other test here builds the app and hands it to a uvicorn this process
    started. `serve()` is what `seahaven serve` calls, and it owns three things
    no assembled app can show: it sizes the concurrency gate, it starts uvicorn
    itself, and it does so with one worker. So it is run for real, in a
    subprocess, and the port is read out of the line uvicorn logs -- which is
    also why the log level is `info` and not something quieter.
    """
    program = (
        "import seahaven.openenv.serve as s; "
        "import projecttracker as p; "
        "s.serve(p.world, host='127.0.0.1', port=0)"
    )
    server = subprocess.Popen(
        [sys.executable, "-c", program], stderr=subprocess.PIPE, text=True, bufsize=1
    )
    try:
        assert server.stderr is not None
        url = None
        deadline = time.monotonic() + START_TIMEOUT
        while time.monotonic() < deadline:
            line = server.stderr.readline()
            if not line:
                break
            found = re.search(r"Uvicorn running on (http://127\.0\.0\.1:\d+)", line)
            if found:
                url = found.group(1)
                break
        assert url is not None, "the server never said where it was listening"
        with SeahavenClient(base_url=url) as env:
            env.reset(fixture=SMALL_STARTUP)
            assert env.call("get_issue", key="ENG-1").result["key"] == "ENG-1"
    finally:
        server.terminate()
        server.wait(STOP_TIMEOUT)


def test_the_app_is_the_module_level_object_an_import_string_names() -> None:
    """`uvicorn projecttracker.openenv_app:app` resolves, and to an ASGI callable.

    Asserted in a fresh interpreter: this module has imported the world a dozen
    other ways by now, and what a container does is import that one string.
    """
    program = (
        "import importlib; "
        "module = importlib.import_module('projecttracker.openenv_app'); "
        "print(json.dumps({'callable': callable(module.app), "
        "'routes': sorted(r.path for r in module.app.routes)}))"
    )
    done = subprocess.run(
        [sys.executable, "-c", "import json; " + program],
        capture_output=True,
        text=True,
        check=True,
    )
    served = json.loads(done.stdout)
    assert served["callable"] is True
    assert "/ws" in served["routes"]
