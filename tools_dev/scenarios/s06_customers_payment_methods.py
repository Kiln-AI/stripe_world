"""Scenario 6: the customers-and-payment-methods slice, end to end.

Every step is replayable by this world's own tools at the pinned version:
the customer create/update/delete cycle, token-created card payment methods,
attach (idempotent), the recorded attachment refusal of a decline-table
card, the customer-scoped and filtered lists, the scoped retrieve, the
missing-parent and unknown-customer refusals, the update, detach and the
verbatim detach-again refusal. Raw card numbers are
deliberately absent: the recording account refuses them, so `tok_*` is the
recordable path (see the structural declaration in allowed_differences.py).
"""

SCENARIO = "06_customers_payment_methods"
DESCRIPTION = (
    "The customers + payment_methods slice: full customer cycle, token card "
    "creation, attach/detach with their recorded errors, scoped and filtered "
    "lists."
)

_EMAIL = "s06@conformance.stripeapi.invalid"


def record(r):
    customer = r.step(
        "POST",
        "/v1/customers",
        {
            "email": _EMAIL,
            "description": "scenario 06",
            "name": "Six",
            "metadata": {"probe": "s06"},
        },
        binds_as="customer",
    )
    r.step("GET", "/v1/customers/{customer}", path_refs={"customer": customer})
    r.step(
        "POST",
        "/v1/customers/{customer}",
        {"name": "Six II", "email": "s06-2@conformance.stripeapi.invalid"},
        path_refs={"customer": customer},
    )
    visa = r.step(
        "POST",
        "/v1/payment_methods",
        {"type": "card", "card": {"token": "tok_visa"}},
        binds_as="visa",
    )
    declined = r.step(
        "POST",
        "/v1/payment_methods",
        {"type": "card", "card": {"token": "tok_visa_chargeDeclined"}},
        binds_as="declined",
    )
    # A stubbed rail: created with no rail parameter, answered with exactly
    # `"klarna": {}` under its own type key.
    klarna = r.step(
        "POST",
        "/v1/payment_methods",
        {"type": "klarna"},
        binds_as="klarna",
    )
    r.step(
        "GET",
        "/v1/payment_methods/{payment_method}",
        path_refs={"payment_method": klarna},
    )
    r.step(
        "POST",
        "/v1/payment_methods/{payment_method}/attach",
        {"customer": customer},
        path_refs={"payment_method": visa},
    )
    # Idempotent: the second attach of the same pair answers 200 again.
    r.step(
        "POST",
        "/v1/payment_methods/{payment_method}/attach",
        {"customer": customer},
        path_refs={"payment_method": visa},
    )
    # The decline-table card refuses attachment itself, at 402.
    r.step(
        "POST",
        "/v1/payment_methods/{payment_method}/attach",
        {"customer": customer},
        path_refs={"payment_method": declined},
    )
    # The scoped retrieve of a method that exists but is not the customer's:
    # the message-only 404 naming the customer placeholder.
    r.step(
        "GET",
        "/v1/customers/{customer}/payment_methods/{payment_method}",
        path_refs={"customer": customer, "payment_method": declined},
    )
    r.step("GET", "/v1/payment_methods", {"customer": "cus_nope"})
    # The scoped path's missing-parent spelling: this path names `param: "id"`
    # where the top-level query above named `customer` — the per-path
    # inconsistency `Scope.missing_param` exists to carry.
    r.step("GET", "/v1/customers/cus_nope/payment_methods", {"type": "card"})
    r.step(
        "GET",
        "/v1/payment_methods",
        {"customer": customer, "type": "card", "limit": 5},
    )
    r.step("GET", "/v1/payment_methods", {"limit": 3})
    r.step(
        "GET",
        "/v1/customers/{customer}/payment_methods",
        {"type": "card"},
        path_refs={"customer": customer},
    )
    r.step(
        "GET",
        "/v1/customers/{customer}/payment_methods/{payment_method}",
        path_refs={"customer": customer, "payment_method": visa},
    )
    r.step(
        "GET",
        "/v1/payment_methods/{payment_method}",
        {"expand": ["customer"]},
        path_refs={"payment_method": visa},
    )
    r.step(
        "POST",
        "/v1/payment_methods/{payment_method}",
        {"allow_redisplay": "limited", "billing_details": {"name": "Six II"}},
        path_refs={"payment_method": visa},
    )
    r.step(
        "GET",
        "/v1/payment_methods",
        {"customer": customer, "allow_redisplay": "limited"},
    )
    r.step(
        "POST",
        "/v1/payment_methods/{payment_method}/detach",
        path_refs={"payment_method": visa},
    )
    r.step(
        "POST",
        "/v1/payment_methods/{payment_method}/detach",
        path_refs={"payment_method": visa},
    )
    r.step("DELETE", "/v1/customers/{customer}", path_refs={"customer": customer})
    # A deleted customer is as missing as an absent one on the query side:
    # the same 400 resource_missing, unlike a path id where the tombstone
    # still resolves.
    r.step("GET", "/v1/payment_methods", {"customer": customer})
    # …and on the attach parameter too: attaching after the delete refuses
    # rather than accepting a tombstoned holder.
    r.step(
        "POST",
        "/v1/payment_methods/{payment_method}/attach",
        {"customer": customer},
        path_refs={"payment_method": visa},
    )


CLEANUP = {"customer": "/v1/customers"}
