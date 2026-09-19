"""The schema-conformance validator itself, and the hook that runs it.

The corpus rule (components/conformance.md, "Schema conformance"): every
object any test produces is validated — but this module's *unit* tests may
construct objects directly, because the fastest way to prove a violation is
caught is to plant one. ``minimal_object`` builds a valid instance of any
schema from the generated rules, so a planted mutation is the only
difference from a passing object.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest
import seahaven

from conftest import BLANK_NOW
from schema_conformance import capture
from schema_conformance.validate import (
    DELETED_BY_OBJECT,
    NULLABLE_DESPITE_SPEC,
    RULES,
    SchemaViolation,
    validate_object,
    violations_in_body,
)
from stripeapi.spec import DECLARED_OVERRIDES, ENUM_OVERRIDES

REPO = Path(__file__).resolve().parents[2]


def minimal_object(schema_name: str, **overrides: Any) -> dict[str, Any]:
    """A valid instance of ``schema_name`` per the generated rules: every
    required field filled with a type-correct value (an enum's first value, a
    bare-string field's first override value where one exists), optional
    fields absent, ``overrides`` applied last."""
    filled = _fill(RULES[schema_name], schema_name)
    assert isinstance(filled, dict)
    filled.update(overrides)
    return filled


def _fill(rule: Any, owner: str | None = None, field: str | None = None) -> Any:
    if rule is None:
        return None
    if "ref" in rule:
        return _fill(RULES[rule["ref"]], rule["ref"])
    if "any" in rule:
        return _fill(rule["any"][0], owner, field) if rule["any"] else None
    if "enum" in rule and isinstance(rule["enum"], list) and rule["enum"]:
        return rule["enum"][0]
    if rule.get("t") == "string" and owner and field:
        override = ENUM_OVERRIDES.get(owner, {}).get(field)
        if override:
            return override[0]
    if "props" in rule or rule.get("t") == "object":
        props: dict[str, Any] = rule.get("props") or {}
        owner_name = owner
        return {name: _fill(props[name], owner_name, name) for name in rule.get("req", ())}
    return {"string": "x", "integer": 1, "number": 1, "boolean": True, "array": [], "null": None}[
        rule.get("t", "string")
    ]


# --- the happy path: real objects, through the real tools -----------------------

pytestmark = pytest.mark.seahaven(fixture=None, now=BLANK_NOW)


def test_a_live_customer_validates(
    instance: seahaven.Instance, _schema_conformance: list[capture.CapturedCall]
) -> None:
    """A whole create-read-list round trip through the registered tools
    validates clean — and the hook captured it without the test opting in."""
    created = instance.call("stripe_api_write", method="POST", path="/v1/customers", params={})
    read = instance.call("stripe_api_read", path=f"/v1/customers/{created['body']['id']}")
    listed = instance.call("stripe_api_read", path="/v1/customers")
    assert validate_object(created["body"], source="live") == []
    assert violations_in_body(read["body"], source="live") == []
    assert violations_in_body(listed["body"], source="live") == []
    assert [call.tool for call in _schema_conformance] == [
        "stripe_api_write",
        "stripe_api_read",
        "stripe_api_read",
    ]


def test_a_deleted_stub_validates(instance: seahaven.Instance) -> None:
    """The soft-delete stub `{id, object, deleted: true}` validates against
    `deleted_customer`, and the 404 a missing id earns has nothing to flag."""
    missing = instance.call("stripe_api_write", method="DELETE", path="/v1/customers/cus_1")
    assert missing["status"] == 404
    assert violations_in_body(missing["body"], source="live") == []
    assert (
        violations_in_body({"id": "cus_1", "object": "customer", "deleted": True}, source="unit")
        == []
    )


# --- planted violations ----------------------------------------------------------


def test_a_planted_bad_enum_value_fails() -> None:
    dispute = minimal_object("dispute", reason="not_a_real_reason")
    violations = validate_object(dispute, source="unit")
    assert [(v.path, "not_a_real_reason" in v.problem) for v in violations] == [("reason", True)]


def test_a_machine_readable_enum_is_checked_type_strictly() -> None:
    charge = minimal_object("charge", status="not_a_charge_status")
    (violation,) = validate_object(charge, source="unit")
    assert violation.path == "status"
    # And bool never passes for int: `deleted`'s enum is [true], `1` is not it.
    stub = {"id": "cus_1", "object": "customer", "deleted": 1}
    assert any(v.path == "deleted" for v in validate_object(stub, source="unit"))


def test_a_bad_brand_inside_the_nested_card_is_caught_with_its_full_path() -> None:
    """The card-rail override fields are reached through the owning schema's
    name, not a hand-listed path — `payment_method_card` has no `object`
    discriminator of its own, so only the structural walk finds it."""
    payment_method = minimal_object("payment_method")
    payment_method["card"] = dict(minimal_object("payment_method_card"), brand="AMEX")
    violations = validate_object(payment_method, source="unit")
    assert [(v.path, v.problem) for v in violations] == [
        (
            "card.brand",
            "expected one of amex, cartes_bancaires, diners, discover, eftpos_au, "
            "jcb, link, mastercard, unionpay, visa, unknown, got 'AMEX'",
        )
    ]


def test_a_bad_brand_inside_the_charge_wire_path_is_caught() -> None:
    """The same enum under each of its schema names: charge's
    payment_method_details.card is `payment_method_details_card`, the copy
    Phase 8's magic-card serializer writes through — a bad value there must
    not skate past because the storage-shape entry is the only one enrolled."""
    charge = minimal_object("charge")
    details = minimal_object("payment_method_details")
    details["card"] = dict(minimal_object("payment_method_details_card"), brand="AMEX")
    charge["payment_method_details"] = details
    violations = validate_object(charge, source="unit")
    (violation,) = [v for v in violations if v.path.endswith("card.brand")]
    assert violation.path == "payment_method_details.card.brand"
    assert "expected one of amex" in violation.problem


def test_the_pass2_prose_closed_fields_are_enrolled() -> None:
    """One spot-pin per enrolled family, validated through the wire path that
    reaches each schema (embedded schemas carry no `object` discriminator)."""
    cases = (
        ("charge_outcome", "network_status", "approved"),
        ("charge_fraud_details", "user_report", "not_safe"),
        ("payment_method_card_checks", "cvc_check", "maybe"),
        ("refund", "failure_reason", "just_because"),
        ("refund_destination_details_card", "reference_status", "elsewhere"),
        ("invoice_payment", "status", "refunded"),
        ("payouts_trace_id", "status", "lost"),
        ("dispute_payment_method_details_card", "brand", "AMEX"),
    )
    for schema_name, field, planted in cases:
        obj = minimal_object(schema_name, **{field: planted})
        violations = _violations_through_the_wire_path(schema_name, obj)
        assert any(
            v.problem.startswith("expected one of") and planted in v.problem for v in violations
        ), (schema_name, field)


def _violations_through_the_wire_path(
    schema_name: str, obj: dict[str, Any]
) -> list[SchemaViolation]:
    parent, field = {
        "charge_outcome": ("charge", "outcome"),
        "charge_fraud_details": ("charge", "fraud_details"),
        "payment_method_card_checks": ("payment_method", "card"),
        "payment_method_details_card": ("charge", "payment_method_details"),
        "dispute_payment_method_details_card": ("dispute", "payment_method_details"),
        "refund": ("refund", None),
        "refund_destination_details_card": ("refund", "destination_details"),
        "invoice_payment": ("invoice_payment", None),
        "payouts_trace_id": ("payout", "trace_id"),
    }[schema_name]
    if field is None:
        return validate_object(obj, source="unit")
    top = minimal_object(parent)
    if schema_name == "payment_method_card_checks":
        card = minimal_object("payment_method_card")
        card["checks"] = obj
        top[field] = card
    elif schema_name == "refund_destination_details_card":
        # refund.destination_details wraps the per-rail schemas one level down.
        top[field] = minimal_object("refund_destination_details", card=obj)
    elif schema_name == "dispute_payment_method_details_card":
        # And so does dispute.payment_method_details.
        top[field] = minimal_object("dispute_payment_method_details", card=obj)
    else:
        top[field] = obj
    return validate_object(top, source="unit")


def test_the_fields_whose_prose_does_not_close_are_left_open() -> None:
    """The pass-2 exclusions, pinned so a later re-read of the prose is a
    deliberate change: `display_brand` is explicitly forward-compatible, and
    `charge_outcome.reason` names values per rule without claiming the set."""
    payment_method = minimal_object(
        "payment_method", card=minimal_object("payment_method_card", display_brand="weird_brand")
    )
    assert validate_object(payment_method, source="unit") == []
    charge = minimal_object(
        "charge", outcome=minimal_object("charge_outcome", reason="some_other_rule_value")
    )
    assert not [v for v in validate_object(charge, source="unit") if "reason" in v.path]


def test_a_union_failure_surfaces_the_closest_members_violation() -> None:
    """`charge.outcome` is a nullable union wrapping a ref: when the object
    under it fails, the report is the ref member's own violation — the enum
    list — not 'expected a charge_outcome object, got object'."""
    charge = minimal_object("charge", outcome=minimal_object("charge_outcome", type="nope"))
    (violation,) = [
        v for v in validate_object(charge, source="unit") if v.path.startswith("outcome")
    ]
    assert violation.path == "outcome.type"
    assert "expected one of authorized" in violation.problem


def test_a_value_whose_kind_matches_no_union_member_keeps_the_generic_line() -> None:
    invoice = minimal_object("invoice", customer=5)
    (violation,) = validate_object(invoice, source="unit")
    assert violation.path == "customer"
    assert "expected a string" in violation.problem and "got integer" in violation.problem


def test_a_bare_object_rule_with_no_props_or_map_is_open() -> None:
    """`{"type": "object"}` with neither a property set nor an open-map
    declaration constrains nothing under JSON Schema semantics."""
    from schema_conformance.validate import _check

    out: list[SchemaViolation] = []
    _check({"anything": "goes"}, {"t": "object"}, "root", None, out)
    assert out == []


def test_an_undeclared_field_is_a_violation() -> None:
    customer = minimal_object("customer", x_internal_column="leaked")
    assert any(v.path == "x_internal_column" for v in validate_object(customer, source="unit"))


def test_missing_required_wrong_type_and_bad_null_each_report_their_path() -> None:
    customer = minimal_object("customer")
    del customer["created"]
    customer["email"] = 5
    problems = {v.path: v.problem for v in validate_object(customer, source="unit")}
    assert problems["created"] == "required field missing"
    assert problems["email"] == "expected string, got integer"
    # `customer.name` is nullable in the spec, so the non-nullable null is
    # planted on a required string of an embedded schema instead — reached
    # through its parent, the way the wire reaches it.
    payment_method = minimal_object(
        "payment_method", card=minimal_object("payment_method_card", last4=None)
    )
    problems = {v.path: v.problem for v in validate_object(payment_method, source="unit")}
    assert problems["card.last4"] == "may not be null"


def test_nullable_null_and_absent_optional_both_pass() -> None:
    customer = minimal_object("customer", email=None)
    assert "phone" not in customer
    assert validate_object(customer, source="unit") == []


def test_a_nullable_union_accepts_both_an_id_and_an_expanded_object() -> None:
    invoice = minimal_object("invoice", customer="cus_1")
    assert "customer" not in [v.path for v in validate_object(invoice, source="unit")]
    expanded = minimal_object("invoice", customer=minimal_object("customer"))
    assert validate_object(expanded, source="unit") == []


def test_the_nullability_exception_table_covers_the_probed_gaps() -> None:
    customer = minimal_object("customer", default_source=None)
    invoice_settings = minimal_object("invoice_setting_customer_setting")
    invoice_settings["default_payment_method"] = None
    customer["invoice_settings"] = invoice_settings
    assert validate_object(customer, source="unit") == []


# --- the body walker --------------------------------------------------------------


def test_a_list_envelope_is_recursed_into_and_unknown_types_flagged() -> None:
    body = {
        "object": "list",
        "url": "/v1/customers",
        "has_more": False,
        "data": [minimal_object("customer"), {"id": "x", "object": "bogus_thing"}],
    }
    (violation,) = violations_in_body(body, source="unit")
    assert violation.path == "data[1].object"
    assert "bogus_thing" in violation.problem


def test_contents_of_an_open_map_are_still_validated() -> None:
    """`event.data.object` is an open map (`additionalProperties: true`), so
    the structural pass over an event cannot check what is inside it — the
    reason the walker never stops at an already-validated parent."""
    event = minimal_object("event")
    event["data"]["object"] = {"cus_1": minimal_object("customer", email=5)}
    (violation,) = violations_in_body(event, source="unit")
    assert violation.path == "data.object.cus_1.email"


def test_a_registered_scaffold_object_is_skipped_not_flagged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A resource this world has registered but the pinned spec has no schema
    for is test scaffolding (`probe_notes`), not a typo'd discriminator — read
    from the registry at walk time, so a registration made after this module
    imported is still seen."""
    from typing import cast

    from stripeapi.dispatch import resource

    body = {"id": "note_1", "object": "note", "body": "text"}
    assert violations_in_body(body, source="unit"), "an unregistered type is flagged"
    monkeypatch.setitem(resource.BY_OBJECT, "note", cast(Any, object()))
    assert violations_in_body(body, source="unit") == []


def test_an_error_envelope_has_nothing_to_flag() -> None:
    error_body = {
        "error": {"type": "invalid_request_error", "code": "resource_missing", "param": "customer"}
    }
    assert violations_in_body(error_body, source="unit") == []


def test_duplicate_coverage_dedupes_to_one_violation() -> None:
    """The structural pass over the parent and the standalone pass over the
    same nested object must not double-report."""
    body = {"object": "customer", "discount": {"object": "discount", "x": 1}}
    paths = [v.path for v in violations_in_body(body, source="unit")]
    assert paths == sorted(set(paths))


# --- the generated tables the validator consumes ----------------------------------


def test_enum_overrides_are_exactly_the_generated_set() -> None:
    """The committed table is what the pruner extracts, no more, no less —
    a spec update that moves one of these fields changes the generator, not a
    hand-edited constant."""
    from tools_dev.prune_spec import ENUM_OVERRIDES as generated

    assert generated == ENUM_OVERRIDES
    pairs = {(obj, field) for obj, fields in ENUM_OVERRIDES.items() for field in fields}
    assert len(pairs) == 36
    assert {("setup_intent", "usage")} == DECLARED_OVERRIDES
    assert ("setup_intent", "usage") in pairs
    # The same enum under each schema name it can appear as on the wire.
    for schema_name in (
        "payment_method_card",
        "payment_method_details_card",
        "dispute_payment_method_details_card",
    ):
        assert "brand" in ENUM_OVERRIDES[schema_name]


def test_http_tool_names_match_the_envelope_middleware() -> None:
    """The hook and the Stripe-envelope boundary must agree on which tools
    carry HTTP responses; a fourth tool on either side fails here first."""
    from stripeapi.middleware.stripe_envelope import _HTTP_TOOLS

    assert capture.HTTP_TOOL_NAMES == _HTTP_TOOLS


def test_the_nullability_exception_table_names_real_fields() -> None:
    for schema_name, field in NULLABLE_DESPITE_SPEC:
        assert field in RULES[schema_name]["props"], (schema_name, field)


def test_every_deleted_stub_has_rules() -> None:
    for schema_name in DELETED_BY_OBJECT.values():
        assert schema_name in RULES


# --- the hook ----------------------------------------------------------------------


def test_check_raises_with_the_whole_picture() -> None:
    planted = capture.CapturedCall(
        ordinal=1,
        tool="stripe_api_write",
        label="POST /v1/customers",
        body=minimal_object("customer", email=5, x_leak="oops"),
    )
    with pytest.raises(AssertionError) as raised:
        capture.check([planted])
    message = str(raised.value)
    assert "call #1 stripe_api_write POST /v1/customers" in message
    assert "email" in message and "x_leak" in message
    assert "2 violation(s)" in message


def test_check_passes_silently_on_clean_bodies() -> None:
    clean = capture.CapturedCall(
        ordinal=1,
        tool="stripe_api_read",
        label="GET /v1/customers",
        body={"object": "list", "data": [minimal_object("customer")]},
    )
    capture.check([clean])


def test_conformance_code_never_imports_stripe_python() -> None:
    """CI's replay-and-validate set never needs the SDK; `import stripe`
    belongs to the Phase 5 recorder under `tools_dev/` alone."""
    scanned = [
        path
        for path in (
            *Path(REPO, "src/stripeapi").rglob("*.py"),
            *Path(REPO, "tests/schema_conformance").rglob("*.py"),
            *Path(REPO, "tests/conformance").rglob("*.py"),
        )
        if path.is_file()
    ]
    assert scanned, "the scan found nothing to scan"
    for path in scanned:
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert not any(
                    alias.name == "stripe" or alias.name.startswith("stripe.")
                    for alias in node.names
                ), f"{path}: import stripe"
            if isinstance(node, ast.ImportFrom) and isinstance(node.module, str):
                assert not (node.module == "stripe" or node.module.startswith("stripe.")), (
                    f"{path}: from stripe import"
                )


def test_schema_violation_line_renders_source_path_problem() -> None:
    violation = SchemaViolation(source="call #1", path="data[0].email", problem="expected string")
    assert violation.line() == "call #1: data[0].email: expected string"


def test_a_captured_call_source_never_carries_a_trailing_space() -> None:
    assert capture.CapturedCall(1, "stripe_api_write", "POST /v1/customers", {}).source() == (
        "call #1 stripe_api_write POST /v1/customers"
    )
    # A call with neither method nor path renders bare, no trailing space.
    assert (
        capture.CapturedCall(2, "stripe_api_write", "", {}).source() == "call #2 stripe_api_write"
    )
