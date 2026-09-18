"""`create_user`, `get_user`, `list_users`, driven the way an agent drives them.

Nothing here calls a tool function. What is being tested is the tool -- its
schema, its argument model, the transaction it runs in and the error shapes it
raises -- and only `instance.call` puts all of that in the path.
"""

import pytest

import seahaven
from conftest import BLANK_NOW, SMALL_STARTUP

pytestmark = pytest.mark.seahaven(fixture=None, now=BLANK_NOW)


def test_creating_a_user_answers_with_the_row_that_was_written(
    instance: seahaven.Instance,
) -> None:
    created = instance.call("create_user", email="ada@tracker.invalid", name="Ada", role="admin")
    assert created == {
        "id": created["id"],
        "email": "ada@tracker.invalid",
        "name": "Ada",
        "role": "admin",
        "created_at": BLANK_NOW,
    }
    assert instance.call("get_user", user_id=created["id"]) == created


def test_a_user_defaults_to_member(instance: seahaven.Instance) -> None:
    assert instance.call("create_user", email="bo@tracker.invalid", name="Bo")["role"] == "member"


@pytest.mark.parametrize(
    "email", ["nope", "@tracker.invalid", "ada@", "ada@tracker", "ada tracker@x.invalid", ""]
)
def test_an_address_that_is_not_an_address_is_invalid_input(
    instance: seahaven.Instance, email: str
) -> None:
    with pytest.raises(seahaven.ToolError) as raised:
        instance.call("create_user", email=email, name="Ada")
    assert raised.value.code == "INVALID_INPUT"
    assert raised.value.details == {"field": "email"}


def test_the_same_address_twice_is_a_conflict_and_not_a_database_error(
    instance: seahaven.Instance,
) -> None:
    """The UNIQUE index would refuse it; the tool refuses it first, in words.

    A constraint failure reaches the handler as a `DbError` and becomes
    `INTERNAL`, which tells an agent nothing about the address it should have
    reused. That is the whole reason the tool looks first.
    """
    instance.call("create_user", email="ada@tracker.invalid", name="Ada")
    with pytest.raises(seahaven.ToolError) as raised:
        instance.call("create_user", email="ada@tracker.invalid", name="Ada Again")
    assert raised.value.code == "CONFLICT"
    assert "ada@tracker.invalid" in raised.value.message
    # And the refusal left the workspace as it was, with the first Ada in it.
    assert [u["name"] for u in instance.call("list_users")["users"]] == ["Ada"]


def test_a_role_the_product_does_not_have_is_refused_before_the_tool_runs(
    instance: seahaven.Instance,
) -> None:
    """The `Literal` is published in the schema, so the agent is told the three."""
    with pytest.raises(seahaven.ToolError) as raised:
        instance.call("create_user", email="ada@tracker.invalid", name="Ada", role="owner")
    assert raised.value.code == "INVALID_INPUT"
    assert raised.value.details == {"field": "role"}
    schema = next(t for t in instance.tools() if t["name"] == "create_user")["input_schema"]
    assert schema["properties"]["role"]["enum"] == ["admin", "member", "viewer"]


def test_an_unknown_user_is_not_found(instance: seahaven.Instance) -> None:
    with pytest.raises(seahaven.ToolError) as raised:
        instance.call("get_user", user_id="nobody")
    assert raised.value.to_dict() == {
        "code": "NOT_FOUND",
        "message": "user nobody not found",
        "details": {"kind": "user", "key": "nobody"},
    }


def test_the_list_is_ordered_by_creation_then_id_and_says_there_is_no_more(
    instance: seahaven.Instance,
) -> None:
    """The order is `(created_at, id)`, and the second half is not decoration.

    An instance's clock does not move, so three users made in one test are made
    at one instant and the timestamp orders none of them. The id is what makes
    the answer the same on every run -- which is exactly the case a page boundary
    would otherwise fall inside.
    """
    made = [
        instance.call("create_user", email=f"user{n}@tracker.invalid", name=f"User {n}")
        for n in range(3)
    ]
    assert len({user["created_at"] for user in made}) == 1
    listed = instance.call("list_users")
    assert listed == {
        "users": sorted(made, key=lambda user: (user["created_at"], user["id"])),
        "next_cursor": None,
        "has_next": False,
    }


def test_the_list_filters_by_role(instance: seahaven.Instance) -> None:
    admin = instance.call("create_user", email="ada@tracker.invalid", name="Ada", role="admin")
    instance.call("create_user", email="bo@tracker.invalid", name="Bo")
    instance.call("create_user", email="cai@tracker.invalid", name="Cai", role="viewer")
    assert [u["id"] for u in instance.call("list_users", role="admin")["users"]] == [admin["id"]]
    assert len(instance.call("list_users")["users"]) == 3


@pytest.mark.seahaven(fixture=SMALL_STARTUP)
def test_the_fixtures_people_are_there_with_one_admin(instance: seahaven.Instance) -> None:
    """The same tool against a committed fixture, which is how an eval meets it."""
    listed = instance.call("list_users")
    assert len(listed["users"]) == 3
    assert [u["role"] for u in listed["users"]] == ["admin", "member", "member"]
    assert listed["has_next"] is False
