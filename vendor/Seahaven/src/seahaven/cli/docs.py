"""`seahaven docs`: where the bundled documentation is.

The whole output is the path, so the command composes:
`cat "$(seahaven docs)/index.md"`. The docs ship inside the installed package, so
what this prints always matches the version that is installed -- which is the
point of bundling them, and the reason a scaffolded world's `AGENTS.md` tells an
authoring agent to run this rather than to search the web for a framework that
is not in its training data.
"""

import argparse
import importlib.resources

__all__ = ["DOCS_DIRNAME", "add_parser", "docs_path", "run"]

DOCS_DIRNAME = "docs"


def add_parser(subcommands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subcommands.add_parser(
        "docs",
        help="print the directory holding the bundled docs",
        description="Print the directory holding the bundled docs for the installed version.",
    )
    parser.set_defaults(handler=run)


def run(args: argparse.Namespace) -> int:
    print(docs_path())
    return 0


def docs_path() -> str:
    """The bundled docs directory of the installed `seahaven`."""
    return str(importlib.resources.files("seahaven") / DOCS_DIRNAME)
