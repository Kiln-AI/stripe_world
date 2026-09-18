"""A tool module nobody imports, so nothing in it is ever registered.

The failure this rule exists for: the world runs, the tool is simply not there,
and the first sign of it is `unknown_tool` from an eval weeks later.
"""

import seahaven
from messy.world import world

__all__ = ["never_registered"]


def never_registered(ctx: seahaven.Ctx) -> str:
    """Would be a tool if this module were imported."""
    return world.name
