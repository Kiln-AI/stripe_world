"""The idempotency layer, through the real four-tool chain
(`components/cross_cutting.md` §3.1): the four outcomes of §3.1.4, the
pre-execution retraction, and §3.1.8's two doors — the change log cannot see
`idempotency_keys`, so "the replay wrote nothing" is asserted through
`inst.change_log()` and "the key was recorded" through `inst.inspect()`,
never conflated (the mistake §3.1.8 names)."""

import pytest
import seahaven

from conftest import BLANK_NOW
from seahaven_stripe_world.middleware.idempotency import request_hash

pytestmark = pytest.mark.seahaven(fixture=None, now=BLANK_NOW)


def write(
    instance: seahaven.Instance, path: str, params: dict | None = None, key: str | None = None
):
    return instance.call(
        "stripe_api_write", method="POST", path=path, params=params, idempotency_key=key
    )


def key_row(instance: seahaven.Instance, key: str) -> dict | None:
    return instance.inspect().one(
        "SELECT method, path, request_hash, state, status FROM idempotency_keys WHERE key = ?", key
    )


def a_customer(instance: seahaven.Instance, email: str = "idem@example.test") -> str:
    return instance.call(
        "stripe_api_write", method="POST", path="/v1/customers", params={"email": email}
    )["body"]["id"]


# --- the short-circuit: outcome (b), §3.1.8's two doors -------------------------------------------


def test_a_replayed_post_writes_nothing_and_returns_the_stored_body(
    instance: seahaven.Instance,
) -> None:
    first = write(
        instance, "/v1/payment_intents", {"amount": 4900, "currency": "usd"}, key="idem-1"
    )
    assert first["status"] == 200
    before_log = instance.change_log()
    replay = write(
        instance, "/v1/payment_intents", {"amount": 4900, "currency": "usd"}, key="idem-1"
    )
    # Door one: the stored body, byte for byte — `next_` never ran, so nothing
    # was validated, written or emitted, and the two responses are one.
    assert replay == first
    # Door two: exactly the first call's records — the replay added none.
    assert instance.change_log() == before_log
    assert instance.inspect().one("SELECT count(*) AS n FROM payment_intents") == {"n": 1}
    # And the key row records the request it belongs to.
    row = key_row(instance, "idem-1")
    assert row == {
        "method": "POST",
        "path": "/v1/payment_intents",
        "request_hash": request_hash(
            "POST", "/v1/payment_intents", {"amount": 4900, "currency": "usd"}
        ),
        "state": "complete",
        "status": 200,
    }


def test_the_hash_ignores_parameter_key_order(instance: seahaven.Instance) -> None:
    write(instance, "/v1/payment_intents", {"amount": 700, "currency": "usd"}, key="idem-2a")
    replay = write(
        instance, "/v1/payment_intents", {"currency": "usd", "amount": 700}, key="idem-2a"
    )
    assert replay["status"] == 200  # one request, however the caller ordered it
    assert instance.inspect().one("SELECT count(*) AS n FROM payment_intents") == {"n": 1}


# --- the mismatch: outcome (c) --------------------------------------------------------------------


def test_the_same_key_with_different_parameters_answers_the_mismatch(
    instance: seahaven.Instance,
) -> None:
    write(instance, "/v1/payment_intents", {"amount": 4900, "currency": "usd"}, key="idem-3")
    mismatch = write(
        instance, "/v1/payment_intents", {"amount": 5000, "currency": "usd"}, key="idem-3"
    )
    assert mismatch["status"] == 400
    error = mismatch["body"]["error"]
    assert error["type"] == "idempotency_error"
    assert "code" not in error  # the envelope omits null fields
    assert error["message"] == (
        "Keys for idempotent requests can only be used with the same parameters they "
        "were first used with. Try using a key other than 'idem-3' if you meant to "
        "execute a different request."
    )
    # Nothing re-executed and the stored row keeps the original request.
    assert instance.inspect().one("SELECT count(*) AS n FROM payment_intents") == {"n": 1}
    row = key_row(instance, "idem-3")
    assert row is not None
    assert row["request_hash"] == request_hash(
        "POST", "/v1/payment_intents", {"amount": 4900, "currency": "usd"}
    )


def test_a_key_is_scoped_to_the_endpoint(instance: seahaven.Instance) -> None:
    """The endpoint is in the hash, not the primary key (§3.1.2/§7.2): one key
    across two endpoints is a mismatch, not a replay."""
    write(instance, "/v1/customers", {"email": "one@example.test"}, key="idem-4")
    mismatch = write(
        instance, "/v1/payment_intents", {"amount": 100, "currency": "usd"}, key="idem-4"
    )
    assert mismatch["status"] == 400
    assert mismatch["body"]["error"]["type"] == "idempotency_error"


# --- pass-throughs: §3.1.1 ------------------------------------------------------------------------


def test_delete_does_not_honour_the_key(instance: seahaven.Instance) -> None:
    one = a_customer(instance, "gone@example.test")
    two = a_customer(instance, "gone2@example.test")
    first = instance.call(
        "stripe_api_write", method="DELETE", path=f"/v1/customers/{one}", idempotency_key="idem-5"
    )
    second = instance.call(
        "stripe_api_write", method="DELETE", path=f"/v1/customers/{two}", idempotency_key="idem-5"
    )
    assert first["status"] == 200
    # Re-executed, not replayed: the second answer names the second customer.
    assert second["status"] == 200
    assert second["body"]["id"] == two
    assert key_row(instance, "idem-5") is None  # v1 DELETE ignores keys silently


def test_a_post_without_a_key_never_touches_the_table(instance: seahaven.Instance) -> None:
    write(instance, "/v1/customers", {"email": "nokey@example.test"})
    write(instance, "/v1/customers", {"email": "nokey2@example.test"})
    assert instance.inspect().one("SELECT count(*) AS n FROM idempotency_keys") == {"n": 0}
    assert instance.inspect().one("SELECT count(*) AS n FROM customers") == {"n": 2}


def test_a_non_string_path_is_the_frameworks_refusal_not_a_db_error(
    instance: seahaven.Instance,
) -> None:
    """CR round 1: the reservation INSERT bound the raw path, so a non-string
    path with a key escaped as INTERNAL. The pass-through (§3.1.1's reasoning)
    lets validation raise the framework's own invalid-input error and reserves
    nothing."""
    with pytest.raises(seahaven.ToolError) as raised:
        instance.call(
            "stripe_api_write",
            method="POST",
            path=None,  # type: ignore[arg-type]
            params={"email": "x@example.test"},
            idempotency_key="idem-path",
        )
    assert raised.value.code == "INVALID_INPUT"
    assert key_row(instance, "idem-path") is None


# --- outcome (a)'s retraction arms ----------------------------------------------------------------


def test_a_pre_execution_fault_retracts_the_reservation(instance: seahaven.Instance) -> None:
    """`parameter_invalid_integer` on amount=0 is pre-execution (Phase 8's
    recorded refusal): "you can retry these requests" must be true, so the
    reservation is gone and a corrected retry executes fresh."""
    refused = write(instance, "/v1/payment_intents", {"amount": 0, "currency": "usd"}, key="idem-6")
    assert refused["status"] == 400
    assert key_row(instance, "idem-6") is None
    ok = write(instance, "/v1/payment_intents", {"amount": 1200, "currency": "usd"}, key="idem-6")
    assert ok["status"] == 200  # the key was never poisoned
    row = key_row(instance, "idem-6")
    assert row is not None
    assert row["state"] == "complete"


def test_a_raised_execution_fault_is_cached_not_retracted(instance: seahaven.Instance) -> None:
    """§3.5.5's middle branch: a `resource_missing` raised inside a handler
    (pre_execution=False) reached endpoint execution, so the 404 is cached —
    the retry replays it without re-running anything."""
    missing = write(
        instance,
        "/v1/payment_intents/pi_nope/confirm",
        None,
        key="idem-7",
    )
    assert missing["status"] == 404
    assert key_row(instance, "idem-7") == {
        "method": "POST",
        "path": "/v1/payment_intents/pi_nope/confirm",
        "request_hash": request_hash("POST", "/v1/payment_intents/pi_nope/confirm", None),
        "state": "complete",
        "status": 404,
    }
    replay = write(
        instance,
        "/v1/payment_intents/pi_nope/confirm",
        None,
        key="idem-7",
    )
    assert replay == missing


# --- outcome (d) ----------------------------------------------------------------------------------


def test_an_in_flight_key_answers_409(instance: seahaven.Instance) -> None:
    """Seeded through `inst.bulk()` — the one honest way this synchronous
    world reaches (d): a previous call died between reservation and
    completion (§3.1.4)."""
    with instance.bulk() as ctx:
        ctx.db.execute(
            "INSERT INTO idempotency_keys (key, method, path, request_hash, state, status,"
            " body, created) VALUES ('idem-8', 'POST', '/v1/customers', 'deadbeef',"
            " 'in_flight', NULL, NULL, ?)",
            ctx.clock.iso(),
        )
    in_flight = write(instance, "/v1/customers", {"email": "x@example.test"}, key="idem-8")
    assert in_flight["status"] == 409
    error = in_flight["body"]["error"]
    assert error["type"] == "idempotency_error"
    assert error["code"] == "idempotency_key_in_use"
    # In flight beats the hash (§3.1.4d): nothing executed, nothing completed.
    assert instance.inspect().one("SELECT count(*) AS n FROM customers") == {"n": 0}
    row = key_row(instance, "idem-8")
    assert row is not None
    assert row["state"] == "in_flight"


# --- §3.5.5: the decline is an outcome, so it is cached -------------------------------------------


def test_a_returned_402_decline_is_cached_under_the_key(instance: seahaven.Instance) -> None:
    cus = a_customer(instance, "declined@example.test")
    declined = instance.call(
        "stripe_api_write",
        method="POST",
        path="/v1/payment_methods",
        params={
            "type": "card",
            "card": {"number": "4000000000000341", "exp_month": 9, "exp_year": 2027},
        },
    )["body"]["id"]
    params = {
        "amount": 1000,
        "currency": "usd",
        "customer": cus,
        "payment_method": declined,
        "confirm": True,
    }
    first = write(instance, "/v1/payment_intents", params, key="idem-9")
    assert first["status"] == 402
    replay = write(instance, "/v1/payment_intents", params, key="idem-9")
    assert replay == first  # the decline replayed, not re-run
    # Exactly one failed charge row exists across both calls: re-running the
    # confirm would have written a second one (§3.5.5's stated stake).
    assert instance.inspect().one("SELECT count(*) AS n FROM charges WHERE status = 'failed'") == {
        "n": 1
    }
    row = key_row(instance, "idem-9")
    assert row is not None
    assert row["status"] == 402
