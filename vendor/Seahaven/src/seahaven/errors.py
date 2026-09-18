"""Everything Seahaven raises.

Two kinds of failure, and the split is who the failure is for. A `ToolError` is
written for the agent: it has a code, a message and details, it is rendered onto
the observation over OpenEnv, and a world's own errors subclass it. A `WorldBug`
is written for the author: a registration mistake, a call on a destroyed
instance, a result that will not serialise. An agent never sees one.

Anything else a tool raises is neither: the world did not plan for it, so the
agent is answered with the generic error this module fixes and the real
exception goes to the log. The three classes are named here because every wire
boundary sorts by them -- `seahaven/openenv/env.py` for an observation and an
error frame, `CallRecord.to_dict` for the state document.
"""

from enum import StrEnum
from typing import Any, ClassVar

__all__ = [
    "INTERNAL_ERROR_CODE",
    "INTERNAL_ERROR_MESSAGE",
    "ArgumentError",
    "DbError",
    "SeahavenError",
    "ToolError",
    "ToolErrorType",
    "UnknownTool",
    "WorldBug",
]

# What an agent is told when a tool failed in a way nobody wrote down. The code
# and the message are fixed and say nothing: the real failure is in the server's
# log, with its traceback, and an eval that grades on error text must not be able
# to read a stack frame out of one. The same wording answers a `WorldBug` on an
# error frame and stands in for both classes in the state document, so one string
# is what a reader learns to recognise.
INTERNAL_ERROR_CODE = "internal"
INTERNAL_ERROR_MESSAGE = "internal error"


class ToolErrorType(StrEnum):
    """The category an error is published under: OpenEnv's `ToolErrorType`.

    Spelled here rather than imported, because `openenv` is the `serve` extra and
    the framework's errors are core. The values match upstream's member for
    member (`openenv/core/env_server/mcp_types.py` line 198 in openenv 0.5.0), so
    the boundary converts one to the other by value.

    Two of upstream's five are missing on purpose. `TIMEOUT` is never Seahaven's,
    because Seahaven does not bound a call, and `TRANSPORT_ERROR` is OpenEnv's own
    to raise.
    """

    EXECUTION_ERROR = "execution_error"
    INVALID_ARGS = "invalid_args"
    TOOL_NOT_FOUND = "tool_not_found"


class SeahavenError(Exception):
    """The root of the hierarchy: anything Seahaven raises."""


class WorldBug(SeahavenError):
    """Framework misuse, or a bug in world code. Never shown to an agent."""


class ToolError(SeahavenError):
    """A failure the agent is meant to read.

    `code` is the world's vocabulary (`"not_found"`, `"permission_denied"`), free
    apart from the three the framework fixes below. `details` is anything
    JSON-serialisable the agent can act on.

    `error_type` is the framework's, not the world's. It is the coarse category
    OpenEnv publishes beside the message, and the two narrow values describe *the
    call*: `TOOL_NOT_FOUND` says the tool does not exist and `INVALID_ARGS` says
    the arguments did not fit its schema. Neither describes the domain, so a
    world's `not_found` for a missing issue is an `EXECUTION_ERROR` -- telling a
    harness the tool does not exist is a worse answer than a coarse one. That is
    why a world cannot set it and `__init_subclass__` below refuses the attempt.
    """

    error_type: ClassVar[ToolErrorType] = ToolErrorType.EXECUTION_ERROR

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Refuse a subclass outside this module that declares its own `error_type`.

        The category is framework-only by decision, and a rule that is only
        written down is a rule that drifts. Opening this later is one deleted
        check; closing it after a world has shipped an error type of its own is
        not.
        """
        super().__init_subclass__(**kwargs)
        if "error_type" in cls.__dict__ and cls.__module__ != __name__:
            raise WorldBug(
                f"{cls.__module__}.{cls.__qualname__} sets error_type; it is the framework's "
                f"category for the call and a world does not choose it. Use `code` for this "
                f"world's own vocabulary: it reaches the agent whole."
            )

    def __init__(self, code: str, message: str, details: Any = None) -> None:
        # The message, not the triple, so that str(e) and a traceback read as prose.
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(code={self.code!r}, message={self.message!r}, "
            f"details={self.details!r})"
        )

    def to_dict(self) -> dict[str, Any]:
        """The Seahaven triple, the same in process and over the wire.

        Over OpenEnv it travels in `metadata["seahaven_error"]`, because
        `error` itself carries upstream's `{error_type, message}` and forbids
        anything else (`seahaven/openenv/env.py`).
        """
        return {"code": self.code, "message": self.message, "details": self.details}


class ArgumentError(ToolError):
    """Arguments that did not validate against the tool's argument model."""

    error_type: ClassVar[ToolErrorType] = ToolErrorType.INVALID_ARGS

    def __init__(self, tool: str, violations: list[dict[str, str]]) -> None:
        super().__init__(
            "invalid_arguments",
            "invalid arguments: " + "; ".join(f"{v['path']}: {v['message']}" for v in violations),
            {"tool": tool, "violations": violations},
        )
        self.tool = tool
        self.violations = violations


class DbError(ToolError):
    """A SQLite failure, wrapped once at `Db` so world code catches one type.

    SQLite's own text is carried but is never the message: engine text reaches an
    agent only when a world's handler or a helper chooses to include it.
    """

    def __init__(
        self,
        sqlite_message: str,
        sqlite_code: int | None = None,
        refusals: tuple[str, ...] = (),
        *,
        message: str | None = None,
    ) -> None:
        # `message=` is how a helper "explicitly does" include engine text
        # (`world_and_dispatch.md` §5): a SQL door mimics a product whose error
        # text *is* SQLite's, so `run_sql` and `controller_run_sql` pass
        # `sqlite_message` here. Nothing else does, and the default is unchanged.
        if message is None:
            message = f"not allowed: {refusals[0]}" if refusals else "database error"
        super().__init__("db_error", message, {"refusals": list(refusals)} if refusals else None)
        self.sqlite_message = sqlite_message
        self.sqlite_code = sqlite_code
        self.refusals = refusals


class UnknownTool(ToolError):
    """A call naming a tool the world does not have."""

    error_type: ClassVar[ToolErrorType] = ToolErrorType.TOOL_NOT_FOUND

    def __init__(self, name: str) -> None:
        super().__init__("unknown_tool", f"unknown tool: {name}", {"name": name})
        self.name = name
