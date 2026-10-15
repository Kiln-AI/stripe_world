"""Instance startup: builds `ctx.state["account"]` once per instance.

Every downstream reader — `_ids` for the account fragment, `_context` for
validation, the account tools for their two faces, the serializer for
`livemode`, the invoice's account fields, the ledger and dunning — reads this
state. The hook runs before the first call, so the state is always present
when a tool body executes.

The account itself is the one row of the `account` table
(`schema/006_account.sql`), so a fixture carries its own account the way it
carries every other row. The schema seeds the fresh sandbox account this world
has always served. The row also records the account's **mode**, set when the
account is created and kept: a fixture's data (its cards, the `livemode` inside
stored JSON) is coupled to the mode it was made in (company_fixtures functional
spec §2.1).
"""

import re
from typing import Any, cast

import seahaven

from seahaven_stripe_world import _json
from seahaven_stripe_world.world import world

__all__ = ["ACCOUNT_ID", "open_account"]

# The sandbox account `006_account.sql` seeds: `acct_` + 16 alphanumerics.
# Real Stripe ids use this shape; the old human-readable constant was a tell.
ACCOUNT_ID = "acct_0NGvb3Cz7hWRKLEp"

_ACCOUNT_ID = re.compile(r"acct_[A-Za-z0-9]{16}")

# The account-level overrides the world reads from `ctx.state["account"]`:
# `ledger.ledger_spec` and `dunning.policy`.
_SETTINGS_KEYS = frozenset({"ledger", "dunning"})


def open_account(ctx: seahaven.Ctx, *, livemode: bool | None) -> dict[str, Any]:
    """Read the account row into the `ctx.state["account"]` shape, settling its mode.

    A row with no mode yet (a blank instance) takes `livemode`, live when it
    is omitted, and is stamped with it. A row with a mode keeps it, and
    refuses a `livemode` that asks for the other one. Anything but exactly one
    well-formed row is an instance that cannot start.

    Returns a new, plain dict on every call: callers (tests among them) mutate
    `ctx.state["account"]["dunning"]` in place.
    """
    rows = ctx.db.rows("SELECT id, livemode, object, settings FROM account")
    if not rows:
        raise seahaven.WorldBug(
            "the account table is empty: an instance needs exactly one account row"
        )
    if len(rows) > 1:
        raise seahaven.WorldBug(
            f"the account table holds {len(rows)} rows: an instance is exactly one account"
        )
    row = rows[0]
    account_id = str(row["id"])
    if not _ACCOUNT_ID.fullmatch(account_id):
        raise seahaven.WorldBug(f"account id {account_id!r} is not acct_ plus 16 alphanumerics")
    # The table's CHECKs guarantee both columns hold a JSON object.
    obj = cast("dict[str, Any]", _json.loads(row["object"]))
    settings = cast("dict[str, Any]", _json.loads(row["settings"]))
    if obj.get("id") != account_id:
        raise seahaven.WorldBug(f"account row {account_id!r} holds the object of {obj.get('id')!r}")
    for key in sorted(settings):
        if key not in _SETTINGS_KEYS:
            raise seahaven.WorldBug(
                f"account settings key {key!r} is not one of {sorted(_SETTINGS_KEYS)}"
            )

    stored = row["livemode"]
    if stored is None:
        mode = livemode if livemode is not None else True
        ctx.db.execute("UPDATE account SET livemode = ? WHERE id = ?", int(mode), account_id)
    else:
        mode = bool(stored)
        if livemode is not None and livemode != mode:
            raise seahaven.WorldBug(
                f"account {account_id} holds {'live' if mode else 'test'}-mode data and cannot "
                f"be opened with livemode={livemode}; omit livemode, or open a blank instance"
            )

    state: dict[str, Any] = {
        "id": account_id,
        "livemode": mode,
        "name": _field(obj, account_id, "settings", "dashboard", "display_name"),
        "country": _field(obj, account_id, "country"),
        "default_currency": _field(obj, account_id, "default_currency"),
        "object": obj,
    }
    state.update(settings)
    return state


def _field(obj: dict[str, Any], account_id: str, *path: str) -> Any:
    """The account object's field at `path`, which must be present (its value may be null)."""
    value: Any = obj
    for key in path:
        if not isinstance(value, dict) or key not in value:
            raise seahaven.WorldBug(f"account {account_id} object has no {'.'.join(path)}")
        value = value[key]
    return value


@world.instance_startup
def _startup(ctx: seahaven.Ctx, *, livemode: bool | None = None, **_kwargs: object) -> None:
    """Build `ctx.state["account"]` from the account row.

    `livemode` chooses the mode of an account that has none yet (a blank
    instance), and must agree with the mode of one that has (any fixture);
    omitted, it is live for a blank instance and the row's mode otherwise.
    Extra kwargs are accepted and ignored so that other startup parameters
    (from extensions or harnesses) do not cause errors.
    """
    ctx.state["account"] = open_account(ctx, livemode=livemode)
