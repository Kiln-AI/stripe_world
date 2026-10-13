"""The two company accounts (functional spec §2.2; architecture §2.3), and their installation.

Each `CompanyAccount` is the sandbox golden object (`tests/golden/sandbox_account.json`) with the
company's fields changed: an activated US company account, test-mode, with one test bank account
for payouts and a manual payout schedule.
"""

import copy
from dataclasses import dataclass
from typing import Any

import seahaven

from seahaven_stripe_world import _json, startup

__all__ = ["OSTRANDER", "WRENFIELD", "CompanyAccount", "install_account"]


@dataclass(frozen=True)
class CompanyAccount:
    id: str
    object: dict[str, Any]
    settings: dict[str, Any]

    @property
    def bank_account_id(self) -> str:
        """The payout destination: the account's one external bank account."""
        return str(self.object["external_accounts"]["data"][0]["id"])


def install_account(ctx: seahaven.Ctx, account: CompanyAccount) -> None:
    """Replace the account row with `account`, then reload `ctx.state["account"]`, so every id
    minted afterwards carries the company's account fragment. `livemode` stays as startup stamped
    it. The one row a builder writes directly: it is the fixture's definition, not history."""
    ctx.db.execute(
        "UPDATE account SET id = ?, object = ?, settings = ?",
        account.id,
        _json.dumps(account.object),
        _json.dumps(account.settings),
    )
    ctx.state["account"] = startup.open_account(ctx, livemode=None)


_SANDBOX: dict[str, Any] = {
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
    "controller": {"is_controller": True, "type": "account"},
    "country": "US",
    "created": 1704067200,
    "default_currency": "usd",
    "details_submitted": False,
    "email": None,
    "external_accounts": {"data": [], "has_more": False, "object": "list", "url": ""},
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
    "id": "",
    "object": "account",
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
        "bacs_debit_payments": {"display_name": None, "service_user_number": None},
        "branding": {"icon": None, "logo": None, "primary_color": None, "secondary_color": None},
        "card_issuing": {"tos_acceptance": {"date": None, "ip": None}},
        "card_payments": {
            "decline_on": {"avs_failure": False, "cvc_failure": False},
            "statement_descriptor_prefix": None,
            "statement_descriptor_prefix_kana": None,
            "statement_descriptor_prefix_kanji": None,
        },
        "dashboard": {"display_name": None, "timezone": "Etc/UTC"},
        "invoices": {"default_account_tax_ids": None},
        "payments": {
            "statement_descriptor": None,
            "statement_descriptor_kana": None,
            "statement_descriptor_kanji": None,
        },
        "payouts": {
            "debit_negative_balances": True,
            "schedule": {"delay_days": 7, "interval": "daily"},
            "statement_descriptor": None,
        },
        "sepa_debit_payments": {},
        "treasury": {"tos_acceptance": {"date": None, "ip": None}},
    },
    "tos_acceptance": {"date": None, "ip": None, "user_agent": None},
    "type": "standard",
}


def _bank_account(account_id: str, holder: str, created_suffix: str) -> dict[str, Any]:
    """The one test-mode external bank account; its `ba_` id derives from the account id."""
    return {
        "account": account_id,
        "account_holder_name": holder,
        "account_holder_type": "company",
        "account_type": None,
        "available_payout_methods": ["standard"],
        "bank_name": "STRIPE TEST BANK",
        "country": "US",
        "currency": "usd",
        "default_for_currency": True,
        "fingerprint": account_id[5:].lower()[::-1],
        "future_requirements": {
            "currently_due": [],
            "errors": [],
            "past_due": [],
            "pending_verification": [],
        },
        "id": f"ba_{account_id[5:]}{created_suffix}",
        "last4": "6789",
        "metadata": {},
        "object": "bank_account",
        "requirements": {
            "currently_due": [],
            "errors": [],
            "past_due": [],
            "pending_verification": [],
        },
        "routing_number": "110000000",
        "status": "new",
    }


def _company(
    *,
    account_id: str,
    bank_suffix: str,
    business_name: str,
    display_name: str,
    url: str,
    support_email: str,
    support_phone: str | None,
    support_address: dict[str, Any],
    email: str,
    timezone: str,
    descriptor: str,
    descriptor_prefix: str,
    created: int,
    dunning: dict[str, Any],
) -> CompanyAccount:
    obj = copy.deepcopy(_SANDBOX)
    obj["id"] = account_id
    obj["business_profile"].update(
        {
            "mcc": "5734",
            "name": business_name,
            "support_address": support_address,
            "support_email": support_email,
            "support_phone": support_phone,
            "url": url,
        }
    )
    obj["business_type"] = "company"
    obj["capabilities"] = {"card_payments": "active", "transfers": "active"}
    obj["charges_enabled"] = True
    obj["payouts_enabled"] = True
    obj["details_submitted"] = True
    obj["created"] = created
    obj["email"] = email
    obj["external_accounts"] = {
        "data": [_bank_account(account_id, display_name, bank_suffix)],
        "has_more": False,
        "object": "list",
        "url": f"/v1/accounts/{account_id}/external_accounts",
    }
    obj["requirements"]["disabled_reason"] = None
    settings = obj["settings"]
    settings["card_payments"]["statement_descriptor_prefix"] = descriptor_prefix
    settings["dashboard"] = {"display_name": display_name, "timezone": timezone}
    settings["payments"]["statement_descriptor"] = descriptor
    settings["payouts"]["schedule"] = {"delay_days": 2, "interval": "manual"}
    obj["tos_acceptance"] = {"date": created, "ip": None, "user_agent": None}
    return CompanyAccount(id=account_id, object=obj, settings={"dunning": dunning})


def _address(city: str, state: str, postal_code: str) -> dict[str, Any]:
    return {
        "city": city,
        "country": "US",
        "line1": None,
        "line2": None,
        "postal_code": postal_code,
        "state": state,
    }


WRENFIELD = _company(
    account_id="acct_1Pq7WrnFd3kT9mZa",
    bank_suffix="Wf01Bk",
    business_name="Wrenfield",
    display_name="Wrenfield Labs",
    url="https://wrenfield.app",
    support_email="support@wrenfield.app",
    support_phone=None,
    support_address=_address("Asheville", "NC", "28801"),
    email="hello@wrenfield.app",
    timezone="America/New_York",
    descriptor="WRENFIELD",
    descriptor_prefix="WRENFIELD",
    created=1756130400,  # 2025-08-25T14:00:00Z
    dunning={"max_attempts": 8, "window": "2w", "end_behavior": "cancel"},
)

OSTRANDER = _company(
    account_id="acct_1OsTr4ndXq8NvK2e",
    bank_suffix="Os01Bk",
    business_name="Ostrander FieldOps",
    display_name="Ostrander Systems",
    url="https://ostranderfieldops.com",
    support_email="billing@ostranderfieldops.com",
    support_phone="+17205550147",
    support_address=_address("Denver", "CO", "80202"),
    email="finance@ostranderfieldops.com",
    timezone="America/Denver",
    descriptor="OSTRANDER FIELDOPS",
    descriptor_prefix="OSTRANDER",
    created=1739199600,  # 2025-02-10T15:00:00Z
    dunning={"max_attempts": 8, "window": "3w", "end_behavior": "mark_unpaid"},
)
