"""The call path: a `<methodCall>` in, a `<methodResponse>` out, and every way in between.

Every test drives the endpoint the way an agent does -- `instance.call("rpc",
body=...)` -- with `xmlrpc.client` building the request and reading the answer.
"""

import xmlrpc.client
from collections.abc import Callable
from fractions import Fraction
from typing import Any

import pytest

import seahaven
import seahaven_xmlrpc
from conftest import NOW, RPC, method_call, rpc

pytestmark = pytest.mark.seahaven(fixture=None, now=NOW)


def test_a_method_call_reaches_the_handler_and_comes_back_as_a_method_response(
    instance: seahaven.Instance,
) -> None:
    """The whole path: parse, dispatch, the handler's write, render."""
    created = rpc(instance, "user.create", "ada@tracker.invalid", "Ada", "admin")
    assert created["email"] == "ada@tracker.invalid"
    assert rpc(instance, "user.get", created["id"]) == created


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(42, id="an int"),
        pytest.param("a string", id="a string"),
        pytest.param(True, id="a boolean"),
        pytest.param(1.5, id="a double"),
        pytest.param([1, "two", False], id="an array"),
        pytest.param({"a": 1, "b": ["c"]}, id="a struct"),
        pytest.param("<&>'\"", id="text XML has to escape"),
        pytest.param(xmlrpc.client.Binary(b"\x00\xff bytes"), id="base64"),
        pytest.param(xmlrpc.client.DateTime("20260601T09:00:00"), id="dateTime.iso8601"),
    ],
)
def test_every_xml_rpc_type_survives_the_round_trip(
    instance: seahaven.Instance, value: Any
) -> None:
    """The document is real XML-RPC in both directions, not a shape of its own.

    `xmlrpc.client` writes the request and reads the response, so anything the
    two sides disagreed about -- an encoding, an escape, a tag -- fails here.
    """
    assert rpc(instance, "tracker.ping", value) == value


def test_a_handler_reads_the_instances_clock_and_its_ids(instance: seahaven.Instance) -> None:
    """A method is world code: its timestamps are the instance's, never the wall clock."""
    created = rpc(instance, "user.create", "grace@tracker.invalid", "Grace", "member")
    assert created["created_at"] == NOW
    assert created["id"] != ""


def test_the_method_namespace_is_the_protocols_and_not_the_tool_registrys(
    instance: seahaven.Instance,
) -> None:
    """One tool, five methods behind it: the claim the whole example exists to make."""
    assert [listed["name"] for listed in instance.tools()] == [RPC]
    assert rpc(instance, "system.listMethods") == [
        "system.listMethods",
        "tracker.ping",
        "user.create",
        "user.get",
        "user.list",
    ]


def test_a_method_nobody_registered_is_a_method_not_found_fault(
    instance: seahaven.Instance,
) -> None:
    with pytest.raises(xmlrpc.client.Fault) as raised:
        rpc(instance, "user.destroy", "u1")
    assert raised.value.faultCode == -32601
    assert "user.destroy" in raised.value.faultString


@pytest.mark.parametrize(
    "params",
    [
        pytest.param((), id="too few"),
        pytest.param(("u1", "u2"), id="too many"),
    ],
)
def test_parameters_the_handler_cannot_take_are_an_invalid_params_fault(
    instance: seahaven.Instance, params: tuple[Any, ...]
) -> None:
    with pytest.raises(xmlrpc.client.Fault) as raised:
        rpc(instance, "user.get", *params)
    assert raised.value.faultCode == -32602
    assert "user.get" in raised.value.faultString


def test_a_type_error_inside_a_handler_is_not_reported_as_a_parameter_error(
    probe: Callable[..., seahaven.World],
) -> None:
    """Why the parameters are bound and not merely passed.

    `except TypeError` around the call would catch a `TypeError` the handler
    raised itself and tell the agent its parameters were wrong, sending it to fix
    a call that was correct. Binding asks the signature the question instead, so
    a handler's own `TypeError` is an unexpected failure and reaches the world's
    error handler like any other bug.
    """

    def raises_its_own_type_error(ctx: seahaven.Ctx, value: str) -> Any:
        raise TypeError(f"{value}: a mistake inside the handler, not in its parameters")

    world = probe({"boom": raises_its_own_type_error})
    with world.instance(None, now=NOW) as live, pytest.raises(seahaven.ToolError) as raised:
        rpc(live, "boom", "one")
    assert raised.value.code == "INTERNAL"


def test_a_document_that_is_not_well_formed_is_a_parse_error(
    instance: seahaven.Instance,
) -> None:
    with pytest.raises(xmlrpc.client.Fault) as raised:
        xmlrpc.client.loads(instance.call(RPC, body="<methodCall><methodName>x"))
    assert raised.value.faultCode == -32700


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(
            "<methodCall><methodName>tracker.ping</methodName><params><param><value><struct>"
            "<member><value><int>1</int></value></member></struct></value></param></params>"
            "</methodCall>",
            id="a struct member with a value and no name",
        ),
        pytest.param(
            "<methodCall><methodName>tracker.ping</methodName><params><param>"
            "<value><boolean>7</boolean></value></param></params></methodCall>",
            id="a boolean that is neither 0 nor 1",
        ),
    ],
)
def test_well_formed_xml_that_is_malformed_xml_rpc_is_a_parse_error(
    instance: seahaven.Instance, body: str
) -> None:
    """Both documents parse as XML and neither is XML-RPC, and both are the caller's.

    They are here because `xmlrpc.client` reports them as an `IndexError` and a
    `TypeError`, which no reader would guess and nothing documents. An except
    clause that named the types it expected let them past as world bugs: the agent
    was told the *server* broke, and the framework logged a traceback -- which an
    agent could then provoke at will from a 200-byte string.
    """
    with pytest.raises(xmlrpc.client.Fault) as raised:
        xmlrpc.client.loads(instance.call(RPC, body=body))
    assert raised.value.faultCode == -32700


@pytest.mark.parametrize(
    ("value", "code"),
    [
        pytest.param("<nil/>", -32600, id="nil, which this endpoint does not accept"),
        pytest.param("<int>99999999999</int>", -32700, id="an int past the 32-bit ceiling"),
        pytest.param("<i4>-99999999999</i4>", -32700, id="an i4 past the 32-bit floor"),
        pytest.param(
            "<array><data><value><nil/></value></data></array>", -32600, id="nil inside an array"
        ),
        pytest.param(
            "<struct><member><name>a</name><value><nil/></value></member></struct>",
            -32600,
            id="nil inside a struct",
        ),
        pytest.param(
            "<bigdecimal>1.5</bigdecimal>", -32700, id="a bigdecimal, which is not XML-RPC"
        ),
        pytest.param(
            "<array><data><value><bigdecimal>1.5</bigdecimal></value></data></array>",
            -32700,
            id="a bigdecimal inside an array",
        ),
        pytest.param(
            "<struct><member><name>a</name><value><int>1</int></value>"
            "<value><int>2</int></value><value><int>3</int></value></member></struct>",
            -32600,
            id="a struct member with more values than names",
        ),
    ],
)
def test_a_value_the_endpoint_could_not_send_back_is_refused_on_the_way_in(
    instance: seahaven.Instance, value: str, code: int
) -> None:
    """The two sides of `xmlrpc.client` disagree, and the caller must not pay for it.

    `loads` has no `allow_none` at all and unmarshals `<nil/>` whatever the server
    thinks; it converts `<int>` with a bare `int()` while `dumps` enforces the
    32-bit bounds. Accepted inbound, either would reach `dumps` through any
    handler that answers with what it was given -- `tracker.ping` is exactly that
    shape -- and fail as a `WorldBug`, telling the agent the world broke over a
    value the agent itself chose, with a traceback in the log to match.
    """
    body = (
        "<methodCall><methodName>tracker.ping</methodName>"
        f"<params><param><value>{value}</value></param></params></methodCall>"
    )
    with pytest.raises(xmlrpc.client.Fault) as raised:
        xmlrpc.client.loads(instance.call(RPC, body=body))
    assert raised.value.faultCode == code


def test_a_type_the_parser_learns_later_is_refused_here_and_not_by_dumps(
    instance: seahaven.Instance, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The allowlist's whole point, in the one shape that cannot be written today.

    `<bigdecimal>` is in the parametrised cases above because the standard library
    grew it; the check has to survive whatever it grows next. A tag is added to
    `Unmarshaller.dispatch` here that unmarshals to a type `dumps` cannot write,
    which is exactly what `<bigdecimal>` was before anyone noticed, and the answer
    must still be a fault rather than a `WorldBug` with a traceback behind it.
    """

    def end_fraction(unmarshaller: Any, data: str) -> None:
        unmarshaller.append(Fraction(data))
        unmarshaller._value = 0

    monkeypatch.setitem(xmlrpc.client.Unmarshaller.dispatch, "fraction", end_fraction)
    body = (
        "<methodCall><methodName>tracker.ping</methodName>"
        "<params><param><value><fraction>1/2</fraction></value></param></params></methodCall>"
    )
    with pytest.raises(xmlrpc.client.Fault) as raised:
        xmlrpc.client.loads(instance.call(RPC, body=body))
    assert raised.value.faultCode == -32700
    assert "Fraction" in raised.value.faultString


def test_nil_is_carried_both_ways_by_an_endpoint_that_enables_it(
    probe: Callable[..., seahaven.World],
) -> None:
    """`allow_none=True` is a world saying its product speaks the `<nil/>` extension.

    The same walk that refuses `<nil/>` above lets it through here, and `dumps`
    renders it, so the round trip is `None` in and `None` out.
    """
    world = probe({"echo": lambda ctx, value: value}, allow_none=True)
    with world.instance(None, now=NOW) as live:
        body = (
            "<methodCall><methodName>echo</methodName>"
            "<params><param><value><nil/></value></param></params></methodCall>"
        )
        (value,), _ = xmlrpc.client.loads(live.call(RPC, body=body))
        assert value is None


def test_a_response_too_deeply_nested_to_render_is_an_internal_error_fault(
    instance: seahaven.Instance,
) -> None:
    """The other XML bomb: nesting, which costs nothing to parse and recurses to render.

    Parsing is iterative and survives it; `dumps` recurses, so the echo falls over
    building the reply. It is the caller's document, so it comes back as a fault
    rather than as a logged traceback and this world's `INTERNAL`.
    """
    depth = 1_400
    nested = "<array><data><value>" * depth + "<int>1</int>" + "</value></data></array>" * depth
    body = (
        "<methodCall><methodName>tracker.ping</methodName>"
        f"<params><param><value>{nested}</value></param></params></methodCall>"
    )
    assert len(body) < seahaven_xmlrpc.MAX_BODY
    with pytest.raises(xmlrpc.client.Fault) as raised:
        xmlrpc.client.loads(instance.call(RPC, body=body))
    assert raised.value.faultCode == seahaven_xmlrpc.INTERNAL_ERROR


def test_a_document_with_no_method_name_is_an_invalid_request(
    instance: seahaven.Instance,
) -> None:
    """A `<methodCall>` missing its `<methodName>` parses and is still not a call."""
    with pytest.raises(xmlrpc.client.Fault) as raised:
        xmlrpc.client.loads(instance.call(RPC, body="<methodCall><params/></methodCall>"))
    assert raised.value.faultCode == -32600


def test_a_response_document_sent_as_a_call_is_an_invalid_request(
    instance: seahaven.Instance,
) -> None:
    """The other half of the protocol, sent to the wrong end of it.

    A fault response is the case worth pinning: `xmlrpc.client.loads` *raises* the
    fault it carries, and re-raising that would answer the caller with a fault
    this server never decided on.
    """
    sent = xmlrpc.client.dumps(xmlrpc.client.Fault(7, "not ours"), methodresponse=True)
    with pytest.raises(xmlrpc.client.Fault) as raised:
        xmlrpc.client.loads(instance.call(RPC, body=sent))
    assert raised.value.faultCode == -32600
    assert "not ours" not in raised.value.faultString


def test_a_document_type_declaration_is_refused_before_it_is_parsed(
    instance: seahaven.Instance,
) -> None:
    """The billion-laughs guard, with an entity that would otherwise expand.

    Expat expands internal entities, and `xmlrpc.client` leaves that on; the
    document here is written by the agent under test, in a process running
    hundreds of other instances. The entity is small enough to be harmless and is
    what proves the refusal happened before the parse: had the document been
    parsed, the ping would have echoed `AAAA`.
    """
    body = (
        '<?xml version="1.0"?>'
        '<!DOCTYPE methodCall [ <!ENTITY a "AAAA"> ]>'
        "<methodCall><methodName>tracker.ping</methodName>"
        "<params><param><value><string>&a;</string></value></param></params></methodCall>"
    )
    with pytest.raises(xmlrpc.client.Fault) as raised:
        xmlrpc.client.loads(instance.call(RPC, body=body))
    assert raised.value.faultCode == -32700
    assert "DOCTYPE" in raised.value.faultString


def test_a_body_longer_than_the_tool_accepts_is_an_argument_error(
    instance: seahaven.Instance,
) -> None:
    """A malformed *tool call* is not a malformed XML-RPC request.

    The cap is `Field(max_length=...)` on the argument, so an oversized document
    never reaches the parser and never becomes a fault: it is an `ArgumentError`,
    which this world's error handler restates in the product's own words.
    """
    with pytest.raises(seahaven.ToolError) as raised:
        instance.call(RPC, body=method_call("tracker.ping", "x" * 70_000))
    assert raised.value.code == "INVALID_INPUT"
    assert raised.value.details == {"field": "body"}


def test_an_empty_body_is_refused_by_the_argument_model(instance: seahaven.Instance) -> None:
    with pytest.raises(seahaven.ToolError) as raised:
        instance.call(RPC, body="")
    assert raised.value.code == "INVALID_INPUT"
