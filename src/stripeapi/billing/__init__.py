"""Billing behavior that spans resources (`architecture.md` §8).

Each module here is a pure function over rows plus `ctx` wherever possible,
imported by the resource modules that need it — never the other way round, so
`resources/` stays the only layer that talks to routes.
"""

from stripeapi.billing import magic_cards

__all__ = ["magic_cards"]
