"""The child this world declares for a type checker.

Complete and correct: every name `shop.world` adds is annotated and nothing else
is, which is what `seahaven check` binds a `Worlds` subclass to (SH502, SH503).
"""

import seahaven

__all__ = ["ShopWorlds"]


class ShopWorlds(seahaven.Worlds):
    """`shop`'s children, declared so a type checker knows them."""

    payments: seahaven.WorldHandle
