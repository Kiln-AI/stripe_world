"""The single JSON dump convention for every JSON `TEXT` column.

`sort_keys=True, separators=(",", ":"), ensure_ascii=False` — canonical bytes,
so two runs that make the same change write the same fixture and the same
`data_object` snapshot (ProjectTracker's established pattern, and the reason a
determinism test can compare bytes rather than parsed structures).
"""

import json
from decimal import Decimal

__all__ = ["decimal_text", "dumps", "loads"]


def dumps(value: object) -> str:
    """The only function that writes a JSON TEXT column. Reproducible bytes
    across runs."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def loads(text: str | None) -> object | None:
    """None-preserving `json.loads`: a NULL column stays `None`, not `"null"`."""
    if text is None:
        return None
    return json.loads(text)


def decimal_text(value: int | float | str | Decimal) -> str:
    """A rate parameter as the canonical TEXT decimal literal a rate column
    stores (data_model rule 6): `10` stays `'10'`, `22.5` stays `'22.5'`,
    `8.875` stays `'8.875'` — no exponent, no trailing zeros, no float on
    the path. The serializer emits `json.loads(text)`, so the wire carries a
    JSON number of exactly these digits."""
    decimal = value if isinstance(value, Decimal) else Decimal(str(value))
    return format(decimal.normalize(), "f")
