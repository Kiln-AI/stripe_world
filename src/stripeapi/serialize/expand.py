"""The expansion resolver: `expand[]` validation and inflation.

Validation is the `EXPANDABLE_FIELDS` walk of `components/cross_cutting.md`
§3.3.2 with this phase's probe corrections applied to §3.3.5's error table —
at `2026-08-26.dahlia`:

- the `because it doesn't exist` variant never fires: a nonexistent field
  (`bogus_field_probe`) and an exists-but-unexpandable one (`description_probe`)
  both answer the plain form, `This property cannot be expanded (<field>).`;
- a bad *nested* segment carries the whole dotted path as the token —
  `(invoice_settings.bogus)` — not the lone segment;
- on a list endpoint, any bare first segment — even a bogus one — gets the
  redirecting hint, `… You may want to try expanding 'data.<seg>' instead.`;
- `expand[]=data` alone is accepted, not an error;
- an embedded object listed in `x-expandableFields` (`address`,
  `invoice_settings`) is accepted, traversed for deeper hops, and inflated
  never — it is already the object.

Path *validation* runs at bind, in the parameter layer, against the route's
declared `response_object`/`envelope` — statically, with no database access
and before any row is written (`components/cross_cutting.md` §3.3.1): a bad
path outranks both a missing resource and an empty filtered page (both probed
live this round), and a `POST … expand[]=nonsense` creates nothing. Inflation
here is breadth-first over the merged path trie, one batched
`SELECT … WHERE id IN (…)` per level per target table, never per row; a field
whose expansion target was pruned at the scope boundary is a terminal no-op,
because its union has no `$ref` left to follow.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from functools import cache
from typing import Any, Final, Literal

import seahaven

from stripeapi.spec import EXPANDABLE_FIELDS, spec_document
from stripeapi.stripe_errors import cannot_expand

__all__ = ["PathTrie", "apply", "validate_paths"]

#: At most four dot-separated segments, `data.` included
#: (`components/cross_cutting.md` §3.3.3).
MAX_SEGMENTS: Final = 4

#: The `data.` prefix a nested list envelope requires (`invoice.lines` is
#: addressed as `lines.data.price`), and the bare `data` of a top-level list.
DATA: Final = "data"

_CHUNK: Final = 500
_REF = re.compile(r"#/components/schemas/([A-Za-z0-9_.\-]+)")

type PathTrie = dict[str, PathTrie]

EdgeKind = Literal["reference", "embedded", "array"]


@dataclass(frozen=True, slots=True)
class _Edge:
    """One hop: the field, what it holds, and where the walk continues."""

    kind: EdgeKind
    target: str  # schema name; "" for a pruned terminal
    children: dict[str, _Edge] = field(default_factory=dict)


_target_cache: dict[tuple[str, str], tuple[str, ...]] = {}


def validate_paths(
    paths: tuple[str, ...] | list[str],
    *,
    object_name: str,
    is_list: bool,
) -> dict[str, _Edge]:
    """Check every requested path against the expandable map, with no database
    access at all.

    `object_name` is the schema name of the object the response carries — the
    item's, for a list. Returns the paths merged into one trie of `_Edge`s:
    `["customer", "customer.default_source"]` fetches the customer once.
    """
    root: dict[str, _Edge] = {}
    for path in paths:
        segments = path.split(".")
        if not path or any(not segment for segment in segments):
            raise seahaven.WorldBug(f"empty segment in expand path {path!r}")
        if len(segments) > MAX_SEGMENTS:
            raise cannot_expand(path, exists=True)
        if is_list:
            first, *rest = segments
            if first != DATA:
                # Probed: the hint fires for any bare first segment on a list,
                # whether or not the item has such a field.
                raise cannot_expand(first, exists=True, hint=DATA)
            segments = rest
            if not segments:
                continue  # `expand[]=data` alone: accepted, a no-op
        edges = root
        schema = object_name
        index = 0
        while index < len(segments):
            segment = segments[index]
            token = ".".join(segments[: index + 1])
            edge = edges.get(segment)
            if edge is None:
                if segment not in EXPANDABLE_FIELDS.get(schema, ()):
                    # The probe-corrected plain form; the token is the path so
                    # far, which is the bare field for a first segment.
                    raise cannot_expand(token, exists=True)
                edge = _make_edge(schema, segment)
                edges[segment] = edge
            if edge.kind == "array":
                # A nested list envelope is addressed through its `data`, the
                # same prefix rule as a top-level list (`lines.data.price`)
                # — `components/cross_cutting.md` §3.3.2 step 4. `data` is a
                # prefix marker, not an edge: the walk continues on the item.
                # A bare array field is a harmless no-op — its values are
                # always full objects already.
                index += 1
                if index >= len(segments):
                    break
                if segments[index] != DATA:
                    raise cannot_expand(token, exists=True)
                schema = edge.target
                edges = edge.children
                index += 1
                continue
            schema = edge.target
            edges = edge.children
            index += 1
    return root


def _make_edge(schema_name: str, field_name: str) -> _Edge:
    kind: EdgeKind
    prop = spec_document()["components"]["schemas"][schema_name]["properties"][field_name]
    if prop.get("type") == "array" or "items" in prop:
        kind = "array"
    elif _has_string_member(prop):
        kind = "reference"
    else:
        kind = "embedded"
    return _Edge(kind=kind, target=_first_ref(prop) or "", children={})


def _has_string_member(prop: dict) -> bool:
    members = prop.get("anyOf", prop.get("oneOf", []))
    if not members:
        return prop.get("type") == "string"
    return any(member.get("type") == "string" for member in members)


def _first_ref(prop: dict) -> str | None:
    """The first non-deleted `$ref` target of a property, through one-member
    wrappers, the union forms, and an array's `items`."""
    if "$ref" in prop:
        return _ref_name(prop["$ref"])
    items = prop.get("items")
    if isinstance(items, dict) and "$ref" in items:
        return _ref_name(items["$ref"])
    for member in prop.get("anyOf", prop.get("oneOf", [])):
        if "$ref" in member:
            name = _ref_name(member["$ref"])
            if name is not None and not name.startswith("deleted_"):
                return name
    return None


def _ref_name(ref: str) -> str | None:
    match = _REF.fullmatch(ref)
    return match.group(1) if match else None


def apply(
    ctx: seahaven.Ctx,
    payload: dict[str, object],
    paths: tuple[str, ...],
    *,
    object_name: str,
    is_list: bool,
) -> dict[str, object]:
    """Inflate every reference the validated `paths` name, into `payload`.

    The route's declarations — the response object and envelope validated at
    bind — drive this, never the payload's own shape: an empty page and a
    populated one expand identically, because there is nothing left to sniff.
    Read-only. `validate_paths` runs again here so a future direct caller
    cannot bypass validation; against an already-bound request it is
    idempotent and cheap.
    """
    trie = validate_paths(paths, object_name=object_name, is_list=is_list)
    if not trie:
        return payload
    if is_list:
        data = payload.get("data")
        if not isinstance(data, list) or not data:
            return payload  # nothing to inflate; validation already happened at bind
        members: Sequence[object] = data
    else:
        members = (payload,)
    frontier = [obj for obj in members if isinstance(obj, dict)]
    if not frontier:
        return payload
    _inflate_level(ctx, frontier, trie)
    return payload


def _inflate_level(
    ctx: seahaven.Ctx, frontier: list[dict[str, object]], edges: dict[str, _Edge]
) -> None:
    """One trie level over the whole frontier: substitute references, walk
    into embedded objects and list envelopes, recurse."""
    from stripeapi.dispatch.resource import BY_OBJECT

    for field_name, edge in edges.items():
        values = [(obj, obj.get(field_name)) for obj in frontier]
        if edge.kind == "reference" and edge.target:
            ids = [value for _, value in values if isinstance(value, str)]
            cache = _fetch(ctx, BY_OBJECT, edge, ids)
            for obj, value in values:
                if isinstance(value, str):
                    obj[field_name] = cache[value]
        if not edge.children:
            continue
        deeper: list[dict[str, object]] = []
        for obj, value in values:
            if isinstance(value, str) and edge.kind == "reference":
                substituted = obj[field_name]
                if isinstance(substituted, dict):
                    deeper.append(substituted)
            elif isinstance(value, dict):
                items = value[DATA] if value.get("object") == "list" else [value]
                deeper.extend(item for item in items if isinstance(item, dict))
        if deeper:
            _inflate_level(ctx, deeper, edge.children)


def _fetch(
    ctx: seahaven.Ctx,
    by_object: dict[str, Any],
    edge: _Edge,
    ids: Sequence[str],
) -> dict[str, dict[str, object]]:
    """The serialized objects for `ids`, one batched statement per call.

    The target is the resource registered under the referenced schema's
    `object` discriminator. Until a resource phase registers it, the reference
    cannot be inflated — and cannot be non-null either, because nothing
    writes rows for an unbuilt resource — so an unbuilt target here is this
    world's bug, loudly.
    """
    if not ids:
        return {}
    discriminator = _discriminator(edge.target)
    spec = by_object.get(discriminator)
    if spec is None:
        raise seahaven.WorldBug(
            f"cannot expand into {edge.target!r}: no resource is registered for it yet"
        )
    resolved: dict[str, dict[str, object]] = {}
    wanted = sorted(set(ids))
    for start in range(0, len(wanted), _CHUNK):
        chunk = wanted[start : start + _CHUNK]
        placeholders = ", ".join("?" for _ in chunk)
        rows = ctx.db.rows(f"SELECT * FROM {spec.table} WHERE id IN ({placeholders})", *chunk)
        for row in rows:
            resolved[row["id"]] = spec.serializer(ctx, row)
    missing = [value for value in wanted if value not in resolved]
    if missing:
        # The engine emitted this id from a row it just read; a missing target
        # row is a schema or write bug, not something an agent did
        # (`components/cross_cutting.md` §3.3.4).
        raise seahaven.WorldBug(f"dangling reference into {edge.target!r}: {missing}")
    return resolved


@cache
def _discriminator(schema_name: str) -> str:
    properties = spec_document()["components"]["schemas"][schema_name]["properties"]
    return properties["object"]["enum"][0]
