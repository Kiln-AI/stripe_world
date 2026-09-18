"""What the wheels carry, proved by building them and reading them.

Two of this repository's three packages ship files that no `import` mentions:
the framework's `docs/*.md` and `cli/templates/**`, read through
`importlib.resources` at runtime, and the reference world's `schema/*.sql`, read
the same way by `sql_files`. Nothing in the code imports any of them, so a build
backend that stopped including them would build clean, install clean, pass every
other test in this suite, and fail for the first person who ran `seahaven docs`
or `seahaven new` out of a released wheel.

That is the failure these tests exist to catch, and the only way to catch it is
to build the artefact and look inside it. The backend is `uv_build`; it packages
every file under the module directory rather than only the `.py` ones, which is
what makes the arrangement work and is exactly the assumption worth holding a
backend to.

The strongest statement here is the parity one: every file under `src/<module>`
is in the wheel, and nothing else is. A page or a template added to the source
tree is then covered the day it is written, with nothing to remember. The named
checks below it are the ones a reader of a failure wants: which page, which
entry point.

Nothing here runs when the suite runs against an installed wheel rather than a
checkout -- there is nothing to build -- or where `uv` is not on the path.
"""

import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

from seahaven.cli.new import TEMPLATE_SUFFIX, render
from tests.test_docs import PAGES

REPO_ROOT = Path(__file__).resolve().parents[1]

# The four packages, spelled as (project directory, module directory under it).
# `seahaven-xmlrpc`'s module is its distribution name normalised, which is the
# whole of what the backend is told: none of the three declares a module name.
PACKAGES = {
    "seahaven": (REPO_ROOT, "src/seahaven"),
    "projecttracker": (REPO_ROOT / "worlds" / "projecttracker", "src/projecttracker"),
    "seahaven-xmlrpc": (
        REPO_ROOT / "extensions" / "seahaven-xmlrpc",
        "src/seahaven_xmlrpc",
    ),
}

needs_checkout = pytest.mark.skipif(
    not (REPO_ROOT / "pyproject.toml").is_file() or shutil.which("uv") is None,
    reason="building a wheel needs the checkout and uv, and an installed wheel has neither",
)

pytestmark = needs_checkout


def build(project: Path, out: Path) -> Path:
    """`uv build` for one project, and the wheel it wrote.

    Not `--wheel`: the default builds the sdist and then the wheel *from it*,
    which is the path a release takes, so a file missing from the sdist fails
    here too rather than only once someone installs from one.
    """
    finished = subprocess.run(
        ["uv", "build", "--project", str(project), "--out-dir", str(out)],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert finished.returncode == 0, finished.stdout + finished.stderr
    wheels = sorted(out.glob("*.whl"))
    assert len(wheels) == 1, f"expected one wheel in {out}, found {wheels}"
    return wheels[0]


def members(wheel: Path) -> set[str]:
    """Every file in a wheel. Directory entries are not files and are dropped."""
    with zipfile.ZipFile(wheel) as archive:
        return {name for name in archive.namelist() if not name.endswith("/")}


def source_files(directory: Path) -> set[str]:
    """Every committed file under a module directory, relative to its parent."""
    return {
        str(path.relative_to(directory.parent))
        for path in directory.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }


@pytest.fixture(scope="session")
def wheels(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """Every package built once, into a directory of this run's own."""
    out = tmp_path_factory.mktemp("wheels")
    return {
        distribution: build(project, out / distribution)
        for distribution, (project, _) in PACKAGES.items()
    }


@pytest.fixture(scope="session")
def framework(wheels: dict[str, Path]) -> set[str]:
    return members(wheels["seahaven"])


@pytest.mark.parametrize("distribution", sorted(PACKAGES))
def test_every_file_under_the_module_directory_is_in_the_wheel(
    wheels: dict[str, Path], distribution: str
) -> None:
    """The parity check: the module travels whole, and nothing is added to it.

    `uv_build` is told nothing about what to include, so what this asserts is the
    default it is trusted for. The `.dist-info` is the build's own and is not
    part of the module.
    """
    project, module = PACKAGES[distribution]
    packaged = {name for name in members(wheels[distribution]) if ".dist-info/" not in name}
    assert packaged == source_files(project / module)


def test_every_docs_page_is_in_the_framework_wheel(framework: set[str]) -> None:
    """`seahaven docs` and every agent reading them work out of a wheel."""
    pages = {f"seahaven/docs/{page}" for page in PAGES}
    assert pages <= framework
    assert {name for name in framework if name.startswith("seahaven/docs/")} == pages


def test_every_scaffold_template_is_in_the_framework_wheel(framework: set[str]) -> None:
    """`seahaven new` renders these out of `importlib.resources`, not the repository."""
    templates = source_files(REPO_ROOT / "src" / "seahaven" / "cli" / "templates")
    assert templates, "the template tree is empty; the source, not the wheel, is wrong"
    assert all(name.endswith(TEMPLATE_SUFFIX) for name in templates)
    assert {f"seahaven/cli/{name}" for name in templates} <= framework


def test_the_framework_wheel_declares_both_entry_points(wheels: dict[str, Path]) -> None:
    """The console script, and the pytest plugin an installed world's tests need."""
    with zipfile.ZipFile(wheels["seahaven"]) as archive:
        (name,) = [n for n in archive.namelist() if n.endswith("/entry_points.txt")]
        declared = archive.read(name).decode("utf-8")
    assert "[console_scripts]\nseahaven = seahaven.cli:main" in declared
    assert "[pytest11]\nseahaven = seahaven.pytest_plugin" in declared


def test_the_worlds_schema_travels_and_its_fixtures_do_not(wheels: dict[str, Path]) -> None:
    """`sql_files` reads the schema out of the installed package; `fixtures/` is outside it."""
    packaged = members(wheels["projecttracker"])
    schema = {name for name in packaged if name.endswith(".sql")}
    assert schema == {
        f"projecttracker/schema/{path.name}"
        for path in (
            REPO_ROOT / "worlds" / "projecttracker" / "src" / "projecttracker" / "schema"
        ).glob("*.sql")
    }
    assert not [name for name in packaged if "fixtures" in name]


def test_a_scaffolded_world_builds_and_its_wheel_carries_its_schema(tmp_path: Path) -> None:
    """The template's backend, rendered: a world an author makes is a world that builds.

    The scaffold's `schema/001_items.sql` is the same arrangement as the reference
    world's and the same thing can silently stop travelling, so it is built here
    rather than read. Its `seahaven~=X.Y` dependency is never resolved: a build
    needs the backend and not the project's own requirements.
    """
    world = render("my-world", tmp_path / "my-world")
    packaged = members(build(world, tmp_path / "dist"))
    assert "my_world/schema/001_items.sql" in packaged
    assert "my_world/world.py" in packaged
    assert not [name for name in packaged if "fixtures" in name]


def test_no_project_declares_a_build_backend_other_than_uvs() -> None:
    """Including the scaffold template, so every world made from now on is a uv build."""
    declarations = [
        path.read_text(encoding="utf-8")
        for path in (
            *(project / "pyproject.toml" for project, _ in PACKAGES.values()),
            REPO_ROOT / "src/seahaven/cli/templates/base/pyproject.toml.tmpl",
        )
    ]
    assert len(declarations) == 4
    for text in declarations:
        assert 'build-backend = "uv_build"' in text
        assert "hatch" not in text
