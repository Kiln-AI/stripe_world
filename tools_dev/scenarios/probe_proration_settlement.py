"""Probe round 3: settlement of proration invoices and the balance trail
(Phase 14's step 3).

Round 2 left two open settlement questions: the recorded proration invoices
totaled 1 and 4 cents and were paid WITHOUT a charge, each cent rolled onto
the customer balance as owed — a minimum-chargeable rule or an
always_invoice-never-charges rule? And a net-NEGATIVE proration invoice (a
downgrade) was never recorded, nor the `customer_balance_transaction.type`
any of these rolls write. This round records both directions plus the
balance-transaction list.
"""

from tools_dev.scenarios._dsl import ref

SCENARIO = "probe_proration_settlement"
DESCRIPTION = (
    "Proration settlement: a large-net upgrade (charged or rolled?), a "
    "net-negative downgrade, and the customer balance transactions both "
    "leave behind."
)

_EMAIL = "probe-proration3@conformance.stripeapi.invalid"


def _third(body: dict) -> int:
    """`proration_date` leaving ~1/3 of the item's period (exact thirds for
    whole-day spans)."""
    item = body["items"]["data"][0]
    span = item["current_period_end"] - item["current_period_start"]
    return item["current_period_end"] - span // 3


def record(r):
    customer = r.step(
        "POST",
        "/v1/customers",
        {"email": _EMAIL, "description": "proration probe 3"},
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
    product = r.step("POST", "/v1/products", {"name": "probe proration 3"}, binds_as="product")
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
    pbig = r.step(
        "POST",
        "/v1/prices",
        {
            "product": product,
            "unit_amount": 60000,
            "currency": "usd",
            "recurring": {"interval": "month"},
        },
        binds_as="pbig",
    )
    psmall = r.step(
        "POST",
        "/v1/prices",
        {
            "product": product,
            "unit_amount": 1000,
            "currency": "usd",
            "recurring": {"interval": "month"},
        },
        binds_as="psmall",
    )
    # --- the big upgrade: 20.00 -> 600.00 at 2/3 remaining (net +~265) ---
    up = r.step(
        "POST",
        "/v1/subscriptions",
        {"customer": customer, "items": [{"price": p20}]},
        binds_as="up",
    )
    up_body = r.captured[-1].body
    up_update = r.step(
        "POST",
        "/v1/subscriptions/{subscription_exposed_id}",
        {
            "items": [{"id": up_body["items"]["data"][0]["id"], "price": pbig}],
            "proration_behavior": "always_invoice",
            "proration_date": _third(up_body),
        },
        path_refs={"subscription_exposed_id": up},
        binds_as="up_update",
    )
    r.step(
        "GET",
        "/v1/invoices/{invoice}",
        path_refs={"invoice": ref(up_update, "latest_invoice")},
    )
    # --- the downgrade: 20.00 -> 1.00 at 2/3 remaining (net -~127) ---
    down = r.step(
        "POST",
        "/v1/subscriptions",
        {"customer": customer, "items": [{"price": p20}]},
        binds_as="down",
    )
    down_body = r.captured[-1].body
    down_update = r.step(
        "POST",
        "/v1/subscriptions/{subscription_exposed_id}",
        {
            "items": [{"id": down_body["items"]["data"][0]["id"], "price": psmall}],
            "proration_behavior": "always_invoice",
            "proration_date": _third(down_body),
        },
        path_refs={"subscription_exposed_id": down},
        binds_as="down_update",
    )
    r.step(
        "GET",
        "/v1/invoices/{invoice}",
        path_refs={"invoice": ref(down_update, "latest_invoice")},
    )
    # --- the customer and its balance trail ---
    r.step("GET", "/v1/customers/{customer}", path_refs={"customer": customer})
    r.step(
        "GET",
        "/v1/customers/{customer}/balance_transactions",
        path_refs={"customer": customer},
    )


CLEANUP = {"customer": "/v1/customers"}
