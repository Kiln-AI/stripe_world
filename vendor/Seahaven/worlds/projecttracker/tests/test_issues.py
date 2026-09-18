"""The seven issue tools: keys, the trail, the closed-issue rule, archiving, filters."""

from typing import Any

import pytest

import seahaven
from conftest import BLANK_NOW, SMALL_STARTUP, Scaffold, an_issue

pytestmark = pytest.mark.seahaven(fixture=None, now=BLANK_NOW)


def events(instance: seahaven.Instance, issue_id: str) -> list[dict[str, Any]]:
    """The issue's trail, oldest first, as `issue_events` holds it.

    Read through `inspect()` rather than through a tool, because this world has
    no tool that lists events: the trail is what an eval grades on, and it
    reaches an eval through `run_sql`, the change log, or a read like this one.

    `created_at, rowid`, and the second half is load bearing. An instance's clock
    does not move, so three events written by three calls in one test all carry
    the same instant and the timestamp orders none of them; SQLite's insertion
    order does, and it is what "then this happened" means inside one session. A
    fixture built over a span is ordered by the timestamp and the rowid never
    comes into it.
    """
    return instance.inspect().rows(
        "SELECT kind, payload, actor_id FROM issue_events WHERE issue_id = ?"
        " ORDER BY created_at, rowid",
        issue_id,
    )


def test_creating_an_issue_mints_the_teams_next_key_and_records_it(
    instance: seahaven.Instance, scaffold: Scaffold
) -> None:
    issue = an_issue(instance, scaffold)
    assert issue == {
        "id": issue["id"],
        "project_id": scaffold.project,
        "key": "ENG-1",
        "title": "Login page is broken",
        "description": "Users cannot sign in with SSO",
        "status": "backlog",
        "priority": 0,
        "assignee_id": None,
        "creator_id": scaffold.admin,
        "created_at": BLANK_NOW,
        "updated_at": BLANK_NOW,
        "due_at": None,
        "archived_at": None,
        "label_ids": [],
    }
    assert [event["kind"] for event in events(instance, issue["id"])] == ["created"]


def test_the_key_counter_is_the_teams_and_not_the_projects(
    instance: seahaven.Instance, scaffold: Scaffold
) -> None:
    """`ENG-2` is the team's second issue, wherever it was filed.

    A per-project counter would mint `ENG-1` in both projects, and `issues.key`
    is UNIQUE, so the second call would fail outright.
    """
    second_project = instance.call("create_project", team_id=scaffold.team, name="Mobile")
    first = an_issue(instance, scaffold)
    elsewhere = an_issue(instance, scaffold, project_id=second_project["id"])
    assert [first["key"], elsewhere["key"]] == ["ENG-1", "ENG-2"]


def test_two_teams_mint_their_own_keys(instance: seahaven.Instance, scaffold: Scaffold) -> None:
    other = instance.call("create_team", key="DES", name="Design")
    theirs = instance.call("create_project", team_id=other["id"], name="Brand")
    assert an_issue(instance, scaffold)["key"] == "ENG-1"
    assert an_issue(instance, scaffold, project_id=theirs["id"])["key"] == "DES-1"
    assert an_issue(instance, scaffold)["key"] == "ENG-2"


def test_an_issue_in_a_project_that_does_not_exist_is_not_found(
    instance: seahaven.Instance, scaffold: Scaffold
) -> None:
    with pytest.raises(seahaven.ToolError) as raised:
        an_issue(instance, scaffold, project_id="nowhere")
    assert raised.value.to_dict()["details"] == {"kind": "project", "key": "nowhere"}


def test_an_issue_is_fetched_by_id_or_by_key(
    instance: seahaven.Instance, scaffold: Scaffold
) -> None:
    issue = an_issue(instance, scaffold)
    assert instance.call("get_issue", issue_id=issue["id"]) == issue
    assert instance.call("get_issue", key="ENG-1") == issue


@pytest.mark.parametrize(
    "arguments",
    [
        pytest.param({}, id="neither"),
        pytest.param({"issue_id": "a", "key": "ENG-1"}, id="both"),
    ],
)
def test_get_issue_wants_exactly_one_of_the_two(
    instance: seahaven.Instance, arguments: dict[str, str]
) -> None:
    with pytest.raises(seahaven.ToolError) as raised:
        instance.call("get_issue", **arguments)
    assert raised.value.code == "INVALID_INPUT"
    assert raised.value.details == {"field": "issue_id"}


@pytest.mark.parametrize(
    "arguments", [{"issue_id": "nowhere"}, {"key": "ENG-99"}], ids=["by id", "by key"]
)
def test_an_issue_that_is_not_there_is_not_found_either_way(
    instance: seahaven.Instance, arguments: dict[str, str]
) -> None:
    with pytest.raises(seahaven.ToolError) as raised:
        instance.call("get_issue", **arguments)
    assert raised.value.to_dict()["details"]["kind"] == "issue"


def test_an_update_changes_what_it_names_and_nothing_else(
    instance: seahaven.Instance, scaffold: Scaffold
) -> None:
    issue = an_issue(instance, scaffold)
    changed = instance.call(
        "update_issue",
        issue_id=issue["id"],
        title="SSO sign-in fails",
        priority=3,
        actor_id=scaffold.admin,
    )
    assert changed == issue | {"title": "SSO sign-in fails", "priority": 3}
    # Neither field is a status or an assignee, so the trail has only the creation.
    assert [event["kind"] for event in events(instance, issue["id"])] == ["created"]


def test_an_update_that_names_nothing_is_refused(
    instance: seahaven.Instance, scaffold: Scaffold
) -> None:
    issue = an_issue(instance, scaffold)
    with pytest.raises(seahaven.ToolError) as raised:
        instance.call("update_issue", issue_id=issue["id"], actor_id=scaffold.admin)
    assert raised.value.code == "INVALID_INPUT"


def test_assigning_an_issue_records_who_it_went_to(
    instance: seahaven.Instance, scaffold: Scaffold
) -> None:
    issue = an_issue(instance, scaffold)
    assigned = instance.call(
        "assign_issue",
        issue_id=issue["id"],
        assignee_id=scaffold.member,
        actor_id=scaffold.admin,
    )
    assert assigned["assignee_id"] == scaffold.member
    (_created, assignment) = events(instance, issue["id"])
    assert assignment["kind"] == "assignee"
    assert assignment["payload"] == f'{{"from":null,"to":"{scaffold.member}"}}'
    assert assignment["actor_id"] == scaffold.admin


def test_an_issue_can_be_assigned_to_nobody(
    instance: seahaven.Instance, scaffold: Scaffold
) -> None:
    """`update_issue` cannot unassign -- an argument left out is left alone -- so this can."""
    issue = an_issue(instance, scaffold, assignee_id=scaffold.member)
    cleared = instance.call(
        "assign_issue", issue_id=issue["id"], assignee_id=None, actor_id=scaffold.admin
    )
    assert cleared["assignee_id"] is None


def test_assigning_to_someone_who_is_not_in_the_workspace_is_not_found(
    instance: seahaven.Instance, scaffold: Scaffold
) -> None:
    issue = an_issue(instance, scaffold)
    with pytest.raises(seahaven.ToolError) as raised:
        instance.call(
            "assign_issue", issue_id=issue["id"], assignee_id="nobody", actor_id=scaffold.admin
        )
    assert raised.value.to_dict()["details"] == {"kind": "user", "key": "nobody"}


def test_a_transition_records_where_the_issue_came_from(
    instance: seahaven.Instance, scaffold: Scaffold
) -> None:
    issue = an_issue(instance, scaffold)
    moved = instance.call(
        "transition_issue", issue_id=issue["id"], status="in_progress", actor_id=scaffold.admin
    )
    assert moved["status"] == "in_progress"
    (_created, transition) = events(instance, issue["id"])
    assert transition["kind"] == "status"
    assert transition["payload"] == '{"from":"backlog","to":"in_progress"}'


def test_a_transition_to_the_status_it_already_has_changes_nothing(
    instance: seahaven.Instance, scaffold: Scaffold
) -> None:
    """A no-op write would put a lie in the trail and a record in the change log."""
    issue = an_issue(instance, scaffold)
    same = instance.call(
        "transition_issue", issue_id=issue["id"], status="backlog", actor_id=scaffold.admin
    )
    assert same == issue
    assert [event["kind"] for event in events(instance, issue["id"])] == ["created"]


@pytest.mark.parametrize("status", ["done", "canceled"])
def test_closing_an_issue_drops_its_assignee_and_says_so(
    instance: seahaven.Instance, scaffold: Scaffold, status: str
) -> None:
    issue = an_issue(instance, scaffold, assignee_id=scaffold.member)
    closed = instance.call(
        "transition_issue", issue_id=issue["id"], status=status, actor_id=scaffold.admin
    )
    assert (closed["status"], closed["assignee_id"]) == (status, None)
    # The creation carried the assignee in its own payload, so the trail is the
    # creation, the transition, and the drop the transition caused.
    assert [event["kind"] for event in events(instance, issue["id"])] == [
        "created",
        "status",
        "assignee",
    ]
    assert events(instance, issue["id"])[-1]["payload"] == (
        f'{{"from":"{scaffold.member}","to":null}}'
    )


@pytest.mark.parametrize("status", ["done", "canceled"])
def test_a_closed_issue_cannot_be_assigned(
    instance: seahaven.Instance, scaffold: Scaffold, status: str
) -> None:
    issue = an_issue(instance, scaffold, status=status)
    with pytest.raises(seahaven.ToolError) as raised:
        instance.call(
            "assign_issue",
            issue_id=issue["id"],
            assignee_id=scaffold.member,
            actor_id=scaffold.admin,
        )
    assert raised.value.code == "CONFLICT"
    assert status in raised.value.message


def test_an_issue_cannot_be_created_closed_and_assigned_at_once(
    instance: seahaven.Instance, scaffold: Scaffold
) -> None:
    with pytest.raises(seahaven.ToolError) as raised:
        an_issue(instance, scaffold, status="done", assignee_id=scaffold.member)
    assert raised.value.code == "CONFLICT"


def test_an_update_that_closes_and_assigns_at_once_is_refused(
    instance: seahaven.Instance, scaffold: Scaffold
) -> None:
    """Half-applying it would be worse: the agent asked for a state that cannot exist."""
    issue = an_issue(instance, scaffold)
    with pytest.raises(seahaven.ToolError) as raised:
        instance.call(
            "update_issue",
            issue_id=issue["id"],
            status="done",
            assignee_id=scaffold.member,
            actor_id=scaffold.admin,
        )
    assert raised.value.code == "CONFLICT"
    assert instance.call("get_issue", issue_id=issue["id"]) == issue


def test_an_update_that_closes_an_assigned_issue_drops_the_assignee(
    instance: seahaven.Instance, scaffold: Scaffold
) -> None:
    issue = an_issue(instance, scaffold, assignee_id=scaffold.member)
    closed = instance.call(
        "update_issue", issue_id=issue["id"], status="done", actor_id=scaffold.admin
    )
    assert (closed["status"], closed["assignee_id"]) == ("done", None)


def test_archiving_stamps_the_instant_and_takes_the_issue_off_the_list(
    instance: seahaven.Instance, scaffold: Scaffold
) -> None:
    issue = an_issue(instance, scaffold)
    still_there = an_issue(instance, scaffold, title="Signup loses state")
    archived = instance.call("archive_issue", issue_id=issue["id"])
    assert archived["archived_at"] == BLANK_NOW
    assert [i["id"] for i in instance.call("list_issues")["issues"]] == [still_there["id"]]
    # Off the list, still in the tracker.
    assert instance.call("get_issue", key="ENG-1")["archived_at"] == BLANK_NOW


def test_archiving_twice_is_a_conflict(instance: seahaven.Instance, scaffold: Scaffold) -> None:
    issue = an_issue(instance, scaffold)
    instance.call("archive_issue", issue_id=issue["id"])
    with pytest.raises(seahaven.ToolError) as raised:
        instance.call("archive_issue", issue_id=issue["id"])
    assert raised.value.code == "CONFLICT"
    assert "already archived" in raised.value.message


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("update_issue", {"title": "Anything", "actor_id": True}),
        ("assign_issue", {"assignee_id": None, "actor_id": True}),
        ("transition_issue", {"status": "done", "actor_id": True}),
        ("set_issue_labels", {"label_ids": []}),
    ],
)
def test_no_tool_changes_a_field_of_an_archived_issue(
    instance: seahaven.Instance, scaffold: Scaffold, tool: str, arguments: dict[str, Any]
) -> None:
    """All four tools that write to an issue, and not the three that happen to
    share a module.

    `set_issue_labels` lives in `tools/labels.py` and is the one a sweep over
    `issues.py` misses; it is here because the rule is about the issue, not about
    where the tool is written. `add_comment` is deliberately not in the list --
    archiving freezes the issue, not the conversation about it.
    """
    issue = an_issue(instance, scaffold)
    instance.call("archive_issue", issue_id=issue["id"])
    named = {
        name: (scaffold.admin if value is True else value) for name, value in arguments.items()
    }
    with pytest.raises(seahaven.ToolError) as raised:
        instance.call(tool, issue_id=issue["id"], **named)
    assert raised.value.code == "CONFLICT"
    assert "archived" in raised.value.message


def test_the_list_filters_compose(instance: seahaven.Instance, scaffold: Scaffold) -> None:
    mine = an_issue(instance, scaffold, assignee_id=scaffold.member, status="todo")
    an_issue(instance, scaffold, assignee_id=scaffold.member, status="backlog")
    an_issue(instance, scaffold, status="todo")
    found = instance.call("list_issues", status="todo", assignee_id=scaffold.member)
    assert [issue["id"] for issue in found["issues"]] == [mine["id"]]


def test_the_list_filters_by_label_and_wants_every_one_named(
    instance: seahaven.Instance, scaffold: Scaffold
) -> None:
    """All of them, not any: each filter narrows the result and none widens it."""
    second = instance.call("create_label", team_id=scaffold.team, name="chore", color="#16a34a")
    both = an_issue(instance, scaffold)
    one = an_issue(instance, scaffold, title="Signup loses state")
    instance.call("set_issue_labels", issue_id=both["id"], label_ids=[scaffold.label, second["id"]])
    instance.call("set_issue_labels", issue_id=one["id"], label_ids=[scaffold.label])

    assert {
        i["id"] for i in instance.call("list_issues", label_ids=[scaffold.label])["issues"]
    } == {
        both["id"],
        one["id"],
    }
    assert [
        i["id"]
        for i in instance.call("list_issues", label_ids=[scaffold.label, second["id"]])["issues"]
    ] == [both["id"]]


def test_the_list_filters_by_creation_time(instance: seahaven.Instance, scaffold: Scaffold) -> None:
    """A blank instance's clock does not move, so nothing is after its own instant."""
    an_issue(instance, scaffold)
    assert instance.call("list_issues", created_after=BLANK_NOW)["issues"] == []
    assert (
        len(instance.call("list_issues", created_after="2026-01-01T00:00:00.000Z")["issues"]) == 1
    )


def test_an_ordering_the_tool_does_not_have_is_refused(instance: seahaven.Instance) -> None:
    with pytest.raises(seahaven.ToolError) as raised:
        instance.call("list_issues", order="priority_desc")
    assert raised.value.code == "INVALID_INPUT"
    assert raised.value.details == {"field": "order"}


def test_a_write_with_no_actor_and_no_viewer_is_refused(
    instance: seahaven.Instance, scaffold: Scaffold
) -> None:
    """A blank instance had no admin when its startup hook ran, so it has no viewer."""
    with pytest.raises(seahaven.ToolError) as raised:
        instance.call("create_issue", project_id=scaffold.project, title="Nobody filed this")
    assert raised.value.to_dict() == {
        "code": "INVALID_INPUT",
        "message": "actor_id: no actor",
        "details": {"field": "actor_id"},
    }


def test_an_actor_who_is_not_in_the_workspace_is_not_found(
    instance: seahaven.Instance, scaffold: Scaffold
) -> None:
    with pytest.raises(seahaven.ToolError) as raised:
        an_issue(instance, scaffold, actor_id="nobody")
    assert raised.value.to_dict()["details"] == {"kind": "user", "key": "nobody"}


@pytest.mark.seahaven(fixture=SMALL_STARTUP)
def test_the_fixtures_viewer_is_the_actor_when_a_call_names_none(
    instance: seahaven.Instance,
) -> None:
    """`small_startup` has an admin, so the startup hook found a viewer to fall back to."""
    admin = instance.call("list_users", role="admin")["users"][0]
    project = instance.call("list_projects")["projects"][0]
    filed = instance.call("create_issue", project_id=project["id"], title="Filed by the viewer")
    assert filed["creator_id"] == admin["id"]


@pytest.mark.seahaven(fixture=SMALL_STARTUP)
def test_the_forty_first_issue_of_the_small_startup_is_eng_41(
    instance: seahaven.Instance,
) -> None:
    """The fixture holds forty, so the counter the tools mint from is at forty."""
    assert instance.inspect().one("SELECT count(*) AS n FROM issues") == {"n": 40}
    project = instance.call("list_projects")["projects"][0]
    assert instance.call("create_issue", project_id=project["id"], title="One more")["key"] == (
        "ENG-41"
    )


@pytest.mark.seahaven(fixture=SMALL_STARTUP, user_id=None)
def test_the_fixture_never_has_a_closed_issue_with_an_assignee(
    instance: seahaven.Instance,
) -> None:
    """The product's rule, asserted of the committed data an eval will grade against."""
    assert instance.inspect().one(
        "SELECT count(*) AS n FROM issues"
        " WHERE status IN ('done', 'canceled') AND assignee_id IS NOT NULL"
    ) == {"n": 0}
