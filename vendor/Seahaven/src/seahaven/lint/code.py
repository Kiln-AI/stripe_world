"""The world-code rules: SH201, SH203, SH205, SH206, SH208 and SH209.

Three warnings and two errors about code, plus one warning about the text an
agent reads. A wall-clock read and a draw from `random` are nearly always a
mistake -- fixture-relative data dated from the machine's clock, a run that will
not replay -- but sometimes deliberate, and a rule an author has to argue with is
a rule an author turns off. An empty tool description is the same shape of
problem: the description is what an agent reads to decide whether to call the
tool, and a world under development has tools that do not have one yet.

The two call rules work on the dotted name of the expression being called, with
its root expanded through the module's own imports, so `datetime.now()`,
`datetime.datetime.now()`, `from datetime import datetime as dt; dt.now()` and
`from time import time; time()` are one rule and not four. It is also what keeps
the endorsed spellings clean: the root of `ctx.clock.now()` and of
`ctx.ids.random.random()` is `ctx`, which no import binds.

The two composition rules are errors because neither is ever deliberate. An
instance made inside a call is a `WorldBug` the moment the line runs, and a child
name that is not registered is a `WorldBug` the moment it is reached -- both in
an eval, weeks after the module was written. SH206 is the third: a prefix does
not rewrite description text (functional spec section 3.3), so an added world
whose descriptions name their siblings ships an agent surface that names tools
the agent cannot call. The framework never edits the text; the host drops the
prefix or accepts the infidelity.
"""

import ast
import inspect
import re
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import NamedTuple

from seahaven.composition import Contributed
from seahaven.lint import Finding, Target
from seahaven.lint.coverage import REGISTERING_DIRECTORIES
from seahaven.lint.world import declaration_path
from seahaven.tool import Tool

__all__ = ["run"]

# The directory whose modules are exempt from SH201: the error handler and its
# neighbours are the layer a world logs and times a call in, and that is a real
# wall clock doing a real job.
_MIDDLEWARE = "middleware"

# Every spelling of a wall-clock read, as the whole resolved dotted name. Whole
# and not a suffix: a suffix match reports `self.time.time()` and anything else
# whose last two segments happen to line up, which is the very thing resolving
# the root is supposed to prevent. Both the module-qualified form (`import
# datetime`) and the imported-name form (`from datetime import datetime`, which
# `_resolve` rewrites to `datetime.datetime`) are listed, because the alias map
# turns one into the other and either can arrive here.
_WALL_CLOCK = frozenset(
    {
        "datetime.now",
        "datetime.utcnow",
        "datetime.datetime.now",
        "datetime.datetime.utcnow",
        "date.today",
        "datetime.date.today",
        "time.time",
        "time.monotonic",
        "time.perf_counter",
    }
)

# `uuid.uuid4()` and `uuid.uuid1()` read the OS entropy pool, so a run that uses
# either does not replay. `import uuid` on its own is not a finding: `uuid.UUID`
# is how `ctx.ids.uuid()`'s own output is parsed.
_UUID = frozenset({"uuid.uuid4", "uuid.uuid1"})

# The attribute whose call makes an instance. Matched as a name and not as a
# resolved dotted expression, because `world.instance(...)`, `payments.world
# .instance(...)` and `self._world.instance(...)` are one mistake and the
# receiver is whatever the module happens to have called its `World`.
_INSTANCE = "instance"

# The one expression this package reads child names out of. Exactly this and not
# any `.worlds`, because `ctx.worlds.<child>.worlds.<grandchild>` is the
# sanctioned way to a grandchild and those names belong to the child's
# registrations rather than to this world's.
_CTX_WORLDS = "ctx.worlds"

_CLOCK_FIX = "take the instance's time from ctx.clock.iso() or ctx.clock.now()"
_IDS_FIX = "draw from ctx.ids: ctx.ids.uuid() for an identifier, ctx.ids.random for anything else"
_INSTANCE_FIX = (
    "reach an added world with ctx.worlds.<name>.call(...); world.instance(...) belongs in an "
    "eval or a test, never in a call"
)
_STALE_FIX = (
    "drop the tool_prefix on that add_world, or accept the mention: check never rewrites a "
    "description"
)


def run(target: Target) -> list[Finding]:
    """SH201, SH203, SH205, SH206, SH208 and SH209 over the world's package."""
    findings: list[Finding] = []
    package_dir = target.package_dir
    children = {added.name for added in target.world.added_worlds}
    for path in sorted(package_dir.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except OSError, UnicodeDecodeError, SyntaxError:
            # A module that does not parse did not import either, and the import
            # failure is already SH501; two reports of one broken file is noise.
            continue
        findings += _module_findings(
            path,
            tree,
            in_middleware=_inside(path, package_dir, _MIDDLEWARE),
            registers=any(
                _inside(path, package_dir, directory) for directory in REGISTERING_DIRECTORIES
            ),
            world=target.world.name,
            children=children,
        )
    findings += _description_findings(target)
    findings += _stale_description_findings(target)
    return findings


def _module_findings(
    path: Path,
    tree: ast.Module,
    *,
    in_middleware: bool,
    registers: bool,
    world: str,
    children: set[str],
) -> list[Finding]:
    aliases = _aliases(tree)
    findings: list[Finding] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import | ast.ImportFrom):
            if _imports_random(node):
                findings.append(
                    Finding(
                        code="SH203",
                        severity="warning",
                        path=path,
                        line=node.lineno,
                        message="the random module does not replay",
                        fix=_IDS_FIX,
                    )
                )
            continue
        named = _child_named(node)
        if named is not None and named.child not in children:
            findings.append(
                Finding(
                    code="SH209",
                    severity="error",
                    path=path,
                    line=named.line,
                    message=f"world {world!r} adds no world named {named.child!r}",
                    fix=_child_fix(children),
                )
            )
        if not isinstance(node, ast.Call):
            continue
        if registers and isinstance(node.func, ast.Attribute) and node.func.attr == _INSTANCE:
            findings.append(
                Finding(
                    code="SH208",
                    severity="error",
                    path=path,
                    line=node.lineno,
                    message="creating an instance from inside a call is a WorldBug",
                    fix=_INSTANCE_FIX,
                )
            )
        called = _resolve(_dotted(node.func), aliases)
        if called is None:
            continue
        if not in_middleware and called in _WALL_CLOCK:
            findings.append(
                Finding(
                    code="SH201",
                    severity="warning",
                    path=path,
                    line=node.lineno,
                    message=f"{called}() reads the wall clock, not the instance's",
                    fix=_CLOCK_FIX,
                )
            )
        if called.startswith("random.") or called in _UUID:
            findings.append(
                Finding(
                    code="SH203",
                    severity="warning",
                    path=path,
                    line=node.lineno,
                    message=f"{called}() does not replay",
                    fix=_IDS_FIX,
                )
            )
    return findings


def _description_findings(target: Target) -> list[Finding]:
    """SH205: a registered tool with nothing for an agent to read.

    Asked of the `World` rather than of the source, because a tool's description
    is its docstring *or* the `description=` it was registered with *or* whatever
    a factory put on the `Tool`, and only the registry knows which.
    """
    findings: list[Finding] = []
    for tool in target.world.tools.values():
        # The framework's own two. They are never listed and never reach an
        # agent, so a description is not what they are for.
        if tool.control or tool.description.strip():
            continue
        path, line = _source_of(tool, target.package_dir)
        findings.append(
            Finding(
                code="SH205",
                severity="warning",
                path=path,
                line=line,
                message=f"tool {tool.name!r} has an empty description",
                fix=(
                    "give the function a docstring, which becomes the whole description, or pass"
                    " description= to @world.tool"
                ),
            )
        )
    return findings


def _stale_description_findings(target: Target) -> list[Finding]:
    """SH206: a renamed tool's description names a sibling by the name nobody sees.

    Per node, over the names a node's tools are actually *published* under. A node
    reached by two routes is described once here even though it is contributed
    twice -- there is one piece of text, written in one place, and the second
    route's stale surface is SH207's to report.

    Anchored on the host's own `add_world`, not on the sentence. The text belongs
    to the added world and the edit does not: an installed vendor world's
    description is inside `site-packages`, a file the host's author cannot change
    and should not, while `tool_prefix=` is in this world's `world.py`. Where the
    sentence is written is in the message, because a reader does need to know it
    is not theirs.
    """
    composition = target.composition
    if composition is None:
        return []
    findings: list[Finding] = []
    for path, entries in _renamed_by_node(composition.tools.values()).items():
        for tool, exposed in entries.items():
            for other, other_exposed in entries.items():
                if other is tool or not _mentions(tool.description, other.name):
                    continue
                findings.append(
                    Finding(
                        code="SH206",
                        severity="warning",
                        path=declaration_path(target),
                        message=(
                            f"{exposed!r} describes {other.name!r}, which node {path!r} "
                            f"contributes as {other_exposed!r}; the description is "
                            f"{_written_at(tool, target.package_dir)}"
                        ),
                        fix=_STALE_FIX,
                    )
                )
    return findings


def _written_at(tool: Tool, fallback: Path) -> str:
    """Where a tool's description is written, for a message that has to name a file."""
    path, line = _source_of(tool, fallback)
    return f"{path}:{line}" if line is not None else str(path)


def _renamed_by_node(entries: Iterable[Contributed]) -> dict[str, dict[Tool, str]]:
    """Each node's renamed tools, as the tool itself and the name the agent sees.

    Renamed only: a tool contributed under its own name is one whose description
    a reader of the tool list can follow, and a world with no prefix anywhere is
    the case this rule has nothing to say about.
    """
    renamed: dict[str, dict[Tool, str]] = {}
    for entry in entries:
        if entry.name != entry.tool.name:
            renamed.setdefault(entry.node.path, {}).setdefault(entry.tool, entry.name)
    return renamed


def _mentions(description: str, name: str) -> bool:
    """Whether a description names a tool, on a word boundary and not inside another name."""
    return re.search(rf"\b{re.escape(name)}\b", description) is not None


class _Child(NamedTuple):
    """A child name a module spells out, and the line it spells it on."""

    child: str
    line: int


def _child_named(node: ast.AST) -> _Child | None:
    """The child name a `ctx.worlds` attribute or string subscript spells, if any.

    A name beginning with an underscore is not one: `Worlds.__getattr__` is
    consulted only for what ordinary attribute lookup does not find, so
    `ctx.worlds.__class__` reaches the container itself and never a child.
    """
    match node:
        case ast.Attribute(value=value, attr=str(name)) if _dotted(value) == _CTX_WORLDS:
            return None if name.startswith("_") else _Child(name, node.lineno)
        case ast.Subscript(value=value, slice=ast.Constant(value=str(name))) if (
            _dotted(value) == _CTX_WORLDS
        ):
            return _Child(name, node.lineno)
        case _:
            return None


def _child_fix(children: set[str]) -> str:
    """What to do about a name no `add_world` registered."""
    if not children:
        return "this world adds no worlds, so ctx.worlds reaches nothing; add_world one first"
    return (
        f"name one of the worlds it adds ({', '.join(sorted(children))}), or add the one it means"
    )


def _source_of(tool: Tool, fallback: Path) -> tuple[Path, int | None]:
    """Where a tool's function is written, as far as Python can say."""
    try:
        path = inspect.getsourcefile(tool.fn)
        if path is None:
            return fallback, None
        return Path(path), inspect.getsourcelines(tool.fn)[1]
    except OSError, TypeError:
        # A tool built from a callable with no source of its own: a factory's
        # closure, a partial, something defined in a REPL.
        return fallback, None


def _inside(path: Path, package_dir: Path, directory: str) -> bool:
    """Whether a module is part of one of the world's registering layers.

    The module `tools.py` counts as `tools/`: a small world keeps its tools in
    one module and the rule is about what the code is for, not about how many
    files it took.
    """
    parts = path.relative_to(package_dir).parts
    return directory in parts[:-1] or parts[-1] == f"{directory}.py"


def _imports_random(node: ast.Import | ast.ImportFrom) -> bool:
    """Whether a statement imports the standard library's `random`.

    `level == 0` for the same reason `_bindings` insists on it: `from .random
    import seeded` is the world's own `random.py`, which is a module a world is
    perfectly entitled to have and is not the module this rule is about.
    """
    if isinstance(node, ast.ImportFrom):
        return node.module == "random" and node.level == 0
    return any(alias.name == "random" or alias.name.startswith("random.") for alias in node.names)


def _dotted(node: ast.expr) -> str | None:
    """`a.b.c` for an attribute chain rooted in a plain name, else `None`.

    A call on anything else -- a subscript, a call's result, a literal -- is not
    a name these rules can reason about, and guessing is how a lint earns a
    reputation for crying wolf.
    """
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return None
    parts.append(current.id)
    return ".".join(reversed(parts))


def _resolve(dotted: str | None, aliases: dict[str, str]) -> str | None:
    """The dotted name with its root replaced by what the module imported it as."""
    if dotted is None:
        return None
    root, _, rest = dotted.partition(".")
    target = aliases.get(root)
    if target is None:
        return dotted
    return f"{target}.{rest}" if rest else target


def _aliases(tree: ast.Module) -> dict[str, str]:
    """Every name the module's imports bind, mapped to what it names.

    `import datetime as dtm` gives `dtm -> datetime`; `from datetime import
    datetime` gives `datetime -> datetime.datetime`. Only module-level and
    function-level `import` statements exist to find, and `ast.walk` gets both.
    """
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        for name, full in _bindings(node):
            aliases[name] = full
    return aliases


def _bindings(node: ast.AST) -> Iterator[tuple[str, str]]:
    match node:
        case ast.Import():
            # Only the renames. `import datetime` and `import a.b` bind a name
            # that already spells what it means, so resolving them would map a
            # name to itself.
            for alias in node.names:
                if alias.asname:
                    yield alias.asname, alias.name
        case ast.ImportFrom(module=str(module), level=0):
            for alias in node.names:
                yield alias.asname or alias.name, f"{module}.{alias.name}"
        case _:
            return
