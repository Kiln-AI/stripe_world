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
    DECLARED_OVERRIDES,
    DECLINE_CODES,
    DOC_ONLY_ENUMS,
    ENUM_OVERRIDES,
    EXTRA_DOC_ONLY_ENUMS,
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


def _apply_enum_override_schemas(schemas: dict) -> None:
    """Give every (schema, field) pair the enum-override assertion walks a
    live-shaped property: merged into the hand-written schemas rather than
    replacing them (`payment_method_details_card` exists in both), with a
    description naming every hand-transcribed value — synthesized from the
    tables themselves so the two cannot drift apart."""
    merged: dict[str, dict[str, tuple[str, ...]]] = {}
    for table in (DOC_ONLY_ENUMS, EXTRA_DOC_ONLY_ENUMS):
        for obj, fields in table.items():
            merged.setdefault(obj, {}).update(fields)
    for obj, fields in merged.items():
        props = schemas.setdefault(obj, {"properties": {}})["properties"]
        for field, values in fields.items():
            props[field] = {
                "type": "string",
                "description": "Values: " + ", ".join(f"`{value}`" for value in values) + ".",
            }


def synthetic_spec() -> dict:
    """A spec just rich enough to trigger every transform: a stoplisted union
    member, a stoplisted direct ref, an all-stoplisted union, rail fan-out
    properties, HTML prose, and cut operations under a real resource root.
    The response schema carries an `object` discriminator (the rule builder's
    roots) and `_apply_enum_override_schemas` adds the override fields with
    live-shaped descriptions (the description-token assertion)."""
    spec = {
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
                    "required": ["card"],
                    "properties": {
                        "object": {"enum": ["widget"], "type": "string"},
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
                        # How the real spec reaches its deleted_* stubs: a
                        # nullable union member (invoice.customer carries
                        # deleted_customer exactly this way).
                        "prior": {"anyOf": [_ref("deleted_widget")], "nullable": True},
                    },
                },
                "deleted_widget": {
                    "required": ["id", "object", "deleted"],
                    "properties": {
                        "id": {"type": "string"},
                        "object": {"enum": ["widget"], "type": "string"},
                        "deleted": {"enum": [True], "type": "boolean"},
                    },
                },
                "widget_detail": {
                    "properties": {
                        "note": {"type": "string"},
                        "meta": {"additionalProperties": {"type": "string"}, "type": "object"},
                        "tags": {"items": {"type": "string"}, "type": "array"},
                    }
                },
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
    _apply_enum_override_schemas(spec["components"]["schemas"])
    return spec


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
    assert set(widget["properties"]) == {
        "object",
        "card",
        "owner",
        "tax",
        "ledger",
        "detail",
        "prior",
    }
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
    assert artifacts.enum_overrides == ENUM_OVERRIDES
    assert artifacts.declared_overrides == DECLARED_OVERRIDES
    assert artifacts.decline_codes == DECLINE_CODES  # hand-transcribed, carried verbatim
    assert artifacts.event_types == EVENTS


# --- the enum-override description-token assertion ------------------------------


def test_a_value_missing_from_the_live_description_raises() -> None:
    spec = synthetic_spec()
    prop = spec["components"]["schemas"]["refund"]["properties"]["status"]
    prop["description"] = prop["description"].replace("`canceled`", "")
    with pytest.raises(GenerationError, match="no longer"):
        build(spec)


def test_a_machine_enum_where_a_bare_string_was_raises() -> None:
    spec = synthetic_spec()
    spec["components"]["schemas"]["payout"]["properties"]["method"]["enum"] = ["standard"]
    with pytest.raises(GenerationError, match="no longer a bare string"):
        build(spec)


def test_a_missing_override_schema_raises() -> None:
    spec = synthetic_spec()
    del spec["components"]["schemas"]["dispute"]
    with pytest.raises(GenerationError, match="no schema"):
        build(spec)


# --- the schema-conformance rule set --------------------------------------------


def test_rules_key_discriminated_roots_and_deleted_stubs() -> None:
    rules = build(synthetic_spec()).schema_rules
    assert rules["by_object"] == {"widget": "widget"}
    assert rules["deleted_by_object"] == {"widget": "deleted_widget"}
    # Only response-reachable schemas: the enum-override stubs are in the
    # synthetic spec but nothing routes to them, so no rules for them.
    assert set(rules["rules"]) == {
        "widget",
        "deleted_widget",
        "widget_detail",
        "payment_method_card",
    }


def test_rules_normalize_unions_maps_arrays_and_enums() -> None:
    rules = build(synthetic_spec()).schema_rules["rules"]
    assert rules["widget"]["props"]["owner"] == {"any": [{"t": "string"}], "nul": True}
    assert rules["widget"]["props"]["object"] == {"t": "string", "enum": ["widget"]}
    assert rules["widget_detail"]["props"]["meta"] == {
        "t": "object",
        "map": {"t": "string"},
    }
    assert rules["widget_detail"]["props"]["tags"] == {"t": "array", "items": {"t": "string"}}
    assert rules["deleted_widget"]["props"]["deleted"] == {"t": "boolean", "enum": [True]}
    assert rules["widget"]["req"] == ["card"]


def test_an_allof_outside_x_keys_raises() -> None:
    spec = synthetic_spec()
    spec["components"]["schemas"]["widget"]["properties"]["merged"] = {
        "allOf": [_ref("widget_detail"), {"type": "object"}]
    }
    with pytest.raises(GenerationError, match="allOf"):
        build(spec)


def test_additional_properties_false_stays_closed_and_true_stays_open() -> None:
    spec = synthetic_spec()
    spec["components"]["schemas"]["widget_detail"]["properties"]["shut"] = {
        "additionalProperties": False,
        "type": "object",
    }
    spec["components"]["schemas"]["widget_detail"]["properties"]["anything"] = {
        "additionalProperties": True,
        "type": "object",
    }
    rules = build(spec).schema_rules["rules"]["widget_detail"]["props"]
    # Closed: no `map` key at all, so the (empty) property set governs.
    assert rules["shut"] == {"t": "object", "props": {}}
    # Open: `map` present and null, so any key is accepted.
    assert rules["anything"] == {"t": "object", "map": None}


def test_a_bare_additional_properties_false_node_is_a_closed_object() -> None:
    spec = synthetic_spec()
    spec["components"]["schemas"]["widget_detail"]["properties"]["shut"] = {
        "additionalProperties": False
    }
    rules = build(spec).schema_rules["rules"]["widget_detail"]["props"]
    # An *empty closed set*, not a bare `t: object` (which the validator
    # reads as open) — so a spec bump introducing this shape cannot silently
    # open a closed set.
    assert rules["shut"] == {"props": {}}


def test_an_unrecognized_additional_properties_shape_raises() -> None:
    spec = synthetic_spec()
    spec["components"]["schemas"]["widget_detail"]["properties"]["odd"] = {
        "additionalProperties": "sometimes",
        "type": "object",
    }
    with pytest.raises(GenerationError, match="additionalProperties"):
        build(spec)


def test_a_non_dict_property_rule_raises() -> None:
    """The rules contract's third clause: a property rule is a dict or None.
    `_normalize_rule` cannot produce anything else today, so the guard is
    exercised directly — it stands against a future normalizer change."""
    from tools_dev.prune_spec import _assert_rule_shapes

    with pytest.raises(GenerationError, match="property rule"):
        _assert_rule_shapes("widget", {"props": {"note": ["not", "a", "dict"]}})


def test_no_discriminated_roots_raises() -> None:
    spec = synthetic_spec()
    del spec["components"]["schemas"]["widget"]["properties"]["object"]
    del spec["components"]["schemas"]["deleted_widget"]
    with pytest.raises(GenerationError, match="no discriminated object"):
        build(spec)


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


# --- §8a: request bodies filtered to the wired ParamSpec -----------------------


def test_a_wired_route_filters_its_request_body_to_the_allowlist() -> None:
    """Discovery §8a: `stripe_api_details` may not document a parameter the
    write tool rejects. A wired route's body keeps its `ParamSpec`'s names
    plus the centrally-handled `expand` and `metadata` — and `required`
    follows."""
    from stripeapi.dispatch.params import Param, ParamSpec

    spec = synthetic_spec()
    spec["paths"]["/v1/prices"]["post"]["requestBody"]["content"][
        "application/x-www-form-urlencoded"
    ] = spec["paths"]["/v1/prices"]["post"]["requestBody"]["content"].pop("application/json")
    body = spec["paths"]["/v1/prices"]["post"]["requestBody"]["content"][
        "application/x-www-form-urlencoded"
    ]["schema"]
    body["properties"] = {
        "name": {"type": "string"},
        "nickname": {"type": "string"},
        "on_behalf_of": {"type": "string"},  # Connect: out of scope, must go
        "expand": {"type": "array"},
        "metadata": {"type": "object"},
        "limit": {"type": "integer"},  # central on lists only, not on a create
    }
    body["required"] = ["name", "on_behalf_of"]

    wired = Route(
        method="POST",
        pattern="/v1/prices",
        op_id="PostPrices",
        params=ParamSpec(
            op_id="PostPrices",
            body=(Param(name="name", kind="string", required=True),),
            metadata=True,
        ),
    )
    artifacts = build_artifacts(spec, (ROUTES[0], wired), EVENTS)
    served = artifacts.spec3_min["paths"]["/v1/prices"]["post"]["requestBody"]["content"][
        "application/x-www-form-urlencoded"
    ]["schema"]
    assert set(served["properties"]) == {"name", "expand", "metadata"}
    assert served["required"] == ["name"]
    # An unwired body would have kept all six properties verbatim.


def test_an_unwired_route_keeps_its_body_verbatim() -> None:
    """Until a route's resource phase wires its `ParamSpec`, its body is
    served as the spec wrote it — the interim rule that lets the slices land
    one at a time."""
    spec = synthetic_spec()
    body = spec["paths"]["/v1/prices"]["post"]["requestBody"]["content"]["application/json"][
        "schema"
    ]
    body["properties"]["on_behalf_of"] = {"type": "string"}
    artifacts = build(spec)
    served = artifacts.spec3_min["paths"]["/v1/prices"]["post"]["requestBody"]["content"][
        "application/json"
    ]["schema"]
    assert set(served["properties"]) == {"name", "on_behalf_of"}


def test_a_wired_route_filters_its_query_parameters_to_the_allowlist() -> None:
    """The query half of §8a: the spec's `parameters` arrive verbatim
    (`test_clock`-shaped cut filters included) and must be reduced to the
    enforced set — the body's names, the path placeholders, and the central
    parameters the operation actually accepts."""
    from stripeapi.dispatch.params import ParamSpec
    from stripeapi.resources.customers import SPEC

    spec = synthetic_spec()
    spec["paths"]["/v1/prices/{id}"] = {
        "get": {
            "operationId": "GetPricesId",
            "parameters": [
                {"in": "path", "name": "id", "required": True, "schema": {"type": "string"}},
                {"in": "query", "name": "email", "schema": {"type": "string"}},
                {"in": "query", "name": "test_clock", "schema": {"type": "string"}},
                {"in": "query", "name": "expand", "schema": {"type": "array"}},
                {"in": "query", "name": "limit", "schema": {"type": "integer"}},
                {"in": "query", "name": "starting_after", "schema": {"type": "string"}},
                {"in": "query", "name": "ending_before", "schema": {"type": "string"}},
            ],
            "responses": {},
        }
    }
    wired_get = Route(
        method="GET",
        pattern="/v1/prices/{id}",
        op_id="GetPricesId",
        params=ParamSpec(op_id="GetPricesId", path=("id",), paginated=True),
        resource=SPEC,
        action="list",
    )

    unwired_post = Route(method="POST", pattern="/v1/prices", op_id="PostPrices")
    artifacts = build_artifacts(spec, (wired_get, unwired_post), EVENTS)

    served = artifacts.spec3_min["paths"]["/v1/prices/{id}"]["get"]["parameters"]
    assert [(p["in"], p["name"]) for p in served] == [
        ("path", "id"),
        ("query", "email"),
        ("query", "expand"),
        ("query", "limit"),
        ("query", "starting_after"),
        ("query", "ending_before"),
    ]
    # The unwired route keeps everything, path and query alike.
    served_post = artifacts.spec3_min["paths"]["/v1/prices"]["post"]
    assert served_post.get("parameters") is None


def test_a_stub_delete_drops_expand_from_its_query_parameters() -> None:
    """One of the nine `expand=False` stub DELETEs: `expand` is rejected, so
    it must not be documented."""
    from stripeapi.dispatch.params import ParamSpec
    from stripeapi.resources.customers import SPEC

    spec = synthetic_spec()
    spec["paths"]["/v1/prices/{id}"] = {
        "delete": {
            "operationId": "DeletePricesId",
            "parameters": [
                {"in": "path", "name": "id", "required": True, "schema": {"type": "string"}},
                {"in": "query", "name": "expand", "schema": {"type": "array"}},
            ],
            "responses": {},
        }
    }
    wired = Route(
        method="DELETE",
        pattern="/v1/prices/{id}",
        op_id="DeletePricesId",
        params=ParamSpec(op_id="DeletePricesId", path=("id",), expand=False),
        resource=SPEC,
        action="delete",
    )
    artifacts = build_artifacts(spec, (wired,), EVENTS)
    served = artifacts.spec3_min["paths"]["/v1/prices/{id}"]["delete"]["parameters"]
    assert [(p["in"], p["name"]) for p in served] == [("path", "id")]


def test_central_parameters_are_gated_on_their_param_spec_flags() -> None:
    """`expand` and `metadata` are documented exactly where the dispatcher
    accepts them: a wired route with `metadata=False` whose spec body carries
    `metadata` must have it filtered out (and the mirrored `expand=False`
    case on the body side likewise) — the §8a hole one parameter over."""
    from stripeapi.dispatch.params import Param, ParamSpec
    from stripeapi.resources.customers import SPEC

    spec = synthetic_spec()
    spec["paths"]["/v1/prices/{id}"] = {
        "post": {
            "operationId": "PostPricesId",
            "parameters": [{"in": "path", "name": "id", "schema": {"type": "string"}}],
            "requestBody": {
                "content": {
                    "application/x-www-form-urlencoded": {
                        "schema": {
                            "properties": {
                                "name": {"type": "string"},
                                "metadata": {"type": "object"},
                                "expand": {"type": "array"},
                            },
                            "required": ["metadata"],
                        }
                    }
                }
            },
            "responses": {},
        }
    }
    wired = Route(
        method="POST",
        pattern="/v1/prices/{id}",
        op_id="PostPricesId",
        params=ParamSpec(
            op_id="PostPricesId",
            path=("id",),
            body=(Param(name="name", kind="string"),),
            expand=False,
            metadata=False,
        ),
        resource=SPEC,
        action="update",
    )
    artifacts = build_artifacts(spec, (wired,), EVENTS)
    served = artifacts.spec3_min["paths"]["/v1/prices/{id}"]["post"]["requestBody"]["content"][
        "application/x-www-form-urlencoded"
    ]["schema"]
    assert set(served["properties"]) == {"name"}
    assert "required" not in served
