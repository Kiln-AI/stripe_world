"""The committed `empty` fixture: what it is on disk and what an instance of it
starts from.

Phase 1's schema is the placeholder file, so "schema only, no rows" is "no
tables" until the resource phases add theirs. The test asserts that honestly
rather than pretending otherwise: what it pins is that the fixture exists, is
frozen at the one instant, and that nothing — table or row — rides along with
it.
"""

import pytest
import seahaven
from seahaven.db import world_tables

from conftest import FIXTURE_NOW

pytestmark = pytest.mark.seahaven(fixture="empty")


def test_the_empty_fixture_is_committed(world: seahaven.World) -> None:
    fixtures = {frozen.id: frozen for frozen in world.fixtures()}
    assert set(fixtures) == {"empty"}
    empty = fixtures["empty"]
    assert empty.now == FIXTURE_NOW
    assert empty.parent_id is None
    assert empty.description.startswith("Schema only.")


def test_an_instance_of_empty_starts_from_nothing(instance: seahaven.Instance) -> None:
    assert world_tables(instance.inspect().conn) == []
    assert instance.inspect().one(
        "SELECT count(*) AS n FROM sqlite_master WHERE type = 'table'"
    ) == {"n": 0}
