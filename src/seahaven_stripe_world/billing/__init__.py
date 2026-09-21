"""Billing behavior that spans resources (`architecture.md` §8).

Each module here is a pure function over rows plus `ctx` wherever possible,
imported by the resource modules that need it. The reverse imports are
narrow, not forbidden outright: a billing module may import the cycle-free
resource leaves (`_lookup`, `events`) but never a resource module that
imports billing back (`ledger` ← `refunds`/`charges`/`disputes`/`payouts`),
which is why `ledger` keeps its own copy of the currency-symbol table
rather than reaching for `refunds._CURRENCY_SYMBOLS`.
"""

from seahaven_stripe_world.billing import (
    _money,
    invoicing,
    ledger,
    magic_cards,
    subscription_lifecycle,
)

__all__ = ["_money", "invoicing", "ledger", "magic_cards", "subscription_lifecycle"]
