"""The Stripe test cards the fixtures use, by role (replay.md §4; functional spec §3.2)."""

import random
from dataclasses import dataclass
from datetime import datetime

__all__ = [
    "ALL",
    "AUTH_REQUIRED",
    "DECLINING",
    "DECLINING_CODE",
    "DISPUTE_FRAUDULENT",
    "DISPUTE_INQUIRY",
    "DISPUTE_NOT_RECEIVED",
    "GOOD",
    "Card",
    "expiry",
    "pick_good",
]


@dataclass(frozen=True)
class Card:
    number: str
    brand: str
    weight: int = 0  # share among GOOD cards


GOOD = (
    Card("4242424242424242", "visa", 45),
    Card("4000056655665556", "visa", 10),
    Card("5555555555554444", "mastercard", 20),
    Card("5200828282828210", "mastercard", 5),
    Card("378282246310005", "amex", 15),
    Card("6011111111111117", "discover", 5),
)
DECLINING = Card("4000000000000341", "visa")  # attaches; every charge declines
DECLINING_CODE = "generic_decline"
DISPUTE_FRAUDULENT = Card("4000000000000259", "visa")
DISPUTE_NOT_RECEIVED = Card("4000000000002685", "visa")
DISPUTE_INQUIRY = Card("4000000000001976", "visa")
AUTH_REQUIRED = Card("4000002760003184", "visa")
ALL = (*GOOD, DECLINING, DISPUTE_FRAUDULENT, DISPUTE_NOT_RECEIVED, DISPUTE_INQUIRY, AUTH_REQUIRED)


def pick_good(rng: random.Random) -> Card:
    """A good card, weighted by brand mix."""
    return rng.choices(GOOD, weights=[card.weight for card in GOOD])[0]


def expiry(rng: random.Random, from_iso: str) -> tuple[int, int]:
    """`(month, year)` 8 to 48 months after `from_iso`."""
    start = datetime.fromisoformat(from_iso)
    total = start.year * 12 + (start.month - 1) + rng.randint(8, 48)
    return total % 12 + 1, total // 12
