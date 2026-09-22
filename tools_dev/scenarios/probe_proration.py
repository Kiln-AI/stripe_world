"""Probe: proration line shapes at `2026-08-26.dahlia` (Phase 14's step 3).

Why a probe first: the tie-break (an exact `x.xx5`), the quantity-only
reproration shape (whole-item vs delta), the pending invoiceitem's wire body,
and the cancel-credit shape are each open questions the curated cassette 01
will pin — this trail discovers the shapes before the scenario commits them.

The tie engineering: a monthly price whose period is `L` seconds, switched at
`proration_date = T1 - L/400` — a fraction of exactly 1/400 — makes an old
price of 10.00 credit `-1000/400 = -2.5` (a tie: half-up -3, half-even -2)
and a new price of 18.00 debit `+1800/400 = +4.5` (a tie: +5 vs +4). Both
lines tie at once, on any whole-day month, at record time AND replay time.
"""

SCENARIO = "probe_proration"
DESCRIPTION = (
    "Proration probes: the half-cent tie on both lines, quantity-only "
    "reproration, pending proration items, cancel credits, and the anchor "
    "reset."
)

_EMAIL = "probe-proration@conformance.stripeapi.invalid"


def _tie_date(body: dict) -> int:
    """`proration_date` leaving exactly 1/400 of the item's period."""
    item = body["items"]["data"][0]
    span = item["current_period_end"] - item["current_period_start"]
    return item["current_period_end"] - span // 400


def record(r):
    customer = r.step(
        "POST",
        "/v1/customers",
        {"email": _EMAIL, "description": "proration probe"},
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
    product = r.step("POST", "/v1/products", {"name": "probe proration"}, binds_as="product")
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
    # --- the tie: price switch with both lines on an exact half cent ---
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
    # --- quantity-only: whole-item reproration vs delta ---
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
    # --- create_prorations: the pending invoiceitem bodies ---
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
            "items": [{"id": pend_body["items"]["data"][0]["id"], "price": p18}],
            "proration_behavior": "create_prorations",
            "proration_date": _tie_date(pend_body),
        },
        path_refs={"subscription_exposed_id": pend},
    )
    r.step("GET", "/v1/invoiceitems", {"customer": customer, "pending": True})
    # --- none: no lines at all ---
    none_sub = r.step(
        "POST",
        "/v1/subscriptions",
        {"customer": customer, "items": [{"price": p10}]},
        binds_as="none_sub",
    )
    none_body = r.captured[-1].body
    r.step(
        "POST",
        "/v1/subscriptions/{subscription_exposed_id}",
        {
            "items": [{"id": none_body["items"]["data"][0]["id"], "price": p18}],
            "proration_behavior": "none",
        },
        path_refs={"subscription_exposed_id": none_sub},
    )
    r.step("GET", "/v1/invoiceitems", {"customer": customer, "pending": True})
    # --- the cancel credits ---
    r.step(
        "DELETE",
        "/v1/subscriptions/{subscription_exposed_id}",
        {"prorate": True},
        path_refs={"subscription_exposed_id": none_sub},
    )
    cancel2 = r.step(
        "POST",
        "/v1/subscriptions",
        {"customer": customer, "items": [{"price": p10}]},
        binds_as="cancel2",
    )
    r.step(
        "DELETE",
        "/v1/subscriptions/{subscription_exposed_id}",
        {"prorate": True, "invoice_now": True},
        path_refs={"subscription_exposed_id": cancel2},
    )
    # --- the anchor reset ---
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
    r.step(
        "POST",
        "/v1/subscriptions/{subscription_exposed_id}",
        {"billing_cycle_anchor": "unchanged"},
        path_refs={"subscription_exposed_id": anchor},
    )


CLEANUP = {"customer": "/v1/customers"}
