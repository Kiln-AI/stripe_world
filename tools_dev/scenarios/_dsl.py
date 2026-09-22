"""The scenario DSL: the one ``step(...)`` primitive every recording uses.

A scenario module is executable twice by the same code — once by
``tools_dev/record.py`` against real Stripe test mode, once by
``tests/conformance/replay.py`` against a world instance — because the
``Recorder`` is transport-agnostic: record time resolves every `Ref` against
*real Stripe's* responses and sends the literal on the wire; replay time
resolves the same `Ref` against *this run's* responses and dispatches through
the world's own tools. That double run is what makes scenario-script drift
loud: a scenario edited after its cassette was recorded disagrees with the
cassette at the position it drifted, before any body is diffed.

This module never imports ``stripe`` — only the recorder's transport does —
so the replay path and every test importing a scenario module stay
network-free (`components/conformance.md` Dependencies).

Entry points bootstrap ``sys.path`` so ``conformance.*`` (under ``tests/``)
and ``tools_dev`` (repo root) are both importable; importing this module from
anywhere else is a bug.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from conformance.cassette import Ref
from seahaven_stripe_world.spec import pinned_version

__all__ = ["PINNED_VERSION", "Recorder", "StepHandle", "ref"]

#: The one API version this world serves and every recorded request pins,
#: read from the committed spec so the recorder's pin and the served shapes
#: are the same fact rather than two constants.
PINNED_VERSION = pinned_version()

_PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")


@dataclass(frozen=True, slots=True)
class StepHandle:
    """What ``Recorder.step`` returns: the name later steps `ref()` against."""

    name: str


def ref(handle: StepHandle | str, field_: str = "id", *, offset: int = 0) -> Ref:
    """Declare a value wired to an earlier step's response (`field_` is a
    dotted path into that step's body, `data[3].id` included; `offset` adds
    a constant to an integer field — the proration tie engineering, stored
    relatively so record and replay each resolve their own). Passing the
    handle itself — or the bare binds_as name — elsewhere in a step means the
    same as `ref(...)` with the default field."""
    name = handle.name if isinstance(handle, StepHandle) else handle
    return Ref(step=name, field=field_, offset=offset)


def _as_ref(value: StepHandle | str | Ref) -> Ref:
    if isinstance(value, Ref):
        return value
    return ref(value)


@dataclass(frozen=True, slots=True)
class Wire:
    """One dispatchable request with every `Ref` already resolved."""

    method: str
    path: str  # concrete — placeholders substituted
    params: dict[str, Any]  # literals only
    idempotency_key: str | None
    stripe_version: str


Transport = Callable[[Wire], tuple[int, dict[str, Any]]]
#: Returns ``(HTTP status, parsed JSON body)`` and raises nothing — error
#: statuses arrive as values exactly like success ones, mirroring the world's
#: own ``{"status", "body"}`` contract.


@dataclass(slots=True)
class Captured:
    """Everything one ``step()`` declared and got back, position-tagged."""

    seq: int
    method: str
    pattern: str  # the route pattern, placeholders intact
    path_refs: dict[str, Ref]
    declared_params: dict[str, Any]  # with Refs intact
    idempotency_key: str | None
    binds_as: str | None
    stripe_version: str
    status: int
    body: dict[str, Any]


@dataclass(slots=True)
class Recorder:
    """Drives one scenario, record time or replay time, through a transport."""

    transport: Transport
    captured: list[Captured] = field(default_factory=list)
    _bindings: dict[str, Any] = field(default_factory=dict)

    def step(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        path_refs: dict[str, Ref | StepHandle | str] | None = None,
        binds_as: str | None = None,
        idempotency_key: str | None = None,
        stripe_version_override: str | None = None,
    ) -> StepHandle:
        """One request. `path` is the route pattern; `path_refs` names which
        placeholders take an earlier step's value. Returns the handle other
        steps `ref()` against when `binds_as` is given."""
        if binds_as is not None and binds_as in self._bindings:
            raise ValueError(f"step {len(self.captured)}: binds_as {binds_as!r} is already bound")
        refs = {name: _as_ref(value) for name, value in (path_refs or {}).items()}
        placeholders = set(_PLACEHOLDER.findall(path))
        if placeholders != set(refs):
            raise ValueError(
                f"step {len(self.captured)}: path {path!r} placeholders {sorted(placeholders)} "
                f"do not match path_refs {sorted(refs)}"
            )
        concrete = _substitute(path, refs, self._bindings)
        wire_params = _resolve(params or {}, self._bindings)
        version = PINNED_VERSION if stripe_version_override is None else stripe_version_override
        status, body = self.transport(
            Wire(
                method=method,
                path=concrete,
                params=wire_params,
                idempotency_key=idempotency_key,
                stripe_version=version,
            )
        )
        self.captured.append(
            Captured(
                seq=len(self.captured),
                method=method,
                pattern=path,
                path_refs=refs,
                declared_params=_declared(params or {}),
                idempotency_key=idempotency_key,
                binds_as=binds_as,
                stripe_version=version,
                status=status,
                body=body,
            )
        )
        if binds_as is not None:
            self._bindings[binds_as] = body
        return StepHandle(binds_as if binds_as is not None else f"__step_{len(self.captured) - 1}")


def _substitute(pattern: str, refs: dict[str, Ref], bindings: dict[str, Any]) -> str:
    def replace(match: re.Match[str]) -> str:
        return str(refs[match.group(1)].resolve(bindings))

    return _PLACEHOLDER.sub(replace, pattern)


def _declared(value: Any) -> Any:
    """The params as the cassette stores them: handles normalized to `Ref`s,
    everything else verbatim."""
    if isinstance(value, Ref | StepHandle):
        return _as_ref(value)
    if isinstance(value, dict):
        return {key: _declared(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_declared(item) for item in value]
    return value


def _resolve(value: Any, bindings: dict[str, Any]) -> Any:
    # A bare string stays a literal: only an explicit `ref(...)` (or the
    # handle it returns) wires a value to an earlier step, so a scenario
    # param that merely spells a binds_as name is never hijacked.
    if isinstance(value, Ref | StepHandle):
        return _as_ref(value).resolve(bindings)
    if isinstance(value, dict):
        return {key: _resolve(item, bindings) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve(item, bindings) for item in value]
    return value
