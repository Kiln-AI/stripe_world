"""People: the workspace's users, and who may do what.

`role` is the whole of this product's permission model and nothing enforces it --
a `viewer` can create issues here. A world that mimicked the enforcement would be
mimicking a product's authorisation rules, which is a different world from this
one; what the column is for is that an eval can ask an agent to find the admins,
or to add someone with the right role, and grade the answer.
"""

import re
from typing import Any

import seahaven
from projecttracker.errors import Conflict, InvalidInput
from projecttracker.tools import _pagination, _rows
from projecttracker.tools._types import Cursor, Limit, Role
from projecttracker.world import world

__all__ = ["BY_CREATION", "create_user", "get_user", "list_users"]

# Deliberately loose, and the same rule a product of this kind actually applies:
# something, an `@`, something with a dot in it. An address this refuses is one no
# tracker would have accepted; an address it accepts may still bounce, which is
# not a tracker's business to know.
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

BY_CREATION = _pagination.Order(
    key="users:created_at_asc", column="created_at", id_column="id", descending=False
)


@world.tool
def create_user(ctx: seahaven.Ctx, email: str, name: str, role: Role = "member") -> dict[str, Any]:
    """Add a person to the workspace.

    The email address is the workspace's unique handle for them, so adding
    someone twice is refused rather than silently ignored.
    """
    if not _EMAIL.match(email):
        raise InvalidInput("email", f"{email!r} is not an email address")
    if ctx.db.one("SELECT id FROM users WHERE email = ?", email) is not None:
        # Checked rather than left to the UNIQUE index: a constraint failure is a
        # `DbError`, and the handler turns that into `INTERNAL`, which tells an
        # agent nothing about the address it should have reused.
        raise Conflict(f"a user with the email {email} is already in this workspace")
    user_id = ctx.ids.uuid()
    ctx.db.execute(
        "INSERT INTO users (id, email, name, role, created_at) VALUES (?, ?, ?, ?, ?)",
        user_id,
        email,
        name,
        role,
        ctx.clock.iso(),
    )
    return _rows.require_user(ctx, user_id)


@world.tool
def get_user(ctx: seahaven.Ctx, user_id: str) -> dict[str, Any]:
    """One person, by id."""
    return _rows.require_user(ctx, user_id)


@world.tool
def list_users(
    ctx: seahaven.Ctx,
    role: Role | None = None,
    limit: Limit = 50,
    cursor: Cursor | None = None,
) -> dict[str, Any]:
    """The workspace's people, oldest first, optionally of one role.

    A page at a time: `next_cursor` continues where this page stopped, and
    `has_next` says whether there is anything to continue to.
    """
    page = _pagination.page(
        ctx,
        select=f"SELECT {_rows.USER_COLUMNS} FROM users",
        where=() if role is None else ("role = ?",),
        params=() if role is None else (role,),
        order=BY_CREATION,
        limit=limit,
        cursor=cursor,
        sort_column="created_at",
    )
    return page.as_result("users")
