"""stripeapi: a Seahaven world.

Importing this package is what builds the world. `world.py` constructs the
`World`; importing `middleware` and `tools` runs the registrations inside them,
which is why they are imported here for their side effects and not for their
names. Registration order is chain order, outermost first, so the error handler
-- the first thing `middleware` registers -- wraps everything.

The tooling finds a world by importing its package and reading `world` off it,
so that attribute is this package's whole public surface.
"""

from stripeapi import middleware, tools
from stripeapi.world import world

__all__ = ["middleware", "tools", "world"]
