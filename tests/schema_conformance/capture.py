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

#: The tools whose results are HTTP responses: the two MCP faces
#: (which return bare body on success) and the raw-HTTP escape hatch
#: (which wraps in ``{status, body, headers}``).
HTTP_TOOL_NAMES = frozenset(("stripe_api_read", "stripe_api_write", "call_stripe"))

#: The raw-HTTP face keeps ``{status, body, headers}``; the MCP faces
#: return the body directly on success.
_RAW_TOOLS = frozenset(("call_stripe",))


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
        if isinstance(tool, str) and tool in HTTP_TOOL_NAMES and isinstance(result, dict):
            # Two shapes: raw-HTTP ``{status, body, headers}`` from call_stripe,
            # and bare body from the MCP tools.
            if tool in _RAW_TOOLS and "status" in result and "body" in result:
                body = result["body"]
            elif tool not in _RAW_TOOLS:
                body = result
            else:
                return result
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
                CapturedCall(ordinal=len(captured) + 1, tool=tool, label=label, body=body)
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
