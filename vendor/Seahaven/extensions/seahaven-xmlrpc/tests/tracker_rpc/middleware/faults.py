"""The XML-RPC response layer, inside the product's error handler.

`render_faults` catches what the endpoint raises and answers with the document a
client reads. It must be registered *after* `error_handler` -- outermost first --
or the product's handler would restate the `DbError` as `INTERNAL` before this
layer ever saw it.
"""

import seahaven_xmlrpc
from tracker_rpc.world import world

__all__ = ["faults"]

faults = world.middleware(seahaven_xmlrpc.render_faults(tools=["rpc"]))
