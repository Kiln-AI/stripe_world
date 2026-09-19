"""Tier-1 consistency of the committed rule artifact: recomputable from
`spec3.min.json` alone, so CI checks it without the 8 MB source spec (the
tier-2 regeneration byte-diff lives in `test_spec_artifacts.py`).
"""

from __future__ import annotations

from typing import Any

from schema_conformance.validate import BY_OBJECT, DELETED_BY_OBJECT, RULES
from stripeapi.dispatch.routes import ALL as ROUTES
from stripeapi.spec import ENUM_OVERRIDES, schema_rules, spec_document


def _discriminate(body: dict[str, Any]) -> str | None:
    obj = body.get("properties", {}).get("object")
    if (
        isinstance(obj, dict)
        and isinstance(obj.get("enum"), list)
        and len(obj["enum"]) == 1
        and isinstance(obj["enum"][0], str)
    ):
        return obj["enum"][0]
    return None


def test_rules_match_the_committed_spec_discriminators() -> None:
    """The maps are a pure function of `spec3.min.json`: recompute them, and
    they must equal what the validator loaded."""
    by_object: dict[str, str] = {}
    deleted: dict[str, str] = {}
    for name, body in spec_document()["components"]["schemas"].items():
        value = _discriminate(body)
        if value is None:
            continue
        (deleted if name.startswith("deleted_") else by_object)[value] = name
    assert by_object == BY_OBJECT
    assert deleted == DELETED_BY_OBJECT


def test_every_wired_resource_has_a_schema() -> None:
    """The authority behind the registered-resource exemption: any resource
    the router can actually answer for must have a schema the walker can
    find, or the exemption would silently skip it. Derived from the routing
    table so a later phase wiring its routes extends the guarantee with no
    edit here — unwired routes carry no `response_object` until their slice
    lands, which is exactly when this assertion starts covering them."""
    wired = {route.response_object for route in ROUTES if route.response_object}
    assert wired, "no route is wired; the exemption has no authority to lean on"
    assert wired <= set(BY_OBJECT) | set(DELETED_BY_OBJECT)


def test_the_artifact_round_trips_through_the_loader() -> None:
    doc = schema_rules()
    assert set(doc) == {"by_object", "deleted_by_object", "rules"}
    assert doc["rules"].keys() == RULES.keys()


def _refs(node: Any) -> set[str]:
    if isinstance(node, dict):
        return ({node["ref"]} if "ref" in node else set()) | {
            ref for value in node.values() for ref in _refs(value)
        }
    if isinstance(node, list):
        return {ref for member in node for ref in _refs(member)}
    return set()


def test_every_ref_in_the_rules_resolves() -> None:
    for name, node in RULES.items():
        dangling = _refs(node) - RULES.keys()
        assert not dangling, f"{name}: {sorted(dangling)}"


def test_rule_enums_carry_only_strings_and_booleans() -> None:
    def enums(node: Any) -> list[list[Any]]:
        if isinstance(node, dict):
            return (
                [node["enum"]]
                if "enum" in node
                else [e for value in node.values() for e in enums(value)]
            )
        if isinstance(node, list):
            return [e for member in node for e in enums(member)]
        return []

    for name, node in RULES.items():
        for values in enums(node):
            assert all(type(value) in (str, bool) for value in values), (name, values)


def test_enum_overrides_point_at_real_rules() -> None:
    """Every closed-set table entry names a schema the rules actually carry
    and a field that schema actually has — otherwise the override would be a
    silent no-op."""
    for obj, fields in ENUM_OVERRIDES.items():
        props = RULES.get(obj, {}).get("props")
        assert isinstance(props, dict), obj
        for field in fields:
            assert field in props, (obj, field)
