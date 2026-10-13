"""The timeline: steps run in instant order, ties in scheduling order (replay.md §2)."""

import heapq
from collections.abc import Callable
from dataclasses import dataclass, field

import seahaven
from fixtures_src.replay.clock import at as at_instant

__all__ = ["FixtureBuildError", "Step", "Timeline"]


class FixtureBuildError(Exception):
    """A step failed; the message names the step and its instant."""


@dataclass(frozen=True)
class Step:
    at: str
    seq: int
    label: str
    run: Callable[[seahaven.Ctx], None] = field(compare=False)


class Timeline:
    """A heap of `(at, seq, step)`. Instants are canonical strings, so string order is time
    order; `seq` makes every key unique, so steps themselves never compare."""

    def __init__(self, base: seahaven.Ctx, *, until: str) -> None:
        self.base = base
        self.until = until
        self._heap: list[tuple[str, int, Step]] = []
        self._seq = 0
        self._running: str | None = None
        self._earliest: str | None = None

    @property
    def now(self) -> str:
        """The running step's instant; before run(), the earliest instant ever scheduled (the
        base clock's reading when nothing has been)."""
        if self._running is not None:
            return self._running
        if self._earliest is not None:
            return self._earliest
        return self.base.clock.iso()

    def add(self, at: str, label: str, run: Callable[[seahaven.Ctx], None]) -> bool:
        """Schedule `run` at `at`. Returns False (and drops it) when `at` is after `until`.
        Raises FixtureBuildError when `at` is before `now` while running."""
        if at > self.until:
            return False
        if self._running is not None and at < self._running:
            raise FixtureBuildError(
                f"{label} scheduled at {at}, before the running step's {self._running}: "
                "history only moves forward"
            )
        if self._earliest is None or at < self._earliest:
            self._earliest = at
        self._seq += 1
        heapq.heappush(self._heap, (at, self._seq, Step(at, self._seq, label, run)))
        return True

    def run(self) -> None:
        """Pop steps in (at, seq) order and run each against a context reading its instant."""
        while self._heap:
            _at, _seq, step = heapq.heappop(self._heap)
            self._running = step.at
            try:
                step.run(at_instant(self.base, step.at))
            except Exception as exc:
                raise FixtureBuildError(f"{step.label} at {step.at}: {exc}") from exc
