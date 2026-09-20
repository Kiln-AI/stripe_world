"""`seahaven serve`: one world behind an OpenEnv server.

Argument parsing and nothing else. `seahaven/openenv/serve.py` is the three lines
that set the gate, build the app and run uvicorn, and it lives there so a harness
that wants a server inside its own process can call it without going through
`argv`.

The serve extra is checked before the world is: `openenv`'s wheel is large enough
to be left out of a default install deliberately, and "install the extra" is the
only thing to do about its absence whatever else is wrong.
"""

import argparse

from seahaven.cli import CliError, add_world_option, find_world

__all__ = ["MISSING_EXTRA", "add_parser", "run"]

MISSING_EXTRA = 'seahaven serve needs the serve extra: pip install "seahaven[serve]"'


def add_parser(subcommands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subcommands.add_parser(
        "serve",
        help="run this world's OpenEnv server",
        description="Run this world's OpenEnv server: one world, many sessions.",
    )
    add_world_option(parser)
    parser.add_argument("--host", default=None, help="the address to bind (default 0.0.0.0)")
    parser.add_argument("--port", type=int, default=None, help="the port to bind (default 8000)")
    # Underscores, not hyphens: this is OpenEnv's own option name, and a second
    # spelling of it here would be one more thing to translate.
    parser.add_argument(
        "--max_concurrent_envs",
        type=int,
        default=None,
        help="how many sessions may be open at once (default 500)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=None,
        help="how many tool calls run at once; 0 for no gate (default min(cpus, 16))",
    )
    parser.add_argument(
        "--session-timeout",
        type=float,
        default=None,
        help="seconds of idleness before a session is reaped; 0 disables the reaper (default 3600)",
    )
    parser.add_argument(
        "--include-control-tools",
        action="store_true",
        help="make the control tool callable over the wire; it is never listed",
    )
    parser.add_argument(
        "--no-console",
        action="store_true",
        help="do not serve the web console at /console",
    )
    parser.set_defaults(handler=run)


def run(args: argparse.Namespace) -> int:
    """Resolve the world and hand it to `seahaven.openenv.serve`.

    Every option defaults to `None` in the parser and is resolved here against
    the constants `seahaven.openenv` declares, rather than being defaulted in
    `add_parser`: those constants cannot be imported until the extra is known to
    be installed, and one set of defaults -- the framework's -- is the point.
    """
    try:
        from seahaven.openenv import DEFAULT_MAX_CONCURRENT_ENVS, DEFAULT_SESSION_TIMEOUT
        from seahaven.openenv import serve as serving
    except ImportError as error:
        raise CliError(MISSING_EXTRA) from error
    world = find_world(args.world)
    serving.serve(
        world,
        host=serving.DEFAULT_HOST if args.host is None else args.host,
        port=serving.DEFAULT_PORT if args.port is None else args.port,
        max_concurrent_envs=(
            DEFAULT_MAX_CONCURRENT_ENVS
            if args.max_concurrent_envs is None
            else args.max_concurrent_envs
        ),
        # `None` is "leave it to the framework's default"; `0` is "no gate", and
        # is a value `serve` has to see.
        concurrency=args.concurrency,
        # OpenEnv wants `None` for "no reaper" and refuses a `0`, so the CLI's
        # `0` is translated here rather than anywhere further in.
        session_timeout=_session_timeout(args.session_timeout, DEFAULT_SESSION_TIMEOUT),
        include_control_tools=args.include_control_tools,
        console=not args.no_console,
    )
    return 0


def _session_timeout(given: float | None, default: float) -> float | None:
    """The reaper's idleness budget: the default, the value, or off."""
    if given is None:
        return default
    return None if given == 0 else given
