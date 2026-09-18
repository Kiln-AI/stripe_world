"""One seed, one run: the property an eval that compares two runs depends on.

`functional_spec.md` §12 puts reproducibility on the world rather than on the
framework -- Seahaven promises a fixed clock and a seeded source, and a world that
reaches past `ctx` for either gets what it asked for. This module is where this
world says it does not: a scripted sequence of calls against one fixture and one
seed produces the same identifiers, the same keys and the same change log every
time, and a different seed produces different identifiers.
"""

from typing import Any

import pytest

import seahaven
from conftest import SMALL_STARTUP
from projecttracker.world import world


def scripted(seed: int) -> tuple[list[Any], list[dict[str, Any]]]:
    """The same eight calls, on a fresh instance of `small_startup` at `seed`.

    Deliberately a mix: two writes that mint ids, one that mints a key, one that
    appends to the trail, and reads between them. A script of reads alone would
    be reproducible in any world.
    """
    with world.instance(SMALL_STARTUP, seed=seed) as instance:
        project = instance.call("list_projects")["projects"][0]
        person = instance.call("create_user", email="new@tracker.invalid", name="New")
        issue = instance.call("create_issue", project_id=project["id"], title="Filed by the script")
        instance.call("assign_issue", issue_id=issue["id"], assignee_id=person["id"])
        instance.call("add_comment", issue_id=issue["id"], body="On it.")
        instance.call("transition_issue", issue_id=issue["id"], status="in_progress")
        answers = [person, issue, instance.call("get_issue", issue_id=issue["id"])]
        return answers, [record.to_dict() for record in instance.change_log()]


def test_one_seed_replays_the_ids_the_keys_and_the_change_log() -> None:
    """Two runs of the same script agree on everything an eval could grade."""
    first_answers, first_log = scripted(seed=7)
    second_answers, second_log = scripted(seed=7)
    assert first_answers == second_answers
    assert first_log == second_log
    # And it really did write something, so agreeing is not agreeing about nothing.
    assert first_answers[1]["key"] == "ENG-41"
    # `teams` is in there because minting a key bumps the team's counter, which
    # is a row change like any other and is exactly the kind of thing a log
    # comparison would otherwise miss.
    assert {record["table"] for record in first_log} == {
        "users",
        "teams",
        "issues",
        "comments",
        "issue_events",
    }


def test_another_seed_gives_another_run() -> None:
    """The ids differ; everything the product decides does not.

    A world whose identifiers were constant would pass the test above and be
    useless for the thing seeds are for, which is why the two are a pair. The
    key is not an identifier -- it is the tracker's own counter -- so it is the
    same under both seeds, and that is the point of splitting the assertion.
    """
    seven, seven_log = scripted(seed=7)
    eight, eight_log = scripted(seed=8)
    assert [row["id"] for row in seven] != [row["id"] for row in eight]
    assert seven_log != eight_log
    assert seven[1]["key"] == eight[1]["key"] == "ENG-41"
    assert seven[1]["title"] == eight[1]["title"]


def test_the_default_seed_is_a_seed_like_any_other() -> None:
    """`seed=None` is `b"default"`, not "no seed", so it replays too."""
    with world.instance(SMALL_STARTUP) as first, world.instance(SMALL_STARTUP) as second:
        assert first.seed == second.seed
        assert (
            first.call("create_user", email="a@tracker.invalid", name="A")["id"]
            == (second.call("create_user", email="a@tracker.invalid", name="A")["id"])
        )


def test_two_fixtures_at_one_seed_are_two_streams() -> None:
    """The seed is derived from the fixture id too, so one seed is not one stream."""
    with (
        world.instance(SMALL_STARTUP, seed=7) as startup,
        world.instance("empty", seed=7) as empty,
    ):
        assert startup.seed != empty.seed
        assert (
            startup.call("create_user", email="a@tracker.invalid", name="A")["id"]
            != (empty.call("create_user", email="a@tracker.invalid", name="A")["id"])
        )


@pytest.mark.seahaven(fixture=SMALL_STARTUP, seed=7)
def test_the_marker_carries_the_seed_to_the_instance(instance: seahaven.Instance) -> None:
    """How a test asks for a seed: the plugin passes everything but `fixture` through."""
    assert instance.seed == seahaven.ids.instance_seed(SMALL_STARTUP, 7)
