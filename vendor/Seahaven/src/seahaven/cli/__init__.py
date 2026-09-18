"""`seahaven`: the command, its parser, and how it finds a world.

Five subcommands, one module each, and nothing in any of them that is not
argument parsing, world discovery or output. The work is done by code that
already exists -- `World.instance`, `Instance.freeze`, `seahaven.openenv.serve`,
`seahaven.lint` -- which is what keeps the CLI from becoming a second API beside
the Python one.

**Finding the world** is the convention of functional spec §2.2 and nothing more:
the nearest `pyproject.toml` walking up, its `[project] name` normalised to a
package name, and the attribute `world` on that package. `--world module:attr`
overrides it for a layout the convention does not fit. A world that has not been
installed still resolves, because the project root and its `src/` are put on
`sys.path` first; the docs recommend `uv sync` and an editable install rather
than relying on that.

**Failure** is one line on stderr and exit 1, never a traceback: every error a
user can provoke here says what to do about it, and a traceback out of
`import_module` says only that Python was involved. Exit 2 is argparse's, for a
usage error.

There is one deliberate exception, and it is not a user error: an exception out
of the generator `seahaven fixture --run` names is the author's own code failing,
and its traceback points at the line. `cli/fixture.py` says why; phase 7's plan
records it as a departure from the component spec, which asked for a message.
"""

import argparse
import re
import sys
import tomllib
import traceback
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from types import ModuleType

from seahaven.errors import SeahavenError
from seahaven.world import DDL_DOES_NOT_EXECUTE, World

__all__ = ["CliError", "Discovery", "build_parser", "discover", "find_world", "main"]

PROJECT_FILE = "pyproject.toml"
SOURCE_DIRNAME = "src"
WORLD_ATTRIBUTE = "world"

# `my-world` and `my.world` are both the package `my_world`: PEP 503-ish
# normalisation, which is what an installer would have done to the same name.
_NON_PACKAGE = re.compile(r"[-.]+")

_OVERRIDE = "or pass --world module:attr"


class CliError(Exception):
    """A failure with a fix in it, printed as one line and never as a traceback.

    `code` is set when `seahaven check` has to render the failure as a finding
    rather than as a user error: an import that never produced a `World` is
    SH501, and one that failed on the world's DDL is SH104. A `CliError` without
    a code -- no `pyproject.toml`, a malformed `--world` -- is the user's to fix
    before any lint can run at all.
    """

    def __init__(self, message: str, *, code: str | None = None, path: Path | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.path = path


@dataclass(frozen=True)
class Discovery:
    """A world and everything the tooling knows about where it came from."""

    world: World
    package: ModuleType
    # `sys.modules` immediately after the package was imported: what SH301 has to
    # compare against, and it cannot be recovered later.
    imported: frozenset[str]
    root: Path


def main(argv: list[str] | None = None) -> int:
    """The entry point `[project.scripts] seahaven` names."""
    args = build_parser().parse_args(argv)
    try:
        return int(args.handler(args))
    except (CliError, SeahavenError) as error:
        print(str(error), file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    """The whole command tree. Each subcommand module owns its own arguments.

    Every subcommand's parser carries its function as the default `handler`, so
    no subcommand may take an option spelled `--handler`: argparse would write
    the option's value over it. `--run` is why the name is not `run`.
    """
    # Imported here rather than at module top: every subcommand module imports
    # this one for `CliError` and `find_world`.
    from seahaven.cli import check, docs, fixture, new, serve

    parser = argparse.ArgumentParser(prog="seahaven", description="Build and run Seahaven worlds.")
    subcommands = parser.add_subparsers(dest="command", required=True, metavar="<command>")
    for module in (new, check, docs, fixture, serve):
        module.add_parser(subcommands)
    return parser


def add_world_option(parser: argparse.ArgumentParser) -> None:
    """`--world module:attr`, on every subcommand that needs a world."""
    parser.add_argument(
        "--world",
        metavar="module:attr",
        default=None,
        help=(
            "the world to act on, as an importable module and the attribute holding it; "
            "the default is the convention: the project's package, attribute 'world'"
        ),
    )


def find_world(explicit: str | None, start: Path | None = None) -> World:
    """The world `seahaven` and the pytest plugin act on."""
    return discover(explicit, start).world


def discover(explicit: str | None, start: Path | None = None) -> Discovery:
    """`find_world`, plus the module, the project root and the import snapshot."""
    start = (start or Path.cwd()).resolve()
    if explicit is not None:
        module_name, separator, attribute = explicit.partition(":")
        if not separator or not module_name or not attribute:
            raise CliError(
                f"--world takes module:attr, not {explicit!r}; for example --world myworld:world"
            )
        _make_importable(start, start / SOURCE_DIRNAME)
        return _import(module_name, attribute, root=start)
    root = _project_root(start)
    _make_importable(root, root / SOURCE_DIRNAME)
    return _import(_package_name(root), WORLD_ATTRIBUTE, root=root)


def _project_root(start: Path) -> Path:
    """The nearest directory at or above `start` holding a `pyproject.toml`."""
    for directory in (start, *start.parents):
        if (directory / PROJECT_FILE).is_file():
            return directory
    raise CliError(
        f"no {PROJECT_FILE} in {start} or any directory above it, so there is no world here; "
        f"run seahaven from inside a world's project, {_OVERRIDE}"
    )


def _package_name(root: Path) -> str:
    """The package a project's `[project] name` normalises to."""
    path = root / PROJECT_FILE
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise CliError(f"{path}: cannot be read as TOML: {error}") from error
    name = data.get("project", {}).get("name")
    if not isinstance(name, str) or not name:
        raise CliError(
            f"{path} has no [project] name, so there is no package to import; {_OVERRIDE}"
        )
    return package_name(name)


def package_name(name: str) -> str:
    """The package name a distribution name normalises to: `my-world` is `my_world`."""
    return _NON_PACKAGE.sub("_", name).lower()


def _import(module_name: str, attribute: str, *, root: Path) -> Discovery:
    """Import a module, take the world off it, and say what went wrong if not."""
    try:
        module = import_module(module_name)
    except SeahavenError as error:
        # A world that refused to be constructed. Its message is written for its
        # author and is the whole of what `check` should print: SH104 when the
        # DDL is what SQLite refused, SH501 for everything else a `World` raises
        # at import -- a duplicate tool name, a middleware of the wrong shape.
        code = "SH104" if DDL_DOES_NOT_EXECUTE in str(error) else "SH501"
        raise CliError(str(error), code=code, path=root) from error
    except Exception as error:
        raise CliError(
            f"cannot import {module_name!r}: {_last_traceback_line(error)}",
            code="SH501",
            path=root,
        ) from error
    imported = frozenset(sys.modules)
    world = getattr(module, attribute, None)
    if not isinstance(world, World):
        raise CliError(_no_world(module_name, attribute, world), code="SH501", path=root)
    package = _package_of(module)
    if package is None:
        # Functional spec §2.1: a world is a package, never a single module and
        # never a directory loaded by path. Said here, where the layout is what
        # the user chose, rather than later as whatever the first rule to read a
        # directory happens to raise.
        raise CliError(
            f"{module_name} is a module, not a package, and a world is a package: move it to "
            f"{module_name}/__init__.py, with world.py, schema/, tools/ and middleware/ beside it"
        )
    return Discovery(world=world, package=package, imported=imported, root=root)


def _package_of(module: ModuleType) -> ModuleType | None:
    """The package the lints walk: `module`, or the package it lives in.

    `--world mypkg.world:world` names the module the `World` is built in, which
    is the natural way to spell the override for a world in the standard layout.
    What the lints need is the package around it -- the directory holding
    `schema/`, `tools/` and `middleware/` -- so a module is climbed out of rather
    than refused. Only a world that is one top-level module has nowhere to climb
    to, and that is the layout §2.1 rules out.
    """
    while not getattr(module, "__path__", None):
        parent = module.__name__.rpartition(".")[0]
        found = sys.modules.get(parent) if parent else None
        if found is None:
            return None
        module = found
    return module


def _no_world(module_name: str, attribute: str, found: object) -> str:
    what = "has no" if found is None else f"has a {type(found).__name__} and not a World for its"
    return (
        f"{module_name} {what} {attribute!r}; "
        f"{module_name}/__init__.py must export {attribute} = seahaven.World(...), {_OVERRIDE}"
    )


def _last_traceback_line(error: BaseException) -> str:
    """The exception as Python would print it, without the frames above it."""
    return traceback.format_exception_only(error)[-1].strip()


def _make_importable(*directories: Path) -> None:
    """Put a world's project on `sys.path`, so an uninstalled one still imports."""
    for directory in directories:
        entry = str(directory)
        if directory.is_dir() and entry not in sys.path:
            sys.path.insert(0, entry)
