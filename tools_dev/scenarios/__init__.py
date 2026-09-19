"""Scenario registration: "add a file," nothing else to wire up.

Stripe-free by design (``tools_dev/record.py`` and the conformance gate both
import this; only the recorder's transport imports ``stripe``), so the test
suite enumerates scenarios without the recording dependency installed.
"""

from importlib import import_module
from pathlib import Path

__all__ = ["SCENARIOS_DIR", "registry"]

SCENARIOS_DIR = Path(__file__).resolve().parent


def registry() -> dict[str, str]:
    """Every scenario module under this directory -> its `SCENARIO` name.

    `sNN_slug.py` are the curated conformance scenarios (cassette
    `NN_slug.json`); `probe_*.py` are a resource phase's throwaway step-3
    recordings — same command, same mechanism, no conformance obligation
    (`components/conformance.md` "The recorder").
    """
    scenarios: dict[str, str] = {}
    for path in sorted(SCENARIOS_DIR.glob("*.py")):
        if path.name.startswith("_"):
            continue
        module = import_module(f"tools_dev.scenarios.{path.stem}")
        scenarios[module.SCENARIO] = path.stem
    return scenarios
