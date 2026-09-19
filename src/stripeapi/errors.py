"""This world's Seahaven error shapes: what it answers with when *authoring* went wrong.

Two error systems live in this package, deliberately not merged
(specs/projects/stripe_world/architecture.md §7):

- **Stripe errors** (`stripe_errors.py`) are the API's own responses — a `402`
  decline, a `404` missing resource — and they are *return values*, because an
  error a call legitimately earned must keep the rows it wrote.
- **These** are Seahaven `ToolError` subclasses for failures that are *not*
  Stripe responses: an unusable `method`, a malformed parameter object, a call
  that violates a tool's own contract. Raising is correct for them — the call
  should never have been accepted, and its writes (if any began) must go.

The framework's own codes (`invalid_arguments`, `db_error`, `unknown_tool`)
reach an agent only if the error handler lets them, which is what the handler
is for.
"""

from collections.abc import Sequence
from typing import Any

import seahaven

__all__ = ["Internal", "InvalidInput"]


class InvalidInput(seahaven.ToolError):
    """An argument this surface will not accept.

    The tool-contract violations functional spec §2.3 reserves for Seahaven's
    declared-error mechanism: a write verb sent to the read tool, a non-object
    `params`. Where Stripe's own parameter faults belong — an unknown
    parameter, a bad `limit` — those are Stripe errors and live in
    `stripe_errors.py`.
    """

    def __init__(self, field: str, why: str) -> None:
        super().__init__("INVALID_INPUT", f"{field}: {why}", {"field": field})

    @classmethod
    def from_violations(cls, violations: Sequence[dict[str, Any]]) -> InvalidInput:
        """One `INVALID_INPUT` carrying every violation the framework found.

        The framework reports all of an argument model's failures at once so the
        agent can fix them in one turn; restating them one per error would throw
        that away.
        """
        if not violations:
            return cls("arguments", "invalid")
        first, *rest = violations
        why = "; ".join(
            [str(first.get("message", "invalid"))]
            + [f"{v.get('path')}: {v.get('message')}" for v in rest]
        )
        return cls(str(first.get("path") or "arguments"), why)


class Internal(seahaven.ToolError):
    """Something failed in a way this product does not explain.

    The default message is the whole of what an agent learns: an engine message
    or a Python traceback is for this world's author and reaches the log instead.
    """

    def __init__(self, message: str = "Something went wrong") -> None:
        super().__init__("INTERNAL", message)
