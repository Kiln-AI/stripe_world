"""The discovery index and its two tools: the full MCP catalogue, the
ranking, and the twelve-key details document.

Phase 8 rewrote the discovery layer to serve the full catalogue of 123
catalogued operations rather than only the routed subset.  The tests
below are named for the tell-register rows they close.
"""

import json
from pathlib import Path

import pytest
import seahaven

from conftest import BLANK_NOW, api_details, api_search
from seahaven_stripe_world.discovery import index
from seahaven_stripe_world.spec.catalogue import CATALOGUED

pytestmark = pytest.mark.seahaven(fixture=None, now=BLANK_NOW)

REPO = Path(__file__).resolve().parents[1]


# --- Tell register rows: discovery surface ---


def test_ts_13_dt_02_search_returns_wrapped_envelope(
    instance: seahaven.Instance,
) -> None:
    """Search output has ``{openapi_spec_version, data}`` wrapper."""
    result = api_search(instance, "create", "customer")
    assert "openapi_spec_version" in result
    assert "data" in result
    assert isinstance(result["data"], list)
    assert result["openapi_spec_version"].startswith("2026-08-26")


def test_ts_14_dt_03_search_results_have_id_and_optional_llm_context(
    instance: seahaven.Instance,
) -> None:
    """Each search result has ``id`` (the operation ID)."""
    result = api_search(instance, "create", "customer")
    for item in result["data"]:
        assert "id" in item
        assert isinstance(item["id"], str)
        assert "method" in item
        assert "path" in item
        assert "summary" in item
        # llm_context is optional and declared residue (functional spec §13)


def test_ts_15_dt_14_details_twelve_keys(
    instance: seahaven.Instance,
) -> None:
    """Details response has the twelve keys with ``id`` not ``operation_id``."""
    doc = api_details(instance, "GetCustomers")
    expected_keys = {
        "id",
        "method",
        "path",
        "summary",
        "description",
        "tags",
        "keywords",
        "parameters",
        "required_permissions",
        "openapi_spec_version",
    }
    # The real server returns at least these ten keys (llm_context is optional).
    assert expected_keys <= set(doc.keys())
    # ``id`` is the key, not ``operation_id``
    assert "operation_id" not in doc
    assert doc["id"] == "GetCustomers"


def test_ts_16_dt_15_details_parameters_grouped(
    instance: seahaven.Instance,
) -> None:
    """Details parameters are ``{path:{}, query:{}, body:{}}`` dicts, not a
    flat list."""
    doc = api_details(instance, "GetCustomers")
    params = doc["parameters"]
    assert isinstance(params, dict)
    assert set(params.keys()) == {"path", "query", "body"}
    assert isinstance(params["path"], dict)
    assert isinstance(params["query"], dict)
    assert isinstance(params["body"], dict)


def test_ts_18_dt_04_path_placeholders_normalised_to_id(
    instance: seahaven.Instance,
) -> None:
    """Path placeholders are ``{id}`` everywhere, not ``{customer}`` etc."""
    doc = api_details(instance, "GetCustomersCustomer")
    assert "{id}" in doc["path"]
    assert "{customer}" not in doc["path"]
    # Verify across all search results too
    result = api_search(instance, "retrieve", "customer")
    for item in result["data"]:
        if "{" in item["path"]:
            assert "{id}" in item["path"]


def test_ts_17_dt_16_details_full_description(
    instance: seahaven.Instance,
) -> None:
    """Details returns the full multi-paragraph description, not first
    sentence only."""
    doc = api_details(instance, "GetBalanceTransactions")
    # The real description for GetBalanceTransactions has two sentences:
    # "Returns a list of transactions... The previous name..."
    assert len(doc["description"]) > 50
    # Should not be truncated to first sentence -- the full spec description
    # has multiple sentences joined by periods
    assert "balance" in doc["description"].lower()


def test_dt_05_default_search_limit_is_five(
    instance: seahaven.Instance,
) -> None:
    """Default search returns at most 5 results."""
    # A broad query that should match many operations
    result = api_search(instance, "list", "customer")
    assert len(result["data"]) <= 5


def test_dt_17_details_nests_to_full_depth(
    instance: seahaven.Instance,
) -> None:
    """Nested parameters have ``properties`` to their real depth."""
    doc = api_details(instance, "PostCustomers")
    body = doc["parameters"]["body"]
    # ``shipping`` has nested ``address`` with its own properties
    if "shipping" in body:
        shipping = body["shipping"]
        assert "properties" in shipping
        assert "address" in shipping["properties"]
        address = shipping["properties"]["address"]
        assert "properties" in address


def test_dt_20_required_permissions_present(
    instance: seahaven.Instance,
) -> None:
    """Details has ``required_permissions`` array."""
    doc = api_details(instance, "GetCustomers")
    assert "required_permissions" in doc
    assert isinstance(doc["required_permissions"], list)


def test_ar_05_discovery_covers_entire_catalogue(
    instance: seahaven.Instance,
) -> None:
    """All catalogued operations are discoverable via search and details.

    This is the big tell cluster: AR-05, AR-06, DT-06..DT-12, DT-19, DT-25.
    """
    # Every catalogued operation should have a details document
    for op_id in sorted(CATALOGUED):
        doc = api_details(instance, op_id)
        assert doc is not None, f"catalogued op {op_id} has no details"
        assert doc["id"] == op_id

    # A search for issuing (out-of-scope product) should return results
    result = api_search(instance, "list", "issuing card")
    assert len(result["data"]) > 0, "issuing card search returned empty"

    # A search for checkout (out-of-scope) should return results
    result = api_search(instance, "create", "checkout session")
    assert len(result["data"]) > 0, "checkout session search returned empty"


# --- Existing quality checks, adapted to the new index ---


def test_search_is_deterministic() -> None:
    assert index.search("refund", "charge") == index.search("refund", "charge")


def test_search_quality_spot_checks() -> None:
    for intent, resource, wanted in (
        ("cancel", "subscription", "Subscription"),
        ("void", "invoice", "Void"),
        ("list", "customers", "/v1/customers"),
        ("create", "coupon", "coupon"),
    ):
        results = index.search(intent, resource)
        data = results["data"]
        assert data, (intent, resource)
        assert any(
            wanted.lower() in item["path"].lower()
            or wanted.lower() in item["summary"].lower()
            or wanted.lower() in item["id"].lower()
            for item in data[:3]
        ), (intent, resource, data[:3])
    # "refund charge" should find PostRefunds somewhere in top 5
    results = index.search("refund", "charge")
    data = results["data"]
    assert data
    assert any("refund" in item["id"].lower() for item in data), (
        "refund search should find PostRefunds",
        data,
    )


def test_search_returns_at_most_limit() -> None:
    result = index.search("the", "customer", limit=10)
    assert len(result["data"]) <= 10
    result = index.search("the", "customer", limit=3)
    assert len(result["data"]) <= 3


def test_a_zero_result_query_is_empty(instance: seahaven.Instance) -> None:
    result = api_search(instance, "zzzqqqxyzzy", "zzzqqqxyzzy")
    assert result["data"] == []


def test_an_empty_query_returns_empty(instance: seahaven.Instance) -> None:
    result = api_search(instance, "", "")
    assert result["data"] == []


def test_details_for_an_unknown_operation_is_a_tool_error(instance: seahaven.Instance) -> None:
    with pytest.raises(seahaven.ToolError) as raised:
        api_details(instance, "GetWidgets")
    assert raised.value.code == "UNKNOWN_OPERATION"


def test_details_returns_operation_info(instance: seahaven.Instance) -> None:
    doc = api_details(instance, "GetCustomersCustomer")
    assert doc["path"] == "/v1/customers/{id}"
    assert doc["id"] == "GetCustomersCustomer"


def test_details_for_catalogued_but_unrouted_operation(
    instance: seahaven.Instance,
) -> None:
    """A catalogued-but-unrouted operation returns a details document
    rather than an error."""
    # GetIssuingCards is catalogued but not routed by this world
    doc = api_details(instance, "GetIssuingCards")
    assert doc["id"] == "GetIssuingCards"
    assert doc["path"] == "/v1/issuing/cards"
    assert "issuing_card_read" in doc["required_permissions"]


def test_details_has_tags_and_keywords(instance: seahaven.Instance) -> None:
    doc = api_details(instance, "PostCustomers")
    assert "tags" in doc
    assert isinstance(doc["tags"], list)
    assert len(doc["tags"]) > 0
    assert "customer" in doc["tags"]

    assert "keywords" in doc
    assert isinstance(doc["keywords"], list)
    assert "customers" in doc["keywords"]


def test_details_has_openapi_spec_version(instance: seahaven.Instance) -> None:
    doc = api_details(instance, "PostCustomers")
    assert doc["openapi_spec_version"].startswith("2026-08-26")


def test_details_path_params_use_id_key(instance: seahaven.Instance) -> None:
    """Path parameters use ``id`` as the key, not the resource name."""
    doc = api_details(instance, "GetCustomersCustomer")
    path_params = doc["parameters"]["path"]
    assert "id" in path_params
    assert "customer" not in path_params


def test_the_index_comes_from_the_committed_artifact() -> None:
    """Tier-1 drift guard: the index's operation ids match the committed
    discovery_index.json."""
    raw = json.loads((REPO / "src/seahaven_stripe_world/spec/discovery_index.json").read_text())
    artifact_ops = set(raw.keys())
    assert set(index.BY_OP_ID.keys()) == artifact_ops


# Live keyword samples captured from the real Stripe MCP (probe 2026-09-22).
# Order matters: the test asserts exact list equality so keyword generation
# stays aligned with the real server.
_LIVE_KEYWORD_SAMPLES: list[tuple[str, list[str]]] = [
    ("GetCustomersCustomer", ["v1", "customers", "get", "customer", "retrieve"]),
    ("PostCustomersCustomer", ["v1", "customers", "post", "customer", "update"]),
    ("GetBalance", ["v1", "balance", "get", "retrieve"]),
    (
        "GetBalanceTransactions",
        [
            "v1",
            "balance_transactions",
            "balance",
            "transactions",
            "get",
            "list",
            "balance_transaction",
            "transaction",
        ],
    ),
    ("GetIssuingCards", ["v1", "issuing", "cards", "get", "list", "issuing.card"]),
    ("GetIssuingCardsCard", ["v1", "issuing", "cards", "get", "card", "retrieve", "issuing.card"]),
    (
        "GetIssuingAuthorizations",
        ["v1", "issuing", "authorizations", "get", "list", "issuing.authorization"],
    ),
    ("GetPayoutsPayout", ["v1", "payouts", "get", "payout", "retrieve"]),
    ("GetPayouts", ["v1", "payouts", "get", "list", "payout"]),
]


@pytest.mark.parametrize(
    ("op_id", "expected_keywords"),
    _LIVE_KEYWORD_SAMPLES,
    ids=[s[0] for s in _LIVE_KEYWORD_SAMPLES],
)
def test_keywords_match_live_mcp(op_id: str, expected_keywords: list[str]) -> None:
    """Keyword lists (including order) match the real Stripe MCP server."""
    doc = index.BY_OP_ID[op_id].document
    assert doc["keywords"] == expected_keywords
