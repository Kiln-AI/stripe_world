"""A copy of ProjectTracker that speaks XML-RPC: the world this extension is tested on.

Importing this package is what builds the world. `world.py` constructs the
`World`; importing `startup`, `middleware` and `tools` runs the registrations
inside them, which is why they are imported here for their side effects and not
for their names.

The tooling finds a world by importing its package and reading `world` off it, so
that attribute is this package's whole public surface.
"""

from tracker_rpc import middleware, startup, tools
from tracker_rpc.world import world

__all__ = ["middleware", "startup", "tools", "world"]
