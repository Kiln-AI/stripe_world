"""The `get_stripe_account_info` tool: a static account object.

The account is not stored in the database and is not routable through the
dispatcher. It is a hardcoded default representing a test-mode standard
account, matching the shape `GET /v1/account` returns from Stripe's API.

Only billing-relevant fields are included. Fields that are never meaningful
in a billing-and-payments world (Connect onboarding, identity verification,
external bank accounts) are omitted rather than filled with noise.

No docstring names another tool (lint SH206).
"""

from typing import Any

import seahaven

from seahaven_stripe_world.world import world

__all__ = ["get_stripe_account_info"]

# A fixed account id. Stripe test-mode accounts use the `acct_` prefix.
_ACCOUNT_ID = "acct_1SWTestAccount00"

# Created timestamp: a plausible past date, well before any fixture's `now`.
_CREATED = 1704067200  # 2024-01-01T00:00:00Z


def _account_object() -> dict[str, Any]:
    """The static account object. Built once per call (cheap dict literal)."""
    return {
        "id": _ACCOUNT_ID,
        "object": "account",
        "business_profile": {
            "mcc": None,
            "name": "Test Business",
            "support_address": None,
            "support_email": None,
            "support_phone": None,
            "support_url": None,
            "url": None,
        },
        "business_type": "company",
        "capabilities": {
            "card_payments": "active",
            "transfers": "active",
        },
        "charges_enabled": True,
        "country": "US",
        "created": _CREATED,
        "default_currency": "usd",
        "details_submitted": True,
        "email": "test@example.com",
        "metadata": {},
        "payouts_enabled": True,
        "settings": {
            "branding": {
                "icon": None,
                "logo": None,
                "primary_color": None,
                "secondary_color": None,
            },
            "dashboard": {
                "display_name": "Test Business",
                "timezone": "Etc/UTC",
            },
            "payments": {
                "statement_descriptor": None,
                "statement_descriptor_kana": None,
                "statement_descriptor_kanji": None,
            },
            "payouts": {
                "debit_negative_balances": True,
                "schedule": {
                    "delay_days": 2,
                    "interval": "daily",
                },
                "statement_descriptor": None,
            },
        },
        "type": "standard",
    }


@world.tool
def get_stripe_account_info(ctx: seahaven.Ctx) -> dict[str, Any]:
    """Retrieve the account object for this Stripe account.

    Returns the account object with billing-relevant fields: business
    profile, capabilities, default currency, and payout settings. The
    account is static within an instance.
    """
    return _account_object()
