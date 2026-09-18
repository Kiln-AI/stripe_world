"""This world's XML-RPC methods: ordinary functions over the `users` table.

A handler is `(ctx, *params) -> value`. It takes the instance context first, like
every other function in a Seahaven world, and the document's parameters after it;
nothing about it is XML-RPC-shaped, which is the whole claim the extension makes.

Faults raised here carry this product's own codes -- positive, three digits,
mirroring the HTTP status the same tracker's REST API returns -- and never the
interoperability codes in `seahaven_xmlrpc.faults`, which are the protocol's and
belong to failures of the protocol. A real XML-RPC product's fault codes are its
own vocabulary in exactly this way.
"""

from typing import Any

import seahaven
from seahaven_xmlrpc import XmlRpcFault

__all__ = ["METHODS", "NOT_FOUND"]

# This product's fault code for "you named a record that does not exist".
NOT_FOUND = 404

_COLUMNS = "id, email, name, role, created_at"


def ping(ctx: seahaven.Ctx, value: Any) -> Any:
    """Answer with whatever was sent, to prove the endpoint is reachable.

    The tracker's connectivity check, and the one method whose parameter is not a
    string: an XML-RPC client sends an int, a double, a boolean, an array or a
    struct through it and reads the same value back, which is what makes it the
    method a round-trip test drives.
    """
    return value


def list_methods(ctx: seahaven.Ctx) -> list[str]:
    """The methods this server serves, which XML-RPC clients ask for by name."""
    return sorted(METHODS)


def create_user(ctx: seahaven.Ctx, email: str, name: str, role: str) -> dict[str, Any]:
    """Add a user and answer with the row as it was written.

    Nothing here validates `email` or `role`: the schema does, with a `UNIQUE`
    and a `CHECK`, and what SQLite refuses arrives as a `DbError` -- which is the
    failure `render_faults` exists to turn into a fault, and the reason the write
    this method did before it failed must roll back.
    """
    user_id = ctx.ids.uuid()
    ctx.db.execute(
        f"INSERT INTO users ({_COLUMNS}) VALUES (?, ?, ?, ?, ?)",
        user_id,
        email,
        name,
        role,
        ctx.clock.iso(),
    )
    return get_user(ctx, user_id)


def get_user(ctx: seahaven.Ctx, user_id: str) -> dict[str, Any]:
    """One user, or this product's `404` fault."""
    row = ctx.db.one(f"SELECT {_COLUMNS} FROM users WHERE id = ?", user_id)
    if row is None:
        raise XmlRpcFault(NOT_FOUND, f"no such user: {user_id}")
    return row


def list_users(ctx: seahaven.Ctx) -> list[dict[str, Any]]:
    """Every user, oldest first, then by id so the order is total."""
    return ctx.db.rows(f"SELECT {_COLUMNS} FROM users ORDER BY created_at, id")


METHODS = {
    "system.listMethods": list_methods,
    "tracker.ping": ping,
    "user.create": create_user,
    "user.get": get_user,
    "user.list": list_users,
}
