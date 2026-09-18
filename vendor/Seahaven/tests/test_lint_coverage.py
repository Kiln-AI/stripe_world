"""SH301: the rule that a forgotten tool module cannot go unnoticed.

The failure it exists for is silent -- the world imports, the tool is simply not
registered, and the first sign is `unknown_tool` from an eval -- so the test that
matters most here is the last one: the walk that finds the modules must not be
what makes them look imported.
"""

import sys
from pathlib import Path

import pytest

from seahaven.cli import discover
from seahaven.lint import Target
from seahaven.lint import coverage as coverage_lint
from seahaven.world import World
from tests.conftest import NOTES_SCHEMA, WORLDS, stub_target

pytestmark = pytest.mark.usefixtures("isolated_imports")


def package(tmp_path: Path, modules: dict[str, str]) -> Path:
    """A package directory holding `modules`, by path relative to it."""
    package_dir = tmp_path / "pkg"
    for relative, source in modules.items():
        path = package_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return package_dir


def world_for(tmp_path: Path) -> World:
    return World(
        "linted",
        "1.0.0",
        NOTES_SCHEMA,
        fixtures_dir=tmp_path / "fixtures",
        work_dir=tmp_path / "work",
        state_format="seahaven.state/1",
    )


def test_a_world_that_imports_every_module_is_clean() -> None:
    found = discover(None, WORLDS / "tidy")
    assert coverage_lint.run(Target(found.world, found.package, found.imported)) == []


def test_a_module_under_tools_that_is_not_imported_is_sh301() -> None:
    found = discover(None, WORLDS / "messy")
    (finding,) = coverage_lint.run(Target(found.world, found.package, found.imported))
    assert finding.code == "SH301"
    assert finding.severity == "error"
    assert "messy.tools.orphan" in finding.message
    assert "messy/__init__.py" in finding.fix
    assert finding.path.name == "orphan.py"


def test_a_world_with_no_tools_directory_is_not_a_finding(tmp_path: Path) -> None:
    """Not every world has both directories, and an absent one registers nothing."""
    target = stub_target(world_for(tmp_path), package(tmp_path, {"__init__.py": ""}))
    assert coverage_lint.run(target) == []


@pytest.mark.parametrize("directory", ["tools", "middleware"])
def test_an_unimported_module_in_either_directory_is_sh301(tmp_path: Path, directory: str) -> None:
    package_dir = package(
        tmp_path,
        {
            "__init__.py": "",
            f"{directory}/__init__.py": "",
            f"{directory}/forgotten.py": "# never imported\n",
        },
    )
    target = stub_target(
        world_for(tmp_path), package_dir, imported=frozenset({f"stub.{directory}"})
    )
    (finding,) = coverage_lint.run(target)
    assert f"stub.{directory}.forgotten" in finding.message


def test_the_directory_itself_is_reported_when_the_package_never_imports_it(
    tmp_path: Path,
) -> None:
    """A `tools/` nobody imports is the whole directory registering nothing."""
    package_dir = package(tmp_path, {"__init__.py": "", "tools/__init__.py": ""})
    target = stub_target(world_for(tmp_path), package_dir)
    (finding,) = coverage_lint.run(target)
    assert finding.message.startswith("module 'stub.tools'")
    assert finding.path.name == "__init__.py"


def test_the_walk_does_not_answer_its_own_question(tmp_path: Path) -> None:
    """`pkgutil.walk_packages` imports the packages it recurses into.

    A nested package under `tools/` is imported by the walk itself, so a rule
    that compared against a live `sys.modules` would report the nested package's
    modules as imported -- by the walk, a moment ago, and by nothing else.
    """
    package(
        tmp_path,
        {
            "__init__.py": "",
            "tools/__init__.py": "",
            "tools/nested/__init__.py": "",
            "tools/nested/deep.py": "# never imported\n",
        },
    )
    sys.path.insert(0, str(tmp_path))
    target = Target(
        world=world_for(tmp_path),
        package=__import__("pkg"),
        imported=frozenset({"pkg", "pkg.tools"}),
    )
    reported = {finding.message for finding in coverage_lint.run(target)}
    assert any("pkg.tools.nested.deep" in message for message in reported)
    assert any("pkg.tools.nested'" in message for message in reported)


def test_a_namespace_tools_directory_is_pointed_at_by_directory(tmp_path: Path) -> None:
    """A `tools/` with no `__init__.py` imports fine; the finding must not name one."""
    package_dir = package(tmp_path, {"__init__.py": "", "tools/forgotten.py": "# never\n"})
    findings = coverage_lint.run(stub_target(world_for(tmp_path), package_dir))
    (directory,) = [f for f in findings if f.message.startswith("module 'stub.tools'")]
    assert directory.path == package_dir / "tools"
    assert directory.path.is_dir()
