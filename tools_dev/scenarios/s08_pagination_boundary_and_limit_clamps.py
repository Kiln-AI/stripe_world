"""Scenario 8 (functional spec §12): pagination past a page boundary, plus
the `limit` questions this world's Phase 3 flagged for the cassettes to
settle.

One hundred and one same-email customers (the minimum that makes the
high clamp visible), then: `limit=0` answers one item, `limit=101` answers
one hundred — the live API clamps silently into [1, 100] and never 400s,
superseding the documented 1-100 contract. A forward walk crosses two page
boundaries (40/40/21, the last page short and `has_more` false) and one
`ending_before` step walks back. Distinct descriptions make every page's
order assertable without comparing ids or timestamps.
"""

from tools_dev.scenarios._dsl import ref

SCENARIO = "08_pagination_boundary_and_limit_clamps"
DESCRIPTION = (
    "limit=0 -> 1 item and limit=101 -> 100 items (the silent clamp), a "
    "forward walk across page boundaries 40/40/21, and one ending_before "
    "back-step."
)

_EMAIL = "s08-cohort@conformance.stripeapi.invalid"


def record(r):
    for index in range(101):
        r.step("POST", "/v1/customers", {"email": _EMAIL, "description": f"s08 {index:03d}"})
    r.step("GET", "/v1/customers", {"email": _EMAIL, "limit": 0})
    r.step("GET", "/v1/customers", {"email": _EMAIL, "limit": 101})
    page_one = r.step("GET", "/v1/customers", {"email": _EMAIL, "limit": 40}, binds_as="page_one")
    page_two = r.step(
        "GET",
        "/v1/customers",
        {"email": _EMAIL, "limit": 40, "starting_after": ref(page_one, "data[39].id")},
        binds_as="page_two",
    )
    r.step(
        "GET",
        "/v1/customers",
        {"email": _EMAIL, "limit": 40, "starting_after": ref(page_two, "data[39].id")},
        binds_as="page_three",
    )
    r.step(
        "GET",
        "/v1/customers",
        {"email": _EMAIL, "limit": 40, "ending_before": ref(page_two, "data[0].id")},
    )


CLEANUP = {"customer": "/v1/customers"}
