"""Probe: the event shapes behind this slice — `/v1/events` is unrouted until
Phase 17, so this recording is the committed evidence rather than a
replayable cassette (probe scenarios are excluded from the conformance gate
by design).

What it pins, all at the pinned version:

- `customer.deleted`'s `data.object`: the FULL pre-delete customer object
  with NO `deleted` key — the settle of the Phase 3 carry-over that decided
  `resource.delete`'s emission order and snapshot shape;
- `customer.updated`'s `previous_attributes`: the changed keys with their
  prior values;
- `payment_method.attached`: no `previous_attributes`;
- `payment_method.detached`: `previous_attributes: {"customer": …}`;
- `payment_method.updated`: exists (the engine's updated_event), captured
  from the update step;
- and the absence of any `payment_method.created`.
"""

SCENARIO = "probe_customer_payment_method_events"
DESCRIPTION = (
    "The events behind customers and payment methods, read from /v1/events: "
    "the customer.deleted full-object snapshot, previous_attributes shapes, "
    "and the payment_method.* family."
)

_EMAIL = "probe-events@conformance.stripeapi.invalid"


def record(r):
    customer = r.step(
        "POST",
        "/v1/customers",
        {"email": _EMAIL, "name": "Before", "metadata": {"probe": "events"}},
        binds_as="customer",
    )
    pm = r.step(
        "POST",
        "/v1/payment_methods",
        {"type": "card", "card": {"token": "tok_visa"}},
        binds_as="pm",
    )
    r.step(
        "POST",
        "/v1/payment_methods/{payment_method}/attach",
        {"customer": customer},
        path_refs={"payment_method": pm},
    )
    r.step(
        "POST",
        "/v1/payment_methods/{payment_method}",
        {"allow_redisplay": "limited"},
        path_refs={"payment_method": pm},
    )
    r.step(
        "POST",
        "/v1/payment_methods/{payment_method}/detach",
        path_refs={"payment_method": pm},
    )
    r.step(
        "POST",
        "/v1/customers/{customer}",
        {"name": "After", "email": "probe-events-2@conformance.stripeapi.invalid"},
        path_refs={"customer": customer},
    )
    r.step("DELETE", "/v1/customers/{customer}", path_refs={"customer": customer})
    r.step("GET", "/v1/events", {"type": "customer.deleted", "limit": 1})
    r.step("GET", "/v1/events", {"type": "customer.updated", "limit": 1})
    r.step("GET", "/v1/events", {"type": "customer.created", "limit": 1})
    r.step("GET", "/v1/events", {"type": "payment_method.attached", "limit": 1})
    r.step("GET", "/v1/events", {"type": "payment_method.detached", "limit": 1})
    r.step("GET", "/v1/events", {"type": "payment_method.updated", "limit": 1})
    r.step("GET", "/v1/events", {"type": "payment_method.created", "limit": 1})


CLEANUP = {"customer": "/v1/customers"}
