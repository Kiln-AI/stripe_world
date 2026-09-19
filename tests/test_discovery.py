"""The discovery index and its two tools: the route-table shadow, the
ranking, and the parameter rendering."""

import json
from pathlib import Path

import pytest
import seahaven

from conftest import BLANK_NOW
from stripeapi.discovery import index
from stripeapi.dispatch import routes
from stripeapi.dispatch.params import body_of

pytestmark = pytest.mark.seahaven(fixture=None, now=BLANK_NOW)

REPO = Path(__file__).resolve().parents[1]


def test_the_index_matches_the_route_table_both_directions() -> None:
    """`index ≡ routes` (architecture §10): an operation cannot be advertised
    and unimplemented, or implemented and undiscoverable."""
    route_keys = {(route.method, route.pattern) for route in routes.ALL}
    assert set(index.BY_KEY) == route_keys


def test_an_unrouted_operation_is_invisible() -> None:
    """`/v1/customers/search` exists in the full spec and is deliberately cut
    (functional spec §3.3): it has neither results nor details."""
    assert ("GET", "/v1/customers/search") not in index.BY_KEY
    assert all("/search" not in result["path"] for result in index.search("search customers"))


def test_search_is_deterministic_with_a_total_order() -> None:
    assert index.search("refund a charge") == index.search("refund a charge")
    # The tie-break is (method, path), unique across the routed set.
    scored = [op for op in index.INDEX]
    keys = [(op.method, op.path) for op in scored]
    assert len(set(keys)) == len(keys)


def test_search_quality_spot_checks() -> None:
    for query, wanted in (
        ("cancel a subscription", "cancel"),
        ("void invoice", "void"),
        ("refund a charge", "refund"),
        ("list customers", "/v1/customers"),
        ("create a coupon", "coupon"),
    ):
        results = index.search(query)
        assert results, query
        assert any(
            wanted in result["path"] or wanted in result["summary"].lower()
            for result in results[:3]
        ), (query, results[:3])


def test_search_returns_at_most_ten() -> None:
    assert len(index.search("the")) <= 10


def test_a_zero_result_query_is_an_empty_list(instance: seahaven.Instance) -> None:
    assert instance.call("stripe_api_search", query="zzzqqqxyzzy") == []


def test_an_empty_query_is_a_tool_error(instance: seahaven.Instance) -> None:
    for query in ("", "   ", "!"):
        with pytest.raises(seahaven.ToolError) as raised:
            instance.call("stripe_api_search", query=query)
        assert raised.value.code == "INVALID_SEARCH_QUERY"


def test_details_for_a_concrete_path_resolves_to_the_pattern(
    instance: seahaven.Instance,
) -> None:
    documented = instance.call("stripe_api_details", method="GET", path="/v1/customers/cus_1")
    assert documented["path"] == "/v1/customers/{customer}"
    assert documented["operation_id"] == "GetCustomersCustomer"


def test_details_for_an_unknown_path_is_a_tool_error(instance: seahaven.Instance) -> None:
    with pytest.raises(seahaven.ToolError) as raised:
        instance.call("stripe_api_details", method="GET", path="/v1/widgets")
    assert raised.value.code == "UNKNOWN_OPERATION"


def test_details_refuses_a_non_routed_verb(instance: seahaven.Instance) -> None:
    """`PATCH` never reaches the tool: the `Literal` annotation refuses it, and
    the error handler restates the framework's refusal as this world's
    `INVALID_INPUT` — the fourth method is an authoring mistake, not a Stripe
    request (functional spec §2.3)."""
    with pytest.raises(seahaven.ToolError) as raised:
        instance.call("stripe_api_details", method="PATCH", path="/v1/customers")
    assert raised.value.code == "INVALID_INPUT"


def test_wired_details_document_exactly_the_enforced_allowlist(
    instance: seahaven.Instance,
) -> None:
    """The cross-check that closes "documents what's rejected"
    (`components/discovery.md` §8a) — on the body route *and* the query
    route: a wired operation's documented parameters are its `ParamSpec`'s
    plus the central parameters that operation accepts, so nothing advertised
    is rejected and nothing enforced is undocumented."""
    from stripeapi.dispatch.router import ROUTER

    documented = instance.call("stripe_api_details", method="POST", path="/v1/customers")
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
    listed = instance.call("stripe_api_details", method="GET", path="/v1/customers")
    names = {parameter["name"] for parameter in listed["parameters"]}
    assert names == {
        "created",
        "email",
        "ending_before",
        "expand",
        "limit",
        "starting_after",
    }
    result = instance.call("stripe_api_read", path="/v1/customers", params={"test_clock": "ts_1"})
    assert result["status"] == 400
    assert result["body"]["error"]["param"] == "test_clock"


def test_nesting_depth_is_one_level(instance: seahaven.Instance) -> None:
    documented = instance.call("stripe_api_details", method="POST", path="/v1/customers")
    for parameter in documented["parameters"]:
        for key in ("fields", "item_fields"):
            for nested in parameter.get(key, ()):
                assert "fields" not in nested
                assert "item_fields" not in nested


def test_descriptions_are_one_sentence(instance: seahaven.Instance) -> None:
    documented = instance.call("stripe_api_details", method="POST", path="/v1/customers")

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
    wired = [
        (route.method, route.pattern)
        for route in routes.ALL
        if route.params is not None and route.method == "POST"
    ]
    assert wired  # the throwaway customers slice, at minimum
    for method, path in wired:
        documented = index.details(method, path)
        assert documented is not None
        assert len(json.dumps(documented)) < 10_000, (method, path)
    for method, path in (("POST", "/v1/subscriptions"), ("POST", "/v1/invoices")):
        documented = index.details(method, path)
        assert documented is not None
        assert len(json.dumps(documented)) < 25_000, (method, path)


def test_the_index_comes_from_the_committed_artifact() -> None:
    """Tier-1 drift guard: the index's operation ids are the artifact's, so a
    hand edit of `spec3.min.json` shows up here before anywhere else."""
    raw = json.loads((REPO / "src/stripeapi/spec/spec3.min.json").read_text())
    artifact_ops = {
        (method.upper(), path)
        for path, item in raw["paths"].items()
        for method in item
        if method in ("get", "post", "delete")
    }
    assert set(index.BY_KEY) == artifact_ops
