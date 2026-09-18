"""The two directions of the wire: a `<methodCall>` in, a `<methodResponse>` out.

`xmlrpc.client` does the XML in both directions. What this module adds is the
part a client library has no reason to have: a server's judgement about a
document someone else wrote. Which failures are the *caller's* -- a fault with a
code -- and which are the *world's* -- a `WorldBug` for its author -- is decided
here and nowhere else.
"""

import xmlrpc.client
from typing import Any

import seahaven
from seahaven_xmlrpc.faults import INTERNAL_ERROR, INVALID_REQUEST, PARSE_ERROR, XmlRpcFault

__all__ = ["parse_method_call", "render_response"]

# A document type declaration is refused before anything parses it. `xmlrpc.client`
# is expat with entity expansion left on, so a document declaring an entity in
# terms of nine copies of another entity, ten deep, expands to a gigabyte inside
# the parser -- and the document here was written by the agent under test, in a
# process running hundreds of other instances. No XML-RPC request needs a DOCTYPE:
# the protocol's whole grammar is elements, and every implementation sends none.
#
# The check is on the literal, because XML spells markup declarations in exactly
# one case. `<!ENTITY` needs no check of its own: an entity declaration is only
# legal inside a DOCTYPE, and expat rejects one outside it as malformed.
#
# It is a text search over the whole document, so a document that carries the
# characters `<!DOCTYPE` anywhere is refused. That is a false positive, and a
# narrow one: a conforming client escapes `<` to `&lt;`, so an agent sending a
# snippet of XML as a *parameter* passes cleanly, and what is left to trip on is
# a CDATA section or a document built by hand. It is the cheap side of the trade
# either way -- the alternative is to parse first and decide afterwards, which is
# the parse this guard exists to prevent.
#
# The other XML bomb, a deeply nested document, is not guarded here and does not
# need to be: expat and `Unmarshaller` are both iterative, so parsing one costs
# its size and no more. It resurfaces on the way *out*, where `dumps` recurses,
# and `render_response` answers it there.
_DOCTYPE = "<!DOCTYPE"

# The leaf values `_check_values` lets through, beside the three it decides for
# itself (`None`, `bool`, `int`) and the two it walks into (`list`, `dict`).
# `<string>`, `<double>`, `<dateTime.iso8601>` and `<base64>` are the rest of
# what XML-RPC carries, and `Marshaller.dispatch` has an entry for each. Anything
# else a parse produces is refused here rather than on the way out.
_CARRIED = (str, float, xmlrpc.client.DateTime, xmlrpc.client.Binary)


def parse_method_call(body: str, *, allow_none: bool = False) -> tuple[str, tuple[Any, ...]]:
    """The method and its parameters, or the fault the caller should read.

    Every way a document can be unusable is one of two faults. `PARSE_ERROR` is
    "I could not read this": malformed XML, a value that is not the type its tag
    claims, a DOCTYPE. `INVALID_REQUEST` is "I read it and it is not a call":
    a `<methodResponse>` sent to a server, or a `<methodCall>` with no
    `<methodName>` in it -- which `xmlrpc.client` reports by returning `None` for
    the name rather than by raising, because a client parsing a response never
    expects one.

    `allow_none` is the endpoint's, and is asked here rather than only on the way
    out because the two sides of `xmlrpc.client` do not agree about what XML-RPC
    carries: `loads` takes no `allow_none` at all and unmarshals `<nil/>` to
    `None` whatever the server thinks, and it converts `<int>` with a bare `int()`
    while `dumps` enforces the 32-bit bounds. Accepting a value inbound that
    cannot be rendered outbound is what makes a handler of the echo shape -- a
    `ping`, or any `*.update` answering with its own struct -- fail as a
    `WorldBug` over a value the *caller* chose. `_check_values` is what keeps the
    two sides honest, and it is why a `TypeError` or an `OverflowError` out of
    `dumps` can be read as the author's mistake.
    """
    if _DOCTYPE in body:
        raise XmlRpcFault(
            PARSE_ERROR,
            "document type declarations are not accepted; send a methodCall with no DOCTYPE",
        )
    try:
        params, method = xmlrpc.client.loads(body)
    except xmlrpc.client.Fault as error:
        # The document was a well-formed fault *response*. `loads` raises it, as
        # a client would; here it means the caller sent the wrong half of the
        # protocol, and the fault it carries is not this server's to re-raise.
        raise XmlRpcFault(
            INVALID_REQUEST, "a methodCall is expected; this document is a fault response"
        ) from error
    except Exception as error:
        # Everything else `loads` raises, by the class of failure and not by the
        # exception type: the only thing in the `try` is the caller's document, so
        # anything that comes out of it is about that document and is the caller's
        # to fix. Enumerating the types was wrong in a way worth recording, because
        # they are nowhere documented and are not what a reader would guess:
        # `xmlrpc.client` raises `ExpatError` for malformed XML and `ResponseError`
        # for a structure it cannot walk, but also `ValueError` for an `<int>`
        # holding letters, `TypeError` for `<boolean>7</boolean>`, and `IndexError`
        # for a `<member>` with a `<value>` and no `<name>` -- and a future
        # release may add a sixth. Missing one does not merely mislabel it: it
        # leaves the framework logging a traceback and telling the agent the
        # *world* broke, which an agent can then provoke at will with a 200-byte
        # string.
        raise XmlRpcFault(
            PARSE_ERROR, f"the document is not well-formed XML-RPC: {error}"
        ) from error
    if method is None:
        raise XmlRpcFault(
            INVALID_REQUEST, "a methodCall is expected; this document carries no methodName"
        )
    _check_values(params, allow_none=allow_none)
    return method, params


def _check_values(params: tuple[Any, ...], *, allow_none: bool) -> None:
    """Refuse the parsed values this endpoint could not send back.

    An allowlist, and the reason is the same one that made the clause around
    `loads` an `except Exception`: what XML-RPC *carries* is a closed set the
    specification names, and what `xmlrpc.client` *emits* is neither closed nor
    documented and has grown. `<bigdecimal>` is not in the specification at all
    and unmarshals to a `Decimal` no `dumps` can write; naming the failures
    instead of the successes left it through, and would leave through whatever
    the next release adds. So the question asked of every value is "is this one
    of the types this endpoint can send back", and everything else is a fault.

    Iterative, with an explicit stack, and that is not a style preference either:
    a deeply nested document is a document this parser accepts by design --
    parsing one is cheap -- and a recursive walk here would fall over on it,
    which is the failure this whole check exists to keep off the author's side of
    the line. A cycle cannot appear: the values come from a document, not from
    world code.
    """
    stack = list(params)
    while stack:
        value = stack.pop()
        if value is None:
            if allow_none:
                continue
            # Not a parse error: the document is well formed and `<nil/>` is a
            # real tag. It is an extension, and this endpoint does not speak it.
            raise XmlRpcFault(
                INVALID_REQUEST,
                "<nil/> is an XML-RPC extension this endpoint does not accept",
            )
        # Before the `int` branch, because `bool` is a subclass of it and a
        # boolean is `<boolean>`, not an out-of-range integer.
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            # `loads` converts `<int>`, `<i8>` and `<biginteger>` with a bare
            # `int()`; `dumps` writes the 32 bits the specification gives it.
            if not xmlrpc.client.MININT <= value <= xmlrpc.client.MAXINT:
                raise XmlRpcFault(
                    PARSE_ERROR,
                    f"{value} is outside the 32-bit range XML-RPC carries "
                    f"({xmlrpc.client.MININT} to {xmlrpc.client.MAXINT}); send it as a string",
                )
            continue
        if isinstance(value, list):
            stack.extend(value)
            continue
        if isinstance(value, dict):
            for name, member in value.items():
                if not isinstance(name, str):
                    # `end_struct` pairs the value stack blindly, two at a time,
                    # so a `<member>` carrying one `<name>` and more than one
                    # `<value>` yields a key that is not the name -- an int, a
                    # list -- which `dumps` refuses. A struct's names are the
                    # tag's own text only in a conforming document, and this one
                    # is the caller's.
                    raise XmlRpcFault(
                        INVALID_REQUEST,
                        "a struct member carries one <name> and one <value>",
                    )
                stack.append(member)
            continue
        if not isinstance(value, _CARRIED):
            raise XmlRpcFault(
                PARSE_ERROR,
                f"a value in this document parsed to a {type(value).__name__}, which this "
                f"endpoint cannot carry; XML-RPC carries int, boolean, double, string, "
                f"dateTime.iso8601, base64, array and struct",
            )


def render_response(value: Any, *, method: str, allow_none: bool = False) -> str:
    """One return value as the `<methodResponse>` that carries it.

    A value XML-RPC has no type for is a `WorldBug`, which is the framework's own
    rule for a tool result (`call.py`'s `serialise`) said again for a narrower
    set of types: XML-RPC carries six scalars, an array and a struct, and a
    handler returning anything else is the world's mistake and never the agent's.
    `allow_none` is the `<nil/>` extension, off by default because it is an
    extension -- a world whose real product returns nothing from a method turns
    it on.

    `OverflowError` is the second way that goes wrong and is the same mistake:
    XML-RPC's `<int>` is 32 bits, so a handler answering with a database rowid or
    a millisecond timestamp has picked a type the wire cannot carry. A world that
    needs the value sends it as a string, which is what the products that hit this
    do.

    Both readings hold only because `parse_method_call` refuses the same values on
    the way in. A `None` or an out-of-range integer the *caller* sent would
    otherwise arrive here through any handler that answers with what it was given,
    and be charged to the author.

    A `RecursionError` is the one failure here that is *not* the world's, and it
    is the other half of the XML bomb the DOCTYPE guard turns away. Parsing a
    deeply nested document is iterative and survives it; `dumps` recurses, so a
    handler echoing what it was sent -- which is what a `ping` does, and what any
    `*.update` returning its own struct does -- falls over rendering the reply.
    The nesting came from the caller, so it is answered as a fault rather than
    charged to the author or left to reach the framework as a logged traceback an
    agent can produce at will.
    """
    try:
        return xmlrpc.client.dumps((value,), methodresponse=True, allow_none=allow_none)
    except RecursionError as error:
        raise XmlRpcFault(
            INTERNAL_ERROR, f"{method}: the response is nested too deeply to render"
        ) from error
    except (TypeError, OverflowError) as error:
        raise seahaven.WorldBug(
            f"XML-RPC method {method!r} returned a value XML-RPC cannot carry: {error}"
        ) from error
