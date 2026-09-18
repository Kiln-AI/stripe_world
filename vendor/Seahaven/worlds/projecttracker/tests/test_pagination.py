"""The cursor every list in this world shares: three pages, and the refusals.

Written against `list_issues` because it is the only list with more than one
ordering, and a cursor's third field exists to keep two orderings apart. The
mechanics -- one extra row read, `has_next`, `next_cursor` -- are the same
function under every other list, and the modules for those lists assert their own
order and filters.
"""

import base64
import json
from typing import Any

import pytest

import seahaven
from conftest import AGENCY, BLANK_NOW, Scaffold, an_issue

pytestmark = pytest.mark.seahaven(fixture=None, now=BLANK_NOW)


def walk(instance: seahaven.Instance, **arguments: Any) -> list[list[str]]:
    """Every page of `list_issues`, as lists of ids, following the cursors."""
    pages: list[list[str]] = []
    cursor: str | None = None
    while True:
        page = instance.call("list_issues", cursor=cursor, **arguments)
        pages.append([issue["id"] for issue in page["issues"]])
        assert (page["next_cursor"] is not None) == page["has_next"]
        if not page["has_next"]:
            return pages
        cursor = page["next_cursor"]
        assert len(pages) < 20, "the walk is not terminating"


def test_three_pages_cover_every_row_once_and_in_order(
    instance: seahaven.Instance, scaffold: Scaffold
) -> None:
    made = [an_issue(instance, scaffold, title=f"Issue {n}") for n in range(7)]
    pages = walk(instance, limit=3, order="created_at_asc")
    assert [len(page) for page in pages] == [3, 3, 1]
    walked = [issue_id for page in pages for issue_id in page]
    assert walked == sorted(issue["id"] for issue in made)
    assert len(set(walked)) == len(made)


def test_the_pages_are_the_same_rows_the_unpaged_list_gives(
    instance: seahaven.Instance, scaffold: Scaffold
) -> None:
    """A page boundary must not change the answer, only where it is cut."""
    for n in range(7):
        an_issue(instance, scaffold, title=f"Issue {n}")
    whole = [issue["id"] for issue in instance.call("list_issues", limit=250)["issues"]]
    assert [issue_id for page in walk(instance, limit=2) for issue_id in page] == whole


def test_the_last_full_page_says_there_is_no_more(
    instance: seahaven.Instance, scaffold: Scaffold
) -> None:
    """Exactly `limit` rows left: `has_next` is read from the row that is not there.

    The off-by-one every keyset gets wrong once. Four rows and a limit of two is
    two full pages and no third, and a cursor on the second would hand an agent
    an empty page it was told to expect rows in.
    """
    for n in range(4):
        an_issue(instance, scaffold, title=f"Issue {n}")
    assert [len(page) for page in walk(instance, limit=2)] == [2, 2]


def test_a_filter_travels_with_the_cursor(instance: seahaven.Instance, scaffold: Scaffold) -> None:
    """The cursor is a position, not a query: the caller re-sends the filters."""
    wanted = [an_issue(instance, scaffold, status="todo", title=f"Todo {n}") for n in range(3)]
    for n in range(3):
        an_issue(instance, scaffold, title=f"Backlog {n}")
    pages = walk(instance, limit=2, status="todo")
    assert sorted(issue_id for page in pages for issue_id in page) == sorted(
        issue["id"] for issue in wanted
    )


def test_a_cursor_cut_for_another_ordering_is_refused(
    instance: seahaven.Instance, scaffold: Scaffold
) -> None:
    """The whole point of the third field: the page would otherwise be plausible and wrong."""
    for n in range(3):
        an_issue(instance, scaffold, title=f"Issue {n}")
    cursor = instance.call("list_issues", limit=1, order="created_at_asc")["next_cursor"]
    with pytest.raises(seahaven.ToolError) as raised:
        instance.call("list_issues", limit=1, order="updated_at_desc", cursor=cursor)
    assert raised.value.code == "INVALID_INPUT"
    assert raised.value.details == {"field": "cursor"}
    assert "issues:created_at_asc" in raised.value.message


def test_a_cursor_from_another_list_is_refused_too(
    instance: seahaven.Instance, scaffold: Scaffold
) -> None:
    """The sort key names the resource as well, so lists cannot borrow each other's."""
    for n in range(3):
        an_issue(instance, scaffold, title=f"Issue {n}")
    cursor = instance.call("list_users", limit=1)["next_cursor"]
    with pytest.raises(seahaven.ToolError) as raised:
        instance.call("list_issues", limit=1, cursor=cursor)
    assert raised.value.code == "INVALID_INPUT"
    assert "users:created_at_asc" in raised.value.message


@pytest.mark.parametrize(
    "cursor",
    [
        pytest.param("not base64 at all !!", id="not base64"),
        pytest.param(base64.urlsafe_b64encode(b"not json").decode(), id="not json"),
        pytest.param(
            base64.urlsafe_b64encode(json.dumps({"a": 1}).encode()).decode(), id="not a triple"
        ),
        pytest.param(
            base64.urlsafe_b64encode(json.dumps(["x", "y"]).encode()).decode(), id="too short"
        ),
        pytest.param(
            base64.urlsafe_b64encode(json.dumps(["x", "y", 7]).encode()).decode(),
            id="a key that is not a string",
        ),
        pytest.param(
            base64.urlsafe_b64encode(
                json.dumps([{"a": 1}, "x", "issues:created_at_desc"]).encode()
            ).decode(),
            id="a sort value SQLite cannot bind",
        ),
        pytest.param(
            base64.urlsafe_b64encode(
                json.dumps(["x", ["y"], "issues:created_at_desc"]).encode()
            ).decode(),
            id="an id SQLite cannot bind",
        ),
        pytest.param(
            base64.urlsafe_b64encode(
                json.dumps([2**80, "x", "issues:created_at_desc"]).encode()
            ).decode(),
            id="a sort value past 64 bits",
        ),
        pytest.param(
            base64.urlsafe_b64encode(
                json.dumps(["x", -(2**63) - 1, "issues:created_at_desc"]).encode()
            ).decode(),
            id="an id past 64 bits",
        ),
        pytest.param("", id="empty"),
    ],
)
def test_a_cursor_this_list_did_not_cut_is_invalid_input(
    instance: seahaven.Instance, cursor: str
) -> None:
    """Every way a cursor can be wrong ends in one error, including the four that
    are well-formed enough to reach SQLite.

    The cursor is the one place in this world where a value an agent wrote
    becomes a SQL parameter without passing through a pydantic-typed argument, so
    it is the one place where the *shape* of a JSON value matters. Two shapes
    APSW refuses: a container, with `TypeError`, and an integer past 64 bits,
    with `OverflowError` -- JSON has no integer bound and `2 ** 80` decodes
    happily. Neither is a `ToolError`, so unchecked the handler's last branch
    answers `INTERNAL` and logs a traceback, and an agent mangling the opaque
    string it was handed makes the world accuse itself of a bug, once per call.
    """
    with pytest.raises(seahaven.ToolError) as raised:
        instance.call("list_issues", cursor=cursor)
    assert raised.value.code == "INVALID_INPUT"
    assert raised.value.details == {"field": "cursor"}


@pytest.mark.parametrize("limit", [0, -1, 251, 1000])
def test_a_page_size_outside_the_products_range_is_refused(
    instance: seahaven.Instance, limit: int
) -> None:
    with pytest.raises(seahaven.ToolError) as raised:
        instance.call("list_issues", limit=limit)
    assert raised.value.code == "INVALID_INPUT"
    assert raised.value.details == {"field": "limit"}


@pytest.mark.seahaven(fixture=AGENCY)
@pytest.mark.parametrize(
    "order", ["created_at_desc", "created_at_asc", "updated_at_desc", "updated_at_asc"]
)
def test_every_ordering_walks_the_agency_fixture_whole(
    instance: seahaven.Instance, order: str
) -> None:
    """Six hundred issues, a hundred at a time, in each of the four orders.

    Against a fixture with real timestamps rather than a blank instance, because
    a keyset over `(column, id)` is only interesting when the column has ties in
    some places and not others -- which is what a six-month history has.
    """
    pages: list[list[dict[str, Any]]] = []
    cursor: str | None = None
    while True:
        page = instance.call("list_issues", limit=100, order=order, cursor=cursor)
        pages.append(page["issues"])
        if not page["has_next"]:
            break
        cursor = page["next_cursor"]
    walked = [issue for page in pages for issue in page]
    assert len(walked) == 600
    assert len({issue["id"] for issue in walked}) == 600
    column, _, direction = order.rpartition("_")
    assert walked == sorted(
        walked, key=lambda issue: (issue[column], issue["id"]), reverse=direction == "desc"
    )
