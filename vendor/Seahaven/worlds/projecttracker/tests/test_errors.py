"""What an agent is allowed to be told, over the whole tool surface.

`test_declared_errors.py` pins the four shapes and `test_error_handler.py` pins
the mapping. This module is the claim that covers the *world*: every refusal an
agent can provoke from any of the twenty-five tools is one of this product's four
codes, and none of them carries SQLite's words -- except through `run_sql`, which
is a SQL console and whose errors are SQL errors by design.

It is written as a sweep rather than as one test per tool because the property is
about the surface as a whole. A twenty-sixth tool that raised `DbError` for a
missing parent would be caught here without anyone remembering to add a case.
"""

import base64
import json
import logging
from typing import Any

import pytest

import seahaven
from conftest import BLANK_NOW, Scaffold, an_issue
from projecttracker.errors import Conflict, Internal, InvalidInput, NotFound
from projecttracker.middleware.error_handler import SQL_DOOR_TOOLS
from projecttracker.world import world

pytestmark = pytest.mark.seahaven(fixture=None, now=BLANK_NOW)

# The four codes this product answers with, and the whole of what an agent reads.
DECLARED = {"NOT_FOUND", "INVALID_INPUT", "CONFLICT", "INTERNAL"}

# Words that would only be in a message because SQLite put them there. A message
# carrying one of these has leaked the engine through a door that is not the SQL
# door.
ENGINE_WORDS = (
    "sqlite",
    "no such table",
    "no such column",
    "constraint failed",
    "UNIQUE",
    "FOREIGN KEY",
    "CHECK",
    "syntax error",
    "datatype mismatch",
)


def _cursor(halves: list[Any]) -> str:
    """A structurally valid cursor for `list_issues` carrying `halves`.

    The two halves are what the cursor puts in front of SQLite as parameters, so
    a cursor is how an agent gets an arbitrary JSON value into a binding. The
    sort key is the real one, so the cursor gets past the ordering check and the
    value itself is what is being tested.
    """
    payload = json.dumps([*halves, "issues:created_at_desc"], separators=(",", ":"))
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii")


def refusals(instance: seahaven.Instance, scaffold: Scaffold) -> list[tuple[str, Any]]:
    """Every way this suite knows to make a tool refuse, one per tool that can.

    Built from a live scaffold, so the ids in it are real and the calls fail for
    the reason named rather than for a missing parent nobody meant to test.
    """
    issue = an_issue(instance, scaffold)
    archived = an_issue(instance, scaffold, title="Archived")
    instance.call("archive_issue", issue_id=archived["id"])
    closed = an_issue(instance, scaffold, title="Closed", status="done")
    return [
        ("create_user", {"email": "not an address", "name": "A"}),
        ("create_user", {"email": "ada@tracker.invalid", "name": "A"}),
        ("get_user", {"user_id": "nobody"}),
        ("list_users", {"role": "owner"}),
        ("list_users", {"cursor": "nonsense"}),
        ("list_issues", {"cursor": _cursor([{"a": 1}, "x"])}),
        ("list_issues", {"cursor": _cursor([2**80, "x"])}),
        ("create_team", {"key": "eng", "name": "E"}),
        ("create_team", {"key": "ENG", "name": "E"}),
        ("get_team", {"key": "OPS"}),
        ("list_teams", {"limit": 0}),
        ("add_team_member", {"team_id": "nobody", "user_id": scaffold.admin}),
        ("list_team_members", {"team_id": "nobody"}),
        ("create_project", {"team_id": "nobody", "name": "P"}),
        ("get_project", {"project_id": "nowhere"}),
        ("list_projects", {"state": "shipped"}),
        ("update_project", {"project_id": scaffold.project}),
        ("create_label", {"team_id": scaffold.team, "name": "bug", "color": "#e11d48"}),
        ("create_label", {"team_id": scaffold.team, "name": "new", "color": "chartreuse"}),
        ("list_labels", {"team_id": "nobody"}),
        ("set_issue_labels", {"issue_id": issue["id"], "label_ids": ["nothing"]}),
        ("create_issue", {"project_id": "nowhere", "title": "T", "actor_id": scaffold.admin}),
        ("create_issue", {"project_id": scaffold.project, "title": "T"}),
        ("get_issue", {}),
        ("get_issue", {"key": "ENG-999"}),
        ("list_issues", {"order": "priority_desc"}),
        ("update_issue", {"issue_id": archived["id"], "title": "T", "actor_id": scaffold.admin}),
        (
            "assign_issue",
            {
                "issue_id": closed["id"],
                "assignee_id": scaffold.member,
                "actor_id": scaffold.admin,
            },
        ),
        ("transition_issue", {"issue_id": "nowhere", "status": "done", "actor_id": scaffold.admin}),
        ("archive_issue", {"issue_id": archived["id"]}),
        ("add_comment", {"issue_id": "nowhere", "body": "B", "actor_id": scaffold.admin}),
        ("list_comments", {"issue_id": "nowhere"}),
        ("search_issues", {"query": '"'}),
    ]


def test_every_refusal_an_agent_can_provoke_is_one_of_this_products_four_codes(
    instance: seahaven.Instance, scaffold: Scaffold
) -> None:
    for tool, arguments in refusals(instance, scaffold):
        with pytest.raises(seahaven.ToolError) as raised:
            instance.call(tool, **arguments)
        assert raised.value.code in DECLARED, f"{tool}{arguments} answered {raised.value.code}"
        assert isinstance(raised.value, Conflict | Internal | InvalidInput | NotFound)


def test_no_sqlite_text_escapes_through_any_tool_but_the_sql_door(
    instance: seahaven.Instance, scaffold: Scaffold
) -> None:
    """The claim the error handler exists to make, checked over the whole surface."""
    for tool, arguments in refusals(instance, scaffold):
        with pytest.raises(seahaven.ToolError) as raised:
            instance.call(tool, **arguments)
        rendered = repr(raised.value.to_dict())
        for word in ENGINE_WORDS:
            assert word.lower() not in rendered.lower(), f"{tool} leaked {word!r}: {rendered}"


def test_nothing_an_agent_can_provoke_reaches_the_authors_log(
    instance: seahaven.Instance, scaffold: Scaffold, caplog: pytest.LogCaptureFixture
) -> None:
    """A refusal is not a bug, and the log is where this world reports its bugs.

    The sweep above cannot see this. `INTERNAL` is one of the four declared codes,
    so a call that answered `INTERNAL` would pass it -- and `INTERNAL` in this
    world means "a bug in world code", which `error_handler` and `seahaven.call`
    both write to the log with a traceback. An agent that can make the world
    accuse itself has found a defect whatever code reaches it, and the only thing
    that shows it is the log being empty.
    """
    for tool, arguments in refusals(instance, scaffold):
        caplog.clear()
        with caplog.at_level(logging.ERROR), pytest.raises(seahaven.ToolError):
            instance.call(tool, **arguments)
        assert caplog.records == [], (
            f"{tool}{arguments} logged {[record.getMessage() for record in caplog.records]}"
        )


def test_the_search_door_is_the_one_tool_that_quotes_the_engine_and_says_whose_fault_it_is(
    instance: seahaven.Instance, scaffold: Scaffold
) -> None:
    """FTS5's complaint reaches the agent, on the field the query came in on.

    It is engine text and it is deliberate: the agent wrote the query, so the
    parser is the only thing that can say what is wrong with it. What the sweep
    above checks is that it arrives as `INVALID_INPUT` and not as a `db_error`,
    and that no *other* tool does this.
    """
    with pytest.raises(seahaven.ToolError) as raised:
        instance.call("search_issues", query='"')
    assert raised.value.code == "INVALID_INPUT"
    assert raised.value.message == "query: unterminated string"


def test_the_sql_door_is_the_one_tool_that_answers_with_sqlites_own_code(
    instance: seahaven.Instance,
) -> None:
    """`run_sql` is outside the sweep because it is the declared exception."""
    assert sorted(SQL_DOOR_TOOLS) == ["run_sql"]
    with pytest.raises(seahaven.DbError) as raised:
        instance.call("run_sql", query="SELCT 1")
    assert raised.value.code == "db_error"
    assert "syntax error" in raised.value.message


def test_a_tool_this_world_does_not_have_is_the_frameworks_own_answer(
    instance: seahaven.Instance,
) -> None:
    """`unknown_tool` is raised before the chain, so the handler never sees it.

    It is the one framework code an agent can read, and it is right that it can:
    the answer to "that tool does not exist" is the tool list, not this product's
    vocabulary.
    """
    with pytest.raises(seahaven.UnknownTool) as raised:
        instance.call("close_sprint")
    assert raised.value.to_dict() == {
        "code": "unknown_tool",
        "message": "unknown tool: close_sprint",
        "details": {"name": "close_sprint"},
    }


def test_every_tool_an_agent_can_see_has_something_for_it_to_read(
    instance: seahaven.Instance,
) -> None:
    """SH205 as a test: a description is how an agent decides whether to call a tool."""
    listing = instance.tools()
    assert len(listing) == 27
    for tool in listing:
        assert tool["description"].strip(), tool["name"]
        assert tool["input_schema"]["additionalProperties"] is False


def test_the_control_tool_is_not_on_the_list_an_agent_reads(
    instance: seahaven.Instance,
) -> None:
    assert "controller_run_sql" in world.tools
    assert "controller_run_sql" not in {tool["name"] for tool in instance.tools()}
