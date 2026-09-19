"""The merge gate: every committed conformance cassette replays green
against a fresh `empty`-fixture instance, with its scenario script driving
(so a drifted script fails as itself), through the world's own tools, with
no network and no recording dependency.

A scenario here is fully self-contained by construction: the `binds_as` /
`Ref` machinery cannot name an object the scenario did not itself create,
which is why the result never depends on fixture content or on what else
the recording account happened to hold.
"""

from importlib import import_module
from pathlib import Path

import pytest
import seahaven
from tools_dev.scenarios import registry

from conformance.cassette import load
from conformance.replay import check

pytestmark = pytest.mark.seahaven(fixture="empty")

CASSETTES = Path(__file__).parent / "cassettes"


def conformance_cassettes() -> list[str]:
    return [p.stem for p in sorted(CASSETTES.glob("*.json")) if not p.name.startswith("probe_")]


@pytest.mark.parametrize("scenario", conformance_cassettes())
def test_replay_conformance(scenario: str, instance: seahaven.Instance) -> None:
    cassette = load(CASSETTES / f"{scenario}.json")
    module = import_module(f"tools_dev.scenarios.{registry()[scenario]}")
    check(cassette, instance, script=module.record)
