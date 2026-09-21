"""The invoice path: line construction from subscription items and pending
invoice items, totals arithmetic, and the draft → open → paid machine's
internal steps (`components/billing_engine.md` §2 and §3).

Since Phase 13 the routed `/v1/invoices` surface lives here too: the sweep
of pending `invoiceitems` onto a draft (all of the customer's pending items,
newest-first — the invoice's own zero-width period bounds nothing, recorded),
the draft-edit rebuild, delete-draft (which does NOT release the swept items
back to pending — recorded), and the `$0-settles` finalize helper the
finalize/pay/send routes share. The invoice serializer shipped with Phase
13's routes, so this module's `invoice.*` events emit verbatim serialized
bodies (`components/cross_cutting.md` §3.4.3).

Every amount is an `int` of minor units or a `fractions.Fraction` on the way
to one; no float exists in this module's money path.
"""

import calendar
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction
from typing import Any

import seahaven

from stripeapi import _ids, _json, _seq, _time
from stripeapi.billing._money import apportion, round_half_up
from stripeapi.resources import _lookup, charges, events, payment_intents
from stripeapi.stripe_errors import declined as declined_body

__all__ = [
    "INVOICE_PAYMENT_SETTINGS",
    "DiscountSpec",
    "ItemLine",
    "TaxRateSpec",
    "add_interval",
    "build_item_lines",
    "compute_automatically_finalizes_at",
    "compute_totals",
    "create_invoice",
    "delete_draft_invoice",
    "describe_amount",
    "finalize_and_settle",
    "finalize_invoice",
    "invoice_item_line",
    "iso_plus_hours",
    "mark_uncollectible_invoice",
    "next_payment_attempt_at",
    "pay_invoice",
    "pending_invoice_item_rows",
    "rebuild_invoice_lines",
    "settle_customer_balance",
    "sweep_pending_items",
    "void_invoice",
]

#: The recorded `invoice.payment_settings` constant (Phase 12 probe: the
#: paid `subscription_create` invoice carries exactly this).
INVOICE_PAYMENT_SETTINGS: dict[str, object] = {
    "default_mandate": None,
    "payment_method_options": None,
    "payment_method_types": None,
}

#: The recorded `taxability_reason` on every line/total tax entry (the
#: billing engine design quoted `standard_rated` from the schema's
#: description; the recording says `not_available`, and the recording wins).
_TAXABILITY_REASON = "not_available"

_ZERO_DECIMAL_CURRENCIES = frozenset(
    (
        "bif",
        "clp",
        "djf",
        "gnf",
        "jpy",
        "kmf",
        "krw",
        "mga",
        "pyg",
        "rwf",
        "ugx",
        "vnd",
        "vuv",
        "xaf",
        "xof",
        "xpf",
    )
)

_SYMBOLS = {
    "aud": "$",
    "cad": "$",
    "gbp": "£",
    "hkd": "$",
    "jpy": "¥",
    "mxn": "$",
    "nzd": "$",
    "sgd": "$",
    "usd": "$",
    "eur": "€",
}

_PLURAL = {"day": "days", "week": "weeks", "month": "months", "year": "years"}


def _load_dict(text: str | None) -> dict[str, Any]:
    loaded: object = _json.loads(text)
    return loaded if isinstance(loaded, dict) else {}


def _load_list(text: str | None) -> list[Any]:
    loaded: object = _json.loads(text)
    return loaded if isinstance(loaded, list) else []


def add_interval(iso: str, *, interval: str, interval_count: int = 1) -> str:
    """One billing period forward: calendar-true for months and years (the
    day is clamped to the target month's length — Stripe billing cycles are
    calendar-anchored), second-exact for days and weeks."""
    moment = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%S.%fZ")
    if interval in ("month", "year"):
        months = interval_count * (12 if interval == "year" else 1)
        total = moment.month - 1 + months
        year = moment.year + total // 12
        month = total % 12 + 1
        day = min(moment.day, calendar.monthrange(year, month)[1])
        return (
            moment.replace(year=year, month=month, day=day).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
            + "Z"
        )
    seconds = interval_count * {"day": 86_400, "week": 604_800}[interval]
    return _time.from_unix(_time.to_unix(iso) + seconds)


def iso_plus_hours(iso: str, *, hours: int) -> str:
    return _time.from_unix(_time.to_unix(iso) + hours * 3_600)


def describe_amount(amount: int, currency: str) -> str:
    """`2000, "cad"` -> `$20.00` — the recorded line-description spelling
    (the account's own symbol for its currency; zero-decimal currencies
    print without the fraction)."""
    symbol = _SYMBOLS.get(currency, currency.upper() + " ")
    if currency in _ZERO_DECIMAL_CURRENCIES:
        return f"{symbol}{amount}"
    return f"{symbol}{amount // 100}.{amount % 100:02d}"


def describe_interval(interval: str, interval_count: int = 1) -> str:
    """`month, 1` -> `month`; `month, 3` -> `3 months` (the count>1 spelling
    is unrecorded; the plural is the natural English form)."""
    if interval_count == 1:
        return interval
    return f"{interval_count} {_PLURAL[interval]}"


# --- the pure arithmetic -----------------------------------------------------------


@dataclass(frozen=True)
class DiscountSpec:
    """A resolved coupon: `percent_off` is the exact TEXT-decimal Fraction."""

    discount_id: str
    coupon_id: str
    amount_off: int | None
    percent_off: Fraction | None


@dataclass(frozen=True)
class TaxRateSpec:
    tax_rate_id: str
    percentage: Fraction
    inclusive: bool


@dataclass(frozen=True)
class ItemLine:
    """One line's source configuration, pre-arithmetic.

    A subscription-item line (`invoice_item_id is None`) carries
    `parent.subscription_item_details`; an invoice-item line carries
    `parent.invoice_item_details` naming the backing `ii_` row, with
    `price_id`/`product_id` pointing at the one-off price the item's
    `pricing` block names (recorded, cassette 13).

    `proration` marks a proration-shaped line (a conversion credit, later
    the Phase 14 proration lines): never discountable, `proration: true`
    under the line's `parent.subscription_item_details`, and no
    `unit_amount_decimal` (`spec3.json`'s own rule; probed on the
    conversion credit, round 4's trail)."""

    subscription_item_id: str | None
    subscription_id: str | None
    price_id: str | None
    product_id: str | None
    unit_amount: int
    quantity: int
    period_start: str
    period_end: str
    description: str
    proration: bool = False
    invoice_item_id: str | None = None
    discountable: bool = True


@dataclass(frozen=True)
class Totals:
    lines: list[dict[str, Any]]
    subtotal: int
    invoice_discount_total: int
    total_discount_amounts: list[dict[str, Any]]
    total_pretax_credit_amounts: list[dict[str, Any]]
    exclusive_tax_total: int
    inclusive_tax_total: int
    total_excluding_tax: int
    subtotal_excluding_tax: int
    total: int
    total_taxes: list[dict[str, Any]]


def build_item_lines(
    ctx: seahaven.Ctx,
    items: Sequence[Mapping[str, Any]],
    *,
    currency: str,
    trial: bool = False,
) -> list[ItemLine]:
    """One `ItemLine` per subscription-item row, in creation order (the
    recorded `items` order, Phase 12). `quantity` defaults to 1; a price
    with no integer unit amount (metered, tiered) bills 0 here — no usage
    exists to bill, which is what the real API invoices at creation too.

    `trial=True` builds the recorded trial-create line: amount 0,
    `Free trial for 1 x <product>` over `[start, trial_end)` (probed,
    Phase 12 — the trialing subscription's first invoice is a paid $0
    `subscription_create` invoice, not no invoice at all).
    """
    lines: list[ItemLine] = []
    for item in items:
        price = _lookup.require_row(ctx, "prices", "price", item["price"], param="price")
        product = _lookup.require_row(ctx, "products", "product", price["product"], param="product")
        recurring = _load_dict(price["recurring"])
        unit_amount = price["unit_amount"] or 0
        quantity = item["quantity"] if item["quantity"] is not None else 1
        if trial:
            description = f"Free trial for {quantity} \u00d7 {product['name']}"
            unit_amount = 0
        else:
            description = (
                f"{quantity} \u00d7 {product['name']} "
                f"(at {describe_amount(unit_amount, currency)} / "
                f"{
                    describe_interval(
                        recurring.get('interval', 'month'), recurring.get('interval_count', 1)
                    )
                })"
            )
        lines.append(
            ItemLine(
                subscription_item_id=item["id"],
                subscription_id=item["subscription"],
                price_id=price["id"],
                product_id=price["product"],
                unit_amount=unit_amount,
                quantity=quantity,
                period_start=item["current_period_start"],
                period_end=item["current_period_end"],
                description=description,
            )
        )
    return lines


def _tax_entry(tax: TaxRateSpec, amount: int) -> int:
    if tax.inclusive:
        exact = Fraction(amount * tax.percentage, 100 + tax.percentage)
    else:
        exact = Fraction(amount * tax.percentage, 100)
    return round_half_up(exact)


def compute_totals(
    line_inputs: Sequence[ItemLine],
    *,
    currency: str,
    invoice_discounts: Sequence[DiscountSpec] = (),
    tax_rates: Sequence[TaxRateSpec] = (),
    invoice_id: str = "",
) -> Totals:
    """The recorded arithmetic, pure: per-line amounts, invoice-scope
    discounts apportioned floor-then-remainder (`_money`'s rule, documented
    for coupons), then exclusive/inclusive tax on each line's
    post-discount net (recorded: the 5% GST taxes 2700, not 3000).

    `invoice_id` is threaded only so draft lines can name their invoice;
    pass "" while the invoice's id is still unminted.
    """
    amounts = [line.unit_amount * line.quantity for line in line_inputs]
    subtotal = sum(amounts)

    # Invoice-scope discounts, sequential, apportioned back per line. A
    # proration line is never a discount base (`spec3.json`: "Always false
    # for prorations" — discountable both as a flag and as an apportion
    # weight), but it still carries an explicit 0-amount entry (recorded on
    # the conversion credit's invoice, Phase 12's trail: a non-discountable
    # line shows the discount with amount 0 rather than omitting it).
    discountable = [
        amount if not line.proration else 0
        for amount, line in zip(amounts, line_inputs, strict=True)
    ]
    per_line_discounts: list[dict[str, int]] = [{} for _ in line_inputs]
    total_discount_amounts: list[dict[str, Any]] = []
    for discount in invoice_discounts:
        base = sum(discountable)
        if discount.amount_off is not None:
            amount = min(discount.amount_off, base)
        else:
            assert discount.percent_off is not None
            amount = round_half_up(Fraction(base * discount.percent_off, 100))
        if base > 0 and amount > 0:
            shares = apportion(amount, discountable)
        else:
            shares = [0] * len(discountable)
        for index, share in enumerate(shares):
            # Every line names every invoice-scope discount, 0 included —
            # the recorded shape (the `discounts` id array stays empty; the
            # apportioned amount is what `discount_amounts` carries).
            per_line_discounts[index][discount.discount_id] = share
            discountable[index] -= share
        if amount:
            total_discount_amounts.append({"amount": amount, "discount": discount.discount_id})

    # Tax on each line's post-discount net — where a proration line's net
    # is its own amount: any coupon it carries was pre-netted into that
    # amount (the conversion credit's probed shape), never apportioned, so
    # the zeroed discount weight must not zero the tax base.
    tax_base = [
        amount - sum(per_line_discounts[index].values()) for index, amount in enumerate(amounts)
    ]
    per_line_taxes: list[list[dict[str, Any]]] = []
    taxes_by_rate: dict[str, dict[str, Any]] = {}
    exclusive_total = 0
    inclusive_total = 0
    for _index, net in enumerate(tax_base):
        entries: list[dict[str, Any]] = []
        for tax in tax_rates:
            tax_amount = _tax_entry(tax, net)
            entry = {
                "amount": tax_amount,
                "tax_behavior": "inclusive" if tax.inclusive else "exclusive",
                "tax_rate_details": {"tax_rate": tax.tax_rate_id},
                "taxability_reason": _TAXABILITY_REASON,
                "taxable_amount": net,
                "type": "tax_rate_details",
            }
            entries.append(entry)
            aggregated: dict[str, Any] = taxes_by_rate.setdefault(
                tax.tax_rate_id,
                {
                    "amount": 0,
                    "tax_behavior": entry["tax_behavior"],
                    "tax_rate_details": entry["tax_rate_details"],
                    "taxability_reason": _TAXABILITY_REASON,
                    "taxable_amount": 0,
                    "type": "tax_rate_details",
                },
            )
            aggregated["amount"] += tax_amount
            aggregated["taxable_amount"] += net
            if tax.inclusive:
                inclusive_total += tax_amount
            else:
                exclusive_total += tax_amount
        per_line_taxes.append(entries)

    lines: list[dict[str, Any]] = []
    for index, line in enumerate(line_inputs):
        discount_amounts = [
            {"amount": amount, "discount": discount_id}
            for discount_id, amount in per_line_discounts[index].items()
        ]
        # The recorded pretax-credit echo: every invoice-scope discount a
        # line carries reappears here with `type: "discount"` (observed on
        # the Phase 12 conversion-credit invoices and the sandbox's own
        # discounted bodies; cassette 13 carries no coupon to replay it).
        pretax_credit_amounts = [
            {"amount": amount, "discount": discount_id, "type": "discount"}
            for discount_id, amount in per_line_discounts[index].items()
        ]
        if line.invoice_item_id is not None:
            parent = {
                "invoice_item_details": {
                    "invoice_item": line.invoice_item_id,
                    "proration": line.proration,
                    "proration_details": {"credited_items": None},
                    "subscription": line.subscription_id,
                },
                "subscription_item_details": None,
                "type": "invoice_item_details",
            }
        else:
            parent = {
                "invoice_item_details": None,
                "subscription_item_details": {
                    "invoice_item": None,
                    "proration": line.proration,
                    "proration_details": {"credited_items": None},
                    "subscription": line.subscription_id,
                    "subscription_item": line.subscription_item_id,
                },
                "type": "subscription_item_details",
            }
        lines.append(
            {
                "id": _line_placeholder_id(index),
                "object": "line_item",
                "livemode": False,
                "amount": amounts[index],
                "currency": currency,
                "description": line.description,
                "discount_amounts": discount_amounts,
                "discountable": line.discountable and not line.proration,
                "discounts": [],
                "invoice": invoice_id,
                "metadata": {},
                "parent": parent,
                "period": {
                    "start": _time.to_unix(line.period_start),
                    "end": _time.to_unix(line.period_end),
                },
                "pretax_credit_amounts": pretax_credit_amounts,
                "pricing": {
                    "type": "price_details",
                    "price_details": {
                        "price": line.price_id,
                        "product": line.product_id,
                    },
                    "unit_amount_decimal": None if line.proration else str(line.unit_amount),
                },
                "quantity": line.quantity,
                "quantity_decimal": str(line.quantity),
                "subtotal": amounts[index],
                "taxes": per_line_taxes[index],
            }
        )

    invoice_discount_total = sum(entry["amount"] for entry in total_discount_amounts)
    return Totals(
        lines=lines,
        subtotal=subtotal,
        invoice_discount_total=invoice_discount_total,
        total_discount_amounts=total_discount_amounts,
        # The same aggregation with the echo's `type` key (recorded: the
        # totals mirror the per-line pretax credits discount-for-discount).
        total_pretax_credit_amounts=[
            {**entry, "type": "discount"} for entry in total_discount_amounts
        ],
        exclusive_tax_total=exclusive_total,
        inclusive_tax_total=inclusive_total,
        total_excluding_tax=subtotal - invoice_discount_total - inclusive_total,
        subtotal_excluding_tax=subtotal - inclusive_total,
        total=subtotal - invoice_discount_total + exclusive_total,
        total_taxes=list(taxes_by_rate.values()),
    )


def _line_placeholder_id(index: int) -> str:
    """Draft lines are built before the invoice's id exists; the `il_` ids
    are minted when the row is written (`create_invoice` rewrites them)."""
    return f"__line_{index}"


def settle_customer_balance(total: int, starting_balance: int) -> tuple[int, int, int]:
    """§3.5 verbatim: -> (amount_due, ending_balance, customer_balance_delta).

    `customer.balance` negative means credit available, positive means owed.
    """
    if total >= 0:
        credit_available = max(0, -starting_balance)
        credit_used = min(credit_available, total)
        debit_pending = max(0, starting_balance)
        amount_due = total + debit_pending - credit_used
        delta = credit_used - debit_pending
    else:
        amount_due = 0
        delta = total
    return amount_due, starting_balance + delta, delta


def compute_automatically_finalizes_at(
    created_iso: str, *, auto_advance: bool, collection_method: str
) -> str | None:
    """Populated and honest, never self-firing (the frozen clock): the real
    ~1-hour draft window for an auto-advancing draft, NULL otherwise.

    The offset is **3601 seconds, not 3600** — recorded twice (cassette 13,
    steps 20 and 55): Stripe computes the window from the un-floored
    creation instant and ceils, where `next_payment_attempt_at` below floors
    the same instant, which is why the two fields sit one second apart on
    the wire.
    """
    if not auto_advance:
        return None
    return _time.from_unix(_time.to_unix(created_iso) + 3_601)


def next_payment_attempt_at(
    created_iso: str, *, auto_advance: bool, collection_method: str
) -> str | None:
    """The scheduled first collection attempt on an auto-advancing
    `charge_automatically` draft: `created + 3600s` (recorded, cassette 13
    step 20). `send_invoice` drafts carry none — email collection has no
    payment attempt — and neither does any non-advancing draft."""
    if not auto_advance or collection_method != "charge_automatically":
        return None
    return _time.from_unix(_time.to_unix(created_iso) + 3_600)


# --- the stateful path ----------------------------------------------------------------


def create_invoice(
    ctx: seahaven.Ctx,
    *,
    customer_id: str,
    currency: str,
    collection_method: str,
    billing_reason: str,
    totals: Totals,
    subscription_id: str | None,
    auto_advance: bool,
    days_until_due: int | None,
    period_start: str,
    period_end: str,
    discounts: Sequence[Mapping[str, Any]] = (),
    tax_rate_ids: Sequence[str] = (),
    sweep: bool = False,
    extra_columns: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Write one draft invoice row with its lines and totals already
    computed, and emit `invoice.created`. Finalization (`finalize_invoice`)
    is the caller's next step or never, per the flow.

    `effective_at` is NULL here and lands at finalization (recorded,
    cassette 13: every draft — manual and subscription alike — carries
    null; the finalize moment stamps it).

    `sweep=True` attaches the customer's same-currency pending items and
    rebuilds the lines BEFORE the event fires, so the `invoice.created`
    snapshot is the object as the call left it — events are queryable
    state (§6.6, "the object as of the change") and an eval reading a
    pre-sweep snapshot would grade wrong. `extra_columns` folds the
    manual-create field set into the INSERT for the same reason."""
    now = ctx.clock.iso()
    customer = _lookup.require_row(ctx, "customers", "customer", customer_id, param="customer")
    id_ = _ids.stripe_id(ctx, "in_")
    lines = [{**line, "id": _ids.stripe_id(ctx, "il_"), "invoice": id_} for line in totals.lines]
    due_date = (
        _time.from_unix(_time.to_unix(now) + days_until_due * 86_400)
        if days_until_due is not None
        else None
    )
    cols: dict[str, Any] = {
        "id": id_,
        "x_seq": _seq.next_seq(ctx, "invoices"),
        "created": now,
        # A draft's amount_due mirrors its total (recorded, cassette 13
        # step 13: 3500 on the draft); finalization overwrites it with the
        # post-settlement figure.
        "amount_due": max(totals.total, 0),
        "amount_paid": 0,
        "amount_remaining": max(totals.total, 0),
        "attempt_count": 0,
        "attempted": 0,
        "auto_advance": int(auto_advance),
        "automatically_finalizes_at": compute_automatically_finalizes_at(
            now, auto_advance=auto_advance, collection_method=collection_method
        ),
        "billing_reason": billing_reason,
        "collection_method": collection_method,
        "currency": currency,
        "customer": customer_id,
        "customer_address": customer["address"],
        "customer_email": customer["email"],
        "customer_name": customer["name"],
        "customer_phone": customer["phone"],
        "customer_shipping": customer["shipping"],
        "customer_tax_exempt": customer["tax_exempt"],
        "default_tax_rates": _json.dumps(list(tax_rate_ids)),
        "discounts": _json.dumps(list(discounts)),
        "due_date": due_date,
        "effective_at": None,
        "lines": _json.dumps(lines),
        "next_payment_attempt": next_payment_attempt_at(
            now, auto_advance=auto_advance, collection_method=collection_method
        ),
        "parent_type": "subscription_details" if subscription_id else None,
        "parent_subscription": subscription_id,
        "payment_settings": _json.dumps(INVOICE_PAYMENT_SETTINGS),
        "period_start": period_start,
        "period_end": period_end,
        "starting_balance": 0,
        "status": "draft",
        "status_transitions": _json.dumps(
            {
                "finalized_at": None,
                "marked_uncollectible_at": None,
                "paid_at": None,
                "voided_at": None,
            }
        ),
        "subtotal": totals.subtotal,
        "subtotal_excluding_tax": totals.subtotal_excluding_tax,
        "total": totals.total,
        "total_discount_amounts": _json.dumps(totals.total_discount_amounts),
        "total_excluding_tax": totals.total_excluding_tax,
        "total_pretax_credit_amounts": _json.dumps(totals.total_pretax_credit_amounts),
        "total_taxes": _json.dumps(totals.total_taxes),
    }
    cols.update({column: value for column, value in (extra_columns or {}).items()})
    columns = ", ".join(cols)
    placeholders = ", ".join("?" for _ in cols)
    ctx.db.execute(f"INSERT INTO invoices ({columns}) VALUES ({placeholders})", *cols.values())
    if sweep:
        sweep_pending_items(ctx, id_, customer_id, currency)
        row = rebuild_invoice_lines(ctx, id_)
    else:
        row = _lookup.require_row(ctx, "invoices", "invoice", id_, param="invoice")
    emit_invoice_event(ctx, "invoice.created", row)
    return row


# --- the pending-item path (Phase 13) -------------------------------------------------


def invoice_item_line(ctx: seahaven.Ctx, item: Mapping[str, Any]) -> ItemLine:
    """One attached invoiceitem row as a line input: `pricing` names the
    one-off price behind the item (the recorded shape — an `amount+currency`
    create mints a price and product live, and so does this world)."""
    pricing = _load_dict(item["pricing"])
    price_details = pricing.get("price_details")
    if not isinstance(price_details, dict):
        raise seahaven.WorldBug(f"invoiceitem {item['id']} carries no price_details")
    return ItemLine(
        subscription_item_id=None,
        subscription_id=None,
        price_id=price_details.get("price"),
        product_id=price_details.get("product"),
        unit_amount=item["amount"],
        quantity=item["quantity"],
        period_start=item["period_start"],
        period_end=item["period_end"],
        description=item["description"] or "",
        invoice_item_id=item["id"],
        discountable=bool(item["discountable"]),
    )


def pending_invoice_item_rows(ctx: seahaven.Ctx, customer_id: str) -> list[dict[str, Any]]:
    """The customer's unswept items, newest-first (the recorded `lines`
    order for the pending bucket)."""
    return ctx.db.rows(
        "SELECT * FROM invoiceitems WHERE customer = ? AND invoice IS NULL ORDER BY x_seq DESC",
        customer_id,
    )


def sweep_pending_items(
    ctx: seahaven.Ctx, invoice_id: str, customer_id: str, currency: str
) -> None:
    """Attach the customer's pending items that match the invoice's
    currency (the `include` behavior, recorded: the sweep is bounded by the
    customer, never by the invoice's own zero-width period). One currency
    per invoice is Stripe's own invariant — an invoice sums one currency —
    so mismatched items stay pending for a later invoice of theirs
    (unprobed live; the declared ruling)."""
    ctx.db.execute(
        "UPDATE invoiceitems SET invoice = ? WHERE customer = ? AND invoice IS NULL"
        " AND currency = ?",
        invoice_id,
        customer_id,
        currency,
    )


def _line_inputs_for(ctx: seahaven.Ctx, row: Mapping[str, Any]) -> list[ItemLine]:
    """A draft's line inputs: its attached invoice items newest-first, then
    the subscription's items newest-first (the recorded collapse of the
    spec's three buckets into two — a later-added item is simply the newest
    pending item, cassette 13 step 32)."""
    inputs = [
        invoice_item_line(ctx, item)
        for item in ctx.db.rows(
            "SELECT * FROM invoiceitems WHERE invoice = ? ORDER BY x_seq DESC", row["id"]
        )
    ]
    if row["parent_subscription"] is not None:
        items = ctx.db.rows(
            "SELECT * FROM subscription_items WHERE subscription = ? ORDER BY x_seq DESC",
            row["parent_subscription"],
        )
        inputs.extend(build_item_lines(ctx, items, currency=row["currency"]))
    return inputs


def _invoice_discount_specs(ctx: seahaven.Ctx, row: Mapping[str, Any]) -> list[DiscountSpec]:
    """The invoice's stored inline discount objects resolved into specs
    (the same re-resolution `_persisted_discount_specs` applies to a
    subscription's, coupon rows the authority)."""
    from stripeapi.billing import subscription_lifecycle

    specs: list[DiscountSpec] = []
    for discount in _load_list(row["discounts"]):
        source = discount.get("source") if isinstance(discount, dict) else None
        if isinstance(source, dict) and source.get("coupon") is not None:
            specs.extend(
                subscription_lifecycle._persisted_discount_specs(
                    ctx, {"discounts": _json.dumps([dict(discount)])}
                )
            )
    return specs


def _invoice_tax_specs(ctx: seahaven.Ctx, row: Mapping[str, Any]) -> list[TaxRateSpec]:
    from stripeapi.billing import subscription_lifecycle

    return subscription_lifecycle._tax_specs(ctx, _load_list(row["default_tax_rates"]))


def rebuild_invoice_lines(ctx: seahaven.Ctx, invoice_id: str) -> dict[str, Any]:
    """Recompute a draft's lines and totals from scratch — nothing is
    incremental (billing_engine §2) — while PRESERVING each line's `il_` id
    across the rebuild: a line is keyed by its source (the backing
    invoiceitem, or the subscription item plus its period), and a draft
    edit must answer the same ids it was given (recorded, cassette 13 step
    33 — the updated line keeps its id). A draft's `amount_due` mirrors the
    recomputed total; finalization applies the customer balance over it."""
    row = _lookup.require_row(ctx, "invoices", "invoice", invoice_id, param="invoice")
    if row["status"] != "draft":
        raise seahaven.WorldBug(f"rebuild_invoice_lines on a {row['status']!r} invoice")
    totals = compute_totals(
        _line_inputs_for(ctx, row),
        currency=row["currency"],
        invoice_discounts=_invoice_discount_specs(ctx, row),
        tax_rates=_invoice_tax_specs(ctx, row),
        invoice_id=invoice_id,
    )
    previous_ids: dict[tuple[str, ...], str] = {}
    for line in _load_list(row["lines"]):
        if not isinstance(line, dict):
            continue
        parent = line.get("parent") or {}
        item_details = parent.get("invoice_item_details")
        sub_details = parent.get("subscription_item_details")
        if isinstance(item_details, dict) and item_details.get("invoice_item"):
            previous_ids[("ii", str(item_details["invoice_item"]))] = str(line["id"])
        elif isinstance(sub_details, dict) and sub_details.get("subscription_item"):
            previous_ids[
                ("si", str(sub_details["subscription_item"]), str(line["period"]["start"]))
            ] = str(line["id"])
    lines = []
    for line in totals.lines:
        parent = line["parent"]
        item_details = parent["invoice_item_details"]
        if item_details is not None:
            key = ("ii", str(item_details["invoice_item"]))
        else:
            key = (
                "si",
                str(parent["subscription_item_details"]["subscription_item"]),
                str(line["period"]["start"]),
            )
        line = {
            **line,
            "id": previous_ids.get(key) or _ids.stripe_id(ctx, "il_"),
        }
        lines.append(line)
    # A draft's amount fields mirror the new total (recorded: the swept
    # draft carries 3500). Bound from Python: an UPDATE's expressions read
    # the pre-update row, so `max(total, 0)` in SQL would see the OLD total.
    ctx.db.execute(
        "UPDATE invoices SET lines = ?, subtotal = ?, subtotal_excluding_tax = ?,"
        " total = ?, total_discount_amounts = ?, total_excluding_tax = ?,"
        " total_pretax_credit_amounts = ?, total_taxes = ?,"
        " amount_due = ?, amount_remaining = ? WHERE id = ?",
        _json.dumps(lines),
        totals.subtotal,
        totals.subtotal_excluding_tax,
        totals.total,
        _json.dumps(totals.total_discount_amounts),
        totals.total_excluding_tax,
        _json.dumps(totals.total_pretax_credit_amounts),
        _json.dumps(totals.total_taxes),
        max(totals.total, 0),
        max(totals.total, 0),
        invoice_id,
    )
    return _lookup.require_row(ctx, "invoices", "invoice", invoice_id, param="invoice")


def delete_draft_invoice(ctx: seahaven.Ctx, invoice_id: str) -> dict[str, Any]:
    """Remove a draft row. The swept items are NOT released back to pending
    — recorded (cassette 13 steps 23-24, 62-63): they stay attached to the
    dead invoice, refuse later deletes, and read as deleted on update."""
    row = _lookup.require_row(ctx, "invoices", "invoice", invoice_id, param="invoice")
    if row["status"] != "draft":
        raise seahaven.WorldBug(f"delete_draft_invoice on a {row['status']!r} invoice")
    emit_invoice_event(ctx, "invoice.deleted", row)
    ctx.db.execute("DELETE FROM invoices WHERE id = ?", invoice_id)
    return row


def emit_invoice_event(
    ctx: seahaven.Ctx,
    type: str,
    row: Mapping[str, Any],
    *,
    previous: Mapping[str, Any] | None = None,
) -> str:
    """One `invoice.*` event with the verbatim serialized body. The
    serializer lives in `resources/invoices.py` (this module writes rows,
    that one shapes them), so the import is function-level — the same
    cycle-break `pay_invoice`'s recovery hook uses."""
    from stripeapi.resources import invoices

    return events.emit_event(ctx, type=type, obj=invoices.serialize(ctx, row), previous=previous)


def finalize_invoice(ctx: seahaven.Ctx, invoice_id: str) -> dict[str, Any]:
    """draft -> open: the number (per-customer prefix + sequence,
    `data_model.md` §3.13), the customer-balance settlement and its
    `customer_balance_transactions` row, the frozen monetary fields, and
    `effective_at` (the finalize moment — recorded: drafts carry null).
    Emits `invoice.finalized` plus `invoice.sent` for `send_invoice` (the
    machine table's §2 pairing).

    A $0 settlement does NOT pay here — `finalize_and_settle` (the routes'
    helper) owns the recorded `$0 finalize pays` step, so internal callers
    that drive their own pay keep explicit control."""
    row = _lookup.require_row(ctx, "invoices", "invoice", invoice_id, param="invoice")
    if row["status"] != "draft":
        # The routed /finalize refusal is the recorded no-code spelling
        # (cassette 13 step 36); this guard is the internal-contract backstop
        # and no routed caller can reach it.
        raise seahaven.WorldBug(f"finalize_invoice on a {row['status']!r} invoice")
    now = ctx.clock.iso()
    customer = _lookup.require_row(ctx, "customers", "customer", row["customer"], param="customer")
    number = f"{customer['invoice_prefix']}-{customer['next_invoice_sequence']:04d}"
    ctx.db.execute(
        "UPDATE customers SET next_invoice_sequence = next_invoice_sequence + 1 WHERE id = ?",
        customer["id"],
    )
    starting_balance = customer["balance"]
    amount_due, ending_balance, delta = settle_customer_balance(row["total"], starting_balance)
    if delta:
        ctx.db.execute(
            "UPDATE customers SET balance = ? WHERE id = ?", ending_balance, customer["id"]
        )
        cbt_id = _ids.stripe_id(ctx, "cbtxn_")
        ctx.db.execute(
            "INSERT INTO customer_balance_transactions"
            " (id, x_seq, created, amount, currency, customer, ending_balance,"
            " invoice, type) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            cbt_id,
            _seq.next_seq(ctx, "customer_balance_transactions"),
            now,
            delta,
            row["currency"],
            customer["id"],
            ending_balance,
            invoice_id,
            # Credit consumed at finalization (a positive delta: the balance
            # grows toward zero); `adjustment` covers the debit-drawn and
            # negative-total directions (§3.5's table; Phase 15 records the
            # wire).
            "applied_to_invoice" if delta > 0 else "adjustment",
        )
    transitions = _load_dict(row["status_transitions"])
    transitions["finalized_at"] = _time.to_unix(now)
    ctx.db.execute(
        "UPDATE invoices SET number = ?, status = 'open', starting_balance = ?,"
        " amount_due = ?, amount_remaining = ?, ending_balance = ?,"
        " effective_at = ?, automatically_finalizes_at = NULL,"
        " next_payment_attempt = NULL, status_transitions = ? WHERE id = ?",
        number,
        starting_balance,
        amount_due,
        amount_due,
        ending_balance,
        now,
        _json.dumps(transitions),
        invoice_id,
    )
    fresh = _lookup.require_row(ctx, "invoices", "invoice", invoice_id, param="invoice")
    emit_invoice_event(ctx, "invoice.finalized", fresh)
    if row["collection_method"] == "send_invoice":
        emit_invoice_event(ctx, "invoice.sent", fresh)
    return fresh


def finalize_and_settle(
    ctx: seahaven.Ctx, invoice_id: str, *, auto_advance: bool | None = None
) -> dict[str, Any]:
    """The routed finalize family's shared step: optionally set
    `auto_advance` (the /finalize parameter), finalize, and settle a
    zero-`amount_due` result to `paid` inside the same call (recorded,
    cassette 13 steps 43/49/56: a $0 finalize answers `paid` with
    `attempted: true, attempt_count: 0`)."""
    if auto_advance is not None:
        # Set before finalizing so the collection machinery reads it; the
        # window fields are moot on a row about to leave `draft`.
        ctx.db.execute(
            "UPDATE invoices SET auto_advance = ? WHERE id = ?",
            int(auto_advance),
            invoice_id,
        )
    row = finalize_invoice(ctx, invoice_id)
    if row["amount_due"] == 0:
        _mark_paid(ctx, row, charge_free=True)
        return _re_read(ctx, invoice_id)
    return row


def _resolve_pm(
    ctx: seahaven.Ctx, invoice_row: Mapping[str, Any], sub_row: Mapping[str, Any] | None
) -> dict[str, Any] | None:
    """The invoice payment's resolution chain: the subscription's default,
    then the customer's `invoice_settings.default_payment_method`."""
    pm_id = None
    if sub_row is not None:
        pm_id = sub_row["default_payment_method"]
    if pm_id is None:
        customer = _lookup.require_row(
            ctx, "customers", "customer", invoice_row["customer"], param="customer"
        )
        settings = _load_dict(customer["invoice_settings"])
        pm_id = settings.get("default_payment_method")
    if pm_id is None:
        return None
    return _lookup.require_live_row(
        ctx, "payment_methods", "PaymentMethod", pm_id, param="payment_method"
    )


def pay_invoice(
    ctx: seahaven.Ctx,
    invoice_id: str,
    *,
    sub_row: Mapping[str, Any] | None = None,
    pm_row: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """One collection attempt on an open invoice. The outcome dictionary
    carries `outcome` ∈ `paid | failed | requires_action | no_payment_method`
    and, on `failed`, the `error` envelope a caller may return or raise.

    A decline is an outcome, not an exception — the invoice's rows survive
    with `attempt_count` advanced (the raise-loses rule)."""
    row = _lookup.require_row(ctx, "invoices", "invoice", invoice_id, param="invoice")
    if row["status"] != "open":
        # Phase 13's /pay route owns the recorded isn't-open 400; this
        # internal guard stays unreachable from routed callers.
        raise seahaven.WorldBug(f"pay_invoice on a {row['status']!r} invoice")
    if row["amount_due"] == 0:
        return _mark_paid(ctx, row, charge_free=True)
    if pm_row is None:
        pm_row = _resolve_pm(ctx, row, sub_row)
    if pm_row is None:
        # Nothing attempted: the recorded shape of the resume-time cycle
        # invoice (`attempted: false`, the count untouched).
        return {"outcome": "no_payment_method"}
    tags = payment_intents._behavior_tags(pm_row)
    if tags.get("three_d_secure") == "required":
        _attempted_without_count(ctx, invoice_id)
        emit_invoice_event(ctx, "invoice.payment_action_required", _re_read(ctx, invoice_id))
        return {"outcome": "requires_action"}
    if "charge_declined" in tags:
        code = tags["charge_declined"]
        decline_code = tags.get("decline_code")
        message = payment_intents._DECLINE_MESSAGES.get((code, decline_code))
        if message is None:
            raise seahaven.WorldBug(
                f"no recorded message for decline ({code!r}, {decline_code!r}): "
                "add the card's message to _DECLINE_MESSAGES"
            )
        charge = charges.insert_charge(
            ctx,
            payment_intent=None,
            pm_row=pm_row,
            amount=row["amount_due"],
            captured=True,
            currency=row["currency"],
            customer=row["customer"],
            description="Subscription creation"
            if row["billing_reason"] == "subscription_create"
            else None,
            receipt_email=None,
            shipping=None,
            statement_descriptor=None,
            statement_descriptor_suffix=None,
            failure_code=code,
            failure_message=message,
            outcome=charges.outcome_declined(decline_code or "generic_decline"),
        )
        _count_attempt(ctx, invoice_id)
        events.emit_event(ctx, type="charge.failed", obj=charges.serialize(ctx, charge))
        emit_invoice_event(ctx, "invoice.payment_failed", _re_read(ctx, invoice_id))
        error = declined_body(
            code=code,
            decline_code=decline_code,
            message=message,
            charge=charge["id"],
        )
        return {"outcome": "failed", "error": error}
    charge = charges.insert_charge(
        ctx,
        payment_intent=None,
        pm_row=pm_row,
        amount=row["amount_due"],
        captured=True,
        currency=row["currency"],
        customer=row["customer"],
        description="Subscription creation"
        if row["billing_reason"] == "subscription_create"
        else None,
        receipt_email=None,
        shipping=None,
        statement_descriptor=None,
        statement_descriptor_suffix=None,
    )
    events.emit_event(ctx, type="charge.succeeded", obj=charges.serialize(ctx, charge))
    return _mark_paid(ctx, _re_read(ctx, invoice_id), charge_id=charge["id"], counted=True)


def _re_read(ctx: seahaven.Ctx, invoice_id: str) -> dict[str, Any]:
    return _lookup.require_row(ctx, "invoices", "invoice", invoice_id, param="invoice")


def _count_attempt(ctx: seahaven.Ctx, invoice_id: str) -> None:
    ctx.db.execute(
        "UPDATE invoices SET attempted = 1, attempt_count = attempt_count + 1 WHERE id = ?",
        invoice_id,
    )


def _attempted_without_count(ctx: seahaven.Ctx, invoice_id: str) -> None:
    """The recorded 3DS shape: `attempted: true, attempt_count: 0` — the
    action gate blocks the network attempt, so the counter never moves."""
    ctx.db.execute("UPDATE invoices SET attempted = 1 WHERE id = ?", invoice_id)


def _mark_paid(
    ctx: seahaven.Ctx,
    row: Mapping[str, Any],
    *,
    charge_free: bool = False,
    charge_id: str | None = None,
    counted: bool = False,
    out_of_band: bool = False,
) -> dict[str, Any]:
    now = ctx.clock.iso()
    transitions = _load_dict(row["status_transitions"])
    transitions["paid_at"] = _time.to_unix(now)
    ctx.db.execute(
        "UPDATE invoices SET status = 'paid', attempted = ?,"
        # A charge-free payment ($0 or negative-total settlement) attempts
        # nothing: `attempted` flips but the counter stays (probed on the
        # trial-conversion invoice, Phase 12 CR round 3: attempt_count 0).
        # An out-of-band payment attempts nothing at all — `attempted`
        # itself stays false (recorded, cassette 13 step 61). Paying also
        # ends automatic advancement: `auto_advance` flips false (recorded,
        # step 62 — the out-of-band pay of an advancing draft).
        " auto_advance = 0,"
        " attempt_count = CASE WHEN ? THEN attempt_count + 1 ELSE attempt_count END,"
        " amount_paid = ?, amount_remaining = 0, status_transitions = ? WHERE id = ?",
        0 if out_of_band else 1,
        int(counted),
        row["amount_due"],
        _json.dumps(transitions),
        row["id"],
    )
    fresh = _re_read(ctx, row["id"])
    if charge_id is not None:
        # A real collection attempt succeeded: the payment event pair
        # (billing_engine §1's creation rows). Charge-free and out-of-band
        # settlements emit `invoice.paid` alone.
        emit_invoice_event(ctx, "invoice.payment_succeeded", fresh)
    emit_invoice_event(ctx, "invoice.paid", fresh)
    # The single recovery hook (billing_engine §1): incomplete/past_due/unpaid
    # -> active when their latest invoice reaches paid. Function-level import
    # because subscription_lifecycle imports this module for its own flows.
    from stripeapi.billing import subscription_lifecycle

    subscription_lifecycle.on_invoice_paid(ctx, row["id"])
    return {"outcome": "paid", "charge": charge_id}


def mark_uncollectible_invoice(ctx: seahaven.Ctx, invoice_id: str) -> dict[str, Any]:
    """open -> uncollectible, stamping `status_transitions
    .marked_uncollectible_at` (§2's transition; the sibling of
    `void_invoice`). The routed /mark_uncollectible refusal family is the
    caller's (recorded, cassette 13)."""
    row = _lookup.require_row(ctx, "invoices", "invoice", invoice_id, param="invoice")
    if row["status"] != "open":
        raise seahaven.WorldBug(f"mark_uncollectible_invoice on a {row['status']!r} invoice")
    now = ctx.clock.iso()
    transitions = _load_dict(row["status_transitions"])
    transitions["marked_uncollectible_at"] = _time.to_unix(now)
    ctx.db.execute(
        "UPDATE invoices SET status = 'uncollectible', status_transitions = ? WHERE id = ?",
        _json.dumps(transitions),
        invoice_id,
    )
    fresh = _re_read(ctx, invoice_id)
    emit_invoice_event(ctx, "invoice.marked_uncollectible", fresh)
    return fresh


def void_invoice(ctx: seahaven.Ctx, invoice_id: str) -> dict[str, Any]:
    """open -> void with the amounts left as they were (recorded, cassette
    13 step 45: `amount_due`/`amount_remaining` keep their open values)."""
    row = _lookup.require_row(ctx, "invoices", "invoice", invoice_id, param="invoice")
    if row["status"] != "open":
        raise seahaven.WorldBug(f"void_invoice on a {row['status']!r} invoice")
    now = ctx.clock.iso()
    transitions = _load_dict(row["status_transitions"])
    transitions["voided_at"] = _time.to_unix(now)
    ctx.db.execute(
        "UPDATE invoices SET status = 'void', status_transitions = ? WHERE id = ?",
        _json.dumps(transitions),
        invoice_id,
    )
    fresh = _re_read(ctx, invoice_id)
    emit_invoice_event(ctx, "invoice.voided", fresh)
    return fresh
