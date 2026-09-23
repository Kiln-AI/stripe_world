"""The discovery layer's runtime module: the routed-operation catalogue.

Loads `spec3.min.json` once, at import, and answers the two discovery tools:
search over `intent` + `resource` terms, and parameter documentation for
one operation by its operation ID.  Framework-agnostic on purpose — no
`seahaven` import, no `ctx` — so it is unit-testable as plain Python, with
`tools/api.py` owning the translation of its `None` into this world's
Seahaven error shapes.

The index is the route table's shadow, not a second list: its key set is
`routes.ALL`'s, and the drift test asserts both directions. An operation that
is not routed is not searchable and has no details — including real Stripe
paths this world deliberately cut.
"""

import re
from dataclasses import dataclass
from typing import Any, Final

from seahaven_stripe_world.spec import spec_document

__all__ = ["BY_KEY", "BY_OP_ID", "INDEX", "Operation", "details", "search"]

#: Fixed: ten results keep a worst-case response under a kilobyte.
DEFAULT_LIMIT: Final = 5

EXACT_WEIGHT: Final[dict[str, int]] = {"path": 6, "operation_id": 5, "summary": 4, "description": 2}
SUBSTR_WEIGHT: Final[dict[str, int]] = {
    "path": 3,
    "operation_id": 2,
    "summary": 2,
    "description": 1,
}

_VERBS: Final = ("GET", "POST", "DELETE")
_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")
_SENTENCE_END = re.compile(r"(?<=[.!?]) (?=[A-Z0-9])")
_REF = re.compile(r"#/components/schemas/([A-Za-z0-9_.\-]+)")


@dataclass(frozen=True, slots=True)
class Operation:
    """One routed operation with its search text precomputed at load."""

    method: str
    path: str
    operation_id: str
    summary: str
    description: str
    parameters: tuple[dict[str, Any], ...]
    tokens: dict[str, frozenset[str]]
    lower: dict[str, str]

    def result(self) -> dict[str, str]:
        return {
            "id": self.operation_id,
            "method": self.method,
            "path": self.path,
            "summary": self.summary,
        }


def _first_sentence(text: str) -> str:
    """Stripe's prose, cut to its first sentence. Loose by design: the drift
    test pins "no more than one `.`-terminated sentence", not exact spans."""
    if not text:
        return ""
    split = _SENTENCE_END.split(text, maxsplit=1)
    return split[0]


def _type_of(schema: dict[str, Any]) -> str:
    """A compact type string: `array<object>`, `string | integer` — `anyOf`
    collapses into a `|`-joined list rather than nesting."""
    if "$ref" in schema:
        match = _REF.fullmatch(schema["$ref"])
        return match.group(1) if match else "object"
    for union_key in ("anyOf", "oneOf"):
        members = schema.get(union_key)
        if isinstance(members, list) and members:
            return " | ".join(_type_of(member) for member in members)
    kind = schema.get("type", "object")
    if kind == "array":
        items = schema.get("items", {})
        return f"array<{_type_of(items)}>"
    return str(kind)


def _param_doc(
    name: str, schema: dict[str, Any], required: bool, description: str
) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "name": name,
        "type": _type_of(schema),
        "required": required,
        "description": _first_sentence(description),
    }
    enum = schema.get("enum")
    if enum is None:
        for union_key in ("anyOf", "oneOf"):
            for member in schema.get(union_key, []):
                if isinstance(member, dict) and "enum" in member:
                    enum = member["enum"]
                    break
            if enum is not None:
                break
    if enum is not None:
        doc["enum"] = list(enum)
    return doc


def _props_of(schema: dict[str, Any]) -> dict[str, Any]:
    """The flattened property table a schema addresses, through one-member
    wrappers: a `{"type": "object", "properties": …}` member of a union."""
    if "properties" in schema:
        return schema["properties"]
    for union_key in ("anyOf", "oneOf"):
        for member in schema.get(union_key, []):
            if isinstance(member, dict) and "properties" in member:
                return member["properties"]
    return {}


def _render_object_fields(schema: dict[str, Any]) -> list[dict[str, Any]]:
    """Exactly one level of an object parameter's own fields, then stop —
    `payment_settings` shows its keys; three levels down collapses to
    `"type": "object"`."""
    return [
        _param_doc(name, prop, False, prop.get("description", ""))
        for name, prop in _props_of(schema).items()
    ]


def _build_parameters(operation: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    """The rendered parameter list: the spec's own path/query parameters plus
    the request body's (already allowlist-filtered, for a wired route)
    properties, flattened to depth one."""
    docs: list[dict[str, Any]] = []
    for parameter in operation.get("parameters", []):
        schema = parameter.get("schema", {})
        docs.append(
            _param_doc(
                parameter["name"],
                schema,
                bool(parameter.get("required")),
                parameter.get("description", ""),
            )
        )
    content = (
        operation.get("requestBody", {}).get("content", {}).get("application/x-www-form-urlencoded")
    )
    if content:
        schema = content.get("schema", {})
        properties = _props_of(schema)
        required = set(schema.get("required", []))
        for name, prop in properties.items():
            doc = _param_doc(name, prop, name in required, prop.get("description", ""))
            kind = prop.get("type")
            if (
                kind == "object"
                or "properties" in prop
                or any(
                    "properties" in member for member in prop.get("anyOf", prop.get("oneOf", []))
                )
            ):
                doc["fields"] = _render_object_fields(prop)
            elif kind == "array":
                items = prop.get("items", {})
                if items.get("type") == "object" or "properties" in items:
                    doc["item_fields"] = _render_object_fields(items)
            docs.append(doc)
    return tuple(docs)


def _build_operation(method: str, path: str, operation: dict[str, Any]) -> Operation:
    summary = operation.get("summary", "")
    description = operation.get("description", "")
    operation_id = operation.get("operationId", "")
    texts = {
        "path": path,
        "operation_id": operation_id,
        "summary": summary,
        "description": description,
    }
    return Operation(
        method=method,
        path=path,
        operation_id=operation_id,
        summary=summary,
        description=description,
        parameters=_build_parameters(operation),
        tokens={
            field: frozenset(part for part in _TOKEN_SPLIT.split(text.lower()) if part)
            for field, text in texts.items()
        },
        lower={field: text.lower() for field, text in texts.items()},
    )


def _load_index() -> tuple[
    tuple[Operation, ...],
    dict[tuple[str, str], Operation],
    dict[str, Operation],
]:
    raw = spec_document()
    operations = tuple(
        _build_operation(method.upper(), path, item)
        for path, path_item in raw["paths"].items()
        for method, item in path_item.items()
        if method in ("get", "post", "delete")
    )
    by_key = {(op.method, op.path): op for op in operations}
    by_op_id = {op.operation_id: op for op in operations}
    return operations, by_key, by_op_id


INDEX, BY_KEY, BY_OP_ID = _load_index()


def search(intent: str, resource: str, *, limit: int = DEFAULT_LIMIT) -> list[dict[str, str]]:
    """Ranked operations for an intent+resource query.

    Combines ``intent`` and ``resource`` terms for scoring.  Exact-token
    matches outrank substring matches.  The tie-break
    ``(-score, method, path)`` is a total order so results never depend on
    iteration order.
    """
    terms = [
        term for word in (intent, resource) for term in _TOKEN_SPLIT.split(word.lower()) if term
    ]
    if not terms:
        return []
    scored: list[tuple[int, Operation]] = []
    for operation in INDEX:
        score = 0
        for field in ("path", "operation_id", "summary", "description"):
            tokens = operation.tokens[field]
            text = operation.lower[field]
            for term in terms:
                if term in tokens:
                    score += EXACT_WEIGHT[field]
                elif term in text:
                    score += SUBSTR_WEIGHT[field]
        if score > 0:
            scored.append((score, operation))
    scored.sort(key=lambda pair: (-pair[0], pair[1].method, pair[1].path))
    return [operation.result() for _, operation in scored[:limit]]


def details(operation_id: str) -> dict[str, Any] | None:
    """The parameter documentation for one operation by its operation ID,
    or ``None`` for an operation not in the routed set."""
    operation = BY_OP_ID.get(operation_id)
    if operation is None:
        return None
    return {
        "method": operation.method,
        "path": operation.path,
        "operation_id": operation.operation_id,
        "summary": operation.summary,
        "description": _first_sentence(operation.description),
        "parameters": [dict(parameter) for parameter in operation.parameters],
    }
