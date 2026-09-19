"""Scenario 2 (functional spec §12): a cursor against the id of a deleted
object — a genuine documentation silence, settled by this recording.

Three same-email customers, the middle one deleted: the retrieve answers the
three-key stub, both cursors still resolve against the deleted id (it is a
coordinate in the ordering, not a membership test), the deleted row never
appears in a page, and the plain filtered list shows the survivors newest
first. Distinct descriptions make the page order assertable without
comparing ids or timestamps."""

SCENARIO = "02_pagination_cursor_against_deleted_id"
DESCRIPTION = (
    "starting_after / ending_before against a deleted object id, and the "
    "deleted-stub retrieve: the documented silence, settled by recording."
)

_EMAIL = "s02-cohort@conformance.stripeapi.invalid"


def record(r):
    r.step("POST", "/v1/customers", {"email": _EMAIL, "description": "s02 oldest"})
    middle = r.step(
        "POST", "/v1/customers", {"email": _EMAIL, "description": "s02 middle"}, binds_as="middle"
    )
    r.step("POST", "/v1/customers", {"email": _EMAIL, "description": "s02 newest"})
    r.step("DELETE", "/v1/customers/{customer}", path_refs={"customer": middle})
    r.step("GET", "/v1/customers/{customer}", path_refs={"customer": middle})
    r.step("GET", "/v1/customers", {"email": _EMAIL, "starting_after": middle})
    r.step("GET", "/v1/customers", {"email": _EMAIL, "ending_before": middle})
    r.step("GET", "/v1/customers", {"email": _EMAIL, "limit": 10})


CLEANUP = {"customer": "/v1/customers"}
