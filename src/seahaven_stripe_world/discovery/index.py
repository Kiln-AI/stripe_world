"""The discovery layer: search and details over the full MCP catalogue.

Loads ``discovery_index.json`` once at import — a precomputed index of all
catalogued operations (architecture §5.1).  The runtime answers two questions:

- **search**: ``intent`` + ``resource`` terms, scored and ranked.
- **details**: the twelve-key document for one operation by its operation ID.

The index covers every operation the real Stripe MCP catalogues (123 at last
enumeration), not just the routed subset.  This is what closes the "discovery
covers entire Stripe API" tell cluster (AR-05 / DT-06..DT-12 / DT-19 / DT-25).

Framework-agnostic on purpose — no ``seahaven`` import, no ``ctx`` — so it
is unit-testable as plain Python.  ``tools/api.py`` owns the translation of
a ``None`` into this world's Seahaven error shapes.
"""

import re
from dataclasses import dataclass
from typing import Any, Final

from seahaven_stripe_world.spec import discovery_index_document, pinned_version

__all__ = ["BY_OP_ID", "INDEX", "Operation", "details", "search"]

DEFAULT_LIMIT: Final = 5

# Scoring weights (architecture §5.2)
_RESOURCE_TAG_WEIGHT: Final = 3
_RESOURCE_KEYWORD_WEIGHT: Final = 3
_RESOURCE_PATH_WEIGHT: Final = 2
_INTENT_VERB_WEIGHT: Final = 2
_INTENT_SUMMARY_WEIGHT: Final = 1

_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")

# Intent-to-verb mapping (architecture §5.2)
_INTENT_VERB_MAP: Final[dict[str, set[str]]] = {
    "create": {"POST"},
    "add": {"POST"},
    "list": {"GET"},
    "retrieve": {"GET"},
    "get": {"GET"},
    "read": {"GET"},
    "update": {"POST"},
    "modify": {"POST"},
    "delete": {"DELETE"},
    "remove": {"DELETE"},
    "cancel": {"POST"},
    "void": {"POST"},
    "refund": {"POST"},
    "finalize": {"POST"},
    "capture": {"POST"},
    "confirm": {"POST"},
    "search": {"GET"},
}


@dataclass(frozen=True, slots=True)
class Operation:
    """One catalogued operation with precomputed search tokens."""

    op_id: str
    method: str
    path: str
    summary: str
    tags: tuple[str, ...]
    keywords: tuple[str, ...]
    has_path_param: bool
    # The full details document for direct return from details()
    document: dict[str, Any]
    # Precomputed lower-cased token sets for search
    tag_tokens: frozenset[str]
    keyword_tokens: frozenset[str]
    path_tokens: frozenset[str]
    summary_lower: str


def _build_operation(doc: dict[str, Any]) -> Operation:
    tags = tuple(doc.get("tags", []))
    keywords = tuple(doc.get("keywords", []))
    path = doc["path"]

    # Precompute search tokens
    tag_tokens: set[str] = set()
    for tag in tags:
        for part in _TOKEN_SPLIT.split(tag.lower()):
            if part:
                tag_tokens.add(part)

    keyword_tokens = frozenset(k.lower() for k in keywords if k)

    path_tokens: set[str] = set()
    for seg in path.split("/"):
        if seg and not seg.startswith("{"):
            for part in _TOKEN_SPLIT.split(seg.lower()):
                if part:
                    path_tokens.add(part)

    return Operation(
        op_id=doc["id"],
        method=doc["method"],
        path=path,
        summary=doc.get("summary", ""),
        tags=tags,
        keywords=keywords,
        has_path_param="{" in path,
        document=doc,
        tag_tokens=frozenset(tag_tokens),
        keyword_tokens=keyword_tokens,
        path_tokens=frozenset(path_tokens),
        summary_lower=doc.get("summary", "").lower(),
    )


def _load_index() -> tuple[tuple[Operation, ...], dict[str, Operation]]:
    raw = discovery_index_document()
    operations = tuple(_build_operation(doc) for doc in sorted(raw.values(), key=lambda d: d["id"]))
    by_op_id = {op.op_id: op for op in operations}
    return operations, by_op_id


INDEX, BY_OP_ID = _load_index()


def search(intent: str, resource: str, *, limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
    """Ranked operations for an intent+resource query.

    Returns the wrapped envelope matching the real Stripe MCP:
    ``{"openapi_spec_version": ..., "data": [...]}``.
    """
    intent_terms = [t for t in _TOKEN_SPLIT.split(intent.lower()) if t]
    resource_terms = [t for t in _TOKEN_SPLIT.split(resource.lower()) if t]

    if not intent_terms and not resource_terms:
        return {"openapi_spec_version": pinned_version(), "data": []}

    scored: list[tuple[int, Operation]] = []
    for op in INDEX:
        score = 0

        # Resource scoring (architecture §5.2):
        # tags and keywords (weight 3), path segments (weight 2)
        for term in resource_terms:
            if term in op.tag_tokens:
                score += _RESOURCE_TAG_WEIGHT
            if term in op.keyword_tokens:
                score += _RESOURCE_KEYWORD_WEIGHT
            if term in op.path_tokens:
                score += _RESOURCE_PATH_WEIGHT

        # Intent scoring:
        # verb class match (weight 2), summary match (weight 1)
        for term in intent_terms:
            mapped_verbs = _INTENT_VERB_MAP.get(term)
            if mapped_verbs and op.method in mapped_verbs:
                score += _INTENT_VERB_WEIGHT
            if term in op.summary_lower:
                score += _INTENT_SUMMARY_WEIGHT

        if score > 0:
            scored.append((score, op))

    # Sort by score descending, then by operation id for stability
    scored.sort(key=lambda pair: (-pair[0], pair[1].op_id))

    data = []
    for _, op in scored[:limit]:
        result: dict[str, Any] = {
            "id": op.op_id,
            "method": op.method,
            "path": op.path,
            "summary": op.summary,
        }
        # llm_context is Stripe-authored prose and is only present where
        # the probe captured it. Since we have no source for it, it is
        # omitted (declared residue, functional spec §13).
        data.append(result)

    return {"openapi_spec_version": pinned_version(), "data": data}


def details(operation_id: str) -> dict[str, Any] | None:
    """The twelve-key details document for one operation, or ``None``.

    Returns the precomputed document directly from the index. Every
    catalogued operation has a document; absent operations return None.
    """
    op = BY_OP_ID.get(operation_id)
    if op is None:
        return None
    return dict(op.document)
