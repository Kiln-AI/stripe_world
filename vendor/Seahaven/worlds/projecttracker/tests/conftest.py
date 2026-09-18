"""What every test of this world starts from: the two instants, and a probe world.

A live instance comes from Seahaven's own pytest plugin: `@pytest.mark.seahaven`
says which fixture it starts from and the `instance` fixture makes it, so nothing
here builds one. That is the same path an eval takes -- `world.instance(...)`
then `instance.call(...)` -- and it is the only one that proves the chain, the
transaction and the serialiser are wired up. Nothing here calls a tool function
directly.
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

import seahaven
from projecttracker.middleware.error_handler import error_handler
from projecttracker.world import world

# The instant every fixture of this world is frozen at (`fixtures_src/generate.py`).
FIXTURE_NOW = "2026-06-01T09:00:00.000Z"

# A blank instance's clock in the tests that do not use a fixture. Deliberately
# not `FIXTURE_NOW`, so a test asserting one cannot pass because of the other.
BLANK_NOW = "2026-07-04T12:30:45.678Z"

# The two populated fixtures, by id, for the tests that read them rather than
# build their own state.
SMALL_STARTUP = "small_startup"
AGENCY = "agency"


@pytest.fixture
def probe(tmp_path: Path) -> Callable[..., seahaven.World]:
    """Builds a throwaway world carrying this world's real error handler.

    The handler's job is to map what the tools *below* it raise, and this world's
    own tools raise only shapes it passes through untouched. Rather than test the
    middleware as a function with a stub for `next_` -- which proves it maps an
    exception, not that it maps one raised by a tool inside a real chain -- each
    case registers a tool that fails the way a broken one would, on a world of
    its own, and drives it through `Instance.call`.

    The world is this world's schema and this world's middleware; only the tools
    are the test's. Registering them on the real `world` would add them to the
    world every other test and every later phase sees, and registration is for
    the life of the process.
    """

    def build(*tools: Any, name: str = "probe", schema: str = "") -> seahaven.World:
        built = seahaven.World(
            name,
            world.version,
            world.schema + schema,
            fixtures_dir=tmp_path / "fixtures",
            work_dir=tmp_path / "work",
            state_format="seahaven.state/1",
        )
        built.middleware(error_handler)
        for tool in tools:
            built.tool(tool)
        return built

    return build


@dataclass(frozen=True)
class Scaffold:
    """A blank instance's minimum viable workspace: two people, a team, a project.

    Built through the tools rather than through `bulk()`, so a test that then
    drives one tool is driving it against state the rest of the tools produced.
    Every id here is a real id from a real call, which is what lets a test assert
    on `creator_id` or `team_id` without knowing how ids are made.
    """

    admin: str
    member: str
    team: str
    team_key: str
    project: str
    label: str


@pytest.fixture
def scaffold(instance: seahaven.Instance) -> Scaffold:
    """The workspace every issue, label and comment test starts from."""
    admin = instance.call("create_user", email="ada@tracker.invalid", name="Ada", role="admin")
    member = instance.call("create_user", email="bo@tracker.invalid", name="Bo")
    team = instance.call("create_team", key="ENG", name="Engineering")
    instance.call("add_team_member", team_id=team["id"], user_id=admin["id"])
    instance.call("add_team_member", team_id=team["id"], user_id=member["id"])
    project = instance.call("create_project", team_id=team["id"], name="Platform", state="active")
    label = instance.call("create_label", team_id=team["id"], name="bug", color="#e11d48")
    return Scaffold(
        admin=admin["id"],
        member=member["id"],
        team=team["id"],
        team_key=team["key"],
        project=project["id"],
        label=label["id"],
    )


def an_issue(instance: seahaven.Instance, scaffold: Scaffold, **overrides: Any) -> dict[str, Any]:
    """One issue in the scaffold's project, filed by its admin.

    A helper and not a fixture: most tests want several, and each of them wants
    to say something different about the one it is about.
    """
    arguments: dict[str, Any] = {
        "project_id": scaffold.project,
        "title": "Login page is broken",
        "description": "Users cannot sign in with SSO",
        "actor_id": scaffold.admin,
    }
    return instance.call("create_issue", **(arguments | overrides))
