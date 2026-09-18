"""The lints behind `seahaven check`, as a library.

Nothing in this package prints, reads `argv` or exits. A rule is a function from
a `Target` -- a world, the module it was imported from, and what `sys.modules`
held the moment it was imported -- to a list of `Finding`, and `run_all` is the
concatenation of the five rule modules in code order. `seahaven/cli/check.py` is
the only thing here that knows about a terminal.

The codes are stable and their gaps are deliberate: a retired rule's number is
never reused, so a fix written against `SH203` in a world's history always means
the same rule.

| Code | Severity | Rule |
|---|---|---|
| SH101 | error | a table that is not `STRICT` |
| SH102 | error | a table with no explicit primary key |
| SH103 | error | a wall-clock expression anywhere in the DDL |
| SH104 | error | DDL that does not execute |
| SH201 | warning | a wall-clock call in world code, outside `middleware/` |
| SH203 | warning | `random` or `uuid.uuid4()` in world code |
| SH205 | warning | a tool with an empty description |
| SH206 | warning | a prefixed world's description names a sibling tool the agent cannot call |
| SH207 | warning | one tool of a shared node contributed under two names |
| SH208 | error | `.instance(` in a module under `tools/` or `middleware/` |
| SH209 | error | `ctx.worlds` naming something that is not a registered child |
| SH301 | error | a module under `tools/` or `middleware/` that is never imported |
| SH401 | error | a fixture sidecar that does not validate |
| SH402 | error | a fixture's `file_sha256` does not match its state file |
| SH403 | error | a fixture's `schema_hash` does not match the world |
| SH404 | error | a fixture's `now` is not canonical |
| SH405 | error | a fixture's state file has `-wal` or `-shm` companions |
| SH406 | error | a composite sidecar's `nodes` disagrees with the world's composition |
| SH501 | error | the package does not export a `World` named `world` |
| SH502 | error | a `Worlds` subclass annotates a name no `add_world` registered |
| SH503 | warning | a registered child name no declared `Worlds` subclass annotates |
| SH504 | error | the world's composition does not seal |

The composition is sealed before any other rule runs, so a tree that does not
resolve is SH504 rather than a traceback out of the first rule that asks for it,
and every rule that needs no tree still runs. `Target.composition` is how a rule
asks for the tree and gets `None` when there is not one.
"""

from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Literal

from seahaven.composition import Composition
from seahaven.errors import WorldBug
from seahaven.world import World

__all__ = ["Finding", "Severity", "Target", "run_all"]

type Severity = Literal["error", "warning"]


@dataclass(frozen=True)
class Finding:
    """One thing `seahaven check` has to say, and what to do about it.

    `fix` is not optional and is not prose about the problem: every rule here
    exists because an authoring agent makes the mistake silently, and a finding
    that does not name the edit is one more thing to work out.
    """

    code: str
    severity: Severity
    path: Path
    message: str
    fix: str
    # A fixture's sidecar, a module that was never imported: some findings are
    # about a file rather than a place in one, and inventing line 1 for them
    # would send a reader to the wrong place with confidence.
    line: int | None = None

    def render(self, root: Path | None = None) -> str:
        """The line `seahaven check` prints, with the path relative to `root`."""
        where = str(_relative(self.path, root))
        if self.line is not None:
            where = f"{where}:{self.line}"
        return f"{self.code} {self.severity} {where}  {self.message}  fix: {self.fix}"

    @property
    def sort_key(self) -> tuple[str, str, int]:
        """Code, then path, then line: the order `check` prints findings in."""
        return (self.code, str(self.path), self.line or 0)


@dataclass(frozen=True)
class Target:
    """A world as the lints see it: the object, its package, and what it imported."""

    world: World
    package: ModuleType
    # `sys.modules` as it stood immediately after the world package was imported.
    # A snapshot rather than a live read because `coverage.py` walks the package
    # with `pkgutil.walk_packages`, which imports the subpackages it recurses
    # into: against a live `sys.modules` the walk would answer its own question.
    imported: frozenset[str]

    @property
    def package_dir(self) -> Path:
        """The directory the world's package lives in.

        A world is a package and never a single module (functional spec §2.1), so
        a module with no `__path__` is refused here in a sentence rather than
        reaching the first rule that reads a directory as an `AttributeError`.
        `seahaven check` says the same thing earlier and better, at discovery;
        this is the guard for anything that builds a `Target` itself.

        A package's `__path__` has one entry, and the first is taken rather than
        asserted: a namespace package spread over two directories is a world with
        other problems, and a lint is not the place to raise them.
        """
        path = getattr(self.package, "__path__", None)
        if not path:
            raise WorldBug(
                f"{self.package.__name__} is a module, not a package, and a world is a package: "
                f"move it to {self.package.__name__}/__init__.py, with its schema and its tools "
                f"beside it"
            )
        return Path(next(iter(path)))

    @property
    def composition(self) -> Composition | None:
        """The world's sealed tree, or `None` when it does not seal.

        A seal is lazy and its failures are whole-tree registration errors
        (architecture section 4.3), so every rule that reads the tree would
        otherwise have to decide for itself what to do about a `WorldBug`.
        `lint.world` is the one rule that wants the message -- it is SH504 -- and
        everything else wants the tree or nothing.

        Resolved rather than cached here: `World.composition()` already caches
        per registration epoch, so a second rule asking is an integer compare.
        """
        try:
            return self.world.composition()
        except WorldBug:
            return None


def run_all(target: Target) -> list[Finding]:
    """Every rule, in the order `check` prints them.

    `lint.world` runs first because it is what seals the composition: a tree that
    does not resolve is reported once, as SH504, and the rules below it then read
    `Target.composition` as `None` rather than raising a `WorldBug` apiece.
    """
    # Imported here rather than at module top: each rule module imports this one
    # for `Finding` and `Target`, and the package is the surface they hang off.
    from seahaven.lint import code, coverage, ddl, fixtures, world

    findings = (
        world.run(target)
        + ddl.run(target)
        + code.run(target)
        + coverage.run(target)
        + fixtures.run(target)
    )
    return sorted(findings, key=lambda finding: finding.sort_key)


def _relative(path: Path, root: Path | None) -> Path:
    """`path` under `root`, or `path` itself when it is somewhere else entirely.

    An installed world's package is outside the project directory a check was run
    from, and an absolute path is the honest answer there.
    """
    if root is None:
        return path
    try:
        return path.relative_to(root)
    except ValueError:
        return path
