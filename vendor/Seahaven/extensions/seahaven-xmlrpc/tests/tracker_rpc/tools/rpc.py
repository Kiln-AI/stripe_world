"""The XML-RPC endpoint: one tool, and every method of this world behind it.

The tool registry knows `rpc` and knows nothing about the five methods inside
it, because the protocol's namespace is the protocol's business. `name="rpc"` is
this world choosing the name -- the factory's default is `xmlrpc_call` -- and the
same name is what `middleware/faults.py` covers; a world that renames the tool
renames it in both places or the faults go unrendered.
"""

import seahaven_xmlrpc
from tracker_rpc.methods import METHODS
from tracker_rpc.world import world

__all__ = ["rpc"]

rpc = world.tool(
    seahaven_xmlrpc.xmlrpc_call(
        methods=METHODS,
        name="rpc",
        # The call log needs `CALL_LOG_DDL` in the schema, which `world.py` put
        # there.
        log_calls=True,
    )
)
