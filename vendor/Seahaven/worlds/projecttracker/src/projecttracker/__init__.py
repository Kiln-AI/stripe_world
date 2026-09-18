"""ProjectTracker: a fictional issue tracker, and Seahaven's reference world.

Importing this package is what builds the world. `world.py` constructs the
`World`; importing `startup`, `middleware` and `tools` runs the registrations
inside them, which is why they are imported here for their side effects and not
for their names. Registration order is chain order, outermost first, so the error
handler -- the first thing `middleware` registers -- wraps everything.

The tooling finds a world by importing its package and reading `world` off it,
so that attribute is this package's whole public surface.
"""

from projecttracker import middleware, startup, tools
from projecttracker.world import world

__all__ = ["middleware", "startup", "tools", "world"]
