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
    from stripeapi.dispatch.params import Param, ParamSpec

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
    kind: Literal["exact", "range", "literal", "boolean", "in", "json"]
    choices: tuple[str, ...] = ()
    id_prefixes: tuple[str, ...] = ()
    required: bool = False  # `subscription` on GET /v1/subscription_items
    # True when the live API answers an empty page rather than an error when
    # this filter is absent — the parameter is de facto required but not de
    # jure (probed at the pinned version: `GET /v1/payment_methods` without
    # `customer` returns nothing, with it the customer's attached methods).
    empty_without: bool = False
    # The object name this filter's value must name. When set, the engine
    # looks the row up before querying and refuses at 400 `resource_missing`
    # — probed: `GET /v1/payment_methods?customer=<unknown-or-deleted>`
    # answers "No such customer", query-side, unlike a path id's 404. Bind
    # checks only the prefix; existence is a table read, and bind reads none.
    references: str | None = None
    # A `kind="json"` filter's accepted subfields, as body-shaped `Param`s:
    # `prices?recurring[interval]=month` is one of these.
    sub_shape: tuple[Param, ...] = ()


@dataclass(frozen=True, slots=True)
class DeleteSpec:
    """How a resource's generated DELETE behaves."""

    mode: Literal["soft", "hard"]
    requires_status: tuple[str, ...] = ()  # ("draft",) for invoices
    status_column: str = "status"
    # 0/1 columns the soft delete zeroes before the deleted-event snapshot is
    # taken: `product.deleted` carries `active: false` and `coupon.deleted`
    # carries `valid: false` (both recorded, Phase 7) — the snapshot is the
    # post-write row, not the row as it was read.
    zero_columns: tuple[str, ...] = ()
    # What a later retrieve of the tombstoned row answers. Stripe is
    # per-resource inconsistent here (both recorded, Phase 7): a deleted
    # customer retrieves as the three-key stub, a deleted product or coupon
    # is a 404 `resource_missing` naming the path id.
    deleted_retrieve: Literal["stub", "missing"] = "stub"
    # A refusal that depends on other rows, raised before any write: the
    # engine's own `requires_status` covers the row's own state, and this
    # covers everything else (a product with attached prices refuses its
    # delete, recorded in cassette 07). Raising is correct — nothing has
    # been written yet.
    guard: Callable[[seahaven.Ctx, Mapping[str, Any]], None] | None = None


@dataclass(frozen=True, slots=True)
class Scope:
    """How a nested collection is constrained to one parent.

    The parent is looked up first, so a bad `{charge}` is Stripe's
    `resource_missing` rather than an empty page or a `DbError` from a foreign
    key (`architecture.md` §7). `missing_param` overrides the `param` that
    lookup names — live Stripe is per-resource inconsistent here too (probed
    at the pinned version: `/v1/customers/{customer}/balance_transactions`
    with a bad customer names `customer`, while
    `/v1/customers/{customer}/payment_methods` names `id`).
    """

    path_param: str  # "charge"
    column: str  # "charge_id" on the child table
    parent: ResourceSpec  # looked up before the child query runs
    missing_param: str | None = None


@dataclass(frozen=True, slots=True)
class ResourceSpec:
    """One resource's declaration, from which the engine serves CRUD."""

    object: str  # "customer" — the API discriminator
    table: str
    id_prefix: str
    collection_url: str  # "/v1/customers"
    serializer: Serializer
    columns: tuple[str, ...]  # the SELECT list; data_model.md owns it
    # How `create` mints the row's id. None — the default — draws
    # `_ids.stripe_id(ctx, id_prefix)`; the one resource whose id is
    # caller-suppliable and unprefixed (coupon) overrides it, because
    # `stripe_id` refuses the empty prefix by design.
    mint_id: Callable[[seahaven.Ctx, str | None], str] | None = None
    # The `param` a missing path id names, when live Stripe does not use the
    # placeholder: it is per-resource inconsistent (probed: customers and
    # charges say "id"; prices and payment_methods keep the placeholder;
    # nested sub-resource paths always keep it), so each slice pins its own
    # spelling from its recordings. None means the placeholder.
    missing_path_param: str | None = None
    # The name Stripe's own error messages call this resource — its spelling
    # is not the discriminator's (probed: `No such PaymentMethod: 'pm_…'`
    # against `object: "payment_method"`; customers are merely lucky that
    # "customer" is both). None means the discriminator.
    error_name: str | None = None
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
    clauses = list(where)
    binds: list[Any] = list(params)
    # Resolve before refusing the pair: a bogus cursor 400s even when both
    # cursors were sent (probed live, Phase 5 cassettes scenario 09), so
    # resolution is the first thing that touches the table.
    after_seq = (
        None
        if starting_after is None
        else _cursor_seq(ctx, table, object_name, starting_after, "starting_after")
    )
    before_seq = (
        None
        if ending_before is None
        else _cursor_seq(ctx, table, object_name, ending_before, "ending_before")
    )
    if after_seq is not None and before_seq is not None:
        # Wire-verbatim from the live probe with two real ids; no `code` —
        # the live envelope carries type and message only.
        raise invalid_request(
            "Received both starting_after and ending_before parameters. Please pass in only one.",
            pre_execution=True,
        )
    if after_seq is not None:
        clauses.append("x_seq < ?")
        binds.append(after_seq)
    if before_seq is not None:
        clauses.append("x_seq > ?")
        binds.append(before_seq)
    where_sql = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    direction = "ASC" if before_seq is not None else "DESC"
    rows = ctx.db.rows(
        f"SELECT * FROM {table}{where_sql} ORDER BY x_seq {direction} LIMIT ?", *binds, limit + 1
    )
    has_more = len(rows) > limit
    chosen = rows[:limit]
    if ending_before is not None:
        chosen = list(reversed(chosen))
    return ListPage(rows=chosen, has_more=has_more)


def _cursor_seq(ctx: seahaven.Ctx, table: str, object_name: str, id: str, param: str) -> int:
    """Resolve a cursor to its `x_seq`, at 400 (`resource_missing`).

    Resolution is scoped to the table, not to the filter: a cursor naming a
    real customer on a filtered list still resolves — it is a coordinate in
    the ordering, and only the page query applies the filter
    (`components/cross_cutting.md` §3.2.3). A soft-deleted row resolves the
    same way; the list's own `deleted = 0` keeps it out of the page. The
    status is the recorded one (Phase 5 cassettes, scenario 09): a query-side
    `resource_missing` is a 400, unlike the 404 a path id earns.
    """
    row = ctx.db.one(f"SELECT x_seq FROM {table} WHERE id = ?", id)
    if row is None:
        raise resource_missing(object_name, id, param=param, status=400)
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
    chosen = list(items)
    # The same resolve-then-refuse order as `page()`: a bogus cursor 400s even
    # alongside a second cursor, and only a pair that both resolves is refused.
    after_at = (
        None
        if page.starting_after is None
        else _cursor_index(items, page.starting_after, object_name, "starting_after")
    )
    before_at = (
        None
        if page.ending_before is None
        else _cursor_index(items, page.ending_before, object_name, "ending_before")
    )
    if after_at is not None and before_at is not None:
        raise invalid_request(
            "Received both starting_after and ending_before parameters. Please pass in only one.",
            pre_execution=True,
        )
    if after_at is not None:
        chosen = list(items[after_at + 1 :])
    elif before_at is not None:
        # The newer-than-cursor set, already newest-first; the final slice
        # below takes the `limit` nearest the cursor — the previous page.
        chosen = list(items[:before_at])
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


def _cursor_index(
    items: Sequence[dict[str, Any]],
    cursor: str,
    object_name: str,
    param: str,
) -> int:
    """Resolve a cursor against an embedded list, at 400 like `page()`."""
    ids = [item["id"] for item in items]
    try:
        return ids.index(cursor)
    except ValueError:
        raise resource_missing(object_name, cursor, param=param, status=400) from None


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


def _missing_param(spec: ResourceSpec, path_param: str) -> str:
    """The `param` a missing path id names: the resource's pinned spelling,
    else the placeholder (`ResourceSpec.missing_path_param`)."""
    return spec.missing_path_param if spec.missing_path_param is not None else path_param


def _display_name(spec: ResourceSpec) -> str:
    """The resource's name in Stripe's own messages (`error_name`)."""
    return spec.error_name if spec.error_name is not None else spec.object


def _scope_clause(ctx: seahaven.Ctx, req: Request) -> tuple[str, Any] | None:
    """The parent of a scoped route, looked up before any child query runs."""
    scope = req.route.scope
    if scope is None:
        return None
    parent_id = req.path_params[scope.path_param]
    _lookup.require_row(
        ctx,
        scope.parent.table,
        scope.parent.object,
        parent_id,
        param=scope.missing_param if scope.missing_param is not None else scope.path_param,
    )
    return scope.column, parent_id


def _require_filter_reference(ctx: seahaven.Ctx, flt: ListFilter, value: str) -> None:
    """A filter value that names another resource must name a live one.

    The 400 and the soft-delete refusal are both probed at the pinned version
    on the payment-methods customer filter; a tombstoned customer is as
    missing as an absent one, unlike a path id where the deleted row still
    resolves (probed, cassette 06 step 23 — and step 24 for the same rule on
    attach's customer parameter; the path-id contrast is
    `components/cross_cutting.md` §3.2.4).
    """
    parent = BY_OBJECT.get(flt.references)
    if parent is None:
        raise seahaven.WorldBug(
            f"list filter {flt.name!r} references unknown object {flt.references!r}"
        )
    row = ctx.db.one(f"SELECT * FROM {parent.table} WHERE id = ?", value)
    soft = parent.delete is not None and parent.delete.mode == "soft"
    if row is None or (soft and row["deleted"] == 1):
        raise resource_missing(_display_name(parent), value, param=flt.name, status=400)


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
            if flt.empty_without and req.route.scope is None:
                # Not an error: the de facto required filter is simply absent,
                # and the live API answers an empty page (see ListFilter). A
                # scoped route never lands here — its parent pins the column.
                # A cursor still resolves first — `page()` reads the table
                # before these clauses ever run.
                where.append("1 = 0")
            continue
        value = req.params[flt.name]
        if flt.references is not None:
            _require_filter_reference(ctx, flt, value)
        if flt.kind == "range":
            for op, bound in value.items():
                where.append(f"{flt.column} {_RANGE_SQL[op]} ?")
                binds.append(bound)
        elif flt.kind == "boolean":
            where.append(f"{flt.column} = ?")
            binds.append(int(value))
        elif flt.kind == "in":
            # An array-valued filter (`products?ids[]=`, `prices?lookup_keys[]=`):
            # membership in the column, empty page for an empty array.
            if value:
                placeholders = ", ".join("?" for _ in value)
                where.append(f"{flt.column} IN ({placeholders})")
                binds.extend(value)
            else:
                where.append("1 = 0")
        elif flt.kind == "json":
            # A filter over one JSON object column (`prices?recurring[interval]=`):
            # each present subfield becomes one `json_extract` comparison.
            for sub_key, sub_value in value.items():
                where.append(f"json_extract({flt.column}, '$.{sub_key}') = ?")
                binds.append(sub_value)
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
        object_name=_display_name(spec),
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
    error_param = _missing_param(spec, path_param)
    scoped = _scope_clause(ctx, req)
    if scoped is not None:
        column, parent_id = scoped
        row = ctx.db.one(
            f"SELECT * FROM {spec.table} WHERE id = ? AND {column} = ?", id_, parent_id
        )
        if row is None:
            if ctx.db.one(f"SELECT id FROM {spec.table} WHERE id = ?", id_) is None:
                raise resource_missing(_display_name(spec), id_, param=error_param)
            # The row exists but is not this parent's: probed at the pinned
            # version (GET /v1/customers/{c}/payment_methods/{pm} of another
            # customer's pm, attached or not) — a 404 carrying type, message
            # and `param: <scope placeholder>` only, no `code`. Note the
            # spelling split within one path: the *missing parent* names
            # `Scope.missing_param` ("id" here), the mismatch names the
            # placeholder itself ("customer") — both probed.
            scope = req.route.scope
            assert scope is not None  # only a scoped retrieve reaches here
            raise invalid_request("Invalid request", param=scope.path_param, status=404)
    else:
        row = _lookup.require_row(ctx, spec.table, _display_name(spec), id_, param=error_param)
    if spec.delete is not None and spec.delete.mode == "soft" and row.get("deleted") == 1:
        if spec.delete.deleted_retrieve == "missing":
            # Probed (Phase 7): a deleted product or coupon is a 404 on
            # retrieve — `No such product: 'prod_…'` — while a deleted
            # customer retrieves as the three-key stub (also probed). The
            # knob carries the split; customers keep the default.
            raise resource_missing(_display_name(spec), id_, param=error_param)
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
        "id": (
            _ids.stripe_id(ctx, spec.id_prefix)
            if spec.mint_id is None
            else spec.mint_id(ctx, req.params.get("id"))
        ),
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
    row = _lookup.require_row(ctx, spec.table, _display_name(spec), cols["id"], param="id")
    body = spec.serializer(ctx, row)
    if spec.created_event is not None:
        events.emit_event(ctx, type=spec.created_event, obj=body)
    return body


def update(ctx: seahaven.Ctx, req: Request) -> dict[str, Any]:
    """`POST /v1/<collection>/{id}`: present-means-set, absent-means-unchanged."""
    spec = _spec_of(req)
    path_param, id_ = _path_id(req)
    error_param = _missing_param(spec, path_param)
    row = _lookup.require_row(ctx, spec.table, _display_name(spec), id_, param=error_param)
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
    fresh = _lookup.require_row(ctx, spec.table, _display_name(spec), id_, param=error_param)
    new = spec.serializer(ctx, fresh)
    if spec.updated_event is not None:
        previous = _previous_attributes(old, new)
        events.emit_event(ctx, type=spec.updated_event, obj=new, previous=previous or None)
    return new


def _previous_attributes(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """The changed keys' prior values, with `metadata` diffed per key.

    Stripe's own `previous_attributes` shows only the metadata keys that
    changed — `metadata: {"a": null}` for a newly-set key, the old value for
    a changed or removed one (recorded on `product.updated` and
    `coupon.updated`, Phase 7 probe) — not the whole prior map, which is
    what a naive field-level diff would put there.
    """
    previous: dict[str, Any] = {}
    for key, value in old.items():
        fresh = new.get(key)
        if fresh == value:
            continue
        if key == "metadata" and isinstance(value, dict) and isinstance(fresh, dict):
            per_key = {k: v for k, v in value.items() if fresh.get(k) != v}
            per_key.update({k: None for k in fresh if k not in value})
            if per_key:
                previous["metadata"] = per_key
        else:
            previous[key] = value
    return previous


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
    error_param = _missing_param(spec, path_param)
    row = _lookup.require_row(ctx, spec.table, _display_name(spec), id_, param=error_param)
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
    if delete_spec.guard is not None:
        delete_spec.guard(ctx, row)
    # The event is emitted AFTER the write, like create and update, per the
    # cross-cutting rule "after every row for that change is written"
    # (`components/cross_cutting.md` §3.4.2). This is the deliberately settled
    # Phase 3 carry-over: the snapshot is the serializer's view of the row, and
    # for a soft delete that view is identical before and after the tombstone
    # because `deleted` is not a serialized field — recorded against real test
    # mode at the pinned version, `customer.deleted`'s `data.object` is the
    # FULL pre-delete object with NO `deleted` key, never the three-key stub
    # and never `deleted: true` (cassette `probe_customer_payment_method_
    # events`, step for `type=customer.deleted`). Emission order is therefore
    # unobservable through the snapshot and is fixed by the rule instead.
    if delete_spec.mode == "soft":
        if delete_spec.zero_columns:
            assignments = ", ".join(f"{column} = 0" for column in delete_spec.zero_columns)
            ctx.db.execute(f"UPDATE {spec.table} SET deleted = 1, {assignments} WHERE id = ?", id_)
        else:
            ctx.db.execute(f"UPDATE {spec.table} SET deleted = 1 WHERE id = ?", id_)
    else:
        ctx.db.execute(f"DELETE FROM {spec.table} WHERE id = ?", id_)
    if spec.deleted_event is not None:
        # `zero_columns` flips serialized fields (`product.active`,
        # `coupon.valid`) and the snapshot must show them flipped
        # (`product.deleted` carries `active: false`, `coupon.deleted`
        # `valid: false`, both probed Phase 7), so the snapshot row is
        # re-read after the write.
        final = (
            _lookup.require_row(ctx, spec.table, _display_name(spec), id_, param=error_param)
            if delete_spec.zero_columns
            else row
        )
        events.emit_event(ctx, type=spec.deleted_event, obj=spec.serializer(ctx, final))
    return fields.deleted_stub(spec.object, id_)


_ENGINE: Final[dict[Action, Handler]] = {
    "list": list_,
    "create": create,
    "retrieve": retrieve,
    "update": update,
    "delete": delete,
}
