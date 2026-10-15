"""What it costs to start a run from each company fixture (functional spec §3.5, §6).

An eval opens one instance per episode, so a fixture's fork cost is paid on every rollout. Each
case opens 30 instances of the committed fixture, timing each from `world.instance` to the end
of its `with` block, and asserts the median and the maximum under ceilings set from the Phase 4
measurement (a warm run on the development container):

| Fixture | File | Median | Max |
|---|---|---|---|
| `small_co` | 1.7 MB | 7 ms | 14 ms |
| `big_co` | 23.0 MB | 15 ms | 80 ms |

The median ceilings are ten times the measurement, so a slower CI machine passes and a fixture
that has grown, or an open that has started doing real work, does not. The maximum is held to a
second: the first open also verifies the file's checksum, and a cold disk cache was seen to cost
a small fixture 146 ms.
"""

import statistics
import time

import pytest
import seahaven

FORKS = 30
CEILINGS = {  # fixture -> (median seconds, max seconds)
    "small_co": (0.07, 1.0),
    "big_co": (0.15, 1.0),
}


@pytest.mark.parametrize("fixture", sorted(CEILINGS))
def test_fork_cost(world: seahaven.World, fixture: str) -> None:
    seconds: list[float] = []
    for _ in range(FORKS):
        started = time.perf_counter()
        with world.instance(fixture):
            pass
        seconds.append(time.perf_counter() - started)
    median_ceiling, max_ceiling = CEILINGS[fixture]
    print(f"{fixture}: median {statistics.median(seconds):.4f}s, max {max(seconds):.4f}s")
    assert statistics.median(seconds) < median_ceiling
    assert max(seconds) < max_ceiling
