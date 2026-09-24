"""The merge gate: every committed conformance cassette replays green
against a fresh instance, with its scenario script driving (so a drifted
script fails as itself), through the world's own middleware, with no
network and no recording dependency.

The instance comes from a probe world that registers ``call_stripe`` as a
tool (via ``conftest.dispatch_tool()``), so the replay exercises the
shipped middleware chain -- error handler, stripe envelope, idempotency --
without polluting the production world's tool surface.

A scenario here is fully self-contained by construction: the ``binds_as`` /
``Ref`` machinery cannot name an object the scenario did not itself create,
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
from conftest import BLANK_NOW, dispatch_tool

CASSETTES = Path(__file__).parent / "cassettes"


@pytest.fixture
def instance(probe):
    """A probe-world instance with ``call_stripe`` registered, so the
    conformance replay exercises the real middleware chain."""
    w = probe(dispatch_tool())
    with w.instance(None, now=BLANK_NOW) as inst:
        yield inst


def conformance_cassettes() -> list[str]:
    return [p.stem for p in sorted(CASSETTES.glob("*.json")) if not p.name.startswith("probe_")]


@pytest.mark.parametrize("scenario", conformance_cassettes())
def test_replay_conformance(scenario: str, instance: seahaven.Instance) -> None:
    cassette = load(CASSETTES / f"{scenario}.json")
    module = import_module(f"tools_dev.scenarios.{registry()[scenario]}")
    check(cassette, instance, script=module.record)
