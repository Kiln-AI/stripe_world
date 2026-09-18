"""The three committed fixtures: the artifacts, their contents, and the recipe.

A fixture is bytes in the repository, and bytes with no source are bytes nobody
can change. Three things are asserted here: that each committed artifact is
intact and is the one this world's schema belongs to, that what is inside it is
what its description promises an eval author, and that `fixtures_src/generate.py`
still makes it -- so the recipe cannot rot beside the files it produced.
"""

import copy
import hashlib
import importlib
import importlib.util
import inspect
import shutil
import struct
import subprocess
import sys
from pathlib import Path
from typing import Any

import apsw
import pytest

import seahaven
from conftest import AGENCY, FIXTURE_NOW, SMALL_STARTUP
from projecttracker.world import world

# `load_all` and `verify` by their module, not through `seahaven`: a name that
# the component document section covering a module lists as part of that
# module's interface is public, and `components/fixtures_instances.md` §1 lists
# these. `seahaven/__init__.py` re-exports only the subset worth a short import,
# which is why the rule is about the component documents and not about that
# list -- the same rule under which `middleware/error_handler.py` imports
# `Handler` from `seahaven.world`.
# The rule is `architecture.md` section 1, published in `docs/reference/api.md`.
from seahaven import fixtures as fixture_files

# Where SQLite stamps the version that wrote a database file: a four-byte big
# endian `SQLITE_VERSION_NUMBER` at offset 96 of the header.
_VERSION_OFFSET = 96

# `components/projecttracker.md` §4: every fixture of this world together fits in
# ten megabytes. A fixture is copied on every instance creation and committed to
# git; the budget is what keeps both cheap.
_SIZE_BUDGET = 10 * 1024 * 1024

# What each fixture holds, table by table -- all nine of them, including the trail
# and the labels, which are what an eval reads and which no `Workspace` field
# names. Written out rather than derived, because the point of the assertion is
# that the numbers in the committed bytes are the numbers somebody chose. A
# generator change that shifts one of them is meant to fail here and be looked
# at, not to pass because the test recomputed it.
_CONTENTS = {
    "empty": {
        "users": 0,
        "teams": 0,
        "team_members": 0,
        "projects": 0,
        "issues": 0,
        "labels": 0,
        "issue_labels": 0,
        "comments": 0,
        "issue_events": 0,
    },
    SMALL_STARTUP: {
        "users": 3,
        "teams": 1,
        "team_members": 3,
        "projects": 2,
        "issues": 40,
        "labels": 6,
        "issue_labels": 62,
        "comments": 60,
        "issue_events": 144,
    },
    AGENCY: {
        "users": 12,
        "teams": 3,
        "team_members": 16,
        "projects": 9,
        "issues": 600,
        "labels": 30,
        "issue_labels": 868,
        "comments": 1500,
        "issue_events": 3030,
    },
}


def generate() -> Any:
    """`fixtures_src/generate.py`, imported by path.

    It is not part of the installed package -- it is authoring source that ships
    with the repository and not with the world -- so a test that must work from
    any rootdir names the file. That is *not* what the CLI will do: `--run
    module:function` takes a module path (`architecture.md` §8.6,
    `functional_spec.md` §18), and the module path this file has is
    `fixtures_src.generate`, which
    `test_the_generator_is_reachable_by_the_module_path_its_docstring_names`
    pins.
    """
    path = Path(world.fixtures_dir).parent / "fixtures_src" / "generate.py"
    # A name of this test's own, so nothing reads it as the module path the
    # recipe documents -- that is `fixtures_src.generate`, tested below.
    spec = importlib.util.spec_from_file_location("_generate_loaded_by_path", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def world_writing_into(directory: Path) -> seahaven.World:
    """This world, with its fixtures directory pointed at `directory`.

    `build(..., world=...)` freezes into the world it is handed, so this is how a
    test says "build them over here". A copy and not the imported object: moving
    the imported world's `fixtures_dir` moves it for everything else in the run,
    and a test that failed halfway would leave the rest of the session reading
    fixtures out of a deleted temporary directory. `World.__copy__` gives the
    copy its own instance manager, which is what makes the redirect hold.
    """
    elsewhere = copy.copy(world)
    elsewhere.fixtures_dir = directory
    return elsewhere


def test_the_generator_is_reachable_by_the_module_path_its_docstring_names() -> None:
    """`fixtures_src.generate` resolves, because that is the contract Phase 7 will read.

    The generator's own docstring tells an author to freeze with
    `--run fixtures_src.generate:<id>`, and a recipe that names an entry point
    nobody can import is a recipe that will be found broken by the phase that
    builds the CLI. `fixtures_src/` has no `__init__.py`; it resolves as a
    namespace package with the world root on `sys.path`, which is where the CLI
    runs from.
    """
    world_root = Path(world.fixtures_dir).parent
    added = str(world_root) not in sys.path
    if added:
        sys.path.insert(0, str(world_root))
    try:
        for name in ("fixtures_src.generate", "fixtures_src"):
            sys.modules.pop(name, None)
        module = importlib.import_module("fixtures_src.generate")
        assert Path(module.__file__ or "") == world_root / "fixtures_src" / "generate.py"
        # `<module>:<function>` -- both halves, since both are in the command.
        assert callable(module.empty)
    finally:
        for name in ("fixtures_src.generate", "fixtures_src"):
            sys.modules.pop(name, None)
        if added:
            sys.path.remove(str(world_root))


def test_the_world_has_the_three_fixtures_the_component_document_names() -> None:
    assert [fixture.id for fixture in world.fixtures()] == [AGENCY, "empty", SMALL_STARTUP]


@pytest.mark.parametrize("fixture_id", ["empty", SMALL_STARTUP, AGENCY])
def test_every_fixture_describes_itself_to_whoever_writes_an_eval(fixture_id: str) -> None:
    """The description is the whole of what a fixture tells an eval author.

    Prose about the data and what it is good for, not about the schema: an author
    choosing between three fixtures reads these and nothing else.
    """
    fixture = _by_id()[fixture_id]
    assert fixture.description == generate().DESCRIPTIONS[fixture_id]
    assert len(fixture.description) > 60
    assert fixture.now == FIXTURE_NOW
    assert fixture.parent_id is None


@pytest.mark.parametrize("fixture_id", ["empty", SMALL_STARTUP, AGENCY])
def test_the_committed_bytes_are_the_bytes_the_sidecar_records(fixture_id: str) -> None:
    """`file_sha256`, checked the way instance creation checks it."""
    fixture = _by_id()[fixture_id]
    fixture_files.verify(fixture)
    digest = hashlib.sha256(fixture.state_path.read_bytes()).hexdigest()
    assert digest == fixture.meta.file_sha256


@pytest.mark.parametrize("fixture_id", ["empty", SMALL_STARTUP, AGENCY])
def test_every_fixture_was_frozen_from_this_worlds_schema(fixture_id: str) -> None:
    """The conformance check instance creation makes, made here with a name on it.

    A schema change without regenerated fixtures is the commonest way a world
    breaks, and the failure it causes otherwise is at the next `instance()`.
    """
    fixture = _by_id()[fixture_id]
    assert fixture.meta.schema_hash == world.schema_hash
    assert fixture.meta.world == world.name
    assert fixture.meta.world_version == world.version


def test_the_three_fixtures_together_fit_the_size_budget() -> None:
    """Ten megabytes for all of them, because each is copied per instance and committed."""
    total = sum(fixture.state_path.stat().st_size for fixture in world.fixtures())
    assert total < _SIZE_BUDGET, f"the fixtures are {total} bytes, over {_SIZE_BUDGET}"


@pytest.mark.parametrize("fixture_id", ["empty", SMALL_STARTUP, AGENCY])
def test_every_fixture_holds_what_its_description_promises(fixture_id: str) -> None:
    """The counts, read out of the committed bytes rather than out of the generator."""
    with world.instance(fixture_id) as instance:
        db = instance.inspect()
        for table, expected in _CONTENTS[fixture_id].items():
            assert db.one(f"SELECT count(*) AS n FROM {table}") == {"n": expected}, table


@pytest.mark.parametrize(("fixture_id", "span_days"), [(SMALL_STARTUP, 60), (AGENCY, 180)])
def test_every_timestamp_is_inside_the_span_the_fixture_claims(
    fixture_id: str, span_days: int
) -> None:
    """The invariant the generator asserts while building, asserted of the bytes.

    A description that says "the last two months" has to be true of the rows, and
    a due date is the one thing that may point forward -- up to forty-five days,
    which is the tracker's horizon.
    """
    module = generate()
    with world.instance(fixture_id) as instance:
        db = instance.inspect()
        floor = module._shift(instance.clock, span_days)
        horizon = module._shift(instance.clock, -module.DUE_HORIZON_DAYS)
        for table, column, ahead in module._TIMESTAMPS:
            found = db.one(
                f"SELECT min({column}) AS low, max({column}) AS high FROM {table}"
                f" WHERE {column} IS NOT NULL"
            )
            assert found is not None
            if found["low"] is None:
                continue
            assert floor <= found["low"], f"{table}.{column}"
            assert found["high"] <= (horizon if ahead else FIXTURE_NOW), f"{table}.{column}"


@pytest.mark.parametrize("fixture_id", [SMALL_STARTUP, AGENCY])
def test_every_populated_fixture_has_something_overdue_and_a_trail_someone_else_wrote(
    fixture_id: str,
) -> None:
    """Two things `agency`'s description promises by saying "reporting".

    A tracker whose every due date is in the future has no answer to "what is
    overdue", and one whose every event was acted by the issue's filer answers
    "who moved the most issues to done" with "whoever filed them". Both are among
    the first questions a reporting eval asks, and both were true of these bytes
    once.
    """
    with world.instance(fixture_id) as instance:
        db = instance.inspect()
        overdue = db.one(
            "SELECT count(*) AS n FROM issues WHERE due_at IS NOT NULL AND due_at < ?",
            FIXTURE_NOW,
        )
        upcoming = db.one(
            "SELECT count(*) AS n FROM issues WHERE due_at IS NOT NULL AND due_at >= ?",
            FIXTURE_NOW,
        )
        assert overdue is not None and upcoming is not None
        assert overdue["n"] > 0 and upcoming["n"] > 0
        elsewhere = db.one(
            "SELECT count(*) AS n FROM issue_events"
            " JOIN issues ON issues.id = issue_events.issue_id"
            " WHERE issue_events.kind <> 'comment' AND issue_events.actor_id <> issues.creator_id"
        )
        assert elsewhere is not None and elsewhere["n"] > 0


@pytest.mark.parametrize("fixture_id", [SMALL_STARTUP, AGENCY])
def test_every_row_of_a_fixture_belongs_where_it_says_it_does(fixture_id: str) -> None:
    """The generator's own row-by-row check, made of the committed bytes.

    Two kinds of claim, both of which the span check alone passes: nothing is
    older than what it refers to -- these fixtures once had a third of their
    issues filed by people who had not joined yet -- and nothing reaches across a
    team, which is the boundary `agency` is sold on. Asserted here as well as in
    the generator because the artifact is what evals are written against, and it
    outlives the run that made it.
    """
    module = generate()
    with world.instance(fixture_id) as instance:
        db = instance.inspect()
        for what, query in module._PARENTS:
            found = db.one(query)
            assert found is not None
            assert found["n"] == 0, f"{fixture_id}: {found['n']} rows with {what}"


@pytest.mark.parametrize("fixture_id", [SMALL_STARTUP, AGENCY])
def test_every_populated_fixture_has_an_admin_for_the_viewer_to_be(fixture_id: str) -> None:
    """ "Viewer is the admin" is a property of the hook, so the fixture needs one."""
    with world.instance(fixture_id) as instance:
        viewer = instance.ctx.state["viewer_id"]
        assert instance.inspect().one("SELECT role FROM users WHERE id = ?", viewer) == {
            "role": "admin"
        }


def test_the_state_file_carries_no_journal_or_lock_file_beside_it() -> None:
    """The whole of what SH405 checks: a fixture is a sealed file, not a live database.

    The file's mode is deliberately not asserted here, and not because it does
    not matter. `freeze` sets `0o444`, and the framework's own suite tests that
    it does. But git records only the executable bit, so the mode of a
    *committed* fixture is whatever the cloning umask gave it, and any assertion
    about it here would pass in the tree that froze the file and fail in every
    clone. SH405 is the journal companions alone for that same reason
    (`components/cli_and_check.md` section 3).
    """
    for fixture in world.fixtures():
        assert not list(fixture.dir.glob("state.sqlite-*")), (
            "a fixture is checkpointed and journal-free"
        )
        assert sorted(path.name for path in fixture.dir.iterdir()) == [
            "fixture.yaml",
            "state.sqlite",
        ]


@pytest.mark.seahaven(fixture="empty")
def test_an_instance_of_the_empty_fixture_starts_at_the_frozen_instant_with_no_rows(
    instance: seahaven.Instance,
) -> None:
    """The committed bytes, opened the way an eval opens them: through the marker."""
    assert instance.clock.iso() == FIXTURE_NOW
    assert instance.fixture == "empty"
    assert instance.call("list_users") == {"users": [], "next_cursor": None, "has_next": False}


@pytest.mark.seahaven(fixture=SMALL_STARTUP)
def test_an_instance_of_a_populated_fixture_is_the_tracker_the_description_promises(
    instance: seahaven.Instance,
) -> None:
    """One read through the tools, which is how an eval meets a fixture."""
    assert instance.clock.iso() == FIXTURE_NOW
    issues = instance.call("list_issues", limit=250)["issues"]
    assert len(issues) == 40
    assert {issue["key"] for issue in issues} == {f"ENG-{n}" for n in range(1, 41)}
    assert instance.call("get_issue", key="ENG-1")["project_id"] in {
        project["id"] for project in instance.call("list_projects")["projects"]
    }


@pytest.mark.parametrize("fixture_id", ["empty", SMALL_STARTUP, AGENCY])
def test_using_a_fixture_does_not_touch_the_committed_file(fixture_id: str) -> None:
    """A fixture is copied, never opened: writing to an instance cannot damage it."""
    fixture = _by_id()[fixture_id]
    before = fixture.state_path.read_bytes()
    with world.instance(fixture_id) as instance, instance.bulk() as ctx:
        ctx.db.execute(
            "INSERT INTO users (id, email, name, role, created_at) VALUES ('u', 'a@b.invalid',"
            " 'A', 'admin', '2026-06-01T09:00:00.000Z')"
        )
    assert fixture.state_path.read_bytes() == before


@pytest.fixture
def rebuilt(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> tuple[Any, Any]:
    """Run the committed recipe into `tmp_path`, and hand back what it made and what is committed.

    Through `generate.main`, not through a copy of what it does: a test that
    rebuilt the fixture its own way would pass while the committed recipe rotted.
    `main` passes its `world=` down to `build`, which is the seam that lets the
    rebuild land in `tmp_path` -- `freeze` refuses to overwrite, and nothing may
    write into the repository from a test.
    """
    committed = _by_id()
    module = generate()

    rebuilt_dir = tmp_path / "fixtures"
    rebuilt_dir.mkdir()
    assert module.main([], world=world_writing_into(rebuilt_dir)) == 0

    printed = capsys.readouterr()
    for fixture_id in committed:
        assert f"froze {fixture_id} at {FIXTURE_NOW}" in printed.out

    made = fixture_files.load_all(rebuilt_dir)
    assert sorted(made) == sorted(committed)
    return made, committed


def test_the_generator_still_makes_the_fixtures_that_are_committed(
    rebuilt: tuple[Any, Any],
) -> None:
    """What the recipe makes today has the same schema, rows, clock and description.

    True on any SQLite build, which is why it is the test that must always run.
    The bytes are the test next door.
    """
    made, committed = rebuilt
    for fixture_id, fixture in committed.items():
        rebuilt_fixture = made[fixture_id]
        assert rebuilt_fixture.meta.schema_hash == fixture.meta.schema_hash
        assert rebuilt_fixture.now == fixture.now
        assert rebuilt_fixture.description == fixture.description
        assert rebuilt_fixture.parent_id == fixture.parent_id
        assert _dump(rebuilt_fixture.state_path) == _dump(fixture.state_path), fixture_id


def test_the_generator_still_makes_the_committed_fixtures_byte_for_byte(
    rebuilt: tuple[Any, Any],
) -> None:
    """And on the build that wrote the committed files, the same bytes.

    `VACUUM INTO` is byte-reproducible on one build, and its output carries the
    version that made it in the header, so the comparison can ask rather than
    assume. The ask is the first thing here rather than the last: a run that
    verified everything it could and then called `pytest.skip` would report as
    "not run", which is not what happened. Splitting the byte comparison out is
    what lets the content test above be reported as the pass it is.
    """
    made, committed = rebuilt
    for fixture_id, fixture in committed.items():
        committed_bytes = fixture.state_path.read_bytes()
        wrote = _sqlite_version_of(committed_bytes)
        if wrote != apsw.SQLITE_VERSION_NUMBER:
            pytest.skip(
                f"the committed fixtures were written by SQLite {wrote}, "
                f"not {apsw.SQLITE_VERSION_NUMBER}: bytes cannot be compared, and "
                "test_the_generator_still_makes_the_fixtures_that_are_committed "
                "compares the content"
            )
        assert made[fixture_id].state_path.read_bytes() == committed_bytes, (
            f"the generator no longer reproduces the committed {fixture_id}; "
            "regenerate it and commit the new bytes"
        )


def test_the_generator_run_as_a_script_refuses_to_overwrite_a_committed_fixture() -> None:
    """The way a fixture is built by hand today, driven as a script.

    `empty` is already in the repository, so the run must fail and change
    nothing: a fixture is immutable, and rebuilding one means deleting it
    deliberately first. This is also the only thing that runs the module's
    `__main__` block, which is what the instructions in its docstring tell an
    author to use.
    """
    script = Path(world.fixtures_dir).parent / "fixtures_src" / "generate.py"
    before = sorted(path.name for path in world.fixtures_dir.iterdir())

    done = subprocess.run([sys.executable, str(script), "empty"], capture_output=True, text=True)

    assert done.returncode != 0
    assert "already exists" in done.stderr
    assert sorted(path.name for path in world.fixtures_dir.iterdir()) == before


def test_the_generators_functions_say_in_their_types_what_a_builder_is() -> None:
    """`--run module:function` has to find a real signature at the end of it.

    The recipe's contract is its three signatures: a builder takes a live
    instance and returns nothing, `build` hands back the frozen fixture, and
    `main` is the command line over `build` -- `world=` is part of that seam now,
    so a caller rebuilding the whole set somewhere else has it at both doors.
    They are annotations, so nothing at runtime evaluates them on its own -- this
    asks them to resolve, which is what a reader, `ty`, and Phase 7's CLI all
    assume they do.
    """
    module = generate()

    assert inspect.get_annotations(module.empty, eval_str=True) == {
        "inst": seahaven.Instance,
        "return": None,
    }
    assert inspect.get_annotations(module.build, eval_str=True) == {
        "fixture_id": str,
        "world": seahaven.World | None,
        "return": seahaven.Fixture,
    }
    assert inspect.get_annotations(module.main, eval_str=True) == {
        "argv": list[str],
        "world": seahaven.World | None,
        "return": int,
    }


def test_the_generator_run_as_a_script_builds_the_world_it_lives_beside(tmp_path: Path) -> None:
    """From a checkout, the script uses that checkout's world, installed or not.

    Its docstring tells an author to run `python .../generate.py <id>` from a
    clone, where the world is source on disk and not a package on the path. So
    the module puts its own `src/` first, and this proves it does: the copy here
    calls itself version 9.9.9, and that is the version that has to reach the
    sidecar. An installed `projecttracker` is on this interpreter's path the
    whole time, and it is the wrong one.
    """
    source = Path(world.fixtures_dir).parent
    checkout = tmp_path / "projecttracker"
    (checkout / "src").mkdir(parents=True)
    (checkout / "fixtures").mkdir()
    (checkout / "fixtures_src").mkdir()
    # The nearest `pyproject.toml` above the package is what `fixtures/` hangs
    # off, so the copy needs one for the fixture to land where a checkout's does.
    (checkout / "pyproject.toml").write_text('[project]\nname = "projecttracker"\n')
    shutil.copytree(source / "src" / "projecttracker", checkout / "src" / "projecttracker")
    shutil.copy(source / "fixtures_src" / "generate.py", checkout / "fixtures_src" / "generate.py")
    world_py = checkout / "src" / "projecttracker" / "world.py"
    world_py.write_text(world_py.read_text().replace('version="1.0.0"', 'version="9.9.9"'))

    done = subprocess.run(
        [sys.executable, str(checkout / "fixtures_src" / "generate.py"), "empty"],
        capture_output=True,
        text=True,
    )

    assert done.returncode == 0, done.stderr
    built = fixture_files.load_all(checkout / "fixtures")
    assert built["empty"].meta.world_version == "9.9.9"
    assert built["empty"].now == FIXTURE_NOW


def test_the_generator_freezes_what_its_builder_wrote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A builder's writes reach the frozen file. `empty`'s builder writes nothing.

    That is the whole contract the two populated fixtures rest on, and `empty`
    alone cannot show it: its builder is empty, so a `build` that never called
    the builder would produce exactly the same fixture. A builder that does
    write is registered here, taken through the same `build`, and the row is
    looked for in the instance the fixture makes.
    """
    module = generate()

    def one_user(inst: seahaven.Instance) -> None:
        with inst.bulk() as ctx:
            ctx.db.execute(
                "INSERT INTO users (id, email, name, role, created_at) VALUES (?, ?, ?, ?, ?)",
                ctx.ids.uuid(),
                "founder@example.invalid",
                "Founder",
                "admin",
                ctx.clock.iso(),
            )

    monkeypatch.setitem(module.BUILDERS, "one_user", one_user)
    monkeypatch.setitem(module.DESCRIPTIONS, "one_user", "One user, for a test.")
    (tmp_path / "fixtures").mkdir()
    elsewhere = world_writing_into(tmp_path / "fixtures")

    fixture = module.build("one_user", world=elsewhere)

    assert fixture.description == "One user, for a test."
    assert fixture.now == FIXTURE_NOW
    assert fixture.dir.parent == tmp_path / "fixtures"
    with elsewhere.instance("one_user") as instance:
        (only,) = instance.call("list_users")["users"]
        assert only["email"] == "founder@example.invalid"


def test_the_generator_refuses_a_fixture_it_does_not_know(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`main` is what `python generate.py <id>` runs, and an id it has no builder for."""
    assert generate().main(["no_such_fixture"]) == 1
    printed = capsys.readouterr()
    assert printed.out == ""
    assert "no such fixture: no_such_fixture" in printed.err
    assert f"known fixtures: {AGENCY}, empty, {SMALL_STARTUP}" in printed.err


def _dump(state: Path) -> list[Any]:
    """Everything in a database file that is not the file's own layout.

    The schema and every row of every table, which is what "the same fixture"
    means on a SQLite build that lays its pages out differently.
    """
    connection = apsw.Connection(
        f"file:{state}?mode=ro", flags=apsw.SQLITE_OPEN_READONLY | apsw.SQLITE_OPEN_URI
    )
    try:
        schema = connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_schema ORDER BY type, name"
        ).fetchall()
        # Virtual tables are left out of the row comparison and only out of that:
        # an FTS5 table has no `rowid` column to order by, and nothing of its own
        # to compare -- the terms it holds live in the shadow tables beside it,
        # which are ordinary tables and are compared like the rest.
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type = 'table'"
                " AND name NOT LIKE 'sqlite_%' AND sql NOT LIKE 'CREATE VIRTUAL%' ORDER BY name"
            )
        ]
        # Sorted in Python rather than by SQL, because two of FTS5's shadow
        # tables are WITHOUT ROWID and have no `rowid` to order by, and their
        # column names are the module's business. `repr` orders tuples that mix
        # types and NULLs, which a plain sort refuses to.
        rows = [
            (table, sorted(connection.execute(f"SELECT * FROM {table}").fetchall(), key=repr))
            for table in tables
        ]
        return [schema, rows]
    finally:
        connection.close()


def _sqlite_version_of(database: bytes) -> int:
    """The `SQLITE_VERSION_NUMBER` SQLite stamped into the file header."""
    return struct.unpack(">I", database[_VERSION_OFFSET : _VERSION_OFFSET + 4])[0]


def _by_id() -> dict[str, Any]:
    """The world's fixtures by id, which is how every test here names one."""
    return {fixture.id: fixture for fixture in world.fixtures()}
