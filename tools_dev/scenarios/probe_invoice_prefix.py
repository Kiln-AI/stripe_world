"""Probe: the `invoice_prefix` contract on customers — the format refusal
and the uniqueness refusal, recorded at the pinned version.

Why a probe and not a merge-gate cassette: the taken-by message embeds the
holding customer's id, so a replay's only differences would be id-shaped;
the conformance side of this evidence is the predicated `**.error.message`
entry, and the refusal *shapes* (status, `param`, no `code`) are what this
recording commits.

What it pins:

- format: `'a'`, `'abc'`, `'AB_1'` and 13 characters all refuse identically —
  400 `invalid_request_error`, the bespoke message (en dash Stripe's own),
  no `code`, no `param`;
- a 10-character date-derived holder prefix (12-character acceptance is
  unit-tested; the 13-character refusal is recorded);
- uniqueness: a second create onto the taken prefix, and an update onto it,
  refuse with `param: invoice_prefix` and the taken-by message verbatim,
  naming the holding customer; re-sending one's own prefix is a 200.

The 1-character acceptance boundary is deliberately NOT recorded: every
accepted create permanently reserves that prefix on the recording account
(deleting a customer tombstones it and keeps its prefix reserved,
data_model §3.13), so a recorded single letter would break every re-record.
The unit test covers it; the holder prefix below is date-derived for the
same reason — a re-record on a later day starts from a free prefix.
"""

from datetime import UTC, datetime

SCENARIO = "probe_invoice_prefix"
DESCRIPTION = (
    "The customers invoice_prefix contract: format refusal, a 10-character "
    "date-derived holder prefix, and the uniqueness refusal on create and "
    "update."
)

_EMAIL = "probe-prefix@conformance.stripeapi.invalid"

# Date-derived, so a re-record on a later day starts from a free prefix and
# the holder's creation doubles as an acceptance data point (10 characters;
# 12-character acceptance is unit-tested). Two recordings on the same day
# collide on it — re-record on another day.
_HOLDER_PREFIX = f"P6{datetime.now(UTC):%y%m%d}01"


def record(r):
    for bad in ("a", "abc", "AB_1", "ABCDEFGHIJKLM"):
        r.step("POST", "/v1/customers", {"email": _EMAIL, "invoice_prefix": bad})
    holder = r.step(
        "POST",
        "/v1/customers",
        {"email": _EMAIL, "invoice_prefix": _HOLDER_PREFIX},
        binds_as="holder",
    )
    other = r.step("POST", "/v1/customers", {"email": _EMAIL}, binds_as="other")
    r.step(
        "POST",
        "/v1/customers",
        {"email": _EMAIL, "invoice_prefix": _HOLDER_PREFIX},
    )
    r.step(
        "POST",
        "/v1/customers/{customer}",
        {"invoice_prefix": _HOLDER_PREFIX},
        path_refs={"customer": other},
    )
    # Re-sending a customer's own prefix is not a conflict.
    r.step(
        "POST",
        "/v1/customers/{customer}",
        {"invoice_prefix": _HOLDER_PREFIX},
        path_refs={"customer": holder},
    )


CLEANUP = {"customer": "/v1/customers"}
