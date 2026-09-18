"""Who the tracker thinks is using it: the viewer, set once per instance.

A real tracker's API knows who the token belongs to, and every write it makes is
attributed to that person without the caller saying so. This world spells that as
a startup hook: `world.instance("agency", user_id=...)` -- or `reset(user_id=...)`
over OpenEnv -- names the person the session is driving the tracker as, and every
tool that writes falls back to them when the call passed no `actor_id`.

`actor_id` on the call is still there, and wins, because an eval that wants two
people writing in one episode should not need two instances to do it.

With no `user_id` the hook picks the workspace's first admin, which is what makes
"the viewer is the admin" true of the fixtures that have one
(`components/projecttracker.md` §4) without an eval having to look a generated id
up first. A workspace with no admin -- `empty`, or a fresh blank instance -- has no
viewer, and a write there must name its `actor_id` or be refused. That refusal is
`InvalidInput("actor_id", "no actor")`, raised in `tools/_rows.py` where the
fallback is read.
"""

import seahaven
from projecttracker.world import world

__all__ = ["remember_viewer"]


@world.instance_startup
def remember_viewer(ctx: seahaven.Ctx, *, user_id: str | None = None) -> None:
    """Put the session's viewer in `ctx.state`, or leave it unset.

    A `user_id` naming nobody is a `WorldBug` and not a product error: it comes
    from the eval's `reset`, not from the agent, and it is the same class of
    mistake as naming a fixture that does not exist -- which the framework also
    refuses before the instance is usable. Failing here aborts creation, so an
    episode never runs attributing its writes to a person who is not in the
    workspace.
    """
    if user_id is None:
        ctx.state["viewer_id"] = _first_admin(ctx)
        return
    if ctx.db.one("SELECT id FROM users WHERE id = ?", user_id) is None:
        raise seahaven.WorldBug(
            f"reset(user_id={user_id!r}) names nobody in this workspace; pass the id of a user "
            f"the fixture has, or omit it to drive the tracker as its first admin"
        )
    ctx.state["viewer_id"] = user_id


def _first_admin(ctx: seahaven.Ctx) -> str | None:
    """The earliest-created admin, or `None` in a workspace that has none.

    Ordered by `id` as well as by `created_at` so that two admins created in the
    same instant -- which a bulk-loaded fixture makes easy -- still resolve to the
    same one on every run.
    """
    row = ctx.db.one("SELECT id FROM users WHERE role = 'admin' ORDER BY created_at, id LIMIT 1")
    return None if row is None else str(row["id"])
