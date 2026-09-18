"""`add_comment` and `list_comments`."""

import pytest

import seahaven
from conftest import BLANK_NOW, SMALL_STARTUP, Scaffold, an_issue

pytestmark = pytest.mark.seahaven(fixture=None, now=BLANK_NOW)


def test_a_comment_is_written_and_answers_with_its_row(
    instance: seahaven.Instance, scaffold: Scaffold
) -> None:
    issue = an_issue(instance, scaffold)
    comment = instance.call(
        "add_comment", issue_id=issue["id"], body="Looking at this now.", actor_id=scaffold.member
    )
    assert comment == {
        "id": comment["id"],
        "issue_id": issue["id"],
        "author_id": scaffold.member,
        "body": "Looking at this now.",
        "created_at": BLANK_NOW,
    }


def test_a_comment_appends_to_the_issues_trail(
    instance: seahaven.Instance, scaffold: Scaffold
) -> None:
    issue = an_issue(instance, scaffold)
    comment = instance.call(
        "add_comment", issue_id=issue["id"], body="Reproduced.", actor_id=scaffold.member
    )
    trail = instance.inspect().rows(
        "SELECT kind, payload, actor_id FROM issue_events WHERE issue_id = ?"
        " ORDER BY created_at, rowid",
        issue["id"],
    )
    assert [event["kind"] for event in trail] == ["created", "comment"]
    assert trail[-1]["payload"] == f'{{"comment_id":"{comment["id"]}"}}'
    assert trail[-1]["actor_id"] == scaffold.member


def test_a_comment_does_not_touch_the_issue(
    instance: seahaven.Instance, scaffold: Scaffold
) -> None:
    """In this product a comment is activity, not a change to the issue."""
    issue = an_issue(instance, scaffold)
    instance.call("add_comment", issue_id=issue["id"], body="Hello.", actor_id=scaffold.admin)
    assert instance.call("get_issue", issue_id=issue["id"]) == issue


def test_comments_written_in_one_episode_come_back_in_id_order_and_not_in_writing_order(
    instance: seahaven.Instance, scaffold: Scaffold
) -> None:
    """The frozen clock's consequence, asserted rather than assumed.

    Every comment one episode writes carries the tracker's instant, so the
    timestamp orders none of them and the keyset's second half -- the id -- does.
    That is stable across runs and is not the order they were written in, which
    is what `list_comments` says and what `AGENTS.md` tells an eval author not to
    grade on. The property asserted is "sorted by id", exactly; that the five
    seeded uuids here happen not to sort into writing order is a fact about this
    seed and not something to pin.
    """
    issue = an_issue(instance, scaffold)
    written = [
        instance.call(
            "add_comment", issue_id=issue["id"], body=f"Comment {n}", actor_id=scaffold.admin
        )
        for n in range(5)
    ]
    assert len({comment["created_at"] for comment in written}) == 1

    listed = instance.call("list_comments", issue_id=issue["id"])
    assert listed == {
        "comments": sorted(written, key=lambda row: row["id"]),
        "next_cursor": None,
        "has_next": False,
    }


def test_comments_belong_to_their_own_issue(
    instance: seahaven.Instance, scaffold: Scaffold
) -> None:
    first = an_issue(instance, scaffold)
    second = an_issue(instance, scaffold, title="Signup loses state")
    instance.call(
        "add_comment", issue_id=first["id"], body="On the first.", actor_id=scaffold.admin
    )
    assert instance.call("list_comments", issue_id=second["id"])["comments"] == []


@pytest.mark.parametrize("tool", ["add_comment", "list_comments"])
def test_an_issue_that_does_not_exist_is_not_found(
    instance: seahaven.Instance, scaffold: Scaffold, tool: str
) -> None:
    arguments = {"body": "Hello.", "actor_id": scaffold.admin} if tool == "add_comment" else {}
    with pytest.raises(seahaven.ToolError) as raised:
        instance.call(tool, issue_id="nowhere", **arguments)
    assert raised.value.to_dict()["details"] == {"kind": "issue", "key": "nowhere"}


def test_a_comment_with_no_actor_and_no_viewer_is_refused(
    instance: seahaven.Instance, scaffold: Scaffold
) -> None:
    issue = an_issue(instance, scaffold)
    with pytest.raises(seahaven.ToolError) as raised:
        instance.call("add_comment", issue_id=issue["id"], body="Who said this?")
    assert raised.value.to_dict() == {
        "code": "INVALID_INPUT",
        "message": "actor_id: no actor",
        "details": {"field": "actor_id"},
    }


def test_a_comment_can_be_left_on_an_archived_issue(
    instance: seahaven.Instance, scaffold: Scaffold
) -> None:
    """Archiving freezes the issue, not the conversation about it."""
    issue = an_issue(instance, scaffold)
    instance.call("archive_issue", issue_id=issue["id"])
    comment = instance.call(
        "add_comment",
        issue_id=issue["id"],
        body="Archived, for the record.",
        actor_id=scaffold.admin,
    )
    assert len(instance.call("list_comments", issue_id=issue["id"])["comments"]) == 1
    # And the trail records it, so an archived issue is not frozen either. This is
    # the carve-out `README.md` and `AGENTS.md` name; the other four tools that
    # write to an issue refuse an archived one (`test_issues.py`).
    trail = instance.inspect().rows(
        "SELECT kind, payload FROM issue_events WHERE issue_id = ? ORDER BY created_at, rowid",
        issue["id"],
    )
    assert [event["kind"] for event in trail] == ["created", "comment"]
    assert trail[-1]["payload"] == f'{{"comment_id":"{comment["id"]}"}}'


@pytest.mark.seahaven(fixture=SMALL_STARTUP)
def test_the_fixture_holds_sixty_comments(instance: seahaven.Instance) -> None:
    assert instance.inspect().one("SELECT count(*) AS n FROM comments") == {"n": 60}


@pytest.mark.seahaven(fixture=SMALL_STARTUP)
def test_a_fixtures_comments_really_are_oldest_first(instance: seahaven.Instance) -> None:
    """Across a history the timestamps differ, so the list is chronological.

    The other half of the pair above: the ordering is not broken, it is
    degenerate only where every row shares one instant.
    """
    busiest = instance.inspect().one(
        "SELECT issue_id, count(*) AS n FROM comments GROUP BY issue_id"
        " ORDER BY n DESC, issue_id LIMIT 1"
    )
    assert busiest is not None and busiest["n"] > 1
    listed = instance.call("list_comments", issue_id=busiest["issue_id"])["comments"]
    stamps = [comment["created_at"] for comment in listed]
    assert stamps == sorted(stamps)
    assert len(set(stamps)) == len(stamps), "a fixture's comments are at distinct instants"
