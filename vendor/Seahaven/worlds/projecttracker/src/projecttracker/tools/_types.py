"""The argument types every tool module spells its parameters with.

A tool's signature is its published contract: the framework builds the argument
model and the JSON schema from it, so a type named here is what an agent reads in
the tool list and what a call is validated against. Declaring them once means the
five places that take a status take the same five strings, and a sixth cannot
appear in one tool and not another.

The `Literal`s mirror the schema's `CHECK` constraints on purpose, and the
duplication is the point: the `CHECK` is what the database will not store, and
the `Literal` is what the agent is told before it tries. A world that had only
the constraint would answer a bad status with `INTERNAL` after a failed write,
which says nothing about the five words that would have worked.

They are plain assignments and not `type` statements. A PEP 695 alias is a named
object, and pydantic publishes it as a `$def` with a `$ref` pointing at it; a
plain alias is substituted, so the tool list an agent reads carries the five
statuses where the argument is rather than a reference to look up.
"""

from typing import Annotated, Literal

from pydantic import Field

__all__ = [
    "CLOSED_STATUSES",
    "TABLES",
    "Colour",
    "Cursor",
    "EventKind",
    "IssueOrder",
    "IssueStatus",
    "Limit",
    "Priority",
    "ProjectState",
    "Role",
    "TeamKey",
    "Timestamp",
]

Role = Literal["admin", "member", "viewer"]
ProjectState = Literal["planned", "active", "done"]
IssueStatus = Literal["backlog", "todo", "in_progress", "done", "canceled"]
Priority = Literal[0, 1, 2, 3, 4]

# The orderings `list_issues` offers. Each is a column of `issues` with an index
# on it, because a keyset page is a scan of `(column, id)` and an ordering with no
# index behind it is a sort of the whole table per page.
IssueOrder = Literal["created_at_desc", "created_at_asc", "updated_at_desc", "updated_at_asc"]

# What an issue's status means for its assignee: this product drops the assignee
# when an issue closes, so these two are named once and read by every tool that
# can change a status.
CLOSED_STATUSES = frozenset({"done", "canceled"})

# The kinds an `issue_events` row can have, as the schema's CHECK spells them.
EventKind = Literal["created", "status", "assignee", "comment"]

# The canonical timestamp: what `ctx.clock.iso()` writes and what every timestamp
# column of this world holds. An argument typed this way is rejected before the
# tool runs if it is in any other format, which is what keeps a comparison against
# a stored value a text comparison that means what it says.
Timestamp = Annotated[
    str,
    Field(
        pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$",
        description="A UTC timestamp with milliseconds, e.g. 2026-06-01T09:00:00.000Z.",
    ),
]

# A page size. The ceiling is the product's, not SQLite's: an agent that asks for
# everything gets a page and a cursor, which is the shape every list here has.
Limit = Annotated[int, Field(ge=1, le=250, description="How many rows to return, 1 to 250.")]

# An opaque page token. Deliberately a bare `str`: its contents are this world's
# business (`tools/_pagination.py`), and a pattern here would publish them.
Cursor = str

# A team key as the product spells it: two to five capitals, `ENG`, `DES`, `OPS`.
TeamKey = Annotated[
    str,
    Field(
        min_length=2,
        max_length=5,
        pattern=r"^[A-Z]+$",
        description="A team key: two to five capital letters, e.g. ENG.",
    ),
]

# A colour as this product stores it: `#rrggbb`, lower case.
Colour = Annotated[
    str,
    Field(pattern=r"^#[0-9a-f]{6}$", description="A hex colour, lower case, e.g. #3b82f6."),
]

# The nine tables the SQL door and its companion are registered over: every table
# this world owns, and not the FTS5 index, which is reached through
# `search_issues` rather than through SQL an agent wrote.
TABLES = (
    "users",
    "teams",
    "team_members",
    "projects",
    "issues",
    "labels",
    "issue_labels",
    "comments",
    "issue_events",
)
