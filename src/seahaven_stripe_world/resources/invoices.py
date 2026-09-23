"""The invoices slice (Phase 13): the five-status machine's routed surface
and the serializer every `invoice.*` event carries.

Every wire shape and refusal is a live probe at `2026-08-26.dahlia`
(2026-09-21, cassette 13): the wrong-state refusals are no-`code` spellings
("You can only pass in open invoices. This invoice isn't open.",
"Invoices with \\`paid\\` payments cannot be voided.", …), the 404 `param`
spelling is per-endpoint (GET/DELETE//send//add_lines name `invoice`; the
POST sub-actions name `id`), `/pay` on a draft finalizes and collects inside
the one call, and a $0 finalize settles to `paid` with
`attempted: true, attempt_count: 0`.

Body rulings against the recordings: `rendering` is emitted as null (the
recorded account's dashboard default object is an account echo, and
subscription invoices record null); `hosted_invoice_url`/`invoice_pdf` are
derived id-shaped URLs from finalization on (the credit_note.pdf
precedent); `webhooks_delivered_at` mirrors `created`; `payments` and
`threshold_reason` are omitted (neither required nor ever non-empty here);
`account_country`/`account_name` come from the instance's static account
config (`startup`-family state, defaulting like the ledger's constants).

Scope cuts, declared in `allowed_differences.py`: `create_preview` and
`attach_payment` stay unrouted; the Connect/shipping/rendering/custom-field
create/update parameters, `/pay`'s `mandate`/`off_session`/`source`/
`payment_method` family, and `invoice_payment.paid` (no invoice_payment
object exists) are cut.
"""

from collections.abc import Mapping
from typing import Any

import seahaven

from seahaven_stripe_world import _json, _time
from seahaven_stripe_world.billing import invoicing
from seahaven_stripe_world.dispatch.params import Param, ParamSpec
from seahaven_stripe_world.dispatch.resource import (
    ListFilter,
    ResourceSpec,
    register,
)
from seahaven_stripe_world.dispatch.response import ApiResponse, Request
from seahaven_stripe_world.resources import _lookup
from seahaven_stripe_world.serialize.fields import FieldMap, presence_sets, serializer_for
from seahaven_stripe_world.stripe_errors import invalid_request

__all__ = [
    "FIELDS",
    "INVOICE_CREATE",
    "INVOICE_LIST",
    "INVOICE_RETRIEVE",
    "INVOICE_UPDATE",
    "SPEC",
    "add_lines",
    "create",
    "delete",
    "finalize",
    "list_lines",
    "mark_uncollectible",
    "pay",
    "remove_lines",
    "send",
    "serialize",
    "update",
    "update_line",
    "update_lines",
    "void",
]

CUS = ("cus_",)
IN = ("in_",)
PM = ("pm_",)
TXR = ("txr_",)

#: The recorded `status` filter enum, message-verbatim order included
#: (cassette 13 step 18 — the order is Stripe's own, not the schema's).
STATUS_FILTERS = ("draft", "open", "void", "paid", "uncollectible")

_PERIOD = Param(
    name="period",
    kind="object",
    shape=(
        Param(name="end", kind="timestamp", required=True),
        Param(name="start", kind="timestamp", required=True),
    ),
)

_TAX_RATES = Param(name="tax_rates", kind="array", item=Param(name="", kind="id", id_prefixes=TXR))

_EDITABLE_LINE = (
    Param(name="amount", kind="integer"),
    Param(name="description", kind="string", max_length=5_000),
    Param(name="discountable", kind="boolean"),
    _PERIOD,
    Param(name="quantity", kind="integer", minimum=1),
    _TAX_RATES,
)

INVOICE_CREATE = ParamSpec(
    op_id="PostInvoices",
    body=(
        Param(name="auto_advance", kind="boolean"),
        Param(
            name="collection_method",
            kind="literal",
            choices=("charge_automatically", "send_invoice"),
        ),
        Param(name="currency", kind="currency"),
        Param(name="customer", kind="id", id_prefixes=CUS, required=True),
        Param(name="days_until_due", kind="integer", minimum=1, maximum=365),
        Param(name="default_payment_method", kind="id", id_prefixes=PM),
        _TAX_RATES,
        Param(name="description", kind="string", max_length=5_000, unset_with_empty_string=True),
        Param(name="due_date", kind="timestamp"),
        Param(name="footer", kind="string", max_length=5_000, unset_with_empty_string=True),
        Param(
            name="pending_invoice_items_behavior",
            kind="literal",
            choices=("include", "exclude"),
        ),
        Param(name="statement_descriptor", kind="string", max_length=22),
    ),
    metadata=True,
)

INVOICE_UPDATE = ParamSpec(
    op_id="PostInvoicesInvoice",
    path=("invoice",),
    body=(
        Param(name="auto_advance", kind="boolean"),
        Param(
            name="collection_method",
            kind="literal",
            choices=("charge_automatically", "send_invoice"),
        ),
        Param(name="days_until_due", kind="integer", minimum=1, maximum=365),
        Param(
            name="default_payment_method", kind="id", id_prefixes=PM, unset_with_empty_string=True
        ),
        _TAX_RATES,
        Param(name="description", kind="string", max_length=5_000, unset_with_empty_string=True),
        Param(name="due_date", kind="timestamp"),
        Param(name="footer", kind="string", max_length=5_000, unset_with_empty_string=True),
        Param(name="statement_descriptor", kind="string", max_length=22),
    ),
    metadata=True,
)

INVOICE_LIST = ParamSpec(
    op_id="GetInvoices",
    paginated=True,
    body=(
        Param(
            name="collection_method",
            kind="literal",
            choices=("charge_automatically", "send_invoice"),
        ),
        Param(name="created", kind="range"),
        Param(name="customer", kind="id", id_prefixes=CUS),
        Param(name="due_date", kind="range"),
        Param(name="status", kind="literal", choices=STATUS_FILTERS),
        Param(name="subscription", kind="id", id_prefixes=("sub_",)),
    ),
)

INVOICE_RETRIEVE = ParamSpec(
    op_id="GetInvoicesInvoice",
    path=("invoice",),
)

INVOICE_FINALIZE = ParamSpec(
    op_id="PostInvoicesInvoiceFinalize",
    path=("invoice",),
    body=(Param(name="auto_advance", kind="boolean"),),
)

INVOICE_PAY = ParamSpec(
    op_id="PostInvoicesInvoicePay",
    path=("invoice",),
    body=(Param(name="paid_out_of_band", kind="boolean"),),
)

# add_lines is the create shape: `amount` (the line's unit amount) is
# required, `quantity` the multiplier beside it (a quantity without an
# amount has no unit to multiply — the binder's missing-parameter refusal
# answers it, and amount+quantity together hit the recorded XOR refusal).
_ADD_LINES_SHAPE = Param(
    name="",
    kind="object",
    shape=(
        Param(name="amount", kind="integer", required=True),
        Param(name="description", kind="string", max_length=5_000),
        Param(name="discountable", kind="boolean"),
        _PERIOD,
        Param(name="quantity", kind="integer", minimum=1),
        _TAX_RATES,
    ),
)

INVOICE_ADD_LINES = ParamSpec(
    op_id="PostInvoicesInvoiceAddLines",
    path=("invoice",),
    body=(Param(name="lines", kind="array", item=_ADD_LINES_SHAPE, required=True),),
)

_UPDATE_LINES_SHAPE = Param(
    name="",
    kind="object",
    shape=(Param(name="id", kind="id", id_prefixes=("il_",), required=True), *_EDITABLE_LINE),
)

INVOICE_UPDATE_LINES = ParamSpec(
    op_id="PostInvoicesInvoiceUpdateLines",
    path=("invoice",),
    body=(Param(name="lines", kind="array", item=_UPDATE_LINES_SHAPE, required=True),),
)

_REMOVE_LINES_SHAPE = Param(
    name="",
    kind="object",
    shape=(
        Param(name="id", kind="id", id_prefixes=("il_",), required=True),
        Param(name="behavior", kind="literal", choices=("delete", "unassign"), required=True),
    ),
)

INVOICE_REMOVE_LINES = ParamSpec(
    op_id="PostInvoicesInvoiceRemoveLines",
    path=("invoice",),
    body=(Param(name="lines", kind="array", item=_REMOVE_LINES_SHAPE, required=True),),
)

INVOICE_LINE_UPDATE = ParamSpec(
    op_id="PostInvoicesInvoiceLinesLineItemId",
    path=("invoice", "line_item_id"),
    body=_EDITABLE_LINE,
)

INVOICE_VOID = ParamSpec(op_id="PostInvoicesInvoiceVoid", path=("invoice",))
INVOICE_MARK_UNCOLLECTIBLE = ParamSpec(
    op_id="PostInvoicesInvoiceMarkUncollectible", path=("invoice",)
)
INVOICE_SEND = ParamSpec(op_id="PostInvoicesInvoiceSend", path=("invoice",))
INVOICE_LINES = ParamSpec(
    op_id="GetInvoicesInvoiceLines",
    path=("invoice",),
    paginated=True,
)
INVOICE_DELETE = ParamSpec(op_id="DeleteInvoicesInvoice", path=("invoice",), expand=False)

# --- the serializer -----------------------------------------------------------------


def _load_dict(text: str | None) -> dict[str, Any]:
    loaded: object = _json.loads(text)
    return loaded if isinstance(loaded, dict) else {}


def _load_list(text: str | None) -> list[Any]:
    loaded: object = _json.loads(text)
    return loaded if isinstance(loaded, list) else []


def _account_field(ctx: seahaven.Ctx, key: str, default: Any) -> Any:
    account = ctx.state.get("account")
    if isinstance(account, dict):
        return account.get(key, default)
    return default


def _lines_envelope(ctx: seahaven.Ctx, row: Mapping[str, Any]) -> dict[str, Any]:
    # The spec's four-key nested envelope; the recorded `total_count` is
    # undeclared by the pinned schema and omitted like `items.total_count`
    # (the Phase 12 ruling, allow-listed).
    return {
        "object": "list",
        "data": _load_list(row["lines"]),
        "has_more": False,
        "url": f"/v1/invoices/{row['id']}/lines",
    }


def _parent_body(ctx: seahaven.Ctx, row: Mapping[str, Any]) -> dict[str, Any] | None:
    if row["parent_type"] is None:
        return None
    subscription = row["parent_subscription"]
    return {
        "quote_details": None,
        "subscription_details": {
            # The immutable subscription-metadata snapshot at finalization;
            # this world stores no second copy, so the snapshot is the
            # subscription's own (empty unless it carries metadata).
            "metadata": _subscription_metadata(ctx, subscription),
            "subscription": subscription,
        },
        "type": "subscription_details",
    }


def _subscription_metadata(ctx: seahaven.Ctx, subscription_id: str | None) -> dict[str, Any]:
    if subscription_id is None:
        return {}
    row = ctx.db.one("SELECT metadata FROM subscriptions WHERE id = ?", subscription_id)
    metadata = _load_dict(row["metadata"]) if row is not None else {}
    return metadata


def _default_tax_rate_bodies(ctx: seahaven.Ctx, row: Mapping[str, Any]) -> list[dict[str, Any]]:
    from seahaven_stripe_world.resources import tax_rates

    bodies = []
    for id_ in _load_list(row["default_tax_rates"]):
        tax_row = _lookup.require_row(
            ctx, "tax_rates", "tax rate", str(id_), param="default_tax_rates"
        )
        bodies.append(tax_rates._serialize(ctx, tax_row))
    return bodies


def _discount_ids(ctx: seahaven.Ctx, row: Mapping[str, Any]) -> list[str]:
    loaded: object = _json.loads(row["discounts"])
    discounts = loaded if isinstance(loaded, list) else []
    return [dict(discount)["id"] for discount in discounts if isinstance(discount, dict)]


def _derived_url(row: Mapping[str, Any], kind: str) -> str | None:
    # The credit_note.pdf precedent: a deterministic id-shaped URL, present
    # only once finalization has made the invoice customer-facing. The
    # recorded URLs embed the dashboard account id and a signed payload;
    # the replay compares redaction placeholders against these.
    if row["status"] == "draft":
        return None
    return f"https://pay.stripe.com/invoice/{row['id']}/{kind}"


always_present, omit_when_none = presence_sets("invoice")

FIELDS = FieldMap(
    object="invoice",
    table="invoices",
    columns={
        "id": "id",
        "created": "created",
        "amount_due": "amount_due",
        "amount_overpaid": "amount_overpaid",
        "amount_paid": "amount_paid",
        "amount_paid_off_stripe": "amount_paid_off_stripe",
        "amount_remaining": "amount_remaining",
        "amount_shipping": "amount_shipping",
        "attempt_count": "attempt_count",
        "attempted": "attempted",
        "auto_advance": "auto_advance",
        "automatically_finalizes_at": "automatically_finalizes_at",
        "billing_reason": "billing_reason",
        "collection_method": "collection_method",
        "currency": "currency",
        "customer": "customer",
        "customer_address": "customer_address",
        "customer_email": "customer_email",
        "customer_name": "customer_name",
        "customer_phone": "customer_phone",
        "customer_shipping": "customer_shipping",
        "customer_tax_exempt": "customer_tax_exempt",
        "default_payment_method": "default_payment_method",
        "description": "description",
        "due_date": "due_date",
        "effective_at": "effective_at",
        "ending_balance": "ending_balance",
        "footer": "footer",
        "last_finalization_error": "last_finalization_error",
        "metadata": "metadata",
        "next_payment_attempt": "next_payment_attempt",
        "number": "number",
        "payment_settings": "payment_settings",
        "period_end": "period_end",
        "period_start": "period_start",
        "post_payment_credit_notes_amount": "post_payment_credit_notes_amount",
        "pre_payment_credit_notes_amount": "pre_payment_credit_notes_amount",
        "starting_balance": "starting_balance",
        "statement_descriptor": "statement_descriptor",
        "status": "status",
        "status_transitions": "status_transitions",
        "subtotal": "subtotal",
        "subtotal_excluding_tax": "subtotal_excluding_tax",
        "total": "total",
        "total_discount_amounts": "total_discount_amounts",
        "total_excluding_tax": "total_excluding_tax",
        "total_pretax_credit_amounts": "total_pretax_credit_amounts",
        "total_taxes": "total_taxes",
        # derived below: lines, parent, default_tax_rates, discounts, the
        # URLs, webhooks_delivered_at, account echoes, and §7's constants
    },
    timestamps=frozenset(
        {
            "created",
            "automatically_finalizes_at",
            "due_date",
            "effective_at",
            "next_payment_attempt",
            "period_end",
            "period_start",
        }
    ),
    json_columns=frozenset(
        {
            "customer_address",
            "customer_shipping",
            "last_finalization_error",
            "metadata",
            "payment_settings",
            "status_transitions",
            "total_discount_amounts",
            "total_pretax_credit_amounts",
            "total_taxes",
        }
    ),
    booleans=frozenset({"attempted", "auto_advance"}),
    derived={
        "lines": _lines_envelope,
        "parent": _parent_body,
        "default_tax_rates": _default_tax_rate_bodies,
        "discounts": _discount_ids,
        "hosted_invoice_url": lambda ctx, row: _derived_url(row, ""),
        "invoice_pdf": lambda ctx, row: _derived_url(row, "pdf"),
        "webhooks_delivered_at": lambda ctx, row: _time.to_unix(row["created"]),
        "account_country": lambda ctx, row: _account_field(ctx, "country", "US"),
        "account_name": lambda ctx, row: _account_field(ctx, "name", None),
    },
    constants={
        "application": None,
        "account_tax_ids": None,
        "automatic_tax": {
            "disabled_reason": None,
            "enabled": False,
            "liability": None,
            "provider": None,
            "status": None,
        },
        "custom_fields": None,
        "customer_account": None,
        "customer_tax_ids": [],
        "default_source": None,
        "from_invoice": None,
        "issuer": {"type": "self"},
        "latest_revision": None,
        "on_behalf_of": None,
        "receipt_number": None,
        "rendering": None,
        "shipping_cost": None,
        "shipping_details": None,
        "test_clock": None,
    },
    always_present=always_present,
    omit_when_none=omit_when_none,
)

_serialize = serializer_for(FIELDS)


def serialize(ctx: seahaven.Ctx, row: Mapping[str, Any]) -> dict[str, Any]:
    return _serialize(ctx, row)


# --- the shared row lookup and refusal helpers --------------------------------------


def _invoice(ctx: seahaven.Ctx, req: Request, *, param: str) -> dict[str, Any]:
    """The path invoice under this endpoint's own recorded `param` spelling
    (recorded, cassette 13 steps 67-76: the GET/DELETE//send//add_lines
    family names `invoice`, the POST sub-actions name `id`)."""
    return _lookup.require_row(ctx, "invoices", "invoice", req.path_params["invoice"], param=param)


def _require_draft(ctx: seahaven.Ctx, req: Request, *, param: str) -> dict[str, Any]:
    row = _invoice(ctx, req, param=param)
    if row["status"] != "draft":
        # Wire-verbatim (cassette 13 step 27): no code, no param.
        raise invalid_request(
            "Invalid invoice: This invoice is no longer editable",
            code="invoice_not_editable",
        )
    return row


def _require_voidable(ctx: seahaven.Ctx, req: Request, *, param: str) -> dict[str, Any]:
    """/void's own guard order (recorded): a paid invoice answers the
    payments-cannot-be-voided spelling before the isn't-open one."""
    row = _invoice(ctx, req, param=param)
    if row["status"] == "paid":
        raise invalid_request("Invoices with `paid` payments cannot be voided.")
    if row["status"] != "open":
        # Wire-verbatim (cassette 13 steps 41/47): no code, no param.
        raise invalid_request("You can only pass in open invoices. This invoice isn't open.")
    return row


def _recompute_window(ctx: seahaven.Ctx, row: Mapping[str, Any]) -> None:
    """Draft-only: recompute both window fields from `created` (recorded,
    cassette 13 steps 21-22 — re-setting `auto_advance` restores the exact
    original timestamps, not now-offset ones)."""
    ctx.db.execute(
        "UPDATE invoices SET automatically_finalizes_at = ?, next_payment_attempt = ? WHERE id = ?",
        invoicing.compute_automatically_finalizes_at(
            row["created"],
            auto_advance=bool(row["auto_advance"]),
            collection_method=row["collection_method"],
        ),
        invoicing.next_payment_attempt_at(
            row["created"],
            auto_advance=bool(row["auto_advance"]),
            collection_method=row["collection_method"],
        ),
        row["id"],
    )


# --- the handlers --------------------------------------------------------------------


def create(ctx: seahaven.Ctx, req: Request) -> dict[str, Any]:
    """`POST /v1/invoices`: the manual create. The sweep defaults to
    exclude (recorded — the unparameterized create answers an empty-lines
    draft even with pending items); `include` attaches them all."""
    params = req.params
    # A request-parameter lookup refuses at 400 (the `_check` id rule's
    # query-side half, recorded at cassette 13 step 80).
    customer = _lookup.require_row(
        ctx, "customers", "customer", params["customer"], param="customer", status=400
    )
    collection_method = params.get("collection_method", "charge_automatically")
    days_until_due = params.get("days_until_due")
    due_date = params.get("due_date")
    if collection_method == "send_invoice" and days_until_due is None and due_date is None:
        # Wire-verbatim (cassette 13 step 54).
        raise invalid_request(
            "If invoice collection method is 'send_invoice', you must specify "
            "'due_date' or 'days_until_due'."
        )
    if collection_method == "charge_automatically" and (days_until_due is not None or due_date):
        # Wire-verbatim (cassette 13 step 78).
        raise invalid_request(
            "You can only specify 'due_date' or 'days_until_due' if invoice "
            "collection method is 'send_invoice'."
        )
    currency = params.get("currency")
    if currency is None:
        pending = invoicing.pending_invoice_item_rows(ctx, customer["id"])
        currency = pending[0]["currency"] if pending else "usd"
    auto_advance = params.get("auto_advance", False)
    extra_columns: dict[str, Any] = {
        "description": params.get("description"),
        "footer": params.get("footer"),
        "statement_descriptor": params.get("statement_descriptor"),
        "default_payment_method": params.get("default_payment_method"),
    }
    if due_date is not None:
        # An absolute due date lands verbatim (the days detour would round);
        # omitted otherwise, so the days-derived value survives.
        extra_columns["due_date"] = due_date
    row = invoicing.create_invoice(
        ctx,
        customer_id=customer["id"],
        currency=currency,
        collection_method=collection_method,
        billing_reason="manual",
        totals=invoicing.compute_totals([], currency=currency),
        subscription_id=None,
        auto_advance=auto_advance,
        days_until_due=days_until_due,
        period_start=ctx.clock.iso(),
        period_end=ctx.clock.iso(),
        tax_rate_ids=params.get("default_tax_rates", ()),
        # The sweep and the field set land inside the one create, so the
        # emitted `invoice.created` snapshot is the object as answered.
        sweep=params.get("pending_invoice_items_behavior") == "include",
        extra_columns=extra_columns,
    )
    return serialize(ctx, row)


def _re_read(ctx: seahaven.Ctx, invoice_id: str) -> dict[str, Any]:
    return _lookup.require_row(ctx, "invoices", "invoice", invoice_id, param="invoice")


def update(ctx: seahaven.Ctx, req: Request) -> dict[str, Any]:
    """`POST /v1/invoices/{id}`: drafts take the editable field set;
    non-drafts take `metadata` only (both recorded, cassette 13 steps
    37-38 — description on a paid invoice refuses, metadata succeeds)."""
    params = req.params
    row = _invoice(ctx, req, param="id")
    if row["status"] != "draft" and params:
        # Any field parameter on a finalized invoice is the recorded
        # refusal; `metadata` is lifted out of `params` by the binder, so
        # its recorded success never reaches this branch.
        raise invalid_request(
            "Finalized invoices can't be updated in this way", param=sorted(params)[0]
        )
    old = serialize(ctx, row)
    if row["status"] != "draft":
        # metadata-only (recorded): apply and answer.
        return _apply_metadata(ctx, req, row, old)
    days_until_due = params.get("days_until_due")
    due_date = params.get("due_date")
    if days_until_due is not None or due_date is not None:
        method = params.get("collection_method", row["collection_method"])
        if method == "charge_automatically":
            raise invalid_request(
                "You can only specify 'due_date' or 'days_until_due' if invoice "
                "collection method is 'send_invoice'."
            )
        if days_until_due is not None:
            # The relative spelling derives from the invoice's own creation
            # (recorded), not from the update moment.
            due_date = _time.from_unix(_time.to_unix(row["created"]) + days_until_due * 86_400)
        ctx.db.execute("UPDATE invoices SET due_date = ? WHERE id = ?", due_date, row["id"])
    for column in (
        "collection_method",
        "default_payment_method",
        "description",
        "footer",
        "statement_descriptor",
    ):
        if column in params:
            ctx.db.execute(
                f"UPDATE invoices SET {column} = ? WHERE id = ?", params[column], row["id"]
            )
    if "default_tax_rates" in params:
        ctx.db.execute(
            "UPDATE invoices SET default_tax_rates = ? WHERE id = ?",
            _json.dumps(list(params["default_tax_rates"])),
            row["id"],
        )
    if "auto_advance" in params:
        ctx.db.execute(
            "UPDATE invoices SET auto_advance = ? WHERE id = ?",
            int(params["auto_advance"]),
            row["id"],
        )
    fresh = _re_read(ctx, row["id"])
    _recompute_window(ctx, fresh)
    return _apply_metadata(ctx, req, _re_read(ctx, row["id"]), old)


def _apply_metadata(
    ctx: seahaven.Ctx,
    req: Request,
    row: Mapping[str, Any],
    old: dict[str, Any],
) -> dict[str, Any]:
    if req.metadata is not None:
        current = _load_dict(row.get("metadata"))
        ctx.db.execute(
            "UPDATE invoices SET metadata = ? WHERE id = ?",
            _json.dumps(dict(req.metadata.apply(current))),
            row["id"],
        )
    fresh = _re_read(ctx, row["id"])
    new = serialize(ctx, fresh)
    previous = _previous_attributes(old, new)
    if previous:
        # Stripe emits `invoice.updated` only on change — a no-op update
        # answers 200 and writes no event row.
        invoicing.emit_invoice_event(ctx, "invoice.updated", fresh, previous=previous)
    return new


def _previous_attributes(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
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
    """`DELETE /v1/invoices/{id}`: draft-only (recorded refusal otherwise);
    the swept items stay attached to the dead invoice."""
    row = _invoice(ctx, req, param="invoice")
    if row["status"] != "draft":
        raise invalid_request("You can only delete draft invoices.")
    invoicing.delete_draft_invoice(ctx, row["id"])
    return {"id": row["id"], "object": "invoice", "deleted": True}


def finalize(ctx: seahaven.Ctx, req: Request) -> dict[str, Any]:
    row = _invoice(ctx, req, param="id")
    if row["status"] != "draft":
        raise invalid_request(
            "This invoice is already finalized, you can't re-finalize a non-draft invoice."
        )
    fresh = invoicing.finalize_and_settle(
        ctx, row["id"], auto_advance=req.params.get("auto_advance")
    )
    return serialize(ctx, fresh)


def pay(ctx: seahaven.Ctx, req: Request) -> dict[str, Any] | ApiResponse:
    """/pay: a draft finalizes and collects inside the one call (recorded);
    `paid_out_of_band` settles without an attempt or an `attempted` flip.

    A decline is an outcome, not an abandonment (architecture §7's
    raise-loses rule): the failed charge, the attempt counters and
    `invoice.payment_failed` are all real writes, so the 402 envelope is
    RETURNED — returning keeps them; raising would roll the whole call
    back, finalization included."""
    row = _invoice(ctx, req, param="id")
    if row["status"] == "paid":
        raise invalid_request("Invoice is already paid")
    if row["status"] == "draft":
        fresh = invoicing.finalize_and_settle(ctx, row["id"])
        if fresh["status"] == "paid":
            return serialize(ctx, fresh)
        row = fresh
    if row["status"] != "open":
        # void / uncollectible are dead ends (billing_engine §2); the
        # isn't-open spelling is the recorded family they belong to.
        raise invalid_request("You can only pass in open invoices. This invoice isn't open.")
    if req.params.get("paid_out_of_band"):
        outcome = invoicing._mark_paid(ctx, row, out_of_band=True)
        assert outcome["outcome"] == "paid"
        return serialize(ctx, _re_read(ctx, row["id"]))
    sub_row = None
    if row["parent_subscription"] is not None:
        sub_row = ctx.db.one("SELECT * FROM subscriptions WHERE id = ?", row["parent_subscription"])
    outcome = invoicing.pay_invoice(ctx, row["id"], sub_row=sub_row)
    if outcome["outcome"] == "failed":
        return ApiResponse(402, outcome["error"])
    return serialize(ctx, _re_read(ctx, row["id"]))


def void(ctx: seahaven.Ctx, req: Request) -> dict[str, Any]:
    row = _require_voidable(ctx, req, param="id")
    return serialize(ctx, invoicing.void_invoice(ctx, row["id"]))


def mark_uncollectible(ctx: seahaven.Ctx, req: Request) -> dict[str, Any]:
    row = _invoice(ctx, req, param="id")
    if row["status"] == "uncollectible":
        # Wire-verbatim (cassette 13 step 53).
        raise invalid_request("This invoice has already been marked uncollectible.")
    if row["status"] != "open":
        raise invalid_request("You can only pass in open invoices. This invoice isn't open.")
    return serialize(ctx, invoicing.mark_uncollectible_invoice(ctx, row["id"]))


def send(ctx: seahaven.Ctx, req: Request) -> dict[str, Any]:
    """/send: a draft finalizes first (and a $0 result settles, recorded);
    an open `send_invoice` answers its own unchanged body; anything else
    answers the recorded cannot-be-sent refusal."""
    row = _invoice(ctx, req, param="invoice")
    if row["status"] == "draft":
        fresh = invoicing.finalize_and_settle(ctx, row["id"])
        return serialize(ctx, fresh)
    if row["status"] == "open" and row["collection_method"] == "send_invoice":
        return serialize(ctx, row)
    # Wire-verbatim (cassette 13 step 57). The paid case is recorded; an
    # open charge_automatically invoice reads the same way here — email
    # collection is a send_invoice concept (declared ruling, unprobed).
    raise invalid_request(
        "This invoice cannot be sent right now. Please contact us via "
        "https://support.stripe.com/contact with details, so we can help."
    )


def list_lines(ctx: seahaven.Ctx, req: Request) -> dict[str, Any]:
    """`GET /v1/invoices/{id}/lines`: the same cursor semantics over the
    frozen nested list (`resource.page_embedded` — no handler reimplements
    pagination)."""
    from seahaven_stripe_world.dispatch.resource import page_embedded

    row = _invoice(ctx, req, param="invoice")
    if req.page is None:
        raise seahaven.WorldBug("GetInvoicesInvoiceLines bound without a page")
    return page_embedded(
        _load_list(row["lines"]),
        req.page,
        url=req.path,
        object_name="invoice",
    )


def _line_edit_checks(line: Mapping[str, Any]) -> None:
    if "amount" in line and "quantity" in line:
        # Wire-verbatim (probed, Phase 13's add_lines round): the pair is
        # mutually exclusive inside one line, and the refusal names amount.
        raise invalid_request(
            "You may only specify one of these parameters: amount, quantity.",
            param="lines[0][amount]",
        )


def _mint_line_item(
    ctx: seahaven.Ctx,
    invoice_row: Mapping[str, Any],
    line: Mapping[str, Any],
) -> str:
    """One add_lines entry as an attached `invoiceitems` row (the recorded
    mechanism: the added line IS a pending invoice item minted now, which
    is why it lands first in the newest-first pending bucket). `amount` is
    the line's UNIT amount; the line bills unit x quantity (binder-enforced
    required, so no None fallback exists here)."""
    from seahaven_stripe_world.resources import invoiceitems

    period = line.get("period") or {}
    now = ctx.clock.iso()
    item = invoiceitems.insert_invoice_item(
        ctx,
        customer_id=invoice_row["customer"],
        amount=line["amount"],
        currency=invoice_row["currency"],
        description=line.get("description"),
        discountable=line.get("discountable", True),
        invoice_id=invoice_row["id"],
        period_start=period.get("start") or now,
        period_end=period.get("end") or now,
        tax_rate_ids=line.get("tax_rates", ()),
        quantity=line.get("quantity", 1),
    )
    return item["id"]


def add_lines(ctx: seahaven.Ctx, req: Request) -> dict[str, Any]:
    row = _require_draft(ctx, req, param="invoice")
    old = serialize(ctx, row)
    for line in req.params["lines"]:
        _line_edit_checks(line)
        _mint_line_item(ctx, row, line)
    fresh = invoicing.rebuild_invoice_lines(ctx, row["id"])
    new = serialize(ctx, fresh)
    previous = _previous_attributes(old, new)
    if previous:
        invoicing.emit_invoice_event(ctx, "invoice.updated", fresh, previous=previous)
    return new


def _find_line(row: Mapping[str, Any], line_id: str) -> dict[str, Any]:
    for line in _load_list(row["lines"]):
        if isinstance(line, dict) and line.get("id") == line_id:
            return dict(line)
    raise invalid_request(f"No such invoice line item: '{line_id}'", param="line_item_id")


def _backing_item_id(row: Mapping[str, Any], line_id: str) -> str:
    """The `ii_` a custom line edits (recorded: every add_lines line is an
    invoice item; only invoice-item lines are editable — a subscription-item
    line regenerates from its item on every rebuild, so an in-place edit
    could not survive. Unprobed live; the declared ruling is the plain
    not-editable refusal)."""
    line = _find_line(row, line_id)
    parent = line.get("parent") or {}
    item_id = (parent.get("invoice_item_details") or {}).get("invoice_item")
    if item_id is None:
        raise invalid_request(
            "Invalid invoice: This invoice is no longer editable",
            code="invoice_not_editable",
        )
    return str(item_id)


def update_line(ctx: seahaven.Ctx, req: Request) -> dict[str, Any]:
    """`POST …/lines/{line_item_id}`: answers the updated LINE (recorded),
    not the invoice — but the invoice still rebuilds and emits
    `invoice.updated` beside it, consistent with its four sibling paths
    (add_lines, update_lines, remove_lines, and an invoiceitem create with
    `invoice=`). The endpoint's invoice-level emission is unprobed live;
    the consistency ruling is declared in `allowed_differences.py`."""
    from seahaven_stripe_world.resources import invoiceitems

    row = _require_draft(ctx, req, param="invoice")
    old = serialize(ctx, row)
    line_id = req.path_params["line_item_id"]
    item_id = _backing_item_id(row, line_id)
    invoiceitems.apply_line_edit(ctx, item_id, req.params)
    fresh = invoicing.rebuild_invoice_lines(ctx, row["id"])
    new = serialize(ctx, fresh)
    previous = _previous_attributes(old, new)
    if previous:
        invoicing.emit_invoice_event(ctx, "invoice.updated", fresh, previous=previous)
    return _find_line(fresh, line_id)


def update_lines(ctx: seahaven.Ctx, req: Request) -> dict[str, Any]:
    from seahaven_stripe_world.resources import invoiceitems

    row = _require_draft(ctx, req, param="invoice")
    old = serialize(ctx, row)
    for line in req.params["lines"]:
        item_id = _backing_item_id(row, line["id"])
        invoiceitems.apply_line_edit(ctx, item_id, line)
    fresh = invoicing.rebuild_invoice_lines(ctx, row["id"])
    new = serialize(ctx, fresh)
    previous = _previous_attributes(old, new)
    if previous:
        invoicing.emit_invoice_event(ctx, "invoice.updated", fresh, previous=previous)
    return new


def remove_lines(ctx: seahaven.Ctx, req: Request) -> dict[str, Any]:
    """/remove_lines with `behavior`: `delete` drops the backing item and
    the line; `unassign` returns the item to pending (the spec's own
    semantics for the two; only `delete` is recorded)."""
    row = _require_draft(ctx, req, param="invoice")
    old = serialize(ctx, row)
    for line in req.params["lines"]:
        target = _find_line(row, line["id"])
        parent = target.get("parent") or {}
        item_id = (parent.get("invoice_item_details") or {}).get("invoice_item")
        if item_id is None:
            raise invalid_request(
                "Invalid invoice: This invoice is no longer editable",
                code="invoice_not_editable",
            )
        if line["behavior"] == "delete":
            ctx.db.execute("DELETE FROM invoiceitems WHERE id = ?", item_id)
        else:
            ctx.db.execute("UPDATE invoiceitems SET invoice = NULL WHERE id = ?", item_id)
    fresh = invoicing.rebuild_invoice_lines(ctx, row["id"])
    new = serialize(ctx, fresh)
    previous = _previous_attributes(old, new)
    if previous:
        invoicing.emit_invoice_event(ctx, "invoice.updated", fresh, previous=previous)
    return new


SPEC = register(
    ResourceSpec(
        object="invoice",
        table="invoices",
        id_prefix="in_",
        collection_url="/v1/invoices",
        serializer=serialize,
        columns=tuple(FIELDS.columns),
        # The GET family's recorded 404 spelling (cassette 13 step 67); the
        # POST sub-actions spell their own inside their handlers.
        missing_path_param="invoice",
        error_name="invoice",
        list_filters=(
            ListFilter(
                name="collection_method",
                column="collection_method",
                kind="literal",
                choices=("charge_automatically", "send_invoice"),
            ),
            ListFilter(name="created", column="created", kind="range"),
            ListFilter(name="customer", column="customer", kind="exact", id_prefixes=CUS),
            ListFilter(name="due_date", column="due_date", kind="range"),
            ListFilter(name="status", column="status", kind="literal", choices=STATUS_FILTERS),
            ListFilter(
                name="subscription",
                column="parent_subscription",
                kind="exact",
                id_prefixes=("sub_",),
            ),
        ),
        creatable=None,
        updatable=None,
        delete=None,
        metadata=True,
        created_event=None,
        updated_event=None,
    )
)
