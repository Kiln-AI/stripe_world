"""Scenario 01 (functional spec §12): the proration half-cent tie-break —
the one part of proration rounding the documents never stated — plus the
whole-configuration quantity case, the documented -667/+333/-334 invoice,
and the settlement trails.

The engineering, at record time AND replay time: a monthly price whose
period spans `L` seconds, switched at `proration_date = T1 - L//400` (an
exact fraction of 1/400 — every whole-day month divides evenly), makes the
10.00 credit land on -2.5 and the 18.00 debit on +4.5. Both lines tie at
once, so one recording distinguishes floor from every nearest-integer
rule. A second switch at `T1 - L//3` (an exact third, again always whole)
reproduces the documented -667/+333/-334 invoice live.

Recorded on the sandbox account, whose dashboard `billing_mode` is
flexible — the classic/flexible pair the scenario list names collapses to
what the account can produce, and the rounding of a line is mode-
independent arithmetic: declared in `allowed_differences.py`.
"""

from conformance.cassette import Ref
from tools_dev.scenarios._dsl import ref

SCENARIO = "01_proration_half_cent"
DESCRIPTION = (
    "Proration: the half-cent tie on both lines (floor), whole-item "
    "quantity reproration, the documented -667/+333/-334 invoice with its "
    "balance trail, sub-minimum rolls, pending items under "
    "create_prorations, none, and the cancel credits."
)

_EMAIL = "s01@conformance.stripeapi.invalid"


def _tie(body: dict) -> int:
    """The offset leaving exactly 1/400 of the item's period — every
    whole-day month divides evenly, so the fraction is exact at any
    recording's span."""
    item = body["items"]["data"][0]
    span = item["current_period_end"] - item["current_period_start"]
    return -(span // 400)


def _third(body: dict) -> int:
    """The offset leaving exactly one third of the item's period — 86400
    divides by 3, so any whole-day span yields an exact third."""
    item = body["items"]["data"][0]
    span = item["current_period_end"] - item["current_period_start"]
    return -(span // 3)


def _tie_ref(handle, body: dict) -> Ref:
    return ref(handle, "items.data[0].current_period_end", offset=_tie(body))


def _third_ref(handle, body: dict) -> Ref:
    return ref(handle, "items.data[0].current_period_end", offset=_third(body))


def record(r):
    customer = r.step(
        "POST",
        "/v1/customers",
        {"email": _EMAIL, "description": "scenario 01"},
        binds_as="customer",
    )
    visa = r.step(
        "POST",
        "/v1/payment_methods",
        {"type": "card", "card": {"token": "tok_visa"}},
        binds_as="visa",
    )
    r.step(
        "POST",
        "/v1/payment_methods/{payment_method}/attach",
        {"customer": customer},
        path_refs={"payment_method": visa},
    )
    r.step(
        "POST",
        "/v1/customers/{customer}",
        {"invoice_settings": {"default_payment_method": visa}},
        path_refs={"customer": customer},
    )
    product = r.step("POST", "/v1/products", {"name": "s01 proration"}, binds_as="product")
    p10 = r.step(
        "POST",
        "/v1/prices",
        {
            "product": product,
            "unit_amount": 1000,
            "currency": "usd",
            "recurring": {"interval": "month"},
        },
        binds_as="p10",
    )
    p18 = r.step(
        "POST",
        "/v1/prices",
        {
            "product": product,
            "unit_amount": 1800,
            "currency": "usd",
            "recurring": {"interval": "month"},
        },
        binds_as="p18",
    )
    p20 = r.step(
        "POST",
        "/v1/prices",
        {
            "product": product,
            "unit_amount": 2000,
            "currency": "usd",
            "recurring": {"interval": "month"},
        },
        binds_as="p20",
    )
    # --- the tie: both proration lines on an exact half cent ---
    tie = r.step(
        "POST",
        "/v1/subscriptions",
        {"customer": customer, "items": [{"price": p10}]},
        binds_as="tie",
    )
    tie_body = r.captured[-1].body
    tie_update = r.step(
        "POST",
        "/v1/subscriptions/{subscription_exposed_id}",
        {
            "items": [{"id": ref(tie, "items.data[0].id"), "price": p18}],
            "proration_behavior": "always_invoice",
            "proration_date": _tie_ref(tie, tie_body),
        },
        path_refs={"subscription_exposed_id": tie},
        binds_as="tie_update",
    )
    r.step(
        "GET",
        "/v1/invoices/{invoice}",
        path_refs={"invoice": ref(tie_update, "latest_invoice")},
    )
    # --- quantity-only: whole-configuration reproration ---
    qty = r.step(
        "POST",
        "/v1/subscriptions",
        {"customer": customer, "items": [{"price": p10}]},
        binds_as="qty",
    )
    qty_body = r.captured[-1].body
    qty_update = r.step(
        "POST",
        "/v1/subscriptions/{subscription_exposed_id}",
        {
            "items": [{"id": ref(qty, "items.data[0].id"), "quantity": 3}],
            "proration_behavior": "always_invoice",
            "proration_date": _tie_ref(qty, qty_body),
        },
        path_refs={"subscription_exposed_id": qty},
        binds_as="qty_update",
    )
    r.step(
        "GET",
        "/v1/invoices/{invoice}",
        path_refs={"invoice": ref(qty_update, "latest_invoice")},
    )
    # --- the documented -667/+333/-334, live, with its balance trail ---
    doc = r.step(
        "POST",
        "/v1/subscriptions",
        {"customer": customer, "items": [{"price": p20}]},
        binds_as="doc",
    )
    doc_body = r.captured[-1].body
    doc_update = r.step(
        "POST",
        "/v1/subscriptions/{subscription_exposed_id}",
        {
            "items": [{"id": ref(doc, "items.data[0].id"), "price": p10}],
            "proration_behavior": "always_invoice",
            "proration_date": _third_ref(doc, doc_body),
        },
        path_refs={"subscription_exposed_id": doc},
        binds_as="doc_update",
    )
    r.step(
        "GET",
        "/v1/invoices/{invoice}",
        path_refs={"invoice": ref(doc_update, "latest_invoice")},
    )
    r.step("GET", "/v1/customers/{customer}", path_refs={"customer": customer})
    # The balance trail itself (`balance_transactions` — the
    # `applied_to_invoice` -334 / `invoice_too_small` +1 rows) is recorded
    # in `probe_proration_settlement.json`: the list route is Phase 15's
    # surface and cannot replay here, so the curated cassette keeps to this
    # world's routed reads (the customer's `balance` carries the net).
    # --- create_prorations: the pending items' wire bodies ---
    pend = r.step(
        "POST",
        "/v1/subscriptions",
        {"customer": customer, "items": [{"price": p10}]},
        binds_as="pend",
    )
    pend_body = r.captured[-1].body
    r.step(
        "POST",
        "/v1/subscriptions/{subscription_exposed_id}",
        {
            "items": [{"id": ref(pend, "items.data[0].id"), "price": p18}],
            "proration_behavior": "create_prorations",
            "proration_date": _tie_ref(pend, pend_body),
        },
        path_refs={"subscription_exposed_id": pend},
    )
    r.step("GET", "/v1/invoiceitems", {"customer": customer, "pending": True})
    # --- none: nothing lands ---
    none_sub = r.step(
        "POST",
        "/v1/subscriptions",
        {"customer": customer, "items": [{"price": p10}]},
        binds_as="none_sub",
    )
    r.step(
        "POST",
        "/v1/subscriptions/{subscription_exposed_id}",
        {
            "items": [{"id": ref(none_sub, "items.data[0].id"), "price": p18}],
            "proration_behavior": "none",
        },
        path_refs={"subscription_exposed_id": none_sub},
    )
    r.step("GET", "/v1/invoiceitems", {"customer": customer, "pending": True})
    # --- the cancel credits: invoice_now's final invoice, then the
    # prorate-only pending credit ---
    final = r.step(
        "POST",
        "/v1/subscriptions",
        {"customer": customer, "items": [{"price": p10}]},
        binds_as="final",
    )
    final_delete = r.step(
        "DELETE",
        "/v1/subscriptions/{subscription_exposed_id}",
        {"prorate": True, "invoice_now": True},
        path_refs={"subscription_exposed_id": final},
        binds_as="final_delete",
    )
    r.step(
        "GET",
        "/v1/invoices/{invoice}",
        path_refs={"invoice": ref(final_delete, "latest_invoice")},
    )
    r.step("GET", "/v1/invoiceitems", {"customer": customer, "pending": True})
    r.step(
        "DELETE",
        "/v1/subscriptions/{subscription_exposed_id}",
        {"prorate": True},
        path_refs={"subscription_exposed_id": none_sub},
    )
    r.step("GET", "/v1/invoiceitems", {"customer": customer, "pending": True})


CLEANUP = {"customer": "/v1/customers"}
