"""`big_co`'s population draw is pure and has the shape it claims (components/big_co.md §3, §9)."""

import random
import re

from fixtures_src.big_co.loose_ends import scripted
from fixtures_src.big_co.population import NOW, Prospect, company_key, draw_population
from fixtures_src.big_co.shape import SHAPE, TIERS
from fixtures_src.replay import cards
from fixtures_src.replay.checks import DENIED_EMAIL_DOMAINS, DENIED_NAMES
from fixtures_src.replay.clock import plus


def _draw(seed: int) -> list[Prospect]:
    return draw_population(random.Random(seed), SHAPE)


def test_population_is_pure() -> None:
    assert _draw(7) == _draw(7)
    assert _draw(7) != _draw(8)


def test_cohort_sizes_and_order() -> None:
    people = _draw(11)
    cohorts = [p.cohort for p in people]
    assert cohorts.count("migration") == SHAPE.migration_customers
    assert cohorts.count("organic") == SHAPE.organic_customers
    assert cohorts.count("duplicate") == SHAPE.duplicate_pairs
    assert [(p.created, p.key) for p in people] == sorted((p.created, p.key) for p in people)
    assert len({p.key for p in people}) == len(people)
    assert all(p.created < NOW for p in people)


def test_tier_mix_within_three_points() -> None:
    """Pooled over eight draws: one scaled population is a few hundred prospects, too few for a
    single draw to promise ±3 points."""
    drawn = [p for seed in range(8) for p in _draw(seed) if p.cohort != "duplicate"]
    for tier in TIERS:
        actual = sum(p.tier == tier for p in drawn) / len(drawn)
        assert abs(actual - SHAPE.tier_mix[tier]) < 0.03, (tier, actual)


def test_every_prospect_is_reachable_and_plausible() -> None:
    for p in _draw(11):
        assert p.domain and p.address["state"] and p.address["postal_code"]
        assert (p.card is None) == (p.card_exp is None)
        assert p.email is None or p.email.endswith("@" + p.domain)
        assert p.email is None or p.email.rsplit("@", 1)[1] not in DENIED_EMAIL_DOMAINS
        assert p.phone is None or re.fullmatch(r"\+1\d{3}55501\d{2}", p.phone)
        assert not any(word in p.company.lower() for word in DENIED_NAMES)
        low, high = SHAPE.seats[p.tier]
        assert low <= p.seats <= high or p.cohort == "duplicate"


def test_tax_exempt_buyers_are_public_bodies() -> None:
    exempt = [p for p in _draw(11) if p.tax_exempt]
    assert len(exempt) == SHAPE.tax_exempt_customers
    for p in exempt:
        assert p.company.endswith(("Unified School District", "Municipal Facilities Department"))
        assert p.domain.endswith(".gov")


def test_migration_cohort() -> None:
    migrated = [p for p in _draw(11) if p.cohort == "migration"]
    assert all(
        SHAPE.migration_start <= p.created <= SHAPE.migration_end and p.trial == "migration"
        for p in migrated
    )
    legacy_ids = [p.metadata["legacy_account_id"] for p in migrated]
    assert len(set(legacy_ids)) == len(legacy_ids)
    assert all(re.fullmatch(r"OS-\d{6}", legacy_id) for legacy_id in legacy_ids)
    assert all("2019-01-01" <= p.metadata["customer_since"] <= "2025-02-28" for p in migrated)
    for p in migrated:
        assert p.trial_end is not None
        horizon = 30 if p.interval == "month" else 365
        assert p.created < p.trial_end <= plus(p.created, days=horizon)
        assert p.trial_end[11:] == p.created[11:]  # the legacy renewal keeps the time of day
    assert all(p.interval == "month" for p in migrated if p.legacy)
    assert not any(p.legacy for p in migrated if p.tier in ("business", "enterprise"))


def test_organic_signups_follow_the_sales_motion() -> None:
    organic = [p for p in _draw(11) if p.cohort == "organic"]
    for p in organic:
        assert "13:00:00.000Z" <= p.created[11:] <= "23:59:59.999Z"
        if p.tier in ("starter", "pro"):
            assert p.trial == "card" and p.card is not None
        elif p.tier == "business":
            assert p.trial in ("cardless", "none")
            assert (p.trial == "cardless") == (p.card is None)
            assert (p.onboarding == "onboarding_business") == (p.trial == "none")
        else:
            assert p.trial == "none" and p.onboarding == "onboarding_enterprise"
            assert p.enterprise_rate is not None and 7000 <= p.enterprise_rate <= 9500
        if p.tier in ("business", "enterprise"):
            (key,) = p.metadata
            assert key in ("crm_account_id", "CRM ID", "crmAccountId")
        if p.card is cards.AUTH_REQUIRED:
            assert p.trial == "none"


def test_duplicates_copy_an_earlier_company() -> None:
    """Every look-alike pair is an intended duplicate: drawn companies never share a name key or
    a domain, and each duplicate shares both with one earlier company."""
    people = _draw(11)
    drawn = [p for p in people if p.cohort != "duplicate"]
    assert len({company_key(p.company) for p in drawn}) == len(drawn)
    assert len({p.domain.rsplit(".", 1)[0] for p in drawn}) == len(drawn)
    originals = {company_key(p.company): p for p in drawn}
    for dup in (p for p in people if p.cohort == "duplicate"):
        original = originals[company_key(dup.company)]
        assert dup.domain == original.domain
        assert original.created < dup.created <= plus(original.created, days=201)
        assert dup.company != original.company and dup.email != original.email
        assert not dup.subscribes or (dup.trial == "card" and not dup.converts)


def test_company_key_folds_the_variant_spellings() -> None:
    assert company_key("Rivera Heating & Air") == company_key("Rivera Heating and Air LLC")
    assert company_key("Summit Plumbing Co.") == company_key("Summit Plumbing")
    assert company_key("Talbot Comfort Systems Inc.") == "talbot comfort systems"
    assert company_key("Summit Plumbing") != company_key("Summit Electric")


def test_loose_ends_are_fixed() -> None:
    assert scripted(SHAPE) == scripted(SHAPE)
    keys = [p.key for p in scripted(SHAPE)]
    assert len(keys) == len(set(keys)) and all(k.startswith("le_") for k in keys)
    assert all(p.scripted and p.cohort == "loose_end" for p in scripted(SHAPE))
