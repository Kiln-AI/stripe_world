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

__all__ = [
    "INLINE_LISTS",
    "POLYMORPHIC_REFS",
    "InlineList",
    "PathTrie",
    "apply",
    "register_inline_list",
    "validate_paths",
]

#: At most four dot-separated segments, `data.` included
#: (`components/cross_cutting.md` §3.3.3).
MAX_SEGMENTS: Final = 4

#: The `data.` prefix a nested list envelope requires (`invoice.lines` is
#: addressed as `lines.data.price`), and the bare `data` of a top-level list.
DATA: Final = "data"

#: How many items an inline list envelope carries before `has_more` flips —
#: the platform's default page size, the same clamp every list applies.
INLINE_PAGE: Final = 10

_CHUNK: Final = 500
_REF = re.compile(r"#/components/schemas/([A-Za-z0-9_.\-]+)")

type PathTrie = dict[str, PathTrie]

EdgeKind = Literal["reference", "embedded", "array", "inline_list"]


@dataclass(frozen=True, slots=True)
class _Edge:
    """One hop: the field, what it holds, and where the walk continues."""

    kind: EdgeKind
    target: str  # schema name; "" for a pruned terminal
    children: dict[str, _Edge] = field(default_factory=dict)
    # Set when the owner field is a polymorphic reference (see
    # POLYMORPHIC_REFS): prefix -> API object name, consulted at inflation.
    polymorphic: dict[str, str] | None = None


@dataclass(frozen=True, slots=True)
class InlineList:
    """One expand-only inline list: the child rows live in their own table,
    scoped to the parent, and the field is absent unless `expand[]` asks.

    Recorded at the pinned version (Phase 9 probe on `charge.refunds`): the
    live body omits the field unexpanded and answers
    `{object: "list", data, has_more, url}` under `expand[]=refunds` (plus a
    `total_count` the pinned spec's inline schema does not declare, omitted
    here per the spec-is-authority ruling). The parent-scoped collection URL
    is per field, not derivable, so it rides on the registration.
    """

    child_object: str  # the child's API object name, e.g. "refund"
    scope_column: str  # the child column holding the parent id, e.g. "charge"
    url_template: str  # "{id}" stands for the parent's id


#: `(object, field) -> InlineList`, filled by the child resource's module at
#: import. An inline-list field with no entry is a scope cut, not a bug: the
#: field is never emitted unexpanded and expansion is a no-op (customer's
#: `sources`/`subscriptions`/`tax_ids` are exactly that until their phases).
INLINE_LISTS: dict[tuple[str, str], InlineList] = {}

#: `(schema, field) -> {id prefix: API object name}` for the polymorphic
#: references whose union names several tables and the wire resolves by
#: prefix. One exists in scope: `balance_transaction.source`, `anyOf[string |
#: charge | dispute | payout | refund]` (probed, Phase 11: `expand[]=source`
#: inflates the right object per row — a charge row and a dispute row in one
#: list). Without this map the resolver would follow the union's first `$ref`
#: alone and read every id from the charges table. Read onto the `_Edge` at
#: construction, where the owner schema is still known.
POLYMORPHIC_REFS: dict[tuple[str, str], dict[str, str]] = {
    ("balance_transaction", "source"): {
        "ch_": "charge",
        "re_": "refund",
        "du_": "dispute",
        "po_": "payout",
    },
}


def register_inline_list(object_name: str, field_name: str, spec: InlineList) -> None:
    key = (object_name, field_name)
    if key in INLINE_LISTS:
        raise seahaven.WorldBug(
            f"inline list {field_name!r} on {object_name!r} is registered twice"
        )
    INLINE_LISTS[key] = spec


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
            if edge.kind in ("array", "inline_list"):
                # A nested list envelope is addressed through its `data`, the
                # same prefix rule as a top-level list (`lines.data.price`)
                # — `components/cross_cutting.md` §3.3.2 step 4. `data` is a
                # prefix marker, not an edge: the walk continues on the item.
                # A bare array field is a harmless no-op — its values are
                # always full objects already — and a bare inline list is the
                # expansion itself.
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
    elif _inline_list_item(prop) is not None:
        # An expand-only inline list: an embedded list envelope whose `data`
        # holds one referenced object type (`charge.refunds`, recorded Phase
        # 9). Inflation comes from the child table through INLINE_LISTS.
        kind = "inline_list"
    else:
        kind = "embedded"
    return _Edge(
        kind=kind,
        target=_first_ref(prop) or _inline_list_item(prop) or "",
        children={},
        polymorphic=POLYMORPHIC_REFS.get((schema_name, field_name)),
    )


def _inline_list_item(prop: dict) -> str | None:
    """The item schema of an inline list envelope's `data`, else None."""
    props = prop.get("properties")
    if prop.get("type") != "object" or not isinstance(props, dict):
        return None
    data = props.get("data")
    items = data.get("items") if isinstance(data, dict) else None
    if isinstance(items, dict) and "$ref" in items:
        return _ref_name(items["$ref"])
    return None


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
            if edge.polymorphic is not None:
                # The prefix names the table: one batched fetch per prefix,
                # the same chunking as the single-target path (§3.3.4).
                by_object: dict[str, list[str]] = {}
                for _, value in values:
                    if isinstance(value, str):
                        for prefix, object_name in edge.polymorphic.items():
                            if value.startswith(prefix):
                                by_object.setdefault(object_name, []).append(value)
                resolved: dict[str, dict[str, object]] = {}
                for object_name, ids in by_object.items():
                    resolved.update(_fetch_by_object(ctx, BY_OBJECT, object_name, ids))
                for obj, value in values:
                    if isinstance(value, str) and value in resolved:
                        obj[field_name] = resolved[value]
            else:
                ids = [value for _, value in values if isinstance(value, str)]
                cache = _fetch(ctx, BY_OBJECT, edge, ids)
                for obj, value in values:
                    if isinstance(value, str):
                        obj[field_name] = cache[value]
        if edge.kind == "inline_list":
            # The registry key is read off each object's own discriminator:
            # one level's frontier can mix schemas (the trie merges paths
            # whose hops land on different objects), so the parent type is a
            # property of the object, not of the level. Parents group by
            # their registration so the child fetch stays one batched query
            # per level, not one per row (§3.3.4). No registration is a
            # scope cut (customer.sources and friends) — the field stays
            # absent rather than failing, matching its never-emitted
            # unexpanded shape.
            groups: dict[InlineList, list[dict[str, object]]] = {}
            for obj in frontier:
                if not isinstance(obj.get("id"), str):
                    continue
                inline = INLINE_LISTS.get((str(obj.get("object")), field_name))
                if inline is not None:
                    groups.setdefault(inline, []).append(obj)
            for inline, parents in groups.items():
                _inflate_inline_list(ctx, BY_OBJECT, parents, field_name, inline)
            if edge.children:
                # A hop under the envelope (`refunds.data.charge`) descends
                # into exactly the page the response carries — `values` was
                # captured before inflation, so the envelope is re-read —
                # never into rows `has_more` hides, which no caller could
                # observe (functional spec §6.3: nothing validated is
                # silently ignored).
                page_items: list[dict[str, object]] = []
                for obj in frontier:
                    envelope = obj.get(field_name)
                    if isinstance(envelope, dict) and envelope.get("object") == "list":
                        page_items.extend(
                            item for item in envelope.get(DATA, []) if isinstance(item, dict)
                        )
                if page_items:
                    _inflate_level(ctx, page_items, edge.children)
            continue
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


def _inflate_inline_list(
    ctx: seahaven.Ctx,
    by_object: dict[str, Any],
    parents: list[dict[str, object]],
    field_name: str,
    inline: InlineList,
) -> None:
    """Substitute each parent's absent field with its inline list envelope,
    built from the child table scoped to the parent id: newest-first, the
    first INLINE_PAGE items with `has_more` beyond that — the recorded shape
    of `expand[]=refunds` (Phase 9)."""
    spec = by_object.get(inline.child_object)
    if spec is None:
        raise seahaven.WorldBug(
            f"inline list {field_name!r} names unregistered object {inline.child_object!r}"
        )
    by_parent: dict[str, list[dict[str, Any]]] = {str(obj["id"]): [] for obj in parents}
    wanted = sorted(by_parent)
    for start in range(0, len(wanted), _CHUNK):
        chunk = wanted[start : start + _CHUNK]
        placeholders = ", ".join("?" for _ in chunk)
        rows = ctx.db.rows(
            f"SELECT * FROM {spec.table} WHERE {inline.scope_column} IN ({placeholders}) "
            "ORDER BY x_seq DESC",
            *chunk,
        )
        for row in rows:
            by_parent.setdefault(row[inline.scope_column], []).append(spec.serializer(ctx, row))
    for obj in parents:
        items = by_parent.get(str(obj["id"]), [])
        obj[field_name] = {
            "object": "list",
            "data": items[:INLINE_PAGE],
            "has_more": len(items) > INLINE_PAGE,
            "url": inline.url_template.format(id=obj["id"]),
        }


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
    return _fetch_by_object(ctx, by_object, _discriminator(edge.target), ids)


def _fetch_by_object(
    ctx: seahaven.Ctx,
    by_object: dict[str, Any],
    object_name: str,
    ids: Sequence[str],
) -> dict[str, dict[str, object]]:
    """`_fetch`'s batched read against an API object name directly — the
    polymorphic path's spelling, where the prefix (not the schema's first
    `$ref`) named the table."""
    if not ids:
        return {}
    spec = by_object.get(object_name)
    if spec is None:
        raise seahaven.WorldBug(
            f"cannot expand into {object_name!r}: no resource is registered for it yet"
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
        raise seahaven.WorldBug(f"dangling reference into {object_name!r}: {missing}")
    return resolved


@cache
def _discriminator(schema_name: str) -> str:
    properties = spec_document()["components"]["schemas"][schema_name]["properties"]
    return properties["object"]["enum"][0]
