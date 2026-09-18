"""A middleware that times a call on the machine's clock, which is allowed.

SH201 exempts `middleware/`: measuring how long a call took is a real wall clock
doing a real job, and it never reaches the instance's data.
"""

import time
from typing import Any

import seahaven
from messy.world import world
from seahaven.world import Handler

__all__ = ["timing"]


@world.middleware
def timing(ctx: seahaven.Ctx, call: seahaven.Call, next_: Handler) -> Any:
    """Record how long the rest of the chain took."""
    started = time.perf_counter()
    try:
        return next_(ctx, call)
    finally:
        ctx.state["last_call_seconds"] = time.perf_counter() - started
