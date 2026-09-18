"""Labels, and what carries them.

A label belongs to a team, so two teams can both have a `bug` and they are two
labels. An issue's labels are set as a whole rather than added and removed one at
a time: the product's UI is a picker, `set_issue_labels` is that picker, and an
agent that wants to add one reads the issue's `label_ids` and sends them back with
one more.
"""

from typing import Any

import seahaven
from projecttracker.errors import Conflict, InvalidInput
from projecttracker.tools import _pagination, _rows
from projecttracker.tools._types import Colour, Cursor, Limit
from projecttracker.world import world

__all__ = ["BY_NAME", "create_label", "list_labels", "set_issue_labels"]

# Labels are a picker, and a picker is alphabetical: nobody looks for a label by
# when it was made. The keyset's second half is still the id, because two teams'
# labels can share a name.
BY_NAME = _pagination.Order(key="labels:name_asc", column="name", id_column="id", descending=False)


@world.tool
def create_label(ctx: seahaven.Ctx, team_id: str, name: str, color: Colour) -> dict[str, Any]:
    """Add a label to a team's set."""
    _rows.require_team(ctx, team_id)
    if (
        ctx.db.one("SELECT id FROM labels WHERE team_id = ? AND name = ?", team_id, name)
        is not None
    ):
        raise Conflict(f"team {team_id} already has a label called {name}")
    label_id = ctx.ids.uuid()
    ctx.db.execute(
        "INSERT INTO labels (id, team_id, name, color) VALUES (?, ?, ?, ?)",
        label_id,
        team_id,
        name,
        color,
    )
    return _rows.require_label(ctx, label_id)


@world.tool
def list_labels(
    ctx: seahaven.Ctx, team_id: str, limit: Limit = 50, cursor: Cursor | None = None
) -> dict[str, Any]:
    """A team's labels, by name."""
    _rows.require_team(ctx, team_id)
    return _pagination.page(
        ctx,
        select=f"SELECT {_rows.LABEL_COLUMNS} FROM labels",
        where=("team_id = ?",),
        params=(team_id,),
        order=BY_NAME,
        limit=limit,
        cursor=cursor,
        sort_column="name",
    ).as_result("labels")


@world.tool
def set_issue_labels(ctx: seahaven.Ctx, issue_id: str, label_ids: list[str]) -> dict[str, Any]:
    """Replace an issue's labels with exactly this set.

    The whole set, so an empty list clears them. Every label is checked before
    anything is written, and the call runs in one transaction, so an unknown id
    leaves the issue's labels as they were rather than half replaced.

    Every label has to belong to the issue's own team. A label is a team's, so a
    `bug` in `DES` and a `bug` in `ENG` are two labels, and an issue wearing the
    other team's would make `list_issues(label_ids=...)` answer across a boundary
    the product says is there.

    Labelling does not stamp the issue's `updated_at` and writes no event: the
    four event kinds this product records are creation, status, assignee and
    comment, and in this tracker a label is a property of the issue's filing
    rather than a thing that happened to it. An archived issue keeps its labels
    as it keeps its other fields: this tool refuses one, as every other tool that
    writes to an issue does.
    """
    _rows.require_open_issue(ctx, issue_id)
    team_id = _team_of_issue(ctx, issue_id)
    # Duplicates in the argument are one label: the set is a set, and the primary
    # key on `issue_labels` would refuse the second row anyway.
    wanted = sorted(set(label_ids))
    for label_id in wanted:
        label = _rows.require_label(ctx, label_id)
        if label["team_id"] != team_id:
            # By id and then by name: the agent sent ids, and two teams can hold
            # the same name -- which is the whole reason labels are team-scoped --
            # so a message naming only the name cannot say which id was wrong.
            raise InvalidInput(
                "label_ids",
                f"label {label_id} ({label['name']}) belongs to another team; an issue can "
                f"only carry its own team's labels",
            )
    ctx.db.execute("DELETE FROM issue_labels WHERE issue_id = ?", issue_id)
    ctx.db.executemany(
        "INSERT INTO issue_labels (issue_id, label_id) VALUES (?, ?)",
        [(issue_id, label_id) for label_id in wanted],
    )
    return {"issue_id": issue_id, "label_ids": wanted}


def _team_of_issue(ctx: seahaven.Ctx, issue_id: str) -> Any:
    """Which team owns an issue: its project's, since an issue has no team of its own."""
    row = ctx.db.one(
        "SELECT projects.team_id AS team_id FROM issues"
        " JOIN projects ON projects.id = issues.project_id WHERE issues.id = ?",
        issue_id,
    )
    # The issue was read by the caller a line earlier and every issue has a
    # project by foreign key; the assertion states that for the type checker.
    assert row is not None
    return row["team_id"]
