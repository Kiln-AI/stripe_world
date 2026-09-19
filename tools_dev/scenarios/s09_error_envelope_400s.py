"""Scenario 9 (functional spec §12): deliberate 400s pinning the error
envelope, including a nested `param` name.

Five faults the whole dispatcher shares, so they are pinned once here rather
than per slice: an unknown parameter, an unknown parameter nested one object
down (`invoice_settings[nope]` — the bracket form), a non-integer `limit`,
a well-formed but nonexistent cursor id (a query-side `resource_missing` is
a **400**, and its resolution precedes the both-cursors refusal), and two
real cursors sent together (the exclusivity refusal, which carries type and
message only — no `code`).

Type-fault cases whose live rendering depends on form-encoding scalars
(`email=5` stringifies to `"5"` on the wire before Stripe validates it) are
deliberately absent: this surface takes JSON, and that transport difference
is declared in `allowed_differences.py` rather than recorded.
"""

SCENARIO = "09_error_envelope_400s"
DESCRIPTION = (
    "Unknown parameter, nested unknown parameter, non-integer limit, a "
    "nonexistent cursor at 400, and both cursors together: the error "
    "envelope, pinned."
)


def record(r):
    first = r.step(
        "POST",
        "/v1/customers",
        {"email": "s09-first@conformance.stripeapi.invalid"},
        binds_as="first",
    )
    second = r.step(
        "POST",
        "/v1/customers",
        {"email": "s09-second@conformance.stripeapi.invalid"},
        binds_as="second",
    )
    r.step("GET", "/v1/customers", {"limit": "abc"})
    r.step("GET", "/v1/customers", {"wat": "x"})
    r.step("POST", "/v1/customers", {"invoice_settings": {"nope": "x"}})
    r.step("GET", "/v1/customers", {"starting_after": "cus_aaa"})
    r.step("GET", "/v1/customers", {"starting_after": first, "ending_before": second})


CLEANUP = {"customer": "/v1/customers"}
