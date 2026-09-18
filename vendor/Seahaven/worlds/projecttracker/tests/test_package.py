"""The package as the tooling meets it: one world, its tools, its middleware and its hook.

The CLI, the pytest plugin and the OpenEnv app all find this world the same way
-- import the package, read `world` off it -- so what that import produces is
this world's real interface and is worth asserting directly.
"""

import json
import pkgutil
import subprocess
import sys
from pathlib import Path

import pytest

import projecttracker
import seahaven
from conftest import BLANK_NOW
from projecttracker import middleware, tools
from projecttracker.middleware.error_handler import error_handler
from projecttracker.startup import remember_viewer
from projecttracker.world import world

# The twenty-five tools `components/projecttracker.md` §3 names, plus the two
# helper factories it registers beside them. Written out, because a count would
# pass for a tool that was renamed and a set will not.
WORLD_TOOLS = {
    "create_user",
    "get_user",
    "list_users",
    "create_team",
    "get_team",
    "list_teams",
    "add_team_member",
    "list_team_members",
    "create_project",
    "get_project",
    "list_projects",
    "update_project",
    "create_label",
    "list_labels",
    "set_issue_labels",
    "create_issue",
    "get_issue",
    "list_issues",
    "update_issue",
    "assign_issue",
    "transition_issue",
    "archive_issue",
    "add_comment",
    "list_comments",
    "search_issues",
}
HELPERS = {"run_sql", "describe_schema"}

# The table tests in the second half of this module drive an instance with no
# fixture behind it; the package tests in the first half take none at all, and a
# module-wide marker costs them nothing.
pytestmark = pytest.mark.seahaven(fixture=None, now=BLANK_NOW)


def test_the_package_exports_the_world_object_the_tooling_looks_for() -> None:
    """`projecttracker.world` is a `World`, not the module of that name.

    The package has a submodule called `world` and an attribute called `world`,
    and the attribute wins because `__init__` binds it last. Everything that
    discovers a world -- `seahaven check`'s SH501, the CLI, the plugin -- reads
    that attribute, so an import reordering that let the module shadow it would
    break all three at once and nothing else would notice.
    """
    assert isinstance(projecttracker.world, seahaven.World)
    assert projecttracker.world is world


def test_the_world_is_named_and_versioned() -> None:
    assert world.name == "projecttracker"
    assert world.version == "1.0.0"


def test_the_schema_is_the_sql_files_on_disk_in_filename_order() -> None:
    """The DDL is `schema/*.sql` as written, and `World` proved it executes."""
    on_disk = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((Path(projecttracker.__file__).parent / "schema").glob("*.sql"))
    )
    assert world.schema == on_disk
    # The core tables first and the index that reads them second: `002_search.sql`
    # creates triggers on `issues`, which has to exist by then.
    assert world.schema.index("CREATE TABLE issues") < world.schema.index(
        "CREATE VIRTUAL TABLE issues_fts"
    )


def test_the_registry_holds_every_tool_and_the_control_tool() -> None:
    """The control tool is on every world; the rest is this world's whole surface."""
    assert set(world.tools) == WORLD_TOOLS | HELPERS | {"controller_run_sql"}


def test_the_tool_list_an_agent_sees_is_the_twenty_five_and_the_two_helpers() -> None:
    with world.instance(None, now="2026-01-01T00:00:00.000Z") as instance:
        listing = instance.tools()
    assert {tool["name"] for tool in listing} == WORLD_TOOLS | HELPERS
    assert len(WORLD_TOOLS) == 25
    for tool in listing:
        assert tool["description"], f"SH205: {tool['name']} has an empty description"


def test_the_startup_hook_is_registered_and_takes_the_reset_argument_it_documents() -> None:
    """`reset(user_id=...)` reaches the hook because the hook named it, and only then.

    `world.instance(..., anything_else=1)` is refused by the framework, so the
    set of `reset` arguments this world takes is exactly what its hooks spell.
    """
    (hook,) = world.startup_hooks
    assert hook.fn is remember_viewer
    assert world.accepted_startup_kwargs == {"user_id"}


def test_the_error_handler_is_registered_outermost() -> None:
    """Registration order is chain order, and an error wrapper wraps everything."""
    assert world.middlewares[0] is error_handler


def test_importing_the_package_is_what_registers_the_tools_and_the_middleware() -> None:
    """A fresh interpreter that imports the package and nothing else.

    Asserting this in process proves nothing: the test modules import
    `projecttracker.tools` and `projecttracker.middleware.error_handler`
    themselves, so the registrations have happened whether or not the package
    performs them. Deleting either import from `__init__.py` leaves a world that
    serves no tools and wraps no errors, and only a separate process can see it.
    """
    program = (
        "import json, projecttracker as p; "
        "print(json.dumps({'tools': sorted(p.world.tools), "
        "'middlewares': [m.__name__ for m in p.world.middlewares], "
        "'hooks': [h.fn.__name__ for h in p.world.startup_hooks]}))"
    )
    done = subprocess.run(
        [sys.executable, "-c", program], capture_output=True, text=True, check=True
    )
    registered = json.loads(done.stdout)
    assert set(registered["tools"]) >= WORLD_TOOLS
    assert registered["middlewares"] == ["error_handler"]
    assert registered["hooks"] == ["remember_viewer"]


def test_every_module_under_tools_and_middleware_is_imported_by_the_package() -> None:
    """The property `seahaven check`'s SH301 exists to enforce, asserted here too.

    A module that is written, never imported and therefore never registered is
    invisible: the world runs, and the tool is simply missing. The lint is
    Phase 7's; the property is true today and cheap to pin.
    """
    for package in (tools, middleware):
        for module in pkgutil.iter_modules(package.__path__):
            assert hasattr(package, module.name), (
                f"{package.__name__}/{module.name}.py is never imported, so nothing in it registers"
            )


def test_the_world_reads_its_fixtures_from_the_package_directory() -> None:
    """`fixtures/` beside `pyproject.toml`, found by walking up from `world.py`."""
    assert world.fixtures_dir.name == "fixtures"
    assert (world.fixtures_dir.parent / "pyproject.toml").is_file()


def test_the_worlds_description_is_a_sentence_the_readme_really_contains() -> None:
    """The hand-kept invariant `world.py` states, pinned as containment and no more.

    `world.py` carries the one-line description as a literal and says in a
    comment that it is kept in step with the README's opening paragraph by hand.
    Nothing derives it -- deriving prose from the README is precisely the
    machinery this design removed -- so without a test the two drift apart
    silently the next time someone edits the README, and the drift shows up on a
    published hub card and nowhere else.

    The check is deliberately loose: the sentence has to appear *somewhere* in
    the README, not be equal to its first paragraph and not be located by
    parsing one. A test that went looking for the opening paragraph would be a
    paragraph reader again, in the test suite instead of the runtime, and would
    fail on a heading added above it or a line rewrapped beneath it. This fails
    on one thing only, which is the thing that matters: the README no longer
    says what the card says.

    Whitespace is normalised on both sides because the README is hard-wrapped at
    a hundred columns and the literal is joined from three source lines, so the
    two agree on words and disagree on newlines. That is not a parse; it is the
    same sentence, read with the line breaks ignored.

    The README is located the way `get_metadata` locates it and the way the
    fixtures test above derives its path -- beside the world's `pyproject.toml`,
    from `fixtures_dir.parent` -- rather than by counting `..` from this file.
    """
    readme = (world.fixtures_dir.parent / "README.md").read_text(encoding="utf-8")
    assert world.description is not None
    assert " ".join(world.description.split()) in " ".join(readme.split()), (
        "worlds/projecttracker/README.md no longer contains world.py's description verbatim; "
        "they are kept in step by hand, so update whichever one is now wrong"
    )


@pytest.mark.parametrize(
    "role",
    ["admin", "member", "viewer"],
)
def test_the_users_table_accepts_the_three_roles(instance: seahaven.Instance, role: str) -> None:
    with instance.bulk() as ctx:
        ctx.db.execute(
            "INSERT INTO users (id, email, name, role, created_at) VALUES (?, ?, ?, ?, ?)",
            f"u_{role}",
            f"{role}@example.invalid",
            role.title(),
            role,
            ctx.clock.iso(),
        )
    assert len(instance.call("list_users")["users"]) == 1


def test_the_users_table_refuses_a_role_that_is_not_one_of_the_three(
    instance: seahaven.Instance,
) -> None:
    """The CHECK constraint is the schema's, and it is really there."""
    with pytest.raises(seahaven.DbError), instance.bulk() as ctx:
        ctx.db.execute(
            "INSERT INTO users (id, email, name, role, created_at) VALUES (?, ?, ?, ?, ?)",
            "u_1",
            "someone@example.invalid",
            "Someone",
            "owner",
            ctx.clock.iso(),
        )


def test_the_users_table_refuses_a_duplicate_email(instance: seahaven.Instance) -> None:
    with instance.bulk() as ctx:
        ctx.db.execute(
            "INSERT INTO users (id, email, name, role, created_at) VALUES ('u_1', 'a@b.invalid',"
            " 'A', 'member', '2026-06-01T09:00:00.000Z')"
        )
    with pytest.raises(seahaven.DbError), instance.bulk() as ctx:
        ctx.db.execute(
            "INSERT INTO users (id, email, name, role, created_at) VALUES ('u_2', 'a@b.invalid',"
            " 'B', 'member', '2026-06-01T09:00:00.000Z')"
        )


@pytest.mark.parametrize("column", ["name", "created_at", "email", "role"])
def test_the_users_table_refuses_a_row_with_a_column_missing(
    instance: seahaven.Instance, column: str
) -> None:
    """Every column of `users` is `NOT NULL`, and every one of them is asserted.

    The schema hash in the fixture's sidecar notices any change to this file, so
    dropping a `NOT NULL` is "caught" by a fixture test -- but that is a tripwire
    on the text, not a statement about the table. This asks the table.
    """
    row = {
        "id": "u_1",
        "email": "a@b.invalid",
        "name": "A",
        "role": "member",
        "created_at": "2026-06-01T09:00:00.000Z",
    }
    row[column] = None
    with pytest.raises(seahaven.DbError), instance.bulk() as ctx:
        ctx.db.execute(
            "INSERT INTO users (id, email, name, role, created_at) VALUES (?, ?, ?, ?, ?)",
            row["id"],
            row["email"],
            row["name"],
            row["role"],
            row["created_at"],
        )


def test_the_users_table_refuses_a_duplicate_id(instance: seahaven.Instance) -> None:
    """`id TEXT PRIMARY KEY`: the key is a key, not just a column named id."""
    with instance.bulk() as ctx:
        ctx.db.execute(
            "INSERT INTO users (id, email, name, role, created_at) VALUES ('u_1', 'a@b.invalid',"
            " 'A', 'member', '2026-06-01T09:00:00.000Z')"
        )
    with pytest.raises(seahaven.DbError), instance.bulk() as ctx:
        ctx.db.execute(
            "INSERT INTO users (id, email, name, role, created_at) VALUES ('u_1', 'c@d.invalid',"
            " 'C', 'member', '2026-06-01T09:00:00.000Z')"
        )


def test_the_users_table_is_strict(instance: seahaven.Instance) -> None:
    """STRICT in the DDL and STRICT in the live schema, which is what matters.

    The lint that would catch a table without it is Phase 7's (SH101); this asks
    SQLite, which is the only answer that cannot be wrong. A blob into a TEXT
    column is what the flag buys: any other type SQLite would convert, and a
    blob it refuses.
    """
    assert instance.inspect().one("SELECT strict FROM pragma_table_list WHERE name = 'users'") == {
        "strict": 1
    }
    with pytest.raises(seahaven.DbError), instance.bulk() as ctx:
        ctx.db.execute(
            "INSERT INTO users (id, email, name, role, created_at) VALUES (x'00ff', 'a@b.invalid',"
            " 'A', 'member', '2026-06-01T09:00:00.000Z')"
        )
