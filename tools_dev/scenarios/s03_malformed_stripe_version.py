"""Scenario 3 (functional spec §12): a malformed `Stripe-Version` — also
undocumented, settled by this recording: a 400 `invalid_request_error`
carrying type and message only (no code, no param), for any malformed value.

The world deliberately does not implement it: one version is served, the
version is not an agent-facing parameter, and the tool return has no header
channel (functional spec §6.5). The replayed response is therefore the
world's normal pinned-version answer, and the whole-response difference is
declared in `allowed_differences.py`, scoped to this scenario alone.
"""

SCENARIO = "03_malformed_stripe_version"
DESCRIPTION = (
    "A malformed Stripe-Version header on a plain GET: what real Stripe "
    "answers, recorded once; this world serves one fixed version and "
    "declares the difference."
)


def record(r):
    # The one deliberately un-pinned call in the entire recorder
    # (`components/conformance.md` "The recorder"), named here so nobody
    # mistakes it for an accidental drift from the pin.
    r.step(
        "GET",
        "/v1/customers",
        {"limit": 1},
        stripe_version_override="not-a-real-version",
    )
