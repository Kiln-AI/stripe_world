"""A real server for the HTTP tests: uvicorn on a free port, in a thread."""

import threading
import time
from collections.abc import Iterator

import pytest
import seahaven.http

from seahaven_stripe_world import world
from seahaven_stripe_world.http_api import handle

uvicorn = pytest.importorskip("uvicorn")


@pytest.fixture
def server_url() -> Iterator[str]:
    """`http://127.0.0.1:<port>`, serving this world as `serve_http.py` does with no options."""
    served = seahaven.http.app(world, handle)
    server = uvicorn.Server(uvicorn.Config(served, host="127.0.0.1", port=0, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert thread.is_alive() and time.monotonic() < deadline, "the server did not start"
        time.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
