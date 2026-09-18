"""The composition rules: SH504, SH502, SH503 and SH207.

Everything here is a question about the *tree* rather than about a file, and
SH504 is why this module runs first. A composition is sealed lazily, at the first
use of the tree, and every whole-tree failure -- a list naming a tool nobody
contributes, two routes producing one name, a startup keyword bound twice, more
stores than SQLite can attach -- raises out of that first use as a `WorldBug`
(architecture section 4.3). `seahaven check` seals as its first act, which turns
each of those into a line with the offending `add_world` on it, in development,
which is where the functional spec's "registration error" intent actually lands.

`Target.composition` is the rest of that arrangement: every other rule that reads
the tree gets `None` here rather than a traceback, and reports nothing rather
than reporting a consequence of a failure SH504 has already named.

SH502 and SH503 are the `Worlds` subclass a host may declare (architecture
section 8.3). The class is never instantiated and the runtime satisfies any
attribute name, so this is the only thing that ever binds it to the
registrations; declaring it stays optional, which is why SH503 says nothing about
a world that declares no class at all.
"""

import annotationlib
import inspect
import sys
from collections.abc import Iterable, Iterator
from pathlib import Path

from seahaven.errors import WorldBug
from seahaven.handles import Worlds
from seahaven.lint import Finding, Target

__all__ = ["declaration_path", "run"]

# Where the convention puts `add_world`, and therefore where a finding about the
# shape of the tree sends a reader. A world that keeps its declaration somewhere
# else is pointed at its package instead, which is still inside the right world.
WORLD_MODULE = "world.py"

_SEAL_FIX = "correct the add_world the message names; nothing can use this world until it seals"


def run(target: Target) -> list[Finding]:
    """SH504, SH502, SH503 and SH207 over the world's composition and its declaration."""
    return (
        _seal_findings(target)
        + _worlds_class_findings(target)
        + _shared_contribution_findings(target)
    )


def _seal_findings(target: Target) -> list[Finding]:
    """SH504: the composition does not resolve, in the seal's own words."""
    try:
        target.world.composition()
    except WorldBug as error:
        return [
            Finding(
                code="SH504",
                severity="error",
                path=declaration_path(target),
                message=str(error),
                fix=_SEAL_FIX,
            )
        ]
    return []


def _worlds_class_findings(target: Target) -> list[Finding]:
    """SH502 and SH503: the declared child names against the registered ones.

    SH502 is per annotation and SH503 is *not* per class: a name any declared
    class annotates counts as annotated, and the one finding is anchored on the
    first class the package declares. Section 14 says "when the world declares a
    `Worlds` subclass", singular, and a world that splits its children over two
    classes has declared all of them between the two; reporting each class for
    the names the other holds would make the split itself the finding.
    """
    declared = list(_declared_classes(target))
    if not declared:
        return []
    registered = {added.name for added in target.world.added_worlds}
    findings: list[Finding] = []
    annotated: set[str] = set()
    for declaration in declared:
        for name in declaration.names:
            annotated.add(name)
            if name in registered:
                continue
            findings.append(
                Finding(
                    code="SH502",
                    severity="error",
                    path=declaration.path,
                    line=declaration.line_of(name),
                    message=(
                        f"{declaration.cls.__name__} annotates {name!r}, which world "
                        f"{target.world.name!r} does not add"
                    ),
                    fix=_sh502_fix(registered),
                )
            )
    first = declared[0]
    for name in sorted(registered - annotated):
        findings.append(
            Finding(
                code="SH503",
                severity="warning",
                path=first.path,
                line=first.line,
                message=(
                    f"child {name!r} is not annotated on {first.cls.__name__}, so "
                    f"ctx.worlds.{name} is not checked"
                ),
                fix=f"add `{name}: seahaven.WorldHandle` to {first.cls.__name__}",
            )
        )
    return findings


def _shared_contribution_findings(target: Target) -> list[Finding]:
    """SH207: one tool of one node published under more than one name.

    Two routes to a shared node each apply their own prefix and lists, which is
    two declarations and not a collision (functional spec section 2.3): the
    agent sees two names, both reaching the same store, and neither its tool list
    nor its results say so. Deliberate sometimes -- a client really does surface
    one account twice -- so a warning.
    """
    composition = target.composition
    if composition is None:
        return []
    names: dict[tuple[str, str], list[str]] = {}
    for entry in composition.tools.values():
        names.setdefault((entry.node.path, entry.tool.name), []).append(entry.name)
    return [
        Finding(
            code="SH207",
            severity="warning",
            path=declaration_path(target),
            message=(
                f"tool {tool!r} of node {path!r} is contributed under {len(published)} names "
                f"({_names(published)}), which all reach the one store"
            ),
            fix=(
                "give every route but one tool_allow_list=[] or a tool_block_list naming it, "
                "or accept that the agent sees one account twice"
            ),
        )
        for (path, tool), published in sorted(names.items())
        if len(published) > 1
    ]


class _Declaration:
    """One `Worlds` subclass a world's package declares, and where it is written."""

    def __init__(self, cls: type[Worlds], fallback: Path) -> None:
        self.cls = cls
        self._lines, self.line = _source_lines(cls)
        self.path = _source_path(cls, fallback)
        # `Format.STRING` never evaluates the annotation, so a class annotated
        # with a name this process cannot resolve -- a `TYPE_CHECKING` import,
        # most often -- is still read for the names it declares, which is the
        # whole of what this rule asks about.
        #
        # A name beginning with an underscore is not a declaration: no child can
        # be spelled that way (`composition.NAME`), and `Worlds.__getattr__` lets
        # `_` names fall to ordinary attribute lookup precisely so a subclass may
        # carry `__slots__` or a private field of its own. This is
        # `code._child_named`'s exclusion for SH209, for the same reason: the two
        # rules have to agree about what a child name can look like.
        self.names = tuple(
            name
            for name in annotationlib.get_annotations(cls, format=annotationlib.Format.STRING)
            if not name.startswith("_")
        )

    def line_of(self, name: str) -> int | None:
        """The line the annotation is written on, or the class's own."""
        if self._lines is None or self.line is None:
            return None
        for offset, text in enumerate(self._lines):
            if text.strip().startswith(f"{name}:"):
                return self.line + offset
        return self.line


def _declared_classes(target: Target) -> Iterator[_Declaration]:
    """Every `Worlds` subclass written in a module of the world's package.

    Found through `Target.imported` rather than by reading the source, because the
    binding this rule makes is between a *class* and the registrations, and a
    subclass of a subclass is still one. A class in a module nothing imported is
    invisible here and that costs nothing: the point of declaring one is to
    annotate a `Ctx[...]` with it, which imports the module it is written in.
    """
    package = target.package.__name__
    seen: set[type[Worlds]] = set()
    for module_name in sorted(target.imported):
        if module_name != package and not module_name.startswith(f"{package}."):
            continue
        module = sys.modules.get(module_name)
        if module is None:
            continue
        for value in list(vars(module).values()):
            if not isinstance(value, type) or not issubclass(value, Worlds):
                continue
            # Declared here, not imported from somewhere else: a class a second
            # module imports is one declaration, reported once, where it is
            # written.
            if value is Worlds or value.__module__ != module_name or value in seen:
                continue
            seen.add(value)
            yield _Declaration(value, target.package_dir)


def _source_lines(cls: type) -> tuple[list[str] | None, int | None]:
    """A class's source and the line it starts on, as far as Python can say."""
    try:
        lines, start = inspect.getsourcelines(cls)
    # Unparenthesized because `ruff format` -- a gate this project runs before
    # every commit -- takes the parentheses off again where nothing is bound with
    # `as` (PEP 758). `openenv/env.py` carries the long version of this; the five
    # other multi-type `except` clauses in `src/`, `code._source_of`'s identical
    # pair among them, are all written this way for the same reason.
    except OSError, TypeError:
        # A class defined in a REPL, or built by a call rather than a statement.
        return None, None
    return lines, start


def _source_path(cls: type, fallback: Path) -> Path:
    """The file a class is written in, or the world's package when Python cannot say."""
    found = inspect.getsourcefile(cls)
    return Path(found) if found is not None else fallback


def declaration_path(target: Target) -> Path:
    """Where a world declares its tree: `world.py`, or the package it is missing from.

    Public because SH206 anchors on it too: that rule's finding is about text
    written in an *added* world and its fix is an `add_world` written here.
    """
    module = target.package_dir / WORLD_MODULE
    return module if module.is_file() else target.package_dir


def _sh502_fix(registered: set[str]) -> str:
    """What to do about an attribute no `add_world` backs."""
    if not registered:
        return "this world adds no worlds, so drop the attribute or add the world it names"
    return f"annotate one of the names it does add ({_names(registered)}), or drop the attribute"


def _names(names: Iterable[str]) -> str:
    return ", ".join(sorted(names))
