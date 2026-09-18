"""`seahaven serve`: the gate, the app, and one uvicorn worker.

The three lines `seahaven serve` is made of, kept here rather than in the CLI so
that the CLI module is argument parsing and nothing else -- and so that a
harness that wants a server in its own process can call `serve(world, ...)`
without going through argv.

**One worker, always.** A session is in-process state: the instance, its
connections and its working directory live on the environment object that
OpenEnv made for that connection. A second worker process would answer a
session's second frame with an environment that has never seen its first.
Scaling out is more processes behind a load balancer with connection affinity,
which is the operator's business; hosting is out of Seahaven's scope.
"""

import uvicorn

from seahaven.instances import default_concurrency, set_concurrency
from seahaven.openenv import (
    DEFAULT_MAX_CONCURRENT_ENVS,
    DEFAULT_SESSION_TIMEOUT,
    app,
)
from seahaven.world import World

__all__ = ["DEFAULT_HOST", "DEFAULT_PORT", "serve"]

# A container serves on the network it was given, not on loopback; an
# operator who wants loopback says so with `--host 127.0.0.1`.
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8000
LOG_LEVEL = "info"


def serve(
    world: World,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    max_concurrent_envs: int = DEFAULT_MAX_CONCURRENT_ENVS,
    concurrency: int | None = None,
    session_timeout: float | None = DEFAULT_SESSION_TIMEOUT,
    include_control_tools: bool = False,
) -> None:
    """Serve one world until the process is stopped.

    `concurrency` is the process-wide gate on how many tool calls execute at
    once -- not how many sessions are admitted, which is `max_concurrent_envs`.
    `None` leaves it at the framework's default (`min(cpus, 16)`, which follows
    a container's CPU affinity) and `0` removes it. It is set before the app is
    built, so it is in force for the first call the server takes.

    `session_timeout` is seconds of idleness before OpenEnv reaps a session, or
    `None` for no reaper; the CLI's `--session-timeout 0` is spelled `None`
    here, because that is what OpenEnv wants and a `0` it would refuse.
    """
    set_concurrency(default_concurrency() if concurrency is None else concurrency)
    uvicorn.run(
        app(
            world,
            include_control_tools=include_control_tools,
            max_concurrent_envs=max_concurrent_envs,
            session_timeout=session_timeout,
        ),
        host=host,
        port=port,
        # An ASGI app object rather than an import string, so `workers` is the
        # only spelling of "one process" uvicorn will take.
        workers=1,
        log_level=LOG_LEVEL,
    )
