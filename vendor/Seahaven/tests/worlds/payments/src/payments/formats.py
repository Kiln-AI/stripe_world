"""A state format of this world's own: the total charged, and nothing else.

Registered on `payments`, which is a *leaf* of the `emporium` tree. That is what
it is here for: a format resolves against the root of an instance, so this one
serves `payments.world.instance(...)` and is not reachable from a root that adds
this world.
"""

from typing import Any

import seahaven
from payments.world import world

__all__ = ["charged"]


@world.state_format("payments.state/1")
def charged(world: seahaven.World, instance: seahaven.Instance | None) -> dict[str, Any]:
    """How much this account has charged, counted from the change log."""
    records = instance.change_log() if instance is not None else []
    return {
        "charged": sum(
            record.after["amount"]
            for record in records
            if record.table == "charges" and record.op == "insert" and record.after is not None
        )
    }
