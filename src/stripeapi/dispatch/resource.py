"""The `ResourceSpec` engine: generated CRUD from one declaration per resource.

The rule that decides what it serves, stated once so a reviewer can apply it:
an operation is generated when its whole behavior is validate → touch one row,
or one page of rows, of one table → serialize. An operation that moves money,
drives a status machine, writes a second table, or computes rather than reads
is hand-written (`components/dispatcher.md` §3.5). The engine never imports a
resource module; it works entirely from `req.route.resource`.

`before_create` / `before_update` are the one escape, with a deliberately
narrow contract: given the validated `Request` and the column dict the engine
is about to write, return the column dict it should write instead. A
normalizer may derive columns and may `SELECT` to validate a reference; it may
not write another table, emit an event, or change the response.

There is no per-resource ordering knob: every list orders by `x_seq DESC`, the
monotonic insert counter (`components/data_model.md` §3.11 retires the
`sort`/`order_column` idea the dispatcher sketch carried).
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, Literal

import seahaven

from stripeapi import _ids, _json, _seq
from stripeapi.dispatch.response import Handler, Page, Request
from stripeapi.resources import _lookup, events
from stripeapi.serialize import fields
from stripeapi.stripe_errors import invalid_request, missing_parameter, resource_missing

if TYPE_CHECKING:
    from stripeapi.dispatch.params import ParamSpec

__all__ = [
    "BY_OBJECT",
    "DeleteSpec",
    "ListFilter",
    "ListPage",
    "Normalizer",
    "ResourceSpec",
    "Scope",
    "Serializer",
    "create",
    "delete",
    "list_",
    "page",
    "page_embedded",
    "register",
    "retrieve",
    "update",
]

Serializer = Callable[[seahaven.Ctx, Mapping[str, Any]], dict[str, Any]]
Normalizer = Callable[[seahaven.Ctx, Request, dict[str, Any]], dict[str, Any]]

Action = Literal["list", "create", "retrieve", "update", "delete"]


@dataclass(frozen=True, slots=True)
class ListFilter:
    """One query parameter a generated list serves from a column."""

    name: str  # the query parameter: "email", "created", "status"
    column: str
    kind: Literal["exact", "range", "literal"]
    choices: tuple[str, ...] = ()
    id_prefixes: tuple[str, ...] = ()
    required: bool = False  # `subscription` on GET /v1/subscription_items


@dataclass(frozen=True, slots=True)
class DeleteSpec:
    """How a resource's generated DELETE behaves."""

    mode: Literal["soft", "hard"]
    requires_status: tuple[str, ...] = ()  # ("draft",) for invoices
    status_column: str = "status"


@dataclass(frozen=True, slots=True)
class Scope:
    """How a nested collection is constrained to one parent.

    The parent is looked up first, so a bad `{charge}` is Stripe's
    `resource_missing` rather than an empty page or a `DbError` from a foreign
    key (`architecture.md` §7).
    """

    path_param: str  # "charge"
    column: str  # "charge_id" on the child table
    parent: ResourceSpec  # looked up before the child query runs


@dataclass(frozen=True, slots=True)
class ResourceSpec:
    """One resource's declaration, from which the engine serves CRUD."""

    object: str  # "customer" — the API discriminator
    table: str
    id_prefix: str
    collection_url: str  # "/v1/customers"
    serializer: Serializer
    columns: tuple[str, ...]  # the SELECT list; data_model.md owns it
    list_filters: tuple[ListFilter, ...] = ()
    creatable: ParamSpec | None = None
    updatable: ParamSpec | None = None
    delete: DeleteSpec | None = None
    metadata: bool = True
    before_create: Normalizer | None = None
    before_update: Normalizer | None = None
    created_event: str | None = None  # "customer.created"
    updated_event: str | None = None
    deleted_event: str | None = None


#: Every registered resource by API object name. The expansion resolver reads
#: it to inflate a reference; resource modules register at import.
BY_OBJECT: dict[str, ResourceSpec] = {}


def register(spec: ResourceSpec) -> ResourceSpec:
    if spec.object in BY_OBJECT:
        raise seahaven.WorldBug(f"resource {spec.object!r} is registered twice")
    BY_OBJECT[spec.object] = spec
    return spec


# --- Pagination ----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ListPage:
    """One page of raw rows and the `has_more` the probe row paid for."""

    rows: list[dict[str, Any]]
    has_more: bool

    def envelope(
        self, url: str, serialize: Callable[[Mapping[str, Any]], dict[str, Any]]
    ) -> dict[str, Any]:
        return {
            "object": "list",
            "data": [serialize(row) for row in self.rows],
            "has_more": self.has_more,
            "url": url,
        }


_RANGE_SQL: Final[dict[str, str]] = {
    "eq": "=",
    "gt": ">",
    "gte": ">=",
    "lt": "<",
    "lte": "<=",
}


def page(
    ctx: seahaven.Ctx,
    *,
    table: str,
    object_name: str,
    where: Sequence[str] = (),
    params: Sequence[Any] = (),
    limit: int,
    starting_after: str | None,
    ending_before: str | None,
) -> ListPage:
    """One page, cut by at most one cursor. Raises for a bad cursor; never writes.

    Ordering is `x_seq DESC` — a total order under a frozen clock, where
    `created` alone is not (`components/data_model.md` §3.11). `has_more` means
    "at least one more object in the direction of travel": older objects for a
    default or `starting_after` page, newer ones for `ending_before`, whose
    ascending scan is reversed in Python so `data` is newest-first whichever
    cursor produced it.
    """
    if starting_after is not None and ending_before is not None:
        raise seahaven.WorldBug("page() received both cursors; bind rejects that")
    clauses = list(where)
    binds: list[Any] = list(params)
    if starting_after is not None:
        clauses.append("x_seq < ?")
        binds.append(_cursor_seq(ctx, table, object_name, starting_after, "starting_after"))
    if ending_before is not None:
        clauses.append("x_seq > ?")
        binds.append(_cursor_seq(ctx, table, object_name, ending_before, "ending_before"))
    where_sql = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    direction = "ASC" if ending_before is not None else "DESC"
    rows = ctx.db.rows(
        f"SELECT * FROM {table}{where_sql} ORDER BY x_seq {direction} LIMIT ?", *binds, limit + 1
    )
    has_more = len(rows) > limit
    chosen = rows[:limit]
    if ending_before is not None:
        chosen = list(reversed(chosen))
    return ListPage(rows=chosen, has_more=has_more)


def _cursor_seq(ctx: seahaven.Ctx, table: str, object_name: str, id: str, param: str) -> int:
    """Resolve a cursor to its `x_seq`.

    Resolution is scoped to the table, not to the filter: a cursor naming a
    real customer on a filtered list still resolves — it is a coordinate in
    the ordering, and only the page query applies the filter
    (`components/cross_cutting.md` §3.2.3). A soft-deleted row resolves the
    same way; the list's own `deleted = 0` keeps it out of the page.
    """
    row = ctx.db.one(f"SELECT x_seq FROM {table} WHERE id = ?", id)
    if row is None:
        raise resource_missing(object_name, id, param=param)
    return int(row["x_seq"])


def page_embedded(
    items: Sequence[dict[str, Any]],
    page: Page,
    *,
    url: str,
    object_name: str,
) -> dict[str, Any]:
    """The same cursor semantics over an in-memory list of serialized items.

    For the four routed reads that page over JSON nested on a parent row rather
    than a table (`components/dispatcher.md` §5); no handler reimplements
    pagination.
    """
    if page.starting_after is not None and page.ending_before is not None:
        raise seahaven.WorldBug("page_embedded received both cursors; bind rejects that")
    chosen = list(items)
    if page.starting_after is not None:
        chosen = _after_embedded_cursor(items, page.starting_after, object_name, "starting_after")
    elif page.ending_before is not None:
        newer_first = _after_embedded_cursor(
            list(reversed(items)), page.ending_before, object_name, "ending_before"
        )
        chosen = list(reversed(newer_first))
    has_more = len(chosen) > page.limit
    # Forward: the newest `limit` of the older-than-cursor set. Backward: the
    # `limit` nearest the cursor — the previous page, matching `page()`'s
    # ascending-then-reverse scan — and never the cursor row itself, which the
    # live API excludes however close to exhausted the walk is (probed this
    # phase: `ending_before` past the second-newest returns the newest alone).
    data = chosen[-page.limit :] if page.ending_before is not None else chosen[: page.limit]
    return {
        "object": "list",
        "data": data,
        "has_more": has_more,
        "url": url,
    }


def _after_embedded_cursor(
    items: Sequence[dict[str, Any]],
    cursor: str,
    object_name: str,
    param: str,
) -> list[dict[str, Any]]:
    ids = [item["id"] for item in items]
    try:
        index = ids.index(cursor)
    except ValueError:
        raise resource_missing(object_name, cursor, param=param) from None
    return list(items[index + 1 :])


# --- The engine actions ---------------------------------------------------------


def _spec_of(req: Request) -> ResourceSpec:
    spec = req.route.resource
    if spec is None:
        raise seahaven.WorldBug(f"route {req.route.op_id} carries no ResourceSpec")
    return spec


def _path_id(req: Request) -> tuple[str, str]:
    """The id a path-scoped action names: the pattern's last placeholder."""
    spec = req.route.params
    if spec is None or not spec.path:
        raise seahaven.WorldBug(f"route {req.route.op_id} has no path placeholder")
    name = spec.path[-1]
    return name, req.path_params[name]


def _scope_clause(ctx: seahaven.Ctx, req: Request) -> tuple[str, Any] | None:
    """The parent of a scoped route, looked up before any child query runs."""
    scope = req.route.scope
    if scope is None:
        return None
    parent_id = req.path_params[scope.path_param]
    _lookup.require_row(
        ctx, scope.parent.table, scope.parent.object, parent_id, param=scope.path_param
    )
    return scope.column, parent_id


def list_(ctx: seahaven.Ctx, req: Request) -> dict[str, Any]:
    """`GET /v1/<collection>`: filters, one page, the list envelope."""
    spec = _spec_of(req)
    where: list[str] = []
    binds: list[Any] = []
    scoped = _scope_clause(ctx, req)
    if scoped is not None:
        column, parent_id = scoped
        where.append(f"{column} = ?")
        binds.append(parent_id)
    for flt in spec.list_filters:
        if flt.name not in req.params:
            if flt.required:
                # Belt-and-braces: bind step 4 already raises this through
                # `body_of`, so the unreachable fallback at least carries the
                # same code and message.
                raise missing_parameter(flt.name)
            continue
        value = req.params[flt.name]
        if flt.kind == "range":
            for op, bound in value.items():
                where.append(f"{flt.column} {_RANGE_SQL[op]} ?")
                binds.append(bound)
        else:
            where.append(f"{flt.column} = ?")
            binds.append(value)
    if spec.delete is not None and spec.delete.mode == "soft":
        where.append("deleted = 0")
    if req.page is None:
        raise seahaven.WorldBug(f"paginated route {req.route.op_id} bound without a page")
    one_page = page(
        ctx,
        table=spec.table,
        object_name=spec.object,
        where=where,
        params=binds,
        limit=req.page.limit,
        starting_after=req.page.starting_after,
        ending_before=req.page.ending_before,
    )
    return one_page.envelope(req.path, lambda row: spec.serializer(ctx, row))


def retrieve(ctx: seahaven.Ctx, req: Request) -> dict[str, Any]:
    """`GET /v1/<collection>/{id}`: one row, or the deleted stub."""
    spec = _spec_of(req)
    path_param, id_ = _path_id(req)
    scoped = _scope_clause(ctx, req)
    if scoped is not None:
        column, parent_id = scoped
        row = ctx.db.one(
            f"SELECT * FROM {spec.table} WHERE id = ? AND {column} = ?", id_, parent_id
        )
        if row is None:
            raise resource_missing(spec.object, id_, param=path_param)
    else:
        row = _lookup.require_row(ctx, spec.table, spec.object, id_, param=path_param)
    if spec.delete is not None and spec.delete.mode == "soft" and row.get("deleted") == 1:
        return fields.deleted_stub(spec.object, id_)
    return spec.serializer(ctx, row)


def _store(value: Any) -> Any:
    """A coerced parameter value as SQLite stores it: booleans as 0/1, JSON
    columns through the one canonical dump."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, dict | list):
        return _json.dumps(value)
    return value


def create(ctx: seahaven.Ctx, req: Request) -> dict[str, Any]:
    """`POST /v1/<collection>`: mint, insert, answer with the re-read row.

    Reading back rather than echoing the parameters means the response is
    exactly what a later retrieve answers, defaults included.
    """
    spec = _spec_of(req)
    cols: dict[str, Any] = {
        "id": _ids.stripe_id(ctx, spec.id_prefix),
        "x_seq": _seq.next_seq(ctx, spec.table),
        "created": ctx.clock.iso(),
    }
    cols.update({column: _store(value) for column, value in req.params.items()})
    if req.metadata is not None:
        cols["metadata"] = _json.dumps(dict(req.metadata.apply({})))
    if spec.before_create is not None:
        cols = spec.before_create(ctx, req, cols)
    columns = ", ".join(cols)
    placeholders = ", ".join("?" for _ in cols)
    ctx.db.execute(f"INSERT INTO {spec.table} ({columns}) VALUES ({placeholders})", *cols.values())
    row = _lookup.require_row(ctx, spec.table, spec.object, cols["id"], param="id")
    body = spec.serializer(ctx, row)
    if spec.created_event is not None:
        events.emit_event(ctx, type=spec.created_event, obj=body)
    return body


def update(ctx: seahaven.Ctx, req: Request) -> dict[str, Any]:
    """`POST /v1/<collection>/{id}`: present-means-set, absent-means-unchanged."""
    spec = _spec_of(req)
    path_param, id_ = _path_id(req)
    row = _lookup.require_row(ctx, spec.table, spec.object, id_, param=path_param)
    old = spec.serializer(ctx, row)
    sets: dict[str, Any] = {column: _store(value) for column, value in req.params.items()}
    if req.metadata is not None:
        current = _json.loads(row.get("metadata"))
        sets["metadata"] = _json.dumps(dict(req.metadata.apply(current or {})))
    if spec.before_update is not None:
        sets = spec.before_update(ctx, req, sets)
    if sets:
        assignments = ", ".join(f"{column} = ?" for column in sets)
        ctx.db.execute(f"UPDATE {spec.table} SET {assignments} WHERE id = ?", *sets.values(), id_)
    fresh = _lookup.require_row(ctx, spec.table, spec.object, id_, param=path_param)
    new = spec.serializer(ctx, fresh)
    if spec.updated_event is not None:
        previous = {key: value for key, value in old.items() if new.get(key) != value}
        events.emit_event(ctx, type=spec.updated_event, obj=new, previous=previous or None)
    return new


def delete(ctx: seahaven.Ctx, req: Request) -> dict[str, Any]:
    """`DELETE /v1/<collection>/{id}`: soft keeps the row, hard removes it.

    Both answer the three-key `deleted` stub with status 200, which is why the
    nine stub DELETE routes set `expand=False`.
    """
    spec = _spec_of(req)
    delete_spec = spec.delete
    if delete_spec is None:
        raise seahaven.WorldBug(f"route {req.route.op_id} is generated-delete with no DeleteSpec")
    path_param, id_ = _path_id(req)
    row = _lookup.require_row(ctx, spec.table, spec.object, id_, param=path_param)
    if (
        delete_spec.requires_status
        and row.get(delete_spec.status_column) not in delete_spec.requires_status
    ):
        # The one refusal is invoice-shaped (`invoice_not_editable`); its exact
        # code and message are the invoices phase's to pin, so none is invented
        # here (`components/dispatcher.md` §3.5.5).
        raise invalid_request(
            f"This {spec.object} cannot be deleted in its current state.", param=path_param
        )
    if spec.deleted_event is not None:
        events.emit_event(ctx, type=spec.deleted_event, obj=spec.serializer(ctx, row))
    if delete_spec.mode == "soft":
        ctx.db.execute(f"UPDATE {spec.table} SET deleted = 1 WHERE id = ?", id_)
    else:
        ctx.db.execute(f"DELETE FROM {spec.table} WHERE id = ?", id_)
    return fields.deleted_stub(spec.object, id_)


_ENGINE: Final[dict[Action, Handler]] = {
    "list": list_,
    "create": create,
    "retrieve": retrieve,
    "update": update,
    "delete": delete,
}
