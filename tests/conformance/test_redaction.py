"""Redaction: the scrubber works, independent of what happens to be
committed right now — the mechanism the hygiene test trusts."""

import pytest

from conformance.cassette import Ref, Step
from conformance.redact import REQUEST_LOG_PLACEHOLDER, RedactionError, scrub


def a_step(**overrides: object) -> Step:
    fields: dict = {
        "seq": 0,
        "method": "POST",
        "path": "/v1/customers",
        "path_refs": {},
        "params": {},
        "idempotency_key": None,
        "binds_as": None,
        "recorded_status": 200,
        "recorded_body": {},
        "recorded_stripe_version": "2026-08-26.dahlia",
    }
    fields.update(overrides)
    return Step(**fields)  # type: ignore[arg-type]


def test_redaction_catches_planted_secrets() -> None:
    """A planted key and account id are replaced and reported as findings.
    Replacing is not enough — the findings are what make the recorder abort
    instead of committing a placeholder. Both key shapes the recorder's own
    gate admits (`sk_test_` and `rk_test_`) are planted: a restricted key is
    a supported recording configuration and must not slip the scrub."""
    step, findings = scrub(
        a_step(
            params={
                "metadata": {
                    "leak": "sk_test_1234567890abcdefghijklmnop",
                    "restricted": "rk_test_1234567890qrstuvwx",
                }
            },
            recorded_body={"description": "charged acct_1UHQMoHIBYZYyePr today"},
        )
    )
    assert step.params["metadata"]["leak"] == "<redacted:api_key>"
    assert step.params["metadata"]["restricted"] == "<redacted:api_key>"
    assert "acct_1UHQMoHIBYZYyePr" not in step.recorded_body["description"]
    assert any("account id" in finding for finding in findings)
    assert any("API key" in finding for finding in findings)
    # And the committed-file scan shares the same regexes, so it catches
    # both shapes too.
    from conformance.redact import scan_text

    assert scan_text("x rk_test_1234567890qrstuvwx y") != []


def test_a_non_synthetic_email_raises_rather_than_laundering() -> None:
    with pytest.raises(RedactionError, match=r"someone@example\.com"):
        scrub(a_step(recorded_body={"email": "someone@example.com"}))


def test_the_reserved_domain_passes_the_email_guard() -> None:
    step, findings = scrub(a_step(recorded_body={"email": "s02@conformance.stripeapi.invalid"}))
    assert step.recorded_body["email"] == "s02@conformance.stripeapi.invalid"
    assert findings == []


def test_request_log_url_is_normalized_without_a_finding() -> None:
    """It is a declared allow-list field present in every live error
    envelope; normalizing (not finding) it keeps ordinary recordings from
    aborting while killing the per-re-record request-id churn."""
    step, findings = scrub(
        a_step(
            recorded_status=400,
            recorded_body={
                "error": {
                    "type": "invalid_request_error",
                    "request_log_url": (
                        "https://dashboard.stripe.com/acct_1UHQMoHIBYZYyePr/test/logs?object=req_ABC"
                    ),
                }
            },
        )
    )
    assert step.recorded_body["error"]["request_log_url"] == REQUEST_LOG_PLACEHOLDER
    assert findings == []


def test_refs_are_never_touched_by_the_scrubber() -> None:
    """A `Ref` holds a step name and a field path, never a wire value — the
    scrubber must not need to know that, and must not mangle one."""
    step, _ = scrub(
        a_step(
            path="/v1/customers/{customer}",
            path_refs={"customer": Ref("customer", "id")},
            params={"customer": Ref("customer", "id")},
        )
    )
    assert step.path_refs["customer"] == Ref("customer", "id")
    assert step.params["customer"] == Ref("customer", "id")
