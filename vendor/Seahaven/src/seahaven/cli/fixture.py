"""`seahaven fixture list | freeze | fork`: the three things a fixture author does.

Freezing is create an instance, fill it, freeze it. The filling is the author's
own code -- a module that uses `inst.bulk()` and the world's tools -- and
`--run module:function` is how the CLI reaches it. The CLI adds nothing to that
function and knows nothing about what it does; it makes the instance, hands it
over, and publishes the result. Every fixture in a repository is therefore
committed with the script that generates it, which is what makes a binary
artifact something a later author can change.

`freeze` starts from a blank instance (`--now`, or the wall clock, which is one
of the two places a world reads it); `fork` starts from a fixture and records it
as the new one's parent. Both destroy the instance on the way out, and
`Instance.freeze` publishes by rename, so a generator that raises leaves no
fixture directory and no half-written one either.

An exception out of the generator is deliberately *not* turned into a one-line
CLI error. It is a bug in the author's own code, and the traceback names the line
-- which is what `python generate.py` would have given them, and is worth more
than a sentence saying that something went wrong somewhere.
"""

import argparse
from collections.abc import Callable
from importlib import import_module

from seahaven.cli import CliError, add_world_option, find_world
from seahaven.fixtures import SIDECAR_NAME, Fixture
from seahaven.instances import Instance

__all__ = ["add_parser", "run_fork", "run_freeze", "run_list"]

type Generator = Callable[[Instance], None]


def add_parser(subcommands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subcommands.add_parser(
        "fixture",
        help="list, freeze and fork this world's fixtures",
        description="List, freeze and fork this world's fixtures.",
    )
    actions = parser.add_subparsers(dest="action", required=True, metavar="<action>")

    listing = actions.add_parser("list", help="every fixture: id, parent, now, description")
    add_world_option(listing)
    listing.set_defaults(handler=run_list)

    freeze = actions.add_parser("freeze", help="fill a blank instance and freeze it")
    freeze.add_argument("id", help="the id of the fixture to mint")
    _add_generator_options(freeze)
    freeze.add_argument(
        "--now",
        default=None,
        metavar="<ts>",
        help="the instant to freeze the clock at; the default is the wall clock at creation",
    )
    freeze.set_defaults(handler=run_freeze)

    fork = actions.add_parser("fork", help="fill an instance of a fixture and freeze the result")
    fork.add_argument("parent", help="the fixture to start from")
    fork.add_argument("id", help="the id of the fixture to mint")
    _add_generator_options(fork)
    fork.set_defaults(handler=run_fork)


def _add_generator_options(parser: argparse.ArgumentParser) -> None:
    add_world_option(parser)
    parser.add_argument(
        "--run",
        required=True,
        metavar="module:function",
        help="the generator: a function taking the live instance and filling it",
    )
    parser.add_argument(
        "--description",
        required=True,
        metavar="<text>",
        help="what the fixture holds and what scenarios it supports, for eval authors",
    )


def run_list(args: argparse.Namespace) -> int:
    """One line per fixture: id, parent, `now`, description."""
    world = find_world(args.world)
    for fixture in world.fixtures():
        print(f"{fixture.id}\t{fixture.parent_id or '-'}\t{fixture.now}\t{fixture.description}")
    return 0


def run_freeze(args: argparse.Namespace) -> int:
    """A blank instance, filled by the generator, frozen under `id`."""
    world = find_world(args.world)
    generator = _generator(args.run)
    with world.instance(None, now=args.now) as instance:
        generator(instance)
        fixture = instance.freeze(args.id, args.description)
    _print_sidecar(fixture)
    return 0


def run_fork(args: argparse.Namespace) -> int:
    """An instance of `parent`, filled by the generator, frozen under `id`."""
    world = find_world(args.world)
    generator = _generator(args.run)
    with world.instance(args.parent) as instance:
        generator(instance)
        fixture = instance.freeze(args.id, args.description)
    _print_sidecar(fixture)
    return 0


def _generator(spec: str) -> Generator:
    """The `module:function` the author wrote, imported and checked."""
    module_name, separator, function_name = spec.partition(":")
    if not separator or not module_name or not function_name:
        raise CliError(
            f"--run takes module:function, not {spec!r}; "
            f"for example --run fixtures_src.generate:small_startup"
        )
    try:
        module = import_module(module_name)
    except Exception as error:
        raise CliError(f"cannot import {module_name!r}: {error}") from error
    generator = getattr(module, function_name, None)
    if not callable(generator):
        what = "has no" if generator is None else f"has a {type(generator).__name__} and not a"
        raise CliError(
            f"{module_name} {what} {function_name!r}; a generator is a function taking the "
            f"instance to fill"
        )
    return generator


def _print_sidecar(fixture: Fixture) -> None:
    """The sidecar as it was written, which is the record of what was minted."""
    print(f"{fixture.dir}/{SIDECAR_NAME}")
    print((fixture.dir / SIDECAR_NAME).read_text(encoding="utf-8"), end="")
