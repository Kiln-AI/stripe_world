"""The replayer: a cassette walked against a live world instance.

Dispatch goes through the same tools an agent calls — read for `GET`, write
for `POST`/`DELETE` — via `instance.call(...)`, never the bare handler, so
parameter validation, the error envelope and (later) idempotency middleware
are genuinely in play during a conformance replay.

Comparison is a structural tree diff over `{"status", "body"}`: same JSON
type at every path, same value unless an `AllowedDifference` permits it, same
key set unless the missing key's own path is allow-listed. Every violation at
every path is collected before failing — a single broken handler produces
several related diffs and an author wants the whole picture in one run.

Two ways to drive:

- `replay(cassette, instance)` walks the cassette's steps directly, resolving
  each `Ref` against *this run's* earlier responses — never the recorded ids.
- `replay(cassette, instance, script=<a scenario's record fn>)` runs the
  scenario module itself against an instance-backed transport first, so a
  scenario edited after its cassette was recorded fails loudly at the
  position it drifted — a distinct failure, never a silent body mismatch.

The merge gate (`test_replay_conformance.py`) always passes the script.
"""

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import seahaven
from tools_dev.scenarios._dsl import Recorder, Wire

from conformance.allowed_differences import allowed
from conformance.cassette import Cassette, Ref, Step

__all__ = [
    "ConformanceFailure",
    "ReplayRun",
    "Violation",
    "check",
    "diff",
    "replay",
    "run",
]

SCRATCH_DIR = Path(__file__).parent / ".replay-out"

RecordFn = Callable[[Recorder], None]


@dataclass(frozen=True, slots=True)
class Violation:
    """One undeclared difference, at one path."""

    path: str
    recorded: Any
    replayed: Any
    note: str = ""


class ConformanceFailure(AssertionError):
    """Every undeclared difference in one message, plus where the full bodies
    were written — the message is built to be read without a debugger."""


# --- Dispatch --------------------------------------------------------------------


def _dispatch(instance: seahaven.Instance, wire: Wire) -> tuple[int, dict[str, Any]]:
    params = wire.params or None
    if wire.method == "GET":
        result = instance.call("stripe_api_read", path=wire.path, params=params)
    else:
        result = instance.call(
            "stripe_api_write",
            method=wire.method,
            path=wire.path,
            params=params,
            idempotency_key=wire.idempotency_key,
        )
    return int(result["status"]), result["body"]


def _concrete(step: Step, bindings: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """The path and params to dispatch: this run's ids where `Ref`s stand."""

    def sub(match: re.Match[str]) -> str:
        return str(step.path_refs[match.group(1)].resolve(bindings))

    path = re.sub(r"\{([a-z_]+)\}", sub, step.path)
    params = _resolve_refs(step.params, bindings)
    return path, params


def _resolve_refs(value: Any, bindings: dict[str, Any]) -> Any:
    if isinstance(value, Ref):
        return value.resolve(bindings)
    if isinstance(value, dict):
        return {key: _resolve_refs(item, bindings) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_refs(item, bindings) for item in value]
    return value


# --- The diff --------------------------------------------------------------------


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int | float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "unknown"


def diff(recorded: Any, replayed: Any, scenario: str, path: str = "") -> list[Violation]:
    """Every undeclared difference between the two trees, as `Violation`s.

    `bool` is never an `int` here even though Python says otherwise, and a
    missing key is a difference at that key's path. An allow-listed path
    prunes its whole subtree — "this path may differ arbitrarily" means
    everything under it too, which is what scenario 03's whole-body entry
    relies on — so no branch re-checks `allowed` at `path` itself. The two
    missing-key branches are the exception in shape only: a key absent on
    one side has no child call to prune, so its own (never-evaluated) path
    is checked right there.
    """
    if allowed(path, recorded, replayed, scenario) is not None:
        return []
    out: list[Violation] = []
    if _json_type(recorded) != _json_type(replayed):
        out.append(
            Violation(
                path,
                recorded,
                replayed,
                note=f"type {_json_type(recorded)} vs {_json_type(replayed)}",
            )
        )
        return out
    if isinstance(recorded, dict):
        for key in sorted(set(recorded) | set(replayed)):
            child = f"{path}.{key}" if path else key
            if key not in recorded:
                if allowed(child, None, replayed[key], scenario) is None:
                    out.append(Violation(child, "<absent>", replayed[key]))
            elif key not in replayed:
                if allowed(child, recorded[key], None, scenario) is None:
                    out.append(Violation(child, recorded[key], "<absent>"))
            else:
                out.extend(diff(recorded[key], replayed[key], scenario, child))
        return out
    if isinstance(recorded, list):
        if len(recorded) != len(replayed):
            out.append(
                Violation(
                    path,
                    f"<{len(recorded)} items>",
                    f"<{len(replayed)} items>",
                    note="list length",
                )
            )
            return out
        for index, (one, two) in enumerate(zip(recorded, replayed, strict=True)):
            out.extend(diff(one, two, scenario, f"{path}[{index}]"))
        return out
    if recorded != replayed:
        out.append(Violation(path, recorded, replayed))
    return out


# --- Replay ----------------------------------------------------------------------


@dataclass(slots=True)
class ReplayRun:
    """One replay's full outcome: the violations, and the replayed responses
    per position — the half that exists only at replay time and that a
    failing run writes beside the recorded bodies for side-by-side reading."""

    cassette: Cassette
    violations: list[Violation]
    replayed: list[tuple[int, dict[str, Any]]]  # (status, body) per step position


def run(
    cassette: Cassette,
    instance: seahaven.Instance,
    script: RecordFn | None = None,
) -> ReplayRun:
    """Walk `cassette.steps` against `instance`; every violation, never the
    first only, plus this run's responses. With `script`, run the scenario
    module itself first so a drifted script is its own loud failure (see the
    module docstring)."""
    replayed: list[tuple[int, dict[str, Any]]] = []
    if script is not None:
        recorder = Recorder(lambda wire: _dispatch(instance, wire))
        script(recorder)
        violations = _drift(cassette, recorder)
        replayed = [(captured.status, captured.body) for captured in recorder.captured]
    else:
        violations = []
        bindings: dict[str, Any] = {}
        for step in cassette.steps:
            path, params = _concrete(step, bindings)
            wire = Wire(
                method=step.method,
                path=path,
                params=params,
                idempotency_key=step.idempotency_key,
                stripe_version=step.recorded_stripe_version,
            )
            status, body = _dispatch(instance, wire)
            if step.binds_as is not None:
                bindings[step.binds_as] = body
            replayed.append((status, body))
    for (status, body), step in zip(replayed, cassette.steps, strict=True):
        violations.extend(
            diff(
                {"status": step.recorded_status, "body": step.recorded_body},
                {"status": status, "body": body},
                cassette.scenario,
            )
        )
    return ReplayRun(cassette=cassette, violations=violations, replayed=replayed)


def replay(
    cassette: Cassette,
    instance: seahaven.Instance,
    script: RecordFn | None = None,
) -> list[Violation]:
    """`run(...)`'s violations alone, for the callers that only want the
    verdict."""
    return run(cassette, instance, script).violations


def _drift(cassette: Cassette, recorder: Recorder) -> list[Violation]:
    """A scenario script that no longer matches its cassette: the distinct,
    loud failure, naming the file to re-record."""
    problems: list[str] = []
    captured = recorder.captured
    if len(captured) != len(cassette.steps):
        problems.append(
            f"the script performs {len(captured)} steps; the cassette recorded "
            f"{len(cassette.steps)}"
        )
    for index, step in enumerate(cassette.steps):
        if index >= len(captured):
            break
        one = captured[index]
        if (
            one.method != step.method
            or one.pattern != step.path
            or one.declared_params != step.params
            or one.path_refs != step.path_refs
            or one.idempotency_key != step.idempotency_key
        ):
            problems.append(
                f"step {step.seq}: cassette recorded {step.method} {step.path} "
                f"with {json.dumps(_plain(step.params))}, but the script would "
                f"dispatch {one.method} {one.pattern} with "
                f"{json.dumps(_plain(one.declared_params))}"
            )
        # The version is compared separately because a mismatch there can be
        # the *only* difference — scenario 03's allow-list entries permit the
        # whole response to differ, so a script that dropped its
        # `stripe_version_override` would otherwise diff green against the
        # malformed-version recording and silently test nothing.
        if one.stripe_version != step.recorded_stripe_version:
            problems.append(
                f"step {step.seq}: cassette recorded stripe_version "
                f"{step.recorded_stripe_version!r}, but the script would send "
                f"{one.stripe_version!r}"
            )
    if problems:
        raise ConformanceFailure(
            f"scenario '{cassette.scenario}' has drifted from its cassette — re-record it "
            f"(`python -m tools_dev.record --scenario {cassette.scenario}`):\n  "
            + "\n  ".join(problems)
        )
    return []


def _plain(value: Any) -> Any:
    """A Ref-bearing value as printable JSON (`$ref` spelled out)."""
    if isinstance(value, Ref):
        return {"$ref": {"step": value.step, "field": value.field}}
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return value


# --- The failure -----------------------------------------------------------------


def check(cassette: Cassette, instance: seahaven.Instance, script: RecordFn | None = None) -> None:
    """Replay and raise `ConformanceFailure` on any undeclared difference,
    with every violation listed and the full bodies — recorded *and*
    replayed — written to a scratch directory an inline diff of either would
    make unreadable. The replayed half exists only at replay time, so this is
    the only place it can land on disk."""
    outcome = run(cassette, instance, script)
    if not outcome.violations:
        return
    SCRATCH_DIR.mkdir(exist_ok=True)
    lines = [f"Conformance FAILED: scenario '{cassette.scenario}'"]
    lines.append("")
    lines.append(f"  {len(outcome.violations)} undeclared difference(s):")
    lines.append("")
    for violation in outcome.violations:
        lines.append(f"    {violation.path}{f' ({violation.note})' if violation.note else ''}")
        lines.append(f"      recorded (real Stripe):  {json.dumps(violation.recorded)}")
        lines.append(f"      replayed (this world):   {json.dumps(violation.replayed)}")
        lines.append("")
    lines.append(
        "  None of these paths is permitted by tests/conformance/allowed_differences.py.\n"
        "  Fix the handler, or add an AllowedDifference with a reason — never leave this failing."
    )
    recorded_at: list[str] = []
    replayed_at: list[str] = []
    for index, (step, (status, body)) in enumerate(
        zip(cassette.steps, outcome.replayed, strict=True)
    ):
        recorded_out = SCRATCH_DIR / f"{cassette.scenario}-step{index}-recorded.json"
        recorded_out.write_text(
            json.dumps({"status": step.recorded_status, "body": step.recorded_body}, indent=2)
            + "\n"
        )
        replayed_out = SCRATCH_DIR / f"{cassette.scenario}-step{index}-replayed.json"
        replayed_out.write_text(json.dumps({"status": status, "body": body}, indent=2) + "\n")
        recorded_at.append(str(recorded_out))
        replayed_at.append(str(replayed_out))
    lines.append(f"  Full recorded responses: {', '.join(recorded_at)}")
    lines.append(f"  Full replayed responses: {', '.join(replayed_at)}")
    lines.append(f"  (scratch dir {SCRATCH_DIR}, git-ignored)")
    raise ConformanceFailure("\n".join(lines))
