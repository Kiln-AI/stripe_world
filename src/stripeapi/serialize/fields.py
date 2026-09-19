"""The per-resource field map: how one row becomes one Stripe object.

Everything a serializer does is declared here rather than coded per resource,
so a rename that lives only in a Python dict is a rename that cannot drift
(`components/data_model.md` §1). The rules the map carries:

- a column is named exactly after its Stripe field; only the declared
  flattenings differ, and none of them exist in this phase's resources;
- `object` comes from the map and `livemode` from `constants` — neither is
  stored;
- columns in `timestamps` are canonical TEXT converted to Unix seconds by
  `_time.to_unix`, the only such conversion in the package;
- columns in `json_columns` are inflated by `_json.loads`;
- columns in `booleans` are the `INTEGER NOT NULL CHECK (IN (0, 1))` of
  data_model rule 4 and emit `bool(value)`;


The `booleans` frozenset and the `OMIT` sentinel are additions to the
`FieldMap` sketch in `components/data_model.md`'s Public Interface: rule 4
needs the former (the sketch has no way to know `delinquent` is a boolean) and
§7's omitted-constants table needs the latter. Flagged here as the visible
deviation it is.
"""

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from functools import cache
from typing import Any

import seahaven

from stripeapi import _json, _time
from stripeapi.spec import spec_document

__all__ = ["OMIT", "FieldMap", "deleted_stub", "to_api"]

#: The sentinel for a `constants` entry whose API field is omitted entirely —
#: `customer.sources` and friends, which exist only as expand-only inline lists
#: this world never emits (`components/data_model.md` §7).
OMIT = object()


@cache
def spec_schemas() -> Mapping[str, Any]:
    """The pruned spec's schema table, read once per process."""
    return spec_document()["components"]["schemas"]


def presence_sets(object_name: str) -> tuple[frozenset[str], frozenset[str]]:
    """`always_present` and `omit_when_none` for one object, from the spec.

    `always_present` is the set of required and nullable fields (data_model §8):
    those are emitted even
    when `None`, as JSON `null`. Everything else is omitted when valueless.
    """
    schema = spec_schemas()[object_name]
    properties = schema.get("properties", {})
    required = set(schema.get("required", ()))
    always = required | {name for name, prop in properties.items() if prop.get("nullable")}
    return frozenset(always), frozenset(properties) - always


@dataclass(frozen=True, slots=True)
class FieldMap:
    """One resource's row-to-object declaration."""

    object: str
    table: str
    columns: Mapping[str, str]  # column -> API field; no `x_` column appears here
    timestamps: frozenset[str] = frozenset()
    json_columns: frozenset[str] = frozenset()
    booleans: frozenset[str] = frozenset()
    money: frozenset[str] = frozenset()
    decimals: frozenset[str] = frozenset()
    constants: Mapping[str, object] = field(default_factory=dict)
    derived: Mapping[str, Callable[[seahaven.Ctx, Mapping[str, Any]], object]] = field(
        default_factory=dict
    )
    always_present: frozenset[str] = frozenset()
    omit_when_none: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        for column in self.columns:
            if column.startswith("x_"):
                msg = f"{self.object}: world-internal column {column!r} must not be serialised"
                raise ValueError(msg)


def to_api(ctx: seahaven.Ctx, fmap: FieldMap, row: Mapping[str, Any]) -> dict[str, Any]:
    """One row -> one Stripe object, unexpanded.

    References serialise as the bare id string; inflation is the expansion
    resolver's business. Raises `KeyError`-shaped `WorldBug` material if a
    mapped column is absent from the row — a SELECT list that disagrees with
    the map is an authoring bug, and a loud one.
    """
    out: dict[str, Any] = {"object": fmap.object}
    for column, api_name in fmap.columns.items():
        if column not in row:
            msg = f"{fmap.object}: column {column!r} absent from the row"
            raise seahaven.WorldBug(msg)
        value = row[column]
        if column in fmap.timestamps:
            out[api_name] = _time.to_unix(value)
        elif column in fmap.json_columns:
            out[api_name] = _json.loads(value)
        elif column in fmap.booleans:
            out[api_name] = bool(value)
        elif column in fmap.decimals:
            # A TEXT decimal literal emitted as a JSON number of exactly the
            # digits stored (data_model rule 6): no float ever exists here.
            out[api_name] = json.loads(value) if value is not None else None
        else:
            out[api_name] = value
    for api_name, constant in fmap.constants.items():
        if constant is OMIT:
            continue
        out[api_name] = constant
    for api_name, derive in fmap.derived.items():
        out[api_name] = derive(ctx, row)
    return _apply_presence(fmap, out)


def _apply_presence(fmap: FieldMap, out: dict[str, Any]) -> dict[str, Any]:
    """Drop the valueless fields the spec does not declare always-present."""
    for api_name in fmap.omit_when_none:
        if api_name in out and out[api_name] is None:
            del out[api_name]
    return out


def deleted_stub(object_name: str, id: str) -> dict[str, Any]:
    """The three-key shape of a soft-deleted object's retrieve."""
    return {"id": id, "object": object_name, "deleted": True}


def serializer_for(fmap: FieldMap) -> Callable[[seahaven.Ctx, Mapping[str, Any]], dict[str, Any]]:
    """`ResourceSpec.serializer` for one field map: `to_api` with the map bound."""

    def serialize(ctx: seahaven.Ctx, row: Mapping[str, Any]) -> dict[str, Any]:
        return to_api(ctx, fmap, row)

    return serialize
