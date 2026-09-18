"""`seahaven check`: every framework rule as a lint with a code and a named fix.

The whole command is discovery, `lint.run_all`, and printing. What makes it worth
a module of its own is the one case discovery cannot hand to the lints: a world
that could not be imported has no `World` to lint, and the failure is itself a
finding -- SH501 for a package with no world in it, SH104 for one whose DDL
SQLite refused. Reported as a line like any other, never as a traceback, because
an authoring agent reads the first and re-runs on the second.

Paths are printed relative to the *project*, not to wherever the shell happened
to be: `seahaven check` from `src/mypkg/middleware/` finds the same world as
`seahaven check` from the project root, and the two should not describe the same
file differently.
"""

import argparse
from dataclasses import dataclass
from pathlib import Path

from seahaven import lint
from seahaven.cli import CliError, add_world_option, discover
from seahaven.lint import Finding
from seahaven.lint.ddl import ddl_execution_finding

__all__ = ["Report", "add_parser", "collect", "run"]


@dataclass(frozen=True)
class Report:
    """What a check found, and the project the paths in it are relative to."""

    root: Path
    findings: list[Finding]

    @property
    def failed(self) -> bool:
        """Whether anything found is an error. A warning alone does not stop a commit."""
        return any(finding.severity == "error" for finding in self.findings)


def add_parser(subcommands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subcommands.add_parser(
        "check",
        help="run every lint over this world",
        description="Run every lint over this world.",
    )
    add_world_option(parser)
    parser.set_defaults(handler=run)


def run(args: argparse.Namespace) -> int:
    """Print every finding, sorted; 1 if any of them is an error."""
    report = collect(args.world, Path.cwd())
    for finding in report.findings:
        print(finding.render(report.root))
    return 1 if report.failed else 0


def collect(explicit: str | None, start: Path) -> Report:
    """Every finding for the world `start` resolves to, and that world's root.

    A `CliError` carrying a code is a finding about the world; one without a code
    -- no project, a malformed `--world`, a world that is not a package -- is the
    user's error and propagates to `main`, because there is nothing to lint until
    it is fixed.
    """
    try:
        found = discover(explicit, start)
    except CliError as error:
        if error.code is None:
            raise
        return Report(root=error.path or start, findings=[_finding_for(error, start)])
    return Report(
        root=found.root,
        findings=lint.run_all(
            lint.Target(world=found.world, package=found.package, imported=found.imported)
        ),
    )


def _finding_for(error: CliError, start: Path) -> Finding:
    path = error.path or start
    if error.code == "SH104":
        return ddl_execution_finding(error.message, path)
    return Finding(
        code="SH501",
        severity="error",
        path=path,
        message=error.message,
        # The message already names the export and the override, because it is
        # also what `find_world` raises outside `check`. Repeating it here would
        # print one sentence twice on one line.
        fix="give the package a world, or point at the one it has",
    )
