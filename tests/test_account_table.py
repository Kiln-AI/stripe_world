"""The account row: what the schema seeds, how startup reads it, and what it refuses.

Every case goes through a real instance of the real world, because the row is
read by the startup hook at instance creation and nowhere else: `setup_sql`
shapes the row before the hook runs, and a refusal is a `WorldBug` from
`world.instance(...)` itself (company_fixtures architecture §1.4).
"""

import copy
import json
from pathlib import Path

import pytest
import seahaven
from seahaven.db import build_blank

from conftest import BLANK_NOW
from seahaven_stripe_world.billing import dunning, ledger
from seahaven_stripe_world.startup import ACCOUNT_ID

GOLDEN = Path(__file__).resolve().parent / "golden" / "sandbox_account.json"

# Another well-formed account, for the cases that install one with `setup_sql`.
OTHER_ACCOUNT = "acct_7QmZx2RtLw9KpVe4"


def install(account_id: str) -> str:
    """`setup_sql` moving the seeded row, and its object's id, to `account_id`."""
    return (
        f"UPDATE account SET id = '{account_id}', object = json_set(object, '$.id', '{account_id}')"
    )


def account_entry(inst: seahaven.Instance) -> dict:
    """The one account `list_available_accounts_or_orgs` offers."""
    (entry,) = inst.call("list_available_accounts_or_orgs")["accounts"]
    return entry


def stored_mode(inst: seahaven.Instance) -> list[dict]:
    return inst.inspect().rows("SELECT id, livemode FROM account")


def test_seed_is_todays_sandbox_account(world: seahaven.World) -> None:
    """The schema alone seeds the sandbox object this world always served, with no mode."""
    conn = build_blank(
        ":memory:", world.schema, clock=seahaven.Clock.from_iso(BLANK_NOW), seed=b"golden"
    )
    try:
        rows = list(conn.execute("SELECT id, livemode, object, settings FROM account"))
    finally:
        conn.close()
    assert rows == [(ACCOUNT_ID, None, GOLDEN.read_text(encoding="utf-8"), "{}")]


def test_blank_defaults_live_and_stamps(world: seahaven.World) -> None:
    with world.instance(None, now=BLANK_NOW) as inst:
        assert inst.call("get_stripe_account_info") == json.loads(
            GOLDEN.read_text(encoding="utf-8")
        )
        assert account_entry(inst) == {"stripe_context": ACCOUNT_ID, "livemode": True, "name": None}
        assert stored_mode(inst) == [{"id": ACCOUNT_ID, "livemode": 1}]
        # The stamp is a startup write: starting state, not the episode's work.
        assert inst.change_log() == []


def test_blank_opens_test_and_stamps(world: seahaven.World) -> None:
    with world.instance(None, now=BLANK_NOW, startup={"livemode": False}) as inst:
        assert inst.call("get_stripe_account_info") == json.loads(
            GOLDEN.read_text(encoding="utf-8")
        )
        assert account_entry(inst)["livemode"] is False
        assert stored_mode(inst) == [{"id": ACCOUNT_ID, "livemode": 0}]


def test_empty_opens_live(world: seahaven.World) -> None:
    with world.instance("empty") as inst:
        assert account_entry(inst) == {"stripe_context": ACCOUNT_ID, "livemode": True, "name": None}


def test_empty_refuses_test_mode(world: seahaven.World) -> None:
    with pytest.raises(seahaven.WorldBug) as raised:
        world.instance("empty", startup={"livemode": False})
    message = str(raised.value)
    assert f"account {ACCOUNT_ID} holds live-mode data" in message
    assert "livemode=False" in message


def test_matching_mode_is_accepted(world: seahaven.World) -> None:
    with world.instance("empty", startup={"livemode": True}) as inst:
        assert account_entry(inst)["livemode"] is True


def test_frozen_test_mode_reopens_in_test_mode(world: seahaven.World, tmp_path: Path) -> None:
    """Freezing keeps the stamp, so the fixture is a test-mode account from then on."""
    elsewhere = copy.copy(world)
    elsewhere.fixtures_dir = tmp_path / "fixtures"
    elsewhere.fixtures_dir.mkdir()
    with elsewhere.instance(
        None, now=BLANK_NOW, clock_mode="fixed", startup={"livemode": False}
    ) as inst:
        inst.freeze("sandbox_test", "A blank account, opened in test mode.")

    with elsewhere.instance("sandbox_test") as inst:
        assert account_entry(inst)["livemode"] is False
        assert stored_mode(inst) == [{"id": ACCOUNT_ID, "livemode": 0}]
    with pytest.raises(seahaven.WorldBug) as raised:
        elsewhere.instance("sandbox_test", startup={"livemode": True})
    assert "test-mode data" in str(raised.value)
    assert "livemode=True" in str(raised.value)


def test_missing_row_refused(world: seahaven.World) -> None:
    with pytest.raises(seahaven.WorldBug, match="the account table is empty"):
        world.instance(None, now=BLANK_NOW, setup_sql="DELETE FROM account")


def test_second_row_refused(world: seahaven.World) -> None:
    second = (
        "INSERT INTO account (id, livemode, object, settings) "
        f"SELECT '{OTHER_ACCOUNT}', livemode, json_set(object, '$.id', '{OTHER_ACCOUNT}'), "
        "settings FROM account"
    )
    with pytest.raises(seahaven.WorldBug, match="the account table holds 2 rows"):
        world.instance(None, now=BLANK_NOW, setup_sql=second)


@pytest.mark.parametrize(
    "account_id",
    ["acct_short", "acct_0NGvb3Cz7hWRKLEpX", "acct_0NGvb3Cz7hWRKL-p", "cust_0NGvb3Cz7hWRKLEp"],
)
def test_malformed_id_refused(world: seahaven.World, account_id: str) -> None:
    with pytest.raises(seahaven.WorldBug) as raised:
        world.instance(None, now=BLANK_NOW, setup_sql=install(account_id))
    assert str(raised.value) == f"account id {account_id!r} is not acct_ plus 16 alphanumerics"


def test_object_id_mismatch_refused(world: seahaven.World) -> None:
    with pytest.raises(seahaven.WorldBug) as raised:
        world.instance(None, now=BLANK_NOW, setup_sql=f"UPDATE account SET id = '{OTHER_ACCOUNT}'")
    assert str(raised.value) == f"account row {OTHER_ACCOUNT!r} holds the object of {ACCOUNT_ID!r}"


def test_unknown_settings_key_refused(world: seahaven.World) -> None:
    with pytest.raises(seahaven.WorldBug, match="account settings key 'fees' is not one of"):
        world.instance(
            None, now=BLANK_NOW, setup_sql="""UPDATE account SET settings = '{"fees": {}}'"""
        )


@pytest.mark.parametrize(
    "path", ["$.settings.dashboard.display_name", "$.settings.dashboard", "$.country",
             "$.default_currency"],
)  # fmt: skip
def test_an_object_missing_a_field_the_state_reads_is_refused(
    world: seahaven.World, path: str
) -> None:
    """The name, country and currency are read from the object: a row without one cannot start.
    A null value is fine (the sandbox's display name is null); a missing key is not."""
    setup_sql = f"UPDATE account SET object = json_remove(object, '{path}')"
    with pytest.raises(seahaven.WorldBug) as raised:
        world.instance(None, now=BLANK_NOW, setup_sql=setup_sql)
    missing = "settings.dashboard.display_name" if "dashboard" in path else path[2:]
    assert str(raised.value) == f"account {ACCOUNT_ID} object has no {missing}"


def test_settings_reach_ledger_and_dunning(world: seahaven.World) -> None:
    settings = {
        "ledger": {"dispute_received_fee": 2000},
        "dunning": {"end_behavior": "mark_unpaid"},
    }
    setup_sql = f"UPDATE account SET settings = '{json.dumps(settings, ensure_ascii=False)}'"
    with world.instance(None, now=BLANK_NOW, setup_sql=setup_sql) as inst, inst.bulk() as ctx:
        assert ledger.ledger_spec(ctx).dispute_received_fee == 2000
        assert dunning.policy(ctx).end_behavior == "mark_unpaid"
        # Only what the row says changes; the rest keeps the world's defaults.
        assert ledger.ledger_spec(ctx).settlement_business_days == 2
        assert dunning.policy(ctx).max_attempts == 8


def test_ids_carry_the_rows_account_fragment(world: seahaven.World) -> None:
    """Every minted Format B id embeds the account the row names, not the sandbox's."""
    with world.instance(
        None, now=BLANK_NOW, clock_mode="fixed", setup_sql=install(OTHER_ACCOUNT)
    ) as inst:
        assert account_entry(inst)["stripe_context"] == OTHER_ACCOUNT
        intent = inst.call(
            "stripe_api_write",
            stripe_api_operation_id="PostPaymentIntents",
            parameters={"amount": 2000, "currency": "usd"},
            stripe_context=OTHER_ACCOUNT,
            livemode=True,
        )
    # pi_ + version digit (1) + timestamp group (5) + account fragment (10) + random (8).
    token = intent["id"].removeprefix("pi_")
    assert token[6:16] == OTHER_ACCOUNT[-10:]
