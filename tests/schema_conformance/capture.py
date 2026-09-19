"""The hook's capture side: what every test's dispatcher calls returned.

The design's own mechanism for this hook was "read ``instance.call_log``"
(components/conformance.md, Public Interface) — but a Seahaven ``CallRecord``
carries ``tool``/``arguments``/``error`` and no result, so nothing a test got
back can be recovered from it. The hook therefore wraps
``seahaven.Instance.call`` for the test's lifetime and records the bodies
itself; logged as SEAHAVEN_FINDINGS.md Entry 9.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
import seahaven

from schema_conformance.validate import violations_in_body

__all__ = ["CapturedCall", "begin", "check"]

#: The tools whose results are HTTP responses — the two registered faces
#: plus the escape hatch. Deliberately the same set `stripe_envelope` applies
#: to; it is a private constant there, so `test_http_tool_names_match_the_
#: envelope_middleware` pins the two together and fails if either grows.
HTTP_TOOL_NAMES = frozenset(("stripe_api_read", "stripe_api_write", "call_stripe"))


@dataclass(frozen=True)
class CapturedCall:
    ordinal: int
    tool: str
    label: str  # "POST /v1/customers" or the bare tool name
    body: Any

    def source(self) -> str:
        return " ".join(part for part in (f"call #{self.ordinal}", self.tool, self.label) if part)


def begin(monkeypatch: pytest.MonkeyPatch) -> list[CapturedCall]:
    """Record every HTTP-shaped tool result for the rest of this test."""
    captured: list[CapturedCall] = []
    original = seahaven.Instance.call

    def call(self: seahaven.Instance, tool: Any, /, *args: Any, **arguments: Any) -> Any:
        result = original(self, tool, *args, **arguments)
        if (
            isinstance(tool, str)
            and tool in HTTP_TOOL_NAMES
            and isinstance(result, dict)
            and "status" in result
            and "body" in result
        ):
            method = arguments.get("method")
            path = arguments.get("path")
            label = (
                f"{method} {path}"
                if isinstance(method, str) and isinstance(path, str)
                else path
                if isinstance(path, str)
                else ""
            )
            captured.append(
                CapturedCall(ordinal=len(captured) + 1, tool=tool, label=label, body=result["body"])
            )
        return result

    monkeypatch.setattr(seahaven.Instance, "call", call)
    return captured


def check(captured: list[CapturedCall]) -> None:
    """Validate every captured body; raise with the whole picture, not the
    first violation — a broken serializer usually produces several."""
    lines: list[str] = []
    for call in captured:
        violations = violations_in_body(call.body, source=call.source())
        if violations:
            lines.extend(f"  {violation.line()}" for violation in violations)
    if lines:
        raise AssertionError(
            "schema conformance: this test returned object(s) that do not match the "
            f"pinned spec ({len(lines)} violation(s)).\n" + "\n".join(lines)
        )
