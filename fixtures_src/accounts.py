"""The two company accounts (functional spec §2.2; architecture §2.3), and their installation.

A `CompanyAccount` holds only what the company sets: its id, its fields of the account object and
its settings. The rest of the object is the sandbox account the schema seeds
(`schema/006_account.sql`), read from the row when the account is installed, so the SQL seed is
the one copy of it. Each company is an activated US company account, test-mode, with one test bank
account for payouts and a manual payout schedule.
"""

import copy
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

import seahaven
from fixtures_src.replay.timeline import FixtureBuildError

from seahaven_stripe_world import _json, startup

__all__ = ["OSTRANDER", "WRENFIELD", "CompanyAccount", "install_account"]


@dataclass(frozen=True)
class CompanyAccount:
    id: str
    fields: Mapping[str, Any]  # the company's values, nested as in the account object
    settings: Mapping[str, Any]  # the row's `settings`: ledger and dunning overrides
    bank_suffix: str  # the external bank account's id is `ba_` + the account fragment + this

    @property
    def bank_account_id(self) -> str:
        """The payout destination: the account's one external bank account."""
        return f"ba_{self.id[5:]}{self.bank_suffix}"

    def object_over(self, sandbox: Mapping[str, Any]) -> dict[str, Any]:
        """`sandbox` with this company's fields set over it.

        A nested field is set inside the sandbox's object of that name; anything else, an empty
        object included, replaces the sandbox's value. A field the sandbox does not have is
        refused, so a change to the seed cannot silently leave a company field behind."""
        return _set_over(copy.deepcopy(dict(sandbox)), self.fields, "")


def _set_over(base: dict[str, Any], fields: Mapping[str, Any], prefix: str) -> dict[str, Any]:
    for key, value in fields.items():
        path = f"{prefix}{key}"
        if key not in base:
            raise FixtureBuildError(f"the sandbox account object has no {path}")
        if isinstance(value, Mapping) and isinstance(base[key], dict) and base[key]:
            _set_over(base[key], value, f"{path}.")
        else:
            base[key] = copy.deepcopy(value)
    return base


def install_account(ctx: seahaven.Ctx, account: CompanyAccount) -> None:
    """Make the seeded sandbox row `account`, then reload `ctx.state["account"]`, so every id
    minted afterwards carries the company's account fragment. `livemode` stays as startup stamped
    it. The one row a builder writes directly: it is the fixture's definition, not history."""
    row = ctx.db.one("SELECT id, object FROM account")
    if row is None or row["id"] != startup.ACCOUNT_ID:
        found = None if row is None else row["id"]
        raise FixtureBuildError(
            f"install_account starts from the seeded sandbox account {startup.ACCOUNT_ID},"
            f" not {found!r}"
        )
    # The table's CHECK guarantees the column holds a JSON object.
    obj = account.object_over(cast("dict[str, Any]", _json.loads(row["object"])))
    ctx.db.execute(
        "UPDATE account SET id = ?, object = ?, settings = ?",
        account.id,
        _json.dumps(obj),
        _json.dumps(dict(account.settings)),
    )
    ctx.state["account"] = startup.open_account(ctx, livemode=None)


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
    fields = {
        "id": account_id,
        "business_profile": {
            "mcc": "5734",
            "name": business_name,
            "support_address": support_address,
            "support_email": support_email,
            "support_phone": support_phone,
            "url": url,
        },
        "business_type": "company",
        "capabilities": {"card_payments": "active", "transfers": "active"},
        "charges_enabled": True,
        "payouts_enabled": True,
        "details_submitted": True,
        "created": created,
        "email": email,
        "external_accounts": {
            "data": [_bank_account(account_id, display_name, bank_suffix)],
            "url": f"/v1/accounts/{account_id}/external_accounts",
        },
        "requirements": {"disabled_reason": None},
        "settings": {
            "card_payments": {"statement_descriptor_prefix": descriptor_prefix},
            "dashboard": {"display_name": display_name, "timezone": timezone},
            "payments": {"statement_descriptor": descriptor},
            "payouts": {"schedule": {"delay_days": 2, "interval": "manual"}},
        },
        "tos_acceptance": {"date": created},
    }
    return CompanyAccount(
        id=account_id, fields=fields, settings={"dunning": dunning}, bank_suffix=bank_suffix
    )


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
