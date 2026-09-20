"""The four tools as an agent meets them: the contract of each face."""

import pytest
import seahaven

from conftest import BLANK_NOW

pytestmark = pytest.mark.seahaven(fixture=None, now=BLANK_NOW)


def test_the_read_tool_refuses_a_write_verb(instance: seahaven.Instance) -> None:
    """The `Literal` makes the closed set visible in the tool's schema and the
    refusal Seahaven's own: the world's `INVALID_INPUT`, not a Stripe envelope
    (functional spec §2.3)."""
    with pytest.raises(seahaven.ToolError) as raised:
        instance.call("stripe_api_read", path="/v1/customers", method="POST")
    assert raised.value.code == "INVALID_INPUT"


def test_the_write_tool_refuses_get_and_unknown_verbs(instance: seahaven.Instance) -> None:
    for method in ("GET", "PATCH", "post"):
        with pytest.raises(seahaven.ToolError) as raised:
            instance.call("stripe_api_write", method=method, path="/v1/customers")
        assert raised.value.code == "INVALID_INPUT"


def test_a_non_object_params_is_refused_before_the_dispatcher(
    instance: seahaven.Instance,
) -> None:
    with pytest.raises(seahaven.ToolError) as raised:
        instance.call("stripe_api_read", path="/v1/customers", params=["nope"])
    assert raised.value.code == "INVALID_INPUT"


def test_the_read_tool_cannot_reach_a_post_only_route(instance: seahaven.Instance) -> None:
    """A `POST`-only path through the read tool is the router's 405, not a
    tool-contract error — the verb was legal, the URL was not."""
    result = instance.call("stripe_api_read", path="/v1/charges/ch_1/capture")
    assert result["status"] == 405


def test_call_stripe_reaches_every_verb(instance: seahaven.Instance) -> None:
    """The unregistered escape hatch cannot rot: it dispatches all three verbs
    over the same router (functional spec §2.5). Driven through `bulk()`'s
    context, the one context a caller can hold outside a tool call."""
    from stripeapi.dispatch.response import ApiResponse
    from stripeapi.tools.api import call_stripe

    with instance.bulk() as ctx:
        created = call_stripe(ctx, "POST", "/v1/customers", {"name": "hatch"})
        assert isinstance(created, ApiResponse)
        assert created.status == 200
        listed = call_stripe(ctx, "GET", "/v1/customers")
        assert listed.status == 200
        cus = listed.body["data"][0]["id"]
        deleted = call_stripe(ctx, "DELETE", f"/v1/customers/{cus}")
        assert deleted.status == 200
        assert deleted.body["deleted"] is True


def test_the_tool_descriptions_name_no_sibling_tool() -> None:
    """Lint `SH206`'s rule, asserted: a prefixing host renames tools without
    rewriting descriptions, so no description may carry a sibling's name."""
    names = ["stripe_api_read", "stripe_api_write", "stripe_api_search", "stripe_api_details"]
    descriptions = {name: seahaven_world_tool_description(name) for name in names}
    for name, description in descriptions.items():
        for other in names:
            if other != name:
                assert other not in description, (name, other)


def seahaven_world_tool_description(name: str) -> str:
    from stripeapi.world import world

    return str(world.tools[name].description)


def test_the_wired_surface_is_small_and_named() -> None:
    """What is served end to end: the customers phase's thirteen routes (five
    core customers, six payment_methods, two customer-scoped payment-method
    reads). The rest are routed data awaiting their resource phases, and
    calling one is an honest `INTERNAL` naming the op (see `test_dispatch.py`)."""
    wired = [route.op_id for route in _all_routes() if route.params is not None]
    assert wired == [
        "GetCustomers",
        "PostCustomers",
        "GetCustomersCustomer",
        "PostCustomersCustomer",
        "DeleteCustomersCustomer",
        "GetCustomersCustomerPaymentMethods",
        "GetCustomersCustomerPaymentMethodsPaymentMethod",
        "GetPaymentMethods",
        "PostPaymentMethods",
        "GetPaymentMethodsPaymentMethod",
        "PostPaymentMethodsPaymentMethod",
        "PostPaymentMethodsPaymentMethodAttach",
        "PostPaymentMethodsPaymentMethodDetach",
    ]


def _all_routes():
    from stripeapi.dispatch.routes import ALL

    return ALL
