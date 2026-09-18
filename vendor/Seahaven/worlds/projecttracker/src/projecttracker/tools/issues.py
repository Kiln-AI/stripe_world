"""Issues: the thing this tracker is for, and the seven tools that move them.

Three rules run through the whole module, and each of them is this product's
rather than the framework's.

**Keys.** An issue is minted `TEAMKEY-n` from a counter on its team, bumped by the
same `UPDATE ... RETURNING` that reads it, so two issues never take one number and
a number is never handed back when an issue is archived. `ENG-41` is the
forty-first issue team `ENG` ever had, whichever project it is in and whatever
became of the forty before it.

**A closed issue has no assignee.** Moving an issue to `done` or `canceled` drops
its assignee, and assigning one is refused. The product's reasoning is that an
assignee means "this person is doing this", which a finished issue has nobody
doing; the eval-facing consequence is that `assignee_id IS NOT NULL` and
`status = 'done'` is a state this world cannot be in, and a grader may rely on it.

**An archived issue keeps its fields.** `archive_issue` stamps `archived_at` and
takes the issue out of `list_issues`. It is still there -- `get_issue` answers
with it, `run_sql` sees it, the change log carries it -- and no tool will change a
field of it again, `set_issue_labels` in `labels.py` included. Commenting on one
still works, and appends to its trail: archiving freezes the issue, not the
conversation about it.
"""

from typing import Any

import seahaven
from projecttracker.errors import Conflict, InvalidInput, NotFound
from projecttracker.tools import _pagination, _rows
from projecttracker.tools._events import record_event
from projecttracker.tools._types import (
    CLOSED_STATUSES,
    Cursor,
    IssueOrder,
    IssueStatus,
    Limit,
    Priority,
    Timestamp,
)
from projecttracker.world import world

__all__ = [
    "ORDERS",
    "archive_issue",
    "assign_issue",
    "create_issue",
    "get_issue",
    "list_issues",
    "transition_issue",
    "update_issue",
]

# The four orderings `list_issues` offers, each backed by an index on the column
# it sorts. The keyset's second half is always the id, which is unique, so no page
# boundary can fall inside a group of rows sharing a timestamp.
ORDERS = {
    "created_at_desc": _pagination.Order("issues:created_at_desc", "created_at", "id", True),
    "created_at_asc": _pagination.Order("issues:created_at_asc", "created_at", "id", False),
    "updated_at_desc": _pagination.Order("issues:updated_at_desc", "updated_at", "id", True),
    "updated_at_asc": _pagination.Order("issues:updated_at_asc", "updated_at", "id", False),
}


@world.tool
def create_issue(
    ctx: seahaven.Ctx,
    project_id: str,
    title: str,
    description: str = "",
    status: IssueStatus = "backlog",
    priority: Priority = 0,
    assignee_id: str | None = None,
    due_at: Timestamp | None = None,
    actor_id: str | None = None,
) -> dict[str, Any]:
    """File an issue in a project.

    The key is minted from the project's team: the first issue team `ENG` ever
    has is `ENG-1`, whichever of its projects it is filed in.
    """
    project = _rows.require_project(ctx, project_id)
    actor = _rows.resolve_actor(ctx, actor_id)
    if assignee_id is not None:
        _rows.require_user(ctx, assignee_id)
        if status in CLOSED_STATUSES:
            raise Conflict(f"an issue that is {status} has no assignee")
    issue_id = ctx.ids.uuid()
    now = ctx.clock.iso()
    # Minted before the insert and not inside its argument list: taking the
    # number is a write to the team, and a write should be a statement of its
    # own rather than something that happens while arguments are evaluated.
    key = _mint_key(ctx, str(project["team_id"]))
    ctx.db.execute(
        "INSERT INTO issues (id, project_id, key, title, description, status, priority,"
        " assignee_id, creator_id, created_at, updated_at, due_at, archived_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
        issue_id,
        project_id,
        key,
        title,
        description,
        status,
        priority,
        assignee_id,
        actor,
        now,
        now,
        due_at,
    )
    record_event(
        ctx,
        issue_id=issue_id,
        actor_id=actor,
        kind="created",
        payload={"status": status, "priority": priority, "assignee_id": assignee_id},
    )
    return _rows.require_issue(ctx, issue_id)


@world.tool
def get_issue(
    ctx: seahaven.Ctx, issue_id: str | None = None, key: str | None = None
) -> dict[str, Any]:
    """One issue, by id or by key (`ENG-41`). Exactly one of the two.

    Both at once is refused rather than resolved by precedence: an agent that
    sent both believes they name the same issue, and answering with one of them
    would hide the case where they do not.
    """
    match (issue_id, key):
        case (None, None):
            raise InvalidInput("issue_id", "give an issue_id or a key")
        case (str(), str()):
            raise InvalidInput("issue_id", "give an issue_id or a key, not both")
        case (str(found), None):
            return _rows.require_issue(ctx, found)
        case _:
            return _rows.require_issue(ctx, _id_of_key(ctx, str(key)))


@world.tool
def list_issues(
    ctx: seahaven.Ctx,
    status: IssueStatus | None = None,
    assignee_id: str | None = None,
    project_id: str | None = None,
    label_ids: list[str] | None = None,
    created_after: Timestamp | None = None,
    order: IssueOrder = "created_at_desc",
    limit: Limit = 50,
    cursor: Cursor | None = None,
) -> dict[str, Any]:
    """The issues that match every filter given, newest first by default.

    `label_ids` matches issues carrying *all* of the labels named, which is what
    makes the filters compose: each one narrows the result, and none of them
    widens it.

    Archived issues are not here. `get_issue` still answers with one, and
    `search_issues` still finds one; this is the product's list of live work.
    """
    where = ["archived_at IS NULL"]
    params: list[Any] = []
    if status is not None:
        where.append("status = ?")
        params.append(status)
    if assignee_id is not None:
        _rows.require_user(ctx, assignee_id)
        where.append("assignee_id = ?")
        params.append(assignee_id)
    if project_id is not None:
        _rows.require_project(ctx, project_id)
        where.append("project_id = ?")
        params.append(project_id)
    if created_after is not None:
        where.append("created_at > ?")
        params.append(created_after)
    if label_ids:
        wanted = sorted(set(label_ids))
        for label_id in wanted:
            _rows.require_label(ctx, label_id)
        placeholders = ", ".join("?" * len(wanted))
        where.append(
            f"(SELECT count(*) FROM issue_labels WHERE issue_id = issues.id"
            f" AND label_id IN ({placeholders})) = ?"
        )
        params += [*wanted, len(wanted)]
    page = _pagination.page(
        ctx,
        select=f"SELECT {_rows.ISSUE_COLUMNS} FROM issues",
        where=where,
        params=params,
        order=ORDERS[order],
        limit=limit,
        cursor=cursor,
        sort_column=ORDERS[order].column,
    )
    return {
        "issues": _rows.attach_labels(ctx, page.rows),
        "next_cursor": page.next_cursor,
        "has_next": page.has_next,
    }


@world.tool
def update_issue(
    ctx: seahaven.Ctx,
    issue_id: str,
    title: str | None = None,
    description: str | None = None,
    status: IssueStatus | None = None,
    priority: Priority | None = None,
    assignee_id: str | None = None,
    due_at: Timestamp | None = None,
    actor_id: str | None = None,
) -> dict[str, Any]:
    """Change one or more of an issue's fields.

    An argument left out is left alone, so this tool sets fields and never clears
    them. `assign_issue(issue_id, assignee_id=null)` is how an issue is
    unassigned; a due date, which has no tool of its own, cannot be removed once
    it is set -- move it instead. Moving the issue to `done` or `canceled` drops
    its assignee, and naming an assignee in the same call as a closing status is
    refused rather than half applied.
    """
    issue = _rows.require_open_issue(ctx, issue_id)
    actor = _rows.resolve_actor(ctx, actor_id)
    changes: dict[str, Any] = {
        column: value
        for column, value in (
            ("title", title),
            ("description", description),
            ("status", status),
            ("priority", priority),
            ("due_at", due_at),
            ("assignee_id", assignee_id),
        )
        if value is not None
    }
    if not changes:
        raise InvalidInput("issue_id", "nothing to update: give at least one field to change")
    if status in CLOSED_STATUSES:
        if assignee_id is not None:
            raise Conflict(f"an issue that is {status} has no assignee")
        # The product's rule applied rather than refused: an update that closes an
        # issue takes it off whoever had it, and `_apply` records that it did.
        changes["assignee_id"] = None
    elif assignee_id is not None:
        _rows.require_user(ctx, assignee_id)
    _apply(ctx, issue, changes, actor)
    return _rows.require_issue(ctx, issue_id)


@world.tool
def assign_issue(
    ctx: seahaven.Ctx, issue_id: str, assignee_id: str | None, actor_id: str | None = None
) -> dict[str, Any]:
    """Give an issue to someone, or to nobody (`assignee_id: null`)."""
    issue = _rows.require_open_issue(ctx, issue_id)
    actor = _rows.resolve_actor(ctx, actor_id)
    if assignee_id is not None:
        _rows.require_user(ctx, assignee_id)
        if issue["status"] in CLOSED_STATUSES:
            raise Conflict(
                f"issue {issue['key']} is {issue['status']} and an issue that is "
                f"{issue['status']} has no assignee"
            )
    _apply(ctx, issue, {"assignee_id": assignee_id}, actor)
    return _rows.require_issue(ctx, issue_id)


@world.tool
def transition_issue(
    ctx: seahaven.Ctx, issue_id: str, status: IssueStatus, actor_id: str | None = None
) -> dict[str, Any]:
    """Move an issue to another status.

    Moving it to `done` or `canceled` also drops its assignee, and the trail
    records both. Moving it to the status it is already in changes nothing and
    records nothing: the agent's belief about the issue is already true.
    """
    issue = _rows.require_open_issue(ctx, issue_id)
    actor = _rows.resolve_actor(ctx, actor_id)
    changes: dict[str, Any] = {"status": status}
    if status in CLOSED_STATUSES:
        changes["assignee_id"] = None
    _apply(ctx, issue, changes, actor)
    return _rows.require_issue(ctx, issue_id)


@world.tool
def archive_issue(ctx: seahaven.Ctx, issue_id: str) -> dict[str, Any]:
    """Take an issue out of the tracker's lists, keeping the row.

    Archiving is final: no tool will change a field of an archived issue again,
    and archiving one twice is refused rather than restamped, so `archived_at` is
    when it happened. Comments are the exception and still work.
    """
    issue = _rows.require_issue(ctx, issue_id)
    if issue["archived_at"] is not None:
        raise Conflict(f"issue {issue['key']} is already archived")
    now = ctx.clock.iso()
    ctx.db.execute(
        "UPDATE issues SET archived_at = ?, updated_at = ? WHERE id = ?", now, now, issue["id"]
    )
    return _rows.require_issue(ctx, issue_id)


def _apply(ctx: seahaven.Ctx, issue: dict[str, Any], changes: dict[str, Any], actor: str) -> None:
    """Write the fields that really differ, stamp `updated_at`, and record the trail.

    "Really differ" is what keeps the trail and the change log honest: a call that
    sets the status an issue already has writes nothing, stamps nothing and
    records nothing, so two runs that reached the same state through different
    numbers of calls still compare equal.
    """
    different = {column: value for column, value in changes.items() if issue[column] != value}
    if not different:
        return
    assignments = ", ".join(f"{column} = ?" for column in different)
    ctx.db.execute(
        f"UPDATE issues SET {assignments}, updated_at = ? WHERE id = ?",
        *different.values(),
        ctx.clock.iso(),
        issue["id"],
    )
    if "status" in different:
        record_event(
            ctx,
            issue_id=str(issue["id"]),
            actor_id=actor,
            kind="status",
            payload={"from": issue["status"], "to": different["status"]},
        )
    if "assignee_id" in different:
        record_event(
            ctx,
            issue_id=str(issue["id"]),
            actor_id=actor,
            kind="assignee",
            payload={"from": issue["assignee_id"], "to": different["assignee_id"]},
        )


def _mint_key(ctx: seahaven.Ctx, team_id: str) -> str:
    """The team's next issue key, taken and bumped in one statement.

    `UPDATE ... RETURNING` rather than a read and a write: the read of the counter
    and the write of the next value are the same statement, so there is no window
    between them at all -- not because this world is concurrent (one instance has
    one writer) but because a counter that can be read without being bumped is a
    counter somebody will eventually read that way.
    """
    row = ctx.db.one(
        "UPDATE teams SET issue_counter = issue_counter + 1 WHERE id = ?"
        " RETURNING key, issue_counter",
        team_id,
    )
    # The team was read by `require_project`'s caller before this ran, and a
    # foreign key guarantees the project's team exists; the assertion states that
    # for the type checker rather than checking the data.
    assert row is not None
    return f"{row['key']}-{row['issue_counter']}"


def _id_of_key(ctx: seahaven.Ctx, key: str) -> str:
    row = ctx.db.one("SELECT id FROM issues WHERE key = ?", key)
    if row is None:
        raise NotFound("issue", key)
    return str(row["id"])
