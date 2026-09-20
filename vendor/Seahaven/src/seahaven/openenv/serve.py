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
    CONSOLE_PATH,
    DEFAULT_MAX_CONCURRENT_ENVS,
    DEFAULT_SESSION_TIMEOUT,
    app,
)
from seahaven.world import World

__all__ = ["DEFAULT_HOST", "DEFAULT_PORT", "console_url", "serve"]

# A container serves on the network it was given, not on loopback; an
# operator who wants loopback says so with `--host 127.0.0.1`.
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8000
LOG_LEVEL = "info"


def console_url(host: str, port: int) -> str:
    """The address to print for the console, which is not always the bind address.

    A server bound to `0.0.0.0` or `::` is listening on every interface, and
    neither spelling is an address a browser can open. Loopback is the one that
    works on the machine the message is printed on.
    """
    reachable = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    if ":" in reachable and not reachable.startswith("["):
        reachable = f"[{reachable}]"
    return f"http://{reachable}:{port}{CONSOLE_PATH}"


def serve(
    world: World,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    max_concurrent_envs: int = DEFAULT_MAX_CONCURRENT_ENVS,
    concurrency: int | None = None,
    session_timeout: float | None = DEFAULT_SESSION_TIMEOUT,
    include_control_tools: bool = False,
    console: bool = True,
) -> None:
    """Serve one world until the process is stopped.

    `concurrency` is the process-wide gate on how many tool calls execute at
    once -- not how many sessions are admitted, which is `max_concurrent_envs`.
    `None` leaves it at the framework's default (`min(cpus, 16)`, which follows
    a container's CPU affinity) and `0` removes it. It is set before the app is
    built, so it is in force for the first call the server takes.

    `console` serves the web console at `/console` and prints its address just
    before the server starts. `False` leaves both out.

    `session_timeout` is seconds of idleness before OpenEnv reaps a session, or
    `None` for no reaper; the CLI's `--session-timeout 0` is spelled `None`
    here, because that is what OpenEnv wants and a `0` it would refuse.
    """
    set_concurrency(default_concurrency() if concurrency is None else concurrency)
    served = app(
        world,
        include_control_tools=include_control_tools,
        max_concurrent_envs=max_concurrent_envs,
        session_timeout=session_timeout,
        console=console,
    )
    if console:
        # `print` and not `logging`: uvicorn calls `configure_logging()` from
        # inside `uvicorn.run`, so until then the root logger has no handler
        # and sits at WARNING, and an `info` call here is dropped. The line
        # therefore lands above uvicorn's own startup output and before the
        # socket is bound, which is what "will be available" allows for.
        # `flush=True` because stdout is block-buffered when it is not a
        # terminal, and a server that then runs until it is killed would hold
        # the line in that buffer and never print it.
        print(f"Starting. Web console will be available at {console_url(host, port)}", flush=True)
    uvicorn.run(
        served,
        host=host,
        port=port,
        # One process, always. `workers` left unset is read from
        # `WEB_CONCURRENCY`, and `uvicorn.run` exits with STARTUP_FAILURE for a
        # `workers > 1` it cannot re-import -- an app object is not an import
        # string -- so this argument is what keeps that environment variable
        # from stopping the server. The app is an object rather than an import
        # string because a session's instance would not survive that import.
        workers=1,
        log_level=LOG_LEVEL,
    )
