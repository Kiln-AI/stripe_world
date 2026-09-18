"""The children this world declares for a type checker, and gets wrong.

`market` is annotated and never added (SH502); `books` is added and never
annotated (SH503), which is the half-declared class that checks the child its
author remembered and silently not the one they forgot.

`__slots__` and `_cache` are neither: no child can be spelled with a leading
underscore, and `Worlds.__getattr__` lets such a name fall to ordinary attribute
lookup precisely so a subclass may carry one. They are here so that the rule has
something it must leave alone.
"""

from typing import ClassVar

import seahaven

__all__ = ["BazaarWorlds"]


class BazaarWorlds(seahaven.Worlds):
    """`bazaar`'s children, as its author believes them to be."""

    __slots__: ClassVar[tuple[str, ...]] = ()
    _cache: int

    ledger: seahaven.WorldHandle
    market: seahaven.WorldHandle
