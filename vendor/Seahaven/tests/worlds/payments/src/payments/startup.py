"""The one thing a host configures per account: which region it is."""

import seahaven
from payments.world import world

__all__ = ["record_the_region"]


@world.instance_startup
def record_the_region(ctx: seahaven.Ctx, *, region: str = "us") -> None:
    """Remember the account family this store belongs to."""
    ctx.state["region"] = region
