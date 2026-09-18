"""The fault vocabulary: the codes, the error, and the document a fault is sent as.

XML-RPC has no error channel of its own. A method that fails answers with an
ordinary response whose body is a `<fault>` struct of two members, `faultCode`
and `faultString`, and the client raises from that. So a fault here is two
things at once -- an exception while it is travelling up through Seahaven, and a
document by the time it reaches the caller -- and both live in this module.

`XmlRpcFault` subclasses `ToolError`, which `functional_spec.md` §21 permits. It
is worth doing rather than inventing an exception of this package's own: a world
that registers the tool and forgets the middleware still hands its agent an error
with a code and a message, instead of an exception nothing in the chain knows.
"""

import xmlrpc.client

import seahaven

__all__ = [
    "APPLICATION_ERROR",
    "INTERNAL_ERROR",
    "INVALID_PARAMS",
    "INVALID_REQUEST",
    "METHOD_NOT_FOUND",
    "PARSE_ERROR",
    "TOOL_ERROR_CODE",
    "XmlRpcFault",
    "fault_document",
]

# The interoperability codes: the set XML-RPC implementations agreed on for the
# failures the *protocol* has, as against the failures an application has. A
# world's own methods raise their own codes, and positive ones by convention --
# which is what a real XML-RPC product does, and what `tests/tracker_rpc` shows.
#
# Published as the set, not as the subset this package happens to raise: a
# world's own methods reach for INTERNAL_ERROR and APPLICATION_ERROR when they
# decide a failure is the server's, and should not have to write `-32603` to say
# so. The two codes a *transport* has (-32400, -32300) are left out: there is no
# transport here.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
APPLICATION_ERROR = -32500

# `ToolError.code` for every fault. The agent-facing vocabulary of a world that
# speaks XML-RPC is `faultCode`, which is in `details`; this is the one code the
# framework's own machinery sees, and it says which door the failure came out of.
TOOL_ERROR_CODE = "xmlrpc_fault"


class XmlRpcFault(seahaven.ToolError):
    """A fault, on its way out of the tool and before it is a document.

    `details` carries the two members under the names the wire uses, so the fault
    is readable even on the path where it is never rendered -- a world that
    registered the tool without `render_faults`, or a caller reading the
    exception in-process.

    Over OpenEnv, `details` is part of the Seahaven triple and travels in the
    observation's `metadata["seahaven_error"]`, beside an `error` field carrying
    OpenEnv's own two-key shape. A harness that reads a `faultCode` off an
    observation reads it there.
    """

    def __init__(self, fault_code: int, fault_string: str) -> None:
        super().__init__(
            TOOL_ERROR_CODE,
            fault_string,
            {"faultCode": fault_code, "faultString": fault_string},
        )
        self.fault_code = fault_code
        self.fault_string = fault_string

    def document(self) -> str:
        """This fault as the `<methodResponse>` the caller reads."""
        return fault_document(self.fault_code, self.fault_string)


def fault_document(fault_code: int, fault_string: str) -> str:
    """A `<methodResponse>` carrying a fault.

    A fault's two members are an int and a string, so there is no `allow_none`
    here as there is on the response side: `<nil/>` cannot arise. What matters is
    that the document is rendered by `xmlrpc.client` and not by string building --
    the escaping of a `faultString` holding `<` or `&` is the standard library's
    problem, and a message built from an engine's error text will hold both
    sooner or later.
    """
    return xmlrpc.client.dumps(xmlrpc.client.Fault(fault_code, fault_string), methodresponse=True)
