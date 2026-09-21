"""The invoiceitems slice (Phase 13): the pending-item rows a manual
invoice sweeps, as their own resource.

Every wire shape and refusal is a live probe at `2026-08-26.dahlia`
(2026-09-21, cassette 13): an `amount`+`currency` create mints a one-off
price and product behind `pricing.price_details` exactly as live does (the
recorded body names both minted ids); the list is newest-first with
`customer`/`pending`/`invoice`/`created`(→`date`) filters; the unknown-id
family is Stripe's own odd spelling `No such Invoice Item: '…'(livemode=
false)` under `param: id`; a pending item deletes hard (three-key stub,
404 after) while an item attached to a no-longer-editable invoice refuses
both delete and update — an item whose invoice was deleted reads as
deleted ("This invoice item has been deleted.").

The live body's `invoicing_rules` is a field the pinned spec does not
declare; omitted like every other undeclared live echo (allow-listed).

Scope cuts, declared in `allowed_differences.py`: the
`price_data`/`pricing`/`discounts`/`tax_code`/`tax_behavior`/
`unit_amount_decimal`/`quantity_decimal`/`subscription`/`customer_account`
parameters (this world creates items by `amount`+`currency` or nothing).
"""

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

import seahaven

from stripeapi import _ids, _json, _seq, _time
from stripeapi.dispatch.params import Param, ParamSpec
from stripeapi.dispatch.resource import ResourceSpec, register
from stripeapi.resources import _lookup, events
from stripeapi.serialize.fields import FieldMap, presence_sets, serializer_for
from stripeapi.stripe_errors import StripeApiError, invalid_request

if TYPE_CHECKING:
    from stripeapi.dispatch.response import Request

__all__ = [
    "FIELDS",
    "ITEM_CREATE",
    "ITEM_DELETE",
    "ITEM_LIST",
    "ITEM_RETRIEVE",
    "ITEM_UPDATE",
    "SPEC",
    "apply_line_edit",
    "create",
    "delete",
    "insert_invoice_item",
    "list_",
    "serialize",
    "update",
]

CUS = ("cus_",)
IN = ("in_",)
TXR = ("txr_",)

_PERIOD = Param(
    name="period",
    kind="object",
    shape=(
        Param(name="end", kind="timestamp"),
        Param(name="start", kind="timestamp"),
    ),
)

_TAX_RATES = Param(name="tax_rates", kind="array", item=Param(name="", kind="id", id_prefixes=TXR))

ITEM_CREATE = ParamSpec(
    op_id="PostInvoiceitems",
    body=(
        Param(name="amount", kind="integer", required=True),
        Param(name="currency", kind="currency", required=True),
        Param(name="customer", kind="id", id_prefixes=CUS, required=True),
        Param(name="description", kind="string", max_length=5_000),
        Param(name="discountable", kind="boolean"),
        Param(name="invoice", kind="id", id_prefixes=IN),
        _PERIOD,
        Param(name="quantity", kind="integer", minimum=1),
        _TAX_RATES,
    ),
    metadata=True,
)

ITEM_UPDATE = ParamSpec(
    op_id="PostInvoiceitemsInvoiceitem",
    path=("invoiceitem",),
    body=(
        Param(name="amount", kind="integer"),
        Param(name="description", kind="string", max_length=5_000),
        Param(name="discountable", kind="boolean"),
        _PERIOD,
        Param(name="quantity", kind="integer", minimum=1),
        _TAX_RATES,
    ),
    metadata=True,
)

ITEM_LIST = ParamSpec(
    op_id="GetInvoiceitems",
    paginated=True,
    body=(
        # `invoiceitem` has no `created`; the filter maps onto `date`
        # (`components/data_model.md` §3.11) — `list_` applies it there.
        Param(name="created", kind="range"),
        Param(name="customer", kind="id", id_prefixes=CUS),
        Param(name="invoice", kind="id", id_prefixes=IN),
        Param(name="pending", kind="boolean"),
    ),
)

ITEM_RETRIEVE = ParamSpec(
    op_id="GetInvoiceitemsInvoiceitem",
    path=("invoiceitem",),
)

ITEM_DELETE = ParamSpec(
    op_id="DeleteInvoiceitemsInvoiceitem",
    path=("invoiceitem",),
    expand=False,
)

# --- the one-off price minter ---------------------------------------------------------


def _mint_one_off_price(
    ctx: seahaven.Ctx,
    *,
    amount: int,
    currency: str,
    name: str,
    product_id: str | None = None,
) -> str:
    """The recorded mechanism behind an `amount`+`currency` item: live mints
    a one-off price (and its product) and `pricing.price_details` names
    them; so does this world. The rows are ordinary catalog rows — exactly
    what the real API leaves behind in `/v1/prices` — so they carry the
    catalog's own `product.created`/`price.created` events, in the recorded
    product-first order (cassette 07).

    `product_id` is the re-mint seam: an amount edit mints a NEW price
    against the SAME product (recorded, cassette 13 step 33 — the price id
    changes on the wire, the product does not), so no product row or event
    is written when it is passed."""
    from stripeapi.resources import prices, products

    if product_id is None:
        product_id = _ids.stripe_id(ctx, "prod_")
        now = ctx.clock.iso()
        ctx.db.execute(
            "INSERT INTO products (id, x_seq, created, updated, active, images,"
            " marketing_features, metadata, name)"
            " VALUES (?, ?, ?, ?, 1, '[]', '[]', '{}', ?)",
            product_id,
            _seq.next_seq(ctx, "products"),
            now,
            now,
            name,
        )
        products._emit_created(
            ctx, _lookup.require_row(ctx, "products", "product", product_id, param="product")
        )
    price_id = _ids.stripe_id(ctx, "price_")
    ctx.db.execute(
        "INSERT INTO prices (id, x_seq, created, active, billing_scheme, currency,"
        " product, tax_behavior, type, unit_amount, unit_amount_decimal)"
        " VALUES (?, ?, ?, 1, 'per_unit', ?, ?, 'unspecified', 'one_time', ?, ?)",
        price_id,
        _seq.next_seq(ctx, "prices"),
        ctx.clock.iso(),
        currency,
        product_id,
        amount,
        str(amount),
    )
    prices._emit_created(ctx, _lookup.require_row(ctx, "prices", "price", price_id, param="price"))
    return price_id


def _product_of_price(ctx: seahaven.Ctx, price_id: str) -> str | None:
    row = ctx.db.one("SELECT product FROM prices WHERE id = ?", price_id)
    return None if row is None else str(row["product"])


def _pricing_body(ctx: seahaven.Ctx, *, amount: int, currency: str, name: str) -> str:
    price_id = _mint_one_off_price(ctx, amount=amount, currency=currency, name=name)
    return _json.dumps(
        {
            "type": "price_details",
            "price_details": {"price": price_id, "product": _product_of_price(ctx, price_id)},
            "unit_amount_decimal": str(amount),
        }
    )


def _no_such_invoice_item(id_: str, *, status: int = 404) -> StripeApiError:
    # Wire-verbatim (cassette 13 steps 10/64): the capitalized name and the
    # `(livemode=false)` suffix are Stripe's own odd spellings here.
    return invalid_request(
        f"No such Invoice Item: '{id_}'(livemode=false)",
        code="resource_missing",
        param="id",
        status=status,
    )


def _require_item(ctx: seahaven.Ctx, id_: str) -> dict[str, Any]:
    row = ctx.db.one("SELECT * FROM invoiceitems WHERE id = ?", id_)
    if row is None:
        raise _no_such_invoice_item(id_)
    return row


def _invoice_still_editable(ctx: seahaven.Ctx, invoice_id: str | None) -> bool:
    """The delete/update gate (recorded, cassette 13 steps 62-63): a pending
    item is editable; an item attached to a draft invoice is editable; an
    item whose invoice is finalized OR deleted is not — a hard-deleted
    invoice leaves its items attached to a dead id, and those read as
    deleted."""
    if invoice_id is None:
        return True
    invoice = ctx.db.one("SELECT status FROM invoices WHERE id = ?", invoice_id)
    return invoice is not None and invoice["status"] == "draft"


# --- the serializer -------------------------------------------------------------------


def _load_list(text: str | None) -> list[Any]:
    loaded: object = _json.loads(text)
    return loaded if isinstance(loaded, list) else []


def _tax_rate_bodies(ctx: seahaven.Ctx, row: Mapping[str, Any]) -> list[dict[str, Any]]:
    from stripeapi.resources import tax_rates

    bodies = []
    for id_ in _load_list(row["tax_rates"]):
        tax_row = _lookup.require_row(ctx, "tax_rates", "tax rate", str(id_), param="tax_rates")
        bodies.append(tax_rates._serialize(ctx, tax_row))
    return bodies


always_present, omit_when_none = presence_sets("invoiceitem")

FIELDS = FieldMap(
    object="invoiceitem",
    table="invoiceitems",
    columns={
        "id": "id",
        "date": "date",
        "amount": "amount",
        "currency": "currency",
        "customer": "customer",
        "description": "description",
        "discountable": "discountable",
        "discounts": "discounts",
        "frozen_fields": "frozen_fields",
        "invoice": "invoice",
        "metadata": "metadata",
        "net_amount": "net_amount",
        "parent": "parent",
        "proration": "proration",
        "proration_details": "proration_details",
        "quantity": "quantity",
        "quantity_decimal": "quantity_decimal",
        # derived below: period, pricing, tax_rates
    },
    timestamps=frozenset({"date"}),
    json_columns=frozenset(
        {"discounts", "frozen_fields", "metadata", "parent", "proration_details"}
    ),
    booleans=frozenset({"discountable", "proration"}),
    derived={
        "period": lambda ctx, row: {
            "start": _time.to_unix(row["period_start"]),
            "end": _time.to_unix(row["period_end"]),
        },
        "pricing": lambda ctx, row: _json.loads(row["pricing"]),
        "tax_rates": _tax_rate_bodies,
    },
    constants={
        "livemode": False,
        "customer_account": None,
        "test_clock": None,
    },
    always_present=always_present,
    omit_when_none=omit_when_none,
)

_serialize = serializer_for(FIELDS)


def serialize(ctx: seahaven.Ctx, row: Mapping[str, Any]) -> dict[str, Any]:
    return _serialize(ctx, row)


# --- the row writer the invoice routes share ------------------------------------------


def insert_invoice_item(
    ctx: seahaven.Ctx,
    *,
    customer_id: str,
    amount: int,
    currency: str,
    description: str | None,
    discountable: bool,
    invoice_id: str | None,
    period_start: str,
    period_end: str,
    tax_rate_ids: Sequence[str] = (),
    quantity: int = 1,
    proration: bool = False,
) -> dict[str, Any]:
    """Write one invoiceitem row (minting its one-off price) and emit
    `invoiceitem.created`."""
    now = ctx.clock.iso()
    item_id = _ids.stripe_id(ctx, "ii_")
    pricing = _pricing_body(
        ctx, amount=amount, currency=currency, name=description or "One-time item"
    )
    ctx.db.execute(
        "INSERT INTO invoiceitems (id, x_seq, date, amount, currency, customer,"
        " description, discountable, discounts, frozen_fields, invoice, metadata,"
        " parent, period_end, period_start, pricing, proration, proration_details,"
        " quantity, quantity_decimal, tax_rates)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, '[]', '[]', ?, '{}',"
        " NULL, ?, ?, ?, ?, NULL, ?, ?, ?)",
        item_id,
        _seq.next_seq(ctx, "invoiceitems"),
        now,
        amount,
        currency,
        customer_id,
        description,
        int(discountable),
        invoice_id,
        period_end,
        period_start,
        pricing,
        int(proration),
        quantity,
        str(quantity),
        _json.dumps(list(tax_rate_ids)),
    )
    row = _require_item(ctx, item_id)
    events.emit_event(ctx, type="invoiceitem.created", obj=serialize(ctx, row))
    return row


def apply_line_edit(ctx: seahaven.Ctx, item_id: str, edit: Mapping[str, Any]) -> None:
    """One line edit routed onto its backing item. An `amount` change mints
    a NEW one-off price (recorded, cassette 13 step 33 — the price id
    changes on the wire, the product does not); a `quantity` change only
    restamps the multiplier — the row's `amount` is the unit amount and the
    line bills unit x quantity, so the total follows at the next rebuild."""
    row = _require_item(ctx, item_id)
    sets: dict[str, Any] = {}
    if "amount" in edit:
        amount = int(edit["amount"])
        sets["amount"] = amount
        loaded: object = _json.loads(row["pricing"])
        pricing: dict[str, Any] = loaded if isinstance(loaded, dict) else {}
        details = pricing.get("price_details") or {}
        old_product = (
            _product_of_price(ctx, str(details.get("price")))
            if isinstance(details, dict) and details.get("price")
            else None
        )
        # Re-mint against the SAME product (recorded: only the price id
        # changes) — the seam skips the product INSERT and its event, so
        # the catalog row and the pricing echo keep naming one product.
        new_price = _mint_one_off_price(
            ctx,
            amount=amount,
            currency=row["currency"],
            name=row["description"] or "One-time item",
            product_id=old_product,
        )
        sets["pricing"] = _json.dumps(
            {
                "type": "price_details",
                "price_details": {
                    "price": new_price,
                    "product": _product_of_price(ctx, new_price),
                },
                "unit_amount_decimal": str(amount),
            }
        )
    if "quantity" in edit:
        quantity = int(edit["quantity"])
        sets["quantity"] = quantity
        sets["quantity_decimal"] = str(quantity)
    if "description" in edit:
        sets["description"] = edit["description"]
    if "discountable" in edit:
        sets["discountable"] = int(edit["discountable"])
    if "tax_rates" in edit:
        sets["tax_rates"] = _json.dumps(list(edit["tax_rates"]))
    period = edit.get("period")
    if isinstance(period, dict):
        if "start" in period:
            sets["period_start"] = period["start"]
        if "end" in period:
            sets["period_end"] = period["end"]
    if sets:
        assignments = ", ".join(f"{column} = ?" for column in sets)
        ctx.db.execute(
            f"UPDATE invoiceitems SET {assignments} WHERE id = ?", *sets.values(), item_id
        )


# --- the handlers ----------------------------------------------------------------------


def create(ctx: seahaven.Ctx, req: Request) -> dict[str, Any]:
    params = req.params
    customer = _lookup.require_row(
        ctx, "customers", "customer", params["customer"], param="customer"
    )
    invoice_id = params.get("invoice")
    old_invoice = None
    if invoice_id is not None:
        invoice = _lookup.require_row(ctx, "invoices", "invoice", invoice_id, param="invoice")
        if invoice["status"] != "draft":
            # A create aimed at a finalized invoice is the same not-editable
            # family the line endpoints answer (unprobed; the declared
            # ruling reuses the recorded spelling).
            raise invalid_request(
                "Invalid invoice: This invoice is no longer editable",
                code="invoice_not_editable",
            )
        from stripeapi.resources import invoices

        old_invoice = invoices.serialize(ctx, invoice)
    period = params.get("period") or {}
    now = ctx.clock.iso()
    metadata: dict[str, str] = {}
    if req.metadata is not None:
        metadata = dict(req.metadata.apply({}))
    row = insert_invoice_item(
        ctx,
        customer_id=customer["id"],
        amount=params["amount"],
        currency=params["currency"],
        description=params.get("description"),
        discountable=params.get("discountable", True),
        invoice_id=invoice_id,
        period_start=period.get("start") or now,
        period_end=period.get("end") or now,
        tax_rate_ids=params.get("tax_rates", ()),
        quantity=params.get("quantity", 1),
    )
    if metadata:
        ctx.db.execute(
            "UPDATE invoiceitems SET metadata = ? WHERE id = ?", _json.dumps(metadata), row["id"]
        )
        row = _require_item(ctx, row["id"])
    if invoice_id is not None:
        # The draft's lines include the new item in the same call (the
        # invoice the caller sees is the invoice the item joined).
        from stripeapi.billing import invoicing
        from stripeapi.resources import invoices

        assert old_invoice is not None  # set beside the draft guard above
        fresh = invoicing.rebuild_invoice_lines(ctx, invoice_id)
        new_invoice = invoices.serialize(ctx, fresh)
        previous = invoices._previous_attributes(old_invoice, new_invoice)
        invoicing.emit_invoice_event(ctx, "invoice.updated", fresh, previous=previous or None)
    return serialize(ctx, row)


def retrieve(ctx: seahaven.Ctx, req: Request) -> dict[str, Any]:
    row = ctx.db.one("SELECT * FROM invoiceitems WHERE id = ?", req.path_params["invoiceitem"])
    if row is None:
        raise _no_such_invoice_item(req.path_params["invoiceitem"])
    return serialize(ctx, row)


def update(ctx: seahaven.Ctx, req: Request) -> dict[str, Any]:
    """Update a pending (or draft-attached) item; an item whose invoice is
    gone reads as deleted (recorded). No `invoiceitem.updated` exists in
    the closed event set — Stripe's own catalog emits none for this
    resource, so neither does this world."""
    row = _require_item(ctx, req.path_params["invoiceitem"])
    if not _invoice_still_editable(ctx, row["invoice"]):
        # Wire-verbatim (cassette 13 step 66).
        raise invalid_request("This invoice item has been deleted.")
    apply_line_edit(ctx, row["id"], req.params)
    if req.metadata is not None:
        current = _json.loads(row.get("metadata"))
        ctx.db.execute(
            "UPDATE invoiceitems SET metadata = ? WHERE id = ?",
            _json.dumps(dict(req.metadata.apply(current or {}))),
            row["id"],
        )
    return serialize(ctx, _require_item(ctx, row["id"]))


def list_(ctx: seahaven.Ctx, req: Request) -> dict[str, Any]:
    """`GET /v1/invoiceitems`: hand-written for the one filter the engine
    has no knob for — `pending` is a null-test on `invoice`, not a value
    comparison (recorded: `pending=true` answers exactly the unswept
    items)."""
    from stripeapi.dispatch import resource

    where: list[str] = []
    binds: list[Any] = []
    for name, value in req.params.items():
        if name == "pending":
            where.append("invoice IS NULL" if value else "invoice IS NOT NULL")
        elif name == "customer":
            where.append("customer = ?")
            binds.append(value)
        elif name == "invoice":
            where.append("invoice = ?")
            binds.append(value)
        elif name == "created":
            # `invoiceitem` has no `created` column: the filter reads
            # `date` (`components/data_model.md` §3.11).
            for op, bound in value.items():
                where.append(f"date {resource._RANGE_SQL[op]} ?")
                binds.append(bound)
        else:
            raise seahaven.WorldBug(f"unexpected list parameter {name!r} on GetInvoiceitems")
    if req.page is None:
        raise seahaven.WorldBug("GetInvoiceitems bound without a page")
    one_page = resource.page(
        ctx,
        table="invoiceitems",
        object_name="invoiceitem",
        where=where,
        params=binds,
        limit=req.page.limit,
        starting_after=req.page.starting_after,
        ending_before=req.page.ending_before,
    )
    return one_page.envelope(req.path, lambda row: serialize(ctx, row))


def delete(ctx: seahaven.Ctx, req: Request) -> dict[str, Any]:
    """ "Delete a pending item; an attached one refuses (recorded)."""
    row = _require_item(ctx, req.path_params["invoiceitem"])
    if not _invoice_still_editable(ctx, row["invoice"]):
        # Wire-verbatim (cassette 13 step 65).
        raise invalid_request(
            "Can't delete an invoice item that is attached to an invoice that is no longer editable"
        )
    events.emit_event(ctx, type="invoiceitem.deleted", obj=serialize(ctx, row))
    ctx.db.execute("DELETE FROM invoiceitems WHERE id = ?", row["id"])
    return {"id": row["id"], "object": "invoiceitem", "deleted": True}


SPEC = register(
    ResourceSpec(
        object="invoiceitem",
        table="invoiceitems",
        id_prefix="ii_",
        collection_url="/v1/invoiceitems",
        serializer=serialize,
        columns=tuple(FIELDS.columns),
        # The recorded 404 family is not the `No such …` shape at all (see
        # `_no_such_invoice_item`); the handlers raise it themselves.
        missing_path_param="id",
        error_name="invoiceitem",
        # The list is hand-written (`list_`): the `pending` filter is a
        # null-test the engine has no knob for, so the filter set lives in
        # `ITEM_LIST.body` and nowhere else (the subscriptions pattern).
        creatable=None,
        updatable=None,
        delete=None,
        metadata=True,
        created_event=None,
        updated_event=None,
    )
)
