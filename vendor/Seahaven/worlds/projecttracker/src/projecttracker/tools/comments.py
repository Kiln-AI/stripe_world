"""Comments: the conversation on an issue.

A comment is the one write in this world that leaves the issue's own row alone --
it does not stamp `updated_at`, because in this product a comment is activity and
not a change to the issue. It does append to the trail, so an eval asking "what
happened to ENG-41" gets the comment in order with the transitions around it.
"""

from typing import Any

import seahaven
from projecttracker.tools import _pagination, _rows
from projecttracker.tools._events import record_event
from projecttracker.tools._types import Cursor, Limit
from projecttracker.world import world

__all__ = ["BY_CREATION", "add_comment", "list_comments"]

# By `(created_at, id)`, ascending. Across a fixture's history that is oldest
# first, which is how a conversation is read. Within one episode it is not:
# the instance's clock does not move, so every comment an episode writes carries
# the same instant and the id -- a uuid -- is what actually orders them.
#
# The tiebreaker cannot be fixed here. `ORDER BY created_at, rowid` is what
# `AGENTS.md` tells a SQL reader to use and it works for them, but a keyset cursor
# has to carry its tiebreaker as a value and `rowid` is not a column this world
# projects. A monotonic sequence column on `comments` would carry, and was
# considered and not taken, for three reasons: the cause is the framework's frozen
# clock rather than anything this table does, so the fix belongs where the cause
# is; `components/projecttracker.md` is `status: complete` and its §1 spells this
# table's columns, so adding one is a deviation with no correctness argument
# behind it, unlike the three the phase plan records; and saying it costs nothing,
# because `seahaven.tool` publishes a tool's whole docstring as its description,
# so the caveat below is in the tool list the agent reads and not only in this
# file.
BY_CREATION = _pagination.Order(
    key="comments:created_at_asc", column="created_at", id_column="id", descending=False
)


@world.tool
def add_comment(
    ctx: seahaven.Ctx, issue_id: str, body: str, actor_id: str | None = None
) -> dict[str, Any]:
    """Say something on an issue."""
    issue = _rows.require_issue(ctx, issue_id)
    actor = _rows.resolve_actor(ctx, actor_id)
    comment_id = ctx.ids.uuid()
    ctx.db.execute(
        "INSERT INTO comments (id, issue_id, author_id, body, created_at) VALUES (?, ?, ?, ?, ?)",
        comment_id,
        issue_id,
        actor,
        body,
        ctx.clock.iso(),
    )
    record_event(
        ctx,
        issue_id=str(issue["id"]),
        actor_id=actor,
        kind="comment",
        payload={"comment_id": comment_id},
    )
    comment = ctx.db.one(f"SELECT {_rows.COMMENT_COLUMNS} FROM comments WHERE id = ?", comment_id)
    # The row was inserted in this statement's own transaction and read back by
    # its primary key; the assertion states that for the type checker.
    assert comment is not None
    return comment


@world.tool
def list_comments(
    ctx: seahaven.Ctx, issue_id: str, limit: Limit = 50, cursor: Cursor | None = None
) -> dict[str, Any]:
    """An issue's comments, by `created_at` and then by id.

    On a fixture's history that is oldest first. Comments written during one
    episode all share the tracker's instant -- its clock does not move -- and are
    ordered among themselves by id, which is stable across runs but is not the
    order they were written in.
    """
    _rows.require_issue(ctx, issue_id)
    return _pagination.page(
        ctx,
        select=f"SELECT {_rows.COMMENT_COLUMNS} FROM comments",
        where=("issue_id = ?",),
        params=(issue_id,),
        order=BY_CREATION,
        limit=limit,
        cursor=cursor,
        sort_column="created_at",
    ).as_result("comments")
