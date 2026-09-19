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

__all__ = ["Internal", "InvalidInput", "InvalidMethod", "InvalidSearchQuery", "UnknownOperation"]


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


# --- The discovery tools' two error conditions (`components/discovery.md` §2):
# an agent that mis-calls the catalogue has made an authoring mistake, not a
# Stripe request, so these are Seahaven errors rather than a Stripe envelope.


class InvalidSearchQuery(seahaven.ToolError):
    """A search query with no tokens after normalization."""

    def __init__(self, query: str) -> None:
        super().__init__(
            "INVALID_SEARCH_QUERY",
            f"a search query needs at least one keyword: {query!r}",
            {"query": query},
        )


class InvalidMethod(seahaven.ToolError):
    """A method outside the three verbs the routed surface serves.

    Defensive only: the registered tool's `Literal` annotation makes a bad
    verb an `ArgumentError` — restated as `INVALID_INPUT` — before the tool
    body runs, so this shape is unreachable through the tool and exists for
    the direct-call path alone.
    """

    def __init__(self, method: str) -> None:
        super().__init__(
            "INVALID_METHOD",
            f"method must be GET, POST or DELETE: {method!r}",
            {"method": method},
        )


class UnknownOperation(seahaven.ToolError):
    """A `(method, path)` that is not a routed operation — including a real
    Stripe path this world cut."""

    def __init__(self, method: str, path: str) -> None:
        super().__init__(
            "UNKNOWN_OPERATION",
            f"no routed operation for {method} {path}",
            {"method": method, "path": path},
        )
