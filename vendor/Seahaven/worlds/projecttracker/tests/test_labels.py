"""Labels, and the set of them an issue carries."""

import pytest

import seahaven
from conftest import AGENCY, BLANK_NOW, Scaffold, an_issue

pytestmark = pytest.mark.seahaven(fixture=None, now=BLANK_NOW)


def test_creating_a_label_answers_with_the_row(
    instance: seahaven.Instance, scaffold: Scaffold
) -> None:
    created = instance.call(
        "create_label", team_id=scaffold.team, name="regression", color="#2563eb"
    )
    assert created == {
        "id": created["id"],
        "team_id": scaffold.team,
        "name": "regression",
        "color": "#2563eb",
    }


@pytest.mark.parametrize("color", ["red", "#fff", "#GGGGGG", "#E11D48", "e11d48", "#e11d488"])
def test_a_colour_that_is_not_lower_case_rrggbb_is_refused(
    instance: seahaven.Instance, scaffold: Scaffold, color: str
) -> None:
    with pytest.raises(seahaven.ToolError) as raised:
        instance.call("create_label", team_id=scaffold.team, name="bug", color=color)
    assert raised.value.code == "INVALID_INPUT"
    assert raised.value.details == {"field": "color"}


def test_the_same_name_twice_in_one_team_is_a_conflict(
    instance: seahaven.Instance, scaffold: Scaffold
) -> None:
    with pytest.raises(seahaven.ToolError) as raised:
        instance.call("create_label", team_id=scaffold.team, name="bug", color="#2563eb")
    assert raised.value.code == "CONFLICT"


def test_the_same_name_in_another_team_is_another_label(
    instance: seahaven.Instance, scaffold: Scaffold
) -> None:
    """A label belongs to a team, so two teams both having a `bug` is two labels."""
    other = instance.call("create_team", key="DES", name="Design")
    theirs = instance.call("create_label", team_id=other["id"], name="bug", color="#2563eb")
    assert theirs["id"] != scaffold.label
    assert [
        label["id"] for label in instance.call("list_labels", team_id=other["id"])["labels"]
    ] == [theirs["id"]]


def test_labels_are_listed_by_name(instance: seahaven.Instance, scaffold: Scaffold) -> None:
    """A picker is alphabetical: nobody looks for a label by when it was made."""
    for name in ("regression", "chore", "docs"):
        instance.call("create_label", team_id=scaffold.team, name=name, color="#16a34a")
    listed = instance.call("list_labels", team_id=scaffold.team)
    assert [label["name"] for label in listed["labels"]] == [
        "bug",
        "chore",
        "docs",
        "regression",
    ]


def test_setting_an_issues_labels_replaces_the_whole_set(
    instance: seahaven.Instance, scaffold: Scaffold
) -> None:
    second = instance.call("create_label", team_id=scaffold.team, name="chore", color="#16a34a")
    issue = an_issue(instance, scaffold)
    assert issue["label_ids"] == []

    set_to_one = instance.call("set_issue_labels", issue_id=issue["id"], label_ids=[scaffold.label])
    assert set_to_one == {"issue_id": issue["id"], "label_ids": [scaffold.label]}

    instance.call("set_issue_labels", issue_id=issue["id"], label_ids=[second["id"]])
    assert instance.call("get_issue", issue_id=issue["id"])["label_ids"] == [second["id"]]

    instance.call("set_issue_labels", issue_id=issue["id"], label_ids=[])
    assert instance.call("get_issue", issue_id=issue["id"])["label_ids"] == []


def test_the_set_is_a_set_and_comes_back_sorted(
    instance: seahaven.Instance, scaffold: Scaffold
) -> None:
    second = instance.call("create_label", team_id=scaffold.team, name="chore", color="#16a34a")
    issue = an_issue(instance, scaffold)
    ids = [second["id"], scaffold.label, second["id"]]
    assert instance.call("set_issue_labels", issue_id=issue["id"], label_ids=ids)["label_ids"] == (
        sorted({second["id"], scaffold.label})
    )


def test_an_unknown_label_leaves_the_set_as_it_was(
    instance: seahaven.Instance, scaffold: Scaffold
) -> None:
    """Checked before anything is written, and the call is one transaction anyway."""
    issue = an_issue(instance, scaffold)
    instance.call("set_issue_labels", issue_id=issue["id"], label_ids=[scaffold.label])
    with pytest.raises(seahaven.ToolError) as raised:
        instance.call(
            "set_issue_labels", issue_id=issue["id"], label_ids=[scaffold.label, "nothing"]
        )
    assert raised.value.to_dict()["details"] == {"kind": "label", "key": "nothing"}
    assert instance.call("get_issue", issue_id=issue["id"])["label_ids"] == [scaffold.label]


def test_labelling_an_issue_that_does_not_exist_is_not_found(
    instance: seahaven.Instance, scaffold: Scaffold
) -> None:
    with pytest.raises(seahaven.ToolError) as raised:
        instance.call("set_issue_labels", issue_id="nowhere", label_ids=[scaffold.label])
    assert raised.value.to_dict()["details"] == {"kind": "issue", "key": "nowhere"}


def test_an_issue_cannot_wear_another_teams_label(
    instance: seahaven.Instance, scaffold: Scaffold
) -> None:
    """A label belongs to a team, so an issue can only carry its own team's.

    Without this the two halves of the product model contradict each other:
    `list_issues(label_ids=[a DES label])` would answer with an ENG issue, and the
    fixtures -- whose generator draws each issue's labels from its own team --
    would hold an invariant the tools did not keep, which is the worst way for an
    eval author to learn a rule.
    """
    other = instance.call("create_team", key="DES", name="Design")
    theirs = instance.call("create_label", team_id=other["id"], name="polish", color="#a855f7")
    issue = an_issue(instance, scaffold)
    with pytest.raises(seahaven.ToolError) as raised:
        instance.call("set_issue_labels", issue_id=issue["id"], label_ids=[theirs["id"]])
    assert raised.value.code == "INVALID_INPUT"
    assert raised.value.details == {"field": "label_ids"}
    assert instance.call("get_issue", issue_id=issue["id"])["label_ids"] == []


def test_one_foreign_label_refuses_the_whole_set(
    instance: seahaven.Instance, scaffold: Scaffold
) -> None:
    """Checked before anything is written, like an unknown id."""
    other = instance.call("create_team", key="DES", name="Design")
    theirs = instance.call("create_label", team_id=other["id"], name="polish", color="#a855f7")
    issue = an_issue(instance, scaffold)
    instance.call("set_issue_labels", issue_id=issue["id"], label_ids=[scaffold.label])
    with pytest.raises(seahaven.ToolError):
        instance.call(
            "set_issue_labels", issue_id=issue["id"], label_ids=[scaffold.label, theirs["id"]]
        )
    assert instance.call("get_issue", issue_id=issue["id"])["label_ids"] == [scaffold.label]


def test_an_archived_issue_cannot_be_relabelled(
    instance: seahaven.Instance, scaffold: Scaffold
) -> None:
    """`set_issue_labels` is the fourth tool that writes to an issue, and archiving
    is final for all four.

    Labels are part of what `get_issue` answers with, so a relabelled archived
    issue would contradict what three documents say archiving means.
    """
    issue = an_issue(instance, scaffold)
    instance.call("archive_issue", issue_id=issue["id"])
    with pytest.raises(seahaven.ToolError) as raised:
        instance.call("set_issue_labels", issue_id=issue["id"], label_ids=[scaffold.label])
    assert raised.value.code == "CONFLICT"
    assert "archived" in raised.value.message


@pytest.mark.seahaven(fixture=AGENCY)
def test_the_agency_fixtures_labels_are_per_team(instance: seahaven.Instance) -> None:
    teams = instance.call("list_teams")["teams"]
    assert len(teams) == 3
    for team in teams:
        assert len(instance.call("list_labels", team_id=team["id"], limit=50)["labels"]) == 10
