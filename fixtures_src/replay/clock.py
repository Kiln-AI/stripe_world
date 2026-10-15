"""The historical clock: a context reading one fixed instant (replay.md §1)."""

import dataclasses
from datetime import datetime, timedelta

import seahaven

__all__ = ["at", "plus"]


def at(ctx: seahaven.Ctx, iso: str) -> seahaven.Ctx:
    """`ctx` with a fixed clock reading `iso`: same db, ids, state, instance and worlds.

    World code reads time only through `ctx.clock`, so every row, id time-group and event written
    through the result carries `iso`. Seahaven derives call contexts the same way
    (`Ctx.with_call` is a `dataclasses.replace`).
    """
    return dataclasses.replace(ctx, clock=seahaven.Clock.from_iso(iso))


def plus(
    iso: str, *, days: float = 0, hours: float = 0, minutes: float = 0, seconds: float = 0
) -> str:
    """`iso` shifted, rendered canonically. Calendar months and years come from the world's
    `invoicing.add_interval`, never from here."""
    moment = datetime.fromisoformat(iso) + timedelta(
        days=days, hours=hours, minutes=minutes, seconds=seconds
    )
    return seahaven.Clock(moment).iso()
