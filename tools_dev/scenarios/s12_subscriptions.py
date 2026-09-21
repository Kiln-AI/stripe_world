"""Scenario 12 (functional spec §12, the subscriptions slice): the
eight-status machine's reachable surface and the item resource.

Everything the customer holds is a `tok_visa` card, so every first invoice
pays synchronously; the 3DS card supplies `incomplete`; the PM-less
customer supplies the recorded no-payment-method refusal. The decline-card
creation path is deliberately absent — the pinned version has no
attachable-decline token (probed, Phase 12; the structural declaration in
`allowed_differences.py`), so no step could reach a subscription decline.
"""

from tools_dev.scenarios._dsl import ref

SCENARIO = "12_subscriptions"
DESCRIPTION = (
    "Subscriptions: create/retrieve/list, cancel_at_period_end, the item "
    "CRUD with its own 404 family, immediate cancel and the canceled-update "
    "rules, the trial paths including pause and the resume park, the 3DS "
    "incomplete branch and its error_if_incomplete 402, the no-PM and "
    "send_invoice refusals, and the recorded parameter-error spellings."
)

_EMAIL = "s12@conformance.stripeapi.invalid"


def record(r):
    customer = r.step(
        "POST",
        "/v1/customers",
        {"email": _EMAIL, "description": "scenario 12"},
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
    product = r.step("POST", "/v1/products", {"name": "s12 monthly"}, binds_as="product")
    price = r.step(
        "POST",
        "/v1/prices",
        {
            "product": product,
            "unit_amount": 2000,
            "currency": "cad",
            "recurring": {"interval": "month"},
        },
        binds_as="price",
    )
    price2 = r.step(
        "POST",
        "/v1/prices",
        {
            "product": product,
            "unit_amount": 5000,
            "currency": "cad",
            "recurring": {"interval": "month"},
        },
        binds_as="price2",
    )
    # The plain create: active, first invoice paid, period on the item.
    sub = r.step(
        "POST",
        "/v1/subscriptions",
        {"customer": customer, "items": [{"price": price}]},
        binds_as="sub",
    )
    r.step(
        "GET",
        "/v1/subscriptions/{subscription_exposed_id}",
        path_refs={"subscription_exposed_id": sub},
    )
    r.step("GET", "/v1/subscriptions", {"customer": customer, "status": "all"})
    r.step("GET", "/v1/subscriptions", {"price": price})
    r.step("GET", "/v1/subscriptions", {"status": "bogus"})
    r.step(
        "POST",
        "/v1/subscriptions/{subscription_exposed_id}",
        {"cancel_at_period_end": True},
        path_refs={"subscription_exposed_id": sub},
    )
    r.step(
        "POST",
        "/v1/subscriptions/{subscription_exposed_id}",
        {"cancel_at_period_end": False},
        path_refs={"subscription_exposed_id": sub},
    )
    # The item resource: add, list (oldest-first), update, delete.
    item = r.step(
        "POST",
        "/v1/subscription_items",
        {"subscription": sub, "price": price2},
        binds_as="item",
    )
    r.step("GET", "/v1/subscription_items", {"subscription": sub})
    r.step(
        "POST",
        "/v1/subscription_items/{item}",
        {"quantity": 3},
        path_refs={"item": item},
    )
    r.step(
        "DELETE",
        "/v1/subscription_items/{item}",
        {"proration_behavior": "none"},
        path_refs={"item": item},
    )
    r.step("GET", "/v1/subscription_items/si_nope")
    r.step("GET", "/v1/subscription_items")
    # Sub-update through items[0][id]: the quantity path.
    r.step(
        "POST",
        "/v1/subscriptions/{subscription_exposed_id}",
        {
            "items": [
                {"id": ref(sub, "items.data[0].id"), "quantity": 5},
            ]
        },
        path_refs={"subscription_exposed_id": sub},
    )
    # Immediate cancel, second cancel, and the canceled-update rules.
    r.step(
        "DELETE",
        "/v1/subscriptions/{subscription_exposed_id}",
        path_refs={"subscription_exposed_id": sub},
    )
    r.step(
        "DELETE",
        "/v1/subscriptions/{subscription_exposed_id}",
        path_refs={"subscription_exposed_id": sub},
    )
    r.step(
        "POST",
        "/v1/subscriptions/{subscription_exposed_id}",
        {"metadata": {"k": "v"}},
        path_refs={"subscription_exposed_id": sub},
    )
    r.step(
        "POST",
        "/v1/subscriptions/{subscription_exposed_id}",
        {"description": "after death"},
        path_refs={"subscription_exposed_id": sub},
    )
    # The missing-id 404s.
    r.step("GET", "/v1/subscriptions/sub_nope")
    r.step("POST", "/v1/subscriptions/sub_nope", {"metadata": {"k": "v"}})
    r.step("DELETE", "/v1/subscriptions/sub_nope")
    # Trial: created trialing, ended now with a PM present.
    trial = r.step(
        "POST",
        "/v1/subscriptions",
        {"customer": customer, "items": [{"price": price}], "trial_end": 1798761600},
        binds_as="trial",
    )
    r.step(
        "POST",
        "/v1/subscriptions/{subscription_exposed_id}",
        {"trial_end": "now"},
        path_refs={"subscription_exposed_id": trial},
    )
    # The 3DS branch: incomplete, then the error_if_incomplete 402.
    tds = r.step(
        "POST",
        "/v1/payment_methods",
        {"type": "card", "card": {"token": "tok_threeDSecure2Required"}},
        binds_as="tds",
    )
    r.step(
        "POST",
        "/v1/payment_methods/{payment_method}/attach",
        {"customer": customer},
        path_refs={"payment_method": tds},
    )
    r.step(
        "POST",
        "/v1/subscriptions",
        {"customer": customer, "items": [{"price": price}], "default_payment_method": tds},
    )
    r.step(
        "POST",
        "/v1/subscriptions",
        {
            "customer": customer,
            "items": [{"price": price}],
            "default_payment_method": tds,
            "payment_behavior": "error_if_incomplete",
        },
    )
    # The no-PM customer's recorded refusal.
    nopm = r.step(
        "POST",
        "/v1/customers",
        {"email": "s12b@conformance.stripeapi.invalid"},
        binds_as="nopm",
    )
    r.step("POST", "/v1/subscriptions", {"customer": nopm, "items": [{"price": price}]})
    # send_invoice: the draft-invoice activation, and the two refusals.
    r.step(
        "POST",
        "/v1/subscriptions",
        {
            "customer": customer,
            "items": [{"price": price}],
            "collection_method": "send_invoice",
            "days_until_due": 30,
        },
    )
    r.step(
        "POST",
        "/v1/subscriptions",
        {"customer": customer, "items": [{"price": price}], "days_until_due": 5},
    )
    # The pause path and the resume park.
    paused = r.step(
        "POST",
        "/v1/subscriptions",
        {
            "customer": nopm,
            "items": [{"price": price}],
            "trial_end": 1798761600,
            "trial_settings": {"end_behavior": {"missing_payment_method": "pause"}},
        },
        binds_as="paused",
    )
    r.step(
        "POST",
        "/v1/subscriptions/{subscription_exposed_id}",
        {"trial_end": "now"},
        path_refs={"subscription_exposed_id": paused},
    )
    r.step(
        "POST",
        "/v1/subscriptions/{subscription}/resume",
        {},
        path_refs={"subscription": paused},
    )
    r.step(
        "POST",
        "/v1/subscriptions/{subscription}/resume",
        {},
        path_refs={"subscription": sub},
    )
    # The create-time parameter refusals.
    r.step(
        "POST",
        "/v1/subscriptions",
        {
            "customer": customer,
            "items": [{"price": price}],
            "payment_behavior": "pending_if_incomplete",
        },
    )
    r.step(
        "POST",
        "/v1/subscriptions",
        {
            "customer": customer,
            "items": [{"price": price}],
            "payment_behavior": "bogus",
        },
    )
    r.step(
        "POST",
        "/v1/subscriptions",
        {"customer": customer, "items": [{"price": price}, {"price": price}]},
    )
    r.step("POST", "/v1/subscriptions", {"customer": customer})
    r.step(
        "POST",
        "/v1/subscriptions",
        {"customer": customer, "items": [{"price": "price_nope"}]},
    )
    r.step(
        "POST",
        "/v1/subscriptions",
        {"customer": "cus_nope", "items": [{"price": price}]},
    )
    r.step(
        "POST",
        "/v1/subscriptions",
        {
            "customer": customer,
            "items": [{"price": price}],
            "trial_end": 1798761600,
            "trial_from_plan": True,
        },
    )
    r.step(
        "POST",
        "/v1/subscriptions",
        {"customer": customer, "items": [{"price": price}], "trial_end": 1000},
    )


CLEANUP = {"customer": "/v1/customers"}
