"""The cassette file format: one committed JSON file per scenario.

A cassette is a short, ordered script — "create a customer, attach a card,
charge it, retry the charge under the same idempotency key" — and the unit a
human reviews and a re-recording touches. Requests are keyed for replay **by
position** (`seq`): the replayer executes the steps in order and nothing else,
so two identical requests at positions 3 and 7 are simply steps 3 and 7, and
an idempotent-retry scenario is a direct two-step comparison rather than
something a matcher must be taught not to collapse.

Ids flow across steps explicitly, never by scanning for id-shaped strings:
where a request value came from an earlier step's response, the value is a
`Ref` and the replayer substitutes *this run's* id at dispatch time. The
literal ids real Stripe returned stay in `recorded_body` — that is the oracle
the diff compares against, and the allow-list is what says "don't compare
`id` here".

`dump` is the only writer of cassette files and its serialization is
canonical — fixed key order per object type, 2-space indent,
`ensure_ascii=False`, trailing newline — so a re-recording that changes
nothing material produces a near-empty diff.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = ["Cassette", "Ref", "Step", "dump", "load"]

# The one reserved marker inside `params` / `path_refs` JSON: an object of
# exactly this shape decodes to a `Ref` on load. A scenario never sends such
# an object literally.
_REF_KEY = "$ref"

_FIELD_SEGMENT = re.compile(r"^(?P<name>[^\[\]]+)((?P<index>\[\d+\])*)$")
_DIGITS = re.compile(r"\[(\d+)\]")


@dataclass(frozen=True, slots=True)
class Ref:
    """A placeholder: "use the value this replay run actually produced," not
    the literal recorded value. Only meaningful inside a `Step`'s `path`
    placeholders (`path_refs`) or `params` values."""

    step: str  # the `binds_as` name of an earlier step in the same scenario
    field: str = "id"  # dotted path into that step's response body

    def resolve(self, bindings: dict[str, Any]) -> Any:
        """Walk `field` (`id`, `data[3].id`, …) through a bound response body."""
        if self.step not in bindings:
            raise KeyError(f"Ref names step {self.step!r}, which no earlier step bound")
        value: Any = bindings[self.step]
        for segment in self.field.split("."):
            matched = _FIELD_SEGMENT.match(segment)
            if matched is None:
                raise KeyError(f"Ref field {self.field!r} has an unparseable segment {segment!r}")
            name = matched.group("name")
            if not isinstance(value, dict) or name not in value:
                raise KeyError(f"Ref field {self.field!r}: {name!r} is absent from the bound body")
            value = value[name]
            for index in _DIGITS.findall(matched.group("index") or ""):
                if not isinstance(value, list) or int(index) >= len(value):
                    raise KeyError(f"Ref field {self.field!r}: index {index} is out of range")
                value = value[int(index)]
        return value


@dataclass(frozen=True, slots=True)
class Step:
    seq: int
    method: str  # "GET" | "POST" | "DELETE"
    path: str  # the route pattern; concrete ids travel as `path_refs`, never here
    path_refs: dict[str, Ref]  # placeholder name -> Ref, substituted before dispatch
    params: dict[str, Any]  # literal values, or Refs substituted at replay time
    idempotency_key: str | None
    binds_as: str | None  # the name later steps may Ref against; None if unused
    recorded_status: int
    recorded_body: dict[str, Any]
    recorded_stripe_version: str  # normally the pin; scenario 3 deliberately varies it


@dataclass(frozen=True, slots=True)
class Cassette:
    scenario: str
    description: str
    recorded_at: str  # ISO 8601, informational only, never diffed
    stripe_version_pin: str
    steps: tuple[Step, ...]


# --- Canonical JSON --------------------------------------------------------------
#
# Key order is the dataclass field order for Step/Cassette and insertion order
# (the order real Stripe sent) for response bodies; `sort_keys` is off so a
# recorded body round-trips byte-identically rather than being re-alphabetized
# into a whole-file diff.


def _dump_ref(ref: Ref) -> dict[str, Any]:
    return {_REF_KEY: {"step": ref.step, "field": ref.field}}


def _dump_value(value: Any) -> Any:
    if isinstance(value, Ref):
        return _dump_ref(value)
    if isinstance(value, dict):
        return {key: _dump_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_dump_value(item) for item in value]
    return value


def _step_to_json(step: Step) -> dict[str, Any]:
    return {
        "seq": step.seq,
        "method": step.method,
        "path": step.path,
        # `{step, field}` directly — refs by nature, no ambiguity to mark.
        "path_refs": {
            name: {"step": ref.step, "field": ref.field} for name, ref in step.path_refs.items()
        },
        "params": _dump_value(step.params),
        "idempotency_key": step.idempotency_key,
        "binds_as": step.binds_as,
        "recorded_status": step.recorded_status,
        "recorded_stripe_version": step.recorded_stripe_version,
        "recorded_body": step.recorded_body,
    }


def dump(cassette: Cassette, path: Path) -> None:
    """Write the one canonical serialization. Re-running `dump` on a `load`ed
    cassette reproduces the file byte-for-byte."""
    document = {
        "scenario": cassette.scenario,
        "description": cassette.description,
        "recorded_at": cassette.recorded_at,
        "stripe_version_pin": cassette.stripe_version_pin,
        "steps": [_step_to_json(step) for step in cassette.steps],
    }
    text = json.dumps(document, indent=2, ensure_ascii=False) + "\n"
    path.write_text(text, encoding="utf-8")


def _load_ref(value: Any, where: str) -> Ref:
    if not isinstance(value, dict) or set(value) != {"step", "field"}:
        raise ValueError(f"{where}: expected a {{step, field}} object, got {value!r}")
    if not isinstance(value["step"], str) or not isinstance(value["field"], str):
        raise ValueError(f"{where}: a ref's step and field must be strings")
    return Ref(step=value["step"], field=value["field"])


def _load_value(value: Any, where: str) -> Any:
    if isinstance(value, dict) and set(value) == {_REF_KEY}:
        return _load_ref(value[_REF_KEY], where)
    if isinstance(value, dict):
        return {key: _load_value(item, f"{where}.{key}") for key, item in value.items()}
    if isinstance(value, list):
        return [_load_value(item, f"{where}[{index}]") for index, item in enumerate(value)]
    return value


def _step_from_json(raw: dict[str, Any], where: str) -> Step:
    expected = {
        "seq",
        "method",
        "path",
        "path_refs",
        "params",
        "idempotency_key",
        "binds_as",
        "recorded_status",
        "recorded_stripe_version",
        "recorded_body",
    }
    if set(raw) != expected:
        raise ValueError(f"{where}: step keys are {sorted(raw)}, expected {sorted(expected)}")
    method = raw["method"]
    if method not in ("GET", "POST", "DELETE"):
        raise ValueError(f"{where}: method {method!r} is not one GET/POST/DELETE")
    if not isinstance(raw["recorded_body"], dict):
        raise ValueError(f"{where}: recorded_body must be an object")
    return Step(
        seq=raw["seq"],
        method=method,
        path=raw["path"],
        # path_refs values are refs by nature — `{step, field}` directly,
        # no `$ref` wrapper (that marker exists only inside `params`, where
        # a literal could be ambiguous).
        path_refs={
            name: _load_ref(ref, f"{where}.path_refs.{name}")
            for name, ref in raw["path_refs"].items()
        },
        params=_load_value(raw["params"], f"{where}.params"),
        idempotency_key=raw["idempotency_key"],
        binds_as=raw["binds_as"],
        recorded_status=raw["recorded_status"],
        recorded_body=raw["recorded_body"],
        recorded_stripe_version=raw["recorded_stripe_version"],
    )


def load(path: Path) -> Cassette:
    document = json.loads(path.read_text(encoding="utf-8"))
    if set(document) != {
        "scenario",
        "description",
        "recorded_at",
        "stripe_version_pin",
        "steps",
    }:
        raise ValueError(f"{path}: cassette keys are {sorted(document)}")
    steps = tuple(_step_from_json(raw, f"{path} step {raw['seq']}") for raw in document["steps"])
    for index, step in enumerate(steps):
        if step.seq != index:
            raise ValueError(f"{path}: step seq {step.seq} is not its position {index}")
    return Cassette(
        scenario=document["scenario"],
        description=document["description"],
        recorded_at=document["recorded_at"],
        stripe_version_pin=document["stripe_version_pin"],
        steps=steps,
    )
