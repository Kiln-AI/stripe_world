"""Teams and who is in them.

A team owns projects, owns labels, and owns the counter every issue key is minted
from, which is why its `key` is short, capitalised and unique: it is the visible
half of `ENG-41`, and an agent reads a team out of an issue key without asking.

`get_team` takes the key rather than the id, and it is the only `get_` here that
does. That is the product's shape: an issue key is what an agent has in front of
it, and making it look an id up first to ask about the team would be a round trip
in service of a foreign key.
"""

from typing import Any

import seahaven
from projecttracker.errors import Conflict, NotFound
from projecttracker.tools import _pagination, _rows
from projecttracker.tools._types import Cursor, Limit, TeamKey
from projecttracker.world import world

__all__ = [
    "BY_CREATION",
    "MEMBERS_BY_JOINING",
    "add_team_member",
    "create_team",
    "get_team",
    "list_team_members",
    "list_teams",
]

BY_CREATION = _pagination.Order(
    key="teams:created_at_asc", column="created_at", id_column="id", descending=False
)

# A membership has no id of its own -- its key is `(team_id, user_id)` -- so the
# keyset's second half is the user, which is unique within the one team a page of
# members is about.
MEMBERS_BY_JOINING = _pagination.Order(
    key="team_members:joined_at_asc", column="joined_at", id_column="user_id", descending=False
)


@world.tool
def create_team(ctx: seahaven.Ctx, key: TeamKey, name: str) -> dict[str, Any]:
    """Start a team. Its key becomes the prefix of every issue key in it."""
    if ctx.db.one("SELECT id FROM teams WHERE key = ?", key) is not None:
        raise Conflict(f"a team with the key {key} already exists")
    team_id = ctx.ids.uuid()
    ctx.db.execute(
        "INSERT INTO teams (id, key, name, issue_counter, created_at) VALUES (?, ?, ?, 0, ?)",
        team_id,
        key,
        name,
        ctx.clock.iso(),
    )
    return _rows.require_team(ctx, team_id)


@world.tool
def get_team(ctx: seahaven.Ctx, key: TeamKey) -> dict[str, Any]:
    """One team, by its key: the `ENG` of `ENG-41`."""
    team = ctx.db.one(f"SELECT {_rows.TEAM_COLUMNS} FROM teams WHERE key = ?", key)
    if team is None:
        raise NotFound("team", key)
    return team


@world.tool
def list_teams(
    ctx: seahaven.Ctx, limit: Limit = 50, cursor: Cursor | None = None
) -> dict[str, Any]:
    """Every team, oldest first."""
    return _pagination.page(
        ctx,
        select=f"SELECT {_rows.TEAM_COLUMNS} FROM teams",
        order=BY_CREATION,
        limit=limit,
        cursor=cursor,
        sort_column="created_at",
    ).as_result("teams")


@world.tool
def add_team_member(ctx: seahaven.Ctx, team_id: str, user_id: str) -> dict[str, Any]:
    """Put someone on a team.

    Adding a member twice is refused rather than treated as a no-op: the second
    call means the caller believes something that is not so, and the `joined_at`
    already on the row is the answer to when they joined.
    """
    _rows.require_team(ctx, team_id)
    _rows.require_user(ctx, user_id)
    if (
        ctx.db.one(
            "SELECT user_id FROM team_members WHERE team_id = ? AND user_id = ?", team_id, user_id
        )
        is not None
    ):
        raise Conflict(f"user {user_id} is already a member of team {team_id}")
    joined_at = ctx.clock.iso()
    ctx.db.execute(
        "INSERT INTO team_members (team_id, user_id, joined_at) VALUES (?, ?, ?)",
        team_id,
        user_id,
        joined_at,
    )
    return {"team_id": team_id, "user_id": user_id, "joined_at": joined_at}


@world.tool
def list_team_members(
    ctx: seahaven.Ctx, team_id: str, limit: Limit = 50, cursor: Cursor | None = None
) -> dict[str, Any]:
    """Who is on a team, in the order they joined."""
    _rows.require_team(ctx, team_id)
    return _pagination.page(
        ctx,
        select=f"SELECT {_rows.MEMBER_COLUMNS} FROM team_members",
        where=("team_id = ?",),
        params=(team_id,),
        order=MEMBERS_BY_JOINING,
        limit=limit,
        cursor=cursor,
        sort_column="joined_at",
        id_column="user_id",
    ).as_result("members")
