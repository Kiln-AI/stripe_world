"""The company accounts (architecture §2.3): each is the account object the schema seeds, with only
the company's own fields set over it, installed into a blank instance's row."""

import json
from typing import Any

import pytest
from fixtures_src import accounts
from fixtures_src.replay.timeline import FixtureBuildError

from replay.support import blank, rows
from seahaven_stripe_world.startup import ACCOUNT_ID


def _leaves(value: Any, prefix: str = "") -> dict[str, Any]:
    """`value` flattened to dotted paths; lists and empty objects are leaves."""
    if isinstance(value, dict) and value:
        out: dict[str, Any] = {}
        for key, inner in value.items():
            out.update(_leaves(inner, f"{prefix}{key}."))
        return out
    return {prefix.rstrip("."): value}


def _related(path: str, set_paths: set[str]) -> bool:
    """`path` is one the company sets, inside one, or holds one (a replaced empty object)."""
    return any(path == s or path.startswith(f"{s}.") or s.startswith(f"{path}.") for s in set_paths)


@pytest.mark.parametrize("company", [accounts.WRENFIELD, accounts.OSTRANDER], ids=lambda c: c.id)
def test_a_company_differs_from_the_sandbox_only_in_its_own_fields(
    company: accounts.CompanyAccount,
) -> None:
    with blank() as inst:
        (seed,) = rows(inst, "SELECT object FROM account")
        with inst.bulk() as ctx:
            accounts.install_account(ctx, company)
            assert ctx.state["account"]["id"] == company.id
        (row,) = rows(inst, "SELECT id, livemode, object, settings FROM account")
    sandbox, installed = _leaves(json.loads(seed["object"])), _leaves(json.loads(row["object"]))
    own = _leaves(dict(company.fields))
    changed = {
        path
        for path in sandbox.keys() | installed.keys()
        if sandbox.get(path) != installed.get(path)
    }
    assert changed and all(_related(path, set(own)) for path in changed)
    assert all(installed[path] == value for path, value in own.items())
    assert (row["id"], row["livemode"]) == (company.id, 0)
    assert json.loads(row["settings"]) == dict(company.settings)
    bank = json.loads(row["object"])["external_accounts"]["data"]
    assert [account["id"] for account in bank] == [company.bank_account_id]


def test_a_field_the_sandbox_lacks_is_refused() -> None:
    renamed = accounts.CompanyAccount(
        id=accounts.WRENFIELD.id,
        fields={"settings": {"dashboard": {"display_title": "Wrenfield Labs"}}},
        settings={},
        bank_suffix="Wf01Bk",
    )
    missing = r"has no settings\.dashboard\.display_title"
    with blank() as inst, inst.bulk() as ctx, pytest.raises(FixtureBuildError, match=missing):
        accounts.install_account(ctx, renamed)


def test_installing_starts_from_the_seeded_sandbox() -> None:
    other = "acct_7QmZx2RtLw9KpVe4"
    setup_sql = f"UPDATE account SET id = '{other}', object = json_set(object, '$.id', '{other}')"
    refused = pytest.raises(FixtureBuildError, match=f"seeded sandbox account {ACCOUNT_ID}")
    with blank(setup_sql) as inst, inst.bulk() as ctx, refused:
        accounts.install_account(ctx, accounts.WRENFIELD)
