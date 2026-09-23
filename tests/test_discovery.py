"""The discovery index and its two tools: the route-table shadow, the
ranking, and the parameter rendering."""

import json
from pathlib import Path

import pytest
import seahaven

from conftest import BLANK_NOW, api_details, api_read, api_search
from seahaven_stripe_world.discovery import index
from seahaven_stripe_world.dispatch import routes
from seahaven_stripe_world.dispatch.params import body_of
from seahaven_stripe_world.errors import StripeToolError

pytestmark = pytest.mark.seahaven(fixture=None, now=BLANK_NOW)

REPO = Path(__file__).resolve().parents[1]


def test_the_index_matches_the_route_table_both_directions() -> None:
    """`index ≡ routes` (architecture §10): an operation cannot be advertised
    and unimplemented, or implemented and undiscoverable."""
    route_keys = {(route.method, route.pattern) for route in routes.ALL}
    assert set(index.BY_KEY) == route_keys


def test_search_operations_are_visible() -> None:
    """The seven search endpoints (Phase 22) are now routed and visible."""
    assert ("GET", "/v1/customers/search") in index.BY_KEY
    assert ("GET", "/v1/charges/search") in index.BY_KEY


def test_search_is_deterministic_with_a_total_order() -> None:
    assert index.search("refund", "charge") == index.search("refund", "charge")
    # The tie-break is (method, path), unique across the routed set.
    scored = [op for op in index.INDEX]
    keys = [(op.method, op.path) for op in scored]
    assert len(set(keys)) == len(keys)


def test_search_quality_spot_checks() -> None:
    for intent, resource, wanted in (
        ("cancel", "subscription", "cancel"),
        ("void", "invoice", "void"),
        ("refund", "charge", "refund"),
        ("list", "customers", "/v1/customers"),
        ("create", "coupon", "coupon"),
    ):
        results = index.search(intent, resource)
        assert results, (intent, resource)
        assert any(
            wanted in result["path"] or wanted in result["summary"].lower()
            for result in results[:3]
        ), (intent, resource, results[:3])


def test_search_returns_at_most_limit() -> None:
    assert len(index.search("the", "", limit=10)) <= 10
    assert len(index.search("the", "", limit=5)) <= 5


def test_a_zero_result_query_is_an_empty_list(instance: seahaven.Instance) -> None:
    assert api_search(instance, "zzzqqqxyzzy", "zzzqqqxyzzy") == []


def test_an_empty_query_returns_empty_list(instance: seahaven.Instance) -> None:
    """Empty intent and resource return an empty list (no error)."""
    assert api_search(instance, "", "") == []


def test_details_returns_operation_info(
    instance: seahaven.Instance,
) -> None:
    documented = api_details(instance, "GetCustomersCustomer")
    assert documented["path"] == "/v1/customers/{customer}"
    assert documented["operation_id"] == "GetCustomersCustomer"


def test_details_for_an_unknown_operation_is_a_tool_error(instance: seahaven.Instance) -> None:
    with pytest.raises(seahaven.ToolError) as raised:
        api_details(instance, "GetWidgets")
    assert raised.value.code == "UNKNOWN_OPERATION"


def test_wired_details_document_exactly_the_enforced_allowlist(
    instance: seahaven.Instance,
) -> None:
    """The cross-check that closes "documents what's rejected"
    (`components/discovery.md` §8a) — on the body route *and* the query
    route: a wired operation's documented parameters are its `ParamSpec`'s
    plus the central parameters that operation accepts, so nothing advertised
    is rejected and nothing enforced is undocumented."""
    from seahaven_stripe_world.dispatch.router import ROUTER

    documented = api_details(instance, "PostCustomers")
    spec_parameters = {parameter["name"] for parameter in documented["parameters"]}
    route = ROUTER.resolve("POST", "/v1/customers")
    assert route is not None and route.params is not None
    enforced = {param.name for param in body_of(route)}
    if route.params.expand:
        enforced.add("expand")
    if route.params.metadata:
        enforced.add("metadata")
    # POST /v1/customers has no path placeholders, so every documented name is
    # body-or-central: the documented set is exactly the enforced one, with
    # the central parameters gated on the same ParamSpec flags the dispatcher
    # enforces — one parameter over, on either side, is the hole this closes.
    assert spec_parameters == enforced

    # The list route's query surface: the list filters plus the pagination
    # parameters and `expand` it accepts — and not `test_clock`, which the
    # spec serves and the dispatcher rejects.
    listed = api_details(instance, "GetCustomers")
    names = {parameter["name"] for parameter in listed["parameters"]}
    assert names == {
        "created",
        "email",
        "ending_before",
        "expand",
        "limit",
        "starting_after",
    }
    with pytest.raises(StripeToolError) as exc_info:
        api_read(instance, "/v1/customers", {"test_clock": "ts_1"})
    assert exc_info.value.status == 400
    assert exc_info.value.stripe_body["error"]["param"] == "test_clock"


def test_nesting_depth_is_one_level(instance: seahaven.Instance) -> None:
    documented = api_details(instance, "PostCustomers")
    for parameter in documented["parameters"]:
        for key in ("fields", "item_fields"):
            for nested in parameter.get(key, ()):
                assert "fields" not in nested
                assert "item_fields" not in nested


def test_descriptions_are_one_sentence(instance: seahaven.Instance) -> None:
    documented = api_details(instance, "PostCustomers")

    def sentences(text: str) -> int:
        return text.count(". ")

    assert sentences(documented["description"]) == 0
    for parameter in documented["parameters"]:
        assert sentences(parameter["description"]) == 0


def test_details_response_size_budget() -> None:
    """The size problem of §8 (`POST /v1/subscriptions`'s raw body is 23 KB).
    A *wired* operation is filtered to its `ParamSpec` and fits comfortably;
    an unwired one serves the spec verbatim until its resource phase lands,
    so its guard is the interim one and tightens as the slices do."""
    wired = [route for route in routes.ALL if route.params is not None and route.method == "POST"]
    assert wired  # the throwaway customers slice, at minimum
    for route in wired:
        documented = index.details(route.op_id)
        assert documented is not None
        assert len(json.dumps(documented)) < 10_000, (route.method, route.pattern)
    for method, path in (("POST", "/v1/subscriptions"), ("POST", "/v1/invoices")):
        op = index.BY_KEY.get((method, path))
        assert op is not None, f"missing {method} {path}"
        documented = index.details(op.operation_id)
        assert documented is not None
        assert len(json.dumps(documented)) < 25_000, (method, path)


def test_the_index_comes_from_the_committed_artifact() -> None:
    """Tier-1 drift guard: the index's operation ids are the artifact's, so a
    hand edit of `spec3.min.json` shows up here before anywhere else."""
    raw = json.loads((REPO / "src/seahaven_stripe_world/spec/spec3.min.json").read_text())
    artifact_ops = {
        (method.upper(), path)
        for path, item in raw["paths"].items()
        for method in item
        if method in ("get", "post", "delete")
    }
    assert set(index.BY_KEY) == artifact_ops
