"""Projects: the four tools in `tools/projects.py`."""

import pytest

import seahaven
from conftest import AGENCY, BLANK_NOW, Scaffold

pytestmark = pytest.mark.seahaven(fixture=None, now=BLANK_NOW)


def test_creating_a_project_answers_with_the_row(
    instance: seahaven.Instance, scaffold: Scaffold
) -> None:
    created = instance.call("create_project", team_id=scaffold.team, name="Mobile")
    assert created == {
        "id": created["id"],
        "team_id": scaffold.team,
        "name": "Mobile",
        "state": "planned",
        "created_at": BLANK_NOW,
    }
    assert instance.call("get_project", project_id=created["id"]) == created


def test_a_project_under_a_team_that_does_not_exist_is_not_found(
    instance: seahaven.Instance,
) -> None:
    with pytest.raises(seahaven.ToolError) as raised:
        instance.call("create_project", team_id="nobody", name="Mobile")
    assert raised.value.to_dict()["details"] == {"kind": "team", "key": "nobody"}


def test_an_unknown_project_is_not_found(instance: seahaven.Instance) -> None:
    with pytest.raises(seahaven.ToolError) as raised:
        instance.call("get_project", project_id="nowhere")
    assert raised.value.code == "NOT_FOUND"


def test_a_state_the_product_does_not_have_is_refused(
    instance: seahaven.Instance, scaffold: Scaffold
) -> None:
    with pytest.raises(seahaven.ToolError) as raised:
        instance.call("create_project", team_id=scaffold.team, name="Mobile", state="shipped")
    assert raised.value.code == "INVALID_INPUT"
    assert raised.value.details == {"field": "state"}


def test_the_list_filters_by_team_and_by_state(
    instance: seahaven.Instance, scaffold: Scaffold
) -> None:
    other = instance.call("create_team", key="DES", name="Design")
    planned = instance.call("create_project", team_id=scaffold.team, name="Mobile")
    elsewhere = instance.call("create_project", team_id=other["id"], name="Brand", state="done")

    assert [p["id"] for p in instance.call("list_projects", team_id=other["id"])["projects"]] == [
        elsewhere["id"]
    ]
    assert [p["id"] for p in instance.call("list_projects", state="planned")["projects"]] == [
        planned["id"]
    ]
    assert instance.call("list_projects", team_id=other["id"], state="planned")["projects"] == []
    assert len(instance.call("list_projects")["projects"]) == 3


def test_listing_the_projects_of_a_team_that_does_not_exist_is_not_found(
    instance: seahaven.Instance,
) -> None:
    with pytest.raises(seahaven.ToolError) as raised:
        instance.call("list_projects", team_id="nobody")
    assert raised.value.code == "NOT_FOUND"


def test_an_update_changes_only_what_it_names(
    instance: seahaven.Instance, scaffold: Scaffold
) -> None:
    project = instance.call("get_project", project_id=scaffold.project)
    renamed = instance.call("update_project", project_id=project["id"], name="Platform Core")
    assert renamed == project | {"name": "Platform Core"}
    moved = instance.call("update_project", project_id=project["id"], state="done")
    assert moved == renamed | {"state": "done"}


def test_an_update_that_names_nothing_is_refused(
    instance: seahaven.Instance, scaffold: Scaffold
) -> None:
    """It would answer with the project unchanged and look like it had done something."""
    with pytest.raises(seahaven.ToolError) as raised:
        instance.call("update_project", project_id=scaffold.project)
    assert raised.value.code == "INVALID_INPUT"


def test_updating_a_project_that_does_not_exist_is_not_found(
    instance: seahaven.Instance,
) -> None:
    with pytest.raises(seahaven.ToolError) as raised:
        instance.call("update_project", project_id="nowhere", name="Anything")
    assert raised.value.code == "NOT_FOUND"


@pytest.mark.seahaven(fixture=AGENCY)
def test_the_agency_fixture_has_nine_projects_one_of_them_finished(
    instance: seahaven.Instance,
) -> None:
    listed = instance.call("list_projects", limit=50)["projects"]
    assert len(listed) == 9
    assert [p["state"] for p in listed].count("done") == 1
