"""What every test here starts from: an XML-RPC client, and a throwaway world.

A live instance comes from Seahaven's own pytest plugin -- `@pytest.mark.seahaven`
says which fixture it starts from and the `instance` fixture makes it -- against
`tracker_rpc`, which `pyproject.toml` names with `--seahaven-world`. Nothing here
builds one, and nothing here calls a tool function or a handler directly: what is
being tested is a tool, and a tool is the framework's work on a signature, which
only a real call does.

The other side of the wire is `xmlrpc.client`, the standard library's own client.
`method_call` builds the request with `dumps` and `rpc` reads the answer with
`loads`, so a fault arrives as `xmlrpc.client.Fault` and a test asserts on
`faultCode` the way a client would. A test that built its own XML and matched it
with a regular expression would prove only that the extension agrees with the
test; this proves the documents are XML-RPC.
"""

import xmlrpc.client
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest

import seahaven
import seahaven_xmlrpc
from projecttracker.middleware.error_handler import error_handler
from tracker_rpc.world import world as tracker

# The instant every blank instance in this suite is frozen at. These tests use no
# fixture on disk: the extension is about the call path, and a blank instance of
# a copy of ProjectTracker is the whole of the state it needs.
NOW = "2026-06-01T09:00:00.000Z"

# The tool `tracker_rpc` registered the endpoint under, which is also the name its
# `render_faults` covers.
RPC = "rpc"


def method_call(method: str, *params: Any, allow_none: bool = False) -> str:
    """One `<methodCall>` document, as a client would send it."""
    return xmlrpc.client.dumps(params, methodname=method, allow_none=allow_none)


def rpc(instance: seahaven.Instance, method: str, *params: Any, tool: str = RPC) -> Any:
    """Call `method` over the endpoint and read the answer as a client reads it.

    Raises `xmlrpc.client.Fault` when the server answered with a fault, which is
    what `loads` does and what makes a fault assertion in these tests read the
    way it would in a client's own test.
    """
    (value,), _ = xmlrpc.client.loads(instance.call(tool, body=method_call(method, *params)))
    return value


@pytest.fixture
def probe(tmp_path: Path) -> Callable[..., seahaven.World]:
    """Builds a throwaway world around handlers a copy of ProjectTracker should not carry.

    A handler that raises `TypeError`, one that returns something XML-RPC cannot
    put on the wire, an endpoint registered without its middleware: each is a real
    case and none of them belongs in a world that is meant to read like a world.
    Registering them on `tracker_rpc` would add them to the world every other test
    in the session sees, and registration is for the life of the process.

    What the throwaway keeps is everything that is not the test's: the same
    schema, and ProjectTracker's own error handler outermost.
    """

    def build(
        methods: Mapping[str, seahaven_xmlrpc.MethodHandler],
        *,
        tools: tuple[seahaven.Tool, ...] = (),
        render_faults: bool = True,
        **options: Any,
    ) -> seahaven.World:
        built = seahaven.World(
            "probe",
            tracker.version,
            tracker.schema,
            fixtures_dir=tmp_path / "fixtures",
            work_dir=tmp_path / "work",
            state_format="seahaven.state/1",
        )
        built.middleware(error_handler)
        if render_faults:
            built.middleware(seahaven_xmlrpc.render_faults(tools=[RPC]))
        built.tool(seahaven_xmlrpc.xmlrpc_call(methods=methods, name=RPC, **options))
        for tool in tools:
            built.tool(tool)
        return built

    return build
