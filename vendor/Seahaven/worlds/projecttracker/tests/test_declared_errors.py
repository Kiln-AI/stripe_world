"""`errors.py`: the shapes this product answers with, and the wire form of each.

These classes are the product's API as much as the tools are -- an eval asserts
on `code` and on `details` -- so each one's triple is pinned here rather than
left to the tools that raise it.
"""

import pytest

import seahaven
from projecttracker.errors import Conflict, Internal, InvalidInput, NotFound


@pytest.mark.parametrize("error_class", [Conflict, Internal, InvalidInput, NotFound])
def test_every_declared_error_is_a_tool_error(error_class: type) -> None:
    """A `ToolError` is what reaches an agent; a plain exception would be a bug."""
    assert issubclass(error_class, seahaven.ToolError)
    assert not issubclass(error_class, seahaven.WorldBug)


def test_not_found_names_the_kind_and_the_key() -> None:
    assert NotFound("issue", "ENG-12").to_dict() == {
        "code": "NOT_FOUND",
        "message": "issue ENG-12 not found",
        "details": {"kind": "issue", "key": "ENG-12"},
    }


def test_invalid_input_names_the_field() -> None:
    assert InvalidInput("email", "must contain @").to_dict() == {
        "code": "INVALID_INPUT",
        "message": "email: must contain @",
        "details": {"field": "email"},
    }


def test_conflict_carries_the_reason_and_no_details() -> None:
    assert Conflict("issue ENG-12 is already done").to_dict() == {
        "code": "CONFLICT",
        "message": "issue ENG-12 is already done",
        "details": None,
    }


def test_internal_says_nothing_about_what_went_wrong() -> None:
    """The default is deliberate: an agent gets no engine text and no traceback."""
    assert Internal().to_dict() == {
        "code": "INTERNAL",
        "message": "Something went wrong",
        "details": None,
    }
    assert Internal("The tracker is down for maintenance").message == (
        "The tracker is down for maintenance"
    )


def test_from_violations_reports_every_violation_in_one_error() -> None:
    """The framework collects them all so the agent can fix them in one turn."""
    error = InvalidInput.from_violations(
        [
            {"path": "email", "message": "must contain @", "type": "value_error"},
            {"path": "role", "message": "must be admin, member or viewer", "type": "literal_error"},
        ]
    )
    assert error.code == "INVALID_INPUT"
    assert error.details == {"field": "email"}
    assert error.message == ("email: must contain @; role: must be admin, member or viewer")


def test_from_violations_on_one_violation_reads_like_a_hand_written_one() -> None:
    error = InvalidInput.from_violations([{"path": "title", "message": "required"}])
    assert error.message == "title: required"
    assert error.details == {"field": "title"}


def test_from_violations_survives_a_violation_with_no_path() -> None:
    """A violation with no path names the arguments, not the empty string.

    Nothing the framework raises today gets here: `Tool.validate` builds `path`
    from pydantic's `loc`, and every violation an argument model produces is
    located at a field -- an unknown argument included, whose `loc` is the name
    the caller sent. The branch exists because an `INVALID_INPUT` carrying
    `{"field": ""}` would be worse than useless to an agent, and this test is
    what says so.
    """
    error = InvalidInput.from_violations([{"path": "", "message": "not a valid model"}])
    assert error.details == {"field": "arguments"}


def test_from_violations_survives_a_violation_with_no_message() -> None:
    """A violation with no message still says something an agent can read.

    Same family as the empty path: the framework always sends one, and
    `"invalid"` is what the message reads rather than `None` if one day it does
    not.
    """
    error = InvalidInput.from_violations([{"path": "title"}])
    assert error.details == {"field": "title"}
    assert error.message == "title: invalid"


def test_from_violations_with_nothing_to_report_is_still_an_invalid_input() -> None:
    """Nothing raises this, and it must not be an `IndexError` when something does."""
    error = InvalidInput.from_violations([])
    assert error.code == "INVALID_INPUT"
    assert error.details == {"field": "arguments"}
