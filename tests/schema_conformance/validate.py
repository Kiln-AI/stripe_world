"""``validate_object``: does one object this world returned match the pinned
spec's own shape?

The rules are generated, not hand-written — ``tools_dev/prune_spec.py``
normalizes ``spec3.min.json`` into ``src/stripeapi/spec/schema_rules.json`` —
so the validator and the discovery layer cannot drift from each other
(functional spec §2.7). What is checked, per object:

- the ``object`` discriminator selects the schema (``deleted: true`` selects
  the ``deleted_*`` stub), and an unknown type is itself a violation — the
  real API never returns one;
- required fields, JSON types (a bool is not an integer), nullability, and
  machine-readable ``enum``s, compared type-strictly;
- the property set is *closed*: an undeclared field is a violation, which is
  what catches a serializer leaking an internal column name;
- unions, so an unexpanded id string and an expanded object both pass
  (``expand[]`` legality is elsewhere's problem — this layer only checks that
  whatever shape came back is a shape the spec allows);
- ``ENUM_OVERRIDES`` — the bare-``string`` fields whose closed value set
  lives only in description prose (data_model.md §12's pass-2 enrollment,
  including one enum's copies under each schema name it takes on the wire),
  consulted at ``(owning schema, field)`` wherever the walk sits inside a
  named schema. ``setup_intent.usage`` among them is this project's declared
  reading, not Stripe's documentation (functional spec §4).

``violations_in_body`` walks any response body: every dict carrying a known
discriminator is validated with full path context, ``"list"`` envelopes are
recursed into rather than validated, and — because open maps such as
``event.data.object`` are *not* covered by the structural pass — every dict's
children are walked too. A nested object the structural pass already covered
is validated again standalone; the two produce identical ``(path, problem)``
pairs, deduped here, so each violation reports exactly once.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from stripeapi.dispatch import resource as _resource
from stripeapi.spec import ENUM_OVERRIDES, schema_rules

__all__ = ["SchemaViolation", "validate_object", "violations_in_body"]

_DOC = schema_rules()
RULES: dict[str, Any] = _DOC["rules"]
BY_OBJECT: dict[str, str] = _DOC["by_object"]
DELETED_BY_OBJECT: dict[str, str] = _DOC["deleted_by_object"]

#: Fields the pinned spec types as non-nullable that the live API emits `null`
#: for anyway — confirmed by direct probe at the pinned version (Phase 4,
#: 2026-09-19, `POST /v1/customers` on the sandbox account: a fresh customer
#: carries `default_source: null` and `invoice_settings.default_payment_method:
#: null`, while `spec3.json` gives both an expandable union with no `nullable`
#: key). Stripe's own `nullable` annotations are incomplete here; the recorded
#: behavior wins (implementation plan, recipe step 3). Each entry is added only
#: with probe evidence, and this set is reviewed with Phase 5's
#: `allowed_differences.py`, where the general declaration belongs.
NULLABLE_DESPITE_SPEC: frozenset[tuple[str, str]] = frozenset(
    {
        ("customer", "default_source"),
        ("invoice_setting_customer_setting", "default_payment_method"),
    }
)


@dataclass(frozen=True)
class SchemaViolation:
    """One way an object failed its schema, at a path from the body root.

    ``path`` is dotted relative to the response body (``data[2].card.brand``);
    empty means the object itself. ``source`` names the call that produced the
    body, and is folded into the failure message.
    """

    source: str
    path: str
    problem: str

    def line(self) -> str:
        where = self.path or "<the object>"
        return f"{self.source}: {where}: {self.problem}"


def _join(path: str, key: str) -> str:
    return f"{path}.{key}" if path else key


def _kind(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):  # before int: bool is an int in Python
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _type_ok(value: Any, expected: str) -> bool:
    match expected:
        case "string":
            return isinstance(value, str)
        case "integer":
            return isinstance(value, int) and not isinstance(value, bool)
        case "number":
            return isinstance(value, (int, float)) and not isinstance(value, bool)
        case "boolean":
            return isinstance(value, bool)
        case "array":
            return isinstance(value, list)
        case "object":
            return isinstance(value, dict)
        case "null":
            return value is None
        case _:
            return True  # an unknown type keyword is not this layer's verdict


def _in_enum(value: Any, allowed: list[Any]) -> bool:
    # Type-strict: True == 1 in Python, and `deleted`'s enum is [true].
    return any(type(value) is type(candidate) and value == candidate for candidate in allowed)


def _member_label(member: dict[str, Any]) -> str:
    if "ref" in member:
        return f"a {member['ref']} object"
    if "any" in member:
        return " or ".join(_member_label(inner) for inner in member["any"])
    return {
        "string": "a string",
        "integer": "an integer",
        "number": "a number",
        "boolean": "a boolean",
        "array": "an array",
        "object": "an object",
        "null": "null",
    }.get(member.get("t", ""), "anything")


def _allows_null(rule: dict[str, Any]) -> bool:
    if rule.get("nul"):
        return True
    return any(member.get("t") == "null" or member.get("nul") for member in rule.get("any", ()))


def _accepts_kind(value: Any, rule: Any) -> bool:
    """Whether a value's JSON kind could satisfy this rule at all — the
    shallow type check, with no structure looked at."""
    if not isinstance(rule, dict):
        return True
    if "ref" in rule:
        target = RULES.get(rule["ref"])
        return True if target is None else _accepts_kind(value, target)
    if "any" in rule:
        return any(_accepts_kind(value, member) for member in rule["any"])
    expected = rule.get("t")
    if expected is not None:
        return _type_ok(value, expected)
    if "props" in rule or "map" in rule:
        return isinstance(value, dict)
    return True


def _check(
    value: Any,
    rule: Any,
    path: str,
    owner: str | None,
    out: list[SchemaViolation],
    field: str | None = None,
) -> None:
    """One value against one rule node.

    ``owner`` is the schema whose fields are being walked — the key the
    ``ENUM_OVERRIDES`` table is indexed by — and becomes the ``ref`` target
    when the walk enters one. ``field`` is the property name the value sits
    under, carried for the nullability exception table.
    """
    if rule is None:
        return
    if not isinstance(rule, dict):
        return
    if value is None:
        if not _allows_null(rule) and (owner, field) not in NULLABLE_DESPITE_SPEC:
            out.append(SchemaViolation("", path, "may not be null"))
        return
    if "ref" in rule:
        target = RULES.get(rule["ref"])
        if target is not None:
            _check(value, target, path, rule["ref"], out)
        return
    if "any" in rule:
        trials: list[tuple[dict[str, Any], list[SchemaViolation]]] = []
        for member in rule["any"]:
            trial: list[SchemaViolation] = []
            _check(value, member, path, owner, trial)
            if not trial:
                return
            trials.append((member, trial))
        # Failure output is the primary interface (components/conformance.md):
        # when every member failed, surface the closest member's violations —
        # the enum list, the missing field — rather than discarding them for a
        # generic line. Closest is: a member whose type the value even matches
        # (an object under a string|ref union belongs to the ref member), then
        # the fewest violations, then a ref member, then spec order. A value
        # whose kind matches no member keeps the generic line, which is the
        # actionable fact in that case.
        typed = [(member, trial) for member, trial in trials if _accepts_kind(value, member)]
        if typed:
            _, closest = min(typed, key=lambda mt: (len(mt[1]), 0 if "ref" in mt[0] else 1))
            out.extend(closest)
        else:
            labels = " or ".join(_member_label(member) for member in rule["any"])
            out.append(SchemaViolation("", path, f"expected {labels}, got {_kind(value)}"))
        return
    expected = rule.get("t")
    if expected is not None and not _type_ok(value, expected):
        out.append(SchemaViolation("", path, f"expected {expected}, got {_kind(value)}"))
        return
    if "enum" in rule and not _in_enum(value, rule["enum"]):
        allowed = ", ".join(repr(candidate) for candidate in rule["enum"])
        out.append(SchemaViolation("", path, f"expected one of {allowed}, got {value!r}"))
        return
    if isinstance(value, dict) and ("props" in rule or "map" in rule):
        # A bare `{"type": "object"}` rule — no props, no map — is open under
        # JSON Schema semantics, so it is not walked: only a declared property
        # set or an explicit open map constrains the keys.
        _check_fields(value, rule, path, owner, out)
    if isinstance(value, list) and "items" in rule:
        for index, item in enumerate(value):
            _check(item, rule["items"], f"{path}[{index}]" if path else f"[{index}]", owner, out)


def _check_fields(
    obj: dict[str, Any],
    rule: dict[str, Any],
    path: str,
    owner: str | None,
    out: list[SchemaViolation],
) -> None:
    props: dict[str, Any] = rule.get("props") or {}
    for name in rule.get("req", ()):
        if name not in obj:
            out.append(SchemaViolation("", _join(path, name), "required field missing"))
    open_map = "map" in rule
    for key, value in obj.items():
        where = _join(path, key)
        if key in props:
            _check(value, props[key], where, owner, out, field=key)
            allowed = ENUM_OVERRIDES.get(owner or "", {}).get(key)
            if allowed is not None and isinstance(value, str) and value not in allowed:
                listed = ", ".join(allowed)
                out.append(SchemaViolation("", where, f"expected one of {listed}, got {value!r}"))
        elif open_map:
            if rule["map"] is not None:
                _check(value, rule["map"], where, owner, out)
        else:
            out.append(
                SchemaViolation(
                    "", where, f"undeclared field {key!r}: the spec's property set is closed"
                )
            )


def validate_object(obj: dict[str, Any], *, source: str, path: str = "") -> list[SchemaViolation]:
    """Validate one API object against its schema; every violation, never the
    first only. ``obj["object"]`` selects the schema."""
    discriminator = obj.get("object")
    if not isinstance(discriminator, str):
        return [
            SchemaViolation(
                source, _join(path, "object"), "no `object` discriminator to select a schema with"
            )
        ]
    table = DELETED_BY_OBJECT if obj.get("deleted") is True else BY_OBJECT
    schema_name = table.get(discriminator)
    if schema_name is None:
        return [
            SchemaViolation(
                source,
                _join(path, "object"),
                f"unknown object type {discriminator!r}: not a schema in the pinned spec",
            )
        ]
    out: list[SchemaViolation] = []
    _check(obj, RULES[schema_name], path, schema_name, out)
    return [SchemaViolation(source, v.path, v.problem) for v in out]


def violations_in_body(body: Any, *, source: str) -> list[SchemaViolation]:
    """Every schema violation in a response body, sorted by path.

    The body root is usually a resource object or a ``{"object": "list"}``
    envelope, but any shape is walked: the harness never trusts a handler to
    have returned the envelope its route promised. An ``object`` value the
    pinned spec has no schema for is a violation *unless* it names a
    resource this world has registered — a test-local probe resource is
    scaffolding with no pinned schema, not a typo'd discriminator; read from
    the registry at walk time so late registrations are seen.
    """
    found: dict[tuple[str, str], None] = {}

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            discriminator = node.get("object")
            if isinstance(discriminator, str) and discriminator != "list":
                if discriminator in BY_OBJECT or discriminator in DELETED_BY_OBJECT:
                    for violation in validate_object(node, source=source, path=path):
                        found[(violation.path, violation.problem)] = None
                elif discriminator not in _resource.BY_OBJECT:
                    found[
                        (
                            _join(path, "object"),
                            f"unknown object type {discriminator!r}: not a schema in the "
                            "pinned spec nor a registered resource",
                        )
                    ] = None
            for key, value in node.items():
                walk(value, _join(path, key))
        elif isinstance(node, list):
            for index, item in enumerate(node):
                walk(item, f"{path}[{index}]" if path else f"[{index}]")

    walk(body, "")
    return [SchemaViolation(source, path, problem) for path, problem in sorted(found)]
