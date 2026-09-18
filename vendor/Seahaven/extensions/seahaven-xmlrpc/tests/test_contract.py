"""The contract of `functional_spec.md` §21, asserted rather than asserted-to.

This package exists to prove that an extension can carry a protocol Seahaven has
never heard of, over the published seams and nothing else. The tests in the other
modules prove the protocol works; these prove it was built the way the contract
says, because an example that quietly reached past the contract would teach the
wrong thing to everyone who copied it.
"""

import ast
import importlib
import importlib.metadata
import sys
from pathlib import Path

import pytest

import seahaven
import seahaven_xmlrpc
from seahaven.cli import check
from seahaven.world import CONTROL_TOOL_NAMES

_EXTENSION = Path(seahaven_xmlrpc.__file__).parent
_FRAMEWORK = Path(seahaven.__file__).parent
_WORLD = "tracker_rpc:world"


def _modules(root: Path) -> list[Path]:
    return sorted(root.rglob("*.py"))


def test_importing_the_extension_registers_nothing() -> None:
    """An extension must not register itself: the world registers what it wants.

    A `World` built after this package is imported carries the framework's own two
    control tools and nothing else. Nothing in the extension runs at import: no
    tool, no middleware, no startup hook, no schema.
    """
    world = seahaven.World(
        "empty_after_import",
        "1.0.0",
        "CREATE TABLE t (id TEXT PRIMARY KEY);",
        state_format="seahaven.state/1",
    )
    assert set(world.tools) == set(CONTROL_TOOL_NAMES)
    assert world.middlewares == ()
    assert world.startup_hooks == ()


def test_seahaven_does_not_import_the_extension() -> None:
    """The dependency graph is extension -> seahaven, and never the other way.

    Read off the framework's source rather than off `sys.modules`, which this very
    test has already polluted by importing the extension.
    """
    mentions = [
        path.relative_to(_FRAMEWORK)
        for path in _modules(_FRAMEWORK)
        if "seahaven_xmlrpc" in path.read_text(encoding="utf-8")
    ]
    assert mentions == []


def test_seahaven_does_not_depend_on_the_extension_either() -> None:
    """The packaging half of the same rule, which is the half that would ship broken.

    `test_seahaven_does_not_import_the_extension` reads the source; this reads the
    installed metadata, including every extra. A framework that named an extension
    in its requirements would inflict it on everyone who installed the framework,
    which is the thing "Seahaven never imports an extension" is protecting.
    """
    requires = importlib.metadata.distribution("seahaven").requires or []
    assert [requirement for requirement in requires if "xmlrpc" in requirement] == []


def test_the_extension_does_not_monkeypatch_the_framework() -> None:
    """The other prohibition of §21: nothing here writes to anything of Seahaven's.

    Every spelling a monkeypatch has, because the shortest one --
    `seahaven.Thing = x` -- is the one nobody would use: the natural way to patch
    this framework is `seahaven.world.World.tool = ...`, three attributes deep,
    and the other two ways are `setattr` and a name imported out of the framework
    (`from seahaven import Tool; Tool.validate = ...`). All four are found by
    rooting an attribute chain at its `Name` and asking whether that name is
    Seahaven's -- which is what `_framework_names` reads off the module's own
    imports, rather than assuming the module spells it `seahaven`.
    """
    for path in _modules(_EXTENSION):
        assert _patches(path.read_text(encoding="utf-8")) == [], (
            f"{path.name} patches the framework"
        )


def test_the_monkeypatch_walk_finds_every_spelling_of_one() -> None:
    """The guard above, proved on source that does patch the framework.

    Without this the test is a claim about a file that happens to be clean, and a
    walk that flagged nothing at all would pass it. Each line below is a real way
    to monkeypatch this framework and each must be found; the two after them are
    the near misses that must not be, or the guard would refuse the extension's
    own ordinary code.
    """
    patching = """
import seahaven
import seahaven.world
from seahaven import Tool

seahaven.Tool = None
seahaven.world.World.tool = None
setattr(seahaven.tool.Tool, "validate", None)
Tool.validate = None
seahaven.Tool.validate: object = None
seahaven.Tool.count += 1
"""
    assert _patches(patching) == [6, 7, 8, 9, 10, 11]

    innocent = """
import seahaven
from seahaven import Tool

tool = Tool
ctx.state["x"] = seahaven.Tool
value.attribute = seahaven.Tool
setattr(value, "attribute", seahaven.Tool)
"""
    assert _patches(innocent) == []


def _patches(source: str) -> list[int]:
    """The line numbers in `source` that write to something of Seahaven's."""
    tree = ast.parse(source)
    names = _framework_names(tree)
    # The line comes off the target expression rather than off the statement:
    # `ast.walk` yields `AST`, which carries no position, and every target here is
    # an expression, which does.
    return sorted(
        where.lineno
        for node in ast.walk(tree)
        for where in _written_to(node)
        if _root_name(where) in names
    )


def _framework_names(tree: ast.Module) -> set[str]:
    """Every local name bound to something of Seahaven's, however it was imported."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {
                (alias.asname or alias.name).split(".")[0]
                for alias in node.names
                if alias.name.split(".")[0] == "seahaven"
            }
        elif isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "seahaven":
            names |= {alias.asname or alias.name for alias in node.names}
    return names


def _written_to(node: ast.AST) -> list[ast.expr]:
    """The attribute expressions a statement writes to, in any of the four spellings."""
    if isinstance(node, ast.Assign):
        return [target for target in node.targets if isinstance(target, ast.Attribute)]
    if isinstance(node, ast.AnnAssign | ast.AugAssign):
        return [node.target] if isinstance(node.target, ast.Attribute) else []
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in ("setattr", "delattr")
        and node.args
    ):
        return [node.args[0]]
    return []


def _root_name(expression: ast.expr) -> str | None:
    """`seahaven.world.World.tool` down to `seahaven`; `f().x` down to nothing."""
    while isinstance(expression, ast.Attribute):
        expression = expression.value
    return expression.id if isinstance(expression, ast.Name) else None


def test_the_extension_imports_only_published_names() -> None:
    """Every name it takes from Seahaven is in the `__all__` of the module it came from.

    That is the line this repository draws around what is public: `seahaven`'s own
    `__all__` is the short list worth a short import, and a component document's §1
    names the rest -- `seahaven.world.Middleware` is how a middleware states its
    own shape, which ProjectTracker's error handler does too.
    """
    for path in _modules(_EXTENSION):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.ImportFrom) or not (node.module or "").startswith(
                "seahaven"
            ):
                continue
            published = getattr(importlib.import_module(node.module or ""), "__all__", ())
            for alias in node.names:
                assert alias.name in published, (
                    f"{path.name}:{node.lineno} imports {alias.name!r} from {node.module!r}, "
                    f"which does not publish it"
                )


def test_the_extension_depends_on_nothing_but_seahaven_and_the_standard_library() -> None:
    """`pydantic` is the one exception, and it is one the framework hands over.

    An argument annotated `Annotated[str, Field(...)]` is how a Seahaven tool
    declares a constraint, so an extension building a tool has to name pydantic --
    which is why it is a dependency of `seahaven` itself and never resolved
    separately.
    """
    allowed = {"seahaven", "seahaven_xmlrpc", "pydantic"}
    for path in _modules(_EXTENSION):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                roots = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                roots = [(node.module or "").split(".")[0]]
            else:
                continue
            for root in roots:
                assert root in allowed or _is_standard_library(root), (
                    f"{path.name}:{node.lineno} imports {root!r}"
                )


def _is_standard_library(name: str) -> bool:
    return name in sys.stdlib_module_names


def test_the_world_built_on_the_extension_passes_seahaven_check() -> None:
    """Every lint, including the DDL rules over the table the extension ships.

    A schema fragment from an extension is not special by the time `check` sees
    it: there is one schema, and `CALL_LOG_DDL` has to be STRICT with an explicit
    primary key and no wall-clock default like everything else in it.
    """
    report = check.collect(_WORLD, Path(__file__).parent)
    assert [finding.render(report.root) for finding in report.findings] == []


@pytest.mark.seahaven(fixture=None, now="2026-06-01T09:00:00.000Z")
def test_the_extensions_table_is_in_the_worlds_schema_hash(
    world: seahaven.World, instance: seahaven.Instance
) -> None:
    """§21 point 4's cost, made concrete: the fragment changes the world's identity.

    The same world without `CALL_LOG_DDL` hashes differently, which is why every
    fixture of a world that adds one has to be regenerated -- and why this suite
    runs on a copy of ProjectTracker rather than on ProjectTracker.
    """
    without = seahaven.World(
        "tracker_rpc",
        world.version,
        seahaven.sql_files("projecttracker", "schema"),
        state_format="seahaven.state/1",
    )
    assert world.schema_hash != without.schema_hash
    assert instance.inspect().one(
        f"SELECT name FROM sqlite_master WHERE name = '{seahaven_xmlrpc.CALL_LOG_TABLE}'"
    ) == {"name": seahaven_xmlrpc.CALL_LOG_TABLE}
