"""`seahaven new <name>`: the scaffold, and nothing more than the scaffold.

Renders `templates/` and stops. Nothing is imported, no world is built and no
fixture is made, so `new` cannot fail halfway through something: either the
directory is there and complete, or it was never created.

The templates are ordinary files with a `.tmpl` suffix, substituted with
`string.Template` -- `$name`, `$package`, `$seahaven_requirement` -- and written
out without it. The suffix is what keeps a directory full of `$package` out of
the repository's own ruff, ty and pytest runs: a `.py` holding
`from $package.world import world` is not Python, and every tool in the tree
would have an opinion about it. The package directory is spelled `PACKAGE` in
the template tree for the same reason, and is renamed on the way out.

The result passes `seahaven check` with no findings and `pytest` with no fixture
on disk, which is the point: an author's first run of both succeeds, so the first
failure they see is one they caused.
"""

import argparse
import keyword
import re
import shutil
from collections.abc import Iterator
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from string import Template

import seahaven
from seahaven.cli import CliError, package_name
from seahaven.names import NAME_RULE, why_not_a_name

__all__ = ["HUB_FILES", "TEMPLATE_SUFFIX", "add_parser", "render", "run"]

TEMPLATE_SUFFIX = ".tmpl"
# The directory the world's package is spelled as inside `templates/`.
PACKAGE_PLACEHOLDER = "PACKAGE"

BASE_TEMPLATES = "base"
HUB_TEMPLATES = "hub"

# What `--hub` adds, and the whole of what it adds: the files `openenv push`
# validates a pushed directory for (`components/openenv.md` §6). A world that
# does not publish to a hub carries none of them.
HUB_FILES = ("Dockerfile", "__init__.py", "client.py", "models.py", "openenv.yaml")

# `seahaven~=X.Y`: a world is built against the framework's authoring API, which
# moves with the minor version, and a world that pinned the patch would need a
# release for every one of ours.
_RELEASE = re.compile(r"^(\d+)\.(\d+)")


def add_parser(subcommands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subcommands.add_parser(
        "new", help="scaffold a new world", description="Scaffold a new Seahaven world."
    )
    parser.add_argument("name", help="the world's name; the package is this, normalised")
    parser.add_argument(
        "--dir",
        default=".",
        metavar="<path>",
        help="the directory to create the world in (default: here)",
    )
    parser.add_argument(
        "--hub",
        action="store_true",
        help="also write the files `openenv push` requires, for a world that publishes to a hub",
    )
    parser.set_defaults(handler=run)


def run(args: argparse.Namespace) -> int:
    """Render the scaffold and print what to do next."""
    target = Path(args.dir) / args.name
    render(args.name, target, hub=args.hub)
    print(f"created {target}")
    print("next:")
    print(f"  cd {target}")
    print("  uv sync")
    print("  uv run pytest")
    print("  uv run seahaven check")
    return 0


def render(name: str, target: Path, *, hub: bool = False) -> Path:
    """Write a world named `name` into `target`, and return it.

    Refuses an existing directory rather than merging into one: a scaffold is a
    starting point, and writing half of one over a world someone has been working
    on is not recoverable from.
    """
    package = package_name(name)
    # `isalpha` as well as `isidentifier`, because `-` normalises to `_`, which
    # is a legal identifier and a terrible name for a world: the message below
    # promises a letter to start with, and a rule that does not is a lie.
    if not package.isidentifier() or keyword.iskeyword(package) or not package[0].isalpha():
        raise CliError(
            f"{name!r} does not make a package name: {package!r} is not a Python identifier; "
            f"use letters, digits and underscores, starting with a letter"
        )
    # The name reaches the rendered `world.py` verbatim, and `World(name=...)`
    # holds it to `names.why_not_a_name`. Refused here rather than there: a
    # scaffold that cannot be imported is met at the author's first `pytest`, in
    # a directory they then have to delete.
    reason = why_not_a_name(name)
    if reason is not None:
        raise CliError(f"{name!r} is not a world name: {reason}. A world's name is {NAME_RULE}.")
    if target.exists():
        raise CliError(f"{target} already exists; seahaven new writes a new directory")
    substitutions = {
        "name": name,
        "package": package,
        "seahaven_requirement": seahaven_requirement(),
    }
    try:
        for group in (BASE_TEMPLATES, *((HUB_TEMPLATES,) if hub else ())):
            _render_group(group, target, package, substitutions)
    except BaseException:
        # Either a whole world or nothing: a directory holding three of its
        # seventeen files is worse than no directory at all.
        shutil.rmtree(target, ignore_errors=True)
        raise
    return target


def seahaven_requirement() -> str:
    """The dependency a scaffolded world pins: `seahaven~=<major>.<minor>`."""
    release = _RELEASE.match(seahaven.__version__)
    if release is None:  # pragma: no cover - a version string setuptools cannot produce
        return "seahaven"
    return f"seahaven~={release.group(1)}.{release.group(2)}"


def _render_group(group: str, target: Path, package: str, substitutions: dict[str, str]) -> None:
    root = resources.files(__package__) / "templates" / group
    for source, relative in _templates(root, Path()):
        destination = target / _destination(relative, package)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            Template(source.read_text(encoding="utf-8")).substitute(substitutions),
            encoding="utf-8",
        )


def _templates(root: Traversable, prefix: Path) -> Iterator[tuple[Traversable, Path]]:
    """Every template under `root`, with its path relative to the group."""
    for entry in sorted(root.iterdir(), key=lambda entry: entry.name):
        here = prefix / entry.name
        if entry.is_dir():
            yield from _templates(entry, here)
        elif entry.name.endswith(TEMPLATE_SUFFIX):
            yield entry, here


def _destination(relative: Path, package: str) -> Path:
    """Where a template lands: the suffix dropped, `PACKAGE` renamed."""
    parts = [package if part == PACKAGE_PLACEHOLDER else part for part in relative.parts]
    parts[-1] = parts[-1][: -len(TEMPLATE_SUFFIX)]
    return Path(*parts)
