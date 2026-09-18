"""The plugin, driven the way a world drives it: pytest inside pytest.

Every test here writes a world and a test module into a directory of its own and
runs pytest over them, because what is being tested is a *plugin* -- the marker,
the fixtures, the option and the failures -- and none of that exists outside a
run. Asserting on `seahaven.pytest_plugin`'s functions directly would test the
same code with none of the machinery that makes it a plugin.

The inner run is in process, so a world it imports stays in `sys.modules` and the
directory it was imported from stays on `sys.path`: `isolated_imports` undoes
both, which is what keeps one test's world from answering the next one's import.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.usefixtures("isolated_imports")

NOW = "2026-01-01T00:00:00.000Z"
OTHER_NOW = "2031-12-25T06:30:00.500Z"

# The world every test here writes: one STRICT table, three tools over it, and a
# startup hook, which is the whole of what a `seahaven` marker can reach.
#
# The hook writes with `INSERT OR REPLACE` because it runs on an instance made
# from a fixture as well as on a blank one, and a fixture frozen from this world
# already carries the row it writes.
WORLD_SOURCE = '''
"""A world small enough to read, real enough to freeze."""

import seahaven

world = seahaven.World(
    name="$package",
    version="1.0.0",
    schema="CREATE TABLE notes (id TEXT PRIMARY KEY, body TEXT NOT NULL) STRICT;",
    state_format="seahaven.state/1",
)


@world.instance_startup
def record_the_tier(ctx: seahaven.Ctx, /, *, tier: str = "free") -> None:
    """Write down the tier the marker asked for, so a test can read it back."""
    ctx.db.execute("INSERT OR REPLACE INTO notes (id, body) VALUES (?, ?)", "tier", tier)


@world.tool
def add_note(ctx: seahaven.Ctx, body: str) -> dict[str, str]:
    """Write a note and return it."""
    note = {"id": ctx.ids.uuid(), "body": body}
    ctx.db.execute("INSERT INTO notes (id, body) VALUES (?, ?)", note["id"], note["body"])
    return note


@world.tool
def bodies(ctx: seahaven.Ctx) -> list[str]:
    """Every note's body, in order."""
    return [str(row["body"]) for row in ctx.db.rows("SELECT body FROM notes ORDER BY body")]


@world.tool
def now(ctx: seahaven.Ctx) -> str:
    """The instance's clock."""
    return ctx.clock.iso()
'''

FIXTURE_NOTE = "from the fixture"


def write_world(pytester: pytest.Pytester, package: str = "tinyworld") -> Path:
    """A world on disk that discovery can find, in the `pytester` directory.

    The `pyproject.toml` carries a `[tool.pytest.ini_options]` table because that
    is what pins the inner run's rootdir here: the `world` fixture starts its
    search at `config.rootpath`, and a rootdir left to be inferred could sit
    above this directory, where Seahaven's own project is.
    """
    root = pytester.path
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "{package}"\n\n[tool.pytest.ini_options]\ntestpaths = ["."]\n',
        encoding="utf-8",
    )
    package_dir = root / "src" / package
    package_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / "__init__.py").write_text(
        WORLD_SOURCE.replace("$package", package), encoding="utf-8"
    )
    return root


def freeze_fixture(pytester: pytest.Pytester, fixture_id: str = "empty") -> None:
    """Freeze a fixture of the world `write_world` wrote, before the inner run.

    Loaded by its path and under a name of its own, because the inner run imports
    the same package properly, by name: a module left in `sys.modules` under that
    name is the one the run under test would get, and then the test would be
    about this function instead.
    """
    path = pytester.path / "src" / "tinyworld" / "__init__.py"
    spec = importlib.util.spec_from_file_location("_tinyworld_by_path", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        with module.world.instance(None, now=NOW) as instance:
            instance.call("add_note", body=FIXTURE_NOTE)
            instance.freeze(fixture_id, f"{fixture_id}, for a test")
    finally:
        del sys.modules[spec.name]


def test_a_marked_test_gets_an_instance_of_the_fixture_it_names(
    pytester: pytest.Pytester,
) -> None:
    """The row, the id and the clock all come from the fixture the marker names."""
    write_world(pytester)
    freeze_fixture(pytester)
    pytester.makepyfile(
        f"""
        import pytest

        @pytest.mark.seahaven(fixture="empty")
        def test_it(instance):
            assert instance.fixture == "empty"
            assert "{FIXTURE_NOTE}" in instance.call("bodies")
            assert instance.call("now") == "{NOW}"
        """
    )
    pytester.runpytest().assert_outcomes(passed=1)


def test_a_blank_instance_is_what_fixture_none_means(pytester: pytest.Pytester) -> None:
    """`fixture=None` is a value the marker carries, not a marker that said nothing."""
    write_world(pytester)
    freeze_fixture(pytester)
    pytester.makepyfile(
        f"""
        import pytest

        @pytest.mark.seahaven(fixture=None)
        def test_it(instance):
            assert instance.fixture is None
            assert instance.call("bodies") == ["free"]
            assert "{FIXTURE_NOTE}" not in instance.call("bodies")
        """
    )
    pytester.runpytest().assert_outcomes(passed=1)


def test_the_fixture_may_be_positional(pytester: pytest.Pytester) -> None:
    write_world(pytester)
    freeze_fixture(pytester)
    pytester.makepyfile(
        """
        import pytest

        @pytest.mark.seahaven("empty")
        def test_it(instance):
            assert instance.fixture == "empty"
        """
    )
    pytester.runpytest().assert_outcomes(passed=1)


def test_the_instance_is_destroyed_when_the_test_ends(pytester: pytest.Pytester) -> None:
    """Including when the test failed: an instance is a directory, not a leak budget."""
    write_world(pytester)
    pytester.makepyfile(
        """
        from pathlib import Path

        import pytest

        HERE = Path(__file__).parent
        pytestmark = pytest.mark.seahaven(fixture=None)

        def test_passes(instance):
            (HERE / "passed.txt").write_text(str(instance.dir))

        def test_fails(instance):
            (HERE / "failed.txt").write_text(str(instance.dir))
            raise AssertionError("deliberate: the instance is destroyed anyway")
        """
    )
    pytester.runpytest().assert_outcomes(passed=1, failed=1)
    for outcome in ("passed", "failed"):
        recorded = Path((pytester.path / f"{outcome}.txt").read_text())
        assert not recorded.exists(), f"the instance of the test that {outcome} was left behind"


def test_two_tests_with_one_marker_do_not_share_state(pytester: pytest.Pytester) -> None:
    write_world(pytester)
    pytester.makepyfile(
        """
        import pytest

        pytestmark = pytest.mark.seahaven(fixture=None)

        def test_writes(instance):
            instance.call("add_note", body="mine")
            assert "mine" in instance.call("bodies")

        def test_does_not_see_it(instance):
            assert "mine" not in instance.call("bodies")
        """
    )
    pytester.runpytest().assert_outcomes(passed=2)


def test_an_instance_without_a_marker_fails_with_the_line_to_add(
    pytester: pytest.Pytester,
) -> None:
    write_world(pytester)
    pytester.makepyfile(
        """
        def test_it(instance):
            assert instance is not None
        """
    )
    result = pytester.runpytest()
    result.assert_outcomes(errors=1)
    result.stdout.fnmatch_lines(["*the `instance` fixture needs a marker*"])
    result.stdout.fnmatch_lines(['*@pytest.mark.seahaven(fixture="empty")*'])


def test_a_marker_that_names_no_fixture_fails(pytester: pytest.Pytester) -> None:
    """The marker is there and says nothing; `fixture=None` is how "blank" is said."""
    write_world(pytester)
    pytester.makepyfile(
        """
        import pytest

        @pytest.mark.seahaven()
        def test_it(instance):
            assert instance is not None
        """
    )
    result = pytester.runpytest()
    result.assert_outcomes(errors=1)
    result.stdout.fnmatch_lines(["*needs a fixture*"])
    # The commonest way to write this one: a marker meant to *add* `now=` to a
    # module's `pytestmark`. The message says why that is not what happened.
    result.stdout.fnmatch_lines(["*replaces a module's `pytestmark` marker*"])


def test_a_fixture_given_twice_is_refused(pytester: pytest.Pytester) -> None:
    """One of the two would be dropped, and nobody should have to know which."""
    write_world(pytester)
    pytester.makepyfile(
        """
        import pytest

        @pytest.mark.seahaven("empty", fixture=None)
        def test_it(instance):
            assert instance is not None
        """
    )
    result = pytester.runpytest()
    result.assert_outcomes(errors=1)
    result.stdout.fnmatch_lines(["*given a fixture twice*"])


def test_two_markers_on_one_test_are_refused(pytester: pytest.Pytester) -> None:
    """On one node nothing chooses between them: the lower decorator wins, silently.

    Which is the opposite of how a reader going down the file reads them, and is
    the same ambiguity as a fixture given twice inside one marker.
    """
    write_world(pytester)
    freeze_fixture(pytester)
    pytester.makepyfile(
        """
        import pytest

        @pytest.mark.seahaven(fixture="empty")
        @pytest.mark.seahaven(fixture=None)
        def test_it(instance):
            assert instance is not None
        """
    )
    result = pytester.runpytest()
    result.assert_outcomes(errors=1)
    result.stdout.fnmatch_lines(["*2 @pytest.mark.seahaven markers*"])


def test_two_markers_in_a_modules_pytestmark_are_refused(pytester: pytest.Pytester) -> None:
    """The same ambiguity a level up, and it does not even resolve the same way.

    `pytestmark` as a list is one node carrying two markers, and `get_closest_marker`
    takes the *first* -- the opposite end from the two decorators above. Both tests
    run, both against `empty`, and nothing says so; the guard has to reach past the
    item into the nodes above it.
    """
    write_world(pytester)
    freeze_fixture(pytester)
    pytester.makepyfile(
        """
        import pytest

        pytestmark = [
            pytest.mark.seahaven(fixture="empty"),
            pytest.mark.seahaven(fixture=None),
        ]

        def test_it(instance):
            assert instance is not None
        """
    )
    result = pytester.runpytest()
    result.assert_outcomes(errors=1)
    result.stdout.fnmatch_lines(["*2 @pytest.mark.seahaven markers*"])


def test_a_subclass_pytestmark_beside_its_bases_is_refused(pytester: pytest.Pytester) -> None:
    """A base class and a subclass each carrying `pytestmark`: still one node.

    `get_unpacked_marks` consolidates a class's marks over `reversed(__mro__)`,
    so the subclass's `Class` node owns *both* -- the base's first -- and
    `get_closest_marker` takes the first. The override a reader writes is read
    backwards and nothing says so, which is the same defect one level down from
    two decorators on a test.
    """
    write_world(pytester)
    freeze_fixture(pytester)
    pytester.makepyfile(
        """
        import pytest

        class Base:
            pytestmark = [pytest.mark.seahaven(fixture=None)]

        class TestIt(Base):
            pytestmark = [pytest.mark.seahaven(fixture="empty")]

            def test_it(self, instance):
                assert instance is not None
        """
    )
    result = pytester.runpytest()
    result.assert_outcomes(errors=1)
    result.stdout.fnmatch_lines(["*2 @pytest.mark.seahaven markers*"])


def test_a_marker_on_a_test_overrides_the_modules(pytester: pytest.Pytester) -> None:
    """The legitimate override, which the refusal above must not touch.

    Two nodes and not one: a module of fixture tests with a single blank-instance
    case is how a world's suite is written, and `pytestmark` plus one marker on
    the test is how it says so.
    """
    write_world(pytester)
    freeze_fixture(pytester)
    pytester.makepyfile(
        """
        import pytest

        pytestmark = pytest.mark.seahaven(fixture="empty")

        def test_from_the_fixture(instance):
            assert instance.fixture == "empty"

        @pytest.mark.seahaven(fixture=None)
        def test_blank(instance):
            assert instance.fixture is None
        """
    )
    pytester.runpytest().assert_outcomes(passed=2)


def test_more_than_one_positional_fixture_is_refused(pytester: pytest.Pytester) -> None:
    write_world(pytester)
    pytester.makepyfile(
        """
        import pytest

        @pytest.mark.seahaven("empty", "other")
        def test_it(instance):
            assert instance is not None
        """
    )
    result = pytester.runpytest()
    result.assert_outcomes(errors=1)
    result.stdout.fnmatch_lines(["*takes one fixture, not 2*"])


def test_seed_passes_through(pytester: pytest.Pytester) -> None:
    """One seed, one run: two instances of the same marker mint the same id."""
    write_world(pytester)
    pytester.makepyfile(
        """
        import pytest

        MINTED = []

        @pytest.mark.seahaven(fixture=None, seed=7)
        @pytest.mark.parametrize("run", [1, 2])
        def test_it(instance, run):
            MINTED.append(instance.call("add_note", body="x")["id"])
            if run == 2:
                assert MINTED[0] == MINTED[1]
        """
    )
    pytester.runpytest().assert_outcomes(passed=2)


def test_a_bytes_seed_in_the_marker_is_refused(pytester: pytest.Pytester) -> None:
    """The marker passes `seed=` straight through, so a world's author meets the narrowing here."""
    write_world(pytester)
    pytester.makepyfile(
        """
        import pytest

        @pytest.mark.seahaven(fixture=None, seed=b"sixteen bytes!!!")
        def test_it(instance):
            assert False, "the instance should never have been made"
        """
    )
    result = pytester.runpytest()
    result.assert_outcomes(errors=1)
    result.stdout.fnmatch_lines(["*a seed must be an int or None, not bytes*"])


def test_a_state_format_in_the_marker_passes_through(pytester: pytest.Pytester) -> None:
    """The marker's keywords are the world's, and `state_format` is `world.instance`'s own."""
    write_world(pytester)
    pytester.makepyfile(
        """
        import pytest

        @pytest.mark.seahaven(fixture=None, state_format="seahaven.state+calls/1")
        def test_it(instance):
            instance.call("add_note", body="x")
            document = instance.state()
            assert document["format"] == "seahaven.state+calls/1"
            assert [call["tool"] for call in document["state"]["calls"]] == ["add_note"]
        """
    )
    pytester.runpytest().assert_outcomes(passed=1)


def test_now_passes_through(pytester: pytest.Pytester) -> None:
    write_world(pytester)
    pytester.makepyfile(
        f"""
        import pytest

        @pytest.mark.seahaven(fixture=None, now="{OTHER_NOW}")
        def test_it(instance):
            assert instance.call("now") == "{OTHER_NOW}"
        """
    )
    pytester.runpytest().assert_outcomes(passed=1)


def test_startup_kwargs_pass_through(pytester: pytest.Pytester) -> None:
    """Anything the marker carries that is not `fixture` is the world's to read."""
    write_world(pytester)
    pytester.makepyfile(
        """
        import pytest

        @pytest.mark.seahaven(fixture=None, tier="gold")
        def test_it(instance):
            assert instance.call("bodies") == ["gold"]
        """
    )
    pytester.runpytest().assert_outcomes(passed=1)


def test_a_startup_kwarg_the_world_does_not_take_is_still_the_worlds_error(
    pytester: pytest.Pytester,
) -> None:
    """The plugin hands the marker's keywords on; it does not vet them, and a
    world's typo detection is the thing that must not be swallowed."""
    write_world(pytester)
    pytester.makepyfile(
        """
        import pytest

        @pytest.mark.seahaven(fixture=None, teir="gold")
        def test_it(instance):
            assert instance is not None
        """
    )
    result = pytester.runpytest()
    result.assert_outcomes(errors=1)
    result.stdout.fnmatch_lines(["*unknown reset argument*teir*"])


def test_the_world_fixture_is_one_object_for_the_session(pytester: pytest.Pytester) -> None:
    """Session-scoped: a `World` is a declaration, and importing it twice is waste."""
    write_world(pytester)
    pytester.makepyfile(
        """
        SEEN = []

        def test_first(world):
            SEEN.append(world)

        def test_second(world):
            assert SEEN[0] is world
        """
    )
    pytester.runpytest().assert_outcomes(passed=2)


def test_the_world_option_overrides_the_convention(pytester: pytest.Pytester) -> None:
    """`--seahaven-world module:attr`, the CLI's `--world` under pytest's name."""
    root = write_world(pytester)
    second = root / "src" / "second"
    second.mkdir(parents=True)
    # Under a second name and *not* under `world`, so a run that ignored the
    # option would find `tinyworld` by the convention and pass on the wrong world.
    (second / "__init__.py").write_text(
        WORLD_SOURCE.replace("$package", "second") + "\nother = world\ndel world\n",
        encoding="utf-8",
    )
    pytester.makepyfile(
        """
        def test_it(world):
            assert world.name == "second"
        """
    )
    pytester.runpytest("--seahaven-world", "second:other").assert_outcomes(passed=1)


def test_a_package_with_no_world_fails_with_the_fix_and_no_traceback(
    pytester: pytest.Pytester,
) -> None:
    """`find_world`'s message is one line naming the edit; a traceback is not."""
    root = write_world(pytester, package="worldless")
    (root / "src" / "worldless" / "__init__.py").write_text("value = 1\n", encoding="utf-8")
    pytester.makepyfile(
        """
        def test_it(world):
            assert world is not None
        """
    )
    result = pytester.runpytest()
    result.assert_outcomes(errors=1)
    result.stdout.fnmatch_lines(["*must export world = seahaven.World(...)*"])
    assert "Traceback" not in result.stdout.str()


def test_the_marker_is_registered(pytester: pytest.Pytester) -> None:
    """`--strict-markers` is what a world's CI runs; an unregistered marker fails it."""
    write_world(pytester)
    pytester.makepyfile(
        """
        import pytest

        @pytest.mark.seahaven(fixture=None)
        def test_it(instance):
            assert instance is not None
        """
    )
    pytester.runpytest("--strict-markers").assert_outcomes(passed=1)


def test_the_plugin_does_nothing_to_a_run_that_does_not_use_it(
    pytester: pytest.Pytester,
) -> None:
    """Installed in every project; felt only by a test that asks for a fixture.

    There is no world in this directory at all, so a `world` fixture that
    resolved at collection or at session start would fail a run that never
    mentioned Seahaven.
    """
    pytester.makepyfile(
        """
        def test_it():
            assert True
        """
    )
    pytester.runpytest().assert_outcomes(passed=1)
