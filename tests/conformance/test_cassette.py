"""The cassette format: canonical serialization, `$ref` fidelity, and the
two loud failures the format exists to make possible."""

import json
from pathlib import Path

import pytest
from tools_dev.scenarios._dsl import Recorder

from conformance.cassette import Cassette, Ref, Step, dump, load

CASSETTES = Path(__file__).parent / "cassettes"


def _mask_recorded_at(text: str) -> list[str]:
    return [line for line in text.splitlines() if not line.startswith('"recorded_at"')]


@pytest.mark.parametrize("path", sorted(CASSETTES.glob("*.json")))
def test_cassette_json_is_canonically_formatted(path: Path, tmp_path: Path) -> None:
    """`dump(load(path))` reproduces the file byte-for-byte, `recorded_at`
    aside — the property that keeps a re-recording a small, legible diff
    instead of key-order churn."""
    round_tripped = tmp_path / path.name
    dump(load(path), round_tripped)
    assert _mask_recorded_at(round_tripped.read_text()) == _mask_recorded_at(path.read_text())


@pytest.mark.parametrize("path", sorted(CASSETTES.glob("*.json")))
def test_every_step_pins_the_declared_version(path: Path) -> None:
    """Every step carries the cassette's pin except where a scenario
    deliberately varies it — which is scenario 03 alone, by design."""
    cassette = load(path)
    for step in cassette.steps:
        if cassette.scenario == "03_malformed_stripe_version":
            assert step.recorded_stripe_version != cassette.stripe_version_pin
        else:
            assert step.recorded_stripe_version == cassette.stripe_version_pin


def test_ref_round_trips_through_the_file(tmp_path: Path) -> None:
    step = Step(
        seq=0,
        method="GET",
        path="/v1/payment_methods/{payment_method}",
        path_refs={"payment_method": Ref("pm", "id")},
        params={"customer": Ref("customer", "id"), "note": "plain", "nested": {"pm": Ref("pm")}},
        idempotency_key=None,
        binds_as=None,
        recorded_status=200,
        recorded_body={"id": "pm_1", "object": "payment_method"},
        recorded_stripe_version="2026-08-26.dahlia",
    )
    path = tmp_path / "c.json"
    dump(Cassette("x", "d", "2026-09-19T00:00:00+00:00", "2026-08-26.dahlia", (step,)), path)
    raw = json.loads(path.read_text())
    # The wire spellings: `$ref` inside params, `{step, field}` bare inside
    # path_refs (refs by nature — no ambiguity to mark).
    assert raw["steps"][0]["params"]["customer"] == {"$ref": {"step": "customer", "field": "id"}}
    assert raw["steps"][0]["path_refs"]["payment_method"] == {"step": "pm", "field": "id"}
    loaded = load(path)
    assert loaded.steps[0] == step


def test_load_rejects_a_step_whose_seq_is_not_its_position(tmp_path: Path) -> None:
    document = {
        "scenario": "x",
        "description": "d",
        "recorded_at": "t",
        "stripe_version_pin": "v",
        "steps": [_step_json(seq=3)],
    }
    path = tmp_path / "c.json"
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="not its position"):
        load(path)


def _step_json(seq: int) -> dict:
    return {
        "seq": seq,
        "method": "GET",
        "path": "/v1/customers",
        "path_refs": {},
        "params": {},
        "idempotency_key": None,
        "binds_as": None,
        "recorded_status": 200,
        "recorded_stripe_version": "v",
        "recorded_body": {},
    }


# --- The two loud failures -------------------------------------------------------


def test_step_ref_resolves_against_this_runs_ids_not_recorded_ids() -> None:
    """A synthetic two-step run whose replayed ids deliberately differ from
    the recorded ones: the second request must carry the ids the *stub run*
    produced, never the recorded literals."""
    sent: list[dict] = []

    def transport(wire) -> tuple[int, dict]:
        sent.append({"path": wire.path, "params": wire.params})
        # The "replay" answers with different ids than any recording holds.
        if wire.path == "/v1/customers":
            return 200, {"id": "cus_REPLAYED", "object": "customer"}
        return 200, {"id": "cus_other", "object": "customer"}

    recorder = Recorder(transport)
    customer = recorder.step("POST", "/v1/customers", {}, binds_as="customer")
    recorder.step(
        "GET",
        "/v1/customers/{customer}",
        {"starting_after": customer},
        path_refs={"customer": customer},
    )
    assert sent[1]["path"] == "/v1/customers/cus_REPLAYED"
    assert sent[1]["params"] == {"starting_after": "cus_REPLAYED"}


def test_a_dotted_ref_field_indexes_into_list_bodies() -> None:
    seen: dict = {}

    def transport(wire) -> tuple[int, dict]:
        seen[wire.params.get("limit")] = wire.params.get("starting_after")
        if wire.params.get("starting_after") is None:
            return 200, {"data": [{"id": "cus_0"}, {"id": "cus_1"}, {"id": "cus_2"}]}
        return 200, {"data": []}

    from tools_dev.scenarios._dsl import ref

    recorder = Recorder(transport)
    page = recorder.step("GET", "/v1/customers", {"limit": 3}, binds_as="page")
    recorder.step("GET", "/v1/customers", {"limit": 3, "starting_after": ref(page, "data[1].id")})
    assert seen[3] == "cus_1"
