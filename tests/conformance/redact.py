"""The redaction pass between capture and disk.

A `Step` never reaches a cassette file without passing through `scrub`. The
four categories carry different risks, so they get different postures:

1. **API keys** — replaced with a placeholder, and the replacement is a
   *finding*: a fresh recording that contained a key means the scenario script
   leaks one, and the write aborts rather than committing a placeholder.
2. **Real emails** — never laundered. Every scenario must use the reserved
   synthetic domain; any other `local@domain` substring raises, because a real
   address reaching Stripe test mode is the problem, not the cassette's bytes.
3. **Account ids** (`acct_…`) — replaced, and a finding: no Connect surface is
   routed, so none can legitimately appear.
4. **`request_log_url`** — a *known* allow-listed field, normalized to a fixed
   placeholder without a finding. It appears in every live error envelope,
   carries the dashboard account id, and its absence on this world's side is a
   declared `AllowedDifference`; normalizing kills the per-re-record churn the
   embedded request id would otherwise cause.

Request-Id and Idempotency-Key *headers* are never stored at all (the recorder
drops headers at capture), so no scrubber exists for them — a hygiene test
asserts the names never appear in a committed file.
"""

import re
from typing import Any

from conformance.cassette import Step

__all__ = ["RESERVED_EMAIL_DOMAIN", "RedactionError", "scrub"]

RESERVED_EMAIL_DOMAIN = "@conformance.stripeapi.invalid"

_API_KEY = re.compile(r"\b(?:sk|rk|pk)_(?:test|live)_[A-Za-z0-9]{10,}\b")
_ACCOUNT_ID = re.compile(r"\bacct_[A-Za-z0-9]{10,}\b")
# RFC-5322-ish: a local part, an @, and a dotted domain with a real TLD-ish
# tail. Deliberately loose — the guard's job is to raise on anything
# email-shaped that is not the reserved domain, not to parse email.
_EMAIL = re.compile(r"[\w.+-]+@([A-Za-z0-9-]+\.)+[A-Za-z]{2,}")

KEY_PLACEHOLDER = "<redacted:api_key>"
ACCOUNT_PLACEHOLDER = "<redacted:account_id>"
REQUEST_LOG_PLACEHOLDER = "<redacted:request_log_url>"
RECEIPT_URL_PLACEHOLDER = "<redacted:receipt_url>"


class RedactionError(Exception):
    """A non-synthetic email — the recording aborts; nothing is written."""


def scrub(step: Step) -> tuple[Step, list[str]]:
    """One step with every secret-bearing string leaf replaced, plus the
    findings (replacements that mean the scenario script itself leaked
    something). Raises `RedactionError` on a non-reserved email, naming the
    location."""
    findings: list[str] = []
    path = _scrub_string(step.path, "path", findings)
    path_refs = {
        name: ref for name, ref in step.path_refs.items()
    }  # Refs hold step names and field paths only, never wire values
    params = _scrub_value(step.params, "params", findings)
    body = _scrub_value(step.recorded_body, "recorded_body", findings)
    return (
        Step(
            seq=step.seq,
            method=step.method,
            path=path,
            path_refs=path_refs,
            params=params,
            idempotency_key=step.idempotency_key,
            binds_as=step.binds_as,
            recorded_status=step.recorded_status,
            recorded_body=body,
            recorded_stripe_version=step.recorded_stripe_version,
        ),
        findings,
    )


def _scrub_value(value: Any, where: str, findings: list[str]) -> Any:
    if isinstance(value, dict):
        return {key: _scrub_value(item, f"{where}.{key}", findings) for key, item in value.items()}
    if isinstance(value, list):
        return [
            _scrub_value(item, f"{where}[{index}]", findings) for index, item in enumerate(value)
        ]
    if isinstance(value, str):
        return _scrub_string(value, where, findings)
    return value


def _scrub_string(text: str, where: str, findings: list[str]) -> str:
    if where.endswith(".request_log_url"):
        # A known, declared field (see the module docstring): normalized, not
        # a finding, so ordinary error envelopes do not abort every recording.
        return REQUEST_LOG_PLACEHOLDER
    if where.endswith(".receipt_url"):
        # A charge receipt URL base64-embeds the dashboard account id inside
        # its signature segment (probed, Phase 8), which no regex in here can
        # see through — normalized like request_log_url rather than trusted.
        return RECEIPT_URL_PLACEHOLDER
    for pattern, placeholder, label in (
        (_API_KEY, KEY_PLACEHOLDER, "an API key"),
        (_ACCOUNT_ID, ACCOUNT_PLACEHOLDER, "an account id"),
    ):
        if pattern.search(text):
            findings.append(f"{where} contained {label}, replaced with {placeholder}")
            text = pattern.sub(placeholder, text)
    for match in _EMAIL.finditer(text):
        if not match.group(0).endswith(RESERVED_EMAIL_DOMAIN):
            raise RedactionError(
                f"{where} contains the non-synthetic email {match.group(0)!r}: every "
                f"email a scenario touches must use the reserved domain "
                f"{RESERVED_EMAIL_DOMAIN}"
            )
    return text


def scan_text(text: str) -> list[str]:
    """The committed-file scan the hygiene test runs: every hit is a failure.

    `request_log_url` placeholders contain neither keys nor account ids, so a
    scrubbed cassette scans clean; anything else does not.
    """
    hits: list[str] = []
    for pattern, label in ((_API_KEY, "an API key"), (_ACCOUNT_ID, "an account id")):
        for match in pattern.finditer(text):
            hits.append(f"{label}: {match.group(0)[:12]}…")
    for match in _EMAIL.finditer(text):
        if not match.group(0).endswith(RESERVED_EMAIL_DOMAIN):
            hits.append(f"a non-synthetic email: {match.group(0)}")
    return hits


def header_names_never_stored(text: str) -> list[str]:
    """`Request-Id` / `Idempotency-Key` in their header spellings. The
    snake_case `idempotency_key` *request field* is legitimate cassette
    content and does not match."""
    hits: list[str] = []
    for name in ("Request-Id", "Idempotency-Key"):
        if name in text:
            hits.append(name)
    return hits
