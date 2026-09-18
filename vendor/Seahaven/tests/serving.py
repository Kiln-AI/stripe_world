"""A real server on a real port, for the tests that will not settle for less.

Not an ASGI test client. Every defect on this project that cost a review round
passed a unit test and failed on a real call, and the transport is exactly where
that happens: a websocket frame, a session executor, a thread that is not the
test's. So these tests start uvicorn in a thread on a port the kernel chose, and
talk to it the way an eval does.

Port `0` rather than a scan for a free one: asking the kernel is the only way
that does not race with whatever takes the port between the scan and the bind.

`worlds/projecttracker/tests/test_openenv.py` carries the same helper,
because a world's suite is a world author's suite: it runs with that directory
as its rootdir and cannot import the framework's tests.

Importing this module imports `openenv`, so every module that imports it does so
below its own `importorskip`, never at the top of the file.
"""

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import uvicorn

from seahaven.openenv import app
from seahaven.world import World

START_TIMEOUT = 30.0
STOP_TIMEOUT = 30.0


@contextmanager
def serving(world: World, **options: Any) -> Iterator[str]:
    """Serve a world on a free port for the block, and answer its base URL."""
    server = uvicorn.Server(
        uvicorn.Config(app(world, **options), host="127.0.0.1", port=0, log_level="warning")
    )
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
