"""ProjectTracker's own error handler, registered on the copy.

Imported rather than retyped: what the extension has to work under is the
reference world's real behaviour -- framework errors restated in the product's
words, `WorldBug` re-raised, anything unexpected becoming `INTERNAL` -- and a
handler written here to agree with the tests would prove nothing.

Registered first, so it is the outermost layer and every fault the endpoint
renders has already passed `middleware/faults.py` on the way up.
"""

from projecttracker.middleware.error_handler import error_handler
from tracker_rpc.world import world

__all__ = ["error_handler"]

world.middleware(error_handler)
