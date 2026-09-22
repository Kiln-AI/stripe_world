"""Credit notes (Phase 15): the three-channel settlement model — refund,
customer balance, out-of-band — and the ``credit_note_line_item`` freeze.

A credit note issues against a **paid** or **open** invoice (the ``open``
case is a pre-payment credit reducing what is owed; ``paid`` is a
post-payment credit that settles through refund, customer balance, or
out-of-band channels).

Settlement channels:
- ``credit_amount`` → writes a ``customer_balance_transactions`` row with
  ``type = 'credit_note'`` and adjusts ``customer.balance``.
- ``refund_amount`` → creates a refund against the invoice's charge.
- ``out_of_band_amount`` → records the amount credited outside Stripe.

Numbering (data_model §3.13): ``{invoice.number}-CN-{n}`` where ``n`` is
the count of existing credit notes for that invoice plus one.  Credit notes
are never deleted — ``void`` keeps the row — so the count is monotonic.

Lines are frozen JSON on the parent row, same as ``invoice.lines``
(data_model §7, functional spec §3.4).
"""

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

import seahaven

from seahaven_stripe_world import _ids, _json, _seq
from seahaven_stripe_world.dispatch.params import Param, ParamSpec
from seahaven_stripe_world.dispatch.resource import (
    ListFilter,
    ResourceSpec,
    page_embedded,
    register,
)
from seahaven_stripe_world.dispatch.response import Page
from seahaven_stripe_world.resources import _lookup, events
from seahaven_stripe_world.serialize.fields import FieldMap, presence_sets, serializer_for
from seahaven_stripe_world.stripe_errors import invalid_request

if TYPE_CHECKING:
    from seahaven_stripe_world.dispatch.response import Request

__all__ = [
    "CREDIT_NOTE_CREATE",
    "CREDIT_NOTE_LINES",
    "CREDIT_NOTE_LIST",
    "CREDIT_NOTE_PREVIEW",
    "CREDIT_NOTE_PREVIEW_LINES",
    "CREDIT_NOTE_RETRIEVE",
    "CREDIT_NOTE_UPDATE",
    "CREDIT_NOTE_VOID",
    "FIELDS",
    "SPEC",
    "create",
    "list_lines",
    "preview",
    "preview_lines",
    "serialize",
    "update",
    "void",
]

CN = ("cn_",)
IN = ("in_",)
CUS = ("cus_",)
IL = ("il_",)

# Reasons accepted on create (the spec's own enum):
REASONS = ("duplicate", "fraudulent", "order_change", "product_unsatisfactory")

# --- ParamSpecs -----------------------------------------------------------------

_LINE_ITEM_SHAPE = Param(
    name="",
    kind="object",
    shape=(
        Param(name="amount", kind="integer"),
        Param(name="description", kind="string", max_length=5_000),
        Param(name="invoice_line_item", kind="id", id_prefixes=IL),
        Param(name="quantity", kind="integer", minimum=1),
        Param(
            name="type",
            kind="literal",
            choices=("invoice_line_item", "custom_line_item"),
            required=True,
        ),
    ),
)

_COMMON_CREATE_BODY = (
    Param(name="amount", kind="integer"),
    Param(name="credit_amount", kind="integer"),
    Param(name="effective_at", kind="timestamp"),
    Param(name="invoice", kind="id", id_prefixes=IN, required=True),
    Param(name="lines", kind="array", item=_LINE_ITEM_SHAPE),
    Param(name="memo", kind="string", max_length=5_000),
    Param(name="out_of_band_amount", kind="integer"),
    Param(name="reason", kind="literal", choices=REASONS),
    Param(name="refund_amount", kind="integer"),
)

CREDIT_NOTE_CREATE = ParamSpec(
    op_id="PostCreditNotes",
    body=_COMMON_CREATE_BODY,
    metadata=True,
)

CREDIT_NOTE_UPDATE = ParamSpec(
    op_id="PostCreditNotesId",
    path=("id",),
    body=(Param(name="memo", kind="string", max_length=5_000),),
    metadata=True,
)

CREDIT_NOTE_VOID = ParamSpec(
    op_id="PostCreditNotesIdVoid",
    path=("id",),
)

CREDIT_NOTE_LIST = ParamSpec(
    op_id="GetCreditNotes",
    paginated=True,
    body=(
        Param(name="created", kind="range"),
        Param(name="customer", kind="id", id_prefixes=CUS),
        Param(name="invoice", kind="id", id_prefixes=IN),
    ),
)

CREDIT_NOTE_RETRIEVE = ParamSpec(
    op_id="GetCreditNotesId",
    path=("id",),
)

CREDIT_NOTE_LINES = ParamSpec(
    op_id="GetCreditNotesCreditNoteLines",
    path=("credit_note",),
    paginated=True,
)

# Preview shares the create body but is a GET (query params), not a POST.
CREDIT_NOTE_PREVIEW = ParamSpec(
    op_id="GetCreditNotesPreview",
    body=_COMMON_CREATE_BODY,
    metadata=True,
)

CREDIT_NOTE_PREVIEW_LINES = ParamSpec(
    op_id="GetCreditNotesPreviewLines",
    body=_COMMON_CREATE_BODY,
    metadata=True,
    paginated=True,
)

# --- the serializer -----------------------------------------------------------------


def _load_list(text: str | None) -> list[Any]:
    loaded: object = _json.loads(text)
    return loaded if isinstance(loaded, list) else []


def _lines_envelope(ctx: seahaven.Ctx, row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "object": "list",
        "data": _load_list(row["lines"]),
        "has_more": False,
        "url": f"/v1/credit_notes/{row['id']}/lines",
    }


def _refunds_list(ctx: seahaven.Ctx, row: Mapping[str, Any]) -> list[dict[str, Any]]:
    """``credit_note.refunds`` is a plain array of ``credit_note_refund``
    objects (not a list envelope).  Each entry names a refund id and the
    amount that applies to this credit note."""
    raw = _load_list(row["refunds"])
    result: list[dict[str, Any]] = []
    for entry in raw:
        if isinstance(entry, str):
            # Stored as a bare refund id; look up the refund for the amount
            refund_row = ctx.db.one("SELECT * FROM refunds WHERE id = ?", entry)
            result.append(
                {
                    "amount_refunded": refund_row["amount"] if refund_row else 0,
                    "payment_record_refund": None,
                    "refund": entry,
                    "type": "refund",
                }
            )
        elif isinstance(entry, dict):
            result.append(entry)
    return result


def _pdf(ctx: seahaven.Ctx, row: Mapping[str, Any]) -> str:
    """Derived constant URL (data_model §7)."""
    return f"https://pay.stripe.com/credit_notes/{row['id']}/pdf"


always_present, omit_when_none = presence_sets("credit_note")

FIELDS = FieldMap(
    object="credit_note",
    table="credit_notes",
    columns={
        "id": "id",
        "created": "created",
        "amount": "amount",
        "amount_shipping": "amount_shipping",
        "currency": "currency",
        "customer": "customer",
        "customer_balance_transaction": "customer_balance_transaction",
        "discount_amount": "discount_amount",
        "discount_amounts": "discount_amounts",
        "effective_at": "effective_at",
        "invoice": "invoice",
        "memo": "memo",
        "metadata": "metadata",
        "number": "number",
        "out_of_band_amount": "out_of_band_amount",
        "post_payment_amount": "post_payment_amount",
        "pre_payment_amount": "pre_payment_amount",
        "pretax_credit_amounts": "pretax_credit_amounts",
        "reason": "reason",
        "status": "status",
        "subtotal": "subtotal",
        "subtotal_excluding_tax": "subtotal_excluding_tax",
        "total": "total",
        "total_excluding_tax": "total_excluding_tax",
        "total_taxes": "total_taxes",
        "type": "type",
        "voided_at": "voided_at",
    },
    timestamps=frozenset({"created", "effective_at", "voided_at"}),
    json_columns=frozenset(
        {
            "discount_amounts",
            "metadata",
            "pretax_credit_amounts",
            "total_taxes",
        }
    ),
    derived={
        "lines": _lines_envelope,
        "refunds": _refunds_list,
        "pdf": _pdf,
    },
    constants={
        "livemode": False,
        "customer_account": None,
        "shipping_cost": None,
    },
    always_present=always_present,
    omit_when_none=omit_when_none,
)

_serialize = serializer_for(FIELDS)


def serialize(ctx: seahaven.Ctx, row: Mapping[str, Any]) -> dict[str, Any]:
    return _serialize(ctx, row)


SPEC = register(
    ResourceSpec(
        object="credit_note",
        table="credit_notes",
        id_prefix="cn_",
        collection_url="/v1/credit_notes",
        serializer=serialize,
        columns=tuple(FIELDS.columns),
        missing_path_param="id",
        list_filters=(
            ListFilter(name="created", column="created", kind="range"),
            ListFilter(name="customer", column="customer", kind="exact", id_prefixes=CUS),
            ListFilter(name="invoice", column="invoice", kind="exact", id_prefixes=IN),
        ),
    )
)

# --- helpers -----------------------------------------------------------------------


def _require_cn(ctx: seahaven.Ctx, req: Request, *, param: str) -> dict[str, Any]:
    id_ = req.path_params.get("id") or req.path_params.get("credit_note")
    if id_ is None:
        raise seahaven.WorldBug("no credit_note path param")
    return _lookup.require_row(ctx, "credit_notes", "credit note", id_, param=param)


def _credit_note_number(ctx: seahaven.Ctx, invoice_id: str) -> str:
    """``{invoice.number}-CN-{n}`` where n = count + 1 (data_model §3.13)."""
    invoice = _lookup.require_row(ctx, "invoices", "invoice", invoice_id, param="invoice")
    count_row = ctx.db.one("SELECT COUNT(*) AS cnt FROM credit_notes WHERE invoice = ?", invoice_id)
    n = (count_row["cnt"] if count_row else 0) + 1
    # The invoice number may be None if the invoice was never finalized; use
    # a fallback derived from the invoice id for that edge.
    inv_number = invoice["number"] or invoice["id"]
    return f"{inv_number}-CN-{n}"


def _build_line_items(
    ctx: seahaven.Ctx,
    *,
    invoice_row: Mapping[str, Any],
    params: Mapping[str, Any],
    total_amount: int,
) -> list[dict[str, Any]]:
    """Build credit note line items.

    If ``lines`` is provided, use them directly.  Otherwise, build a single
    ``custom_line_item`` covering the whole amount.
    """
    lines_param = params.get("lines")
    loaded_lines: object = (
        _json.loads(invoice_row["lines"]) if isinstance(invoice_row["lines"], str) else []
    )
    invoice_lines: list[dict[str, Any]] = loaded_lines if isinstance(loaded_lines, list) else []

    if lines_param:
        result: list[dict[str, Any]] = []
        for line_spec in lines_param:
            line_type = line_spec["type"]
            cnli_id = _ids.stripe_id(ctx, "cnli_")
            if line_type == "invoice_line_item" and "invoice_line_item" in line_spec:
                # Find the referenced invoice line
                inv_line = None
                for il in invoice_lines:
                    if isinstance(il, dict) and il.get("id") == line_spec["invoice_line_item"]:
                        inv_line = il
                        break
                amount = line_spec.get("amount", inv_line.get("amount", 0) if inv_line else 0)
                quantity = line_spec.get("quantity", inv_line.get("quantity", 1) if inv_line else 1)
                description = line_spec.get(
                    "description",
                    inv_line.get("description", "") if inv_line else "",
                )
                unit_amount = amount // quantity if quantity else amount
                result.append(
                    {
                        "id": cnli_id,
                        "object": "credit_note_line_item",
                        "amount": amount,
                        "description": description,
                        "discount_amount": 0,
                        "discount_amounts": [],
                        "invoice_line_item": line_spec["invoice_line_item"],
                        "livemode": False,
                        "pretax_credit_amounts": [],
                        "quantity": quantity,
                        "taxes": None,
                        "tax_rates": [],
                        "type": "invoice_line_item",
                        "unit_amount": unit_amount,
                        "unit_amount_decimal": str(unit_amount),
                    }
                )
            else:
                # custom_line_item
                amount = line_spec.get("amount", 0)
                quantity = line_spec.get("quantity", 1)
                description = line_spec.get("description", "")
                unit_amount = amount // quantity if quantity else amount
                result.append(
                    {
                        "id": cnli_id,
                        "object": "credit_note_line_item",
                        "amount": amount,
                        "description": description,
                        "discount_amount": 0,
                        "discount_amounts": [],
                        "livemode": False,
                        "pretax_credit_amounts": [],
                        "quantity": quantity,
                        "taxes": None,
                        "tax_rates": [],
                        "type": "custom_line_item",
                        "unit_amount": unit_amount,
                        "unit_amount_decimal": str(unit_amount),
                    }
                )
        return result

    # Default: one custom_line_item for the whole amount
    cnli_id = _ids.stripe_id(ctx, "cnli_")
    return [
        {
            "id": cnli_id,
            "object": "credit_note_line_item",
            "amount": total_amount,
            "description": None,
            "discount_amount": 0,
            "discount_amounts": [],
            "livemode": False,
            "pretax_credit_amounts": [],
            "quantity": 1,
            "taxes": None,
            "tax_rates": [],
            "type": "custom_line_item",
            "unit_amount": total_amount,
            "unit_amount_decimal": str(total_amount),
        }
    ]


def _compute_credit_note(
    ctx: seahaven.Ctx,
    *,
    params: Mapping[str, Any],
    invoice_row: Mapping[str, Any],
) -> dict[str, Any]:
    """Compute the credit note fields from the create parameters.

    Returns a dict of the fields that will be inserted.  Used by both
    ``create`` and ``preview``.
    """
    invoice_status = invoice_row["status"]
    if invoice_status not in ("open", "paid", "uncollectible"):
        raise invalid_request(
            f"Credit notes can only be created on open, paid, or uncollectible invoices."
            f" This invoice is {invoice_status}.",
        )

    # Determine total amount and settlement breakdown
    amount = params.get("amount")
    credit_amount = params.get("credit_amount", 0)
    refund_amount = params.get("refund_amount", 0)
    out_of_band = params.get("out_of_band_amount", 0)

    if amount is None:
        # If lines are provided, sum their amounts
        lines_param = params.get("lines")
        if lines_param:
            amount = sum(line.get("amount", 0) for line in lines_param)
        else:
            # Default to the sum of the settlement channels
            amount = credit_amount + refund_amount + out_of_band

    if amount <= 0:
        raise invalid_request(
            "You must credit the invoice for a positive amount.",
            param="amount",
        )

    # Guard: credit note cannot exceed the remaining creditable amount
    existing_pre = invoice_row["pre_payment_credit_notes_amount"]
    existing_post = invoice_row["post_payment_credit_notes_amount"]

    if invoice_status == "paid":
        # Post-payment: cannot exceed amount_paid minus already-credited
        max_creditable = invoice_row["amount_paid"] - existing_post
    else:
        # Pre-payment: cannot exceed amount_remaining minus already-credited
        max_creditable = invoice_row["amount_remaining"] - existing_pre
    if max_creditable < 0:
        max_creditable = 0

    if amount > max_creditable:
        raise invalid_request(
            "The credit note amount must be less than or equal to the remaining"
            f" creditable amount ({max_creditable}).",
            param="amount",
        )

    # If no settlement channel is specified, choose the default:
    # paid → refund; pre-payment → no channel (amount reduces invoice).
    if credit_amount == 0 and refund_amount == 0 and out_of_band == 0:
        if invoice_status == "paid":
            # Post-payment: default to refund
            refund_amount = amount
        else:
            # Pre-payment: the full amount reduces the invoice
            credit_amount = 0

    # Validate that the settlement channels sum to the total
    channel_total = credit_amount + refund_amount + out_of_band
    if channel_total > 0 and channel_total != amount:
        raise invalid_request(
            "The sum of credit_amount, refund_amount, and out_of_band_amount"
            " must equal the credit note amount.",
        )

    # Determine type — the status guard above restricts to paid | open |
    # uncollectible, so no else branch is reachable.
    if invoice_status == "paid":
        pre_payment_amount = 0
        post_payment_amount = amount
        cn_type = "post_payment"
    else:  # open or uncollectible
        pre_payment_amount = amount
        post_payment_amount = 0
        cn_type = "pre_payment"

    line_items = _build_line_items(
        ctx,
        invoice_row=invoice_row,
        params=params,
        total_amount=amount,
    )

    return {
        "amount": amount,
        "credit_amount": credit_amount,
        "refund_amount": refund_amount,
        "out_of_band_amount": out_of_band if out_of_band > 0 else None,
        "pre_payment_amount": pre_payment_amount,
        "post_payment_amount": post_payment_amount,
        "type": cn_type,
        "subtotal": amount,
        "total": amount,
        "line_items": line_items,
    }


# --- handlers ----------------------------------------------------------------------


def create(ctx: seahaven.Ctx, req: Request) -> dict[str, Any]:
    """``POST /v1/credit_notes``: create a credit note against an invoice.

    The three settlement channels write side effects:
    - ``credit_amount`` → ``customer_balance_transactions`` row
    - ``refund_amount`` → a refund on the invoice's charge
    - ``out_of_band_amount`` → recorded on the credit note
    """
    params = req.params
    invoice = _lookup.require_row(ctx, "invoices", "invoice", params["invoice"], param="invoice")
    computed = _compute_credit_note(ctx, params=params, invoice_row=invoice)

    now = ctx.clock.iso()
    cn_id = _ids.stripe_id(ctx, "cn_")
    number = _credit_note_number(ctx, invoice["id"])
    effective_at = params.get("effective_at", now)
    metadata_text = "{}"
    if req.metadata is not None:
        metadata_text = _json.dumps(dict(req.metadata.apply({})))

    lines_json = _json.dumps(computed["line_items"])
    refunds_json = "[]"
    cbt_id = None

    # Settlement channel: credit_amount → customer balance
    credit_amount = computed["credit_amount"]
    if credit_amount > 0:
        customer = _lookup.require_row(
            ctx, "customers", "customer", invoice["customer"], param="customer"
        )
        ending_balance = customer["balance"] - credit_amount
        cbt_id = _ids.stripe_id(ctx, "cbtxn_")
        # Insert with credit_note=NULL first; the credit note row does not
        # exist yet (mutual FK pair -- data_model §3.11).  The back-link
        # UPDATE below fills it in after the credit note is inserted.
        ctx.db.execute(
            "INSERT INTO customer_balance_transactions"
            " (id, x_seq, created, amount, currency, customer,"
            " ending_balance, type) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            cbt_id,
            _seq.next_seq(ctx, "customer_balance_transactions"),
            now,
            -credit_amount,  # negative = credit
            invoice["currency"],
            invoice["customer"],
            ending_balance,
            "credit_note",
        )
        ctx.db.execute(
            "UPDATE customers SET balance = ?, currency = COALESCE(currency, ?) WHERE id = ?",
            ending_balance,
            invoice["currency"],
            invoice["customer"],
        )

    # Settlement channel: refund_amount → create a refund
    refund_amount = computed["refund_amount"]
    if refund_amount > 0 and invoice["status"] == "paid":
        # Look up the charge that paid this invoice.  The internal
        # `_charge` column is set by `_mark_paid` in the billing engine.
        # Invoices paid out-of-band have no charge; refuse the refund
        # channel rather than silently skipping it.
        if invoice["_charge"] is None:
            raise invalid_request(
                "This invoice was not paid via a charge, so a refund cannot"
                " be issued. Use credit_amount or out_of_band_amount instead.",
                param="refund_amount",
            )
        from seahaven_stripe_world.resources import refunds as refund_mod

        charge_row = ctx.db.one("SELECT * FROM charges WHERE id = ?", invoice["_charge"])
        if charge_row is not None:
            refund_row = refund_mod.create_refund(
                ctx,
                params={"amount": refund_amount},
                metadata_text="{}",
                charge_id=charge_row["id"],
            )
            refunds_json = _json.dumps([refund_row["id"]])

    # Insert the credit note
    ctx.db.execute(
        "INSERT INTO credit_notes"
        " (id, x_seq, created, amount, amount_shipping, currency, customer,"
        " customer_balance_transaction, discount_amount, discount_amounts,"
        " effective_at, invoice, lines, memo, metadata, number,"
        " out_of_band_amount, post_payment_amount, pre_payment_amount,"
        " pretax_credit_amounts, reason, refunds, status, subtotal,"
        " subtotal_excluding_tax, total, total_excluding_tax, type)"
        " VALUES (?, ?, ?, ?, 0, ?, ?, ?, 0, '[]', ?, ?, ?, ?, ?, ?, ?, ?, ?, '[]', ?, ?, 'issued',"
        " ?, NULL, ?, NULL, ?)",
        cn_id,
        _seq.next_seq(ctx, "credit_notes"),
        now,
        computed["amount"],
        invoice["currency"],
        invoice["customer"],
        cbt_id,
        effective_at,
        invoice["id"],
        lines_json,
        params.get("memo"),
        metadata_text,
        number,
        computed["out_of_band_amount"],
        computed["post_payment_amount"],
        computed["pre_payment_amount"],
        params.get("reason"),
        refunds_json,
        computed["subtotal"],
        computed["total"],
        computed["type"],
    )

    # Update the invoice's credit note amounts
    if computed["pre_payment_amount"] > 0:
        ctx.db.execute(
            "UPDATE invoices SET pre_payment_credit_notes_amount ="
            " pre_payment_credit_notes_amount + ?,"
            " amount_remaining = amount_remaining - ? WHERE id = ?",
            computed["pre_payment_amount"],
            computed["pre_payment_amount"],
            invoice["id"],
        )
    if computed["post_payment_amount"] > 0:
        ctx.db.execute(
            "UPDATE invoices SET post_payment_credit_notes_amount ="
            " post_payment_credit_notes_amount + ? WHERE id = ?",
            computed["post_payment_amount"],
            invoice["id"],
        )

    # Back-link the cbt → credit_note (the circular FK two-step write)
    if cbt_id is not None:
        ctx.db.execute(
            "UPDATE customer_balance_transactions SET credit_note = ? WHERE id = ?",
            cn_id,
            cbt_id,
        )

    row = _lookup.require_row(ctx, "credit_notes", "credit note", cn_id, param="id")
    events.emit_event(ctx, type="credit_note.created", obj=serialize(ctx, row))
    return serialize(ctx, row)


def update(ctx: seahaven.Ctx, req: Request) -> dict[str, Any]:
    """``POST /v1/credit_notes/{id}``: update memo and/or metadata."""
    row = _require_cn(ctx, req, param="id")
    old = serialize(ctx, row)
    if "memo" in req.params:
        ctx.db.execute(
            "UPDATE credit_notes SET memo = ? WHERE id = ?",
            req.params["memo"],
            row["id"],
        )
    if req.metadata is not None:
        current = _json.loads(row["metadata"]) if isinstance(row["metadata"], str) else {}
        if not isinstance(current, dict):
            current = {}
        ctx.db.execute(
            "UPDATE credit_notes SET metadata = ? WHERE id = ?",
            _json.dumps(dict(req.metadata.apply(current))),
            row["id"],
        )
    fresh = _lookup.require_row(ctx, "credit_notes", "credit note", row["id"], param="id")
    body = serialize(ctx, fresh)
    # Emit update event with previous attributes
    previous: dict[str, Any] = {}
    for k in ("memo", "metadata"):
        if old.get(k) != body.get(k):
            previous[k] = old[k]
    if previous:
        events.emit_event(ctx, type="credit_note.updated", obj=body, previous=previous)
    return body


def void(ctx: seahaven.Ctx, req: Request) -> dict[str, Any]:
    """``POST /v1/credit_notes/{id}/void``: void an issued credit note."""
    row = _require_cn(ctx, req, param="id")
    if row["status"] == "void":
        raise invalid_request("This credit note has already been voided.")
    now = ctx.clock.iso()
    ctx.db.execute(
        "UPDATE credit_notes SET status = 'void', voided_at = ? WHERE id = ?",
        now,
        row["id"],
    )

    # Reverse the pre-payment reduction on the invoice
    if row["pre_payment_amount"] > 0:
        ctx.db.execute(
            "UPDATE invoices SET pre_payment_credit_notes_amount ="
            " pre_payment_credit_notes_amount - ?,"
            " amount_remaining = amount_remaining + ? WHERE id = ?",
            row["pre_payment_amount"],
            row["pre_payment_amount"],
            row["invoice"],
        )
    if row["post_payment_amount"] > 0:
        ctx.db.execute(
            "UPDATE invoices SET post_payment_credit_notes_amount ="
            " post_payment_credit_notes_amount - ? WHERE id = ?",
            row["post_payment_amount"],
            row["invoice"],
        )

    # Reverse the customer-balance settlement: write a reversing CBT of
    # the opposite sign and update customer.balance accordingly.
    if row["customer_balance_transaction"] is not None:
        original_cbt = ctx.db.one(
            "SELECT * FROM customer_balance_transactions WHERE id = ?",
            row["customer_balance_transaction"],
        )
        if original_cbt is not None:
            reversal_amount = -original_cbt["amount"]  # flip sign
            customer = _lookup.require_row(
                ctx, "customers", "customer", original_cbt["customer"], param="customer"
            )
            ending_balance = customer["balance"] + reversal_amount
            reversal_id = _ids.stripe_id(ctx, "cbtxn_")
            ctx.db.execute(
                "INSERT INTO customer_balance_transactions"
                " (id, x_seq, created, amount, credit_note, currency, customer,"
                " ending_balance, type) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                reversal_id,
                _seq.next_seq(ctx, "customer_balance_transactions"),
                now,
                reversal_amount,
                row["id"],
                original_cbt["currency"],
                original_cbt["customer"],
                ending_balance,
                "credit_note",
            )
            ctx.db.execute(
                "UPDATE customers SET balance = ? WHERE id = ?",
                ending_balance,
                original_cbt["customer"],
            )

    fresh = _lookup.require_row(ctx, "credit_notes", "credit note", row["id"], param="id")
    body = serialize(ctx, fresh)
    events.emit_event(ctx, type="credit_note.voided", obj=body)
    return body


def list_lines(ctx: seahaven.Ctx, req: Request) -> dict[str, Any]:
    """``GET /v1/credit_notes/{credit_note}/lines``: paginate the frozen
    line items embedded in the credit note's JSON."""
    row = _require_cn(ctx, req, param="credit_note")
    loaded: object = _json.loads(row["lines"]) if isinstance(row["lines"], str) else []
    items: list[dict[str, Any]] = loaded if isinstance(loaded, list) else []
    page = req.page or Page(limit=10, starting_after=None, ending_before=None)
    return page_embedded(
        items,
        page,
        url=f"/v1/credit_notes/{row['id']}/lines",
        object_name="credit_note_line_item",
    )


def preview(ctx: seahaven.Ctx, req: Request) -> dict[str, Any]:
    """``GET /v1/credit_notes/preview``: compute what a credit note would
    look like without persisting anything."""
    params = req.params
    invoice = _lookup.require_row(ctx, "invoices", "invoice", params["invoice"], param="invoice")
    computed = _compute_credit_note(ctx, params=params, invoice_row=invoice)
    now = ctx.clock.iso()
    effective_at = params.get("effective_at", now)
    # Build a preview row dict (not persisted)
    preview_row: dict[str, Any] = {
        "id": "cn_preview",
        "created": now,
        "amount": computed["amount"],
        "amount_shipping": 0,
        "currency": invoice["currency"],
        "customer": invoice["customer"],
        "customer_balance_transaction": None,
        "discount_amount": 0,
        "discount_amounts": "[]",
        "effective_at": effective_at,
        "invoice": invoice["id"],
        "lines": _json.dumps(computed["line_items"]),
        "memo": params.get("memo"),
        "metadata": _json.dumps(dict(req.metadata.apply({}))) if req.metadata is not None else "{}",
        "number": "",
        "out_of_band_amount": computed["out_of_band_amount"],
        "post_payment_amount": computed["post_payment_amount"],
        "pre_payment_amount": computed["pre_payment_amount"],
        "pretax_credit_amounts": "[]",
        "reason": params.get("reason"),
        "refunds": "[]",
        "status": "issued",
        "subtotal": computed["subtotal"],
        "subtotal_excluding_tax": None,
        "total": computed["total"],
        "total_excluding_tax": None,
        "total_taxes": None,
        "type": computed["type"],
        "voided_at": None,
    }
    return serialize(ctx, preview_row)


def preview_lines(ctx: seahaven.Ctx, req: Request) -> dict[str, Any]:
    """``GET /v1/credit_notes/preview/lines``: paginate the preview's lines
    without persisting anything."""
    params = req.params
    invoice = _lookup.require_row(ctx, "invoices", "invoice", params["invoice"], param="invoice")
    computed = _compute_credit_note(ctx, params=params, invoice_row=invoice)
    items = computed["line_items"]
    page = req.page or Page(limit=10, starting_after=None, ending_before=None)
    return page_embedded(
        items,
        page,
        url="/v1/credit_notes/preview/lines",
        object_name="credit_note_line_item",
    )
