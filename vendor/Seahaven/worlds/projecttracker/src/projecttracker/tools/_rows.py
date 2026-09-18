"""What a row looks like on its way out, and how a tool finds the one it was given.

Two jobs, and they are the same job from both ends. The `*_COLUMNS` constants and
the projections below are the shape this product answers with: a tool selects
those columns and returns that dict, so two tools that return an issue return the
same issue. The `require_*` functions are the other end: a tool that was handed an
id looks the record up here, and a record that is not there is `NOT_FOUND` with
the kind and the key in it.

Looking a parent up before writing a child is not a belt-and-braces check. Foreign
keys are on, so an insert naming a missing parent fails -- but it fails as a
`DbError`, which the error handler turns into `INTERNAL`, and an agent told
"Something went wrong" after naming a project that does not exist has been told
nothing. One `SELECT` first turns that into the sentence it should be. The world
is single-writer per instance, so nothing can remove the parent between the check
and the insert.
"""

from collections.abc import Sequence
from typing import Any

import seahaven
from projecttracker.errors import Conflict, InvalidInput, NotFound

__all__ = [
    "COMMENT_COLUMNS",
    "ISSUE_COLUMNS",
    "ISSUE_COLUMNS_QUALIFIED",
    "ISSUE_FIELDS",
    "LABEL_COLUMNS",
    "MEMBER_COLUMNS",
    "PROJECT_COLUMNS",
    "TEAM_COLUMNS",
    "USER_COLUMNS",
    "attach_labels",
    "require_issue",
    "require_label",
    "require_project",
    "require_team",
    "require_user",
    "resolve_actor",
]

USER_COLUMNS = "id, email, name, role, created_at"
# `issue_counter` is not in the team's projection. It is the key counter, not a
# count of anything -- an archived issue still consumed its number -- so a team
# object carrying it would be read as "how many issues this team has" and be
# wrong. It is not a secret: `describe_schema` lists the column and `run_sql`
# reads it, which is right, because a SQL door shows the schema as it is.
TEAM_COLUMNS = "id, key, name, created_at"
MEMBER_COLUMNS = "team_id, user_id, joined_at"
PROJECT_COLUMNS = "id, team_id, name, state, created_at"

# An issue's columns as a tuple, because two statements need them two ways: a
# plain list for `SELECT ... FROM issues`, and one qualified with the table for
# the join `search_issues` makes against the FTS5 index. Derived from one place
# rather than re-derived by splitting the string, so a column added here reaches
# both.
ISSUE_FIELDS = (
    "id",
    "project_id",
    "key",
    "title",
    "description",
    "status",
    "priority",
    "assignee_id",
    "creator_id",
    "created_at",
    "updated_at",
    "due_at",
    "archived_at",
)
ISSUE_COLUMNS = ", ".join(ISSUE_FIELDS)
ISSUE_COLUMNS_QUALIFIED = ", ".join(f"issues.{field}" for field in ISSUE_FIELDS)
LABEL_COLUMNS = "id, team_id, name, color"
COMMENT_COLUMNS = "id, issue_id, author_id, body, created_at"


def require_user(ctx: seahaven.Ctx, user_id: str) -> dict[str, Any]:
    return _require(ctx, "user", user_id, f"SELECT {USER_COLUMNS} FROM users WHERE id = ?")


def require_team(ctx: seahaven.Ctx, team_id: str) -> dict[str, Any]:
    return _require(ctx, "team", team_id, f"SELECT {TEAM_COLUMNS} FROM teams WHERE id = ?")


def require_project(ctx: seahaven.Ctx, project_id: str) -> dict[str, Any]:
    return _require(
        ctx, "project", project_id, f"SELECT {PROJECT_COLUMNS} FROM projects WHERE id = ?"
    )


def require_label(ctx: seahaven.Ctx, label_id: str) -> dict[str, Any]:
    return _require(ctx, "label", label_id, f"SELECT {LABEL_COLUMNS} FROM labels WHERE id = ?")


def require_issue(ctx: seahaven.Ctx, issue_id: str) -> dict[str, Any]:
    """One issue by id, with its labels. An archived issue is still an issue.

    `archive_issue` hides an issue from `list_issues`; it does not delete it, and
    a tool that was given its id or its key answers with it. That is what makes
    archiving something an eval can grade: the row is still there, with the
    instant it happened on it.
    """
    issue = _require(ctx, "issue", issue_id, f"SELECT {ISSUE_COLUMNS} FROM issues WHERE id = ?")
    return attach_labels(ctx, [issue])[0]


def require_open_issue(ctx: seahaven.Ctx, issue_id: str) -> dict[str, Any]:
    """The issue, if it is one this product will still change.

    Archiving is final, so every tool that writes to an issue asks for it this
    way rather than through `require_issue`. Living here and not in `issues.py`
    because `set_issue_labels` is in `labels.py` and writes to an issue too: a
    rule that three of the four mutating tools import from the fourth is a rule
    the fifth will be written without.

    `add_comment` deliberately does not use it. Archiving freezes the issue, not
    the conversation about it, and `comments` is a table of its own.
    """
    issue = require_issue(ctx, issue_id)
    if issue["archived_at"] is not None:
        raise Conflict(f"issue {issue['key']} is archived and cannot be changed")
    return issue


def attach_labels(ctx: seahaven.Ctx, issues: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fill `label_ids` on every issue in a page, in one query rather than per row.

    An issue's labels are part of what an issue is, so a list that left them out
    would make the agent call a second tool per row to find out. Reading them one
    issue at a time would be that same round trip moved inside the world; this
    reads the whole page's labels once and hands each row its own.
    """
    rows = list(issues)
    if not rows:
        return rows
    placeholders = ", ".join("?" * len(rows))
    labels: dict[str, list[str]] = {issue["id"]: [] for issue in rows}
    for row in ctx.db.rows(
        f"SELECT issue_id, label_id FROM issue_labels WHERE issue_id IN ({placeholders})"
        " ORDER BY issue_id, label_id",
        *(issue["id"] for issue in rows),
    ):
        labels[row["issue_id"]].append(row["label_id"])
    return [{**issue, "label_ids": labels[issue["id"]]} for issue in rows]


def resolve_actor(ctx: seahaven.Ctx, actor_id: str | None) -> str:
    """Who is making this write: the `actor_id` given, else the instance's viewer.

    The viewer is what `reset(user_id=...)` put in `ctx.state` (`startup.py`), so
    an eval that says who it is driving the tracker as need not repeat it on every
    call, and one that drives several people names each of them per call. With
    neither, there is no one to attribute the write to and the product refuses --
    which is the case `empty` starts in, because a tracker with no users has no
    viewer to fall back to.
    """
    resolved = actor_id if actor_id is not None else ctx.state.get("viewer_id")
    if not resolved:
        raise InvalidInput("actor_id", "no actor")
    require_user(ctx, resolved)
    return str(resolved)


def _require(ctx: seahaven.Ctx, kind: str, key: str, sql: str) -> dict[str, Any]:
    row = ctx.db.one(sql, key)
    if row is None:
        raise NotFound(kind, key)
    return row
