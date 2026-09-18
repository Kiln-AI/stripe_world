"""The error hierarchy: what each type says, and who it is written for."""

import pytest

from seahaven.errors import (
    ArgumentError,
    DbError,
    SeahavenError,
    ToolError,
    ToolErrorType,
    UnknownTool,
    WorldBug,
)


class NotFound(ToolError):
    """A world's own error, as `seahaven new` scaffolds them."""

    def __init__(self, kind: str, key: str) -> None:
        super().__init__("not_found", f"no such {kind}: {key}", {"kind": kind, "key": key})


def test_tool_error_carries_the_wire_shape() -> None:
    error = ToolError("rate_limited", "too many requests", {"retry_after": 30})

    assert error.to_dict() == {
        "code": "rate_limited",
        "message": "too many requests",
        "details": {"retry_after": 30},
    }
    assert str(error) == "too many requests"
    assert repr(error) == (
        "ToolError(code='rate_limited', message='too many requests', details={'retry_after': 30})"
    )


def test_details_default_to_none() -> None:
    assert ToolError("nope", "no").to_dict() == {"code": "nope", "message": "no", "details": None}


def test_argument_error_lists_every_violation() -> None:
    violations = [
        {"path": "title", "message": "field required", "type": "missing"},
        {"path": "count", "message": "not an integer", "type": "int_type"},
    ]

    error = ArgumentError("create_issue", violations)

    assert error.code == "invalid_arguments"
    assert error.message == ("invalid arguments: title: field required; count: not an integer")
    assert error.tool == "create_issue"
    assert error.violations == violations
    assert error.to_dict()["details"] == {"tool": "create_issue", "violations": violations}


def test_db_error_hides_sqlite_text_behind_a_flat_message() -> None:
    error = DbError("no such column: foo", 267)

    assert error.code == "db_error"
    assert error.message == "database error"
    assert "foo" not in error.message
    assert error.sqlite_message == "no such column: foo"
    assert error.sqlite_code == 267
    assert error.refusals == ()
    assert error.to_dict()["details"] is None


def test_db_error_names_the_first_refusal() -> None:
    error = DbError("not authorized", None, ("read of table 'audit'", "function 'random'"))

    assert error.message == "not allowed: read of table 'audit'"
    assert error.to_dict()["details"] == {
        "refusals": ["read of table 'audit'", "function 'random'"]
    }


def test_unknown_tool_names_the_tool() -> None:
    error = UnknownTool("create_issue")

    assert (error.code, error.message, error.name) == (
        "unknown_tool",
        "unknown tool: create_issue",
        "create_issue",
    )


def test_a_world_error_fixes_its_own_code() -> None:
    with pytest.raises(ToolError) as raised:
        raise NotFound("issue", "ENG-1")

    assert raised.value.code == "not_found"
    assert raised.value.to_dict()["details"] == {"kind": "issue", "key": "ENG-1"}
    assert repr(raised.value).startswith("NotFound(code='not_found'")


def test_the_two_branches_of_the_hierarchy_are_siblings() -> None:
    assert issubclass(ToolError, SeahavenError)
    assert issubclass(WorldBug, SeahavenError)
    assert not issubclass(WorldBug, ToolError)
    assert not issubclass(ToolError, WorldBug)

    for framework_error in (ArgumentError, DbError, UnknownTool):
        assert issubclass(framework_error, ToolError)


# --- the category the wire publishes ---------------------------------------


def test_a_world_error_and_a_db_error_are_execution_errors() -> None:
    """The default, and what it is for.

    `TOOL_NOT_FOUND` and `INVALID_ARGS` describe the call. A world's `not_found`
    describes the domain: the tool exists, the arguments were fine, and the issue
    is not there. Mapping it to `TOOL_NOT_FOUND` would tell a harness the tool
    does not exist, which is a worse answer than a coarse one.
    """
    assert NotFound("issue", "ENG-1").error_type is ToolErrorType.EXECUTION_ERROR
    assert DbError("no such column").error_type is ToolErrorType.EXECUTION_ERROR
    assert ToolError("nope", "no").error_type is ToolErrorType.EXECUTION_ERROR


def test_the_two_framework_errors_that_describe_the_call_say_so() -> None:
    assert UnknownTool("rows").error_type is ToolErrorType.TOOL_NOT_FOUND
    assert ArgumentError("rows", []).error_type is ToolErrorType.INVALID_ARGS


def test_seahaven_never_publishes_a_timeout_or_a_transport_error() -> None:
    """Seahaven does not bound a call, and the transport is OpenEnv's."""
    assert {member.value for member in ToolErrorType} == {
        "execution_error",
        "invalid_args",
        "tool_not_found",
    }


def test_a_world_cannot_choose_its_own_error_type() -> None:
    """Framework-only, and refused where it is written rather than ignored later."""
    with pytest.raises(WorldBug, match="error_type"):

        class Impostor(ToolError):
            error_type = ToolErrorType.TOOL_NOT_FOUND

    class Ordinary(ToolError):
        """A world's error, which sets a code and inherits the category."""

    assert Ordinary("not_found", "no").error_type is ToolErrorType.EXECUTION_ERROR
