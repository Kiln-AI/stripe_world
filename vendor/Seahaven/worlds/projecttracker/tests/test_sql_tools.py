"""The two helper tools: the SQL door and the schema it is open onto.

Both are Seahaven's factories, registered in `tools/__init__.py` over this world's
nine tables. What is tested here is this world's decision -- which tables, and
what an agent meets when it uses them -- and not the factories themselves, which
have their own suite in the framework.
"""

import pytest

import seahaven
from conftest import AGENCY, BLANK_NOW, Scaffold, an_issue
from projecttracker.tools import TABLES

pytestmark = pytest.mark.seahaven(fixture=None, now=BLANK_NOW)


def test_the_door_is_open_onto_every_table_this_world_owns(
    instance: seahaven.Instance,
) -> None:
    """All nine, read one at a time: a table missing from the list is a table an
    agent is told does not exist, which is a harder thing to notice than a
    refusal."""
    assert len(TABLES) == 9
    for table in TABLES:
        answer = instance.call("run_sql", query=f"SELECT count(*) AS n FROM {table}")
        assert answer["columns"] == ["n"]
        assert answer["rows"] == [[0]]


def test_the_door_reads_the_rows_the_tools_wrote(
    instance: seahaven.Instance, scaffold: Scaffold
) -> None:
    an_issue(instance, scaffold)
    answer = instance.call(
        "run_sql",
        query=(
            "SELECT issues.key, users.name FROM issues"
            " JOIN users ON users.id = issues.creator_id ORDER BY issues.key"
        ),
    )
    assert answer == {
        "columns": ["key", "name"],
        "rows": [["ENG-1", "Ada"]],
        "row_count": 1,
        "truncated": False,
    }


def test_the_door_is_read_only(instance: seahaven.Instance, scaffold: Scaffold) -> None:
    issue = an_issue(instance, scaffold)
    with pytest.raises(seahaven.DbError) as raised:
        instance.call("run_sql", query="UPDATE issues SET title = 'rewritten'")
    assert "not allowed" in raised.value.message
    assert instance.call("get_issue", issue_id=issue["id"])["title"] == issue["title"]


def test_the_door_answers_a_typo_with_sqlites_own_words(instance: seahaven.Instance) -> None:
    """The one place engine text reaches an agent, because the product is a SQL console."""
    with pytest.raises(seahaven.DbError) as raised:
        instance.call("run_sql", query="SELCT * FROM issues")
    assert raised.value.code == "db_error"
    assert raised.value.message == raised.value.sqlite_message
    assert "syntax error" in raised.value.message


def test_the_search_index_is_not_behind_the_door(instance: seahaven.Instance) -> None:
    """`search_issues` is the way to search; a second way would need its shadow tables too."""
    with pytest.raises(seahaven.DbError) as raised:
        instance.call("run_sql", query="SELECT * FROM issues_fts")
    assert raised.value.details == {"refusals": ["read of table 'issues_fts'"]}


def test_the_schema_tool_describes_the_same_nine_tables(instance: seahaven.Instance) -> None:
    described = instance.call("describe_schema")
    assert [table["name"] for table in described["tables"]] == list(TABLES)


def test_the_schema_tool_names_keys_and_foreign_keys(instance: seahaven.Instance) -> None:
    """What an agent needs to write the join the SQL door will run."""
    tables = {table["name"]: table for table in instance.call("describe_schema")["tables"]}

    issues = tables["issues"]
    assert [column["name"] for column in issues["columns"] if column["primary_key"]] == ["id"]
    assert {column["name"] for column in issues["columns"] if column["nullable"]} == {
        "assignee_id",
        "due_at",
        "archived_at",
    }
    assert {
        (key["columns"][0], key["references_table"], key["references_columns"][0])
        for key in issues["foreign_keys"]
    } == {
        ("project_id", "projects", "id"),
        ("assignee_id", "users", "id"),
        ("creator_id", "users", "id"),
    }

    # A composite primary key, which is the shape a single-key assertion misses.
    assert [
        column["name"] for column in tables["team_members"]["columns"] if column["primary_key"]
    ] == ["team_id", "user_id"]


def test_the_schema_tool_writes_nothing_and_takes_no_arguments(
    instance: seahaven.Instance,
) -> None:
    listing = next(tool for tool in instance.tools() if tool["name"] == "describe_schema")
    assert listing["input_schema"]["properties"] == {}
    assert instance.change_log() == []
    instance.call("describe_schema")
    assert instance.change_log() == []


@pytest.mark.seahaven(fixture=AGENCY)
def test_the_door_answers_the_cross_team_question_the_fixture_is_for(
    instance: seahaven.Instance,
) -> None:
    """A report over the whole workspace, which is what `agency` exists to support."""
    answer = instance.call(
        "run_sql",
        query=(
            "SELECT teams.key, count(*) AS open_issues FROM issues"
            " JOIN projects ON projects.id = issues.project_id"
            " JOIN teams ON teams.id = projects.team_id"
            " WHERE issues.status NOT IN ('done', 'canceled')"
            " GROUP BY teams.key ORDER BY teams.key"
        ),
    )
    assert [row[0] for row in answer["rows"]] == ["DES", "ENG", "OPS"]
    # The three groups partition the whole: a `GROUP BY` that lost or duplicated
    # rows would still have answered with three plausible numbers.
    whole = instance.call(
        "run_sql",
        query="SELECT count(*) AS n FROM issues WHERE status NOT IN ('done', 'canceled')",
    )
    assert sum(row[1] for row in answer["rows"]) == whole["rows"][0][0]
