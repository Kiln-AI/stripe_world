"""The one ISO ↔ Unix-second conversion.

Storage is Seahaven's canonical UTC text (`2026-06-01T09:00:00.000Z`); the
Stripe API returns Unix seconds. The conversion lives here and nowhere else —
a serializer emits `to_unix(row["created"])` and a parameter layer accepts
`from_unix(agent_value)`, and no other module parses a timestamp
(`components/data_model.md` §4.2 of the architecture).
"""

import math
import re
from datetime import UTC, datetime

__all__ = ["ISO_PATTERN", "from_unix", "to_unix"]

ISO_PATTERN = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$"

_ISO_RE = re.compile(ISO_PATTERN)


def to_unix(iso: str) -> int:
    """Canonical Seahaven TEXT timestamp -> Unix seconds.

    Floors, not truncates: a millisecond the API never returns is dropped
    toward the past, so a pre-epoch instant with a fractional part stays on the
    correct second (`int()` truncates toward zero and would round it up).

    Raises `ValueError` for anything that is not the canonical form — a
    non-canonical timestamp in a TEXT column is a bug in whatever wrote it, and
    a silent `dateutil`-style rescue would hide it.
    """
    if not _ISO_RE.match(iso):
        raise ValueError(f"not a canonical timestamp: {iso!r}")
    parsed = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
    return math.floor(parsed.timestamp())


def from_unix(seconds: int) -> str:
    """Unix seconds -> canonical `2026-06-01T09:00:00.000Z`.

    Used by the parameter layer when an agent supplies a Unix timestamp
    (`trial_end`, `cancel_at`, `billing_cycle_anchor`).
    """
    moment = datetime.fromtimestamp(seconds, tz=UTC)
    # Milliseconds spelled by hand: `isoformat` gives `+00:00` and no
    # milliseconds, and the canonical form wants `Z` and exactly three.
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"
