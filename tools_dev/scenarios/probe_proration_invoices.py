"""Probe round 2: the invoice bodies behind proration (Phase 14's step 3).

Round 1 (`probe_proration`) pinned the pending-item shapes and the rounding
tie (floor). This round reads the INVOICES: the always_invoice update's
billing reason and line set, the quantity-only reproration shape, the
cancel-with-invoice_now final invoice, and whether an anchor reset mints
anything at all.
"""

SCENARIO = "probe_proration_invoices"
DESCRIPTION = (
    "The invoices behind proration: always_invoice's billing_reason and "
    "lines, quantity-only reproration, cancel's final invoice, and the "
    "anchor reset's no-op."
)

_EMAIL = "probe-proration2@conformance.stripeapi.invalid"


def _tie_date(body: dict) -> int:
    item = body["items"]["data"][0]
    span = item["current_period_end"] - item["current_period_start"]
    return item["current_period_end"] - span // 400


def record(r):
    customer = r.step(
        "POST",
        "/v1/customers",
        {"email": _EMAIL, "description": "proration probe 2"},
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
    product = r.step("POST", "/v1/products", {"name": "probe proration 2"}, binds_as="product")
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
    # --- always_invoice: the invoice's own shape ---
    tie = r.step(
        "POST",
        "/v1/subscriptions",
        {"customer": customer, "items": [{"price": p10}]},
        binds_as="tie",
    )
    tie_body = r.captured[-1].body
    r.step(
        "POST",
        "/v1/subscriptions/{subscription_exposed_id}",
        {
            "items": [{"id": tie_body["items"]["data"][0]["id"], "price": p18}],
            "proration_behavior": "always_invoice",
            "proration_date": _tie_date(tie_body),
        },
        path_refs={"subscription_exposed_id": tie},
    )
    r.step(
        "GET",
        "/v1/invoices/{invoice}",
        path_refs={"invoice": ref_invoice(tie, r)},
    )
    # --- quantity-only switch: whole-item reproration or delta ---
    qty = r.step(
        "POST",
        "/v1/subscriptions",
        {"customer": customer, "items": [{"price": p10}]},
        binds_as="qty",
    )
    qty_body = r.captured[-1].body
    r.step(
        "POST",
        "/v1/subscriptions/{subscription_exposed_id}",
        {
            "items": [{"id": qty_body["items"]["data"][0]["id"], "quantity": 3}],
            "proration_behavior": "always_invoice",
            "proration_date": _tie_date(qty_body),
        },
        path_refs={"subscription_exposed_id": qty},
    )
    r.step(
        "GET",
        "/v1/invoices/{invoice}",
        path_refs={"invoice": ref_invoice(qty, r)},
    )
    # --- cancel: prorate's pending credit, then invoice_now's final invoice ---
    cancel = r.step(
        "POST",
        "/v1/subscriptions",
        {"customer": customer, "items": [{"price": p10}]},
        binds_as="cancel",
    )
    r.step(
        "DELETE",
        "/v1/subscriptions/{subscription_exposed_id}",
        {"prorate": True, "invoice_now": True},
        path_refs={"subscription_exposed_id": cancel},
    )
    r.step("GET", "/v1/invoiceitems", {"customer": customer, "pending": True})
    r.step(
        "GET",
        "/v1/invoices/{invoice}",
        path_refs={"invoice": ref_invoice(cancel, r)},
    )
    # --- the anchor reset: what lands on the sub ---
    anchor = r.step(
        "POST",
        "/v1/subscriptions",
        {"customer": customer, "items": [{"price": p10}]},
        binds_as="anchor",
    )
    r.step(
        "POST",
        "/v1/subscriptions/{subscription_exposed_id}",
        {"billing_cycle_anchor": "now"},
        path_refs={"subscription_exposed_id": anchor},
    )
    r.step("GET", "/v1/invoiceitems", {"customer": customer, "pending": True})
    r.step(
        "GET",
        "/v1/invoices/{invoice}",
        path_refs={"invoice": ref_invoice(anchor, r)},
    )


def ref_invoice(handle, r):
    from tools_dev.scenarios._dsl import ref

    return ref(handle, "latest_invoice")


CLEANUP = {"customer": "/v1/customers"}
