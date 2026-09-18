"""ProjectTracker's error shapes: what this product answers with when it refuses.

A world declares the errors the real product returns as ordinary `ToolError`
subclasses and raises them where they happen; nothing about them is registered
on the `World`. Each class fixes its own code and message format here, so the
wording is in one place and a tool module raises it by name.

The codes are this product's vocabulary, not the framework's: `SCREAMING_SNAKE`
because that is what this (fictional) tracker's API has always returned, where
the framework's own three codes (`invalid_arguments`, `db_error`,
`unknown_tool`) are lower case. An agent sees these; the framework's reach it
only if the error handler lets them, which is what the handler is for.
"""

from collections.abc import Sequence
from typing import Any

import seahaven

__all__ = ["Conflict", "Internal", "InvalidInput", "NotFound"]


class NotFound(seahaven.ToolError):
    """A record the caller named does not exist."""

    def __init__(self, kind: str, key: str) -> None:
        super().__init__("NOT_FOUND", f"{kind} {key} not found", {"kind": kind, "key": key})


class InvalidInput(seahaven.ToolError):
    """An argument this product will not accept."""

    def __init__(self, field: str, why: str) -> None:
        super().__init__("INVALID_INPUT", f"{field}: {why}", {"field": field})

    @classmethod
    def from_violations(cls, violations: Sequence[dict[str, Any]]) -> InvalidInput:
        """One `INVALID_INPUT` carrying every violation the framework found.

        The framework reports all of an argument model's failures at once so the
        agent can fix them in one turn; restating them one per error would throw
        that away. `details` names a single field, because that is the shape
        every `INVALID_INPUT` this product returns has, and the message reads
        `first_field: why; other_field: why` -- the first violation's field is
        the one `__init__` puts in front, so only the rest carry their own.

        A violation with no path would be pydantic reporting the model rather
        than one of its fields; `arguments` is the field then, because
        `{"field": ""}` would tell an agent nothing. Nothing the framework
        raises today takes that branch -- `Tool.validate` builds `path` from
        pydantic's `loc`, and every violation a generated argument model
        produces is located at a field, an unknown argument included (its
        `loc` is the name the caller sent). It is here for the same reason as
        the empty-list branch above: a `dict.get` that can return nothing
        should say what it does then, in one place, rather than reach an agent
        as `{"field": ""}`.
        """
        if not violations:
            # Nothing raises `ArgumentError` with an empty list, and a caller who
            # builds one by hand should get an error, not an `IndexError`.
            return cls("arguments", "invalid")
        first, *rest = violations
        why = "; ".join(
            [str(first.get("message", "invalid"))]
            + [f"{v.get('path')}: {v.get('message')}" for v in rest]
        )
        return cls(str(first.get("path") or "arguments"), why)


class Conflict(seahaven.ToolError):
    """The request is well formed but contradicts the state of the tracker."""

    def __init__(self, why: str) -> None:
        super().__init__("CONFLICT", why)


class Internal(seahaven.ToolError):
    """Something failed in a way this product does not explain.

    The default message is the whole of what an agent learns: an engine message
    or a Python traceback is for the world's author, and reaches the log rather
    than the agent.
    """

    def __init__(self, message: str = "Something went wrong") -> None:
        super().__init__("INTERNAL", message)
