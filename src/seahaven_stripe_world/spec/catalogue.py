"""The MCP catalogue: which operations the real Stripe MCP server exposes.

Loaded once at module import from ``mcp_catalogue.jsonl`` (architecture
section 4.1).  The committed artifact was enumerated by
``tools_dev/enumerate_catalogue.py`` against the live Stripe MCP on
2026-09-23.

Three verdicts, three runtime sets:

- **absent** -- the real MCP does not expose this operation.  Bucket A:
  ``Operation '{id}' is not available.``
- **catalogued** -- the real MCP exposes it, but this world does not route
  it.  Bucket B: a product-activation (B1) or permission (B2) refusal.
- **routed** -- catalogued *and* this world implements it.  Normal dispatch.

``verdict: "error"`` records are never treated as absent (architecture
section 4.1) -- they are skipped, and the catalogue is conservative: an
unknown operation is bucket A only if it appears in ``ABSENT``.
"""

import json
from importlib import resources
from typing import Final

__all__ = [
    "ABSENT",
    "CATALOGUED",
    "PERMISSIONS",
    "is_absent",
    "is_catalogued",
    "permissions",
]


def _load() -> tuple[frozenset[str], frozenset[str], dict[str, list[str]]]:
    text = resources.files("seahaven_stripe_world.spec").joinpath("mcp_catalogue.jsonl").read_text()
    absent: set[str] = set()
    catalogued: set[str] = set()
    perms: dict[str, list[str]] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        op = rec.get("op")
        if op is None:
            continue  # skip kind=tools record
        verdict = rec.get("verdict")
        if verdict == "absent":
            absent.add(op)
        elif verdict == "catalogued":
            catalogued.add(op)
            perms[op] = rec.get("permissions", [])
        # verdict == "error" is intentionally ignored
    return frozenset(absent), frozenset(catalogued), perms


ABSENT: Final[frozenset[str]]
CATALOGUED: Final[frozenset[str]]
PERMISSIONS: Final[dict[str, list[str]]]
ABSENT, CATALOGUED, PERMISSIONS = _load()


def is_absent(op_id: str) -> bool:
    """True when the real MCP does not expose this operation."""
    return op_id in ABSENT


def is_catalogued(op_id: str) -> bool:
    """True when the real MCP exposes this operation."""
    return op_id in CATALOGUED


def permissions(op_id: str) -> list[str]:
    """The ``required_permissions`` the real MCP declares for this operation."""
    return PERMISSIONS.get(op_id, [])
