"""The replayer against real instances: every violation at once, allow-listed
differences passing, refs bound to this run, and drifted scripts failing as
themselves.

The synthetic cassettes here are built from a *real* replayed body with
specific fields perturbed, so a violation always means the perturbation —
never the ordinary shape gap between a five-key sketch and a real object."""

import json

import pytest
import seahaven
from tools_dev.scenarios._dsl import Recorder

from conformance.cassette import Cassette, Ref, Step
from conformance.replay import ConformanceFailure, check, diff, replay
from conftest import BLANK_NOW

pytestmark = pytest.mark.seahaven(fixture=None, now=BLANK_NOW)


def a_cassette(recorded_body: dict, scenario: str = "synthetic") -> Cassette:
    return Cassette(
        scenario=scenario,
        description="synthetic",
        recorded_at="2026-09-19T00:00:00+00:00",
        stripe_version_pin="2026-08-26.dahlia",
        steps=(
            Step(
                seq=0,
                method="POST",
                path="/v1/customers",
                path_refs={},
                params={"description": "probe"},
                idempotency_key=None,
                binds_as=None,
                recorded_status=200,
                recorded_body=recorded_body,
                recorded_stripe_version="2026-08-26.dahlia",
            ),
        ),
    )


def a_real_body(instance: seahaven.Instance) -> dict:
    return instance.call(
        "stripe_api_write", method="POST", path="/v1/customers", params={"description": "probe"}
    )


def test_replayer_reports_every_undeclared_difference_at_once(instance: seahaven.Instance) -> None:
    """Two wrong fields, both named: a replay never stops at the first
    mismatch, because one broken handler produces several related diffs. The
    failure message also names where both full bodies — recorded *and*
    replayed — were written: the replayed half exists only at replay time,
    so the scratch file is the only place to read it."""
    recorded = dict(a_real_body(instance))
    recorded["description"] = "recorded says this"
    recorded["metadata"] = {"tag": "recorded"}
    with pytest.raises(ConformanceFailure) as raised:
        check(a_cassette(recorded, scenario="scratch_probe"), instance)
    message = str(raised.value)
    assert "body.description" in message
    assert "body.metadata.tag" in message
    assert "2 undeclared difference" in message
    # The allow-listed ones (`id`, `created`, `invoice_prefix`) are not
    # differences at all.
    assert "body.id" not in message
    assert "body.created" not in message
    assert "Full recorded responses:" in message
    assert "Full replayed responses:" in message
    from conformance.replay import SCRATCH_DIR

    replayed_file = SCRATCH_DIR / "scratch_probe-step0-replayed.json"
    recorded_file = SCRATCH_DIR / "scratch_probe-step0-recorded.json"
    assert replayed_file.exists() and recorded_file.exists()
    written = json.loads(replayed_file.read_text())
    assert written["status"] == 200
    # This run's body, not the recorded one: the field the recording changed
    # is the value the replay actually produced.
    assert written["body"]["description"] == "probe"


def test_replayer_passes_when_only_allow_listed_fields_differ(instance: seahaven.Instance) -> None:
    recorded = dict(a_real_body(instance))
    recorded["id"] = "cus_record_time"
    recorded["created"] = 1789855000
    recorded["invoice_prefix"] = "RECORDED"
    assert replay(a_cassette(recorded), instance) == []


def test_undeclared_missing_key_is_a_violation(instance: seahaven.Instance) -> None:
    """A recorded key the replayed body omits entirely — a serializer that
    stops emitting a field must be caught, not silently skipped."""
    recorded = dict(a_real_body(instance))
    recorded["future_field"] = "recorded has it"
    assert [v.path for v in replay(a_cassette(recorded), instance)] == ["body.future_field"]


def test_an_extra_replayed_key_is_a_violation(instance: seahaven.Instance) -> None:
    recorded = dict(a_real_body(instance))
    del recorded["metadata"]
    assert [v.path for v in replay(a_cassette(recorded), instance)] == ["body.metadata"]


def test_bool_is_never_an_int() -> None:
    violations = diff({"flag": 1}, {"flag": True}, "synthetic")
    assert violations and violations[0].path == "flag"


def test_list_length_is_a_violation_at_the_list_path() -> None:
    assert [v.path for v in diff({"data": [1, 2]}, {"data": [1]}, "s")] == ["data"]
    assert diff({"data": [1, 2]}, {"data": [1, 2]}, "s") == []


def test_drifted_scenario_fails_loudly_not_silently(instance: seahaven.Instance) -> None:
    """A script that would dispatch a different call than its cassette's step
    0 recorded fails with the position mismatch — never a body diff."""
    step = Step(
        seq=0,
        method="GET",
        path="/v1/customers",
        path_refs={},
        params={"limit": 1},
        idempotency_key=None,
        binds_as=None,
        recorded_status=200,
        recorded_body={"object": "list", "data": [], "has_more": False, "url": "/v1/customers"},
        recorded_stripe_version="2026-08-26.dahlia",
    )
    cassette = Cassette("drift", "d", "t", "2026-08-26.dahlia", (step,))

    def drifted_script(r: Recorder) -> None:
        # The same wired call with different parameters than the cassette's
        # step 0 recorded — the dispatch succeeds, so the failure can only
        # come from the drift check, which is the point.
        r.step("GET", "/v1/customers", {"limit": 2})

    with pytest.raises(ConformanceFailure, match=r"drifted from its cassette.*re-record"):
        replay(cassette, instance, script=drifted_script)


def test_a_dropped_version_override_is_drift_not_a_green_replay(
    instance: seahaven.Instance,
) -> None:
    """The scenario-03 hazard: its allow-list entries permit the whole
    response to differ, so a script that dropped its
    `stripe_version_override` would diff green against the malformed-version
    recording and silently test nothing. The drift check compares the
    version per step and fails as drift instead."""
    step = Step(
        seq=0,
        method="GET",
        path="/v1/customers",
        path_refs={},
        params={"limit": 1},
        idempotency_key=None,
        binds_as=None,
        recorded_status=400,
        recorded_body={"error": {"type": "invalid_request_error", "message": "Invalid …"}},
        recorded_stripe_version="not-a-real-version",
    )
    cassette = Cassette("03_shaped", "d", "t", "2026-08-26.dahlia", (step,))

    def override_dropped(r: Recorder) -> None:
        # Identical call and params — only the version differs: the script
        # sends the pin where the cassette recorded the malformed value.
        r.step("GET", "/v1/customers", {"limit": 1})

    with pytest.raises(ConformanceFailure, match="stripe_version") as raised:
        replay(cassette, instance, script=override_dropped)
    assert "not-a-real-version" in str(raised.value)


def test_an_undrifted_script_replays_clean(instance: seahaven.Instance) -> None:
    """The same script that recorded the cassette replays it green through
    `replay(..., script=...)`: ids come from this run, not the recording."""
    real = a_real_body(instance)

    def script(r: Recorder) -> None:
        customer = r.step("POST", "/v1/customers", {"description": "probe"}, binds_as="customer")
        r.step(
            "GET",
            "/v1/customers/{customer}",
            path_refs={"customer": customer},
        )

    recorded = dict(real, id="cus_RECORD_TIME_ID", created=1789855000)
    step_create = Step(
        seq=0,
        method="POST",
        path="/v1/customers",
        path_refs={},
        params={"description": "probe"},
        idempotency_key=None,
        binds_as="customer",
        recorded_status=200,
        recorded_body=recorded,
        recorded_stripe_version="2026-08-26.dahlia",
    )
    step_get = Step(
        seq=1,
        method="GET",
        path="/v1/customers/{customer}",
        path_refs={"customer": Ref("customer", "id")},
        params={},
        idempotency_key=None,
        binds_as=None,
        recorded_status=200,
        recorded_body=recorded,
        recorded_stripe_version="2026-08-26.dahlia",
    )
    cassette = Cassette("undrifted", "d", "t", "2026-08-26.dahlia", (step_create, step_get))
    assert replay(cassette, instance, script=script) == []
