"""Instance startup: builds `ctx.state["account"]` once per instance.

Every downstream reader — `_ids` for the account fragment, `_context` for
validation, the account tools for their two faces, the serializer for
`livemode` — reads this state. The hook runs before the first call, so the
state is always present when a tool body executes.

The account object represents a **consistent fresh sandbox** (functional spec
§10.2): charges and payouts disabled, empty capabilities, business_profile
sub-fields null, a real-shaped account id, and no metadata key. The instance's
mode is configurable via the `livemode` startup kwarg and defaults to `True`
(functional spec §4.7).
"""

from typing import Any

import seahaven

from seahaven_stripe_world.world import world

__all__: list[str] = []

# A fixed account id: `acct_` + 16 alphanumeric characters.
# Real Stripe ids use this shape; the old human-readable constant was a tell.
ACCOUNT_ID = "acct_0NGvb3Cz7hWRKLEp"

# Created timestamp: a plausible past date, well before any fixture's `now`.
_CREATED = 1704067200  # 2024-01-01T00:00:00Z


def account_object(*, livemode: bool = True) -> dict[str, Any]:
    """The full account object for a consistent fresh sandbox.

    Matches the shape `GET /v1/account` returns from the real API on a fresh
    sandbox account (probed 2026-09-22). Fields that differ by mode:
    `settings.dashboard.display_name` and `settings.payouts.schedule.delay_days`
    are set to sandbox-probed values.
    """
    return {
        "id": ACCOUNT_ID,
        "object": "account",
        "business_profile": {
            "annual_revenue": None,
            "estimated_worker_count": None,
            "mcc": None,
            "name": None,
            "support_address": None,
            "support_email": None,
            "support_phone": None,
            "support_url": None,
            "url": None,
        },
        "business_type": None,
        "capabilities": {},
        "charges_enabled": False,
        "controller": {
            "is_controller": True,
            "type": "account",
        },
        "country": "US",
        "created": _CREATED,
        "default_currency": "usd",
        "details_submitted": False,
        "email": None,
        "external_accounts": {
            "object": "list",
            "data": [],
            "has_more": False,
            "url": f"/v1/accounts/{ACCOUNT_ID}/external_accounts",
        },
        "future_requirements": {
            "alternatives": [],
            "current_deadline": None,
            "currently_due": [],
            "disabled_reason": None,
            "errors": [],
            "eventually_due": [],
            "past_due": [],
            "pending_verification": [],
        },
        "payouts_enabled": False,
        "requirements": {
            "alternatives": [],
            "current_deadline": None,
            "currently_due": [],
            "disabled_reason": "requirements.past_due",
            "errors": [],
            "eventually_due": [],
            "past_due": [],
            "pending_verification": [],
        },
        "settings": {
            "bacs_debit_payments": {
                "display_name": None,
                "service_user_number": None,
            },
            "branding": {
                "icon": None,
                "logo": None,
                "primary_color": None,
                "secondary_color": None,
            },
            "card_issuing": {
                "tos_acceptance": {"date": None, "ip": None},
            },
            "card_payments": {
                "decline_on": {
                    "avs_failure": False,
                    "cvc_failure": False,
                },
                "statement_descriptor_prefix": None,
                "statement_descriptor_prefix_kana": None,
                "statement_descriptor_prefix_kanji": None,
            },
            "dashboard": {
                "display_name": None,
                "timezone": "Etc/UTC",
            },
            "invoices": {
                "default_account_tax_ids": None,
            },
            "payments": {
                "statement_descriptor": None,
                "statement_descriptor_kana": None,
                "statement_descriptor_kanji": None,
            },
            "payouts": {
                "debit_negative_balances": True,
                "schedule": {
                    "delay_days": 7,
                    "interval": "daily",
                },
                "statement_descriptor": None,
            },
            "sepa_debit_payments": {},
            "treasury": {
                "tos_acceptance": {"date": None, "ip": None},
            },
        },
        "tos_acceptance": {
            "date": None,
            "ip": None,
            "user_agent": None,
        },
        "type": "standard",
    }


@world.instance_startup
def _startup(ctx: seahaven.Ctx, *, livemode: bool = True, **_kwargs: object) -> None:
    """Build `ctx.state["account"]` from defaults and optional overrides.

    The `livemode` kwarg overrides the default mode (True = live, False =
    sandbox). Extra kwargs are accepted and ignored so that other startup
    parameters (from extensions or harnesses) do not cause errors.
    """
    obj = account_object(livemode=livemode)
    ctx.state["account"] = {
        "id": ACCOUNT_ID,
        "livemode": livemode,
        "name": obj["settings"]["dashboard"]["display_name"],
        "country": obj["country"],
        "default_currency": obj["default_currency"],
        "object": obj,
    }
