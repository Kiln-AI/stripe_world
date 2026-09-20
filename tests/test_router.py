"""The trie: precedence, backtracking, and the joint (method, path) match
with its single 404 answer.

These run against the real compiled `ROUTER` — the same instance the tools
dispatch through — plus small synthetic tables for the malformed-entry
refusals, so an import-time check is exercised rather than trusted.
"""

from dataclasses import replace
from typing import Any

import pytest
import seahaven

from stripeapi.dispatch import routes
from stripeapi.dispatch.params import ParamSpec
from stripeapi.dispatch.router import Router
from stripeapi.dispatch.routes import Route
from stripeapi.stripe_errors import StripeApiError


def op_id_of(method: str, path: str) -> str:
    from stripeapi.dispatch.router import ROUTER

    return ROUTER.match(method, path).route.op_id


def test_exact_beats_placeholder_credit_notes_preview() -> None:
    assert op_id_of("GET", "/v1/credit_notes/preview") == "GetCreditNotesPreview"


def test_exact_beats_placeholder_invoices_create_preview() -> None:
    assert op_id_of("POST", "/v1/invoices/create_preview") == "PostInvoicesCreatePreview"


def test_placeholder_matches_a_real_id() -> None:
    from stripeapi.dispatch.router import ROUTER

    match = ROUTER.match("GET", "/v1/credit_notes/cn_123")
    assert match.route.op_id == "GetCreditNotesId"
    assert match.path_values == ("cn_123",)


def test_backtracks_out_of_a_literal_dead_end() -> None:
    """`POST /v1/credit_notes/preview/void` is a legal call: void the credit
    note whose id is the string `preview`. The exact branch dead-ends and the
    walk returns to the placeholder (`components/dispatcher.md` §3.2.3)."""
    from stripeapi.dispatch.router import ROUTER

    match = ROUTER.match("POST", "/v1/credit_notes/preview/void")
    assert match.route.op_id == "PostCreditNotesIdVoid"
    assert match.path_values == ("preview",)


def test_two_placeholder_names_share_one_node() -> None:
    from stripeapi.dispatch.router import ROUTER

    assert ROUTER.match("POST", "/v1/subscriptions/sub_1/resume").path_values == ("sub_1",)
    assert (
        ROUTER.match("DELETE", "/v1/subscriptions/sub_1/discount").route.op_id
        == "DeleteSubscriptionsSubscriptionExposedIdDiscount"
    )


def test_method_mismatch_prefers_a_placeholder_route() -> None:
    """`POST /v1/credit_notes/preview` updates the credit note called
    `preview` rather than 405-ing on the literal GET terminal."""
    assert op_id_of("POST", "/v1/credit_notes/preview") == "PostCreditNotesId"


def test_unknown_path_is_404() -> None:
    from stripeapi.dispatch.router import ROUTER

    with pytest.raises(StripeApiError) as raised:
        ROUTER.match("GET", "/v1/widgets")
    error = raised.value
    assert error.status == 404
    assert error.type == "invalid_request_error"
    assert error.code is None
    assert error.message == "Unrecognized request URL (GET: /v1/widgets)."


def test_method_mismatch_is_the_unrecognized_404() -> None:
    """A path that exists under other verbs but not this one answers the same
    404 as an unknown path — probed live at the pinned version (Phase 7:
    DELETE on /v1/prices/{price}, which carries GET and POST, answered 404
    `Unrecognized request URL`), superseding the 405 the dispatcher design
    had to declare as a guess."""
    from stripeapi.dispatch.router import ROUTER

    with pytest.raises(StripeApiError) as raised:
        ROUTER.match("GET", "/v1/charges/ch_1/capture")
    assert raised.value.status == 404
    assert raised.value.message == "Unrecognized request URL (GET: /v1/charges/ch_1/capture)."


def test_trailing_slash_is_404() -> None:
    from stripeapi.dispatch.router import ROUTER

    with pytest.raises(StripeApiError):
        ROUTER.match("GET", "/v1/customers/")


def test_empty_segment_is_404() -> None:
    from stripeapi.dispatch.router import ROUTER

    with pytest.raises(StripeApiError):
        ROUTER.match("GET", "/v1/customers//balance_transactions")


def test_every_pattern_round_trips() -> None:
    """Substituting a plausible id for each placeholder matches back to the
    same route, for all 148."""
    from stripeapi.dispatch.router import ROUTER

    for route in routes.ALL:
        path = route.pattern
        for placeholder, fake in (
            ("{customer}", "cus_1"),
            ("{id}", "x_1"),
            ("{invoice}", "in_1"),
            ("{credit_note}", "cn_1"),
            ("{subscription_exposed_id}", "sub_1"),
            ("{subscription}", "sub_1"),
        ):
            path = path.replace(placeholder, fake)
        assert ROUTER.match(route.method, path).route is route, route.op_id


def test_resolve_finds_a_pattern_and_a_concrete_path() -> None:
    from stripeapi.dispatch.router import ROUTER

    pattern_route = ROUTER.resolve("GET", "/v1/customers/{customer}")
    concrete_route = ROUTER.resolve("GET", "/v1/customers/cus_1")
    assert pattern_route is not None and pattern_route.op_id == "GetCustomersCustomer"
    assert concrete_route is not None and concrete_route.op_id == "GetCustomersCustomer"
    assert ROUTER.resolve("GET", "/v1/widgets") is None
    assert ROUTER.resolve("PATCH", "/v1/customers") is None


# --- The import-time table checks ---------------------------------------------


def wired(**kwargs: Any) -> Route:
    """A minimally wired route: an engine-served list of the customers table."""
    from stripeapi.resources.customers import SPEC

    route = Route(
        method="GET",
        pattern="/v1/probe",
        op_id="GetProbe",
        params=ParamSpec(op_id="GetProbe", path=(), paginated=True),
        resource=SPEC,
        action="list",
        response_object="customer",
        envelope="list",
    )
    return route if not kwargs else replace(route, **kwargs)


def test_a_duplicate_method_pattern_is_refused() -> None:
    with pytest.raises(seahaven.WorldBug, match="duplicate route"):
        Router((wired(), wired()))


def test_a_non_v1_pattern_is_refused() -> None:
    with pytest.raises(seahaven.WorldBug, match="not an absolute"):
        Router((wired(pattern="/api/probe"),))


def test_an_empty_segment_is_refused() -> None:
    with pytest.raises(seahaven.WorldBug, match="empty segment"):
        Router((wired(pattern="/v1/probe//x"),))


def test_a_malformed_placeholder_is_refused() -> None:
    with pytest.raises(seahaven.WorldBug, match="malformed placeholder"):
        Router((wired(pattern="/v1/probe/{1nvalid}"),))


def test_both_handler_and_engine_is_refused() -> None:
    with pytest.raises(seahaven.WorldBug, match="exactly one"):
        Router((wired(handler=lambda ctx, req: {}),))  # type: ignore[arg-type]


def test_a_param_spec_path_that_disagrees_with_the_pattern_is_refused() -> None:
    spec = ParamSpec(op_id="GetProbe", path=("wrong",), paginated=True)
    with pytest.raises(seahaven.WorldBug, match="placeholders"):
        Router((wired(pattern="/v1/probe/{thing}", params=spec),))


def test_a_central_parameter_in_a_body_is_refused() -> None:
    from stripeapi.dispatch.params import Param

    spec = ParamSpec(op_id="GetProbe", body=(Param(name="limit", kind="integer"),))
    with pytest.raises(seahaven.WorldBug, match="handled centrally"):
        Router((wired(params=spec),))


def test_an_alias_of_an_unknown_op_id_is_refused() -> None:
    with pytest.raises(seahaven.WorldBug, match="aliases"):
        Router((wired(alias_of="NoSuchOperation"),))


def test_an_unwired_route_carries_no_wiring() -> None:
    with pytest.raises(seahaven.WorldBug, match="no ParamSpec"):
        Router((Route(method="GET", pattern="/v1/probe", op_id="GetProbe", action="list"),))


def test_a_creatable_that_drifts_from_the_route_body_is_refused() -> None:
    """`ResourceSpec.creatable`/`updatable` are declarations the engine does
    not read — the route's `ParamSpec` is the enforced allowlist — so the
    router refuses a route whose body has drifted from its resource's
    declaration: the two cannot silently diverge."""
    from stripeapi.dispatch.params import Param, ParamSpec

    drifted = replace(
        wired(),
        method="POST",
        op_id="PostProbe",
        params=ParamSpec(op_id="PostProbe", body=(Param(name="email", kind="string"),)),
        action="create",
    )
    # The route's body (email alone) disagrees with the resource's declared
    # creatable (customers' full allowlist), and the router says so.
    with pytest.raises(seahaven.WorldBug, match="drifted"):
        Router((drifted,))


def test_accepting_expand_without_a_response_object_is_refused() -> None:
    """Static expand validation needs something to validate against
    (cross_cutting.md §3.3.1): a wired route that accepts `expand[]` without
    declaring its response object is refused at import, so validation can
    never silently fall back to sniffing the payload."""
    undeclared = replace(wired(), response_object=None, envelope=None)
    with pytest.raises(seahaven.WorldBug, match="no response_object"):
        Router((undeclared,))


def test_refusing_expand_with_a_response_object_is_refused() -> None:
    refusing = replace(
        wired(),
        params=ParamSpec(op_id="GetProbe", path=(), paginated=True, expand=False),
        response_object=None,
        envelope=None,
    )
    declared = replace(refusing, response_object="customer", envelope="object")
    with pytest.raises(seahaven.WorldBug, match="declares a response object"):
        Router((declared,))
