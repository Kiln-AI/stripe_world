"""What runs once per instance, before its first call.

`remember_client` is the extension's hook, registered by this world -- the
framework has no idea it exists, and `functional_spec.md` §21 point 3 is exactly
this: an extension exports a startup hook, the world registers it, and the
`reset()` keyword arguments it names reach it.

    world.instance("empty", xmlrpc_client="acme-crm/2.4")
"""

import seahaven_xmlrpc
from tracker_rpc.world import world

__all__ = ["remember_client"]

remember_client = world.instance_startup(seahaven_xmlrpc.remember_client)
