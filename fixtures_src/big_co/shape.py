"""Ostrander's population knobs and catalog (components/big_co.md §1, §2).

**The population is scaled down (the user's decision, Phase 4).** The spec's `Shape` holds about
3,000 customers; the Phase 2 benchmark measured about 9 KB per billing cycle, which put 3,000
customers at 135-250 MB, past both the 25 MB budget and GitHub's 100 MB file limit. The user chose
about 500 customers with the full 18-month story and a frozen file of 25 MB or less, the count
coming down further if 500 does not fit. `SCALE` is the factor that fits: every count-based knob
is the spec's count times `SCALE` (rounded up, so no feature drops to zero), and every rate is the
spec's. The file's size follows the number of billing cycles the draw happens to produce, so it
moves by several percent from one draw to the next: at 0.115 the build holds 379 customers,
scripted loose ends included, in 23.0 MB, while draws at 0.12 and 0.125 came to 25.9 and 25.1 MB.
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

__all__ = [
    "CATALOG",
    "SCALE",
    "SERVICES",
    "SHAPE",
    "TAX_RATES",
    "TIERS",
    "ProductSpec",
    "Shape",
    "scaled",
]

TIERS = ("starter", "pro", "business", "enterprise")

# The share of big_co.md §1's counts this build draws (see the module docstring).
SCALE = 0.115


def scaled(count: int) -> int:
    """One of the spec's counts at `SCALE`, rounded up."""
    return math.ceil(count * SCALE)


def _frozen[V](**values: V) -> Mapping[str, V]:
    return MappingProxyType(dict(values))


@dataclass(frozen=True)
class Shape:
    migration_customers: int = scaled(1450)
    organic_customers: int = scaled(1550)
    migration_start: str = "2025-03-03T14:00:00.000Z"
    migration_end: str = "2025-03-07T22:00:00.000Z"
    organic_start: str = "2025-03-10T00:00:00.000Z"
    growth: float = 2.0
    tier_mix: Mapping[str, float] = field(
        default_factory=lambda: _frozen(starter=0.40, pro=0.35, business=0.20, enterprise=0.05)
    )
    annual_share: Mapping[str, float] = field(
        default_factory=lambda: _frozen(starter=0.10, pro=0.25, business=0.50, enterprise=1.0)
    )
    seats: Mapping[str, tuple[int, int]] = field(
        default_factory=lambda: _frozen(
            starter=(1, 5), pro=(3, 15), business=(10, 60), enterprise=(40, 400)
        )
    )
    legacy_share: float = 0.30
    fleet_share: float = 0.30
    sms_share: float = 0.40
    phone_share: float = 0.60
    no_email_share: float = 0.04
    tax_exempt_customers: int = scaled(20)
    trial_conversion: float = 0.65
    cardless_sales_trial_share: float = 0.25
    auth_required_share: float = 0.01
    monthly_churn: Mapping[str, float] = field(
        default_factory=lambda: _frozen(starter=0.025, pro=0.015, business=0.010)
    )
    annual_nonrenewal: float = 0.15
    enterprise_nonrenewal: float = 0.08
    churn_at_period_end_share: float = 0.70
    seat_change_monthly: float = 0.06
    seat_up_share: float = 0.70
    upgrade_monthly: float = 0.01
    immediate_downgrade_monthly: float = 0.001
    decline_per_renewal: float = 0.08
    decline_recovers: float = 0.75
    unpaid_cancel_after_days: int = 45
    refund_per_charge: float = 0.03
    refund_partial_share: float = 0.50
    # Disputes come from one-off services payments confirmed on a dispute test card, the path on
    # which the world itself opens a card's dispute (the user's decision in Phase 4: renewals
    # charged by the subscription engine open none). The rate is per card-paid period.
    dispute_per_charge: float = 0.003
    # Added in Phase 4: ordinary one-off services bought on the card on file, so the disputed
    # services payments are not the account's only PaymentIntents. Per card-paid period.
    services_per_period: float = 0.03
    dispute_mix: Mapping[str, float] = field(
        default_factory=lambda: _frozen(fraudulent=0.60, not_received=0.25, inquiry=0.15)
    )
    dispute_fresh_days: int = 21
    fresh_evidence_share: float = 0.40
    settled_won_share: float = 0.45
    fraud_dispute_cancels: float = 0.50
    switch25_share: float = 0.08
    association_share: float = 0.05
    concessions: int = scaled(40)
    scheduled_downgrades: int = scaled(10)
    credit_note_per_invoice: float = 0.005
    enterprise_card_share: float = 0.60
    duplicate_pairs: int = scaled(25)
    cs_notes: int = scaled(30)


SHAPE = Shape()


@dataclass(frozen=True)
class ProductSpec:
    key: str
    name: str
    description: str
    # (lookup_key, unit_amount, interval or None for one-time)
    prices: tuple[tuple[str, int, str | None], ...]
    legacy: bool = False


CATALOG: tuple[ProductSpec, ...] = (
    ProductSpec(
        "starter",
        "FieldOps Starter",
        "Scheduling, dispatch and the technician app for small crews. Priced per technician.",
        (("starter_monthly", 3900, "month"), ("starter_annual", 39000, "year")),
    ),
    ProductSpec(
        "pro",
        "FieldOps Pro",
        "Starter plus quoting, job costing and customer portal. Priced per technician.",
        (("pro_monthly", 6900, "month"), ("pro_annual", 69000, "year")),
    ),
    ProductSpec(
        "business",
        "FieldOps Business",
        "Pro plus multi-location dispatch, inventory and reporting. Priced per technician.",
        (("business_monthly", 9900, "month"), ("business_annual", 99000, "year")),
    ),
    ProductSpec(
        "enterprise",
        "FieldOps Enterprise",
        "Contracted per deal: SSO, audit log, dedicated success manager. Priced per technician.",
        (),
    ),
    ProductSpec(
        "fleet",
        "Fleet Tracking",
        "GPS tracking and route history for service vehicles. Priced per vehicle.",
        (("fleet_monthly", 1200, "month"), ("fleet_annual", 12000, "year")),
    ),
    ProductSpec(
        "sms",
        "SMS Reminders",
        "Appointment reminders and on-my-way texts to homeowners.",
        (("sms_monthly", 2900, "month"), ("sms_annual", 29000, "year")),
    ),
    ProductSpec(
        "onboarding",
        "Onboarding",
        "Guided setup, data import and crew training.",
        (
            ("onboarding_pro", 49900, None),
            ("onboarding_business", 149900, None),
            ("onboarding_enterprise", 499900, None),
        ),
    ),
    ProductSpec(
        "legacy_starter",
        "Legacy Starter",
        "Pre-2025 Starter plan carried over from the legacy billing system.",
        (("legacy_starter_monthly", 2900, "month"),),
        legacy=True,
    ),
    ProductSpec(
        "legacy_pro",
        "Legacy Pro",
        "Pre-2025 Pro plan carried over from the legacy billing system.",
        (("legacy_pro_monthly", 5900, "month"),),
        legacy=True,
    ),
)

# One-off services sold to card-paying customers and charged as a PaymentIntent on the spot:
# (description, amount in cents).
SERVICES: tuple[tuple[str, int], ...] = (
    ("On-site crew training day", 89900),
    ("Legacy data import", 49900),
    ("Custom dispatch report build", 34900),
    ("Technician app rollout session", 24900),
)

# State code -> percentage literal (functional spec §5.4): state rates only.
TAX_RATES: tuple[tuple[str, str, str], ...] = (
    ("TX", "Texas", "6.25"),
    ("WA", "Washington", "6.5"),
    ("NY", "New York", "4"),
    ("PA", "Pennsylvania", "6"),
    ("OH", "Ohio", "5.75"),
    ("TN", "Tennessee", "7"),
)
