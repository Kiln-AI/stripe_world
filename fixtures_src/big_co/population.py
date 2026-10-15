"""The drawn population (components/big_co.md §3): pure, a function of the `rng` and the shape."""

import math
import random
import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast

from fixtures_src.big_co.shape import TIERS, Shape
from fixtures_src.replay import cards, people
from fixtures_src.replay.cards import Card
from fixtures_src.replay.clock import plus

__all__ = [
    "Interval",
    "Prospect",
    "Tier",
    "Trial",
    "company_key",
    "crm_id",
    "draw_population",
    "iso",
]

# generate.NOW; repeated rather than imported so this package does not import the recipe module.
NOW = "2026-09-01T14:00:00.000Z"
SWITCH25_FROM = "2025-04-01T15:00:00.000Z"
ASSOCIATIONS_FROM = "2025-05-15T15:00:00.000Z"

Tier = Literal["starter", "pro", "business", "enterprise"]
Trial = Literal["none", "card", "cardless", "migration"]
Interval = Literal["month", "year"]

_CS_NOTES = (
    "Pays by check — contact AP dept before the due date.",
    "Owner prefers phone calls; billing contact changes often.",
    "Asked about multi-location pricing on the last QBR.",
    "Seasonal business: expect seat drops over the winter.",
    "Renewal owner is the office manager, not the founder.",
    "Requested invoices with PO numbers in the memo.",
    "Escalated a dispatch outage in March; handle with care.",
    "Interested in Fleet Tracking once their vans are replaced.",
)


# Invented place names for the tax-exempt public buyers, so none names a real school district or
# town (the business-name pool's generic geography includes real places, such as "Piedmont").
_PUBLIC_PLACES = (
    "Larkmoor", "Corrandale", "Vesperton", "Tamberly", "Marrowfield", "Quillon Valley",
    "Fenwhistle", "Hollinbrook", "Bramwyn", "Kestrel Flats", "Orrin Valley", "Thistlecombe",
    "Ashcombe", "Calder Basin", "Merrow Bend", "Ambervale", "Sorrel Ridge", "Halvern",
    "Copperfield Mesa", "Indigo Springs",
)  # fmt: skip


@dataclass(frozen=True)
class Prospect:
    key: str
    cohort: Literal["migration", "organic", "duplicate", "loose_end"]
    created: str
    company: str
    domain: str
    email: str | None
    phone: str | None
    address: Mapping[str, Any]
    description: str | None
    metadata: Mapping[str, str]
    tax_exempt: bool
    tier: Tier
    interval: Literal["month", "year"]
    legacy: bool
    seats: int
    vehicles: int | None
    sms: bool
    card: Card | None
    card_exp: tuple[int, int] | None
    trial: Trial
    trial_end: str | None
    converts: bool
    coupon: str | None
    onboarding: str | None
    enterprise_rate: int | None
    pays_by_card: bool
    subscribes: bool = True
    scripted: bool = False
    script: tuple[Any, ...] = field(default=())


def iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.") + (
        f"{moment.microsecond // 1000:03d}Z"
    )


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value)


# The trade-association coupons (big_co.md §3), by the words in a company's name naming its trade.
_ASSOCIATION_WORDS = (
    ("MSPG10", frozenset({"plumbing", "drain", "sewer"})),
    (
        "FRHA10",
        frozenset({"hvac", "heating", "air", "comfort", "cooling", "climate", "conditioning"}),
    ),
    ("RMEC10", frozenset({"electric", "electrical"})),
)


def _trade_coupon(company: str) -> str | None:
    """The association coupon for the company's trade, matched on whole words: "Prairie" is not
    "air"."""
    words = set(re.findall(r"[a-z]+", company.lower()))
    for coupon, trade in _ASSOCIATION_WORDS:
        if words & trade:
            return coupon
    return None


def _tier(rng: random.Random, shape: Shape) -> Tier:
    names = list(TIERS)
    return cast(Tier, rng.choices(names, weights=[shape.tier_mix[t] for t in names])[0])


def crm_id(number: int) -> str:
    """A CRM record id: `006` and 12 digits, the one format every sales-led account carries."""
    return f"006{number:012d}"


def _crm_metadata(rng: random.Random) -> dict[str, str]:
    key = rng.choices(("crm_account_id", "CRM ID", "crmAccountId"), weights=(70, 20, 10))[0]
    return {key: crm_id(rng.randint(10**11, 10**12 - 1))}


def company_key(name: str) -> str:
    """A company name with its variant spellings folded together: case, "&" and "and", and a
    trailing "Co.", "LLC" or "Inc.". A duplicate shares its original's key; no two drawn
    companies do, so the only look-alike customers are the intended duplicates."""
    words = name.lower().replace("&", "and").split()
    while words and words[-1] in ("co.", "llc", "inc."):
        words.pop()
    return " ".join(words)


class _Names:
    """Draws company names, never two with the same `company_key` (nor, so, the same domain)."""

    def __init__(self) -> None:
        self.used: set[str] = set()

    def company(self, rng: random.Random) -> str:
        while True:
            name = people.business_name(rng)
            if company_key(name) not in self.used:
                self.used.add(company_key(name))
                return name


def _contact(
    rng: random.Random, company: str, shape: Shape, *, email: bool = True
) -> dict[str, Any]:
    city = people.pick_city(rng)
    domain = people.domain_for(company, rng)
    first, last = rng.choice(people.FIRST_NAMES), rng.choice(people.LAST_NAMES)
    return {
        "domain": domain,
        "email": people.contact_email(first, last, domain, rng) if email else None,
        "phone": people.phone(rng, city.area_code) if rng.random() < shape.phone_share else None,
        "address": people.address(rng, city=city),
    }


def _plan(rng: random.Random, shape: Shape, tier: Tier) -> dict[str, Any]:
    low, high = shape.seats[tier]
    if tier == "enterprise":
        seats = int(math.exp(rng.uniform(math.log(low), math.log(high))))
    else:
        seats = rng.randint(low, high)
    interval = "year" if rng.random() < shape.annual_share[tier] else "month"
    vehicles = None
    if tier != "starter" and rng.random() < shape.fleet_share:
        vehicles = max(1, round(seats * rng.uniform(0.8, 1.2)))
    return {
        "tier": tier,
        "seats": seats,
        "interval": interval,
        "vehicles": vehicles,
        "sms": rng.random() < shape.sms_share,
    }


def _card_exp(rng: random.Random) -> tuple[int, int]:
    # Valid at NOW and for a while after, so no renewal meets an expired card by accident.
    return cards.expiry(rng, NOW)


def _organic_instants(rng: random.Random, shape: Shape) -> list[str]:
    """Signup instants with density rising linearly from 1 to `growth`."""
    start = _parse(shape.organic_start)
    end = _parse(plus(NOW, days=-1))
    span = (end - start).total_seconds()
    g = shape.growth - 1.0
    out: list[str] = []
    for _ in range(shape.organic_customers):
        u = rng.random() * (1 + g / 2)
        t = u if g == 0 else (-1 + math.sqrt(1 + 2 * g * u)) / g
        day = start + timedelta(seconds=t * span)
        moment = day.replace(hour=13, minute=0, second=0, microsecond=0) + timedelta(
            seconds=rng.randint(0, 11 * 3600 - 1)
        )
        out.append(iso(moment))
    return sorted(out)


def draw_population(rng: random.Random, shape: Shape) -> list[Prospect]:
    """Migration, organic and duplicate prospects, sorted by `created`, then `key`."""
    names = _Names()
    out: list[Prospect] = []

    # Migration week: imports spaced evenly through the window.
    m_start, m_end = _parse(shape.migration_start), _parse(shape.migration_end)
    step = (m_end - m_start) / max(1, shape.migration_customers - 1)
    legacy_ids = rng.sample(range(100000, 999999), shape.migration_customers)
    for i in range(shape.migration_customers):
        created = iso(m_start + step * i)
        tier = _tier(rng, shape)
        plan = _plan(rng, shape, tier)
        legacy = tier in ("starter", "pro") and rng.random() < shape.legacy_share
        if legacy:
            plan["interval"] = "month"
        company = names.company(rng)
        contact = _contact(rng, company, shape, email=rng.random() >= shape.no_email_share)
        days_ahead = rng.randint(1, 30 if plan["interval"] == "month" else 365)
        since = datetime(2019, 1, 1, tzinfo=UTC) + timedelta(days=rng.randint(0, 2250))
        pays_by_card = tier != "enterprise" or rng.random() < shape.enterprise_card_share
        out.append(
            Prospect(
                key=f"m{i + 1:04d}",
                cohort="migration",
                created=created,
                company=company,
                description=None,
                metadata={
                    "legacy_account_id": f"OS-{legacy_ids[i]}",
                    "customer_since": since.strftime("%Y-%m-%d"),
                },
                tax_exempt=False,
                legacy=legacy,
                card=cards.pick_good(rng) if pays_by_card else None,
                card_exp=_card_exp(rng) if pays_by_card else None,
                trial="migration",
                trial_end=plus(created, days=days_ahead),
                converts=True,
                coupon=None,
                onboarding=None,
                enterprise_rate=rng.randint(70, 95) * 100 if tier == "enterprise" else None,
                pays_by_card=pays_by_card,
                **contact,
                **plan,
            )
        )

    # Organic and sales-led growth.
    for i, created in enumerate(_organic_instants(rng, shape)):
        tier = _tier(rng, shape)
        plan = _plan(rng, shape, tier)
        company = names.company(rng)
        contact = _contact(rng, company, shape)
        trial: Trial = "none"
        converts = True
        onboarding: str | None = None
        metadata: dict[str, str] = {}
        card: Card | None = cards.pick_good(rng)
        pays_by_card = True
        if tier in ("starter", "pro"):
            trial = "card"
            converts = rng.random() < shape.trial_conversion
            if tier == "pro" and rng.random() < 0.4:
                onboarding = "onboarding_pro"
        elif tier == "business":
            metadata = _crm_metadata(rng)
            if rng.random() < shape.cardless_sales_trial_share:
                trial, card = "cardless", None
                converts = rng.random() < shape.trial_conversion
            else:
                onboarding = "onboarding_business"
        else:
            metadata = _crm_metadata(rng)
            onboarding = "onboarding_enterprise"
            pays_by_card = rng.random() < shape.enterprise_card_share
            if not pays_by_card:
                card = None
        if trial == "none" and card is not None and rng.random() < shape.auth_required_share:
            card = cards.AUTH_REQUIRED
        coupon = None
        if created >= SWITCH25_FROM and rng.random() < shape.switch25_share:
            coupon = "SWITCH25"
        elif created >= ASSOCIATIONS_FROM and rng.random() < shape.association_share:
            coupon = _trade_coupon(company)
        out.append(
            Prospect(
                key=f"o{i + 1:04d}",
                cohort="organic",
                created=created,
                company=company,
                description=None,
                metadata=metadata,
                tax_exempt=False,
                legacy=False,
                card=card,
                card_exp=_card_exp(rng) if card is not None else None,
                trial=trial,
                trial_end=None,
                converts=converts,
                coupon=coupon,
                onboarding=onboarding,
                enterprise_rate=rng.randint(70, 95) * 100 if tier == "enterprise" else None,
                pays_by_card=pays_by_card,
                **contact,
                **plan,
            )
        )

    # Tax-exempt public buyers: rename some organic Pro/Business prospects.
    candidates = [
        i
        for i, p in enumerate(out)
        if p.cohort == "organic" and p.tier in ("pro", "business") and p.coupon is None
    ]
    places = rng.sample(_PUBLIC_PLACES, shape.tax_exempt_customers)
    for n, i in enumerate(rng.sample(candidates, shape.tax_exempt_customers)):
        p = out[i]
        if n % 2 == 0:
            company = f"{places[n]} Municipal Facilities Department"
        else:
            company = f"{places[n]} Unified School District"
        names.used.add(company_key(company))
        domain = people.domain_for(company, rng).rsplit(".", 1)[0] + ".gov"
        out[i] = replace(
            p,
            company=company,
            domain=domain,
            email=f"facilities@{domain}",
            tax_exempt=True,
        )

    # Customer-success notes on a few accounts.
    for n, i in enumerate(rng.sample(range(len(out)), shape.cs_notes)):
        out[i] = replace(out[i], description=_CS_NOTES[n % len(_CS_NOTES)])

    # Duplicates: an earlier company under a variant name, with a different contact.
    originals = rng.sample(
        [p for p in out if p.created < plus(NOW, days=-210) and not p.tax_exempt],
        shape.duplicate_pairs,
    )
    for i, original in enumerate(originals):
        variant = _variant(original.company, rng)
        created = plus(original.created, days=rng.randint(5, 200), hours=rng.randint(0, 6))
        email = original.email
        while email == original.email:
            first, last = rng.choice(people.FIRST_NAMES), rng.choice(people.LAST_NAMES)
            email = people.contact_email(first, last, original.domain, rng)
        subscribes = rng.random() < 0.4
        out.append(
            replace(
                original,
                key=f"d{i + 1:04d}",
                cohort="duplicate",
                created=created,
                company=variant,
                email=email,
                description=None,
                metadata={},
                tier="starter" if subscribes else original.tier,
                interval="month",
                legacy=False,
                seats=min(original.seats, 5),
                vehicles=None,
                sms=False,
                card=cards.pick_good(rng) if subscribes else None,
                card_exp=_card_exp(rng) if subscribes else None,
                trial="card",
                trial_end=None,
                converts=False,
                coupon=None,
                onboarding=None,
                enterprise_rate=None,
                pays_by_card=True,
                subscribes=subscribes,
            )
        )
    out.sort(key=lambda p: (p.created, p.key))
    return out


def _variant(name: str, rng: random.Random) -> str:
    if "&" in name:
        return name.replace("&", "and") + rng.choice(("", " LLC"))
    if name.endswith(" Co."):
        return name[: -len(" Co.")]
    return name + rng.choice((" LLC", " Inc."))
