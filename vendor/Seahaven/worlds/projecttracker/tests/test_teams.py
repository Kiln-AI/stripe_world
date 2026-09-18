"""Teams and membership: the five tools in `tools/teams.py`."""

import pytest

import seahaven
from conftest import BLANK_NOW, SMALL_STARTUP

pytestmark = pytest.mark.seahaven(fixture=None, now=BLANK_NOW)


def test_creating_a_team_answers_with_the_row_and_hides_the_key_counter(
    instance: seahaven.Instance,
) -> None:
    """`issue_counter` is how a key is minted, not something an agent counts with.

    An archived issue still consumed its number, so a counter on the wire would
    be read as "how many issues this team has" and be wrong.
    """
    created = instance.call("create_team", key="ENG", name="Engineering")
    assert created == {
        "id": created["id"],
        "key": "ENG",
        "name": "Engineering",
        "created_at": BLANK_NOW,
    }
    assert "issue_counter" not in created


@pytest.mark.parametrize("key", ["E", "ENGINEER", "eng", "EN1", "EN-G", ""])
def test_a_key_that_is_not_two_to_five_capitals_is_refused(
    instance: seahaven.Instance, key: str
) -> None:
    with pytest.raises(seahaven.ToolError) as raised:
        instance.call("create_team", key=key, name="Engineering")
    assert raised.value.code == "INVALID_INPUT"
    assert raised.value.details == {"field": "key"}


def test_the_same_key_twice_is_a_conflict(instance: seahaven.Instance) -> None:
    instance.call("create_team", key="ENG", name="Engineering")
    with pytest.raises(seahaven.ToolError) as raised:
        instance.call("create_team", key="ENG", name="Engineering Again")
    assert raised.value.code == "CONFLICT"


def test_a_team_is_fetched_by_its_key_and_not_by_its_id(instance: seahaven.Instance) -> None:
    """The key is what an agent has: it is the front half of every issue key."""
    created = instance.call("create_team", key="ENG", name="Engineering")
    assert instance.call("get_team", key="ENG") == created


def test_an_unknown_key_is_not_found(instance: seahaven.Instance) -> None:
    with pytest.raises(seahaven.ToolError) as raised:
        instance.call("get_team", key="OPS")
    assert raised.value.to_dict() == {
        "code": "NOT_FOUND",
        "message": "team OPS not found",
        "details": {"kind": "team", "key": "OPS"},
    }


def test_members_are_added_and_listed_in_joining_order(instance: seahaven.Instance) -> None:
    team = instance.call("create_team", key="ENG", name="Engineering")
    people = [
        instance.call("create_user", email=f"user{n}@tracker.invalid", name=f"User {n}")
        for n in range(3)
    ]
    added = [
        instance.call("add_team_member", team_id=team["id"], user_id=person["id"])
        for person in people
    ]
    assert added[0] == {
        "team_id": team["id"],
        "user_id": people[0]["id"],
        "joined_at": BLANK_NOW,
    }
    listed = instance.call("list_team_members", team_id=team["id"])
    # One instant for all three, so the keyset's second half -- the user, since a
    # membership has no id of its own -- is what makes the order total.
    assert listed["members"] == sorted(added, key=lambda row: (row["joined_at"], row["user_id"]))
    assert listed["has_next"] is False


def test_the_same_member_twice_is_a_conflict(instance: seahaven.Instance) -> None:
    """Not a silent no-op: the second call means the caller believes something untrue."""
    team = instance.call("create_team", key="ENG", name="Engineering")
    person = instance.call("create_user", email="ada@tracker.invalid", name="Ada")
    instance.call("add_team_member", team_id=team["id"], user_id=person["id"])
    with pytest.raises(seahaven.ToolError) as raised:
        instance.call("add_team_member", team_id=team["id"], user_id=person["id"])
    assert raised.value.code == "CONFLICT"


@pytest.mark.parametrize(
    ("bad", "kind"),
    [("team_id", "team"), ("user_id", "user")],
)
def test_adding_a_member_names_whichever_side_is_missing(
    instance: seahaven.Instance, bad: str, kind: str
) -> None:
    """A foreign key would refuse this too, as `INTERNAL`. The tool says which end."""
    team = instance.call("create_team", key="ENG", name="Engineering")
    person = instance.call("create_user", email="ada@tracker.invalid", name="Ada")
    arguments = {"team_id": team["id"], "user_id": person["id"]} | {bad: "nobody"}
    with pytest.raises(seahaven.ToolError) as raised:
        instance.call("add_team_member", **arguments)
    assert raised.value.to_dict()["details"] == {"kind": kind, "key": "nobody"}


def test_listing_members_of_a_team_that_does_not_exist_is_not_found(
    instance: seahaven.Instance,
) -> None:
    """An empty page would be a lie: there is no such team to have no members."""
    with pytest.raises(seahaven.ToolError) as raised:
        instance.call("list_team_members", team_id="nobody")
    assert raised.value.code == "NOT_FOUND"


@pytest.mark.seahaven(fixture=SMALL_STARTUP)
def test_the_fixture_has_one_team_with_everyone_on_it(instance: seahaven.Instance) -> None:
    teams = instance.call("list_teams")["teams"]
    assert [team["key"] for team in teams] == ["ENG"]
    members = instance.call("list_team_members", team_id=teams[0]["id"])["members"]
    assert len(members) == 3
