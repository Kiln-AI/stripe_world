"""The pruner's rules, exercised against a small synthetic spec so every rule
runs in CI without the 8 MB source artifact (components/discovery.md's test
plan; the tier-2 byte-diff against the real spec lives in
test_spec_artifacts.py).
"""

import copy
import json

import pytest
from tools_dev.prune_spec import (
    API_VERSION,
    DOC_ONLY_ENUMS,
    SCHEMA_BUDGET,
    GenerationError,
    _dump_canonical,
    build_artifacts,
    derive_operations,
)

from stripeapi.dispatch.routes import Route

EVENTS = frozenset({"invoice.paid", "customer.created"})


def _ref(name: str) -> dict:
    return {"$ref": f"#/components/schemas/{name}"}


def synthetic_spec() -> dict:
    """A spec just rich enough to trigger every transform: a stoplisted union
    member, a stoplisted direct ref, an all-stoplisted union, rail fan-out
    properties, HTML prose, and cut operations under a real resource root."""
    return {
        "openapi": "3.0.0",
        "info": {"version": API_VERSION, "title": "synthetic"},
        "paths": {
            "/v1/prices": {
                "get": {
                    "operationId": "GetPrices",
                    "summary": "<b>List</b> prices &amp; such",
                    "description": "<p>Lists <code>widget</code> objects.</p>",
                    "responses": {
                        "200": {"content": {"application/json": {"schema": _ref("widget")}}}
                    },
                },
                "post": {
                    "operationId": "PostPrices",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "properties": {"name": {"type": "string"}},
                                    "description": "Create a <em>widget</em>.",
                                }
                            }
                        }
                    },
                    "responses": {
                        "200": {"content": {"application/json": {"schema": _ref("widget")}}}
                    },
                },
            },
            "/v1/prices/search": {
                "get": {"operationId": "GetPricesSearch", "responses": {}},
            },
            "/v1/balance/history": {
                "get": {"operationId": "GetBalanceHistory", "responses": {}},
            },
            "/v1/customers/{customer}/bank_accounts": {
                "get": {"operationId": "GetCustomersCustomerBankAccounts", "responses": {}},
            },
        },
        "components": {
            "schemas": {
                "payment_method": {
                    "properties": {"type": {"enum": ["card", "klarna", "sepa_debit"]}},
                },
                "widget": {
                    "x-expandableFields": ["card", "klarna", "owner", "detail"],
                    "required": ["card", "klarna"],
                    "properties": {
                        "card": _ref("payment_method_card"),
                        "klarna": _ref("payment_method_klarna"),
                        "sepa_debit": _ref("payment_method_sepa_debit"),
                        "detail": _ref("widget_detail"),
                        "owner": {
                            "anyOf": [{"type": "string"}, _ref("account")],
                            "nullable": True,
                            "x-expansionResources": {"oneOf": [_ref("account")]},
                        },
                        "tax": _ref("customer_tax"),
                        "ledger": {
                            "anyOf": [_ref("transfer"), _ref("reserve_transaction")],
                            "nullable": True,
                        },
                    },
                },
                "widget_detail": {"properties": {"note": {"type": "string"}}},
                "payment_method_card": {"properties": {"last4": {"type": "string"}}},
                "payment_method_klarna": {"properties": {"dob": {"type": "string"}}},
                "payment_method_sepa_debit": {"properties": {"iban": {"type": "string"}}},
                "account": {"properties": {"country": {"type": "string"}}},
                "transfer": {"properties": {"amount": {"type": "integer"}}},
                "reserve_transaction": {"properties": {"amount": {"type": "integer"}}},
                "customer_tax": {"properties": {"enabled": {"type": "boolean"}}},
            }
        },
    }


ROUTES = (
    Route(method="GET", pattern="/v1/prices", op_id="GetPrices"),
    Route(method="POST", pattern="/v1/prices", op_id="PostPrices"),
)


def build(spec: dict):
    return build_artifacts(spec, ROUTES, EVENTS)


# --- route/spec validation ----------------------------------------------------


def test_unmatched_route_raises() -> None:
    spec = synthetic_spec()
    bad = Route(method="DELETE", pattern="/v1/prices/{id}", op_id="DeleteWidgetsId")
    with pytest.raises(GenerationError, match="no match"):
        build_artifacts(spec, (*ROUTES, bad), EVENTS)


def test_wrong_op_id_raises() -> None:
    spec = synthetic_spec()
    bad = Route(method="GET", pattern="/v1/prices", op_id="TotallyDifferent")
    with pytest.raises(GenerationError, match="operationId"):
        build_artifacts(spec, (bad,), EVENTS)


def test_duplicate_route_raises() -> None:
    with pytest.raises(GenerationError, match="duplicate"):
        build_artifacts(synthetic_spec(), (*ROUTES, ROUTES[0]), EVENTS)


def test_wrong_api_version_raises() -> None:
    spec = synthetic_spec()
    spec["info"]["version"] = "2020-01-01.old"
    with pytest.raises(GenerationError, match="pinned"):
        build_artifacts(spec, ROUTES, EVENTS)


# --- derive_operations: the 39 cuts -------------------------------------------


def test_derive_operations_cuts_search_history_and_legacy_subresources() -> None:
    spec = synthetic_spec()
    derived = derive_operations(spec, expected_count=2)
    assert [(r.method, r.pattern) for r in derived] == [
        ("GET", "/v1/prices"),
        ("POST", "/v1/prices"),
    ]


def test_derive_operations_count_mismatch_raises() -> None:
    with pytest.raises(GenerationError, match="expected exactly"):
        derive_operations(synthetic_spec())


# --- the stoplist --------------------------------------------------------------


def test_stoplisted_union_member_leaves_the_string_sibling() -> None:
    owner = build(synthetic_spec()).spec3_min["components"]["schemas"]["widget"]["properties"][
        "owner"
    ]
    assert owner == {"anyOf": [{"type": "string"}], "nullable": True}


def test_emptied_x_expansion_resources_is_dropped() -> None:
    owner = build(synthetic_spec()).spec3_min["components"]["schemas"]["widget"]["properties"][
        "owner"
    ]
    assert "x-expansionResources" not in owner


def test_direct_ref_to_stoplisted_schema_becomes_null() -> None:
    tax = build(synthetic_spec()).spec3_min["components"]["schemas"]["widget"]["properties"]["tax"]
    assert tax == {"type": "null"}


def test_all_stoplisted_union_becomes_the_string_stub() -> None:
    ledger = build(synthetic_spec()).spec3_min["components"]["schemas"]["widget"]["properties"][
        "ledger"
    ]
    assert ledger == {"type": "string", "nullable": True}


def test_stoplisted_refs_nested_in_union_members_are_recursed() -> None:
    spec = synthetic_spec()
    spec["components"]["schemas"]["widget"]["properties"]["nested"] = {
        "anyOf": [{"allOf": [_ref("account"), _ref("transfer")]}, {"type": "string"}]
    }
    nested = build(spec).spec3_min["components"]["schemas"]["widget"]["properties"]["nested"]
    assert nested == {"anyOf": [{"type": "string"}, {"type": "string"}]}


def test_stoplisted_schemas_leave_the_closure() -> None:
    schemas = build(synthetic_spec()).spec3_min["components"]["schemas"]
    for name in ("account", "transfer", "reserve_transaction", "customer_tax"):
        assert name not in schemas


# --- the rail trim -------------------------------------------------------------


def test_stubbed_rails_drop_from_properties_required_and_expandable() -> None:
    widget = build(synthetic_spec()).spec3_min["components"]["schemas"]["widget"]
    assert set(widget["properties"]) == {"card", "owner", "tax", "ledger", "detail"}
    assert widget["required"] == ["card"]
    assert tuple(widget["x-expandableFields"]) == ("card", "owner", "detail")


def test_stubbed_rail_schemas_leave_the_closure() -> None:
    schemas = build(synthetic_spec()).spec3_min["components"]["schemas"]
    assert "payment_method_card" in schemas
    assert "payment_method_klarna" not in schemas
    assert "payment_method_sepa_debit" not in schemas


# --- prose, structure, determinism ---------------------------------------------


def test_html_is_stripped_from_summaries_and_descriptions() -> None:
    paths = build(synthetic_spec()).spec3_min["paths"]
    assert paths["/v1/prices"]["get"]["summary"] == "List prices & such"
    assert paths["/v1/prices"]["get"]["description"] == "Lists widget objects."
    body = paths["/v1/prices"]["post"]["requestBody"]["content"]["application/json"]["schema"]
    assert body["description"] == "Create a widget."


def test_operations_carry_only_the_six_kept_keys() -> None:
    get_op = build(synthetic_spec()).spec3_min["paths"]["/v1/prices"]["get"]
    assert set(get_op) == {"summary", "description", "operationId", "responses"}


def test_two_runs_are_byte_identical() -> None:
    first = build(synthetic_spec())
    second = build(synthetic_spec())
    assert _dump_canonical(first.spec3_min) == _dump_canonical(second.spec3_min)
    assert first.event_types == second.event_types


def test_artifacts_carry_enums_and_event_types_verbatim() -> None:
    artifacts = build(synthetic_spec())
    assert artifacts.enums == DOC_ONLY_ENUMS
    assert artifacts.event_types == EVENTS


def test_expandable_takes_only_non_empty_arrays() -> None:
    spec = synthetic_spec()
    spec["components"]["schemas"]["widget_detail"]["x-expandableFields"] = []
    artifacts = build(spec)
    assert artifacts.expandable == {"widget": ("card", "owner", "detail")}


def test_closure_budget_is_enforced() -> None:
    spec = synthetic_spec()
    filler = {
        f"filler_{i}": {"properties": {"next": _ref(f"filler_{i + 1}")}}
        for i in range(SCHEMA_BUDGET)
    }
    spec["components"]["schemas"]["widget"]["properties"]["first_filler"] = _ref("filler_0")
    spec["components"]["schemas"].update(filler)
    with pytest.raises(GenerationError, match="budget"):
        build(spec)


def test_build_does_not_mutate_the_input_spec() -> None:
    spec = synthetic_spec()
    pristine = copy.deepcopy(spec)
    build(spec)
    assert spec == pristine


def test_canonical_dump_is_sorted_and_compact() -> None:
    blob = _dump_canonical({"b": 1, "a": {"y": 1, "x": 2}})
    assert blob == '{"a":{"x":2,"y":1},"b":1}'
    json.loads(blob)
