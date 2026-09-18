"""SH301: a module under `tools/` or `middleware/` that importing the package did not.

Registration happens at import time, so a tool module nobody imports registers
nothing and the world silently has one tool fewer than its author believes. The
symptom -- `unknown_tool` from an eval, weeks later -- says nothing about the
missing `from mypkg.tools import invoices` line, which is why this is an error
and not a warning.

The comparison is against `Target.imported`, a snapshot of `sys.modules` taken
the moment the package finished importing, and not against `sys.modules` now:
`pkgutil.walk_packages` imports the subpackages it recurses into, so a live read
would let the walk answer its own question.

That same import is why the walk is given an `onerror`. Without one,
`walk_packages` re-raises anything a subpackage's `__init__` raises that is not
an `ImportError`, and `seahaven check` -- whose whole promise is a line with a
fix on it rather than a traceback -- would die with one, on a world whose only
problem is the module this rule was about to report. A subpackage that cannot be
imported is yielded before it is imported, so swallowing the failure loses
nothing: the module is still reported as unimported, which it is.
"""

import pkgutil
from pathlib import Path

from seahaven.lint import Finding, Target

__all__ = ["REGISTERING_DIRECTORIES", "run"]

# The two directories whose modules exist to register something. A world may put
# helpers anywhere; these are the two the layout of functional spec §2.1 gives a
# meaning to, and the rule is deliberately not "every module in the package".
REGISTERING_DIRECTORIES = ("tools", "middleware")


def run(target: Target) -> list[Finding]:
    """SH301 for every module under the registering directories."""
    package = target.package.__name__
    findings: list[Finding] = []
    for directory in REGISTERING_DIRECTORIES:
        root = target.package_dir / directory
        if not root.is_dir():
            continue
        for name, path in _modules(root, f"{package}.{directory}"):
            if name in target.imported:
                continue
            findings.append(
                Finding(
                    code="SH301",
                    severity="error",
                    path=path,
                    message=f"module {name!r} is never imported, so it registers nothing",
                    fix=f"import it from {package}/__init__.py, or from {package}.{directory}",
                )
            )
    return findings


def _modules(root: Path, package: str) -> list[tuple[str, Path]]:
    """The subpackage itself and every module under it, as `(name, file)`.

    The subpackage is in the list because `tools/__init__.py` is where the
    imports of its siblings live: a `tools/` the world's `__init__` never imports
    is the whole directory registering nothing, and reporting only its children
    would bury that in a list.
    """
    # A namespace package has no `__init__.py` and imports perfectly well, so the
    # directory itself is the honest place to point when the file is not there.
    marker = root / "__init__.py"
    found = [(package, marker if marker.is_file() else root)]
    for info in pkgutil.walk_packages([str(root)], prefix=f"{package}.", onerror=_ignore):
        found.append((info.name, _path_of(root, package, info.name)))
    return found


def _ignore(name: str) -> None:
    """What to do about a subpackage that raises while `walk_packages` imports it.

    Nothing. It is a lint's business to report modules, not to run them, and the
    alternative -- `onerror=None` -- is a traceback out of `seahaven check`.
    """


def _path_of(root: Path, package: str, name: str) -> Path:
    """Where a walked module's file is, derived from its dotted name."""
    parts = name[len(package) + 1 :].split(".")
    module = root.joinpath(*parts).with_suffix(".py")
    if module.is_file():
        return module
    marker = root.joinpath(*parts, "__init__.py")
    return marker if marker.is_file() else root.joinpath(*parts)
