"""`search_issues`: FTS5 through a tool, and what a bad query is told.

The index is external content over `issues(title, description)` and is kept in
step by three triggers, so every assertion here about finding a row is also an
assertion that the triggers fired.
"""

from typing import Any

import pytest

import seahaven
from conftest import AGENCY, BLANK_NOW, Scaffold, an_issue

pytestmark = pytest.mark.seahaven(fixture=None, now=BLANK_NOW)


def keys(found: dict[str, Any]) -> list[str]:
    """The keys of a search result, which is what every assertion here is about."""
    return [issue["key"] for issue in found["issues"]]


@pytest.fixture
def corpus(instance: seahaven.Instance, scaffold: Scaffold) -> None:
    """Three issues with words a query can tell apart."""
    an_issue(
        instance,
        scaffold,
        title="Login page is broken",
        description="Users cannot sign in with the single sign-on provider",
    )
    an_issue(
        instance,
        scaffold,
        title="Signup loses state",
        description="The signup form forgets the plan on reload",
    )
    an_issue(
        instance,
        scaffold,
        title="Billing export is empty",
        description="The CSV export has headers and no rows",
    )


def test_a_word_finds_the_issue_that_has_it(instance: seahaven.Instance, corpus: None) -> None:
    assert keys(instance.call("search_issues", query="login")) == ["ENG-1"]


def test_a_phrase_is_not_two_words(instance: seahaven.Instance, corpus: None) -> None:
    assert keys(instance.call("search_issues", query='"signup form"')) == ["ENG-2"]
    assert keys(instance.call("search_issues", query='"form signup"')) == []


def test_a_prefix_matches_what_it_starts(instance: seahaven.Instance, corpus: None) -> None:
    assert sorted(keys(instance.call("search_issues", query="sign*"))) == ["ENG-1", "ENG-2"]


def test_a_column_scoped_query_looks_in_one_column(
    instance: seahaven.Instance, corpus: None
) -> None:
    assert keys(instance.call("search_issues", query="title:billing")) == ["ENG-3"]
    assert keys(instance.call("search_issues", query="title:csv")) == []
    assert keys(instance.call("search_issues", query="description:csv")) == ["ENG-3"]


def test_a_hit_carries_a_score_and_a_snippet_of_what_matched(
    instance: seahaven.Instance, corpus: None
) -> None:
    """`bm25` and `snippet`, which is what makes a result readable without a second call."""
    (hit,) = instance.call("search_issues", query="login")["issues"]
    assert hit["key"] == "ENG-1"
    assert hit["score"] < 0
    assert "[Login]" in hit["snippet"]
    # And the whole issue beside them, so a result is something to act on.
    assert hit["status"] == "backlog"
    assert hit["label_ids"] == []


def test_the_triggers_keep_the_index_in_step_with_the_table(
    instance: seahaven.Instance, scaffold: Scaffold
) -> None:
    """An update retracts the old text and indexes the new: the `_after_update` trigger."""
    issue = an_issue(instance, scaffold, title="Login page is broken", description="")
    assert keys(instance.call("search_issues", query="login")) == ["ENG-1"]
    instance.call(
        "update_issue", issue_id=issue["id"], title="Logout loops", actor_id=scaffold.admin
    )
    assert keys(instance.call("search_issues", query="login")) == []
    assert keys(instance.call("search_issues", query="logout")) == ["ENG-1"]


def test_an_archived_issue_is_still_findable(
    instance: seahaven.Instance, scaffold: Scaffold
) -> None:
    """Off the list of live work, still in the record of what was decided."""
    issue = an_issue(instance, scaffold, title="Login page is broken")
    instance.call("archive_issue", issue_id=issue["id"])
    assert instance.call("list_issues")["issues"] == []
    assert keys(instance.call("search_issues", query="login")) == ["ENG-1"]


def test_ties_are_broken_by_id_so_the_order_is_total(
    instance: seahaven.Instance, scaffold: Scaffold
) -> None:
    """Three identical documents score identically; the order is still the same every run."""
    made = [
        an_issue(instance, scaffold, title="Identical title", description="Identical body")
        for _ in range(3)
    ]
    found = instance.call("search_issues", query="identical")["issues"]
    assert len({issue["score"] for issue in found}) == 1
    assert [issue["id"] for issue in found] == sorted(issue["id"] for issue in made)


def test_the_limit_cuts_the_result_at_the_best_matches(
    instance: seahaven.Instance, corpus: None
) -> None:
    """No cursor: a `bm25` rank is only a key for one query string, so there is nothing to page."""
    found = instance.call("search_issues", query="sign*", limit=1)
    assert len(found["issues"]) == 1
    assert "next_cursor" not in found and "has_next" not in found


@pytest.mark.parametrize(
    "query", ['"', "AND", "NEAR(", "title:", "(login", "*"], ids=lambda q: repr(q)
)
def test_a_query_fts5_cannot_parse_is_invalid_input_on_the_query(
    instance: seahaven.Instance, corpus: None, query: str
) -> None:
    """The agent wrote the query, so FTS5's complaint about it is the useful answer."""
    with pytest.raises(seahaven.ToolError) as raised:
        instance.call("search_issues", query=query)
    assert raised.value.code == "INVALID_INPUT"
    assert raised.value.details == {"field": "query"}
    cause = raised.value.__cause__
    assert isinstance(cause, seahaven.DbError)
    assert raised.value.message == f"query: {cause.sqlite_message}"


def test_a_query_that_matches_nothing_is_an_empty_result_and_not_an_error(
    instance: seahaven.Instance, corpus: None
) -> None:
    assert instance.call("search_issues", query="kubernetes") == {"issues": []}


@pytest.mark.seahaven(fixture=AGENCY)
def test_search_finds_the_fixtures_issues(instance: seahaven.Instance) -> None:
    """Six hundred bulk-written issues, indexed by the triggers as they were written."""
    found = instance.call("search_issues", query="billing", limit=250)["issues"]
    assert found
    assert all("billing" in (issue["title"] + issue["description"]).lower() for issue in found)
    assert [issue["score"] for issue in found] == sorted(issue["score"] for issue in found)
