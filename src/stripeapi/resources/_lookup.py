"""Parent lookups: a missing parent is Stripe's 404, never an engine error.

Every handler that writes a child row `SELECT`s its parent first
(`architecture.md` §7): a bare foreign-key violation would surface as a
`DbError` and reach the agent as `INTERNAL`, instead of the actionable 404
Stripe sends. This module is that family, plus `subject`, the one-line helper
the eight legacy alias handlers use to read their subject id under whichever
placeholder name the pattern spelled (`components/dispatcher.md` §3.1.3).

It deliberately takes a table and an object name rather than a `ResourceSpec`:
sitting below the engine in the import order is what keeps the cycle away.
"""

from collections.abc import Mapping
from typing import Any

import seahaven

from stripeapi.stripe_errors import resource_missing

__all__ = ["require_live_row", "require_row", "subject"]


def require_row(
    ctx: seahaven.Ctx,
    table: str,
    object_name: str,
    id: str,
    *,
    param: str,
    status: int = 404,
) -> dict[str, Any]:
    """One row of `table` by primary key, or Stripe's `resource_missing`.

    The probed message shape: `No such customer: 'cus_…'`, no trailing period.
    `status` distinguishes where the id came from — 404 for a path id, 400
    for a request parameter (both probed at the pinned version; see
    `dispatch/params.py::_check`).
    """
    row = ctx.db.one(f"SELECT * FROM {table} WHERE id = ?", id)
    if row is None:
        raise resource_missing(object_name, id, param=param, status=status)
    return row


def require_live_row(
    ctx: seahaven.Ctx,
    table: str,
    object_name: str,
    id: str,
    *,
    param: str,
    status: int = 400,
) -> dict[str, Any]:
    """`require_row`, refusing a soft-deleted row as missing.

    A request-parameter id names an object that must still exist: a
    tombstoned customer is as missing as an absent one (probed on the
    payment-methods customer filter and on attach-after-delete, cassette 06),
    unlike a path id where the deleted row still resolves
    (`components/cross_cutting.md` §3.2.4). Tables without a `deleted`
    column — the majority, since only soft-delete resources carry one — pass
    through untouched.
    """
    row = require_row(ctx, table, object_name, id, param=param, status=status)
    if row.get("deleted") == 1:
        raise resource_missing(object_name, id, param=param, status=status)
    return row


def subject(req: Mapping[str, str], *names: str) -> str:
    """The id an alias handler reads under its pattern's own placeholder name.

    `subscriptions.update` looks for `subscription_exposed_id` or
    `subscription`, whichever the matched pattern used; a miss is a `WorldBug`
    because the route table — not the agent — supplies the names.
    """
    for name in names:
        if name in req:
            return req[name]
    raise seahaven.WorldBug(f"none of {names!r} present in the path parameters")
