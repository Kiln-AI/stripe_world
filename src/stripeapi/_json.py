"""The single JSON dump convention for every JSON `TEXT` column.

`sort_keys=True, separators=(",", ":"), ensure_ascii=False` — canonical bytes,
so two runs that make the same change write the same fixture and the same
`data_object` snapshot (ProjectTracker's established pattern, and the reason a
determinism test can compare bytes rather than parsed structures).
"""

import json

__all__ = ["dumps", "loads"]


def dumps(value: object) -> str:
    """The only function that writes a JSON TEXT column. Reproducible bytes
    across runs."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def loads(text: str | None) -> object | None:
    """None-preserving `json.loads`: a NULL column stays `None`, not `"null"`."""
    if text is None:
        return None
    return json.loads(text)
