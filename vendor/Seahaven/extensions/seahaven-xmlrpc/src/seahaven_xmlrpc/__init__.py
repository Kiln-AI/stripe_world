"""XML-RPC for Seahaven worlds, and Seahaven's worked example of an extension.

A world whose real product speaks XML-RPC registers two things from this package
and writes its methods as ordinary functions:

```python
import seahaven
import seahaven_xmlrpc
from myworld.world import world

world.tool(seahaven_xmlrpc.xmlrpc_call(methods=METHODS, name="rpc", log_calls=True))
world.middleware(error_handler)                              # the world's own, outermost
world.middleware(seahaven_xmlrpc.render_faults(tools=["rpc"]))
world.instance_startup(seahaven_xmlrpc.remember_client)
```

with `schema=seahaven.sql_files(__package__, "schema") + seahaven_xmlrpc.CALL_LOG_DDL`
if it wants the call log, and a handler that looks like every other function in a
world:

```python
def create_user(ctx: seahaven.Ctx, email: str, name: str) -> dict[str, object]:
    ...
```

Nothing in this package is privileged. It depends on `seahaven`, uses the public
API -- `Tool.from_function`, the middleware shape, instance startup, `ToolError`,
DDL as text -- and Seahaven never imports it: `functional_spec.md` §21 is the
whole of the contract, and this package is the proof that the contract is enough
to carry a protocol the framework has never heard of.

Read it in this order: `faults.py` (what a failure is here), `documents.py` (the
wire), `tool.py` (the endpoint), `middleware.py` (why rendering is not in the
tool), `call_log.py` (the two seams the endpoint does not need).
"""

from seahaven_xmlrpc.call_log import (
    CALL_LOG_DDL,
    CALL_LOG_TABLE,
    CLIENT_STATE_KEY,
    UNKNOWN_CLIENT,
    remember_client,
)
from seahaven_xmlrpc.documents import parse_method_call, render_response
from seahaven_xmlrpc.faults import (
    APPLICATION_ERROR,
    INTERNAL_ERROR,
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    TOOL_ERROR_CODE,
    XmlRpcFault,
    fault_document,
)
from seahaven_xmlrpc.middleware import render_faults
from seahaven_xmlrpc.tool import MAX_BODY, MethodHandler, xmlrpc_call

__all__ = [
    "APPLICATION_ERROR",
    "CALL_LOG_DDL",
    "CALL_LOG_TABLE",
    "CLIENT_STATE_KEY",
    "INTERNAL_ERROR",
    "INVALID_PARAMS",
    "INVALID_REQUEST",
    "MAX_BODY",
    "METHOD_NOT_FOUND",
    "PARSE_ERROR",
    "TOOL_ERROR_CODE",
    "UNKNOWN_CLIENT",
    "MethodHandler",
    "XmlRpcFault",
    "fault_document",
    "parse_method_call",
    "remember_client",
    "render_faults",
    "render_response",
    "xmlrpc_call",
]
