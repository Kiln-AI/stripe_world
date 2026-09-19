"""The recorder's gates, with no network and no `stripe` import: the key
refusal, the version pin through a stubbed transport, and the
registry/collection invariants the merge gate relies on.

`tools_dev/record.py` itself imports `stripe` and is exercised only by
recording for real; everything testable about its policy lives in the
stripe-free modules these tests import.
"""

import json
from pathlib import Path

import pytest
from tools_dev.scenarios import registry
from tools_dev.scenarios._dsl import PINNED_VERSION, Recorder
from tools_dev.scenarios._key import RecordRefused, resolve_api_key

from conformance.cassette import load

CASSETTES = Path(__file__).parent / "cassettes"


# --- The key gate ----------------------------------------------------------------


def test_recorder_refuses_without_a_test_mode_key(tmp_path: Path) -> None:
    with pytest.raises(RecordRefused, match="no API key"):
        resolve_api_key(environ={}, env_file=tmp_path / "absent.env")


def test_recorder_refuses_a_live_key(tmp_path: Path) -> None:
    with pytest.raises(RecordRefused, match="test-mode"):
        resolve_api_key(
            environ={"STRIPE_CONFORMANCE_TEST_KEY": "sk_live_51H8…" * 2},
            env_file=tmp_path / "absent.env",
        )


def test_the_env_file_fallback_is_read(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("STRIPE_PUB=pk_test_x\nSTRIPE_SECRET_KEY=sk_test_from_env_file\n")
    assert resolve_api_key(environ={}, env_file=env_file) == "sk_test_from_env_file"


def test_the_environment_beats_the_env_file(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("STRIPE_SECRET_KEY=sk_test_from_env_file\n")
    assert (
        resolve_api_key(
            environ={"STRIPE_CONFORMANCE_TEST_KEY": "rk_test_from_environ"},
            env_file=env_file,
        )
        == "rk_test_from_environ"
    )


# --- The version pin -------------------------------------------------------------


def test_recorder_pins_stripe_version_by_default() -> None:
    """Every captured step carries the pin read from the committed spec —
    the same constant the served shapes come from, so the two cannot drift.
    The override exists for scenario 03 alone."""
    seen: list[str] = []

    def transport(wire) -> tuple[int, dict]:
        seen.append(wire.stripe_version)
        return 200, {"id": "cus_1", "object": "customer"}

    recorder = Recorder(transport)
    recorder.step("GET", "/v1/customers", {"limit": 1})
    recorder.step(
        "GET", "/v1/customers", {"limit": 1}, stripe_version_override="not-a-real-version"
    )
    assert seen == [PINNED_VERSION, "not-a-real-version"]
    assert PINNED_VERSION == "2026-08-26.dahlia"  # the spec's info.version, read live


# --- Registry / collection invariants --------------------------------------------


def conformance_cassettes(directory: Path = CASSETTES) -> list[Path]:
    """The merge gate's collection: `NN_slug.json` conformance cassettes,
    with `probe_*` explicitly excluded by prefix."""
    return [p for p in sorted(directory.glob("*.json")) if not p.name.startswith("probe_")]


def test_scenario_registry_has_no_orphans() -> None:
    """Every curated `sNN_*.py` has its `NN_*.json`, and every conformance
    cassette has its scenario module — a cassette without a script cannot be
    drift-checked, and a script without a cassette replays nothing."""
    modules = {stem[1:] for stem in registry().values() if stem.startswith("s")}
    cassettes = {p.stem for p in conformance_cassettes()}
    assert modules == cassettes, (
        f"scenario modules without cassettes: {sorted(modules - cassettes)}; "
        f"cassettes without modules: {sorted(cassettes - modules)}"
    )


def test_probe_cassettes_are_excluded_from_conformance_collection(tmp_path: Path) -> None:
    (tmp_path / "02_kept.json").write_text("{}")
    (tmp_path / "probe_customers.json").write_text("{}")
    assert [p.name for p in conformance_cassettes(tmp_path)] == ["02_kept.json"]


def test_every_committed_cassette_scenario_name_matches_its_filename() -> None:
    for path in conformance_cassettes():
        assert load(path).scenario == path.stem


def test_committed_cassettes_have_well_formed_steps() -> None:
    """Structural sanity of what the gate loads: every cassette has steps,
    every step an integer status. (Cassette-vs-script agreement is the drift
    check's job, inside a live replay.)"""
    for path in conformance_cassettes():
        cassette = load(path)
        assert len(cassette.steps) > 0
        assert all(isinstance(s.recorded_status, int) for s in cassette.steps)


def test_the_recorder_dumps_what_the_dsl_captured(tmp_path: Path) -> None:
    """End to end through the stripe-free half of the recorder: a stubbed
    transport, the DSL, the scrub, the canonical dump, and the load — the
    exact pipeline `record.py` wraps around `stripe-python`."""
    from importlib import import_module

    from conformance import redact
    from conformance.cassette import Cassette, Step, dump

    def transport(wire) -> tuple[int, dict]:
        if wire.method == "GET":
            return 200, {"object": "list", "data": [], "has_more": False, "url": "/v1/customers"}
        return 200, {"id": "cus_stub", "object": "customer", "metadata": {}}

    recorder = Recorder(transport)
    module = import_module(
        f"tools_dev.scenarios.{registry()['02_pagination_cursor_against_deleted_id']}"
    )
    module.record(recorder)
    steps = []
    for captured in recorder.captured:
        step, findings = redact.scrub(
            Step(
                seq=captured.seq,
                method=captured.method,
                path=captured.pattern,
                path_refs=captured.path_refs,
                params=captured.declared_params,
                idempotency_key=captured.idempotency_key,
                binds_as=captured.binds_as,
                recorded_status=captured.status,
                recorded_body=captured.body,
                recorded_stripe_version=captured.stripe_version,
            )
        )
        assert findings == []
        steps.append(step)
    out = tmp_path / "probe.json"
    dump(Cassette("probe", "d", "t", PINNED_VERSION, tuple(steps)), out)
    loaded = load(out)
    assert len(loaded.steps) == len(recorder.captured)
    assert json.loads(out.read_text())["steps"][3]["path"] == "/v1/customers/{customer}"
