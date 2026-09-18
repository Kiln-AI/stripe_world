"""Projects: what a team's issues are grouped under.

A project's `state` is the project's own and says nothing about its issues -- a
`done` project can still hold a `backlog` issue, because closing a project in this
product is a statement about the work, not a cascade over the rows. An eval that
wants both asks for both.
"""

from typing import Any

import seahaven
from projecttracker.errors import InvalidInput
from projecttracker.tools import _pagination, _rows
from projecttracker.tools._types import Cursor, Limit, ProjectState
from projecttracker.world import world

__all__ = ["BY_CREATION", "create_project", "get_project", "list_projects", "update_project"]

BY_CREATION = _pagination.Order(
    key="projects:created_at_asc", column="created_at", id_column="id", descending=False
)


@world.tool
def create_project(
    ctx: seahaven.Ctx, team_id: str, name: str, state: ProjectState = "planned"
) -> dict[str, Any]:
    """Start a project under a team."""
    _rows.require_team(ctx, team_id)
    project_id = ctx.ids.uuid()
    ctx.db.execute(
        "INSERT INTO projects (id, team_id, name, state, created_at) VALUES (?, ?, ?, ?, ?)",
        project_id,
        team_id,
        name,
        state,
        ctx.clock.iso(),
    )
    return _rows.require_project(ctx, project_id)


@world.tool
def get_project(ctx: seahaven.Ctx, project_id: str) -> dict[str, Any]:
    """One project, by id."""
    return _rows.require_project(ctx, project_id)


@world.tool
def list_projects(
    ctx: seahaven.Ctx,
    team_id: str | None = None,
    state: ProjectState | None = None,
    limit: Limit = 50,
    cursor: Cursor | None = None,
) -> dict[str, Any]:
    """Projects, oldest first, optionally of one team or in one state."""
    where: list[str] = []
    params: list[str] = []
    if team_id is not None:
        _rows.require_team(ctx, team_id)
        where.append("team_id = ?")
        params.append(team_id)
    if state is not None:
        where.append("state = ?")
        params.append(state)
    return _pagination.page(
        ctx,
        select=f"SELECT {_rows.PROJECT_COLUMNS} FROM projects",
        where=where,
        params=params,
        order=BY_CREATION,
        limit=limit,
        cursor=cursor,
        sort_column="created_at",
    ).as_result("projects")


@world.tool
def update_project(
    ctx: seahaven.Ctx,
    project_id: str,
    name: str | None = None,
    state: ProjectState | None = None,
) -> dict[str, Any]:
    """Rename a project, move it to another state, or both.

    An argument left out is left alone, which is what makes the tool safe to call
    with the one field an agent wants to change. Calling it with nothing to change
    is refused: it would return the project unchanged and look like it had done
    something.
    """
    _rows.require_project(ctx, project_id)
    changes = {"name": name, "state": state}
    given = {column: value for column, value in changes.items() if value is not None}
    if not given:
        raise InvalidInput("project_id", "nothing to update: give a name, a state, or both")
    assignments = ", ".join(f"{column} = ?" for column in given)
    ctx.db.execute(f"UPDATE projects SET {assignments} WHERE id = ?", *given.values(), project_id)
    return _rows.require_project(ctx, project_id)
