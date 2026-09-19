"""The recipe behind the committed fixtures, exercised against what it committed.

A fixture is bytes in the repository, and bytes whose recipe no longer runs are
bytes nobody can change. `empty` is frozen; what is asserted here is that the
recipe still makes exactly those bytes, and that it freezes into the world it
is handed rather than the imported one.

`world=` is what makes that safe to test. A fixture lands in
`world.fixtures_dir`, so a test builds through a *copy* of the world pointed at
a temporary directory. Moving the imported world's directory instead would move
it for everything else in the run, and `copy.copy(world)` is the supported way
to say "this world, writing somewhere else".

`small` and `large` get a case each in the fixtures phase, when they are built.
"""

import copy
import hashlib
import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest
import seahaven

from conftest import BLANK_NOW, FIXTURE_NOW

# Named as a file and not imported as a module: `fixtures_src/` is authoring
# source that lives beside the package rather than inside it, so there is no
# import path to it that works from every rootdir.
GENERATE = Path(__file__).resolve().parent.parent / "fixtures_src" / "generate.py"


@pytest.fixture(scope="session")
def generate() -> Any:
    """`fixtures_src/generate.py`, imported by path, once for the session."""
    spec = importlib.util.spec_from_file_location("_generate_loaded_by_path", GENERATE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_the_recipe_still_makes_the_committed_empty_bytes(
    generate: Any, world: seahaven.World, tmp_path: Path
) -> None:
    """Build `empty` into a temporary directory and compare state-file bytes."""
    elsewhere = copy.copy(world)
    elsewhere.fixtures_dir = tmp_path / "fixtures"
    elsewhere.fixtures_dir.mkdir()

    fixture = generate.build("empty", world=elsewhere)

    assert fixture.id == "empty"
    assert fixture.now == generate.NOW
    assert fixture.description == generate.DESCRIPTIONS["empty"]
    assert fixture.dir == tmp_path / "fixtures" / "empty"
    assert [frozen.id for frozen in elsewhere.fixtures()] == ["empty"]
    committed = world.fixtures()[0]
    assert _sha256(fixture.state_path) == _sha256(committed.state_path)
    # The copy is what took the write: the imported world still holds exactly
    # the committed fixture.
    assert [frozen.id for frozen in world.fixtures()] == ["empty"]


def test_the_recipe_defaults_to_this_packages_world(generate: Any, world: seahaven.World) -> None:
    """No `world=` means this package's, imported by name when a build asks for it.

    The one line of the recipe that names the package, so the one line a rename
    can leave pointing at a module that is not here any more.
    """
    assert generate._package_world() is world


def test_the_recipe_rejects_an_unknown_fixture(generate: Any, tmp_path: Path) -> None:
    """`main` names what it knows and refuses what it does not."""
    assert generate.main(["nope"]) == 1
    assert generate.main(["empty"], world=_writing_nowhere(generate, tmp_path)) == 0


def test_the_one_instant_is_one_literal_everywhere(generate: Any) -> None:
    """`conftest.BLANK_NOW`, `conftest.FIXTURE_NOW` and `generate.NOW` are the
    same instant, chosen once (`components/fixtures.md`). A second literal that
    drifts is the bug this catches."""
    assert generate.NOW == FIXTURE_NOW == BLANK_NOW


def _writing_nowhere(generate: Any, tmp_path: Path) -> seahaven.World:
    """This package's world, pointed at a scratch fixtures directory."""
    elsewhere = copy.copy(generate._package_world())
    elsewhere.fixtures_dir = tmp_path / "fixtures"
    elsewhere.fixtures_dir.mkdir()
    return elsewhere


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
